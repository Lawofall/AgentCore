"""User context cut (方案切点): retire exploration before one message.

Auto compaction keeps a recency window of original text and only folds what
falls outside it. A cut ignores that window: everything still in the model
window before the chosen turn goes into the same rolling summary. The chosen
message stays verbatim. An assistant cut also keeps the user message that
opened that turn, so the assistant-role summary is not followed by another
assistant.

Preview runs the production summarizer and caches that a fold was shown.
Commit stores the prose the user confirmed and reattaches this fold's
identity ledger. It does not call the model again.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.billing.gate import BackgroundLlmSkip, run_compaction_llm
from agentcore.config import settings
from agentcore.conversation.compact_prompt import (
    IDENTITY_LEDGER_FENCE,
    attach_identity_ledger,
    merge_identity_items,
    parse_identity_ledger,
    render_identity_ledger,
    strip_identity_ledger,
)
from agentcore.conversation.context_cut_plan import (
    CHOSEN_RUNNING,
    NOTHING_TO_UNDO,
    PAUSED,
    PREVIEW_STALE,
    STILL_RUNNING,
    SUMMARY_EMPTY,
    SUMMARY_UNAVAILABLE,
    TEAM_OPEN,
    UNDO_MOVED,
    ContextCutError,
    accept_cut_prose,
    as_undo_dict,
    context_cut_undoable,
    fold_digest,
    parse_optional_dt,
    resolve_verbatim_start,
    take_fold,
    undo_snapshot,
)
from agentcore.conversation.store import MESSAGE_STATUS_RUNNING
from agentcore.core.errors import ConflictError, NotFoundError
from agentcore.core.logging import get_logger
from agentcore.db.models import Conversation, Message, PausedTurnRow, TurnLeaseRow
from agentcore.db.repositories import ConversationRepository, MessageRepository
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.factory import build_provider

logger = get_logger(__name__)

_PREVIEW_TTL_SECONDS = 600.0
_previews: dict[tuple[str, str], tuple[float, str]] = {}


@dataclass(frozen=True)
class CutPlan:
    fold: tuple[Message, ...]
    verbatim_id: str
    fold_through: datetime
    digest: str
    old_summary: str
    user_id: str


@dataclass(frozen=True)
class ContextCutPreview:
    summary: str
    keep_message_id: str
    fold_through: datetime
    fold_digest: str
    folded_count: int


def remember_preview(conversation_id: str, digest: str, summary: str) -> None:
    now = time.monotonic()
    expired = [key for key, (at, _) in _previews.items() if now - at > _PREVIEW_TTL_SECONDS]
    for key in expired:
        _previews.pop(key, None)
    _previews[(conversation_id, digest)] = (now, summary)


def lookup_preview(conversation_id: str, digest: str) -> str | None:
    item = _previews.get((conversation_id, digest))
    if item is None:
        return None
    at, summary = item
    if time.monotonic() - at > _PREVIEW_TTL_SECONDS:
        _previews.pop((conversation_id, digest), None)
        return None
    return summary


def drop_preview(conversation_id: str, digest: str) -> None:
    _previews.pop((conversation_id, digest), None)


def _refuse(exc: ContextCutError) -> ConflictError:
    return ConflictError(exc.message)


async def _assert_idle(session: AsyncSession, conversation_id: str) -> None:
    from agentcore.runtime.coordination.session import (
        registered_coordination_for_conversation,
    )

    team = registered_coordination_for_conversation(conversation_id)
    if (
        team is not None
        and team.settled_via is None
        and (
            team.turn_attached
            or team.has_inflight_work()
            or bool(team.running_workers())
        )
    ):
        raise ConflictError(TEAM_OPEN)
    lease = await session.execute(
        select(TurnLeaseRow.message_id)
        .where(TurnLeaseRow.conversation_id == conversation_id)
        .limit(1)
    )
    if lease.scalar_one_or_none() is not None:
        raise ConflictError(STILL_RUNNING)
    paused = await session.execute(
        select(PausedTurnRow.message_id)
        .where(PausedTurnRow.conversation_id == conversation_id)
        .limit(1)
    )
    if paused.scalar_one_or_none() is not None:
        raise ConflictError(PAUSED)


async def load_cut_plan(
    session: AsyncSession, conversation_id: str, message_id: str
) -> tuple[Conversation, CutPlan]:
    conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
    if conv is None:
        raise NotFoundError("对话不存在")
    msg_repo = MessageRepository(session)
    chosen = await msg_repo.get_by_id(message_id, conversation_id=conversation_id)
    if chosen is None:
        raise NotFoundError("消息不存在")
    usage = chosen.usage if isinstance(chosen.usage, dict) else {}
    if usage.get("status") == MESSAGE_STATUS_RUNNING:
        raise _refuse(ContextCutError(CHOSEN_RUNNING))
    older, _more = await msg_repo.list_before(
        conversation_id, before=chosen.created_at, limit=8
    )
    try:
        verbatim = resolve_verbatim_start([*older, chosen], message_id)
    except ContextCutError as exc:
        raise _refuse(exc) from exc
    limit = settings.compaction_max_fold_messages
    rows, more = await msg_repo.list_strictly_between(
        conversation_id,
        before=verbatim.created_at,
        after=conv.compacted_through,
        limit=limit,
    )
    try:
        fold = take_fold(rows, max_fold=limit, more=more)
    except ContextCutError as exc:
        raise _refuse(exc) from exc
    plan = CutPlan(
        fold=tuple(fold),
        verbatim_id=str(verbatim.id),
        fold_through=fold[-1].created_at,
        digest=fold_digest(conv.compacted_through, fold),
        old_summary=conv.compaction_summary or "",
        user_id=str(conv.user_id),
    )
    return conv, plan


async def _fold_identity_ledger(plan: CutPlan) -> str:
    """Program-owned path/command ledger for this fold. No model call."""
    from agentcore.conversation.compaction import _assistant_turn_ids
    from agentcore.runtime.context.working_set import (
        identity_items_from_traces,
        load_fold_tool_traces,
    )

    turn_ids = _assistant_turn_ids(plan.fold)
    trace_rows = await load_fold_tool_traces(turn_ids)
    prior_ledger = ""
    if IDENTITY_LEDGER_FENCE in plan.old_summary:
        prior_ledger = plan.old_summary.split(IDENTITY_LEDGER_FENCE, 1)[1]
    prior_paths, prior_cmds = parse_identity_ledger(prior_ledger)
    new_paths, new_cmds = identity_items_from_traces(trace_rows)
    ledger_paths, ledger_cmds = merge_identity_items(
        prior_paths, prior_cmds, new_paths, new_cmds
    )
    return render_identity_ledger(paths=ledger_paths, commands=ledger_cmds)


async def _stored_summary(conversation_id: str, plan: CutPlan) -> str:
    from agentcore.conversation.compaction import (
        _assistant_turn_ids,
        _load_turn_journals,
        _summarize,
    )
    from agentcore.llm.resolve import resolve_turn_model as resolve_model
    from agentcore.runtime.context.working_set import build_fold_file_ledger

    turn_ids = _assistant_turn_ids(plan.fold)
    journals = await _load_turn_journals(turn_ids)
    file_ledger = await build_fold_file_ledger(turn_ids)
    identity_ledger = await _fold_identity_ledger(plan)

    async def _runner(credentials: LLMCredentials) -> str:
        model = resolve_model(credentials)
        provider = build_provider(credentials, purpose="platform_internal")
        try:
            return await _summarize(
                provider,
                plan.old_summary,
                plan.fold,
                model=model,
                conversation_id=conversation_id,
                user_waiting=True,
                file_ledger=file_ledger,
                journals=journals,
            )
        finally:
            close = getattr(provider, "close", None)
            if close is not None:
                await close()

    outcome = await run_compaction_llm(plan.user_id, conversation_id, runner=_runner)
    if isinstance(outcome, BackgroundLlmSkip):
        raise ConflictError(SUMMARY_UNAVAILABLE)
    prose = strip_identity_ledger(outcome.value or "").strip()
    if not prose:
        raise ConflictError(SUMMARY_EMPTY)
    return attach_identity_ledger(prose, identity_ledger)


async def preview_context_cut(
    session: AsyncSession, conversation_id: str, message_id: str
) -> ContextCutPreview:
    await _assert_idle(session, conversation_id)
    _conv, plan = await load_cut_plan(session, conversation_id, message_id)
    stored = await _stored_summary(conversation_id, plan)
    remember_preview(conversation_id, plan.digest, stored)
    logger.info(
        "context_cut.previewed",
        conversation_id=conversation_id,
        folded=len(plan.fold),
        summary_chars=len(stored),
    )
    return ContextCutPreview(
        summary=strip_identity_ledger(stored).strip(),
        keep_message_id=plan.verbatim_id,
        fold_through=plan.fold_through,
        fold_digest=plan.digest,
        folded_count=len(plan.fold),
    )


async def commit_context_cut(
    session: AsyncSession,
    conversation_id: str,
    *,
    message_id: str,
    fold_digest_value: str,
    summary: str,
) -> Conversation:
    await _assert_idle(session, conversation_id)
    conv, plan = await load_cut_plan(session, conversation_id, message_id)
    if plan.digest != fold_digest_value:
        raise ConflictError(PREVIEW_STALE)
    if lookup_preview(conversation_id, plan.digest) is None:
        raise ConflictError(PREVIEW_STALE)
    try:
        stored = accept_cut_prose(
            summary,
            await _fold_identity_ledger(plan),
            max_chars=settings.compaction_summary_char_budget,
        )
    except ContextCutError as exc:
        raise ConflictError(exc.message) from exc
    repo = ConversationRepository(session)
    await repo.apply_context_cut(
        conversation_id,
        summary=stored,
        compacted_through=plan.fold_through,
        undo=undo_snapshot(conv, cut_watermark=plan.fold_through),
    )
    drop_preview(conversation_id, plan.digest)
    fresh = await repo.get_by_id_unscoped(conversation_id)
    if fresh is None:
        raise NotFoundError("对话不存在")
    logger.info(
        "context_cut.committed",
        conversation_id=conversation_id,
        folded=len(plan.fold),
        summary_chars=len(stored),
    )
    return fresh


async def undo_context_cut(session: AsyncSession, conversation_id: str) -> Conversation:
    await _assert_idle(session, conversation_id)
    repo = ConversationRepository(session)
    conv = await repo.get_by_id_unscoped(conversation_id)
    if conv is None:
        raise NotFoundError("对话不存在")
    payload = as_undo_dict(getattr(conv, "context_cut_undo", None))
    if payload is None:
        raise ConflictError(NOTHING_TO_UNDO)
    if not context_cut_undoable(payload, conv.compacted_through):
        raise ConflictError(UNDO_MOVED)
    try:
        through = parse_optional_dt(payload.get("compacted_through"))
    except ContextCutError as exc:
        raise ConflictError(exc.message) from exc
    tokens = payload.get("input_tokens")
    await repo.restore_context_cut(
        conversation_id,
        summary=payload.get("summary") if isinstance(payload.get("summary"), str) else None,
        compacted_through=through,
        input_tokens=tokens if isinstance(tokens, int) else None,
    )
    fresh = await repo.get_by_id_unscoped(conversation_id)
    if fresh is None:
        raise NotFoundError("对话不存在")
    logger.info("context_cut.undone", conversation_id=conversation_id)
    return fresh
