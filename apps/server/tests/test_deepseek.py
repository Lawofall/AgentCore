"""DeepSeek official search: request shape, result mapping, account choice."""

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
from agentcore.search.choice import (
    DEEPSEEK,
    SEARXNG,
    SearchTarget,
    load_search_target,
    route_from_target,
)
from agentcore.tools.builtin.web.deepseek import DeepSeekBackend
from agentcore.tools.builtin.web.search_backend import SearchResult
from agentcore.tools.builtin.web.search_cache import (
    ConversationSearchCache,
    SearchCacheEntry,
)

pytestmark = pytest.mark.anyio


async def test_deepseek_sends_official_search_and_prefers_summary(monkeypatch):
    captured: dict = {}
    req = httpx.Request("POST", "https://api.deepseek.com/v1/tools/search")
    payload = {
        "results": [
            {
                "title": "杭州",
                "url": "https://a.example/hz",
                "snippet": "短摘要",
                "summary": "给模型读的长摘要",
                "content": "should-not-be-used",
            },
            {"title": "", "url": "https://b.example", "snippet": "drop"},
            {"title": "无链接", "snippet": "no url"},
            {
                "title": "重复",
                "url": "https://a.example/hz#section",
                "summary": "dup",
            },
        ]
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
    backend = DeepSeekBackend(api_key="sk-test", base_url="https://api.deepseek.com")
    results = await backend.search("杭州西湖", max_results=5, language="zh")

    assert [(r.title, r.url, r.snippet) for r in results] == [
        ("杭州", "https://a.example/hz", "给模型读的长摘要"),
    ]
    assert captured["url"] == "https://api.deepseek.com/v1/tools/search"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["json"]["query"] == "杭州西湖 lang:zh"
    assert captured["json"]["count"] == 5
    assert captured["json"]["need_content"] is False


async def test_deepseek_clips_query_to_128(monkeypatch):
    captured: dict = {}
    req = httpx.Request("POST", "https://api.deepseek.com/v1/tools/search")

    class _Client:
        async def post(self, url, json=None, headers=None):
            del url, headers
            captured["json"] = json
            return httpx.Response(200, json={"results": []}, request=req)

        async def aclose(self):
            return None

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client())
    backend = DeepSeekBackend(api_key="sk-test")
    await backend.search("a" * 200, language="en")
    query = captured["json"]["query"]
    assert len(query) == 128
    assert query.endswith(" lang:en")


async def test_deepseek_requires_api_key():
    backend = DeepSeekBackend(api_key="")
    with pytest.raises(EgressError, match="API Key"):
        await backend.search("q")


async def test_deepseek_maps_invalid_api_key(monkeypatch):
    req = httpx.Request("POST", "https://api.deepseek.com/v1/tools/search")

    class _Client:
        async def post(self, *args, **kwargs):
            del args, kwargs
            return httpx.Response(
                401,
                json={"error": {"message": "invalid key", "code": "invalid_api_key"}},
                request=req,
            )

        async def aclose(self):
            return None

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client())
    backend = DeepSeekBackend(api_key="sk-test")
    with pytest.raises(EgressError, match="API Key 不正确"):
        await backend.search("q")


async def test_deepseek_maps_quota_without_body(monkeypatch):
    req = httpx.Request("POST", "https://api.deepseek.com/v1/tools/search")

    class _Client:
        async def post(self, *args, **kwargs):
            del args, kwargs
            return httpx.Response(429, json={"error": {"message": "slow down"}}, request=req)

        async def aclose(self):
            return None

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client())
    backend = DeepSeekBackend(api_key="sk-test")
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
            results=[SearchResult("DeepSeek", "https://d.example", "d")],
            max_results=5,
            stored_at=time.time(),
            engine=DEEPSEEK,
        )
    )
    searx = cache.get("杭州", min_results=1, engine=SEARXNG)
    deepseek = cache.get("杭州", min_results=1, engine=DEEPSEEK)
    assert searx is not None and searx.results[0].url == "https://s.example"
    assert deepseek is not None and deepseek.results[0].url == "https://d.example"


