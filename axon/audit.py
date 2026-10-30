"""Audit log — every agent action is logged, attributable, and replayable.

Append-only JSONL. In macOS/Windows, an agent's actions are indistinguishable
from the user's. Here, every call carries the agent identity, the capability id
that authorized it, the params, and the outcome. Trust through transparency.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterator


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, entry: dict[str, Any]) -> str:
        entry_id = f"{int(time.time() * 1000):x}-{id(entry) & 0xFFFF:04x}"
        entry = {"id": entry_id, "ts": time.time(), **entry}
        with self.path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        return entry_id

    def tail(self, n: int = 20) -> list[dict[str, Any]]:
        return list(self._iter())[-n:]

    def _iter(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        for line in self.path.read_text().splitlines():
            if line.strip():
                yield json.loads(line)
