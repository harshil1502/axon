"""Process actions — inspect and signal OS processes through the typed path.

Portable and dependency-free: everything wraps the POSIX ``ps`` tool and the
``kill`` syscall (via :func:`os.kill`), so there is no ``psutil`` requirement.
``proc.kill`` is flagged ``risk="dangerous"`` — the Policy Engine gates it behind
an explicit approval.
"""

from __future__ import annotations

import os
import signal as _signal
import subprocess
from typing import Any

from ..registry import Registry

# Fields pulled for a single process, in order. ``comm`` is last because it can
# contain spaces (parsed with a bounded split).
_INFO_FIELDS = ("pid", "ppid", "user", "%cpu", "%mem", "rss", "stat", "comm")


def register(reg: Registry) -> None:
    @reg.action(
        "proc.info",
        "Return metadata for a single process id (cpu, mem, state, name).",
        {"pid": {"type": "integer", "required": True, "desc": "process id"}},
    )
    def proc_info(pid: int) -> dict[str, Any]:
        spec = ",".join(
            f"{f}=" for f in _INFO_FIELDS
        )  # empty '=' header -> no header row
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", spec],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if not out:
            raise ValueError(f"proc.info: no such process: {pid}")
        parts = out.split(None, len(_INFO_FIELDS) - 1)
        row = dict(zip(_INFO_FIELDS, parts))
        return {
            "pid": int(row["pid"]),
            "ppid": int(row["ppid"]),
            "user": row["user"],
            "cpu": float(row["%cpu"]),
            "mem": float(row["%mem"]),
            "rss": int(row["rss"]),
            "stat": row["stat"],
            "name": row.get("comm", ""),
        }

    @reg.action(
        "proc.tree",
        "Return the process tree as nested {pid, ppid, name, children} nodes.",
        {},
    )
    def proc_tree() -> list[dict[str, Any]]:
        out = subprocess.run(
            ["ps", "-axo", "pid,ppid,comm"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()[1:]
        nodes: dict[int, dict[str, Any]] = {}
        edges: list[tuple[int, int]] = []
        for line in out:
            parts = line.strip().split(None, 2)
            if len(parts) < 2:
                continue
            pid, ppid = int(parts[0]), int(parts[1])
            name = parts[2] if len(parts) == 3 else ""
            nodes[pid] = {"pid": pid, "ppid": ppid, "name": name, "children": []}
            edges.append((ppid, pid))
        roots: list[dict[str, Any]] = []
        for ppid, pid in edges:
            child = nodes[pid]
            parent = nodes.get(ppid)
            if parent is not None and ppid != pid:
                parent["children"].append(child)
            else:
                roots.append(child)
        return roots

    @reg.action(
        "proc.kill",
        "Send a signal to a process (default SIGTERM). Dangerous.",
        {
            "pid": {"type": "integer", "required": True, "desc": "process id"},
            "signal": {
                "type": "integer",
                "required": False,
                "desc": "signal number (default 15/SIGTERM; 0 only checks existence)",
            },
        },
        risk="dangerous",
    )
    def proc_kill(pid: int, signal: int = _signal.SIGTERM) -> str:
        try:
            os.kill(int(pid), int(signal))
        except ProcessLookupError as exc:
            raise ValueError(f"proc.kill: no such process: {pid}") from exc
        except PermissionError as exc:
            raise ValueError(f"proc.kill: not permitted to signal {pid}") from exc
        return f"sent signal {int(signal)} to pid {pid}"