async def test_load_search_target_skips_db_for_stub_user_id():
    target = await load_search_target("u")
    assert target.kind == "platform"
    assert target.protocol == SEARXNG


def test_route_hides_keys():
    platform = route_from_target(SearchTarget(kind="platform", protocol=SEARXNG))
    assert platform.kind == "platform"
    assert platform.protocol == SEARXNG
    assert platform.provider_id is None
    deepseek = route_from_target(
        SearchTarget(
            kind="provider",
            protocol=DEEPSEEK,
            provider_id="d",
            base_url="https://api.deepseek.com",
            api_key="sk-test",
        )
    )
    assert deepseek.kind == "provider"
    assert deepseek.protocol == DEEPSEEK
    assert deepseek.provider_id == "d"
    assert not hasattr(deepseek, "api_key")
    dumped = deepseek.model_dump()
    assert "base_url" not in dumped
    assert "proxy" not in dumped


async def test_inference_web_search_uses_deepseek_when_selected(monkeypatch):
    from agentcore.api.routes.inference import web_search as web_search_mod
    from agentcore.tools.builtin.web import search_dispatch

    class _Backend:
        def __init__(self, api_key: str = "", base_url: str | None = None):
            del api_key, base_url
            self.calls = 0

        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            del max_results, on_phase, language
            self.calls += 1
            return [SearchResult("DeepSeek", "https://d.example", query)]

        async def aclose(self):
            return None

    deepseek = _Backend()
    searx = _Backend()

    async def _load(_user_id: str) -> SearchTarget:
        return SearchTarget(
            kind="provider",
            protocol=DEEPSEEK,
            provider_id="p",
            base_url="https://api.deepseek.com",
            api_key="sk-test",
        )

    monkeypatch.setattr(web_search_mod, "load_search_target", _load)
    monkeypatch.setattr(web_search_mod, "get_search_backend", lambda: searx)
    monkeypatch.setattr(search_dispatch, "DeepSeekBackend", lambda *args, **kwargs: deepseek)

    async def _noop(_user_id, *, message_id=None, **_kw):
        del message_id, _kw
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
    assert resp.results[0].url == "https://d.example"
    assert deepseek.calls == 1
    assert searx.calls == 0


_USER = "11111111-1111-4111-8111-111111111111"


def test_official_deepseek_host_rejects_opencode():
    from agentcore.tools.builtin.web.deepseek import is_official_deepseek_base_url

    assert is_official_deepseek_base_url("https://api.deepseek.com")
    assert is_official_deepseek_base_url("https://api.deepseek.com/v1")
    assert is_official_deepseek_base_url("https://api.deepseek.com/v1/")
    assert not is_official_deepseek_base_url("https://opencode.ai/zen/go/v1")
    assert not is_official_deepseek_base_url("https://api.deepseek.com.evil")


async def test_platform_selection_does_not_follow_official_llm(monkeypatch):
    from agentcore.search.choice import platform_target

    async def _account(_user_id: str) -> SearchTarget:
        return platform_target()

    async def _official(_user_id: str):
        raise AssertionError("official provider")

    monkeypatch.setattr(
        "agentcore.search.choice.account_search_target",
        _account,
    )
    monkeypatch.setattr(
        "agentcore.llm.provider_service.load_official_deepseek_provider",
        _official,
    )
    target = await load_search_target(_USER)
    assert target.kind == "platform"
    assert target.protocol == SEARXNG


async def test_sidecar_route_has_no_caller_query(monkeypatch):
    from agentcore.search.choice import _fetch_remote_target

    captured: dict = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "kind": "provider",
                "protocol": "deepseek",
                "provider_id": "ds",
            }

    class _Client:
        async def get(self, url, headers=None, params=None):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(
        "agentcore.search.choice.outbound_async_client",
        lambda **_kwargs: _Client(),
    )
    target = await _fetch_remote_target(
        "inference-jwt",
        "https://api.example.com/v1/inference/v1",
        {"X-AgentCore-Conversation": "c"},
    )
    assert captured["params"] is None
    assert target.protocol == DEEPSEEK
    assert target.api_key == ""
    assert target.provider_id == "ds"
