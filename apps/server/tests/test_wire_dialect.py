"""Wire dialect resolution — Kimi/Moonshot omit_temperature + Anthropic + o-series."""

from __future__ import annotations

import pytest

from agentcore.llm.provider.openai_compatible import (
    _PROBE_TOOLS_MAX_TOKENS,
    OpenAICompatibleProvider,
)
from agentcore.llm.provider.protocol import LLMMessage, LLMRequest
from agentcore.llm.provider.wire_dialect import resolve_wire_dialect

_MOONSHOT_URL = "https://api.moonshot.cn/v1"
_MOONSHOT_AI_URL = "https://api.moonshot.ai/v1"
_ZEN_URL = "https://opencode.ai/zen/v1"
_GO_URL = "https://opencode.ai/zen/go/v1"
_RELAY_URL = "https://relay.example/v1"

_O_SERIES_MODELS = (
    "o1",
    "o1-mini",
    "o3",
    "o3-mini",
    "o3-mini-2025-01-31",
    "o4-mini",
    "openai/o3-mini",
    "platform/o3-mini",
)

_PROBE_OK_BODY = (
    b'{"choices":[{"message":{"role":"assistant","content":"pong"},'
    b'"finish_reason":"stop"}]}'
)


class _ProbeResp:
    def __init__(self, status_code: int = 200, content: bytes = _PROBE_OK_BODY) -> None:
        self.status_code = status_code
        self.content = content
        self.text = content.decode("utf-8", errors="replace")

    def json(self) -> dict:
        import json

        return json.loads(self.content)


class _ToolCallsResp:
    status_code = 200

    def json(self) -> dict:
        return {"choices": [{"message": {"tool_calls": [{"id": "1"}]}}]}


@pytest.mark.parametrize(
    "model",
    ["kimi-k3", "kimi-k2.5", "kimi-k2.6", "platform/kimi-k2.6"],
)
def test_resolve_omits_temperature_for_kimi_leaf(model: str):
    assert resolve_wire_dialect(model).omit_temperature is True
    assert resolve_wire_dialect(model, base_url=_ZEN_URL).omit_temperature is True
    assert resolve_wire_dialect(model, base_url=_GO_URL).omit_temperature is True


def test_resolve_keeps_temperature_for_moonshot_v1_leaf():
    assert resolve_wire_dialect("moonshot-v1-128k").omit_temperature is False
    assert (
        resolve_wire_dialect("moonshot-v1-128k", base_url=_MOONSHOT_URL).omit_temperature
        is False
    )


def test_resolve_short_k3_omits_only_on_moonshot_base_url():
    assert resolve_wire_dialect("k3").omit_temperature is False
    assert resolve_wire_dialect("k3", base_url=_ZEN_URL).omit_temperature is False
    assert resolve_wire_dialect("k3", base_url=_GO_URL).omit_temperature is False
    assert resolve_wire_dialect("k3", base_url=_MOONSHOT_URL).omit_temperature is True
    assert resolve_wire_dialect("k3", base_url=_MOONSHOT_AI_URL).omit_temperature is True


@pytest.mark.parametrize(
    "model",
    [
        "platform/claude-opus-5",
        "claude-opus-4-7",
        "claude-opus-4.8",
        "anthropic/claude-fable-5",
        "claude-mythos-5",
    ],
)
def test_resolve_anthropic_restricted_leaves_still_omit(model: str):
    assert resolve_wire_dialect(model).omit_temperature is True


def test_resolve_ordinary_models_keep_temperature():
    for model in ("gpt-4o", "deepseek-v4-flash", "claude-opus-4-20250514", "hy3"):
        dialect = resolve_wire_dialect(model)
        assert dialect.omit_temperature is False, model
        assert dialect.use_max_completion_tokens is False, model
        assert dialect.token_limit_field == "max_tokens", model
        assert resolve_wire_dialect(model, base_url=_ZEN_URL).omit_temperature is False, model
        assert resolve_wire_dialect(model, base_url=_GO_URL).omit_temperature is False, model


@pytest.mark.parametrize("model", ["kimi-k3", "kimi-k2.5", "kimi-k2.6"])
def test_build_payload_omits_temperature_for_kimi(model: str):
    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url=_ZEN_URL)
    req = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=model,
        temperature=0.7,
    )
    payload = provider._build_payload(req, stream=False)
    assert "temperature" not in payload


def test_build_payload_keeps_temperature_for_moonshot_v1():
    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url=_MOONSHOT_URL)
    req = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model="moonshot-v1-128k",
        temperature=0.7,
    )
    payload = provider._build_payload(req, stream=False)
    assert payload["temperature"] == 0.7


def test_build_payload_short_k3_omits_on_moonshot_base_url():
    moonshot = OpenAICompatibleProvider(name="test", api_key="k", base_url=_MOONSHOT_URL)
    zen = OpenAICompatibleProvider(name="test", api_key="k", base_url=_ZEN_URL)
    go = OpenAICompatibleProvider(name="test", api_key="k", base_url=_GO_URL)
    req = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model="k3",
        temperature=0.5,
    )
    assert "temperature" not in moonshot._build_payload(req, stream=False)
    assert zen._build_payload(req, stream=False)["temperature"] == 0.5
    assert go._build_payload(req, stream=False)["temperature"] == 0.5


