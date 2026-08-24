"""Operational observability + conversation replay (运营观测看板 / 会话复盘)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.admin.audit import record_admin_audit
from agentcore.api.dependencies import (
    AdminUser,
    get_conversation_repo,
    get_cost_event_repo,
    get_db,
    get_message_repo,
    get_turn_journal_repo,
    get_turn_metrics_repo,
    get_user_repo,
)
from agentcore.api.routes.admin._shared import (
    _TREND_DAYS,
    _health_window,
    _project_spans,
    fold_replay_journal,
)
from agentcore.api.schemas import (
    AdminConversationReplay,
    AdminObservabilitySummary,
    AdminReplayTurnFinalState,
    DailyTurns,
    ReplayConversation,
    ReplayMessage,
    TurnMetricLine,
)
from agentcore.core.errors import NotFoundError
from agentcore.db.repositories import (
    ConversationRepository,
    CostEventRepository,
    MessageRepository,
    TurnJournalRepository,
    TurnMetricsRepository,
    UserRepository,
)
from agentcore.llm.model_profiles import LlmModelProfileService

router = APIRouter(tags=["admin"])

# 观测看板「近期错误」feed length — the recent failures worth a glance (the long tail
# is for the drill-down, not the dashboard).
_ERROR_FEED = 20
# 会话复盘 message cap: latest window (newest-first fetch, chronological return).
# Older history past this cap is signalled by ``has_more_before`` — ops triage
# needs the recent side, not the oldest 500.
_REPLAY_MAX_MESSAGES = 500


def _usage_origin_fields(usage: object) -> tuple[str | None, str | None]:
    """Lift ``usage.origin`` / ``usage.harvest_kind`` for ReplayMessage attribution."""
    if not isinstance(usage, dict):
        return None, None
    origin_raw = usage.get("origin")
    origin = origin_raw.strip() if isinstance(origin_raw, str) and origin_raw.strip() else None
    kind_raw = usage.get("harvest_kind")
    harvest_kind = (
        kind_raw.strip() if isinstance(kind_raw, str) and kind_raw.strip() else None
    )
    return origin, harvest_kind


@router.get("/observability/summary", response_model=AdminObservabilitySummary)
async def observability_summary(
    admin: AdminUser,
    repo: TurnMetricsRepository = Depends(get_turn_metrics_repo),
) -> AdminObservabilitySummary:
    """运营观测看板 (观测, P1): platform-wide turn health (today + trailing 7 days),
    the 7-day daily trend, and the most recent errored turns.

    Sourced from ``turn_metrics`` (the per-turn telemetry sink), NOT the dev log
    file — so it works under prod's stdout-only logging posture and aggregates with
    indexed SQL instead of scanning a multi-MB JSONL. Aggregated over *every*
    account (admin is a cross-user surface). Each error row carries its ``trace_id``
    / ``conversation_id`` to drill from a failure into the full turn (会话复盘, P2).
    """
    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # The trailing-7-day window shares its start with the trend so「近 7 日」health and
    # the trend bars span exactly the same UTC days.
    week_start = day_start - timedelta(days=_TREND_DAYS - 1)

    today = await repo.aggregate_health_for_window(since=day_start)
    week = await repo.aggregate_health_for_window(since=week_start)

    # 近 7 日趋势: zero-fill the daily map into a fixed, oldest-first series ending
    # today so the bars are a stable length even on a quiet day.
    daily = await repo.aggregate_daily_for_window(since=week_start)
    recent_daily = []
    for i in range(_TREND_DAYS):
        iso = (week_start + timedelta(days=i)).date().isoformat()
        point = daily.get(iso) or {}
        recent_daily.append(
            DailyTurns(
                date=iso,
                turns=int(point.get("turns", 0)),
                errors=int(point.get("errors", 0)),
            )
        )

    errors = await repo.list_recent_errors(limit=_ERROR_FEED)
    return AdminObservabilitySummary(
        today=_health_window(today),
        week=_health_window(week),
        recent_daily=recent_daily,
        recent_errors=[TurnMetricLine.model_validate(row) for row in errors],
    )


@router.get(
    "/observability/conversations/{conversation_id}",
    response_model=AdminConversationReplay,
)
async def observability_conversation(
    conversation_id: str,
    admin: AdminUser,
    db: AsyncSession = Depends(get_db),
    conversations: ConversationRepository = Depends(get_conversation_repo),
    messages_repo: MessageRepository = Depends(get_message_repo),
    metrics_repo: TurnMetricsRepository = Depends(get_turn_metrics_repo),
    cost_repo: CostEventRepository = Depends(get_cost_event_repo),
    journal_repo: TurnJournalRepository = Depends(get_turn_journal_repo),
    users: UserRepository = Depends(get_user_repo),
) -> AdminConversationReplay:
    """会话复盘 (观测 P2): one conversation's merged timeline — the message thread
    (bodies) overlaid with each turn's outcome/quality (turn_metrics), spend
    (cost_events), execution spans, and multi-agent runs (turn_journal), joined by
    trace_id / message_id. Per-message ``models`` / ``credential_source`` come from
    ``cost_calls`` (message_id; bare turn markers fall back to trace_id).

    Assistant-row ``runs_payload`` / ``projected`` stay off this list (always
    null) so a long thread does not re-inflate the payload ``ReplaySpan`` was
    built to drop. ``has_final_state`` marks turns whose pair is available via
    ``GET .../messages/{id}/final-state``. The thread is the latest window
    (not oldest-first 500). Soft-deleted conversations are readable (roster
    default includes them).

    Admin-only and cross-user (any account's conversation), unlike the owner-scoped
    ``/v1/conversations/*``. The drill-down target of the 近期错误 feed: open a
    failed turn in full context (prompt + reply/error + rounds/latency + ¥ + the
    turn's tool/LLM spans + member tree).
    """
    conv = await conversations.get_by_id_unscoped(
        conversation_id, include_deleted=True
    )  # admin cross-user; roster includes tombstones
    if conv is None:
        raise NotFoundError("对话不存在")

    owner = await users.get_by_id(conv.user_id)
    rows, has_more_before = await messages_repo.list_latest(
        conversation_id, limit=_REPLAY_MAX_MESSAGES
    )
    metrics = await metrics_repo.list_for_conversation(conversation_id)
    # Only the assistant reply carries a trace_id (the user prompt's is NULL), so a
    # trace overlays exactly one message — its turn's outcome/quality.
    metrics_by_trace = {m.trace_id: m for m in metrics if m.trace_id}
    cost_by_message = await cost_repo.aggregate_cost_by_message_for_conversation(conversation_id)
    call_by_message = await cost_repo.models_and_source_by_message_for_conversation(
        conversation_id
    )
    # Bare text-less turn markers have no message_id — fall back to trace join.
    message_traces = {m.trace_id for m in rows if m.trace_id}
    bare_traces = [
        tm.trace_id for tm in metrics if tm.trace_id and tm.trace_id not in message_traces
    ]
    call_by_trace = await cost_repo.models_and_source_by_trace(bare_traces)
    # Each turn's execution spans live in turn_journal keyed by turn_id == the
    # assistant message id (NOT turn_metrics.turn_id, a separate id). Batch-load all
    # assistant turns' journals in one query (no N+1); a plain chat journaled nothing.
    journals = await journal_repo.load_map([m.id for m in rows if m.role == "assistant"])

    expanded = await LlmModelProfileService(db).expand_for_conversation(conv.user_id, conv)

    # The timeline is the messages ⟕ turns outer-join: a turn with a text reply rides
    # that assistant message (overlay); a text-less turn (e.g. an early hard error
    # that persisted no reply) has no message to ride, so it joins as a bare turn
    # marker — 复盘 must never hide a failure. Its spend stays in the rollup below.
    # Bare markers older than the latest-N window stay out so the cap keeps the
    # recent side (conversation-wide turn/error/cost rollups are unchanged).
    window_start = rows[0].created_at if rows else None
    timeline: list[ReplayMessage] = []
    consumed: set[str] = set()
    for m in rows:
        overlay = metrics_by_trace.get(m.trace_id) if m.trace_id else None
        if overlay is not None:
            consumed.add(m.trace_id)
        journal = journals.get(m.id, [])
        models, cred_src = call_by_message.get(m.id, ([], None))
        origin, harvest_kind = _usage_origin_fields(m.usage)
        runs = []
        has_final_state = False
        if m.role == "assistant":
            runs, _, payload = fold_replay_journal(journal)
            has_final_state = payload is not None
        timeline.append(
            ReplayMessage(
                id=m.id,
                role=m.role,
                content=m.content,
                reasoning_content=m.reasoning_content,
                created_at=m.created_at,
                trace_id=m.trace_id,
                metrics=TurnMetricLine.model_validate(overlay) if overlay else None,
                cost_total=cost_by_message.get(m.id, 0),
                models=models,
                credential_source=cred_src,
                origin=origin,
                harvest_kind=harvest_kind,
                spans=_project_spans(journal),
                runs=runs,
                runs_payload=None,
                projected=None,
                has_final_state=has_final_state,
                attachments=m.attachments or [],
                agent_mentions=m.agent_mentions or [],
            )
        )
    for tm in metrics:
        if not tm.trace_id or tm.trace_id in consumed:
            continue
        if window_start is not None and tm.created_at < window_start:
            continue
        models, cred_src = call_by_trace.get(tm.trace_id, ([], None))
        timeline.append(
            ReplayMessage(
                id=tm.turn_id,
                role="assistant",
                content=None,
                created_at=tm.created_at,
                trace_id=tm.trace_id,
                metrics=TurnMetricLine.model_validate(tm),
                cost_total=0,
                models=models,
                credential_source=cred_src,
            )
        )
    timeline.sort(key=lambda r: r.created_at)

    await record_admin_audit(
        db,
        actor_id=admin.user_id,
        action="conversation.replay",
        target_type="conversation",
        target_id=conversation_id,
        detail={
            "owner_user_id": conv.user_id,
            "turns": len(metrics),
            "messages": len(timeline),
        },
    )

    return AdminConversationReplay(
        conversation=ReplayConversation(
            id=conv.id,
            title=conv.title,
            user_id=conv.user_id,
            username=owner.username if owner else None,
            display_name=owner.display_name if owner else None,
            created_at=conv.created_at,
            model_profile_id=conv.model_profile_id,
            model_profile_name=expanded.name,
            deleted_at=conv.deleted_at,
        ),
        messages=timeline,
        turns=len(metrics),
        errors=sum(1 for m in metrics if m.status == "error"),
        cost_total=sum(cost_by_message.values()),
        has_more_before=has_more_before,
    )


@router.get(
    "/observability/conversations/{conversation_id}/messages/{message_id}/final-state",
    response_model=AdminReplayTurnFinalState,
)
async def observability_conversation_turn_final_state(
    conversation_id: str,
    message_id: str,
    admin: AdminUser,
    conversations: ConversationRepository = Depends(get_conversation_repo),
    messages_repo: MessageRepository = Depends(get_message_repo),
    journal_repo: TurnJournalRepository = Depends(get_turn_journal_repo),
) -> AdminReplayTurnFinalState:
    """On-demand user-end final state for one 会话复盘 assistant turn.

    Same pair as ``MessageDetail.runs`` + ``project_turn`` of its events. The
    conversation list omits this pair so opening a long thread stays on the
    compressed spans/runs view. Soft-deleted conversations are readable.
    """
    conv = await conversations.get_by_id_unscoped(
        conversation_id, include_deleted=True
    )
    if conv is None:
        raise NotFoundError("对话不存在")
    message = await messages_repo.get_by_id(message_id, conversation_id=conversation_id)
    if message is None:
        raise NotFoundError("消息不存在")
    if message.role != "assistant":
        return AdminReplayTurnFinalState(message_id=message_id)
    journal = await journal_repo.load_owned(message_id, conversation_id)
    _, projected, runs_payload = fold_replay_journal(journal)
    return AdminReplayTurnFinalState(
        message_id=message_id,
        runs_payload=runs_payload,
        projected=projected,
    )
