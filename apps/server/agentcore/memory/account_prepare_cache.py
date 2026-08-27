"""Process-local rules/memory snapshot for account-ticketed prepare/resume.

When sidecar turns bind account credentials, prepare must not serially await
``/rules/list`` / ``memory/list|load``. Warm (non-turn) fetches once, seeds this
cache; prepare/resume read ``cache_only`` (miss → empty injection).

Mirrors MCP discover cache (``tools/mcp/wire.py``): success TTL ~300s; degraded
entries use a shorter negative TTL.

Entries lapse, and a lapsed entry injects **nothing** (no cloud fallback), so the
warmer owns renewal: the warm RPC hands back this entry's remaining life
(``account_rules_memory_ttl_remaining``) and the desktop re-warms before it runs
out — including on a TTL cadence while an execution is still in flight, so a
follow-up user turn still hits. Never let a caller assume "warmed once"
means "warm forever".

During prepare→assemble, ``prepare_reads_cache_only`` is bound so
``DocumentMemoryStore`` list/load/save also stay on this snapshot (no sync cloud).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from agentcore.account.credentials import (
    AccountCredentials,
    cloud_list_user_rules,
    cloud_memory_list,
    cloud_memory_load,
    cloud_memory_scope_state_get,
)
from agentcore.core.logging import get_logger
from agentcore.memory.episode_store import ScopeMemoryMeta
from agentcore.memory.injection import MemoryTopic
from agentcore.memory.scope_chain import ancestor_scopes, cloud_scope_chain
from agentcore.memory.store import (
    ALWAYS_MEMORY_FILES,
    CORE_MEMORY_FILE,
    NAVIGATION_MEMORY_FILE,
    is_topic_path,
    topic_slug,
)

logger = get_logger(__name__)

_CACHE_TTL_SECONDS = 300.0
_NEGATIVE_CACHE_TTL_SECONDS = 30.0

# Bound True for the prepare→assemble window (see pipeline/run.py). When set,
# DocumentMemoryStore ticketed reads use this snapshot only (miss → empty);
# saves no-op so explore meta drift cannot block TTFT on cloud writes.
prepare_reads_cache_only: ContextVar[bool] = ContextVar(
    "prepare_reads_cache_only", default=False
)
# Conversation folder_id used as the warm-cache key while cache_only is on
# (snapshot holds both global ``""`` and folder bodies under one seed).
prepare_account_folder_id: ContextVar[str | None] = ContextVar(
    "prepare_account_folder_id", default=None
)


@dataclass(frozen=True)
class AccountPrepareSnapshot:
    """One warm fetch covering prepare's rules + memory injection needs."""

    rules_payload: Mapping[str, Any] = field(default_factory=dict)
    # (scope_key, path) → markdown; scope_key "" = global, else folder_id.
    # Only entries that may ride the prompt are cached — warm drops user-disputed notes.
    memory_bodies: Mapping[tuple[str, str], str] = field(default_factory=dict)
    # (scope_key, path) → retrieval description (absent when the entry has none).
    memory_descriptions: Mapping[tuple[str, str], str] = field(default_factory=dict)
    # scope_key → consolidation/explore sidecar (replaces ``_memory_meta.json`` warm).
    scope_states: Mapping[str, ScopeMemoryMeta] = field(default_factory=dict)
    memory_topics: tuple[MemoryTopic, ...] = ()
    # Folder scope chain, outermost-first, current folder last (双模式工作区 §5.4 沿树继承).
    # The sidecar has no folders table, so the cloud resolves it and it rides the snapshot.
    folder_chain: tuple[str, ...] = ()
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


def clear_account_rules_memory_cache() -> None:
    """Drop process-local prepare cache (tests / forced refresh)."""
    _cache.clear()


def _cache_key(user_id: str, folder_id: str | None) -> tuple[str, str | None]:
    return ((user_id or "").strip(), folder_id)


def get_account_rules_memory_snapshot(
    user_id: str,
    folder_id: str | None,
) -> AccountPrepareSnapshot | None:
    """Read process cache only. Miss → None (caller injects empty; no cloud)."""
    key = _cache_key(user_id, folder_id)
    now = time.monotonic()
    entry = _cache.get(key)
    if entry is not None and entry.expires_at > now:
        logger.info(
            "account.rules_memory_cache_hit",
            user_id=key[0] or None,
            folder_id=folder_id,
            degraded=entry.snapshot.degraded,
            topic_count=len(entry.snapshot.memory_topics),
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
    """Seconds this snapshot still serves prepare (0.0 = absent / lapsed).

    The renewal handshake's authoritative half: warm callers (desktop sidecar
    manager) re-warm within this window — including while a long execution is
    still in flight, so sidecar-internal harvest turns do not see a lapsed
    cache. Prepare reads cache-only and a lapsed entry injects no rules /
    memory. Unlike ``get_account_rules_memory_snapshot`` this is a plain
    read — no hit/miss log.
    """
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
        raise ValueError("user_id is required to seed account rules/memory cache")
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
        topic_count=len(snapshot.memory_topics),
        memory_file_count=len(snapshot.memory_bodies),
        ttl_seconds=ttl,
    )


