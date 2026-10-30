"""macOS actions — drive native apps, notifications, and the clipboard.

The agent-native alternative to scripting a GUI: instead of clicking buttons,
the agent calls typed capabilities that wrap the platform's own tools
(``open``/``osascript``/``pbcopy``/``pbpaste``). App control is risk="dangerous"
because launching or force-quitting an app is user-visible and hard to model.
"""

from __future__ import annotations

import shutil
import subprocess
from ..registry import Registry


def register(reg: Registry) -> None:
    @reg.action(
        "app.open",
        "Launch (or focus) a macOS application by name.",
        {
            "name": {
                "type": "string",
                "required": True,
                "desc": "app name, e.g. 'Safari'",
            }
        },
        mutating=True,
        risk="dangerous",
    )
    def app_open(name: str) -> str:
        _require("open")
        subprocess.run(["open", "-a", name], check=True, capture_output=True, text=True)
        return f"opened {name}"

    @reg.action(
        "app.quit",
        "Quit a running macOS application by name.",
        {
            "name": {
                "type": "string",
                "required": True,
                "desc": "app name, e.g. 'Safari'",
            }
        },
        mutating=True,
        risk="dangerous",
    )
    def app_quit(name: str) -> str:
        _require("osascript")
        script = f"tell application {_q(name)} to quit"
        subprocess.run(
            ["osascript", "-e", script], check=True, capture_output=True, text=True
        )
        return f"quit {name}"

    @reg.action(
        "notify",
        "Post a macOS notification banner.",
        {
            "text": {"type": "string", "required": True, "desc": "notification body"},
            "title": {
                "type": "string",
                "required": False,
                "desc": "banner title (default: 'Axon')",
            },
        },
        mutating=True,
        risk="write",
    )
    def notify(text: str, title: str = "Axon") -> str:
        _require("osascript")
        script = f"display notification {_q(text)} with title {_q(title)}"
        subprocess.run(
            ["osascript", "-e", script], check=True, capture_output=True, text=True
        )
        return f"notified: {title}"

    @reg.action(
        "clipboard.read",
        "Return the current contents of the system clipboard.",
        {},
    )
    def clipboard_read() -> str:
        _require("pbpaste")
        return subprocess.run(
            ["pbpaste"], check=True, capture_output=True, text=True
        ).stdout

    @reg.action(
        "clipboard.write",
        "Replace the system clipboard with the given text.",
        {"text": {"type": "string", "required": True, "desc": "text to copy"}},
        mutating=True,
        risk="write",
    )
    def clipboard_write(text: str) -> str:
        _require("pbcopy")
        subprocess.run(["pbcopy"], input=text, check=True, text=True)
        return f"copied {len(text)} chars to clipboard"


def _require(tool: str) -> None:
    """Fail loudly (not silently) when a macOS-only helper is missing."""
    if shutil.which(tool) is None:
        raise RuntimeError(f"'{tool}' not available on this host (macOS-only action)")


def _q(s: str) -> str:
    """Quote a string for embedding in an AppleScript literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
