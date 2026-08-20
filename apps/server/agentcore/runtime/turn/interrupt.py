"""Single closer for incomplete / interrupted turns (user stop · process kill · redrive).

Three historical writers (cancel salvage, lease sweeper, recover degrade) used to emit
divergent body copy and ``finish_reason`` values. All paths funnel here so terminal
metadata stays consistent (frontend maps ``interrupted`` → ``cancelled``). Body text is
streamed captain content only — stop / interrupt chrome is UI StatusStrip, not a
parenthetical suffix.

After the durable incomplete write, this closer also best-effort reconciles the turn
cost ledger (``cost.recorded`` + ``messages.cost`` + ``messages.usage`` tokens) so
/stop does not drop payroll or the bubble token split.
"""

from __future__ import annotations

import contextlib
from enum import StrEnum
from typing import Any

from agentcore.conversation.store.merge import MESSAGE_STATUS_INCOMPLETE, pick_monotonic_content
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import MessageRepository, TurnJournalRepository
from agentcore.runtime.events import FinishReason
from agentcore.runtime.events.stream_checkpointer import (
    CHANNEL_CAPTAIN_CONTENT,
    CHANNEL_CAPTAIN_REASONING,
)
from agentcore.runtime.journal import journal_entries_from_display_runs, persist_turn_journal
from agentcore.runtime.journal.entries import KIND_TURN_END

logger = get_logger(__name__)

_TERMINAL_FINISH = frozenset(
    {
        FinishReason.CANCELLED.value,
        FinishReason.INTERRUPTED.value,
    }
)


class TurnInterruptReason(StrEnum):
    USER_STOP = "user_stop"
    # Reserved for a termination we can actually evidence. Sweeps and stale-row
    # cleanups must NOT borrow it: a turn whose lease simply stopped beating is
    # indistinguishable here from one whose process died, and labelling the guess
    # 「process_kill」sent case 519270db's root-cause hunt down the wrong road twice.
    PROCESS_KILL = "process_kill"
    LEASE_EXPIRED = "lease_expired"
    REDRIVE_FAILED = "redrive_failed"
    # Newer message took the conversation slot. Not a user Stop — do not inherit
    # the USER_STOP silence whitelist.
    OVERLAP = "overlap"


def normalize_interrupt_reason(reason: str | TurnInterruptReason) -> TurnInterruptReason:
    """Map legacy sweeper / recover reason strings onto the closed enum."""
    if isinstance(reason, TurnInterruptReason):
        return reason
    raw = (reason or "").strip()
    if raw == TurnInterruptReason.USER_STOP.value:
        return TurnInterruptReason.USER_STOP
    if raw in (TurnInterruptReason.REDRIVE_FAILED.value, "redrive_unwired"):
        return TurnInterruptReason.REDRIVE_FAILED
    if raw == TurnInterruptReason.PROCESS_KILL.value:
        # Historical rows (and any caller that really did observe a kill).
        return TurnInterruptReason.PROCESS_KILL
    if raw == TurnInterruptReason.OVERLAP.value:
        return TurnInterruptReason.OVERLAP
    # no_dag / lease_expired / unknown → we found it dead, we did not see it die
    return TurnInterruptReason.LEASE_EXPIRED


def finish_reason_for(reason: TurnInterruptReason) -> FinishReason:
    if reason is TurnInterruptReason.USER_STOP:
        return FinishReason.CANCELLED
    return FinishReason.INTERRUPTED


# 案 20260803-fake-dispatch-stall-claim · C：redrive_failed 禁止静默清队无说明。
# 有正文时 USER_STOP / PROCESS_KILL 仍只靠 metadata + StatusStrip；本原因必须正文可见。
#
# 文案只讲用户能判断的三件事：发生了什么、影响是什么、他能做什么。旧稿「本轮未完工（团队已清）」
# 里的「已清」是内部动作（未恢复的队员状态被丢弃），用户读到只会想「清掉的是我的文件吗、是队员
# 写的东西吗」——而崩溃后他最想确认的恰恰是这个，所以这里必须正面回答「都还在」。
REDRIVE_FAILED_USER_VISIBLE = (
    "【中断说明】服务中断后没能接着把这一轮跑完——队员都已停下，这一轮不会再有新进展。"
    "已经写进工作区的文件、以及上面已经产出的内容都还在。"
    "直接发下一条就能继续，也可以让我接着没做完的部分往下做。"
)

