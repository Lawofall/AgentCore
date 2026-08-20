"""Long-term AI memory routes — view / edit / clear (self-only).

The user's long-term memory is the markdown body of their `ai_maintained` rule file
(Agent记忆与知识系统 §1.4 / §五), today backed by the per-user ``MemoryStore`` on disk
until the cloud file tree lands. The desktop「AI 记忆」surface reads it here and edits
it through the SAME source-agnostic markdown editor the file workbench uses, so the
contract mirrors the workspace edit contract: full text + a CAS ``version`` baseline,
and a write reports a ``conflict`` (instead of clobbering) when the offline
consolidation — or another device — changed the file underneath.

All endpoints are self-only (``AuthUser``): memory is private per-user data. Writes
hold the per-user memory lock (``memory/locks.py``) so a manual save and the offline
consolidation pass can never interleave and lose each other's change.

This legacy surface exposes **body only**: stored notes may carry frontmatter
(``apply`` / ``description``), but responses and CAS tags strip it; writes are body-only
and ``save_memory_note`` keeps the existing frontmatter block. Injection still reads the
raw store (not this adapter).

Memory injection and cross-session conversation-log access are product-always-on
(定案 A); there is no user toggle endpoint.

Two editor surfaces sit on top, both reusing the workspace markdown editor (CAS contract):

- **Legacy combined doc** (``GET/PUT /users/me/memory``): treats the GLOBAL core as ONE
  document — combines 偏好.md + 画像.md on read (``merge_global_core``) and splits on write
  (``split_global_core``), which doubles as the organic 偏好/画像 migration (an old 画像.md
  still holding preference sections splits the first time it is saved). ``enabled`` is
  always ``true`` (product gate; not a user toggle).
- **Per-leaf surface** (``GET/PUT /users/me/memory/files/{kind}``, P2): one editable leaf
  per (kind, scope) so the「文件」rail can show 偏好 / 画像 (global), a project's 画像, and
  a project's 导航 separately. ``preferences`` (偏好.md) is GLOBAL-only by invariant;
  ``profile`` (画像.md) honors an optional ``folder_id`` to address a project layer;
  ``navigation`` (导航.md) is PROJECT-only and requires ``folder_id``. ``GET …/projects``
  lists the folder_ids that have project memory so the rail only surfaces a node where
  there is one.
- **On-demand topic surface** (``GET /users/me/memory/topics`` + ``GET/PUT …/topics/{slug}``):
  lists / reads / writes the ``主题/<slug>.md`` notes the agent pulls via ``consult``,
  so the rail's 主题/ folder can finally browse·edit·delete them (Agent记忆与知识系统 §1.6).
  Same ``folder_id`` scope convention as the per-leaf surface (None = global); an empty PUT
  body deletes a note (mirrors clearing a leaf), which drops it from the 记忆主题目录.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agentcore.api.dependencies import (
    AuthUser,
    get_document_repo,
    get_memory_store,
    get_memory_update_repo,
)
from agentcore.api.schemas import MemoryUpdateItemView, MemoryUpdateKind
from agentcore.db.models.documents import MAX_DISPUTED_LINES
from agentcore.db.repositories import DocumentRepository, MemoryUpdateRepository
from agentcore.memory import (
    CORE_MEMORY_FILE,
    NAVIGATION_MEMORY_FILE,
    PREFERENCES_MEMORY_FILE,
    DocumentMemoryStore,
    is_topic_path,
    memory_version,
    merge_global_core,
    split_global_core,
    topic_path,
    topic_slug,
)
from agentcore.memory.dispute_line import (
    DisputeLineConflict,
    DisputeLineError,
    DisputeLineOk,
    dispute_memory_line,
    resolve_memory_file,
    restore_memory_line,
)
from agentcore.memory.document_store import EditorBodyMemoryStore
from agentcore.memory.locks import user_memory_lock
from agentcore.memory.move_bullet import (
    MoveBulletConflict,
    MoveBulletError,
    MoveBulletOk,
    move_memory_bullet,
)

router = APIRouter(prefix="/users/me/memory", tags=["memory"])


def _editor_memory_store(
    store: DocumentMemoryStore = Depends(get_memory_store),
) -> EditorBodyMemoryStore:
    """Body-only view of memory for this legacy editor surface (no frontmatter leak)."""
    return EditorBodyMemoryStore(store)


class MemoryKind(StrEnum):
    """Which always-injected core leaf an editor surface addresses (Agent记忆与知识系统 §1.4).

    ``preferences`` → 偏好.md (沟通/工作习惯, GLOBAL-only); ``profile`` → 画像.md
    (技术栈/关于用户的事实, global or — with a ``folder_id`` — a project layer);
    ``navigation`` → 导航.md (短入口路由表, PROJECT-only — requires ``folder_id``).
    """

    preferences = "preferences"
    profile = "profile"
    navigation = "navigation"


def _resolve_file_scope(kind: MemoryKind, folder_id: str | None) -> tuple[str, str | None]:
    """Map a logical (kind, folder_id) to a concrete (file, scope).

    ``preferences`` is GLOBAL-only by invariant (§1.4 — preferences are universal, never
    copied into a project), so a ``folder_id`` is ignored. ``profile`` is global when
    ``folder_id`` is None, else that project's 画像.md. ``navigation`` is PROJECT-only
    (§1.4 — 导航.md exists only under a project); missing ``folder_id`` → 422.
    """
    if kind is MemoryKind.preferences:
        return PREFERENCES_MEMORY_FILE, None
    if kind is MemoryKind.navigation:
        if not folder_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "navigation_requires_folder",
                    "message": "导航.md 仅项目作用域，须提供 folder_id",
                },
            )
        return NAVIGATION_MEMORY_FILE, folder_id
    return CORE_MEMORY_FILE, folder_id


class MemoryResponse(BaseModel):
    """The user's memory document + always-on ``enabled`` (editor load payload)."""

    content: str
    # Content-addressed CAS tag (memory/store.py ``memory_version``); the client sends
    # it back as the write baseline so a stale overwrite is caught, not silently lost.
    version: str
    # Product-always-on (定案 A); kept on the payload for client compatibility.
    enabled: bool