@pytest.mark.parametrize("model", list(_O_SERIES_MODELS))
def test_resolve_o_series_omits_temperature_and_uses_max_completion_tokens(model: str):
    for base_url in (None, _ZEN_URL, _RELAY_URL):
        dialect = resolve_wire_dialect(model, base_url=base_url)
        assert dialect.omit_temperature is True, (model, base_url)
        assert dialect.use_max_completion_tokens is True, (model, base_url)
        assert dialect.token_limit_field == "max_completion_tokens", (model, base_url)


@pytest.mark.parametrize("model", ["o1pus-9", "o3x-chat", "o40b-instruct"])
def test_resolve_o_series_guard_is_not_a_bare_two_char_prefix(model: str):
    """Synthetic boundary ids: the guard keys on exact bare names + ``oN-`` families.

    A bare ``o1`` / ``o3`` / ``o4`` prefix would swallow unrelated leaves and
    silently drop their ``temperature``, which is harder to trace than a 400.
    """
    dialect = resolve_wire_dialect(model, base_url=_RELAY_URL)
    assert dialect.omit_temperature is False, model
    assert dialect.use_max_completion_tokens is False, model
    assert dialect.token_limit_field == "max_tokens", model


def test_resolve_kimi_still_uses_max_tokens_field():
    dialect = resolve_wire_dialect("kimi-k2.6")
    assert dialect.omit_temperature is True
    assert dialect.use_max_completion_tokens is False
    assert dialect.token_limit_field == "max_tokens"


@pytest.mark.parametrize("model", ["o3-mini", "openai/o3-mini", "o1", "o4-mini"])
def test_build_payload_o_series_omits_temperature_and_uses_max_completion_tokens(
    model: str,
):
    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url=_RELAY_URL)
    req = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=model,
        temperature=0.7,
        max_tokens=1024,
    )
    payload = provider._build_payload(req, stream=False)
    assert "temperature" not in payload
    assert "max_tokens" not in payload
    assert payload["max_completion_tokens"] == 1024


def test_build_payload_gpt_4o_keeps_temperature_and_max_tokens():
    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url=_RELAY_URL)
    req = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model="gpt-4o",
        temperature=0.7,
        max_tokens=1024,
    )
    payload = provider._build_payload(req, stream=False)
    assert payload["temperature"] == 0.7
    assert payload["max_tokens"] == 1024
    assert "max_completion_tokens" not in payload


def test_build_payload_kimi_omits_temperature_but_keeps_max_tokens():
    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url=_ZEN_URL)
    req = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model="kimi-k2.6",
        temperature=0.7,
        max_tokens=512,
    )
    payload = provider._build_payload(req, stream=False)
    assert "temperature" not in payload
    assert payload["max_tokens"] == 512
    assert "max_completion_tokens" not in payload


async def test_probe_uses_max_completion_tokens_for_o_series():
    captured: dict = {}

    async def post(*_a, **k):
        captured.update(k.get("json") or {})
        return _ProbeResp()

    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url=_RELAY_URL)
    provider._client.post = post
    try:
        await provider.probe(model="o3-mini")
    finally:
        await provider.close()
    assert "temperature" not in captured
    assert "max_tokens" not in captured
    assert captured["max_completion_tokens"] == 1


async def test_probe_keeps_max_tokens_for_gpt_4o():
    captured: dict = {}

    async def post(*_a, **k):
        captured.update(k.get("json") or {})
        return _ProbeResp()

    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url=_RELAY_URL)
    provider._client.post = post
    try:
        await provider.probe(model="gpt-4o")
    finally:
        await provider.close()
    assert captured["max_tokens"] == 1
    assert "max_completion_tokens" not in captured


async def test_probe_tools_uses_max_completion_tokens_for_o_series():
    captured: dict = {}

    async def post(*_a, **k):
        captured.update(k.get("json") or {})
        return _ToolCallsResp()

    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url=_RELAY_URL)
    provider._client.post = post
    try:
        assert await provider.probe_tools(model="o3-mini") is True
    finally:
        await provider.close()
    assert "max_tokens" not in captured
    assert captured["max_completion_tokens"] == _PROBE_TOOLS_MAX_TOKENS


async def test_probe_tools_keeps_max_tokens_for_gpt_4o():
    captured: dict = {}

    async def post(*_a, **k):
        captured.update(k.get("json") or {})
        return _ToolCallsResp()

    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url=_RELAY_URL)
    provider._client.post = post
    try:
        assert await provider.probe_tools(model="gpt-4o") is True
    finally:
        await provider.close()
    assert captured["max_tokens"] == _PROBE_TOOLS_MAX_TOKENS
    assert "max_completion_tokens" not in captured
