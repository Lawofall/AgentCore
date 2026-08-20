"""Email verification + password-reset flows (verify-then-create)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.auth.email_codes import (
    generate_email_code,
    hash_email_code,
    normalize_email,
    verify_email_code,
)
from agentcore.auth.email_rate_limit import enforce_email_send_rate_limit
from agentcore.auth.usernames import ALLOCATE_ATTEMPTS, generate_username_handle
from agentcore.config import settings
from agentcore.core.errors import AuthorizationError, ValidationError
from agentcore.core.logging import get_logger
from agentcore.db.models import EmailChallenge, User
from agentcore.db.repositories import (
    CredentialsRepository,
    EmailChallengeRepository,
    PendingRegistrationRepository,
    RefreshTokenRepository,
    UserRepository,
)
from agentcore.mail.sender import (
    PURPOSE_EMAIL_VERIFY,
    PURPOSE_PASSWORD_RESET,
    PURPOSE_REGISTER,
    EmailSender,
    EmailSendError,
)
from agentcore.security import hash_password

logger = get_logger(__name__)

_MIN_PASSWORD_LENGTH = 8
_INVALID_CODE = "验证码无效或已过期"
_TOO_MANY_TRIES = "验证码错误次数过多，请重新获取"
_SEND_FAILED = "验证码发送失败，请稍后重试"


class EmailAuthService:
    def __init__(
        self,
        *,
        users: UserRepository,
        credentials: CredentialsRepository,
        refresh_tokens: RefreshTokenRepository,
        pending: PendingRegistrationRepository,
        challenges: EmailChallengeRepository,
        mailer: EmailSender,
        session: AsyncSession | None = None,
    ) -> None:
        self._users = users
        self._credentials = credentials
        self._refresh_tokens = refresh_tokens
        self._pending = pending
        self._challenges = challenges
        self._mailer = mailer
        self._session = session

    async def _commit(self) -> None:
        if self._session is not None:
            await self._session.commit()

    async def _rollback(self) -> None:
        if self._session is not None:
            await self._session.rollback()

    def _ttl(self) -> timedelta:
        return timedelta(seconds=settings.email_code_ttl_seconds)

    async def _deliver_code(
        self, *, to: str, purpose: str, code: str, raise_on_failure: bool = True
    ) -> None:
        """Hand off a code. Transport failure is never a 500.

        Register / email-verify raise 422 so the user can retry. Password-forgot
        swallows (``raise_on_failure=False``) so a downed SMTP cannot distinguish
        a live inbox from a missing one — that endpoint is always 202.
        """
        try:
            await self._mailer.send_verification_code(
                to=to,
                purpose=purpose,
                code=code,
                ttl_seconds=settings.email_code_ttl_seconds,
            )
        except EmailSendError:
            if raise_on_failure:
                raise ValidationError(_SEND_FAILED) from None
            logger.warning("email.send_failed", purpose=purpose)

    async def _allocate_username(self, *, now: datetime) -> str:
        for _ in range(ALLOCATE_ATTEMPTS):
            candidate = generate_username_handle()
            if await self._users.get_by_username(candidate) is not None:
                continue
            taken = await self._pending.get_unexpired_by_username(candidate, now=now)
            if taken is not None:
                continue
            return candidate
        raise ValidationError("注册繁忙，请稍后重试")

    async def _reuse_or_allocate_username(self, *, email: str, now: datetime) -> str:
        existing = await self._pending.get_by_email(email)
        if (
            existing is not None
            and existing.expires_at > now
            and await self._users.get_by_username(existing.username) is None
        ):
            return existing.username
        return await self._allocate_username(now=now)

    async def start_registration(
        self,
        *,
        password: str,
        email: str,
        registration_ip: str | None = None,
        client_ip: str | None = None,
    ) -> int:
        if not settings.registration_open:
            raise AuthorizationError("注册已关闭")
        if len(password) < _MIN_PASSWORD_LENGTH:
            raise ValidationError(f"密码至少需要 {_MIN_PASSWORD_LENGTH} 个字符")
        email = normalize_email(email)
        now = datetime.now(UTC)
        await self._pending.delete_expired(now=now, commit=False)

        holder = await self._users.get_by_email(email)
        if holder is not None:
            raise ValidationError("该邮箱已被占用")

        enforce_email_send_rate_limit(email=email, ip=client_ip or "unknown")

        expires_at = now + self._ttl()
        code = generate_email_code()
        password_hash = hash_password(password)
        username = await self._reuse_or_allocate_username(email=email, now=now)
        for attempt in range(2):
            await self._pending.upsert(
                email=email,
                username=username,
                password_hash=password_hash,
                display_name="",
                registration_ip=registration_ip,
                expires_at=expires_at,
                commit=False,
            )
            await self._challenges.upsert(
                purpose=PURPOSE_REGISTER,
                email=email,
                code_hash=hash_email_code(code),
                expires_at=expires_at,
                commit=False,
            )
            try:
                await self._commit()
                break
            except IntegrityError:
                await self._rollback()
                if attempt == 0:
                    username = await self._allocate_username(now=now)
                    continue
                raise ValidationError("注册繁忙，请稍后重试") from None
        await self._deliver_code(to=email, purpose=PURPOSE_REGISTER, code=code)
        logger.info("auth.register_code_sent", email_domain=email.rsplit("@", 1)[-1])
        return settings.email_code_ttl_seconds

    async def verify_registration(
        self, *, email: str, code: str, display_name: str | None = None
    ) -> User:
        email = normalize_email(email)
        challenge = await self._assert_code(
            purpose=PURPOSE_REGISTER, email=email, code=code
        )
        pending = await self._pending.get_by_email(email)
        now = datetime.now(UTC)
        if pending is None or pending.expires_at <= now:
            raise ValidationError(_INVALID_CODE)
        username = pending.username
        if await self._users.get_by_username(username) is not None:
            username = await self._allocate_username(now=now)
        nickname = (display_name or "").strip()
        final_display_name = nickname if nickname else username
        try:
            user = await self._users.create(
                username=username,
                display_name=final_display_name,
                email=email,
                email_verified_at=now,
                registration_ip=pending.registration_ip,
                commit=False,
            )
            await self._credentials.create(
                user_id=user.user_id,
                password_hash=pending.password_hash,
                commit=False,
            )
            await self._pending.delete_by_email(email, commit=False)
            if challenge is not None:
                await self._challenges.consume(challenge.id, commit=False)
            await self._commit()
        except IntegrityError:
            await self._rollback()
            raise ValidationError("用户名或邮箱已被占用") from None
        logger.info("auth.register", user_id=user.user_id)
        return user

    async def start_password_reset(self, *, email: str, client_ip: str | None = None) -> int:
        try:
            email = normalize_email(email)
        except ValidationError:
            # Same 202 as a well-formed unknown inbox — do not leak shape vs existence.
            return settings.email_code_ttl_seconds
        enforce_email_send_rate_limit(email=email, ip=client_ip or "unknown")
        user = await self._users.get_by_email(email)
        if user is not None and user.deleted_at is None and user.email:
            code = generate_email_code()
            expires_at = datetime.now(UTC) + self._ttl()
            await self._challenges.upsert(
                purpose=PURPOSE_PASSWORD_RESET,
                email=email,
                user_id=user.user_id,
                code_hash=hash_email_code(code),
                expires_at=expires_at,
                commit=True,
            )
            await self._deliver_code(
                to=email,
                purpose=PURPOSE_PASSWORD_RESET,
                code=code,
                raise_on_failure=False,
            )
        logger.info("auth.password_reset_requested")
        return settings.email_code_ttl_seconds

    async def reset_password(self, *, email: str, code: str, new_password: str) -> None:
        if len(new_password) < _MIN_PASSWORD_LENGTH:
            raise ValidationError(f"密码至少需要 {_MIN_PASSWORD_LENGTH} 个字符")
        email = normalize_email(email)
        challenge = await self._assert_code(
            purpose=PURPOSE_PASSWORD_RESET, email=email, code=code
        )
        user = await self._users.get_by_email(email)
        if (
            user is None
            or user.deleted_at is not None
            or challenge.user_id not in (None, user.user_id)
        ):
            raise ValidationError(_INVALID_CODE)
        now = datetime.now(UTC)
        await self._credentials.set_password(
            user.user_id, hash_password(new_password), must_change=False, commit=False
        )
        await self._refresh_tokens.revoke_all_for_user(user.user_id, commit=False)
        await self._users.update(
            user.user_id, email_verified_at=now, commit=False
        )
        await self._challenges.consume(challenge.id, commit=False)
        await self._commit()
        logger.info("auth.password_reset", user_id=user.user_id)

    async def start_email_verification(
        self,
        *,
        user_id: str,
        email: str,
        client_ip: str | None = None,
    ) -> int:
        email = normalize_email(email)
        holder = await self._users.get_by_email(email)
        if holder is not None and holder.user_id != user_id:
            raise ValidationError("该邮箱已被占用")
        enforce_email_send_rate_limit(email=email, ip=client_ip or "unknown")
        code = generate_email_code()
        expires_at = datetime.now(UTC) + self._ttl()
        await self._challenges.upsert(
            purpose=PURPOSE_EMAIL_VERIFY,
            email=email,
            user_id=user_id,
            code_hash=hash_email_code(code),
            expires_at=expires_at,
            commit=True,
        )
        await self._deliver_code(to=email, purpose=PURPOSE_EMAIL_VERIFY, code=code)
        logger.info("auth.email_code_sent", user_id=user_id)
        return settings.email_code_ttl_seconds

    async def verify_email(self, *, user_id: str, email: str, code: str) -> User:
        email = normalize_email(email)
        challenge = await self._assert_code(
            purpose=PURPOSE_EMAIL_VERIFY, email=email, code=code
        )
        if challenge.user_id != user_id:
            raise ValidationError(_INVALID_CODE)
        holder = await self._users.get_by_email(email)
        if holder is not None and holder.user_id != user_id:
            raise ValidationError("该邮箱已被占用")
        now = datetime.now(UTC)
        try:
            updated = await self._users.update(
                user_id,
                email=email,
                email_verified_at=now,
                commit=False,
            )
            await self._challenges.consume(challenge.id, commit=False)
            await self._commit()
        except IntegrityError:
            await self._rollback()
            raise ValidationError("该邮箱已被占用") from None
        if updated is None:
            raise ValidationError(_INVALID_CODE)
        logger.info("auth.email_verified", user_id=user_id)
        return updated

    async def _assert_code(
        self, *, purpose: str, email: str, code: str
    ) -> EmailChallenge:
        challenge = await self._challenges.get(purpose=purpose, email=email)
        now = datetime.now(UTC)
        live = (
            challenge is not None
            and challenge.consumed_at is None
            and challenge.expires_at > now
            and challenge.attempt_count < settings.email_code_max_attempts
        )
        stored = challenge.code_hash if challenge is not None and live else None
        ok = verify_email_code(code.strip(), stored)
        if not live or not ok:
            if live and challenge is not None:
                attempts = await self._challenges.record_failure(
                    challenge.id,
                    max_attempts=settings.email_code_max_attempts,
                    commit=True,
                )
                if attempts >= settings.email_code_max_attempts:
                    raise ValidationError(_TOO_MANY_TRIES)
            raise ValidationError(_INVALID_CODE)
        assert challenge is not None
        return challenge