class MemoryWriteRequest(BaseModel):
    content: str
    # The version the edit was based on. ``None`` writes unconditionally (used by
    # "清空记忆" / "仍然覆盖"); a non-null value that no longer matches → 200 conflict.
    baseline: str | None = None


class MemoryWriteResult(BaseModel):
    ok: bool
    version: str
    conflict: bool = False


class MemoryFileResponse(BaseModel):
    """One memory leaf's body + its CAS tag (a single editor leaf's load payload)."""

    content: str
    version: str


class MemoryProjectsResponse(BaseModel):
    """folder_ids whose PROJECT memory layer is non-empty (the rail shows a node each)."""

    folders: list[str]


class MemoryTopicsResponse(BaseModel):
    """On-demand TOPIC note slugs in one scope (the rail's 主题/ folder listing).

    Names only (``主题/<slug>.md`` → ``slug``), sorted; the body is pulled per-note via
    ``GET …/topics/{slug}`` when the user opens one (渐进披露, mirrors ``consult``).
    """

    topics: list[str]


class MemoryUpdateFeedItem(BaseModel):
    """One memory-write notice in the cross-conversation「记忆动态」feed.

    Same shape as the conversation-tail card (``kind`` / ``summary`` / ``items``), plus
    ``conversation_id`` so the feed can link back to the source conversation. Projected
    from a ``memory_updates`` row via ``from_attributes``.
    """

    id: str
    conversation_id: str
    kind: MemoryUpdateKind = "semantic"
    summary: str | None = None
    items: list[MemoryUpdateItemView] = Field(default_factory=list)
    created_at: datetime

    model_config = {"from_attributes": True}


class MemoryUpdatesFeedResponse(BaseModel):
    """The user's recent memory updates across ALL conversations, NEWEST-first.

    Backs the「AI 记忆」editor's「最近更新」view (记忆编辑器的跨对话动态视图): the write side
    of memory is per-user long-term data, so its natural home is one chronological stream —
    a question the per-conversation tail card cannot answer.
    """

    updates: list[MemoryUpdateFeedItem]


class MemoryMoveBulletRequest(BaseModel):
    """Move one bullet between GLOBAL and the given project's layer (位置即作用域纠错).

    ``direction`` ``to_project`` = remove from global + add under the same section in
    ``folder_id``; ``to_global`` is the inverse. Optional CAS baselines mirror the
    per-leaf PUT contract (``None`` = unconditional under the memory lock).
    """

    content: str = Field(..., min_length=1, description="Bullet text to move")
    section: str = Field("", description="## section name (required for core leaves)")
    folder_id: str = Field(..., min_length=1, description="Current project folder id")
    direction: Literal["to_project", "to_global"]
    kind: Literal["preferences", "profile", "topic"] = "profile"
    topic_slug: str | None = None
    source_baseline: str | None = None
    target_baseline: str | None = None


