"""Semantic memory apply helpers: MemoryUpdateItem + ops apply + topic-cap.

The live path no longer calls ``maintain_user_memory`` per conversation (that was
the old one-shot 「消息窗口→ops→直写画像」path). Session digests go through
``episodic.py``; batch semantic merges go through ``semantic.py``. This module
still owns the shared ``MemoryUpdateItem`` card shape, topic-cap enforcement, and
``maintain_user_memory`` (ops apply) for tests / callers that already hold ops.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from agentcore.core.logging import get_logger
from agentcore.memory.conversation_title import ChatMessage
from agentcore.memory.store import (
    CORE_MEMORY_FILE,
    PREFERENCES_MEMORY_FILE,
    MemoryScope,
    MemoryStore,
    is_topic_path,
    topic_slug,
)
from agentcore.memory.user_memory import (
    MarkdownMemoryApplier,
    MemoryApplier,
    MemoryExtractInput,
    MemoryExtractor,
    MemoryOp,
    _empty_ops_reason,
    _is_cold_start,
)

logger = get_logger(__name__)


@dataclass
class MemoryUpdateItem:
    """One human-readable applied change, for the conversation-tail「记忆已更新」card.

    Built from the ops that landed in a file that actually changed this pass (Agent记忆与
    知识系统 §1.6). ``file`` is a friendly label (偏好 / 画像 / 主题·<slug>), ``scope`` is
    ``"global"`` or ``"project"`` (the conversation's folder layer), ``content`` is the
    bullet text for add/update or the matched text for remove. ``project_id`` is the
    folder id when scope is not global (desktop 最近更新 deep-link). Serialized to the
    ``memory_updates.items`` JSONB + the firehose event payload via ``dataclasses.asdict``.

    The ``project`` spellings here are the PERSISTED wire shape (JSONB rows already on
    disk + desktop ``memorySource`` leaf paths + mobile card), not product wording — the
    容器统一为文件夹 rename (双模式工作区 §5.4) stops at this boundary; moving it needs a
    backfill, so the UI translates ``project`` → 「本文件夹」on the read side instead.
    """

    action: str  # "add" | "update" | "remove" | "quota" | "quota_denied" | "quota_holder"
    file: str  # friendly label: 偏好 / 画像 / 主题·<slug>
    section: str  # core section name; "" for a topic note's default bucket
    scope: str  # "global" | "project"
    content: str  # bullet text (add/update) or matched text (remove)
    target: str  # synthetic memory-leaf path the card deep-links to ("" = no leaf)
    project_id: str | None = None  # folder_id when scope is project; else None


def _memory_file_label(file: str) -> str:
    """Map a stored memory file path to the friendly label the card shows."""
    if file == PREFERENCES_MEMORY_FILE:
        return "偏好"
    if file == CORE_MEMORY_FILE:
        return "画像"
    if is_topic_path(file):
        return f"主题·{topic_slug(file)}"
    return file


def _memory_leaf_target(file: str, scope: MemoryScope) -> str:
    """The synthetic memory-leaf path the desktop card deep-links to.

    Mirrors the desktop ``memorySource`` scheme EXACTLY so the conversation-tail card can
    open the precise leaf in the「AI 记忆」editor (Agent记忆与知识系统 §1.6): 偏好 →
    ``global/preferences`` (global-only, §二); 画像 → ``global/profile`` or
    ``project/<folderId>/profile``; 主题 → ``{global|project/<folderId>}/topics/<slug>``.
    "" when the file maps to no editable leaf (no deep-link). ``scope`` is the project
    ``folder_id`` (truthy) or None for the global layer.
    """
    if file == PREFERENCES_MEMORY_FILE:
        return "global/preferences"
    if file == CORE_MEMORY_FILE:
        return f"project/{scope}/profile" if scope else "global/profile"
    if is_topic_path(file):
        slug = topic_slug(file)
        return f"project/{scope}/topics/{slug}" if scope else f"global/topics/{slug}"
    return ""


def _item_from_op(op: MemoryOp, *, file: str, scope: MemoryScope) -> MemoryUpdateItem:
    """Project one applied op into the card's summary item (friendly labels + deep-link)."""
    return MemoryUpdateItem(
        action=op.action.value,
        file=_memory_file_label(file),
        section=op.section or "",
        scope="project" if scope else "global",
        content=(op.content or op.match or "").strip(),
        target=_memory_leaf_target(file, scope),
        project_id=scope if scope else None,
    )


def _enforce_topic_cap(
    ops: Sequence[MemoryOp],
    existing_topics_by_scope: dict[MemoryScope, set[str]],
    cap: int | None,
) -> list[MemoryOp]:
    """Drop ops that would create a NEW topic note beyond ``cap`` — PER SCOPE (anti-bloat).

    Core ops and ops on an already-existing topic always pass; new topic files are admitted
    until that scope's total reaches ``cap``, then dropped (Agent记忆与知识系统 §1.5「按作用域
    各算一份」). A non-positive / None cap means no limit. The cap is counted independently
    for the global and each folder layer, since they are separate note sets.
    """
    if not cap or cap <= 0:
        return list(ops)
    allowed = {scope: set(topics) for scope, topics in existing_topics_by_scope.items()}
    kept: list[MemoryOp] = []
    for op in ops:
        if not is_topic_path(op.file):
            kept.append(op)
            continue
        scope_allowed = allowed.setdefault(op.scope, set())
        if op.file in scope_allowed:
            kept.append(op)
        elif len(scope_allowed) < cap:
            scope_allowed.add(op.file)
            kept.append(op)
        else:
            logger.info("memory.topic_cap_reached", file=op.file, scope=op.scope, cap=cap)
    return kept


async def maintain_user_memory(
    *,
    user_id: str,
    messages: Sequence[ChatMessage],
    extractor: MemoryExtractor,
    store: MemoryStore,
    applier: MemoryApplier | None = None,
    today: str = "",
    section_cap: int | None = None,
    max_topic_files: int | None = None,
    folder_id: str | None = None,
    collect_items: list[MemoryUpdateItem] | None = None,
) -> bool:
    """Consolidate durable knowledge from `messages` into the user's memory folders.

    `messages` is the recent conversation window (reconciled against existing memory).
    `today` (ISO date) enables temporal refresh; `section_cap` bounds bullets per section;
    `max_topic_files` caps on-demand topic notes per scope. `folder_id` is the
    conversation's folder (None for a bare chat): it unlocks the FOLDER scope so a fact
    true only in this folder lands in the folder layer instead of polluting global memory
    (Agent记忆与知识系统 §1.5).

    The extractor sees both the global preferences/profile/topics and (when in a folder)
    the folder's profile/topics, then emits ops targeting a `(scope, file)`. Ops are grouped
    per `(scope, file)` and applied independently, so a per-file CAS / edit only touches the
    notes that moved. Returns True iff at least one memory file changed. No ops (or a no-op
    apply) skips the write. Never raises — failures are logged and swallowed.

    When `collect_items` is given, the ops that landed in files that actually changed are
    appended to it as :class:`MemoryUpdateItem`s — the human-readable「记了什么」the
    conversation-tail card lists (记忆更新对话内可见, §1.6). The list is left empty when
    nothing changed, so the caller persists/pushes a card only for a real update.
    """
    if not messages:
        return False
    applier = applier or MarkdownMemoryApplier(section_cap=section_cap)
    try:
        global_topics = {m.path for m in await store.list(user_id) if is_topic_path(m.path)}
        folder_topics: set[str] = set()
        folder_profile = ""
        if folder_id:
            folder_topics = {
                m.path for m in await store.list(user_id, scope=folder_id) if is_topic_path(m.path)
            }
            folder_profile = await store.load(user_id, CORE_MEMORY_FILE, scope=folder_id)
        current_profile = await store.load(user_id, CORE_MEMORY_FILE)
        current_preferences = await store.load(user_id, PREFERENCES_MEMORY_FILE)
        extract_input = MemoryExtractInput(
            user_id=user_id,
            current_profile=current_profile,
            current_preferences=current_preferences,
            folder_id=folder_id,
            current_folder_memory=folder_profile,
            messages=messages,
            today=today,
            topic_files=sorted(topic_slug(path) for path in global_topics),
            folder_topic_files=sorted(topic_slug(path) for path in folder_topics),
        )
        ops = await extractor.extract(extract_input)
        if not ops:
            parse_result = getattr(extractor, "last_parse_result", None)
            logger.info(
                "memory.maintain_no_ops",
                user_id=user_id,
                memory_empty=_is_cold_start(extract_input),
                message_count=len(messages),
                parsed_ops=0,
                raw_ops=parse_result.raw_ops_count if parse_result else None,
                empty_ops_reason=(
                    _empty_ops_reason(parse_result) if parse_result is not None else None
                ),
            )
            return False
        # Existing topics per scope for the cap. Only add the folder key when there IS a
        # folder — otherwise ``{None: ..., None: ...}`` would collapse and lose the global set.
        existing_by_scope: dict[MemoryScope, set[str]] = {None: global_topics}
        if folder_id:
            existing_by_scope[folder_id] = folder_topics
        ops = _enforce_topic_cap(ops, existing_by_scope, max_topic_files)
        # Group by the (scope, file) target so each note is loaded/applied/saved once and a
        # per-file CAS only fires for notes that actually moved.
        by_target: dict[tuple[MemoryScope, str], list[MemoryOp]] = defaultdict(list)
        for op in ops:
            by_target[(op.scope, op.file)].append(op)
        changed_files = 0
        for (scope, file), file_ops in by_target.items():
            current = await store.load(user_id, file, scope=scope)
            updated = applier.apply(current, file_ops)
            if updated != current:
                await store.save(user_id, file, updated, scope=scope)
                changed_files += 1
                if collect_items is not None:
                    # Summarize the ops that drove THIS file's change for the tail card.
                    # File-granular (a dedup no-op op on a file that changed for another
                    # op may ride along) — truthful enough for「记了什么」, and the user
                    # can edit/delete any bullet in the memory editor.
                    collect_items.extend(
                        _item_from_op(op, file=file, scope=scope) for op in file_ops
                    )
        if changed_files:
            logger.info("memory.user_updated", user_id=user_id, ops=len(ops), files=changed_files)
        return changed_files > 0
    except Exception as e:
        logger.warning("memory.user_maintain_failed", user_id=user_id, error=str(e))
        return False
