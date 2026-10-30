"""Tests for the agent loop.

Imports only the stable core (`axon.boot`) and the module under test. Uses a
fake process object (duck-typed AgentProcess) and never touches the network:
the run() test forces the graceful-degrade path so it passes fully offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axon import boot
from axon.agent_loop import AgentLoop


class FakeProcess:
    """Minimal stand-in for runtime.AgentProcess (pid/token/agent/charge)."""

    def __init__(self, pid: str, token: str, agent: str) -> None:
        self.pid = pid
        self.token = token
        self.agent = agent
        self.charged = 0

    def charge(self, *, tokens: int = 0) -> None:
        self.charged += tokens


def make(tmp_path: Path):
    return boot(home=tmp_path / ".axon")


def fake_process(kernel, grants):
    token = kernel.caps.issue("tester", grants)
    return FakeProcess("pid-1", token, "tester")


def test_tool_schema_for_fs_read(tmp_path):
    k = make(tmp_path)
    proc = fake_process(k, ["fs.read", "fs.write"])
    loop = AgentLoop(k)

    tools = {t["name"]: t for t in loop._build_tools(proc)}
    assert "fs.read" in tools

    schema = tools["fs.read"]["input_schema"]
    assert schema["type"] == "object"
    assert schema["properties"]["path"]["type"] == "string"
    assert "path" in schema["required"]
    # Description is carried through from the action.
    assert tools["fs.read"]["description"]


def test_tools_filtered_by_capability(tmp_path):
    k = make(tmp_path)
    proc = fake_process(k, ["fs.read"])  # write NOT granted
    loop = AgentLoop(k)

    names = {t["name"] for t in loop._build_tools(proc)}
    assert "fs.read" in names
    assert "fs.write" not in names
    assert "sys.info" not in names  # not granted either


def test_no_params_action_has_empty_object_schema(tmp_path):
    k = make(tmp_path)
    proc = fake_process(k, ["sys.info"])
    loop = AgentLoop(k)

    tools = {t["name"]: t for t in loop._build_tools(proc)}
    schema = tools["sys.info"]["input_schema"]
    assert schema == {"type": "object", "properties": {}}


def test_optional_param_not_required(tmp_path):
    k = make(tmp_path)
    proc = fake_process(k, ["proc.list"])
    loop = AgentLoop(k)

    tools = {t["name"]: t for t in loop._build_tools(proc)}
    schema = tools["proc.list"]["input_schema"]
    assert schema["properties"]["limit"]["type"] == "integer"
    assert "required" not in schema  # limit is optional


def test_run_returns_dry_run_string_offline(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    # Force the graceful-degrade path deterministically without a network call:
    # if the SDK is installed, make client construction fail; if it isn't, the
    # import inside run() fails on its own — either way we should get a string.
    try:
        import anthropic

        def _boom(*args, **kwargs):
            raise RuntimeError("no credentials")

        monkeypatch.setattr(anthropic, "Anthropic", _boom)
    except ImportError:
        pass

    k = make(tmp_path)
    proc = fake_process(k, ["fs.*"])
    loop = AgentLoop(k)

    out = loop.run(proc, "list my files", max_turns=3)
    assert isinstance(out, str)
    assert "[dry-run]" in out
    assert "list my files" in out
    # Dry-run does no work, so nothing is charged.
    assert proc.charged == 0


def test_run_dry_run_when_sdk_missing(tmp_path, monkeypatch):
    # Simulate `import anthropic` failing regardless of what's installed.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("no module named anthropic")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    k = make(tmp_path)
    proc = fake_process(k, ["fs.read"])
    loop = AgentLoop(k)

    out = loop.run(proc, "read a file", max_turns=2)
    assert out == "[dry-run] no Claude credentials; would run: read a file"


def test_model_defaults(tmp_path):
    k = make(tmp_path)
    assert AgentLoop(k).model == "claude-opus-4-8"
    assert AgentLoop(k, model="claude-sonnet-5").model == "claude-sonnet-5"
