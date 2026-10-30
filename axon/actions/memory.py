"""Memory actions — expose the MemoryStore through the typed, audited path.

An agent reads/writes its own durable memory the same way it touches the
filesystem: as capability-gated Actions. Writes (mem.put/mem.delete) are
risk="write" so the Policy Engine can rate-limit or gate them.
"""

from __future__ import annotations

from typing import Any

from ..memory import MemoryStore
from ..registry import Registry


def register(reg: Registry, store: MemoryStore) -> None:
    @reg.action(
        "mem.put",
        "Store a JSON-serializable value under a key in a namespace.",
        {
            "key": {"type": "string", "required": True, "desc": "memory key"},
            "value": {
                "type": "string",
                "required": True,
                "desc": "JSON-serializable value",
            },
            "namespace": {
                "type": "string",
                "required": False,
                "desc": "key space (default: 'default')",
            },
        },
        mutating=True,
        risk="write",
    )
    def mem_put(key: str, value: Any, namespace: str = "default") -> str:
        store.put(key, value, namespace=namespace)
        return f"stored '{key}' in namespace '{namespace}'"

    @reg.action(
        "mem.get",
        "Retrieve a value by key, or null if absent.",
        {
            "key": {"type": "string", "required": True, "desc": "memory key"},
            "namespace": {
                "type": "string",
                "required": False,
                "desc": "key space (default: 'default')",
            },
        },
    )
    def mem_get(key: str, namespace: str = "default") -> Any:
        return store.get(key, namespace=namespace)

    @reg.action(
        "mem.query",
        "Return every key/value pair whose key starts with a prefix.",
        {
            "prefix": {
                "type": "string",
                "required": False,
                "desc": "key prefix (default: '' = all)",
            },
            "namespace": {
                "type": "string",
                "required": False,
                "desc": "key space (default: 'default')",
            },
        },
    )
    def mem_query(prefix: str = "", namespace: str = "default") -> dict[str, Any]:
        return store.query(prefix, namespace=namespace)

    @reg.action(
        "mem.delete",
        "Delete a key. Returns True if it existed.",
        {
            "key": {"type": "string", "required": True, "desc": "memory key"},
            "namespace": {
                "type": "string",
                "required": False,
                "desc": "key space (default: 'default')",
            },
        },
        mutating=True,
        risk="write",
    )
    def mem_delete(key: str, namespace: str = "default") -> bool:
        return store.delete(key, namespace=namespace)
