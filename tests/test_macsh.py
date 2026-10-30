"""macOS + shell action tests — exercised through a real Kernel + capability.

macOS-only helpers (pbcopy/pbpaste/osascript/open) are guarded with
``shutil.which`` + ``pytest.skip`` so the suite passes on non-mac hosts. The
shell action is portable and always exercised.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from axon.kernel import Kernel
from axon.actions import macos, shell


def make(tmp_path: Path):
    """Fresh kernel with the macos + shell actions registered + a broad token."""
    k = Kernel(home=tmp_path / ".axon")
    macos.register(k.registry)
    shell.register(k.registry)
    tok = k.caps.issue("tester", ["app.*", "clipboard.*", "notify", "shell.*"])
    return k, tok


def test_registration_covers_all_actions(tmp_path):
    k, _ = make(tmp_path)
    names = {a.name for a in k.registry.list("*")}
    assert {
        "app.open",
        "app.quit",
        "notify",
        "clipboard.read",
        "clipboard.write",
        "shell.exec",
    } <= names


def test_risk_flags(tmp_path):
    k, _ = make(tmp_path)
    for name in ("app.open", "app.quit", "shell.exec"):
        assert k.registry.get(name).risk == "dangerous"
    for name in ("notify", "clipboard.write"):
        a = k.registry.get(name)
        assert a.risk == "write" and a.mutating is True
    assert k.registry.get("clipboard.read").risk == "read"


def test_clipboard_roundtrip(tmp_path):
    if shutil.which("pbcopy") is None or shutil.which("pbpaste") is None:
        pytest.skip("pbcopy/pbpaste unavailable (not macOS)")
    k, tok = make(tmp_path)
    payload = "axon clipboard roundtrip 123"
    k.invoke(tok, "clipboard.write", {"text": payload})
    assert k.invoke(tok, "clipboard.read", {}) == payload


def test_shell_exec_echo(tmp_path):
    k, tok = make(tmp_path)
    result = k.invoke(tok, "shell.exec", {"command": "echo hi"})
    assert result["code"] == 0
    assert "hi" in result["stdout"]


def test_shell_exec_nonzero_code(tmp_path):
    k, tok = make(tmp_path)
    result = k.invoke(tok, "shell.exec", {"command": "exit 3"})
    assert result["code"] == 3


def test_shell_exec_timeout(tmp_path):
    k, tok = make(tmp_path)
    result = k.invoke(tok, "shell.exec", {"command": "sleep 5", "timeout": 0.2})
    assert result["code"] == 124
    assert "timed out" in result["stderr"]
