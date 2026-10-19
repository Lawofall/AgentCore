"""Account search connections: one DeepSeek slot.

Connect probes first and writes only after the probe succeeds, then selects
that engine. The slot uses the official DeepSeek BYOK service key. A failed
probe leaves the previous slot untouched. A later search failure marks the
selected slot ``needs_reconnect`` until the next successful connect. The
settings view shows
「来自已连接的服务商」.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.core.errors import NotFoundError, ValidationError
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.models import UserSearchProvider
from agentcore.db.repositories.search import UserSearchProviderRepository
from agentcore.db.repositories.users import UserRepository
from agentcore.llm.provider_service import (
    load_official_deepseek_provider,
    official_deepseek_provider_label,
)
from agentcore.search.choice import DEEPSEEK, SearchTarget
from agentcore.tools.builtin.web.cloud_fallback import get_inference_search_credentials
from agentcore.tools.builtin.web.deepseek import (
    DEEPSEEK_SEARCH_BASE_URL,
    DeepSeekBackend,
)
from agentcore.tools.builtin.web.search_backend import describe_search_error
from agentcore.tools.builtin.web.search_quota import SearchQuotaSnapshot, search_quota_snapshot

logger = get_logger(__name__)

PLATFORM = "platform"
CONNECTED = "connected"
NEEDS_RECONNECT = "needs_reconnect"
PROVIDER_SOURCE = "provider"
_PROBE_QUERY = "杭州"
_LABEL_MAX = 100
_DETAIL_MAX = 300
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@dataclass(frozen=True)
class SearchSlotView:
    status: str
    base_url: str
    masked_key: str | None
    source: str
    message: str | None = None


@dataclass(frozen=True)
class SearchConnectionView:
    selected: str
    quota: SearchQuotaSnapshot
    deepseek: SearchSlotView | None
    official_provider_label: str | None = None


def normalize_search_base_url(protocol: str, base_url: str) -> str:
    del base_url
    if protocol != DEEPSEEK:
        raise ValidationError("不支持的搜索服务")
    return DEEPSEEK_SEARCH_BASE_URL


def normalize_search_label(protocol: str, label: str) -> str:
    text = (label or "").strip()
    if not text:
        text = "DeepSeek"
    if protocol != DEEPSEEK:
        raise ValidationError("不支持的搜索服务")
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
            if match is not None and match.protocol == DEEPSEEK:
                selected = DEEPSEEK
        quota = await search_quota_snapshot(user_id)
        provider_label = await official_deepseek_provider_label(user_id)
        return SearchConnectionView(
            selected=selected,
            quota=quota,
            deepseek=_slot(
                by_protocol.get(DEEPSEEK),
                provider_available=provider_label is not None,
            ),
            official_provider_label=provider_label,
        )

    async def select(self, user_id: str, engine: str) -> SearchConnectionView:
        if engine == PLATFORM:
            await self._users.set_search_provider(user_id, None)
            return await self.get_view(user_id)
        row = await self._require_slot(user_id, engine)
        await self._users.set_search_provider(user_id, row.id)
        return await self.get_view(user_id)

    async def connect_deepseek(self, user_id: str) -> SearchConnectionView:
        found = await load_official_deepseek_provider(user_id)
        if found is None:
            raise ValidationError(
                "还没有连接官方 DeepSeek 服务商。请先在设置 · 服务商连接"
            )
        base_url = normalize_search_base_url(DEEPSEEK, "")
        await _probe(base_url, found.api_key)
        await self._upsert(user_id, base_url=base_url)
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
        if engine != DEEPSEEK:
            raise ValidationError("不支持的搜索服务")
        row = await self._repo.get_by_protocol(user_id, engine)
        if row is None:
            raise NotFoundError("这个搜索服务还没有连接")
        return row

    async def _upsert(self, user_id: str, *, base_url: str) -> None:
        label = normalize_search_label(DEEPSEEK, "")
        row = await self._repo.get_by_protocol(user_id, DEEPSEEK)
        if row is None:
            row = await self._repo.create(
                user_id=user_id,
                label=label,
                protocol=DEEPSEEK,
                base_url=base_url,
                api_key_enc=None,
                status=CONNECTED,
                source=PROVIDER_SOURCE,
                commit=False,
            )
        else:
            saved = await self._repo.save_connected(
                row.id,
                user_id=user_id,
                label=label,
                base_url=base_url,
                api_key_enc=None,
                clear_key=True,
                source=PROVIDER_SOURCE,
                commit=False,
            )
            if saved is None:
                raise NotFoundError("这个搜索服务还没有连接")
            row = saved
        await self._users.set_search_provider(user_id, row.id, commit=False)
        await self._session.commit()


def _slot(
    row: UserSearchProvider | None,
    *,
    provider_available: bool,
) -> SearchSlotView | None:
    if row is None:
        return None
    source = PROVIDER_SOURCE
    status = row.status if row.status in (CONNECTED, NEEDS_RECONNECT) else NEEDS_RECONNECT
    message = row.detail if status == NEEDS_RECONNECT else None
    if not provider_available:
        status = NEEDS_RECONNECT
        message = message or "服务商已断开，请先在设置 · 服务商连接"
    masked = None
    return SearchSlotView(
        status=status,
        base_url=row.base_url,
        masked_key=masked,
        source=source,
        message=message,
    )


async def _probe(base_url: str, api_key: str) -> None:
    backend = DeepSeekBackend(api_key, base_url)
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

    The server writes the row directly. A sidecar POSTs inference, so the
    server that ran the search already recorded it.
    """
    if target.kind != "provider" or not target.provider_id:
        return
    if get_inference_search_credentials() is not None:
        return
    await note_own_search_failure(user_id, reason)
