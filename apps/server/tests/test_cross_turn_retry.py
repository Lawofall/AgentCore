"""``ToolCallFact.cross_turn_retry`` stamps from deny points; unknown stays omitted."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agentcore.core.types import ToolApproval, ToolFace
from agentcore.llm.provider.protocol import ToolCall, ToolCallFunction
from agentcore.runtime.approvals import ApprovalDecision
from agentcore.runtime.engine.tool_call_fact_code import tool_call_fact_cross_turn_retry
from agentcore.runtime.engine.tool_exec import execute_tools
from agentcore.runtime.events import EventSink
from agentcore.runtime.facts import (
    CROSS_TURN_RETRY_KEY,
    CrossTurnRetry,
    FactKind,
    ToolCallFact,
    TurnFactLog,
    current_fact_log,
)
from agentcore.runtime.loop_controller import (
    ERROR_CLASS_PERMANENT,
    ERROR_CLASS_PERMISSION,
    ERROR_CLASS_VALIDATION,
    ToolAttempt,
)
from agentcore.tools.builtin.file_ops import FileWriteTool
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.external_mounts import ExternalMount
from agentcore.workspace.server import ServerWorkspace


def _call(tool_id: str, name: str, args: str = "{}") -> ToolCall:
    return ToolCall(id=tool_id, function=ToolCallFunction(name=name, arguments=args))


def _ctx(workspace: Path | None = None, **fields: Any) -> ToolContext:
    root = workspace or Path(".")
    if workspace is not None:
        keep = workspace / "README.md"
        if not keep.exists():
            keep.write_text("desk\n", encoding="utf-8")
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=root, sandbox=SubprocessSandbox()),
        user_id="u",
        **fields,
    )


def _registry(*tools: Any) -> ToolRegistry:
    reg = ToolRegistry()
    for tool in tools:
        reg.register(tool)
    return reg


def _payloads(log: TurnFactLog) -> list[dict[str, Any]]:
    return [
        dict(entry.get("payload") or {})
        for entry in log.entries()
        if (entry.get("kind") or "") == FactKind.TOOL_CALL.value
    ]


class _OkTool:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            face=ToolFace.SEARCH,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(tool_call_id="", success=True, output="ok")


class _GrantableStub:
    def __init__(self, name: str) -> None:
        self._name = name
        self.executed = False

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            face=ToolFace.SEARCH,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.executed = True
        return ToolResult(tool_call_id="", success=True, output="ok")


class _TimeoutStub:
    def __init__(self) -> None:
        self.executed = False

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="grep",
            description="stub",
            parameters={"type": "object", "properties": {}},
            face=ToolFace.FILE,
            timeout_seconds=0.01,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.executed = True
        await asyncio.sleep(5)
        return ToolResult(tool_call_id="", success=True, output="ok")


class _FailNoStamp:
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="web_search",
            description="stub",
            parameters={"type": "object", "properties": {}},
            face=ToolFace.SEARCH,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(tool_call_id="", success=False, output="", error="upstream blip")


class _ApprovingGate:
    permission_axes = None
    file_op_tools: frozenset[str] = frozenset()

    def will_prompt(self, **_kwargs: Any) -> bool:
        return False

    async def authorize(self, **_kwargs: Any) -> ApprovalDecision:
        return ApprovalDecision.APPROVE


class _DenyingGate:
    permission_axes = None
    file_op_tools: frozenset[str] = frozenset()

    def will_prompt(self, **_kwargs: Any) -> bool:
        return True

    async def authorize(self, **_kwargs: Any) -> ApprovalDecision:
        return ApprovalDecision.DENY


def test_derivation_copies_stamp_never_infers_from_error_class():
    """Unknown stays empty even when error_class / code would tempt a guess."""
    assert (
        tool_call_fact_cross_turn_retry(
            ToolAttempt(
                "f",
                "file_write",
                success=False,
                meta={"error_class": ERROR_CLASS_PERMISSION},
            )
        )
        == ""
    )
    assert (
        tool_call_fact_cross_turn_retry(
            ToolAttempt(
                "f",
                "grep",
                success=False,
                meta={"error_class": ERROR_CLASS_PERMANENT, "liveness_timeout": True},
            )
        )
        == ""
    )
    assert (
        tool_call_fact_cross_turn_retry(
            ToolAttempt(
                "f",
                "file_write",
                success=False,
                contract_failure=True,
                meta={"error_class": ERROR_CLASS_VALIDATION},
            )
        )
        == ""
    )
    stamped = ToolAttempt(
        "f",
        "file_write",
        success=False,
        meta={CROSS_TURN_RETRY_KEY: CrossTurnRetry.FUTILE.value},
    )
    assert tool_call_fact_cross_turn_retry(stamped) == CrossTurnRetry.FUTILE.value
    assert tool_call_fact_cross_turn_retry(ToolAttempt("f", "ok", success=True)) == ""


async def test_allowlist_deny_reaches_tool_call_fact():
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        await execute_tools(
            [_call("c1", "file_write", '{"path":"a.md","content":"x"}')],
            _registry(_OkTool("file_write")),
            _ctx(),
            EventSink(),
            approval_gate=None,
            run_id="r1",
            allowed_tool_names=["file_read"],
        )
    finally:
        current_fact_log.reset(token)
    payloads = _payloads(log)
    assert len(payloads) == 1
    assert payloads[0]["success"] is False
    assert payloads[0][CROSS_TURN_RETRY_KEY] == CrossTurnRetry.FUTILE.value


async def test_approval_denied_reaches_tool_call_fact():
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        await execute_tools(
            [_call("c1", "mcp_write")],
            _registry(_GrantableStub("mcp_write")),
            _ctx(),
            EventSink(),
            approval_gate=_DenyingGate(),  # type: ignore[arg-type]
            run_id="r1",
        )
    finally:
        current_fact_log.reset(token)
    payloads = _payloads(log)
    assert len(payloads) == 1
    assert payloads[0]["success"] is False
    assert payloads[0][CROSS_TURN_RETRY_KEY] == CrossTurnRetry.FUTILE.value


async def test_liveness_timeout_is_not_futile_on_tool_call_fact():
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        await execute_tools(
            [_call("c1", "grep", '{"pattern":"x"}')],
            _registry(_TimeoutStub()),
            _ctx(),
            EventSink(),
            approval_gate=None,
            run_id="r1",
        )
    finally:
        current_fact_log.reset(token)
    payloads = _payloads(log)
    assert len(payloads) == 1
    assert payloads[0]["success"] is False
    assert payloads[0][CROSS_TURN_RETRY_KEY] == CrossTurnRetry.NOT_FUTILE.value


async def test_write_scope_reject_reaches_tool_call_fact(tmp_path: Path):
    ctx = _ctx(tmp_path)
    ctx.write_scope = "none"
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        await execute_tools(
            [_call("c1", "file_write", '{"path":"src/a.py","content":"x"}')],
            _registry(FileWriteTool()),
            ctx,
            EventSink(),
            approval_gate=_ApprovingGate(),  # type: ignore[arg-type]
            run_id="r1",
        )
    finally:
        current_fact_log.reset(token)
    payloads = _payloads(log)
    assert len(payloads) == 1
    assert payloads[0]["success"] is False
    assert payloads[0][CROSS_TURN_RETRY_KEY] == CrossTurnRetry.FUTILE.value


async def test_outside_workspace_reaches_tool_call_fact(tmp_path: Path):
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        await execute_tools(
            [_call("c1", "file_write", '{"path":"../escaped.md","content":"x"}')],
            _registry(FileWriteTool()),
            _ctx(tmp_path),
            EventSink(),
            approval_gate=_ApprovingGate(),  # type: ignore[arg-type]
            run_id="r1",
        )
    finally:
        current_fact_log.reset(token)
    payloads = _payloads(log)
    assert len(payloads) == 1
    assert payloads[0]["success"] is False
    assert payloads[0][CROSS_TURN_RETRY_KEY] == CrossTurnRetry.FUTILE.value


async def test_mount_policy_deny_reaches_tool_call_fact(tmp_path: Path):
    ws = tmp_path / "ws"
    ext = tmp_path / "AgentCode"
    ws.mkdir()
    ext.mkdir()
    (ws / "README.md").write_text("desk\n", encoding="utf-8")
    backend = ServerWorkspace(root=ws, sandbox=SubprocessSandbox())
    backend.attach_external_mounts(
        {
            "AgentCode": ExternalMount(
                alias="AgentCode",
                root_id="ext-r1",
                label="AgentCode",
                abs_path=str(ext),
                mode="organize",
            )
        }
    )
    ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=backend,
        user_id="u",
    )
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        await execute_tools(
            [
                _call(
                    "c1",
                    "file_write",
                    '{"path":"external/AgentCode/out/report.md","content":"leak"}',
                )
            ],
            _registry(FileWriteTool()),
            ctx,
            EventSink(),
            approval_gate=_ApprovingGate(),  # type: ignore[arg-type]
            run_id="r1",
        )
    finally:
        current_fact_log.reset(token)
    payloads = _payloads(log)
    assert len(payloads) == 1
    assert payloads[0]["success"] is False
    assert payloads[0][CROSS_TURN_RETRY_KEY] == CrossTurnRetry.FUTILE.value


async def test_unstamped_failure_omits_cross_turn_retry():
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        await execute_tools(
            [_call("c1", "web_search")],
            _registry(_FailNoStamp()),
            _ctx(),
            EventSink(),
            approval_gate=None,
            run_id="r1",
        )
    finally:
        current_fact_log.reset(token)
    payloads = _payloads(log)
    assert len(payloads) == 1
    assert payloads[0]["success"] is False
    assert CROSS_TURN_RETRY_KEY not in payloads[0]


def test_tool_call_fact_unknown_values_never_serialized():
    payload = (
        ToolCallFact(
            run_id="r",
            tool_call_id="c",
            name="x",
            success=False,
            cross_turn_retry="",
        )
        .to_fact()
        .entry()["payload"]
    )
    assert CROSS_TURN_RETRY_KEY not in payload
