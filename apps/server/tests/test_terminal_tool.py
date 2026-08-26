"""Tests for the ``terminal`` background-process tool (local WorkspaceChannel path)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentcore.config import settings
from agentcore.core.error_codes import ErrorCode
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.runtime.approvals import tool_call_requires_approval
from agentcore.runtime.engine import resolve_tool_timeout
from agentcore.runtime.events import EventType
from agentcore.runtime.interaction import InteractionRegistry
from agentcore.tools.builtin import (
    build_builtin_registry,
    build_ceo_tool_registry,
    build_worker_registry,
    delegation_grantable_tool_names,
)
from agentcore.tools.builtin.terminal import (
    TerminalTool,
    clamp_wait_timeout_seconds,
    terminal_approval_subcommands,
    terminal_op_timeout_seconds,
)
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.channel import WorkspaceChannel, WorkspaceOp
from agentcore.workspace.local import LocalWorkspace
from agentcore.workspace.protocol import PathNotFound, WorkspaceIOError
from agentcore.workspace.server import ServerWorkspace
from tests.client_tool_fulfill_testutil import await_captured_event

pytestmark = pytest.mark.anyio

CONV = "conv-terminal-1"
ROOT_ID = "root-terminal"


def _drain() -> None:
    from tests.client_tool_fulfill_testutil import DELIVERED_EVENTS

    DELIVERED_EVENTS.clear()


async def _await_request():
    """Return the CLIENT_TOOL event just delivered via fulfill."""
    return await await_captured_event()


async def _round_trip(
    coro: Any, registry: InteractionRegistry, response: dict[str, Any]
):
    task = asyncio.create_task(coro)
    event = await _await_request()
    assert registry.resolve(event.payload["request_id"], response, conversation_id=CONV)
    return await task, event


def _ctx(channel: WorkspaceChannel | None) -> ToolContext:
    return ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="w1",
        backend=MagicMock(location="local"),
        user_id="u1",
        conversation_id=CONV,
        workspace_channel=channel,
    )


def _channel(timeout: float = 5.0) -> tuple[WorkspaceChannel, InteractionRegistry]:
    registry = InteractionRegistry()
    channel = WorkspaceChannel(
        user_id="u-test",
        conversation_id=CONV,
        registry=registry,
        timeout_seconds=timeout,
        root_id=ROOT_ID,
    )
    return channel, registry


# --- schema / approval / registry ------------------------------------------


def test_terminal_schema_is_never_execution():
    schema = TerminalTool().schema
    assert schema.name == "terminal"
    assert schema.approval is ToolApproval.NEVER
    assert schema.category is ToolCategory.EXECUTION
    assert "start" in schema.parameters["properties"]["subcommand"]["enum"]
    assert terminal_approval_subcommands() == frozenset({"start"})
    assert "仅本地" not in schema.description
    assert "云桌" in TerminalTool(location="server").schema.description


def test_tool_call_requires_approval_only_for_start():
    schema = TerminalTool().schema
    assert (
        tool_call_requires_approval("terminal", schema.approval, {"subcommand": "start"}) is True
    )
    for sub in ("read", "stop", "list"):
        assert (
            tool_call_requires_approval("terminal", schema.approval, {"subcommand": sub}) is False
        )


def test_terminal_omitted_when_execution_class_withheld():
    """Catalog default includes terminal; CEO/worker follow execution-class gate."""
    assert "terminal" in {s.name for s in build_builtin_registry().list_all()}
    assert "terminal" in {s.name for s in build_ceo_tool_registry().list_all()}
    assert "terminal" in {
        s.name for s in build_builtin_registry(location="local").list_all()
    }
    assert "terminal" in {
        s.name for s in build_ceo_tool_registry(backend_location="local").list_all()
    }
    assert "terminal" in {
        s.name for s in build_ceo_tool_registry(backend_location="server").list_all()
    }
    assert (
        build_ceo_tool_registry(backend_location="local").get("terminal").schema.approval
        is TerminalTool().schema.approval
    )
    assert "terminal" not in {
        s.name
        for s in build_builtin_registry(include_execution_tools=False).list_all()
    }


def test_worker_registry_registers_terminal_with_execution_class():
    local = ServerWorkspace(
        root=Path("."), sandbox=SubprocessSandbox(), location="local"
    )
    server = ServerWorkspace(
        root=Path("."), sandbox=SubprocessSandbox(), location="server"
    )
    assert "terminal" in {s.name for s in build_worker_registry(backend=local).list_all()}
    # conftest forces gvisor off → cloud server withholds the class.
    assert "terminal" not in {s.name for s in build_worker_registry(backend=server).list_all()}
    assert "terminal" in {s.name for s in build_worker_registry().list_all()}


def test_delegation_grantable_includes_terminal():
    assert "terminal" in delegation_grantable_tool_names()


def test_resolve_tool_timeout_raises_for_wait_for():
    schema = TerminalTool().schema
    slack = settings.workspace_execute_timeout_slack_seconds
    assert resolve_tool_timeout(schema, {"subcommand": "start", "command": "x"}) == 60.0
    wait_args = {
        "subcommand": "start",
        "command": "x",
        "wait_for": "ready",
        "wait_timeout_seconds": 45,
    }
    assert resolve_tool_timeout(schema, wait_args) == 45.0 + slack
    assert terminal_op_timeout_seconds(wait_args) == resolve_tool_timeout(schema, wait_args)


def test_clamp_wait_timeout_bounds():
    assert clamp_wait_timeout_seconds(None) == 30.0
    assert clamp_wait_timeout_seconds(0) == 1.0
    assert clamp_wait_timeout_seconds(9999) == 300.0


# --- op contract serialization ---------------------------------------------


async def test_start_emits_process_start_op_and_formats_result():
    channel, registry = _channel()
    tool = TerminalTool()
    response = {
        "ok": True,
        "value": {
            "process_id": "p1",
            "status": "running",
            "output": "Listening on :3000\n",
            "matched": True,
        },
    }
    result, event = await _round_trip(
        tool.execute(
            {
                "subcommand": "start",
                "command": "pnpm dev",
                "cwd": "apps/web",
                "wait_for": "Listening",
                "wait_timeout_seconds": 20,
                "name": "web",
            },
            _ctx(channel),
        ),
        registry,
        response,
    )
    assert event.type is EventType.WORKSPACE_OP_REQUIRED
    assert event.payload["op"] == WorkspaceOp.PROCESS_START
    assert event.payload["args"] == {
        "command": "pnpm dev",
        "cwd": "apps/web",
        "wait_for": "Listening",
        "wait_timeout_seconds": 20.0,
        "name": "web",
    }
    assert result.success
    assert "process_id: p1" in result.output
    assert "【就绪判定】wait_for 已命中" in result.output
    assert result.display == {
        "subcommand": "start",
        "process_id": "p1",
        "status": "running",
        "output": "Listening on :3000\n",
        "matched": True,
    }


async def test_start_long_running_requires_wait_for():
    channel, registry = _channel()
    tool = TerminalTool()
    result = await tool.execute(
        {"subcommand": "start", "command": "npm run dev"},
        _ctx(channel),
    )
    assert result.success is False
    assert result.contract_failure is True
    assert result.metadata.get("code") == "wait_for_required"
    assert "wait_for" in (result.error or "")
    from tests.client_tool_fulfill_testutil import DELIVERED_EVENTS
    assert not DELIVERED_EVENTS  # must not open the channel


async def test_start_matched_false_forbids_ready_claim():
    channel, registry = _channel()
    tool = TerminalTool()
    response = {
        "ok": True,
        "value": {
            "process_id": "p1",
            "status": "running",
            "output": "still compiling…\n",
            "matched": False,
        },
    }
    result, _event = await _round_trip(
        tool.execute(
            {
                "subcommand": "start",
                "command": "npm run dev",
                "wait_for": "Local:",
            },
            _ctx(channel),
        ),
        registry,
        response,
    )
    assert result.success
    assert "禁止宣称已就绪" in result.output


async def test_read_stop_list_op_shapes():
    channel, registry = _channel()
    tool = TerminalTool()

    read_resp = {
        "ok": True,
        "value": {
            "process_id": "p1",
            "status": "running",
            "output": "err line\n",
            "matched": False,
        },
    }
    result, event = await _round_trip(
        tool.execute(
            {"subcommand": "read", "process_id": "p1", "tail_lines": 40},
            _ctx(channel),
        ),
        registry,
        read_resp,
    )
    assert event.payload["op"] == WorkspaceOp.PROCESS_READ
    assert event.payload["args"] == {"process_id": "p1", "tail_lines": 40}
    assert result.success and result.display["subcommand"] == "read"
    _drain()

    stop_resp = {
        "ok": True,
        "value": {"process_id": "p1", "status": "exited", "exit_code": 0},
    }
    result, event = await _round_trip(
        tool.execute({"subcommand": "stop", "process_id": "p1"}, _ctx(channel)),
        registry,
        stop_resp,
    )
    assert event.payload["op"] == WorkspaceOp.PROCESS_STOP
    assert event.payload["args"] == {"process_id": "p1"}
    assert "exit_code: 0" in result.output
    _drain()

    list_resp = {
        "ok": True,
        "value": {
            "processes": [
                {
                    "process_id": "p1",
                    "name": "web",
                    "command": "pnpm dev",
                    "status": "exited",
                    "started_at": "2026-07-12T00:00:00Z",
                    "exit_code": 0,
                }
            ]
        },
    }
    result, event = await _round_trip(
        tool.execute({"subcommand": "list"}, _ctx(channel)),
        registry,
        list_resp,
    )
    assert event.payload["op"] == WorkspaceOp.PROCESS_LIST
    assert event.payload["args"] == {}
    assert "id=p1" in result.output
    assert result.display["subcommand"] == "list"


async def test_start_with_wait_for_extends_channel_timeout(monkeypatch):
    captured: list[float] = []
    real_wait_for = asyncio.wait_for

    async def spy(fut, timeout):  # noqa: ANN001
        captured.append(timeout)
        return await real_wait_for(fut, timeout)

    monkeypatch.setattr("agentcore.runtime.interaction.asyncio.wait_for", spy)

    channel, registry = _channel(timeout=30.0)
    tool = TerminalTool()
    response = {
        "ok": True,
        "value": {"process_id": "p1", "status": "running", "output": "", "matched": True},
    }
    await _round_trip(
        tool.execute(
            {
                "subcommand": "start",
                "command": "pnpm dev",
                "wait_for": "ready",
                "wait_timeout_seconds": 40,
            },
            _ctx(channel),
        ),
        registry,
        response,
    )
    slack = settings.workspace_execute_timeout_slack_seconds
    assert captured[-1] == 40.0 + slack


async def test_missing_channel_errors():
    result = await TerminalTool().execute({"subcommand": "list"}, _ctx(None))
    assert not result.success
    assert "本地" in (result.error or "") or "本机" in (result.error or "")
    # Not a parameter mistake: the environment genuinely has no desktop to host on.
    assert result.metadata.get("code") == "local_workspace_required"
    assert result.contract_failure is False


# --- failure face:每条失败路径带稳定 code，参数错不进 run 熔断累计 -----------------


class _RaisingChannel:
    """Stand-in channel whose op always fails with a given ``WorkspaceError``."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def request(self, *_args: Any, **_kwargs: Any) -> Any:
        raise self._exc


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"subcommand": "restart"},
        {"subcommand": "start"},
        {"subcommand": "read"},
        {"subcommand": "stop"},
        {"subcommand": "read", "process_id": "p1", "tail_lines": "abc"},
    ],
)
async def test_argument_rejections_are_coded_and_off_the_breaker_tally(arguments):
    """确定性参数错误 = contract_failure：模型改写调用即可，不该 3 次就禁掉 terminal。"""
    channel, _registry = _channel()
    result = await TerminalTool().execute(arguments, _ctx(channel))
    assert result.success is False
    assert result.metadata.get("code") == ErrorCode.VALIDATION_ERROR
    assert result.contract_failure is True
    from tests.client_tool_fulfill_testutil import DELIVERED_EVENTS

    assert not DELIVERED_EVENTS  # 参数校验失败不该开通道
    _drain()


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (PathNotFound("cwd 不存在"), "workspace_io_error"),
        (WorkspaceIOError("进程不存在或已清理"), "workspace_io_error"),
        (
            WorkspaceIOError("local workspace op 'process_start' timed out（活性挂起）"),
            "liveness_timeout",
        ),
        (
            WorkspaceIOError("local workspace channel dead（活性挂起）"),
            "workspace_channel_dead",
        ),
    ],
)
async def test_workspace_error_keeps_the_desktop_kind(exc, code):
    """桌面/通道已经分好的 kind 不该被 ``str(e)`` 拍平成同一句话。"""
    result = await TerminalTool().execute(
        {"subcommand": "stop", "process_id": "p1"}, _ctx(_RaisingChannel(exc))
    )
    assert result.success is False
    assert result.metadata.get("code") == code
    assert result.contract_failure is False


async def test_malformed_desktop_result_is_coded_as_workspace_io():
    channel, registry = _channel()
    tool = TerminalTool()
    result, _event = await _round_trip(
        tool.execute({"subcommand": "list"}, _ctx(channel)),
        registry,
        {"ok": True, "value": "not-a-dict"},
    )
    assert result.success is False
    assert result.metadata.get("code") == "workspace_io_error"


async def test_local_workspace_reuses_channel_for_tools():
    """workspace_channel_for_tools must reuse LocalWorkspace._channel (same root_id)."""
    from agentcore.workspace.locate import workspace_channel_for_tools

    channel, _registry = _channel()
    local = LocalWorkspace(channel)
    resolved = workspace_channel_for_tools(
        local,
        user_id="u-test", conversation_id=CONV
    )
    assert resolved is channel
