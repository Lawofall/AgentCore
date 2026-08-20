"""Permission axes (会话级权限 · file_write / command / team_kickoff / host)."""

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
    TeamKickoffAxis,
    recipe_to_axes,
    validate_permission_axes,
)
from agentcore.runtime.kickoff.gate import needs_capability_auth, should_kickoff
from agentcore.runtime.sandbox_approval import (
    cloud_worker_skips_per_call_gate,
    execution_tool_auto_passes,
)
from agentcore.tools.builtin import build_worker_registry

# Explicit kickoff-command axes for授/开工卡 tests (no longer a built-in recipe).
_KICKOFF_RULES = PermissionAxes(
    FileWriteAxis.SESSION,
    CommandAxis.KICKOFF,
    TeamKickoffAxis.RULES,
    HostAxis.ASK,
)
_KICKOFF_SKIP = PermissionAxes(
    FileWriteAxis.SESSION,
    CommandAxis.KICKOFF,
    TeamKickoffAxis.SKIP,
    HostAxis.ASK,
)


class _LocalBackend:
    location = "local"


class _ServerBackend:
    location = "server"


_FILE_OP_CLASS = frozenset(
    {"file_write", "file_append", "str_replace", "write_section", "git"}
)


def test_default_axes_are_less_interrupt():
    assert PermissionAxes(
        FileWriteAxis.SESSION,
        CommandAxis.AUTO,
        TeamKickoffAxis.RULES,
        HostAxis.SESSION,
    ) == DEFAULT_PERMISSION_AXES
    assert recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT) == DEFAULT_PERMISSION_AXES
    assert DEFAULT_PERMISSION_AXES.host is HostAxis.SESSION


def test_builtin_recipes():
    assert recipe_to_axes(AutonomyPolicy.CAUTIOUS) == PermissionAxes(
        FileWriteAxis.ASK,
        CommandAxis.ASK,
        TeamKickoffAxis.RULES,
        HostAxis.OFF,
    )
    assert recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT) == PermissionAxes(
        FileWriteAxis.SESSION,
        CommandAxis.AUTO,
        TeamKickoffAxis.RULES,
        HostAxis.SESSION,
    )
    assert recipe_to_axes(AutonomyPolicy.MANAGED) == PermissionAxes(
        FileWriteAxis.SESSION,
        CommandAxis.AUTO,
        TeamKickoffAxis.SKIP,
        HostAxis.SESSION,
    )


def test_from_mapping_roundtrip_and_legacy_missing_host():
    axes = recipe_to_axes(AutonomyPolicy.MANAGED)
    assert PermissionAxes.from_mapping(axes.to_dict()) == axes
    # Partial JSON without host → host defaults to session (not silently dropped).
    legacy = PermissionAxes.from_mapping(
        {"file_write": "session", "command": "kickoff", "team_kickoff": "rules"}
    )
    assert legacy == PermissionAxes(
        FileWriteAxis.SESSION,
        CommandAxis.KICKOFF,
        TeamKickoffAxis.RULES,
        HostAxis.SESSION,
    )
    assert legacy.host is HostAxis.SESSION
    # Cautious seed persists host=off through dict roundtrip.
    cautious = recipe_to_axes(AutonomyPolicy.CAUTIOUS)
    assert cautious.host is HostAxis.OFF
    assert PermissionAxes.from_mapping(cautious.to_dict()).host is HostAxis.OFF


