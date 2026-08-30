"""Tests for parallel tool execution and per-tool exception firewall (audit/05 P2-1)."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from agentcore.core.errors import SandboxError
from agentcore.core.types import ToolCategory, ToolEffect
from agentcore.llm.provider.protocol import ToolCall, ToolCallFunction
from agentcore.runtime.engine.tool_exec import execute_tools
from agentcore.runtime.engine.tool_failure_face import DEFAULT_TOOL_FAILURE_MESSAGE
from agentcore.runtime.events import EventSink, EventType
from agentcore.tools.builtin.code_execute import CodeExecuteTool
from agentcore.tools.builtin.test_run import TestRunTool
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.protocol import ExecutionRequest, ExecutionResult
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def _ctx(backend=None) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=backend or ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


def _call(tool_id: str, name: str, args: str = "{}") -> ToolCall:
    return ToolCall(id=tool_id, function=ToolCallFunction(name=name, arguments=args))


class _OkTool:
    def __init__(self, name: str = "ok", *, output: str = "done") -> None:
        self._name = name
        self._output = output
        self.executed = False

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.SEARCH,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.executed = True
        return ToolResult(tool_call_id="", success=True, output=self._output)


class _CrashTool:
    def __init__(self, name: str = "crash") -> None:
        self._name = name
        self.executed = False

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.SEARCH,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.executed = True
        raise SandboxError("sandbox blew up")


class _CancelLeakTool:
    """Raises CancelledError without the parent task cancelling (timeout wrap leak)."""

    def __init__(self, name: str = "cancel_leak") -> None:
        self._name = name
        self.executed = False

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.SEARCH,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.executed = True
        raise asyncio.CancelledError()


class _HangTool:
    def __init__(self, name: str = "hang") -> None:
        self._name = name

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.SEARCH,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        await asyncio.Event().wait()
        return ToolResult(tool_call_id="", success=True, output="never")


class _SuspendTool:
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="ask",
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.INTERACTION,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(
            tool_call_id="",
            success=True,
            output="waiting",
            effect=ToolEffect.SUSPEND,
        )


class _HandoffTool:
    def __init__(self, name: str = "handoff") -> None:
        self._name = name

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.ORCHESTRATION,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(
            tool_call_id="",
            success=True,
            output="done",
            effect=ToolEffect.HANDOFF,
            final_text="handoff answer",
        )


class _ContractRejectTool:
    """A tool that returns a self-correctable参数契约拒绝 (web_search A3 shape)."""

    def __init__(self, name: str = "web_search") -> None:
        self._name = name
        self.executed = False

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.RESEARCH,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.executed = True
        return ToolResult(
            tool_call_id="",
            success=False,
            output="",
            error="查询词过多（未加引号部分 8 个词，上限 6）。请改用 2–4 个核心词重试。",
            contract_failure=True,
        )


class _FakeBackend:
    def __init__(self, *, raise_sandbox: bool = False) -> None:
        self._raise_sandbox = raise_sandbox
        self.requests: list[ExecutionRequest] = []

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.requests.append(request)
        if self._raise_sandbox:
            raise SandboxError("代码执行环境启动失败：interpreter missing")
        return ExecutionResult(success=True, stdout="ok\n", stderr="", exit_code=0, duration_ms=1)

    async def read(self, path: str) -> bytes:
        raise FileNotFoundError(path)

    async def index_files(self, *, cap: int = 50, order: str = "recent"):
        return [], 0


@pytest.fixture
def registry() -> tuple[ToolRegistry, _OkTool]:
    ok_b = _OkTool("ok_b", output="beta")
    reg = ToolRegistry()
    reg.register(_OkTool("ok_a", output="alpha"))
    reg.register(ok_b)
    reg.register(_CrashTool())
    reg.register(_CancelLeakTool())
    reg.register(_HangTool())
    reg.register(_SuspendTool())
    return reg, ok_b


async def test_parallel_crash_does_not_cancel_sibling(registry: tuple[ToolRegistry, _OkTool]):
    reg, ok_b = registry
    sink = EventSink()
    messages, terminal, attempts = await execute_tools(
        [_call("c1", "crash"), _call("c2", "ok_b")],
        reg,
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )

    assert ok_b.executed is True
    assert terminal is None
    assert len(messages) == 2
    assert len(attempts) == 2
    assert attempts[0].success is False
    assert attempts[1].success is True
    crash_msg = next(m for m in messages if m.tool_call_id == "c1")
    ok_msg = next(m for m in messages if m.tool_call_id == "c2")
    assert "内部错误" in (crash_msg.content or "")
    assert ok_msg.content == "beta"


async def test_parallel_cancellederror_does_not_cancel_sibling(
    registry: tuple[ToolRegistry, _OkTool],
):
    reg, ok_b = registry
    sink = EventSink()
    messages, terminal, attempts = await execute_tools(
        [_call("c1", "cancel_leak"), _call("c2", "ok_b")],
        reg,
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )

    assert ok_b.executed is True
    assert terminal is None
    assert len(messages) == 2
    assert attempts[0].success is False
    assert attempts[1].success is True
    leak_msg = next(m for m in messages if m.tool_call_id == "c1")
    assert "执行被中止" in (leak_msg.content or "")
    ends = [e for e in sink._history if e.type == EventType.TOOL_USE_END]  # noqa: SLF001
    leak_end = next(e for e in ends if e.payload.get("tool_call_id") == "c1")
    assert leak_end.payload.get("failure", {}).get("code") == "TOOL_ERROR"


async def test_parent_cancel_still_propagates_through_tool_exec(
    registry: tuple[ToolRegistry, _OkTool],
):
    reg, _ok_b = registry

    async def _run() -> None:
        await execute_tools(
            [_call("h1", "hang")],
            reg,
            _ctx(),
            EventSink(),
            approval_gate=None,
            run_id="r1",
        )

    task = asyncio.create_task(_run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_execute_start_logs_read_url_host():
    class _ReadUrlStub:
        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(
                name="read_url",
                description="stub",
                parameters={"type": "object", "properties": {"url": {"type": "string"}}},
                category=ToolCategory.RESEARCH,
            )

        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            return ToolResult(tool_call_id="", success=True, output="ok")

    reg = ToolRegistry()
    reg.register(_ReadUrlStub())
    with capture_logs() as logs:
        await execute_tools(
            [_call("c1", "read_url", '{"url": "https://tldraw.dev/community/license"}')],
            reg,
            _ctx(),
            EventSink(),
            approval_gate=None,
            run_id="r1",
        )
    starts = [e for e in logs if e.get("event") == "tool.execute_start"]
    assert len(starts) == 1
    assert starts[0]["host"] == "tldraw.dev"
    assert "tldraw.dev" in starts[0]["url"]
    ends = [e for e in logs if e.get("event") == "tool.execute_end"]
    assert ends[0]["host"] == "tldraw.dev"


async def test_crash_emits_failed_tool_use_end(registry: tuple[ToolRegistry, _OkTool]):
    reg, _ok_b = registry
    sink = EventSink()
    await execute_tools([_call("c1", "crash")], reg, _ctx(), sink, approval_gate=None, run_id="r1")

    ends = [e for e in sink._history if e.type == EventType.TOOL_USE_END]  # noqa: SLF001
    assert len(ends) == 1
    assert ends[0].payload["status"] == "error"
    # Model face keeps exception detail; user face is curated Chinese.
    assert "SandboxError: sandbox blew up" in (ends[0].payload.get("result") or "")
    assert ends[0].payload.get("failure") == {
        "message": "代码执行环境暂时不可用，请稍后重试。",
        "code": "SANDBOX_ERROR",
    }


async def test_tool_result_error_keeps_model_detail_and_curated_failure():
    """Join path: model ``result`` keeps error+output; ``failure`` never lifts them."""

    class _LeakyTool:
        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(
                name="projects",
                description="x",
                parameters={"type": "object", "properties": {}},
                category=ToolCategory.SEARCH,
            )

        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="列出项目失败。",
                error="FoldersCloudError: folders list unreachable: ConnectError host:5432",
            )

    reg = ToolRegistry()
    reg.register(_LeakyTool())
    sink = EventSink()
    messages, _terminal, _attempts = await execute_tools(
        [_call("c1", "projects", "{}")],
        reg,
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )
    body = messages[0].content or ""
    assert "FoldersCloudError" in body
    assert "host:5432" in body
    ends = [e for e in sink._history if e.type == EventType.TOOL_USE_END]  # noqa: SLF001
    assert ends[0].payload["result"]
    assert "FoldersCloudError" in ends[0].payload["result"]
    assert ends[0].payload["failure"] == {
        "message": DEFAULT_TOOL_FAILURE_MESSAGE,
        "code": "TOOL_ERROR",
    }


async def test_success_tool_use_end_omits_failure(registry: tuple[ToolRegistry, _OkTool]):
    reg, _ok_b = registry
    sink = EventSink()
    await execute_tools([_call("c1", "ok_a")], reg, _ctx(), sink, approval_gate=None, run_id="r1")
    ends = [e for e in sink._history if e.type == EventType.TOOL_USE_END]  # noqa: SLF001
    assert len(ends) == 1
    assert ends[0].payload["status"] == "success"
    assert "failure" not in ends[0].payload


async def test_crash_message_carries_exception_type(registry: tuple[ToolRegistry, _OkTool]):
    """空 str(e) 异常（如裸 NotImplementedError）也必须给模型可读的失败原因。"""

    class _EmptyStrCrashTool(_CrashTool):
        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            raise NotImplementedError

    reg, _ok_b = registry
    reg.register(_EmptyStrCrashTool("crash_empty"))
    sink = EventSink()
    messages, _terminal, _attempts = await execute_tools(
        [_call("c1", "crash_empty"), _call("c2", "crash")],
        reg,
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )

    empty_msg = next(m for m in messages if m.tool_call_id == "c1")
    assert "NotImplementedError" in (empty_msg.content or "")
    assert "：。" not in (empty_msg.content or "")
    # 非空 str(e) 同样带类型名前缀，原文保留。
    crash_msg = next(m for m in messages if m.tool_call_id == "c2")
    assert "SandboxError: sandbox blew up" in (crash_msg.content or "")


class _MissingFileTool:
    def __init__(self, name: str = "missing_file", *, exc: BaseException) -> None:
        self._name = name
        self._exc = exc
        self.executed = False

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.SEARCH,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.executed = True
        raise self._exc


async def test_uncaught_file_not_found_is_contract_rejection_not_crash():
    """Uncaught FileNotFoundError must not leak paths or exception types to the model."""
    import errno

    win_path = r"C:\Users\secret\Projects\missing.txt"
    reg = ToolRegistry()
    reg.register(_MissingFileTool(exc=FileNotFoundError(win_path)))
    sink = EventSink()
    with capture_logs() as logs:
        messages, _terminal, attempts = await execute_tools(
            [_call("c1", "missing_file")],
            reg,
            _ctx(),
            sink,
            approval_gate=None,
            run_id="r1",
        )

    body = messages[0].content or ""
    assert win_path not in body
    assert "FileNotFoundError" not in body
    assert "missing.txt" not in body
    assert "内部资源缺失" in body

    assert len(attempts) == 1
    assert attempts[0].success is False
    assert attempts[0].contract_failure is True
    assert attempts[0].policy_failure is False

    ends = [e for e in sink._history if e.type == EventType.TOOL_USE_END]  # noqa: SLF001
    assert len(ends) == 1
    assert ends[0].payload["status"] == "error"
    assert win_path not in (ends[0].payload.get("result") or "")
    assert ends[0].payload.get("failure") == {
        "message": "未找到所需资源，请换一种方式继续。",
        "code": "NOT_FOUND",
    }

    exec_ends = [e for e in logs if e.get("event") == "tool.execute_end"]
    assert len(exec_ends) == 1
    assert exec_ends[0]["status"] == "error"
    assert exec_ends[0]["status"] != "crash"

    reg2 = ToolRegistry()
    reg2.register(
        _MissingFileTool(
            name="missing_os",
            exc=OSError(errno.ENOENT, "No such file or directory", win_path),
        )
    )
    messages2, _, attempts2 = await execute_tools(
        [_call("c2", "missing_os")],
        reg2,
        _ctx(),
        EventSink(),
        approval_gate=None,
        run_id="r1",
    )
    assert attempts2[0].contract_failure is True
    assert win_path not in (messages2[0].content or "")


async def test_suspend_terminal_unchanged(registry: tuple[ToolRegistry, _OkTool]):
    reg, _ok_b = registry
    sink = EventSink()
    messages, terminal, attempts = await execute_tools(
        [_call("c1", "ask")],
        reg,
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )

    assert terminal is not None
    assert terminal.effect is ToolEffect.SUSPEND
    assert messages == []
    assert len(attempts) == 1
    assert attempts[0].success is True
    # SUSPEND skips durable tool_use_end (挂起即收口) — live UI has *_required already.
    ends = [e for e in sink._history if e.type == EventType.TOOL_USE_END]  # noqa: SLF001
    assert ends == []
    starts = [e for e in sink._history if e.type == EventType.TOOL_USE_START]  # noqa: SLF001
    assert len(starts) == 1


async def test_multi_terminal_prefers_suspend():
    # Defense (audit F6): when a round somehow yields both HANDOFF and SUSPEND,
    # SUSPEND wins (durable pause must not lose to call-order luck). Normal agent
    # toolsets never hold both; this guards the unreachable race.
    reg = ToolRegistry()
    reg.register(_HandoffTool())
    reg.register(_SuspendTool())
    sink = EventSink()
    # HANDOFF listed first — old "first terminal wins" would pick HANDOFF.
    _messages, terminal, _attempts = await execute_tools(
        [_call("c1", "handoff"), _call("c2", "ask")],
        reg,
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )
    assert terminal is not None
    assert terminal.effect is ToolEffect.SUSPEND


async def test_captain_role_strips_run_id_from_sse_but_facts_keep_it(
    registry: tuple[ToolRegistry, _OkTool],
):
    """Display/trace split (CEO 自持工具内联): ``role == "captain"`` self-tools omit
    ``run_id`` on the SSE ``tool_use_*`` wire so the UI renders them as turn-level inline
    steps (matching the conformance ``single_agent`` contract — CEO tools have no run_id),
    while the ``tool_call`` fact still keeps the captain ``run_id`` for §8.3 window fold /
    audit. Workers keep ``run_id`` on the wire (their tools scope to a team-graph run
    node). Locks the runtime fix so a later refactor can't silently re-hide CEO retrieval
    behind '正在思考'."""
    from agentcore.runtime.facts import TurnFactLog, current_fact_log

    reg, _ok_b = registry

    cap_log = TurnFactLog()
    token = current_fact_log.set(cap_log)
    try:
        cap_sink = EventSink()
        await execute_tools(
            [_call("c1", "ok_a")],
            reg,
            _ctx(),
            cap_sink,
            approval_gate=None,
            run_id="cap-run",
            role="captain",
        )
    finally:
        current_fact_log.reset(token)

    cap_events = [
        e
        for e in cap_sink._history  # noqa: SLF001
        if e.type in (EventType.TOOL_USE_START, EventType.TOOL_USE_END)
    ]
    assert cap_events, "captain tool must still emit start/end (only run_id is stripped)"
    assert all("run_id" not in e.payload for e in cap_events)
    tool_facts = [f for f in cap_log.segment_entries() if f["kind"] == "tool_call"]
    assert len(tool_facts) == 1
    assert tool_facts[0]["payload"]["run_id"] == "cap-run"

    wrk_log = TurnFactLog()
    token = current_fact_log.set(wrk_log)
    try:
        wrk_sink = EventSink()
        await execute_tools(
            [_call("w1", "ok_a")],
            reg,
            _ctx(),
            wrk_sink,
            approval_gate=None,
            run_id="w-run",
            role="worker",
        )
    finally:
        current_fact_log.reset(token)

    wrk_events = [
        e
        for e in wrk_sink._history  # noqa: SLF001
        if e.type in (EventType.TOOL_USE_START, EventType.TOOL_USE_END)
    ]
    assert wrk_events
    assert all(e.payload.get("run_id") == "w-run" for e in wrk_events)


async def test_empty_args_rewrite_tool_call_to_valid_json_object():
    """Empty ``function.arguments`` still execute as ``{}``, but the slot is rewritten.

    The next LLM request must send valid JSON (OpenCode Go 400 otherwise). Illegal
    JSON stays on the args_parse_failed path — see the test below.
    """
    tracked = _OkTool("ok_a", output="alpha")
    reg = ToolRegistry()
    reg.register(tracked)
    call = _call("c1", "ok_a", "")
    messages, terminal, attempts = await execute_tools(
        [call],
        reg,
        _ctx(),
        EventSink(),
        approval_gate=None,
        run_id="r1",
    )
    assert terminal is None
    assert tracked.executed is True
    assert attempts[0].success is True
    assert call.function.arguments == "{}"
    assert json.loads(call.function.arguments) == {}
    assert messages[0].role == "tool"


async def test_illegal_json_args_return_explicit_error_not_empty_dict():
    """Illegal tool-call JSON must not silently become ``args={}`` (trace accident chain)."""
    tracked = _OkTool("ok_a", output="alpha")
    reg = ToolRegistry()
    reg.register(tracked)
    # Unescaped quote inside a string — classic model-emitted illegal JSON.
    bad = '{"tasks":[{"role":"研究员","task":"查 "foo" 资料"}]}'
    sink = EventSink()
    with capture_logs() as logs:
        messages, terminal, attempts = await execute_tools(
            [_call("c1", "ok_a", bad)],
            reg,
            _ctx(),
            sink,
            approval_gate=None,
            run_id="r1",
        )

    assert terminal is None
    assert tracked.executed is False
    assert len(messages) == 1
    content = messages[0].content or ""
    assert "不是合法 JSON" in content
    assert "失败位置" in content
    assert "原样重发全部参数" in content
    assert "禁止改写" in content
    assert attempts[0].success is False
    assert attempts[0].parse_failure is True
    assert attempts[0].policy_failure is False

    starts = [e for e in sink._history if e.type == EventType.TOOL_USE_START]  # noqa: SLF001
    ends = [e for e in sink._history if e.type == EventType.TOOL_USE_END]  # noqa: SLF001
    assert len(starts) == 1
    assert starts[0].payload.get("arguments") != {}
    assert starts[0].payload.get("arguments", {}).get("__args_parse_failed__") is True
    assert len(ends) == 1
    assert ends[0].payload["status"] == "error"
    assert "不是合法 JSON" in (ends[0].payload.get("result") or "")

    parse_failed = [e for e in logs if e.get("event") == "tool.args_parse_failed"]
    assert parse_failed
    assert parse_failed[0].get("parse_class") == "escape"


async def test_remember_parse_failure_truncated_vs_escape_copy():
    """remember：truncated → 完整一句/分次；escape → 修转义；截断禁原样重发全部。"""
    tracked = _OkTool("remember", output="ok")
    reg = ToolRegistry()
    reg.register(tracked)

    # Unsalvageable truncated: unclosed key, close still leaves invalid JSON.
    trunc = '{"content": "hello", "bad'
    sink = EventSink()
    with capture_logs() as logs:
        messages, terminal, attempts = await execute_tools(
            [_call("c1", "remember", trunc)],
            reg,
            _ctx(),
            sink,
            approval_gate=None,
            run_id="r1",
        )
    assert terminal is None
    assert tracked.executed is False
    assert attempts[0].parse_failure is True
    content = messages[0].content or ""
    assert "不是合法 JSON" in content
    assert "完整一句" in content
    assert "省略号" in content or "分多" in content
    assert "禁止原样重发" in content or "不要原样重发" in content
    # Escape-default imperative (教原样重发) must not appear on truncated.
    assert "后，原样重发全部参数" not in content
    trunc_logs = [e for e in logs if e.get("event") == "tool.args_parse_failed"]
    assert trunc_logs and trunc_logs[0].get("parse_class") == "truncated"

    escape = '{"content":"查 "foo" 规则"}'
    with capture_logs() as logs2:
        messages2, _, attempts2 = await execute_tools(
            [_call("c2", "remember", escape)],
            reg,
            _ctx(),
            EventSink(),
            approval_gate=None,
            run_id="r2",
        )
    assert attempts2[0].parse_failure is True
    esc = messages2[0].content or ""
    assert "不是合法 JSON" in esc
    assert "转义" in esc
    assert "完整一句" not in esc
    esc_logs = [e for e in logs2 if e.get("event") == "tool.args_parse_failed"]
    assert esc_logs and esc_logs[0].get("parse_class") == "escape"


async def test_default_parse_failure_truncated_forbids_full_replay():
    """default 工具 truncated 禁「原样重发全部」；escape 仍可教修转义后原样重发。"""
    tracked = _OkTool("ok_a", output="alpha")
    reg = ToolRegistry()
    reg.register(tracked)

    trunc = '{"query": "hello", "bad'
    messages, _, attempts = await execute_tools(
        [_call("c1", "ok_a", trunc)],
        reg,
        _ctx(),
        EventSink(),
        approval_gate=None,
        run_id="r1",
    )
    assert attempts[0].parse_failure is True
    body = messages[0].content or ""
    assert "不是合法 JSON" in body
    assert "不要原样重发" in body or "禁止原样重发" in body
    assert "后，原样重发全部参数" not in body
    assert "截断" in body or "缩短" in body or "拆成" in body


async def test_delegate_parse_failure_steers_away_from_nested_arguments():
    """delegate JSON parse fail：明示顶层放 tasks/playbook，禁止再包 arguments。"""
    tracked = _OkTool("delegate", output="ok")
    reg = ToolRegistry()
    reg.register(tracked)
    bad = '{"tasks":[{"role":"A","task":"查 "foo" }]}'
    sink = EventSink()
    messages, terminal, attempts = await execute_tools(
        [_call("c1", "delegate", bad)],
        reg,
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )
    assert terminal is None
    assert tracked.executed is False
    assert attempts[0].parse_failure is True
    content = messages[0].content or ""
    assert "不是合法 JSON" in content
    assert "禁止再包一层" in content or "禁止再包" in content
    assert "arguments" in content
    assert "tasks" in content
    from agentcore.runtime.runs.playbooks import available_playbooks

    assert available_playbooks() not in content


class _CapturingArgsTool:
    def __init__(self, name: str = "delegate") -> None:
        self._name = name
        self.seen_args: dict[str, Any] | None = None

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.SEARCH,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.seen_args = dict(arguments)
        return ToolResult(tool_call_id="", success=True, output="ok")


def test_unwrap_nested_delegate_arguments_success_and_no_false_positive():
    from agentcore.runtime.engine.tool_protocol_sanitize import (
        unwrap_nested_delegate_arguments,
    )

    inner_tasks = [{"role": "A", "task": "短目标"}]
    nested = {"arguments": json.dumps({"tasks": inner_tasks}, ensure_ascii=False)}
    out = unwrap_nested_delegate_arguments(nested)
    assert out is not None
    assert out["tasks"] == inner_tasks

    as_dict = {"arguments": {"playbook": "cite_write_review", "playbook_args": {"topic": "X"}}}
    out2 = unwrap_nested_delegate_arguments(as_dict)
    assert out2 is not None
    assert out2["playbook"] == "cite_write_review"

    # Narrow wrappers: parameters / input sole payload key (same salvage family).
    via_params = {"parameters": {"tasks": inner_tasks}}
    out_p = unwrap_nested_delegate_arguments(via_params)
    assert out_p is not None and out_p["tasks"] == inner_tasks
    via_input = {"input": json.dumps({"tasks": inner_tasks}, ensure_ascii=False)}
    out_i = unwrap_nested_delegate_arguments(via_input)
    assert out_i is not None and out_i["tasks"] == inner_tasks

    # Real top-level tasks + unrelated arguments → do not unwrap.
    mixed = {
        "tasks": inner_tasks,
        "arguments": json.dumps({"tasks": [{"role": "B", "task": "其他"}]}, ensure_ascii=False),
    }
    assert unwrap_nested_delegate_arguments(mixed) is None
    # Top-level tasks + parameters wrapper → no unwrap.
    assert (
        unwrap_nested_delegate_arguments(
            {"tasks": inner_tasks, "parameters": {"tasks": [{"role": "B", "task": "x"}]}}
        )
        is None
    )

    # Empty / garbage inner → no unwrap.
    assert unwrap_nested_delegate_arguments({"arguments": "{not-json"}) is None
    assert unwrap_nested_delegate_arguments({"arguments": {"coordinate": True}}) is None
    assert unwrap_nested_delegate_arguments({"parameters": {"coordinate": True}}) is None
    assert unwrap_nested_delegate_arguments({"input": {"coordinate": True}}) is None
    # Two wrappers at once → no unwrap (ambiguous).
    assert (
        unwrap_nested_delegate_arguments(
            {"arguments": {"tasks": inner_tasks}, "parameters": {"tasks": inner_tasks}}
        )
        is None
    )


async def test_execute_tools_keeps_truncated_args_on_the_honest_failure_path():
    """截断参数不许闭合后执行：工具一次都不能碰，走 args_parse_failed 那张脸。"""
    tracked = _CapturingArgsTool("ok_a")
    reg = ToolRegistry()
    reg.register(tracked)
    raw = '{"query":"半截搜索词'
    sink = EventSink()
    _messages, terminal, attempts = await execute_tools(
        [_call("c1", "ok_a", raw)],
        reg,
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )
    assert terminal is None
    assert attempts[0].success is False
    assert attempts[0].parse_failure is True
    assert tracked.seen_args is None


async def test_execute_tools_salvages_handoff_bare_next_steps():
    """handoff 脏 JSON（裸 next_steps）→ salvage → 工具照常执行，打点 tool.args_salvaged。"""
    tracked = _CapturingArgsTool("handoff")
    reg = ToolRegistry()
    reg.register(tracked)
    bad = '{"summary":"调研完成","key_points":["a"],"next_steps": 请下游去做某事}'
    sink = EventSink()
    with capture_logs() as logs:
        messages, terminal, attempts = await execute_tools(
            [_call("c1", "handoff", bad)],
            reg,
            _ctx(),
            sink,
            approval_gate=None,
            run_id="r1",
        )
    assert attempts[0].success is True
    assert attempts[0].parse_failure is False
    assert tracked.seen_args is not None
    assert tracked.seen_args["next_steps"] == "请下游去做某事"
    assert isinstance(tracked.seen_args["next_steps"], str)
    assert any(entry.get("event") == "tool.args_salvaged" for entry in logs)
    assert not any(entry.get("event") == "tool.args_parse_failed" for entry in logs)
    starts = [e for e in sink._history if e.type == EventType.TOOL_USE_START]  # noqa: SLF001
    assert starts and starts[0].payload.get("arguments", {}).get("next_steps") == "请下游去做某事"


async def test_execute_tools_handoff_unsalvageable_still_parse_fails():
    tracked = _CapturingArgsTool("handoff")
    reg = ToolRegistry()
    reg.register(tracked)
    messages, terminal, attempts = await execute_tools(
        [_call("c1", "handoff", "{@@@}")],
        reg,
        _ctx(),
        EventSink(),
        approval_gate=None,
        run_id="r1",
    )
    assert terminal is None
    assert tracked.seen_args is None
    assert attempts[0].parse_failure is True
    assert "不是合法 JSON" in (messages[0].content or "")


async def test_execute_tools_unwraps_nested_delegate_arguments():
    """json.loads 成功后窄 unwrap：双包 arguments 到达工具时已是顶层 tasks。"""
    tracked = _CapturingArgsTool("delegate")
    reg = ToolRegistry()
    reg.register(tracked)
    inner = {"tasks": [{"role": "调研", "task": "目标+边界+验收"}], "team_brief": "共享口径"}
    wire = json.dumps({"arguments": json.dumps(inner, ensure_ascii=False)}, ensure_ascii=False)
    sink = EventSink()
    with capture_logs() as logs:
        messages, terminal, attempts = await execute_tools(
            [_call("c1", "delegate", wire)],
            reg,
            _ctx(),
            sink,
            approval_gate=None,
            run_id="r1",
        )
    assert terminal is None
    assert attempts[0].success is True
    assert tracked.seen_args is not None
    assert "arguments" not in tracked.seen_args
    assert tracked.seen_args.get("tasks") == inner["tasks"]
    assert tracked.seen_args.get("team_brief") == "共享口径"
    assert any(entry.get("event") == "tool.delegate_arguments_unwrapped" for entry in logs)
    starts = [e for e in sink._history if e.type == EventType.TOOL_USE_START]  # noqa: SLF001
    assert starts and starts[0].payload.get("arguments", {}).get("tasks") == inner["tasks"]


async def test_execute_tools_does_not_unwrap_when_top_level_tasks_present():
    tracked = _CapturingArgsTool("delegate")
    reg = ToolRegistry()
    reg.register(tracked)
    real_tasks = [{"role": "A", "task": "真顶层"}]
    wire = json.dumps(
        {
            "tasks": real_tasks,
            "arguments": json.dumps(
                {"tasks": [{"role": "B", "task": "内层应忽略"}]}, ensure_ascii=False
            ),
        },
        ensure_ascii=False,
    )
    await execute_tools(
        [_call("c1", "delegate", wire)],
        reg,
        _ctx(),
        EventSink(),
        approval_gate=None,
        run_id="r1",
    )
    assert tracked.seen_args is not None
    assert tracked.seen_args["tasks"] == real_tasks
    assert "arguments" in tracked.seen_args


async def test_write_tool_parse_failure_splits_user_and_model_copy():
    """Write-tool JSON parse: ``failure.message`` 人话；模型面 ``result`` 强制分段。"""
    tracked = _OkTool("file_write", output="written")
    reg = ToolRegistry()
    reg.register(tracked)
    bad = '{"path":"r.md","content":"查 "foo" 资料"}'
    sink = EventSink()
    messages, terminal, attempts = await execute_tools(
        [_call("c1", "file_write", bad)],
        reg,
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )
    assert terminal is None
    assert tracked.executed is False
    assert attempts[0].parse_failure is True
    model = messages[0].content or ""
    assert "不是合法 JSON" in model
    assert "分段" in model or "短骨架" in model
    assert "原样重发全部参数" not in model
    ends = [e for e in sink._history if e.type == EventType.TOOL_USE_END]  # noqa: SLF001
    assert len(ends) == 1
    # Model-facing result keeps technical tip (same as transcript, sans marker).
    wire_result = ends[0].payload.get("result") or ""
    assert "不是合法 JSON" in wire_result
    assert "分段" in wire_result or "短骨架" in wire_result
    failure = ends[0].payload.get("failure") or {}
    assert failure.get("message") == "长文保存失败，改成分段写入继续。"
    assert failure.get("code") == "args_parse_failed"
    assert "失败位置" not in (failure.get("message") or "")


async def test_code_execute_maps_sandbox_error_to_failed_result():
    backend = _FakeBackend(raise_sandbox=True)
    result = await CodeExecuteTool().execute(
        {"code": "print(1)", "language": "python"},
        _ctx(backend),  # type: ignore[arg-type]
    )

    assert result.success is False
    assert "代码执行环境启动失败" in (result.error or "")


async def test_execute_tools_denies_tool_outside_allowlist():
    """Least-privilege: registry may hold file_write, but allow-list must block execute."""
    fw = _OkTool("file_write", output="written")
    read = _OkTool("file_read", output="ok")
    reg = ToolRegistry()
    reg.register(fw)
    reg.register(read)
    sink = EventSink()
    messages, terminal, attempts = await execute_tools(
        [_call("c1", "file_write", '{"path":"AgentCore/文档/research/x.md","content":"n"}')],
        reg,
        _ctx(),
        sink,
        approval_gate=None,
        run_id="debate_r1_plaintiff",
        allowed_tool_names=["file_read", "web_search"],
    )
    assert fw.executed is False
    assert terminal is None
    assert len(messages) == 1
    assert attempts[0].success is False
    assert attempts[0].policy_failure is True
    assert "允许列表" in (messages[0].content or "")
    assert "handoff" in (messages[0].content or "") or "escalate" in (messages[0].content or "")
    assert "勿用正文冒充落盘" in (messages[0].content or "")
    assert "产物请改经 handoff 正文回报" not in (messages[0].content or "")
    from agentcore.runtime.engine.tool_exec import TOOL_FAILED_MARKER

    assert TOOL_FAILED_MARKER in (messages[0].content or "")
    ends = [e for e in sink._history if e.type == EventType.TOOL_USE_END]  # noqa: SLF001
    assert len(ends) == 1
    assert ends[0].payload.get("status") == "error"


async def test_execute_tools_rejects_write_landed_imitation_before_allowlist():
    """仿调 `_write_landed` 早拒：落盘状态不是工具；勿落入 allowlist_deny / not_found。"""
    reg = ToolRegistry()
    reg.register(_OkTool("file_write", output="written"))
    sink = EventSink()
    # Allowlist active (would otherwise be allowlist_deny for unknown names).
    messages, terminal, attempts = await execute_tools(
        [
            _call(
                "c1",
                "_write_landed",
                json.dumps(
                    {"status": "landed", "via": "file_write", "path": "docs/a.md", "chars": 10},
                    ensure_ascii=False,
                ),
            )
        ],
        reg,
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r_landed",
        allowed_tool_names=["file_write", "file_read"],
    )
    assert terminal is None
    assert len(messages) == 1
    assert attempts[0].success is False
    assert attempts[0].policy_failure is True
    body = messages[0].content or ""
    assert "_write_landed" in body
    assert "落盘" in body
    assert "不是可调用工具" in body
    assert "允许列表" not in body
    assert "not found" not in body.lower()
    from agentcore.runtime.engine.tool_exec import TOOL_FAILED_MARKER

    assert TOOL_FAILED_MARKER in body
    # No allowlist active → still explicit reject, not generic not_found.
    messages2, _, attempts2 = await execute_tools(
        [_call("c2", "_write_landed", "{}")],
        reg,
        _ctx(),
        EventSink(),
        approval_gate=None,
        allowed_tool_names=None,
    )
    assert attempts2[0].success is False
    body2 = messages2[0].content or ""
    assert "不是可调用工具" in body2
    assert "not found" not in body2.lower()


async def test_execute_tools_allowlist_none_permits_registry_tool():
    fw = _OkTool("file_write", output="written")
    reg = ToolRegistry()
    reg.register(fw)
    messages, _terminal, attempts = await execute_tools(
        [_call("c1", "file_write")],
        reg,
        _ctx(),
        EventSink(),
        approval_gate=None,
        allowed_tool_names=None,
    )
    assert fw.executed is True
    assert attempts[0].success is True
    assert messages[0].content == "written"


async def test_files_touched_uses_execution_success_not_intent():
    """DRIFT fix: denied / failed file_write must not enter files_touched; success must.

    The ledger reads the tool's OWN self-report off the result — a denied call never
    runs (nothing to report) and a failing write reports nothing, so neither can be
    talked into the ledger by the call arguments alone.
    """
    from agentcore.llm.provider.protocol import LLMMessage
    from agentcore.runtime.engine.tool_exec import TOOL_FAILED_MARKER
    from agentcore.runtime.runs.serialize import files_touched_from_transcript
    from agentcore.tools.file_products import file_product

    class _LandingWrite(_OkTool):
        """Self-reports the path it landed, like the real ``file_write``."""

        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            self.executed = True
            return ToolResult(
                tool_call_id="",
                success=True,
                output=self._output,
                file_products=[file_product(str(arguments.get("path") or ""))],
            )

    class _FailWrite(_OkTool):
        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            self.executed = True
            return ToolResult(tool_call_id="", success=False, error="disk full")

    # 1) allowlist deny → marker + no harvest
    fw = _LandingWrite("file_write", output="written")
    reg = ToolRegistry()
    reg.register(fw)
    denied, _, _ = await execute_tools(
        [_call("c1", "file_write", '{"path":"ghost.md","content":"n"}')],
        reg,
        _ctx(),
        EventSink(),
        approval_gate=None,
        allowed_tool_names=["file_read"],
    )
    assistant_deny = LLMMessage(
        role="assistant",
        content=None,
        tool_calls=[_call("c1", "file_write", '{"path":"ghost.md","content":"n"}')],
    )
    assert TOOL_FAILED_MARKER in (denied[0].content or "")
    assert "handoff" in (denied[0].content or "")
    assert files_touched_from_transcript([assistant_deny, denied[0]]) == []

    # 2) successful write → harvested
    ok_reg = ToolRegistry()
    ok_reg.register(_LandingWrite("file_write", output="written"))
    ok_msgs, _, _ = await execute_tools(
        [_call("c2", "file_write", '{"path":"ok.md","content":"y"}')],
        ok_reg,
        _ctx(),
        EventSink(),
        approval_gate=None,
        allowed_tool_names=None,
    )
    assistant_ok = LLMMessage(
        role="assistant",
        content=None,
        tool_calls=[_call("c2", "file_write", '{"path":"ok.md","content":"y"}')],
    )
    assert TOOL_FAILED_MARKER not in (ok_msgs[0].content or "")
    assert files_touched_from_transcript([assistant_ok, ok_msgs[0]]) == ["ok.md"]

    # 3) tool returned success=False → marker + no harvest
    fail_reg = ToolRegistry()
    fail_reg.register(_FailWrite("file_write"))
    fail_msgs, _, _ = await execute_tools(
        [_call("c3", "file_write", '{"path":"io_err.md","content":"z"}')],
        fail_reg,
        _ctx(),
        EventSink(),
        approval_gate=None,
        allowed_tool_names=None,
    )
    assistant_fail = LLMMessage(
        role="assistant",
        content=None,
        tool_calls=[_call("c3", "file_write", '{"path":"io_err.md","content":"z"}')],
    )
    assert TOOL_FAILED_MARKER in (fail_msgs[0].content or "")
    assert files_touched_from_transcript([assistant_fail, fail_msgs[0]]) == []


async def test_execute_tools_forwards_contract_failure_to_attempt():
    """ToolResult.contract_failure 沿 attempt 构造链路传到 ToolAttempt（断路器据此跳过）。"""
    reg = ToolRegistry()
    reg.register(_ContractRejectTool("web_search"))
    messages, terminal, attempts = await execute_tools(
        [_call("c1", "web_search", '{"query":"a b c d e f g h"}')],
        reg,
        _ctx(),
        EventSink(),
        approval_gate=None,
        run_id="r1",
    )
    assert terminal is None
    assert attempts[0].success is False
    assert attempts[0].contract_failure is True
    assert attempts[0].policy_failure is False
    assert "查询词过多" in (messages[0].content or "")


async def test_execute_end_error_carries_aggregable_reason():
    """status=error 的 tool.execute_end 必须带简短可聚合 reason（排查勿靠相邻事件）。"""
    reg = ToolRegistry()
    reg.register(_ContractRejectTool("web_search"))
    with capture_logs() as logs:
        await execute_tools(
            [_call("c1", "web_search", '{"query":"a b c d e f g h"}')],
            reg,
            _ctx(),
            EventSink(),
            approval_gate=None,
            run_id="r1",
        )
    ends = [e for e in logs if e.get("event") == "tool.execute_end"]
    assert len(ends) == 1
    assert ends[0]["status"] == "error"
    assert ends[0]["tool"] == "web_search"
    reason = ends[0].get("reason") or ""
    assert "查询词过多" in reason
    assert "\n" not in reason


async def test_execute_end_ok_omits_reason():
    reg = ToolRegistry()
    reg.register(_OkTool())
    with capture_logs() as logs:
        await execute_tools(
            [_call("c1", "ok")],
            reg,
            _ctx(),
            EventSink(),
            approval_gate=None,
            run_id="r1",
        )
    ends = [e for e in logs if e.get("event") == "tool.execute_end"]
    assert len(ends) == 1
    assert ends[0]["status"] == "ok"
    assert "reason" not in ends[0]


class _CodeSearchMetaTool:
    """Stub that mirrors code_search metadata (index_status) for execute_end forward."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="code_search",
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.FILESYSTEM,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(
            tool_call_id="",
            success=True,
            output="hit",
            metadata={"match_count": 1, "index_status": "ready"},
        )


