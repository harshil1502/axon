"""System actions — read-only introspection of the host.

Proves that non-filesystem capabilities register through the same typed,
audited path. On the eventual bootable OS these bind to native syscalls; on the
macOS layer they wrap portable Python.
"""

from __future__ import annotations

import os
import platform

from ..registry import Registry


def register(reg: Registry) -> None:
    @reg.action("sys.info", "Return basic host/OS information.", {})
    def sys_info() -> dict[str, str]:
        return {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cwd": os.getcwd(),
        }

    @reg.action(
        "proc.list",
        "List running processes (pid + name). Read-only.",
        {"limit": {"type": "integer", "required": False, "desc": "max rows"}},
    )
    def proc_list(limit: int = 20) -> list[dict[str, str]]:
        # Portable, dependency-free: parse `ps`.
        import subprocess

        out = subprocess.run(
            ["ps", "-axo", "pid,comm"], capture_output=True, text=True, check=True
        ).stdout.splitlines()[1:]
        rows = []
        for line in out[:limit]:
            parts = line.strip().split(None, 1)
            if len(parts) == 2:
                rows.append({"pid": parts[0], "name": parts[1]})
        return rows
