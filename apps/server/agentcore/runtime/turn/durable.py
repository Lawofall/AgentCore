"""Restart copy of the unstarted FIFO.

The live deque stays process-local. When a store is installed, every enqueue /
edit / reorder / cancel / pop mirrors it. Boot loads that copy back and drains
only after a restart-killed turn has released its lease. Credentials are not
written; a restored item resolves them at start.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from agentcore.core.logging import get_logger

logger = get_logger(__name__)

ENGINE_CLOUD = "cloud"
ENGINE_SIDECAR = "sidecar"


def turn_queue_durable_enabled() -> bool:
    """Restart copy is on unless a test process opts out.

    Unset or any value other than ``0`` installs the store. The pytest
    conftest sets ``0`` so initialize / lifespan do not read or write a
    shared database.
    """
    return os.environ.get("AGENTCORE_TURN_QUEUE_DURABLE", "1") != "0"

DrainBlocker = Callable[[str], Awaitable[bool]]
CredentialResolver = Callable[[str, str, bool], Awaitable[tuple[Any, bool | None]]]


class TurnQueueStore(Protocol):
    async def upsert(
        self,
        conversation_id: str,
        item: Any,
        position: int,
    ) -> None: ...

    async def update_payload(
        self,
        queue_id: str,
        *,
        content: str,
        attachments: list[Any],
        agent_mentions: list[Any],
    ) -> None: ...

    async def rewrite_order(self, conversation_id: str, queue_ids: list[str]) -> None: ...

    async def delete(self, queue_id: str) -> None: ...

    async def claim_delete(self, queue_id: str) -> bool: ...

    async def delete_conversation(self, conversation_id: str) -> None: ...

    async def load(self) -> list[tuple[str, Any]]: ...


_store: TurnQueueStore | None = None
_blocker: DrainBlocker | None = None
_resolver: CredentialResolver | None = None
_pending: set[asyncio.Task[None]] = set()


def install_turn_queue_store(
    store: TurnQueueStore | None,
    *,
    blocker: DrainBlocker | None = None,
) -> None:
    """Install the restart copy. ``None`` keeps the queue memory-only."""
    global _store, _blocker
    _store = store
    _blocker = blocker
    from agentcore.db.repositories.conversations import bind_conversations_removed
    from agentcore.runtime.turn.queue import turn_queue

    def _clear(conversation_ids: Sequence[str]) -> None:
        for conversation_id in conversation_ids:
            turn_queue.clear(conversation_id)

    bind_conversations_removed(_clear)


def set_drain_credential_resolver(resolver: CredentialResolver | None) -> None:
    """Test seam. Production drain uses :func:`resolve_drain_credentials`."""
    global _resolver
    _resolver = resolver


def reset_turn_queue_durable() -> None:
    """Drop the store, blocker, and resolver. In-flight mirrors no-op after this."""
    global _store, _blocker, _resolver
    _store = None
    _blocker = None
    _resolver = None
    from agentcore.db.repositories.conversations import bind_conversations_removed

    bind_conversations_removed(None)


def durable_payload(conversation_id: str, item: Any, position: int) -> dict[str, Any]:
    """Fields written for a restart. The enqueue-time key is intentionally absent."""
    return {
        "queue_id": item.queue_id,
        "conversation_id": conversation_id,
        "user_id": item.user_id or "",
        "position": position,
        "content": item.content,
        "attachments": list(item.attachments or []),
        "agent_mentions": list(item.agent_mentions or []),
        "requires_tools": bool(item.requires_tools),
        "x_client_platform": item.x_client_platform,
        "origin_device_id": item.origin_device_id,
        "interjection_id": item.interjection_id,
        "user_message_id": item.user_message_id,
        "message_id": item.message_id,
        "trace_id": item.trace_id,
    }


def queued_turn_from_record(row: Any) -> Any:
    """Rebuild a pending item. Credentials are resolved at drain, not read back."""
    from agentcore.runtime.turn.queue import QueuedTurn

    return QueuedTurn(
        queue_id=row.queue_id,
        content=row.content or "",
        attachments=list(row.attachments or []),
        agent_mentions=list(row.agent_mentions or []),
        requires_tools=bool(row.requires_tools),
        x_client_platform=row.x_client_platform,
        origin_device_id=row.origin_device_id,
        user_id=row.user_id or "",
        interjection_id=row.interjection_id,
        user_message_id=row.user_message_id,
        message_id=row.message_id,
        trace_id=row.trace_id,
        credentials_pending=True,
        durable_claim=True,
    )


class PostgresTurnQueueStore:
    """One engine's rows. Cloud loads every cloud row; a sidecar loads its root."""

    def __init__(
        self,
        *,
        engine: str,
        user_id: str = "",
        local_root_id: str | None = None,
    ) -> None:
        self._engine = engine
        self._user_id = user_id
        self._local_root_id = local_root_id

    async def upsert(self, conversation_id: str, item: Any, position: int) -> None:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories.turn_queue import TurnQueueRepository

        payload = durable_payload(conversation_id, item, position)
        async with async_session_factory() as session:
            await TurnQueueRepository(session).upsert(
                **payload,
                engine=self._engine,
                local_root_id=self._local_root_id,
            )

    async def update_payload(
        self,
        queue_id: str,
        *,
        content: str,
        attachments: list[Any],
        agent_mentions: list[Any],
    ) -> None:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories.turn_queue import TurnQueueRepository

        async with async_session_factory() as session:
            await TurnQueueRepository(session).update_payload(
                queue_id,
                content=content,
                attachments=attachments,
                agent_mentions=agent_mentions,
            )

    async def rewrite_order(self, conversation_id: str, queue_ids: list[str]) -> None:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories.turn_queue import TurnQueueRepository

        async with async_session_factory() as session:
            await TurnQueueRepository(session).rewrite_order(conversation_id, queue_ids)

    async def delete(self, queue_id: str) -> None:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories.turn_queue import TurnQueueRepository

        async with async_session_factory() as session:
            await TurnQueueRepository(session).delete(queue_id)

    async def claim_delete(self, queue_id: str) -> bool:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories.turn_queue import TurnQueueRepository

        async with async_session_factory() as session:
            removed = await TurnQueueRepository(session).delete(queue_id)
        return removed > 0

    async def delete_conversation(self, conversation_id: str) -> None:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories.turn_queue import TurnQueueRepository

        async with async_session_factory() as session:
            await TurnQueueRepository(session).delete_conversation(conversation_id)

    async def load(self) -> list[tuple[str, Any]]:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories.turn_queue import TurnQueueRepository

        async with async_session_factory() as session:
            rows = await TurnQueueRepository(session).load(
                engine=self._engine,
                user_id=self._user_id,
                local_root_id=self._local_root_id,
            )
        return [(row.conversation_id, queued_turn_from_record(row)) for row in rows]