async def test_execute_end_forwards_code_search_index_status():
    reg = ToolRegistry()
    reg.register(_CodeSearchMetaTool())
    with capture_logs() as logs:
        await execute_tools(
            [_call("c1", "code_search", '{"query":"ApprovalGate"}')],
            reg,
            _ctx(),
            EventSink(),
            approval_gate=None,
            run_id="r1",
        )
    ends = [e for e in logs if e.get("event") == "tool.execute_end"]
    assert len(ends) == 1
    assert ends[0]["status"] == "ok"
    assert ends[0]["tool"] == "code_search"
    assert ends[0]["index_status"] == "ready"


_SHELL_OBSERVE_KEYS = frozenset({"command_preview", "subcommand", "cwd_preview"})


def test_shell_observe_log_fields_records_facts_not_write_guess():
    from agentcore.core.text import clip_preview
    from agentcore.runtime.engine.tool_exec_args import (
        _SHELL_COMMAND_PREVIEW_MAX,
        _shell_observe_log_fields,
    )

    start = _shell_observe_log_fields(
        "terminal",
        {"subcommand": "start", "command": "pnpm dev", "cwd": "apps/web"},
    )
    assert start == {
        "subcommand": "start",
        "command_preview": "pnpm dev",
        "cwd_preview": "apps/web",
    }
    listed = _shell_observe_log_fields("terminal", {"subcommand": "list"})
    assert listed == {"subcommand": "list"}
    host = _shell_observe_log_fields("host", {"command": "Get-ChildItem"})
    assert host == {"command_preview": "Get-ChildItem"}
    secret_tail = "TOKEN=supersecret"
    long_cmd = "echo hello " + ("n" * 200) + " " + secret_tail
    clipped = _shell_observe_log_fields("host", {"command": long_cmd})
    assert clipped["command_preview"] == clip_preview(long_cmd, _SHELL_COMMAND_PREVIEW_MAX)
    assert secret_tail not in clipped["command_preview"]
    assert set(clipped) <= _SHELL_OBSERVE_KEYS
    assert _shell_observe_log_fields("code_execute", {"command": long_cmd}) == {}
    assert _shell_observe_log_fields("file_write", {"path": "a.py", "command": "x"}) == {}
    assert _shell_observe_log_fields("terminal", "not-a-dict") == {}
    license_url = "https://www.tldraw.dev/community/license"
    url_fields = _shell_observe_log_fields("read_url", {"url": license_url})
    assert url_fields["host"] == "tldraw.dev"
    assert url_fields["url"].startswith("https://")
    assert "license" in url_fields["url"]
    key_url = "https://example.com/x?OPENAI_API_KEY=sk-abcdefgh12345678"
    redacted_url = _shell_observe_log_fields("read_url", {"url": key_url})
    assert "sk-abcdefgh12345678" not in redacted_url["url"]
    assert redacted_url["host"] == "example.com"


