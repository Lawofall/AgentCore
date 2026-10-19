"""Conversation boundary: read | folder | computer | full."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentcore.api.schemas.conversations import PermissionAxesModel, PermissionAxesUpdate
from agentcore.conversation.common import parse_permission_axes
from agentcore.core.types import DEFAULT_PERMISSION_AXES, WorkspaceBoundary
from agentcore.runtime.context.workspace_context import build_workspace_context
from agentcore.runtime.sandbox_approval import (
    boundary_block_message,
    cloud_worker_skips_per_call_gate,
    execution_tool_auto_passes,
)
from agentcore.tools.builtin import build_worker_registry, execution_class_enabled_for


def _gaps(ctx: str) -> set[str]:
    for line in ctx.splitlines():
        if line.startswith("缺口："):
            return {p.strip() for p in line.removeprefix("缺口：").split("、") if p.strip()}
    return set()


def _names(boundary: WorkspaceBoundary) -> set[str]:
    return {
        s.name
        for s in build_worker_registry(
            backend=_LocalBackend(), permission_axes=boundary
        ).list_all()
    }


class _LocalBackend:
    location = "local"


class _ServerBackend:
    location = "server"


def test_default_boundary_is_computer():
    assert DEFAULT_PERMISSION_AXES is WorkspaceBoundary.COMPUTER
    assert DEFAULT_PERMISSION_AXES.allows_write is True
    assert DEFAULT_PERMISSION_AXES.allows_execution is True
    assert DEFAULT_PERMISSION_AXES.allows_host is True
    assert DEFAULT_PERMISSION_AXES.to_dict() == {"boundary": "computer"}


def test_from_mapping_requires_boundary():
    assert WorkspaceBoundary.from_mapping({"boundary": "read"}) is WorkspaceBoundary.READ
    assert WorkspaceBoundary.from_mapping({"boundary": "computer"}) is WorkspaceBoundary.COMPUTER
    assert WorkspaceBoundary.from_mapping({"boundary": "full"}) is WorkspaceBoundary.FULL
    with pytest.raises(ValueError):
        WorkspaceBoundary.from_mapping(None)
    with pytest.raises(ValueError):
        WorkspaceBoundary.from_mapping({})
    with pytest.raises(ValueError):
        WorkspaceBoundary.from_mapping(
            {"file_write": "ask", "command": "ask", "host": "off"}
        )
    with pytest.raises(ValueError):
        WorkspaceBoundary.from_mapping({"boundary": "managed"})


def test_parse_permission_axes_falls_back_to_computer():
    assert parse_permission_axes({"boundary": "read"}) is WorkspaceBoundary.READ
    assert parse_permission_axes(None) is WorkspaceBoundary.COMPUTER
    assert (
        parse_permission_axes({"file_write": "session", "command": "auto"})
        is WorkspaceBoundary.COMPUTER
    )


def test_schema_accepts_only_boundary():
    model = PermissionAxesModel.model_validate({"boundary": "computer"})
    assert model.to_axes() is WorkspaceBoundary.COMPUTER
    assert model.to_axes().to_dict() == {"boundary": "computer"}
    full = PermissionAxesModel.model_validate({"boundary": "full"})
    assert full.to_axes() is WorkspaceBoundary.FULL
    dropped = PermissionAxesModel.model_validate(
        {"boundary": "folder", "file_write": "ask", "command": "auto"}
    )
    assert dropped.boundary is WorkspaceBoundary.FOLDER
    with pytest.raises(ValidationError):
        PermissionAxesModel(boundary="cautious")
    legacy = PermissionAxesUpdate(permission_axes={"file_write": "ask"})
    assert legacy.permission_axes.boundary is WorkspaceBoundary.COMPUTER


def test_read_omits_writes_and_execution():
    names = _names(WorkspaceBoundary.READ)
    assert "run" not in names
    assert "write" not in names
    assert "host" not in names
    assert "web_search" in names
    assert execution_class_enabled_for(_LocalBackend(), WorkspaceBoundary.READ) is False
    out = build_workspace_context(
        _LocalBackend(),
        desktop_online=True,
        permission_axes=WorkspaceBoundary.READ,
    )
    assert "边界：只看" in out
    assert "run" not in _gaps(out)
    assert "host" not in _gaps(out)
    assert boundary_block_message(WorkspaceBoundary.READ, "write")
    assert boundary_block_message(WorkspaceBoundary.READ, "run")
    assert boundary_block_message(WorkspaceBoundary.FOLDER, "run") is None


def test_folder_includes_execution_not_host():
    names = _names(WorkspaceBoundary.FOLDER)
    assert "run" in names
    assert "write" in names
    assert "host" not in names
    out = build_workspace_context(
        _LocalBackend(),
        desktop_online=True,
        permission_axes=WorkspaceBoundary.FOLDER,
    )
    assert "边界：本文件夹" in out
    assert "run" not in _gaps(out)
    assert "host" not in _gaps(out)
    assert (
        execution_tool_auto_passes(
            _LocalBackend(), "run", permission_axes=WorkspaceBoundary.FOLDER
        )
        is True
    )
    assert (
        execution_tool_auto_passes(
            _LocalBackend(), "run", permission_axes=WorkspaceBoundary.READ
        )
        is False
    )
    assert (
        execution_tool_auto_passes(
            _LocalBackend(), "host", permission_axes=WorkspaceBoundary.COMPUTER
        )
        is False
    )


def test_computer_includes_host_and_names_the_boundary():
    names = _names(WorkspaceBoundary.COMPUTER)
    assert "host" in names
    assert "run" in names
    out = build_workspace_context(
        _LocalBackend(),
        desktop_online=False,
        permission_axes=WorkspaceBoundary.COMPUTER,
    )
    assert "边界：这台电脑" in out
    assert "host" in _gaps(out)
    assert boundary_block_message(WorkspaceBoundary.FOLDER, "host")
    assert boundary_block_message(WorkspaceBoundary.COMPUTER, "host") is None


def test_full_local_includes_host_cloud_does_not():
    assert WorkspaceBoundary.FULL.skips_confirm is True
    assert WorkspaceBoundary.FULL.allows_host is True
    assert WorkspaceBoundary.FULL.host_in_registry("local") is True
    assert WorkspaceBoundary.FULL.host_in_registry("server") is False
    names = _names(WorkspaceBoundary.FULL)
    assert "host" in names
    assert "run" in names
    local = build_workspace_context(
        _LocalBackend(),
        desktop_online=True,
        permission_axes=WorkspaceBoundary.FULL,
    )
    assert "边界：完全权限" in local
    assert "host" not in _gaps(local)
    cloud_names = {
        s.name
        for s in build_worker_registry(
            backend=_ServerBackend(), permission_axes=WorkspaceBoundary.FULL
        ).list_all()
    }
    assert "host" not in cloud_names
    cloud = build_workspace_context(
        _ServerBackend(),
        desktop_online=False,
        permission_axes=WorkspaceBoundary.FULL,
    )
    assert "边界：完全权限" in cloud
    assert "host" not in _gaps(cloud)
    assert boundary_block_message(
        WorkspaceBoundary.FULL, "host", location="server"
    )
    assert (
        boundary_block_message(WorkspaceBoundary.FULL, "host", location="local")
        is None
    )


def test_cloud_read_does_not_skip_file_ops():
    assert (
        cloud_worker_skips_per_call_gate(
            _ServerBackend(),
            "write",
            permission_axes=WorkspaceBoundary.READ,
            file_op_tools=frozenset({"write"}),
        )
        is False
    )
    assert (
        cloud_worker_skips_per_call_gate(
            _ServerBackend(),
            "write",
            permission_axes=WorkspaceBoundary.FOLDER,
            file_op_tools=frozenset({"write"}),
        )
        is True
    )
