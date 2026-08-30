"""Memory topic directory + folder-layer labels for prompt injection.

Production always-on injection is assembled by ``memory/rules_injection.py`` (read side
injects the full always pool; no per-file char cap). This module keeps:

- Folder-layer labels shared with ``rules_injection`` (global vs folder wording).
- On-demand TOPIC names + their ``description`` for the 按需目录
  (``load_memory_topics`` / :class:`MemoryTopic`).
- The per-scope set of user-disputed note paths (``disputed_memory_paths``), which every
  memory injection / consult path filters against (纠错通道).

Both topic loading paths are gated by the caller-supplied ``enabled`` flag (product resolve
is always on / 定案 A): False ⇒ [] so unit tests can still exercise the off path.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentcore.core.logging import get_logger
from agentcore.memory.store import (
    MemoryScope,
    MemoryStore,
    is_topic_path,
    topic_slug,
)

logger = get_logger(__name__)

# Labels the folder layer inside the shared <设定> block so the model reads those bullets
# as "current folder only" (a global vs folder conflict resolves by wording + proximity,
# §3.2 — no hard-override structure; the user's explicit instruction still wins).
_FOLDER_MEMORY_LABEL = "（以下为「当前文件夹」专属记忆，仅在本文件夹内适用）"
_FOLDER_NAV_LABEL = "（以下为「当前文件夹」导航短入口，只指路、不塞长文）"
# An ANCESTOR folder's memory (双模式工作区 §5.4 沿树继承): same no-hard-override rule —
# outer layers come first and the wording says the nearer layer wins.
_ANCESTOR_MEMORY_LABEL = (
    "（以下为「上层文件夹」的记忆，其下所有文件夹一并适用；"
    "与更靠近当前文件夹的记忆冲突时，以更近的为准）"
)


@dataclass(frozen=True)
class MemoryTopic:
    """One entry in the 按需目录: a consultable topic note's name + its retrieval summary.

    ``name`` is the slug the model passes to ``consult``; ``summary`` is the entry's
    frontmatter ``description`` — written **for retrieval** ("何时该读"), not the note's
    first content line, which describes nothing about when the note is relevant. "" when
    the entry has no description yet (the directory then shows just the name).
    """

    name: str
    summary: str


async def disputed_memory_paths(
    store: MemoryStore, user_id: str, scope: MemoryScope = None
) -> frozenset[str]:
    """Note paths the user marked wrong in one scope — injection must skip these.

    One listing per scope; the flag rides :class:`~agentcore.memory.store.MemoryFileMeta`
    so this costs no extra round trip. Degrades to「nothing disputed」if the store fails,
    matching every other memory read (a dispute that cannot be read must not break a turn).
    """
    try:
        return frozenset(
            meta.path for meta in await store.list(user_id, scope=scope) if meta.disputed
        )
    except Exception as e:  # noqa: BLE001 - memory reads never break turn assembly
        logger.warning("memory.disputed_paths_failed", user_id=user_id, error=str(e))
        return frozenset()


async def _scope_topics(
    store: MemoryStore, user_id: str, scope: MemoryScope
) -> list[tuple[str, str]]:
    """The (name, description) of every live TOPIC note in one scope (按需目录 fodder).

    Built from the listing alone — the body never rides the directory, and a
    user-disputed note is left out entirely (it must not be consultable either).
    """
    out: list[tuple[str, str]] = []
    for meta in await store.list(user_id, scope=scope):
        if not is_topic_path(meta.path) or meta.disputed:
            continue
        out.append((topic_slug(meta.path), meta.description))
    return out


async def load_memory_topics(
    store: MemoryStore, user_id: str, *, folder_id: str | None, enabled: bool
) -> list[MemoryTopic]:
    """Merge global + the folder chain's on-demand TOPIC notes for the 按需目录 (or []).

    Each topic rides the prompt as its NAME plus its ``description`` — the summary written
    for retrieval, which is what lets the model judge WHEN to ``consult`` the note; the body
    itself never rides the 常驻 prefix. De-duplicated by name and sorted for a stable prefix;
    a topic that exists in several scopes appears once (the OUTER summary wins, matching the
    stable-prefix layer — the body ``consult`` returns is still the nearest layer's).

    ``folder_id`` selects the folder whose layer — and, since §5.4 nesting, whose ancestors'
    layers — to merge; NULL ⇒ global topics only.

    Account-ticketed turns read the process prepare snapshot only (warm seeds it, chain
    included; miss → []); no ticket keeps the store / local-DB path.
    """
    if not enabled:
        return []
    from agentcore.account.credentials import get_account_credentials
    from agentcore.memory.account_prepare_cache import get_account_rules_memory_snapshot
    from agentcore.memory.scope_chain import db_scope_chain

    if get_account_credentials() is not None:
        snap = get_account_rules_memory_snapshot(user_id, folder_id)
        if snap is None:
            return []
        return list(snap.memory_topics)

    summaries: dict[str, str] = {}
    for name, summary in await _scope_topics(store, user_id, None):
        summaries.setdefault(name, summary)
    for scope in await db_scope_chain(user_id, folder_id):
        for name, summary in await _scope_topics(store, user_id, scope):
            summaries.setdefault(name, summary)
    return [MemoryTopic(name=name, summary=summaries[name]) for name in sorted(summaries)]