def test_shell_observe_redacts_secret_shapes_clipping_would_keep():
    """Clip 不等于脱敏：短命令里的 key / Bearer 必须被 redact 掉。

    ``logging.mdc`` 禁记 token。上一个用例的 secret 靠超长命令被 clip 掉，覆盖不到
    「凭据出现在命令开头」这一形状——那时 clip 是空操作。启发式兜底，非完整检测器。
    """
    from agentcore.core.secrets import REDACTED
    from agentcore.runtime.engine.tool_exec_args import _shell_observe_log_fields

    key_cmd = "OPENAI_API_KEY=sk-abcdefgh12345678 node run.js"
    key_preview = _shell_observe_log_fields("host", {"command": key_cmd})[
        "command_preview"
    ]
    assert "sk-abcdefgh12345678" not in key_preview
    assert REDACTED in key_preview
    # 非凭据部分保留，否则这条埋点就没法用来判断命令在干什么。
    assert "node run.js" in key_preview

    bearer_preview = _shell_observe_log_fields(
        "terminal",
        {"subcommand": "start", "command": 'curl -H "Authorization: Bearer abcdefgh1234" api'},
    )["command_preview"]
    assert "abcdefgh1234" not in bearer_preview
    assert REDACTED in bearer_preview


async def test_execute_end_terminal_list_records_subcommand():
    """terminal list 免审，execute_end 记 subcommand 事实。"""
    reg = ToolRegistry()
    reg.register(_OkTool("terminal"))
    with capture_logs() as logs:
        await execute_tools(
            [_call("c1", "terminal", json.dumps({"subcommand": "list"}))],
            reg,
            _ctx(),
            EventSink(),
            approval_gate=None,
            run_id="r1",
        )
    ends = [e for e in logs if e.get("event") == "tool.execute_end"]
    assert len(ends) == 1
    end = ends[0]
    assert end["tool"] == "terminal"
    assert end["status"] == "ok"
    assert end["subcommand"] == "list"
    assert "command_preview" not in end
    assert "is_write" not in end


