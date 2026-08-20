"""恒确认回归：云端 worker 的 git push / create_pr 不得被审批门前置短路。

缺陷形状：恒确认判据原本私藏在 ``ApprovalGate.authorize`` 内，而云端 worker 路径在
调用 authorize **之前**就把 ``needs_approval`` 置 False（默认 ``file_write=session``
令 ``cloud_worker_skips_per_call_gate`` 返回 True），于是用户看不到任何确认卡就推了
远端。定案见 [安全权限与治理 §熔断]：普通 push / create_pr 始终弹确认。

同时钉住「不外扩」：同条件下只读 git 与非发布类 git 写入仍免逐次卡。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from structlog.testing import capture_logs

from agentcore.core.types import AutonomyPolicy, recipe_to_axes
from agentcore.llm.provider.protocol import ToolCall
from agentcore.runtime.approvals import ApprovalDecision, ApprovalGate
from agentcore.runtime.delegate.drive_setup import resolve_worker_gate
from agentcore.runtime.engine.tool_exec_gates import _check_safety_and_approval_gates
from agentcore.runtime.events import EventSink, EventType, SSEEvent
from agentcore.runtime.interaction import InteractionRegistry
from agentcore.runtime.sandbox_approval import cloud_worker_skips_per_call_gate
from agentcore.tools.builtin import approval_class_tool_names, delegation_grantable_tool_names
from agentcore.tools.builtin.git_ops.tool import GitTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry


class _CloudBackend:
    """云端工作区（location=server）——真隔离，历史上 worker 免逐次卡。"""

    location = "server"


def _drain(sink: EventSink) -> list[SSEEvent]:
    events: list[SSEEvent] = []
    while not sink._queue.empty():  # noqa: SLF001 - test-only inspection
        events.append(sink._queue.get_nowait())
    return events


async def _resolve_when_ready(
    registry: InteractionRegistry,
    approval_id: str,
    decision: ApprovalDecision,
    conversation_id: str,
) -> None:
    """Resolve ``approval_id`` as soon as it appears pending (public API only)."""
    for _ in range(2000):
        if registry.resolve(approval_id, decision, conversation_id=conversation_id):
            return
        await asyncio.sleep(0)
    raise AssertionError(f"approval {approval_id!r} never became pending")


def _session_cloud_gate(sink: EventSink, registry: InteractionRegistry) -> ApprovalGate:
    """默认档（``file_write=session``）的本回合门——正是触发短路的那组权限。"""
    return ApprovalGate(
        sink=sink,
        conversation_id="conv-1",
        registry=registry,
        timeout_seconds=5.0,
        file_op_tools=approval_class_tool_names(),
        delegation_grantable_tools=delegation_grantable_tool_names(),
        permission_axes=recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT),
    )


async def _run_worker_gates(
    gate: ApprovalGate | None,
    sink: EventSink,
    *,
    arguments: dict,
    tool_call_id: str,
):
    """跑一次云端 worker 的 git 调用，返回 ``None``（放行）或拒绝结果。"""
    context = ToolContext.create(
        backend=_CloudBackend(),  # type: ignore[arg-type]
        execution_id="exec-1",
        run_id="run-1",
        agent_id="agent-1",
        user_id="user-1",
        conversation_id="conv-1",
    )
    return await _check_safety_and_approval_gates(
        name="git",
        args=arguments,
        tool_schema=GitTool().schema,
        tc=ToolCall(id=tool_call_id),
        context=context,
        sink=sink,
        event_run_id="run-1",
        run_id="run-1",
        role="worker",
        fingerprint="fp-1",
        approval_gate=gate,
    )


async def test_cloud_worker_git_push_still_prompts():
    """云 backend + role=worker + file_write=session：push 仍弹确认卡。"""
    reg = InteractionRegistry()
    sink = EventSink()
    gate = _session_cloud_gate(sink, reg)

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "push-1", ApprovalDecision.APPROVE, "conv-1")
    )
    denied = await _run_worker_gates(
        gate,
        sink,
        arguments={"subcommand": "push", "remote": "origin", "branch": "feature/x"},
        tool_call_id="push-1",
    )
    await resolver

    assert denied is None
    assert any(e.type is EventType.APPROVAL_REQUIRED for e in _drain(sink))


async def test_cloud_worker_git_create_pr_still_prompts_and_deny_blocks():
    """同条件下 create_pr 亦弹卡；用户拒绝则工具不执行。"""
    reg = InteractionRegistry()
    sink = EventSink()
    gate = _session_cloud_gate(sink, reg)

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "pr-1", ApprovalDecision.DENY, "conv-1")
    )
    denied = await _run_worker_gates(
        gate,
        sink,
        arguments={"subcommand": "create_pr", "title": "feat: x"},
        tool_call_id="pr-1",
    )
    await resolver

    assert denied is not None
    assert denied.attempt.policy_failure is True
    assert any(e.type is EventType.APPROVAL_REQUIRED for e in _drain(sink))


async def test_cloud_worker_readonly_git_unaffected():
    """只读 git 子命令在同条件下不受影响：无卡、无挂起。"""
    reg = InteractionRegistry()
    sink = EventSink()
    gate = _session_cloud_gate(sink, reg)

    for idx, sub in enumerate(("status", "log", "diff", "show", "fetch")):
        denied = await _run_worker_gates(
            gate, sink, arguments={"subcommand": sub}, tool_call_id=f"ro-{idx}"
        )
        assert denied is None, sub
        assert not reg.list_pending("conv-1"), sub
        assert not [e for e in _drain(sink) if e.type is EventType.APPROVAL_REQUIRED], sub


async def test_cloud_worker_non_publish_git_write_stays_ungated():
    """拦截面不外扩：非发布类 git 写入在云端 worker 上仍免逐次卡。"""
    reg = InteractionRegistry()
    sink = EventSink()
    gate = _session_cloud_gate(sink, reg)

    for idx, args in enumerate(
        (
            {"subcommand": "commit", "message": "wip"},
            {"subcommand": "add", "paths": ["a.py"]},
            {"subcommand": "checkout", "branch": "feature/x", "create": True},
        )
    ):
        denied = await _run_worker_gates(gate, sink, arguments=args, tool_call_id=f"w-{idx}")
        assert denied is None, args
        assert not reg.list_pending("conv-1"), args
        assert not [e for e in _drain(sink) if e.type is EventType.APPROVAL_REQUIRED], args


async def test_always_confirm_without_gate_is_denied_not_pushed():
    """无人值守作业（handoff job / 评测）没人可问：push 被拒，而不是静默推上远端。

    这条路的 ``approval_gate`` 是**有意**为 None 的——没有客户端能应答确认卡。从前
    「无 gate」被读成「免审」，恒确认在这里整类消失；现在它落回 fail-closed：拒绝执行 +
    留可测 status。
    """
    sink = EventSink()
    with capture_logs() as logs:
        denied = await _run_worker_gates(
            None,
            sink,
            arguments={"subcommand": "push", "remote": "origin", "branch": "feature/x"},
            tool_call_id="push-nogate",
        )

    assert denied is not None
    assert denied.attempt.policy_failure is True
    ends = [e for e in logs if e.get("event") == "tool.execute_end"]
    assert [e["status"] for e in ends] == ["always_confirm_no_gate"]


def test_cloud_worker_skip_never_covers_always_confirm():
    """判据层：云端免逐次卡的判定本身必须先问恒确认。"""
    cloud = _CloudBackend()
    session = recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT)
    file_ops = approval_class_tool_names()

    for sub in ("push", "create_pr"):
        assert (
            cloud_worker_skips_per_call_gate(
                cloud,
                "git",
                arguments={"subcommand": sub},
                permission_axes=session,
                file_op_tools=file_ops,
            )
            is False
        ), sub

    for sub in ("status", "log", "commit", "add"):
        assert (
            cloud_worker_skips_per_call_gate(
                cloud,
                "git",
                arguments={"subcommand": sub},
                permission_axes=session,
                file_op_tools=file_ops,
            )
            is True
        ), sub

    # 同桶的 host(action=install_package)（host_class，这里是第二道）。
    assert (
        cloud_worker_skips_per_call_gate(
            cloud,
            "host",
            arguments={
                "action": "install_package",
                "manager": "winget",
                "package": "git",
            },
            permission_axes=session,
            file_op_tools=file_ops,
        )
        is False
    )


def test_resolve_worker_gate_hands_down_gate_regardless_of_roster():
    """上游不预判：不管这批 worker 手里有什么工具，本回合的门一律往下传。

    这里曾按名册扫描（有 desktop-touch / 恒确认工具才留门，否则给 None）——那是把
    ``sandbox_approval`` 那张表在上游抄了第二遍，抄漏一次就整类失效。「云端沙箱该免的
    卡」现在由收口点查同一张表免掉，本文件上半部分直接钉 ``cloud_worker_skips_per_call_gate``
    的口径。
    """
    gate = object()
    registry = ToolRegistry()
    registry.register(GitTool())
    tool = SimpleNamespace(
        _approval_gate=gate,
        _tools=registry,
        _base_tool_context=SimpleNamespace(backend=_CloudBackend()),
    )
    assert resolve_worker_gate(tool) is gate

    tool_bare = SimpleNamespace(
        _approval_gate=gate,
        _tools=ToolRegistry(),
        _base_tool_context=SimpleNamespace(backend=_CloudBackend()),
    )
    assert resolve_worker_gate(tool_bare) is gate
