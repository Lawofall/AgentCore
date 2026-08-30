"""Resolve a stage_card: start_debate (mechanism-direct) or research_first (CEO 回灌)."""

from __future__ import annotations

import contextlib
import time
from typing import Any

from agentcore.conversation.common import (
    resolve_local_binding,
    resolve_permission_axes,
    resolve_profile_set,
)
from agentcore.conversation.history import load_chat_context
from agentcore.conversation.turn_backend import build_turn_backend
from agentcore.conversation.turn_persistence import (
    create_assistant_placeholder,
    persist_turn_result,
)
from agentcore.conversation.turn_runner import (
    run_and_persist,
    session_callbacks,
    suspension_callbacks,
)
from agentcore.core.log_context import get_log_value, log_context, new_trace_id
from agentcore.core.logging import get_logger
from agentcore.core.types import new_id
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import BoardRepository, ConversationRepository, TurnJournalRepository
from agentcore.llm.resolve import LLMCredentials
from agentcore.runtime.events import EventSink, stage_card_resolved
from agentcore.runtime.kickoff.stage_card import (
    apply_motion_override,
    research_first_bootstrap,
    research_first_user_message,
)
from agentcore.runtime.pipeline.stage_card_debate import run_stage_card_debate_pipeline
from agentcore.runtime.settlement import already_settled_in_writer, prewrite_settlement_direct

logger = get_logger(__name__)

# 会话时间序扫描窗口：最近 N 个 distinct turn（非 in-turn seq）。
_RECENT_TURN_SCAN_LIMIT = 40

async def recent_conversation_turn_ids(
    conversation_id: str, *, limit: int = _RECENT_TURN_SCAN_LIMIT
) -> list[str]:
    """Shared helper: newest-first distinct turn_ids by session time (not in-turn seq)."""
    async with async_session_factory() as db:
        return await TurnJournalRepository(db).list_recent_turn_ids(
            conversation_id, limit=limit
        )

async def load_stage_card_pending(
    conversation_id: str, stage_card_id: str
) -> tuple[str, dict[str, Any]] | None:
    """Find pending stage_card in recent journals → (turn_id, payload) or None."""
    from agentcore.runtime.journal.pending_interactions import fold_interactions

    async with async_session_factory() as db:
        turn_ids = await TurnJournalRepository(db).list_recent_turn_ids(
            conversation_id, limit=_RECENT_TURN_SCAN_LIMIT
        )
        for turn_id in turn_ids:
            entries = await TurnJournalRepository(db).load(turn_id)
            for rec in fold_interactions(entries):
                if (
                    rec.kind == "stage_card"
                    and rec.id == stage_card_id
                    and rec.status == "pending"
                ):
                    return turn_id, dict(rec.payload)
    return None

async def prewrite_stage_card_resolved(
    *,
    turn_id: str,
    conversation_id: str,
    stage_card_id: str,
    decision: str,
    note: str = "",
    motion_override: str | None = None,
) -> None:
    event = stage_card_resolved(
        stage_card_id=stage_card_id,
        decision=decision,
        note=note,
        motion_override=motion_override,
    )
    if already_settled_in_writer(event):
        return
    # 同族 settlement 预写姿势：优先 log context 的 trace_id，取不到落 None。
    await prewrite_settlement_direct(
        turn_id=turn_id,
        conversation_id=conversation_id,
        trace_id=get_log_value("trace_id") or None,
        event=event,
    )

async def finalize_stage_card_start_debate(
    *,
    conversation_id: str,
    host_turn_id: str,
    stage_card_id: str,
    note: str = "",
    motion_override: str | None = None,
    sink: EventSink | None = None,
) -> None:
    """``debate.started``（真正开跑）后落 resolved + 清兄弟 pending。

    成功边界是主持人/计划落地开跑，不是整场辩论结束；中途失败不回 pending。
    """
    await prewrite_stage_card_resolved(
        turn_id=host_turn_id,
        conversation_id=conversation_id,
        stage_card_id=stage_card_id,
        decision="start_debate",
        note=note,
        motion_override=motion_override,
    )
    if sink is not None:
        with contextlib.suppress(Exception):
            sink.emit(
                stage_card_resolved(
                    stage_card_id=stage_card_id,
                    decision="start_debate",
                    note=note,
                    motion_override=motion_override,
                )
            )
    await orphan_sibling_stage_cards(
        conversation_id,
        keep_id=stage_card_id,
        sink=sink,
        reason="superseded",
    )

