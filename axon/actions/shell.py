"""Shell action — the deliberate escape hatch to the host shell.

Unlike the typed filesystem/process/network actions, ``shell.exec`` runs an
arbitrary command, so it is risk="dangerous": the Policy Engine gates it behind
explicit approval. Every run is bounded by a hard timeout and returns structured
output ({stdout, stderr, code}) rather than leaking a live process to the agent.
"""

from __future__ import annotations

import subprocess
from typing import Any

from ..registry import Registry

DEFAULT_TIMEOUT = 30.0


def register(reg: Registry) -> None:
    @reg.action(
        "shell.exec",
        "Run a shell command with a hard timeout; returns {stdout, stderr, code}.",
        {
            "command": {
                "type": "string",
                "required": True,
                "desc": "command line to execute via the shell",
            },
            "timeout": {
                "type": "number",
                "required": False,
                "desc": f"seconds before the command is killed (default: {DEFAULT_TIMEOUT})",
            },
        },
        mutating=True,
        risk="dangerous",
    )
    def shell_exec(command: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "code": proc.returncode,
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "stdout": exc.stdout or "",
                "stderr": f"timed out after {timeout}s",
                "code": 124,
            }
