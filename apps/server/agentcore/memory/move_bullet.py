"""Move one memory bullet between the GLOBAL and FOLDER layers (位置即作用域纠错).

Semantics: remove the bullet from the source layer, then add it under the same
section on the target layer — never a scope flag flip. Used by the desktop
「移到本文件夹 / 移到全局」actions on the semantic diff card
(Agent记忆与知识系统 §1.6 P2-b).

``MoveDirection`` keeps the ``to_project`` spelling: it is the ``POST /v1/memory/move``
request contract the desktop already sends, not product wording (双模式工作区 §5.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agentcore.memory.store import (
    CORE_MEMORY_FILE,
    PREFERENCES_MEMORY_FILE,
    MemoryScope,
    MemoryStore,
    is_topic_path,
    memory_version,
    topic_path,
)
from agentcore.memory.user_memory import (
    MEMORY_SECTIONS,
    PREFERENCES_SECTIONS,
    MarkdownMemoryApplier,
    MemoryAction,
    MemoryOp,
)

# Keep in sync with user_memory._GLOBAL_ONLY / _PROJECT_ONLY_PROFILE_SECTIONS —
# preferences stay global-only via PREFERENCES_SECTIONS / file check above.
_GLOBAL_ONLY_SECTIONS = frozenset({"纠正记录"})
_PROJECT_ONLY_SECTIONS = frozenset({"项目约束"})
_TOPIC_DEFAULT_SECTION = "要点"

MoveDirection = Literal["to_project", "to_global"]


@dataclass(frozen=True)
class MoveBulletError:
    """Validation / not-found failure with a user-facing Chinese message."""

    message: str


@dataclass(frozen=True)
class MoveBulletConflict:
    """CAS miss on source or target; versions are the live tags."""

    source_version: str
    target_version: str


@dataclass(frozen=True)
class MoveBulletOk:
    source_version: str
    target_version: str


MoveBulletResult = MoveBulletOk | MoveBulletConflict | MoveBulletError


def _resolve_file(
    *, kind: str, section: str, topic_slug: str | None
) -> str | MoveBulletError:
    if kind == "preferences":
        return MoveBulletError("偏好仅存在于全局，不能搬到文件夹层")
    if kind == "topic":
        slug = (topic_slug or "").strip()
        if not slug:
            return MoveBulletError("主题笔记搬层需要 topic_slug")
        path = topic_path(slug)
        if not is_topic_path(path):
            return MoveBulletError("无效的主题 slug")
        return path
    # profile (default): section picks 画像 vs a misrouted preference section
    if section in PREFERENCES_SECTIONS:
        return MoveBulletError("偏好条目仅存在于全局，不能搬层")
    if section and section not in MEMORY_SECTIONS and kind == "profile":
        # Free-form / unknown section on 画像 — still allow (user may have edited)
        return CORE_MEMORY_FILE
    if section in MEMORY_SECTIONS:
        # Defence: preference sections already rejected above
        return CORE_MEMORY_FILE
    return CORE_MEMORY_FILE


def validate_move(
    *,
    file: str,
    section: str,
    direction: MoveDirection,
) -> MoveBulletError | None:
    """Reject illegal layer moves before touching the store."""
    if file == PREFERENCES_MEMORY_FILE:
        return MoveBulletError("偏好仅存在于全局，不能搬到文件夹层")
    if section in PREFERENCES_SECTIONS:
        return MoveBulletError("偏好条目仅存在于全局，不能搬层")
    if section in _GLOBAL_ONLY_SECTIONS and direction == "to_project":
        return MoveBulletError("「纠正记录」只属于全局，不能移到本文件夹")
    if section in _PROJECT_ONLY_SECTIONS and direction == "to_global":
        return MoveBulletError("「项目约束」只属于本文件夹，不能移到全局")
    return None


def _scopes(
    direction: MoveDirection, folder_id: str
) -> tuple[MemoryScope, MemoryScope]:
    if direction == "to_project":
        return None, folder_id
    return folder_id, None


async def move_memory_bullet(
    store: MemoryStore,
    *,
    user_id: str,
    content: str,
    section: str,
    folder_id: str,
    direction: MoveDirection,
    kind: str = "profile",
    topic_slug: str | None = None,
    source_baseline: str | None = None,
    target_baseline: str | None = None,
) -> MoveBulletResult:
    """Atomically move one bullet between global and ``folder_id`` folder layers.

    Caller must hold ``user_memory_lock``. Empty ``content`` / ``folder_id`` are
    rejected. CAS baselines are optional (``None`` = unconditional write).
    """
    text = (content or "").strip()
    if not text:
        return MoveBulletError("没有可搬移的内容")
    fid = (folder_id or "").strip()
    if not fid:
        return MoveBulletError("搬到本文件夹需要当前文件夹")
    if direction not in ("to_project", "to_global"):
        return MoveBulletError("无效的搬层方向")

    sec = (section or "").strip() or (
        _TOPIC_DEFAULT_SECTION if kind == "topic" else ""
    )
    if kind != "topic" and not sec:
        return MoveBulletError("搬层需要小节名")

    resolved = _resolve_file(kind=kind, section=sec, topic_slug=topic_slug)
    if isinstance(resolved, MoveBulletError):
        return resolved
    file = resolved

    err = validate_move(file=file, section=sec, direction=direction)
    if err is not None:
        return err

    source_scope, target_scope = _scopes(direction, fid)
    source_md = await store.load(user_id, file, scope=source_scope)
    target_md = await store.load(user_id, file, scope=target_scope)
    source_ver = memory_version(source_md)
    target_ver = memory_version(target_md)

    if source_baseline is not None and source_baseline != source_ver:
        return MoveBulletConflict(source_version=source_ver, target_version=target_ver)
    if target_baseline is not None and target_baseline != target_ver:
        return MoveBulletConflict(source_version=source_ver, target_version=target_ver)

    applier = MarkdownMemoryApplier()
    # Re-render with no ops first so a no-match REMOVE isn't mistaken for a change
    # (apply always re-serializes section/bullet markdown).
    normalized = applier.apply(source_md, [])
    removed = applier.apply(
        normalized,
        [MemoryOp(action=MemoryAction.REMOVE, section=sec, match=text, file=file)],
    )
    # If nothing matched, the source is unchanged — refuse rather than silently add a copy.
    if removed == normalized:
        return MoveBulletError("源层找不到这条记忆（可能已被改过）")

    added = applier.apply(
        target_md,
        [
            MemoryOp(
                action=MemoryAction.ADD,
                section=sec,
                content=text,
                file=file,
            )
        ],
    )

    if removed.strip():
        await store.save(user_id, file, removed, scope=source_scope)
    else:
        await store.delete(user_id, file, scope=source_scope)

    if added.strip():
        await store.save(user_id, file, added, scope=target_scope)
    else:
        await store.delete(user_id, file, scope=target_scope)

    return MoveBulletOk(
        source_version=memory_version(removed),
        target_version=memory_version(added),
    )
