"""Member turns treat the desktop as offline for CEO, workers, and resume."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from agentcore.desktop.channel import DesktopClientChannel
from agentcore.runtime.events import EventSink
from agentcore.runtime.pipeline.assemble import assemble_ceo_turn
from agentcore.runtime.pipeline.prepare import PreparedTurn, prepare_fresh_turn
from agentcore.runtime.pipeline.resume.wire import _wire_continuation_toolset
from agentcore.tools.mcp.wire import McpDiscoverResult
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_turn_profiles

pytestmark = pytest.mark.anyio


class _CapturedAssemble(Exception):
    def __init__(self, kwargs: dict) -> None:
        super().__init__("captured")
        self.kwargs = kwargs


class _CapturedRegistry(Exception):
    def __init__(self, kwargs: dict, *, channel_built: bool) -> None:
        super().__init__("captured")
        self.kwargs = kwargs
        self.channel_built = channel_built


def test_prepared_turn_requires_member_turn():
    assert "member_turn" in {f.name for f in fields(PreparedTurn)}


def test_prepare_and_resume_fold_member_into_channel():
    import inspect

    from agentcore.runtime.pipeline import assemble as assemble_mod
    from agentcore.runtime.pipeline import prepare as prepare_mod
    from agentcore.runtime.pipeline.resume import wire as wire_mod

    prepare_src = inspect.getsource(prepare_mod.prepare_fresh_turn)
    assert "caller_is_desk_member" in prepare_src
    assert "member_turn=member_turn" in prepare_src
    assert ".for_turn(" in prepare_src
    assert "folder_user_id=folder_rules_user_id" in prepare_src
    assert "resolve_desk_folder_label(folder_rules_user_id" in prepare_src
    assert "resolve_folder_owner_user_id" in prepare_src

    assemble_src = inspect.getsource(assemble_mod.assemble_ceo_turn)
    assert ".for_turn(" in assemble_src

    resume_src = inspect.getsource(wire_mod._wire_continuation_toolset)
    assert "caller_is_desk_member" in resume_src
    assert ".for_turn(" in resume_src


def _backend(tmp_path: Path) -> ServerWorkspace:
    return ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())


def _prepared(tmp_path: Path, *, member_turn: bool) -> PreparedTurn:
    backend = _backend(tmp_path)
    return PreparedTurn(
        llm=object(),
        system_prompt="",
        workspace_facts="",
        worker_base_prompt="",
        worker_tools=ToolRegistry(),
        skill_registry=object(),
        board_channel=None,
        base_tool_context=ToolContext.create(
            execution_id="e",
            run_id="r",
            agent_id="a",
            backend=backend,
            user_id="u",
            conversation_id="c",
        ),
        vision_cost_sink=[],
        attachment_context="",
        user_message="你好",
        native_image_parts=[],
        bound_execution_id="e",
        execution_id_token=object(),
        mcp_discover=McpDiscoverResult(),
        member_turn=member_turn,
    )


async def _capture_toolset(monkeypatch, tmp_path: Path, *, member_turn: bool) -> dict:
    def _fake_assemble(**kwargs):
        raise _CapturedAssemble(kwargs)

    monkeypatch.setattr(
        "agentcore.runtime.pipeline.run._assemble_ceo_toolset",
        _fake_assemble,
    )
    with pytest.raises(_CapturedAssemble) as ei:
        await assemble_ceo_turn(
            prepared=_prepared(tmp_path, member_turn=member_turn),
            conversation_id="c",
            user_message="你好",
            history=[],
            sink=EventSink(),
            backend=_backend(tmp_path),
            folder_id=None,
            approvals_enabled=True,
            permission_axes=None,
            profiles=make_turn_profiles(),
            captain_run_id="cap",
            message_id="m",
            session_saver=None,
            session_loader=None,
            suspension_saver=None,
            suspension_deleter=None,
            x_client_platform="desktop",
        )
    return ei.value.kwargs


async def test_assemble_owner_desktop_keeps_bind_and_desktop_online(
    tmp_path, monkeypatch
):
    kwargs = await _capture_toolset(monkeypatch, tmp_path, member_turn=False)
    assert kwargs["advertise_bind_local_folder"] is True
    assert kwargs["desktop_online"] is True


async def test_assemble_member_desktop_hides_bind_and_desktop_online(
    tmp_path, monkeypatch
):
    kwargs = await _capture_toolset(monkeypatch, tmp_path, member_turn=True)
    assert kwargs["advertise_bind_local_folder"] is False
    assert kwargs["desktop_online"] is False


def _stub_member(monkeypatch, target: str, *, member_turn: bool) -> None:
    async def _is_member(*, user_id, folder_id):
        return member_turn

    monkeypatch.setattr(f"{target}.caller_is_desk_member", _is_member)

    async def _empty_mcp(*_a, **_k):
        return McpDiscoverResult()

    monkeypatch.setattr("agentcore.tools.mcp.discover_mcp_tools", _empty_mcp)

    async def _no_provision(*_a, **_k):
        return None

    monkeypatch.setattr(
        "agentcore.tools.sandbox.desk_provision.provision_server_desk",
        _no_provision,
    )


async def _capture_prepare_registry(
    monkeypatch, tmp_path: Path, *, member_turn: bool
) -> _CapturedRegistry:
    _stub_member(
        monkeypatch, "agentcore.runtime.pipeline.prepare", member_turn=member_turn
    )

    async def _empty_rules(*_a, **_k):
        return ""

    async def _no_desk_label(*_a, **_k):
        return None

    async def _no_vision(*_a, **_k):
        return None

    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.assemble_turn_rules", _empty_rules
    )
    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.resolve_desk_folder_label",
        _no_desk_label,
    )
    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.resolve_vision_reader_for_conversation",
        _no_vision,
    )

    channel_built = False

    def _wrap_channel(**kwargs):
        nonlocal channel_built
        channel_built = True
        return DesktopClientChannel(**kwargs)

    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.DesktopClientChannel", _wrap_channel
    )

    def _spy_registry(**kwargs):
        raise _CapturedRegistry(kwargs, channel_built=channel_built)

    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.build_worker_registry", _spy_registry
    )

    with pytest.raises(_CapturedRegistry) as ei:
        await prepare_fresh_turn(
            conversation_id="c",
            user_id="u",
            backend=_backend(tmp_path),
            sink=EventSink(),
            folder_id=None,
            board_id=None,
            attachments=None,
            permission_axes=None,
            llm_credentials=None,
            x_client_platform="desktop",
        )
    return ei.value


async def test_prepare_owner_desktop_wires_channel_and_worker_online(
    tmp_path, monkeypatch
):
    captured = await _capture_prepare_registry(
        monkeypatch, tmp_path, member_turn=False
    )
    assert captured.channel_built is True
    assert captured.kwargs["desktop_online"] is True


async def test_prepare_member_desktop_skips_channel_and_worker_offline(
    tmp_path, monkeypatch
):
    captured = await _capture_prepare_registry(
        monkeypatch, tmp_path, member_turn=True
    )
    assert captured.channel_built is False
    assert captured.kwargs["desktop_online"] is False


async def _capture_resume_registry(
    monkeypatch, tmp_path: Path, *, member_turn: bool
) -> _CapturedRegistry:
    _stub_member(
        monkeypatch, "agentcore.runtime.pipeline.resume.wire", member_turn=member_turn
    )

    async def _langs(*_a, **_k):
        return ()

    monkeypatch.setattr(
        "agentcore.tools.sandbox.exec_languages.resolve_exec_languages",
        _langs,
    )

    channel_built = False

    def _wrap_channel(**kwargs):
        nonlocal channel_built
        channel_built = True
        return DesktopClientChannel(**kwargs)

    monkeypatch.setattr(
        "agentcore.runtime.pipeline.resume.wire.DesktopClientChannel",
        _wrap_channel,
    )

    def _spy_registry(**kwargs):
        raise _CapturedRegistry(kwargs, channel_built=channel_built)

    monkeypatch.setattr(
        "agentcore.runtime.pipeline.resume.wire.build_worker_registry",
        _spy_registry,
    )

    with pytest.raises(_CapturedRegistry) as ei:
        await _wire_continuation_toolset(
            llm=object(),
            sink=EventSink(),
            backend=_backend(tmp_path),
            board_id=None,
            conversation_id="c",
            message_id="m",
            captain_run_id="cap",
            user_id="u",
            folder_id=None,
            base_system_prompt="",
            user_message="你好",
            journal_entries=[],
            display_journal=None,
            profiles=make_turn_profiles(),
            permission_axes=None,
            session_saver=None,
            session_loader=None,
            suspension_saver=None,
            suspension_deleter=None,
            x_client_platform="desktop",
        )
    return ei.value


async def test_resume_owner_desktop_wires_channel_and_worker_online(
    tmp_path, monkeypatch
):
    captured = await _capture_resume_registry(
        monkeypatch, tmp_path, member_turn=False
    )
    assert captured.channel_built is True
    assert captured.kwargs["desktop_online"] is True


async def test_resume_member_desktop_skips_channel_and_worker_offline(
    tmp_path, monkeypatch
):
    captured = await _capture_resume_registry(
        monkeypatch, tmp_path, member_turn=True
    )
    assert captured.channel_built is False
    assert captured.kwargs["desktop_online"] is False
