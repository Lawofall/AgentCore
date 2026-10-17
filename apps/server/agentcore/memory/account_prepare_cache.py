"""Process-local user-rules snapshot for account-ticketed prepare/resume.

When sidecar turns bind account credentials, prepare must not serially await
``/rules/list``. Warm (non-turn) fetches once, seeds this cache; prepare/resume
read ``cache_only`` (miss → empty injection).

Mirrors MCP discover cache (``tools/mcp/wire.py``): success TTL ~300s; degraded
entries use a shorter negative TTL.

Entries lapse, and a lapsed entry injects **nothing** (no cloud fallback), so the
warmer owns renewal: the warm RPC hands back this entry's remaining life
(``account_rules_memory_ttl_remaining``) and the desktop re-warms before it runs
out — including on a TTL cadence while an execution is still in flight, so a
follow-up user turn still hits. Never let a caller assume "warmed once"
means "warm forever".

During prepare→assemble, ``prepare_reads_cache_only`` is bound so ticketed
``DocumentMemoryStore`` reads stay off the cloud.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from agentcore.account.credentials import (
    AccountCredentials,
    cloud_list_user_rules,
)
from agentcore.core.logging import get_logger

logger = get_logger(__name__)

_CACHE_TTL_SECONDS = 300.0
_NEGATIVE_CACHE_TTL_SECONDS = 30.0

# Bound True for the prepare→assemble window (see pipeline/run.py). When set,
# DocumentMemoryStore ticketed reads use this snapshot only (miss → empty).
prepare_reads_cache_only: ContextVar[bool] = ContextVar(
    "prepare_reads_cache_only", default=False
)
# Conversation folder_id used as the warm-cache key while cache_only is on.
prepare_account_folder_id: ContextVar[str | None] = ContextVar(
    "prepare_account_folder_id", default=None
)


@dataclass(frozen=True)
class AccountPrepareSnapshot:
    """One warm fetch covering prepare's user-rule injection needs."""

    rules_payload: Mapping[str, Any] = field(default_factory=dict)
    # Unused leftover slot (DocumentMemoryStore cache_only still keys off it).
    memory_bodies: Mapping[tuple[str, str], str] = field(default_factory=dict)
    memory_descriptions: Mapping[tuple[str, str], str] = field(default_factory=dict)
    degraded: bool = False


@dataclass(frozen=True)
class _CacheEntry:
    snapshot: AccountPrepareSnapshot
    expires_at: float


_cache: dict[tuple[str, str | None], _CacheEntry] = {}


def _cache_miss_origin_fields() -> dict[str, str]:
    """Searchable origin on empty injection (historical ``execution_harvest``)."""
    from agentcore.runtime.delegate.post_close_gate import current_user_message_origin

    origin = current_user_message_origin()
    return {"origin": origin} if origin else {}


def drop_account_rules_memory_cache(
    user_id: str, folder_id: str | None
) -> None:
    """Drop one prepare-cache key (folder delete: hibernate that desk's snapshot)."""
    _cache.pop(_cache_key(user_id, folder_id), None)


def drop_account_rules_memory_cache_for_user(user_id: str) -> None:
    """Drop every prepare-cache key for one account (换用 overlay changed)."""
    uid = (user_id or "").strip()
    stale = [key for key in _cache if key[0] == uid]
    for key in stale:
        _cache.pop(key, None)


def clear_account_rules_memory_cache() -> None:
    """Drop every prepare-cache key (tests / process reset)."""
    _cache.clear()


async def hibernate_folder_injection_cache(
    user_id: str, folder_ids: Sequence[str], *, rewarm: bool = False
) -> None:
    """Stop injecting a just-deleted desk from a stale process snapshot.

    Soft-delete keeps documents so restore can bring 设定 back; this only drops
    (and optionally re-warms) the sidecar/API prepare cache. ``rewarm`` is for
    the ticketed sidecar after folder soft-delete: the next list is global-only.
    """
    uid = (user_id or "").strip()
    if not uid:
        return
    ids = [fid for fid in folder_ids if fid]
    for fid in ids:
        drop_account_rules_memory_cache(uid, fid)
    if not rewarm:
        return
    from agentcore.account.credentials import get_account_credentials

    creds = get_account_credentials()
    if creds is None:
        return
    for fid in ids:
        try:
            await warm_account_rules_memory(creds, user_id=uid, folder_id=fid)
        except Exception as e:  # noqa: BLE001 — cache refresh must not fail the delete
            logger.warning(
                "account.rules_memory_hibernate_rewarm_failed",
                user_id=uid,
                folder_id=fid,
                error=str(e),
            )


