"""Agent memory — durable, namespaced key/value storage.

A capability an agent can *keep*: notes, learned facts, intermediate results
that must survive a process restart. Values are JSON-serializable and land on
disk under ``home/memory/`` (one file per namespace), so a fresh MemoryStore
pointed at the same home sees everything a prior one wrote. Namespaces give
agents (or tasks) isolated key spaces that never collide.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote


class MemoryStore:
    """Persistent, namespaced JSON key/value store.

    Durable under ``home/memory/`` — each namespace is a single JSON file, so
    the store is safe across process restarts and cheap to inspect by hand.
    """

    def __init__(self, home: Path) -> None:
        self.root = Path(home) / "memory"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: str) -> Path:
        # Encode so arbitrary namespace strings map to safe, reversible filenames.
        return self.root / f"{quote(namespace, safe='')}.json"

    def _load(self, namespace: str) -> dict[str, Any]:
        p = self._path(namespace)
        if not p.exists():
            return {}
        return json.loads(p.read_text())

    def _save(self, namespace: str, data: dict[str, Any]) -> None:
        self._path(namespace).write_text(json.dumps(data, sort_keys=True, indent=2))

    def put(
        self,
        key: str,
        value: Any,
        *,
        namespace: str = "default",
        agent: str | None = None,
    ) -> None:
        """Store a JSON-serializable value under key. Overwrites any prior value."""
        data = self._load(namespace)
        data[key] = value
        self._save(namespace, data)

    def get(self, key: str, *, namespace: str = "default") -> Any:
        """Return the value for key, or None if absent."""
        return self._load(namespace).get(key)

    def query(self, prefix: str = "", *, namespace: str = "default") -> dict[str, Any]:
        """Return {key: value} for every key in the namespace starting with prefix."""
        return {k: v for k, v in self._load(namespace).items() if k.startswith(prefix)}

    def delete(self, key: str, *, namespace: str = "default") -> bool:
        """Remove key. Returns True if it existed, False otherwise."""
        data = self._load(namespace)
        if key not in data:
            return False
        del data[key]
        self._save(namespace, data)
        return True

    def namespaces(self) -> list[str]:
        """List every namespace that currently holds data."""
        return sorted(unquote(p.stem) for p in self.root.glob("*.json"))
