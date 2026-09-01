"""Upstream 400 that means the model rejects images: note + honest fail, never strip-retry."""

from __future__ import annotations

import json

import httpx
import pytest

from agentcore.core.errors import LLMError
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
from agentcore.llm.provider.openai_compatible import (
    OpenAICompatibleProvider,
    _is_images_unsupported_rejection,
    _payload_has_image_url,
)
from agentcore.llm.provider.protocol import LLMMessage, LLMRequest

_IMAGE_REJECT_BODY = b'{"error":{"message":"This model does not support images."}}'
_FORMAT_BODY = b'{"error":{"message":"unsupported image format"}}'
_UNRELATED_BODY = b'{"error":{"message":"max_tokens must be positive"}}'


def _image_content() -> list[dict]:
    return [
        {"type": "text", "text": "看这张图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,aa"}},
    ]


def _image_req() -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content=_image_content())],
        model=DEEPSEEK_V4_FLASH,
        scenario="title",
    )


def _text_req() -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=DEEPSEEK_V4_FLASH,
        scenario="title",
    )


async def _mock_provider(handler) -> OpenAICompatibleProvider:
    provider = OpenAICompatibleProvider(
        name="test", api_key="k", base_url="http://example.invalid/v1"
    )
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url="http://example.invalid/v1",
        transport=httpx.MockTransport(handler),
    )
    return provider


def _patch_note(monkeypatch) -> list[str]:
    noted: list[str] = []
    monkeypatch.setattr(
        "agentcore.llm.provider.openai_compatible._note_images_rejected",
        lambda mid: noted.append(mid),
    )
    return noted


@pytest.mark.parametrize(
    ("status", "body", "expect"),
    [
        (400, "This model does not support images.", True),
        (400, "image_url is not supported by this model", True),
        (400, "该模型不支持图片", True),
        (400, "unsupported image format", False),
        (400, "image format not supported", False),
        (400, "max_tokens must be positive", False),
        (401, "This model does not support images.", False),
    ],
)
def test_is_images_unsupported_rejection_semantics(status: int, body: str, expect: bool):
    assert _is_images_unsupported_rejection(status, body) is expect


def test_payload_has_image_url_detects_parts():
    assert _payload_has_image_url(
        {"messages": [{"role": "user", "content": _image_content()}]}
    )
    assert not _payload_has_image_url(
        {"messages": [{"role": "user", "content": "hi"}]}
    )


async def test_complete_image_reject_400_notes_and_does_not_strip_retry(monkeypatch):
    noted = _patch_note(monkeypatch)
    calls: list[dict] = []

    async def fake_sleep(_sec: float) -> None:
        raise AssertionError("image-reject 400 must not retry")

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content.decode()))
        return httpx.Response(400, content=_IMAGE_REJECT_BODY)

    provider = await _mock_provider(handler)
    try:
        with pytest.raises(LLMError, match="该模型不接受图片") as ei:
            await provider.complete(_image_req())
        assert ei.value.retryable is False
        assert ei.value.details.get("upstream_status") == 400
        assert noted == [DEEPSEEK_V4_FLASH]
        assert len(calls) == 1
        content = calls[0]["messages"][0]["content"]
        assert any(
            isinstance(part, dict) and part.get("type") == "image_url" for part in content
        )
    finally:
        await provider.close()


async def test_stream_image_reject_400_notes_and_does_not_strip_retry(monkeypatch):
    noted = _patch_note(monkeypatch)
    calls = {"n": 0}

    async def fake_sleep(_sec: float) -> None:
        raise AssertionError("image-reject 400 must not retry")

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, content=_IMAGE_REJECT_BODY)

    provider = await _mock_provider(handler)
    try:
        with pytest.raises(LLMError, match="该模型不接受图片"):
            async for _ in provider.stream(_image_req()):
                pass
        assert calls["n"] == 1
        assert noted == [DEEPSEEK_V4_FLASH]
    finally:
        await provider.close()


async def test_complete_image_format_400_does_not_note(monkeypatch):
    noted = _patch_note(monkeypatch)
    calls = {"n": 0}

    async def fake_sleep(_sec: float) -> None:
        raise AssertionError("format 400 must not retry")

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, content=_FORMAT_BODY)

    provider = await _mock_provider(handler)
    try:
        with pytest.raises(LLMError) as ei:
            await provider.complete(_image_req())
        assert "不接受图片" not in ei.value.message
        assert noted == []
        assert calls["n"] == 1
    finally:
        await provider.close()


async def test_complete_text_400_looking_like_image_reject_does_not_note(monkeypatch):
    """No image_url on the wire → even a 'does not support images' 400 is not a note."""
    noted = _patch_note(monkeypatch)
    calls = {"n": 0}

    async def fake_sleep(_sec: float) -> None:
        raise AssertionError("text 400 must not retry")

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, content=_IMAGE_REJECT_BODY)

    provider = await _mock_provider(handler)
    try:
        with pytest.raises(LLMError) as ei:
            await provider.complete(_text_req())
        assert "不接受图片" not in ei.value.message
        assert noted == []
        assert calls["n"] == 1
    finally:
        await provider.close()


async def test_complete_image_unrelated_400_does_not_note(monkeypatch):
    noted = _patch_note(monkeypatch)
    calls = {"n": 0}

    async def fake_sleep(_sec: float) -> None:
        raise AssertionError("unrelated 400 must not retry")

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, content=_UNRELATED_BODY)

    provider = await _mock_provider(handler)
    try:
        with pytest.raises(LLMError) as ei:
            await provider.complete(_image_req())
        assert "max_tokens must be positive" in ei.value.message
        assert "不接受图片" not in ei.value.message
        assert noted == []
        assert calls["n"] == 1
    finally:
        await provider.close()
