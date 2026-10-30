"""Capability Engine — per-agent, time-boxed, revocable permissions.

The core departure from macOS/Windows: an agent never gets "root or nothing".
It gets a signed capability token that grants an explicit set of action
patterns, optionally scoped to certain filesystem paths, and expiring at a
fixed time. Tokens are HMAC-signed so they can't be forged, and can be revoked
by id even before they expire.
"""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class CapabilityError(Exception):
    """Raised when a token is invalid, expired, revoked, or too weak."""


@dataclass
class Capability:
    """The decoded contents of a capability token."""

    id: str
    agent: str
    grants: list[str]  # action patterns, e.g. ["fs.read", "proc.*"]
    path_scopes: list[str]  # allowed path prefixes; [] = no fs restriction
    issued_at: float
    expires_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent": self.agent,
            "grants": self.grants,
            "path_scopes": self.path_scopes,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }

    def permits_action(self, action_name: str) -> bool:
        return any(fnmatch.fnmatch(action_name, g) for g in self.grants)

    def permits_path(self, path: str) -> bool:
        if not self.path_scopes:
            return True
        resolved = str(Path(path).expanduser().resolve())
        return any(
            resolved == s or resolved.startswith(s.rstrip("/") + "/")
            for s in (str(Path(p).expanduser().resolve()) for p in self.path_scopes)
        )


class CapabilityEngine:
    """Issues, verifies, and revokes signed capability tokens."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.home.mkdir(parents=True, exist_ok=True)
        self._secret = self._load_or_create_secret()
        self._revoked_path = home / "revoked.json"

    def _load_or_create_secret(self) -> bytes:
        secret_path = self.home / "secret.key"
        if secret_path.exists():
            return secret_path.read_bytes()
        secret = uuid.uuid4().bytes + uuid.uuid4().bytes
        secret_path.write_bytes(secret)
        secret_path.chmod(0o600)
        return secret

    def _sign(self, payload: bytes) -> str:
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def _revoked(self) -> set[str]:
        if not self._revoked_path.exists():
            return set()
        return set(json.loads(self._revoked_path.read_text()))

    def issue(
        self,
        agent: str,
        grants: list[str],
        path_scopes: list[str] | None = None,
        ttl_seconds: int = 3600,
    ) -> str:
        """Mint a signed token. Returns an opaque string the agent presents."""
        now = time.time()
        cap = Capability(
            id=uuid.uuid4().hex[:12],
            agent=agent,
            grants=grants,
            path_scopes=path_scopes or [],
            issued_at=now,
            expires_at=now + ttl_seconds,
        )
        payload = json.dumps(cap.to_dict(), sort_keys=True).encode()
        b64 = base64.urlsafe_b64encode(payload).decode()
        return f"{b64}.{self._sign(payload)}"

    def verify(self, token: str) -> Capability:
        """Decode + authenticate a token, or raise CapabilityError."""
        try:
            b64, sig = token.split(".", 1)
            payload = base64.urlsafe_b64decode(b64.encode())
        except Exception as exc:  # noqa: BLE001
            raise CapabilityError("malformed token") from exc

        if not hmac.compare_digest(sig, self._sign(payload)):
            raise CapabilityError("bad signature — token forged or tampered")

        cap = Capability(**json.loads(payload))
        if time.time() > cap.expires_at:
            raise CapabilityError("token expired")
        if cap.id in self._revoked():
            raise CapabilityError("token revoked")
        return cap

    def revoke(self, cap_id: str) -> None:
        """Kill a token before its natural expiry."""
        revoked = self._revoked()
        revoked.add(cap_id)
        self._revoked_path.write_text(json.dumps(sorted(revoked)))

    def authorize(
        self, cap: Capability, action_name: str, args: dict[str, Any]
    ) -> None:
        """Gatekeeper: does this capability permit this exact call? Raise if not."""
        if not cap.permits_action(action_name):
            raise CapabilityError(
                f"agent '{cap.agent}' not granted action '{action_name}' "
                f"(has: {cap.grants})"
            )
        # Path-scoped enforcement for any arg that looks like a path.
        for key in ("path", "src", "dst", "dir"):
            if key in args and not cap.permits_path(str(args[key])):
                raise CapabilityError(
                    f"path '{args[key]}' outside agent's scope {cap.path_scopes}"
                )
