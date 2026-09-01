"""P2 permission-axes audit + gVisor network grading."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from agentcore.core.types import AutonomyPolicy, recipe_to_axes
from agentcore.runtime.audit.hooks import (
    bind_recorder,
    on_approval_resolved,
    on_journal_fact_appended,
)
from agentcore.runtime.audit.projector import (
    project_approval_resolved,
    project_permission_axes_changed,
)
from agentcore.runtime.audit.recorder import AuditRecorder, current_audit_recorder
from agentcore.tools.builtin.run_short import execute_short
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.gvisor import GVisorSandbox
from agentcore.tools.sandbox.protocol import ExecutionRequest, ExecutionResult
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def test_permission_axes_changed_projection():
    previous = {"file_write": "session", "command": "ask", "host": "off"}
    next_axes = {"file_write": "session", "command": "auto", "host": "session"}
    draft = project_permission_axes_changed(previous=previous, next_axes=next_axes)
    assert draft.category == "permission"
    assert draft.action == "permission.axes_changed"
    assert draft.detail["previous"] == previous
    assert draft.detail["permission_axes"] == next_axes
    assert draft.detail["decided_by"] == "user"


def test_approval_resolved_includes_decided_by_user():
    recorder = AuditRecorder(
        user_id="u1",
        conversation_id="c1",
        turn_id="t1",
        trace_id=None,
        delegated=False,
    )
    draft = project_approval_resolved(
        recorder,
        tool_name="file_write",
        tool_call_id="tc1",
        decision="approve",
        arguments={"path": "a.md"},
    )
    assert draft.detail["decided_by"] == "user"
    assert draft.action == "approval.granted"


@pytest.mark.asyncio
async def test_approval_force_schedules_without_delegation():
    """Solo turns still persist who approved even when audit is not yet active."""
    recorder = AuditRecorder(
        user_id="u1",
        conversation_id="c1",
        turn_id="t1",
        trace_id=None,
        delegated=False,
    )
    token = current_audit_recorder.set(recorder)
    try:
        on_approval_resolved(
            tool_name="file_write",
            tool_call_id="tc1",
            decision="approve",
            arguments={"path": "a.md"},
        )
        assert len(recorder._pending) == 1  # noqa: SLF001
        await recorder.flush()
    finally:
        current_audit_recorder.reset(token)


@pytest.mark.asyncio
async def test_managed_bind_activates_and_snapshots_axes():
    managed = recipe_to_axes(AutonomyPolicy.MANAGED)
    recorder, token = bind_recorder(
        user_id="u1",
        conversation_id=str(uuid4()),
        turn_id=str(uuid4()),
        trace_id=None,
        delegated=True,
        permission_axes=str(managed.to_dict()),
    )
    try:
        assert recorder.active is True
        assert recorder._axes_snapshotted is True  # noqa: SLF001
        assert len(recorder._pending) >= 1  # noqa: SLF001
        await recorder.flush()
    finally:
        current_audit_recorder.reset(token)


@pytest.mark.asyncio
async def test_full_trust_journal_tool_side_effect_without_delegate():
    recorder, token = bind_recorder(
        user_id="u1",
        conversation_id=str(uuid4()),
        turn_id=str(uuid4()),
        trace_id=None,
        delegated=True,
        permission_axes="full_trust",
    )
    try:
        on_journal_fact_appended(
            {
                "kind": "tool_use_start",
                "payload": {
                    "tool_call_id": "tc-1",
                    "tool_name": "run",
                    "arguments": {"command": "print(1)"},
                    "run_id": "r1",
                },
            }
        )
        on_journal_fact_appended(
            {
                "kind": "tool_use_end",
                "payload": {
                    "tool_call_id": "tc-1",
                    "tool_name": "run",
                    "status": "success",
                    "run_id": "r1",
                },
            }
        )
        assert len(recorder._pending) >= 2  # noqa: SLF001
        await recorder.flush()
    finally:
        current_audit_recorder.reset(token)


def test_gvisor_desk_oci_always_has_packaging_netns():
    sandbox = GVisorSandbox(workspace_root="/tmp")
    cache = "/tmp/cache"
    config = sandbox._build_desk_oci(  # noqa: SLF001
        workspace="/tmp/ws",
        scratch_dir="/tmp/scratch",
        netns_path="/var/run/netns/acpkg1",
        cache_host_dir=cache,
        proxy_url="http://10.0.0.1:8898",
    )
    ns_types = {n["type"] for n in config["linux"]["namespaces"]}
    assert "network" in ns_types
    net = next(n for n in config["linux"]["namespaces"] if n["type"] == "network")
    assert net.get("path") == "/var/run/netns/acpkg1"
    assert config["process"]["user"]["uid"] != 65534


@pytest.mark.asyncio
async def test_code_execute_network_mode_follows_axes(tmp_path: Path):
    captured: list[ExecutionRequest] = []

    class _CaptureSandbox(SubprocessSandbox):
        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            captured.append(request)
            return ExecutionResult(
                success=True, stdout="ok", stderr="", exit_code=0, duration_ms=1
            )

    backend = ServerWorkspace(root=tmp_path, sandbox=_CaptureSandbox())

    ctx_trust = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=backend,
        user_id="u",
        permission_axes="{\"file_write\":\"session\",\"command\":\"auto\",\"host\":\"session\"}",
    )
    await execute_short(
        {"code": "print(1)", "language": "python"},
        ctx_trust,
        location="server",
    )
    assert captured[-1].network_mode == "restricted"

    ctx_ws = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=backend,
        user_id="u",
        permission_axes="{\"file_write\":\"ask\",\"command\":\"ask\",\"host\":\"off\"}",
    )
    await execute_short(
        {"code": "print(1)", "language": "python"},
        ctx_ws,
        location="server",
    )
    assert captured[-1].network_mode == "none"
