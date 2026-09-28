"""CleverSee index: request shape, pageItems mapping, account choice."""

from __future__ import annotations

import time
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request

from agentcore.api.routes.inference.web_search import (
    InferenceWebSearchRequest,
    inference_web_search,
)
from agentcore.core.net import EgressError
from agentcore.tools.builtin.web.cleversee import CleverSeeBackend
from agentcore.tools.builtin.web.search_backend import SearchResult
from agentcore.tools.builtin.web.search_cache import (
    ConversationSearchCache,
    SearchCacheEntry,
)
from agentcore.tools.builtin.web.search_choice import (
    CLEVERSEE,
    SEARXNG,
    SearchTarget,
    load_search_target,
    route_from_target,
)

pytestmark = pytest.mark.anyio


async def test_cleversee_sends_cn_lite_basic_and_maps_page_items(monkeypatch):
    captured: dict = {}
    req = httpx.Request("POST", "https://cloud-iqs.aliyuncs.com/search/unified")
    payload = {
        "pageItems": [
            {
                "title": "杭州",
                "link": "https://a.example/hz",
                "snippet": "西湖",
                "mainText": "should-not-be-used",
            },
            {"title": "", "link": "https://b.example", "snippet": "drop"},
            {"title": "天气卡", "snippet": "no link"},
        ],
        "sceneItems": [{"type": "weather", "title": "晴"}],
    }

    class _Client:
        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return httpx.Response(200, json=payload, request=req)

        async def aclose(self):
            return None

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client())
    backend = CleverSeeBackend(api_key="iqs-test", base_url="https://cloud-iqs.aliyuncs.com")
    results = await backend.search("杭州", max_results=5)

    assert [(r.title, r.url, r.snippet) for r in results] == [
        ("杭州", "https://a.example/hz", "西湖"),
    ]
    assert captured["url"] == "https://cloud-iqs.aliyuncs.com/search/unified"
    assert captured["headers"]["Authorization"] == "Bearer iqs-test"
    assert captured["json"]["engineType"] == "CNLiteBasic"
    assert captured["json"]["contents"]["mainText"] is False
    assert captured["json"]["contents"]["summary"] is False
    assert captured["json"]["advancedParams"]["numResults"] == 5


async def test_cleversee_requires_api_key():
    backend = CleverSeeBackend(api_key="", base_url="https://cloud-iqs.aliyuncs.com")
    with pytest.raises(EgressError, match="API key"):
        await backend.search("q")


async def test_cleversee_maps_quota_without_body(monkeypatch):
    req = httpx.Request("POST", "https://cloud-iqs.aliyuncs.com/search/unified")

    class _Client:
        async def post(self, *args, **kwargs):
            return httpx.Response(429, json={"code": "Throttling"}, request=req)

        async def aclose(self):
            return None

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client())
    backend = CleverSeeBackend(api_key="iqs-test", base_url="https://cloud-iqs.aliyuncs.com")
    with pytest.raises(EgressError, match="超出限额"):
        await backend.search("q")


def test_cache_key_splits_engines():
    cache = ConversationSearchCache()
    cache.put(
        SearchCacheEntry(
            query="杭州",
            results=[SearchResult("自建", "https://s.example", "s")],
            max_results=5,
            stored_at=time.time(),
            engine=SEARXNG,
        )
    )
    cache.put(
        SearchCacheEntry(
            query="杭州",
            results=[SearchResult("开析", "https://c.example", "c")],
            max_results=5,
            stored_at=time.time(),
            engine=CLEVERSEE,
        )
    )
    searx = cache.get("杭州", min_results=1, engine=SEARXNG)
    clever = cache.get("杭州", min_results=1, engine=CLEVERSEE)
    assert searx is not None and searx.results[0].url == "https://s.example"
    assert clever is not None and clever.results[0].url == "https://c.example"


async def test_load_search_target_skips_db_for_stub_user_id():
    target = await load_search_target("u")
    assert target.kind == "platform"
    assert target.protocol == SEARXNG
    assert target.proxy is False


def test_route_hides_keys_and_proxies_platform():
    platform = route_from_target(SearchTarget(kind="platform", protocol=SEARXNG))
    assert platform.proxy is True
    assert platform.base_url is None
    own = route_from_target(
        SearchTarget(
            kind="provider",
            protocol=SEARXNG,
            provider_id="p",
            base_url="http://127.0.0.1:8888",
        )
    )
    assert own.proxy is False
    assert own.base_url == "http://127.0.0.1:8888"
    keyed = route_from_target(
        SearchTarget(
            kind="provider",
            protocol=SEARXNG,
            provider_id="p",
            base_url="https://search.example",
            api_key="secret",
        )
    )
    assert keyed.proxy is True
    assert keyed.base_url is None
    clever = route_from_target(
        SearchTarget(
            kind="provider",
            protocol=CLEVERSEE,
            provider_id="c",
            base_url="https://cloud-iqs.aliyuncs.com",
            api_key="iqs",
        )
    )
    assert clever.proxy is True
    assert clever.base_url is None


async def test_inference_web_search_uses_cleversee_when_selected(monkeypatch):
    from agentcore.api.routes.inference import web_search as web_search_mod
    from agentcore.tools.builtin.web import search_dispatch

    class _Backend:
        def __init__(self, api_key: str = "", base_url: str | None = None):
            self.calls = 0

        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            self.calls += 1
            return [SearchResult("开析", "https://c.example", query)]

        async def aclose(self):
            return None

    cleversee = _Backend()
    searx = _Backend()

    async def _load(_user_id: str) -> SearchTarget:
        return SearchTarget(
            kind="provider",
            protocol=CLEVERSEE,
            provider_id="p",
            base_url="https://cloud-iqs.aliyuncs.com",
            api_key="iqs",
        )

    monkeypatch.setattr(web_search_mod, "load_search_target", _load)
    monkeypatch.setattr(web_search_mod, "get_search_backend", lambda: searx)
    monkeypatch.setattr(search_dispatch, "CleverSeeBackend", lambda *args, **kwargs: cleversee)

    async def _noop(_user_id, *, message_id=None, **_kw):
        return None

    monkeypatch.setattr(web_search_mod, "enforce_inference_proxy_rate_limit", _noop)
    resp = await inference_web_search(
        InferenceWebSearchRequest(query="杭州"),
        Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/inference/web_search",
                "headers": [],
            }
        ),
        user=SimpleNamespace(user_id="u1", status="active"),  # type: ignore[arg-type]
    )
    assert resp.results[0].url == "https://c.example"
    assert cleversee.calls == 1
    assert searx.calls == 0
