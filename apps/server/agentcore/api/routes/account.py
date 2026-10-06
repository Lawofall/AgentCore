"""Account narrow-ticket mint + engine surface for sidecar (R3a/R3b).

Desktop convention (parallel desktop inject):
- Mint: ``POST /v1/account/token`` with cookie/Bearer **access** session
  → ``{token, expires_in_sec}`` (``type=account`` JWT).
- Sidecar inject: ``accountAuth: {baseUrl, apiKey}`` where
  ``baseUrl`` = ``{apiOrigin}/v1/account`` and ``apiKey`` = minted token.
- Cloud calls (account ticket **or** access):
  ``POST {baseUrl}/conversations/search|read|chat-context``,
  ``POST {baseUrl}/rules/list|write|read|delete`` (list = always + on_demand bodies for
  规则目录 / ``consult``),
  ``GET {baseUrl}/models`` (same catalog as ``GET /v1/users/me/models``).
- Does **not** open UI conversation / documents CRUD to the
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
from agentcore.api.schemas import ModelCatalogResponse
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
from agentcore.core.search_query import SEARCH_DEFAULT_LIMIT, SEARCH_HARD_CAP
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
from agentcore.memory.rules_injection import mutate_user_rule
from agentcore.security.tokens import create_account_token

router = APIRouter(prefix="/account", tags=["account"])

_MAX_LOOKBACK_HOURS = 168


class AccountTokenResponse(BaseModel):
    """Freshly minted account narrow token + lifetime (sidecar log-tool auth).

    Desktop: ``baseUrl`` for ``accountAuth`` is ``{apiOrigin}/v1/account``;
    ``apiKey`` is ``token``. Mint path: ``POST /v1/account/token``.
    """

    token: str
    expires_in_sec: int


@router.get("/models", response_model=ModelCatalogResponse)
async def list_account_models(
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> ModelCatalogResponse:
    """Model catalog for a ticketed sidecar. Same rows as ``GET /v1/users/me/models``."""
    from agentcore.api.routes.model_catalog import to_model_catalog_response
    from agentcore.llm.catalog import resolve_model_catalog

    catalog = await resolve_model_catalog(session, user.user_id)
    return to_model_catalog_response(catalog)


@router.post("/token", response_model=AccountTokenResponse)
async def mint_account_token(user: AuthUser) -> AccountTokenResponse:
    """Exchange the caller's cookie/Bearer access session for an account narrow ticket."""
    return AccountTokenResponse(
        token=create_account_token(user.user_id),
        expires_in_sec=settings.account_token_expire_minutes * 60,
    )


class ConversationSearchRequest(BaseModel):
    """Sidecar / account-ticket search.

    Log tool always sends archived, never hours / global-only.
    """

    query: str = ""
    folder_id: str | None = None
    include_archived: bool = True
    global_chats_only: bool = False
    exclude_conversation_id: str | None = None
    limit: int = Field(default=SEARCH_DEFAULT_LIMIT, ge=1, le=SEARCH_HARD_CAP)
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
    """Owner-scoped conversation search (account ticket or access)."""
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
    query: str | None = None


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


class HistoryToolCallFunction(BaseModel):
    name: str = ""
    arguments: str = ""


class HistoryToolCall(BaseModel):
    id: str = ""
    type: Literal["function"] = "function"
    function: HistoryToolCallFunction = Field(default_factory=HistoryToolCallFunction)


