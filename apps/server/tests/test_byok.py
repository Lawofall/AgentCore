"""Unit tests for the DeepSeek connectivity probe.

Pure / mocked — no DB and no network (the probe's HTTP client is stubbed), so
these run in the unit lane. End-to-end resolution, the key routes, and the
billing preflight are covered by tests/integration/test_llm_key_api.py.
Key fingerprints live in tests/test_mask.py.
"""

import httpx
import pytest

from agentcore.core.errors import (
    LLMError,
    LLMInsufficientBalanceError,
    LLMTimeoutError,
)
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider


class _Resp:
    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content
        self.text = content.decode("utf-8", errors="replace")


def _provider(post_handler) -> OpenAICompatibleProvider:
    """A provider whose HTTP ``post`` is stubbed (no real network)."""
    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url="http://example.invalid/v1")
    provider._client.post = post_handler
    return provider


_PROBE_OK_BODY = (
    b'{"choices":[{"message":{"role":"assistant","content":"pong"},'
    b'"finish_reason":"stop"}]}'
)


async def test_probe_passes_on_2xx():
    async def post(*a, **k):
        return _Resp(200, _PROBE_OK_BODY)

    provider = _provider(post)
    try:
        await provider.probe(model=DEEPSEEK_V4_FLASH)  # no raise == reachable
    finally:
        await provider.close()


async def test_probe_rejects_html_shell_on_2xx():
    html = b"<html><body><div id=\"root\"></div></body></html>"

    async def post(*a, **k):
        return _Resp(200, html)

    provider = _provider(post)
    try:
        with pytest.raises(LLMError, match="网页|/v1"):
            await provider.probe(model=DEEPSEEK_V4_FLASH)
    finally:
        await provider.close()


async def test_probe_rejects_empty_2xx_body():
    async def post(*a, **k):
        return _Resp(200, b"")

    provider = _provider(post)
    try:
        with pytest.raises(LLMError, match="空响应"):
            await provider.probe(model=DEEPSEEK_V4_FLASH)
    finally:
        await provider.close()


async def test_probe_treats_429_as_reachable():
    # 429 means the key authenticated (just throttled), so the key IS valid.
    async def post(*a, **k):
        return _Resp(429)

    provider = _provider(post)
    try:
        await provider.probe(model=DEEPSEEK_V4_FLASH)
    finally:
        await provider.close()


@pytest.mark.parametrize("code", [400, 401, 403, 500, 503])
async def test_probe_raises_on_failure_codes(code):
    async def post(*a, **k):
        return _Resp(code)

    provider = _provider(post)
    try:
        with pytest.raises(LLMError):
            await provider.probe(model=DEEPSEEK_V4_FLASH)
    finally:
        await provider.close()


async def test_probe_404_path_blames_base_url_when_body_empty():
    async def post(*a, **k):
        return _Resp(404)

    provider = _provider(post)
    try:
        with pytest.raises(LLMError) as ei:
            await provider.probe(model=DEEPSEEK_V4_FLASH)
        assert "base_url" in str(ei.value)
        assert "默认模型" not in str(ei.value)
    finally:
        await provider.close()


@pytest.mark.parametrize(
    "body",
    [
        b'{"error":{"message":"Not found the model xxx","code":"resource_not_found"}}',
        b'{"error":{"message":"Permission denied: model not allowed","type":"invalid_request_error"}}',
        b'{"error":{"message":"The model `foo` does not exist","code":"model_not_found"}}',
    ],
)
async def test_probe_404_model_guides_change_default_model(body):
    async def post(*a, **k):
        return _Resp(404, content=body)

    provider = _provider(post)
    try:
        with pytest.raises(LLMError) as ei:
            await provider.probe(model="stale-model")
        msg = str(ei.value)
        assert "base_url" not in msg
        assert "默认模型" in msg or "model" in msg.lower() or "Model" in msg
    finally:
        await provider.close()


async def test_probe_404_model_prefers_upstream_error_message():
    body = b'{"error":{"message":"Not found the model gpt-old","code":"resource_not_found"}}'

    async def post(*a, **k):
        return _Resp(404, content=body)

    provider = _provider(post)
    try:
        with pytest.raises(LLMError) as ei:
            await provider.probe(model="gpt-old")
        assert "Not found the model gpt-old" in str(ei.value)
        assert "base_url" not in str(ei.value)
    finally:
        await provider.close()


