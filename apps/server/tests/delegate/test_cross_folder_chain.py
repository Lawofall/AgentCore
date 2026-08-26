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
from agentcore.runtime.context.consult_sources import MemoryConsultSource, MergedConsultSource
from agentcore.runtime.delegate.target_desktop import (
    NO_TARGET_SCRATCH_GATE_MSG,
    LocalRootClaimBook,
    apply_target_desktop,
)
from agentcore.runtime.events import EventSink
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.tools.builtin.consult import ConsultTool
from agentcore.tools.builtin.delegate.tool import DelegateTool
from agentcore.tools.builtin.folders import ListFoldersTool, ResolveFolderTool
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

    listed = await ListFoldersTool().execute({}, ceo_ctx)
    assert listed.success
    roster = json.loads(listed.output.split("\n", 1)[1])
    assert {f["id"] for f in roster["folders"]} >= {"folder_alpha", "folder_beta"}

    # Nested roster: the full path resolves, and so does the unambiguous suffix.
    resolved = await ResolveFolderTool().execute({"path": "产品/Alpha App"}, ceo_ctx)
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
        ConsultTool(source=MergedConsultSource(memory=MemoryConsultSource(store=MagicMock(), folder_id="birth_f")))
    )
    binding = SimpleNamespace(
        folder_id="folder_alpha",
        rel_path="产品/Alpha App",
        name="Alpha App",
        local_binding=None,
    )

    async def _fake_rebuild(**_kwargs):
        return "PROMPT_FOR_ALPHA"

    with (
        patch(
            "agentcore.runtime.delegate.target_desktop.load_target_folder_binding",
            new=AsyncMock(return_value=binding),
        ),
        patch(
            "agentcore.runtime.delegate.target_desktop.build_target_backend",
            return_value=target_backend,
        ),
        patch(
            "agentcore.runtime.delegate.target_desktop.rebuild_worker_prompt_for_target",
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
    memory_tool = applied.worker_tools.get("consult")
    assert isinstance(memory_tool, ConsultTool)
    assert memory_tool.source.memory.folder_id == "folder_alpha"
    assert memory_tool.source.memory.folder_id != "birth_f"


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

    ambiguous = await ResolveFolderTool().execute({"path": "图标"}, ceo_ctx)
    assert ambiguous.display["status"] == "ambiguous"
    assert "设计/图标" in ambiguous.output and "归档/图标" in ambiguous.output

    picked = await ResolveFolderTool().execute({"path": "归档/图标"}, ceo_ctx)
    assert picked.display["status"] == "resolved"
    assert picked.display["folder_id"] == "archive"


@pytest.mark.asyncio
async def test_bare_chat_write_no_target_auto_provisions_cloud_desk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """裸聊写盘缺 target → 系统静默建云桌并过 2b 闸（不再拒成催 create）。"""

    class _DummyLLM(LLMProvider):
        async def complete(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise NotImplementedError

        async def stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise NotImplementedError
            yield  # pragma: no cover

    creates: list[str] = []

    async def _fake_create(*, user_id: str, name: str, **_kwargs: Any) -> dict:
        creates.append(name)
        return {"id": "auto_cloud_1", "name": name, "mode": "cloud"}

    monkeypatch.setattr(
        "agentcore.tools.builtin.folders.create_cloud_folder",
        _fake_create,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._load_conversation_title",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._load_auto_desk_folder_id",
        AsyncMock(return_value=None),
    )
    from agentcore.runtime.delegate.target_desktop_auto_cloud import AutoDeskPersistResult

    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._persist_auto_desk_folder_id",
        AsyncMock(return_value=AutoDeskPersistResult("auto_cloud_1", "won")),
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop.bind_tool_context_to_landing_desk",
        AsyncMock(return_value=True),
    )

    ctx = _birth_ctx()
    tool = DelegateTool(
        llm=_DummyLLM(),  # type: ignore[arg-type]
        sink=EventSink(),
        system_prompt="sys",
        user_message="做一个落地页",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=ctx,
        folder_id=None,
        captain_run_id="CEO",
        approval_gate=None,
    )
    result = await tool.execute(
        {
            "tasks": [
                {
                    "role": "工",
                    "task": "写 README",
                    "deliverable": {"form": "files"},
                }
            ]
        },
        ctx,
    )
    err = result.error or ""
    assert not err.startswith(NO_TARGET_SCRATCH_GATE_MSG)
    # 无会话标题 → 通用名；用户原话不参与命名。
    assert creates == ["云文件夹"]
    assert ctx.turn_target_desk.folder_id == "auto_cloud_1"
    assert ctx.turn_target_desk.auto_cloud_provisioned is True
    assert tool.effective_default_target_folder_id() == "auto_cloud_1"


@pytest.mark.asyncio
async def test_bare_chat_prose_no_target_passes_2b_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """方案 C：无出生 + 无写盘 deliverable → 过闸且不静默建云桌。"""

    class _DummyLLM(LLMProvider):
        async def complete(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise NotImplementedError

        async def stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise NotImplementedError
            yield  # pragma: no cover

    creates: list[str] = []

    async def _fake_create(*, user_id: str, name: str, **_kwargs: Any) -> dict:
        creates.append(name)
        return {"id": "should_not", "name": name, "mode": "cloud"}

    monkeypatch.setattr(
        "agentcore.tools.builtin.folders.create_cloud_folder",
        _fake_create,
    )

    ctx = _birth_ctx()
    tool = DelegateTool(
        llm=_DummyLLM(),  # type: ignore[arg-type]
        sink=EventSink(),
        system_prompt="sys",
        user_message="打个招呼",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=ctx,
        folder_id=None,
        captain_run_id="CEO",
        approval_gate=None,
    )
    result = await tool.execute(
        {
            "tasks": [
                {
                    "role": "客服",
                    "task": "打招呼",
                    "deliverable": {"form": "prose"},
                }
            ]
        },
        ctx,
    )
    err = result.error or ""
    assert not err.startswith(NO_TARGET_SCRATCH_GATE_MSG)
    assert creates == []
    assert ctx.turn_target_desk.folder_id is None
    assert ctx.turn_target_desk.auto_cloud_provisioned is False


@pytest.mark.asyncio
async def test_bare_chat_turn_hint_passes_2b_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同回合唯一 create/resolve 写入 turn_target_desk 后，缺省 delegate 可过 2b 闸且不重复建。"""

    class _DummyLLM(LLMProvider):
        async def complete(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise NotImplementedError

        async def stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise NotImplementedError
            yield  # pragma: no cover

    creates: list[str] = []

    async def _fake_create(*, user_id: str, name: str, **_kwargs: Any) -> dict:
        creates.append(name)
        return {"id": "should_not", "name": name, "mode": "cloud"}

    monkeypatch.setattr(
        "agentcore.tools.builtin.folders.create_cloud_folder",
        _fake_create,
    )

    ctx = _birth_ctx()
    ctx.turn_target_desk.note_folder("folder_from_create")
    tool = DelegateTool(
        llm=_DummyLLM(),  # type: ignore[arg-type]
        sink=EventSink(),
        system_prompt="sys",
        user_message="做个官网",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=ctx,
        folder_id=None,
        captain_run_id="CEO",
        approval_gate=None,
    )
    assert tool.effective_default_target_folder_id() == "folder_from_create"
    result = await tool.execute(
        {
            "tasks": [
                {
                    "role": "前端",
                    "task": "写 index.html",
                    "deliverable": {"form": "files"},
                }
            ]
        },
        ctx,
    )
    err = result.error or ""
    assert not err.startswith(NO_TARGET_SCRATCH_GATE_MSG)
    assert creates == []
    assert ctx.turn_target_desk.folder_id == "folder_from_create"
    assert ctx.turn_target_desk.auto_cloud_provisioned is False


@pytest.mark.asyncio
async def test_bare_chat_multi_hint_still_blocked() -> None:
    """同回合两个不同文件夹 → turn hint 清空 → 写盘 task 仍须显式 target_folder_id。"""

    class _DummyLLM(LLMProvider):
        async def complete(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise NotImplementedError

        async def stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise NotImplementedError
            yield  # pragma: no cover

    ctx = _birth_ctx()
    ctx.turn_target_desk.note_folder("folder_a")
    ctx.turn_target_desk.note_folder("folder_b")
    assert ctx.turn_target_desk.folder_id is None
    tool = DelegateTool(
        llm=_DummyLLM(),  # type: ignore[arg-type]
        sink=EventSink(),
        system_prompt="sys",
        user_message="两个文件夹并行",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=ctx,
        folder_id=None,
        captain_run_id="CEO",
        approval_gate=None,
    )
    result = await tool.execute(
        {
            "tasks": [
                {
                    "role": "甲",
                    "task": "在 A 写",
                    "deliverable": {"form": "files"},
                },
                {
                    "role": "乙",
                    "task": "在 B 写",
                    "deliverable": {"form": "files"},
                },
            ]
        },
        ctx,
    )
    assert result.success is False
    assert result.contract_failure is True
    assert (result.error or "").startswith(NO_TARGET_SCRATCH_GATE_MSG)


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
    tool = DelegateTool(
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
