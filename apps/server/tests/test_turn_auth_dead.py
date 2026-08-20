"""Turn-scoped API-key auth death latch (甲+乙) — unit coverage."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.core.errors import (
    InferenceTokenExpiredError,
    LLMAuthError,
    LLMInsufficientBalanceError,
)
from agentcore.llm.call_fence import ObservingLLMProvider
from agentcore.llm.errors import error_context_from
from agentcore.llm.provider.protocol import LLMMessage, LLMRequest, LLMResponse, TokenUsage
from agentcore.llm.turn_auth_dead import (
    bind_turn_auth_dead,
    is_turn_auth_dead,
    mark_turn_auth_dead,
    raise_if_turn_auth_dead,
    reset_turn_auth_dead,
)


def _req() -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model="test-model",
        scenario="chat",
    )


def test_llm_auth_error_sets_credential_source():
    byok = LLMAuthError(provider_name="user")
    assert byok.details.get("credential_source") == "user"
    assert "请更新后重试" in byok.message
    assert "设置" not in byok.message
    assert "user" not in byok.message

    platform = LLMAuthError(provider_name="platform")
    assert platform.details.get("credential_source") == "platform"
    assert "平台模型暂时不可用" in platform.message
    assert "设置" not in platform.message
    assert "platform" not in platform.message

    ctx = error_context_from(platform)
    assert ctx is not None
    assert ctx.get("credential_source") == "platform"


def test_inference_token_expired_does_not_latch():
    token = bind_turn_auth_dead()
    try:
        assert mark_turn_auth_dead(InferenceTokenExpiredError()) is False
        assert not is_turn_auth_dead()
    finally:
        reset_turn_auth_dead(token)


def test_mark_and_raise_short_circuits_same_turn():
    token = bind_turn_auth_dead()
    try:
        assert mark_turn_auth_dead(LLMAuthError(provider_name="user")) is True
        assert is_turn_auth_dead()
        assert mark_turn_auth_dead(LLMAuthError(provider_name="user")) is False
        with pytest.raises(LLMAuthError) as ei:
            raise_if_turn_auth_dead()
        assert ei.value.details.get("short_circuited") is True
        assert ei.value.details.get("credential_source") == "user"
    finally:
        reset_turn_auth_dead(token)


def test_insufficient_balance_latches_and_preserves_error_class():
    token = bind_turn_auth_dead()
    try:
        assert mark_turn_auth_dead(LLMInsufficientBalanceError()) is True
        assert is_turn_auth_dead()
        assert mark_turn_auth_dead(LLMInsufficientBalanceError()) is False
        with pytest.raises(LLMInsufficientBalanceError) as ei:
            raise_if_turn_auth_dead()
        assert ei.value.details.get("short_circuited") is True
        assert "余额" in (ei.value.message or "")
        assert type(ei.value) is LLMInsufficientBalanceError
        assert ei.value.code == LLMInsufficientBalanceError.code
    finally:
        reset_turn_auth_dead(token)


@pytest.mark.asyncio
async def test_observing_provider_marks_balance_and_short_circuits():
    inner = MagicMock()
    inner.name = "user"
    inner.complete = AsyncMock(side_effect=LLMInsufficientBalanceError())
    fence = ObservingLLMProvider(inner)

    token = bind_turn_auth_dead()
    try:
        with pytest.raises(LLMInsufficientBalanceError):
            await fence.complete(_req())
        assert is_turn_auth_dead()
        assert inner.complete.await_count == 1

        with pytest.raises(LLMInsufficientBalanceError) as ei:
            await fence.complete(_req())
        assert ei.value.details.get("short_circuited") is True
        assert inner.complete.await_count == 1
    finally:
        reset_turn_auth_dead(token)


def test_unbound_turn_does_not_latch_or_raise():
    assert not is_turn_auth_dead()
    assert mark_turn_auth_dead(LLMAuthError(provider_name="platform")) is False
    raise_if_turn_auth_dead()  # no-op


@pytest.mark.asyncio
async def test_observing_provider_marks_and_short_circuits():
    inner = MagicMock()
    inner.name = "user"
    inner.complete = AsyncMock(side_effect=LLMAuthError(provider_name="user"))
    fence = ObservingLLMProvider(inner)

    token = bind_turn_auth_dead()
    try:
        with pytest.raises(LLMAuthError):
            await fence.complete(_req())
        assert is_turn_auth_dead()
        assert inner.complete.await_count == 1

        with pytest.raises(LLMAuthError) as ei:
            await fence.complete(_req())
        assert ei.value.details.get("short_circuited") is True
        assert inner.complete.await_count == 1  # no second upstream hit
    finally:
        reset_turn_auth_dead(token)


@pytest.mark.asyncio
async def test_observing_provider_ignores_inference_token_expiry(monkeypatch):
    monkeypatch.setattr(
        "agentcore.llm.observability.settings.log_llm_bodies",
        False,
    )
    inner = MagicMock()
    inner.name = "platform"
    inner.complete = AsyncMock(side_effect=InferenceTokenExpiredError())
    fence = ObservingLLMProvider(inner)

    token = bind_turn_auth_dead()
    try:
        with pytest.raises(InferenceTokenExpiredError):
            await fence.complete(_req())
        assert not is_turn_auth_dead()
        inner.complete = AsyncMock(
            return_value=LLMResponse(
                content="ok",
                model="test-model",
                finish_reason="stop",
                usage=TokenUsage(input_tokens=1, output_tokens=1),
            )
        )
        out = await fence.complete(_req())
        assert out.content == "ok"
    finally:
        reset_turn_auth_dead(token)


@pytest.mark.asyncio
async def test_run_background_llm_skips_when_auth_dead(monkeypatch):
    from agentcore.billing import gate as gate_mod

    token = bind_turn_auth_dead()
    try:
        mark_turn_auth_dead(LLMAuthError(provider_name="platform"))
        runner = AsyncMock()
        result = await gate_mod.run_background_llm("u1", purpose="title", runner=runner)
        assert result == gate_mod.BackgroundLlmSkip(
            reason=gate_mod.BackgroundSkipReason.TURN_AUTH_DEAD
        )
        runner.assert_not_awaited()
    finally:
        reset_turn_auth_dead(token)


@pytest.mark.asyncio
async def test_wave_stops_new_dispatch_when_auth_dead():
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState
    from agentcore.runtime.runs.wave import WaveScheduler
    from agentcore.runtime.turn.token_budget import resolve_wave_budget_hooks

    token = bind_turn_auth_dead()
    try:
        mark_turn_auth_dead(LLMAuthError(provider_name="user"))
        should_stop, _ = resolve_wave_budget_hooks()
        assert should_stop()

        plan = RunPlan()
        plan.add(RunSpec(run_id="a", role="调研", task="t1", agent_id="a"))
        dispatched: list[str] = []

        async def executor(spec, completed):
            dispatched.append(spec.run_id)
            return RunState(phase=RunPhase.COMPLETED, content="ok")

        results = await WaveScheduler().run(plan, executor, should_stop=should_stop)
        assert dispatched == []
        assert "a" not in results
    finally:
        reset_turn_auth_dead(token)


@pytest.mark.asyncio
async def test_delegate_execute_rejects_when_auth_dead():
    from agentcore.tools.builtin.delegate.tool import DelegateTool

    token = bind_turn_auth_dead()
    try:
        mark_turn_auth_dead(LLMAuthError(provider_name="platform"))
        tool = DelegateTool(
            llm=MagicMock(),
            sink=MagicMock(),
            system_prompt="",
            user_message="hi",
            history=[],
            tools=MagicMock(),
            base_tool_context=MagicMock(),
            approval_gate=None,
        )
        tool._tools.list_all = MagicMock(return_value=[])
        result = await tool.execute(
            {"playbook_id": "none", "playbook_none_reason": "x" * 20, "tasks": []},
            MagicMock(),
        )
        assert result.success is False
        assert result.contract_failure is True
        assert "鉴权" in (result.error or "") or "API Key" in (result.error or "")
    finally:
        reset_turn_auth_dead(token)
