"""Admin write surface for the platform credential pool.

Read/pick lives in ``llm/platform_pool.py`` (in-memory snapshot, db-free) +
``llm/resolve.py``. This module encrypts keys with the existing BYOK
``KeyEncryptor``, reloads the process snapshot after every mutation, and
hosts boot/refresh (opens a session). Plaintext never appears on the view.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.config import settings
from agentcore.core.errors import KeyStorageUnavailableError, NotFoundError, ValidationError
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.models.platform import PlatformCredential
from agentcore.db.repositories.platform_credentials import PlatformCredentialRepository
from agentcore.llm.credentials import require_http_header_safe_api_key
from agentcore.llm.platform_pool import (
    PlatformPoolMember,
    ToolSurfaceLimits,
    replace_platform_pool_snapshot,
)
from agentcore.llm.tool_surface import parse_tool_surface_limits, tool_surface_limits_as_dict
from agentcore.security.keys import KeyEncryptor

logger = get_logger(__name__)

_GO_BASE_URL_HINT = "https://opencode.ai/zen/go/v1"

# Other replicas pick up admin disable/add within this window. Local CRUD
# reloads immediately and does not wait.
PLATFORM_POOL_REFRESH_SECONDS = 10.0
_last_reload_sig: tuple | None = None


def _reload_signature(
    members: tuple[PlatformPoolMember, ...] | list[PlatformPoolMember], rows: int
) -> tuple:
    return (
        rows,
        tuple(
            (
                m.id,
                m.enabled,
                m.base_url,
                m.subscription_day,
                hashlib.sha256(m.api_key.encode()).hexdigest()[:16],
            )
            for m in members
        ),
    )


def _master_key_encryptor() -> KeyEncryptor | None:
    if not settings.encryption_key:
        return None
    try:
        return KeyEncryptor(settings.encryption_key)
    except ValueError:
        logger.error("byok.key_malformed")
        return None


def _member_from_row(
    row: PlatformCredential, *, enc: KeyEncryptor | None
) -> PlatformPoolMember | None:
    if enc is None:
        logger.warning("platform_pool.decrypt_failed", credential_id=row.id, error="no_encryptor")
        return None
    try:
        api_key = enc.decrypt(row.api_key_enc).decode()
    except Exception as e:  # noqa: BLE001 — corrupt cipher / rotated master key
        logger.warning("platform_pool.decrypt_failed", credential_id=row.id, error=str(e))
        return None
    return PlatformPoolMember(
        id=row.id,
        label=row.label or "",
        api_key=api_key,
        base_url=row.base_url,
        subscription_day=int(row.subscription_day),
        enabled=bool(row.enabled),
        tool_surface_limits=_limits_from_row(row),
    )


async def reload_platform_credential_pool(session: AsyncSession) -> int:
    """Decrypt every row into the process snapshot. Returns loaded member count."""
    rows = await PlatformCredentialRepository(session).list_all()
    enc = _master_key_encryptor()
    members: list[PlatformPoolMember] = []
    for row in rows:
        member = _member_from_row(row, enc=enc)
        if member is not None:
            members.append(member)
    replace_platform_pool_snapshot(tuple(members))
    sig = _reload_signature(members, len(rows))
    global _last_reload_sig
    unchanged = sig == _last_reload_sig
    _last_reload_sig = sig
    # Two static logger.* calls so the event-registry scanner still sees this name.
    if unchanged:
        logger.debug("platform_pool.reloaded", members=len(members), rows=len(rows))
    else:
        logger.info("platform_pool.reloaded", members=len(members), rows=len(rows))
    return len(members)


async def reload_platform_credential_pool_from_factory() -> int:
    """Open a primary session and reload. Best-effort: failures leave the last snapshot."""
    try:
        async with async_session_factory() as session:
            return await reload_platform_credential_pool(session)
    except Exception as e:  # noqa: BLE001 — boot / refresh must not take down the process
        logger.warning("platform_pool.reload_failed", error=str(e))
        return 0


async def platform_credential_pool_refresh_loop() -> None:
    """Periodic snapshot refresh so other replicas see admin edits without restart."""
    while True:
        await asyncio.sleep(PLATFORM_POOL_REFRESH_SECONDS)
        try:
            await reload_platform_credential_pool_from_factory()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — a failed refresh must not kill the loop
            logger.warning("platform_pool.reload_failed", error=str(e))


@dataclass(frozen=True)
class PlatformCredentialView:
    """Admin view of one pool member — never the plaintext key."""

    id: str
    label: str
    base_url: str
    subscription_day: int
    enabled: bool
    masked_key: str | None
    created_at: datetime | None
    updated_at: datetime | None
    tool_surface_limits: ToolSurfaceLimits = field(default_factory=ToolSurfaceLimits)


def _mask_key(api_key: str) -> str:
    if len(api_key) <= 4:
        return "••••"
    return f"••••{api_key[-4:]}"


def _mask_key_ciphertext(enc: KeyEncryptor | None, api_key_enc: bytes) -> str | None:
    if enc is None or not api_key_enc:
        return None
    try:
        plaintext = enc.decrypt(api_key_enc).decode()
    except Exception:  # noqa: BLE001
        return None
    return _mask_key(plaintext)


def _limits_from_row(row: PlatformCredential) -> ToolSurfaceLimits:
    try:
        return parse_tool_surface_limits(row.tool_surface_limits)
    except ValidationError:
        logger.warning("platform_pool.tool_surface_limits_invalid", credential_id=row.id)
        return ToolSurfaceLimits()


class _CredentialUpdate(TypedDict, total=False):
    """Partial kwargs for :meth:`PlatformCredentialRepository.update` (never ``commit``)."""

    label: str
    api_key_enc: bytes
    base_url: str
    subscription_day: int
    enabled: bool
    tool_surface_limits: dict


class PlatformCredentialService:
    """Add / edit / disable / remove platform-pool members."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = PlatformCredentialRepository(session)

    def _encryptor(self) -> KeyEncryptor | None:
        return _master_key_encryptor()

    def _require_encryptor(self) -> KeyEncryptor:
        enc = self._encryptor()
        if enc is None:
            raise KeyStorageUnavailableError(
                "服务端未配置加密主密钥，暂时无法保存平台 API Key，请联系管理员"
            )
        return enc

    def _view(self, row: PlatformCredential, *, enc: KeyEncryptor | None) -> PlatformCredentialView:
        return PlatformCredentialView(
            id=row.id,
            label=row.label or "",
            base_url=row.base_url,
            subscription_day=int(row.subscription_day),
            enabled=bool(row.enabled),
            masked_key=_mask_key_ciphertext(enc, row.api_key_enc),
            created_at=row.created_at,
            updated_at=row.updated_at,
            tool_surface_limits=_limits_from_row(row),
        )

    async def list_credentials(self) -> list[PlatformCredentialView]:
        enc = self._encryptor()
        rows = await self._repo.list_all()
        views = [self._view(row, enc=enc) for row in rows]
        await reload_platform_credential_pool(self._session)
        return views

    async def get_credential(self, credential_id: str) -> PlatformCredentialView:
        existing = await self._repo.get(credential_id)
        if existing is None:
            raise NotFoundError("平台账号不存在")
        return self._view(existing, enc=self._encryptor())

    async def create_credential(
        self,
        *,
        label: str,
        api_key: str,
        base_url: str,
        subscription_day: int,
        enabled: bool = True,
        tool_surface_limits: object | None = None,
    ) -> PlatformCredentialView:
        label_s = (label or "").strip()
        if not label_s:
            raise ValidationError("账号名称不能为空")
        base = (base_url or "").strip()
        if not base:
            raise ValidationError(
                "Base URL 不能为空（须与该 Key 绑定，勿复用全局 PLATFORM_BASE_URL）"
            )
        if not 1 <= int(subscription_day) <= 31:
            raise ValidationError("订阅日须为 1–31")
        limits = parse_tool_surface_limits(tool_surface_limits)
        safe_key = require_http_header_safe_api_key(api_key)
        enc = self._require_encryptor()
        row = await self._repo.create(
            label=label_s,
            api_key_enc=enc.encrypt(safe_key.encode()),
            base_url=base,
            subscription_day=int(subscription_day),
            enabled=enabled,
            tool_surface_limits=tool_surface_limits_as_dict(limits),
        )
        await reload_platform_credential_pool(self._session)
        return self._view(row, enc=enc)

    async def update_credential(
        self,
        credential_id: str,
        *,
        fields_set: set[str],
        label: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        subscription_day: int | None = None,
        enabled: bool | None = None,
        tool_surface_limits: object | None = None,
    ) -> PlatformCredentialView:
        existing = await self._repo.get(credential_id)
        if existing is None:
            raise NotFoundError("平台账号不存在")

        kwargs: _CredentialUpdate = {}
        if "label" in fields_set:
            label_s = (label or "").strip()
            if not label_s:
                raise ValidationError("账号名称不能为空")
            kwargs["label"] = label_s
        if "api_key" in fields_set:
            key_s = (api_key or "").strip()
            if key_s:
                enc = self._require_encryptor()
                safe_key = require_http_header_safe_api_key(key_s)
                kwargs["api_key_enc"] = enc.encrypt(safe_key.encode())
        if "base_url" in fields_set:
            base = (base_url or "").strip()
            if not base:
                raise ValidationError("Base URL 不能为空（须与该 Key 绑定）")
            kwargs["base_url"] = base
        if "subscription_day" in fields_set:
            day = int(subscription_day) if subscription_day is not None else 0
            if not 1 <= day <= 31:
                raise ValidationError("订阅日须为 1–31")
            kwargs["subscription_day"] = day
        if "enabled" in fields_set and enabled is not None:
            kwargs["enabled"] = bool(enabled)
        if "tool_surface_limits" in fields_set:
            limits = parse_tool_surface_limits(tool_surface_limits)
            kwargs["tool_surface_limits"] = tool_surface_limits_as_dict(limits)

        row = await self._repo.update(credential_id, **kwargs)
        assert row is not None
        await reload_platform_credential_pool(self._session)
        from agentcore.llm.platform_pool_scheduler import clear_account_runtime_state

        clear_account_runtime_state(credential_id)
        return self._view(row, enc=self._encryptor())

    async def delete_credential(self, credential_id: str) -> None:
        removed = await self._repo.delete(credential_id)
        if not removed:
            raise NotFoundError("平台账号不存在")
        await reload_platform_credential_pool(self._session)
        from agentcore.llm.platform_pool_scheduler import clear_account_runtime_state

        clear_account_runtime_state(credential_id)
