"""Pending signup + email-challenge data access."""

from datetime import UTC, datetime

from sqlalchemy import case, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.core.types import new_id
from agentcore.db.models import EmailChallenge, PendingRegistration
from agentcore.db.repositories._base import commit_or_flush


class PendingRegistrationRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_email(self, email: str) -> PendingRegistration | None:
        result = await self._session.execute(
            select(PendingRegistration).where(PendingRegistration.email == email)
        )
        return result.scalar_one_or_none()

    async def get_unexpired_by_username(
        self, username: str, *, now: datetime
    ) -> PendingRegistration | None:
        result = await self._session.execute(
            select(PendingRegistration).where(
                PendingRegistration.username == username,
                PendingRegistration.expires_at > now,
            )
        )
        return result.scalar_one_or_none()

    async def delete_expired(self, *, now: datetime, commit: bool = True) -> int:
        result = await self._session.execute(
            delete(PendingRegistration).where(PendingRegistration.expires_at <= now)
        )
        await commit_or_flush(self._session, commit=commit)
        return int(getattr(result, "rowcount", 0) or 0)

    async def upsert(
        self,
        *,
        email: str,
        username: str,
        password_hash: str,
        display_name: str,
        registration_ip: str | None,
        expires_at: datetime,
        commit: bool = True,
    ) -> PendingRegistration:
        row = await self.get_by_email(email)
        if row is None:
            row = PendingRegistration(
                email=email,
                username=username,
                password_hash=password_hash,
                display_name=display_name,
                registration_ip=registration_ip,
                expires_at=expires_at,
            )
            self._session.add(row)
        else:
            row.username = username
            row.password_hash = password_hash
            row.display_name = display_name
            row.registration_ip = registration_ip
            row.expires_at = expires_at
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(row)
        return row

    async def delete_by_email(self, email: str, *, commit: bool = True) -> None:
        await self._session.execute(
            delete(PendingRegistration).where(PendingRegistration.email == email)
        )
        await commit_or_flush(self._session, commit=commit)


class EmailChallengeRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, *, purpose: str, email: str) -> EmailChallenge | None:
        result = await self._session.execute(
            select(EmailChallenge).where(
                EmailChallenge.purpose == purpose,
                EmailChallenge.email == email,
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        purpose: str,
        email: str,
        code_hash: str,
        expires_at: datetime,
        user_id: str | None = None,
        commit: bool = True,
    ) -> EmailChallenge:
        row = await self.get(purpose=purpose, email=email)
        if row is None:
            row = EmailChallenge(
                id=new_id(),
                purpose=purpose,
                email=email,
                user_id=user_id,
                code_hash=code_hash,
                expires_at=expires_at,
                attempt_count=0,
                consumed_at=None,
            )
            self._session.add(row)
        else:
            row.user_id = user_id
            row.code_hash = code_hash
            row.expires_at = expires_at
            row.attempt_count = 0
            row.consumed_at = None
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(row)
        return row

    async def record_failure(
        self, challenge_id: str, *, max_attempts: int, commit: bool = True
    ) -> int:
        """Atomically bump attempts; consume the row once the cap is reached."""
        now = datetime.now(UTC)
        new_attempts = EmailChallenge.attempt_count + 1
        result = await self._session.execute(
            update(EmailChallenge)
            .where(EmailChallenge.id == challenge_id)
            .values(
                attempt_count=new_attempts,
                consumed_at=case((new_attempts >= max_attempts, now), else_=None),
            )
            .returning(EmailChallenge.attempt_count)
        )
        await commit_or_flush(self._session, commit=commit)
        value = result.scalar_one_or_none()
        return int(value or 0)

    async def consume(self, challenge_id: str, *, commit: bool = True) -> None:
        await self._session.execute(
            update(EmailChallenge)
            .where(EmailChallenge.id == challenge_id)
            .values(consumed_at=datetime.now(UTC))
        )
        await commit_or_flush(self._session, commit=commit)
