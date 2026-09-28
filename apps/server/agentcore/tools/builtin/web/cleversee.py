"""CleverSee public web search (开析) — one engine, ``CNLiteBasic``.

HTTP ``POST {base}/search/unified`` with a server-side API key. Returns title,
link, and snippet only. Does not request main text, markdown, or the paid
summary. ``sceneItems`` (weather cards and the like) are dropped.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.core.net import (
    SEARCH_TIMEOUT,
    WEB_CONNECT_TIMEOUT,
    EgressError,
    outbound_async_client,
)
from agentcore.tools.builtin.web.search_backend import (
    DEFAULT_MAX_RESULTS,
    PhaseCallback,
    SearchResult,
)

logger = get_logger(__name__)

ENGINE_TYPE = "CNLiteBasic"
_SEARCH_PATH = "/search/unified"
# Under CleverSee's default 10 QPS. Same cap the SearXNG leg uses for a team burst.
_CONCURRENCY = 6
_NUM_RESULTS_CAP = 50

_backend: CleverSeeBackend | None = None


class CleverSeeBackend:
    """Process-wide CleverSee client. ``engineType`` is fixed; the model never picks it."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = (settings.cleversee_api_key if api_key is None else api_key).strip()
        self.base_url = (base_url or settings.cleversee_base_url).rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._sem: asyncio.Semaphore | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = outbound_async_client(
                timeout=httpx.Timeout(SEARCH_TIMEOUT, connect=WEB_CONNECT_TIMEOUT)
            )
        return self._client

    def _get_sem(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(_CONCURRENCY)
        return self._sem

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._sem = None

    async def search(
        self,
        query: str,
        max_results: int = DEFAULT_MAX_RESULTS,
        on_phase: PhaseCallback | None = None,
        *,
        language: str | None = None,
    ) -> list[SearchResult]:
        del language  # index is fixed; locale stays a tool-layer concern
        if not self.api_key:
            raise EgressError("开析搜索未配置 API key")
        if on_phase:
            on_phase("querying")
        count = max(1, min(int(max_results), _NUM_RESULTS_CAP))
        payload: dict[str, Any] = {
            "query": query,
            "engineType": ENGINE_TYPE,
            "contents": {
                "mainText": False,
                "markdownText": False,
                "summary": False,
                "rerankScore": False,
            },
            "advancedParams": {"numResults": count},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        async with self._get_sem():
            resp = await self._get_client().post(
                f"{self.base_url}{_SEARCH_PATH}",
                json=payload,
                headers=headers,
            )
        if resp.status_code >= 400:
            raise EgressError(_cleversee_http_message(resp))
        try:
            data = resp.json()
        except ValueError as exc:
            raise EgressError("开析搜索返回了无法解析的响应") from exc
        if not isinstance(data, dict):
            return []
        return _parse_page_items(data, count)


def get_cleversee_backend() -> CleverSeeBackend:
    """Build (once) the process-wide CleverSee client."""
    global _backend
    if _backend is None:
        _backend = CleverSeeBackend()
    return _backend


async def aclose_cleversee_backend() -> None:
    """Release the CleverSee keep-alive pool. No-op if never built."""
    global _backend
    backend = _backend
    _backend = None
    if backend is not None:
        await backend.aclose()


def _cleversee_http_message(resp: httpx.Response) -> str:
    """Honest model-facing reason. Never include the response body or the key."""
    if resp.status_code == 429:
        return "开析搜索超出限额，请稍后再试"
    if resp.status_code in (401, 403):
        return "开析搜索未授权或未开通"
    if resp.status_code >= 500:
        return "开析搜索暂时不可用"
    return f"开析搜索失败（HTTP {resp.status_code}）"


def _parse_page_items(data: dict[str, Any], max_results: int) -> list[SearchResult]:
    """Map ``pageItems`` title/link/snippet. Drop ``sceneItems`` and rows without a link."""
    items = data.get("pageItems")
    if not isinstance(items, list):
        return []
    results: list[SearchResult] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("link") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url or not title:
            continue
        key = url.split("#", 1)[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        snippet = str(item.get("snippet") or "")
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= max_results:
            break
    return results
