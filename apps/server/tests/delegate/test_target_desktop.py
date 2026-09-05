"""P0 桶 B: shape-甲 target desk + 2b bare-chat gate + nest inheritance."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcore.core.types import ToolCategory
from agentcore.runtime.delegate.target_desktop import (
    NO_TARGET_SCRATCH_GATE_MSG,
    LocalRootClaimBook,
    TargetDesktopError,
    apply_target_desktop,
    effective_target_folder_id,
    format_bare_chat_no_target_error,
    gate_bare_chat_requires_target,
    load_target_folder_binding,
    resolve_bare_chat_write_scope,
    task_structurally_requires_write_desk,
)
from agentcore.runtime.delegate.target_desktop_auto_cloud import auto_cloud_desk_name
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.workspace.locate import LocalBinding


class _RecordingSink:
    """Collect emitted events so 裸聊落点告知 can be asserted without a real bus."""

    def __init__(self) -> None:
        self.events: list[object] = []

    def emit(self, event: object) -> None:
        self.events.append(event)


def _auto_folder_notices(sink: _RecordingSink) -> list[dict]:
    return [
        e.payload  # type: ignore[attr-defined]
        for e in sink.events
        if getattr(e, "type", None) is not None
        and getattr(e.type, "value", None) == "auto_folder_created"  # type: ignore[attr-defined]
    ]


def test_effective_target_folder_id_prefers_explicit():
    assert effective_target_folder_id("  f1  ", default="f0") == "f1"
    assert effective_target_folder_id("", default="f0") == "f0"
    assert effective_target_folder_id(None, default=None) is None
    assert effective_target_folder_id("  ", default="  ") is None


def test_task_structurally_requires_write_desk():
    assert task_structurally_requires_write_desk({"task": "打招呼"}) is True
    assert task_structurally_requires_write_desk(
        {"deliverable": {"form": "prose"}}
    ) is False
    assert task_structurally_requires_write_desk({"deliverable": {}}) is True
    assert task_structurally_requires_write_desk(
        {"deliverable": {"form": "files"}}
    ) is True
    assert task_structurally_requires_write_desk(
        {"deliverable": {"form": "workspace"}}
    ) is True
    assert task_structurally_requires_write_desk(
        {"deliverable": {"requires_files": True}}
    ) is True
    assert task_structurally_requires_write_desk(
        {"deliverable": {"requires_files": False}}
    ) is True
    assert task_structurally_requires_write_desk(
        {"deliverable": {"artifacts": ["a.py"]}}
    ) is True
    assert task_structurally_requires_write_desk(
        {"deliverable": {"artifacts": ["  ", ""]}}
    ) is True
    assert task_structurally_requires_write_desk(
        {"deliverable": {"artifacts": []}}
    ) is True


def test_resolve_bare_chat_write_scope():
    assert (
        resolve_bare_chat_write_scope(
            target_folder_id=None,
            session_folder_id=None,
            base_write_scope="project",
        )
        == "none"
    )
    assert (
        resolve_bare_chat_write_scope(
            target_folder_id=None,
            session_folder_id=None,
            base_write_scope="explore_memory",
        )
        == "explore_memory"
    )
    assert (
        resolve_bare_chat_write_scope(
            target_folder_id="t",
            session_folder_id=None,
            base_write_scope="project",
        )
        == "project"
    )
    assert (
        resolve_bare_chat_write_scope(
            target_folder_id=None,
            session_folder_id="birth",
            base_write_scope="project",
        )
        == "project"
    )
    assert (
        resolve_bare_chat_write_scope(
            target_folder_id="new-desk",
            session_folder_id="birth",
            base_write_scope="explore_memory",
            turn_created_folder_ids={"new-desk"},
        )
        == "project"
    )
    assert (
        resolve_bare_chat_write_scope(
            target_folder_id="birth",
            session_folder_id="birth",
            base_write_scope="explore_memory",
            turn_created_folder_ids={"new-desk"},
        )
        == "explore_memory"
    )
    assert (
        resolve_bare_chat_write_scope(
            target_folder_id="new-desk",
            session_folder_id=None,
            base_write_scope="explore_memory",
            turn_created_folder_ids={"new-desk"},
        )
        == "project"
    )


def test_gate_bare_chat_blocks_write_deliverable_without_target():
    """纯闸：无目标 + form=files → 拒（ensure 未跑时的残余拒文案）。"""
    msg = gate_bare_chat_requires_target(
        session_folder_id=None,
        tasks_raw=[
            {
                "role": "工",
                "task": "写文件勿泄露正文",
                "deliverable": {"form": "files"},
            }
        ],
    )
    assert msg is not None
    assert msg.startswith(NO_TARGET_SCRATCH_GATE_MSG)
    assert "写盘任务必须点名" in msg
    assert "纯对话/只读可不点名" in msg
    assert "create" not in msg.lower()
    assert "ask_user" not in msg
    assert "缺目标任务：" in msg
    assert "role=工" in msg
    assert "缺 target_folder_id" in msg
    assert "写文件勿泄露正文" not in msg


def test_gate_bare_chat_allows_prose_only():
    """仅显式 form=prose 免写桌；漏填 / 无 deliverable 须写桌。"""
    assert (
        gate_bare_chat_requires_target(
            session_folder_id=None,
            tasks_raw=[{"role": "客服", "task": "打招呼"}],
        )
        is not None
    )
    assert (
        gate_bare_chat_requires_target(
            session_folder_id=None,
            tasks_raw=[
                {
                    "role": "写手",
                    "task": "写段说明",
                    "deliverable": {"form": "prose"},
                }
            ],
        )
        is None
    )


def test_gate_bare_chat_lists_all_missing_write_targets():
    """部分缺 target 的写盘 task → 整批拒，回执只点名写盘缺项。"""
    msg = gate_bare_chat_requires_target(
        session_folder_id=None,
        tasks_raw=[
            {
                "role": "甲",
                "task": "有目标正文勿泄露",
                "target_folder_id": "proj_a",
                "deliverable": {"form": "files"},
            },
            {
                "id": "n2",
                "role": "乙",
                "task": "缺目标的长任务说明不应出现",
                "deliverable": {"form": "files"},
            },
            {
                "role": "丙",
                "task": "也缺写盘",
                "deliverable": {"form": "files"},
            },
            {"role": "丁", "task": "纯对话不进拒名单", "deliverable": {"form": "prose"}},
        ],
    )
    assert msg is not None
    assert msg.startswith(NO_TARGET_SCRATCH_GATE_MSG)
    assert "role=乙" in msg and "id=n2" in msg
    assert "role=丙" in msg
    assert "role=甲" not in msg  # 有 target 的不进骨架
    assert "role=丁" not in msg  # 无写盘 deliverable 不进骨架
    assert "有目标正文勿泄露" not in msg
    assert "缺目标的长任务说明不应出现" not in msg
    assert "也缺写盘" not in msg
    assert "纯对话不进拒名单" not in msg
    # 同源组装函数契约
    assert msg == format_bare_chat_no_target_error(
        [
            {
                "id": "n2",
                "role": "乙",
                "task": "缺目标的长任务说明不应出现",
                "deliverable": {"form": "files"},
            },
            {
                "role": "丙",
                "task": "也缺写盘",
                "deliverable": {"form": "files"},
            },
        ]
    )


def test_gate_bare_chat_allows_with_target():
    assert (
        gate_bare_chat_requires_target(
            session_folder_id=None,
            tasks_raw=[
                {
                    "role": "工",
                    "task": "写",
                    "target_folder_id": "proj_a",
                    "deliverable": {"form": "files"},
                }
            ],
        )
        is None
    )


def test_gate_bare_chat_allows_when_all_have_target():
    assert (
        gate_bare_chat_requires_target(
            session_folder_id=None,
            tasks_raw=[
                {"role": "甲", "task": "a", "target_folder_id": "p1"},
                {"role": "乙", "task": "b", "target_folder_id": "p2"},
            ],
        )
        is None
    )


def test_gate_birth_allows_omit_target():
    assert (
        gate_bare_chat_requires_target(
            session_folder_id="birth",
            tasks_raw=[
                {"role": "工", "task": "写", "deliverable": {"form": "files"}}
            ],
        )
        is None
    )


def test_gate_bare_inherits_default_target():
    assert (
        gate_bare_chat_requires_target(
            session_folder_id=None,
            tasks_raw=[
                {"role": "子", "task": "续", "deliverable": {"form": "files"}}
            ],
            default_target_folder_id="parent_desk",
        )
        is None
    )


def test_turn_target_desk_hint_single_then_clear():
    from agentcore.tools.protocol import TurnTargetDeskHint

    hint = TurnTargetDeskHint()
    hint.note_folder("  a  ")
    assert hint.folder_id == "a"
    hint.note_folder("a")
    assert hint.folder_id == "a"
    hint.note_folder("b")
    assert hint.folder_id is None


def test_build_run_plan_stamps_target_folder_id():
    plan, errors = build_run_plan(
        [
            {
                "role": "甲",
                "task": "在 A 写",
                "target_folder_id": "folder_a",
            },
            {"role": "乙", "task": "默认桌"},
        ]
    )
    assert errors == []
    by_role = {n.role: n for n in plan.nodes}
    assert by_role["甲"].target_folder_id == "folder_a"
    assert by_role["乙"].target_folder_id is None


def test_build_run_plan_inherits_default_target():
    plan, errors = build_run_plan(
        [{"role": "子", "task": "继承"}],
        default_target_folder_id="parent_x",
    )
    assert errors == []
    assert plan.nodes[0].target_folder_id == "parent_x"


def test_build_run_plan_explicit_overrides_default():
    plan, errors = build_run_plan(
        [{"role": "子", "task": "换桌", "target_folder_id": "other"}],
        default_target_folder_id="parent_x",
    )
    assert errors == []
    assert plan.nodes[0].target_folder_id == "other"


@pytest.mark.asyncio
async def test_local_root_claim_book_allows_second_root():
    """C0：不同 local root 同回合均可认领（不再拒第二根）。"""
    book = LocalRootClaimBook()
    assert await book.try_claim("root_a") is True
    assert await book.try_claim("root_a") is True
    assert await book.try_claim("root_b") is True


@pytest.mark.asyncio
async def test_apply_target_desktop_same_as_session_is_noop():
    backend = SimpleNamespace(location="server")
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=backend,  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c1",
    )
    tools = ToolRegistry()
    applied = await apply_target_desktop(
        target_folder_id="same",
        session_folder_id="same",
        env_system_prompt="PROMPT",
        base_tool_context=ctx,
        worker_tools=tools,
        sink=MagicMock(),
        local_root_claims=None,
    )
    assert applied.system_prompt == "PROMPT"
    assert applied.tool_ctx is ctx
    assert applied.worker_tools is tools


@pytest.mark.asyncio
async def test_apply_target_desktop_switches_backend_and_memory():
    session_backend = SimpleNamespace(location="server", _channel=None)
    target_backend = SimpleNamespace(location="server", _channel=None)
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=session_backend,  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c1",
    )
    tools = ToolRegistry()
    binding = SimpleNamespace(
        folder_id="target_f",
        rel_path="target_f",
        name="目标项目",
        local_binding=None,
    )

    async def _fake_rebuild(**_kwargs):
        return "TARGET_PROMPT"

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
            target_folder_id="target_f",
            session_folder_id="birth_f",
            env_system_prompt="OLD",
            base_tool_context=ctx,
            worker_tools=tools,
            sink=MagicMock(),
            local_root_claims=LocalRootClaimBook(),
        )

    assert applied.system_prompt == "TARGET_PROMPT"
    assert applied.tool_ctx.backend is target_backend
    assert applied.tool_ctx.shared_workspace is True
    assert applied.target_folder_id == "target_f"


@pytest.mark.asyncio
async def test_apply_target_desktop_unknown_folder_errors():
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=SimpleNamespace(location="server"),  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c1",
    )
    with patch(
        "agentcore.runtime.delegate.target_desktop.load_target_folder_binding",
        new=AsyncMock(return_value=None),
    ), pytest.raises(TargetDesktopError, match="不存在或无权"):
        await apply_target_desktop(
            target_folder_id="missing",
            session_folder_id=None,
            env_system_prompt="P",
            base_tool_context=ctx,
            worker_tools=ToolRegistry(),
            sink=MagicMock(),
            local_root_claims=None,
        )


@pytest.mark.asyncio
async def test_load_target_folder_binding_db_unreachable_raises_structured(
    monkeypatch: pytest.MonkeyPatch,
):
    """PG down → TargetDesktopError with stable service-unavailable copy; never forge local_binding."""
    from sqlalchemy.exc import OperationalError

    from agentcore.db.errors import DATABASE_UNAVAILABLE_MESSAGE

    cause = OSError(1225, "远程计算机拒绝网络连接")
    cause.winerror = 1225  # type: ignore[attr-defined]
    err = OperationalError("SELECT 1", {}, cause)
    err.__cause__ = cause

    class _CM:
        async def __aenter__(self) -> object:
            raise err

        async def __aexit__(self, *args: object) -> None:
            return None

    import agentcore.db.base as db_base

    monkeypatch.setattr(db_base, "async_session_factory", lambda: _CM())

    with pytest.raises(TargetDesktopError, match="服务暂时不可用") as caught:
        await load_target_folder_binding(folder_id="any-folder", user_id="u1")

    msg = caught.value.message
    assert DATABASE_UNAVAILABLE_MESSAGE in msg
    assert "请确认数据库" not in msg
    assert "WinError" not in msg
    assert "1225" not in msg


@pytest.mark.asyncio
async def test_apply_target_desktop_db_unreachable_surfaces_structured_error(
    monkeypatch: pytest.MonkeyPatch,
):
    """delegate 换桌: connectivity failure is structured, not bare OS connection code."""
    from sqlalchemy.exc import OperationalError

    from agentcore.db.errors import DATABASE_UNAVAILABLE_MESSAGE

    err = OperationalError("SELECT 1", {}, ConnectionRefusedError("refused"))

    class _CM:
        async def __aenter__(self) -> object:
            raise err

        async def __aexit__(self, *args: object) -> None:
            return None

    import agentcore.db.base as db_base

    monkeypatch.setattr(db_base, "async_session_factory", lambda: _CM())

    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=SimpleNamespace(location="server"),  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c1",
    )
    with pytest.raises(TargetDesktopError) as caught:
        await apply_target_desktop(
            target_folder_id="cloud-or-local",
            session_folder_id="birth",
            env_system_prompt="P",
            base_tool_context=ctx,
            worker_tools=ToolRegistry(),
            sink=MagicMock(),
            local_root_claims=None,
        )

    assert DATABASE_UNAVAILABLE_MESSAGE in caught.value.message
    assert "不存在或无权" not in caught.value.message

@pytest.mark.asyncio
async def test_load_target_folder_binding_cloud_folder_has_no_local_binding(
    monkeypatch: pytest.MonkeyPatch,
):
    """Cloud row → local_binding is None (must not invent a local desk)."""
    folder = SimpleNamespace(
        id="cloud-1",
        name="Cloud Desk",
        local_root_id=None,
        local_subpath=None,
        rel_path="Cloud Desk",
    )

    class _Repo:
        def __init__(self, session: object) -> None:
            del session

        async def get_by_id(self, folder_id: str, *, user_id: str) -> object:
            assert folder_id == "cloud-1"
            assert user_id == "u1"
            return folder

    class _CM:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    import agentcore.db.base as db_base
    import agentcore.db.repositories as repos

    monkeypatch.setattr(db_base, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(repos, "FolderRepository", _Repo)

    binding = await load_target_folder_binding(folder_id="cloud-1", user_id="u1")
    assert binding is not None
    assert binding.folder_id == "cloud-1"
    assert binding.local_binding is None


@pytest.mark.asyncio
async def test_load_target_folder_binding_missing_stays_none(
    monkeypatch: pytest.MonkeyPatch,
):
    """Business miss stays None (apply_target_desktop → 不存在或无权), not DB copy."""

    class _Repo:
        def __init__(self, session: object) -> None:
            del session

        async def get_by_id(self, folder_id: str, *, user_id: str) -> None:
            del folder_id, user_id
            return None

    class _CM:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    import agentcore.db.base as db_base
    import agentcore.db.repositories as repos

    monkeypatch.setattr(db_base, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(repos, "FolderRepository", _Repo)

    assert await load_target_folder_binding(folder_id="missing", user_id="u1") is None


@pytest.mark.asyncio
async def test_apply_target_desktop_allows_second_local_root():
    """C0：会话已占一本地根时，异本地根 prepare 放行（ClaimBook 不拒）。"""
    session_backend = SimpleNamespace(
        location="local",
        _channel=SimpleNamespace(root_id="root_session"),
    )
    target_backend = SimpleNamespace(
        location="local",
        _channel=SimpleNamespace(root_id="root_other"),
    )
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=session_backend,  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c1",
    )
    claims = LocalRootClaimBook()
    await claims.seed_from_backend(session_backend)  # type: ignore[arg-type]
    binding = SimpleNamespace(
        folder_id="local_b",
        rel_path="local_b",
        name="本地B",
        local_binding=LocalBinding(root_id="root_other", root_label="B"),
    )

    async def _fake_rebuild(**_kwargs):
        return "LOCAL_B_PROMPT"

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
            target_folder_id="local_b",
            session_folder_id="birth",
            env_system_prompt="P",
            base_tool_context=ctx,
            worker_tools=ToolRegistry(),
            sink=MagicMock(),
            local_root_claims=claims,
        )

    assert applied.target_folder_id == "local_b"
    assert applied.tool_ctx.backend is target_backend
    assert applied.system_prompt == "LOCAL_B_PROMPT"
    assert await claims.try_claim("root_other") is True


@pytest.mark.asyncio
async def test_apply_target_desktop_mixed_local_and_cloud():
    """混部：本地根已登记时，cloud异桌仍放行。"""
    session_backend = SimpleNamespace(
        location="local",
        _channel=SimpleNamespace(root_id="root_session"),
    )
    cloud_backend = SimpleNamespace(location="server", _channel=None)
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=session_backend,  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c1",
    )
    claims = LocalRootClaimBook()
    await claims.seed_from_backend(session_backend)  # type: ignore[arg-type]
    binding = SimpleNamespace(
        folder_id="cloud_c",
        rel_path="cloud_c",
        name="云C",
        local_binding=None,
    )

    async def _fake_rebuild(**_kwargs):
        return "CLOUD_PROMPT"

    with (
        patch(
            "agentcore.runtime.delegate.target_desktop.load_target_folder_binding",
            new=AsyncMock(return_value=binding),
        ),
        patch(
            "agentcore.runtime.delegate.target_desktop.build_target_backend",
            return_value=cloud_backend,
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
            target_folder_id="cloud_c",
            session_folder_id="birth",
            env_system_prompt="P",
            base_tool_context=ctx,
            worker_tools=ToolRegistry(),
            sink=MagicMock(),
            local_root_claims=claims,
        )

    assert applied.target_folder_id == "cloud_c"
    assert applied.tool_ctx.backend is cloud_backend
    assert applied.system_prompt == "CLOUD_PROMPT"


class _NamedTool:
    """Minimal registry occupant so apply_target_desktop can drop execution names."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="t",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.EXECUTION,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        return ToolResult(tool_call_id="", success=True, output="ok")


