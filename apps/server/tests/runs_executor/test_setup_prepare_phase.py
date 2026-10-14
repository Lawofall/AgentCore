"""worker.prepare_phase: cold-open segments emit phase + ms at info."""

from agentcore.llm.profiles import default_turn_profiles
from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.graph.desk.target_desktop import AppliedTargetDesktop
from agentcore.runtime.events import EventSink
from agentcore.runtime.runs.executor.env import AgentExecutorEnv
from agentcore.runtime.runs.executor.setup import prepare_agent_node
from agentcore.runtime.runs.redrive_sites import (
    ResumeHint,
    ResumeKind,
    bind_resume_hints,
    reset_resume_hints,
)
from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState
from agentcore.tools.registry import ToolRegistry
from agentcore.workspace.write_claims import WriteCoordinator
from tests.conftest import LogSpy
from tests.runs_executor.conftest import _ContentProvider, _ctx, _plan


def _env(plan) -> AgentExecutorEnv:
    return AgentExecutorEnv(
        plan=plan,
        llm=_ContentProvider(["x"]),
        tools=ToolRegistry(),
        sink=EventSink(),
        base_tool_context=_ctx(),
        profiles=default_turn_profiles(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
        delegate_factory=None,
        interaction_bridge=None,
        escalation_timeout=None,
        escalation_armed=False,
        team_brief=None,
        write_coordinator=WriteCoordinator(),
        ancestors_by_id={},
        conversation_id="c",
    )


def _phase_rows(spy: LogSpy) -> list[dict]:
    rows = [kw for name, kw in spy.events if name == "worker.prepare_phase"]
    assert rows, f"expected worker.prepare_phase lines, got {[n for n, _ in spy.events]}"
    for row in rows:
        assert isinstance(row["phase"], str) and row["phase"]
        assert isinstance(row["ms"], int) and row["ms"] >= 0
    return rows


async def test_cold_open_emits_required_prepare_phases(monkeypatch):
    from agentcore.runtime.runs.executor import setup as setup_mod

    spy = LogSpy()
    monkeypatch.setattr(setup_mod, "logger", spy)
    plan = _plan(RunSpec(run_id="w1", agent_id="w1", role="写手", task="写一段"))
    await prepare_agent_node(
        _env(plan),
        plan.by_id("w1"),
        {},
        "w1",
        messages=[],
        resolutions={},
    )
    rows = _phase_rows(spy)
    phases = [row["phase"] for row in rows]
    assert set(phases) >= {"tool_trim", "build_messages", "total"}
    assert "target_desktop" not in phases
    assert phases[-1] == "total"


async def test_target_desktop_phase_emitted_when_folder_set(monkeypatch):
    from agentcore.runtime.runs.executor import setup as setup_mod

    spy = LogSpy()
    monkeypatch.setattr(setup_mod, "logger", spy)

    async def _fake_apply(**kwargs):
        return AppliedTargetDesktop(
            tool_ctx=kwargs["base_tool_context"],
            worker_tools=kwargs["worker_tools"],
            system_prompt=kwargs["env_system_prompt"],
            worker_envelope="",
            target_folder_id=kwargs["target_folder_id"],
        )

    monkeypatch.setattr(
        "agentcore.runtime.graph.desk.target_desktop.apply_target_desktop",
        _fake_apply,
    )
    plan = _plan(
        RunSpec(
            run_id="w1",
            agent_id="w1",
            role="写手",
            task="写一段",
            target_folder_id="fld-1",
        )
    )
    await prepare_agent_node(
        _env(plan),
        plan.by_id("w1"),
        {},
        "w1",
        messages=[],
        resolutions={},
    )
    phases = [row["phase"] for row in _phase_rows(spy)]
    assert "target_desktop" in phases
    assert set(phases) >= {
        "target_desktop",
        "tool_trim",
        "build_messages",
        "total",
    }


async def test_continuation_skips_build_messages_phase(monkeypatch):
    from agentcore.runtime.runs.executor import setup as setup_mod

    spy = LogSpy()
    monkeypatch.setattr(setup_mod, "logger", spy)
    plan = _plan(RunSpec(run_id="w1", agent_id="w1", role="写手", task="写一段"))
    prior = RunState(
        phase=RunPhase.FAILED,
        transcript=[
            LLMMessage(role="system", content="SYS"),
            LLMMessage(role="user", content="task"),
            LLMMessage(role="assistant", content="partial"),
        ],
    )
    token = bind_resume_hints(
        {
            "w1": ResumeHint(
                kind=ResumeKind.INFRA,
                transcript=tuple(prior.transcript),
            )
        }
    )
    try:
        await prepare_agent_node(
            _env(plan),
            plan.by_id("w1"),
            {},
            "w1",
            messages=[],
            resolutions={},
        )
    finally:
        reset_resume_hints(token)
    phases = [row["phase"] for row in _phase_rows(spy)]
    assert "build_messages" not in phases
    assert set(phases) >= {"tool_trim", "total"}


def _pending_write_transcript() -> list[LLMMessage]:
    return [
        LLMMessage(role="system", content="SYS"),
        LLMMessage(role="user", content="task"),
        LLMMessage(
            role="assistant",
            content=None,
            reasoning_content="think-here",
            tool_calls=[
                ToolCall(
                    id="fw",
                    function=ToolCallFunction(
                        name="write",
                        arguments='{"file_path":"a.md","content":"hi"}',
                    ),
                )
            ],
        ),
    ]


async def test_infra_retry_appends_continue_user_after_transcript():
    plan = _plan(RunSpec(run_id="w1", agent_id="w1", role="写手", task="写一段"))
    messages: list[LLMMessage] = []
    transcript = _pending_write_transcript()
    token = bind_resume_hints(
        {"w1": ResumeHint(kind=ResumeKind.INFRA, transcript=tuple(transcript))}
    )
    try:
        await prepare_agent_node(
            _env(plan),
            plan.by_id("w1"),
            {},
            "w1",
            messages=messages,
            resolutions={},
        )
    finally:
        reset_resume_hints(token)
    assert [m.role for m in messages[:3]] == ["system", "user", "assistant"]
    assert messages[-1].role == "user"
    assert "续干指令" in (messages[-1].content or "")
    assert messages[2].tool_calls[0].id == "fw"
    assert messages[2].reasoning_content == "think-here"


async def test_crash_redrive_restores_transcript_without_continue_user():
    plan = _plan(RunSpec(run_id="w1", agent_id="w1", role="写手", task="写一段"))
    messages: list[LLMMessage] = []
    transcript = _pending_write_transcript()
    token = bind_resume_hints(
        {"w1": ResumeHint(kind=ResumeKind.CRASH, transcript=tuple(transcript))}
    )
    try:
        await prepare_agent_node(
            _env(plan),
            plan.by_id("w1"),
            {},
            "w1",
            messages=messages,
            resolutions={},
        )
    finally:
        reset_resume_hints(token)
    assert [m.role for m in messages] == ["system", "user", "assistant"]
    assert not any("续干指令" in (m.content or "") for m in messages if m.role == "user")
    assert messages[-1].tool_calls[0].id == "fw"
    assert messages[-1].reasoning_content == "think-here"
