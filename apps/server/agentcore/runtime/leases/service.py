"""Process-local lease helpers: owner id, acquire / heartbeat / release."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.core.types import new_id
from agentcore.db.base import async_session_factory
from agentcore.runtime.leases.repo import TurnLeaseRepository

logger = get_logger(__name__)

# Minted once per process — durable leases identify this worker.
_OWNER_ID: str = new_id()

# Sidecar occupy heartbeats from the desktop across API workers, so the owner
# must be deterministic from ``message_id`` (not this process's ``_OWNER_ID``).
# Prefix also tells the crash sweeper not to redrive on the cloud.
LOCAL_TURN_LEASE_OWNER_PREFIX = "local-turn:"


def lease_owner_id() -> str:
    """This process's lease owner id (stable for the process lifetime)."""
    return _OWNER_ID


def local_turn_lease_owner_id(message_id: str) -> str:
    """Stable lease owner for a sidecar occupy (any API worker can heartbeat)."""
    return f"{LOCAL_TURN_LEASE_OWNER_PREFIX}{message_id}"


def is_local_turn_lease(row: object) -> bool:
    """True when this lease belongs to a desktop sidecar occupy (not a cloud turn)."""
    owner = str(getattr(row, "owner_id", None) or "")
    if owner.startswith(LOCAL_TURN_LEASE_OWNER_PREFIX):
        return True
    meta = getattr(row, "meta", None)
    return isinstance(meta, dict) and meta.get("source") == "local"


async def list_fresh_conversation_ids_for_user(
    user_id: str,
    *,
    session: AsyncSession | None = None,
    after: datetime | None = None,
) -> list[str]:
    """Distinct conversation ids this user still holds a fresh lease on.

    ``after`` defaults to now minus ``turn_lease_ttl_seconds``. Pass the request
    session when the caller already has one (fulfill connect seed).
    """
    if not user_id:
        return []
    cutoff = after or (
        datetime.now(UTC) - timedelta(seconds=settings.turn_lease_ttl_seconds)
    )

    async def _load(db: AsyncSession) -> list[str]:
        rows = await TurnLeaseRepository(db).list_fresh_for_user(user_id, after=cutoff)
        seen: set[str] = set()
        ids: list[str] = []
        for row in rows:
            cid = row.conversation_id
            if cid and cid not in seen:
                seen.add(cid)
                ids.append(cid)
        return ids

    if session is not None:
        return await _load(session)
    async with async_session_factory() as db:
        return await _load(db)


async def acquire_turn_lease(
    *,
    message_id: str,
    conversation_id: str,
    user_id: str,
    phase: str = "running",
    meta: dict[str, Any] | None = None,
    owner_id: str | None = None,
) -> str:
    """Write / refresh the durable RUNNING lease; returns owner_id."""
    owner = owner_id or _OWNER_ID
    try:
        async with async_session_factory() as session:
            await TurnLeaseRepository(session).upsert(
                message_id=message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                owner_id=owner,
                phase=phase,
                meta=meta,
            )
    except Exception as e:  # noqa: BLE001 — lease must never block the turn
        logger.warning(
            "turn_lease.acquire_failed",
            message_id=message_id,
            error=str(e),
        )
    return owner


async def heartbeat_turn_lease(
    message_id: str,
    *,
    owner_id: str | None = None,
    phase: str | None = None,
    conversation_id: str | None = None,
) -> bool:
    """Bump the lease heartbeat; returns False if ownership was lost."""
    owner = owner_id or _OWNER_ID
    try:
        async with async_session_factory() as session:
            return await TurnLeaseRepository(session).heartbeat(
                message_id,
                owner_id=owner,
                phase=phase,
                conversation_id=conversation_id,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "turn_lease.heartbeat_failed",
            message_id=message_id,
            error=str(e),
        )
        return False


async def release_turn_lease(
    message_id: str,
    *,
    owner_id: str | None = None,
) -> None:
    """Clear the durable lease (terminal / pause / stop)."""
    owner = owner_id or _OWNER_ID
    try:
        async with async_session_factory() as session:
            await TurnLeaseRepository(session).release(message_id, owner_id=owner)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "turn_lease.release_failed",
            message_id=message_id,
            error=str(e),
        )


async def orphan_turn_lease(
    message_id: str,
    *,
    owner_id: str | None = None,
) -> None:
    """Keep the lease as a sweeper crime-scene after true hard kill / crash.

    Unlike :func:`release_turn_lease`, this must not delete the row — a hard kill
    without lifespan salvage (SIGKILL / crash) otherwise leaves no expired lease
    for recover. Graceful lifespan shutdown must not call this — it interrupt-closes
    and :func:`release_turn_lease` instead.
    """
    owner = owner_id or _OWNER_ID
    try:
        async with async_session_factory() as session:
            ok = await TurnLeaseRepository(session).mark_orphaned(
                message_id, owner_id=owner
            )
        if ok:
            logger.info("turn_lease.orphaned", message_id=message_id, owner_id=owner)
        else:
            logger.warning(
                "turn_lease.orphan_miss",
                message_id=message_id,
                owner_id=owner,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "turn_lease.orphan_failed",
            message_id=message_id,
            error=str(e),
        )


async def lease_heartbeat_loop(
    message_id: str,
    *,
    owner_id: str,
    interval_seconds: float,
    stop: asyncio.Event,
    phase: str = "running",
) -> None:
    """Background heartbeat until ``stop`` is set (cancelled cleanly on shutdown)."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            return
        except TimeoutError:
            ok = await heartbeat_turn_lease(message_id, owner_id=owner_id, phase=phase)
            if not ok:
                logger.warning(
                    "turn_lease.ownership_lost",
                    message_id=message_id,
                    owner_id=owner_id,
                )
                return
