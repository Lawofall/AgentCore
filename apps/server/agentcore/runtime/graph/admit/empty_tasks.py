"""Empty-delegate contract reject copy — owned by runtime, not the tool adapter.

``tools.builtin.graph.schema`` re-exports these so the empty-tasks
reject (server + desktop mirror) stays on one string. The copyable
skeleton is not advertised on the per-turn schema.
"""

from __future__ import annotations

# 缺 nodes 拒收回执里的可抄骨架。不进每轮 schema。
HANDWRITTEN_TASKS_SKELETON = (
    '{"nodes":[{"id":"n1","goal":"目标+边界+验收"}]}'
)
EMPTY_DELEGATE_MSG = (
    "graph 缺 nodes：顶层放非空 `nodes`，"
    f"可抄：{HANDWRITTEN_TASKS_SKELETON}。"
)


def is_empty_delegate_error(error: str | None) -> bool:
    """True when a failed graph call is the empty-nodes contract reject."""
    if not error:
        return False
    return error == EMPTY_DELEGATE_MSG or error.startswith("graph 缺 nodes")
