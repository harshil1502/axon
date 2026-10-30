"""Core contract tests for the Axon kernel.

These pin the agent-native guarantees: capability enforcement, path scoping,
expiry, revocation, schema validation, audit, and reversibility.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from axon import boot
from axon.capabilities import CapabilityError


def make(tmp_path: Path):
    return boot(home=tmp_path / ".axon")


def test_granted_action_runs(tmp_path):
    k = make(tmp_path)
    tok = k.caps.issue("tester", ["sys.info"])
    info = k.invoke(tok, "sys.info", {})
    assert "system" in info


def test_ungranted_action_denied(tmp_path):
    k = make(tmp_path)
    tok = k.caps.issue("tester", ["sys.info"])  # not granted fs.read
    with pytest.raises(CapabilityError):
        k.invoke(tok, "fs.read", {"path": str(tmp_path / "x")})


def test_path_scope_enforced(tmp_path):
    k = make(tmp_path)
    allowed = tmp_path / "sandbox"
    allowed.mkdir()
    tok = k.caps.issue("tester", ["fs.*"], path_scopes=[str(allowed)])
    # inside scope: ok
    k.invoke(tok, "fs.write", {"path": str(allowed / "a.txt"), "content": "hi"})
    # outside scope: denied
    with pytest.raises(CapabilityError):
        k.invoke(
            tok, "fs.write", {"path": str(tmp_path / "escape.txt"), "content": "x"}
        )


def test_expiry(tmp_path):
    k = make(tmp_path)
    tok = k.caps.issue("tester", ["sys.info"], ttl_seconds=-1)
    with pytest.raises(CapabilityError):
        k.invoke(tok, "sys.info", {})


def test_revocation(tmp_path):
    k = make(tmp_path)
    tok = k.caps.issue("tester", ["sys.info"])
    cap = k.caps.verify(tok)
    k.caps.revoke(cap.id)
    with pytest.raises(CapabilityError):
        k.invoke(tok, "sys.info", {})


def test_forged_token_rejected(tmp_path):
    k = make(tmp_path)
    tok = k.caps.issue("tester", ["sys.info"])
    forged = tok[:-4] + "0000"
    with pytest.raises(CapabilityError):
        k.invoke(forged, "sys.info", {})


def test_schema_validation(tmp_path):
    k = make(tmp_path)
    tok = k.caps.issue("tester", ["fs.read"])
    with pytest.raises(ValueError):
        k.invoke(tok, "fs.read", {})  # missing required 'path'


def test_reversibility_restores_prior_contents(tmp_path):
    k = make(tmp_path)
    target = tmp_path / "data.txt"
    target.write_text("ORIGINAL")
    tok = k.caps.issue("tester", ["fs.write"], path_scopes=[str(tmp_path)])
    k.invoke(tok, "fs.write", {"path": str(target), "content": "OVERWRITTEN"})
    assert target.read_text() == "OVERWRITTEN"
    # find the mutating audit entry and undo it
    undo_id = next(
        e["id"]
        for e in k.audit.tail(10)
        if e.get("action") == "fs.write" and e.get("status") == "authorized"
    )
    k.undo(undo_id)
    assert target.read_text() == "ORIGINAL"


def test_reversibility_removes_new_file(tmp_path):
    k = make(tmp_path)
    target = tmp_path / "fresh.txt"
    tok = k.caps.issue("tester", ["fs.write"], path_scopes=[str(tmp_path)])
    k.invoke(tok, "fs.write", {"path": str(target), "content": "new"})
    assert target.exists()
    undo_id = next(
        e["id"]
        for e in k.audit.tail(10)
        if e.get("action") == "fs.write" and e.get("status") == "authorized"
    )
    k.undo(undo_id)
    assert not target.exists()
