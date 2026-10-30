"""Daemon + Client tests.

Two layers: the pure `handle()` dispatch (no socket) and one real end-to-end
round-trip over a Unix socket bound under tmp_path. Only the stable core and
this module's own modules are imported.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from axon import boot
from axon.client import Client
from axon.daemon import Daemon


def make_daemon(tmp_path: Path, socket_path: Path | None = None) -> Daemon:
    kernel = boot(home=tmp_path / ".axon")
    return Daemon(kernel, socket_path=socket_path)


# --- pure dispatch (no socket) ----------------------------------------


def test_handle_grant_invoke_audit_then_denied(tmp_path):
    d = make_daemon(tmp_path)

    # grant → token
    r = d.handle(
        {"method": "grant", "params": {"agent": "tester", "grants": ["sys.info"]}}
    )
    assert r["ok"] is True
    token = r["result"]
    assert isinstance(token, str) and "." in token

    # invoke a granted action
    r = d.handle(
        {
            "method": "invoke",
            "params": {"token": token, "action": "sys.info", "args": {}},
        }
    )
    assert r["ok"] is True
    assert "system" in r["result"]

    # audit tail reflects the activity
    r = d.handle({"method": "audit", "params": {"n": 10}})
    assert r["ok"] is True
    assert any(e.get("action") == "sys.info" for e in r["result"])

    # a denied call (action not in grants) comes back ok:false, not an exception
    r = d.handle(
        {
            "method": "invoke",
            "params": {
                "token": token,
                "action": "fs.read",
                "args": {"path": str(tmp_path / "x")},
            },
        }
    )
    assert r["ok"] is False
    assert r.get("error")


def test_handle_actions_and_unknown_method(tmp_path):
    d = make_daemon(tmp_path)

    r = d.handle({"method": "actions", "params": {}})
    assert r["ok"] is True
    names = {a["name"] for a in r["result"]}
    assert "sys.info" in names

    # unknown method degrades gracefully
    r = d.handle({"method": "bogus", "params": {}})
    assert r["ok"] is False
    assert r.get("error")

    # missing required param also degrades to ok:false
    r = d.handle({"method": "grant", "params": {"agent": "x"}})
    assert r["ok"] is False


# --- end to end over a real socket ------------------------------------


def _wait_for(path: Path, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"socket never appeared: {path}")


def test_socket_end_to_end(tmp_path):
    sock_path = tmp_path / "axon.sock"
    d = make_daemon(tmp_path, socket_path=sock_path)
    thread = threading.Thread(target=d.serve_forever, daemon=True)
    thread.start()
    try:
        _wait_for(sock_path)
        client = Client(socket_path=sock_path)

        token = client.call("grant", agent="remote", grants=["sys.info"])
        assert isinstance(token, str)

        info = client.call("invoke", token=token, action="sys.info", args={})
        assert "system" in info

        # discovery works over the wire too
        actions = client.call("actions")
        assert any(a["name"] == "sys.info" for a in actions)

        # a denied call surfaces as RuntimeError on the client side
        with pytest.raises(RuntimeError):
            client.call(
                "invoke",
                token=token,
                action="fs.read",
                args={"path": str(tmp_path / "nope")},
            )
    finally:
        d.stop()
        thread.join(timeout=5.0)
        assert not thread.is_alive()
