"""Historical + live identity for the unified ``browser`` tool.

Execution never aliases old names. Journal / transcript / closing / harvest
accept either ``browser`` + ``action`` or the pre-merge ``browser_*`` names.
"""

from __future__ import annotations

import json
from typing import Any

from agentcore.tools.builtin.browser import browser_action_name

# Pre-merge tool names → action. Live calls use ``name=="browser"`` + ``action``.
_LEGACY_BROWSER_ACTION: dict[str, str] = {
    "browser_navigate": "navigate",
    "browser_click": "click",
    "browser_type": "type",
    "browser_scroll": "scroll",
    "browser_snapshot": "snapshot",
    "browser_screenshot": "screenshot",
    "browser_console": "console",
}

_BROWSER_FACE = "浏览网页"


def _args_dict(arguments: dict[str, Any] | str | None) -> dict[str, Any] | None:
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str) or not arguments.strip():
        return None
    try:
        data = json.loads(arguments)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_browser_tool_name(name: str) -> bool:
    """True for live ``browser`` or any pre-merge ``browser_*`` name."""
    key = (name or "").strip()
    return key == "browser" or key in _LEGACY_BROWSER_ACTION


def browser_call_action(name: str, arguments: dict[str, Any] | str | None = None) -> str:
    """Resolved action: legacy name mapping, or ``action`` on ``browser``."""
    key = (name or "").strip()
    legacy = _LEGACY_BROWSER_ACTION.get(key)
    if legacy:
        return legacy
    if key != "browser":
        return ""
    return browser_action_name(_args_dict(arguments))


def is_browser_navigate_call(
    name: str, arguments: dict[str, Any] | str | None = None
) -> bool:
    return browser_call_action(name, arguments) == "navigate"


def browser_tool_face(name: str) -> str | None:
    """Harvest display face, or ``None`` when ``name`` is not a browser tool."""
    if is_browser_tool_name(name):
        return _BROWSER_FACE
    return None