async def run_stage_card_start_debate(
    *,
    conversation_id: str,
    user_id: str,
    sink: EventSink,
    card: dict[str, Any],
    note: str = "",
    host_turn_id: str | None = None,
    stage_card_id: str | None = None,
    motion_override: str | None = None,
    llm_credentials: LLMCredentials | None = None,
    llm_supports_tools: bool | None = None,
    x_client_platform: str | None = None,
) -> None:
    """Persist synthetic user row + mechanism-direct debate turn.

    成功边界 = ``debate.started``（pipeline 内开跑即 finalize）；
    仅 kickoff/启动失败（未到 ``debate.started``）保持 pending 可重试；
    开跑后中途失败卡保持 resolved。
    """
    _ = llm_supports_tools
    message_id = new_id()
    attempt_id = new_id()
    trace_id = new_trace_id()
    motion = str(card.get("motion") or "").strip()
    user_text = f"按此开辩：{motion}" if motion else "按此开辩"
    card_id = (stage_card_id or str(card.get("stage_card_id") or "")).strip()
    card_for_run = dict(card)
    if host_turn_id:
        card_for_run["_host_turn_id"] = host_turn_id
    if card_id:
        card_for_run["stage_card_id"] = card_id
    if motion_override is not None:
        card_for_run["_motion_override"] = motion_override
    if note:
        card_for_run["_resolve_note"] = note

    async with async_session_factory() as session:
        conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
        if not conv:
            from agentcore.core.error_codes import ErrorCode
            from agentcore.runtime.events import error_event, message_end
            from agentcore.runtime.events.types import FinishReason

            sink.emit(error_event(ErrorCode.NOT_FOUND, "Conversation not found"))
            sink.emit(message_end(FinishReason.ERROR))
            return
        folder_id = conv.folder_id
        local_binding = await resolve_local_binding(session, conv)
        profile_set = await resolve_profile_set(session, conv, user_id)
        permission_axes = await resolve_permission_axes(session, conversation_id)

        board = await BoardRepository(session).get_by_conversation_id(
            conversation_id, user_id=user_id
        )
        board_id = board.id if board else None
        from agentcore.db.repositories import MessageRepository

        await MessageRepository(session).create(
            conversation_id=conversation_id,
            role="user",
            content=user_text,
        )
        history = await load_chat_context(session, conversation_id)

    backend = await build_turn_backend(
        user_id=user_id,
        conversation_id=conversation_id,
        folder_id=folder_id,
        sink=sink,
        local_binding=local_binding,
    )
    session_saver, session_loader = session_callbacks(conversation_id)
    suspension_saver, suspension_deleter = suspension_callbacks()

    started = time.monotonic()
    with log_context(
        trace_id=trace_id,
        conversation_id=conversation_id,
        user_id=user_id,
        attempt_id=attempt_id,
        message_id=message_id,
        agent_id="CEO",
        cost_role="captain",
        persona="CEO",
    ):
        await create_assistant_placeholder(
            conversation_id=conversation_id,
            message_id=message_id,
            trace_id=trace_id,
        )
        sink.bind_content_checkpoint(
            conversation_id=conversation_id,
            message_id=message_id,
        )
        debate_ok = False
        try:
            result = await run_stage_card_debate_pipeline(
                conversation_id=conversation_id,
                user_id=user_id,
                sink=sink,
                backend=backend,
                card=card_for_run,
                note=note,
                history=history[:-1] if history else [],
                folder_id=folder_id,
                board_id=board_id,
                permission_axes=permission_axes,
                profile_set=profile_set,
                llm_credentials=llm_credentials,
                session_saver=session_saver,
                session_loader=session_loader,
                suspension_saver=suspension_saver,
                suspension_deleter=suspension_deleter,
                message_id=message_id,
                x_client_platform=x_client_platform,
            )
            finish = str(result.get("finish_reason") or "")
            debate_ok = finish not in ("error", "interrupted") and not result.get("error")
            # finalize 已在 debate.started 边界完成；此处只区分启动失败 vs 开跑后失败。
            started = bool(result.get("stage_card_finalized"))
            if not debate_ok and card_id:
                from agentcore.core.error_codes import ErrorCode
                from agentcore.runtime.events import error_event

                if started:
                    err_msg = str(result.get("error") or "辩论开跑后未能完成。")
                    log_event = "stage_card.start_debate_failed_after_started"
                else:
                    err_msg = str(
                        result.get("error") or "开辩未能完成，推进卡仍可重试。"
                    )
                    log_event = "stage_card.start_debate_failed_kept_pending"
                with contextlib.suppress(Exception):
                    sink.emit(error_event(ErrorCode.LLM_ERROR, err_msg))
                logger.info(
                    log_event,
                    stage_card_id=card_id,
                    error=err_msg[:120],
                )
            await persist_turn_result(
                result=result,
                conversation_id=conversation_id,
                user_id=user_id,
                folder_id=folder_id,
                backend=backend,
                sink=sink,
                user_message=user_text,
                llm_credentials=llm_credentials,
                trace_id=trace_id,
                turn_id=attempt_id,
                duration_ms=int((time.monotonic() - started) * 1000),
                kind="turn",
            )
        except Exception as exc:
            logger.exception(
                "stage_card.start_debate_exception_kept_pending",
                stage_card_id=card_id or None,
                error=str(exc),
            )
            from agentcore.core.error_codes import ErrorCode
            from agentcore.runtime.events import error_event, message_end
            from agentcore.runtime.events.types import FinishReason

            try:
                sink.emit(
                    error_event(
                        ErrorCode.LLM_ERROR,
                        f"开辩失败，推进卡仍可重试：{exc}",
                    )
                )
                sink.emit(message_end(FinishReason.ERROR))
            except Exception:  # noqa: BLE001
                pass

