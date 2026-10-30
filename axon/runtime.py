"""Agent runtime — processes, capabilities, and budgets.

An agent doesn't just call the kernel ad hoc; it runs as a supervised *process*.
Spawning a process mints a capability token (its authority) and attaches a
Budget (its resource envelope). Every action the process takes flows through the
runtime, which meters usage and refuses to proceed once a budget is spent. Kill
revokes the underlying capability, so a killed agent's token is dead even if it
still holds the string.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from .kernel import Kernel


@dataclass
class Budget:
    """Resource envelope for a process. `None` on a field means unbounded."""

    tokens: int | None = None
    usd: float | None = None
    wall_seconds: float | None = None
    actions: int | None = None


@dataclass
class Usage:
    """Running tally of what a process has consumed so far."""

    tokens: int = 0
    usd: float = 0.0
    actions: int = 0
    started_at: float = 0.0


class BudgetExceeded(Exception):
    """Raised when a charge would push usage past the process's budget."""


@dataclass
class AgentProcess:
    """A supervised agent: its authority (token), envelope (budget), and tally."""

    pid: str
    agent: str
    token: str
    budget: Budget
    usage: Usage
    status: str = "running"  # "running" | "killed" | "done"

    def charge(self, *, tokens: int = 0, usd: float = 0.0, actions: int = 0) -> None:
        """Meter consumption; raise BudgetExceeded if any dimension is overspent."""
        self.usage.tokens += tokens
        self.usage.usd += usd
        self.usage.actions += actions

        b = self.budget
        if b.tokens is not None and self.usage.tokens > b.tokens:
            raise BudgetExceeded(
                f"{self.pid}: token budget exceeded ({self.usage.tokens} > {b.tokens})"
            )
        if b.usd is not None and self.usage.usd > b.usd:
            raise BudgetExceeded(
                f"{self.pid}: usd budget exceeded ({self.usage.usd} > {b.usd})"
            )
        if b.actions is not None and self.usage.actions > b.actions:
            raise BudgetExceeded(
                f"{self.pid}: action budget exceeded "
                f"({self.usage.actions} > {b.actions})"
            )
        if (
            b.wall_seconds is not None
            and self.usage.started_at
            and (time.time() - self.usage.started_at) > b.wall_seconds
        ):
            raise BudgetExceeded(
                f"{self.pid}: wall-clock budget exceeded ({b.wall_seconds}s)"
            )


class Runtime:
    """Spawns, supervises, and reaps agent processes over one kernel."""

    def __init__(self, kernel: Kernel) -> None:
        self.kernel = kernel
        self._procs: dict[str, AgentProcess] = {}

    def spawn(
        self,
        agent: str,
        grants: list[str],
        *,
        path_scopes: list[str] | None = None,
        budget: Budget | None = None,
        ttl: int = 3600,
    ) -> AgentProcess:
        """Mint a capability for `agent` and register a supervised process."""
        token = self.kernel.caps.issue(
            agent, grants, path_scopes=path_scopes, ttl_seconds=ttl
        )
        proc = AgentProcess(
            pid=uuid.uuid4().hex[:12],
            agent=agent,
            token=token,
            budget=budget or Budget(),
            usage=Usage(started_at=time.time()),
        )
        self._procs[proc.pid] = proc
        return proc

    def invoke(
        self, pid: str, action_name: str, args: dict[str, Any] | None = None
    ) -> Any:
        """Charge one action against the budget, then run it through the kernel."""
        proc = self.get(pid)
        proc.charge(actions=1)
        return self.kernel.invoke(proc.token, action_name, args or {})

    def list(self) -> list[AgentProcess]:
        return list(self._procs.values())

    def get(self, pid: str) -> AgentProcess:
        if pid not in self._procs:
            raise KeyError(f"unknown process: {pid}")
        return self._procs[pid]

    def kill(self, pid: str) -> None:
        """Revoke the process's capability and mark it dead."""
        proc = self.get(pid)
        cap = self.kernel.caps.verify(proc.token)
        self.kernel.caps.revoke(cap.id)
        proc.status = "killed"