# 旧稿留下的正文标记：升级前已挂上说明的回合再次收口时，靠它保持幂等（勿删）。
_LEGACY_REDRIVE_MARKERS = ("后台恢复失败",)

# 案 519270db-team-synthesis-discarded-empty-bubble：团队跑满 19 分钟、delegate 综述已产出，
# CEO 终稿尚未开写就被清扫收口 → 用户拿到 0 字空泡，连「失败了」都无从得知。StatusStrip 的
# chrome 救不了一个空气泡：历史里它就是一条什么都没说的助手消息。
INTERRUPTED_EMPTY_USER_VISIBLE = "【中断说明】本轮意外中断，未产出回复。可直接发送下一条继续。"

OVERLAP_EMPTY_USER_VISIBLE = (
    "【中断说明】本轮被你的新消息打断，未产出回复。可直接发送下一条继续，或说明要接着刚才的部分。"
)
HARVEST_YIELD_EMPTY_USER_VISIBLE = (
    "【中断说明】后台工作结束后没能完成收口说明，未产出回复。"
    "已有进展仍在对话和工作区里。可直接发送下一条继续。"
)
MAX_ROUNDS_EMPTY_USER_VISIBLE = (
    "【中断说明】本轮已达回复轮次上限，未产出回复。可直接发送下一条继续，或缩小范围再试。"
)
TOKEN_BUDGET_EMPTY_USER_VISIBLE = (
    "【中断说明】本轮已达回复长度上限，未产出回复。可直接发送下一条继续，或缩小范围再试。"
)

_EMPTY_CLOSE_BY_REASON: dict[str, str] = {
    TurnInterruptReason.USER_STOP.value: "",
    TurnInterruptReason.OVERLAP.value: OVERLAP_EMPTY_USER_VISIBLE,
    TurnInterruptReason.LEASE_EXPIRED.value: INTERRUPTED_EMPTY_USER_VISIBLE,
    TurnInterruptReason.PROCESS_KILL.value: INTERRUPTED_EMPTY_USER_VISIBLE,
    "harvest_yield": HARVEST_YIELD_EMPTY_USER_VISIBLE,
    "max_rounds": MAX_ROUNDS_EMPTY_USER_VISIBLE,
    "token_budget": TOKEN_BUDGET_EMPTY_USER_VISIBLE,
}


def empty_close_user_visible(reason: str | TurnInterruptReason) -> str:
    """User-visible body when a close path has no streamed captain text.

    Only reasons listed in ``_EMPTY_CLOSE_BY_REASON`` produce copy. USER_STOP
    is silent (the user pressed stop). Unregistered reasons — engine early-stop
    such as unproductive / validation_thrash — return empty; FinishReason
    carries the semantics. Do not invent a fallback sentence for them.
    """
    key = reason.value if isinstance(reason, TurnInterruptReason) else (reason or "").strip()
    if key == TurnInterruptReason.USER_STOP.value:
        return ""
    return _EMPTY_CLOSE_BY_REASON.get(key, "")