def install_postgres_turn_queue(
    *,
    engine: str,
    user_id: str = "",
    local_root_id: str | None = None,
) -> None:
    """Bind this process to its rows. Cloud also waits out an in-flight lease."""
    blocker = _cloud_lease_blocks if engine == ENGINE_CLOUD else None
    install_turn_queue_store(
        PostgresTurnQueueStore(
            engine=engine,
            user_id=user_id,
            local_root_id=local_root_id,
        ),
        blocker=blocker,
    )


def schedule_turn_queue_mirror(conversation_id: str, queue_id: str) -> None:
    """Copy one pending item from memory. No-op until a store is installed."""
    store = _store
    if store is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            "turn_queue.durable_write_failed",
            conversation_id=conversation_id,
            queue_id=queue_id,
            error="no running loop",
        )
        return
    try:
        task = loop.create_task(
            _mirror_one(store, conversation_id, queue_id),
            name=f"turn-queue-mirror-{queue_id}",
        )
    except RuntimeError as exc:
        logger.warning(
            "turn_queue.durable_write_failed",
            conversation_id=conversation_id,
            queue_id=queue_id,
            error=str(exc),
        )
        return
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def flush_turn_queue_durable() -> None:
    """Wait for mirrors scheduled on this loop. Shutdown and the send ack use this."""
    if not _pending:
        return
    await asyncio.gather(*list(_pending), return_exceptions=True)


async def _mirror_one(store: TurnQueueStore, conversation_id: str, queue_id: str) -> None:
    from agentcore.runtime.turn.queue import turn_queue

    try:
        async with turn_queue.mutation_lock(conversation_id):
            pending = turn_queue.list_pending(conversation_id)
            for position, item in enumerate(pending, start=1):
                if item.queue_id == queue_id:
                    await store.upsert(conversation_id, item, position)
                    return
            await store.delete(queue_id)
    except Exception as exc:  # noqa: BLE001 — durability must not break the live queue
        logger.warning(
            "turn_queue.durable_write_failed",
            conversation_id=conversation_id,
            queue_id=queue_id,
            error=str(exc),
        )


async def update_durable_payload(
    queue_id: str,
    *,
    content: str,
    attachments: list[Any],
    agent_mentions: list[Any],
) -> bool:
    store = _store
    if store is None:
        return True
    try:
        await store.update_payload(
            queue_id,
            content=content,
            attachments=attachments,
            agent_mentions=agent_mentions,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "turn_queue.durable_write_failed",
            queue_id=queue_id,
            error=str(exc),
        )
        return False
    return True


async def delete_durable_item(queue_id: str) -> bool:
    store = _store
    if store is None:
        return True
    try:
        await store.delete(queue_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "turn_queue.durable_write_failed",
            queue_id=queue_id,
            error=str(exc),
        )
        return False
    return True


