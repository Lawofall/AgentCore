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
    DEFAULT_FOCUS,
    FOCUS_PROCESS,
    MAX_CHUNK_CHARS,
    normalize_focus,
    page_conversation,
    search_hit_from_messages,
)
from agentcore.core.errors import NotFoundError
from agentcore.core.types import is_uuid_id
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
    hit_index: int | None = None


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
        hit_index: int | None = None
        try:
            msgs = await msg_repo.list_all_for_conversation(row["conversation_id"])
            hit = search_hit_from_messages(msgs, (body.query or "").strip())
            if hit:
                snippet = hit.snippet
                hit_index = hit.message_index
        except Exception:  # noqa: BLE001 — snippet is best-effort
            snippet = None
            hit_index = None
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
                hit_index=hit_index,
            )
        )
    return ConversationSearchResponse(rows=out_rows, folder_miss=False)


class ConversationReadRequest(BaseModel):
    conversation_id: str
    cursor: str | None = None
    max_chars: int | None = Field(default=None, ge=1, le=MAX_CHUNK_CHARS)
    focus: str = DEFAULT_FOCUS
    query: str = ""


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
    message_offset: int = 0
    message_end: int = 0
    focus: str = DEFAULT_FOCUS
    query: str | None = None
    query_hit: bool = False
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
    """Owner-scoped transcript read (account ticket or access). Soft miss on 404.

    Default ``focus=dialogue`` (user/assistant visible text). ``process`` includes
    tools / debate / thinking. Pages are message-index cursors.
    """
    cid = (body.conversation_id or "").strip()
    if not cid or not is_uuid_id(cid):
        return ConversationReadResponse(status="soft_miss", conversation_id=cid)

    conv = await ConversationRepository(session).get_by_id(cid, user_id=user.user_id)
    if conv is None or conv.mode == "handoff":
        return ConversationReadResponse(status="soft_miss", conversation_id=cid)

    focus_n = normalize_focus(body.focus) or DEFAULT_FOCUS
    query_s = (body.query or "").strip() or None
    messages = list(await MessageRepository(session).list_all_for_conversation(cid))
    journal_map: dict = {}
    if focus_n == FOCUS_PROCESS:
        assistant_ids = [m.id for m in messages if m.role == "assistant"]
        journal_map = await TurnJournalRepository(session).load_map(assistant_ids)
    cursor_s = (body.cursor or "").strip() or None
    chunk = page_conversation(
        conv,
        messages,
        journal_map,
        focus=focus_n,
        cursor=cursor_s,
        query=query_s,
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
        message_offset=chunk.message_offset,
        message_end=chunk.message_end,
        focus=chunk.focus,
        query=chunk.query,
        query_hit=chunk.query_hit,
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
    # Injection scope (None = global). Sidecar joins always-on <设定> by folder, not author.
    folder_id: str | None = None


class AccountSkillReplacement(BaseModel):
    """One account-level 换用: official slot → user on-demand body for consult."""

    slot: str
    document_id: str
    document_name: str
    description: str = ""
    content: str = ""


class AccountRulesListResponse(BaseModel):
    """Always rules for ``<设定>`` plus on_demand bodies for 规则目录 / ``consult``.

    ``ancestor_*`` carry the enclosing folders' layers, outermost-first, and
    ``folder_chain`` is that same chain by id with the current folder last: the engine may
    be a desktop sidecar with no folders table, so the cloud is the only place that can
    resolve「谁在谁里面」(双模式工作区 §5.4 沿树继承).

    ``skill_replacements`` / ``skill_mutes`` are the merged overlay for this
    ``folder_id`` (account farthest, then the desk-owner folder chain, near wins).
    Bound documents are omitted from on_demand lists so the model does not see the
    same HOW twice. Muted official slots ride ``skill_mutes`` so sidecar consult
    listing matches the cloud overlay.
    """

    global_rules: list[AccountRuleDoc]
    project_rules: list[AccountRuleDoc]
    ancestor_rules: list[AccountRuleDoc] = Field(default_factory=list)
    global_on_demand_rules: list[AccountRuleDoc] = Field(default_factory=list)
    project_on_demand_rules: list[AccountRuleDoc] = Field(default_factory=list)
    ancestor_on_demand_rules: list[AccountRuleDoc] = Field(default_factory=list)
    folder_chain: list[str] = Field(default_factory=list)
    skill_replacements: list[AccountSkillReplacement] = Field(default_factory=list)
    skill_mutes: list[str] = Field(default_factory=list)


def _rule_docs(docs: Sequence[Document]) -> list[AccountRuleDoc]:
    return [
        AccountRuleDoc(
            name=d.name,
            content=d.content or "",
            description=d.description or "",
            folder_id=str(d.folder_id) if d.folder_id else None,
        )
        for d in docs
    ]


def _on_demand_rule_docs(
    docs: Sequence[Document], *, skip_names: set[str]
) -> list[AccountRuleDoc]:
    from agentcore.memory.rules_injection import rule_consult_name

    return [
        doc
        for doc in _rule_docs(docs)
        if rule_consult_name(doc.name) not in skip_names
    ]


async def _folder_scope_is_live(
    session: AsyncSession, user_id: str, folder_id: str | None
) -> bool:
    """False when ``folder_id`` names a missing or soft-deleted desk (global is live)."""
    if not folder_id:
        return True
    return (
        await FolderRepository(session).get_by_id(folder_id, user_id=user_id)
    ) is not None


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
            # Missing / soft-deleted: empty chain, not「只当前层」.
            folder_chain = []
    ancestors = folder_chain[:-1]
    current_id = folder_chain[-1] if folder_chain else None

    ancestor_docs: list[Document] = []
    ancestor_on_demand: list[Document] = []
    for scope in ancestors:
        ancestor_docs += await repo.list_injectable_rules(
            user.user_id, scope, ai_maintained=False
        )
        ancestor_on_demand += await repo.list_on_demand_user_rules(user.user_id, scope)

    project_docs: Sequence[Document] = []
    project_on_demand: Sequence[Document] = []
    if current_id:
        project_docs = await repo.list_injectable_rules(
            user.user_id, current_id, ai_maintained=False
        )
        project_on_demand = await repo.list_on_demand_user_rules(
            user.user_id, current_id
        )
    from agentcore.runtime.skills.replacements import resolve_skill_overlay

    overlay = await resolve_skill_overlay(
        session, user.user_id, folder_id=body.folder_id
    )
    skip_names = {item.document_name for item in overlay.replacements.values()}
    skill_replacements = [
        AccountSkillReplacement(
            slot=slot,
            document_id=item.document_id,
            document_name=item.document_name,
            description=item.summary,
            content=item.body,
        )
        for slot, item in sorted(overlay.replacements.items())
    ]

    return AccountRulesListResponse(
        global_rules=_rule_docs(
            await repo.list_injectable_rules(user.user_id, None, ai_maintained=False)
        ),
        project_rules=_rule_docs(project_docs),
        ancestor_rules=_rule_docs(ancestor_docs),
        global_on_demand_rules=_on_demand_rule_docs(
            await repo.list_on_demand_user_rules(user.user_id, None),
            skip_names=skip_names,
        ),
        project_on_demand_rules=_on_demand_rule_docs(
            project_on_demand, skip_names=skip_names
        ),
        ancestor_on_demand_rules=_on_demand_rule_docs(
            ancestor_on_demand, skip_names=skip_names
        ),
        folder_chain=folder_chain,
        skill_replacements=skill_replacements,
        skill_mutes=sorted(overlay.muted),
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
    in-process turn. A soft-deleted folder is an empty scope (设定 hibernates with the desk).
    """
    if not await _folder_scope_is_live(session, user.user_id, body.scope):
        return AccountMemoryListResponse(files=[])
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
    """Load one memory note body; missing path / hibernating folder → empty string (soft)."""
    path = (body.path or "").strip()
    if not path:
        return AccountMemoryLoadResponse(content="")
    if not await _folder_scope_is_live(session, user.user_id, body.scope):
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
