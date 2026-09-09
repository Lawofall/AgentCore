"""Escalation Gate wiring: ``apply_escalation_gate`` + worker ``react_loop`` path.

Gate 不再因工具输出方案层词产 scheme_escalation；本文件钉死 wiring 静默，
以及结构化 SCOPE payload 仍可走既有 live banner 语义（见 unit 映射单测）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentcore.core.types import ToolFace
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.runtime.engine import ReactLoopOut, react_loop
from agentcore.runtime.engine.escalation_gate import apply_escalation_gate
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.loop_controller import ToolAttempt
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_profile_params


def _tool_chunk(name: str, args: str = "{}", *, call_id: str = "c1") -> LLMChunk:
    return LLMChunk(
        delta_tool_calls=[
            ToolCallDelta(index=0, id=call_id, function_name=name, arguments_delta=args)
        ]
    )


def _content_chunk(text: str) -> LLMChunk:
    return LLMChunk(delta_content=text)


class _ScriptedProvider:
    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk


class _OutputTool:
    def __init__(self, name: str, output: str, *, success: bool = False) -> None:
        self._name = name
        self._output = output
        self._success = success
        self.calls = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            face=ToolFace.SEARCH,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.calls += 1
        return ToolResult(
            tool_call_id="",
            success=self._success,
            output=self._output,
            error="" if self._success else "failed",
        )


def _registry(tool: _OutputTool) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(tool)
    return reg


def _context(*, agent_id: str = "worker-1") -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id=agent_id,
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


class _RecordingSink(EventSink):
    def __init__(self) -> None:
        super().__init__()
        self.emitted = []

    def emit(self, event) -> None:  # noqa: ANN001
        self.emitted.append(event)
        super().emit(event)


def test_apply_escalation_gate_yuequan_report_is_silent():
    """写报告类输出含「越权」不得 live run_escalation kind=scope。"""
    sink = _RecordingSink()
    gate_sink: list[dict] = []
    apply_escalation_gate(
        attempts=[ToolAttempt("fp1", "file_write", success=True)],
        tool_results=[
            LLMMessage(
                role="tool",
                content="报告结论：存在越权风险，超出权限边界。",
                tool_call_id="c1",
            )
        ],
        sink=sink,
        run_id="run-w",
        agent_id="worker-1",
        gate_escalation_sink=gate_sink,
    )

    assert gate_sink == []
    assert not any(e.type == EventType.RUN_ESCALATION_GATE for e in sink.emitted)
    assert not any(e.type == EventType.RUN_ESCALATION for e in sink.emitted)


def test_apply_escalation_gate_scheme_flavored_output_is_silent():
    sink = _RecordingSink()
    gate_sink: list[dict] = []
    apply_escalation_gate(
        attempts=[ToolAttempt("fp1", "file_write", success=False)],
        tool_results=[
            LLMMessage(role="tool", content="超出权限，需改接口契约", tool_call_id="c1")
        ],
        sink=sink,
        run_id="run-w",
        agent_id="worker-1",
        gate_escalation_sink=gate_sink,
    )

    assert gate_sink == []
    assert not any(e.type == EventType.RUN_ESCALATION_GATE for e in sink.emitted)
    assert not any(e.type == EventType.RUN_ESCALATION for e in sink.emitted)


def test_apply_escalation_gate_execution_is_silent():
    sink = _RecordingSink()
    gate_sink: list[dict] = []
    apply_escalation_gate(
        attempts=[ToolAttempt("fp1", "code_execute", success=False)],
        tool_results=[
            LLMMessage(
                role="tool",
                content="Traceback:\nFileNotFoundError: No such file",
                tool_call_id="c1",
            )
        ],
        sink=sink,
        run_id="run-w",
        agent_id="worker-1",
        gate_escalation_sink=gate_sink,
    )

    assert gate_sink == []
    assert not any(e.type == EventType.RUN_ESCALATION_GATE for e in sink.emitted)
    assert not any(e.type == EventType.RUN_ESCALATION for e in sink.emitted)


def test_apply_escalation_gate_ignores_non_tool_attempt_objects():
    """Non-ToolAttempt entries must not crash or fabricate scheme escalations."""
    sink = _RecordingSink()
    gate_sink: list[dict] = []
    apply_escalation_gate(
        attempts=[{"tool_name": "file_write", "success": False}],  # wrong type
        tool_results=[
            LLMMessage(role="tool", content="需求矛盾：A vs B", tool_call_id="c1")
        ],
        sink=sink,
        run_id="run-w",
        agent_id="worker-1",
        gate_escalation_sink=gate_sink,
    )
    assert gate_sink == []
    assert not any(e.type == EventType.RUN_ESCALATION_GATE for e in sink.emitted)


async def test_worker_react_loop_scheme_output_does_not_fill_gate_sink():
    tool = _OutputTool(
        "file_write",
        "继续执行会破坏对外契约 / 改接口契约 / 越权",
        success=False,
    )
    provider = _ScriptedProvider(
        [
            [_tool_chunk("file_write", '{"path":"x"}')],
            [_content_chunk("先做能做的部分")],
        ]
    )
    sink = _RecordingSink()
    gate_sink: list[dict] = []
    await react_loop(
        messages=[LLMMessage(role="user", content="go")],
        llm=provider,
        tools=_registry(tool),
        sink=sink,
        tool_context=_context(agent_id="worker-1"),
        profile=make_profile_params(max_rounds=6),
        turn_model="primary",
        role="worker",
        run_id="run-w",
        out=ReactLoopOut(gate_escalations=gate_sink),
        approval_gate=None,
    )

    assert tool.calls == 1
    assert gate_sink == []
    assert not any(e.type == EventType.RUN_ESCALATION_GATE for e in sink.emitted)
    assert not any(e.type == EventType.RUN_ESCALATION for e in sink.emitted)


async def test_worker_react_loop_execution_failure_does_not_escalate():
    tool = _OutputTool(
        "code_execute",
        "ModuleNotFoundError: No module named 'foo'",
        success=False,
    )
    provider = _ScriptedProvider(
        [
            [_tool_chunk("code_execute", '{"code":"import foo"}')],
            [_content_chunk("改用别的方式")],
        ]
    )
    sink = _RecordingSink()
    gate_sink: list[dict] = []
    content, *_ = await react_loop(
        messages=[LLMMessage(role="user", content="go")],
        llm=provider,
        tools=_registry(tool),
        sink=sink,
        tool_context=_context(agent_id="worker-1"),
        profile=make_profile_params(max_rounds=6),
        turn_model="primary",
        role="worker",
        run_id="run-w",
        out=ReactLoopOut(gate_escalations=gate_sink),
        approval_gate=None,
    )

    assert content == "改用别的方式"
    assert gate_sink == []
    assert not any(e.type == EventType.RUN_ESCALATION_GATE for e in sink.emitted)


async def test_captain_role_does_not_wire_escalation_gate():
    """Same scheme-flavored tool output under captain must not fill gate_sink."""
    tool = _OutputTool(
        "file_write",
        "超出权限，需改接口契约",
        success=False,
    )
    provider = _ScriptedProvider(
        [
            [_tool_chunk("file_write", "{}")],
            [_content_chunk("captain answer")],
        ]
    )
    sink = _RecordingSink()
    gate_sink: list[dict] = []
    await react_loop(
        messages=[LLMMessage(role="user", content="go")],
        llm=provider,
        tools=_registry(tool),
        sink=sink,
        tool_context=_context(agent_id="captain"),
        profile=make_profile_params(max_rounds=6),
        turn_model="primary",
        role="captain",
        run_id="run-c",
        out=ReactLoopOut(gate_escalations=gate_sink),
        approval_gate=None,
    )

    assert gate_sink == []
    assert not any(e.type == EventType.RUN_ESCALATION_GATE for e in sink.emitted)


async def test_worker_without_gate_sink_is_noop_even_on_scheme():
    tool = _OutputTool(
        "file_write",
        "需求矛盾：无法同时满足",
        success=True,
    )
    provider = _ScriptedProvider(
        [
            [_tool_chunk("file_write", "{}")],
            [_content_chunk("ok")],
        ]
    )
    sink = _RecordingSink()
    await react_loop(
        messages=[LLMMessage(role="user", content="go")],
        llm=provider,
        tools=_registry(tool),
        sink=sink,
        tool_context=_context(),
        profile=make_profile_params(max_rounds=6),
        turn_model="primary",
        role="worker",
        run_id="run-w",
        approval_gate=None,
    )
    assert not any(e.type == EventType.RUN_ESCALATION_GATE for e in sink.emitted)