async def test_execute_end_terminal_start_no_gate_still_records_preview():
    """无闸 terminal start 拒执行，仍记 command_preview（只观测，不猜写盘）。"""
    reg = ToolRegistry()
    reg.register(_OkTool("terminal"))
    args = json.dumps(
        {
            "subcommand": "start",
            "command": "echo hello",
            "cwd": "src",
        }
    )
    with capture_logs() as logs:
        await execute_tools(
            [_call("c1", "terminal", args)],
            reg,
            _ctx(),
            EventSink(),
            approval_gate=None,
            run_id="r1",
        )
    ends = [e for e in logs if e.get("event") == "tool.execute_end"]
    assert len(ends) == 1
    end = ends[0]
    assert end["tool"] == "terminal"
    assert end["status"] == "grantable_no_gate"
    assert end["subcommand"] == "start"
    assert end["command_preview"] == "echo hello"
    assert end["cwd_preview"] == "src"
    assert "is_write" not in end


async def test_execute_end_host_records_command_preview():
    """无闸 host(action=shell) 升审批后拒执行，仍记 command_preview。"""
    reg = ToolRegistry()
    # Stub NEVER：schema NEVER，运行时按 action 升审批（与真 HostTool 同）。
    reg.register(_OkTool("host"))
    args = json.dumps({"action": "shell", "command": "hostname"})
    with capture_logs() as logs:
        await execute_tools(
            [_call("c1", "host", args)],
            reg,
            _ctx(),
            EventSink(),
            approval_gate=None,
            run_id="r1",
        )
    ends = [e for e in logs if e.get("event") == "tool.execute_end"]
    assert len(ends) == 1
    end = ends[0]
    assert end["tool"] == "host"
    assert end["status"] == "grantable_no_gate"
    assert end["command_preview"] == "hostname"
    assert "subcommand" not in end
    assert "is_write" not in end


