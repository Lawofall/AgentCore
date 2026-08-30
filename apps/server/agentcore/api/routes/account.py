"""Account narrow-ticket mint + engine surface for sidecar (R3a/R3b).

Desktop convention (parallel desktop inject):
- Mint: ``POST /v1/account/token`` with cookie/Bearer **access** session
  → ``{token, expires_in_sec}`` (``type=account`` JWT).
- Sidecar inject: ``accountAuth: {baseUrl, apiKey}`` where
  ``baseUrl`` = ``{apiOrigin}/v1/account`` and ``apiKey`` = minted token.
- Cloud calls (account ticket **or** access):
  ``POST {baseUrl}/conversations/search|read|chat-context``,
  ``POST {baseUrl}/rules/list|remember`` (list = always + on_demand bodies for
  规则目录 / ``consult``),
  ``POST {baseUrl}/memory/{list,load,save,delete,project-scopes}``.
- Does **not** open UI conversation / documents / memory-editor CRUD to the
  narrow ticket — engine-minimal surface only.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AccountApiUser, AuthUser, get_db
from agentcore.config import settings
from agentcore.conversation.log_export import (
    MAX_CHUNK_CHARS,
    chunk_transcript,
    render_conversation_log,
    search_snippet_from_messages,
)
from agentcore.core.errors import NotFoundError
from agentcore.db.models import Document
from agentcore.db.repositories import (
    ConversationRepository,
    DocumentRepository,
    FolderRepository,
    MessageRepository,
    TurnJournalRepository,
)
from agentcore.memory.always_quota import AlwaysQuotaExceededError
from agentcore.memory.document_store import DocumentMemoryStore
from agentcore.memory.rules_injection import mutate_user_rule
from agentcore.security.tokens import create_account_token

router = APIRouter(prefix="/account", tags=["account"])

_SEARCH_HARD_CAP = 30
_DEFAULT_LIMIT = 10
_MAX_LOOKBACK_HOURS = 168


class AccountTokenResponse(BaseModel):
    """Freshly minted account narrow token + lifetime (sidecar log-tool auth).

    Desktop: ``baseUrl`` for ``accountAuth`` is ``{apiOrigin}/v1/account``;
    ``apiKey`` is ``token``. Mint path: ``POST /v1/account/token``.
    """

    token: str
    expires_in_sec: int


@router.post("/token", response_model=AccountTokenResponse)
async def mint_account_token(user: AuthUser) -> AccountTokenResponse:
    """Exchange the caller's cookie/Bearer access session for an account narrow ticket."""
    return AccountTokenResponse(
        token=create_account_token(user.user_id),
        expires_in_sec=settings.account_token_expire_minutes * 60,
    )


class ConversationSearchRequest(BaseModel):
    """Aligned with Worker ``search_conversations`` (resolved folder filters)."""

    query: str = ""
    folder_id: str | None = None
    include_archived: bool = False
    global_chats_only: bool = False
    exclude_conversation_id: str | None = None
    limit: int = Field(default=_DEFAULT_LIMIT, ge=1, le=_SEARCH_HARD_CAP)
    updated_within_hours: int | None = Field(default=None, ge=1, le=_MAX_LOOKBACK_HOURS)
    # When true, treat ``folder_id`` as an explicit owner-check target (tool's
    # explicit folder_id arg). Missing/unowned → ``folder_miss`` soft empty.
    check_folder_owned: bool = False


class ConversationSearchRow(BaseModel):
    conversation_id: str
    title: str
    folder_id: str | None = None
    folder_name: str | None = None
    updated_at: str | None = None
    message_count: int = 0
    archived: bool = False
    snippet: str | None = None


class ConversationSearchResponse(BaseModel):
    rows: list[ConversationSearchRow]
    folder_miss: bool = False