async def claim_durable_start(item: Any) -> bool:
    """True when this process should start ``item``.

    A restored row is deleted only by the process that wins the claim, so two
    API workers cannot both run it. A live enqueue starts even if the mirror
    has not landed yet.
    """
    store = _store
    if store is None or not getattr(item, "durable_claim", False):
        if store is not None:
            try:
                await store.delete(item.queue_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "turn_queue.durable_write_failed",
                    queue_id=item.queue_id,
                    error=str(exc),
                )
        return True
    try:
        return await store.claim_delete(item.queue_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "turn_queue.durable_write_failed",
            queue_id=item.queue_id,
            error=str(exc),
        )
        return False


async def sync_durable_order(conversation_id: str) -> bool:
    """Rewrite positions from the current memory FIFO. Caller holds the lock."""
    store = _store
    if store is None:
        return True
    from agentcore.runtime.turn.queue import turn_queue

    queue_ids = [item.queue_id for item in turn_queue.list_pending(conversation_id)]
    try:
        await store.rewrite_order(conversation_id, queue_ids)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "turn_queue.durable_write_failed",
            conversation_id=conversation_id,
            error=str(exc),
        )
        return False
    return True


async def delete_durable_conversation(conversation_id: str) -> None:
    store = _store
    if store is None:
        return
    try:
        await store.delete_conversation(conversation_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "turn_queue.durable_write_failed",
            conversation_id=conversation_id,
            error=str(exc),
        )


async def drain_blocked(conversation_id: str) -> bool:
    """True when a restart-killed turn still owns the slot."""
    blocker = _blocker
    if blocker is None:
        return False
    try:
        return await blocker(conversation_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "turn_queue.drain_block_check_failed",
            conversation_id=conversation_id,
            error=str(exc),
        )
        return False


def note_conversation_slot_free(conversation_id: str) -> None:
    """The recovered turn released the slot. Drain whatever is still queued."""
    from agentcore.runtime.turn.queue import turn_queue

    turn_queue.schedule_drain(conversation_id)


async def restore_durable_turn_queue() -> int:
    """Load this process's unstarted items and arm drain. Returns how many loaded."""
    store = _store
    if store is None:
        return 0
    from agentcore.runtime.turn.queue import turn_queue

    try:
        loaded = await store.load()
    except Exception as exc:  # noqa: BLE001
        logger.warning("turn_queue.restore_failed", error=str(exc))
        return 0
    restored = 0
    conversations: list[str] = []
    for conversation_id, item in loaded:
        if turn_queue.find_pending(conversation_id, item.queue_id) is not None:
            continue
        turn_queue.enqueue(conversation_id, item)
        restored += 1
        if conversation_id not in conversations:
            conversations.append(conversation_id)
    for conversation_id in conversations:
        turn_queue.schedule_drain(conversation_id)
    if restored:
        logger.info("turn_queue.restored", count=restored, conversations=len(conversations))
    return restored


async def resolve_drain_credentials(
    user_id: str,
    conversation_id: str,
    *,
    needs_tools: bool,
) -> tuple[Any, bool | None]:
    """Current key for a restored item. Not the key captured at enqueue."""
    if _resolver is not None:
        return await _resolver(user_id, conversation_id, needs_tools)
    from agentcore.billing.gate import preflight_llm_credentials
    from agentcore.core.errors import BYOK_KEY_REQUIRED_MESSAGE
    from agentcore.db.base import async_session_factory
    from agentcore.db.repositories import (
        ConversationRepository,
        CostEventRepository,
        UserRepository,
    )
    from agentcore.db.repositories.auth import UserLlmProviderRepository
    from agentcore.llm.resolve import (
        platform_llm_credentials,
        resolve_conversation_model_selection,
    )

    async with async_session_factory() as session:
        user = await UserRepository(session).get_by_id(user_id)
        if user is None:
            raise RuntimeError("queued turn user is missing")
        conv = await ConversationRepository(session).get_by_id(conversation_id, user_id=user_id)
        if conv is None:
            raise RuntimeError("queued turn conversation is missing")
        selection = await resolve_conversation_model_selection(session, conv, user_id)
        supports: bool | None = None
        if selection.provider_id:
            provider = await UserLlmProviderRepository(session).get(
                selection.provider_id, user_id=user_id
            )
            if provider is not None:
                supports = provider.supports_tools
        credentials = await preflight_llm_credentials(
            session=session,
            user=user,
            cost_repo=CostEventRepository(session),
            byok_missing_message=BYOK_KEY_REQUIRED_MESSAGE,
            model_origin=selection.origin,
            provider_id=selection.provider_id,
        )
        if selection.origin == "platform":
            credentials = platform_llm_credentials(model=selection.model)
        return credentials, supports


async def _cloud_lease_blocks(conversation_id: str) -> bool:
    """A non-local turn lease still owns this conversation (crash recover in flight)."""
    from agentcore.config import settings

    if not settings.turn_lease_enabled:
        return False
    from agentcore.db.base import async_session_factory
    from agentcore.runtime.leases.repo import TurnLeaseRepository
    from agentcore.runtime.leases.service import is_local_turn_lease

    async with async_session_factory() as session:
        rows = await TurnLeaseRepository(session).list_for_conversation(conversation_id)
    return any(not is_local_turn_lease(row) for row in rows)
