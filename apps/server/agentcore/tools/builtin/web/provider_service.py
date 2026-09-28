"""Account search providers: add, edit, select, delete, and connectivity-test.

Keys are encrypted at rest. The settings view only ever sees a masked tail.
A connectivity probe does not consume the platform search-count quota.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.config import settings
from agentcore.core.errors import (
    KeyStorageUnavailableError,
    NotFoundError,
    ValidationError,
)
from agentcore.db.models import UserSearchProvider
from agentcore.db.repositories._base import _UNSET
from agentcore.db.repositories.search import UserSearchProviderRepository
from agentcore.db.repositories.users import UserRepository
from agentcore.tools.builtin.web.cleversee import CleverSeeBackend
from agentcore.tools.builtin.web.search_backend import (
    SearchBackend,
    SearXNGBackend,
    describe_search_error,
)
from agentcore.tools.builtin.web.search_choice import CLEVERSEE, SEARXNG
from agentcore.tools.builtin.web.search_quota import SearchQuotaSnapshot, search_quota_snapshot
from agentcore.tools.builtin.web.search_secrets import (
    decrypt_search_key,
    mask_search_key,
    search_encryptor,
)

MAX_SEARCH_PROVIDERS = 8
_PROBE_QUERY = "ping"
_LABEL_MAX = 100
_URL_MAX = 500


@dataclass(frozen=True)
class SearchProviderView:
    id: str
    label: str
    protocol: str
    base_url: str
    status: str
    masked_key: str | None
    message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class SearchProvidersView:
    selected_provider_id: str | None
    quota: SearchQuotaSnapshot
    providers: list[SearchProviderView]


def normalize_search_base_url(protocol: str, base_url: str) -> str:
    raw = (base_url or "").strip().rstrip("/")
    if protocol == CLEVERSEE and not raw:
        raw = settings.cleversee_base_url.rstrip("/")
    if len(raw) > _URL_MAX:
        raise ValidationError("搜索地址过长")
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValidationError("搜索地址需为 http 或 https")
    return raw


def normalize_search_label(protocol: str, label: str) -> str:
    text = (label or "").strip()
    if not text:
        text = "开析" if protocol == CLEVERSEE else "SearXNG"
    if len(text) > _LABEL_MAX:
        raise ValidationError("名称过长")
    return text


def _require_protocol(protocol: str) -> str:
    if protocol not in (SEARXNG, CLEVERSEE):
        raise ValidationError("不支持的搜索协议")
    return protocol


class SearchProviderService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = UserSearchProviderRepository(session)
        self._users = UserRepository(session)

    async def list_providers(self, user_id: str) -> SearchProvidersView:
        user = await self._users.get_by_id(user_id)
        selected = getattr(user, "search_provider_id", None) if user is not None else None
        rows = await self._repo.list_for_user(user_id)
        if selected and all(row.id != selected for row in rows):
            selected = None
        quota = await search_quota_snapshot(user_id)
        return SearchProvidersView(
            selected_provider_id=selected,
            quota=quota,
            providers=[self._view(row) for row in rows],
        )

    async def create_provider(
        self,
        user_id: str,
        *,
        protocol: str,
        label: str,
        base_url: str,
        api_key: str,
    ) -> SearchProviderView:
        protocol = _require_protocol(protocol)
        if await self._repo.count_for_user(user_id) >= MAX_SEARCH_PROVIDERS:
            raise ValidationError(f"最多保存 {MAX_SEARCH_PROVIDERS} 个搜索服务")
        key = (api_key or "").strip()
        if protocol == CLEVERSEE and not key:
            raise ValidationError("开析需要 API key")
        row = await self._repo.create(
            user_id=user_id,
            label=normalize_search_label(protocol, label),
            protocol=protocol,
            base_url=normalize_search_base_url(protocol, base_url),
            api_key_enc=self._encrypt(key) if key else None,
        )
        return self._view(row, plaintext_key=key)

    async def update_provider(
        self,
        user_id: str,
        provider_id: str,
        *,
        label: str | None,
        base_url: str | None,
        api_key: str | None,
        fields_set: set[str],
    ) -> SearchProviderView:
        row = await self._repo.get(provider_id, user_id=user_id)
        if row is None:
            raise NotFoundError("搜索服务不存在")
        label_value: str | object = (
            normalize_search_label(row.protocol, label or "") if "label" in fields_set else _UNSET
        )
        url_value: str | object = (
            normalize_search_base_url(row.protocol, base_url or "")
            if "base_url" in fields_set
            else _UNSET
        )
        key_text = (api_key or "").strip() if "api_key" in fields_set else ""
        key_value: bytes | None | object = self._encrypt(key_text) if key_text else _UNSET
        # Empty key means keep. A CleverSee row that still has no key cannot run.
        if (
            row.protocol == CLEVERSEE
            and "api_key" in fields_set
            and not key_text
            and not row.api_key_enc
        ):
            raise ValidationError("开析需要 API key")
        updated = await self._repo.update(
            provider_id,
            user_id=user_id,
            label=label_value,
            base_url=url_value,
            api_key_enc=key_value,
        )
        if updated is None:
            raise NotFoundError("搜索服务不存在")
        return self._view(updated, plaintext_key=key_text)

    async def delete_provider(self, user_id: str, provider_id: str) -> None:
        row = await self._repo.get(provider_id, user_id=user_id)
        if row is None:
            raise NotFoundError("搜索服务不存在")
        user = await self._users.get_by_id(user_id)
        await self._repo.delete(provider_id, user_id=user_id, commit=False)
        if user is not None and user.search_provider_id == provider_id:
            await self._users.set_search_provider(user_id, None, commit=False)
        await self._session.commit()

    async def select_provider(self, user_id: str, provider_id: str | None) -> None:
        if provider_id:
            row = await self._repo.get(provider_id, user_id=user_id)
            if row is None:
                raise NotFoundError("搜索服务不存在")
        await self._users.set_search_provider(user_id, provider_id or None)

    async def test_provider(self, user_id: str, provider_id: str) -> SearchProviderView:
        row = await self._repo.get(provider_id, user_id=user_id)
        if row is None:
            raise NotFoundError("搜索服务不存在")
        key = decrypt_search_key(row.api_key_enc)
        if row.protocol == CLEVERSEE and not key:
            raise ValidationError("开析需要 API key")
        backend: SearchBackend
        if row.protocol == CLEVERSEE:
            backend = CleverSeeBackend(key, row.base_url)
        else:
            backend = SearXNGBackend(row.base_url, api_key=key or None)
        message: str | None = None
        status = "error"
        try:
            results = await backend.search(_PROBE_QUERY, max_results=1)
        except Exception as exc:  # noqa: BLE001 — persist the probe outcome, don't crash settings
            message = describe_search_error(exc, backend)[:300]
            status = "error"
        else:
            status = "active"
            message = None if results else "已连通，这次没有结果。"
        finally:
            await backend.aclose()
        await self._repo.set_status(provider_id, user_id=user_id, status=status)
        fresh = await self._repo.get(provider_id, user_id=user_id)
        return self._view(fresh or row, plaintext_key=key, message=message, status=status)

    def _encrypt(self, plaintext: str) -> bytes:
        enc = search_encryptor()
        if enc is None:
            raise KeyStorageUnavailableError("无法保存搜索密钥：服务器未配置加密主密钥")
        return enc.encrypt(plaintext.encode())

    def _view(
        self,
        row: UserSearchProvider,
        *,
        plaintext_key: str = "",
        message: str | None = None,
        status: str | None = None,
    ) -> SearchProviderView:
        masked = mask_search_key(plaintext_key) if plaintext_key else None
        if masked is None and row.api_key_enc:
            masked = mask_search_key(decrypt_search_key(row.api_key_enc))
        return SearchProviderView(
            id=row.id,
            label=row.label,
            protocol=row.protocol,
            base_url=row.base_url,
            status=status or row.status,
            masked_key=masked,
            message=message,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