@pytest.mark.asyncio
async def test_apply_target_desktop_sidecar_strips_cloud_exec_tools(
    monkeypatch: pytest.MonkeyPatch,
):
    """Birth-desk ``run`` must not ride onto a sidecar cloud folder."""
    from agentcore.config import settings
    from agentcore.tools.sandbox.cloud_health import set_cloud_sandbox_health_for_tests

    monkeypatch.setattr(settings, "gvisor_enabled", True)
    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.core.is_sidecar_process", lambda: True
    )
    set_cloud_sandbox_health_for_tests(True)

    session_backend = SimpleNamespace(location="local", _channel=None)
    cloud_backend = SimpleNamespace(location="server", _channel=None)
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=session_backend,  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c1",
    )
    binding = SimpleNamespace(
        folder_id="cloud_c",
        rel_path="cloud_c",
        name="云C",
        local_binding=None,
    )
    worker_tools = ToolRegistry()
    worker_tools.register(_NamedTool("run"))
    worker_tools.register(_NamedTool("file_read"))

    async def _fake_rebuild(**_kwargs):
        return "CLOUD_PROMPT"

    with (
        patch(
            "agentcore.runtime.delegate.target_desktop.load_target_folder_binding",
            new=AsyncMock(return_value=binding),
        ),
        patch(
            "agentcore.runtime.delegate.target_desktop.build_target_backend",
            return_value=cloud_backend,
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
            target_folder_id="cloud_c",
            session_folder_id="birth",
            env_system_prompt="P",
            base_tool_context=ctx,
            worker_tools=worker_tools,
            sink=MagicMock(),
            local_root_claims=None,
        )

    names = set(applied.worker_tools.names)
    assert "run" not in names
    assert "file_read" in names
    assert "run" in set(worker_tools.names)


@pytest.mark.asyncio
async def test_apply_target_desktop_cloud_api_keeps_exec_when_sandbox_healthy(
    monkeypatch: pytest.MonkeyPatch,
):
    """Cloud API process + healthy gVisor: swapping onto a cloud desk keeps execution."""
    from agentcore.config import settings
    from agentcore.tools.sandbox.cloud_health import set_cloud_sandbox_health_for_tests

    monkeypatch.setattr(settings, "gvisor_enabled", True)
    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.core.is_sidecar_process", lambda: False
    )
    set_cloud_sandbox_health_for_tests(True)

    session_backend = SimpleNamespace(location="server", _channel=None)
    cloud_backend = SimpleNamespace(location="server", _channel=None)
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=session_backend,  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c1",
    )
    binding = SimpleNamespace(
        folder_id="cloud_c",
        rel_path="cloud_c",
        name="云C",
        local_binding=None,
    )
    worker_tools = ToolRegistry()
    worker_tools.register(_NamedTool("run"))
    worker_tools.register(_NamedTool("file_read"))

    async def _fake_rebuild(**_kwargs):
        return "CLOUD_PROMPT"

    with (
        patch(
            "agentcore.runtime.delegate.target_desktop.load_target_folder_binding",
            new=AsyncMock(return_value=binding),
        ),
        patch(
            "agentcore.runtime.delegate.target_desktop.build_target_backend",
            return_value=cloud_backend,
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
            target_folder_id="cloud_c",
            session_folder_id="birth",
            env_system_prompt="P",
            base_tool_context=ctx,
            worker_tools=worker_tools,
            sink=MagicMock(),
            local_root_claims=None,
        )

    names = set(applied.worker_tools.names)
    assert "run" in names
    assert "file_read" in names


def test_auto_cloud_desk_name_takes_name_shaped_title():
    """显示宽度 ≤16、没有截断标记 → 直接当文件夹名（中西文同轨）。"""
    assert auto_cloud_desk_name(conversation_title="  抚养费起诉状  ") == "抚养费起诉状"
    assert auto_cloud_desk_name(conversation_title="抚养费起诉状草稿") == "抚养费起诉状草稿"
    assert auto_cloud_desk_name(conversation_title="Q3 report") == "Q3 report"


def test_auto_cloud_desk_name_rejects_truncated_title():
    """`fallback_title` 降级出来的半句话（末尾省略号）不当目录名。"""
    from agentcore.conversation.common import fallback_title

    cut = fallback_title("帮我写一份要求男方支付抚养费的起诉状、金额为4000元每月，另附证据清单")
    assert cut.endswith("…")
    assert auto_cloud_desk_name(conversation_title=cut) == "云文件夹"


def test_auto_cloud_desk_name_rejects_sentence_length_title():
    """没被截断但超宽（含英文长标题）→ 通用名。"""
    zh = "帮我写一份要求男方支付抚养费的起诉状"
    en = "Child support complaint"
    assert auto_cloud_desk_name(conversation_title="抚养费起诉状及证据") == "云文件夹"
    assert auto_cloud_desk_name(conversation_title=zh) == "云文件夹"
    assert auto_cloud_desk_name(conversation_title=en) == "云文件夹"
    assert (
        auto_cloud_desk_name(conversation_title="Draft a child support complaint for me")
        == "云文件夹"
    )


def test_auto_cloud_desk_name_falls_back_without_title():
    """无标题一律退通用名——绝不拿用户原话（可能含身份证 / 电话 / 住址）当目录段。"""
    assert auto_cloud_desk_name(conversation_title=None) == "云文件夹"
    assert auto_cloud_desk_name(conversation_title="   ") == "云文件夹"


@pytest.mark.asyncio
async def test_delegate_execute_bare_chat_auto_provisions(monkeypatch):
    """DelegateTool.execute：裸聊写盘缺 target → 静默建云桌并过闸。"""
    from agentcore.llm.provider.protocol import LLMProvider
    from agentcore.runtime.events import EventSink
    from agentcore.tools.builtin.delegate.tool import DelegateTool

    class _DummyLLM(LLMProvider):
        async def complete(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise NotImplementedError

        async def stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise NotImplementedError
            yield  # pragma: no cover

    async def _fake_create(*, user_id: str, name: str) -> dict:
        return {"id": "auto_desk", "name": name, "mode": "cloud"}

    monkeypatch.setattr(
        "agentcore.tools.builtin.folders.create_cloud_folder",
        _fake_create,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._load_conversation_title",
        AsyncMock(return_value="会话标题甲"),
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._load_auto_desk_folder_id",
        AsyncMock(return_value=None),
    )
    from agentcore.runtime.delegate.target_desktop_auto_cloud import AutoDeskPersistResult

    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._persist_auto_desk_folder_id",
        AsyncMock(return_value=AutoDeskPersistResult("auto_desk", "won")),
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop.bind_tool_context_to_landing_desk",
        AsyncMock(return_value=True),
    )

    backend = SimpleNamespace(location="server")
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=backend,  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c1",
    )
    t = DelegateTool(
        llm=_DummyLLM(),  # type: ignore[arg-type]
        sink=EventSink(),
        system_prompt="sys",
        user_message="用户原话不参与建桌命名",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=ctx,
        folder_id=None,
        captain_run_id="CEO",
        approval_gate=None,
    )
    provisioned: list[dict[str, object]] = []

    import agentcore.runtime.delegate.target_desktop as td_mod

    _orig_info = td_mod.logger.info

    def _capture(event: str, **fields: object) -> None:
        if event == "delegate.auto_cloud_desk_provisioned":
            provisioned.append(fields)
        _orig_info(event, **fields)

    monkeypatch.setattr(td_mod.logger, "info", _capture)

    result = await t.execute(
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
    assert ctx.turn_target_desk.folder_id == "auto_desk"
    assert provisioned and provisioned[0].get("folder_id") == "auto_desk"
    assert provisioned[0].get("name") == "会话标题甲"
    assert provisioned[0].get("conversation_untouched") is True


@pytest.mark.asyncio
async def test_ensure_bare_chat_auto_cloud_desk_skips_when_hint_exists(monkeypatch):
    from agentcore.runtime.delegate.target_desktop import ensure_bare_chat_auto_cloud_desk
    from agentcore.tools.protocol import TurnTargetDeskHint

    creates: list[str] = []

    async def _fake_create(*, user_id: str, name: str) -> dict:
        creates.append(name)
        return {"id": "x", "name": name}

    monkeypatch.setattr(
        "agentcore.tools.builtin.folders.create_cloud_folder",
        _fake_create,
    )
    hint = TurnTargetDeskHint()
    hint.note_folder("existing")
    out = await ensure_bare_chat_auto_cloud_desk(
        session_folder_id=None,
        tasks_raw=[{"role": "工", "deliverable": {"form": "files"}}],
        default_target_folder_id="existing",
        turn_target_desk=hint,
        user_id="u1",
    )
    assert out is None
    assert creates == []
    assert hint.auto_cloud_provisioned is False


@pytest.mark.asyncio
async def test_ensure_bare_chat_auto_cloud_desk_skips_local_workspace(monkeypatch):
    from types import SimpleNamespace

    from agentcore.runtime.delegate.target_desktop import ensure_bare_chat_auto_cloud_desk
    from agentcore.tools.protocol import TurnTargetDeskHint

    creates: list[str] = []

    async def _fake_create(*, user_id: str, name: str) -> dict:
        creates.append(name)
        return {"id": "x", "name": name}

    monkeypatch.setattr(
        "agentcore.tools.builtin.folders.create_cloud_folder",
        _fake_create,
    )
    hint = TurnTargetDeskHint()
    out = await ensure_bare_chat_auto_cloud_desk(
        session_folder_id=None,
        tasks_raw=[{"role": "工", "deliverable": {"form": "files"}}],
        default_target_folder_id=None,
        turn_target_desk=hint,
        user_id="u1",
        tool_context=SimpleNamespace(backend=SimpleNamespace(location="local")),
    )
    assert out is None
    assert creates == []


@pytest.mark.asyncio
async def test_ensure_bare_chat_auto_cloud_desk_skips_prose(monkeypatch):
    from agentcore.runtime.delegate.target_desktop import ensure_bare_chat_auto_cloud_desk
    from agentcore.tools.protocol import TurnTargetDeskHint

    creates: list[str] = []

    async def _fake_create(*, user_id: str, name: str) -> dict:
        creates.append(name)
        return {"id": "x", "name": name}

    monkeypatch.setattr(
        "agentcore.tools.builtin.folders.create_cloud_folder",
        _fake_create,
    )
    hint = TurnTargetDeskHint()
    out = await ensure_bare_chat_auto_cloud_desk(
        session_folder_id=None,
        tasks_raw=[{"role": "客", "deliverable": {"form": "prose"}}],
        default_target_folder_id=None,
        turn_target_desk=hint,
        user_id="u1",
    )
    assert out is None
    assert creates == []


@pytest.mark.asyncio
async def test_ensure_bare_chat_auto_cloud_desk_persists_on_first_mint(monkeypatch):
    """首次建桌写入 auto_desk_folder_id，且不改出生 folder_id。"""
    from agentcore.runtime.delegate.target_desktop import ensure_bare_chat_auto_cloud_desk
    from agentcore.tools.protocol import TurnTargetDeskHint

    persisted: list[tuple[str, str]] = []
    birth_writes: list[object] = []

    async def _fake_create(*, user_id: str, name: str) -> dict:
        return {"id": "desk-1", "name": name}

    async def _fake_persist(*, user_id: str, conversation_id: str | None, folder_id: str):
        from agentcore.runtime.delegate.target_desktop_auto_cloud import AutoDeskPersistResult

        persisted.append((conversation_id or "", folder_id))
        return AutoDeskPersistResult(folder_id, "won")

    async def _fake_bind(context, *, folder_id: str) -> bool:
        context.auto_desk_folder_id = folder_id
        return True

    monkeypatch.setattr(
        "agentcore.tools.builtin.folders.create_cloud_folder",
        _fake_create,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._load_auto_desk_folder_id",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._persist_auto_desk_folder_id",
        _fake_persist,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop.bind_tool_context_to_landing_desk",
        _fake_bind,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._load_conversation_title",
        AsyncMock(return_value=None),
    )

    hint = TurnTargetDeskHint()
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=SimpleNamespace(location="server"),  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c-bare",
    )
    sink = _RecordingSink()
    out = await ensure_bare_chat_auto_cloud_desk(
        session_folder_id=None,
        tasks_raw=[{"role": "工", "deliverable": {"form": "files"}}],
        default_target_folder_id=None,
        turn_target_desk=hint,
        user_id="u1",
        conversation_id="c-bare",
        tool_context=ctx,
        sink=sink,
    )
    assert out == "desk-1"
    assert hint.folder_id == "desk-1"
    assert persisted == [("c-bare", "desk-1")]
    assert ctx.auto_desk_folder_id == "desk-1"
    assert birth_writes == []
    # 建成即告知落点（§5.4 裸聊行）：一条 auto_folder_created，不挂起回合。
    # 无标题 → 通用名（用户原话不参与命名）。
    assert _auto_folder_notices(sink) == [{"folder_id": "desk-1", "name": "云文件夹"}]
    assert "desk-1" in ctx.turn_created_folder_ids


