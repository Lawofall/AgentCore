"""Shared helpers for the conversation route modules.

Kept in one private module so the domain route modules (crud / messages / handoff
/ …) can share the owner-scoping guards and the pre-turn billing gate.
"""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.billing.gate import preflight_llm_credentials
from agentcore.core.errors import BYOK_KEY_REQUIRED_MESSAGE, AuthorizationError, NotFoundError
from agentcore.db.models import Conversation, User
from agentcore.db.repositories import (
    ConversationRepository,
    CostEventRepository,
    UserLlmProviderRepository,
)
from agentcore.llm.resolve import (
    LLMCredentials,
    ModelSelection,
    platform_llm_credentials,
    resolve_account_default_model,
    resolve_conversation_model_selection,
)
from agentcore.llm.tools_gate import TOOLS_SOFT_GATE_WARNING
from agentcore.runtime.events import EventSink, turn_warning


@dataclass(frozen=True)
class TurnPreflightResult:
    """Pre-turn billing gate outcome threaded into the SSE pipeline."""

    credentials: LLMCredentials | None
    warnings: list[str] = field(default_factory=list)
    supports_tools: bool | None = None


def emit_preflight_warnings(sink: EventSink, preflight: TurnPreflightResult) -> None:
    """Push soft-gate hints onto the SSE stream before the pipeline task starts."""
    for warning in preflight.warnings:
        sink.emit(turn_warning(warning))


async def _require_owned_conversation(
    conversation_id: str, user_id: str, repo: ConversationRepository
) -> None:
    """404 unless the conversation exists and the caller may see it."""
    conv = await repo.get_by_id(conversation_id, user_id=user_id)
    if not conv:
        raise NotFoundError("对话不存在")


async def _get_owned_conversation(
    conversation_id: str, user_id: str, repo: ConversationRepository
) -> Conversation:
    """Return the conversation if the caller may see it (owner or accepted desk member).

    Snapshot routes need ``folder_id`` to resolve the right workspace: a folder's
    conversations share its space; an ungrouped one has its own (workspace.locate).
    """
    conv = await repo.get_by_id(conversation_id, user_id=user_id)
    if not conv:
        raise NotFoundError("对话不存在")
    return conv


async def _require_conversation_write(
    conversation_id: str, user_id: str, session: AsyncSession
):
    """404 if invisible; 403 if viewer (read-only desk member)."""
    from agentcore.folders.desk import resolve_conversation_access

    access = await resolve_conversation_access(
        session, conversation_id=conversation_id, user_id=user_id
    )
    if access is None:
        raise NotFoundError("对话不存在")
    if not access.can_write:
        raise AuthorizationError("只读成员不能执行此操作")
    return access


async def _tools_support_warnings(
    session: AsyncSession,
    user_id: str,
    *,
    needs_tools: bool,
    provider_id: str | None,
) -> list[str]:
    """Soft gate hint when probe marked the turn's provider as lacking tool calling.

    Scoped to the provider this turn resolved to (``provider_id``); a keyless / platform
    turn (no provider) has no BYOK probe to warn from.
    """
    if not needs_tools or not provider_id:
        return []
    row = await UserLlmProviderRepository(session).get(provider_id, user_id=user_id)
    if row is not None and row.supports_tools is False:
        return [TOOLS_SOFT_GATE_WARNING]
    return []


async def _preflight_turn_llm(
    *,
    session: AsyncSession,
    user: User,
    cost_repo: CostEventRepository,
    needs_tools: bool = False,
    conv: Conversation | None = None,
) -> TurnPreflightResult:
    """Pre-turn billing gate, run before the SSE opens so a refused turn gets a
    clean error instead of a half-opened stream.

    Credential routing follows the resolved ``model_origin`` for this turn (conversation
    override when present, else account default). BYOK origin returns the user's key
    without quota checks; platform origin enforces quota then runs on the global key.

    When ``needs_tools`` (delegate / debate turn) and probe recorded
    ``supports_tools=False``, a warning is returned — soft gate only, never a 400.
    """
    if conv is not None:
        selection = await resolve_conversation_model_selection(session, conv, user.user_id)
    else:
        selection = await resolve_account_default_model(session, user.user_id)
    warnings = await _tools_support_warnings(
        session, user.user_id, needs_tools=needs_tools, provider_id=selection.provider_id
    )
    supports_tools = await _selection_supports_tools(session, user.user_id, selection)
    credentials = await preflight_llm_credentials(
        session=session,
        user=user,
        cost_repo=cost_repo,
        byok_missing_message=BYOK_KEY_REQUIRED_MESSAGE,
        model_origin=selection.origin,
        provider_id=selection.provider_id,
    )
    if selection.origin == "platform":
        # Platform path: the gate returns None after enforcing quota / vetting availability.
        # Resolve the per-model platform credential so ``build_provider`` uses the right
        # upstream key/base_url for this model (source=platform ⇒ 计费仍按 platform 入账).
        credentials = platform_llm_credentials(model=selection.model)
    return TurnPreflightResult(
        credentials=credentials, warnings=warnings, supports_tools=supports_tools
    )


async def _selection_supports_tools(
    session: AsyncSession, user_id: str, selection: ModelSelection
) -> bool | None:
    """The probed tool-support hint for the turn's resolved provider (None if platform)."""
    if selection.provider_id is None:
        return None
    row = await UserLlmProviderRepository(session).get(selection.provider_id, user_id=user_id)
    return row.supports_tools if row is not None else None


async def _preflight_owned_chat_turn(
    conversation_id: str,
    user: User,
    session: AsyncSession,
    *,
    needs_tools: bool = False,
) -> TurnPreflightResult:
    """Owner check + billing gate on the request-scoped session (SSE preflight only).

    Callers must invoke :func:`agentcore.api.sse.release_request_db_before_sse`
    after this returns and before opening the SSE stream so the pooled connection
    is not held for minutes.
    """
    conv_repo = ConversationRepository(session)
    cost_repo = CostEventRepository(session)
    conv = await conv_repo.get_by_id(conversation_id, user_id=user.user_id)
    if not conv:
        raise NotFoundError("对话不存在")
    await _require_conversation_write(conversation_id, user.user_id, session)
    return await _preflight_turn_llm(
        session=session,
        user=user,
        cost_repo=cost_repo,
        needs_tools=needs_tools,
        conv=conv,
    )
