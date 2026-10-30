"""Axon MCP server — expose the OS's capabilities to an MCP client (Claude Code).

Speaks MCP over stdio: newline-delimited JSON-RPC 2.0, the transport Claude Code
launches a stdio server with. On first use it attaches to the running Axon
daemon, grants *itself* a capability (scope from the environment), and exposes
exactly the permitted actions as MCP tools.

The point: every tool call Claude Code makes is routed through the daemon's one
chokepoint — capability-checked, path-scoped, policy-gated, snapshotted, and
audited. Claude Code operates the machine, but only through Axon's governance,
and only within the token it was granted. It is not a shell; it is a citizen.

Config (env):
  AXON_SOCK        daemon socket (default /var/lib/axon/.axon/axon.sock)
  AXON_MCP_AGENT   agent identity on the token (default "claude-code")
  AXON_MCP_GRANTS  space-separated action globs
                   (default: fs.* mem.* sys.info proc.list proc.info proc.tree fs.search)
  AXON_MCP_SCOPE   space-separated path scopes (default: /root /tmp)
  AXON_MCP_TTL     token lifetime in seconds (default 86400)
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from .client import Client

PROTOCOL = "2025-06-18"


def _log(*a: Any) -> None:
    # stdout is the protocol channel — diagnostics must go to stderr.
    print("[axon-mcp]", *a, file=sys.stderr, flush=True)


def _sanitize(name: str) -> str:
    # MCP/Claude tool names must match ^[a-zA-Z0-9_-]{1,128}$; action names have dots.
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


def _input_schema(params: dict[str, dict[str, Any]]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    for pname, spec in params.items():
        props[pname] = {
            "type": spec.get("type", "string"),
            "description": spec.get("desc", ""),
        }
        if spec.get("required"):
            required.append(pname)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": props,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


class AxonMCP:
    """Bridges the MCP protocol to the Axon daemon over a granted capability."""

    def __init__(self) -> None:
        self._sock = os.environ.get("AXON_SOCK", "/var/lib/axon/.axon/axon.sock")
        self._agent = os.environ.get("AXON_MCP_AGENT", "claude-code")
        self._grants = os.environ.get(
            "AXON_MCP_GRANTS",
            "fs.* mem.* sys.info proc.list proc.info proc.tree fs.search",
        ).split()
        self._scopes = os.environ.get("AXON_MCP_SCOPE", "/root /tmp").split()
        self._ttl = int(os.environ.get("AXON_MCP_TTL", "86400"))
        self._client: Client | None = None
        self._token: str | None = None
        self._tools: list[dict[str, Any]] = []
        self._name_map: dict[str, str] = {}
        self._attach_error: str | None = None
        self._attached = False

    # -- attach lazily so `initialize` never fails on a not-yet-ready daemon ----
    def _ensure(self) -> None:
        if self._attached:
            return
        self._attached = True
        try:
            self._client = Client(socket_path=Path(self._sock))
            self._token = self._client.call(
                "grant",
                agent=self._agent,
                grants=self._grants,
                path_scopes=self._scopes,
                ttl_seconds=self._ttl,
            )
            actions = self._client.call("actions")
            for a in actions:
                # Map every action's sanitized name back to its dotted name, so a
                # call to an ungranted (but real) action reconstructs correctly and
                # the daemon returns an accurate "not granted" denial.
                safe = _sanitize(a["name"])
                self._name_map[safe] = a["name"]
                # …but only EXPOSE the actions this token was granted.
                if not any(fnmatch.fnmatch(a["name"], g) for g in self._grants):
                    continue
                desc = a["description"]
                if a.get("mutating"):
                    desc += " [mutating]"
                desc += f" (risk={a.get('risk', 'read')}, capability-gated)"
                self._tools.append(
                    {
                        "name": safe,
                        "description": desc,
                        "inputSchema": _input_schema(a["params"]),
                    }
                )
            _log(
                f"attached to {self._sock}: {len(self._tools)} tools; "
                f"grants={self._grants} scope={self._scopes}"
            )
        except Exception as exc:  # noqa: BLE001 — degrade, don't crash the transport
            self._attach_error = str(exc)
            _log(f"could not attach to Axon daemon at {self._sock}: {exc}")

    def _call_tool(self, name: str, args: dict[str, Any] | None) -> dict[str, Any]:
        self._ensure()
        if self._token is None:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Axon daemon unreachable: {self._attach_error}",
                    }
                ],
                "isError": True,
            }
        action = self._name_map.get(name, name)
        try:
            result = self._client.call(
                "invoke", token=self._token, action=action, args=args or {}
            )
            text = (
                result
                if isinstance(result, str)
                else json.dumps(result, default=str, indent=2)
            )
            return {"content": [{"type": "text", "text": str(text)}]}
        except Exception as exc:  # noqa: BLE001 — surface denials/errors to the model
            return {
                "content": [{"type": "text", "text": f"error: {exc}"}],
                "isError": True,
            }

    # -- JSON-RPC dispatch -----------------------------------------------------
    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}

        if method == "initialize":
            version = params.get("protocolVersion") or PROTOCOL
            return self._reply(
                mid,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "axon", "version": "0.2.0"},
                    "instructions": "Axon exposes this machine's capabilities as typed, "
                    "capability-gated tools. Every call is scoped, policy-gated, "
                    "reversible, and audited by the Axon kernel.",
                },
            )
        if method in (
            "notifications/initialized",
            "initialized",
            "notifications/cancelled",
        ):
            return None  # notifications take no reply
        if method == "ping":
            return self._reply(mid, {})
        if method == "tools/list":
            self._ensure()
            return self._reply(mid, {"tools": self._tools})
        if method == "tools/call":
            return self._reply(
                mid, self._call_tool(params.get("name"), params.get("arguments"))
            )

        if mid is None:
            return None  # unknown notification — ignore
        return self._error(mid, -32601, f"method not found: {method}")

    @staticmethod
    def _reply(mid: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @staticmethod
    def _error(mid: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "error": {"code": code, "message": message},
        }


def main() -> None:
    server = AxonMCP()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            _log("malformed JSON-RPC line:", exc)
            continue
        response = server.handle(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
