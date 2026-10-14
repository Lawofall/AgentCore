"""跨文件夹指挥金标链（§4.2b / §4.11）：解析 → target_folder_id → 异桌+记忆；附 2b 闸。

紧凑集成向：复用 folders 工具 mock + target_desktop 接线，不跑端到端 UI。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcore.llm.provider.protocol import LLMProvider
from agentcore.runtime.context.consult_sources import MergedConsultSource
from agentcore.runtime.graph.desk.target_desktop import (
    NO_TARGET_SCRATCH_GATE_MSG,
    LocalRootClaimBook,
    apply_target_desktop,
)
from agentcore.runtime.events import EventSink
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.tools.builtin.consult import ConsultTool
from agentcore.tools.builtin.graph.tool import GraphTool
from agentcore.tools.builtin.folders import FoldersTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from tests.test_folders_tools import _ctx, _FakeFolder, _patch_list


def _birth_ctx(
    *,
    user_id: str = "u1",
    conversation_id: str = "c-cmd",
    backend: object | None = None,
) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=(backend or SimpleNamespace(location="server")),  # type: ignore[arg-type]
        user_id=user_id,
        conversation_id=conversation_id,
    )


@pytest.mark.asyncio
async def test_resolve_delegate_target_desk_and_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """金标：列/解析唯一命中 → plan 带 target_folder_id → worker 桌+记忆跟目标。"""
    _patch_list(
        monkeypatch,
        [
            _FakeFolder(id="birth_f", name="Birth Desk"),
            _FakeFolder(id="folder_alpha", name="Alpha App", rel_path="产品/Alpha App"),
            _FakeFolder(id="folder_beta", name="Beta App", rel_path="产品/Beta App"),
        ],
    )
    ceo_ctx = _ctx(user_id="owner-1", conversation_id="cmd-1")

    listed = await FoldersTool().execute({"action": "list"}, ceo_ctx)
    assert listed.success
    roster = json.loads(listed.output.split("\n", 1)[1])
    assert {f["id"] for f in roster["folders"]} >= {"folder_alpha", "folder_beta"}

    # Nested roster: the full path resolves, and so does the unambiguous suffix.
    resolved = await FoldersTool().execute({"action": "resolve", "path": "产品/Alpha App"}, ceo_ctx)
    assert resolved.success
    assert resolved.display["status"] == "resolved"
    target_id = resolved.display["folder_id"]
    assert target_id == "folder_alpha"

    plan, errors = build_run_plan(
        [
            {
                "role": "甲",
                "task": "在 Alpha 写入口",
                "target_folder_id": target_id,
            },
            {
                "role": "乙",
                "task": "在 Beta 写入口",
                "target_folder_id": "folder_beta",
            },
        ]
    )
    assert errors == []
    by_role = {n.role: n for n in plan.nodes}
    assert by_role["甲"].target_folder_id == "folder_alpha"
    assert by_role["乙"].target_folder_id == "folder_beta"

    session_backend = SimpleNamespace(location="server", _channel=None)
    target_backend = SimpleNamespace(location="server", _channel=None)
    base_ctx = _birth_ctx(user_id="owner-1", backend=session_backend)
    # Seed birth-scoped consult so rewire must replace it with target scope.
    birth_tools = ToolRegistry()
    birth_tools.register(
        ConsultTool(source=MergedConsultSource())
    )
    binding = SimpleNamespace(
        folder_id="folder_alpha",
        rel_path="产品/Alpha App",
        name="Alpha App",
        local_binding=None,
    )

    async def _fake_rebuild(**_kwargs):
        return "PROMPT_FOR_ALPHA", ""

    with (
        patch(
            "agentcore.runtime.graph.desk.target_desktop.load_target_folder_binding",
            new=AsyncMock(return_value=binding),
        ),
        patch(
            "agentcore.runtime.graph.desk.target_desktop.build_target_backend",
            return_value=target_backend,
        ),
        patch(
            "agentcore.runtime.graph.desk.target_desktop.rebuild_worker_prompt_for_target",
            new=_fake_rebuild,
        ),
        patch(
            "agentcore.workspace.locate.workspace_channel_for_tools",
            return_value=None,
        ),
    ):
        applied = await apply_target_desktop(
            target_folder_id=by_role["甲"].target_folder_id or "",
            session_folder_id="birth_f",
            env_system_prompt="BIRTH_PROMPT",
            base_tool_context=base_ctx,
            worker_tools=birth_tools,
            sink=MagicMock(),
            local_root_claims=LocalRootClaimBook(),
        )

    assert applied.target_folder_id == "folder_alpha"
    assert applied.tool_ctx.backend is target_backend
    assert applied.tool_ctx.backend is not session_backend
    assert applied.system_prompt == "PROMPT_FOR_ALPHA"
    consult_tool = applied.worker_tools.get("consult")
    assert isinstance(consult_tool, ConsultTool)
    assert consult_tool.source.rule is not None
    assert consult_tool.source.rule.folder_id == "folder_alpha"
    assert consult_tool.source.rule.folder_id != "birth_f"


@pytest.mark.asyncio
async def test_same_last_segment_across_levels_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """嵌套账号：只报末段名 → 歧义候选带完整路径，禁静默猜。"""
    _patch_list(
        monkeypatch,
        [
            _FakeFolder(id="design", name="图标", rel_path="设计/图标"),
            _FakeFolder(id="archive", name="图标", rel_path="归档/图标"),
        ],
    )
    ceo_ctx = _ctx(user_id="owner-1")

    ambiguous = await FoldersTool().execute({"action": "resolve", "path": "图标"}, ceo_ctx)
    assert ambiguous.display["status"] == "ambiguous"
    assert "设计/图标" in ambiguous.output and "归档/图标" in ambiguous.output

    picked = await FoldersTool().execute({"action": "resolve", "path": "归档/图标"}, ceo_ctx)
    assert picked.display["status"] == "resolved"
    assert picked.display["folder_id"] == "archive"


def test_birth_session_ignores_turn_hint() -> None:
    """有出生文件夹时不消费 turn_target_desk（缺省仍坐出生桌）。"""

    class _DummyLLM(LLMProvider):
        async def complete(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise NotImplementedError

        async def stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise NotImplementedError
            yield  # pragma: no cover

    ctx = _birth_ctx()
    ctx.turn_target_desk.note_folder("other_folder")
    tool = GraphTool(
        llm=_DummyLLM(),  # type: ignore[arg-type]
        sink=EventSink(),
        system_prompt="sys",
        user_message="继续本文件夹",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=ctx,
        folder_id="birth_f",
        captain_run_id="CEO",
        approval_gate=None,
    )
    assert tool.effective_default_target_folder_id() is None