class MemoryMoveBulletResult(BaseModel):
    ok: bool
    conflict: bool = False
    source_version: str = ""
    target_version: str = ""
    message: str | None = None


class MemoryDisputeLineRequest(BaseModel):
    """Reject ONE bullet (「这条不对」at sentence granularity, 纠错通道·行级).

    The line leaves the body and is kept in the entry's disputed record, so the rest of the
    entry keeps working — unlike the entry-level ``disputed`` flag on the documents API,
    which silences everything in the file. ``folder_id`` omitted = the global layer.
    """

    content: str = Field(..., min_length=1, description="Bullet text to reject")
    section: str = Field("", description="## section name the bullet sits under")
    folder_id: str | None = None
    kind: Literal["preferences", "profile", "topic"] = "profile"
    topic_slug: str | None = None
    baseline: str | None = None


class MemoryRestoreLineRequest(BaseModel):
    """Undo one line-level dispute by the record's stable ``id``.

    Never by position: rejecting several lines and undoing an earlier one shifts the rest,
    so an index would put back a different line than the one the user pointed at.
    """

    id: str = Field(..., min_length=1)
    folder_id: str | None = None
    kind: Literal["preferences", "profile", "topic"] = "profile"
    topic_slug: str | None = None


class MemoryDisputeLineResult(BaseModel):
    ok: bool
    conflict: bool = False
    version: str = ""
    # Id of the new record row, so the client can offer an immediate undo. "" = none.
    line_id: str = ""


class MemoryDisputedLineView(BaseModel):
    """One rejected bullet, addressable for undo by ``(kind, topic_slug, id)``."""

    kind: Literal["preferences", "profile", "topic"]
    topic_slug: str | None = None
    folder_id: str | None = None
    id: str
    section: str = ""
    text: str
    disputed_at: str = ""


class MemoryDisputedLinesResponse(BaseModel):
    lines: list[MemoryDisputedLineView]
    # Per-entry cap on kept records — the surface states its own bound rather than letting
    # the oldest silently age out unannounced.
    max_per_entry: int = MAX_DISPUTED_LINES


class MemoryClearDisputedLinesResult(BaseModel):
    """How many entries had their rejected-line records dropped."""

    cleared_entries: int


@router.get("", response_model=MemoryResponse)
async def get_my_memory(
    user: AuthUser, store: EditorBodyMemoryStore = Depends(_editor_memory_store)
) -> MemoryResponse:
    """Load the signed-in user's long-term memory (``enabled`` is always true)."""
    content = merge_global_core(
        await store.load(user.user_id, PREFERENCES_MEMORY_FILE),
        await store.load(user.user_id, CORE_MEMORY_FILE),
    )
    return MemoryResponse(
        content=content, version=memory_version(content), enabled=True
    )


@router.put("", response_model=MemoryWriteResult)
async def put_my_memory(
    body: MemoryWriteRequest,
    user: AuthUser,
    store: EditorBodyMemoryStore = Depends(_editor_memory_store),
) -> MemoryWriteResult:
    """Write the user's long-term memory back (full-document edit, CAS-guarded).

    Holds the per-user memory lock so the read-compare-write is atomic against the offline
    consolidation pass. A ``baseline`` that no longer matches the current (merged) version
    returns ``ok=False, conflict=True`` with the live version (never a blind overwrite); the
    client then reloads or forces the write with ``baseline=None``. The edited document is
    split back into 偏好.md + 画像.md; the returned version is that of the re-merged result so
    it matches what the next GET serves (split→save→merge normalizes the markdown).
    """
    async with user_memory_lock(user.user_id):
        current = merge_global_core(
            await store.load(user.user_id, PREFERENCES_MEMORY_FILE),
            await store.load(user.user_id, CORE_MEMORY_FILE),
        )
        current_version = memory_version(current)
        if body.baseline is not None and body.baseline != current_version:
            return MemoryWriteResult(ok=False, version=current_version, conflict=True)
        files = split_global_core(body.content)
        for file, markdown in files.items():
            if markdown:
                await store.save(user.user_id, file, markdown)
            else:
                # An empty file means that core file has no sections (e.g. all preferences
                # were removed, or "清空记忆"): drop it so it stops being injected.
                await store.delete(user.user_id, file)
        new_content = merge_global_core(
            files[PREFERENCES_MEMORY_FILE], files[CORE_MEMORY_FILE]
        )
    return MemoryWriteResult(ok=True, version=memory_version(new_content))