async def run_stage_card_research_first(
    *,
    conversation_id: str,
    user_id: str,
    sink: EventSink,
    card: dict[str, Any],
    llm_credentials: LLMCredentials | None = None,
    llm_supports_tools: bool | None = None,
    x_client_platform: str | None = None,
) -> None:
    """Start a CEO turn with research_first imperative bootstrap."""
    motion = str(card.get("motion") or "").strip()
    user_text = research_first_user_message(motion=motion)
    bootstrap = research_first_bootstrap(motion=motion, user_message=user_text)
    # Prepend imperative so CEO sees the same skeleton as kickoff research_first.
    composed = f"{user_text}\n\n{bootstrap}"

    async with async_session_factory() as session:
        conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
        if not conv:
            from agentcore.core.error_codes import ErrorCode
            from agentcore.runtime.events import error_event, message_end
            from agentcore.runtime.events.types import FinishReason

            sink.emit(error_event(ErrorCode.NOT_FOUND, "Conversation not found"))
            sink.emit(message_end(FinishReason.ERROR))
            return
        folder_id = conv.folder_id
        local_binding = await resolve_local_binding(session, conv)
        profile_set = await resolve_profile_set(session, conv, user_id)
        permission_axes = await resolve_permission_axes(session, conversation_id)

        board = await BoardRepository(session).get_by_conversation_id(
            conversation_id, user_id=user_id
        )
        board_id = board.id if board else None
        from agentcore.db.repositories import MessageRepository

        await MessageRepository(session).create(
            conversation_id=conversation_id,
            role="user",
            content=user_text,
        )
        history = await load_chat_context(session, conversation_id)

    backend = await build_turn_backend(
        user_id=user_id,
        conversation_id=conversation_id,
        folder_id=folder_id,
        sink=sink,
        local_binding=local_binding,
    )
    await run_and_persist(
        conversation_id=conversation_id,
        user_message=composed,
        user_id=user_id,
        folder_id=folder_id,
        sink=sink,
        history=history[:-1] if history else [],
        attachments=None,
        backend=backend,
        llm_credentials=llm_credentials,
        profile_set=profile_set,
        permission_axes=permission_axes,
        board_id=board_id,
        llm_supports_tools=llm_supports_tools,
        x_client_platform=x_client_platform,
    )

def validate_start_debate_card(
    payload: dict[str, Any], motion_override: str | None
) -> tuple[dict[str, Any] | None, str]:
    """Return (merged_card, error). error non-empty ⇒ keep pending (inline 报错)."""
    return apply_motion_override(payload, motion_override)

