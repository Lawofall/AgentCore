"""Account search connections: one 开析 slot and one SearXNG slot.

Connect probes first and writes only after the probe succeeds, then selects
that engine. A failed probe leaves the previous slot untouched. A later search
failure marks the selected slot ``needs_reconnect`` until the next successful
connect. Keys are encrypted at rest. The settings view only ever sees a masked tail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.config import settings
from agentcore.core.errors import (
    KeyStorageUnavailableError,
    NotFoundError,
    ValidationError,
)
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.models import UserSearchProvider
from agentcore.db.repositories.search import UserSearchProviderRepository
from agentcore.db.repositories.users import UserRepository
from agentcore.tools.builtin.web.cleversee import CleverSeeBackend
from agentcore.tools.builtin.web.cloud_fallback import (
    get_inference_search_credentials,
    report_search_connection_failure,
)
from agentcore.tools.builtin.web.search_backend import (
    SearchBackend,
    SearXNGBackend,
    describe_search_error,
)
from agentcore.tools.builtin.web.search_choice import (
    CLEVERSEE,
    SEARXNG,
    SearchTarget,
)
from agentcore.tools.builtin.web.search_quota import SearchQuotaSnapshot, search_quota_snapshot
from agentcore.tools.builtin.web.search_secrets import (
    decrypt_search_key,
    mask_search_key,
    search_encryptor,
)

logger = get_logger(__name__)

PLATFORM = "platform"
CONNECTED = "connected"
NEEDS_RECONNECT = "needs_reconnect"
_PROBE_QUERY = "杭州"
_LABEL_MAX = 100
_URL_MAX = 500
_DETAIL_MAX = 300
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@dataclass(frozen=True)
class SearchSlotView:
    status: str
    base_url: str
    masked_key: str | None
    message: str | None = None


@dataclass(frozen=True)
class SearchConnectionView:
    selected: str
    quota: SearchQuotaSnapshot
    cleversee: SearchSlotView | None
    searxng: SearchSlotView | None


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


def _failure_detail(message: str) -> str:
    text = " ".join((message or "").split())
    return (text or "搜索失败，请重新连接")[:_DETAIL_MAX]


class SearchConnectionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = UserSearchProviderRepository(session)
        self._users = UserRepository(session)

    async def get_view(self, user_id: str) -> SearchConnectionView:
        user = await self._users.get_by_id(user_id)
        selected_id = getattr(user, "search_provider_id", None) if user is not None else None
        rows = await self._repo.list_for_user(user_id)
        by_protocol = {row.protocol: row for row in rows}
        selected = PLATFORM
        if selected_id:
            match = next((row for row in rows if row.id == selected_id), None)
            if match is not None and match.protocol in (CLEVERSEE, SEARXNG):
                selected = match.protocol
        quota = await search_quota_snapshot(user_id)
        return SearchConnectionView(
            selected=selected,
            quota=quota,
            cleversee=_slot(by_protocol.get(CLEVERSEE)),
            searxng=_slot(by_protocol.get(SEARXNG)),
        )

    async def select(self, user_id: str, engine: str) -> SearchConnectionView:
        if engine == PLATFORM:
            await self._users.set_search_provider(user_id, None)
            return await self.get_view(user_id)
        row = await self._require_slot(user_id, engine)
        await self._users.set_search_provider(user_id, row.id)
        return await self.get_view(user_id)

    async def connect_cleversee(self, user_id: str, api_key: str) -> SearchConnectionView:
        key = (api_key or "").strip()
        if not key:
            raise ValidationError("开析需要 API Key")
        self._require_encryptor()
        base_url = normalize_search_base_url(CLEVERSEE, "")
        await _probe(CLEVERSEE, base_url, key)
        await self._upsert(user_id, protocol=CLEVERSEE, base_url=base_url, api_key=key)
        return await self.get_view(user_id)

    async def connect_searxng(
        self,
        user_id: str,
        base_url: str,
        api_key: str,
    ) -> SearchConnectionView:
        url = normalize_search_base_url(SEARXNG, base_url)
        key = (api_key or "").strip()
        if key:
            self._require_encryptor()
        await _probe(SEARXNG, url, key)
        await self._upsert(user_id, protocol=SEARXNG, base_url=url, api_key=key)
        return await self.get_view(user_id)

    async def disconnect(self, user_id: str, engine: str) -> SearchConnectionView:
        row = await self._require_slot(user_id, engine)
        user = await self._users.get_by_id(user_id)
        await self._repo.delete(row.id, user_id=user_id, commit=False)
        if user is not None and user.search_provider_id == row.id:
            await self._users.set_search_provider(user_id, None, commit=False)
        await self._session.commit()
        return await self.get_view(user_id)

    async def mark_selected_failed(self, user_id: str, message: str) -> None:
        user = await self._users.get_by_id(user_id)
        provider_id = getattr(user, "search_provider_id", None) if user is not None else None
        if not provider_id:
            return
        row = await self._repo.get(provider_id, user_id=user_id)
        if row is None:
            return
        detail = _failure_detail(message)
        await self._repo.mark_needs_reconnect(
            row.id,
            user_id=user_id,
            detail=detail,
            commit=False,
        )
        await self._session.commit()
        logger.info(
            "search_connection.needs_reconnect",
            user_id=user_id,
            protocol=row.protocol,
        )

    async def _require_slot(self, user_id: str, engine: str) -> UserSearchProvider:
        if engine not in (CLEVERSEE, SEARXNG):
            raise ValidationError("不支持的搜索服务")
        row = await self._repo.get_by_protocol(user_id, engine)
        if row is None:
            raise NotFoundError("这个搜索服务还没有连接")
        return row

    async def _upsert(self, user_id: str, *, protocol: str, base_url: str, api_key: str) -> None:
        label = normalize_search_label(protocol, "")
        enc = self._encrypt(api_key) if api_key else None
        row = await self._repo.get_by_protocol(user_id, protocol)
        if row is None:
            row = await self._repo.create(
                user_id=user_id,
                label=label,
                protocol=protocol,
                base_url=base_url,
                api_key_enc=enc,
                status=CONNECTED,
                commit=False,
            )
        else:
            saved = await self._repo.save_connected(
                row.id,
                user_id=user_id,
                label=label,
                base_url=base_url,
                api_key_enc=enc,
                clear_key=not api_key,
                commit=False,
            )
            if saved is None:
                raise NotFoundError("这个搜索服务还没有连接")
            row = saved
        await self._users.set_search_provider(user_id, row.id, commit=False)
        await self._session.commit()

    def _require_encryptor(self) -> None:
        if search_encryptor() is None:
            raise KeyStorageUnavailableError("无法保存搜索密钥：服务器未配置加密主密钥")

    def _encrypt(self, plaintext: str) -> bytes:
        enc = search_encryptor()
        if enc is None:
            raise KeyStorageUnavailableError("无法保存搜索密钥：服务器未配置加密主密钥")
        return enc.encrypt(plaintext.encode())


def _slot(row: UserSearchProvider | None) -> SearchSlotView | None:
    if row is None:
        return None
    status = row.status if row.status in (CONNECTED, NEEDS_RECONNECT) else NEEDS_RECONNECT
    masked = mask_search_key(decrypt_search_key(row.api_key_enc))
    return SearchSlotView(
        status=status,
        base_url=row.base_url,
        masked_key=masked,
        message=row.detail if status == NEEDS_RECONNECT else None,
    )


async def _probe(protocol: str, base_url: str, api_key: str) -> None:
    backend: SearchBackend
    if protocol == CLEVERSEE:
        backend = CleverSeeBackend(api_key, base_url)
    else:
        backend = SearXNGBackend(base_url, api_key=api_key or None)
    try:
        await backend.search(_PROBE_QUERY, max_results=1)
    except Exception as exc:  # noqa: BLE001 — the probe outcome is the connect result
        raise ValidationError(_failure_detail(describe_search_error(exc, backend))) from exc
    finally:
        await backend.aclose()


async def note_own_search_failure(user_id: str, message: str) -> None:
    """Mark the selected own engine needs-reconnect. Never raises into the search call."""
    if not _UUID_RE.fullmatch(user_id or ""):
        return
    try:
        async with async_session_factory() as session:
            await SearchConnectionService(session).mark_selected_failed(user_id, message)
    except Exception as exc:  # noqa: BLE001 — bookkeeping must not hide the search error
        logger.warning("search_connection.note_failed", error_type=type(exc).__name__)


async def record_provider_search_failure(target: SearchTarget, user_id: str, reason: str) -> None:
    """Record an own-engine search failure on the account that owns the slot.

    The server writes the row directly. A sidecar that called the engine itself
    (keyless SearXNG) reports the same fact over inference. A proxied call is
    recorded on the server that ran it, so the sidecar does not report twice.
    """
    if target.kind != "provider" or not target.provider_id:
        return
    creds = get_inference_search_credentials()
    if creds is None:
        await note_own_search_failure(user_id, reason)
        return
    if target.proxy:
        return
    await report_search_connection_failure(creds, reason)
