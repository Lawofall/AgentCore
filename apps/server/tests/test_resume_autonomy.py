"""Resume 沿用用户当前自主度（安全权限与治理 §三）——不再硬编码 FIRST_GRANT。

批 2 授权模型统一后，:func:`resume_chat_pipeline` 的 ``ApprovalGate`` 曾把
``autonomy_policy`` 硬编码为 ``FIRST_GRANT``：挂起期间改了设置的用户，续跑照旧按旧档跑。
本文件钉住修后的两条语义（驱动 REAL ``resume_chat_pipeline``，只换 LLM 与 ApprovalGate 记录桩）：

- 调用方解析出的非默认档（云端 ``conversation/turns.py`` 经 ``resolve_permission_axes``；
  sidecar 经每回合 ``autonomyPolicy`` 参数）原样进 ``ApprovalGate``；
- 调用方缺省时回退 ``first_grant``（与 ``run_chat_pipeline`` 相同的回退，行为不变）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentcore.config import settings
from agentcore.core.types import AutonomyPolicy, recipe_to_axes
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime import pipeline
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.events import EventSink, EventType, FinishReason
from agentcore.runtime.pipeline.resume import pipeline as resume_pipeline_mod
from agentcore.runtime.suspension import AskUserSuspension
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_turn_profiles


class _ScriptedProvider:
    """Fake LLM：每次 ``stream`` 回一轮脚本；超出脚本后回无工具内容，使循环必然收尾。"""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the engine loop
        self.calls += 1
        yield LLMChunk(delta_content="收尾")

    async def close(self) -> None:  # resume_chat_pipeline awaits llm.close() in finally
        return None


class _RecordingGate:
    """ApprovalGate 记录桩：只记构造参数。脚本回合无工具调用，故不会被真正咨询。"""

    instances: list[dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        _RecordingGate.instances.append(kwargs)


def _ask_frame() -> AskUserSuspension:
    """最小 ask_user 挂起帧（同 test_resume_consult_e2e 的载体形状）。"""
    susp = AskUserSuspension(
        message_id="m1",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap1",
        checkpoint_id="ck1",
        tool_call_id="call_ask",
        base_system_prompt="SYS",
        user_message="继续干活",
        transcript=[
            LLMMessage(role="system", content="SYS"),
            LLMMessage(role="user", content="继续干活"),
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
        question="要继续吗？",
    )
    susp.journal_entries = [
        {"kind": EventType.CHECKPOINT_REQUIRED.value, "payload": {}, "ts": "t"}
    ]
    return susp


def _patch_seams(monkeypatch) -> None:
    provider = _ScriptedProvider()

    async def _fake_build_turn_router(*_a, **_k):
        return provider

    monkeypatch.setattr(pipeline, "build_turn_router", _fake_build_turn_router)
    # The point under test: the gate IS constructed (flag on) — with the caller's policy.
    monkeypatch.setattr(settings, "approval_gate_enabled", True)
    monkeypatch.setattr(resume_pipeline_mod, "ApprovalGate", _RecordingGate)
    _RecordingGate.instances = []


def _backend() -> ServerWorkspace:
    return ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox())


async def _run_resume(**kwargs: Any) -> dict:
    return await pipeline.resume_chat_pipeline(
        suspension=_ask_frame(),
        decision=CheckpointDecision.CONTINUE,
        note="继续",
        sink=EventSink(),
        backend=_backend(),
        profile_set=make_turn_profiles(model="chat-model"),
        **kwargs,
    )


async def test_resume_gate_carries_callers_permission_axes(monkeypatch):
    """续跑沿用调用方解析的非默认档——挂起期间改设置，续跑立即生效。"""
    _patch_seams(monkeypatch)

    result = await _run_resume(permission_axes=recipe_to_axes(AutonomyPolicy.CAUTIOUS))

    assert result["finish_reason"] == FinishReason.END_TURN
    assert len(_RecordingGate.instances) == 1
    assert _RecordingGate.instances[0]["permission_axes"] == recipe_to_axes(
        AutonomyPolicy.CAUTIOUS
    )


async def test_resume_gate_defaults_to_less_interrupt_when_caller_omits(monkeypatch):
    """调用方缺省（旧调用点 / 解析失败）⇒ 与 run_chat_pipeline 相同的少打断默认。"""
    _patch_seams(monkeypatch)

    result = await _run_resume()

    assert result["finish_reason"] == FinishReason.END_TURN
    assert len(_RecordingGate.instances) == 1
    assert _RecordingGate.instances[0]["permission_axes"] == recipe_to_axes(
        AutonomyPolicy.LESS_INTERRUPT
    )
