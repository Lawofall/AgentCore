"""Parse and cap CEO ``team_brief`` (opening consensus injected into workers)."""

from __future__ import annotations

from typing import Any

from agentcore.runtime.context_cap import log_context_capped

MAX_TEAM_BRIEF_CHARS = 1500


def _clean_brief(text: str, *, execution_id: str | None = None) -> str:
    collapsed = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    original = len(collapsed)
    if original > MAX_TEAM_BRIEF_CHARS:
        collapsed = collapsed[: MAX_TEAM_BRIEF_CHARS - 1].rstrip() + "…"
        log_context_capped(
            site="team_brief",
            original_chars=original,
            final_chars=len(collapsed),
            execution_id=execution_id,
        )
    return collapsed


def parse_team_brief(
    raw: Any, *, execution_id: str | None = None
) -> tuple[str | None, str | None]:
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, "team_brief 必须是字符串。"
    brief = _clean_brief(raw, execution_id=execution_id)
    if not brief:
        return None, "team_brief 清理后为空。"
    return brief, None
