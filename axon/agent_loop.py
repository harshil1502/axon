"""Agent Loop — Claude driving Axon through a capability.

This is the bridge that lets a real language model *be* an agent process on
Axon. It reads the actions the process's capability permits, hands them to
Claude as tools, and runs a manual agentic loop: every tool the model calls is
executed through the same audited kernel chokepoint (`kernel.invoke`) that any
other agent uses, so the model gets no special powers — only what its token
grants.

The `anthropic` SDK is an *optional* dependency (the only third-party import in
Axon, confined to this module). If it isn't installed, or no credentials are
available, `run()` degrades to a dry-run string instead of raising, so the rest
of the system — and the tests — work offline.
"""

from __future__ import annotations

import json
import re
from typing import Any


class AgentLoop:
    """Runs Claude against the actions a process is permitted to call."""

    def __init__(self, kernel: Any, *, model: str = "claude-opus-4-8") -> None:
        self.kernel = kernel
        self.model = model

    # -- tool schema -----------------------------------------------------

    def _build_tools(self, process: Any) -> list[dict[str, Any]]:
        """Claude tool definitions for exactly the actions this token permits."""
        cap = self.kernel.caps.verify(process.token)
        tools: list[dict[str, Any]] = []
        for schema in self.kernel.registry.describe():
            if not cap.permits_action(schema["name"]):
                continue
            tools.append(
                {
                    "name": schema["name"],
                    "description": schema["description"],
                    "input_schema": self._input_schema(schema["params"]),
                }
            )
        return tools

    @staticmethod
    def _input_schema(params: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Map Axon param specs to a JSON-Schema object for tool input."""
        properties: dict[str, Any] = {}
        required: list[str] = []
        for pname, spec in params.items():
            properties[pname] = {
                "type": spec.get("type", "string"),
                "description": spec.get("desc", ""),
            }
            if spec.get("required"):
                required.append(pname)
        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    # -- run -------------------------------------------------------------

    def run(self, process: Any, task: str, *, max_turns: int = 10) -> str:
        """Drive Claude to accomplish `task` using the process's granted actions.

        Returns the model's final text. Degrades to a dry-run string (never
        raises) when `anthropic` is missing or no Claude credentials exist.
        """
        tools = self._build_tools(process)
        # The Claude API requires tool names to match ^[a-zA-Z0-9_-]{1,128}$, but
        # Axon action names contain dots (e.g. "fs.read"). Send sanitized names to
        # the model and map them back to the real action when executing a call.
        name_map: dict[str, str] = {}
        api_tools: list[dict[str, Any]] = []
        for t in tools:
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", t["name"])
            name_map[safe] = t["name"]
            api_tools.append({**t, "name": safe})

        try:
            import anthropic
        except Exception:  # noqa: BLE001 — no SDK: degrade, don't crash
            return self._dry_run(task)

        try:
            client = anthropic.Anthropic()
        except Exception:  # noqa: BLE001 — no credentials at construction time
            return self._dry_run(task)

        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
        final_text = ""
        resp = None
        first_call = True

        for _ in range(max_turns):
            try:
                resp = client.messages.create(
                    model=self.model,
                    max_tokens=8000,
                    thinking={"type": "adaptive"},
                    tools=api_tools,
                    messages=messages,
                )
            except Exception as exc:  # noqa: BLE001
                # A missing-credential / connectivity failure on the first call
                # degrades to a dry-run. Any other first-call error (bad model,
                # malformed request) is surfaced verbatim — not masked as "no
                # credentials". A mid-run failure is genuine and propagates.
                if first_call and isinstance(
                    exc, (anthropic.AuthenticationError, anthropic.APIConnectionError)
                ):
                    return self._dry_run(task)
                if first_call:
                    return f"[error] Claude call failed: {exc}"
                raise
            first_call = False

            usage = getattr(resp, "usage", None)
            if usage is not None:
                process.charge(
                    tokens=(getattr(usage, "input_tokens", 0) or 0)
                    + (getattr(usage, "output_tokens", 0) or 0)
                )

            stop = resp.stop_reason

            if stop == "refusal":
                final_text = self._text(resp) or "[refusal] the model declined the task"
                break

            if stop == "pause_turn":
                # Server-side tool loop paused; resend to resume where it left off.
                messages.append({"role": "assistant", "content": resp.content})
                continue

            tool_uses = [
                b for b in resp.content if getattr(b, "type", None) == "tool_use"
            ]
            if stop == "end_turn" or not tool_uses:
                final_text = self._text(resp)
                break

            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in tool_uses:
                # Charge the action budget here — outside the try — so an
                # over-budget run halts (BudgetExceeded propagates) instead of
                # being swallowed into a tool_result error.
                process.charge(actions=1)
                try:
                    out = self.kernel.invoke(
                        process.token,
                        name_map.get(block.name, block.name),
                        dict(block.input),
                    )
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": self._render(out),
                        }
                    )
                except Exception as exc:  # noqa: BLE001 — report, let model recover
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"error: {exc}",
                            "is_error": True,
                        }
                    )
            messages.append({"role": "user", "content": results})
        else:
            # Exhausted max_turns without a natural stop.
            if resp is not None:
                final_text = self._text(resp)

        return final_text

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _text(resp: Any) -> str:
        return "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        )

    @staticmethod
    def _render(result: Any) -> str:
        if isinstance(result, str):
            return result
        try:
            return json.dumps(result, default=str)
        except (TypeError, ValueError):
            return str(result)

    @staticmethod
    def _dry_run(task: str) -> str:
        return f"[dry-run] no Claude credentials; would run: {task}"
