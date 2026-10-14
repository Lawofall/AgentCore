"""Shared workspace slot + bare-chat auto-desk tree migrate."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcore.tools.builtin.file_ops import FileListTool, FileReadTool
from agentcore.tools.protocol import ToolContext, fork_workspace_slot
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.external_mounts import ExternalMount
from agentcore.workspace.migrate_tree import (
    migrate_and_transfer_cloud_backend,
    migrate_cloud_workspace_tree,
)
from agentcore.workspace.server import ServerWorkspace


def _server_ws(root: Path) -> ServerWorkspace:
    return ServerWorkspace(root=root, sandbox=SubprocessSandbox())


def test_tool_context_replace_follows_backend_rebind(tmp_path: Path):
    scratch = _server_ws(tmp_path / "scratch")
    desk = _server_ws(tmp_path / "desk")
    base = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        user_id="u1",
        backend=scratch,
        material_paths=frozenset({"attachments/a.md"}),
    )
    captain = replace(base, run_id="cap", agent_id="captain")
    worker = replace(base, run_id="w1", agent_id="worker")

    assert captain.backend is scratch
    base.backend = desk
    assert captain.backend is desk
    assert worker.backend is desk
    assert captain.material_paths == frozenset({"attachments/a.md"})
    base.material_paths = frozenset({"attachments/a.md", "attachments/b.md"})
    assert worker.material_paths == frozenset({"attachments/a.md", "attachments/b.md"})


def test_fork_workspace_slot_does_not_follow_parent(tmp_path: Path):
    scratch = _server_ws(tmp_path / "scratch")
    desk = _server_ws(tmp_path / "desk")
    alien = _server_ws(tmp_path / "alien")
    base = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        user_id="u1",
        backend=scratch,
    )
    forked = replace(
        base,
        _workspace=fork_workspace_slot(alien, material_paths=frozenset()),
        run_id="w",
    )
    base.backend = desk
    assert forked.backend is alien
    assert base.backend is desk


def test_migrate_cloud_workspace_tree_moves_attachments(tmp_path: Path):
    src = tmp_path / "conv"
    dst = tmp_path / "folder"
    (src / "attachments").mkdir(parents=True)
    (src / "attachments" / "contract.md").write_text("合同正文\n", encoding="utf-8")
    (src / "notes.txt").write_text("n\n", encoding="utf-8")

    moved = migrate_cloud_workspace_tree(src_root=src, dst_root=dst)
    assert moved >= 2
    assert (dst / "attachments" / "contract.md").read_text(encoding="utf-8") == "合同正文\n"
    assert (dst / "notes.txt").read_text(encoding="utf-8") == "n\n"
    # Idempotent
    assert migrate_cloud_workspace_tree(src_root=src, dst_root=dst) == 0


def test_migrate_transfers_materials_and_mounts(tmp_path: Path):
    src_root = tmp_path / "src"
    dst_root = tmp_path / "dst"
    src_root.mkdir()
    dst_root.mkdir()
    src = _server_ws(src_root)
    dst = _server_ws(dst_root)
    (src_root / "attachments").mkdir()
    (src_root / "attachments" / "a.md").write_text("x\n", encoding="utf-8")
    src.ai_list_materials = frozenset({"attachments/a.md"})
    src.attach_external_mounts(
        {
            "docs": ExternalMount(
                alias="docs",
                root_id="root-1",
                label="docs",
                abs_path=None,
            )
        }
    )

    migrate_and_transfer_cloud_backend(src, dst)
    assert (dst_root / "attachments" / "a.md").is_file()
    assert dst.ai_list_materials == frozenset({"attachments/a.md"})
    assert "docs" in dst._mounts


@pytest.mark.asyncio
async def test_bind_landing_desk_rebinds_shared_slot_and_moves_tree(tmp_path: Path):
    """裸聊附件 + 派单 mint：captain/worker 同 root，下属读得到附件。"""
    from agentcore.runtime.graph.desk.target_desktop import bind_tool_context_to_landing_desk

    scratch_root = tmp_path / "scratch"
    desk_root = tmp_path / "desk"
    scratch_root.mkdir()
    desk_root.mkdir()
    scratch = _server_ws(scratch_root)
    (scratch_root / "attachments").mkdir(parents=True)
    (scratch_root / "attachments" / "合同.docx.md").write_text("条款一\n", encoding="utf-8")
    materials = frozenset({"attachments/合同.docx.md"})
    scratch.ai_list_materials = materials

    base = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        user_id="u1",
        conversation_id="c-bare",
        backend=scratch,
        material_paths=materials,
        attachment_context="附件：合同.docx.md",
    )
    captain = replace(base, run_id="cap", agent_id="captain")

    binding = SimpleNamespace(
        folder_id="desk-1",
        rel_path="desk-1",
        name="云项目",
        local_binding=None,
    )
    target = _server_ws(desk_root)

    with (
        patch(
            "agentcore.runtime.graph.desk.target_desktop.load_target_folder_binding",
            new=AsyncMock(return_value=binding),
        ),
        patch(
            "agentcore.runtime.graph.desk.target_desktop.build_target_backend",
            return_value=target,
        ),
        patch(
            "agentcore.workspace.locate.workspace_channel_for_tools",
            return_value=None,
        ),
    ):
        ok = await bind_tool_context_to_landing_desk(base, folder_id="desk-1")

    assert ok is True
    assert captain.backend is target
    assert base.backend is target
    assert (desk_root / "attachments" / "合同.docx.md").read_text(encoding="utf-8") == "条款一\n"
    assert target.ai_list_materials == materials
    assert captain.material_paths == materials

    worker = replace(base, run_id="w1", agent_id="worker")
    read = await FileReadTool().execute(
        {"file_path": "attachments/合同.docx.md"},
        worker,
    )
    assert read.success is True
    assert "条款一" in read.output

    listing = await FileListTool().execute({"directory": "attachments"}, worker)
    assert listing.success is True
    assert "合同.docx.md" in listing.output


@pytest.mark.asyncio
async def test_apply_target_desktop_forks_slot_and_passes_attachment_context(
    tmp_path: Path,
):
    from agentcore.runtime.graph.desk.target_desktop import apply_target_desktop

    session = _server_ws(tmp_path / "session")
    target_backend = _server_ws(tmp_path / "target")
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        user_id="u1",
        conversation_id="c1",
        backend=session,
        material_paths=frozenset({"attachments/x.md"}),
        attachment_context="ATTACH_BLOCK",
    )
    captain = replace(ctx, run_id="cap")
    tools = ToolRegistry()
    binding = SimpleNamespace(
        folder_id="target_f",
        rel_path="target_f",
        name="目标",
        local_binding=None,
    )
    seen: dict[str, object] = {}

    async def _fake_rebuild(**kwargs):
        seen.update(kwargs)
        return "TARGET_PROMPT", ""

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
            target_folder_id="target_f",
            session_folder_id="birth_f",
            env_system_prompt="OLD",
            base_tool_context=ctx,
            worker_tools=tools,
            sink=MagicMock(),
            local_root_claims=None,
        )

    assert seen.get("attachment_context") == "ATTACH_BLOCK"
    assert applied.tool_ctx.backend is target_backend
    # Parent rebind must not drag the forked worker desk.
    desk2 = _server_ws(tmp_path / "desk2")
    ctx.backend = desk2
    assert captain.backend is desk2
    assert applied.tool_ctx.backend is target_backend
