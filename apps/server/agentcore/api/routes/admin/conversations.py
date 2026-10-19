"""Platform conversation + turn rosters (对话页 · 会话段 / 回合段)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query

from agentcore.api.dependencies import (
    AdminUser,
    get_conversation_repo,
    get_cost_event_repo,
    get_message_repo,
    get_turn_metrics_repo,
)
from agentcore.api.schemas import (
    AdminConversationListItem,
    AdminConversationListResponse,
    AdminTurnListItem,
    AdminTurnListResponse,
    TurnMetricLine,
)
from agentcore.db.repositories import (
    ConversationRepository,
    CostEventRepository,
    MessageRepository,
    TurnMetricsRepository,
)

router = APIRouter(tags=["admin"])

# 对话页 roster caps — paginated, not a dashboard glance.
_CONVERSATION_PAGE_SIZE_DEFAULT = 20
_TURN_PAGE_SIZE_DEFAULT = 20


@router.get("/conversations", response_model=AdminConversationListResponse)
async def list_conversations(
    admin: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(_CONVERSATION_PAGE_SIZE_DEFAULT, ge=1, le=100),
    q: str | None = Query(None, max_length=100),
    user_id: str | None = Query(None),
    has_errors: bool | None = Query(None),
    has_delegated: bool | None = Query(None),
    include_deleted: bool = Query(True),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    sort: Literal["updated_at", "created_at", "cost", "delegated"] = Query("updated_at"),
    order: Literal["asc", "desc"] = Query("desc"),
    conversations: ConversationRepository = Depends(get_conversation_repo),
    messages_repo: MessageRepository = Depends(get_message_repo),
    metrics_repo: TurnMetricsRepository = Depends(get_turn_metrics_repo),
    cost_repo: CostEventRepository = Depends(get_cost_event_repo),
) -> AdminConversationListResponse:
    """平台对话名册 (对话页 · 会话段): cross-user paginated conversation index.

    Each row carries owner identity, housekeeping flags, message/turn/error/
    multi-agent rollups, and all-time spend. Filters AND-combine; soft-deleted
    conversations are included by default (``include_deleted=false`` hides them).
    Drill into 会话复盘 by ``id``.
    """
    rows, total = await conversations.list_admin(
        page=page,
        page_size=page_size,
        query=q,
        user_id=user_id,
        has_errors=has_errors,
        has_delegated=has_delegated,
        include_deleted=include_deleted,
        since=since,
        until=until,
        sort=sort,
        order=order,
    )
    conv_ids = [conv.id for conv, _ in rows]
    msg_counts = await messages_repo.counts_for_conversations(conv_ids)
    turn_stats = await metrics_repo.aggregate_stats_by_conversations(conv_ids)
    costs = await cost_repo.aggregate_cost_by_conversations(conv_ids)

    data: list[AdminConversationListItem] = []
    for conv, owner in rows:
        stats = turn_stats.get(
            conv.id, {"turns": 0, "errors": 0, "delegated_turns": 0, "workers": 0}
        )
        title = conv.title or None
        if title == "":
            title = None
        data.append(
            AdminConversationListItem(
                id=conv.id,
                title=title,
                user_id=conv.user_id,
                username=owner.username if owner else None,
                display_name=owner.display_name if owner else None,
                user_deleted_at=owner.deleted_at if owner else None,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
                deleted_at=conv.deleted_at,
                archived=conv.archived_by_folder_delete,
                messages=msg_counts.get(conv.id, 0),
                turns=stats["turns"],
                errors=stats["errors"],
                cost_total=costs.get(conv.id, 0),
                delegated_turns=stats["delegated_turns"],
                workers=stats["workers"],
            )
        )

    return AdminConversationListResponse(
        data=data,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/conversations/turns", response_model=AdminTurnListResponse)
async def list_conversation_turns(
    admin: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(_TURN_PAGE_SIZE_DEFAULT, ge=1, le=100),
    user_id: str | None = Query(None),
    conversation_id: str | None = Query(None),
    status: Literal["ok", "error"] | None = Query(None),
    delegated: bool | None = Query(None),
    trace_id: str | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    include_deleted_conversations: bool = Query(True),
    metrics_repo: TurnMetricsRepository = Depends(get_turn_metrics_repo),
    cost_repo: CostEventRepository = Depends(get_cost_event_repo),
) -> AdminTurnListResponse:
    """平台回合流水 (对话页 · 回合段): cross-user paginated turn feed.

    Finer-grained than the session roster — each row is one ``turn_metrics`` record
    with conversation title + owner identity for triage. Newest-first; filters
    AND-combine (``delegated`` = multi-agent only; ``trace_id`` for 复盘深链解析).
    ``models`` / ``credential_source`` join ``cost_calls`` by ``trace_id`` (not
    ``turn_id`` — that column is attempt_id ≠ assistant message_id). Drill into
    会话复盘 by ``conversation_id``.
    """
    rows, total = await metrics_repo.list_platform(
        page=page,
        page_size=page_size,
        user_id=user_id,
        conversation_id=conversation_id,
        status=status,
        delegated=delegated,
        trace_id=trace_id,
        include_deleted_conversations=include_deleted_conversations,
        since=since,
        until=until,
    )
    call_by_trace = await cost_repo.models_and_source_by_trace(
        [tm.trace_id for tm, _conv, _owner in rows if tm.trace_id]
    )
    data: list[AdminTurnListItem] = []
    for tm, conv, owner in rows:
        title = conv.title or None
        if title == "":
            title = None
        base = TurnMetricLine.model_validate(tm)
        models, cred_src = call_by_trace.get(tm.trace_id or "", ([], None))
        data.append(
            AdminTurnListItem(
                **base.model_dump(),
                conversation_title=title,
                username=owner.username if owner else None,
                display_name=owner.display_name if owner else None,
                conversation_deleted_at=conv.deleted_at,
                models=models,
                credential_source=cred_src,
            )
        )

    return AdminTurnListResponse(
        data=data,
        total=total,
        page=page,
        page_size=page_size,
    )