def _scope_key(scope: str | None) -> str:
    return "" if scope is None else scope


# One scope's warm result: bodies, retrieval descriptions (both keyed by
# ``(scope_key, path)``), and this scope's (topic slug, description) pairs.
_ScopeWarm = tuple[
    dict[tuple[str, str], str], dict[tuple[str, str], str], list[tuple[str, str]]
]


def _wanted_paths(
    files: list[dict[str, Any]], *, scope: str | None
) -> list[tuple[str, str]]:
    """``(path, description)`` of the entries injection needs from one scope listing.

    Always-injected cores + topics only (scope state is warmed separately), and entries
    the user marked wrong are dropped here — the warm snapshot is exactly「what may ride
    the prompt」, so a disputed note never reaches injection, the 按需目录, or a cache_only
    store read. ``description`` rides along because the directory is built from the
    listing, not from note bodies.
    """
    wanted: list[tuple[str, str]] = []
    seen: set[str] = set()
    always = (
        set(ALWAYS_MEMORY_FILES)
        if scope is None
        else {CORE_MEMORY_FILE, NAVIGATION_MEMORY_FILE}
    )
    for item in files:
        path = str(item.get("path") or "")
        if not path or path in seen or item.get("disputed"):
            continue
        if path in always or is_topic_path(path):
            seen.add(path)
            wanted.append((path, str(item.get("description") or "")))
    return wanted


def _parse_scope_state(data: dict[str, Any]) -> ScopeMemoryMeta:
    from datetime import UTC, datetime

    last_raw = data.get("last_semantic_at")
    last = None
    if isinstance(last_raw, str) and last_raw.strip():
        try:
            last = datetime.fromisoformat(last_raw.replace("Z", "+00:00"))
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
        except ValueError:
            last = None
    key_raw = data.get("explore_workspace_key")
    key = str(key_raw).strip() if isinstance(key_raw, str) and key_raw.strip() else None
    fp_raw = data.get("explore_fingerprint")
    fingerprint = (
        str(fp_raw).strip() if isinstance(fp_raw, str) and fp_raw.strip() else None
    )
    return ScopeMemoryMeta(
        last_semantic_at=last,
        explore_workspace_key=key,
        explore_fingerprint=fingerprint,
        explore_fingerprint_dirty=bool(data.get("explore_fingerprint_dirty")),
    )


async def _fetch_scope_state(
    creds: AccountCredentials, scope: str | None
) -> ScopeMemoryMeta:
    data = await cloud_memory_scope_state_get(creds, scope=scope)
    return _parse_scope_state(data)


async def _fetch_scope_bodies(
    creds: AccountCredentials,
    scope: str | None,
) -> _ScopeWarm:
    """List+load one live memory scope → bodies, descriptions, (slug, description) topics.

    Topic summaries come from the entry's ``description`` (written for retrieval), so an
    older cloud that does not return the field yields an unsummarized name rather than a
    misleading first content line.
    """
    files = await cloud_memory_list(creds, scope=scope)
    wanted = _wanted_paths(files, scope=scope)
    if not wanted:
        return {}, {}, []

    async def _one(path: str) -> tuple[str, str]:
        body = await cloud_memory_load(creds, path=path, scope=scope)
        return path, body

    loaded = await asyncio.gather(*(_one(p) for p, _ in wanted))
    sk = _scope_key(scope)
    described = dict(wanted)
    bodies: dict[tuple[str, str], str] = {}
    descriptions: dict[tuple[str, str], str] = {}
    topics: list[tuple[str, str]] = []
    for path, body in loaded:
        bodies[(sk, path)] = body
        description = described.get(path, "")
        if description:
            descriptions[(sk, path)] = description
        if is_topic_path(path):
            topics.append((topic_slug(path), description))
    return bodies, descriptions, topics