@pytest.mark.asyncio
async def test_ensure_bare_chat_auto_cloud_desk_reuses_persisted(monkeypatch):
    """次轮读持久位复用，不再 create_cloud_folder。"""
    from agentcore.runtime.delegate.target_desktop import ensure_bare_chat_auto_cloud_desk
    from agentcore.tools.protocol import TurnTargetDeskHint

    creates: list[str] = []

    async def _fake_create(*, user_id: str, name: str) -> dict:
        creates.append(name)
        return {"id": "new", "name": name}

    binds: list[str] = []

    async def _fake_bind(context, *, folder_id: str) -> bool:
        binds.append(folder_id)
        context.auto_desk_folder_id = folder_id
        return True

    monkeypatch.setattr(
        "agentcore.tools.builtin.folders.create_cloud_folder",
        _fake_create,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._load_auto_desk_folder_id",
        AsyncMock(return_value="desk-1"),
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop.bind_tool_context_to_landing_desk",
        _fake_bind,
    )

    hint = TurnTargetDeskHint()
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=SimpleNamespace(location="server"),  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c-bare",
    )
    sink = _RecordingSink()
    out = await ensure_bare_chat_auto_cloud_desk(
        session_folder_id=None,
        tasks_raw=[{"role": "工", "deliverable": {"form": "files"}}],
        default_target_folder_id=None,
        turn_target_desk=hint,
        user_id="u1",
        conversation_id="c-bare",
        tool_context=ctx,
        sink=sink,
    )
    assert out == "desk-1"
    assert creates == []
    assert hint.folder_id == "desk-1"
    assert binds == ["desk-1"]
    assert hint.auto_cloud_provisioned is False
    # 复用同一张桌不再告知：落点在建桌那回合已经说过了。
    assert _auto_folder_notices(sink) == []
    assert ctx.turn_created_folder_ids == frozenset()