async def list_pending_stage_cards(
    conversation_id: str,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Recent journals → ``[(host_turn_id, stage_card_id, payload), ...]`` still pending."""
    from agentcore.runtime.journal.pending_interactions import fold_interactions

    found: list[tuple[str, str, dict[str, Any]]] = []
    async with async_session_factory() as db:
        turn_ids = await TurnJournalRepository(db).list_recent_turn_ids(
            conversation_id, limit=_RECENT_TURN_SCAN_LIMIT
        )
        for turn_id in turn_ids:
            entries = await TurnJournalRepository(db).load(turn_id)
            for rec in fold_interactions(entries):
                if rec.kind == "stage_card" and rec.status == "pending":
                    found.append((turn_id, rec.id, dict(rec.payload)))
    return found

async def orphan_conversation_stage_cards(
    conversation_id: str,
    *,
    sink: EventSink | None = None,
    reason: str | None = None,
    exclude_ids: set[str] | None = None,
) -> list[str]:
    """Orphan pending stage_cards (journal fact on host turn + optional live SSE).

    收尾判定入口：调用方须已确认本回合未调 debate / 未起 MLR。
    """
    from agentcore.runtime.events import interaction_orphaned
    from agentcore.runtime.interaction_orphan import emit_orphan_fact

    skip = exclude_ids or set()
    pending = await list_pending_stage_cards(conversation_id)
    orphaned: list[str] = []
    for host_turn_id, card_id, _payload in pending:
        if card_id in skip:
            continue
        await emit_orphan_fact(
            interaction_id=card_id,
            kind="stage_card",
            turn_id=host_turn_id,
            conversation_id=conversation_id,
            prefer_direct=True,
            reason=reason,
        )
        if sink is not None:
            with contextlib.suppress(Exception):
                sink.emit(
                    interaction_orphaned(
                        interaction_id=card_id, kind="stage_card", reason=reason
                    )
                )
        orphaned.append(card_id)
    if orphaned:
        logger.info(
            "stage_card.orphaned",
            conversation_id=conversation_id,
            count=len(orphaned),
            ids=orphaned,
            reason=reason or "bypass",
        )
    return orphaned

async def orphan_sibling_stage_cards(
    conversation_id: str,
    *,
    keep_id: str,
    sink: EventSink | None = None,
    reason: str = "superseded",
) -> list[str]:
    """After consuming one card, orphan any other pending cards in the conversation."""
    kid = (keep_id or "").strip()
    return await orphan_conversation_stage_cards(
        conversation_id,
        sink=sink,
        reason=reason,
        exclude_ids={kid} if kid else None,
    )

async def maybe_orphan_stage_cards_at_turn_end(
    conversation_id: str,
    *,
    sink: EventSink | None = None,
) -> list[str]:
    """回合收尾：未调 debate 且未起 MLR → orphan pending stage_card（journal 事实）。"""
    from agentcore.runtime.kickoff.stage_card import turn_keeps_stage_card

    if turn_keeps_stage_card():
        return []
    return await orphan_conversation_stage_cards(conversation_id, sink=sink)

async def peek_pending_stage_card_for_debate(
    *,
    conversation_id: str,
    ceo_motion: str,
) -> tuple[dict[str, Any] | None, str | None, str, str, str]:
    """口头开赛：查 pending + motion 闸，**不** resolve。

    Returns ``(merged_card, motion_override, error, host_turn_id, card_id)``.
    Gate fail / no card → ``merged_card`` is None；闸失败保持 pending。
    """
    pending = await list_pending_stage_cards(conversation_id)
    if not pending:
        return None, None, "", "", ""
    # 同对话取最近一张 pending（list 按会话时间序 desc，先命中即最新）。
    host_turn_id, card_id, payload = pending[0]
    card_motion = str(payload.get("motion") or "").strip()
    ceo_text = (ceo_motion or "").strip()
    motion_override: str | None = None
    if ceo_text and card_motion and ceo_text != card_motion:
        motion_override = ceo_text
    override_arg = motion_override if motion_override is not None else None
    gate_motion = motion_override if motion_override is not None else None
    merged, err = apply_motion_override(payload, gate_motion)
    if err or merged is None:
        return None, motion_override, err or "`motion` 检定未通过。", host_turn_id, card_id
    if "stage_card_id" not in merged:
        merged = {**merged, "stage_card_id": card_id}
    return merged, override_arg, "", host_turn_id, card_id

async def consume_pending_stage_card_for_debate(
    *,
    conversation_id: str,
    ceo_motion: str,
    sink: EventSink | None = None,
) -> tuple[dict[str, Any] | None, str | None, str]:
    """口头开赛：pending 卡 → 合并参数（**``debate.started`` 前不 resolve**）。

    Returns ``(merged_card, motion_override_or_none, error)``.
    On error (gate fail / no card) ``merged_card`` is None; gate fail keeps card pending.
    调用方在 ``debate.started``（真正开跑）后须 ``finalize_stage_card_start_debate``；
    仅 kickoff/启动失败保持 pending 可重试。
    """
    from agentcore.runtime.kickoff.stage_card import mark_turn_keeps_stage_card

    merged, override_arg, err, host_turn_id, card_id = (
        await peek_pending_stage_card_for_debate(
            conversation_id=conversation_id, ceo_motion=ceo_motion
        )
    )
    if err or merged is None:
        return None, override_arg, err
    # 暂标记 keep，避免本回合收尾误 orphan；开辩失败须 clear。
    mark_turn_keeps_stage_card()
    # Stash host ids for finalize after successful debate.
    merged = {
        **merged,
        "stage_card_id": card_id,
        "_host_turn_id": host_turn_id,
        "_motion_override": override_arg,
    }
    logger.info(
        "stage_card.consume_prepared",
        stage_card_id=card_id,
        motion_override=bool(override_arg),
    )
    _ = sink  # SSE resolve 改在 finalize；保留参以兼容旧调用方
    return merged, override_arg, ""