async def test_execute_end_other_tool_omits_command_preview():
    """command 参数只对 terminal/host 进 execute_end，避免扩大命令泄漏面。"""
    reg = ToolRegistry()
    reg.register(_OkTool("ok"))
    with capture_logs() as logs:
        await execute_tools(
            [_call("c1", "ok", json.dumps({"command": "echo should-not-log"}))],
            reg,
            _ctx(),
            EventSink(),
            approval_gate=None,
            run_id="r1",
        )
    ends = [e for e in logs if e.get("event") == "tool.execute_end"]
    assert len(ends) == 1
    assert ends[0]["tool"] == "ok"
    assert "command_preview" not in ends[0]
    assert "cwd_preview" not in ends[0]


@pytest.mark.asyncio
async def test_same_batch_handoff_waits_for_sibling_write(tmp_path: Path):
    """同批 file_write+handoff：handoff 须在 write 之后执行，才能看到 prose stamp。"""
    import asyncio
    import json

    from agentcore.tools.builtin.file_ops import FileWriteTool
    from agentcore.tools.builtin.handoff import HandoffTool

    order: list[str] = []
    prose = "# 报告\n\n" + ("这是实质正文段落。" * 50)
    assert len(prose) >= 400

    class _SlowWrite(FileWriteTool):
        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            order.append("write_start")
            await asyncio.sleep(0.05)
            result = await super().execute(arguments, context)
            order.append("write_end")
            return result

    class _OrderHandoff(HandoffTool):
        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            order.append("handoff")
            return await super().execute(arguments, context)

    reg = ToolRegistry()
    reg.register(_SlowWrite())
    reg.register(_OrderHandoff())
    ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox()),
        user_id="u",
        handoff_requires_body=True,
        round_content_chars=0,
    )
    write_args = json.dumps({"path": "notes.md", "content": prose}, ensure_ascii=False)
    handoff_args = json.dumps({"summary": "调研已落盘"}, ensure_ascii=False)
    # handoff listed first — without phasing it would race ahead of slow write.
    messages, terminal, attempts = await execute_tools(
        [
            _call("c_h", "handoff", handoff_args),
            _call("c_w", "file_write", write_args),
        ],
        reg,
        ctx,
        EventSink(),
        # 云端沙箱上的 worker 写文件：按 sandbox_approval 免逐次卡（本例测的是同批次序）。
        approval_gate=None,
        run_id="r1",
        role="worker",
    )
    assert order[:2] == ["write_start", "write_end"]
    assert order[-1] == "handoff"
    assert attempts[0].success is True  # handoff (call order)
    assert attempts[1].success is True  # write
    assert terminal is not None
    assert terminal.success is True
    assert ctx.landed_artifact_kinds.get("notes.md") == "prose"
    assert len(messages) == 2


