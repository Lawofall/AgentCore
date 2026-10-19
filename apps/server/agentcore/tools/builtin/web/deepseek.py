"""DeepSeek official web search — ``POST /v1/tools/search``.

The account brings its own API key. The host is the official API, not a
user-entered URL. ``need_content`` stays off: the model-facing text is
``summary`` (falling back to ``snippet``). Page bodies stay on ``web_fetch``.

Each DeepSeek account is limited to 5 requests per second. This process paces
a key at 4/s so a parallel team does not sit on the limit. Several API workers
each keep their own pacer.
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx

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
    _TokenBucket,
)

DEEPSEEK_SEARCH_BASE_URL = "https://api.deepseek.com"
_SEARCH_PATH = "/v1/tools/search"
_QUERY_MAX = 128
_COUNT_MAX = 50
# Official cap is 5 QPS per account. Stay under it in this process.
_RATE_PER_SEC = 4.0
_RATE_BURST = 4.0
_LANGS = frozenset({"zh", "en", "ja", "ko"})

_buckets: dict[str, _TokenBucket] = {}


def is_official_deepseek_base_url(url: str) -> bool:
    """True for the official API host, including the ``/v1`` alias.

    OpenCode Go and any other host stay false even when the model id contains
    ``deepseek``.
    """
    text = (url or "").strip().rstrip("/").lower()
    if text.endswith("/v1"):
        text = text[: -len("/v1")].rstrip("/")
    return text == DEEPSEEK_SEARCH_BASE_URL


class DeepSeekBackend:
    """One account key against the official search API."""

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEEPSEEK_SEARCH_BASE_URL).strip().rstrip("/")
        if not self.base_url:
            self.base_url = DEEPSEEK_SEARCH_BASE_URL
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = outbound_async_client(
                timeout=httpx.Timeout(SEARCH_TIMEOUT, connect=WEB_CONNECT_TIMEOUT)
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def search(
        self,
        query: str,
        max_results: int = DEFAULT_MAX_RESULTS,
        on_phase: PhaseCallback | None = None,
        *,
        language: str | None = None,
    ) -> list[SearchResult]:
        if not self.api_key:
            raise EgressError("DeepSeek 搜索未配置 API Key")
        fitted = fit_query(query, language)
        count = max(1, min(int(max_results), _COUNT_MAX))
        bucket = _bucket_for(self.api_key)
        if on_phase and bucket.would_wait():
            on_phase("queued")
        await bucket.acquire()
        if on_phase:
            on_phase("querying")
        payload = {"query": fitted, "count": count, "need_content": False}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        resp = await self._get_client().post(
            f"{self.base_url}{_SEARCH_PATH}",
            json=payload,
            headers=headers,
        )
        if resp.status_code >= 400:
            raise EgressError(deepseek_failure_message(resp.status_code))
        try:
            data = resp.json()
        except ValueError as exc:
            raise EgressError("DeepSeek 搜索返回了无法解析的响应") from exc
        if not isinstance(data, dict):
            return []
        return _parse_results(data, count)


def fit_query(query: str, language: str | None) -> str:
    """Collapse whitespace and keep the official 128-character query budget.

    A known language is appended as ``lang:`` when it still fits. The operator
    is omitted when the query already carries one.
    """
    text = " ".join((query or "").split())
    if not text:
        raise EgressError("搜索词为空")
    lang = (language or "").strip().lower()
    suffix = ""
    if lang in _LANGS and "lang:" not in text.lower():
        suffix = f" lang:{lang}"
    budget = _QUERY_MAX - len(suffix)
    if budget < 1:
        suffix = ""
        budget = _QUERY_MAX
    if len(text) > budget:
        text = text[:budget].rstrip()
    if not text:
        raise EgressError("搜索词为空")
    return f"{text}{suffix}"


def deepseek_failure_message(status_code: int) -> str:
    """User-facing reason. Never includes the response body or the key."""
    if status_code == 401:
        return "API Key 不正确。到 DeepSeek 开放平台核对或新建"
    if status_code == 402:
        return "DeepSeek 账户余额不足"
    if status_code == 403:
        return "DeepSeek 搜索未授权"
    if status_code == 429:
        return "超出限额，稍后再试"
    if status_code == 400:
        return "DeepSeek 搜索请求无效"
    if status_code == 408 or status_code >= 500:
        return "DeepSeek 搜索暂时不可用"
    return f"DeepSeek 搜索失败（HTTP {status_code}）"


def _bucket_for(api_key: str) -> _TokenBucket:
    digest = hashlib.sha256(api_key.encode()).hexdigest()
    bucket = _buckets.get(digest)
    if bucket is None:
        bucket = _TokenBucket(_RATE_PER_SEC, _RATE_BURST)
        _buckets[digest] = bucket
    return bucket


def _parse_results(data: dict[str, Any], max_results: int) -> list[SearchResult]:
    """Map ``results``. Prefer ``summary`` for the model. Drop rows without a link."""
    items = data.get("results")
    if not isinstance(items, list):
        return []
    results: list[SearchResult] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url or not title:
            continue
        key = url.split("#", 1)[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        summary = str(item.get("summary") or "").strip()
        snippet = summary or str(item.get("snippet") or "").strip()
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= max_results:
            break
    return results
