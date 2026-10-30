"""Process + network action tests — driven through a real Kernel + capability.

No test hits the public network: ``net.fetch`` is exercised on the scheme-
validation path and against a throwaway ``http.server`` bound to localhost.
``proc.kill`` is checked with signal 0 (existence probe) so nothing is actually
terminated.
"""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from axon.kernel import Kernel
from axon.actions import network, process


def make(tmp_path):
    """Fresh kernel with only the proc/net actions registered + a token."""
    k = Kernel(home=tmp_path / ".axon")
    process.register(k.registry)
    network.register(k.registry)
    tok = k.caps.issue("tester", ["proc.*", "net.*"])
    return k, tok


# --- registration / schema -------------------------------------------------


def test_registration_and_risk(tmp_path):
    k, _ = make(tmp_path)
    names = {a.name for a in k.registry.list("proc.*")}
    assert {"proc.info", "proc.tree", "proc.kill"} <= names
    assert k.registry.get("proc.info").risk == "read"
    assert k.registry.get("proc.tree").risk == "read"
    assert k.registry.get("proc.kill").risk == "dangerous"
    assert k.registry.get("net.fetch").risk == "dangerous"


# --- process ---------------------------------------------------------------


def test_proc_info_on_current_pid(tmp_path):
    k, tok = make(tmp_path)
    info = k.invoke(tok, "proc.info", {"pid": os.getpid()})
    assert isinstance(info, dict)
    assert info["pid"] == os.getpid()
    assert isinstance(info["name"], str)
    assert "stat" in info and "rss" in info


def test_proc_info_unknown_pid_raises(tmp_path):
    k, tok = make(tmp_path)
    with pytest.raises(ValueError):
        # PID far outside any plausible range.
        k.invoke(tok, "proc.info", {"pid": 999999})


def test_proc_tree_contains_current_pid(tmp_path):
    k, tok = make(tmp_path)
    roots = k.invoke(tok, "proc.tree", {})
    assert isinstance(roots, list) and roots

    seen: set[int] = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        assert {"pid", "ppid", "name", "children"} <= set(node)
        seen.add(node["pid"])
        stack.extend(node["children"])
    assert os.getpid() in seen


def test_proc_kill_signal_zero_is_nondestructive(tmp_path):
    k, tok = make(tmp_path)
    # signal 0 only checks that we may signal the process; it does not kill it.
    out = k.invoke(tok, "proc.kill", {"pid": os.getpid(), "signal": 0})
    assert "pid" in out and str(os.getpid()) in out


def test_proc_kill_unknown_pid_raises(tmp_path):
    k, tok = make(tmp_path)
    with pytest.raises(ValueError):
        k.invoke(tok, "proc.kill", {"pid": 999999, "signal": 0})


# --- network ---------------------------------------------------------------


def test_net_fetch_rejects_non_http_scheme(tmp_path):
    k, tok = make(tmp_path)
    with pytest.raises(ValueError):
        k.invoke(tok, "net.fetch", {"url": "file:///etc/passwd"})


def test_net_fetch_rejects_ftp_scheme(tmp_path):
    k, tok = make(tmp_path)
    with pytest.raises(ValueError):
        k.invoke(tok, "net.fetch", {"url": "ftp://example.com/x"})


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"hello axon"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence server logging
        pass


def test_net_fetch_localhost(tmp_path):
    k, tok = make(tmp_path)
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        res = k.invoke(tok, "net.fetch", {"url": f"http://127.0.0.1:{port}/"})
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)
    assert res["status"] == 200
    assert res["body"] == "hello axon"
    assert res["truncated"] is False


def test_net_fetch_truncates_body(tmp_path):
    k, tok = make(tmp_path)
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        res = k.invoke(
            tok, "net.fetch", {"url": f"http://127.0.0.1:{port}/", "max_bytes": 4}
        )
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)
    assert res["status"] == 200
    assert res["body"] == "hell"
    assert res["truncated"] is True