@pytest.mark.asyncio
async def test_ensure_bare_chat_explicit_target_skips_persist_reuse(monkeypatch):
    """显式 default target / 已有 hint → 不建桌、不走复用路径。"""
    from agentcore.runtime.delegate.target_desktop import ensure_bare_chat_auto_cloud_desk
    from agentcore.tools.protocol import TurnTargetDeskHint

    creates: list[str] = []
    loads = AsyncMock(return_value="desk-1")

    async def _fake_create(*, user_id: str, name: str) -> dict:
        creates.append(name)
        return {"id": "x", "name": name}

    monkeypatch.setattr(
        "agentcore.tools.builtin.folders.create_cloud_folder",
        _fake_create,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._load_auto_desk_folder_id",
        loads,
    )

    hint = TurnTargetDeskHint()
    hint.note_folder("explicit-target")
    out = await ensure_bare_chat_auto_cloud_desk(
        session_folder_id=None,
        tasks_raw=[
            {
                "role": "工",
                "deliverable": {"form": "files"},
                "target_folder_id": "explicit-target",
            }
        ],
        default_target_folder_id="explicit-target",
        turn_target_desk=hint,
        user_id="u1",
        conversation_id="c-bare",
    )
    assert out is None
    assert creates == []
    assert loads.await_count == 0
    assert hint.folder_id == "explicit-target"


