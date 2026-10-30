"""Policy Engine — the approval, rate-limit, and denylist gate.

The Capability Engine answers *"is this agent allowed to call this action at
all?"*. The Policy Engine answers the orthogonal question the kernel asks right
after: *"even though it is permitted, should this specific call go through
right now?"*. Three orthogonal controls, checked on every ``invoke``:

    1. denylist   — action-name globs that always block (a kill-switch)
    2. approval   — ``risk == "dangerous"`` calls need a one-shot pre-approval
    3. rate limit — per-(agent, action) sliding windows throttle bursts

Rate windows are transient in-process state; the denylist and rate-limit config
are durable, persisted under ``home/policy.json`` so a restarted kernel keeps
its guardrails. Time is read from ``time.time()``.
"""

from __future__ import annotations

import fnmatch
import json
import time
from pathlib import Path
from typing import Any

from .capabilities import Capability
from .registry import Action


class PolicyError(Exception):
    """Raised when policy blocks an otherwise-permitted call."""


class PolicyEngine:
    """Gates permitted calls on denylist, approval, and rate-limit policy."""

    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self._config_path = self.home / "policy.json"
        # Durable config, persisted to policy.json.
        self._denylist: list[str] = []
        self._rate_limits: dict[str, dict[str, float]] = {}
        # Transient in-process state.
        self._windows: dict[tuple[str, str], list[float]] = {}
        self._approvals: dict[tuple[str, str], int] = {}
        self._load()

    # -- kernel hooks --------------------------------------------------------

    def check(
        self,
        cap: Capability,
        action: Action,
        args: dict[str, Any],
        *,
        enforce_approval: bool = True,
    ) -> None:
        """Kernel calls this right after ``authorize()``; raise to block.

        Evaluated as three ordered gates with no side effects until every gate
        has passed, so a rate-limit trip never consumes a pending approval.

        ``enforce_approval`` lets the kernel make the dangerous-action approval
        gate opt-in (it passes its ``require_approval`` flag). The denylist and
        rate limits always apply regardless.
        """
        name = action.name
        agent = cap.agent
        now = time.time()

        # 1. denylist — always blocks, regardless of risk or approval.
        for pat in self._denylist:
            if fnmatch.fnmatch(name, pat):
                raise PolicyError(
                    f"denied by policy: '{name}' matches denylist '{pat}'"
                )

        # 2. approval gate for dangerous actions.
        if enforce_approval and self.requires_approval(cap, action, args):
            raise PolicyError(
                f"approval required: dangerous action '{name}' has no pending "
                f"approval for agent '{agent}'"
            )

        # 3. rate limits — evaluate matching windows without recording yet.
        key = (agent, name)
        window = self._windows.get(key, [])
        matched = [
            (pat, cfg)
            for pat, cfg in self._rate_limits.items()
            if fnmatch.fnmatch(name, pat)
        ]
        for pat, cfg in matched:
            cutoff = now - cfg["per_seconds"]
            recent = sum(1 for t in window if t >= cutoff)
            if recent >= cfg["max_calls"]:
                raise PolicyError(
                    f"rate limit exceeded for agent '{agent}': '{name}' > "
                    f"{int(cfg['max_calls'])} call(s) per {cfg['per_seconds']}s"
                )

        # All gates passed — now apply side effects.
        if enforce_approval and action.risk == "dangerous":
            self._consume_approval(agent, name)
        if matched:
            window = window + [now]
            longest = max(cfg["per_seconds"] for _, cfg in matched)
            self._windows[key] = [t for t in window if t >= now - longest]

    def requires_approval(
        self, cap: Capability, action: Action, args: dict[str, Any]
    ) -> bool:
        """True if this call is dangerous and has no pending pre-approval.

        Pure query — inspecting approval state here never consumes it; only
        ``check()`` consumes a pending approval.
        """
        if action.risk != "dangerous":
            return False
        return self._approvals.get((cap.agent, action.name), 0) <= 0

    # -- config --------------------------------------------------------------

    def set_rate_limit(self, pattern: str, max_calls: int, per_seconds: float) -> None:
        """Throttle actions matching ``pattern`` to ``max_calls`` per window."""
        self._rate_limits[pattern] = {
            "max_calls": int(max_calls),
            "per_seconds": float(per_seconds),
        }
        self._save()

    def deny(self, pattern: str) -> None:
        """Add an action-name glob to the denylist (a hard block)."""
        if pattern not in self._denylist:
            self._denylist.append(pattern)
            self._save()

    def pre_approve(self, agent: str, action_name: str) -> None:
        """Grant a one-shot approval for (agent, action), consumed by check()."""
        key = (agent, action_name)
        self._approvals[key] = self._approvals.get(key, 0) + 1

    # -- internals -----------------------------------------------------------

    def _consume_approval(self, agent: str, action_name: str) -> None:
        key = (agent, action_name)
        n = self._approvals.get(key, 0)
        if n > 0:
            n -= 1
            if n:
                self._approvals[key] = n
            else:
                del self._approvals[key]

    def _load(self) -> None:
        if not self._config_path.exists():
            return
        data = json.loads(self._config_path.read_text())
        self._denylist = list(data.get("denylist", []))
        self._rate_limits = {
            pat: {
                "max_calls": int(cfg["max_calls"]),
                "per_seconds": float(cfg["per_seconds"]),
            }
            for pat, cfg in data.get("rate_limits", {}).items()
        }

    def _save(self) -> None:
        data = {"denylist": self._denylist, "rate_limits": self._rate_limits}
        self._config_path.write_text(json.dumps(data, indent=2))
