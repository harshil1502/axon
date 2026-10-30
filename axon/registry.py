"""Action Registry — the 'everything is a typed API' layer.

In macOS/Windows an agent that wants to do something either shells out or
scrapes a GUI. In Axon, every capability the system exposes is registered here
as a typed, discoverable Action with a JSON-schema signature. Agents *call*
the system instead of impersonating a human at a keyboard.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Action:
    """A single system capability, callable by an agent.

    name:        namespaced identifier, e.g. "fs.read", "proc.list".
    description: human/agent-readable summary (used for discovery).
    params:      {param_name: {"type": ..., "required": bool, "desc": ...}}.
    handler:     the function that performs the work. Receives (**params).
    mutating:    True if it changes system state (drives reversibility/audit).
    risk:        "read" | "write" | "dangerous" — the Policy Engine keys rate
                 limits and approval gates off this.
    """

    name: str
    description: str
    params: dict[str, dict[str, Any]]
    handler: Callable[..., Any]
    mutating: bool = False
    risk: str = "read"

    def schema(self) -> dict[str, Any]:
        """Machine-readable signature an agent can introspect before calling."""
        return {
            "name": self.name,
            "description": self.description,
            "mutating": self.mutating,
            "risk": self.risk,
            "params": self.params,
        }

    def validate(self, args: dict[str, Any]) -> None:
        """Reject calls that don't match the declared signature."""
        for pname, spec in self.params.items():
            if spec.get("required") and pname not in args:
                raise ValueError(f"{self.name}: missing required param '{pname}'")
        unknown = set(args) - set(self.params)
        if unknown:
            raise ValueError(f"{self.name}: unknown params {sorted(unknown)}")


class Registry:
    """Holds every Action the system exposes. Discoverable by pattern."""

    def __init__(self) -> None:
        self._actions: dict[str, Action] = {}

    def register(self, action: Action) -> None:
        if action.name in self._actions:
            raise ValueError(f"action already registered: {action.name}")
        self._actions[action.name] = action

    def action(
        self,
        name: str,
        description: str,
        params: dict[str, dict[str, Any]] | None = None,
        mutating: bool = False,
        risk: str = "read",
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form: @registry.action("fs.read", "Read a file", {...})."""

        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.register(
                Action(
                    name=name,
                    description=description,
                    params=params or {},
                    handler=fn,
                    mutating=mutating,
                    risk=risk,
                )
            )
            return fn

        return deco

    def get(self, name: str) -> Action:
        if name not in self._actions:
            raise KeyError(f"unknown action: {name}")
        return self._actions[name]

    def list(self, pattern: str = "*") -> list[Action]:
        """List actions, optionally filtered by a glob-ish prefix like 'fs.*'."""
        import fnmatch

        return [
            a
            for name, a in sorted(self._actions.items())
            if fnmatch.fnmatch(name, pattern)
        ]

    def describe(self, pattern: str = "*") -> list[dict[str, Any]]:
        """Full discovery payload — what an agent reads to learn the system."""
        return [a.schema() for a in self.list(pattern)]
