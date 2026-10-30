"""Tests for the durable, namespaced MemoryStore and its mem.* actions.

Pins the memory contract: put/get roundtrip, durability across a fresh store,
namespace isolation, prefix queries, deletion, and the actions running through
the kernel's capability-gated invoke path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axon.actions import memory as memory_actions
from axon.capabilities import CapabilityError
from axon.kernel import Kernel
from axon.memory import MemoryStore


def test_put_get_roundtrip(tmp_path):
    store = MemoryStore(tmp_path)
    store.put("greeting", {"text": "hi", "n": 3})
    assert store.get("greeting") == {"text": "hi", "n": 3}
    assert store.get("missing") is None


def test_persists_across_fresh_store(tmp_path):
    store = MemoryStore(tmp_path)
    store.put("k", [1, 2, 3])
    store.put("who", "harshil", namespace="profile")

    fresh = MemoryStore(tmp_path)
    assert fresh.get("k") == [1, 2, 3]
    assert fresh.get("who", namespace="profile") == "harshil"


def test_namespace_isolation(tmp_path):
    store = MemoryStore(tmp_path)
    store.put("x", "in-a", namespace="a")
    store.put("x", "in-b", namespace="b")
    assert store.get("x", namespace="a") == "in-a"
    assert store.get("x", namespace="b") == "in-b"
    assert store.get("x", namespace="c") is None
    assert store.namespaces() == ["a", "b"]


def test_query_by_prefix(tmp_path):
    store = MemoryStore(tmp_path)
    store.put("user:1", "alice")
    store.put("user:2", "bob")
    store.put("session:9", "live")
    users = store.query("user:")
    assert users == {"user:1": "alice", "user:2": "bob"}
    assert store.query() == {"user:1": "alice", "user:2": "bob", "session:9": "live"}


def test_delete(tmp_path):
    store = MemoryStore(tmp_path)
    store.put("temp", 1)
    assert store.delete("temp") is True
    assert store.get("temp") is None
    assert store.delete("temp") is False


def _kernel_with_memory(tmp_path):
    k = Kernel(home=tmp_path / ".axon")
    store = MemoryStore(k.home)
    memory_actions.register(k.registry, store)
    return k, store


def test_mem_actions_through_kernel(tmp_path):
    k, store = _kernel_with_memory(tmp_path)
    tok = k.caps.issue("tester", ["mem.*"])

    assert k.invoke(tok, "mem.put", {"key": "fav", "value": {"lang": "py"}}) == (
        "stored 'fav' in namespace 'default'"
    )
    assert k.invoke(tok, "mem.get", {"key": "fav"}) == {"lang": "py"}

    k.invoke(tok, "mem.put", {"key": "fav2", "value": 42, "namespace": "nums"})
    assert k.invoke(tok, "mem.query", {"prefix": "fav", "namespace": "nums"}) == {
        "fav2": 42
    }

    assert k.invoke(tok, "mem.delete", {"key": "fav"}) is True
    assert k.invoke(tok, "mem.get", {"key": "fav"}) is None
    # The write persisted to disk under the store's home.
    assert store.get("fav2", namespace="nums") == 42


def test_mem_action_requires_grant(tmp_path):
    k, _ = _kernel_with_memory(tmp_path)
    tok = k.caps.issue("tester", ["fs.*"])  # not granted mem.*
    with pytest.raises(CapabilityError):
        k.invoke(tok, "mem.get", {"key": "fav"})
