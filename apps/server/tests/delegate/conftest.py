"""Shared fixtures for DelegateTool tests."""

from __future__ import annotations

import json
from pathlib import Path

from agentcore.llm.provider.protocol import LLMChunk, TokenUsage, ToolCallDelta
from agentcore.runtime.approvals import ApprovalGate
from agentcore.runtime.events import EventSink
from agentcore.runtime.interaction import InteractionRegistry
from agentcore.runtime.runs.types import RunPhase, RunState
from agentcore.tools.builtin import delegation_grantable_tool_names
from agentcore.tools.builtin.delegate import DelegateTool
from agentcore.tools.builtin.escalate import EscalateTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace

# NOTE: the coordination-registry isolation fixture lives in the ROOT tests/conftest.py
# (``_isolate_coordination_registry``). It used to live here, but a directory-level
# autouse fixture silently DROPS when the same directory's files are passed on the CLI
# non-contiguously (delegate file → tests-root file → delegate file): pytest collects
# the directory as two Package nodes and the second one loses this conftest's autouse
# binding — leaking coordination sessions across tests (delegates merge into a stale
# team, 「队员已追加」). Root-conftest autouse survives any argument order.

CKPT_DAG = [
    {"id": "s1", "role": "研究员", "task": "调研", "checkpoint_after": True},
    {"id": "s2", "role": "写手", "task": "撰写", "depends_on": ["s1"]},
]

CKPT_FORK_DAG = [
    {"id": "s1", "role": "研究员", "task": "调研", "checkpoint_after": True},
    {"id": "s2", "role": "写手", "task": "撰写", "depends_on": ["s1"]},
    {"id": "u1", "role": "采购", "task": "比价"},
    {"id": "u2", "role": "出纳", "task": "付款", "depends_on": ["u1"]},
]

LATE_BIND_DAG = [
    {"id": "a", "role": "研究员", "task": "调研"},
    {"id": "b", "role": "待定", "task": "占位", "depends_on": ["a"], "bind_after_deps": True},
]

SCOPE_DAG = [
    {"id": "a", "role": "研究员", "task": "调研真实需求"},
    {"id": "b", "role": "写手", "task": "撰写最终报告", "depends_on": ["a"]},
]


def _upstream_body(marker: str) -> str:
    """Pad a short fixture marker past the upstream handoff body floor."""
    from agentcore.runtime.runs.research_quality import MIN_UPSTREAM_BODY_CHARS

    if len(marker.strip()) >= MIN_UPSTREAM_BODY_CHARS:
        return marker
    return marker + "\n" + ("调研正文填充。" * ((MIN_UPSTREAM_BODY_CHARS // 7) + 1))


class Provider:
    """Fake LLM: one scripted content chunk per call, optionally a usage chunk."""

    def __init__(self, contents: list[str], usage: TokenUsage | None = None) -> None:
        # Pad past MIN_UPSTREAM_BODY_CHARS so handoff accepts short fixture markers.
        self._contents = [_upstream_body(c) for c in contents]
        self._usage = usage
        self.calls = 0
        self.requests: list = []

    async def stream(self, request):
        self.requests.append(request)
        text = self._contents[self.calls] if self.calls < len(self._contents) else "done"
        self.calls += 1
        yield LLMChunk(delta_content=text)
        if self._usage is not None:
            yield LLMChunk(usage=self._usage)


class LocalBackend:
    location = "local"
    root_label = "ws"


class NestingProvider:
    """Fake LLM driving exactly one nested delegation level."""

    CAPTAIN_MARK = "再向下委派一层子团队"

    def __init__(self, usage: TokenUsage | None = None) -> None:
        self._usage = usage
        self.delegate_calls = 0

    async def stream(self, request):
        system = next((m.content or "" for m in request.messages if m.role == "system"), "")
        is_captain = self.CAPTAIN_MARK in system
        has_result = any(m.role == "tool" for m in request.messages)
        # One-level harness: only the first captain nests. Under MAX=3, depth-2 is
        # also captain — do not auto-fan deeper or tree-shape asserts explode.
        if is_captain and not has_result and self.delegate_calls == 0:
            self.delegate_calls += 1
            args = json.dumps(
                {
                    "tasks": [
                        {"role": "子研究员", "task": "子任务A"},
                        {"role": "子写手", "task": "子任务B"},
                    ]
                }
            )
            yield LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0, id="sub-tc", function_name="delegate", arguments_delta=args
                    )
                ]
            )
        elif is_captain:
            yield LLMChunk(delta_content="CAPTAIN_FINAL")
        else:
            yield LLMChunk(delta_content=_upstream_body("SUBOUT"))
        if self._usage is not None:
            yield LLMChunk(usage=self._usage)


class ScopeProvider:
    """Fake LLM where upstream escalates scope deviation then produces output."""

    def __init__(self) -> None:
        self.calls = 0
        self.requests: list = []

    async def stream(self, request):
        self.requests.append(request)
        self.calls += 1
        user = next((m.content or "" for m in request.messages if m.role == "user"), "")
        has_tool_result = any(m.role == "tool" for m in request.messages)
        is_b = "撰写最终报告" in user
        if not is_b and not has_tool_result:
            args = json.dumps(
                {"question": "真问题是X不是Y", "assumption": "暂按X继续", "kind": "scope"}
            )
            yield LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0, id="esc-tc", function_name="escalate", arguments_delta=args
                    )
                ]
            )
            return
        yield LLMChunk(delta_content=_upstream_body("BOUT" if is_b else "AOUT"))


