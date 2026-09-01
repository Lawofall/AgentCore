"""CEO ``delete_folder``：软删一个文件夹，按 id、逐个审批、够不到彻底删。

钉四条人定死的约束：
  * **只软删**——走 ``soft_delete_folder_tree`` / ``DELETE /folders/{id}``；
    ``permanent_delete_folder`` 永不被调用（彻底删只由用户在桌面弹窗勾选）。
  * **只按 folder_id**——嵌套之后同一个末段名可以合法地出现在两层
    （``设计/图标`` 与 ``归档/图标``），按名删必然误删：名字形状的参数一律拒绝，
    且拒绝时不查名册、不删任何东西。
  * **删的是整棵子树，且目录要跟着走**——DB 行进最近删除的同时目录搬去墓碑区，
    否则同层这个名字被占满整个保留期（双模式工作区 §5.4）。
  * **每次删除逐个确认**——恒确认工具：turn grant 吃不掉卡，同回合的兄弟删除调用
    也不会被一次「本轮内都允许」扫掉；用户拒绝则工具根本不执行。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from agentcore.core.types import (
    DEFAULT_PERMISSION_AXES,
    AutonomyPolicy,
    PermissionAxes,
    ToolApproval,
    ToolCategory,
    recipe_to_axes,
)
from agentcore.folders.credentials import FoldersCredentials, folders_credentials_scope
from agentcore.llm.provider.protocol import ToolCall
from agentcore.runtime.always_confirm import (
    always_confirm_tool_names,
    requires_always_confirm,
)
from agentcore.runtime.approval_preview import enrich_approval_preview
from agentcore.runtime.approvals import ApprovalDecision, ApprovalGate
from agentcore.runtime.engine.tool_exec_gates import _check_safety_and_approval_gates
from agentcore.runtime.events import EventSink, EventType, SSEEvent
from agentcore.runtime.interaction import InteractionKind, InteractionRegistry
from agentcore.tools.builtin import (
    approval_class_tool_names,
    delegation_grantable_tool_names,
)
from agentcore.tools.builtin.folders import DeleteFolderTool, looks_like_folder_id
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registration import (
    AUDIENCE_CEO,
    CeoWire,
    ToolSurface,
    declared_tool_name,
    declared_tools,
    tool_registration,
)
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace

ALPHA_ID = "11111111-2222-4333-8444-555555555555"
BETA_ID = "66666666-7777-4888-8999-aaaaaaaaaaaa"


def _ctx(user_id: str = "u1") -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id=user_id,
        conversation_id="conv-1",
    )


class _FakeFolder:
    def __init__(
        self,
        *,
        id: str,
        name: str,
        local_root_id: str | None = None,
        rel_path: str | None = None,
    ) -> None:
        self.id = id
        self.name = name
        self.local_root_id = local_root_id
        self.local_subpath = None
        self.rel_path = None if local_root_id else (rel_path or name)
        self.created_at = datetime(2026, 1, 1)
        self.updated_at = datetime(2026, 1, 2)


class _RepoCalls:
    def __init__(self) -> None:
        self.loaded: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, str]] = []


def _patch_repo(
    monkeypatch: pytest.MonkeyPatch,
    folders: dict[str, _FakeFolder],
    *,
    delete_returns: bool = True,
    delete_raises: BaseException | None = None,
) -> _RepoCalls:
    """Stub the DB path (no folders creds bound) and record every call."""
    import agentcore.folders.tree_ops as tree_ops
    import agentcore.tools.builtin.folders as folders_mod

    calls = _RepoCalls()

    class _Repo:
        def __init__(self, session: Any) -> None:
            del session

        async def get_by_id(self, folder_id: str, *, user_id: str) -> _FakeFolder | None:
            calls.loaded.append((folder_id, user_id))
            folder = folders.get(folder_id)
            return folder if folder is not None else None

        async def list_live_subtree_ids(
            self, folder_id: str, *, user_id: str
        ) -> list[str]:
            del user_id
            return [folder_id] if folder_id in folders else []

    class _CM:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    async def _fake_tree_delete(
        session: Any, *, user_id: str, folder_id: str, **kwargs: Any
    ) -> bool:
        del session, kwargs
        calls.deleted.append((folder_id, user_id))
        if delete_raises is not None:
            raise delete_raises
        return delete_returns and folder_id in folders

    monkeypatch.setattr(folders_mod, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(folders_mod, "FolderRepository", _Repo)
    # The tool must not soft-delete the row without moving the directory out of the
    # user tree, so it goes through tree_ops — patch there, not at the repository.
    monkeypatch.setattr(tree_ops, "soft_delete_folder_tree", _fake_tree_delete)
    return calls


def _forbid_permanent_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    """彻底删对 AI 不可达：任何调用都当作测试失败。"""

    async def _boom(**kwargs: Any) -> bool:
        raise AssertionError(f"delete_folder must never permanently delete: {kwargs}")

    monkeypatch.setattr(
        "agentcore.folders.permanent_delete.permanent_delete_folder", _boom
    )


# --- schema / registration ----------------------------------------------------


def test_delete_folder_schema_is_single_id_only():
    tool = DeleteFolderTool()
    schema = tool.schema
    assert schema.name == "delete_folder"
    assert schema.category is ToolCategory.ORCHESTRATION
    # 每次删除都要人点确认卡。
    assert schema.approval is ToolApproval.GRANTABLE

    props = schema.parameters["properties"]
    assert set(props) == {"folder_id"}
    assert schema.parameters["required"] == ["folder_id"]
    # 无批量形态：不接受数组 / 多 id。
    assert props["folder_id"]["type"] == "string"
    assert "permanent" not in props
    assert "不接受名字或路径" in props["folder_id"]["description"]

    assert "软删" in schema.description
    assert "只按 folder_id" in schema.description
    assert "彻底删除做不到" in schema.description
    assert "一次一个" in schema.description
    # 嵌套：删的是整棵子树，且名字立刻可复用。
    assert "连子文件夹一起" in schema.description


def test_delete_folder_registration_is_ceo_only():
    reg = tool_registration(DeleteFolderTool)
    assert reg.surface is ToolSurface.CEO_ORCHESTRATION
    assert reg.audience == (AUDIENCE_CEO,)
    assert reg.ceo_wire is CeoWire.ALWAYS
    assert "delete_folder" in {declared_tool_name(cls) for cls in declared_tools()}


def test_delete_folder_is_always_confirm():
    """恒确认：任何授权姿态都吃不掉这张卡（判据层）。"""
    assert requires_always_confirm("delete_folder", {"folder_id": ALPHA_ID}) is True
    assert requires_always_confirm("delete_folder", {}) is True
    assert "delete_folder" in always_confirm_tool_names()


def test_looks_like_folder_id_rejects_names():
    assert looks_like_folder_id(ALPHA_ID) is True
    for name in ("dogfood-dup", "设计/图标", "AgentCore", "", "  ", "folder_id"):
        assert looks_like_folder_id(name) is False, name


# --- 拒绝按名 / 按路径删 -------------------------------------------------------


async def test_refuses_name_in_folder_id_without_touching_roster(
    monkeypatch: pytest.MonkeyPatch,
):
    """名字塞进 folder_id：拒绝，且不查名册、不删任何东西。"""
    calls = _patch_repo(monkeypatch, {ALPHA_ID: _FakeFolder(id=ALPHA_ID, name="Alpha")})
    _forbid_permanent_delete(monkeypatch)

    result = await DeleteFolderTool().execute({"folder_id": "dogfood-dup"}, _ctx())

    assert not result.success
    assert result.error == "delete_by_name_refused"
    assert "不接受文件夹名 / 路径" in result.output
    assert "resolve_folder" in result.output
    assert calls.loaded == []
    assert calls.deleted == []


async def test_refuses_path_in_folder_id(monkeypatch: pytest.MonkeyPatch):
    """嵌套路径也不行：跨层同名合法，路径解析属于 resolve_folder 的职责。"""
    calls = _patch_repo(monkeypatch, {ALPHA_ID: _FakeFolder(id=ALPHA_ID, name="图标")})
    result = await DeleteFolderTool().execute({"folder_id": "设计/图标"}, _ctx())
    assert not result.success
    assert result.error == "delete_by_name_refused"
    assert calls.loaded == []
    assert calls.deleted == []


async def test_refuses_name_argument(monkeypatch: pytest.MonkeyPatch):
    """模型改用 name= / path= 想按名删：同样拒绝，不静默忽略参数。"""
    calls = _patch_repo(monkeypatch, {ALPHA_ID: _FakeFolder(id=ALPHA_ID, name="Alpha")})

    for arguments in (
        {"name": "dogfood-dup"},
        {"folder_name": "dogfood-dup"},
        {"path": "设计/图标"},
        {"folder_path": "设计/图标"},
        {"names": ["dogfood-dup", "Alpha"]},
    ):
        result = await DeleteFolderTool().execute(arguments, _ctx())
        assert not result.success, arguments
        assert result.error == "delete_by_name_refused", arguments
    assert calls.deleted == []


async def test_missing_folder_id_is_distinct_from_name_delete(
    monkeypatch: pytest.MonkeyPatch,
):
    calls = _patch_repo(monkeypatch, {})
    result = await DeleteFolderTool().execute({}, _ctx())
    assert not result.success
    assert result.error == "missing folder_id"
    assert "list_folders" in result.output
    assert calls.deleted == []


# --- 软删成功 -----------------------------------------------------------------


async def test_soft_delete_success_names_the_folder(monkeypatch: pytest.MonkeyPatch):
    calls = _patch_repo(
        monkeypatch,
        {
            ALPHA_ID: _FakeFolder(id=ALPHA_ID, name="图标", rel_path="设计/图标"),
            BETA_ID: _FakeFolder(id=BETA_ID, name="图标", rel_path="归档/图标"),
        },
    )
    _forbid_permanent_delete(monkeypatch)

    result = await DeleteFolderTool().execute({"folder_id": BETA_ID}, _ctx("owner-1"))

    assert result.success
    # 只删点名的那个同名文件夹。
    assert calls.deleted == [(BETA_ID, "owner-1")]
    assert result.display == {
        "status": "deleted",
        "folder_id": BETA_ID,
        "name": "图标",
        "rel_path": "归档/图标",
        "mode": "cloud",
        "permanent": False,
    }
    # 回显带完整路径，不是只有一串 id、也不是分不清层级的末段名。
    assert "归档/图标" in result.output
    payload = json.loads(result.output.split("\n", 1)[1])
    assert payload["status"] == "deleted"
    assert payload["permanent"] is False
    assert payload["folder"]["id"] == BETA_ID
    # 连带影响诚实交代：子文件夹 + 归档 + 名字立刻可复用 + 保留期回收 +
    # 不动本机目录 + 彻底删做不到。
    assert "子文件夹" in payload["hint"]
    assert "归档" in payload["hint"]
    assert "名字立刻可以再用" in payload["hint"]
    assert "保留期" in payload["hint"]
    assert "本机目录" in payload["hint"]
    assert "彻底删除" in payload["hint"]


async def test_soft_delete_goes_through_tree_ops(monkeypatch: pytest.MonkeyPatch):
    """DB 行与盘上目录必须一起动：绕开 tree_ops 会让名字被占满保留期。"""
    import agentcore.folders.tree_ops as tree_ops
    import agentcore.tools.builtin.folders as folders_mod

    _patch_repo(monkeypatch, {ALPHA_ID: _FakeFolder(id=ALPHA_ID, name="Alpha")})

    async def _forbid_bare_repo_delete(*a: Any, **k: Any) -> bool:
        raise AssertionError("delete_folder must not soft-delete the row alone")

    monkeypatch.setattr(
        "agentcore.db.repositories.folders.FolderRepository.soft_delete",
        _forbid_bare_repo_delete,
        raising=False,
    )
    assert folders_mod.soft_delete_folder is not None
    assert tree_ops.soft_delete_folder_tree is not None

    result = await DeleteFolderTool().execute({"folder_id": ALPHA_ID}, _ctx())
    assert result.success


async def test_soft_delete_uses_cloud_ticket_when_creds_bound(
    monkeypatch: pytest.MonkeyPatch,
):
    """Sidecar 回合：走 folders 窄票 HTTP 软删，不碰进程内 DB。"""
    import agentcore.folders.tree_ops as tree_ops
    import agentcore.tools.builtin.folders as folders_mod

    class _Repo:
        def __init__(self, session: Any) -> None:
            del session

        async def get_by_id(self, *a: Any, **k: Any) -> None:
            raise AssertionError("must not hit DB when folders creds bound")

    class _CM:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    async def _forbid_tree_delete(*a: Any, **k: Any) -> bool:
        raise AssertionError("must not hit DB when folders creds bound")

    monkeypatch.setattr(folders_mod, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(folders_mod, "FolderRepository", _Repo)
    monkeypatch.setattr(tree_ops, "soft_delete_folder_tree", _forbid_tree_delete)
    _forbid_permanent_delete(monkeypatch)

    deleted: list[str] = []

    async def _fake_get(creds: FoldersCredentials, *, folder_id: str) -> dict[str, Any]:
        assert creds.api_key == "folders-jwt"
        return {
            "id": folder_id,
            "name": "Cloud App",
            "mode": "cloud",
            "local_root_id": None,
            "local_subpath": None,
            "rel_path": "工作/Cloud App",
            "parent_rel_path": "工作",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-02T00:00:00",
        }

    async def _fake_delete(creds: FoldersCredentials, *, folder_id: str) -> bool:
        deleted.append(folder_id)
        return True

    monkeypatch.setattr("agentcore.folders.credentials.cloud_get_folder", _fake_get)
    monkeypatch.setattr(
        "agentcore.folders.credentials.cloud_soft_delete_folder", _fake_delete
    )

    creds = FoldersCredentials(
        api_key="folders-jwt", base_url="https://cloud.example/v1/folders"
    )
    with folders_credentials_scope(creds):
        result = await DeleteFolderTool().execute({"folder_id": ALPHA_ID}, _ctx())

    assert result.success
    assert deleted == [ALPHA_ID]
    assert "工作/Cloud App" in result.output


async def test_unknown_or_foreign_folder_deletes_nothing(
    monkeypatch: pytest.MonkeyPatch,
):
    """不存在 / 别人的文件夹同一张脸（IDOR-safe），且不发起删除。"""
    calls = _patch_repo(monkeypatch, {ALPHA_ID: _FakeFolder(id=ALPHA_ID, name="Alpha")})

    result = await DeleteFolderTool().execute({"folder_id": BETA_ID}, _ctx())

    assert not result.success
    assert result.error == "folder_not_found"
    assert "没有删除任何东西" in result.output
    assert calls.deleted == []


async def test_workspace_busy_is_retriable_and_deleted_nothing(
    monkeypatch: pytest.MonkeyPatch,
):
    """搬目录要拿工作区锁；回合在跑时诚实回「正忙、什么都没删」，别报通用失败。"""
    from agentcore.workspace.locks import WorkspaceBusyError

    _patch_repo(
        monkeypatch,
        {ALPHA_ID: _FakeFolder(id=ALPHA_ID, name="Alpha")},
        delete_raises=WorkspaceBusyError("busy"),
    )

    result = await DeleteFolderTool().execute({"folder_id": ALPHA_ID}, _ctx())
    assert not result.success
    assert result.error == "workspace_busy"
    assert "未删除任何东西" in result.output
    assert "无法确定" not in result.output


async def test_db_unreachable_is_honest_about_not_deleting(
    monkeypatch: pytest.MonkeyPatch,
):
    from sqlalchemy.exc import OperationalError

    import agentcore.tools.builtin.folders as folders_mod
    from agentcore.db.errors import DATABASE_UNAVAILABLE_CODE

    err = OperationalError("SELECT 1", {}, ConnectionRefusedError("refused"))

    class _CM:
        async def __aenter__(self) -> object:
            raise err

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(folders_mod, "async_session_factory", lambda: _CM())

    result = await DeleteFolderTool().execute({"folder_id": ALPHA_ID}, _ctx())
    assert not result.success
    assert result.error == DATABASE_UNAVAILABLE_CODE
    assert "未删除任何东西" in result.output


# --- 审批：逐个确认，未通过则不执行 -------------------------------------------


def _drain(sink: EventSink) -> list[SSEEvent]:
    events: list[SSEEvent] = []
    while not sink._queue.empty():  # noqa: SLF001 - test-only inspection
        events.append(sink._queue.get_nowait())
    return events


async def _resolve_when_ready(
    registry: InteractionRegistry,
    approval_id: str,
    decision: ApprovalDecision,
    conversation_id: str = "conv-1",
) -> None:
    for _ in range(2000):
        if registry.resolve(approval_id, decision, conversation_id=conversation_id):
            return
        await asyncio.sleep(0)
    raise AssertionError(f"approval {approval_id!r} never became pending")


def _ceo_gate(
    sink: EventSink,
    registry: InteractionRegistry,
    *,
    axes: PermissionAxes = DEFAULT_PERMISSION_AXES,
) -> ApprovalGate:
    return ApprovalGate(
        sink=sink,
        conversation_id="conv-1",
        registry=registry,
        timeout_seconds=5.0,
        file_op_tools=approval_class_tool_names(),
        delegation_grantable_tools=delegation_grantable_tool_names(),
        permission_axes=axes,
    )


async def _run_delete_gates(
    gate: ApprovalGate,
    sink: EventSink,
    *,
    folder_id: str,
    tool_call_id: str,
):
    """跑一次 CEO delete_folder 的审批门，返回 ``None``（放行）或拒绝结果。"""
    return await _check_safety_and_approval_gates(
        name="delete_folder",
        args={"folder_id": folder_id},
        tool_schema=DeleteFolderTool().schema,
        tc=ToolCall(id=tool_call_id),
        context=_ctx(),
        sink=sink,
        event_run_id="run-1",
        run_id="run-1",
        role="captain",
        fingerprint="fp-1",
        approval_gate=gate,
    )


async def test_delete_prompts_and_deny_blocks_execution(
    monkeypatch: pytest.MonkeyPatch,
):
    """用户拒绝 → 门直接短路，工具的 execute 根本不跑。"""
    calls = _patch_repo(monkeypatch, {ALPHA_ID: _FakeFolder(id=ALPHA_ID, name="Alpha")})
    _forbid_permanent_delete(monkeypatch)

    reg = InteractionRegistry()
    sink = EventSink()
    gate = _ceo_gate(sink, reg)

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "del-1", ApprovalDecision.DENY)
    )
    denied = await _run_delete_gates(
        gate, sink, folder_id=ALPHA_ID, tool_call_id="del-1"
    )
    await resolver

    assert denied is not None
    assert denied.attempt.policy_failure is True
    assert any(e.type is EventType.APPROVAL_REQUIRED for e in _drain(sink))
    # 门拒绝 ⇒ 引擎不会调 execute，删除从未发生。名册那一次读是弹卡前的实名查询
    # （只读，为了卡上能写出文件夹路径），不是删除路径。
    assert calls.deleted == []
    assert calls.loaded == [(ALPHA_ID, "u1")]


async def test_delete_runs_after_approve(monkeypatch: pytest.MonkeyPatch):
    calls = _patch_repo(monkeypatch, {ALPHA_ID: _FakeFolder(id=ALPHA_ID, name="Alpha")})
    _forbid_permanent_delete(monkeypatch)

    reg = InteractionRegistry()
    sink = EventSink()
    gate = _ceo_gate(sink, reg)

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "del-ok", ApprovalDecision.APPROVE)
    )
    denied = await _run_delete_gates(
        gate, sink, folder_id=ALPHA_ID, tool_call_id="del-ok"
    )
    await resolver

    assert denied is None
    assert any(e.type is EventType.APPROVAL_REQUIRED for e in _drain(sink))
    # 门放行后引擎才执行工具本体。
    result = await DeleteFolderTool().execute({"folder_id": ALPHA_ID}, _ctx())
    assert result.success
    assert calls.deleted == [(ALPHA_ID, "u1")]


async def test_turn_grant_cannot_cover_a_second_delete(
    monkeypatch: pytest.MonkeyPatch,
):
    """「本轮内都允许」被降级为一次性：第二个删除仍要自己那张卡。"""
    _patch_repo(monkeypatch, {ALPHA_ID: _FakeFolder(id=ALPHA_ID, name="Alpha")})

    reg = InteractionRegistry()
    sink = EventSink()
    gate = _ceo_gate(sink, reg)

    first = asyncio.create_task(
        _resolve_when_ready(reg, "del-a", ApprovalDecision.APPROVE_ALWAYS)
    )
    assert await _run_delete_gates(
        gate, sink, folder_id=ALPHA_ID, tool_call_id="del-a"
    ) is None
    await first
    _drain(sink)

    second = asyncio.create_task(
        _resolve_when_ready(reg, "del-b", ApprovalDecision.APPROVE)
    )
    assert await _run_delete_gates(
        gate, sink, folder_id=BETA_ID, tool_call_id="del-b"
    ) is None
    await second
    assert any(e.type is EventType.APPROVAL_REQUIRED for e in _drain(sink))


async def test_file_class_grant_does_not_sweep_pending_deletes():
    """一次「本轮内允许所有文件改动」不得顺手扫掉挂起的删除卡。"""
    reg = InteractionRegistry()
    sink = EventSink()
    # 谨慎档：file_write=ask，写文件才会真的弹卡（默认档 session 会静默放行）。
    gate = _ceo_gate(sink, reg, axes=recipe_to_axes(AutonomyPolicy.CAUTIOUS))

    pending = asyncio.create_task(
        gate.authorize(
            tool_name="delete_folder",
            tool_call_id="del-pending",
            arguments={"folder_id": ALPHA_ID},
        )
    )
    for _ in range(2000):
        if reg.list_pending("conv-1"):
            break
        await asyncio.sleep(0)

    file_grant = asyncio.create_task(
        _resolve_when_ready(reg, "write-1", ApprovalDecision.APPROVE_ALWAYS_FILES)
    )
    assert (
        await gate.authorize(
            tool_name="file_write",
            tool_call_id="write-1",
            arguments={"path": "a.txt", "content": "x"},
        )
        is ApprovalDecision.APPROVE_ALWAYS_FILES
    )
    await file_grant

    # 删除卡还挂在那儿等自己的决定。
    assert [r.id for r in reg.list_pending("conv-1")] == ["del-pending"]
    assert not pending.done()
    reg.resolve("del-pending", ApprovalDecision.DENY, conversation_id="conv-1")
    assert await pending is ApprovalDecision.DENY


# --- 审批卡实名 ---------------------------------------------------------------


async def test_approval_card_carries_folder_path(monkeypatch: pytest.MonkeyPatch):
    """卡上必须有完整路径——来自服务端名册，不是模型自报，也不是分不清层级的末段名。"""
    _patch_repo(
        monkeypatch,
        {ALPHA_ID: _FakeFolder(id=ALPHA_ID, name="图标", rel_path="设计/图标")},
    )

    enriched = await enrich_approval_preview(
        tool_name="delete_folder",
        arguments={"folder_id": ALPHA_ID},
        user_id="u1",
    )
    assert enriched == {"folder_id": ALPHA_ID, "folder_name": "设计/图标"}


async def test_approval_card_enrichment_fails_soft(monkeypatch: pytest.MonkeyPatch):
    import agentcore.tools.builtin.folders as folders_mod

    class _CM:
        async def __aenter__(self) -> object:
            raise RuntimeError("roster down")

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(folders_mod, "async_session_factory", lambda: _CM())

    args = {"folder_id": ALPHA_ID}
    assert await enrich_approval_preview(
        tool_name="delete_folder", arguments=args, user_id="u1"
    ) == args


async def test_approval_card_enrichment_is_scoped_to_delete_folder():
    args = {"path": "a.txt"}
    assert await enrich_approval_preview(
        tool_name="file_write", arguments=args, user_id="u1"
    ) is args


async def test_delete_card_payload_reaches_the_gate(monkeypatch: pytest.MonkeyPatch):
    """端到端：门弹出的卡里带 folder_name（完整路径）。"""
    _patch_repo(
        monkeypatch,
        {ALPHA_ID: _FakeFolder(id=ALPHA_ID, name="图标", rel_path="设计/图标")},
    )

    reg = InteractionRegistry()
    sink = EventSink()
    gate = _ceo_gate(sink, reg)

    async def _deny_when_ready() -> None:
        for _ in range(2000):
            pending = [
                r for r in reg.list_pending("conv-1") if r.kind is InteractionKind.APPROVAL
            ]
            if pending:
                assert pending[0].payload["arguments"]["folder_name"] == "设计/图标"
                reg.resolve(
                    pending[0].id, ApprovalDecision.DENY, conversation_id="conv-1"
                )
                return
            await asyncio.sleep(0)
        raise AssertionError("approval never became pending")

    resolver = asyncio.create_task(_deny_when_ready())
    await _run_delete_gates(gate, sink, folder_id=ALPHA_ID, tool_call_id="del-card")
    await resolver

    card = next(e for e in _drain(sink) if e.type is EventType.APPROVAL_REQUIRED)
    assert card.payload["arguments"]["folder_name"] == "设计/图标"
    assert card.payload["arguments"]["folder_id"] == ALPHA_ID