async def _warm_ancestor_scopes(
    creds: AccountCredentials,
    *,
    uid: str,
    folder_id: str | None,
    ancestors: tuple[str, ...],
    memory_bodies: dict[tuple[str, str], str],
    memory_descriptions: dict[tuple[str, str], str],
    scope_states: dict[str, ScopeMemoryMeta],
) -> tuple[list[list[tuple[str, str]]], bool]:
    """Load every ancestor folder's memory layer into the snapshot being built.

    Mutates ``memory_bodies`` / ``scope_states`` in place (they are this warm's
    accumulators) and returns the per-ancestor topic pairs, outermost-first, plus whether
    anything degraded. One ancestor failing costs only that layer's inheritance.
    """
    if not ancestors:
        return [], False
    body_results, state_results = await asyncio.gather(
        asyncio.gather(
            *(_fetch_scope_bodies(creds, scope) for scope in ancestors),
            return_exceptions=True,
        ),
        asyncio.gather(
            *(_fetch_scope_state(creds, scope) for scope in ancestors),
            return_exceptions=True,
        ),
    )
    degraded = False
    topics_per_scope: list[list[tuple[str, str]]] = []
    for scope, body_res, state_res in zip(
        ancestors, body_results, state_results, strict=True
    ):
        if isinstance(body_res, BaseException):
            degraded = True
            logger.warning(
                "account.rules_memory_warm_failed",
                user_id=uid,
                folder_id=folder_id,
                ancestor_folder_id=scope,
                part="memory_ancestor",
                error=str(body_res),
            )
            topics_per_scope.append([])
        else:
            bodies, descriptions, topics = body_res
            memory_bodies.update(bodies)
            memory_descriptions.update(descriptions)
            topics_per_scope.append(topics)
        if isinstance(state_res, BaseException):
            degraded = True
            logger.warning(
                "account.rules_memory_warm_failed",
                user_id=uid,
                folder_id=folder_id,
                ancestor_folder_id=scope,
                part="scope_state_ancestor",
                error=str(state_res),
            )
            scope_states[scope] = ScopeMemoryMeta(last_semantic_at=None)
        else:
            scope_states[scope] = state_res
    return topics_per_scope, degraded


def _merge_topics(*groups: list[tuple[str, str]]) -> tuple[MemoryTopic, ...]:
    """Global → ancestors outermost-first → current folder; first summary wins a name."""
    summaries: dict[str, str] = {}
    for group in groups:
        for name, summary in group:
            summaries.setdefault(name, summary)
    return tuple(
        MemoryTopic(name=name, summary=summaries[name]) for name in sorted(summaries)
    )


