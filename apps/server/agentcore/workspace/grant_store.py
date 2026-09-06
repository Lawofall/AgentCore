"""Conversation-scoped external mount grants (W3 readonly + organize).

Durable across API restarts and multi-worker: Postgres
``conversation_external_grants`` is the source of truth. A short in-process
cache avoids a DB round-trip on every turn build. Lifecycle follows the
**conversation** (revoke / soft-delete / hard-delete), not the process.

Grants bind ``conversation_id → alias → ExternalMount`` (desktop ``root_id``
only; absolute paths never leave the desktop). Cloud turns attach a
``WorkspaceChannel`` on ``ServerWorkspace`` so ``external/`` ops use per-op
``root_id``; ``location`` stays ``server`` (no worker_gate).
"""

from __future__ import annotations

import threading

from sqlalchemy.exc import IntegrityError

from agentcore.workspace.external_mounts import (
    ExternalMount,
    ExternalMountMode,
    alias_is_routable,
    normalize_mount_mode,
    uniquify_alias,
)

_lock = threading.Lock()
# Short cache: conversation_id → alias → ExternalMount. Invalidated on writes.
_cache: dict[str, dict[str, ExternalMount]] = {}
# Test-only memory backend (no DB). Enabled by ``clear_all_for_tests``.
_memory: dict[str, dict[str, ExternalMount]] | None = None

# Concurrent insert races hit uq_(conv,alias) / uq_(conv,root); retry with a
# fresh cache + recomputed alias (or same-root refresh) instead of a bare 500.
_MAX_GRANT_UPSERT_ATTEMPTS = 5


def _row_to_mount(row) -> ExternalMount:
    return ExternalMount(
        alias=row.alias,
        root_id=row.root_id,
        label=row.label or row.alias,
        abs_path=None,
        mode=normalize_mount_mode(row.mode),
    )


async def _load_from_db(conversation_id: str) -> dict[str, ExternalMount]:
    from agentcore.db.base import async_session_factory
    from agentcore.db.repositories.external_grants import ExternalGrantRepository

    async with async_session_factory() as session:
        rows = await ExternalGrantRepository(session).list_for_conversation(
            conversation_id
        )
        return {r.alias: _row_to_mount(r) for r in rows}


async def _ensure_cached(conversation_id: str) -> dict[str, ExternalMount]:
    with _lock:
        if _memory is not None:
            return dict(_memory.get(conversation_id, {}))
        hit = _cache.get(conversation_id)
        if hit is not None:
            return dict(hit)
    loaded = await _load_from_db(conversation_id)
    with _lock:
        _cache[conversation_id] = dict(loaded)
        return dict(loaded)


def _invalidate(conversation_id: str) -> None:
    with _lock:
        _cache.pop(conversation_id, None)


async def list_grants(conversation_id: str) -> list[ExternalMount]:
    return list((await _ensure_cached(conversation_id)).values())


async def grants_as_dict(conversation_id: str) -> dict[str, ExternalMount]:
    return await _ensure_cached(conversation_id)


