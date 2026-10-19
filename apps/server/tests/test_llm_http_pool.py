"""Origin-pooled LLM httpx clients survive turn close and isolate tenant headers."""

from __future__ import annotations

import httpx

from agentcore.llm import http_pool as http_pool_mod
from agentcore.llm.factory import spawn_independent_llm
from agentcore.llm.http_pool import (
    LLM_KEEPALIVE_EXPIRY_S,
    LLM_WARM_COOLDOWN_S,
    aclose_llm_http_pool,
    acquire_llm_http_client,
    origin_key,
    peek_llm_http_client,
    warm_llm_origin,
)
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
from agentcore.llm.provider.anthropic import AnthropicMessagesProvider
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.protocol import LLMMessage, LLMRequest
from agentcore.llm.provider.responses import OpenAIResponsesProvider

_A = "http://llm-pool-a.test/v1"
_B = "http://llm-pool-b.test/v1"


def _ok_body() -> dict:
    return {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        "model": DEEPSEEK_V4_FLASH,
    }


def test_origin_key_strips_slash() -> None:
    assert origin_key("https://api.example/v1/") == "https://api.example/v1"
    assert origin_key("") == ""


def test_same_origin_shares_client_different_origin_does_not() -> None:
    a1 = OpenAICompatibleProvider(name="user", api_key="ka", base_url=_A)
    a2 = OpenAICompatibleProvider(
        name="user",
        api_key="kb",
        base_url=_A + "/",
        extra_headers={"X-Tenant": "secret"},
    )
    b = OpenAICompatibleProvider(name="user", api_key="ka", base_url=_B)
    assert a1._client is a2._client
    assert a1._client is not b._client
    names = {k.lower() for k in a1._client.headers}
    assert "authorization" not in names
    assert "x-tenant" not in names


def test_openai_and_anthropic_same_origin_share_transport() -> None:
    openai = OpenAICompatibleProvider(name="user", api_key="k", base_url=_A)
    anthropic = AnthropicMessagesProvider(name="user", api_key="k", base_url=_A)
    responses = OpenAIResponsesProvider(name="user", api_key="k", base_url=_A)
    assert openai._client is anthropic._client
    assert openai._client is responses._client


def test_keepalive_expiry_outlives_a_turn_gap() -> None:
    assert LLM_KEEPALIVE_EXPIRY_S == 60.0
    client = acquire_llm_http_client(_A)
    pool = getattr(getattr(client, "_transport", None), "_pool", None)
    expiry = getattr(pool, "keepalive_expiry", None) or getattr(pool, "_keepalive_expiry", None)
    assert expiry == LLM_KEEPALIVE_EXPIRY_S


async def test_instance_close_does_not_tear_pooled_transport() -> None:
    leaf = OpenAICompatibleProvider(name="user", api_key="k", base_url=_A)
    cloned, owns = spawn_independent_llm(leaf)
    assert owns is True
    assert cloned is not leaf
    assert cloned._client is leaf._client
    await leaf.close()
    assert leaf._closed is True
    assert cloned._closed is False
    assert not cloned._client.is_closed
    assert peek_llm_http_client(_A) is cloned._client
    await cloned.close()
    assert cloned._closed is True
    assert not cloned._client.is_closed


async def test_aclose_pool_then_acquire_is_fresh() -> None:
    first = acquire_llm_http_client(_A)
    await aclose_llm_http_pool()
    assert first.is_closed
    second = acquire_llm_http_client(_A)
    assert second is not first
    assert not second.is_closed


async def test_complete_sends_per_request_authorization() -> None:
    seen_auth: list[str] = []
    seen_tenant: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("authorization", ""))
        seen_tenant.append(request.headers.get("x-tenant"))
        return httpx.Response(200, json=_ok_body())

    a = OpenAICompatibleProvider(
        name="user",
        api_key="key-a",
        base_url=_A,
        extra_headers={"X-Tenant": "secret"},
    )
    b = OpenAICompatibleProvider(name="user", api_key="key-b", base_url=_A)
    a._client = httpx.AsyncClient(base_url=_A, transport=httpx.MockTransport(handler))
    b._client = a._client
    req = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=DEEPSEEK_V4_FLASH,
        scenario="chat",
    )
    try:
        await a.complete(req)
        await b.complete(req)
        assert seen_auth == ["Bearer key-a", "Bearer key-b"]
        assert seen_tenant == ["secret", None]
    finally:
        await a.close()
        await b.close()


def _install_mock(base_url: str, handler: object) -> None:
    client = httpx.AsyncClient(base_url=base_url, transport=httpx.MockTransport(handler))
    with http_pool_mod._LOCK:
        http_pool_mod._CLIENTS[origin_key(base_url)] = client


async def test_warm_get_models_404_is_ok() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.headers.get("authorization", "")))
        return httpx.Response(404, json={"error": "no catalog"})

    _install_mock(_A, handler)
    ok = await warm_llm_origin(_A, authorization="tok")
    assert ok is True
    assert seen == [("GET", "Bearer tok")]
    names = {k.lower() for k in acquire_llm_http_client(_A).headers}
    assert "authorization" not in names


async def test_warm_connect_error_is_false() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _install_mock(_A, handler)
    assert await warm_llm_origin(_A, authorization="tok") is False


async def test_warm_cooldown_skips_second_get(monkeypatch) -> None:  # noqa: ANN001
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        return httpx.Response(200, json={"data": []})

    _install_mock(_A, handler)
    clock = {"t": 0.0}
    monkeypatch.setattr(http_pool_mod.time, "monotonic", lambda: clock["t"])
    assert await warm_llm_origin(_A) is True
    assert hits["n"] == 1
    clock["t"] = LLM_WARM_COOLDOWN_S - 1
    assert await warm_llm_origin(_A) is True
    assert hits["n"] == 1
    clock["t"] = LLM_WARM_COOLDOWN_S + 0.1
    assert await warm_llm_origin(_A) is True
    assert hits["n"] == 2


async def test_warm_blank_url_skips() -> None:
    assert await warm_llm_origin("") is False
    assert await warm_llm_origin("   ") is False