def compose_interrupt_body(content: str, *, reason: TurnInterruptReason) -> str:
    """Return captain text for an interrupted turn.

    USER_STOP / PROCESS_KILL: streamed content only — stop/interrupt chrome stays in
    message metadata + StatusStrip (no parenthetical body notes). Truncate at the
    first DSML open tag so unfinished tool XML never enters the incomplete bubble
    (``upsert_assistant`` still runs sanitize + length ceiling).

    Nothing streamed is the exception: an empty bubble states nothing at all, so it
    gets the honesty note. Silence is whitelisted to USER_STOP alone — the user
    pressed stop and already knows why the turn ended; every other way a turn can die
    owes them words, including reasons added later.

    REDRIVE_FAILED: always leave a user-visible honesty note in the body so a
    kickoff bubble cannot freeze as「已开工」while the team was silently cleared.
    """
    from agentcore.runtime.engine.tool_protocol_sanitize import prepare_assistant_content

    text = prepare_assistant_content((content or "").strip(), salvage=True)
    if reason is not TurnInterruptReason.REDRIVE_FAILED:
        if not text:
            return empty_close_user_visible(reason)
        return text
    if REDRIVE_FAILED_USER_VISIBLE in text or any(
        marker in text for marker in _LEGACY_REDRIVE_MARKERS
    ):
        return text
    if not text:
        return REDRIVE_FAILED_USER_VISIBLE
    return f"{text}\n\n{REDRIVE_FAILED_USER_VISIBLE}"


def _journal_has_turn_end(entries: list[dict] | None) -> bool:
    return any((e.get("kind") or "") == KIND_TURN_END for e in (entries or []))


def _already_terminal_incomplete(meta: dict[str, Any] | None) -> bool:
    if not isinstance(meta, dict):
        return False
    status = meta.get("status")
    finish = meta.get("finish_reason")
    incomplete = meta.get("incomplete") is True or status == MESSAGE_STATUS_INCOMPLETE
    return incomplete and finish in _TERMINAL_FINISH


async def _reconcile_interrupted_turn_cost(
    *,
    message_id: str,
    conversation_id: str,
    trace_id: str | None,
) -> None:
    """Best-effort turn ledger reconcile after interrupt close (stop / sweeper / kill).

    Successful LLM calls usually already sit in ``cost_calls``; interrupt closers
    historically skipped turn-end reconcile, so ``cost.recorded`` / ``messages.cost``
    never landed, and ``messages.usage`` kept only incomplete chrome (no token
    fields). Reuse the same ``reconcile_turn_cost_ledger`` + ``log_cost_recorded``
    path as cloud finalize with empty ``cost_runs`` (no forged orphans — vision sink
    may still be lost on cancel). Stamp ledger token totals onto ``messages.usage``
    *before* ``messages.cost`` so a crash between the two writes retries (cost not
    yet stamped). Skip emit when ``messages.cost`` is already stamped so a second
    closer does not double-log ``cost.recorded``.
    """
    from agentcore.billing.turn_ledger import (
        drain_cost_ledger_before_reconcile,
        reconcile_turn_cost_ledger,
    )
    from agentcore.conversation.common import log_cost_recorded
    from agentcore.db.repositories import ConversationRepository, MessageRepository
    from agentcore.runtime.costing import aggregate_cost, aggregate_usage_tokens

    # Drain before main-pool session (same discipline as cloud finalize / handoff).
    ledger_drained = await drain_cost_ledger_before_reconcile(
        conversation_id=conversation_id,
        message_id=message_id,
    )
    async with async_session_factory() as session:
        conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
        if conv is None or not conv.user_id:
            logger.warning(
                "cost.interrupt_reconcile_skipped",
                conversation_id=conversation_id,
                message_id=message_id,
                reason="conversation_missing",
            )
            return
        msg_repo = MessageRepository(session)
        existing = await msg_repo.get_by_id(message_id, conversation_id=conversation_id)
        if existing is not None and existing.cost:
            # Already stamped (prior interrupt reconcile or a later finalize) — DB
            # reconcile is idempotent, but cost.recorded must not double-emit.
            return
        try:
            ledger_rows = await reconcile_turn_cost_ledger(
                session,
                drained=ledger_drained,
                user_id=str(conv.user_id),
                conversation_id=conversation_id,
                message_id=message_id,
                cost_runs=[],
                trace_id=trace_id,
            )
        except Exception as e:
            await session.rollback()
            logger.warning(
                "cost.ledger_write_failed",
                conversation_id=conversation_id,
                message_id=message_id,
                error=str(e),
                source="interrupt",
            )
            return
        if not ledger_rows:
            return
        log_cost_recorded(conversation_id, message_id, ledger_rows)
        try:
            # Tokens first: ``set_cost`` is the skip latch. A crash after cost but
            # before usage would leave the bubble empty on retry (early return).
            await msg_repo.merge_usage(
                message_id,
                conversation_id=conversation_id,
                usage=aggregate_usage_tokens(ledger_rows),
            )
            await msg_repo.set_cost(
                message_id,
                conversation_id=conversation_id,
                cost=dict(aggregate_cost(ledger_rows)),
            )
        except Exception as e:
            await session.rollback()
            logger.warning(
                "cost.message_column_write_failed",
                conversation_id=conversation_id,
                message_id=message_id,
                error=str(e),
                source="interrupt",
            )


