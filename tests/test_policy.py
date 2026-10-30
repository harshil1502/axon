"""Policy Engine contract tests.

Pin the three orthogonal gates the kernel enforces after authorization:
dangerous actions need one-shot approval, per-(agent, action) sliding windows
throttle bursts, denylisted globs always block, and read actions flow freely.
Config persists under ``home/policy.json``.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from axon.capabilities import Capability
from axon.policy import PolicyEngine, PolicyError
from axon.registry import Action


def cap(agent: str = "worker") -> Capability:
    now = time.time()
    return Capability(
        id="cap0",
        agent=agent,
        grants=["*"],
        path_scopes=[],
        issued_at=now,
        expires_at=now + 3600,
    )


def act(name: str, risk: str = "read") -> Action:
    return Action(
        name=name,
        description=name,
        params={},
        handler=lambda **kw: None,
        mutating=risk != "read",
        risk=risk,
    )


def engine(tmp_path: Path) -> PolicyEngine:
    return PolicyEngine(tmp_path / ".axon")


def test_read_actions_pass_freely(tmp_path):
    p = engine(tmp_path)
    a = act("fs.read", risk="read")
    # Repeatedly, with no config at all, read actions never trip policy.
    for _ in range(50):
        p.check(cap(), a, {"path": "/x"})


def test_dangerous_needs_approval(tmp_path):
    p = engine(tmp_path)
    a = act("shell.exec", risk="dangerous")
    assert p.requires_approval(cap(), a, {}) is True
    with pytest.raises(PolicyError, match="approval required"):
        p.check(cap(), a, {"command": "rm -rf /"})


def test_pre_approve_consumed_once(tmp_path):
    p = engine(tmp_path)
    a = act("shell.exec", risk="dangerous")
    p.pre_approve("worker", "shell.exec")
    assert p.requires_approval(cap(), a, {}) is False
    # First call is allowed by the one-shot approval.
    p.check(cap(), a, {"command": "ls"})
    # Approval was consumed — the next call requires a fresh one.
    assert p.requires_approval(cap(), a, {}) is True
    with pytest.raises(PolicyError, match="approval required"):
        p.check(cap(), a, {"command": "ls"})


def test_pre_approve_is_per_agent(tmp_path):
    p = engine(tmp_path)
    a = act("app.open", risk="dangerous")
    p.pre_approve("alice", "app.open")
    # bob has no approval even though alice does.
    with pytest.raises(PolicyError, match="approval required"):
        p.check(cap("bob"), a, {})
    # alice's approval still stands and lets exactly one call through.
    p.check(cap("alice"), a, {})


def test_rate_limit_trips(tmp_path):
    p = engine(tmp_path)
    p.set_rate_limit("net.fetch", max_calls=3, per_seconds=60)
    a = act("net.fetch", risk="read")
    for _ in range(3):
        p.check(cap(), a, {"url": "http://x"})
    with pytest.raises(PolicyError, match="rate limit exceeded"):
        p.check(cap(), a, {"url": "http://x"})


def test_rate_limit_is_per_agent(tmp_path):
    p = engine(tmp_path)
    p.set_rate_limit("net.*", max_calls=1, per_seconds=60)
    a = act("net.fetch", risk="read")
    p.check(cap("alice"), a, {})
    # alice is now at her limit...
    with pytest.raises(PolicyError):
        p.check(cap("alice"), a, {})
    # ...but bob's window is independent.
    p.check(cap("bob"), a, {})


def test_rate_limit_window_slides(tmp_path):
    p = engine(tmp_path)
    p.set_rate_limit("net.fetch", max_calls=1, per_seconds=0.25)
    a = act("net.fetch", risk="read")
    p.check(cap(), a, {})
    with pytest.raises(PolicyError):
        p.check(cap(), a, {})
    # After the window elapses the old call ages out and a new one is allowed.
    time.sleep(0.3)
    p.check(cap(), a, {})


def test_denylist_blocks(tmp_path):
    p = engine(tmp_path)
    p.deny("shell.*")
    a = act("shell.exec", risk="dangerous")
    with pytest.raises(PolicyError, match="denylist"):
        p.check(cap(), a, {"command": "ls"})


def test_denylist_blocks_even_read_and_approved(tmp_path):
    p = engine(tmp_path)
    p.deny("fs.read")
    a = act("fs.read", risk="read")
    # A harmless read still hits the hard kill-switch.
    with pytest.raises(PolicyError, match="denylist"):
        p.check(cap(), a, {"path": "/x"})


def test_config_persists_across_instances(tmp_path):
    home = tmp_path / ".axon"
    p1 = PolicyEngine(home)
    p1.deny("proc.kill")
    p1.set_rate_limit("net.fetch", max_calls=2, per_seconds=30)
    assert (home / "policy.json").exists()

    # A fresh engine over the same home reloads denylist + limits.
    p2 = PolicyEngine(home)
    with pytest.raises(PolicyError, match="denylist"):
        p2.check(cap(), act("proc.kill", risk="dangerous"), {})
    a = act("net.fetch", risk="read")
    p2.check(cap(), a, {})
    p2.check(cap(), a, {})
    with pytest.raises(PolicyError, match="rate limit"):
        p2.check(cap(), a, {})
