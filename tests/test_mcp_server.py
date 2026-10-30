"""MCP server tests — drive the JSON-RPC surface against a real Axon daemon.

Starts a daemon on a Unix socket, points the MCP server at it via AXON_SOCK,
and exercises initialize / tools/list / tools/call — proving Claude Code's tool
calls would route through the daemon's audited, capability-gated chokepoint.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from axon import boot
from axon.daemon import Daemon


@pytest.fixture()
def daemon_sock(tmp_path):
    sock = Path(f"/tmp/axon-mcp-{os.getpid()}.sock")
    if sock.exists():
        sock.unlink()
    kernel = boot(home=tmp_path / ".axon")
    d = Daemon(kernel, socket_path=sock)
    t = threading.Thread(target=d.serve_forever, daemon=True)
    t.start()
    for _ in range(200):
        if sock.exists():
            break
        time.sleep(0.02)
    yield sock, tmp_path
    d.stop()
    t.join(timeout=5)
    if sock.exists():
        sock.unlink()


def _mcp(monkeypatch, sock, tmp_path, grants="sys.info mem.*"):
    monkeypatch.setenv("AXON_SOCK", str(sock))
    monkeypatch.setenv("AXON_MCP_GRANTS", grants)
    monkeypatch.setenv("AXON_MCP_SCOPE", str(tmp_path))
    monkeypatch.setenv("AXON_MCP_TTL", "3600")
    from axon.mcp_server import AxonMCP

    return AxonMCP()


def test_initialize_handshake(monkeypatch, daemon_sock):
    sock, tmp = daemon_sock
    srv = _mcp(monkeypatch, sock, tmp)
    r = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
        }
    )
    assert r["result"]["serverInfo"]["name"] == "axon"
    assert r["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in r["result"]["capabilities"]


def test_tools_list_reflects_grants(monkeypatch, daemon_sock):
    sock, tmp = daemon_sock
    srv = _mcp(monkeypatch, sock, tmp, grants="sys.info mem.*")
    r = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    names = {t["name"] for t in r["result"]["tools"]}
    assert "sys_info" in names  # granted, dot sanitized to underscore
    assert "mem_put" in names
    assert "shell_exec" not in names  # NOT granted → not exposed
    assert "fs_write" not in names
    # sanitized names must be MCP-legal (no dots)
    assert all("." not in n for n in names)


def test_tools_call_routes_through_daemon(monkeypatch, daemon_sock):
    sock, tmp = daemon_sock
    srv = _mcp(monkeypatch, sock, tmp)
    r = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "sys_info", "arguments": {}},
        }
    )
    assert r["result"].get("isError") is not True
    assert "system" in r["result"]["content"][0]["text"]


def test_mem_roundtrip_over_mcp(monkeypatch, daemon_sock):
    sock, tmp = daemon_sock
    srv = _mcp(monkeypatch, sock, tmp)
    srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "mem_put", "arguments": {"key": "k", "value": "hello"}},
        }
    )
    r = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "mem_get", "arguments": {"key": "k"}},
        }
    )
    assert "hello" in r["result"]["content"][0]["text"]


def test_ungranted_call_is_denied_not_executed(monkeypatch, daemon_sock):
    sock, tmp = daemon_sock
    srv = _mcp(monkeypatch, sock, tmp, grants="sys.info")  # no fs.*
    r = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "fs_read", "arguments": {"path": str(tmp / "x")}},
        }
    )
    # even if the model names an ungranted action, the daemon denies it
    assert r["result"]["isError"] is True
    assert (
        "not granted" in r["result"]["content"][0]["text"]
        or "denied" in r["result"]["content"][0]["text"].lower()
    )


def test_unknown_method_errors(monkeypatch, daemon_sock):
    sock, tmp = daemon_sock
    srv = _mcp(monkeypatch, sock, tmp)
    r = srv.handle({"jsonrpc": "2.0", "id": 7, "method": "bogus/thing", "params": {}})
    assert r["error"]["code"] == -32601
