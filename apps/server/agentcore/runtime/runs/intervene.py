"""按人干预（只停这位队员 / 立即改此人）的受理判定与回话。

之前是「入队即成功」：路由把请求塞进进程内队列，回一个整条 execution 的排队计数，
客户端拿它当「引擎将停下这位队员」。可队列只在驱动循环轮询时排干——驱动早退了，
或这个 run 压根不在当前计划里，那条请求就永远躺着，UI 却已经许了愿。

改成先问 :mod:`agentcore.runtime.runs.drive_reach`：够不着就不入队、直说够不着。
面向用户的那句话也在这里写死，三端照抄同一句，不各自编。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agentcore.core.logging import get_logger
from agentcore.runtime.runs.drive_reach import drive_reach
from agentcore.runtime.runs.redirect_queue import enqueue_redirect, peek_redirect_count
from agentcore.runtime.runs.stop_queue import enqueue_stop, peek_stop_count

logger = get_logger(__name__)

InterveneReason = Literal["queued", "no_live_drive", "unknown_run"]


@dataclass(frozen=True, slots=True)
class InterveneAck:
    """服务端对一次按人干预的回答。``accepted=False`` 时 ``queued`` 恒为 0。"""

    accepted: bool
    reason: InterveneReason
    detail: str
    queued: int


_NO_DRIVE_STOP = "这批工作已经不在引擎手里了，没有能停的在跑队员。"
_NO_DRIVE_REDIRECT = "这批工作已经不在引擎手里了，改方向到不了这位队员——把新要求直接说给主管。"
_UNKNOWN_STOP = "引擎当前的计划里没有这位队员，停不到他。"
_UNKNOWN_REDIRECT = "引擎当前的计划里没有这位队员，改不到他——把新要求直接说给主管。"
_QUEUED_STOP_ONE = "已交给引擎：正在停这位队员。"
_QUEUED_STOP_ALL = "已交给引擎：正在停这批队员。"
_QUEUED_REDIRECT = "已交给引擎：这位队员将带着你的新要求重做。"


def _refuse(reason: InterveneReason, detail: str) -> InterveneAck:
    return InterveneAck(accepted=False, reason=reason, detail=detail, queued=0)


def _log_run_stop_ack(
    *,
    conversation_id: str,
    execution_id: str,
    run_id: str | None,
    ack: InterveneAck,
) -> None:
    """Cloud HTTP and sidecar RPC share this so local turns leave the same fingerprint."""
    if ack.accepted:
        logger.info(
            "run_stop.queued",
            conversation_id=conversation_id,
            execution_id=execution_id,
            run_id=run_id,
            queued=ack.queued,
        )
    else:
        logger.info(
            "run_stop.unreachable",
            conversation_id=conversation_id,
            execution_id=execution_id,
            run_id=run_id,
            reason=ack.reason,
        )


def accept_run_stop(
    *,
    execution_id: str,
    conversation_id: str,
    run_id: str | None = None,
) -> InterveneAck:
    """受理「只停这位队员」；``run_id`` 省略 = 停这条 execution 的全体队员。"""
    rid = (run_id or "").strip() or None
    reach = drive_reach(execution_id, rid)
    if not reach.driving:
        ack = _refuse("no_live_drive", _NO_DRIVE_STOP)
    elif not reach.in_plan:
        ack = _refuse("unknown_run", _UNKNOWN_STOP)
    else:
        enqueue_stop(
            execution_id=execution_id,
            conversation_id=conversation_id,
            run_id=rid,
        )
        ack = InterveneAck(
            accepted=True,
            reason="queued",
            detail=_QUEUED_STOP_ONE if rid else _QUEUED_STOP_ALL,
            queued=peek_stop_count(execution_id),
        )
    _log_run_stop_ack(
        conversation_id=conversation_id,
        execution_id=execution_id,
        run_id=rid,
        ack=ack,
    )
    return ack


def accept_run_redirect(
    *,
    execution_id: str,
    run_id: str,
    feedback: str,
    conversation_id: str,
) -> InterveneAck:
    """受理「立即改此人」。"""
    rid = run_id.strip()
    reach = drive_reach(execution_id, rid)
    if not reach.driving:
        return _refuse("no_live_drive", _NO_DRIVE_REDIRECT)
    if not reach.in_plan:
        return _refuse("unknown_run", _UNKNOWN_REDIRECT)
    enqueue_redirect(
        execution_id=execution_id,
        run_id=rid,
        feedback=feedback,
        conversation_id=conversation_id,
    )
    return InterveneAck(
        accepted=True,
        reason="queued",
        detail=_QUEUED_REDIRECT,
        queued=peek_redirect_count(execution_id),
    )
