"""Resume must keep ambient ``credential_source`` (BYOK → user, not platform).

Root cause of the prod misbill: ``prepare_chat_turn`` bound pricing context, but
``resume_chat_pipeline`` built the provider without binding — after pause/resume,
``resolve_credential_source`` fell through to ``platform`` and wrote false billed
nano on BYOK calls. This pins the resume bind + ambient pricing behaviour.
"""

from __future__ import annotations

from pathlib import Path

from agentcore.core.log_context import clear_log_context, get_log_value
from agentcore.llm.credentials import LLMCredentials, bind_credential_pricing_context
from agentcore.llm.observability import log_llm_call
from agentcore.llm.pricing import calculate_cost, resolve_credential_source
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
from agentcore.llm.provider.protocol import (
    LLMChunk,
    LLMMessage,
    TokenUsage,
    ToolCall,
    ToolCallFunction,
)
from agentcore.runtime import pipeline
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.events import EventSink, EventType, FinishReason
from agentcore.runtime.facts import LlmCallFact, RoundBoundaryFact, TurnStartedFact
from agentcore.runtime.suspension import AskUserSuspension
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_turn_profiles


class _CapturingProvider:
    """Records ambient ``credential_source`` at each LLM stream entry."""

    def __init__(self) -> None:
        self.seen_sources: list[str] = []

    async def stream(self, request):  # noqa: ANN001
        self.seen_sources.append(get_log_value("credential_source") or "<unset>")
        yield LLMChunk(delta_content="续跑收尾")

    async def close(self) -> None:
        return None


def _ask_frame() -> AskUserSuspension:
    susp = AskUserSuspension(
        message_id="m-cred",
        conversation_id="c-cred",
        user_id="u-cred",
        captain_run_id="cap-cred",
        checkpoint_id="ck-cred",
        tool_call_id="call_ask",
        base_system_prompt="SYS",
        user_message="继续",
        transcript=[
            LLMMessage(role="system", content="SYS"),
            LLMMessage(role="user", content="继续"),
            LLMMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_ask",
                        function=ToolCallFunction(name="ask_user", arguments="{}"),
                    )
                ],
            ),
        ],
        question="继续吗？",
    )
    susp.journal_entries = _ask_pause_journal(
        system_prompt="SYS",
        user_message="继续",
        captain_run_id="cap-cred",
        tool_call_id="call_ask",
    )
    return susp


def _ask_pause_journal(
    *,
    system_prompt: str,
    user_message: str,
    captain_run_id: str,
    tool_call_id: str,
) -> list[dict]:
    """Journal-at-pause for ask_user: head + suspended tool call + checkpoint."""
    return [
        TurnStartedFact(
            system_prompt=system_prompt, user_message=user_message, model_profile="m"
        )
        .to_fact()
        .entry(),
        RoundBoundaryFact(round_idx=0, run_id=captain_run_id, role="captain").to_fact().entry(),
        LlmCallFact(
            run_id=captain_run_id,
            round_idx=0,
            tool_calls=[
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {"name": "ask_user", "arguments": "{}"},
                }
            ],
            finish_reason="tool_calls",
        )
        .to_fact()
        .entry(),
        {"kind": EventType.CHECKPOINT_REQUIRED.value, "payload": {}, "ts": "t"},
    ]


def _byok_creds() -> LLMCredentials:
    return LLMCredentials(
        api_key="sk-test",
        base_url="https://api.deepseek.com",
        default_model=DEEPSEEK_V4_FLASH,
        source="user",
    )


async def test_resume_binds_byok_credential_source_into_log_context(monkeypatch):
    """BYOK resume must leave ambient credential_source=user for the CEO LLM call."""
    clear_log_context()
    provider = _CapturingProvider()

    async def _fake_build_turn_router(*_a, **_k):
        return provider

    monkeypatch.setattr(pipeline, "build_turn_router", _fake_build_turn_router)

    result = await pipeline.resume_chat_pipeline(
        suspension=_ask_frame(),
        decision=CheckpointDecision.CONTINUE,
        note="继续",
        sink=EventSink(),
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        profile_set=make_turn_profiles(model=DEEPSEEK_V4_FLASH),
        llm_credentials=_byok_creds(),
    )

    assert result["finish_reason"] == FinishReason.END_TURN
    assert provider.seen_sources, "resumed CEO loop must call the model"
    assert all(s == "user" for s in provider.seen_sources)


async def test_resume_without_creds_binds_platform_like_prepare(monkeypatch):
    clear_log_context()
    provider = _CapturingProvider()

    async def _fake_build_turn_router(*_a, **_k):
        return provider

    monkeypatch.setattr(pipeline, "build_turn_router", _fake_build_turn_router)

    await pipeline.resume_chat_pipeline(
        suspension=_ask_frame(),
        decision=CheckpointDecision.CONTINUE,
        note="继续",
        sink=EventSink(),
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        profile_set=make_turn_profiles(model=DEEPSEEK_V4_FLASH),
        llm_credentials=None,
    )

    assert provider.seen_sources
    assert all(s == "platform" for s in provider.seen_sources)


def test_bind_credential_pricing_context_keeps_byok_ambient_pricing_zero():
    """Ambient bind alone must make resolve/calculate/log treat the call as BYOK."""
    clear_log_context()
    bind_credential_pricing_context(_byok_creds())
    assert resolve_credential_source() == "user"

    usage = TokenUsage(
        input_tokens=1_000,
        cache_miss_tokens=1_000,
        output_tokens=500,
    )
    priced = calculate_cost(DEEPSEEK_V4_FLASH, usage)
    assert priced.credential_source == "user"
    assert priced.total > 0  # estimate exists
    # Platform billed column stays 0 when log_llm_call reads ambient user source.
    from structlog.testing import capture_logs

    with capture_logs() as caps:
        log_llm_call(
            scenario="chat",
            model=DEEPSEEK_V4_FLASH,
            usage=usage,
            finish_reason="stop",
            latency_ms=1,
            stream=False,
        )
    call = next(c for c in caps if c.get("event") == "llm.call")
    assert call["cost_nano"] == 0
    assert call.get("cost_estimated_nano", 0) > 0
    assert "platform_credential_id" not in call
    assert get_log_value("platform_credential_id") == ""
    clear_log_context()
