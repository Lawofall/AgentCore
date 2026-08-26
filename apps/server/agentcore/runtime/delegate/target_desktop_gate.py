"""Bare-chat write-desk gate + scratch write_scope policy (no I/O)."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

# Honest reject when bare chat (no birth) would park a *write* worker on scratch.
# Auto cloud-desk provision covers the empty-hint case; this copy is for residual
# rejects (multi-folder same turn, create failure, …) — do not urge create/ask.
NO_TARGET_SCRATCH_GATE_MSG = (
    "写盘任务必须点名目标文件夹（target_folder_id）；"
    "纯对话/只读可不点名（worker 坐会话 scratch、禁写）。"
    "同回合已涉及多个文件夹时请各写盘 task 显式点名。"
)

# Identity tip when a bare-chat worker sits on conv scratch with write_scope=none.
SCRATCH_NO_WRITE_IDENTITY_HINT = (
    "本回合坐会话 scratch、禁写盘；写盘须上级带 target_folder_id 重派。"
)


class TargetDesktopError(Exception):
    """Structured prepare-time failure (unknown folder / DB unreachable / …)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def effective_target_folder_id(
    raw: Any,
    *,
    default: str | None = None,
) -> str | None:
    """Normalise task ``target_folder_id``; fall back to inherited default."""
    if isinstance(raw, str):
        cleaned = raw.strip()
        if cleaned:
            return cleaned
    if isinstance(default, str):
        cleaned_default = default.strip()
        if cleaned_default:
            return cleaned_default
    return None


def task_structurally_requires_write_desk(task: dict[str, Any]) -> bool:
    """True when deliverable structurally needs a write desk (no task-body scan).

    Missing / empty / omitted form → files (must write). Only explicit
    ``form=prose`` is exempt. ``files`` / ``workspace`` / non-empty ``artifacts``
    / ``workspace_native`` also require a write desk.
    """
    from agentcore.runtime.runs.types import raw_deliverable_expects_landing

    return raw_deliverable_expects_landing(task.get("deliverable"))


def resolve_bare_chat_write_scope(
    *,
    target_folder_id: str | None,
    session_folder_id: str | None,
    base_write_scope: str,
    turn_created_folder_ids: Collection[str] | None = None,
) -> str:
    """Scratch seat (no birth, no target): ``write_scope=none``; keep ``explore_memory``.

    A worker whose ``target_folder_id`` was minted this turn (empty new desk)
    gets ``project`` even when the CEO turn is still explore-pending — filling
    that folder *is* the job; the birth folder stays on ``base_write_scope``.
    """
    target = target_folder_id.strip() if isinstance(target_folder_id, str) else ""
    if target and turn_created_folder_ids and target in turn_created_folder_ids:
        return "project"
    if target_folder_id or session_folder_id:
        return base_write_scope
    if base_write_scope == "explore_memory":
        return "explore_memory"
    return "none"


def format_bare_chat_no_target_error(missing_tasks: list[dict[str, Any]]) -> str:
    """Actionable bare-chat gate copy: constant prefix + missing-task skeleton.

    Shared by root ``DelegateTool.execute`` and replan ``apply_replan`` / supervised
    via ``gate_bare_chat_requires_target``. Lists every *write-desk* task lacking a
    valid ``target_folder_id`` (role / optional id / missing-target mark only —
    no task body).
    """
    parts: list[str] = []
    for item in missing_tasks:
        bits: list[str] = []
        role = item.get("role")
        if isinstance(role, str) and role.strip():
            bits.append(f"role={role.strip()}")
        else:
            bits.append("role=?")
        rid = item.get("id")
        if isinstance(rid, str) and rid.strip():
            bits.append(f"id={rid.strip()}")
        bits.append("缺 target_folder_id")
        parts.append("{" + ", ".join(bits) + "}")
    dynamic = "；".join(parts) if parts else "{role=?, 缺 target_folder_id}"
    return f"{NO_TARGET_SCRATCH_GATE_MSG} 缺目标任务：{dynamic}"


def gate_bare_chat_requires_target(
    *,
    session_folder_id: str | None,
    tasks_raw: list[dict[str, Any]],
    default_target_folder_id: str | None = None,
) -> str | None:
    """方案 C: no birth + write-desk task without target → reject before drive.

    Birth desk always passes. Pure chat / readonly (no write deliverable) may omit
    ``target_folder_id`` (worker sits scratch, ``write_scope=none``). Still rejects
    the whole batch when any write-desk task lacks an effective target.

    Callers should run :func:`ensure_bare_chat_auto_cloud_desk` first so bare chat
    with no unique turn hint can silently mint a cloud desk.
    """
    if session_folder_id:
        return None
    missing: list[dict[str, Any]] = []
    for item in tasks_raw:
        if not isinstance(item, dict):
            continue
        if effective_target_folder_id(
            item.get("target_folder_id"),
            default=default_target_folder_id,
        ):
            continue
        if not task_structurally_requires_write_desk(item):
            continue
        missing.append(item)
    if not missing:
        return None
    return format_bare_chat_no_target_error(missing)