class ChatContextItem(BaseModel):
    """One CEO-window row: user, assistant (optional tool_calls), or tool."""

    role: Literal["user", "assistant", "tool"]
    content: str
    tool_calls: list[HistoryToolCall] | None = None
    tool_call_id: str | None = None
    reasoning_content: str | None = None
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
        if role not in ("user", "assistant", "tool") or not isinstance(content, str):
            continue
        if role == "tool" and not (
            isinstance(row.get("tool_call_id"), str) and row.get("tool_call_id")
        ):
            continue
        ledger = row.get("evidence_ledger")
        calls = row.get("tool_calls")
        tcid = row.get("tool_call_id")
        reasoning = row.get("reasoning_content")
        items.append(
            ChatContextItem(
                role=role,
                content=content,
                tool_calls=calls if isinstance(calls, list) and calls else None,
                tool_call_id=tcid if isinstance(tcid, str) and tcid else None,
                reasoning_content=reasoning if isinstance(reasoning, str) and reasoning else None,
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


# --- Engine-minimal user rules (R3b; not the UI documents editor) ---


class AccountRulesListRequest(BaseModel):
    """Optional project layer; global rules always included."""

    folder_id: str | None = None


class AccountRuleDoc(BaseModel):
    id: str = ""
    name: str
    content: str
    # Retrieval summary for the 规则目录; on_demand entries are picked by this, not by body.
    description: str = ""
    # Injection scope (None = global). Sidecar joins always-on <设定> by folder, not author.
    folder_id: str | None = None


class AccountRulesListResponse(BaseModel):
    """Always rules for ``<设定>``, path rules for ``<路径约定>``, on_demand for consult.

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
    global_path_rules: list[AccountRuleDoc] = Field(default_factory=list)
    project_path_rules: list[AccountRuleDoc] = Field(default_factory=list)
    ancestor_path_rules: list[AccountRuleDoc] = Field(default_factory=list)
    folder_chain: list[str] = Field(default_factory=list)


def _rule_docs(docs: Sequence[Document]) -> list[AccountRuleDoc]:
    return [
        AccountRuleDoc(
            id=str(d.id),
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
    """User rules for turn assembly: always → ``<设定>``; paths → index; on_demand → catalog."""
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
    ancestor_path: list[Document] = []
    for scope in ancestors:
        ancestor_docs += await repo.list_injectable_rules(
            user.user_id, scope, ai_maintained=False
        )
        ancestor_on_demand += await repo.list_on_demand_user_rules(user.user_id, scope)
        ancestor_path += await repo.list_path_user_rules(user.user_id, scope)

    project_docs: Sequence[Document] = []
    project_on_demand: Sequence[Document] = []
    project_path: Sequence[Document] = []
    if current_id:
        project_docs = await repo.list_injectable_rules(
            user.user_id, current_id, ai_maintained=False
        )
        project_on_demand = await repo.list_on_demand_user_rules(
            user.user_id, current_id
        )
        project_path = await repo.list_path_user_rules(user.user_id, current_id)
    return AccountRulesListResponse(
        global_rules=_rule_docs(
            await repo.list_injectable_rules(user.user_id, None, ai_maintained=False)
        ),
        project_rules=_rule_docs(project_docs),
        ancestor_rules=_rule_docs(ancestor_docs),
        global_on_demand_rules=_on_demand_rule_docs(
            await repo.list_on_demand_user_rules(user.user_id, None),
            skip_names=set(),
        ),
        project_on_demand_rules=_on_demand_rule_docs(
            project_on_demand, skip_names=set()
        ),
        ancestor_on_demand_rules=_on_demand_rule_docs(
            ancestor_on_demand, skip_names=set()
        ),
        global_path_rules=_rule_docs(await repo.list_path_user_rules(user.user_id, None)),
        project_path_rules=_rule_docs(project_path),
        ancestor_path_rules=_rule_docs(ancestor_path),
        folder_chain=folder_chain,
    )


class AccountRuleWriteRequest(BaseModel):
    name: str
    content: str
    folder_id: str | None = None
    apply: Literal["always", "on_demand", "paths"] | None = None
    description: str | None = None


class AccountRuleNameRequest(BaseModel):
    name: str
    folder_id: str | None = None


class AccountRuleCatalogItem(BaseModel):
    name: str
    apply: str = ""
    description: str = ""


class AccountRuleMutationResponse(BaseModel):
    changed: bool
    action: str
    message: str
    name: str = ""
    apply: str = ""
    body: str = ""
    catalog: list[AccountRuleCatalogItem] = Field(default_factory=list)
    ok: bool = True


def _rule_mutation_response(result: object) -> AccountRuleMutationResponse:
    from agentcore.memory.rules_injection import UserRuleMutationResult

    assert isinstance(result, UserRuleMutationResult)
    return AccountRuleMutationResponse(
        changed=result.changed,
        action=result.action,
        message=result.message,
        name=result.name,
        apply=result.apply,
        body=result.body,
        catalog=[
            AccountRuleCatalogItem(name=n, apply=a, description=d)
            for n, a, d in result.catalog
        ],
        ok=result.ok,
    )


async def _mutate_account_rule(
    *,
    user: AccountApiUser,
    session: AsyncSession,
    action: str,
    name: str | None,
    content: str | None = None,
    folder_id: str | None = None,
    apply: str | None = None,
    description: str | None = None,
) -> AccountRuleMutationResponse:
    try:
        result = await mutate_user_rule(
            DocumentRepository(session),
            user.user_id,
            folder_id=folder_id,
            action=action,
            name=name,
            content=content,
            apply=apply,
            description=description,
        )
    except AlwaysQuotaExceededError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ALWAYS_QUOTA_EXCEEDED",
                "message": exc.message,
            },
        ) from exc
    return _rule_mutation_response(result)


@router.post("/rules/write", response_model=AccountRuleMutationResponse)
async def write_account_user_rule(
    body: AccountRuleWriteRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> AccountRuleMutationResponse:
    """Write a named user-rule markdown under .agentcore/rules/."""
    return await _mutate_account_rule(
        user=user,
        session=session,
        action="write",
        name=body.name,
        content=body.content,
        folder_id=body.folder_id,
        apply=body.apply,
        description=body.description,
    )


@router.post("/rules/read", response_model=AccountRuleMutationResponse)
async def read_account_user_rule(
    body: AccountRuleNameRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> AccountRuleMutationResponse:
    """Read one named user-rule markdown under .agentcore/rules/."""
    return await _mutate_account_rule(
        user=user,
        session=session,
        action="read",
        name=body.name,
        folder_id=body.folder_id,
    )


@router.post("/rules/delete", response_model=AccountRuleMutationResponse)
async def delete_account_user_rule(
    body: AccountRuleNameRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> AccountRuleMutationResponse:
    """Delete one named user-rule markdown under .agentcore/rules/."""
    return await _mutate_account_rule(
        user=user,
        session=session,
        action="delete",
        name=body.name,
        folder_id=body.folder_id,
    )