async def add_grant(
    conversation_id: str,
    *,
    root_id: str,
    label: str,
    alias_hint: str | None = None,
    mode: ExternalMountMode | str = "readonly",
    device_id: str | None = None,
) -> ExternalMount:
    """Register or refresh a conversation grant. Same ``root_id`` updates label/mode.

    Upgrading readonly → organize (or the reverse) on the same root keeps the
    alias stable; the product still requires a fresh desktop confirm before
    the client calls this with the new mode.

    ``device_id`` records which install holds the folder (see
    ``fulfill/declare.py``) so the binding outlives that device's fulfill
    session. It is not part of the mount the engine sees — a mount is addressed
    by ``root_id``, and picking the machine is the hub's job.
    """
    resolved_mode = normalize_mount_mode(mode if isinstance(mode, str) else mode)

    if _memory is not None:
        with _lock:
            by_alias = _memory.setdefault(conversation_id, {})
            for existing in by_alias.values():
                if existing.root_id == root_id:
                    updated = ExternalMount(
                        alias=existing.alias,
                        root_id=root_id,
                        label=label or existing.label,
                        abs_path=None,
                        mode=resolved_mode,
                    )
                    by_alias[existing.alias] = updated
                    return updated
            taken = set(by_alias)
            alias = uniquify_alias(alias_hint or label, taken)
            if not alias_is_routable(alias):
                raise ValueError(f"external alias not routable: {alias!r}")
            mount = ExternalMount(
                alias=alias,
                root_id=root_id,
                label=label or alias,
                abs_path=None,
                mode=resolved_mode,
            )
            by_alias[alias] = mount
            return mount

    from agentcore.db.base import async_session_factory
    from agentcore.db.repositories.external_grants import ExternalGrantRepository

    last_conflict: IntegrityError | None = None
    for _ in range(_MAX_GRANT_UPSERT_ATTEMPTS):
        current = await _ensure_cached(conversation_id)
        existing_alias: str | None = None
        for m in current.values():
            if m.root_id == root_id:
                existing_alias = m.alias
                break
        if existing_alias is not None:
            alias = existing_alias
        else:
            alias = uniquify_alias(alias_hint or label, set(current))
            if not alias_is_routable(alias):
                raise ValueError(f"external alias not routable: {alias!r}")

        try:
            async with async_session_factory() as session:
                row = await ExternalGrantRepository(session).upsert(
                    conversation_id=conversation_id,
                    root_id=root_id,
                    alias=alias,
                    label=label or alias,
                    mode=resolved_mode,
                    device_id=device_id,
                )
                mount = _row_to_mount(row)
        except IntegrityError as exc:
            # Peer won on (conversation_id, alias) or (conversation_id, root_id).
            # Drop stale cache so the next attempt sees the winner and either
            # refreshes the same root or uniquifies a free alias.
            last_conflict = exc
            _invalidate(conversation_id)
            continue

        _invalidate(conversation_id)
        with _lock:
            by_alias = _cache.setdefault(conversation_id, {})
            by_alias[mount.alias] = mount
        return mount

    raise RuntimeError(
        "external grant upsert conflict after retries"
    ) from last_conflict


async def revoke_grant(
    conversation_id: str, *, alias: str | None = None, root_id: str | None = None
) -> bool:
    """Revoke one grant by alias or root_id, or all when both omitted."""
    if _memory is not None:
        with _lock:
            by_alias = _memory.get(conversation_id)
            if not by_alias:
                return False
            if alias is None and root_id is None:
                del _memory[conversation_id]
                return True
            removed = False
            if alias and alias in by_alias:
                del by_alias[alias]
                removed = True
            if root_id:
                for a, m in list(by_alias.items()):
                    if m.root_id == root_id:
                        del by_alias[a]
                        removed = True
            if not by_alias:
                _memory.pop(conversation_id, None)
            return removed

    from agentcore.db.base import async_session_factory
    from agentcore.db.repositories.external_grants import ExternalGrantRepository

    async with async_session_factory() as session:
        repo = ExternalGrantRepository(session)
        if alias is None and root_id is None:
            await repo.clear_conversation(conversation_id)
            _invalidate(conversation_id)
            return True
        n = await repo.delete_one(conversation_id, alias=alias, root_id=root_id)
    _invalidate(conversation_id)
    return n > 0


async def clear_conversation(conversation_id: str) -> None:
    if _memory is not None:
        with _lock:
            _memory.pop(conversation_id, None)
        return
    from agentcore.db.base import async_session_factory
    from agentcore.db.repositories.external_grants import ExternalGrantRepository

    async with async_session_factory() as session:
        await ExternalGrantRepository(session).clear_conversation(conversation_id)
    _invalidate(conversation_id)


def clear_all_for_tests() -> None:
    """Enable in-memory backend and wipe state (unit tests only)."""
    global _memory
    with _lock:
        _memory = {}
        _cache.clear()
