"""Independent prepare/assemble IO overlaps instead of queueing."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from agentcore.memory.rules_injection import TurnRuleView
from agentcore.runtime.context import WorkspaceGitFact
from agentcore.runtime.events import EventSink
from agentcore.runtime.pipeline.assemble import assemble_ceo_turn
from agentcore.runtime.pipeline.prepare import PreparedTurn, prepare_fresh_turn
from agentcore.tools.mcp.wire import McpDiscoverResult
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_turn_profiles

pytestmark = pytest.mark.anyio

_DELAY = 0.04


class _AfterWave(Exception):
    pass


class _CapturedAssemble(Exception):
    def __init__(self, kwargs: dict) -> None:
        super().__init__("captured")
        self.kwargs = kwargs


def _cloud(tmp_path: Path) -> ServerWorkspace:
    return ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())


def _sleeper(name: str, starts: dict, ends: dict, result):
    async def _run(*_a, **_k):
        starts[name] = time.monotonic()
        await asyncio.sleep(_DELAY)
        ends[name] = time.monotonic()
        return result

    return _run


async def test_prepare_independent_io_overlaps(monkeypatch, tmp_path):
    starts: dict[str, float] = {}
    ends: dict[str, float] = {}

    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.load_turn_rule_view",
        _sleeper("rules", starts, ends, TurnRuleView()),
    )
    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.resolve_desk_folder_label",
        _sleeper("desk_folder_label", starts, ends, None),
    )
    monkeypatch.setattr(
        "agentcore.runtime.pipeline.build_turn_router",
        _sleeper("llm", starts, ends, object()),
    )
    monkeypatch.setattr(
        "agentcore.tools.mcp.discover_mcp_tools",
        _sleeper("mcp", starts, ends, McpDiscoverResult()),
    )
    monkeypatch.setattr(
        "agentcore.tools.sandbox.exec_languages.resolve_exec_languages",
        _sleeper("exec_languages", starts, ends, ("python",)),
    )
    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.detect_workspace_git",
        _sleeper("git", starts, ends, WorkspaceGitFact(present=False)),
    )
    monkeypatch.setattr(
        "agentcore.workspace.desk_empty.desk_is_visibly_empty",
        _sleeper("desk_empty", starts, ends, True),
    )
    monkeypatch.setattr(
        "agentcore.tools.sandbox.desk_provision.provision_server_desk",
        _sleeper("cloud_desk", starts, ends, None),
    )

    async def _no_member(*, user_id, folder_id):
        del user_id, folder_id
        return False

    async def _no_owner(_folder_id):
        return None

    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.caller_is_desk_member", _no_member
    )
    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.resolve_folder_owner_user_id", _no_owner
    )

    async def _no_adopt(*_a, **_k):
        return None

    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop.adopt_persisted_auto_desk",
        _no_adopt,
    )

    def _boom(**_k):
        raise _AfterWave

    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.build_worker_registry",
        _boom,
    )

    wall0 = time.monotonic()
    with pytest.raises(_AfterWave):
        await prepare_fresh_turn(
            conversation_id="c-parallel",
            user_id="u-parallel",
            backend=_cloud(tmp_path),
            sink=EventSink(),
            folder_id=None,
            attachments=None,
            permission_axes=None,
            llm_credentials=None,
            x_client_platform="web",
        )
    wall = time.monotonic() - wall0

    overlapped = ("rules", "desk_folder_label", "llm", "mcp", "cloud_desk")
    for name in overlapped:
        assert name in starts, name
    sequential = sum(ends[n] - starts[n] for n in overlapped)
    assert wall < sequential * 0.7, f"wall={wall:.3f}s sequential={sequential:.3f}s"
    assert wall < 0.18


async def test_assemble_cancels_conversation_edits_when_toolset_raises(
    monkeypatch, tmp_path
):
    cancelled = asyncio.Event()

    async def _hang(*_a, **_k):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return ""

    monkeypatch.setattr(
        "agentcore.runtime.pipeline.assemble.build_conversation_edits_block", _hang
    )

    def _fake_assemble(**kwargs):
        raise _CapturedAssemble(kwargs)

    monkeypatch.setattr(
        "agentcore.runtime.pipeline.run._assemble_ceo_toolset",
        _fake_assemble,
    )

    backend = _cloud(tmp_path)
    prepared = PreparedTurn(
        llm=object(),
        system_prompt="",
        workspace_facts="",
        worker_base_prompt="",
        worker_envelope="",
        worker_tools=ToolRegistry(),
        skill_registry=object(),
        table_context="",
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
        member_turn=False,
    )
    with pytest.raises(_CapturedAssemble):
        await assemble_ceo_turn(
            prepared=prepared,
            conversation_id="c",
            user_message="你好",
            history=[],
            sink=EventSink(),
            backend=backend,
            folder_id="folder-1",
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
    assert cancelled.is_set()
