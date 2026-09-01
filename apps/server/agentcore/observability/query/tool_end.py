"""``tool.execute_end`` status buckets — one table for the decision spine and patrol.

Live gate is :func:`is_tool_failure`: ``ok=false``, or a status that is neither
success nor a channel steer. Unknown statuses count as failure so a new deny
code cannot hide on the spine. :data:`TOOL_FAIL_STATUSES` names the known
deny / fault codes; it is not a closed allow-list for the gate.

``redirect`` is a steer: it belongs on Decisions, not in Exec ``error``.
"""

from __future__ import annotations

from typing import Any

TOOL_FAIL_STATUSES = frozenset(
    {
        "error",
        "allowlist_deny",
        "not_found",
        "args_parse_failed",
        "crash",
        "timeout",
        "circuit_breaker_deny",
    }
)
TOOL_OK_STATUSES = frozenset({"ok", "success", "done", ""})
# Wrong-tool-channel steer: runtime refused and named the right tool. Not a fault.
TOOL_STEER_STATUSES = frozenset({"redirect"})


def is_tool_failure(obj: dict[str, Any]) -> bool:
    """``tool.execute_end`` 是否算一次工具失败.

    ``ok=false`` 或 status 不是成功集、也不是改道，即算。**未知 status 也算**——
    新出现的失败态宁可露头，也不要被静默吞掉。
    """
    if obj.get("event") != "tool.execute_end":
        return False
    if obj.get("ok") is False:
        return True
    status = str(obj.get("status") or "").strip().lower()
    if status in TOOL_STEER_STATUSES:
        return False
    return status not in TOOL_OK_STATUSES


def tool_end_on_spine(obj: dict[str, Any]) -> bool:
    """Failures and steers belong on Decisions; success noise stays off."""
    if obj.get("event") != "tool.execute_end":
        return False
    status = str(obj.get("status") or "").strip().lower()
    return is_tool_failure(obj) or status in TOOL_STEER_STATUSES
