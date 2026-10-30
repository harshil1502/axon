"""Regression tests for the integration-review findings.

Each test reproduces a confirmed defect and asserts the fix: directory-aware
reversibility, two-path (move/copy) snapshotting, honest undo_id, and the
gate-denial vs execution-error audit split.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axon import boot
from axon.capabilities import CapabilityError


def make(tmp_path):
    return boot(home=tmp_path / ".axon")


def _last_authorized(kernel, action):
    return [
        e["id"]
        for e in kernel.audit.tail(100)
        if e.get("action") == action and e.get("status") == "authorized"
    ][-1]


# --- finding #3: snapshot_file crashed on directories ---------------------


def test_delete_directory_is_reversible(tmp_path):
    k = make(tmp_path)
    tok = k.caps.issue("demo", ["fs.*"], path_scopes=[str(tmp_path)])
    d = tmp_path / "tree"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "f.txt").write_text("payload")

    k.invoke(tok, "fs.delete", {"path": str(d)})
    assert not d.exists()  # handler actually ran (was a no-op before the fix)

    k.undo(_last_authorized(k, "fs.delete"))
    assert (d / "sub" / "f.txt").read_text() == "payload"  # tree restored


def test_mkdir_on_existing_directory_is_idempotent(tmp_path):
    k = make(tmp_path)
    tok = k.caps.issue("demo", ["fs.*"], path_scopes=[str(tmp_path)])
    d = tmp_path / "exists"
    d.mkdir()
    # Must not raise IsADirectoryError from the snapshot step.
    k.invoke(tok, "fs.mkdir", {"path": str(d)})
    assert d.is_dir()


# --- finding #4: move/copy (src/dst) were never journaled -----------------


def test_move_is_reversible_restores_src_and_dst(tmp_path):
    k = make(tmp_path)
    tok = k.caps.issue("demo", ["fs.*"], path_scopes=[str(tmp_path)])
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("SRC")
    dst.write_text("DST-ORIGINAL")

    k.invoke(tok, "fs.move", {"src": str(src), "dst": str(dst)})
    assert not src.exists() and dst.read_text() == "SRC"

    k.undo(_last_authorized(k, "fs.move"))
    assert src.read_text() == "SRC"  # source restored
    assert dst.read_text() == "DST-ORIGINAL"  # overwrite rolled back


# --- finding #5: undo_id advertised for non-journaled mutating actions ----


def test_non_path_mutation_reports_no_undo_id(tmp_path):
    k = make(tmp_path)
    tok = k.caps.issue("demo", ["mem.*"])
    k.invoke(tok, "mem.put", {"key": "k", "value": "v"})
    ok = [
        e
        for e in k.audit.tail(20)
        if e.get("action") == "mem.put" and e.get("status") == "ok"
    ][-1]
    assert ok["undo_id"] is None  # nothing was journaled, so nothing is promised


# --- findings #1 & #2: gate-denial vs execution-error must be distinct -----


def test_handler_failure_audited_as_error_not_denied(tmp_path):
    k = make(tmp_path)
    tok = k.caps.issue("demo", ["proc.info"])
    with pytest.raises(ValueError):  # handler rejects a nonexistent pid
        k.invoke(tok, "proc.info", {"pid": "999999999"})

    recs = [e for e in k.audit.tail(20) if e.get("action") == "proc.info"]
    assert any(e["status"] == "authorized" for e in recs)
    assert any(e["status"] == "error" for e in recs)
    assert not any(e["status"] == "denied" for e in recs)  # NOT a gate denial


def test_gate_denial_audited_as_denied(tmp_path):
    k = make(tmp_path)
    tok = k.caps.issue("demo", ["sys.info"])  # proc.info NOT granted
    with pytest.raises(CapabilityError):
        k.invoke(tok, "proc.info", {"pid": "1"})

    recs = [e for e in k.audit.tail(20) if e.get("action") == "proc.info"]
    assert recs and recs[-1]["status"] == "denied"
    assert not any(e["status"] == "authorized" for e in recs)  # never ran