def test_illegal_command_auto_with_file_write_ask():
    with pytest.raises(ValueError, match="illegal"):
        PermissionAxes(
            file_write=FileWriteAxis.ASK,
            command=CommandAxis.AUTO,
            team_kickoff=TeamKickoffAxis.SKIP,
            host=HostAxis.ASK,
        )
    with pytest.raises(ValueError, match="illegal"):
        validate_permission_axes(
            file_write="ask", command="auto", team_kickoff="skip", host="ask"
        )
    with pytest.raises(ValidationError):
        PermissionAxesModel(
            file_write="ask", command="auto", team_kickoff="rules", host="ask"
        )
    with pytest.raises(ValidationError):
        PermissionAxesUpdate(
            permission_axes={
                "file_write": "ask",
                "command": "auto",
                "team_kickoff": "skip",
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
    # Without axes, local still shows 已装配 (backend gate alone).
    assert (
        "code_execute=已装配"
        in build_workspace_context(backend, desktop_online=True)
    )


def test_kickoff_command_keeps_capability_auth():
    axes = _KICKOFF_RULES
    assert needs_capability_auth(local_gate=True, axes=axes) is True
    assert should_kickoff(plan_preview=False, local_gate=True, axes=axes) is True
    assert should_kickoff(plan_preview=True, local_gate=False, axes=axes) is True


def test_team_kickoff_skip_with_kickoff_command():
    axes = _KICKOFF_SKIP
    assert should_kickoff(plan_preview=True, local_gate=True, axes=axes) is False
    # command still kickoff — capability auth would apply if card were shown
    assert needs_capability_auth(local_gate=True, axes=axes) is True


def test_team_kickoff_always_forces_plan_half():
    axes = PermissionAxes(
        FileWriteAxis.SESSION,
        CommandAxis.KICKOFF,
        TeamKickoffAxis.ALWAYS,
        HostAxis.ASK,
    )
    assert should_kickoff(plan_preview=False, local_gate=False, axes=axes) is True


def test_command_auto_skips_kickoff_and_local_exec_auto_pass():
    axes = recipe_to_axes(AutonomyPolicy.MANAGED)
    assert should_kickoff(plan_preview=True, local_gate=True, axes=axes) is False
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
            _LocalBackend(), "browser_navigate", permission_axes=axes
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
    assert (
        execution_tool_auto_passes(
            _LocalBackend(),
            "code_execute",
            permission_axes=_KICKOFF_RULES,
        )
        is False
    )
    assert (
        execution_tool_auto_passes(
            _LocalBackend(),
            "terminal",
            permission_axes=_KICKOFF_RULES,
        )
        is False
    )
    assert (
        execution_tool_auto_passes(
            _LocalBackend(),
            "desktop_notify",
            permission_axes=_KICKOFF_RULES,
        )
        is False
    )


def test_less_interrupt_rules_semantics():
    """少打断: session/auto/rules/session — 组队按规则弹卡、静默执行、不蕴含深度研究自治、host=session."""
    axes = recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT)
    assert axes == DEFAULT_PERMISSION_AXES
    assert should_kickoff(plan_preview=True, local_gate=True, axes=axes) is True
    assert should_kickoff(plan_preview=False, local_gate=True, axes=axes) is False
    assert needs_capability_auth(local_gate=True, axes=axes) is False
    assert axes.honors_kickoff_grant is False
    assert axes.auto_executes is True
    assert axes.implies_deep_research_auto is False
    assert axes.host is HostAxis.SESSION
    for tool in (
        "code_execute",
        "test_run",
        "terminal",
        "browser_navigate",
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
        "browser_navigate",
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
    assert needs_capability_auth(local_gate=True, axes=axes) is False
    # rules + plan_preview still hangs team card (组团按 rules)
    assert should_kickoff(plan_preview=True, local_gate=True, axes=axes) is True
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
            "write_section",
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
    # Non-file server tools stay historically ungated even under 谨慎.
    assert (
        cloud_worker_skips_per_call_gate(
            cloud,
            "web_search",
            permission_axes=cautious,
            file_op_tools=_FILE_OP_CLASS,
        )
        is True
    )
    # Local never skips via this helper (full gate shared).
    assert (
        cloud_worker_skips_per_call_gate(
            _LocalBackend(),
            "file_write",
            permission_axes=cautious,
            file_op_tools=_FILE_OP_CLASS,
        )
        is False
    )
    # Desktop-touch keeps the gate on cloud regardless of file_write axis.
    assert (
        cloud_worker_skips_per_call_gate(
            cloud,
            "mcp_filesystem_write",
            permission_axes=session,
            file_op_tools=_FILE_OP_CLASS,
        )
        is False
    )
