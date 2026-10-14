"""WaveScheduler decision-boundary hook (SCOPE)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.runtime.runs.constants import HANDOFF_SUMMARY_CHARS

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan

from agentcore.runtime.graph.host import GraphHost

logger = get_logger(__name__)


def checkpoint_active(tool: GraphHost) -> bool:
    """Whether this turn has a live interactive user (ask_user / escalate armed)."""
    return bool(tool._checkpoint_enabled and tool._registry and tool._conversation_id)


def review_summary_text(
    state: Any | None,
    *,
    limit: int = HANDOFF_SUMMARY_CHARS,
) -> str:
    """Build a boundary-card excerpt for one completed run.

    Prefer handoff debrief (``summary`` + ``key_points``) so cards show scannable
    structure instead of a mid-cut markdown body. Fall back to files_touched, then
    truncated ``content``.
    """
    if state is None:
        return ""

    parts: list[str] = []
    files = [
        str(p).strip()
        for p in (getattr(state, "files_touched", None) or [])
        if str(p).strip()
    ]
    debrief = state.debrief if isinstance(getattr(state, "debrief", None), dict) else None
    if debrief:
        degraded = bool(debrief.get("degraded"))
        raw_points = debrief.get("key_points") or []
        if isinstance(raw_points, str):
            raw_points = [raw_points]
        points = [str(kp).strip() for kp in raw_points if str(kp).strip()]

        if degraded and files:
            for text in points:
                parts.append(f"· {text}")
            if not parts:
                parts.append("已落盘 " + ", ".join(files[:5]))
        else:
            lead = str(debrief.get("summary") or "").strip()
            if lead:
                parts.append(lead)
            for text in points:
                parts.append(f"· {text}")

    if not parts and files:
        parts.append("已落盘 " + ", ".join(files[:5]))

    if not parts:
        content = (getattr(state, "content", None) or "").strip()
        if content:
            parts.append(content)

    text = "\n".join(parts)
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def boundary_hook(tool: GraphHost, plan: RunPlan):
    """Build the WaveScheduler ``on_boundary`` hook for ``plan`` (SCOPE 反应臂)."""
    from agentcore.runtime.runs import BoundaryOutcome, BoundaryReason

    async def on_boundary(reason, nodes, completed) -> BoundaryOutcome:
        if reason is BoundaryReason.SCOPE:
            tool._pending_boundary = (reason, list(nodes))
            return BoundaryOutcome.YIELD
        return BoundaryOutcome.PROCEED

    return on_boundary
