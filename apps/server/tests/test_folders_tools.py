"""Tests for CEO list_folders / resolve_folder / create_folder.

P0 桶 A：列名册与按路径解析（嵌套后同名末段合法，故 resolve 走路径）。
P1 桶 C：云 create（同指挥面；不碰会话归属；可挂到某一层）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.tools.builtin.folders import (
    CreateFolderTool,
    ListFoldersTool,
    ResolveFolderTool,
    resolve_folders_by_path,
)
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

_FOLDER_HOW_CONSULT = "HOW→consult(team_cross_folder)"
# 跨文件夹百科（先建后派、开发双仓、禁猜最近）钉在 skill；本机进桌「导入到云」HOW 在 team_delivery_env。
# 存在性见 test_skills.test_team_cross_folder_skill_teaches_parallel_command。
_SCHEMA_ENCYCLOPEDIA_FORBIDDEN = (
    "先建后派",
    "导入到云",
    "开发双仓",
    "静默猜",
    "Composer",
)


def _assert_short_trigger(description: str) -> None:
    assert _FOLDER_HOW_CONSULT in description
    for phrase in _SCHEMA_ENCYCLOPEDIA_FORBIDDEN:
        assert phrase not in description, f"schema 勿抄 skill 百科：{phrase}"


def _ctx(user_id: str = "u1", *, conversation_id: str = "") -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id=user_id,
        conversation_id=conversation_id,
    )


def _summary(
    *,
    id: str,
    name: str,
    mode: str = "cloud",
    local_root_id: str | None = None,
    local_subpath: str | None = None,
    rel_path: str | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "name": name,
        "mode": mode,
        "local_root_id": local_root_id,
        "local_subpath": local_subpath,
        "rel_path": rel_path if rel_path is not None else name,
        "parent_rel_path": None,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-02T00:00:00",
    }


# --- pure resolve -----------------------------------------------------------


def test_resolve_unique_exact_is_silent_hit():
    rows = [
        _summary(id="a", name="Alpha"),
        _summary(id="b", name="Beta", mode="local", local_root_id="root-1"),
    ]
    out = resolve_folders_by_path(rows, "alpha")
    assert out.status == "resolved"
    assert len(out.matches) == 1
    assert out.matches[0]["id"] == "a"


def test_resolve_zero_hits():
    rows = [_summary(id="a", name="Alpha")]
    out = resolve_folders_by_path(rows, "Gamma")
    assert out.status == "not_found"
    assert out.matches == ()


def test_resolve_multiple_exact_is_ambiguous_not_recent():
    # Same last segment at two levels is legal — must not silently pick either.
    rows = [
        _summary(id="old", name="图标", rel_path="设计/图标"),
        _summary(id="new", name="图标", rel_path="归档/图标"),
    ]
    out = resolve_folders_by_path(rows, "图标")
    assert out.status == "ambiguous"
    assert {m["id"] for m in out.matches} == {"old", "new"}


def test_resolve_full_path_disambiguates_same_last_segment():
    rows = [
        _summary(id="design", name="图标", rel_path="设计/图标"),
        _summary(id="archive", name="图标", rel_path="归档/图标"),
    ]
    out = resolve_folders_by_path(rows, "设计/图标")
    assert out.status == "resolved"
    assert out.matches[0]["id"] == "design"


def test_resolve_path_suffix_hits_deeper_folder():
    rows = [
        _summary(id="deep", name="图标", rel_path="工作/设计/图标"),
        _summary(id="other", name="文档", rel_path="工作/文档"),
    ]
    out = resolve_folders_by_path(rows, "设计/图标")
    assert out.status == "resolved"
    assert out.matches[0]["id"] == "deep"


def test_resolve_exact_path_beats_suffix_of_a_deeper_one():
    """Top-level `图标` must win over `设计/图标` when the query is the bare top path."""
    rows = [
        _summary(id="top", name="图标", rel_path="图标"),
        _summary(id="nested", name="图标", rel_path="设计/图标"),
    ]
    out = resolve_folders_by_path(rows, "图标")
    assert out.status == "resolved"
    assert out.matches[0]["id"] == "top"


def test_resolve_suffix_is_segment_wise_not_substring():
    """`报告` must not match `报告备份`; segment boundaries are respected."""
    rows = [_summary(id="a", name="报告备份", rel_path="归档/报告备份")]
    out = resolve_folders_by_path(rows, "归档/报告")
    assert out.status == "not_found"


def test_resolve_unique_substring_hit():
    rows = [
        _summary(id="1", name="AgentCore"),
        _summary(id="2", name="Other"),
    ]
    out = resolve_folders_by_path(rows, "agent")
    assert out.status == "resolved"
    assert out.matches[0]["id"] == "1"


def test_resolve_substring_only_applies_to_single_segment_query():
    rows = [_summary(id="1", name="AgentCore", rel_path="工作/AgentCore")]
    assert resolve_folders_by_path(rows, "工作/agent").status == "not_found"


def test_resolve_ambiguous_substring():
    rows = [
        _summary(id="1", name="Shop Frontend"),
        _summary(id="2", name="Shop Backend", mode="local", local_root_id="r"),
    ]
    out = resolve_folders_by_path(rows, "Shop")
    assert out.status == "ambiguous"
    assert len(out.matches) == 2


def test_resolve_local_folder_without_rel_path_falls_back_to_name():
    rows = [
        _summary(
            id="l",
            name="Legacy",
            mode="local",
            local_root_id="root-1",
            rel_path=None,
        )
    ]
    out = resolve_folders_by_path(rows, "legacy")
    assert out.status == "resolved"
    assert out.matches[0]["id"] == "l"


def test_resolve_blank_path_is_not_found():
    assert resolve_folders_by_path([_summary(id="a", name="A")], "  ").status == "not_found"


# --- schema / registration --------------------------------------------------


def test_list_folders_schema_and_registration():
    tool = ListFoldersTool()
    assert tool.schema.name == "list_folders"
    assert tool.schema.category is ToolCategory.ORCHESTRATION
    assert tool.schema.approval is ToolApproval.NEVER
    desc = tool.schema.description
    _assert_short_trigger(desc)
    assert "rel_path" in desc
    assert "resolve_folder" in desc
    assert "file_list" in desc
    assert "名册" in desc
    assert "不常驻" in desc
    assert "跨桌" in desc
    assert "先列" in desc
    assert "清单已有" not in desc
    reg = tool_registration(ListFoldersTool)
    assert reg.surface is ToolSurface.CEO_ORCHESTRATION
    assert reg.audience == (AUDIENCE_CEO,)
    assert reg.ceo_wire is CeoWire.ALWAYS


def test_resolve_folder_schema_and_registration():
    tool = ResolveFolderTool()
    assert tool.schema.name == "resolve_folder"
    props = tool.schema.parameters["properties"]
    # Path-addressed, not name-addressed: nesting makes bare names ambiguous.
    assert "path" in props
    assert "name" not in props
    assert tool.schema.parameters["required"] == ["path"]
    assert tool.schema.approval is ToolApproval.NEVER
    desc = tool.schema.description
    _assert_short_trigger(desc)
    assert "路径" in desc and "id" in desc
    assert "完整路径" in desc
    # 匹配顺序是 path 取值语义，不堆工具 description。
    assert "路径后缀" not in desc
    assert "子串" not in desc
    path_desc = props["path"]["description"]
    assert "精确" in path_desc
    assert "后缀" in path_desc
    assert "子串" in path_desc
    reg = tool_registration(ResolveFolderTool)
    assert reg.surface is ToolSurface.CEO_ORCHESTRATION
    assert AUDIENCE_CEO in reg.audience
    assert reg.audience[0] == AUDIENCE_CEO
    assert len(reg.audience) == 1
    assert reg.ceo_wire is CeoWire.ALWAYS


def test_create_folder_schema_and_registration():
    tool = CreateFolderTool()
    assert tool.schema.name == "create_folder"
    props = tool.schema.parameters["properties"]
    assert "name" in props
    # Nesting: the model must be able to say WHERE the new folder hangs.
    assert "parent_path" in props
    assert tool.schema.parameters["required"] == ["name"]
    assert tool.schema.category is ToolCategory.ORCHESTRATION
    assert tool.schema.approval is ToolApproval.NEVER
    # Cloud-only surface: no local_root_id / mode param (local = 桶 D).
    assert "local_root_id" not in props
    assert "mode" not in props
    desc = tool.schema.description
    _assert_short_trigger(desc)
    assert "mode=cloud" in desc
    assert "open_local_project" in desc
    # Must not read as "make a subdirectory" — that is mkdir.
    assert "mkdir" in desc
    assert "用户明确" in desc or "明确要求" in desc
    # 过闸/裸聊写盘禁令在 team_cross_folder skill，schema 不复述。
    assert "禁止为过写盘闸" not in desc
    assert "自动建云文件夹" not in desc
    parent_desc = props["parent_path"]["description"]
    assert "resolve_folder" in parent_desc
    assert "顶层" in parent_desc
    reg = tool_registration(CreateFolderTool)
    assert reg.surface is ToolSurface.CEO_ORCHESTRATION
    assert reg.audience == (AUDIENCE_CEO,)
    assert reg.ceo_wire is CeoWire.ALWAYS


def test_declared_roster_includes_folder_tools():
    names = {declared_tool_name(cls) for cls in declared_tools()}
    assert "list_folders" in names
    assert "resolve_folder" in names
    assert "create_folder" in names


# --- execute (repo mocked) --------------------------------------------------


class _FakeFolder:
    def __init__(
        self,
        *,
        id: str,
        name: str,
        local_root_id: str | None = None,
        local_subpath: str | None = None,
        rel_path: str | None = None,
        user_id: str = "u1",
    ) -> None:
        self.id = id
        self.name = name
        self.user_id = user_id
        self.local_root_id = local_root_id
        self.local_subpath = local_subpath
        self.rel_path = (rel_path or name) if local_root_id is None else None
        self.created_at = datetime(2026, 1, 1)
        self.updated_at = datetime(2026, 1, 2)


def _patch_list(monkeypatch: pytest.MonkeyPatch, folders: list[_FakeFolder]) -> None:
    import agentcore.tools.builtin.folders as folders_mod

    class _Repo:
        def __init__(self, session: Any) -> None:
            del session

        async def list_by_user(self, user_id: str) -> list[_FakeFolder]:
            del user_id
            return folders

    class _CM:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(folders_mod, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(folders_mod, "FolderRepository", _Repo)


def _patch_create(
    monkeypatch: pytest.MonkeyPatch,
    *,
    created: list[dict[str, Any]] | None = None,
    existing: list[_FakeFolder] | None = None,
) -> list[dict[str, Any]]:
    """Stub FolderRepository create/list; return the create call log for assertions."""
    import agentcore.tools.builtin.folders as folders_mod

    calls = created if created is not None else []
    roster = existing or []

    class _Repo:
        def __init__(self, session: Any) -> None:
            del session

        async def list_by_user(self, user_id: str) -> list[_FakeFolder]:
            del user_id
            return roster

        async def create(
            self,
            *,
            user_id: str,
            name: str,
            local_root_id: str | None = None,
            local_subpath: str | None = None,
            parent_rel_path: str | None = None,
        ) -> _FakeFolder:
            calls.append(
                {
                    "user_id": user_id,
                    "name": name,
                    "local_root_id": local_root_id,
                    "local_subpath": local_subpath,
                    "parent_rel_path": parent_rel_path,
                }
            )
            rel = f"{parent_rel_path}/{name}" if parent_rel_path else name
            return _FakeFolder(id="new-cloud-1", name=name, rel_path=rel)

    class _CM:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(folders_mod, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(folders_mod, "FolderRepository", _Repo)
    return calls


async def test_list_folders_returns_folder_summary_shape(monkeypatch: pytest.MonkeyPatch):
    _patch_list(
        monkeypatch,
        [
            _FakeFolder(id="c1", name="Cloud App"),
            _FakeFolder(id="c2", name="图标", rel_path="设计/图标"),
            _FakeFolder(
                id="l1",
                name="Local App",
                local_root_id="root-x",
                local_subpath="repos/app",
            ),
        ],
    )
    result = await ListFoldersTool().execute({}, _ctx())
    assert result.success
    assert result.display == {"count": 3}
    # Payload after the human lead-in
    payload = json.loads(result.output.split("\n", 1)[1])
    assert payload["count"] == 3
    assert {f["id"] for f in payload["folders"]} == {"c1", "c2", "l1"}
    cloud = next(f for f in payload["folders"] if f["id"] == "c1")
    nested = next(f for f in payload["folders"] if f["id"] == "c2")
    local = next(f for f in payload["folders"] if f["id"] == "l1")
    assert cloud["mode"] == "cloud"
    assert cloud["local_root_id"] is None
    # Nesting is visible in the roster, both as full path and as parent prefix.
    assert nested["rel_path"] == "设计/图标"
    assert nested["parent_rel_path"] == "设计"
    assert local["mode"] == "local"
    assert local["local_root_id"] == "root-x"
    assert local["local_subpath"] == "repos/app"
    # No OS absolute path field
    for f in payload["folders"]:
        assert "path" not in f
        assert "local_dir" not in f


async def test_list_folders_empty(monkeypatch: pytest.MonkeyPatch):
    _patch_list(monkeypatch, [])
    result = await ListFoldersTool().execute({}, _ctx())
    assert result.success
    assert result.display == {"count": 0}
    assert "还没有文件夹" in result.output
    assert "create_folder" in result.output
    # Empty roster: create only for explicit new / multi-line; not write-gate default.
    assert "自动建云文件夹" in result.output
    assert "过写盘闸" in result.output or "勿" in result.output
    # Empty roster must not default-nudge open_local_project as the create path.
    assert "勿默认催 open_local_project" in result.output or "导入到云" in result.output
    assert _FOLDER_HOW_CONSULT in result.output
    assert "开发双仓" not in result.output


async def test_resolve_unique(monkeypatch: pytest.MonkeyPatch):
    _patch_list(
        monkeypatch,
        [
            _FakeFolder(id="only", name="Solo"),
            _FakeFolder(id="other", name="Other"),
        ],
    )
    result = await ResolveFolderTool().execute({"path": "solo"}, _ctx())
    assert result.success
    assert result.display["status"] == "resolved"
    assert result.display["folder_id"] == "only"
    assert result.display["rel_path"] == "Solo"
    assert "唯一命中" in result.output
    # Tip encourages early ask on empty/near-empty; not a hard ask_user gate.
    assert "file_list" in result.output
    assert _FOLDER_HOW_CONSULT in result.output
    assert "开发双仓" not in result.output


async def test_resolve_nested_path(monkeypatch: pytest.MonkeyPatch):
    _patch_list(
        monkeypatch,
        [
            _FakeFolder(id="design", name="图标", rel_path="设计/图标"),
            _FakeFolder(id="archive", name="图标", rel_path="归档/图标"),
        ],
    )
    result = await ResolveFolderTool().execute({"path": "归档/图标"}, _ctx())
    assert result.success
    assert result.display["status"] == "resolved"
    assert result.display["folder_id"] == "archive"
    assert result.display["rel_path"] == "归档/图标"


async def test_resolve_zero(monkeypatch: pytest.MonkeyPatch):
    _patch_list(monkeypatch, [_FakeFolder(id="a", name="Alpha")])
    result = await ResolveFolderTool().execute({"path": "Missing"}, _ctx())
    assert result.success
    assert result.display["status"] == "not_found"
    assert "ask_user" in result.output or "list_folders" in result.output
    assert "create_folder" in result.output  # mention only as explicit-new path
    assert "自动建云文件夹" in result.output
    assert "禁止静默猜" in result.output
    # Nested rosters: tell the model to check the level before giving up.
    assert "层级" in result.output
    # Must not default-urge open_local_project as the create path (§4.9 ③A).
    assert "新建本机项目才用 open_local_project" not in result.output
    assert "open_local_project" in result.output or "导入到云" in result.output
    assert "合法非默认" in result.output or "非默认" in result.output
    assert "本机传统" in result.output or "导入到云" in result.output


async def test_resolve_ambiguous(monkeypatch: pytest.MonkeyPatch):
    _patch_list(
        monkeypatch,
        [
            _FakeFolder(id="c", name="Shop", local_root_id=None),
            _FakeFolder(
                id="l",
                name="Shop",
                local_root_id="root-1",
                local_subpath=None,
            ),
        ],
    )
    result = await ResolveFolderTool().execute({"path": "Shop"}, _ctx())
    assert result.success
    assert result.display["status"] == "ambiguous"
    assert result.display["match_count"] == 2
    assert "ask_user" in result.output
    assert "kind=choice" in result.output
    assert "禁止静默猜" in result.output
    # Ambiguity is only actionable when the候选 carry their full path.
    assert "完整路径" in result.output
    payload = json.loads(result.output.split("\n", 1)[1])
    modes = {m["id"]: m["mode"] for m in payload["matches"]}
    assert modes == {"c": "cloud", "l": "local"}


async def test_resolve_missing_path_arg():
    result = await ResolveFolderTool().execute({}, _ctx())
    assert not result.success
    assert result.error == "missing path"


def _patch_list_raises(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    """Stub session so list/resolve hit a DB connectivity failure."""
    import agentcore.tools.builtin.folders as folders_mod

    class _CM:
        async def __aenter__(self) -> object:
            raise exc

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(folders_mod, "async_session_factory", lambda: _CM())


async def test_list_folders_db_unreachable_honest_message(
    monkeypatch: pytest.MonkeyPatch,
):
    """PG down → clear service-unavailable copy; no WinError 1225 as primary narrative."""
    from sqlalchemy.exc import OperationalError

    from agentcore.db.errors import DATABASE_UNAVAILABLE_CODE, DATABASE_UNAVAILABLE_MESSAGE

    cause = OSError(1225, "远程计算机拒绝网络连接")
    cause.winerror = 1225  # type: ignore[attr-defined]
    err = OperationalError("SELECT 1", {}, cause)
    err.__cause__ = cause
    _patch_list_raises(monkeypatch, err)

    result = await ListFoldersTool().execute({}, _ctx())
    assert not result.success
    assert result.error == DATABASE_UNAVAILABLE_CODE
    assert DATABASE_UNAVAILABLE_MESSAGE in result.output
    assert "请确认数据库" not in result.output
    assert "WinError" not in result.output
    assert "1225" not in result.output
    assert "WinError" not in (result.error or "")


async def test_resolve_folder_db_unreachable_honest_message(
    monkeypatch: pytest.MonkeyPatch,
):
    from sqlalchemy.exc import OperationalError

    from agentcore.db.errors import DATABASE_UNAVAILABLE_CODE, DATABASE_UNAVAILABLE_MESSAGE

    err = OperationalError("SELECT 1", {}, ConnectionRefusedError("refused"))
    _patch_list_raises(monkeypatch, err)

    result = await ResolveFolderTool().execute({"path": "Alpha"}, _ctx())
    assert not result.success
    assert result.error == DATABASE_UNAVAILABLE_CODE
    assert DATABASE_UNAVAILABLE_MESSAGE in result.output
    assert "WinError" not in result.output
    # Business not_found must NOT be substituted when the roster never loaded.
    assert '"status": "not_found"' not in result.output


async def test_list_folders_non_db_failure_keeps_generic_path(
    monkeypatch: pytest.MonkeyPatch,
):
    """Non-connectivity faults keep prior soft-fail semantics (not database_unavailable)."""
    _patch_list_raises(monkeypatch, RuntimeError("unexpected boom"))
    result = await ListFoldersTool().execute({}, _ctx())
    assert not result.success
    assert result.error == "unexpected boom"
    assert "数据库不可用" not in result.output


# --- create_folder (P1 桶 C) --------------------------------------------------


async def test_create_folder_cloud_success(monkeypatch: pytest.MonkeyPatch):
    calls = _patch_create(monkeypatch)
    ctx = _ctx(user_id="owner-1", conversation_id="conv-stay")
    result = await CreateFolderTool().execute(
        {"name": "  New Cloud App  "},
        ctx,
    )
    assert result.success
    assert result.display["status"] == "created"
    assert result.display["folder_id"] == "new-cloud-1"
    assert result.display["name"] == "New Cloud App"
    assert result.display["mode"] == "cloud"
    assert result.display["conversation_untouched"] is True
    assert calls == [
        {
            "user_id": "owner-1",
            "name": "New Cloud App",
            "local_root_id": None,
            "local_subpath": None,
            "parent_rel_path": None,
        }
    ]
    # FolderSummary-shaped folder in payload
    payload = json.loads(result.output.split("\n", 1)[1])
    assert payload["status"] == "created"
    assert payload["conversation_untouched"] is True
    folder = payload["folder"]
    assert folder["id"] == "new-cloud-1"
    assert folder["mode"] == "cloud"
    assert folder["local_root_id"] is None
    assert "path" not in folder
    assert "未改会话" in result.output or "conversation_untouched" in result.output
    assert "运行时继承" in result.output or "省略" in result.output
    assert ctx.turn_target_desk.folder_id == "new-cloud-1"
    assert "new-cloud-1" in ctx.turn_created_folder_ids


async def test_create_folder_nested_under_parent_path(monkeypatch: pytest.MonkeyPatch):
    calls = _patch_create(
        monkeypatch,
        existing=[_FakeFolder(id="p1", name="设计", rel_path="工作/设计")],
    )
    result = await CreateFolderTool().execute(
        {"name": "图标", "parent_path": "工作/设计"},
        _ctx(user_id="owner-1"),
    )
    assert result.success
    assert calls[0]["parent_rel_path"] == "工作/设计"
    assert result.display["rel_path"] == "工作/设计/图标"
    assert "工作/设计/图标" in result.output


async def test_create_folder_parent_not_found_creates_nothing(
    monkeypatch: pytest.MonkeyPatch,
):
    calls = _patch_create(monkeypatch, existing=[_FakeFolder(id="p1", name="设计")])
    result = await CreateFolderTool().execute(
        {"name": "图标", "parent_path": "不存在的层"},
        _ctx(),
    )
    assert not result.success
    assert result.error == "parent_not_found"
    assert calls == []
    assert "没有创建任何东西" in result.output


async def test_create_folder_parent_ambiguous_creates_nothing(
    monkeypatch: pytest.MonkeyPatch,
):
    calls = _patch_create(
        monkeypatch,
        existing=[
            _FakeFolder(id="a", name="图标", rel_path="设计/图标"),
            _FakeFolder(id="b", name="图标", rel_path="归档/图标"),
        ],
    )
    result = await CreateFolderTool().execute(
        {"name": "线稿", "parent_path": "图标"},
        _ctx(),
    )
    assert not result.success
    assert result.error == "parent_ambiguous"
    assert calls == []
    assert "没有创建任何东西" in result.output
    assert "设计/图标" in result.output and "归档/图标" in result.output


async def test_create_folder_does_not_touch_conversation(monkeypatch: pytest.MonkeyPatch):
    """Invariant: create is account Folder only — never rebinds conversation.folder_id."""
    import agentcore.tools.builtin.folders as folders_mod

    _patch_create(monkeypatch)
    # If create ever starts mutating conversations, these would be imported/called.
    assert not hasattr(folders_mod, "ConversationRepository")
    assert "ConversationRepository" not in folders_mod.__dict__

    conversation_mutations: list[str] = []

    def _forbid_conversation_touch(*_a: Any, **_k: Any) -> None:
        conversation_mutations.append("touched")
        raise AssertionError("create_folder must not touch conversations")

    # Belt: even if someone later imports Conversation models into this module,
    # a stray setattr on conversation.folder_id should fail the test loudly.
    monkeypatch.setattr(
        folders_mod,
        "ConversationRepository",
        type(
            "ForbiddenConversationRepo",
            (),
            {
                "__init__": lambda self, *a, **k: _forbid_conversation_touch(),
                "update": staticmethod(_forbid_conversation_touch),
            },
        ),
        raising=False,
    )

    result = await CreateFolderTool().execute(
        {"name": "Stay Put"},
        _ctx(conversation_id="conv-must-not-rebind"),
    )
    assert result.success
    assert conversation_mutations == []
    assert result.display.get("conversation_untouched") is True


async def test_create_folder_missing_name():
    result = await CreateFolderTool().execute({}, _ctx())
    assert not result.success
    assert result.error == "missing name"
