"""Leaf call fence: structured llm.call / llm.call_failed via build_provider."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

from agentcore.core.errors import LLMUpstreamError
from agentcore.llm.call_fence import ObservingLLMProvider, observe_provider, unwrap_provider
from agentcore.llm.factory import build_provider
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
from agentcore.llm.provider.protocol import (
    LLMChunk,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    TokenUsage,
)


def _req(*, scenario: str = "chat") -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=DEEPSEEK_V4_FLASH,
        scenario=scenario,
    )


class _FakeLeaf:
    def __init__(self, name: str = "platform") -> None:
        self.name = name
        self._name = name
        self.complete = AsyncMock(
            return_value=LLMResponse(
                content="ok",
                model=DEEPSEEK_V4_FLASH,
                usage=TokenUsage(input_tokens=1, output_tokens=1),
                finish_reason="stop",
                latency_ms=12,
            )
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        yield LLMChunk(delta_content="hi")
        yield LLMChunk(
            finish_reason="stop",
            usage=TokenUsage(input_tokens=2, output_tokens=3),
        )

    def clone(self) -> _FakeLeaf:
        return _FakeLeaf()

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_fence_complete_success_emits_llm_call():
    leaf = _FakeLeaf()
    provider = observe_provider(leaf)
    with capture_logs() as caps:
        resp = await provider.complete(_req(scenario="title"))
    assert resp.content == "ok"
    call = next(c for c in caps if c.get("event") == "llm.call")
    assert call["scenario"] == "title"
    assert call["model"] == DEEPSEEK_V4_FLASH
    assert call["attempt"] == 1
    assert call["stream"] is False
    assert isinstance(call["latency_ms"], int)
    assert call["latency_ms"] >= 0
    assert not any(c.get("event") == "llm.call_failed" for c in caps)


@pytest.mark.asyncio
async def test_fence_complete_failure_emits_llm_call_failed():
    leaf = _FakeLeaf()
    leaf.complete = AsyncMock(
        side_effect=LLMUpstreamError("boom", upstream_status=502, retry_attempts=2)
    )
    provider = observe_provider(leaf)
    with capture_logs() as caps, pytest.raises(LLMUpstreamError):
        await provider.complete(_req())
    failed = next(c for c in caps if c.get("event") == "llm.call_failed")
    assert failed["scenario"] == "chat"
    assert failed["model"] == DEEPSEEK_V4_FLASH
    assert failed["attempt"] == 3  # 0-based retry_attempts=2 → 1-based 3
    assert failed["stream"] is False
    assert "boom" in failed["error"]
    assert "model" in failed
    assert not any(c.get("event") == "llm.call" for c in caps)


@pytest.mark.asyncio
async def test_fence_complete_failure_includes_ambient_credential_fields():
    """llm.call_failed surfaces ambient credential_source + provider_id (no base_url)."""
    from agentcore.core.log_context import bind_log_context, clear_log_context

    leaf = _FakeLeaf(name="user")
    leaf.complete = AsyncMock(
        side_effect=LLMUpstreamError("boom", upstream_status=502, retry_attempts=0)
    )
    provider = observe_provider(leaf)
    clear_log_context()
    bind_log_context(credential_source="user", provider_id="prov-1")
    try:
        with capture_logs() as caps, pytest.raises(LLMUpstreamError):
            await provider.complete(_req())
    finally:
        clear_log_context()
    failed = next(c for c in caps if c.get("event") == "llm.call_failed")
    assert failed["model"] == DEEPSEEK_V4_FLASH
    assert failed["credential_source"] == "user"
    assert failed["provider_id"] == "prov-1"
    assert "base_url" not in failed
    assert "platform_credential_id" not in failed


@pytest.mark.asyncio
async def test_fence_platform_leaf_emits_platform_credential_id(monkeypatch):
    """Platform leaf stamps the pool-member id onto llm.call (not the key)."""
    from agentcore.config import settings
    from agentcore.core.log_context import clear_log_context

    monkeypatch.setattr(settings, "platform_api_key", "sk-default-key")
    monkeypatch.setattr(settings, "platform_base_url", "https://default/v1")
    monkeypatch.setattr(settings, "platform_credential_id", "go-1")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    clear_log_context()
    try:
        provider = observe_provider(_FakeLeaf())
        with capture_logs() as caps:
            await provider.complete(_req())
    finally:
        clear_log_context()
    call = next(c for c in caps if c.get("event") == "llm.call")
    assert call["platform_credential_id"] == "go-1"
    assert "sk-default-key" not in str(call)


@pytest.mark.asyncio
async def test_fence_stream_success_emits_llm_call():
    provider = observe_provider(_FakeLeaf())
    with capture_logs() as caps:
        chunks = [c async for c in provider.stream(_req(scenario="inference.proxy"))]
    assert any(c.delta_content == "hi" for c in chunks)
    call = next(c for c in caps if c.get("event") == "llm.call")
    assert call["scenario"] == "inference.proxy"
    assert call["model"] == DEEPSEEK_V4_FLASH
    assert call["stream"] is True
    assert call["attempt"] == 1
    assert call["output_tokens"] == 3


@pytest.mark.asyncio
async def test_fence_stream_failure_emits_llm_call_failed():
    class _BoomLeaf(_FakeLeaf):
        async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
            yield LLMChunk(delta_content="x")
            raise LLMUpstreamError("stream-down", upstream_status=503, retry_attempts=0)

    provider = observe_provider(_BoomLeaf())
    with capture_logs() as caps, pytest.raises(LLMUpstreamError):
        async for _ in provider.stream(_req()):
            pass
    failed = next(c for c in caps if c.get("event") == "llm.call_failed")
    assert failed["stream"] is True
    assert failed["attempt"] == 1
    assert "stream-down" in failed["error"]
    assert not any(c.get("event") == "llm.call" for c in caps)


@pytest.mark.asyncio
async def test_fence_stream_aclose_with_seen_usage_still_meters(monkeypatch):
    """Consumer aclose after a usage chunk must bill seen tokens (no fabrication)."""
    enqueued: list[TokenUsage] = []

    def _capture_enqueue(*, model, usage, duration_ms, scenario, credential_source):
        enqueued.append(usage)
        return "run-1"

    monkeypatch.setattr(
        "agentcore.billing.call_meter.maybe_enqueue_inprocess_call",
        _capture_enqueue,
    )

    class _UsageThenHang(_FakeLeaf):
        async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
            yield LLMChunk(delta_content="partial")
            yield LLMChunk(usage=TokenUsage(input_tokens=10, output_tokens=4))
            yield LLMChunk(delta_content="more")  # aclose lands here

    provider = observe_provider(_UsageThenHang())
    with capture_logs() as caps:
        gen = provider.stream(_req(scenario="chat"))
        assert (await gen.__anext__()).delta_content == "partial"
        assert (await gen.__anext__()).usage is not None
        await gen.aclose()

    call = next(c for c in caps if c.get("event") == "llm.call")
    assert call["stream"] is True
    assert call["input_tokens"] == 10
    assert call["output_tokens"] == 4
    assert call["finish_reason"] == "stream_closed"
    assert not any(c.get("event") == "llm.call_failed" for c in caps)
    assert len(enqueued) == 1
    assert enqueued[0].input_tokens == 10
    assert enqueued[0].output_tokens == 4


@pytest.mark.asyncio
async def test_fence_stream_aclose_without_usage_does_not_fabricate_bill(monkeypatch):
    """Pure mid-stream aclose with no usage chunk → failed log only, no meter."""
    enqueued: list[object] = []

    def _capture_enqueue(**kwargs):
        enqueued.append(kwargs)
        return "run-1"

    monkeypatch.setattr(
        "agentcore.billing.call_meter.maybe_enqueue_inprocess_call",
        _capture_enqueue,
    )

    class _ContentOnly(_FakeLeaf):
        async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
            yield LLMChunk(delta_content="x")
            yield LLMChunk(delta_content="y")

    provider = observe_provider(_ContentOnly())
    with capture_logs() as caps:
        gen = provider.stream(_req())
        assert (await gen.__anext__()).delta_content == "x"
        await gen.aclose()

    failed = next(c for c in caps if c.get("event") == "llm.call_failed")
    assert failed["error"] == "stream_closed_by_consumer"
    assert failed["error_type"] == "GeneratorExit"
    assert not any(c.get("event") == "llm.call" for c in caps)
    assert enqueued == []


@pytest.mark.asyncio
async def test_fence_stream_failure_with_seen_usage_salvages_meter(monkeypatch):
    """Exception after a usage chunk still meters; failure log remains."""
    enqueued: list[TokenUsage] = []

    def _capture_enqueue(*, model, usage, duration_ms, scenario, credential_source):
        enqueued.append(usage)
        return "run-1"

    monkeypatch.setattr(
        "agentcore.billing.call_meter.maybe_enqueue_inprocess_call",
        _capture_enqueue,
    )

    class _UsageThenBoom(_FakeLeaf):
        async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
            yield LLMChunk(
                finish_reason="stop",
                usage=TokenUsage(input_tokens=7, output_tokens=2),
            )
            raise LLMUpstreamError("after-usage", upstream_status=502, retry_attempts=0)

    provider = observe_provider(_UsageThenBoom())
    with capture_logs() as caps, pytest.raises(LLMUpstreamError):
        async for _ in provider.stream(_req()):
            pass

    assert any(c.get("event") == "llm.call_failed" for c in caps)
    call = next(c for c in caps if c.get("event") == "llm.call")
    assert call["input_tokens"] == 7
    assert call["output_tokens"] == 2
    assert call["finish_reason"] == "stop"
    assert len(enqueued) == 1


@pytest.mark.asyncio
async def test_fence_stream_aborted_with_usage_meters_like_ok(monkeypatch):
    """Post-commit aborted marker + prior usage → normal llm.call (not call_failed)."""
    enqueued: list[TokenUsage] = []

    def _capture_enqueue(*, model, usage, duration_ms, scenario, credential_source):
        enqueued.append(usage)
        return "run-1"

    monkeypatch.setattr(
        "agentcore.billing.call_meter.maybe_enqueue_inprocess_call",
        _capture_enqueue,
    )

    class _AbortWithUsage(_FakeLeaf):
        async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
            yield LLMChunk(delta_content="kept")
            yield LLMChunk(usage=TokenUsage(input_tokens=3, output_tokens=1))
            yield LLMChunk(aborted=True)

    provider = observe_provider(_AbortWithUsage())
    with capture_logs() as caps:
        chunks = [c async for c in provider.stream(_req())]
    assert chunks[-1].aborted is True
    call = next(c for c in caps if c.get("event") == "llm.call")
    assert call["finish_reason"] == "aborted"
    assert call["input_tokens"] == 3
    assert call["output_tokens"] == 1
    assert not any(c.get("event") == "llm.call_failed" for c in caps)
    assert len(enqueued) == 1


def test_build_provider_wraps_with_fence(monkeypatch):
    from agentcore.llm.credentials import LLMCredentials

    provider = build_provider(
        LLMCredentials(
            api_key="k",
            base_url="http://x/v1",
            default_model="deepseek-v4-flash",
            source="user",
        )
    )
    assert isinstance(provider, ObservingLLMProvider)
    assert unwrap_provider(provider).__class__.__name__ == "OpenAICompatibleProvider"
    assert observe_provider(provider) is provider  # idempotent
    # BYOK 设置·测试 calls probe on the fence-wrapped provider — must not AttributeError.
    assert callable(getattr(provider, "probe", None))
    assert callable(getattr(provider, "probe_tools", None))
    assert callable(getattr(provider, "list_models", None))
    assert unwrap_provider(provider)._name == "user"
    assert unwrap_provider(provider).display_name == "服务商"


def test_build_provider_display_name_overrides_leaf_display():
    from agentcore.llm.credentials import LLMCredentials

    provider = build_provider(
        LLMCredentials(
            api_key="k",
            base_url="http://x/v1",
            default_model="deepseek-v4-flash",
            source="user",
        ),
        display_name="我的网关",
    )
    leaf = unwrap_provider(provider)
    assert leaf._name == "user"
    assert leaf.display_name == "我的网关"


def test_build_provider_credentials_label_sets_display():
    from agentcore.llm.credentials import LLMCredentials

    provider = build_provider(
        LLMCredentials(
            api_key="k",
            base_url="http://x/v1",
            default_model="deepseek-v4-flash",
            source="user",
            label="DeepSeek",
        )
    )
    leaf = unwrap_provider(provider)
    assert leaf._name == "user"
    assert leaf.display_name == "DeepSeek"


def test_build_provider_empty_label_falls_back_to_generic():
    from agentcore.llm.credentials import LLMCredentials

    provider = build_provider(
        LLMCredentials(
            api_key="k",
            base_url="http://x/v1",
            default_model="deepseek-v4-flash",
            source="user",
            label="  ",
        )
    )
    leaf = unwrap_provider(provider)
    assert leaf._name == "user"
    assert leaf.display_name == "服务商"


def test_build_provider_clone_preserves_display_name():
    from agentcore.llm.credentials import LLMCredentials

    provider = build_provider(
        LLMCredentials(
            api_key="k",
            base_url="http://x/v1",
            default_model="deepseek-v4-flash",
            source="user",
            label="网关A",
        )
    )
    cloned = unwrap_provider(provider.clone())
    assert cloned._name == "user"
    assert cloned.display_name == "网关A"


def test_build_provider_rejects_none_credentials():
    from agentcore.core.error_codes import ErrorCode
    from agentcore.llm.factory import MissingLLMCredentialsError

    with pytest.raises(MissingLLMCredentialsError) as ei:
        build_provider(None)
    err = ei.value
    assert err.code == ErrorCode.VALIDATION_ERROR
    assert "改选可用模型" in err.message
    assert "设置" not in err.message
    assert "explicit credentials" in (err.details.get("invariant") or "")
    assert "silent" in (err.details.get("invariant") or "")


@pytest.mark.asyncio
async def test_fence_forwards_probe_and_probe_tools():
    class _ProbeLeaf(_FakeLeaf):
        def __init__(self) -> None:
            super().__init__()
            self.probe_model: str | None = None
            self.tools_model: str | None = None
            self.listed = False

        async def probe(self, *, model: str) -> None:
            self.probe_model = model

        async def probe_tools(self, *, model: str) -> bool | None:
            self.tools_model = model
            return True

        async def list_models(self) -> list[str]:
            self.listed = True
            return ["m1"]

    leaf = _ProbeLeaf()
    provider = observe_provider(leaf)
    await provider.probe(model=DEEPSEEK_V4_FLASH)
    assert leaf.probe_model == DEEPSEEK_V4_FLASH
    assert await provider.probe_tools(model=DEEPSEEK_V4_FLASH) is True
    assert leaf.tools_model == DEEPSEEK_V4_FLASH
    assert await provider.list_models() == ["m1"]
    assert leaf.listed is True


@pytest.mark.asyncio
async def test_fence_probe_tools_none_when_leaf_lacks_method():
    provider = observe_provider(_FakeLeaf())  # no probe_tools
    assert await provider.probe_tools(model=DEEPSEEK_V4_FLASH) is None


@pytest.mark.asyncio
async def test_fence_list_models_raises_when_leaf_lacks_method():
    provider = observe_provider(_FakeLeaf())
    with pytest.raises(AttributeError, match="list_models"):
        await provider.list_models()


def test_log_llm_call_includes_attempt():
    from agentcore.llm.observability import log_llm_call

    with capture_logs() as caps:
        log_llm_call(
            scenario="chat",
            model=DEEPSEEK_V4_FLASH,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            finish_reason="stop",
            latency_ms=5,
            stream=False,
            attempt=2,
            credential_source="platform",
        )
    call = next(c for c in caps if c.get("event") == "llm.call")
    assert call["attempt"] == 2
