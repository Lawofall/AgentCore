"""收口后冷开整团重派硬闸（与同图 replan 补跑闸分轨，共用 MAX_GAP_FILL_ADDS）。

检测本回合用户消息 ``origin=execution_harvest``（或工具上等价戳记）。本闸是**路由**
而非整批一刀切：批次先按结构拆成 续派 / 补缺口 / 冷开 三堆
（:mod:`~agentcore.runtime.delegate.team_continuation`），

- 续派（``continue_from_run_id``）走同队续派入口，不进本闸；
- 补缺口（``replaces_run_id``）按 ``MAX_GAP_FILL_ADDS`` 限流；
- 只有真冷开的那堆才判 substantial 大扇出并拒。

非 harvest / 嵌套 / append·同图 merge（由调用方排除）放行。
续派走 ``continue_from_run_id``，补缺口走 ``replaces_run_id``。
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunState

DelegateTool = Any

_USER_MESSAGE_ORIGIN: ContextVar[str] = ContextVar("user_message_origin", default="")

EXECUTION_HARVEST_ORIGIN = "execution_harvest"


def bind_user_message_origin(origin: str | None) -> object:
    """Bind turn-level message origin (harvest sets ``execution_harvest``)."""
    return _USER_MESSAGE_ORIGIN.set((origin or "").strip())


def reset_user_message_origin(token: object) -> None:
    _USER_MESSAGE_ORIGIN.reset(token)  # type: ignore[arg-type]


def current_user_message_origin() -> str:
    return _USER_MESSAGE_ORIGIN.get() or ""


def resolve_user_message_origin(tool: DelegateTool | None = None) -> str:
    """Prefer tool stamp (tests / capture-at-construct); else ContextVar."""
    if tool is not None:
        stamped = getattr(tool, "_user_message_origin", None)
        if isinstance(stamped, str) and stamped.strip():
            return stamped.strip()
    return current_user_message_origin()


def is_post_close_turn(tool: DelegateTool | None = None) -> bool:
    return resolve_user_message_origin(tool) == EXECUTION_HARVEST_ORIGIN


def _session_for_tool(tool: DelegateTool) -> Any | None:
    from agentcore.runtime.coordination.session import (
        active_coordination,
        active_coordination_for_conversation,
    )

    ctx = getattr(tool, "_base_tool_context", None)
    eid = getattr(ctx, "execution_id", None) if ctx is not None else None
    if eid:
        session = active_coordination(str(eid))
        if session is not None:
            return session
    cid = str(getattr(tool, "_conversation_id", None) or "").strip()
    if cid:
        return active_coordination_for_conversation(cid)
    return None


def _completed_snapshot_for_post_close(tool: DelegateTool) -> dict[str, RunState] | None:
    """Build a FAILED/SKIPPED/COMPLETED map from the (possibly inactive) session.

    ``None`` = no session (gaps unknown). Empty dict = known empty roster.
    """
    from agentcore.runtime.runs.types import RunPhase, RunState

    session = _session_for_tool(tool)
    if session is None:
        return None
    out: dict[str, RunState] = {}
    failed = set(getattr(session, "failed_run_ids", None) or ())
    cancelled = set(getattr(session, "cancel_ids", None) or ())
    for rid in set(getattr(session, "completed_run_ids", None) or ()):
        if rid in failed:
            out[rid] = RunState(phase=RunPhase.FAILED, error="failed")
        elif rid in cancelled:
            out[rid] = RunState(phase=RunPhase.SKIPPED)
        else:
            out[rid] = RunState(phase=RunPhase.COMPLETED, content="ok")
    return out


POST_CLOSE_REJECT_GAP_FILL = "gap_fill"
POST_CLOSE_REJECT_COLD_OPEN = "cold_open"


@dataclass(frozen=True, slots=True)
class PostCloseReject:
    """Which post-close refuse branch fired. Tag only — does not change admission."""

    kind: str
    message: str


def post_close_reject(tool: DelegateTool, plan: RunPlan) -> PostCloseReject | None:
    """Same admission as :func:`post_close_cold_open_error`, plus a kind tag."""
    from agentcore.runtime.delegate.batch_shape import is_substantial_batch
    from agentcore.runtime.delegate.team_continuation import (
        classify_batch,
        cold_open_reject_message,
        gap_fill_admission_error,
    )

    if not is_post_close_turn(tool):
        return None
    if int(getattr(tool, "_depth", 0) or 0) != 0:
        return None

    completed = _completed_snapshot_for_post_close(tool)
    shape = classify_batch(plan, completed)

    # 补缺口按缺口限流（与同图 replan 补跑闸同判定）；续派那堆不进限流。
    gap_error = gap_fill_admission_error(shape, completed)
    if gap_error is not None:
        return PostCloseReject(kind=POST_CLOSE_REJECT_GAP_FILL, message=gap_error)

    # 冷开那堆单独判大扇出：与续派/补缺口同批时，不再把整批算成「整团重派」。
    if not is_substantial_batch(len(shape.cold), shape.cold_has_deps):
        return None
    return PostCloseReject(
        kind=POST_CLOSE_REJECT_COLD_OPEN,
        message=cold_open_reject_message(shape),
    )


def post_close_cold_open_error(tool: DelegateTool, plan: RunPlan) -> str | None:
    """Return contract reject message, or ``None`` if the batch is admitted."""
    reject = post_close_reject(tool, plan)
    return None if reject is None else reject.message
