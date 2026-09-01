"""Presence-disconnect + write-desk cold-open hard gate.

When the local desk fulfiller is gone (live hub, or session stamp from a
presence-error envelope) and any pending node structurally needs a write desk,
reject before workers start. Prose / non-write batches still pass.
Does not scan free-text ``task`` bodies — only
:func:`~agentcore.runtime.delegate.target_desktop.task_structurally_requires_write_desk`.
Reconnect restores dispatch; timeouts never trip this gate.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan

DelegateTool = Any

CHANNEL_DEAD_WRITE_DESK_REJECT = (
    "工作区/本地文件连不上：拒绝再派需要写盘的队员"
    "（deliverable.form=files / workspace / 非空 artifacts；省略 form 按 files）。"
    "请基于已有材料收口，或改派纯 prose 队员；桌面重新连上后可再派。"
)


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


def workspace_channel_is_dead(
    tool: DelegateTool,
    *,
    session: Any | None = None,
) -> bool:
    """True when this desk has no workspace fulfiller (live hub or presence stamp)."""
    from agentcore.runtime.engine.governance import is_workspace_channel_sticky_dead
    from agentcore.workspace.presence import local_workspace_files_reachable

    ctx = getattr(tool, "_base_tool_context", None)
    user_id = getattr(ctx, "user_id", None) if ctx is not None else None
    backend = getattr(ctx, "backend", None) if ctx is not None else None
    reachable = local_workspace_files_reachable(
        user_id=str(user_id).strip() if user_id else None,
        backend=backend,
    )
    looked = session if session is not None else _session_for_tool(tool)
    if reachable is True:
        if looked is not None:
            looked.workspace_channel_dead = False
        return False
    if reachable is False:
        return True
    if looked is not None and bool(getattr(looked, "workspace_channel_dead", False)):
        return True
    return is_workspace_channel_sticky_dead(ctx)


def _deliverable_as_dict(deliverable: Any) -> dict[str, Any] | None:
    if deliverable is None:
        return None
    if isinstance(deliverable, dict):
        return deliverable
    if is_dataclass(deliverable) and not isinstance(deliverable, type):
        return asdict(deliverable)
    form = getattr(deliverable, "form", None)
    arts = list(getattr(deliverable, "artifacts", None) or [])
    return {"form": form, "artifacts": arts}


def node_structurally_requires_write_desk(node: Any) -> bool:
    """RunSpec / duck node → same write-desk predicate as task dicts."""
    from agentcore.runtime.delegate.target_desktop import (
        task_structurally_requires_write_desk,
    )

    raw = _deliverable_as_dict(getattr(node, "deliverable", None))
    if raw is None:
        return True
    return task_structurally_requires_write_desk({"deliverable": raw})


def _any_pending_write_desk_node(
    plan: RunPlan,
    *,
    skip_run_ids: set[str] | None = None,
) -> bool:
    skip = skip_run_ids or set()
    for node in getattr(plan, "nodes", None) or []:
        rid = str(getattr(node, "run_id", None) or "").strip()
        if rid and rid in skip:
            continue
        if node_structurally_requires_write_desk(node):
            return True
    return False


def channel_dead_write_desk_error(
    tool: DelegateTool,
    plan: RunPlan,
    *,
    session: Any | None = None,
    skip_run_ids: set[str] | None = None,
) -> str | None:
    """Return contract reject message, or ``None`` if the dispatch is allowed."""
    if not workspace_channel_is_dead(tool, session=session):
        return None
    if not _any_pending_write_desk_node(plan, skip_run_ids=skip_run_ids):
        return None
    return CHANNEL_DEAD_WRITE_DESK_REJECT


def channel_dead_write_tasks_error(
    tool: DelegateTool,
    tasks_raw: list[Any],
    *,
    session: Any | None = None,
) -> str | None:
    """Same gate for replan ``adds`` / raw task dicts (bypass drive cold-open)."""
    from agentcore.runtime.delegate.target_desktop import (
        task_structurally_requires_write_desk,
    )

    if not workspace_channel_is_dead(tool, session=session):
        return None
    for item in tasks_raw:
        if isinstance(item, dict) and task_structurally_requires_write_desk(item):
            return CHANNEL_DEAD_WRITE_DESK_REJECT
    return None