@router.get("/projects", response_model=MemoryProjectsResponse)
async def list_my_memory_projects(
    user: AuthUser, store: EditorBodyMemoryStore = Depends(_editor_memory_store)
) -> MemoryProjectsResponse:
    """List folder_ids that have project-scoped memory (so the「文件」rail can surface them).

    Declared before ``/files/{kind}`` so the static segment wins the route match.
    """
    return MemoryProjectsResponse(folders=await store.project_scopes(user.user_id))


@router.post("/move-bullet", response_model=MemoryMoveBulletResult)
async def move_my_memory_bullet(
    body: MemoryMoveBulletRequest,
    user: AuthUser,
    store: EditorBodyMemoryStore = Depends(_editor_memory_store),
) -> MemoryMoveBulletResult:
    """Move one memory bullet between global and the current project (P2-b 搬层纠错).

    Declared before ``/files/{kind}`` so the static segment wins the route match.
    Holds the per-user memory lock; illegal sections (偏好 / 纠正记录 → project,
    项目约束 → global) return 422 with a clear message.
    """
    async with user_memory_lock(user.user_id):
        result = await move_memory_bullet(
            store,
            user_id=user.user_id,
            content=body.content,
            section=body.section,
            folder_id=body.folder_id,
            direction=body.direction,
            kind=body.kind,
            topic_slug=body.topic_slug,
            source_baseline=body.source_baseline,
            target_baseline=body.target_baseline,
        )
    if isinstance(result, MoveBulletError):
        raise HTTPException(
            status_code=422,
            detail={"code": "memory_move_rejected", "message": result.message},
        )
    if isinstance(result, MoveBulletConflict):
        return MemoryMoveBulletResult(
            ok=False,
            conflict=True,
            source_version=result.source_version,
            target_version=result.target_version,
        )
    assert isinstance(result, MoveBulletOk)
    return MemoryMoveBulletResult(
        ok=True,
        source_version=result.source_version,
        target_version=result.target_version,
    )


def _dispute_target(
    body: MemoryDisputeLineRequest | MemoryRestoreLineRequest,
) -> str:
    resolved = resolve_memory_file(kind=body.kind, topic_slug=body.topic_slug)
    if isinstance(resolved, DisputeLineError):
        raise HTTPException(
            status_code=422,
            detail={"code": "memory_dispute_rejected", "message": resolved.message},
        )
    return resolved


@router.post("/dispute-line", response_model=MemoryDisputeLineResult)
async def dispute_my_memory_line(
    body: MemoryDisputeLineRequest,
    user: AuthUser,
    store: EditorBodyMemoryStore = Depends(_editor_memory_store),
    repo: DocumentRepository = Depends(get_document_repo),
) -> MemoryDisputeLineResult:
    """Reject one bullet the user was shown — the line moves out of the entry (纠错通道·行级).

    Declared before ``/files/{kind}`` so the static segment wins the route match. Holds the
    per-user memory lock. Only an explicit user click reaches here; nothing infers a
    rejection from conversation text.
    """
    async with user_memory_lock(user.user_id):
        result = await dispute_memory_line(
            store,
            repo,
            user_id=user.user_id,
            content=body.content,
            section=body.section,
            scope=body.folder_id,
            kind=body.kind,
            topic_slug=body.topic_slug,
            baseline=body.baseline,
        )
    if isinstance(result, DisputeLineError):
        raise HTTPException(
            status_code=422,
            detail={"code": "memory_dispute_rejected", "message": result.message},
        )
    if isinstance(result, DisputeLineConflict):
        return MemoryDisputeLineResult(ok=False, conflict=True, version=result.version)
    assert isinstance(result, DisputeLineOk)
    return MemoryDisputeLineResult(
        ok=True, version=result.version, line_id=result.line_id
    )


