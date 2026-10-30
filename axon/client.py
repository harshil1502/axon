"""Axon client — talk to a running Daemon over its Unix socket.

Mirrors the daemon's newline-delimited JSON protocol: open a connection, send
one request, read one response, raise on failure. Stdlib only.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any


# Keep in step with daemon._SUN_PATH_MAX: past this length we connect by
# basename from the parent dir to stay under the AF_UNIX sun_path limit.
_SUN_PATH_MAX = 100


class Client:
    """A thin blocking client for the Axon daemon."""

    def __init__(self, socket_path: Path | None = None) -> None:
        self.socket_path = (
            Path(socket_path) if socket_path else Path.home() / ".axon" / "axon.sock"
        )

    def call(self, method: str, **params: Any) -> Any:
        """Invoke one daemon method; return its result or raise RuntimeError.

        A ``{"ok": False, "error": ...}`` response — a denied capability, an
        unknown method, a validation failure — is surfaced as ``RuntimeError``.
        """
        request = {"method": method, "params": params}
        sock = self._connect()
        try:
            stream = sock.makefile("rwb")
            stream.write((json.dumps(request) + "\n").encode())
            stream.flush()
            line = stream.readline()
            stream.close()
        finally:
            sock.close()
        if not line:
            raise RuntimeError("no response from daemon")
        response = json.loads(line)
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "unknown error"))
        return response.get("result")

    def _connect(self) -> socket.socket:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        path = self.socket_path
        target = str(path)
        if len(target) > _SUN_PATH_MAX:
            cwd = os.getcwd()
            os.chdir(str(path.parent))
            try:
                sock.connect(path.name)
            finally:
                os.chdir(cwd)
        else:
            sock.connect(target)
        return sock