@router.post("/conversations/search", response_model=ConversationSearchResponse)
async def search_account_conversations(
    body: ConversationSearchRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> ConversationSearchResponse:
    """Owner-scoped conversation directory search (account ticket or access)."""
    if body.check_folder_owned and body.folder_id:
        folder = await FolderRepository(session).get_by_id(
            body.folder_id, user_id=user.user_id
        )
        if folder is None:
            return ConversationSearchResponse(rows=[], folder_miss=True)

    updated_after: datetime | None = None
    if body.updated_within_hours is not None:
        updated_after = datetime.now(UTC) - timedelta(hours=body.updated_within_hours)

    rows = await ConversationRepository(session).search_with_projections(
        user.user_id,
        (body.query or "").strip(),
        limit=body.limit,
        folder_id=body.folder_id,
        include_archived=body.include_archived,
        global_chats_only=body.global_chats_only,
        exclude_conversation_id=body.exclude_conversation_id or None,
        updated_after=updated_after,
    )
    msg_repo = MessageRepository(session)
    out_rows: list[ConversationSearchRow] = []
    for row in rows:
        snippet: str | None = None
        try:
            msgs = await msg_repo.list_all_for_conversation(row["conversation_id"])
            snippet = search_snippet_from_messages(msgs, (body.query or "").strip()) or None
        except Exception:  # noqa: BLE001 — snippet is best-effort
            snippet = None
        out_rows.append(
            ConversationSearchRow(
                conversation_id=row["conversation_id"],
                title=row["title"],
                folder_id=row.get("folder_id"),
                folder_name=row.get("folder_name"),
                updated_at=row.get("updated_at"),
                message_count=int(row.get("message_count") or 0),
                archived=bool(row.get("archived")),
                snippet=snippet,
            )
        )
    return ConversationSearchResponse(rows=out_rows, folder_miss=False)


class ConversationReadRequest(BaseModel):
    conversation_id: str
    cursor: str | None = None
    max_chars: int | None = Field(default=None, ge=1, le=MAX_CHUNK_CHARS)


class ConversationReadResponse(BaseModel):
    status: Literal["ok", "soft_miss"]
    title: str = ""
    conversation_id: str = ""
    transcript: str = ""
    truncated: bool = False
    next_cursor: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    message_count: int = 0
    char_offset: int = 0
    total_chars: int = 0


class ChatContextItem(BaseModel):
    """One ``load_chat_context`` row (role/content; ledger is engine-only)."""

    role: Literal["user", "assistant"]
    content: str
    evidence_ledger: list[Any] | None = None


class ChatContextRequest(BaseModel):
    conversation_id: str


class ChatContextResponse(BaseModel):
    history: list[ChatContextItem]


def _chat_context_items(rows: list[dict[str, Any]]) -> list[ChatContextItem]:
    items: list[ChatContextItem] = []
    for row in rows:
        role = row.get("role")
        content = row.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        ledger = row.get("evidence_ledger")
        items.append(
            ChatContextItem(
                role=role,
                content=content,
                evidence_ledger=ledger if isinstance(ledger, list) and ledger else None,
            )
        )
    return items


@router.post("/conversations/chat-context", response_model=ChatContextResponse)
async def account_chat_context(
    body: ChatContextRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> ChatContextResponse:
    """Owner-scoped CEO window (same ``load_chat_context`` as a cloud send).

    Engine-minimal: sidecar start/harvest and desktop fallback when the account
    ticket is missing. Not UI message CRUD — does not expose compaction text
    as its own field (it rides inside the assembled assistant summary block).
    """
    from agentcore.conversation.chat_context import assemble_owned_chat_context

    cid = (body.conversation_id or "").strip()
    if not cid:
        raise NotFoundError("对话不存在")
    history = await assemble_owned_chat_context(
        session, cid, user_id=user.user_id
    )
    return ChatContextResponse(history=_chat_context_items(history))


@router.post("/conversations/read", response_model=ConversationReadResponse)
async def read_account_conversation(
    body: ConversationReadRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> ConversationReadResponse:
    """Owner-scoped deep transcript read (account ticket or access). Soft miss on 404."""
    cid = (body.conversation_id or "").strip()
    if not cid:
        return ConversationReadResponse(status="soft_miss", conversation_id="")

    conv = await ConversationRepository(session).get_by_id(cid, user_id=user.user_id)
    if conv is None or conv.mode == "handoff":
        return ConversationReadResponse(status="soft_miss", conversation_id=cid)

    messages = list(await MessageRepository(session).list_all_for_conversation(cid))
    assistant_ids = [m.id for m in messages if m.role == "assistant"]
    journal_map = await TurnJournalRepository(session).load_map(assistant_ids)
    full = render_conversation_log(conv, messages, journal_map)
    cursor_s = (body.cursor or "").strip() or None
    chunk = chunk_transcript(
        full,
        conversation=conv,
        messages=messages,
        cursor=cursor_s,
        max_chars=body.max_chars,
    )
    return ConversationReadResponse(
        status="ok",
        title=chunk.title,
        conversation_id=chunk.conversation_id,
        transcript=chunk.transcript,
        truncated=chunk.truncated,
        next_cursor=chunk.next_cursor,
        started_at=chunk.started_at,
        ended_at=chunk.ended_at,
        message_count=chunk.message_count,
        char_offset=chunk.char_offset,
        total_chars=chunk.total_chars,
    )


# --- Engine-minimal rules / memory (R3b; not the UI documents/memory editors) ---


class AccountRulesListRequest(BaseModel):
    """Optional project layer; global rules always included."""

    folder_id: str | None = None


class AccountRuleDoc(BaseModel):
    name: str
    content: str
    # Retrieval summary for the 规则目录; on_demand entries are picked by this, not by body.
    description: str = ""


class AccountRulesListResponse(BaseModel):
    """Always rules for ``<设定>`` plus on_demand bodies for 规则目录 / ``consult``.

    ``ancestor_*`` carry the enclosing folders' layers, outermost-first, and
    ``folder_chain`` is that same chain by id with the current folder last: the engine may
    be a desktop sidecar with no folders table, so the cloud is the only place that can
    resolve「谁在谁里面」(双模式工作区 §5.4 沿树继承).
    """

    global_rules: list[AccountRuleDoc]
    project_rules: list[AccountRuleDoc]
    ancestor_rules: list[AccountRuleDoc] = Field(default_factory=list)
    global_on_demand_rules: list[AccountRuleDoc] = Field(default_factory=list)
    project_on_demand_rules: list[AccountRuleDoc] = Field(default_factory=list)
    ancestor_on_demand_rules: list[AccountRuleDoc] = Field(default_factory=list)
    folder_chain: list[str] = Field(default_factory=list)


def _rule_docs(docs: Sequence[Document]) -> list[AccountRuleDoc]:
    return [
        AccountRuleDoc(
            name=d.name, content=d.content or "", description=d.description or ""
        )
        for d in docs
    ]


@router.post("/rules/list", response_model=AccountRulesListResponse)
async def list_account_user_rules(
    body: AccountRulesListRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> AccountRulesListResponse:
    """User rules for turn assembly: always → ``<设定>``; on_demand → catalog + consult."""
    repo = DocumentRepository(session)
    folder_chain: list[str] = []
    if body.folder_id:
        folder_chain = await FolderRepository(session).list_ancestor_chain_ids(
            body.folder_id, user_id=user.user_id
        )
        if body.folder_id not in folder_chain:
            folder_chain = [body.folder_id]
    ancestors = folder_chain[:-1]

    ancestor_docs: list[Document] = []
    ancestor_on_demand: list[Document] = []
    for scope in ancestors:
        ancestor_docs += await repo.list_injectable_rules(
            user.user_id, scope, ai_maintained=False
        )
        ancestor_on_demand += await repo.list_on_demand_user_rules(user.user_id, scope)

    project_docs: Sequence[Document] = []
    project_on_demand: Sequence[Document] = []
    if body.folder_id:
        project_docs = await repo.list_injectable_rules(
            user.user_id, body.folder_id, ai_maintained=False
        )
        project_on_demand = await repo.list_on_demand_user_rules(
            user.user_id, body.folder_id
        )
    return AccountRulesListResponse(
        global_rules=_rule_docs(
            await repo.list_injectable_rules(user.user_id, None, ai_maintained=False)
        ),
        project_rules=_rule_docs(project_docs),
        ancestor_rules=_rule_docs(ancestor_docs),
        global_on_demand_rules=_rule_docs(
            await repo.list_on_demand_user_rules(user.user_id, None)
        ),
        project_on_demand_rules=_rule_docs(project_on_demand),
        ancestor_on_demand_rules=_rule_docs(ancestor_on_demand),
        folder_chain=folder_chain,
    )


class AccountRememberRequest(BaseModel):
    content: str | None = None
    folder_id: str | None = None
    action: Literal["add", "replace", "forget", "list"] = "add"
    replaces: str | None = None


class AccountRememberResponse(BaseModel):
    changed: bool
    action: str
    message: str
    rules_markdown: str | None = None


@router.post("/rules/remember", response_model=AccountRememberResponse)
async def remember_account_user_rule(
    body: AccountRememberRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> AccountRememberResponse:
    """Mutate the scope's user-rule doc (``add`` / ``replace`` / ``forget`` / ``list``)."""
    try:
        result = await mutate_user_rule(
            DocumentRepository(session),
            user.user_id,
            folder_id=body.folder_id,
            action=body.action,
            content=body.content,
            replaces=body.replaces,
        )
    except AlwaysQuotaExceededError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ALWAYS_QUOTA_EXCEEDED",
                "message": exc.message,
            },
        ) from exc
    return AccountRememberResponse(
        changed=result.changed,
        action=result.action,
        message=result.message,
        rules_markdown=result.rules_markdown,
    )


class AccountMemoryScopeRequest(BaseModel):
    scope: str | None = None


class AccountMemoryFileMeta(BaseModel):
    path: str
    version: str
    # Retrieval summary shown in the 按需目录 ("" = the entry has none yet).
    description: str = ""
    # User marked this note wrong (纠错通道) — the sidecar must not inject or consult it.
    disputed: bool = False


class AccountMemoryListResponse(BaseModel):
    files: list[AccountMemoryFileMeta]


@router.post("/memory/list", response_model=AccountMemoryListResponse)
async def list_account_memory(
    body: AccountMemoryScopeRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> AccountMemoryListResponse:
    """List memory notes under one scope (global when ``scope`` is null).

    Carries each note's retrieval ``description`` and its ``disputed`` mark so a sidecar
    warm builds the same directory — and skips the same user-disputed entries — as an
    in-process turn.
    """
    store = DocumentMemoryStore(session)
    metas = await store.list(user.user_id, body.scope)
    return AccountMemoryListResponse(
        files=[
            AccountMemoryFileMeta(
                path=m.path,
                version=m.version,
                description=m.description,
                disputed=m.disputed,
            )
            for m in metas
        ]
    )


class AccountMemoryLoadRequest(BaseModel):
    path: str
    scope: str | None = None


class AccountMemoryLoadResponse(BaseModel):
    content: str


@router.post("/memory/load", response_model=AccountMemoryLoadResponse)
async def load_account_memory(
    body: AccountMemoryLoadRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> AccountMemoryLoadResponse:
    """Load one memory note body; missing path → empty string (soft)."""
    path = (body.path or "").strip()
    if not path:
        return AccountMemoryLoadResponse(content="")
    store = DocumentMemoryStore(session)
    content = await store.load(user.user_id, path, body.scope)
    return AccountMemoryLoadResponse(content=content)


class AccountMemorySaveRequest(BaseModel):
    path: str
    content: str
    scope: str | None = None


class AccountMemoryOkResponse(BaseModel):
    ok: bool = True


@router.post("/memory/save", response_model=AccountMemoryOkResponse)
async def save_account_memory(
    body: AccountMemorySaveRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> AccountMemoryOkResponse:
    """Upsert one memory note (画像/导航/主题/…). Write failures raise HTTP errors."""
    path = (body.path or "").strip()
    if not path:
        raise HTTPException(status_code=422, detail="path required")
    store = DocumentMemoryStore(session)
    await store.save(user.user_id, path, body.content, body.scope)
    return AccountMemoryOkResponse(ok=True)


class AccountMemoryDeleteRequest(BaseModel):
    path: str
    scope: str | None = None


@router.post("/memory/delete", response_model=AccountMemoryOkResponse)
async def delete_account_memory(
    body: AccountMemoryDeleteRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> AccountMemoryOkResponse:
    """Soft-delete one memory note (no-op if missing)."""
    path = (body.path or "").strip()
    if not path:
        raise HTTPException(status_code=422, detail="path required")
    store = DocumentMemoryStore(session)
    await store.delete(user.user_id, path, body.scope)
    return AccountMemoryOkResponse(ok=True)


class AccountMemoryProjectScopesResponse(BaseModel):
    scopes: list[str]


@router.post("/memory/project-scopes", response_model=AccountMemoryProjectScopesResponse)
async def list_account_memory_project_scopes(
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> AccountMemoryProjectScopesResponse:
    """Folder ids that hold a semantic project memory layer."""
    store = DocumentMemoryStore(session)
    scopes = await store.project_scopes(user.user_id)
    return AccountMemoryProjectScopesResponse(scopes=scopes)


# --- Consolidation pipeline state (episodes + scope sidecar; not Document-tree) ---


class AccountEpisodeAppendRequest(BaseModel):
    scope: str | None = None
    conversation_id: str
    summary: str
    actions_json: str = ""
    episode_id: str | None = None
    created_at: str | None = None


class AccountEpisodeRecord(BaseModel):
    id: str
    conversation_id: str
    summary: str
    created_at: str
    actions_json: str = ""


@router.post("/memory/episodes/append", response_model=AccountEpisodeRecord)
async def append_account_memory_episode(
    body: AccountEpisodeAppendRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> AccountEpisodeRecord:
    """Append one episodic digest into ``memory_episodes``."""
    from datetime import datetime

    from agentcore.memory.episode_store import DbEpisodeStore

    created: datetime | None = None
    if body.created_at:
        try:
            created = datetime.fromisoformat(body.created_at.replace("Z", "+00:00"))
        except ValueError:
            created = None
    store = DbEpisodeStore(session)
    rec = await store.append_episode(
        user.user_id,
        conversation_id=body.conversation_id,
        summary=body.summary,
        scope=body.scope,
        actions_json=body.actions_json or "",
        episode_id=body.episode_id,
        created_at=created,
    )
    return AccountEpisodeRecord(
        id=rec.id,
        conversation_id=rec.conversation_id,
        summary=rec.summary,
        created_at=rec.created_at,
        actions_json=rec.actions_json,
    )


class AccountEpisodesListResponse(BaseModel):
    episodes: list[AccountEpisodeRecord]


@router.post("/memory/episodes/list-undigested", response_model=AccountEpisodesListResponse)
async def list_account_undigested_episodes(
    body: AccountMemoryScopeRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> AccountEpisodesListResponse:
    from agentcore.memory.episode_store import DbEpisodeStore

    store = DbEpisodeStore(session)
    rows = await store.list_undigested(user.user_id, scope=body.scope)
    return AccountEpisodesListResponse(
        episodes=[
            AccountEpisodeRecord(
                id=r.id,
                conversation_id=r.conversation_id,
                summary=r.summary,
                created_at=r.created_at,
                actions_json=r.actions_json,
            )
            for r in rows
        ]
    )


class AccountEpisodesMarkDigestedRequest(BaseModel):
    scope: str | None = None
    episode_ids: list[str] = []
    consolidated_at: str | None = None


@router.post("/memory/episodes/mark-digested", response_model=AccountMemoryOkResponse)
async def mark_account_episodes_digested(
    body: AccountEpisodesMarkDigestedRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> AccountMemoryOkResponse:
    from datetime import UTC, datetime

    from agentcore.memory.episode_store import DbEpisodeStore

    stamp: datetime | None = None
    if body.consolidated_at:
        try:
            stamp = datetime.fromisoformat(body.consolidated_at.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
        except ValueError:
            stamp = None
    store = DbEpisodeStore(session)
    await store.mark_digested(
        user.user_id,
        list(body.episode_ids or []),
        scope=body.scope,
        consolidated_at=stamp,
    )
    return AccountMemoryOkResponse(ok=True)


class AccountEpisodesPurgeRequest(BaseModel):
    older_than_days: int = 30


class AccountEpisodesPurgeResponse(BaseModel):
    deleted: int


@router.post("/memory/episodes/purge", response_model=AccountEpisodesPurgeResponse)
async def purge_account_digested_episodes(
    body: AccountEpisodesPurgeRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> AccountEpisodesPurgeResponse:
    from agentcore.memory.episode_store import DbEpisodeStore

    store = DbEpisodeStore(session)
    deleted = await store.purge_digested(
        older_than_days=body.older_than_days, user_id=user.user_id
    )
    return AccountEpisodesPurgeResponse(deleted=deleted)


class AccountScopeStateResponse(BaseModel):
    last_semantic_at: str | None = None
    explore_workspace_key: str | None = None
    explore_fingerprint: str | None = None
    explore_fingerprint_dirty: bool = False


@router.post("/memory/scope-state/get", response_model=AccountScopeStateResponse)
async def get_account_scope_state(
    body: AccountMemoryScopeRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> AccountScopeStateResponse:
    from agentcore.memory.episode_store import DbEpisodeStore

    store = DbEpisodeStore(session)
    meta = await store.load_scope_meta(user.user_id, scope=body.scope)
    return AccountScopeStateResponse(
        last_semantic_at=(
            meta.last_semantic_at.isoformat() if meta.last_semantic_at else None
        ),
        explore_workspace_key=meta.explore_workspace_key,
        explore_fingerprint=meta.explore_fingerprint,
        explore_fingerprint_dirty=meta.explore_fingerprint_dirty,
    )


class AccountScopeStateSaveRequest(BaseModel):
    scope: str | None = None
    last_semantic_at: str | None = None
    explore_workspace_key: str | None = None
    explore_fingerprint: str | None = None
    explore_fingerprint_dirty: bool = False


@router.post("/memory/scope-state/save", response_model=AccountMemoryOkResponse)
async def save_account_scope_state(
    body: AccountScopeStateSaveRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> AccountMemoryOkResponse:
    from datetime import UTC, datetime

    from agentcore.memory.episode_store import DbEpisodeStore, ScopeMemoryMeta

    last: datetime | None = None
    if body.last_semantic_at:
        try:
            last = datetime.fromisoformat(body.last_semantic_at.replace("Z", "+00:00"))
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
        except ValueError:
            last = None
    store = DbEpisodeStore(session)
    await store.save_scope_meta(
        user.user_id,
        ScopeMemoryMeta(
            last_semantic_at=last,
            explore_workspace_key=body.explore_workspace_key,
            explore_fingerprint=body.explore_fingerprint,
            explore_fingerprint_dirty=body.explore_fingerprint_dirty,
        ),
        scope=body.scope,
    )
    return AccountMemoryOkResponse(ok=True)