@router.post("/restore-line", response_model=MemoryDisputeLineResult)
async def restore_my_memory_line(
    body: MemoryRestoreLineRequest,
    user: AuthUser,
    store: EditorBodyMemoryStore = Depends(_editor_memory_store),
    repo: DocumentRepository = Depends(get_document_repo),
) -> MemoryDisputeLineResult:
    """Undo one line-level dispute — the bullet goes back into the entry.

    An ``id`` that is no longer on file is a 422, never a best-effort restore of some other
    record: putting back a line the user did not name would be worse than doing nothing.
    """
    file = _dispute_target(body)
    async with user_memory_lock(user.user_id):
        result = await restore_memory_line(
            store,
            repo,
            user_id=user.user_id,
            file=file,
            line_id=body.id,
            scope=body.folder_id,
        )
    if isinstance(result, DisputeLineError):
        raise HTTPException(
            status_code=422,
            detail={"code": "memory_dispute_rejected", "message": result.message},
        )
    assert isinstance(result, DisputeLineOk)
    return MemoryDisputeLineResult(ok=True, version=result.version)


@router.get("/disputed-lines", response_model=MemoryDisputedLinesResponse)
async def list_my_disputed_lines(
    user: AuthUser,
    folder_id: str | None = None,
    repo: DocumentRepository = Depends(get_document_repo),
) -> MemoryDisputedLinesResponse:
    """Bullets the user rejected, so the editor can show and undo them.

    Rejected lines are gone from the body — without this surface a mistaken click would be
    unrecoverable, which is exactly the trap the entry-level channel avoided by never
    deleting. ``folder_id`` omitted covers EVERY layer (global + each project with memory)
    rather than the global one alone: a line rejected in a project layer must be findable
    from the one place the editor lists them, or「可撤销」is only true for a few seconds.
    Declared before ``/files/{kind}`` so the static segment wins the route match.
    """
    scopes: list[str | None] = (
        [folder_id]
        if folder_id
        else [None, *await repo.list_memory_project_scopes(user.user_id)]
    )
    lines: list[MemoryDisputedLineView] = []
    for scope in scopes:
        for note in await repo.list_memory_notes(user.user_id, scope):
            for entry in note.disputed_lines:
                slug = topic_slug(note.name) if is_topic_path(note.name) else None
                kind: Literal["preferences", "profile", "topic"] = (
                    "topic"
                    if slug is not None
                    else (
                        "preferences"
                        if note.name == PREFERENCES_MEMORY_FILE
                        else "profile"
                    )
                )
                lines.append(
                    MemoryDisputedLineView(
                        kind=kind,
                        topic_slug=slug,
                        folder_id=scope,
                        id=entry["id"],
                        section=entry["section"],
                        text=entry["text"],
                        disputed_at=entry["disputed_at"],
                    )
                )
    return MemoryDisputedLinesResponse(lines=lines)


@router.delete("/disputed-lines", response_model=MemoryClearDisputedLinesResult)
async def clear_my_disputed_lines(
    user: AuthUser,
    repo: DocumentRepository = Depends(get_document_repo),
) -> MemoryClearDisputedLinesResult:
    """Empty the rejected-line list (「已移走的记忆」的清空入口).

    The lines stay rejected — bodies are not touched. What goes is the ability to put them
    back, which is why this is an explicit, confirmed action rather than something the cap
    does for the user. Declared before ``/files/{kind}`` so the static segment wins.
    """
    return MemoryClearDisputedLinesResult(
        cleared_entries=await repo.clear_memory_disputed_lines(user.user_id)
    )


@router.get("/updates", response_model=MemoryUpdatesFeedResponse)
async def list_my_memory_updates(
    user: AuthUser,
    mem_update_repo: MemoryUpdateRepository = Depends(get_memory_update_repo),
    limit: int = 50,
) -> MemoryUpdatesFeedResponse:
    """The signed-in user's recent memory updates across ALL conversations (记忆动态 feed).

    Newest-first; the offline consolidation pass appends a row whenever it changed a memory
    file, so this is the「AI 最近学了什么」stream that powers the editor's「最近更新」view.
    Declared before ``/files/{kind}`` / ``/topics/{slug}`` so the static segment wins the
    route match.
    """
    rows = await mem_update_repo.list_for_user(user.user_id, limit=limit)
    return MemoryUpdatesFeedResponse(
        updates=[MemoryUpdateFeedItem.model_validate(row) for row in rows]
    )


