"""Axon — an agent-native OS layer.

Phases 1–3: Action Registry, Capability Engine, Policy Engine, Reversibility,
Memory substrate, Agent Runtime, and a Claude agent loop — running as a daemon
on macOS. The same code becomes the userland layer of the bootable OS (Phase 4).
"""

from __future__ import annotations

from pathlib import Path

from .kernel import Kernel

__version__ = "0.2.0"


def boot(home: Path | None = None) -> Kernel:
    """Build a kernel with every built-in action + the memory substrate wired in.

    The kernel already owns the Capability Engine, Policy Engine, Audit log, and
    Reversibility journal (see Kernel.__init__). Here we register the full action
    library and attach a durable MemoryStore as the shared world-model.
    """
    from .actions import filesystem, macos, network, process, shell, system
    from .actions import memory as memory_actions
    from .memory import MemoryStore

    kernel = Kernel(home)
    for module in (filesystem, system, process, network, macos, shell):
        module.register(kernel.registry)

    store = MemoryStore(kernel.home)
    memory_actions.register(kernel.registry, store)
    kernel.memory = store  # type: ignore[attr-defined]

    return kernel


__all__ = ["Kernel", "boot", "__version__"]
