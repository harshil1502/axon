"""Axon daemon — the kernel exposed as a persistent local service.

Newline-delimited JSON over a Unix domain socket. Every request names a
`method` and its `params`; `handle()` is a *pure* function of (kernel, request)
with no I/O of its own, so the whole dispatch table is testable without ever
binding a socket. `serve_forever()`/`stop()` are a thin transport wrapped
around it.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any

from .kernel import Kernel


# macOS/BSD sockaddr_un.sun_path is 104 bytes; long tmp/home paths overflow it,
# so past this length we bind/connect by basename from the parent directory.
_SUN_PATH_MAX = 100


class Daemon:
    """Runs a Kernel as a long-lived service reachable over a Unix socket."""

    def __init__(self, kernel: Kernel, socket_path: Path | None = None) -> None:
        self.kernel = kernel
        self.socket_path = (
            Path(socket_path) if socket_path else kernel.home / "axon.sock"
        )
        self._sock: socket.socket | None = None
        self._running = False

    # --- pure dispatch (no socket, fully testable) ---------------------
    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        """Route one request to the kernel.

        Never raises: any failure — bad method, missing param, denied
        capability — degrades to ``{"ok": False, "error": ...}`` so the
        transport layer stays trivial. Success is ``{"ok": True, "result": ...}``.
        """
        method = request.get("method")
        params = request.get("params") or {}
        try:
            return {"ok": True, "result": self._dispatch(method, params)}
        except Exception as exc:  # noqa: BLE001 — everything maps to ok:false
            return {"ok": False, "error": str(exc)}

    def _dispatch(self, method: str | None, params: dict[str, Any]) -> Any:
        if method == "invoke":
            action = params.get("action") or params.get("action_name")
            return self.kernel.invoke(params["token"], action, params.get("args") or {})
        if method == "grant":
            return self.kernel.caps.issue(
                params["agent"],
                params["grants"],
                params.get("path_scopes"),
                params.get("ttl_seconds", 3600),
            )
        if method == "actions":
            return self.kernel.registry.describe(params.get("pattern", "*"))
        if method == "audit":
            return self.kernel.audit.tail(params.get("n", 20))
        if method == "undo":
            return self.kernel.undo(params["audit_id"])
        if method == "revoke":
            self.kernel.caps.revoke(params["cap_id"])
            return {"revoked": params["cap_id"]}
        raise ValueError(f"unknown method: {method!r}")

    # --- socket transport ---------------------------------------------
    def serve_forever(self) -> None:
        """Bind the Unix socket and dispatch requests until stop() is called."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._bind(sock)
        sock.listen(64)
        sock.settimeout(0.25)  # wake periodically so stop() is honoured
        self._sock = sock
        self._running = True
        try:
            while self._running:
                try:
                    conn, _ = sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                with conn:
                    self._serve_connection(conn)
        finally:
            sock.close()
            self._sock = None
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass

    def _serve_connection(self, conn: socket.socket) -> None:
        """Handle newline-delimited requests until the peer closes the stream.

        The accepted socket carries a read timeout so a stalled or silent peer
        is dropped rather than blocking the (serial) accept loop indefinitely —
        a well-behaved client sends its line immediately and is unaffected.
        """
        conn.settimeout(30.0)
        stream = conn.makefile("rwb")
        try:
            while self._running:
                try:
                    raw = stream.readline()
                except socket.timeout:
                    break  # idle/stalled peer — free the accept loop
                if not raw:
                    break  # peer closed the stream
                line = raw.strip()
                if not line:
                    continue
                try:
                    response = self.handle(json.loads(line))
                except Exception as exc:  # noqa: BLE001 — malformed JSON, etc.
                    response = {"ok": False, "error": str(exc)}
                stream.write((json.dumps(response) + "\n").encode())
                stream.flush()
        finally:
            stream.close()

    def stop(self) -> None:
        """Ask serve_forever's loop to exit (safe to call from another thread)."""
        self._running = False

    @staticmethod
    def _is_live(target: str) -> bool:
        """True if a daemon is already accepting connections at `target`."""
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.2)
        try:
            probe.connect(target)
            return True
        except OSError:
            return False
        finally:
            probe.close()

    def _bind(self, sock: socket.socket) -> None:
        path = self.socket_path
        path.parent.mkdir(parents=True, exist_ok=True)
        target = str(path)
        if path.exists():
            # Refuse to hijack a live daemon; only clear a genuinely stale socket.
            if len(target) <= _SUN_PATH_MAX and self._is_live(target):
                raise OSError(f"address already in use: a daemon is live at {path}")
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        if len(target) > _SUN_PATH_MAX:
            cwd = os.getcwd()
            os.chdir(str(path.parent))
            try:
                sock.bind(path.name)
            finally:
                os.chdir(cwd)
        else:
            sock.bind(target)
