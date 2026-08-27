"""Orphan 热路 pending 交互（提问确认交互统一 P1 · D6）.

四触发点：
1. turn 结束 finally（防御性）
2. lease sweeper / 启动恢复（先 orphan 再 recover）
3. resolve 端点兜底（journal 有 required、无 Future → 410）
4. regenerate / stop

规则：只 orphan 热路四 kind；``awaiting=ceo`` 不 orphan；断连 Future 存活不 orphan。
"""

from __future__ import annotations

import contextlib
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.events import interaction_orphaned
from agentcore.runtime.interaction import (
    default_interaction_registry,
    is_hot_user_pending_kind,
)
from agentcore.runtime.journal.pending_interactions import (
    PendingInteraction,
    fold_pending_interactions,
)
from agentcore.runtime.settlement import prewrite_settlement, prewrite_settlement_direct

logger = get_logger(__name__)


def _should_orphan_pending(pending: PendingInteraction) -> bool:
    return is_hot_user_pending_kind(pending.kind, pending.payload)


def has_hot_user_pending(conversation_id: str | None) -> bool:
    """True when registry has user-side hot pending (approval / auth / user escalation)."""
    cid = (conversation_id or "").strip()
    if not cid:
        return False
    registry = default_interaction_registry()
    for req in registry.list_pending(cid):
        if is_hot_user_pending_kind(req.kind.value, req.payload):
            return True
    return False


def holds_for_hot_user(session: Any) -> bool:
    """Harvest / DRIVE_CANCELLED close must wait while a user-side hot card is up.

    ``user_stopped`` is excluded so Stop / regenerate still cancel + harvest.
    """
    if getattr(session, "user_stopped", False):
        return False
    return has_hot_user_pending(getattr(session, "conversation_id", None))


def suppress_drive_cancelled_wake(session: Any) -> bool:
    """Skip posting DRIVE_CANCELLED (soft_stop hang-frame, or hot user card)."""
    if getattr(session, "soft_stop", False):
        return True
    return holds_for_hot_user(session)


def hot_pending_card_labels(conversation_id: str | None) -> list[str]:
    """``tool_name`` when present, else interaction kind. Stable, de-duplicated."""
    cid = (conversation_id or "").strip()
    if not cid:
        return []
    labels: list[str] = []
    seen: set[str] = set()
    registry = default_interaction_registry()
    for req in registry.list_pending(cid):
        if not is_hot_user_pending_kind(req.kind.value, req.payload):
            continue
        tool = str((req.payload or {}).get("tool_name") or "").strip()
        label = tool or req.kind.value
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return labels


def format_hot_pending_hold_line(conversation_id: str | None) -> str:
    """CEO brief: 等你允许, name the card, team still live, do not wrap up."""
    labels = hot_pending_card_labels(conversation_id)
    if labels:
        named = "、".join(f"`{n}`" for n in labels)
        where = f"卡在 {named}"
    else:
        where = "有队员在等用户审批/授权"
    return (
        f"状态：等你允许（{where}）。队还在，不是收场。"
        "向用户报告阻塞后可 wait 听团；禁止空 wait 假装推进；"
        "禁止把团队说成已结束或调度中断。"
    )


async def wait_hot_user_pending_change(
    conversation_id: str | None, *, timeout: float = 1.0
) -> None:
    """Wait until a hot pending future settles, or ``timeout`` to recheck flags."""
    import asyncio

    cid = (conversation_id or "").strip()
    if not cid:
        await asyncio.sleep(timeout)
        return
    registry = default_interaction_registry()
    futs = [
        req.future
        for req in registry.list_pending(cid)
        if is_hot_user_pending_kind(req.kind.value, req.payload) and not req.future.done()
    ]
    if not futs:
        await asyncio.sleep(min(0.2, timeout))
        return
    await asyncio.wait(futs, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)


async def emit_orphan_fact(
    *,
    interaction_id: str,
    kind: str,
    turn_id: str | None = None,
    conversation_id: str | None = None,
    trace_id: str | None = None,
    prefer_direct: bool = False,
    reason: str | None = None,
) -> None:
    """Write ``interaction_orphaned`` (settlement 预写路径；失败只记日志不抛)."""
    event = interaction_orphaned(
        interaction_id=interaction_id, kind=kind, reason=reason
    )
    try:
        if prefer_direct and turn_id and conversation_id:
            await prewrite_settlement_direct(
                turn_id=turn_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
                event=event,
            )
            return
        written = await prewrite_settlement(event)
        if not written and turn_id and conversation_id:
            await prewrite_settlement_direct(
                turn_id=turn_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
                event=event,
            )
    except Exception as e:  # noqa: BLE001 — orphan 不得阻断主路径
        logger.warning(
            "interaction.orphan_write_failed",
            interaction_id=interaction_id,
            kind=kind,
            conversation_id=conversation_id,
            turn_id=turn_id,
            error=str(e),
        )


