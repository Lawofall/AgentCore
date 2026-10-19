"""Per-model chat/completions vs /messages dispatch (no live upstream)."""

from __future__ import annotations

import json

import httpx
import pytest

pytestmark = pytest.mark.anyio

from agentcore.llm.provider.anthropic import AnthropicMessagesProvider
from agentcore.llm.provider.anthropic_messages import (
    ANTHROPIC_VERSION,
    DEFAULT_MAX_TOKENS,
)
from agentcore.llm.provider.dispatch import ProtocolDispatchProvider, build_credential_leaf
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.protocol import LLMMessage, LLMRequest, LLMResponse
from agentcore.llm.provider.responses import OpenAIResponsesProvider

_ZEN = "https://opencode.ai/zen/v1"
_RELAY = "https://openrouter.ai/api/v1"
_OPENAI = "https://api.openai.com/v1"


def _request(model: str) -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=model,
        stream=False,
    )


async def test_dispatch_routes_complete_by_model(monkeypatch):
    seen: list[str] = []

    async def _openai_complete(self, request: LLMRequest) -> LLMResponse:
        seen.append(f"openai:{request.model}")
        return LLMResponse(content="flash", model=request.model)

    async def _anthropic_complete(self, request: LLMRequest) -> LLMResponse:
        seen.append(f"anthropic:{request.model}")
        return LLMResponse(content="haiku", model=request.model)

    async def _responses_complete(self, request: LLMRequest) -> LLMResponse:
        seen.append(f"responses:{request.model}")
        return LLMResponse(content="gpt", model=request.model)

    monkeypatch.setattr(OpenAICompatibleProvider, "complete", _openai_complete)
    monkeypatch.setattr(AnthropicMessagesProvider, "complete", _anthropic_complete)
    monkeypatch.setattr(OpenAIResponsesProvider, "complete", _responses_complete)
    leaf = build_credential_leaf(
        name="user", api_key="sk-test", base_url=_ZEN, vendor_id="opencode_zen"
    )
    try:
        assert isinstance(leaf, ProtocolDispatchProvider)
        assert isinstance(leaf._leaf("deepseek-v4-flash"), OpenAICompatibleProvider)
        assert isinstance(leaf._leaf("grok-4.5"), OpenAIResponsesProvider)
        assert isinstance(leaf._leaf("gpt-5.6-luna"), OpenAIResponsesProvider)
        assert isinstance(leaf._leaf("claude-haiku-4-5"), AnthropicMessagesProvider)
        assert isinstance(leaf._leaf("claude-haiku-5-5"), AnthropicMessagesProvider)
        assert isinstance(leaf._leaf("claude-haiku-9"), AnthropicMessagesProvider)
        flash = await leaf.complete(_request("deepseek-v4-flash"))
        grok = await leaf.complete(_request("grok-4.5"))
        luna = await leaf.complete(_request("gpt-5.6-luna"))
        haiku = await leaf.complete(_request("claude-haiku-4-5"))
        haiku55 = await leaf.complete(_request("claude-haiku-5-5"))
        union = await leaf.complete(_request("union-alpha"))
        assert flash.content == "flash"
        assert grok.content == "gpt"
        assert luna.content == "gpt"
        assert haiku.content == "haiku"
        assert haiku55.content == "haiku"
        assert union.content == "haiku"
        assert seen == [
            "openai:deepseek-v4-flash",
            "responses:grok-4.5",
            "responses:gpt-5.6-luna",
            "anthropic:claude-haiku-4-5",
            "anthropic:claude-haiku-5-5",
            "anthropic:union-alpha",
        ]
    finally:
        await leaf.close()

    relay = build_credential_leaf(
        name="user", api_key="sk-test", base_url=_RELAY, vendor_id="openrouter"
    )
    try:
        assert isinstance(relay._leaf("claude-haiku-4-5"), OpenAICompatibleProvider)
        assert isinstance(relay._leaf("grok-4.5"), OpenAICompatibleProvider)
        out = await relay.complete(_request("claude-haiku-4-5"))
        assert out.content == "flash"
        assert seen[-1] == "openai:claude-haiku-4-5"
    finally:
        await relay.close()

    openai = build_credential_leaf(
        name="user", api_key="sk-test", base_url=_OPENAI, vendor_id="openai"
    )
    try:
        assert isinstance(openai._leaf("gpt-4o"), OpenAIResponsesProvider)
        assert isinstance(openai._leaf("o3-mini"), OpenAIResponsesProvider)
        assert isinstance(openai._leaf("gpt-6-astra"), OpenAIResponsesProvider)
        out = await openai.complete(_request("gpt-4o"))
        assert out.content == "gpt"
        assert seen[-1] == "responses:gpt-4o"
    finally:
        await openai.close()

    # Identity, not URL: OpenAI vendor on a relay still /responses;
    # custom vendor on api.openai.com stays Completions.
    openai_relay = build_credential_leaf(
        name="user", api_key="sk-test", base_url=_RELAY, vendor_id="openai"
    )
    try:
        assert isinstance(openai_relay._leaf("gpt-4o"), OpenAIResponsesProvider)
        assert isinstance(openai_relay._leaf("gpt-6.1-sol"), OpenAIResponsesProvider)
        out = await openai_relay.complete(_request("gpt-6.1-sol"))
        assert out.content == "gpt"
        assert seen[-1] == "responses:gpt-6.1-sol"
    finally:
        await openai_relay.close()

    custom_on_official = build_credential_leaf(
        name="user", api_key="sk-test", base_url=_OPENAI, vendor_id="custom"
    )
    try:
        assert isinstance(custom_on_official._leaf("gpt-4o"), OpenAICompatibleProvider)
        out = await custom_on_official.complete(_request("gpt-4o"))
        assert out.content == "flash"
        assert seen[-1] == "openai:gpt-4o"
    finally:
        await custom_on_official.close()


