"""FastAPI dependencies (DB session, repositories, current user)."""

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Annotated

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from agentcore.memory import DocumentMemoryStore

from agentcore.admin import AdminService
from agentcore.auth import AuthService
from agentcore.auth.email_service import EmailAuthService
from agentcore.auth.mfa import AdminMfaService
from agentcore.config import settings
from agentcore.core.errors import (
    AdminProductForbiddenError,
    AuthenticationError,
    AuthorizationError,
    MfaRequiredError,
    MfaSetupRequiredError,
)
from agentcore.db.base import get_session
from agentcore.db.models import User
from agentcore.db.repositories import (
    AdminAuditRepository,
    AdminMfaRepository,
    AgentAuditEventRepository,
    BoardRepository,
    BookmarkRepository,
    ChatRepository,
    ConversationRepository,
    ConversationShareRepository,
    CostEventRepository,
    CredentialsRepository,
    DocumentRepository,
    EmailChallengeRepository,
    FeedbackRepository,
    FolderMemberRepository,
    FolderRepository,
    FriendRepository,
    HandoffJobRepository,
    MemoryUpdateRepository,
    MessageRepository,
    PendingRegistrationRepository,
    ProductNoticeRepository,
    PushDeviceRepository,
    RefreshTokenRepository,
    StandingTaskRepository,
    StandingTaskRunRepository,
    TurnJournalRepository,
    TurnMetricsRepository,
    UserBlockRepository,
    UserDirectoryRepository,
    UserLlmProviderRepository,
    UserRepository,
    UserWorkflowRepository,
)
from agentcore.folders.service import FolderDeskService
from agentcore.messaging import MessagingService
from agentcore.messaging.hub import HubChatEventPublisher, default_chat_hub
from agentcore.security.tokens import (
    TokenAudience,
    decode_access_token_claims,
    decode_access_token_family,
    decode_access_token_mfa_verified,
    decode_account_token,
    decode_folders_token,
)
from agentcore.storage.assets import AssetStorage, build_asset_storage

# Cookie name carrying the access JWT (set by the auth routes).
ACCESS_TOKEN_COOKIE = "access_token"

_AUTH_PREFIX = "/v1/auth"
_ADMIN_PREFIX = "/v1/admin"


def _bearer_token(authorization: str | None) -> str | None:
    """Extract the JWT from an ``Authorization: Bearer <token>`` header.

    The bearer path serves non-cookie clients (mobile web / Capacitor shell, M2):
    their origin can't rely on SameSite cookies, so they send the access token as a
    Bearer header instead. Returns None when the header is absent or not the Bearer
    scheme, so the caller falls back to the cookie (desktop) or to a 401.
    """
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


def _is_auth_path(path: str) -> bool:
    return path.startswith(_AUTH_PREFIX)


def _is_admin_path(path: str) -> bool:
    return path.startswith(_ADMIN_PREFIX)


def session_audience(request: Request) -> TokenAudience:
    """The audience of the caller's current access token.

    Reads the claim ``get_current_user`` stashed on ``request.state`` (already
    validated to be one of the two audiences). Routes that re-mint a token pair for
    the *same* session must carry this over rather than defaulting, or the successor
    session fails ``_enforce_audience_bounds`` on the next ``/v1/admin/*`` call.
    """
    return "admin" if getattr(request.state, "token_aud", None) == "admin" else "product"


def session_mfa_verified(request: Request) -> bool:
    """Whether the caller's access token carries the admin-MFA proof (claim ``mfa``)."""
    return getattr(request.state, "mfa_verified", False) is True


