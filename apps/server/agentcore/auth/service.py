"""Authentication service: registration, login, token refresh, logout.

Holds all auth business logic and policy:
- open registration gated by ``REGISTRATION_OPEN``,
- brute-force lockout (failed-attempt counting + temporary lock),
- refresh-token rotation with reuse detection (a presented token that was
  already rotated/revoked compromises the whole family -> revoke it).

Repositories do pure data access; ``security`` does hashing/JWT; the HTTP layer
stays thin. The service depends on repository instances so it is unit-testable
with in-memory fakes (no DB).
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.auth.client import ClientPlatform, is_product_platform, platform_to_audience
from agentcore.auth.email_codes import normalize_email
from agentcore.auth.mfa import AdminMfaService
from agentcore.auth.usernames import (
    USERNAME_COOLDOWN_DAYS,
    is_generated_handle,
    validate_username_for_claim,
)
from agentcore.config import settings
from agentcore.core.errors import (
    AdminProductForbiddenError,
    AuthenticationError,
    AuthorizationError,
    EmailNotVerifiedError,
    NotFoundError,
    ValidationError,
)
from agentcore.core.logging import get_logger
from agentcore.core.types import new_id
from agentcore.db.models import RefreshToken, User
from agentcore.db.repositories import (
    CredentialsRepository,
    RefreshTokenRepository,
    UserRepository,
)
from agentcore.security import (
    create_access_token,
    create_mfa_pending_token,
    decode_mfa_pending_token,
    generate_refresh_token,
    generate_temp_password,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from agentcore.security.tokens import TokenAudience

logger = get_logger(__name__)


def _subject_hash(username: str) -> str:
    """Short SHA-256 of normalized username — audit subject without plaintext identity."""
    return hashlib.sha256(username.strip().lower().encode()).hexdigest()[:16]

_MIN_PASSWORD_LENGTH = 8
_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_DURATION = timedelta(minutes=15)
_USER_AGENT_MAX = 512
# Benign-concurrency grace for refresh rotation: a just-rotated token re-presented
# within this window is treated as the *same* logical refresh, not a leak. Clients
# routinely fire several requests at once; when the access token has expired they
# each 401 and refresh with the same refresh cookie before the rotated one lands,
# so revoking the family there would log the user out mid-session for no reason
# (认证与会话.md §五). Past the window, a rotated token reappearing is a genuine
# reuse/replay and still nukes the family. The frontend also single-flights its
# refresh calls (services/api.ts) so this window is a backstop, not the only guard.
_REFRESH_REUSE_GRACE = timedelta(seconds=10)

# Constant-time login: an argon2 hash of a throwaway value. When the username/credentials
# don't exist we still run one verify against this, so "no such user" costs the same
# wall-clock as "wrong password" — denying the timing oracle an unauthenticated caller
# would otherwise use to enumerate valid usernames (SEC-004). Computed once at import.
_DUMMY_PASSWORD_HASH = hash_password("agentcore-login-timing-equalizer")

# Sentinel for "field not provided" in a partial profile update, distinct from an
# explicit None (which clears the nullable email column).
_UNSET: Any = object()


class _ProfileUpdate(TypedDict, total=False):
    """Partial kwargs for :meth:`UserRepository.update` (never ``commit``)."""

    display_name: str
    email: str | None
    email_verified_at: datetime | None
    username: str
    username_changed_at: datetime


@dataclass(frozen=True)
class TokenPair:
    """Access JWT + opaque refresh token (raw form, for the caller to set as cookies)."""

    access_token: str
    refresh_token: str
    # Mirrors refresh_tokens.persist_session — routes use this for cookie Max-Age
    # and bearer refresh_expires_in.
    persist_session: bool = True


@dataclass(frozen=True)
class LoginResult:
    """Outcome of a credential check — tokens may be deferred for MFA."""

    user: User
    tokens: TokenPair | None = None
    mfa_required: bool = False
    pending_token: str | None = None
    mfa_setup_required: bool = False


@dataclass(frozen=True)
class SessionMeta:
    """Request-bound session bookkeeping captured at login / refresh."""

    platform: ClientPlatform | None = None
    user_agent: str | None = None
    ip: str | None = None


@dataclass(frozen=True)
class AuthSession:
    """One active login device (refresh-token family), owner-scoped."""

    id: str
    platform: str | None
    user_agent: str | None
    ip: str | None
    created_at: datetime
    last_used_at: datetime
    current: bool


def _truncate_ua(user_agent: str | None) -> str | None:
    if user_agent is None:
        return None
    ua = user_agent.strip()
    if not ua:
        return None
    return ua[:_USER_AGENT_MAX]


class AuthService:
    def __init__(
        self,
        *,
        users: UserRepository,
        credentials: CredentialsRepository,
        refresh_tokens: RefreshTokenRepository,
        mfa: AdminMfaService | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self._users = users
        self._credentials = credentials
        self._refresh_tokens = refresh_tokens
        self._mfa = mfa
        # Shared request session for multi-repo unit-of-work commits (P1-8).
        # Unit tests pass ``None`` and use in-memory fakes (no real txn).
        self._session = session

    async def _commit(self) -> None:
        """Commit after a multi-repo composite; no-op for unit-test fakes."""
        if self._session is not None:
            await self._session.commit()

    async def register(
        self,
        *,
        username: str,
        password: str,
        display_name: str | None = None,
        email: str | None = None,
        registration_ip: str | None = None,
    ) -> User:
        if not settings.registration_open:
            raise AuthorizationError("注册已关闭")

        username = validate_username_for_claim(username)
        if len(password) < _MIN_PASSWORD_LENGTH:
            raise ValidationError(f"密码至少需要 {_MIN_PASSWORD_LENGTH} 个字符")

        if await self._users.get_by_username(username) is not None:
            raise ValidationError("该用户名已被占用")
        if email:
            email = email.strip() or None
        if email:
            existing = await self._users.get_by_email(email)
            if existing is not None:
                raise ValidationError("该邮箱已被占用")

        # One txn: user + credentials (avoid orphan user if credentials insert fails).
        user = await self._users.create(
            username=username,
            display_name=display_name or username,
            email=email,
            registration_ip=registration_ip,
            commit=False,
        )
        await self._credentials.create(
            user_id=user.user_id, password_hash=hash_password(password), commit=False
        )
        await self._commit()
        return user

    async def _resolve_login_user(self, identifier: str) -> User | None:
        """Username field accepts email (contains ``@``) or a handle.

        Malformed email shapes return None so login stays a uniform 401.
        Accounts with a null email are found only via the handle path.
        """
        raw = identifier.strip()
        if not raw:
            return None
        if "@" in raw:
            try:
                email = normalize_email(raw)
            except ValidationError:
                return None
            return await self._users.get_by_email(email)
        return await self._users.get_by_username(raw)

    async def login(
        self,
        *,
        username: str,
        password: str,
        platform: ClientPlatform = "desktop",
        meta: SessionMeta | None = None,
        persist_session: bool = True,
    ) -> LoginResult:
        user = await self._resolve_login_user(username)
        creds = await self._credentials.get_by_user_id(user.user_id) if user else None
        # Uniform failure: never reveal whether the account exists. Run one verify
        # against a dummy hash so a missing user takes the same wall-clock as a wrong
        # password — no timing oracle for username enumeration (SEC-004). Result ignored.
        if user is None or creds is None:
            verify_password(password, _DUMMY_PASSWORD_HASH)
            logger.warning(
                "auth.login_failed",
                reason="unknown",
                subject=_subject_hash(username),
                platform=platform,
            )
            raise AuthenticationError("用户名或密码错误")

        now = datetime.now(UTC)
        if creds.locked_until is not None and creds.locked_until > now:
            logger.warning(
                "auth.login_failed",
                reason="locked",
                user_id=user.user_id,
                platform=platform,
            )
            raise AuthenticationError("账户已临时锁定，请稍后再试")

        if not verify_password(password, creds.password_hash):
            await self._register_failure(creds.user_id, now)
            logger.warning(
                "auth.login_failed",
                reason="password",
                user_id=user.user_id,
                platform=platform,
            )
            raise AuthenticationError("用户名或密码错误")

        if creds.failed_attempts or creds.locked_until is not None:
            await self._credentials.reset_failure_state(user.user_id)

        if settings.require_email_verified and getattr(user, "email_verified_at", None) is None:
            raise EmailNotVerifiedError()

        if user.role == "admin" and is_product_platform(platform):
            raise AdminProductForbiddenError()

        if user.role != "admin" and platform == "admin":
            logger.warning(
                "auth.login_failed",
                reason="role",
                user_id=user.user_id,
                platform=platform,
            )
            raise AuthenticationError("用户名或密码错误")

        audience = platform_to_audience(platform)
        session_meta = meta or SessionMeta(platform=platform)

        if user.role == "admin":
            if (
                settings.admin_mfa_required
                and self._mfa is not None
                and await self._mfa.is_enrolled(user.user_id)
            ):
                pending = create_mfa_pending_token(
                    user.user_id,
                    audience=audience,
                    persist_session=persist_session,
                )
                return LoginResult(
                    user=user,
                    mfa_required=True,
                    pending_token=pending,
                )
            tokens = await self._issue_tokens(
                user.user_id,
                family=new_id(),
                now=now,
                audience=audience,
                meta=session_meta,
                persist_session=persist_session,
            )
            return LoginResult(
                user=user,
                tokens=tokens,
                mfa_setup_required=settings.admin_mfa_required,
            )

        tokens = await self._issue_tokens(
            user.user_id,
            family=new_id(),
            now=now,
            audience=audience,
            meta=session_meta,
            persist_session=persist_session,
        )
        return LoginResult(user=user, tokens=tokens)

    async def complete_mfa_login(
        self,
        *,
        pending_token: str,
        code: str | None = None,
        recovery_code: str | None = None,
        meta: SessionMeta | None = None,
    ) -> tuple[User, TokenPair]:
        if self._mfa is None:
            raise AuthenticationError("MFA not configured")
        user_id, audience, persist_session = decode_mfa_pending_token(pending_token)
        user = await self._users.get_by_id(user_id)
        if user is None or user.status != "active" or user.role != "admin":
            raise AuthenticationError("Invalid or expired MFA session")
        verified = False
        method: str
        if code:
            method = "totp"
            verified = await self._mfa.verify_code(user_id=user_id, code=code.strip())
        elif recovery_code:
            method = "recovery"
            verified = await self._mfa.verify_recovery_code(
                user_id=user_id, code=recovery_code.strip()
            )
        else:
            raise ValidationError("请输入验证码或恢复码")
        if not verified:
            logger.warning(
                "auth.login_failed",
                reason="mfa",
                method=method,
                user_id=user_id,
            )
            raise AuthenticationError("验证码无效或已过期")
        if method == "recovery":
            logger.info("auth.mfa_recovery_used", user_id=user_id)
        now = datetime.now(UTC)
        platform: ClientPlatform = "admin" if audience == "admin" else "desktop"
        session_meta = meta or SessionMeta(platform=platform)
        tokens = await self._issue_tokens(
            user.user_id,
            family=new_id(),
            now=now,
            audience=audience,
            meta=session_meta,
            mfa_verified=True,
            persist_session=persist_session,
        )
        return user, tokens

    async def refresh(
        self, *, refresh_token: str, meta: SessionMeta | None = None
    ) -> TokenPair:
        record = await self._refresh_tokens.get_by_hash(hash_refresh_token(refresh_token))
        now = datetime.now(UTC)

        if record is None:
            raise AuthenticationError("Invalid refresh token")

        # Already revoked (logout / password change / a prior reuse detection):
        # the session is dead -> keep the family revoked, force a fresh login.
        if record.revoked_at is not None:
            await self._refresh_tokens.revoke_family(record.token_family)
            raise AuthenticationError("Refresh token reuse detected")

        # Absolute family ceiling (sliding renewals must not outlive this).
        try:
            self._assert_family_within_max(record, now=now)
        except AuthenticationError:
            await self._refresh_tokens.revoke_family(record.token_family)
            raise

        # Already rotated: benign concurrent retry vs. a real replay/leak. Inside
        # the grace window it's the same logical refresh (the access token expired
        # and several requests refreshed with the same cookie at once) -> mint a
        # fresh successor in the same family without revoking anyone. Outside it,
        # a rotated token reappearing is a genuine reuse -> revoke the family.
        if record.rotated_at is not None:
            if now - record.rotated_at > _REFRESH_REUSE_GRACE:
                await self._refresh_tokens.revoke_family(record.token_family)
                raise AuthenticationError("Refresh token reuse detected")
            return await self._issue_tokens(
                record.user_id,
                family=record.token_family,
                now=now,
                audience=record.client_aud,  # type: ignore[arg-type]
                meta=self._meta_for_refresh(record, meta),
                family_started_at=record.family_started_at,
                mfa_verified=await self._refresh_mfa_verified(record),
                persist_session=bool(record.persist_session),
            )

        if record.expires_at <= now:
            raise AuthenticationError("Refresh token expired")

        await self._refresh_tokens.mark_rotated(record.id, commit=False)
        pair = await self._issue_tokens(
            record.user_id,
            family=record.token_family,
            now=now,
            audience=record.client_aud,  # type: ignore[arg-type]
            meta=self._meta_for_refresh(record, meta),
            family_started_at=record.family_started_at,
            mfa_verified=await self._refresh_mfa_verified(record),
            persist_session=bool(record.persist_session),
            commit=False,
        )
        await self._commit()
        return pair

    async def logout(self, *, refresh_token: str) -> None:
        record = await self._refresh_tokens.get_by_hash(hash_refresh_token(refresh_token))
        if record is not None:
            await self._refresh_tokens.revoke_family(record.token_family)

    # --- sessions (device management) ---

    async def list_sessions(
        self, *, user_id: str, current_family: str | None
    ) -> list[AuthSession]:
        """List active login devices for ``user_id``, aggregated by token family."""
        tips = await self._refresh_tokens.list_active_session_tips(user_id=user_id)
        by_family: dict[str, RefreshToken] = {}
        for tip in tips:
            prev = by_family.get(tip.token_family)
            if prev is None or tip.last_used_at >= prev.last_used_at:
                by_family[tip.token_family] = tip
        sessions = [
            AuthSession(
                id=row.token_family,
                platform=row.client_platform,
                user_agent=row.user_agent,
                ip=row.ip,
                created_at=row.family_started_at,
                last_used_at=row.last_used_at,
                current=bool(current_family and row.token_family == current_family),
            )
            for row in by_family.values()
        ]
        sessions.sort(key=lambda s: s.last_used_at, reverse=True)
        return sessions

    async def revoke_session(self, *, user_id: str, family_id: str) -> None:
        """Revoke one device family. Non-owner / unknown → 404 (no existence leak)."""
        owned = await self._refresh_tokens.family_belongs_to_user(
            user_id=user_id, token_family=family_id
        )
        if not owned:
            raise NotFoundError("会话不存在")
        await self._refresh_tokens.revoke_family(family_id)
        logger.info("auth.session_revoked", user_id=user_id, family_id=family_id)

    async def revoke_other_sessions(self, *, user_id: str, current_family: str) -> None:
        """Revoke every family except the caller's current one.

        Requires a ``fam`` claim on the access token so "current" is well-defined —
        legacy tokens without ``fam`` get 422 rather than silently logging the caller
        out (revoke-all) or guessing which family to keep.
        """
        await self._refresh_tokens.revoke_other_families(
            user_id, keep_family=current_family
        )
        logger.info(
            "auth.sessions_revoke_others", user_id=user_id, keep_family=current_family
        )

    async def revoke_all_sessions(self, *, user_id: str) -> None:
        """Revoke every refresh family for ``user_id`` (e.g. after MFA enrollment)."""
        await self._refresh_tokens.revoke_all_for_user(user_id)
        logger.info("auth.sessions_revoke_all", user_id=user_id)

    # --- admin account ops ---

    async def admin_reset_password(self, *, user_id: str) -> str:
        """Reset an account's password to a fresh one-off, returned once for the admin
        to hand over (重置密码). Revokes the user's refresh tokens (forces re-login on
        every device) and clears any brute-force lockout. The plaintext is never stored
        — only its hash. Raises ``NotFoundError`` for an unknown account.
        """
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError("用户不存在")
        creds = await self._credentials.get_by_user_id(user_id)
        if creds is None:  # pragma: no cover - an account always has credentials
            raise NotFoundError("用户凭据不存在")

        temp_password = generate_temp_password()
        await self._credentials.set_password(
            user_id, hash_password(temp_password), must_change=True, commit=False
        )
        # Force re-login everywhere: the old sessions must not outlive the reset.
        await self._refresh_tokens.revoke_all_for_user(user_id, commit=False)
        await self._commit()
        return temp_password

    async def admin_set_password(
        self, *, user_id: str, new_password: str, force_change: bool = True
    ) -> None:
        """Set an account's password to an admin-chosen value (设置密码).

        Revokes the user's refresh tokens (forces re-login on every device) and
        clears any brute-force lockout. The plaintext is never stored — only its
        hash. Raises ``NotFoundError`` for an unknown account, ``ValidationError``
        if the password is too short.
        """
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError("用户不存在")
        creds = await self._credentials.get_by_user_id(user_id)
        if creds is None:  # pragma: no cover - an account always has credentials
            raise NotFoundError("用户凭据不存在")
        if len(new_password) < _MIN_PASSWORD_LENGTH:
            raise ValidationError(f"密码至少需要 {_MIN_PASSWORD_LENGTH} 个字符")
        await self._credentials.set_password(
            user_id, hash_password(new_password), must_change=force_change, commit=False
        )
        await self._refresh_tokens.revoke_all_for_user(user_id, commit=False)
        await self._commit()

    async def admin_delete_account(self, *, actor_id: str, user_id: str) -> tuple[User, str | None]:
        """Admin-initiated 注销 (account deletion): soft-delete + anonymize the target
        and revoke its sessions — no password (the admin role gate + the client's
        二次确认 prove intent, and the operator can't know the target's password).

        Refuses self-deletion (``不能注销自己的账户``): the no-self-lockout guard that,
        with accounts never hard-deleted, keeps the platform at ≥1 active admin (the
        same invariant as ``AdminService.update_user``). Idempotent for an already-注销
        account (returns it untouched, no re-revoke). Returns ``(tombstone_record,
        pre-deletion avatar_key)`` so the route can GC the avatar object *after*
        anonymization has nulled the key. Cross-domain cleanup (conversations / shares
        / BYOK) is the route's, via the shared ``cleanup_account_resources``. Raises
        ``NotFoundError`` for an unknown account.
        """
        if actor_id == user_id:
            raise ValidationError("不能注销自己的账户")
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError("用户不存在")
        if user.deleted_at is not None:
            return user, None
        avatar_key = user.avatar_key
        updated = await self._users.soft_delete(user_id, commit=False)
        await self._refresh_tokens.revoke_all_for_user(user_id, commit=False)
        await self._commit()
        return (updated or user), avatar_key

    async def password_must_change(self, *, user_id: str) -> bool:
        """Whether the account must set a new password before normal use."""
        creds = await self._credentials.get_by_user_id(user_id)
        return bool(creds and creds.password_must_change)

    # --- self-service account ops (账户设置: 改密码 / 改资料 / 注销) ---

    async def change_password(
        self,
        *,
        user_id: str,
        current_password: str,
        new_password: str,
        audience: TokenAudience,
        mfa_verified: bool = False,
    ) -> TokenPair:
        """Change a logged-in user's password (修改密码), verifying the current one.

        Confirms the current password before rotating to the new one, enforces the same
        minimum length as registration, then revokes every refresh family (all other
        devices must re-login) and mints a fresh pair for the current session so the
        active device stays signed in. Raises ``AuthenticationError`` if the current
        password is wrong, ``ValidationError`` if the new password is too weak/unchanged.

        ``audience`` / ``mfa_verified`` describe the session doing the change and are
        carried onto the replacement pair — the successor must be the *same kind* of
        session, not a downgraded one. ``audience`` has no default on purpose: minting
        a ``product`` pair for an admin console session silently 403s every
        ``/v1/admin/*`` call afterwards (api/dependencies.py::_enforce_audience_bounds),
        and dropping the ``mfa`` claim 428s them back to the enrollment wizard.
        """
        creds = await self._credentials.get_by_user_id(user_id)
        if creds is None or not verify_password(current_password, creds.password_hash):
            raise AuthenticationError("当前密码不正确")
        if len(new_password) < _MIN_PASSWORD_LENGTH:
            raise ValidationError(f"密码至少需要 {_MIN_PASSWORD_LENGTH} 个字符")
        if verify_password(new_password, creds.password_hash):
            raise ValidationError("新密码不能与当前密码相同")
        await self._credentials.set_password(
            user_id, hash_password(new_password), must_change=False, commit=False
        )
        await self._refresh_tokens.revoke_all_for_user(user_id, commit=False)
        pair = await self._issue_tokens(
            user_id,
            family=new_id(),
            now=datetime.now(UTC),
            audience=audience,
            mfa_verified=mfa_verified,
            commit=False,
        )
        await self._commit()
        return pair

    async def update_profile(
        self,
        *,
        user_id: str,
        display_name: str | object = _UNSET,
        email: str | None | object = _UNSET,
        username: str | object = _UNSET,
    ) -> User:
        """Update a user's profile (个人资料编辑: 显示名 / 邮箱), returning the new row.

        Patch semantics — only the passed fields change. Display name must be
        non-empty; email must be unique (a collision with another live account → 422),
        and an explicit ``None``/blank clears it. Username claims enforce handle
        policy, occupancy, and the post-claim cooldown. Raises ``NotFoundError`` for
        an unknown user, ``ValidationError`` on invalid or conflicting fields.
        """
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError("用户不存在")

        changed: _ProfileUpdate = {}
        if display_name is not _UNSET:
            name = display_name.strip() if isinstance(display_name, str) else ""
            if not name:
                raise ValidationError("昵称不能为空")
            changed["display_name"] = name
        if email is not _UNSET:
            normalized = email.strip() if isinstance(email, str) else ""
            if not normalized:
                changed["email"] = None
                changed["email_verified_at"] = None
            else:
                normalized = normalized.lower()
                existing = await self._users.get_by_email(normalized)
                if existing is not None and existing.user_id != user_id:
                    raise ValidationError("该邮箱已被占用")
                changed["email"] = normalized
                current = (user.email or "").strip().lower()
                if normalized != current:
                    changed["email_verified_at"] = None
        if username is not _UNSET:
            if not isinstance(username, str):
                raise ValidationError("用户名无效")
            new_username = validate_username_for_claim(username)
            if new_username != user.username.lower():
                holder = await self._users.get_by_username(new_username)
                if holder is not None and holder.user_id != user_id:
                    raise ValidationError("该用户名已被占用")
                if not is_generated_handle(user.username):
                    changed_at = getattr(user, "username_changed_at", None)
                    if changed_at is not None:
                        now = datetime.now(UTC)
                        if now - changed_at < timedelta(days=USERNAME_COOLDOWN_DAYS):
                            raise ValidationError(
                                f"用户名 {USERNAME_COOLDOWN_DAYS} 天内只能修改一次"
                            )
                changed["username"] = new_username
                changed["username_changed_at"] = datetime.now(UTC)

        if not changed:
            return user
        updated = await self._users.update(user_id, **changed)
        return updated or user

    async def delete_account(self, *, user_id: str, password: str) -> None:
        """Self-service account deletion (注销账户): verify password, then soft-delete.

        Confirms the password — a destructive, irreversible action must prove intent —
        then soft-deletes + anonymizes the account (frees username/email, disables it)
        and revokes all refresh families. Cross-domain cleanup (the user's conversations
        + BYOK key) is the route's job, since those repos live outside the auth domain.
        Raises ``NotFoundError`` for an unknown user, ``AuthenticationError`` if the
        password is wrong.
        """
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError("用户不存在")
        creds = await self._credentials.get_by_user_id(user_id)
        if creds is None or not verify_password(password, creds.password_hash):
            raise AuthenticationError("密码不正确")
        await self._users.soft_delete(user_id, commit=False)
        await self._refresh_tokens.revoke_all_for_user(user_id, commit=False)
        await self._commit()

    # --- internals ---

    async def _issue_tokens(
        self,
        user_id: str,
        *,
        family: str,
        now: datetime,
        # No default: an omitted audience mints a ``product`` pair, which locks an
        # admin console session out of every ``/v1/admin/*`` route. Every caller
        # must state which session it is minting for.
        audience: TokenAudience,
        meta: SessionMeta | None = None,
        family_started_at: datetime | None = None,
        mfa_verified: bool = False,
        persist_session: bool = True,
        commit: bool = True,
    ) -> TokenPair:
        raw, token_hash = generate_refresh_token()
        started = family_started_at or now
        if persist_session:
            expires_at = now + timedelta(days=settings.jwt_refresh_token_expire_days)
        else:
            # Absolute tip expiry aligned with the ephemeral family ceiling — no
            # long sliding window that would outlive "关浏览器即丢会话".
            family_end = started + timedelta(
                hours=settings.ephemeral_refresh_family_max_hours
            )
            expires_at = min(
                now + timedelta(hours=settings.ephemeral_refresh_family_max_hours),
                family_end,
            )
        platform = meta.platform if meta else None
        await self._refresh_tokens.create(
            user_id=user_id,
            token_hash=token_hash,
            token_family=family,
            expires_at=expires_at,
            client_aud=audience,
            client_platform=platform,
            user_agent=_truncate_ua(meta.user_agent if meta else None),
            ip=meta.ip if meta else None,
            family_started_at=started,
            last_used_at=now,
            persist_session=persist_session,
            commit=commit,
        )
        return TokenPair(
            access_token=create_access_token(
                user_id,
                audience=audience,
                family=family,
                mfa_verified=mfa_verified,
            ),
            refresh_token=raw,
            persist_session=persist_session,
        )

    async def _refresh_mfa_verified(self, record: RefreshToken) -> bool:
        """Propagate MFA session proof across access-token refresh.

        Admin refresh families for enrolled users only exist after ``complete_mfa_login``
        (password-only families are revoked on MFA enrollment), so enrollment is a
        reliable proxy for "this family completed MFA".
        """
        if record.client_aud != "admin" or self._mfa is None:
            return False
        return await self._mfa.is_enrolled(record.user_id)

    def _meta_for_refresh(
        self, record: RefreshToken, request_meta: SessionMeta | None
    ) -> SessionMeta:
        """Inherit platform from the family; refresh IP/UA from the current request
        when provided so the session list reflects latest activity location."""
        raw_platform = record.client_platform
        platform: ClientPlatform | None = (
            raw_platform
            if raw_platform in ("desktop", "mobile", "admin", "web")
            else None  # type: ignore[assignment]
        )
        if request_meta is None:
            return SessionMeta(
                platform=platform,
                user_agent=record.user_agent,
                ip=record.ip,
            )
        return SessionMeta(
            platform=platform,
            user_agent=request_meta.user_agent or record.user_agent,
            ip=request_meta.ip or record.ip,
        )

    def _assert_family_within_max(self, record: RefreshToken, *, now: datetime) -> None:
        started = record.family_started_at
        if not record.persist_session:
            max_age = timedelta(hours=settings.ephemeral_refresh_family_max_hours)
        elif record.client_aud == "admin":
            max_age = timedelta(hours=settings.admin_refresh_family_max_hours)
        else:
            max_age = timedelta(days=settings.refresh_family_max_days)
        if now - started > max_age:
            raise AuthenticationError("Refresh token family expired")

    async def _register_failure(self, user_id: str, now: datetime) -> None:
        await self._credentials.increment_failure(
            user_id,
            max_attempts=_MAX_FAILED_ATTEMPTS,
            lock_until=now + _LOCKOUT_DURATION,
        )
