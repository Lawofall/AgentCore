"""Account web_search target: platform SearXNG, or one own provider.

The model still sees one ``web_search``. The choice is the signed-in account's.
A sidecar has no user row, so it asks the cloud with the turn's inference JWT
and never receives a search key. Short non-UUID user ids (unit tests) stay on
the platform index and do not open a database connection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.core.net import (
    SEARCH_TIMEOUT,
    WEB_CONNECT_TIMEOUT,
    EgressError,
    outbound_async_client,
)
from agentcore.tools.builtin.web.cloud_fallback import (
    get_inference_search_credentials,
    inference_web_search_url,
)

logger = get_logger(__name__)

SEARXNG: Literal["searxng"] = "searxng"
CLEVERSEE: Literal["cleversee"] = "cleversee"
PLATFORM = "platform"

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@dataclass(frozen=True)
class SearchTarget:
    """Where this search runs.

    ``proxy`` is true when the desktop must POST inference and must not see
    the key: platform (so the count is real) and any provider that has a key.
    A keyless own SearXNG carries ``base_url`` and ``proxy=False``.
    """

    kind: Literal["platform", "provider"]
    protocol: Literal["searxng", "cleversee"]
    provider_id: str | None = None
    base_url: str = ""
    api_key: str = ""
    proxy: bool = False

    @property
    def cache_key(self) -> str:
        """Conversation cache identity. Platform keeps the historical searxng key."""
        if self.kind == "platform":
            return SEARXNG
        return self.provider_id or self.protocol


class SearchRouteView(BaseModel):
    """Sidecar-visible route. Never includes an API key."""

    kind: Literal["platform", "provider"]
    protocol: Literal["searxng", "cleversee"]
    provider_id: str | None = None
    proxy: bool
    base_url: str | None = None


def platform_target() -> SearchTarget:
    return SearchTarget(
        kind="platform",
        protocol=SEARXNG,
        base_url=settings.searxng_url.rstrip("/"),
        proxy=False,
    )


def route_from_target(target: SearchTarget) -> SearchRouteView:
    """Public view. Keyed targets and the platform index are proxied."""
    proxy = target.kind == "platform" or bool(target.api_key) or target.protocol == CLEVERSEE
    return SearchRouteView(
        kind=target.kind,
        protocol=target.protocol,
        provider_id=target.provider_id,
        proxy=proxy,
        base_url=None if proxy else (target.base_url or None),
    )


def target_from_route(payload: dict[str, Any]) -> SearchTarget:
    kind = payload.get("kind")
    protocol = payload.get("protocol")
    if kind not in ("platform", "provider") or protocol not in (SEARXNG, CLEVERSEE):
        raise EgressError("读取联网搜索设置失败")
    provider_id = payload.get("provider_id")
    base_url = payload.get("base_url") or ""
    return SearchTarget(
        kind=kind,  # type: ignore[arg-type]
        protocol=protocol,  # type: ignore[arg-type]
        provider_id=provider_id if isinstance(provider_id, str) and provider_id else None,
        base_url=base_url if isinstance(base_url, str) else "",
        proxy=bool(payload.get("proxy")),
    )


def inference_search_route_url(base_url: str) -> str:
    """``GET …/v1/inference/search_route`` beside the cloud web_search POST."""
    search_url = inference_web_search_url(base_url)
    suffix = "/web_search"
    if search_url.endswith(suffix):
        return search_url[: -len(suffix)] + "/search_route"
    raise ValueError(f"unexpected inference web_search url: {search_url}")


async def resolve_search_target(user_id: str) -> SearchTarget:
    """Target for this search. Sidecar reads the cloud; the server reads the user row."""
    creds = get_inference_search_credentials()
    if creds is not None:
        return await _fetch_remote_target(creds.api_key, creds.base_url, creds.extra_headers)
    return await load_search_target(user_id)


async def load_search_target(user_id: str) -> SearchTarget:
    """Server-side target, including the decrypted key when the account has one."""
    if not _UUID_RE.fullmatch(user_id or ""):
        return platform_target()
    from agentcore.db.base import async_session_factory
    from agentcore.db.repositories import UserRepository
    from agentcore.db.repositories.search import UserSearchProviderRepository
    from agentcore.tools.builtin.web.search_secrets import decrypt_search_key

    async with async_session_factory() as session:
        user = await UserRepository(session).get_by_id(user_id)
        provider_id = getattr(user, "search_provider_id", None) if user is not None else None
        if not provider_id:
            return platform_target()
        row = await UserSearchProviderRepository(session).get(provider_id, user_id=user_id)
    if row is None:
        return platform_target()
    api_key = decrypt_search_key(row.api_key_enc)
    return SearchTarget(
        kind="provider",
        protocol=row.protocol,  # type: ignore[arg-type]
        provider_id=row.id,
        base_url=row.base_url,
        api_key=api_key,
        proxy=False,
    )


async def _fetch_remote_target(
    api_key: str,
    base_url: str,
    extra_headers: dict[str, str] | None,
) -> SearchTarget:
    url = inference_search_route_url(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        **(extra_headers or {}),
    }
    try:
        async with outbound_async_client(
            timeout=httpx.Timeout(SEARCH_TIMEOUT, connect=WEB_CONNECT_TIMEOUT)
        ) as client:
            resp = await client.get(url, headers=headers)
    except Exception as exc:  # noqa: BLE001 — don't guess an index when the read fails
        logger.warning("search.engine_read_failed", error_type=type(exc).__name__)
        raise EgressError("读取联网搜索设置失败") from exc
    if resp.status_code >= 400:
        logger.warning("search.engine_read_failed", status=resp.status_code)
        raise EgressError("读取联网搜索设置失败")
    try:
        payload: Any = resp.json()
    except ValueError as exc:
        raise EgressError("读取联网搜索设置失败") from exc
    if not isinstance(payload, dict):
        raise EgressError("读取联网搜索设置失败")
    return target_from_route(payload)
