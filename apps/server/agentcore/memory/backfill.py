"""One-off backfill: re-queue memory consolidation for users with empty memory.

When the extraction prompt was too conservative, conversations could be marked
consolidated (``memory_synced_at`` advanced) without producing any durable memory.
After the prompt is fixed, this pass resets the watermark for affected users so
the periodic sweeper re-runs consolidation over their existing chats.

Safety: only users whose global 偏好+画像 are empty AND who hold no other memory
notes (topics / folder layers) are touched. Memory is product-always-on (定案 A),
so the scan covers every account. Idempotent — a second run
finds watermarks already NULL and makes no changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import ConversationRepository, UserRepository
from agentcore.memory.store import (
    CORE_MEMORY_FILE,
    PREFERENCES_MEMORY_FILE,
    MemoryStore,
    default_memory_store,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class MemoryBackfillStats:
    """Outcome counters for a backfill run."""

    users_scanned: int
    users_reset: int
    conversations_reset: int
    users_skipped_has_memory: int


async def is_user_memory_empty(store: MemoryStore, user_id: str) -> bool:
    """True when global 偏好+画像 are empty and no other memory notes exist."""
    preferences = await store.load(user_id, PREFERENCES_MEMORY_FILE)
    profile = await store.load(user_id, CORE_MEMORY_FILE)
    if preferences.strip() or profile.strip():
        return False
    if await store.list(user_id):
        return False
    return not await store.project_scopes(user_id)


async def backfill_empty_memory_watermarks(
    *,
    store: MemoryStore | None = None,
    dry_run: bool = False,
) -> MemoryBackfillStats:
    """Reset ``memory_synced_at`` for users with empty memory files.

    ``dry_run=True`` reports what would change without writing. Never raises —
    per-user failures are logged and skipped.
    """
    store = store or default_memory_store()

    async with async_session_factory() as session:
        user_ids = await UserRepository(session).list_user_ids()

    users_reset = 0
    conversations_reset = 0
    users_skipped_has_memory = 0

    for user_id in user_ids:
        try:
            if not await is_user_memory_empty(store, user_id):
                users_skipped_has_memory += 1
                logger.debug("memory.backfill_skip_has_memory", user_id=user_id)
                continue

            async with async_session_factory() as session:
                convs = ConversationRepository(session)
                if dry_run:
                    count = await convs.count_memory_watermarked_chat_conversations(user_id)
                else:
                    count = await convs.reset_memory_synced_at_for_user(user_id)

            if count:
                users_reset += 1
                conversations_reset += count
                event = "memory.backfill_would_reset" if dry_run else "memory.backfill_reset"
                logger.info(event, user_id=user_id, conversations=count)
        except Exception as e:
            logger.warning("memory.backfill_user_failed", user_id=user_id, error=str(e))

    return MemoryBackfillStats(
        users_scanned=len(user_ids),
        users_reset=users_reset,
        conversations_reset=conversations_reset,
        users_skipped_has_memory=users_skipped_has_memory,
    )
