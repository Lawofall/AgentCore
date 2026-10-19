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
    from agentcore.db.repositories import UserRepository
    from agentcore.llm.catalog import resolve_model_catalog
    from agentcore.llm.composer_knobs import knobs_by_model

    users = UserRepository(session)
    hidden = await users.hidden_model_refs(user.user_id)
    enabled = await users.enabled_model_refs(user.user_id)
    catalog = await resolve_model_catalog(
        session, user.user_id, enabled_refs=enabled
    )
    names = await users.model_display_names(user.user_id)
    composer = await users.composer_model_override(user.user_id)
    vision_accept = await users.vision_accept_refs(user.user_id)
    return to_model_catalog_response(
        catalog,
        hidden_refs=list(hidden),
        enabled_refs=list(enabled),
        display_names=names,
        composer_model=composer,
        composer_model_knobs=knobs_by_model(composer),
        vision_accept_refs=vision_accept,
    )


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
    """``folder_id`` selects this desk's markdown. Empty means the account only."""

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
    """Account markdown, plus the requested folder. No ancestor scope."""

    global_rules: list[AccountRuleDoc]
    project_rules: list[AccountRuleDoc]
    global_on_demand_rules: list[AccountRuleDoc] = Field(default_factory=list)
    project_on_demand_rules: list[AccountRuleDoc] = Field(default_factory=list)
    global_path_rules: list[AccountRuleDoc] = Field(default_factory=list)
    project_path_rules: list[AccountRuleDoc] = Field(default_factory=list)


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


def _channel_lists(
    docs: Sequence[Document],
) -> tuple[list[AccountRuleDoc], list[AccountRuleDoc], list[AccountRuleDoc]]:
    """Split saved markdown by the frontmatter channel. Missing ``apply`` stays out."""
    from agentcore.documents.frontmatter import FrontmatterError, parse_entry_frontmatter
    from agentcore.documents.path_rules import patterns_are_unbounded

    always: list[AccountRuleDoc] = []
    on_demand: list[AccountRuleDoc] = []
    paths: list[AccountRuleDoc] = []
    for doc in docs:
        parsed = parse_entry_frontmatter(doc.content or "")
        if isinstance(parsed, FrontmatterError) or not parsed.apply_present:
            continue
        item = _rule_docs([doc])[0]
        if parsed.apply == "always" or (
            parsed.apply == "paths" and patterns_are_unbounded(parsed.paths)
        ):
            always.append(item)
        elif parsed.apply == "on_demand" and (doc.description or "").strip():
            on_demand.append(item)
        elif parsed.apply == "paths":
            paths.append(item)
    return always, on_demand, paths


@router.post("/rules/list", response_model=AccountRulesListResponse)
async def list_account_user_rules(
    body: AccountRulesListRequest,
    user: AccountApiUser,
    session: AsyncSession = Depends(get_db),
) -> AccountRulesListResponse:
    """Account markdown, plus this folder when ``folder_id`` is set."""
    repo = DocumentRepository(session)
    account_always, account_demand, account_paths = _channel_lists(
        await repo.list_scoped_markdown(user.user_id, None)
    )
    folder_always: list[AccountRuleDoc] = []
    folder_demand: list[AccountRuleDoc] = []
    folder_paths: list[AccountRuleDoc] = []
    if body.folder_id:
        folder_always, folder_demand, folder_paths = _channel_lists(
            await repo.list_scoped_markdown(user.user_id, body.folder_id)
        )
    return AccountRulesListResponse(
        global_rules=account_always,
        project_rules=folder_always,
        global_on_demand_rules=account_demand,
        project_on_demand_rules=folder_demand,
        global_path_rules=account_paths,
        project_path_rules=folder_paths,
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
    """Write one account-library rule by catalog name."""
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
    """Read one account-library rule by catalog name."""
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
    """Delete one account-library rule by catalog name."""
    return await _mutate_account_rule(
        user=user,
        session=session,
        action="delete",
        name=body.name,
        folder_id=body.folder_id,
    )