async def orphan_live_turn_hot_pending(conversation_id: str) -> list[str]:
    """Orphan hot pending before stop / regenerate (触发点④).

    Reads the **live** turn's ``sink.message_id`` as journal ``turn_id`` (not the
    regenerate target user-message id). HTTP handlers have no ContextVar journal
    writer, so ``prefer_direct`` is set when a live ``turn_id`` is available.
    """
    from agentcore.core.log_context import get_log_value
    from agentcore.runtime.turn.runs import turn_runs

    live = turn_runs.get(conversation_id)
    turn_id: str | None = None
    sink = None
    if live is not None:
        mid = getattr(live.sink, "message_id", None) or ""
        turn_id = mid.strip() or None
        sink = live.sink
    trace_raw = get_log_value("trace_id")
    trace_id = trace_raw.strip() or None if isinstance(trace_raw, str) else None
    return await orphan_registry_pending(
        conversation_id,
        turn_id=turn_id,
        trace_id=trace_id,
        prefer_direct=bool(turn_id),
        sink=sink,
    )


async def orphan_registry_pending(
    conversation_id: str,
    *,
    turn_id: str | None = None,
    trace_id: str | None = None,
    prefer_direct: bool = False,
    sink: Any | None = None,
) -> list[str]:
    """Orphan in-process hot pending for a conversation (turn-end / stop).

    Discards registry entries after writing orphan facts. Returns orphaned ids.
    Does NOT cancel Futures with a result — callers that stop the turn cancel the
    task; this only marks journal + clears registry so recovery won't show fake cards.

    ``prefer_direct=True`` for HTTP stop (no ContextVar journal writer). When
    ``sink`` is provided, also emit ``interaction_orphaned`` SSE (best-effort;
    emit failures never raise).
    """
    registry = default_interaction_registry()
    orphaned: list[str] = []
    for req in list(registry.list_pending(conversation_id)):
        kind = req.kind.value
        if not is_hot_user_pending_kind(kind, req.payload):
            continue
        await emit_orphan_fact(
            interaction_id=req.id,
            kind=kind,
            turn_id=turn_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            prefer_direct=prefer_direct,
        )
        if sink is not None:
            with contextlib.suppress(Exception):
                sink.emit(
                    interaction_orphaned(interaction_id=req.id, kind=kind, reason=None)
                )
        # Cancel unsettled future so awaiters don't hang if turn teardown is slow.
        if not req.future.done():
            req.future.cancel()
        registry.discard(req.id)
        orphaned.append(req.id)
    if orphaned:
        logger.info(
            "interaction.orphaned_registry",
            conversation_id=conversation_id,
            count=len(orphaned),
            ids=orphaned,
        )
    return orphaned


async def orphan_journal_pending(
    *,
    turn_id: str,
    conversation_id: str,
    entries: list[dict[str, Any]],
    trace_id: str | None = None,
) -> list[str]:
    """Orphan journal-fold pending for a turn (lease recover / resolve兜底)."""
    pending = fold_pending_interactions(entries, message_id=turn_id)
    orphaned: list[str] = []
    for item in pending:
        if not _should_orphan_pending(item):
            continue
        # Future 仍在 → 断连≠失效，跳过
        registry = default_interaction_registry()
        live = registry.get(item.id)
        if live is not None and not live.future.done():
            continue
        await emit_orphan_fact(
            interaction_id=item.id,
            kind=item.kind,
            turn_id=turn_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            prefer_direct=True,
        )
        orphaned.append(item.id)
    if orphaned:
        logger.info(
            "interaction.orphaned_journal",
            turn_id=turn_id,
            count=len(orphaned),
            ids=orphaned,
        )
    return orphaned


async def orphan_turn_before_recover(
    *,
    turn_id: str,
    conversation_id: str,
    trace_id: str | None = None,
) -> list[str]:
    """Lease sweeper 入口：先 orphan 该 turn 热路 pending，再由调用方 recover."""
    from agentcore.db.base import async_session_factory
    from agentcore.db.repositories import TurnJournalRepository

    async with async_session_factory() as db:
        entries = await TurnJournalRepository(db).load(turn_id)
    return await orphan_journal_pending(
        turn_id=turn_id,
        conversation_id=conversation_id,
        entries=entries,
        trace_id=trace_id,
    )
