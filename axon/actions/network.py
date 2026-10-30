"""Network actions — outbound HTTP(S) fetch through the typed, gated path.

Uses only ``urllib`` from the stdlib. The scheme is allowlisted to http/https
so the action can't be turned into a file/gopher/ftp read primitive; anything
else raises ``ValueError`` (which the kernel records as a denial). Flagged
``risk="dangerous"`` so the Policy Engine requires an approval before egress.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from ..registry import Registry

_ALLOWED_SCHEMES = ("http", "https")
_DEFAULT_MAX_BYTES = 64 * 1024
_DEFAULT_TIMEOUT = 15.0


def register(reg: Registry) -> None:
    @reg.action(
        "net.fetch",
        "Fetch an http/https URL and return its status and a truncated body.",
        {
            "url": {"type": "string", "required": True, "desc": "http/https URL"},
            "max_bytes": {
                "type": "integer",
                "required": False,
                "desc": "max body bytes to read (default 65536)",
            },
        },
        risk="dangerous",
    )
    def net_fetch(url: str, max_bytes: int = _DEFAULT_MAX_BYTES) -> dict[str, Any]:
        scheme = urlparse(url).scheme.lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise ValueError(
                f"net.fetch: only {'/'.join(_ALLOWED_SCHEMES)} allowed, got '{scheme}'"
            )
        req = urllib.request.Request(url, headers={"User-Agent": "axon/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as resp:
                raw = resp.read(max_bytes + 1)
                status = getattr(resp, "status", None) or resp.getcode()
        except urllib.error.HTTPError as exc:  # 4xx/5xx still carry a body/status
            raw = exc.read(max_bytes + 1)
            status = exc.code
        truncated = len(raw) > max_bytes
        body = raw[:max_bytes].decode("utf-8", errors="replace")
        return {"status": int(status), "body": body, "truncated": truncated}