@pytest.mark.asyncio
async def test_ensure_bare_chat_birth_session_skips_auto_desk(monkeypatch):
    """有出生 folder_id 时不触碰 auto desk。"""
    from agentcore.runtime.delegate.target_desktop import ensure_bare_chat_auto_cloud_desk
    from agentcore.tools.protocol import TurnTargetDeskHint

    creates: list[str] = []
    loads = AsyncMock(return_value="desk-1")

    async def _fake_create(*, user_id: str, name: str) -> dict:
        creates.append(name)
        return {"id": "x", "name": name}

    monkeypatch.setattr(
        "agentcore.tools.builtin.folders.create_cloud_folder",
        _fake_create,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._load_auto_desk_folder_id",
        loads,
    )

    hint = TurnTargetDeskHint()
    out = await ensure_bare_chat_auto_cloud_desk(
        session_folder_id="birth-project",
        tasks_raw=[{"role": "工", "deliverable": {"form": "files"}}],
        default_target_folder_id=None,
        turn_target_desk=hint,
        user_id="u1",
        conversation_id="c-proj",
    )
    assert out is None
    assert creates == []
    assert loads.await_count == 0


def test_resolve_turn_file_workspace_keeps_birth_over_auto_desk():
    from agentcore.conversation.common import resolve_turn_file_workspace

    ws, auto = resolve_turn_file_workspace(
        birth_folder_id="birth",
        auto_desk_folder_id="auto",
    )
    assert ws == "birth"
    assert auto is None

    ws2, auto2 = resolve_turn_file_workspace(
        birth_folder_id=None,
        auto_desk_folder_id="auto",
    )
    assert ws2 == "auto"
    assert auto2 == "auto"


