"""Document-tree-backed :class:`MemoryStore` (Agent记忆与知识系统 §5.7「一处替换收口」).

The MVP long-term memory lived in per-user markdown files (:class:`FileMemoryStore`). The
Document subsystem lands the terminal form: memory is now ``ai_maintained=true`` ``rule`` nodes
in the single ``documents`` tree, addressed exactly as before through the ``MemoryStore`` seam —
``(user_id, path, scope)`` where ``path`` is a note's store-relative name ("画像.md",
"主题/部署.md") and ``scope`` is the layer (``None`` = global, a ``folder_id`` = that
folder). Episodic digests and per-scope consolidation sidecar live in dedicated tables
(``memory_episodes`` / ``memory_scope_states``), not this store. Because every semantic
memory consumer depends only on this Protocol, swapping the backing here changes the base
for the injectable chain — 换底, not a rewrite.

Session strategy: when constructed with a bound ``session`` (the request DI path — routes) all
ops use it, so they run in the caller's transaction / test schema. With no session (the default
``default_memory_store()`` — background consolidation, turn tools) each op opens its own from the
global factory, 照 ``memory/consolidation.py``. CAS stays content-hash (``memory_version``), so
it is store-agnostic and an in-flight editor baseline survived the file→document migration.

``delete`` soft-deletes the tree row AND unlinks the legacy on-disk source under
``data/memory/`` (injectable via ``file_store``), so the startup file→document migration cannot
resurrect a user-deleted note from a leftover markdown file.

Sidecar dual-path (R3b): when the turn bound account narrow-ticket credentials and this
store has **no** request session, list/load/save/delete/project_scopes call cloud
``/v1/account/memory/*``. Bound-session DI (cloud API handlers) always stays on the
in-process DB. Reads soft-degrade to empty + log; writes raise (no fake success).

Prepare→assemble cache_only: when ``prepare_reads_cache_only`` is bound and an
account ticket is present, list/load read only ``account_prepare_cache`` (miss →
empty); save is a no-op so explore fingerprint drift cannot sync-write semantic
notes on the TTFT path (scope-state writes go through :class:`EpisodeStore`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import DocumentRepository
from agentcore.documents.description import maybe_schedule_description_fill
from agentcore.memory.always_quota import (
    AlwaysQuotaExceededError,
    notify_always_quota_exceeded,
)
from agentcore.memory.store import (
    FileMemoryStore,
    MemoryFileMeta,
    MemoryScope,
    default_file_memory_store,
    is_topic_path,
    memory_version,
)

logger = get_logger(__name__)


def memory_editor_body(raw: str) -> str:
    """Legacy ``/users/me/memory`` editor contract: return markdown body only.

    Storage keeps frontmatter (``apply`` / ``description``); this surface must not expose
    it. Unclosed frontmatter cannot be stripped safely — return ``raw`` unchanged (no
    guess-repair). Injection callers must use :class:`DocumentMemoryStore` directly.
    """
    from agentcore.documents.frontmatter import strip_entry_frontmatter

    stripped = strip_entry_frontmatter(raw)
    return raw if stripped is None else stripped


def _classify(path: str) -> tuple[str, str]:
    """Map a memory note's store-relative path to its ``(role, apply_mode)`` in the tree.

    The always-injected core (偏好.md / 画像.md) and on-demand topics (主题/*.md) are ``rule``
    docs so they are the injectable / consultable memory (§5.2); topics are ``on_demand`` (name
    rides the directory, not ``<设定>``). Episodic digests and the meta sidecar no longer
    live in the documents tree — they have dedicated tables.
    """
    if is_topic_path(path):
        return "rule", "on_demand"
    return "rule", "always"


class DocumentMemoryStore:
    """A :class:`MemoryStore` over the ``documents`` tree (see module docstring)."""

    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        file_store: FileMemoryStore | None = None,
    ) -> None:
        self._session = session
        # Optional override of the legacy on-disk source (``data/memory/…``). Production
        # uses ``default_file_memory_store()``; tests inject a tmp-dir store.
        self._file_store = file_store

    def _legacy_file_store(self) -> FileMemoryStore:
        return self._file_store if self._file_store is not None else default_file_memory_store()

    def _account_cloud_creds(self):
        """Sidecar unbound store + account ticket → cloud HTTP; bound session stays DB."""
        if self._session is not None:
            return None
        from agentcore.account.credentials import get_account_credentials

        return get_account_credentials()

    def _prepare_cache_only_snapshot(self, user_id: str):
        """When prepare cache_only + ticket: snapshot or None (caller treats as miss)."""
        from agentcore.memory.account_prepare_cache import (
            prepare_reads_cache_only,
            snapshot_for_prepare_store_read,
        )

        if not prepare_reads_cache_only.get():
            return False, None  # not in cache_only mode
        return True, snapshot_for_prepare_store_read(user_id)

    @asynccontextmanager
    async def _repo(self) -> AsyncIterator[DocumentRepository]:
        if self._session is not None:
            yield DocumentRepository(self._session)
        else:
            async with async_session_factory() as session:
                yield DocumentRepository(session)

    async def list(self, user_id: str, scope: MemoryScope = None) -> list[MemoryFileMeta]:
        # Reads DEGRADE to empty on any failure (照 FileMemoryStore's OSError handling) — memory
        # must never break a turn's assembly (§1.6). A non-UUID user_id / transient DB error
        # simply surfaces as「no memory」rather than raising into the pipeline.
        creds = self._account_cloud_creds()
        if creds is not None:
            cache_only, snapshot = self._prepare_cache_only_snapshot(user_id)
            if cache_only:
                if snapshot is None:
                    return []
                sk = "" if scope is None else scope
                out: list[MemoryFileMeta] = []
                for (scope_key, path), body in snapshot.memory_bodies.items():
                    if scope_key != sk or not path.endswith(".md"):
                        continue
                    # Warm already dropped disputed notes, so everything cached is live.
                    out.append(
                        MemoryFileMeta(
                            path=path,
                            version=memory_version(body),
                            description=snapshot.memory_descriptions.get((sk, path), ""),
                        )
                    )
                return out
            try:
                from agentcore.account.credentials import cloud_memory_list

                files = await cloud_memory_list(creds, scope=scope)
            except Exception as e:  # noqa: BLE001 - memory read must never break a turn
                logger.warning("memory.list_failed", user_id=user_id, error=str(e))
                return []
            out = []
            for item in files:
                path = str(item.get("path") or "")
                if not path.endswith(".md"):
                    continue
                version = str(item.get("version") or memory_version(""))
                out.append(
                    MemoryFileMeta(
                        path=path,
                        version=version,
                        description=str(item.get("description") or ""),
                        disputed=bool(item.get("disputed")),
                    )
                )
            return out
        try:
            async with self._repo() as repo:
                notes = await repo.list_memory_notes(user_id, scope)
        except Exception as e:  # noqa: BLE001 - memory read must never break a turn
            logger.warning("memory.list_failed", user_id=user_id, error=str(e))
            return []
        # FileMemoryStore listed only ``*.md`` (rglob) — the meta sidecar is addressed by exact
        # path, never listed. Keep that so callers' path-prefix filters behave identically.
        # Disputed notes are listed (the editor must still show them) and flagged; skipping
        # injection is the reader's call, not this store's.
        return [
            MemoryFileMeta(
                path=n.name,
                version=memory_version(n.content),
                description=n.description or "",
                disputed=n.disputed_at is not None,
            )
            for n in notes
            if n.name.endswith(".md")
        ]

    async def load(self, user_id: str, path: str, scope: MemoryScope = None) -> str:
        creds = self._account_cloud_creds()
        if creds is not None:
            cache_only, snapshot = self._prepare_cache_only_snapshot(user_id)
            if cache_only:
                if snapshot is None:
                    return ""
                from agentcore.memory.account_prepare_cache import memory_body_from_snapshot

                return memory_body_from_snapshot(snapshot, path, scope=scope)
            try:
                from agentcore.account.credentials import cloud_memory_load

                return await cloud_memory_load(creds, path=path, scope=scope)
            except Exception as e:  # noqa: BLE001 - memory read must never break a turn
                logger.warning("memory.load_failed", user_id=user_id, error=str(e))
                return ""
        try:
            async with self._repo() as repo:
                note = await repo.get_memory_note(user_id, path, scope)
        except Exception as e:  # noqa: BLE001 - memory read must never break a turn
            logger.warning("memory.load_failed", user_id=user_id, error=str(e))
            return ""
        return note.content if note is not None else ""

    async def save(
        self,
        user_id: str,
        path: str,
        markdown: str,
        scope: MemoryScope = None,
        *,
        writer: Literal["user", "ai"] = "ai",
    ) -> None:
        creds = self._account_cloud_creds()
        if creds is not None:
            from agentcore.memory.account_prepare_cache import prepare_reads_cache_only

            if prepare_reads_cache_only.get():
                # Explore fingerprint drift must not sync-write meta on the TTFT path.
                logger.info(
                    "memory.save_skipped_prepare_cache_only",
                    user_id=user_id,
                    path=path,
                    scope=scope or "global",
                )
                return
            from agentcore.account.credentials import cloud_memory_save

            # Writes must NOT soft-succeed: propagate AccountCloudError to callers.
            await cloud_memory_save(creds, path=path, content=markdown, scope=scope)
            return
        role, apply_mode = _classify(path)
        async with self._repo() as repo:
            try:
                note = await repo.save_memory_note(
                    user_id,
                    path,
                    markdown,
                    scope,
                    role=role,
                    apply_mode=apply_mode,
                    writer=writer,
                )
            except AlwaysQuotaExceededError as exc:
                await notify_always_quota_exceeded(user_id, exc)
                raise
        if apply_mode == "on_demand":
            # An on-demand topic reaches the model as NAME + description only, so a topic
            # with no description is effectively unfindable. Always-injected cores ride the
            # prompt whole and need none.
            maybe_schedule_description_fill(
                document_id=note.id,
                user_id=user_id,
                kind=note.kind,
                description=note.description or "",
                content=note.content,
            )

    async def delete(self, user_id: str, path: str, scope: MemoryScope = None) -> None:
        creds = self._account_cloud_creds()
        if creds is not None:
            from agentcore.account.credentials import cloud_memory_delete

            await cloud_memory_delete(creds, path=path, scope=scope)
            return
        async with self._repo() as repo:
            await repo.delete_memory_note(user_id, path, scope)
        # Soft-delete alone leaves the legacy on-disk source intact; the startup
        # file→document migration would then re-INSERT the note. Unlink the source
        # (missing file = silent no-op, 照 FileMemoryStore.delete).
        await self._legacy_file_store().delete(user_id, path, scope)

    async def project_scopes(self, user_id: str) -> list[str]:
        creds = self._account_cloud_creds()
        if creds is not None:
            try:
                from agentcore.account.credentials import cloud_memory_project_scopes

                return await cloud_memory_project_scopes(creds)
            except Exception as e:  # noqa: BLE001 - degrade to no project layers
                logger.warning("memory.project_scopes_failed", user_id=user_id, error=str(e))
                return []
        try:
            async with self._repo() as repo:
                return await repo.list_memory_project_scopes(user_id)
        except Exception as e:  # noqa: BLE001 - degrade to no project layers (照 FileMemoryStore)
            logger.warning("memory.project_scopes_failed", user_id=user_id, error=str(e))
            return []


class EditorBodyMemoryStore:
    """Adapter for the legacy ``/users/me/memory`` editor routes.

    ``load`` returns body-only (CAS tags match the editor contract). ``save`` passes the
    body through; ``DocumentRepository.save_memory_note`` keeps any stored frontmatter
    block. Does **not** strip at the backing store — injection still reads raw notes.
    """

    def __init__(self, inner: DocumentMemoryStore) -> None:
        self._inner = inner

    async def list(self, user_id: str, scope: MemoryScope = None) -> list[MemoryFileMeta]:
        return await self._inner.list(user_id, scope)

    async def load(self, user_id: str, path: str, scope: MemoryScope = None) -> str:
        return memory_editor_body(await self._inner.load(user_id, path, scope=scope))

    async def save(
        self,
        user_id: str,
        path: str,
        markdown: str,
        scope: MemoryScope = None,
        *,
        writer: Literal["user", "ai"] = "user",
    ) -> None:
        await self._inner.save(user_id, path, markdown, scope=scope, writer=writer)

    async def delete(self, user_id: str, path: str, scope: MemoryScope = None) -> None:
        await self._inner.delete(user_id, path, scope=scope)

    async def project_scopes(self, user_id: str) -> list[str]:
        return await self._inner.project_scopes(user_id)
