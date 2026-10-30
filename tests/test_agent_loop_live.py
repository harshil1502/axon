"""Live-path tests for the agent loop, with a fake Anthropic client.

The offline tests in test_agent_loop.py cover schema-building and graceful
degrade. These drive the *actual* agentic loop — tool execution through the
kernel, tool-name sanitization + reverse mapping, token charging, and the
end_turn/refusal stop branches — by injecting a scripted fake `anthropic`
module, so the flagship "Claude drives Axon" path is exercised without network.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from axon import boot
from axon.agent_loop import AgentLoop
from axon.runtime import Budget, BudgetExceeded, Runtime


class Blk:
    """A stand-in content block / response object (duck-typed)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Usage:
    def __init__(self, out):
        self.output_tokens = out


def _resp(stop, content, out):
    return Blk(stop_reason=stop, content=content, usage=_Usage(out))


class _FakeMessages:
    def __init__(self, script):
        self.script = script
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return self.script[len(self.calls) - 1]


class _FakeClient:
    def __init__(self, script):
        self.messages = _FakeMessages(script)


def _install_fake_anthropic(monkeypatch, script, holder):
    mod = types.ModuleType("anthropic")

    def _factory(*a, **k):
        client = _FakeClient(script)
        holder.append(client)
        return client

    mod.Anthropic = _factory
    monkeypatch.setitem(sys.modules, "anthropic", mod)


class FakeProcess:
    def __init__(self, token):
        self.pid = "p1"
        self.token = token
        self.agent = "demo"
        self.charged = 0

    def charge(self, *, tokens: int = 0, actions: int = 0, usd: float = 0.0) -> None:
        self.charged += tokens


def test_live_loop_executes_real_tool(tmp_path: Path, monkeypatch):
    k = boot(home=tmp_path / ".axon")
    proc = FakeProcess(k.caps.issue("demo", ["sys.info"]))

    # Turn 1: model calls the (sanitized) tool. Turn 2: model finishes.
    script = [
        _resp(
            "tool_use",
            [Blk(type="tool_use", name="sys_info", input={}, id="t1")],
            5,
        ),
        _resp("end_turn", [Blk(type="text", text="done: reported the OS")], 3),
    ]
    holder: list = []
    _install_fake_anthropic(monkeypatch, script, holder)

    out = AgentLoop(k).run(proc, "what OS am I on?", max_turns=5)

    # Final text returned, and both turns' output tokens were charged.
    assert out == "done: reported the OS"
    assert proc.charged == 8

    # The real sys.info action ran through the audited kernel chokepoint.
    assert any(
        e.get("action") == "sys.info" and e.get("status") == "ok"
        for e in k.audit.tail(20)
    )

    calls = holder[0].messages.calls
    # Tools were sent with SANITIZED names (no dots — Claude's regex).
    tool_names = {t["name"] for t in calls[0]["tools"]}
    assert "sys_info" in tool_names
    assert all("." not in n for n in tool_names)
    # A tool_result for the call was fed back on the second request.
    second_msgs = calls[1]["messages"]
    assert any(
        m["role"] == "user"
        and isinstance(m["content"], list)
        and any(
            b.get("type") == "tool_result" and b.get("tool_use_id") == "t1"
            for b in m["content"]
        )
        for m in second_msgs
    )


def test_live_loop_reports_tool_error_to_model(tmp_path: Path, monkeypatch):
    """A failing action comes back as an is_error tool_result, not a crash."""
    k = boot(home=tmp_path / ".axon")
    # Grant fs.read but the file won't exist -> handler raises -> is_error result.
    proc = FakeProcess(k.caps.issue("demo", ["fs.read"]))
    missing = str(tmp_path / "nope.txt")

    script = [
        _resp(
            "tool_use",
            [Blk(type="tool_use", name="fs_read", input={"path": missing}, id="t9")],
            4,
        ),
        _resp("end_turn", [Blk(type="text", text="could not read it")], 2),
    ]
    holder: list = []
    _install_fake_anthropic(monkeypatch, script, holder)

    out = AgentLoop(k).run(proc, "read the file", max_turns=5)
    assert out == "could not read it"

    tool_result = next(
        b
        for m in holder[0].messages.calls[1]["messages"]
        if m["role"] == "user" and isinstance(m["content"], list)
        for b in m["content"]
        if b.get("tool_use_id") == "t9"
    )
    assert tool_result.get("is_error") is True


def test_live_loop_handles_refusal(tmp_path: Path, monkeypatch):
    k = boot(home=tmp_path / ".axon")
    proc = FakeProcess(k.caps.issue("demo", ["sys.info"]))
    script = [_resp("refusal", [Blk(type="text", text="")], 1)]
    holder: list = []
    _install_fake_anthropic(monkeypatch, script, holder)

    out = AgentLoop(k).run(proc, "do something disallowed", max_turns=5)
    assert "refusal" in out.lower()


def test_live_loop_enforces_action_budget(tmp_path: Path, monkeypatch):
    """A real budgeted process is throttled by the loop, not just by Runtime."""
    k = boot(home=tmp_path / ".axon")
    proc = Runtime(k).spawn("demo", ["sys.info"], budget=Budget(actions=1))

    # One turn, two tool calls -> the second exceeds the actions budget.
    script = [
        _resp(
            "tool_use",
            [
                Blk(type="tool_use", name="sys_info", input={}, id="t1"),
                Blk(type="tool_use", name="sys_info", input={}, id="t2"),
            ],
            5,
        )
    ]
    holder: list = []
    _install_fake_anthropic(monkeypatch, script, holder)

    with pytest.raises(BudgetExceeded):
        AgentLoop(k).run(proc, "info twice", max_turns=3)
