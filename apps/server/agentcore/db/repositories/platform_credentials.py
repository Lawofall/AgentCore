"""Operator platform-credential pool (encrypted upstream keys)."""

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.core.types import new_id
from agentcore.db.models.platform import PlatformCredential
from agentcore.db.repositories._base import _UNSET, commit_or_flush


class PlatformCredentialRepository:
    """CRUD for ``platform_credentials``. Encryption is the service layer's job."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> Sequence[PlatformCredential]:
        """Oldest-first (stable fill-first order)."""
        result = await self._session.execute(
            select(PlatformCredential).order_by(
                PlatformCredential.created_at.asc(),
                PlatformCredential.id.asc(),
            )
        )
        return result.scalars().all()

    async def get(self, credential_id: str) -> PlatformCredential | None:
        result = await self._session.execute(
            select(PlatformCredential).where(PlatformCredential.id == credential_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        label: str,
        api_key_enc: bytes,
        base_url: str,
        subscription_day: int,
        enabled: bool = True,
        tool_surface_limits: dict | None = None,
        credential_id: str | None = None,
        commit: bool = True,
    ) -> PlatformCredential:
        row = PlatformCredential(
            id=credential_id or new_id(),
            label=(label or "").strip(),
            api_key_enc=api_key_enc,
            base_url=base_url.strip(),
            subscription_day=subscription_day,
            enabled=enabled,
            tool_surface_limits=dict(tool_surface_limits or {}),
        )
        self._session.add(row)
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(row)
        return row

    async def update(
        self,
        credential_id: str,
        *,
        label: str | object = _UNSET,
        api_key_enc: bytes | object = _UNSET,
        base_url: str | object = _UNSET,
        subscription_day: int | object = _UNSET,
        enabled: bool | object = _UNSET,
        tool_surface_limits: dict | object = _UNSET,
        commit: bool = True,
    ) -> PlatformCredential | None:
        row = await self.get(credential_id)
        if row is None:
            return None
        if label is not _UNSET:
            row.label = str(label or "").strip()
        if api_key_enc is not _UNSET:
            row.api_key_enc = api_key_enc  # type: ignore[assignment]
        if base_url is not _UNSET:
            row.base_url = str(base_url).strip()
        if subscription_day is not _UNSET:
            row.subscription_day = int(subscription_day)  # type: ignore[arg-type]
        if enabled is not _UNSET:
            row.enabled = bool(enabled)
        if tool_surface_limits is not _UNSET:
            row.tool_surface_limits = (
                dict(tool_surface_limits) if isinstance(tool_surface_limits, dict) else {}
            )
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(row)
        return row

    async def delete(self, credential_id: str, *, commit: bool = True) -> bool:
        result = await self._session.execute(
            delete(PlatformCredential).where(PlatformCredential.id == credential_id)
        )
        await commit_or_flush(self._session, commit=commit)
        return bool(int(getattr(result, "rowcount", 0) or 0))
