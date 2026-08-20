"""Classic in-flight turn steer (同对话再发 P1).

When a solo / non-coordination turn is mid-flight and the user sends
``delivery=steer``, the message is parked here (process-local) until the
captain ``react_loop`` drains it at the next ReAct step boundary and injects
it as a user-role LLM message — **not** a new turn, **not** a hard stop.

Acceptance window = captain ``react_loop`` lifetime for that conversation
(``begin_accepting`` … ``end_accepting``). Outside the window the API falls
back to FIFO ``turn_queue`` (may carry ``degraded_from=steer``).

Durable ack uses the shared ``user_interjection`` contract (经典:
``received`` → ``injected`` | ``queued`` | ``failed``；无 ``addressed``).
Process-local pending remains the inject buffer; the SSE/journal record is
what survives refresh.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentcore.conversation.mentions import format_agent_mention_prompt, wire_agent_mentions
from agentcore.core.logging import get_logger
from agentcore.core.types import new_id
from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.events import user_interjection
from agentcore.workspace.attachments import interjection_attachment_meta

logger = get_logger(__name__)

# Prefix so the model treats the inject as mid-turn correction, not a new task.
_STEER_USER_PREFIX = (
    "[用户中途补充] 以下是用户对当前任务的补充或纠偏。"
    "请继续完成当前任务，并把这些内容纳入后续步骤：\n\n"
)

_CONTENT_PREVIEW_MAX = 200


@dataclass(slots=True)
class PendingTurnSteer:
    """One classic mid-turn steer waiting for the next ReAct step boundary."""

    interjection_id: str
    conversation_id: str
    execution_id: str
    content: str
    user_id: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    agent_mentions: list[dict[str, Any]] = field(default_factory=list)
    requires_tools: bool = False
    x_client_platform: str | None = None
    # Survives promotion to the conversation queue so a leftover steer keeps
    # pointing at the machine that typed it (see fulfill/origin.py).
    origin_device_id: str | None = None
    llm_credentials: Any = None
    llm_supports_tools: bool | None = None


_pending: dict[str, list[PendingTurnSteer]] = {}
# conversation_id → execution_id while the captain loop is accepting.
_accepting: dict[str, str] = {}


def content_preview(content: str, *, max_len: int = _CONTENT_PREVIEW_MAX) -> str:
    """Truncate steer body for log previews."""
    text = (content or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len] + "…"


def _att_meta(attachments: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not attachments:
        return None
    meta = interjection_attachment_meta(list(attachments))
    return meta or None


def _format_steer_attachment_lines(attachments: list[dict[str, Any]]) -> list[str]:
    """Readable attachment inventory for injected user text (parity with coord inject)."""
    lines: list[str] = []
    for a in attachments:
        if not isinstance(a, dict):
            continue
        name = a.get("name")
        name = "?" if not isinstance(name, str) or not name.strip() else name.strip()
        wp = a.get("workspace_path") or ""
        path_bit = f" → {wp}" if isinstance(wp, str) and wp.strip() else ""
        mark = "（二进制）" if bool(a.get("binary")) else ""
        lines.append(f"附件：{name}{path_bit}{mark}")
    return lines


def format_steer_user_message(
    content: str,
    attachments: list[dict[str, Any]] | None = None,
    agent_mentions: list[dict[str, Any]] | None = None,
) -> str:
    """User-role text injected into the live LLM window.

    Attachments are surfaced as a readable inventory (LLMMessage is text-only here;
    same posture as coordination ``user_interjection`` brief lines). Agent mentions
    reuse the main-path soft-hint block (非强制派单 / 非硬路由).
    Never silently drop.
    """
    body = (content or "").strip()
    text = f"{_STEER_USER_PREFIX}{body}" if body else _STEER_USER_PREFIX.rstrip()
    att_lines = _format_steer_attachment_lines(list(attachments or []))
    if att_lines:
        text = f"{text}\n\n" + "\n".join(att_lines)
    mention = format_agent_mention_prompt(agent_mentions)
    if mention:
        text = f"{text}\n\n{mention}"
    return text


def is_accepting(conversation_id: str) -> bool:
    return bool(conversation_id.strip()) and conversation_id.strip() in _accepting


def accepting_execution_id(conversation_id: str) -> str | None:
    cid = conversation_id.strip()
    if not cid:
        return None
    eid = _accepting.get(cid)
    return eid if eid else None


def begin_accepting(conversation_id: str, *, execution_id: str = "") -> None:
    """Open the classic-steer window for this conversation (captain loop enter).

    Drops any stale pending from a prior crashed loop so we never inject orphans.
    """
    cid = conversation_id.strip()
    if not cid:
        return
    stale = _pending.pop(cid, None)
    if stale:
        logger.warning(
            "turn_steer.stale_cleared",
            conversation_id=cid,
            dropped=len(stale),
        )
    _accepting[cid] = (execution_id or "").strip()
    logger.debug("turn_steer.accepting_begin", conversation_id=cid)


def end_accepting(conversation_id: str) -> list[PendingTurnSteer]:
    """Close the window; return undrained leftovers for FIFO promote."""
    cid = conversation_id.strip()
    if not cid:
        return []
    _accepting.pop(cid, None)
    leftovers = _pending.pop(cid, [])
    if leftovers:
        logger.info(
            "turn_steer.accepting_end_leftovers",
            conversation_id=cid,
            leftover=len(leftovers),
        )
    else:
        logger.debug("turn_steer.accepting_end", conversation_id=cid)
    return leftovers


def try_enqueue(
    *,
    conversation_id: str,
    content: str,
    user_id: str = "",
    attachments: list[dict[str, Any]] | None = None,
    agent_mentions: list[dict[str, Any]] | None = None,
    requires_tools: bool = False,
    x_client_platform: str | None = None,
    origin_device_id: str | None = None,
    llm_credentials: Any = None,
    llm_supports_tools: bool | None = None,
) -> PendingTurnSteer | None:
    """Park a classic steer if the captain loop is accepting; else ``None`` (→ FIFO)."""
    cid = conversation_id.strip()
    if not cid or cid not in _accepting:
        return None
    item = PendingTurnSteer(
        interjection_id=new_id(),
        conversation_id=cid,
        execution_id=_accepting[cid],
        content=content,
        user_id=user_id,
        attachments=list(attachments or []),
        agent_mentions=list(agent_mentions or []),
        requires_tools=requires_tools,
        x_client_platform=x_client_platform,
        origin_device_id=origin_device_id,
        llm_credentials=llm_credentials,
        llm_supports_tools=llm_supports_tools,
    )
    bucket = _pending.setdefault(cid, [])
    bucket.append(item)
    logger.info(
        "turn_steer.enqueued",
        conversation_id=cid,
        interjection_id=item.interjection_id,
        pending=len(bucket),
        preview=content_preview(content, max_len=80),
    )
    return item


def drain(conversation_id: str) -> list[PendingTurnSteer]:
    """FIFO drain all pending steers for ``conversation_id`` (never blocks)."""
    cid = conversation_id.strip()
    if not cid:
        return []
    items = _pending.pop(cid, [])
    if items:
        logger.info(
            "turn_steer.drained",
            conversation_id=cid,
            count=len(items),
        )
    return items


def _injected_user_message(
    item: PendingTurnSteer,
    *,
    sink: Any | None,
    execution_id: str | None,
) -> LLMMessage:
    msg = LLMMessage(
        role="user",
        content=format_steer_user_message(
            item.content,
            item.attachments,
            item.agent_mentions,
        ),
    )
    if sink is None:
        return msg
    eid = (execution_id or item.execution_id or "").strip()
    sink.emit(
        user_interjection(
            interjection_id=item.interjection_id,
            execution_id=eid,
            content=item.content,
            status="injected",
            attachments=_att_meta(item.attachments),
            agent_mentions=wire_agent_mentions(item.agent_mentions),
        )
    )
    return msg


def drain_as_messages(
    conversation_id: str,
    *,
    sink: Any | None = None,
    execution_id: str | None = None,
) -> list[LLMMessage]:
    """Drain pending steers, map to user-role LLM messages, emit ``injected``.

    ``injected`` is the classic terminal status (内容真正进模型上下文).
    """
    return [
        _injected_user_message(item, sink=sink, execution_id=execution_id)
        for item in drain(conversation_id)
    ]


async def drain_injected(
    conversation_id: str,
    *,
    sink: Any | None = None,
    execution_id: str | None = None,
) -> list[LLMMessage]:
    """Drain + emit ``injected``. Ask settlement waits for host-turn commit."""
    return drain_as_messages(
        conversation_id, sink=sink, execution_id=execution_id
    )


def peek_count(conversation_id: str) -> int:
    return len(_pending.get(conversation_id.strip(), ()))


def _emit_degraded_turn_queued(
    *,
    conversation_id: str,
    queue_id: str,
    position: int,
    queue_depth: int,
    interjection_id: str,
) -> bool:
    """Honest signal: accepted steer could not soft-insert → now FIFO.

    Clients that already saw ``user_interjection(received)`` must also see
    ``user_interjection(queued)`` + ``turn_queued.degraded_from=steer`` (dual emit,
    same posture as coordination enqueue). Returns whether a live sink received
    ``turn_queued`` — other端 following the conversation are reached either way.

    Called after the ``queued`` status so the dual emit keeps its contract order; that
    is why the enqueue itself passes ``signal_watchers=False``.
    """
    from .queue import broadcast_turn_queued

    reached_live_sink = broadcast_turn_queued(
        conversation_id=conversation_id,
        queue_id=queue_id,
        position=position,
        queue_depth=queue_depth,
        degraded_from="steer",
        on_live_sink=True,
    )
    if not reached_live_sink:
        logger.info(
            "turn_steer.promoted_to_queue_no_sink",
            conversation_id=conversation_id,
            interjection_id=interjection_id,
            queue_id=queue_id,
            position=position,
            queue_depth=queue_depth,
            degraded_from="steer",
        )
    return reached_live_sink


def _emit_interjection_status(
    *,
    conversation_id: str,
    item: PendingTurnSteer,
    status: str,
    note: str | None = None,
) -> None:
    from .runs import turn_runs

    run = turn_runs.get(conversation_id)
    if run is None or run.task.done():
        return
    run.sink.emit(
        user_interjection(
            interjection_id=item.interjection_id,
            execution_id=item.execution_id,
            content=item.content,
            status=status,
            note=note,
            attachments=_att_meta(item.attachments),
            agent_mentions=wire_agent_mentions(item.agent_mentions),
        )
    )


# Wording must stay true for both explicit Stop and overlap-cancel (a newer turn
# taking the slot marks the run superseded) — do not claim "你按了停止".
_USER_STOP_DISCARD_NOTE = "本回合已中止，这条插话未被主 Agent 读取，已丢弃"


def discard_leftovers_on_user_stop(leftovers: list[PendingTurnSteer]) -> int:
    """Drop undrained classic steers when the turn closed via user_stop.

    Stop = silent: do **not** promote onto FIFO / auto-start a new turn.
    Honest client signal reuses ``user_interjection(failed)`` + fixed note
    (no new protocol enum). Does not touch user-initiated FIFO entries.

    Returns how many leftovers were discarded. Caller should only invoke after
    ``end_accepting``.
    """
    if not leftovers:
        return 0
    n = 0
    for item in leftovers:
        _emit_interjection_status(
            conversation_id=item.conversation_id,
            item=item,
            status="failed",
            note=_USER_STOP_DISCARD_NOTE,
        )
        n += 1
        logger.info(
            "turn_steer.discarded_on_user_stop",
            conversation_id=item.conversation_id,
            interjection_id=item.interjection_id,
        )
    return n


def promote_leftovers_to_queue(leftovers: list[PendingTurnSteer]) -> int:
    """Re-home undrained steers onto the conversation FIFO (回合收口竞态).

    Dual-emits ``user_interjection(queued)`` + ``turn_queued.degraded_from=steer``
    on a live sink when present (协调升队先例). Enqueue failure → ``failed``.

    Returns how many items were enqueued. Caller should only invoke after
    ``end_accepting`` so a live loop cannot race-drain the same items.
    Natural turn close only — user_stop leftovers use
    :func:`discard_leftovers_on_user_stop` instead.
    """
    if not leftovers:
        return 0
    from .queue import new_queued_turn, turn_queue

    n = 0
    for item in leftovers:
        try:
            status = turn_queue.enqueue_and_ensure_drain(
                item.conversation_id,
                new_queued_turn(
                    content=item.content,
                    user_id=item.user_id,
                    attachments=item.attachments,
                    agent_mentions=item.agent_mentions,
                    requires_tools=item.requires_tools,
                    x_client_platform=item.x_client_platform,
                    origin_device_id=item.origin_device_id,
                    llm_credentials=item.llm_credentials,
                    llm_supports_tools=item.llm_supports_tools,
                    interjection_id=item.interjection_id,
                ),
                # 双发次序是契约：queued 状态先行，degraded turn_queued 随后由
                # ``_emit_degraded_turn_queued`` 发出（含对话级信号）。
                signal_watchers=False,
            )
        except Exception as exc:  # noqa: BLE001 — surface as failed, never raise into loop finally
            logger.exception(
                "turn_steer.promote_enqueue_failed",
                conversation_id=item.conversation_id,
                interjection_id=item.interjection_id,
            )
            _emit_interjection_status(
                conversation_id=item.conversation_id,
                item=item,
                status="failed",
                note=f"转入对话级排队失败：{exc}",
            )
            continue
        _emit_interjection_status(
            conversation_id=item.conversation_id,
            item=item,
            status="queued",
            note="当前回合已收口，已自动转入下一回合",
        )
        emitted = _emit_degraded_turn_queued(
            conversation_id=item.conversation_id,
            queue_id=status.queue_id,
            position=status.position,
            queue_depth=status.queue_depth,
            interjection_id=item.interjection_id,
        )
        n += 1
        logger.info(
            "turn_steer.promoted_to_queue",
            conversation_id=item.conversation_id,
            interjection_id=item.interjection_id,
            queue_id=status.queue_id,
            position=status.position,
            queue_depth=status.queue_depth,
            emitted_turn_queued=emitted,
            degraded_from="steer",
        )
    return n


def _reset_for_tests() -> None:
    """Test helper — clear process-local state."""
    _pending.clear()
    _accepting.clear()