@pytest.mark.asyncio
async def test_ensure_bare_chat_race_loser_reclaims_orphan_mint(monkeypatch):
    """并发 first-write：输掉竞态的调用方回收本回合刚 mint 的 Folder。"""
    from agentcore.runtime.delegate.target_desktop import ensure_bare_chat_auto_cloud_desk
    from agentcore.runtime.delegate.target_desktop_auto_cloud import AutoDeskPersistResult
    from agentcore.tools.protocol import TurnTargetDeskHint

    reclaimed: list[str] = []

    async def _fake_create(*, user_id: str, name: str) -> dict:
        return {"id": "mint-loser", "name": name}

    async def _fake_persist(*, user_id: str, conversation_id: str | None, folder_id: str):
        return AutoDeskPersistResult("desk-winner", "lost")

    async def _fake_reclaim(*, user_id: str, folder_id: str) -> None:
        reclaimed.append(folder_id)

    async def _fake_bind(context, *, folder_id: str) -> bool:
        context.auto_desk_folder_id = folder_id
        return True

    monkeypatch.setattr(
        "agentcore.tools.builtin.folders.create_cloud_folder",
        _fake_create,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._load_auto_desk_folder_id",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._persist_auto_desk_folder_id",
        _fake_persist,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._reclaim_orphan_auto_desk_folder",
        _fake_reclaim,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop.bind_tool_context_to_landing_desk",
        _fake_bind,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._load_conversation_title",
        AsyncMock(return_value=None),
    )

    hint = TurnTargetDeskHint()
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=SimpleNamespace(location="server"),  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c-bare",
    )
    sink = _RecordingSink()
    out = await ensure_bare_chat_auto_cloud_desk(
        session_folder_id=None,
        tasks_raw=[{"role": "工", "deliverable": {"form": "files"}}],
        default_target_folder_id=None,
        turn_target_desk=hint,
        user_id="u1",
        conversation_id="c-bare",
        tool_context=ctx,
        sink=sink,
    )
    assert out == "desk-winner"
    assert hint.folder_id == "desk-winner"
    assert ctx.auto_desk_folder_id == "desk-winner"
    assert reclaimed == ["mint-loser"]
    # 输掉竞态：本回合的 mint 已回收，若再告知就会指向一个刚被丢掉的文件夹。
    assert _auto_folder_notices(sink) == []


