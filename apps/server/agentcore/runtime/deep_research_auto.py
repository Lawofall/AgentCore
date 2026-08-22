"""深度研究自治 — session-level auto-adopt debate helpers.

只认会话显式 ``deep_research_auto`` 旗标；权限配方不再蕴含本自治。
运行时判断只走这些 helpers，禁止在 kickoff / ceo_format / 工具层散落双判断。
"""

from __future__ import annotations

import uuid
from typing import Any


def _is_persistable_conversation_id(conversation_id: str) -> bool:
    """True when ``conversation_id`` is a real UUID (DB column type).

    Unit tests / evals use short synthetic ids (``c-dra``, …); those must bump
    the in-memory counter only and must not open a Postgres session.
    """
    try:
        uuid.UUID(conversation_id)
    except ValueError:
        return False
    return True

# 自治路径自动开辩：每会话上限（超限优雅降级，不报错）。
AUTO_DEBATE_SESSION_LIMIT = 1


def deep_research_auto_active(
    *,
    deep_research_auto: bool = False,
    permission_axes: Any | None = None,
) -> bool:
    """True only when the session flag is on. Axes no longer imply autonomy."""
    _ = permission_axes
    return bool(deep_research_auto)


def may_auto_debate(
    *,
    deep_research_auto: bool = False,
    permission_axes: Any | None = None,
    auto_debate_count: int = 0,
    limit: int = AUTO_DEBATE_SESSION_LIMIT,
) -> bool:
    """True when the session flag is on **and** under the per-session auto-debate cap.

    Used by ceo_format consumption guidance. Over the limit ⇒ False (guidance
    falls back to present-to-user). Axes are ignored.
    """
    if int(auto_debate_count or 0) >= limit:
        return False
    return deep_research_auto_active(
        deep_research_auto=deep_research_auto,
        permission_axes=permission_axes,
    )


def tool_may_auto_debate(tool: Any) -> bool:
    """Read flag/count from a DelegateTool / DebateTool's base ToolContext."""
    ctx = getattr(tool, "_base_tool_context", None)
    return may_auto_debate(
        deep_research_auto=bool(getattr(ctx, "deep_research_auto", False)),
        permission_axes=getattr(tool, "_permission_axes", None),
        auto_debate_count=int(getattr(ctx, "deep_research_auto_debate_count", 0) or 0),
    )


async def load_deep_research_auto_state(conversation_id: str) -> tuple[bool, int]:
    """Load ``(flag, debate_count)`` for a conversation; missing ⇒ ``(False, 0)``."""
    if not (conversation_id or "").strip():
        return False, 0
    try:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories import ConversationRepository

        async with async_session_factory() as session:
            conv = await ConversationRepository(session).get_by_id_unscoped(
                conversation_id
            )
            if not conv:
                return False, 0
            return (
                bool(getattr(conv, "deep_research_auto", False)),
                int(getattr(conv, "deep_research_auto_debate_count", 0) or 0),
            )
    except Exception:  # noqa: BLE001 — turn path must not die on optional flag load
        return False, 0


async def record_auto_debate(tool: Any) -> None:
    """Persist + bump in-memory auto-debate count after a waived debate kickoff.

    Empty / non-UUID ``conversation_id`` (tests / evals) only bumps the
    in-memory counter. Failures are logged and swallowed — never block the
    debate start.
    """
    from agentcore.core.logging import get_logger

    logger = get_logger(__name__)
    ctx = getattr(tool, "_base_tool_context", None)
    conversation_id = (
        str(getattr(tool, "_conversation_id", None) or "")
        or str(getattr(ctx, "conversation_id", None) or "")
    ).strip()

    new_count: int | None = None
    if conversation_id and _is_persistable_conversation_id(conversation_id):
        try:
            from agentcore.db.base import async_session_factory
            from agentcore.db.repositories import ConversationRepository

            async with async_session_factory() as session:
                new_count = await ConversationRepository(
                    session
                ).increment_deep_research_auto_debate_count(conversation_id)
        except Exception as exc:  # noqa: BLE001 — never block kickoff
            logger.warning(
                "deep_research_auto.record_failed",
                conversation_id=conversation_id,
                error=str(exc),
            )

    if ctx is not None:
        if new_count is not None:
            ctx.deep_research_auto_debate_count = int(new_count)
        else:
            ctx.deep_research_auto_debate_count = (
                int(getattr(ctx, "deep_research_auto_debate_count", 0) or 0) + 1
            )