async def test_test_run_maps_sandbox_error_to_failed_result(monkeypatch: pytest.MonkeyPatch):
    backend = _FakeBackend(raise_sandbox=True)

    async def _profile(_backend):
        from agentcore.runtime.context.workspace_profile import WorkspaceProfile

        return WorkspaceProfile(
            languages=["python"],
            frameworks=[],
            package_managers=[],
            test_commands=["pytest"],
        )

    async def _framework(_backend, _profile, _arg):
        return "pytest"

    monkeypatch.setattr(
        "agentcore.tools.builtin.test_run.detect_workspace_profile",
        _profile,
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.test_run._detect_framework",
        _framework,
    )

    result = await TestRunTool().execute({"scope": "all"}, _ctx(backend))  # type: ignore[arg-type]

    assert result.success is False
    assert "代码执行环境启动失败" in (result.error or "")


async def test_parallel_same_path_file_read_coalesces_once(tmp_path: Path):
    """Same-round parallel file_read on one path+window → one underlying read, fan-out."""
    from agentcore.tools.builtin.file_ops import FileReadTool

    (tmp_path / "doc.md").write_text("# Hello\nshared body\n", encoding="utf-8")
    backend = ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())
    reads = {"n": 0}
    orig_read_lines = backend.read_lines

    async def _counting_read_lines(path: str, *args: Any, **kwargs: Any):
        reads["n"] += 1
        return await orig_read_lines(path, *args, **kwargs)

    backend.read_lines = _counting_read_lines  # type: ignore[method-assign]

    reg = ToolRegistry()
    reg.register(FileReadTool())
    ctx = _ctx(backend)
    sink = EventSink()
    args = '{"path": "doc.md"}'
    messages, terminal, attempts = await execute_tools(
        [
            _call("r1", "file_read", args),
            _call("r2", "file_read", args),
            _call("r3", "file_read", args),
        ],
        reg,
        ctx,
        sink,
        approval_gate=None,
        run_id="r1",
    )

    assert terminal is None
    assert len(messages) == 3
    assert all(a.success for a in attempts)
    assert reads["n"] == 1
    assert ctx.file_read_counts.get("doc.md") == 1
    bodies = [m.content or "" for m in messages]
    assert all("shared body" in b for b in bodies)
    # Each call still gets its own tool_use_start/end on the wire.
    starts = [e for e in sink._history if e.type == EventType.TOOL_USE_START]  # noqa: SLF001
    ends = [e for e in sink._history if e.type == EventType.TOOL_USE_END]  # noqa: SLF001
    assert {e.payload["tool_call_id"] for e in starts} == {"r1", "r2", "r3"}
    assert {e.payload["tool_call_id"] for e in ends} == {"r1", "r2", "r3"}