class DepProvider:
    """Fake LLM where upstream escalates a dependency gap (escalate kind=dep, §2.4 变·worker
    的「拉」: 卡在缺输入) then produces output — the reactive-boundary twin of ScopeProvider."""

    def __init__(self) -> None:
        self.calls = 0
        self.requests: list = []

    async def stream(self, request):
        self.requests.append(request)
        self.calls += 1
        user = next((m.content or "" for m in request.messages if m.role == "user"), "")
        has_tool_result = any(m.role == "tool" for m in request.messages)
        is_b = "撰写最终报告" in user
        if not is_b and not has_tool_result:
            args = json.dumps(
                {
                    "question": "缺错误返回结构才能写完整测试",
                    "assumption": "暂按 {code,msg}",
                    "kind": "dep",
                }
            )
            yield LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0, id="esc-tc", function_name="escalate", arguments_delta=args
                    )
                ]
            )
            return
        yield LLMChunk(delta_content=_upstream_body("BOUT" if is_b else "AOUT"))


def ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


def local_ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=LocalBackend(),
        user_id="u",
    )


# 有出生默认：多数委派单测模拟项目会话；裸聊 2b 闸见 test_target_desktop。
_TEST_BIRTH_FOLDER_ID = "test_birth"


def tool(provider: Provider, sink: EventSink | None = None) -> DelegateTool:
    return DelegateTool(
        llm=provider,
        sink=sink or EventSink(),
        system_prompt="SYS",
        user_message="原始请求",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=ctx(),
        approval_gate=None,
        folder_id=_TEST_BIRTH_FOLDER_ID,
    )


def capture_gate(monkeypatch) -> dict:
    captured: dict = {}

    def fake_build(**kwargs):
        captured["gate"] = kwargs.get("approval_gate")

        async def _exec(spec, completed):  # noqa: ANN001
            return RunState(phase=RunPhase.COMPLETED, content="X")

        return _exec

    monkeypatch.setattr("agentcore.runtime.runs.build_agent_executor", fake_build)
    return captured


def gate() -> ApprovalGate:
    return ApprovalGate(
        sink=EventSink(),
        conversation_id="c",
        registry=InteractionRegistry(),
        timeout_seconds=1.0,
        delegation_grantable_tools=delegation_grantable_tool_names(),
    )

def tool_with_gate(ctx: ToolContext, approval_gate: ApprovalGate) -> DelegateTool:
    return DelegateTool(
        llm=Provider(["X"]),
        sink=EventSink(),
        system_prompt="SYS",
        user_message="原始请求",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=ctx,
        approval_gate=approval_gate,
        folder_id=_TEST_BIRTH_FOLDER_ID,
    )


def nesting_tool(provider: NestingProvider, sink: EventSink) -> DelegateTool:
    return DelegateTool(
        llm=provider,
        sink=sink,
        system_prompt="SYS",
        user_message="原始请求",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=ctx(),
        approval_gate=None,
        captain_run_id="CEO",
        folder_id=_TEST_BIRTH_FOLDER_ID,
    )


def tool_ckpt(
    provider: Provider,
    sink: EventSink,
    registry: InteractionRegistry,
    conversation_id: str,
    *,
    timeout: float,
) -> DelegateTool:
    return DelegateTool(
        llm=provider,
        sink=sink,
        system_prompt="SYS",
        user_message="原始请求",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=ctx(),
        approval_gate=None,
        conversation_id=conversation_id,
        registry=registry,
        checkpoint_timeout_seconds=timeout,
        checkpoint_enabled=True,
        folder_id=_TEST_BIRTH_FOLDER_ID,
    )


def tool_durable(
    provider: Provider,
    sink: EventSink,
    registry: InteractionRegistry,
    saver,
    deleter,
    folder_id: str | None = _TEST_BIRTH_FOLDER_ID,
):
    # Pin CAUTIOUS: keeps command=ask / file_write=ask for approval-path tests.
    # (DEFAULT 少打断 now uses team_kickoff=rules and would also show the card.)
    from agentcore.core.types import AutonomyPolicy, recipe_to_axes

    return DelegateTool(
        llm=provider,
        sink=sink,
        system_prompt="SYS",
        user_message="原始请求",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=ctx(),
        approval_gate=None,
        conversation_id="conv1",
        registry=registry,
        checkpoint_timeout_seconds=5.0,
        checkpoint_enabled=True,
        message_id="m1",
        suspension_saver=saver,
        suspension_deleter=deleter,
        captain_run_id="CEO",
        folder_id=folder_id,
        permission_axes=recipe_to_axes(AutonomyPolicy.CAUTIOUS),
    )


def scope_tool(provider: ScopeProvider | DepProvider) -> DelegateTool:
    # Shared by the scope (职责偏离) and dep (依赖缺口) reactive-boundary tests — both ride the
    # same SCOPE boundary, both just need a worker that can call escalate.
    tools = ToolRegistry()
    tools.register(EscalateTool())
    return DelegateTool(
        llm=provider,
        sink=EventSink(),
        system_prompt="SYS",
        user_message="原始请求",
        history=[],
        tools=tools,
        base_tool_context=ctx(),
        approval_gate=None,
        folder_id=_TEST_BIRTH_FOLDER_ID,
    )


def resume_plan(prefix: str = "del_resume"):
    from agentcore.runtime.runs import build_run_plan

    plan, errors = build_run_plan(
        CKPT_DAG,
        valid_tools=set(),
        id_prefix=prefix,
        parent_run_id="CEO",
        depth=1,
    )
    assert not errors
    return plan
