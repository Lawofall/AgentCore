"""Sticky exec-env-dead seeds EXEC_ENV_TIMEOUT_FAMILY into disabled_tools.

Teammates that never hung themselves must still stop seeing ``code_execute`` /
``test_run`` when ``session.exec_env_dead``. ``terminal`` stays offered.
Identity ``can_execute`` is computed after that retire.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcore.core.types import ToolCategory
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.runtime.coordination.session import (
    CoordinationSession,
    clear_active_coordination,
    set_active_coordination,
)
from agentcore.runtime.engine import react_loop
from agentcore.runtime.engine.governance import (
    apply_exec_env_dead_retire,
    create_loop_controller,
    is_exec_env_sticky_dead,
    registry_can_execute,
)
from agentcore.runtime.events import EventSink
from agentcore.runtime.loop_controller.types import EXEC_ENV_TIMEOUT_FAMILY
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.limits import EXEC_ENV_DEAD_CEO_INJECT
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_profile_params

pytestmark = pytest.mark.anyio


def _content_chunk(text: str) -> LLMChunk:
    return LLMChunk(delta_content=text)


def _tool_chunk(name: str, args: str, *, call_id: str = "c") -> LLMChunk:
    return LLMChunk(
        delta_tool_calls=[
            ToolCallDelta(index=0, id=call_id, function_name=name, arguments_delta=args)
        ]
    )


class _StubTool:
    def __init__(self, name: str, *, category: ToolCategory = ToolCategory.EXECUTION) -> None:
        self._name = name
        self._category = category
        self.calls = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=self._category,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        self.calls += 1
        return ToolResult(tool_call_id="", success=True, output="ok")


class _ToolsRecordingProvider:
    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0
        self.offered: list[list[str]] = []

    async def stream(self, request):  # noqa: ANN001
        self.offered.append([t["function"]["name"] for t in (request.tools or [])])
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk


def _server_ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


def test_apply_retire_from_session_exec_env_dead():
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="exec-ee",
        total_workers=2,
        conversation_id="conv-ee",
    )
    session.exec_env_dead = True
    set_active_coordination(session)
    try:
        disabled: set[str] = set()
        controller = create_loop_controller(frozenset())
        assert is_exec_env_sticky_dead() is True
        assert (
            apply_exec_env_dead_retire(
                disabled_tools=disabled,
                controller=controller,
            )
            is True
        )
        for name in EXEC_ENV_TIMEOUT_FAMILY:
            assert name in disabled
        assert "terminal" not in disabled
        assert (
            apply_exec_env_dead_retire(
                disabled_tools=disabled,
                controller=controller,
            )
            is False
        )
    finally:
        clear_active_coordination()


def test_alive_session_does_not_seed_exec_family():
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="exec-live",
        total_workers=1,
        conversation_id="conv-live",
    )
    set_active_coordination(session)
    try:
        disabled: set[str] = set()
        assert is_exec_env_sticky_dead() is False
        assert apply_exec_env_dead_retire(disabled_tools=disabled) is False
        assert disabled == set()
    finally:
        clear_active_coordination()


def test_registry_can_execute_false_after_exec_env_dead_retire():
    """Identity flag is computed after retire — registry still has the tool."""
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="exec-id",
        total_workers=1,
        conversation_id="conv-id",
    )
    session.exec_env_dead = True
    set_active_coordination(session)
    try:
        reg = ToolRegistry()
        reg.register(_StubTool("code_execute"))
        reg.register(_StubTool("test_run"))
        reg.register(_StubTool("terminal", category=ToolCategory.EXECUTION))
        assert reg.get_optional("code_execute") is not None
        assert registry_can_execute(reg) is False
        from agentcore.runtime.runs.executor.identities import build_worker_identity

        body = build_worker_identity(has_dependents=False, can_execute=False)
        assert "本回合执行环境未装配" in body
        assert "已运行 / 已验证 / 已生成" not in body
    finally:
        clear_active_coordination()


async def test_sibling_worker_does_not_offer_code_execute_when_exec_env_dead():
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="exec-react-ee",
        total_workers=2,
        conversation_id="conv-react-ee",
    )
    session.exec_env_dead = True
    set_active_coordination(session)
    try:
        reg = ToolRegistry()
        reg.register(_StubTool("code_execute"))
        reg.register(_StubTool("test_run"))
        reg.register(_StubTool("terminal"))
        reg.register(_StubTool("other", category=ToolCategory.SEARCH))
        assert reg.offer("terminal") is True
        provider = _ToolsRecordingProvider([[_content_chunk("done")]])
        await react_loop(
            messages=[LLMMessage(role="user", content="go")],
            llm=provider,
            tools=reg,
            sink=EventSink(),
            tool_context=_server_ctx(),
            profile=make_profile_params(max_rounds=3),
            turn_model="m",
            run_id="sibling-exec",
            role="worker",
            approval_gate=None,
        )
        assert provider.offered
        offered = provider.offered[0]
        assert "other" in offered
        assert "terminal" in offered
        assert "code_execute" not in offered
        assert "test_run" not in offered
        assert registry_can_execute(reg) is False
    finally:
        clear_active_coordination()


async def test_executor_identity_can_execute_false_when_session_exec_env_dead():
    """New worker: registry still has code_execute, but identity + offer both retire."""
    from agentcore.runtime.runs.builder import build_run_plan
    from agentcore.runtime.runs.executor import build_agent_executor
    from tests.runs_executor.conftest import _ContentProvider, _ctx

    class _Record(_ContentProvider):
        def __init__(self) -> None:
            super().__init__(["OUT"])
            self.tool_names: list[list[str]] = []

        async def stream(self, request):  # noqa: ANN001
            names: list[str] = []
            for item in request.tools or []:
                if isinstance(item, dict):
                    fn = item.get("function") or {}
                    if isinstance(fn, dict) and fn.get("name"):
                        names.append(str(fn["name"]))
            self.tool_names.append(names)
            async for chunk in super().stream(request):
                yield chunk

    clear_active_coordination()
    session = CoordinationSession(
        execution_id="e",
        total_workers=1,
        conversation_id="conv-id-exec",
    )
    session.exec_env_dead = True
    set_active_coordination(session)
    try:
        plan, _ = build_run_plan(
            [{"role": "工程师", "task": "写脚本"}],
            id_prefix="ee",
        )
        provider = _Record()
        reg = ToolRegistry()
        reg.register(_StubTool("code_execute"))
        executor = build_agent_executor(
            plan=plan,
            llm=provider,
            tools=reg,
            sink=EventSink(),
            base_tool_context=_ctx(),
            system_prompt="SYS",
            user_message="原始请求",
            execution_id="e",
            approval_gate=None,
        )
        await executor(plan.nodes[0], {})
        assert provider.system_messages
        assert "本回合执行环境未装配" in provider.system_messages[0]
        assert provider.tool_names
        assert "code_execute" not in provider.tool_names[0]
    finally:
        clear_active_coordination()


async def test_react_loop_round_poll_exec_env_dead_strips_family():
    """Alive at entry; mid-team stamp → next LLM round drops code_execute."""
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="exec-poll-ee",
        total_workers=2,
        conversation_id="conv-poll-ee",
    )
    set_active_coordination(session)
    try:
        reg = ToolRegistry()
        reg.register(_StubTool("code_execute"))
        reg.register(_StubTool("other", category=ToolCategory.SEARCH))

        def _mark_dead_after_round0() -> list[LLMMessage]:
            session.exec_env_dead = True
            return []

        provider = _ToolsRecordingProvider(
            [
                [_content_chunk("r0"), _tool_chunk("other", "{}")],
                [_content_chunk("done")],
            ]
        )
        await react_loop(
            messages=[LLMMessage(role="user", content="go")],
            llm=provider,
            tools=reg,
            sink=EventSink(),
            tool_context=_server_ctx(),
            profile=make_profile_params(max_rounds=5),
            turn_model="m",
            run_id="poll-exec",
            role="worker",
            on_round_begin=_mark_dead_after_round0,
            approval_gate=None,
        )
        assert len(provider.offered) >= 2
        assert "code_execute" in provider.offered[0]
        assert "other" in provider.offered[0]
        assert "code_execute" not in provider.offered[1]
        assert "other" in provider.offered[1]
    finally:
        clear_active_coordination()


def test_ceo_inject_names_exec_env_dead():
    from agentcore.runtime.coordination.inject import format_coordination_events
    from agentcore.runtime.coordination.session import CoordinationEvent, CoordinationEventKind

    session = CoordinationSession(execution_id="exec-inj", total_workers=1)
    session.exec_env_dead = True
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.WORKER_COMPLETED,
                payload={"run_id": "w1", "role": "写手", "status": "completed", "summary": "ok"},
            )
        ],
    )
    assert EXEC_ENV_DEAD_CEO_INJECT in text
    assert "禁止再派需要 code_execute/test_run 的队员" in text
    assert "只读/只写文档可以" in text


def test_ceo_inject_user_stop_unchanged_when_exec_env_dead():
    from agentcore.runtime.coordination.cancel_close import USER_STOPPED_MARK
    from agentcore.runtime.coordination.inject import format_coordination_events
    from agentcore.runtime.coordination.session import CoordinationEvent, CoordinationEventKind

    session = CoordinationSession(execution_id="exec-stop", total_workers=2)
    session.user_stopped = True
    session.exec_env_dead = True
    session._worker_started_at["w1"] = 1.0  # noqa: SLF001
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={"completed": 1, "total": 2, "cancelled": True},
            )
        ],
    )
    assert USER_STOPPED_MARK in text
    assert EXEC_ENV_DEAD_CEO_INJECT in text
    assert "调度中断" not in text
