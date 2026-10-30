"""Filesystem actions — typed, scoped, reversible file operations.

Registered against the kernel's registry. Every one is path-scope enforced by
the Capability Engine (path params are named ``path``/``src``/``dst`` so the
engine can bound them) and, for mutating single-path ops, snapshotted by the
Journal so the kernel can roll them back.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..registry import Registry


def register(reg: Registry) -> None:
    @reg.action(
        "fs.read",
        "Read a text file and return its contents.",
        {"path": {"type": "string", "required": True, "desc": "file to read"}},
    )
    def fs_read(path: str) -> str:
        return Path(path).expanduser().read_text()

    @reg.action(
        "fs.write",
        "Write text to a file (creates or overwrites). Reversible.",
        {
            "path": {"type": "string", "required": True, "desc": "file to write"},
            "content": {"type": "string", "required": True, "desc": "text content"},
        },
        mutating=True,
        risk="write",
    )
    def fs_write(path: str, content: str) -> str:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"wrote {len(content)} bytes to {p}"

    @reg.action(
        "fs.list",
        "List the entries of a directory.",
        {"path": {"type": "string", "required": True, "desc": "directory to list"}},
    )
    def fs_list(path: str) -> list[str]:
        return sorted(e.name for e in Path(path).expanduser().iterdir())

    @reg.action(
        "fs.append",
        "Append text to a file (creates it if absent). Reversible.",
        {
            "path": {"type": "string", "required": True, "desc": "file to append to"},
            "content": {"type": "string", "required": True, "desc": "text to append"},
        },
        mutating=True,
        risk="write",
    )
    def fs_append(path: str, content: str) -> str:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            fh.write(content)
        return f"appended {len(content)} bytes to {p}"

    @reg.action(
        "fs.delete",
        "Delete a file or directory tree.",
        {"path": {"type": "string", "required": True, "desc": "path to delete"}},
        mutating=True,
        risk="write",
    )
    def fs_delete(path: str) -> str:
        p = Path(path).expanduser()
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
            return f"deleted directory {p}"
        p.unlink()
        return f"deleted {p}"

    @reg.action(
        "fs.mkdir",
        "Create a directory (including parents). No error if it exists.",
        {"path": {"type": "string", "required": True, "desc": "directory to create"}},
        mutating=True,
        risk="write",
    )
    def fs_mkdir(path: str) -> str:
        p = Path(path).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return f"created directory {p}"

    @reg.action(
        "fs.move",
        "Move or rename a file/directory from src to dst.",
        {
            "src": {"type": "string", "required": True, "desc": "source path"},
            "dst": {"type": "string", "required": True, "desc": "destination path"},
        },
        mutating=True,
        risk="write",
    )
    def fs_move(src: str, dst: str) -> str:
        s = Path(src).expanduser()
        d = Path(dst).expanduser()
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
        return f"moved {s} -> {d}"

    @reg.action(
        "fs.copy",
        "Copy a file or directory tree from src to dst.",
        {
            "src": {"type": "string", "required": True, "desc": "source path"},
            "dst": {"type": "string", "required": True, "desc": "destination path"},
        },
        mutating=True,
        risk="write",
    )
    def fs_copy(src: str, dst: str) -> str:
        s = Path(src).expanduser()
        d = Path(dst).expanduser()
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.is_dir():
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
        return f"copied {s} -> {d}"

    @reg.action(
        "fs.stat",
        "Return metadata for a path (size, type, mtime).",
        {"path": {"type": "string", "required": True, "desc": "path to inspect"}},
    )
    def fs_stat(path: str) -> dict[str, Any]:
        p = Path(path).expanduser()
        st = p.stat()
        return {
            "path": str(p),
            "size": st.st_size,
            "is_dir": p.is_dir(),
            "is_file": p.is_file(),
            "is_symlink": p.is_symlink(),
            "mtime": st.st_mtime,
            "mode": oct(st.st_mode & 0o777),
        }

    @reg.action(
        "fs.search",
        "Find files matching a glob pattern under a directory.",
        {
            "path": {"type": "string", "required": True, "desc": "directory to search"},
            "pattern": {
                "type": "string",
                "required": True,
                "desc": "glob pattern, e.g. '*.txt' or '**/*.py'",
            },
        },
    )
    def fs_search(path: str, pattern: str) -> list[str]:
        base = Path(path).expanduser()
        base_resolved = base.resolve()
        # Keep results under the searched dir so a '..' pattern can't escape the
        # capability's path scope (which only bounds the `path` arg, not the glob).
        out = []
        for m in base.glob(pattern):
            rm = m.resolve()
            if rm == base_resolved or base_resolved in rm.parents:
                out.append(str(m))
        return sorted(out)
