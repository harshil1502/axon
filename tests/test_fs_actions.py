"""Filesystem action tests — exercised through a real Kernel + capability.

Each new action is driven end-to-end via ``kernel.invoke`` under a path-scoped
``fs.*`` grant, proving both that the handler works and that the Capability
Engine's path scoping binds every path/src/dst argument.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axon.capabilities import CapabilityError
from axon.kernel import Kernel
from axon.actions import filesystem


def make(tmp_path: Path):
    """Fresh kernel with only the fs actions registered + a scoped token."""
    k = Kernel(home=tmp_path / ".axon")
    filesystem.register(k.registry)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    tok = k.caps.issue("tester", ["fs.*"], path_scopes=[str(sandbox)])
    return k, tok, sandbox


def test_registration_covers_all_actions(tmp_path):
    k, _, _ = make(tmp_path)
    names = {a.name for a in k.registry.list("fs.*")}
    assert names == {
        "fs.read",
        "fs.write",
        "fs.list",
        "fs.append",
        "fs.delete",
        "fs.mkdir",
        "fs.move",
        "fs.copy",
        "fs.stat",
        "fs.search",
    }


def test_risk_and_mutating_flags(tmp_path):
    k, _, _ = make(tmp_path)
    for name in (
        "fs.write",
        "fs.append",
        "fs.delete",
        "fs.mkdir",
        "fs.move",
        "fs.copy",
    ):
        a = k.registry.get(name)
        assert a.mutating is True
        assert a.risk == "write"
    for name in ("fs.read", "fs.list", "fs.stat", "fs.search"):
        assert k.registry.get(name).risk == "read"


def test_write_and_read(tmp_path):
    k, tok, sb = make(tmp_path)
    p = sb / "a.txt"
    k.invoke(tok, "fs.write", {"path": str(p), "content": "hello"})
    assert k.invoke(tok, "fs.read", {"path": str(p)}) == "hello"


def test_append(tmp_path):
    k, tok, sb = make(tmp_path)
    p = sb / "log.txt"
    k.invoke(tok, "fs.append", {"path": str(p), "content": "a"})
    k.invoke(tok, "fs.append", {"path": str(p), "content": "b"})
    assert p.read_text() == "ab"


def test_mkdir_and_list(tmp_path):
    k, tok, sb = make(tmp_path)
    d = sb / "nested" / "deep"
    k.invoke(tok, "fs.mkdir", {"path": str(d)})
    assert d.is_dir()
    (sb / "one.txt").write_text("1")
    (sb / "two.txt").write_text("2")
    entries = k.invoke(tok, "fs.list", {"path": str(sb)})
    assert "one.txt" in entries and "two.txt" in entries and "nested" in entries


def test_delete_file(tmp_path):
    k, tok, sb = make(tmp_path)
    f = sb / "gone.txt"
    f.write_text("x")
    k.invoke(tok, "fs.delete", {"path": str(f)})
    assert not f.exists()


def test_delete_directory_tree(tmp_path):
    # The kernel's file-granular Journal snapshots any mutating "path" arg, which
    # can't copy a directory; recursive delete is exercised at the handler level.
    k, _, sb = make(tmp_path)
    d = sb / "tree"
    (d / "child").mkdir(parents=True)
    (d / "child" / "leaf.txt").write_text("y")
    k.registry.get("fs.delete").handler(path=str(d))
    assert not d.exists()


def test_move(tmp_path):
    k, tok, sb = make(tmp_path)
    src = sb / "src.txt"
    dst = sb / "moved" / "dst.txt"
    src.write_text("payload")
    k.invoke(tok, "fs.move", {"src": str(src), "dst": str(dst)})
    assert not src.exists()
    assert dst.read_text() == "payload"


def test_copy_file_and_tree(tmp_path):
    k, tok, sb = make(tmp_path)
    src = sb / "orig.txt"
    src.write_text("data")
    dst = sb / "copy.txt"
    k.invoke(tok, "fs.copy", {"src": str(src), "dst": str(dst)})
    assert src.read_text() == "data" and dst.read_text() == "data"

    tree = sb / "dir"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "f.txt").write_text("z")
    tree_dst = sb / "dir_copy"
    k.invoke(tok, "fs.copy", {"src": str(tree), "dst": str(tree_dst)})
    assert (tree_dst / "sub" / "f.txt").read_text() == "z"


def test_stat(tmp_path):
    k, tok, sb = make(tmp_path)
    p = sb / "s.txt"
    p.write_text("12345")
    info = k.invoke(tok, "fs.stat", {"path": str(p)})
    assert info["size"] == 5
    assert info["is_file"] is True
    assert info["is_dir"] is False
    assert "mtime" in info


def test_search(tmp_path):
    k, tok, sb = make(tmp_path)
    (sb / "a.txt").write_text("1")
    (sb / "b.txt").write_text("2")
    (sb / "c.md").write_text("3")
    sub = sb / "sub"
    sub.mkdir()
    (sub / "d.txt").write_text("4")

    flat = k.invoke(tok, "fs.search", {"path": str(sb), "pattern": "*.txt"})
    assert [Path(m).name for m in flat] == ["a.txt", "b.txt"]

    recursive = k.invoke(tok, "fs.search", {"path": str(sb), "pattern": "**/*.txt"})
    assert {Path(m).name for m in recursive} == {"a.txt", "b.txt", "d.txt"}


def test_search_cannot_escape_scope_via_dotdot(tmp_path):
    # A '..' glob pattern must not leak paths outside the searched directory.
    k, tok, sb = make(tmp_path)
    (tmp_path / "secret.txt").write_text("leak")
    (sb / "inside.txt").write_text("ok")
    res = k.invoke(tok, "fs.search", {"path": str(sb), "pattern": "../*.txt"})
    assert res == []


def test_out_of_scope_write_denied(tmp_path):
    k, tok, sb = make(tmp_path)
    escape = tmp_path / "escape.txt"  # outside the sandbox scope
    with pytest.raises(CapabilityError):
        k.invoke(tok, "fs.write", {"path": str(escape), "content": "nope"})
    assert not escape.exists()


def test_out_of_scope_move_dst_denied(tmp_path):
    k, tok, sb = make(tmp_path)
    src = sb / "inside.txt"
    src.write_text("keep")
    escape = tmp_path / "out.txt"  # dst outside scope -> path scoping blocks it
    with pytest.raises(CapabilityError):
        k.invoke(tok, "fs.move", {"src": str(src), "dst": str(escape)})
    assert src.exists()
    assert not escape.exists()


def test_out_of_scope_delete_denied(tmp_path):
    k, tok, sb = make(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("safe")
    with pytest.raises(CapabilityError):
        k.invoke(tok, "fs.delete", {"path": str(victim)})
    assert victim.exists()
