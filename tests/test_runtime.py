"""Runtime tests — process supervision, budgets, and reaping.

Exercises the runtime over a real kernel with system actions registered, so the
capability/budget path is end-to-end rather than mocked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axon.kernel import Kernel
from axon.actions import system
from axon.capabilities import CapabilityError
from axon.runtime import AgentProcess, Budget, BudgetExceeded, Runtime, Usage


def make_runtime(tmp_path: Path) -> Runtime:
    kernel = Kernel(home=tmp_path / ".axon")
    system.register(kernel.registry)
    return Runtime(kernel)


def test_spawn_and_invoke_granted_action(tmp_path):
    rt = make_runtime(tmp_path)
    proc = rt.spawn("worker", ["sys.info"])
    assert proc.status == "running"
    assert proc.token  # a capability was issued
    info = rt.invoke(proc.pid, "sys.info", {})
    assert "system" in info
    assert proc.usage.actions == 1


def test_action_budget_trips_budget_exceeded(tmp_path):
    rt = make_runtime(tmp_path)
    proc = rt.spawn("worker", ["sys.info"], budget=Budget(actions=2))
    rt.invoke(proc.pid, "sys.info", {})  # 1
    rt.invoke(proc.pid, "sys.info", {})  # 2
    with pytest.raises(BudgetExceeded):
        rt.invoke(proc.pid, "sys.info", {})  # 3 -> over budget
    assert proc.usage.actions == 3


def test_charge_enforces_token_budget(tmp_path):
    rt = make_runtime(tmp_path)
    proc = rt.spawn("worker", ["sys.info"], budget=Budget(tokens=100))
    proc.charge(tokens=100)  # exactly at limit is fine
    with pytest.raises(BudgetExceeded):
        proc.charge(tokens=1)


def test_kill_revokes_capability(tmp_path):
    rt = make_runtime(tmp_path)
    proc = rt.spawn("worker", ["sys.info"])
    rt.invoke(proc.pid, "sys.info", {})  # works while alive
    rt.kill(proc.pid)
    assert proc.status == "killed"
    with pytest.raises(CapabilityError):
        rt.invoke(proc.pid, "sys.info", {})  # revoked token is dead


def test_list_and_get(tmp_path):
    rt = make_runtime(tmp_path)
    a = rt.spawn("a", ["sys.info"])
    b = rt.spawn("b", ["sys.info"])
    pids = {p.pid for p in rt.list()}
    assert pids == {a.pid, b.pid}
    assert rt.get(a.pid) is a
    with pytest.raises(KeyError):
        rt.get("nonexistent")


def test_unknown_pid_invoke_raises(tmp_path):
    rt = make_runtime(tmp_path)
    with pytest.raises(KeyError):
        rt.invoke("nope", "sys.info", {})


def test_usage_and_budget_defaults():
    u = Usage()
    assert u.tokens == 0 and u.usd == 0.0 and u.actions == 0
    b = Budget()
    assert b.tokens is None and b.actions is None
    # An unbudgeted process never trips on charge.
    proc = AgentProcess(pid="p", agent="a", token="t", budget=b, usage=u)
    proc.charge(tokens=10_000, usd=5.0, actions=99)  # no raise