def _enforce_audience_bounds(request: Request, user: User, aud: str) -> None:
    """Block admin/product session crossover at the dependency layer."""
    path = request.url.path

    if _is_admin_path(path):
        if aud != "admin":
            raise AuthorizationError("请使用管理后台登录")
        return

    if _is_auth_path(path):
        return

    if user.role == "admin" or aud == "admin":
        raise AdminProductForbiddenError()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session."""
    async for session in get_session():
        yield session


def get_user_repo(session: AsyncSession = Depends(get_db)) -> UserRepository:
    return UserRepository(session)


def get_admin_mfa_repo(session: AsyncSession = Depends(get_db)) -> AdminMfaRepository:
    return AdminMfaRepository(session)


def get_admin_mfa_service(
    mfa_repo: AdminMfaRepository = Depends(get_admin_mfa_repo),
) -> AdminMfaService:
    return AdminMfaService(mfa_repo=mfa_repo)


def get_admin_service(session: AsyncSession = Depends(get_db)) -> AdminService:
    """Build the admin account-management service (用户管理) on the request session."""
    return AdminService(users=UserRepository(session))


def get_admin_audit_repo(session: AsyncSession = Depends(get_db)) -> AdminAuditRepository:
    return AdminAuditRepository(session)


def get_conversation_repo(session: AsyncSession = Depends(get_db)) -> ConversationRepository:
    return ConversationRepository(session)


def get_bookmark_repo(session: AsyncSession = Depends(get_db)) -> BookmarkRepository:
    return BookmarkRepository(session)


def get_conversation_share_repo(
    session: AsyncSession = Depends(get_db),
) -> ConversationShareRepository:
    return ConversationShareRepository(session)


def get_user_llm_provider_repo(
    session: AsyncSession = Depends(get_db),
) -> UserLlmProviderRepository:
    return UserLlmProviderRepository(session)


def get_asset_storage() -> AssetStorage:
    """The process-wide asset store (头像等小对象); filesystem in dev, S3 in prod."""
    return build_asset_storage()


def get_folder_repo(session: AsyncSession = Depends(get_db)) -> FolderRepository:
    return FolderRepository(session)


def get_document_repo(session: AsyncSession = Depends(get_db)) -> DocumentRepository:
    return DocumentRepository(session)


def get_memory_store(session: AsyncSession = Depends(get_db)) -> "DocumentMemoryStore":
    """A memory store bound to the REQUEST session (Agent记忆与知识系统 §5.7 换底).

    Route handlers use this rather than ``default_memory_store()`` so the editor's
    read-compare-write runs inside the request transaction — and, in the integration suite,
    the per-test schema the ``get_db`` override points at. Background callers (consolidation,
    turn tools) keep using ``default_memory_store()`` (self-opening sessions).
    """
    from agentcore.memory import DocumentMemoryStore

    return DocumentMemoryStore(session=session)


def get_board_repo(session: AsyncSession = Depends(get_db)) -> BoardRepository:
    return BoardRepository(session)


def get_message_repo(session: AsyncSession = Depends(get_db)) -> MessageRepository:
    return MessageRepository(session)


def get_agent_audit_repo(
    session: AsyncSession = Depends(get_db),
) -> AgentAuditEventRepository:
    return AgentAuditEventRepository(session)


def get_memory_update_repo(session: AsyncSession = Depends(get_db)) -> MemoryUpdateRepository:
    return MemoryUpdateRepository(session)


def get_cost_event_repo(session: AsyncSession = Depends(get_db)) -> CostEventRepository:
    return CostEventRepository(session)


def get_turn_metrics_repo(
    session: AsyncSession = Depends(get_db),
) -> TurnMetricsRepository:
    return TurnMetricsRepository(session)


def get_turn_journal_repo(
    session: AsyncSession = Depends(get_db),
) -> TurnJournalRepository:
    return TurnJournalRepository(session)


def get_handoff_job_repo(session: AsyncSession = Depends(get_db)) -> HandoffJobRepository:
    return HandoffJobRepository(session)


def get_standing_task_repo(
    session: AsyncSession = Depends(get_db),
) -> StandingTaskRepository:
    return StandingTaskRepository(session)


def get_standing_task_run_repo(
    session: AsyncSession = Depends(get_db),
) -> StandingTaskRunRepository:
    return StandingTaskRunRepository(session)


def get_user_workflow_repo(
    session: AsyncSession = Depends(get_db),
) -> UserWorkflowRepository:
    return UserWorkflowRepository(session)


def get_push_device_repo(
    session: AsyncSession = Depends(get_db),
) -> PushDeviceRepository:
    return PushDeviceRepository(session)


def get_feedback_repo(session: AsyncSession = Depends(get_db)) -> FeedbackRepository:
    return FeedbackRepository(session)


def get_notice_repo(session: AsyncSession = Depends(get_db)) -> ProductNoticeRepository:
    return ProductNoticeRepository(session)


def get_messaging_service(
    session: AsyncSession = Depends(get_db),
) -> MessagingService:
    """Build MessagingService (消息页 找人 IM) with its repos on one session.

    The realtime publisher fans a persisted message out to recipients' SSE
    firehoses through the process-wide in-process hub (消息IM.md §四); swap it
    for a Redis / NATS publisher behind the ``ChatEventPublisher`` seam to scale
    past one worker.
    """
    return MessagingService(
        users=UserRepository(session),
        chats=ChatRepository(session),
        blocks=UserBlockRepository(session),
        directory=UserDirectoryRepository(session),
        friends=FriendRepository(session),
        events=HubChatEventPublisher(default_chat_hub()),
        folder_members=FolderMemberRepository(session),
    )


def get_folder_desk_service(
    session: AsyncSession = Depends(get_db),
) -> FolderDeskService:
    """Cloud-folder collaboration desk (邀请/成员) on the request session."""
    return FolderDeskService(
        folders=FolderRepository(session),
        members=FolderMemberRepository(session),
        users=UserRepository(session),
        blocks=UserBlockRepository(session),
        directory=UserDirectoryRepository(session),
        events=HubChatEventPublisher(default_chat_hub()),
    )


def get_auth_service(
    session: AsyncSession = Depends(get_db),
    mfa: AdminMfaService = Depends(get_admin_mfa_service),
) -> AuthService:
    """Build AuthService with all repos bound to one request session."""
    return AuthService(
        users=UserRepository(session),
        credentials=CredentialsRepository(session),
        refresh_tokens=RefreshTokenRepository(session),
        mfa=mfa,
        session=session,
    )


def get_email_sender():
    from agentcore.mail import build_email_sender

    return build_email_sender()


def get_email_auth_service(
    session: AsyncSession = Depends(get_db),
    mailer=Depends(get_email_sender),
) -> EmailAuthService:
    return EmailAuthService(
        users=UserRepository(session),
        credentials=CredentialsRepository(session),
        refresh_tokens=RefreshTokenRepository(session),
        pending=PendingRegistrationRepository(session),
        challenges=EmailChallengeRepository(session),
        mailer=mailer,
        session=session,
    )


def get_credentials_repo(
    session: AsyncSession = Depends(get_db),
) -> CredentialsRepository:
    return CredentialsRepository(session)


async def get_current_user(
    request: Request,
    access_token: Annotated[str | None, Cookie(alias=ACCESS_TOKEN_COOKIE)] = None,
    authorization: Annotated[str | None, Header()] = None,
    user_repo: UserRepository = Depends(get_user_repo),
) -> User:
    """Resolve the authenticated user from the access-token cookie (desktop) or an
    ``Authorization: Bearer`` header (mobile/web bearer clients); 401 if absent/invalid."""
    token = access_token or _bearer_token(authorization)
    if not token:
        raise AuthenticationError("Not authenticated")
    user_id, aud = decode_access_token_claims(token)
    user = await user_repo.get_by_id(user_id)
    if user is None or user.status != "active":
        raise AuthenticationError("User not found or inactive")
    request.state.token_aud = aud
    request.state.token_family = decode_access_token_family(token)
    request.state.mfa_verified = decode_access_token_mfa_verified(token)
    _enforce_audience_bounds(request, user, aud)
    return user


async def get_optional_user(
    request: Request,
    access_token: Annotated[str | None, Cookie(alias=ACCESS_TOKEN_COOKIE)] = None,
    authorization: Annotated[str | None, Header()] = None,
    user_repo: UserRepository = Depends(get_user_repo),
) -> User | None:
    """Like get_current_user but returns None instead of raising when unauthenticated."""
    token = access_token or _bearer_token(authorization)
    if not token:
        return None
    try:
        user_id, aud = decode_access_token_claims(token)
    except AuthenticationError:
        return None
    user = await user_repo.get_by_id(user_id)
    if user is None or user.status != "active":
        return None
    request.state.token_aud = aud
    try:
        request.state.token_family = decode_access_token_family(token)
    except AuthenticationError:
        request.state.token_family = None
    try:
        request.state.mfa_verified = decode_access_token_mfa_verified(token)
    except AuthenticationError:
        request.state.mfa_verified = False
    try:
        _enforce_audience_bounds(request, user, aud)
    except (AdminProductForbiddenError, AuthorizationError):
        return None
    return user


async def get_folders_api_user(
    request: Request,
    access_token: Annotated[str | None, Cookie(alias=ACCESS_TOKEN_COOKIE)] = None,
    authorization: Annotated[str | None, Header()] = None,
    user_repo: UserRepository = Depends(get_user_repo),
) -> User:
    """Resolve the user for account folders read/write (list/create/get-by-id/soft-delete).

    Accepts either a normal product access session (cookie or Bearer access JWT)
    **or** a folders narrow ticket (``Authorization: Bearer`` with ``type=folders``).
    Inference tokens are refused (wrong type on both decoders). Sidecar must never
    receive an access token — it uses the folders ticket only.

    Soft-delete is in scope so the sidecar CEO's ``delete_folder`` shares one path
    with the sidebar. The irreversible ``DELETE /{id}/permanent`` deliberately stays
    on ``AuthUser`` — never reachable with a folders ticket.
    """
    bearer = _bearer_token(authorization)
    if bearer:
        try:
            user_id = decode_folders_token(bearer)
        except AuthenticationError:
            user_id = None
        if user_id is not None:
            user = await user_repo.get_by_id(user_id)
            if user is None or user.status != "active":
                raise AuthenticationError("User not found or inactive")
            return user
    return await get_current_user(
        request,
        access_token=access_token,
        authorization=authorization,
        user_repo=user_repo,
    )


async def get_account_api_user(
    request: Request,
    access_token: Annotated[str | None, Cookie(alias=ACCESS_TOKEN_COOKIE)] = None,
    authorization: Annotated[str | None, Header()] = None,
    user_repo: UserRepository = Depends(get_user_repo),
) -> User:
    """Resolve the user for account engine surface (R3a/R3b).

    Accepts either a normal product access session (cookie or Bearer access JWT)
    **or** an account narrow ticket (``Authorization: Bearer`` with ``type=account``).
    Folders / inference tokens are refused. Sidecar must never receive an access
    token — it uses the account ticket only. UI conversation / documents /
    memory-editor CRUD stays access-session only (not opened to this dependency).
    """
    bearer = _bearer_token(authorization)
    if bearer:
        try:
            user_id = decode_account_token(bearer)
        except AuthenticationError:
            user_id = None
        if user_id is not None:
            user = await user_repo.get_by_id(user_id)
            if user is None or user.status != "active":
                raise AuthenticationError("User not found or inactive")
            return user
    return await get_current_user(
        request,
        access_token=access_token,
        authorization=authorization,
        user_repo=user_repo,
    )


AuthUser = Annotated[User, Depends(get_current_user)]
FoldersApiUser = Annotated[User, Depends(get_folders_api_user)]
AccountApiUser = Annotated[User, Depends(get_account_api_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]


async def get_current_admin(
    request: Request,
    user: AuthUser,
    mfa_repo: AdminMfaRepository = Depends(get_admin_mfa_repo),
) -> User:
    """Resolve the current user and require the admin role (403 otherwise).

    When ``admin_mfa_required`` is on and the admin has enrolled MFA, the access
    token must carry ``mfa: true`` (issued only after ``/auth/login/mfa``).
    Unenrolled admins still get ``MfaSetupRequiredError`` so they can finish
    binding via ``AdminSessionUser`` routes; password-only sessions after
    enrollment are rejected until MFA login.
    """
    if user.role != "admin":
        raise AuthorizationError("Admin privileges required")
    if settings.admin_mfa_required:
        row = await mfa_repo.get_by_user_id(user.user_id)
        if row is None or row.enabled_at is None:
            raise MfaSetupRequiredError("请先完成双因素认证绑定")
        if not getattr(request.state, "mfa_verified", False):
            raise MfaRequiredError("请完成双因素认证后再访问管理接口")
    return user


AdminUser = Annotated[User, Depends(get_current_admin)]


async def get_admin_session_user(user: AuthUser) -> User:
    """Admin-role session without MFA enrollment (MFA setup routes only)."""
    if user.role != "admin":
        raise AuthorizationError("Admin privileges required")
    return user


AdminSessionUser = Annotated[User, Depends(get_admin_session_user)]