def _cache_key(user_id: str, folder_id: str | None) -> tuple[str, str | None]:
    return ((user_id or "").strip(), folder_id)


def get_account_rules_memory_snapshot(
    user_id: str, folder_id: str | None
) -> AccountPrepareSnapshot | None:
    """Return a live snapshot, or None on miss / lapse (empty injection)."""
    key = _cache_key(user_id, folder_id)
    entry = _cache.get(key)
    if entry is not None and entry.expires_at > time.monotonic():
        logger.info(
            "account.rules_memory_cache_hit",
            user_id=key[0] or None,
            folder_id=folder_id,
            degraded=entry.snapshot.degraded,
        )
        return entry.snapshot
    logger.info(
        "account.rules_memory_cache_miss",
        user_id=key[0] or None,
        folder_id=folder_id,
        **_cache_miss_origin_fields(),
    )
    return None


def account_rules_memory_ttl_remaining(
    user_id: str,
    folder_id: str | None,
) -> float:
    """Seconds this snapshot still serves prepare (0.0 = absent / lapsed)."""
    entry = _cache.get(_cache_key(user_id, folder_id))
    if entry is None:
        return 0.0
    return max(0.0, entry.expires_at - time.monotonic())


def seed_account_rules_memory_cache(
    user_id: str,
    folder_id: str | None,
    snapshot: AccountPrepareSnapshot,
) -> None:
    """Write an already-fetched snapshot into the process cache (non-turn warm)."""
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required to seed account rules cache")
    ttl = (
        _NEGATIVE_CACHE_TTL_SECONDS
        if snapshot.degraded
        else _CACHE_TTL_SECONDS
    )
    key = _cache_key(uid, folder_id)
    _cache[key] = _CacheEntry(
        snapshot=snapshot, expires_at=time.monotonic() + ttl
    )
    logger.info(
        "account.rules_memory_cache_seed",
        user_id=uid,
        folder_id=folder_id,
        degraded=snapshot.degraded,
        ttl_seconds=ttl,
    )


def _scope_key(scope: str | None) -> str:
    return "" if scope is None else scope


async def warm_account_rules_memory(
    creds: AccountCredentials,
    *,
    user_id: str,
    folder_id: str | None,
) -> AccountPrepareSnapshot:
    """Fetch ``/rules/list`` once, seed cache, return snapshot."""
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required to warm account rules cache")

    degraded = False
    rules_payload: dict[str, Any] = {}
    try:
        rules_res = await cloud_list_user_rules(creds, folder_id=folder_id)
    except Exception as e:  # noqa: BLE001 — warm degrades rather than raising
        degraded = True
        logger.warning(
            "account.rules_memory_warm_failed",
            user_id=uid,
            folder_id=folder_id,
            part="rules",
            error=str(e),
        )
        rules_res = None
    if isinstance(rules_res, dict):
        rules_payload = dict(rules_res)
    elif rules_res is not None:
        degraded = True

    snapshot = AccountPrepareSnapshot(
        rules_payload=rules_payload,
        degraded=degraded,
    )
    seed_account_rules_memory_cache(uid, folder_id, snapshot)
    return snapshot


def memory_body_from_snapshot(
    snapshot: AccountPrepareSnapshot,
    path: str,
    *,
    scope: str | None,
) -> str:
    """Look up one leftover store body from a warm snapshot (missing → \"\")."""
    return snapshot.memory_bodies.get((_scope_key(scope), path), "")


def snapshot_for_prepare_store_read(
    user_id: str,
) -> AccountPrepareSnapshot | None:
    """Snapshot for DocumentMemoryStore under ``prepare_reads_cache_only``."""
    if not prepare_reads_cache_only.get():
        return None
    return get_account_rules_memory_snapshot(
        user_id, prepare_account_folder_id.get()
    )


__all__ = [
    "AccountPrepareSnapshot",
    "account_rules_memory_ttl_remaining",
    "clear_account_rules_memory_cache",
    "drop_account_rules_memory_cache",
    "get_account_rules_memory_snapshot",
    "hibernate_folder_injection_cache",
    "memory_body_from_snapshot",
    "prepare_account_folder_id",
    "prepare_reads_cache_only",
    "seed_account_rules_memory_cache",
    "snapshot_for_prepare_store_read",
    "warm_account_rules_memory",
]
