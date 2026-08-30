"""Permission axes (会话级权限 · file_write / command / host)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentcore.api.schemas.conversations import PermissionAxesModel, PermissionAxesUpdate
from agentcore.core.types import (
    DEFAULT_PERMISSION_AXES,
    AutonomyPolicy,
    CommandAxis,
    FileWriteAxis,
    HostAxis,
    PermissionAxes,
    recipe_to_axes,
    validate_permission_axes,
)
from agentcore.runtime.sandbox_approval import (
    cloud_worker_skips_per_call_gate,
    execution_tool_auto_passes,
)
from agentcore.tools.builtin import build_worker_registry


class _LocalBackend:
    location = "local"


class _ServerBackend:
    location = "server"


_FILE_OP_CLASS = frozenset(
    {"file_write", "file_append", "str_replace", "git"}
)


def test_default_axes_are_less_interrupt():
    assert PermissionAxes(
        FileWriteAxis.SESSION,
        CommandAxis.AUTO,
        HostAxis.SESSION,
    ) == DEFAULT_PERMISSION_AXES
    assert recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT) == DEFAULT_PERMISSION_AXES
    assert DEFAULT_PERMISSION_AXES.host is HostAxis.SESSION


def test_builtin_recipes():
    assert recipe_to_axes(AutonomyPolicy.CAUTIOUS) == PermissionAxes(
        FileWriteAxis.ASK,
        CommandAxis.ASK,
        HostAxis.OFF,
    )
    assert recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT) == PermissionAxes(
        FileWriteAxis.SESSION,
        CommandAxis.AUTO,
        HostAxis.SESSION,
    )
    assert recipe_to_axes(AutonomyPolicy.MANAGED) == PermissionAxes(
        FileWriteAxis.SESSION,
        CommandAxis.AUTO,
        HostAxis.SESSION,
    )
    assert recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT) == recipe_to_axes(
        AutonomyPolicy.MANAGED
    )


def test_from_mapping_roundtrip_and_retired_kickoff():
    axes = recipe_to_axes(AutonomyPolicy.MANAGED)
    assert PermissionAxes.from_mapping(axes.to_dict()) == axes
    assert "team_kickoff" not in axes.to_dict()
    assert "kickoff" not in axes.to_dict().values()

    # Extra team_kickoff dropped; command=kickoff is not merged to auto/ask.
    leftover_session = PermissionAxes.from_mapping(
        {"file_write": "session", "command": "kickoff", "team_kickoff": "rules"}
    )
    assert leftover_session == DEFAULT_PERMISSION_AXES

    leftover_ask = PermissionAxes.from_mapping(
        {"file_write": "ask", "command": "kickoff", "team_kickoff": "always"}
    )
    assert leftover_ask == DEFAULT_PERMISSION_AXES
    assert leftover_ask.file_write is not FileWriteAxis.ASK
    assert leftover_ask.command is not CommandAxis.ASK

    cautious = recipe_to_axes(AutonomyPolicy.CAUTIOUS)
    assert cautious.host is HostAxis.OFF
    assert PermissionAxes.from_mapping(cautious.to_dict()).host is HostAxis.OFF


def test_permission_axes_model_rejects_command_kickoff():
    with pytest.raises(ValidationError):
        PermissionAxesModel.model_validate(
            {"file_write": "session", "command": "kickoff", "team_kickoff": "skip"}
        )
    with pytest.raises(ValidationError):
        PermissionAxesModel.model_validate(
            {"file_write": "ask", "command": "kickoff", "team_kickoff": "rules"}
        )
    dropped = PermissionAxesModel.model_validate(
        {"file_write": "session", "command": "auto", "team_kickoff": "rules"}
    )
    assert dropped.command is CommandAxis.AUTO
    assert "team_kickoff" not in dropped.model_dump()


def test_validate_permission_axes_rejects_command_kickoff():
    with pytest.raises(ValueError):
        validate_permission_axes(file_write="session", command="kickoff")
    with pytest.raises(ValueError):
        validate_permission_axes(file_write="ask", command="kickoff")


def test_illegal_command_auto_with_file_write_ask():
    with pytest.raises(ValueError, match="illegal"):
        PermissionAxes(
            file_write=FileWriteAxis.ASK,
            command=CommandAxis.AUTO,
            host=HostAxis.ASK,
        )
    with pytest.raises(ValueError, match="illegal"):
        validate_permission_axes(file_write="ask", command="auto", host="ask")
    with pytest.raises(ValidationError):
        PermissionAxesModel(file_write="ask", command="auto", host="ask")
    with pytest.raises(ValidationError):
        PermissionAxesUpdate(
            permission_axes={
                "file_write": "ask",
                "command": "auto",
                "host": "ask",
            }
        )


def test_command_ask_withholds_execution_tools():
    axes = recipe_to_axes(AutonomyPolicy.CAUTIOUS)
    names = {
        s.name
        for s in build_worker_registry(
            backend=_LocalBackend(), permission_axes=axes
        ).list_all()
    }
    assert "code_execute" not in names
    assert "test_run" not in names
    assert "terminal" not in names
    assert "file_write" in names
    assert "web_search" in names


def test_command_ask_capability_line_matches_registry():
    """案 20260803-docx-office-exec-capability-lie A：能力行与 registry 同一谓词（含 ask）。"""
    from agentcore.runtime.context.workspace_context import build_workspace_context
    from agentcore.tools.builtin import execution_class_enabled_for

    axes = recipe_to_axes(AutonomyPolicy.CAUTIOUS)
    backend = _LocalBackend()
    assert execution_class_enabled_for(backend, axes) is False
    out = build_workspace_context(
        backend, desktop_online=True, permission_axes=axes
    )
    assert "code_execute=未装配" in out
    assert "terminal=未装配" in out
    assert (
        "code_execute=已装配"
        in build_workspace_context(backend, desktop_online=True)
    )


def test_command_auto_skips_kickoff_and_local_exec_auto_pass():
    axes = recipe_to_axes(AutonomyPolicy.MANAGED)
    assert (
        execution_tool_auto_passes(
            _LocalBackend(), "code_execute", permission_axes=axes
        )
        is True
    )
    assert (
        execution_tool_auto_passes(
            _LocalBackend(), "terminal", permission_axes=axes
        )
        is True
    )
    assert (
        execution_tool_auto_passes(
            _LocalBackend(), "browser", permission_axes=axes,
        )
        is True
    )
    assert (
        execution_tool_auto_passes(
            _LocalBackend(), "desktop_notify", permission_axes=axes
        )
        is True
    )
    # Host / MCP never ride command=auto silent pass.
    assert (
        execution_tool_auto_passes(
            _LocalBackend(), "host", permission_axes=axes
        )
        is False
    )
    assert (
        execution_tool_auto_passes(
            _LocalBackend(), "mcp_filesystem_read", permission_axes=axes
        )
        is False
    )


def test_less_interrupt_and_managed_same_axes():
    """少打断 = 托管: session/auto/session — 静默执行、不蕴含深度研究自治、host=session."""
    axes = recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT)
    assert axes == DEFAULT_PERMISSION_AXES
    assert axes == recipe_to_axes(AutonomyPolicy.MANAGED)
    assert not hasattr(axes, "honors_kickoff_grant")
    assert axes.auto_executes is True
    assert not hasattr(axes, "implies_deep_research_auto")
    assert axes.host is HostAxis.SESSION
    for tool in (
        "code_execute",
        "test_run",
        "terminal",
        "browser",
        "desktop_notify",
    ):
        assert (
            execution_tool_auto_passes(
                _LocalBackend(), tool, permission_axes=axes
            )
            is True
        )


def test_command_ask_no_execution_auto_pass():
    """谨慎档 command=ask：execution_class / desktop_notify 仍需审批卡。"""
    axes = recipe_to_axes(AutonomyPolicy.CAUTIOUS)
    for tool in (
        "code_execute",
        "terminal",
        "browser",
        "desktop_notify",
    ):
        assert (
            execution_tool_auto_passes(
                _LocalBackend(), tool, permission_axes=axes
            )
            is False
        )


def test_command_ask_no_capability_auth():
    axes = recipe_to_axes(AutonomyPolicy.CAUTIOUS)
    assert axes.host_disabled is True


def test_cloud_worker_honors_file_write_ask():
    """云端 worker：file_write=ask 仍弹写文件类；session 仍免逐次卡。"""
    cautious = recipe_to_axes(AutonomyPolicy.CAUTIOUS)
    session = recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT)
    cloud = _ServerBackend()

    assert (
        cloud_worker_skips_per_call_gate(
            cloud,
            "file_write",
            permission_axes=cautious,
            file_op_tools=_FILE_OP_CLASS,
        )
        is False
    )
    assert (
        cloud_worker_skips_per_call_gate(
            cloud,
            "file_write",
            permission_axes=session,
            file_op_tools=_FILE_OP_CLASS,
        )
        is True
    )
    assert (
        cloud_worker_skips_per_call_gate(
            cloud,
            "web_search",
            permission_axes=cautious,
            file_op_tools=_FILE_OP_CLASS,
        )
        is True
    )
    assert (
        cloud_worker_skips_per_call_gate(
            _LocalBackend(),
            "file_write",
            permission_axes=cautious,
            file_op_tools=_FILE_OP_CLASS,
        )
        is False
    )
    assert (
        cloud_worker_skips_per_call_gate(
            cloud,
            "mcp_filesystem_write",
            permission_axes=session,
            file_op_tools=_FILE_OP_CLASS,
        )
        is False
    )