async def warm_account_rules_memory(
    creds: AccountCredentials,
    *,
    user_id: str,
    folder_id: str | None,
) -> AccountPrepareSnapshot:
    """Fetch rules+memory in parallel, seed cache, return snapshot.

    ``/rules/list`` runs once (feeds always + on_demand, and — since §5.4 nesting — hands
    back the folder's ancestor chain, which only the cloud can resolve). Memory scopes
    list/load in parallel; topic summaries ride the listing's ``description``, and entries
    the user marked wrong never enter the snapshot.

    A nested folder costs one extra round-trip phase: the ancestors are only known once
    the rules call returns. Global + the current folder still ride the first phase, so a
    top-level folder (no ancestors) warms in exactly the shape it always did.
    """
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required to warm account rules/memory cache")

    rules_coro = cloud_list_user_rules(creds, folder_id=folder_id)
    global_coro = _fetch_scope_bodies(creds, None)
    global_state_coro = _fetch_scope_state(creds, None)
    if folder_id:
        folder_coro = _fetch_scope_bodies(creds, folder_id)
        folder_state_coro = _fetch_scope_state(creds, folder_id)
        rules_res, global_res, folder_res, global_state_res, folder_state_res = (
            await asyncio.gather(
                rules_coro,
                global_coro,
                folder_coro,
                global_state_coro,
                folder_state_coro,
                return_exceptions=True,
            )
        )
    else:
        rules_res, global_res, global_state_res = await asyncio.gather(
            rules_coro, global_coro, global_state_coro, return_exceptions=True
        )
        folder_res = ({}, {}, [])
        folder_state_res = ScopeMemoryMeta(last_semantic_at=None)

    degraded = False
    rules_payload: dict[str, Any] = {}
    if isinstance(rules_res, BaseException):
        degraded = True
        logger.warning(
            "account.rules_memory_warm_failed",
            user_id=uid,
            folder_id=folder_id,
            part="rules",
            error=str(rules_res),
        )
    elif isinstance(rules_res, dict):
        rules_payload = dict(rules_res)
    else:
        degraded = True

    memory_bodies: dict[tuple[str, str], str] = {}
    memory_descriptions: dict[tuple[str, str], str] = {}
    global_topics: list[tuple[str, str]] = []
    folder_topics: list[tuple[str, str]] = []

    if isinstance(global_res, BaseException):
        degraded = True
        logger.warning(
            "account.rules_memory_warm_failed",
            user_id=uid,
            folder_id=folder_id,
            part="memory_global",
            error=str(global_res),
        )
    else:
        bodies, descriptions, topics = global_res  # type: ignore[misc]
        memory_bodies.update(bodies)
        memory_descriptions.update(descriptions)
        global_topics = topics

    if folder_id:
        if isinstance(folder_res, BaseException):
            degraded = True
            logger.warning(
                "account.rules_memory_warm_failed",
                user_id=uid,
                folder_id=folder_id,
                part="memory_folder",
                error=str(folder_res),
            )
        else:
            bodies, descriptions, topics = folder_res  # type: ignore[misc]
            memory_bodies.update(bodies)
            memory_descriptions.update(descriptions)
            folder_topics = topics

    scope_states: dict[str, ScopeMemoryMeta] = {}
    if isinstance(global_state_res, BaseException):
        degraded = True
        logger.warning(
            "account.rules_memory_warm_failed",
            user_id=uid,
            folder_id=folder_id,
            part="scope_state_global",
            error=str(global_state_res),
        )
        scope_states[""] = ScopeMemoryMeta(last_semantic_at=None)
    else:
        scope_states[""] = global_state_res  # type: ignore[assignment]

    if folder_id:
        if isinstance(folder_state_res, BaseException):
            degraded = True
            logger.warning(
                "account.rules_memory_warm_failed",
                user_id=uid,
                folder_id=folder_id,
                part="scope_state_folder",
                error=str(folder_state_res),
            )
            scope_states[folder_id] = ScopeMemoryMeta(last_semantic_at=None)
        else:
            scope_states[folder_id] = folder_state_res  # type: ignore[assignment]

    folder_chain = cloud_scope_chain(rules_payload, folder_id)
    ancestor_topics, ancestor_degraded = await _warm_ancestor_scopes(
        creds,
        uid=uid,
        folder_id=folder_id,
        ancestors=ancestor_scopes(folder_chain),
        memory_bodies=memory_bodies,
        memory_descriptions=memory_descriptions,
        scope_states=scope_states,
    )

    snapshot = AccountPrepareSnapshot(
        rules_payload=rules_payload,
        memory_bodies=memory_bodies,
        memory_descriptions=memory_descriptions,
        scope_states=scope_states,
        memory_topics=_merge_topics(global_topics, *ancestor_topics, folder_topics),
        folder_chain=folder_chain,
        degraded=degraded or ancestor_degraded,
    )
    seed_account_rules_memory_cache(uid, folder_id, snapshot)
    return snapshot


def memory_body_from_snapshot(
    snapshot: AccountPrepareSnapshot,
    path: str,
    *,
    scope: str | None,
) -> str:
    """Look up one memory file body from a warm snapshot (missing → \"\")."""
    return snapshot.memory_bodies.get((_scope_key(scope), path), "")


def scope_meta_from_snapshot(
    snapshot: AccountPrepareSnapshot,
    *,
    scope: str | None,
) -> ScopeMemoryMeta:
    """Look up one scope sidecar from a warm snapshot (missing → empty meta)."""
    return snapshot.scope_states.get(
        _scope_key(scope), ScopeMemoryMeta(last_semantic_at=None)
    )


def snapshot_for_prepare_store_read(
    user_id: str,
) -> AccountPrepareSnapshot | None:
    """Snapshot for DocumentMemoryStore under ``prepare_reads_cache_only``.

    Uses the conversation ``folder_id`` bound alongside the flag (warm seed key).
    """
    if not prepare_reads_cache_only.get():
        return None
    return get_account_rules_memory_snapshot(
        user_id, prepare_account_folder_id.get()
    )


__all__ = [
    "AccountPrepareSnapshot",
    "account_rules_memory_ttl_remaining",
    "clear_account_rules_memory_cache",
    "get_account_rules_memory_snapshot",
    "memory_body_from_snapshot",
    "prepare_account_folder_id",
    "prepare_reads_cache_only",
    "scope_meta_from_snapshot",
    "seed_account_rules_memory_cache",
    "snapshot_for_prepare_store_read",
    "warm_account_rules_memory",
]
