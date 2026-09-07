"""OpenCode Go/Zen outbound session + User-Agent headers."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest

from agentcore.core.log_context import clear_log_context, log_context
from agentcore.llm.opencode_headers import (
    OPENCODE_SESSION_HEADER,
    OPENCODE_USER_AGENT,
    opencode_client_headers,
    opencode_session_headers,
)
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.protocol import LLMMessage, LLMRequest

_GO = "https://opencode.ai/zen/go/v1"
_ZEN = "https://opencode.ai/zen/v1"
_DEEPSEEK = "https://api.deepseek.com"
_CID = "c2b9f58c-e2c4-4b5f-97cc-bb6c651b9ad4"


@pytest.fixture(autouse=True)
def _clear_log_ctx() -> None:
    clear_log_context()
    yield
    clear_log_context()


def test_client_headers_only_on_opencode_endpoints() -> None:
    assert opencode_client_headers(_GO) == {"User-Agent": OPENCODE_USER_AGENT}
    assert opencode_client_headers(_ZEN + "/") == {"User-Agent": OPENCODE_USER_AGENT}
    assert opencode_client_headers(_DEEPSEEK) == {}
    assert opencode_client_headers("https://relay.example/openai/v1") == {}
    assert OPENCODE_SESSION_HEADER not in opencode_client_headers(_GO)


def test_session_header_uses_conversation_id() -> None:
    with log_context(conversation_id=_CID, trace_id="t" * 32):
        headers = opencode_session_headers(_GO)
    assert headers == {OPENCODE_SESSION_HEADER: _CID}


def test_session_header_probe_falls_back_to_trace() -> None:
    trace = "3eb29cd902874d4f8b7a60ccbe56e456"
    with log_context(trace_id=trace):
        headers = opencode_session_headers(_ZEN)
    assert headers == {OPENCODE_SESSION_HEADER: f"probe:{trace}"}


def test_session_header_probe_mints_id_when_unbound() -> None:
    fake = uuid4()
    with patch("agentcore.llm.opencode_headers.uuid4", return_value=fake):
        headers = opencode_session_headers(_GO)
    assert headers == {OPENCODE_SESSION_HEADER: f"probe:{fake.hex}"}


def test_session_header_never_empty_or_non_ascii() -> None:
    with log_context(conversation_id="  "):
        headers = opencode_session_headers(_GO)
    assert headers[OPENCODE_SESSION_HEADER].startswith("probe:")
    assert headers[OPENCODE_SESSION_HEADER] != ""
    with log_context(conversation_id="会话"):
        headers = opencode_session_headers(_GO)
    assert headers[OPENCODE_SESSION_HEADER].startswith("probe:")
    assert headers[OPENCODE_SESSION_HEADER].isascii()


def test_session_header_absent_off_opencode() -> None:
    with log_context(conversation_id=_CID):
        assert opencode_session_headers(_DEEPSEEK) == {}
        assert opencode_session_headers("https://opencode.ai/zen/v1/extra") == {}


def test_leaf_freezes_user_agent_not_session() -> None:
    go = OpenAICompatibleProvider(name="platform", api_key="k", base_url=_GO)
    ds = OpenAICompatibleProvider(name="user", api_key="k", base_url=_DEEPSEEK)
    assert go._client.headers["user-agent"] == OPENCODE_USER_AGENT
    assert OPENCODE_SESSION_HEADER not in go._client.headers
    assert ds._client.headers["user-agent"].startswith("python-httpx/")


def _ok_body() -> dict:
    return {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        "model": DEEPSEEK_V4_FLASH,
    }


async def test_complete_sends_session_per_request_not_cached_on_leaf() -> None:
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers.get(OPENCODE_SESSION_HEADER, ""))
        return httpx.Response(200, json=_ok_body())

    provider = OpenAICompatibleProvider(name="platform", api_key="k", base_url=_GO)
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url=_GO, transport=httpx.MockTransport(handler)
    )
    req = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=DEEPSEEK_V4_FLASH,
        scenario="chat",
    )
    with log_context(conversation_id="conv-a"):
        await provider.complete(req)
    with log_context(conversation_id="conv-b"):
        await provider.complete(req)
    assert captured == ["conv-a", "conv-b"]
    assert provider._extra_headers is None
    await provider.close()


async def test_list_models_does_not_send_session() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["session"] = request.headers.get(OPENCODE_SESSION_HEADER)
        return httpx.Response(200, json={"data": [{"id": DEEPSEEK_V4_FLASH}]})

    provider = OpenAICompatibleProvider(name="platform", api_key="k", base_url=_GO)
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url=_GO, transport=httpx.MockTransport(handler)
    )
    with log_context(conversation_id=_CID):
        ids = await provider.list_models()
    assert ids == [DEEPSEEK_V4_FLASH]
    assert seen["path"].endswith("/models")
    assert seen["session"] is None
    await provider.close()


async def test_complete_off_opencode_does_not_send_session() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["session"] = request.headers.get(OPENCODE_SESSION_HEADER)
        return httpx.Response(200, json=_ok_body())

    provider = OpenAICompatibleProvider(name="user", api_key="k", base_url=_DEEPSEEK)
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url=_DEEPSEEK, transport=httpx.MockTransport(handler)
    )
    with log_context(conversation_id=_CID):
        await provider.complete(
            LLMRequest(
                messages=[LLMMessage(role="user", content="hi")],
                model=DEEPSEEK_V4_FLASH,
                scenario="chat",
            )
        )
    assert seen["session"] is None
    await provider.close()