async def settle_prior_running_assistants(
    *,
    conversation_id: str,
    keep_message_id: str | None = None,
) -> int:
    """Durable-close earlier non-paused RUNNING assistants before a new attempt.

    Covers dead-registry / no-lease zombies that overlap cancel cannot see (e.g.
    empty-journal cancel that released the lease). Uses
    :class:`TurnInterruptReason.LEASE_EXPIRED` → ``finish_reason=interrupted``
    (supersede semantics) — finding a stale RUNNING row is not evidence of a kill.
    Pause latches (``usage.paused`` / ``paused_turns``)
    are left alone. Best-effort: per-row failures are logged, not raised.
    Returns how many closes reported success (including already-terminal).
    """
    from agentcore.db.repositories import MessageRepository, PausedTurnRepository

    try:
        async with async_session_factory() as session:
            rows = await MessageRepository(session).list_non_paused_running_assistants(
                conversation_id,
                exclude_message_id=keep_message_id,
            )
            paused_ids: set[str] = set()
            for row in rows:
                frame = await PausedTurnRepository(session).get(row.id)
                if frame is not None:
                    paused_ids.add(row.id)
    except Exception as e:  # noqa: BLE001 — never block a new turn on settle lookup
        logger.warning(
            "turn.prior_running_list_failed",
            conversation_id=conversation_id,
            error=str(e),
        )
        return 0

    closed = 0
    for row in rows:
        if row.id in paused_ids:
            continue
        ok = False
        try:
            ok = await close_turn_interrupted(
                message_id=row.id,
                conversation_id=conversation_id,
                trace_id=getattr(row, "trace_id", None),
                reason=TurnInterruptReason.LEASE_EXPIRED,
                load_stream_state=True,
            )
        except Exception as e:  # noqa: BLE001 — continue remaining rows
            logger.warning(
                "turn.prior_running_settle_failed",
                conversation_id=conversation_id,
                message_id=row.id,
                error=str(e),
            )
            ok = False
        if ok:
            closed += 1
            logger.info(
                "turn.prior_running_settled",
                conversation_id=conversation_id,
                message_id=row.id,
                keep_message_id=keep_message_id,
            )
    return closed


