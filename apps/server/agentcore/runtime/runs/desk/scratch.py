"""Run-start scratch write scope. The graph gate decides whether a desk is required."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

SCRATCH_NO_WRITE_IDENTITY_HINT = (
    "本回合坐会话 scratch、禁写盘；写盘须上级带 target_folder_id 重派。"
)


def bare_chat_local_scratch_write_ok(
    *,
    session_folder_id: str | None,
    backend: Any = None,
    turn_target_desk: Any = None,
) -> bool:
    """True when a desktop local bare chat may write on conv scratch without a Folder id."""
    if session_folder_id:
        return False
    if getattr(backend, "location", None) != "local":
        return False
    seen = getattr(turn_target_desk, "_seen", None)
    return not (
        isinstance(seen, set) and seen and not getattr(turn_target_desk, "folder_id", None)
    )


def resolve_bare_chat_write_scope(
    *,
    target_folder_id: str | None,
    session_folder_id: str | None,
    base_write_scope: str,
    turn_created_folder_ids: Collection[str] | None = None,
    allow_local_scratch_write: bool = False,
) -> str:
    """Scratch seat (no birth, no target): ``write_scope=none`` unless local scratch is the desk."""
    target = target_folder_id.strip() if isinstance(target_folder_id, str) else ""
    if target and turn_created_folder_ids and target in turn_created_folder_ids:
        return "project"
    if target_folder_id or session_folder_id:
        return base_write_scope
    if allow_local_scratch_write:
        return "project"
    return "none"
