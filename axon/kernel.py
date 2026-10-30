"""The Axon kernel — the agent-native execution core.

Every agent request flows through one chokepoint: invoke(). That single path
enforces the whole agent-native contract in order:

    1. authenticate the capability token   (Capability Engine)
    2. authorize the specific call          (grants + path scopes)
    3. validate params against the schema   (Action Registry)
    4. run policy                            (denylist / rate limit / approval)
    5. snapshot state for mutating actions  (Reversibility)
    6. execute the handler
    7. log the outcome                       (Audit)

The gate phase (1-4) and the execution phase (5-6) are audited separately, so a
gate *denial* is never confused with an execution *failure*, and no call can
ever leave the audit at only "authorized". Because there is exactly one path,
safety, permissions, reversibility, and audit are not features an agent can
bypass — they are the substrate.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .audit import AuditLog
from .capabilities import CapabilityEngine, CapabilityError
from .policy import PolicyEngine, PolicyError
from .registry import Registry
from .reversibility import Journal

# args whose values are filesystem paths worth snapshotting before a mutation.
_PATH_ARGS = ("path", "src", "dst")


class Kernel:
    def __init__(self, home: Path | None = None) -> None:
        self.home = home or (Path.home() / ".axon")
        self.home.mkdir(parents=True, exist_ok=True)
        self.registry = Registry()
        self.caps = CapabilityEngine(self.home)
        self.audit = AuditLog(self.home / "audit.jsonl")
        self.journal = Journal(self.home)
        self.policy = PolicyEngine(self.home)
        # The approval gate for dangerous actions is opt-in: set True to require
        # an explicit policy.pre_approve() before any risk="dangerous" call runs.
        # Denylist and rate limits always apply regardless of this flag.
        self.require_approval = False

    def invoke(
        self, token: str, action_name: str, args: dict[str, Any] | None = None
    ) -> Any:
        args = args or {}
        agent = "?"
        cap_id = "?"

        # --- gate phase: authenticate, authorize, validate, policy ----------
        # A failure here is a DENIAL — the action never ran.
        try:
            cap = self.caps.verify(token)
            agent, cap_id = cap.agent, cap.id
            action = self.registry.get(action_name)
            self.caps.authorize(cap, action_name, args)
            action.validate(args)
            self.policy.check(cap, action, args, enforce_approval=self.require_approval)
        except (CapabilityError, PolicyError, KeyError, ValueError) as exc:
            self.audit.record(
                {
                    "agent": agent,
                    "cap": cap_id,
                    "action": action_name,
                    "args": args,
                    "status": "denied",
                    "error": str(exc),
                }
            )
            raise

        audit_id = self.audit.record(
            {
                "agent": agent,
                "cap": cap_id,
                "action": action_name,
                "args": args,
                "status": "authorized",
                "mutating": action.mutating,
            }
        )

        # --- execution phase: snapshot then run -----------------------------
        # A failure here is an ERROR — the action was permitted but broke.
        journaled = False
        started = time.time()
        try:
            if action.mutating:
                for key in _PATH_ARGS:
                    if key in args:
                        self.journal.snapshot_file(audit_id, str(args[key]))
                        journaled = True
            result = action.handler(**args)
        except Exception as exc:  # noqa: BLE001 — record then re-raise
            self.audit.record(
                {
                    "agent": agent,
                    "cap": cap_id,
                    "action": action_name,
                    "status": "error",
                    "error": str(exc),
                    "duration_ms": round((time.time() - started) * 1000, 2),
                    # undo_id points at the snapshot so an orphaned/partial
                    # mutation is still discoverable and reversible.
                    "undo_id": audit_id if journaled else None,
                }
            )
            raise

        self.audit.record(
            {
                "agent": agent,
                "cap": cap_id,
                "action": action_name,
                "status": "ok",
                "duration_ms": round((time.time() - started) * 1000, 2),
                # Only advertise reversibility that actually exists.
                "undo_id": audit_id if journaled else None,
            }
        )
        return result

    def undo(self, audit_id: str) -> str:
        result = self.journal.undo(audit_id)
        self.audit.record(
            {"agent": "kernel", "action": "undo", "target": audit_id, "status": "ok"}
        )
        return result