async def close_turn_interrupted(
    *,
    message_id: str,
    conversation_id: str,
    reason: str | TurnInterruptReason,
    trace_id: str | None = None,
    content: str | None = None,
    reasoning_content: str | None = None,
    journal: list[dict[str, Any]] | None = None,
    load_stream_state: bool = False,
) -> bool:
    """Write incomplete + terminal ``turn_end`` for an interrupted turn.

    Returns ``True`` when the close write completed (or was already terminal).
    Returns ``False`` on hard failure so callers can keep the orphaned lease.
    """
    resolved = normalize_interrupt_reason(reason)
    finish = finish_reason_for(resolved)
    if not message_id:
        return False

    try:
        body_content = content
        body_reasoning = reasoning_content
        seg_content = ""
        seg_reasoning = ""
        if load_stream_state:
            from agentcore.conversation.store import get_cloud_store

            segments = await get_cloud_store().list_stream_segments(turn_id=message_id)
            by_ch = {s["channel"]: s.get("text") or "" for s in segments}
            seg_content = by_ch.get(CHANNEL_CAPTAIN_CONTENT) or ""
            seg_reasoning = by_ch.get(CHANNEL_CAPTAIN_REASONING) or ""

        async with async_session_factory() as session:
            existing = await MessageRepository(session).get_by_id(
                message_id, conversation_id=conversation_id
            )
            existing_usage = existing.usage if existing is not None else None
            resolved_trace = trace_id or (existing.trace_id if existing is not None else None)

            skip_upsert = _already_terminal_incomplete(
                existing_usage if isinstance(existing_usage, dict) else None
            )

            if not skip_upsert:
                existing_content = existing.content if existing else None
                existing_reasoning = existing.reasoning_content if existing else None
                if load_stream_state:
                    raw = pick_monotonic_content(existing_content, seg_content)
                    # Passed salvage (e.g. content_reset stash) must join the
                    # monotonic pick — load_stream_state alone used to ignore
                    # ``content`` and could persist an empty body after reset.
                    if body_content is not None:
                        raw = pick_monotonic_content(raw, body_content)
                    body_reasoning = (
                        pick_monotonic_content(existing_reasoning, seg_reasoning) or None
                    )
                else:
                    raw = body_content if body_content is not None else (existing_content or "")
                    if body_reasoning is None and existing_reasoning:
                        body_reasoning = existing_reasoning
                body = compose_interrupt_body(raw or "", reason=resolved)
                await MessageRepository(session).upsert_assistant(
                    conversation_id=conversation_id,
                    message_id=message_id,
                    content=body,
                    reasoning_content=body_reasoning,
                    trace_id=resolved_trace,
                    metadata={
                        "status": MESSAGE_STATUS_INCOMPLETE,
                        "incomplete": True,
                        "finish_reason": finish.value,
                        "interrupt_reason": resolved.value,
                    },
                    merge=True,
                )

            if journal is not None:
                # Best-effort display salvage. Progressive append-on-emit may already own
                # denser seqs; merge-mode persist (seq=0..n) can no-op and drop turn_end —
                # the ensure-append below is the reliable closer. Never wholesale-replace:
                # this snapshot is a sparse display view, not the full fact stream.
                await persist_turn_journal(
                    session,
                    message_id=message_id,
                    conversation_id=conversation_id,
                    trace_id=resolved_trace or "",
                    entries=journal_entries_from_display_runs(
                        {
                            "events": journal,
                            "finish_reason": finish.value,
                        }
                    ),
                    replace=False,
                )
            # turn_end 必写：message 终态与 fold 的 finish_reason 同源。后台 execution
            # 事实在收口后继续追加（批次1）是特性——这里只保证收口时终态事实已落盘。
            entries = await TurnJournalRepository(session).load_owned(message_id, conversation_id)
            if not _journal_has_turn_end(entries or []):
                await TurnJournalRepository(session).append(
                    turn_id=message_id,
                    seq=None,
                    conversation_id=conversation_id,
                    trace_id=resolved_trace,
                    entry={
                        "kind": KIND_TURN_END,
                        "payload": {"finish_reason": finish.value},
                        "ts": None,
                    },
                )

        with contextlib.suppress(Exception):
            from agentcore.conversation.store import get_cloud_store

            await get_cloud_store().clear_stream_segments(turn_id=message_id)

        # Durable incomplete chrome is committed; stamp payroll next (best-effort).
        with contextlib.suppress(Exception):
            await _reconcile_interrupted_turn_cost(
                message_id=message_id,
                conversation_id=conversation_id,
                trace_id=resolved_trace,
            )

        logger.info(
            "turn.interrupt_closed",
            message_id=message_id,
            conversation_id=conversation_id,
            reason=resolved.value,
            finish_reason=finish.value,
            skipped_upsert=skip_upsert,
        )
        return True
    except Exception as e:  # noqa: BLE001 — caller decides lease retention
        logger.warning(
            "turn.interrupt_close_failed",
            message_id=message_id,
            conversation_id=conversation_id,
            reason=str(resolved),
            error=str(e),
        )
        return False
