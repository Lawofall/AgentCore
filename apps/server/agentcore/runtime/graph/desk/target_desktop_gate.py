"""Bare-chat write-desk gate + scratch write_scope policy (no I/O)."""

from __future__ import annotations

from typing import Any

from agentcore.runtime.runs.desk.errors import TargetDesktopError
from agentcore.runtime.runs.desk.scratch import (
    SCRATCH_NO_WRITE_IDENTITY_HINT,
    bare_chat_local_scratch_write_ok,
    resolve_bare_chat_write_scope,
)

# Honest reject when bare chat (no birth) would park a *write* worker on scratch.
# Cloud: auto cloud-desk provision covers the empty-hint case. Local: scratch *is*
# the desk (``allow_local_scratch_write``). Residual rejects: multi-folder same
# turn, cloud create failure — do not urge create/ask.
NO_TARGET_SCRATCH_GATE_MSG = (
    "写盘任务必须点名目标文件夹（target_folder_id）；"
    "纯对话/只读可不点名（worker 坐会话 scratch、禁写）。"
    "同回合已涉及多个文件夹时请各写盘 task 显式点名。"
)

# Conversation UUID is not a Folder id — models copy the nearby conversation_id.
CONV_ID_AS_FOLDER_MSG = (
    "target_folder_id 不能填当前对话 ID；"
    "请用 folders 拿到真正的文件夹 ID 后再派。"
)


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

    Non-empty ``artifacts`` or non-empty ``artifact_dir`` require a write desk.
    Omitted / empty object does not.
    """
    from agentcore.runtime.runs.types import task_raw_expects_landing

    return task_raw_expects_landing(task)


def format_bare_chat_no_target_error(missing_tasks: list[dict[str, Any]]) -> str:
    """Actionable bare-chat gate copy: constant prefix + missing-task skeleton.

    Shared by root ``GraphTool.execute`` and replan ``apply_replan`` / supervised
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
    allow_local_scratch_write: bool = False,
) -> str | None:
    """方案 C: no birth + write-desk task without target → reject before drive.

    Birth desk always passes. Pure chat / readonly (no write deliverable) may omit
    ``target_folder_id`` (worker sits scratch, ``write_scope=none``). Desktop local
    裸聊 (``allow_local_scratch_write``) may write on that scratch. Still rejects
    the whole batch when any write-desk task lacks an effective target (cloud, or
    local multi-folder same turn).

    Callers should run :func:`ensure_bare_chat_auto_cloud_desk` first so cloud bare
    chat with no unique turn hint can silently mint a cloud desk.
    """
    if session_folder_id:
        return None
    if allow_local_scratch_write:
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


def gate_conversation_id_is_not_folder(
    *,
    conversation_id: str | None,
    tasks_raw: list[dict[str, Any]],
    default_target_folder_id: str | None = None,
) -> str | None:
    """Reject when any task's target folder id is the current conversation id."""
    cid = conversation_id.strip() if isinstance(conversation_id, str) else ""
    if not cid:
        return None
    for item in tasks_raw:
        if not isinstance(item, dict):
            continue
        target = effective_target_folder_id(
            item.get("target_folder_id"),
            default=default_target_folder_id,
        )
        if target == cid:
            return CONV_ID_AS_FOLDER_MSG
    return None