@router.get("/topics", response_model=MemoryTopicsResponse)
async def list_my_memory_topics(
    user: AuthUser,
    folder_id: str | None = None,
    store: EditorBodyMemoryStore = Depends(_editor_memory_store),
) -> MemoryTopicsResponse:
    """List on-demand TOPIC note slugs in one scope (the rail's 主题/ folder listing).

    ``folder_id`` None = the GLOBAL 主题/ folder; a folder_id = that project's. Same scope
    convention as ``/files/{kind}`` (no separate ``scope`` enum). Declared before
    ``/topics/{slug}`` / ``/files/{kind}`` so the static segment wins the route match.
    """
    metas = await store.list(user.user_id, scope=folder_id)
    return MemoryTopicsResponse(
        topics=sorted(topic_slug(m.path) for m in metas if is_topic_path(m.path))
    )


@router.get("/topics/{slug}", response_model=MemoryFileResponse)
async def get_my_memory_topic(
    slug: str,
    user: AuthUser,
    folder_id: str | None = None,
    store: EditorBodyMemoryStore = Depends(_editor_memory_store),
) -> MemoryFileResponse:
    """Load ONE on-demand TOPIC note's body — global (``folder_id`` None) or a project's."""
    content = await store.load(user.user_id, topic_path(slug), scope=folder_id)
    return MemoryFileResponse(content=content, version=memory_version(content))


@router.put("/topics/{slug}", response_model=MemoryWriteResult)
async def put_my_memory_topic(
    slug: str,
    body: MemoryWriteRequest,
    user: AuthUser,
    folder_id: str | None = None,
    store: EditorBodyMemoryStore = Depends(_editor_memory_store),
) -> MemoryWriteResult:
    """Write ONE TOPIC note back (CAS-guarded; an empty body deletes — mirrors ``/files/{kind}``).

    Holds the per-user memory lock so the read-compare-write is atomic against the offline
    consolidation pass. A ``baseline`` that no longer matches the note's current version
    returns ``ok=False, conflict=True`` (never a blind overwrite). Clearing a note (empty
    content) deletes the underlying file so it leaves the 记忆主题目录 (and stops being
    consult-able).
    """
    async with user_memory_lock(user.user_id):
        current = await store.load(user.user_id, topic_path(slug), scope=folder_id)
        current_version = memory_version(current)
        if body.baseline is not None and body.baseline != current_version:
            return MemoryWriteResult(ok=False, version=current_version, conflict=True)
        if body.content:
            await store.save(user.user_id, topic_path(slug), body.content, scope=folder_id)
        else:
            await store.delete(user.user_id, topic_path(slug), scope=folder_id)
    return MemoryWriteResult(ok=True, version=memory_version(body.content))


@router.get("/files/{kind}", response_model=MemoryFileResponse)
async def get_my_memory_file(
    kind: MemoryKind,
    user: AuthUser,
    folder_id: str | None = None,
    store: EditorBodyMemoryStore = Depends(_editor_memory_store),
) -> MemoryFileResponse:
    """Load ONE memory leaf — 偏好/画像 (global), a project's 画像, or a project's 导航."""
    file, scope = _resolve_file_scope(kind, folder_id)
    content = await store.load(user.user_id, file, scope=scope)
    return MemoryFileResponse(content=content, version=memory_version(content))


@router.put("/files/{kind}", response_model=MemoryWriteResult)
async def put_my_memory_file(
    kind: MemoryKind,
    body: MemoryWriteRequest,
    user: AuthUser,
    folder_id: str | None = None,
    store: EditorBodyMemoryStore = Depends(_editor_memory_store),
) -> MemoryWriteResult:
    """Write ONE memory leaf back (CAS-guarded; an empty body drops the file).

    Holds the per-user memory lock so the read-compare-write is atomic against the offline
    consolidation pass. A ``baseline`` that no longer matches the leaf's current version
    returns ``ok=False, conflict=True`` (never a blind overwrite). Clearing a leaf (empty
    content) deletes the underlying file so it stops being injected.
    """
    file, scope = _resolve_file_scope(kind, folder_id)
    async with user_memory_lock(user.user_id):
        current = await store.load(user.user_id, file, scope=scope)
        current_version = memory_version(current)
        if body.baseline is not None and body.baseline != current_version:
            return MemoryWriteResult(ok=False, version=current_version, conflict=True)
        if body.content:
            await store.save(user.user_id, file, body.content, scope=scope)
        else:
            await store.delete(user.user_id, file, scope=scope)
    return MemoryWriteResult(ok=True, version=memory_version(body.content))