async def test_parallel_distinct_path_file_reads_not_coalesced(tmp_path: Path):
    """Different paths in one round still each execute (no cross-path fan-out)."""
    from agentcore.tools.builtin.file_ops import FileReadTool

    (tmp_path / "a.md").write_text("AAA", encoding="utf-8")
    (tmp_path / "b.md").write_text("BBB", encoding="utf-8")
    backend = ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())
    reads = {"n": 0}
    orig_read_lines = backend.read_lines

    async def _counting_read_lines(path: str, *args: Any, **kwargs: Any):
        reads["n"] += 1
        return await orig_read_lines(path, *args, **kwargs)

    backend.read_lines = _counting_read_lines  # type: ignore[method-assign]

    reg = ToolRegistry()
    reg.register(FileReadTool())
    ctx = _ctx(backend)
    messages, _terminal, attempts = await execute_tools(
        [
            _call("r1", "file_read", '{"path": "a.md"}'),
            _call("r2", "file_read", '{"path": "b.md"}'),
        ],
        reg,
        ctx,
        EventSink(),
        approval_gate=None,
        run_id="r1",
    )
    assert all(a.success for a in attempts)
    assert reads["n"] == 2
    assert ctx.file_read_counts.get("a.md") == 1
    assert ctx.file_read_counts.get("b.md") == 1
    by_id = {m.tool_call_id: m.content or "" for m in messages}
    assert "AAA" in by_id["r1"]
    assert "BBB" in by_id["r2"]


async def test_parallel_same_path_different_window_file_reads_not_coalesced(
    tmp_path: Path,
):
    """Same path, different offset/limit → each window executes (no first-window fan-out)."""
    from agentcore.tools.builtin.file_ops import FileReadTool

    (tmp_path / "doc.md").write_text("L1\nL2\nL3\nL4\n", encoding="utf-8")
    backend = ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())
    reads = {"n": 0}
    orig_read_lines = backend.read_lines

    async def _counting_read_lines(path: str, *args: Any, **kwargs: Any):
        reads["n"] += 1
        return await orig_read_lines(path, *args, **kwargs)

    backend.read_lines = _counting_read_lines  # type: ignore[method-assign]

    reg = ToolRegistry()
    reg.register(FileReadTool())
    ctx = _ctx(backend)
    messages, _terminal, attempts = await execute_tools(
        [
            _call("r1", "file_read", '{"path": "doc.md", "offset": 1, "limit": 1}'),
            _call("r2", "file_read", '{"path": "doc.md", "offset": 3, "limit": 1}'),
        ],
        reg,
        ctx,
        EventSink(),
        approval_gate=None,
        run_id="r1",
    )
    assert all(a.success for a in attempts)
    assert reads["n"] == 2
    by_id = {m.tool_call_id: m.content or "" for m in messages}
    assert "L1" in by_id["r1"]
    assert "L3" in by_id["r2"]
    assert "L1" not in by_id["r2"]
    assert "L3" not in by_id["r1"]
    assert "第 1–1 行" in by_id["r1"]
    assert "第 3–3 行" in by_id["r2"]


async def test_ceo_str_replace_miss_still_audience_deny():
    """CEO 面缺写盘工具仍走 audience_deny / delegate 提示。"""
    reg = ToolRegistry()
    reg.register(_OkTool("delegate"))
    ctx = _ctx()
    messages, _terminal, attempts = await execute_tools(
        [_call("c1", "str_replace", "{}")],
        reg,
        ctx,
        EventSink(),
        approval_gate=None,
        run_id="",
        role="captain",
    )
    content = messages[0].content or ""
    assert "delegate" in content.lower() or "派工" in content
    assert "form=prose" not in content


@pytest.mark.parametrize("name", ["str_replace", "file_copy"])
async def test_write_allowlist_deny_no_handoff_as_write(name: str):
    """写盘工具不在 allowlist 时说明缺授权，勿劝 handoff 正文冒充落盘。"""
    reg = ToolRegistry()
    reg.register(_OkTool(name))
    messages, _terminal, attempts = await execute_tools(
        [_call("c1", name, "{}")],
        reg,
        _ctx(),
        EventSink(),
        approval_gate=None,
        run_id="r1",
        allowed_tool_names=["file_read", "handoff"],  # write tool not allowed
    )
    content = messages[0].content or ""
    assert "不在本 run 的允许列表" in content or "未授权" in content
    assert "勿用正文冒充落盘" in content
    assert "产物请改经 handoff 正文回报" not in content
    assert attempts[0].success is False
    assert attempts[0].policy_failure is True


class _GrantableBrowser:
    executed = False

    @property
    def schema(self) -> ToolSchema:
        from agentcore.core.types import ToolApproval

        return ToolSchema(
            name="browser",
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.EXECUTION,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.executed = True
        return ToolResult(tool_call_id="", success=True, output="ok")


class _GateThatMustNotPrompt:
    permission_axes = None

    def will_prompt(self, *args, **kwargs) -> bool:
        raise AssertionError("captain browser must not prompt")

    async def authorize(self, *args, **kwargs):
        raise AssertionError("captain browser must not authorize")


async def test_captain_browser_navigate_skips_approval_gate():
    """CEO：captain 直调 browser(action=navigate) 不弹审批。"""
    tool = _GrantableBrowser()
    reg = ToolRegistry()
    reg.register(tool)
    await execute_tools(
        [_call("c1", "browser", '{"action":"navigate","url":"https://example.com"}')],
        reg,
        _ctx(),
        EventSink(),
        run_id="cap-run",
        role="captain",
        approval_gate=_GateThatMustNotPrompt(),  # type: ignore[arg-type]
    )
    assert tool.executed is True


async def test_captain_browser_click_skips_approval_gate():
    """CEO 短操作：captain 直调 browser(action=click) 亦不弹审批。"""
    tool = _GrantableBrowser()
    reg = ToolRegistry()
    reg.register(tool)
    await execute_tools(
        [_call("c1", "browser", '{"action":"click","ref":"e1"}')],
        reg,
        _ctx(),
        EventSink(),
        run_id="cap-run",
        role="captain",
        approval_gate=_GateThatMustNotPrompt(),  # type: ignore[arg-type]
    )
    assert tool.executed is True


async def test_captain_browser_screenshot_does_not_skip_approval_gate():
    """captain + action=screenshot 不走短操作免审（force_breaker 同理仍拦）。"""
    tool = _GrantableBrowser()
    reg = ToolRegistry()
    reg.register(tool)
    await execute_tools(
        [_call("c1", "browser", '{"action":"screenshot"}')],
        reg,
        _ctx(),
        EventSink(),
        run_id="cap-run",
        role="captain",
        approval_gate=None,
    )
    assert tool.executed is False


async def test_captain_legacy_browser_navigate_does_not_skip_gate():
    """执行层不转发旧名：captain 调 browser_navigate 不走 browser 免审。"""
    from agentcore.core.types import ToolApproval

    class _LegacyNavigate:
        executed = False

        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(
                name="browser_navigate",
                description="stub",
                parameters={"type": "object", "properties": {}},
                category=ToolCategory.EXECUTION,
                approval=ToolApproval.GRANTABLE,
            )

        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            self.executed = True
            return ToolResult(tool_call_id="", success=True, output="opened")

    tool = _LegacyNavigate()
    reg = ToolRegistry()
    reg.register(tool)
    await execute_tools(
        [_call("c1", "browser_navigate", '{"url":"https://example.com"}')],
        reg,
        _ctx(),
        EventSink(),
        run_id="cap-run",
        role="captain",
        approval_gate=None,
    )
    assert tool.executed is False


def _drain_events(sink: EventSink) -> list:
    events = []
    while not sink._queue.empty():  # noqa: SLF001
        events.append(sink._queue.get_nowait())
    return events


async def test_cloud_worker_file_write_ask_still_prompts():
    """云端 worker + file_write=ask：写文件类必须走审批（谨慎名副其实）。"""
    import asyncio

    from agentcore.core.types import AutonomyPolicy, ToolApproval, recipe_to_axes
    from agentcore.runtime.approvals import ApprovalDecision, ApprovalGate
    from agentcore.runtime.interaction import InteractionRegistry
    from agentcore.tools.builtin import approval_class_tool_names

    class _WriteTool:
        executed = False

        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(
                name="file_write",
                description="stub",
                parameters={"type": "object", "properties": {}},
                category=ToolCategory.FILESYSTEM,
                approval=ToolApproval.GRANTABLE,
            )

        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            self.executed = True
            return ToolResult(tool_call_id="", success=True, output="wrote")

    sink = EventSink()
    registry = InteractionRegistry()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-cloud-ask",
        registry=registry,
        timeout_seconds=5.0,
        file_op_tools=approval_class_tool_names(),
        permission_axes=recipe_to_axes(AutonomyPolicy.CAUTIOUS),
    )
    tool = _WriteTool()
    reg = ToolRegistry()
    reg.register(tool)

    async def _approve() -> None:
        for _ in range(2000):
            if registry.resolve(
                "tc-cloud-ask", ApprovalDecision.APPROVE, conversation_id="conv-cloud-ask"
            ):
                return
            await asyncio.sleep(0)
        raise AssertionError("approval never pending")

    approve_task = asyncio.create_task(_approve())
    messages, terminal, attempts = await execute_tools(
        [_call("tc-cloud-ask", "file_write", '{"path":"a.md","content":"x"}')],
        reg,
        _ctx(),  # ServerWorkspace → location=server
        sink,
        approval_gate=gate,
        run_id="worker-1",
        role="worker",
    )
    await approve_task
    assert terminal is None
    assert tool.executed is True
    assert attempts[0].success is True
    assert messages[0].content == "wrote"
    required = [e for e in _drain_events(sink) if e.type is EventType.APPROVAL_REQUIRED]
    assert len(required) == 1