async def test_anthropic_complete_posts_messages_path():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["headers"] = {k.lower(): v for k, v in request.headers.items()}
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "model": "claude-haiku-4-5",
                "role": "assistant",
                "content": [{"type": "text", "text": "pong"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        )

    provider = AnthropicMessagesProvider(
        name="user", api_key="sk-test", base_url=_ZEN
    )
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url=_ZEN, transport=httpx.MockTransport(handler)
    )
    try:
        response = await provider.complete(_request("claude-haiku-4-5"))
        assert response.content == "pong"
        assert captured["path"] == "/zen/v1/messages"
        assert captured["body"]["model"] == "claude-haiku-4-5"
        assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]
        assert captured["headers"].get("anthropic-version") == ANTHROPIC_VERSION
        assert captured["body"]["max_tokens"] == DEFAULT_MAX_TOKENS
        assert captured["body"]["stream"] is False
        assert captured["body"]["temperature"] == 0.7
        assert "thinking" not in captured["body"]
    finally:
        await provider.close()


def test_inference_hop_forwards_thinking_blocks_vendor_does_not():
    from agentcore.llm.provider.protocol import ToolCall, ToolCallFunction

    blocks = [
        {"type": "thinking", "thinking": "plan", "signature": "sig_hop"}
    ]
    messages = [
        LLMMessage(role="user", content="hi"),
        LLMMessage(
            role="assistant",
            content="",
            reasoning_content="plan",
            thinking_blocks=blocks,
            tool_calls=[
                ToolCall(
                    id="t1",
                    function=ToolCallFunction(name="read_file", arguments="{}"),
                )
            ],
        ),
    ]
    hop = OpenAICompatibleProvider(
        name="user",
        api_key="sk-test",
        base_url="http://127.0.0.1:9/v1/inference/v1",
    )
    vendor = OpenAICompatibleProvider(
        name="user", api_key="sk-test", base_url=_ZEN
    )
    req = LLMRequest(messages=messages, model="claude-haiku-4-5")
    hop_asst = hop._build_payload(req, stream=False)["messages"][1]
    vendor_asst = vendor._build_payload(req, stream=False)["messages"][1]
    assert hop_asst["reasoning_content"] == "plan"
    assert hop_asst["thinking_blocks"] == blocks
    assert "thinking_blocks" not in vendor_asst
    assert "reasoning_content" not in vendor_asst