async def test_probe_maps_402_to_insufficient_balance():
    # 402 = the key authenticated (it IS valid) but the account is out of balance;
    # the probe should tell the user to top up rather than re-check the key.
    async def post(*a, **k):
        return _Resp(402)

    provider = _provider(post)
    try:
        with pytest.raises(LLMInsufficientBalanceError) as ei:
            await provider.probe(model=DEEPSEEK_V4_FLASH)
        assert "余额" in str(ei.value)
    finally:
        await provider.close()


async def test_probe_raises_timeout_on_httpx_timeout():
    async def post(*a, **k):
        raise httpx.TimeoutException("slow")

    provider = _provider(post)
    try:
        with pytest.raises(LLMTimeoutError):
            await provider.probe(model=DEEPSEEK_V4_FLASH)
    finally:
        await provider.close()


# --- tool-calling probe (OpenAICompatibleProvider.probe_tools) ---


class _ToolResp:
    def __init__(self, *, tool_calls: bool = True, status_code: int = 200) -> None:
        self.status_code = status_code
        self._tool_calls = tool_calls

    def json(self) -> dict:
        message: dict = {}
        if self._tool_calls:
            message["tool_calls"] = [{"id": "1"}]
        return {"choices": [{"message": message}]}


class _TextResp:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


async def test_probe_tools_true_when_tool_calls_present():
    async def post(*a, **k):
        return _ToolResp()

    provider = _provider(post)
    try:
        assert await provider.probe_tools(model=DEEPSEEK_V4_FLASH) is True
    finally:
        await provider.close()


async def test_probe_tools_none_when_2xx_without_tool_calls():
    """Endpoint accepted tools but model did not call — unknown, not False."""

    async def post(*a, **k):
        return _ToolResp(tool_calls=False)

    provider = _provider(post)
    try:
        assert await provider.probe_tools(model=DEEPSEEK_V4_FLASH) is None
    finally:
        await provider.close()


async def test_probe_tools_none_on_auth_failure():
    async def post(*a, **k):
        return _Resp(401)

    provider = _provider(post)
    try:
        assert await provider.probe_tools(model=DEEPSEEK_V4_FLASH) is None
    finally:
        await provider.close()


async def test_probe_tools_none_on_timeout():
    async def post(*a, **k):
        raise httpx.TimeoutException("slow")

    provider = _provider(post)
    try:
        assert await provider.probe_tools(model=DEEPSEEK_V4_FLASH) is None
    finally:
        await provider.close()


async def test_probe_tools_none_on_429():
    async def post(*a, **k):
        return _Resp(429)

    provider = _provider(post)
    try:
        assert await provider.probe_tools(model=DEEPSEEK_V4_FLASH) is None
    finally:
        await provider.close()


async def test_probe_tools_false_on_explicit_tools_rejection():
    """Clear 4xx rejection of tools parameter → False (after required→400 fallback)."""
    calls: list[dict | None] = []

    async def post(*a, **k):
        payload = k.get("json") or {}
        calls.append(payload.get("tool_choice"))
        if payload.get("tool_choice") == "required":
            return _TextResp(400, "tool_choice required is not supported")
        return _TextResp(400, "This model does not support tools / function calling")

    provider = _provider(post)
    try:
        assert await provider.probe_tools(model=DEEPSEEK_V4_FLASH) is False
    finally:
        await provider.close()
    assert calls == ["required", None]


async def test_probe_tools_required_400_falls_back_to_auto_true():
    """DeepSeek-style: required → 400, then auto path returns tool_calls → True."""
    calls: list[dict | None] = []

    async def post(*a, **k):
        payload = k.get("json") or {}
        calls.append(payload.get("tool_choice"))
        assert payload.get("max_tokens", 0) >= 256
        if payload.get("tool_choice") == "required":
            return _TextResp(400, "tool_choice=required is not supported in thinking mode")
        return _ToolResp()

    provider = _provider(post)
    try:
        assert await provider.probe_tools(model=DEEPSEEK_V4_FLASH) is True
    finally:
        await provider.close()
    assert calls == ["required", None]


async def test_probe_tools_required_400_falls_back_unknown_without_calls():
    """required → 400, auto → 2xx without tool_calls → None (not False)."""
    calls: list[dict | None] = []

    async def post(*a, **k):
        payload = k.get("json") or {}
        calls.append(payload.get("tool_choice"))
        if payload.get("tool_choice") == "required":
            return _TextResp(400, "tool_choice required not supported")
        return _ToolResp(tool_calls=False)

    provider = _provider(post)
    try:
        assert await provider.probe_tools(model=DEEPSEEK_V4_FLASH) is None
    finally:
        await provider.close()
    assert calls == ["required", None]