async def test_cloud_worker_file_write_session_still_ungated():
    """云端 worker + file_write=session：写文件仍免逐次卡（少打断/托管）。"""
    from agentcore.core.types import AutonomyPolicy, ToolApproval, recipe_to_axes
    from agentcore.runtime.approvals import ApprovalGate
    from agentcore.runtime.interaction import InteractionRegistry
    from agentcore.tools.builtin import approval_class_tool_names

    class _WriteTool:
        executed = False

        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(
                name="file_write",
                description="stub",
                parameters={"type": "object", "properties": {}},
                category=ToolCategory.FILESYSTEM,
                approval=ToolApproval.GRANTABLE,
            )

        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            self.executed = True
            return ToolResult(tool_call_id="", success=True, output="wrote")

    sink = EventSink()
    registry = InteractionRegistry()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-cloud-session",
        registry=registry,
        timeout_seconds=5.0,
        file_op_tools=approval_class_tool_names(),
        permission_axes=recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT),
    )
    # Cloud session path must drop needs_approval before authorize/will_prompt.
    gate.will_prompt = (  # type: ignore[method-assign]
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("session cloud file_write must not prompt")
        )
    )
    gate.authorize = (  # type: ignore[method-assign]
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("session cloud file_write must not authorize")
        )
    )

    tool = _WriteTool()
    reg = ToolRegistry()
    reg.register(tool)
    messages, terminal, attempts = await execute_tools(
        [_call("tc-cloud-sess", "file_write", '{"path":"a.md","content":"x"}')],
        reg,
        _ctx(),
        sink,
        approval_gate=gate,
        run_id="worker-1",
        role="worker",
    )
    assert terminal is None
    assert tool.executed is True
    assert attempts[0].success is True
    assert messages[0].content == "wrote"


class _GrantableWrite:
    """A GRANTABLE file tool — the class that used to slip through un-asked."""

    executed = False

    @property
    def schema(self) -> ToolSchema:
        from agentcore.core.types import ToolApproval

        return ToolSchema(
            name="file_write",
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.executed = True
        return ToolResult(tool_call_id="", success=True, output="wrote")


async def test_grantable_without_gate_is_denied_not_run():
    """本该有闸却没传 → 拒绝执行，而不是放行（gate 缺席不再等于免审）。

    桌面 worker 的 file_write 该弹卡（``sandbox_approval`` 只对云端沙箱免卡）。若这条
    路的 gate 漏传了，从前会因为「GRANTABLE 判定挂在 gate 存在性上」直接执行；现在先算
    「要不要审批」，再看「有没有人可问」，问不到就拒。
    """

    class _DesktopBackend:
        location = "local"
        root_label = "ws"

    tool = _GrantableWrite()
    reg = ToolRegistry()
    reg.register(tool)
    with capture_logs() as logs:
        messages, terminal, attempts = await execute_tools(
            [_call("tc-nogate", "file_write", '{"path":"a.md","content":"x"}')],
            reg,
            _ctx(backend=_DesktopBackend()),
            EventSink(),
            approval_gate=None,
            run_id="worker-1",
            role="worker",
        )
    assert tool.executed is False
    assert terminal is None
    assert attempts[0].success is False
    assert "没有可询问的用户" in (messages[0].content or "")
    ends = [e for e in logs if e.get("event") == "tool.execute_end"]
    assert [e["status"] for e in ends] == ["grantable_no_gate"]


async def test_grantable_without_gate_denied_on_cloud_captain_path():
    """云端免卡只给 worker：船长路径漏传 gate 同样拒绝，不借沙箱豁免蒙混过关。"""
    tool = _GrantableWrite()
    reg = ToolRegistry()
    reg.register(tool)
    with capture_logs() as logs:
        _messages, _terminal, attempts = await execute_tools(
            [_call("tc-nogate-cap", "file_write", '{"path":"a.md","content":"x"}')],
            reg,
            _ctx(),  # ServerWorkspace → 云端
            EventSink(),
            approval_gate=None,
            run_id="cap-run",
            role="captain",
        )
    assert tool.executed is False
    assert attempts[0].success is False
    ends = [e for e in logs if e.get("event") == "tool.execute_end"]
    assert [e["status"] for e in ends] == ["grantable_no_gate"]


async def test_cloud_code_execute_outer_timeout_is_sandbox_unavailable():
    from agentcore.tools.sandbox.exec_env import (
        EXEC_ENV_SANDBOX_UNAVAILABLE_CODE,
        EXEC_ENV_SANDBOX_UNAVAILABLE_USER_MESSAGE,
    )

    class _HangExec:
        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(
                name="code_execute",
                description="stub",
                parameters={"type": "object", "properties": {}},
                category=ToolCategory.EXECUTION,
                timeout_seconds=0.05,
            )

        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            await asyncio.sleep(5)
            return ToolResult(tool_call_id="", success=True, output="never")

    reg = ToolRegistry()
    reg.register(_HangExec())
    sink = EventSink()
    messages, _terminal, attempts = await execute_tools(
        [_call("c1", "code_execute")],
        reg,
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )
    assert attempts[0].success is False
    assert attempts[0].meta.get("code") == EXEC_ENV_SANDBOX_UNAVAILABLE_CODE
    assert "code_execute" in attempts[0].meta.get("retire_tools", [])
    assert "test_run" in attempts[0].meta.get("retire_tools", [])
    assert attempts[0].meta.get("execution_id") == "e"
    assert EXEC_ENV_SANDBOX_UNAVAILABLE_USER_MESSAGE in (messages[0].content or "")
    assert "本机" not in (messages[0].content or "")
    ends = [e for e in sink._history if e.type == EventType.TOOL_USE_END]  # noqa: SLF001
    assert ends[0].payload.get("failure", {}).get("code") == "exec_env_sandbox_unavailable"


async def test_local_code_execute_outer_timeout_stays_liveness():
    class _HangExec:
        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(
                name="code_execute",
                description="stub",
                parameters={"type": "object", "properties": {}},
                category=ToolCategory.EXECUTION,
                timeout_seconds=0.05,
            )

        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            await asyncio.sleep(5)
            return ToolResult(tool_call_id="", success=True, output="never")

    reg = ToolRegistry()
    reg.register(_HangExec())
    sink = EventSink()
    ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(
            root=Path("."), sandbox=SubprocessSandbox(), location="local"
        ),
        user_id="u",
    )
    messages, _terminal, attempts = await execute_tools(
        [_call("c1", "code_execute")],
        reg,
        ctx,
        sink,
        approval_gate=None,
        run_id="r1",
    )
    assert attempts[0].success is False
    assert attempts[0].meta.get("code") != "exec_env_sandbox_unavailable"
    assert "活性挂起" in (messages[0].content or "")
    ends = [e for e in sink._history if e.type == EventType.TOOL_USE_END]  # noqa: SLF001
    assert ends[0].payload.get("failure", {}).get("code") == "liveness_timeout"
