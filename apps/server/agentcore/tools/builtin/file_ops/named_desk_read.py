"""CEO ``read`` one-shot bind of a this-turn named neighbor Folder.

Not a schema ``folder_id``. Not list / glob / grep / write. Workers do not
inherit the pin as an implicit desk switch. Miss never searches every Folder.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from agentcore.tools.protocol import ToolContext, ToolResult
from agentcore.workspace.cloud_tree import normalize_rel_path
from agentcore.workspace.host_path import classify_tool_path

_OTHER_DESK_NOTE = "（读自文件夹「{name}」，不是当前工作区。）"
_OTHER_DESK_FALLBACK = "另一文件夹"


def sitting_desk_id(context: ToolContext) -> str | None:
    """Desk ``file_*`` currently sit on: birth, else this-turn auto-desk."""
    birth = (context.ownership_desk_id or "").strip() or None
    auto = (context.auto_desk_folder_id or "").strip() or None
    return birth or auto


def _pinable_rel(raw: str | None) -> str | None:
    cleaned = normalize_rel_path(raw)
    if not cleaned or cleaned in (".", ".."):
        return None
    if cleaned == "external" or cleaned.startswith("external/"):
        return None
    if cleaned == "attachments" or cleaned.startswith("attachments/"):
        return None
    parts = [p for p in cleaned.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def stamp_named_file_pins(
    context: ToolContext,
    attachments: list[dict] | None,
    *,
    sitting_folder_id: str | None,
) -> None:
    """Record this-turn other-desk citations onto the shared pin box."""
    sitting = (sitting_folder_id or "").strip() or None
    for att in attachments or []:
        if not isinstance(att, dict):
            continue
        raw_src = att.get("source_folder_id")
        fid = raw_src.strip() if isinstance(raw_src, str) else ""
        if not fid or (sitting and fid == sitting):
            continue
        raw_path = att.get("workspace_path")
        rel = _pinable_rel(raw_path if isinstance(raw_path, str) else None)
        if rel is None:
            continue
        context.named_file_pins.add(fid, rel)


def named_desk_folder_for_read(context: ToolContext, raw_path: Any) -> str | None:
    """Folder id to one-shot bind, or None (stay on the miss)."""
    if not context.named_desk_read:
        return None
    raw = str(raw_path or "").strip()
    if not raw:
        return None
    classified = classify_tool_path(raw)
    if classified.kind != "workspace":
        return None
    rel = _pinable_rel(raw)
    if rel is None:
        return None
    sitting = sitting_desk_id(context)
    pinned = context.named_file_pins.folder_for(rel)
    if pinned and pinned != sitting:
        return pinned
    hint = (context.turn_target_desk.folder_id or "").strip() or None
    if hint and hint != sitting:
        return hint
    return None


def _is_not_found(result: ToolResult) -> bool:
    return (not result.success) and (result.failure_code or "") == "not_found"


def _append_other_desk_note(result: ToolResult, folder_name: str) -> ToolResult:
    label = (folder_name or "").strip() or _OTHER_DESK_FALLBACK
    note = _OTHER_DESK_NOTE.format(name=label)
    output = (result.output or "").rstrip()
    if not output:
        return result
    result.output = f"{output}\n\n{note}"
    if result.output_limit is not None:
        result.output_limit = max(result.output_limit, len(result.output))
    return result


def _sink_from_context(context: ToolContext):
    for holder in (context.workspace_channel, context.desktop_channel):
        if holder is None:
            continue
        candidate = getattr(holder, "sink", None)
        if candidate is not None:
            return candidate
    from agentcore.runtime.events import EventSink

    return EventSink()


async def _fork_named_desk_context(
    context: ToolContext, folder_id: str
) -> tuple[ToolContext, str] | None:
    """Fresh workspace slot on ``folder_id``. Does not rebind the CEO slot."""
    from agentcore.runtime.graph.desk.target_desktop_binding import (
        TargetDesktopError,
        build_target_backend,
        load_target_folder_binding,
    )
    from agentcore.tools.protocol import fork_workspace_slot
    from agentcore.workspace.locate import workspace_channel_for_tools

    uid = (context.user_id or "").strip()
    if not uid:
        return None
    try:
        binding = await load_target_folder_binding(folder_id=folder_id, user_id=uid)
    except TargetDesktopError:
        return None
    except Exception:  # noqa: BLE001 — miss stays a path-not-found, never breaks the tool
        return None
    if binding is None:
        return None
    backend = build_target_backend(
        user_id=uid,
        folder_id=binding.folder_id,
        conversation_id=context.conversation_id or "",
        sink=_sink_from_context(context),
        local_binding=binding.local_binding,
        folder_rel_path=binding.rel_path,
    )
    workspace_channel = workspace_channel_for_tools(
        backend,
        user_id=uid,
        conversation_id=context.conversation_id or "",
    )
    forked = replace(
        context,
        _workspace=fork_workspace_slot(backend, material_paths=frozenset()),
        workspace_channel=workspace_channel,
        named_desk_read=False,
        shared_workspace=True,
    )
    name = (binding.name or "").strip() or _OTHER_DESK_FALLBACK
    return forked, name


ReadOnDesk = Callable[[dict[str, Any], ToolContext, float], Awaitable[ToolResult]]


async def maybe_named_desk_file_read(
    miss: ToolResult,
    arguments: dict[str, Any],
    context: ToolContext,
    start: float,
    read_on_desk: ReadOnDesk,
) -> ToolResult | None:
    """Re-run ``read`` on a this-turn named other desk. None = keep ``miss``."""
    if not _is_not_found(miss):
        return None
    folder_id = named_desk_folder_for_read(context, arguments.get("file_path"))
    if not folder_id:
        return None
    forked = await _fork_named_desk_context(context, folder_id)
    if forked is None:
        return None
    other_ctx, folder_name = forked
    result = await read_on_desk(arguments, other_ctx, start)
    if not result.success:
        return None
    return _append_other_desk_note(result, folder_name)