@pytest.mark.asyncio
async def test_ensure_bare_chat_dead_pointer_remints_after_bind_miss(monkeypatch):
    """指针指向已删 Folder：绑不上 → 清指针 → 本回合重新 mint。"""
    from agentcore.runtime.delegate.target_desktop import ensure_bare_chat_auto_cloud_desk
    from agentcore.runtime.delegate.target_desktop_auto_cloud import AutoDeskPersistResult
    from agentcore.tools.protocol import TurnTargetDeskHint

    creates: list[str] = []
    binds: list[str] = []

    async def _fake_create(*, user_id: str, name: str) -> dict:
        creates.append(name)
        return {"id": "desk-fresh", "name": name}

    async def _fake_persist(*, user_id: str, conversation_id: str | None, folder_id: str):
        return AutoDeskPersistResult(folder_id, "won")

    async def _fake_bind(context, *, folder_id: str) -> bool:
        binds.append(folder_id)
        if folder_id == "desk-dead":
            context.auto_desk_folder_id = None
            return False
        context.auto_desk_folder_id = folder_id
        return True

    monkeypatch.setattr(
        "agentcore.tools.builtin.folders.create_cloud_folder",
        _fake_create,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._load_auto_desk_folder_id",
        AsyncMock(return_value="desk-dead"),
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._persist_auto_desk_folder_id",
        _fake_persist,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop.bind_tool_context_to_landing_desk",
        _fake_bind,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._load_conversation_title",
        AsyncMock(return_value=None),
    )

    hint = TurnTargetDeskHint()
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=SimpleNamespace(location="server"),  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c-bare",
        auto_desk_folder_id="desk-dead",
    )
    out = await ensure_bare_chat_auto_cloud_desk(
        session_folder_id=None,
        tasks_raw=[{"role": "工", "deliverable": {"form": "files"}}],
        default_target_folder_id=None,
        turn_target_desk=hint,
        user_id="u1",
        conversation_id="c-bare",
        tool_context=ctx,
    )
    assert out == "desk-fresh"
    assert creates == ["云文件夹"]
    assert binds == ["desk-dead", "desk-fresh"]
    assert hint.folder_id == "desk-fresh"
    assert ctx.auto_desk_folder_id == "desk-fresh"


@pytest.mark.asyncio
async def test_bind_landing_desk_clears_stale_pointer_when_folder_missing(monkeypatch):
    """绑不上（folder 不存在）时清掉 Conversation.auto_desk_folder_id。"""
    from agentcore.runtime.delegate.target_desktop import bind_tool_context_to_landing_desk

    cleared: list[tuple[str, str]] = []

    async def _fake_clear(*, user_id: str, conversation_id: str | None, folder_id: str):
        cleared.append((conversation_id or "", folder_id))
        return True

    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop.load_target_folder_binding",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.target_desktop._clear_stale_auto_desk_folder_id",
        _fake_clear,
    )

    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=SimpleNamespace(location="server"),  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c-bare",
        auto_desk_folder_id="desk-dead",
    )
    ok = await bind_tool_context_to_landing_desk(ctx, folder_id="desk-dead")
    assert ok is False
    assert ctx.auto_desk_folder_id is None
    assert cleared == [("c-bare", "desk-dead")]
