"""Organize plan binding + journal undo (P1 desktop organize)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agentcore.core.types import ToolApproval
from agentcore.runtime.approvals import tool_call_requires_approval
from agentcore.tools.builtin.ask_user.card import (
    option_to_organize_op,
    parse_card,
    validate_card_shape,
)
from agentcore.tools.builtin.file_ops import FileBatchTool
from agentcore.tools.protocol import ToolContext
from agentcore.workspace import organize_journal, organize_plan_store
from agentcore.workspace.channel import WorkspaceChannel
from agentcore.workspace.external_mounts import ExternalMount
from agentcore.workspace.local import LocalWorkspace


@pytest.fixture(autouse=True)
def _clear_stores():
    organize_plan_store.clear_all_for_tests()
    organize_journal.clear_all_for_tests()
    yield
    organize_plan_store.clear_all_for_tests()
    organize_journal.clear_all_for_tests()


def test_parse_organize_plan_card():
    assert parse_card("organize_plan") == "organize_plan"
    q = [
        {
            "kind": "choice",
            "multiple": True,
            "options": [{"label": "a → b", "op": "move", "source": "a", "destination": "b"}],
        }
    ]
    assert validate_card_shape("organize_plan", questions=q) is None
    assert validate_card_shape("organize_plan", questions=[])


def test_option_to_organize_op():
    assert option_to_organize_op(
        {"op": "move", "source": "external/d/a", "destination": "external/d/b"}
    ) == {"op": "move", "source": "external/d/a", "destination": "external/d/b"}
    assert option_to_organize_op({"op": "delete", "path": "external/d/x"}) == {
        "op": "delete",
        "path": "external/d/x",
    }
    assert option_to_organize_op({"op": "move", "source": "a"}) is None


def test_plan_scope_and_approval_skip():
    ops = [
        {"op": "move", "source": "external/d/a", "destination": "external/d/Docs/a"},
        {"op": "mkdir", "path": "external/d/Docs"},
    ]
    organize_plan_store.register_plan(
        plan_id="plan1", conversation_id="c1", operations=ops
    )
    assert (
        tool_call_requires_approval(
            "file_batch",
            ToolApproval.GRANTABLE,
            {"organize_plan_id": "plan1", "operations": ops},
        )
        is False
    )
    assert (
        tool_call_requires_approval(
            "file_batch",
            ToolApproval.GRANTABLE,
            {
                "organize_plan_id": "plan1",
                "operations": [
                    {
                        "op": "move",
                        "source": "external/d/evil",
                        "destination": "external/d/x",
                    }
                ],
            },
        )
        is True
    )


@pytest.mark.asyncio
async def test_file_batch_plan_scope_rejects_out_of_plan():
    channel = AsyncMock(spec=WorkspaceChannel)
    channel.root_id = "primary"
    channel.conversation_id = "c1"
    channel.request = AsyncMock(return_value=None)
    ws = LocalWorkspace(channel)
    ws.attach_external_mounts(
        {
            "d": ExternalMount(
                alias="d", root_id="ext", label="d", mode="organize"
            )
        }
    )
    organize_plan_store.register_plan(
        plan_id="p1",
        conversation_id="c1",
        operations=[{"op": "mkdir", "path": "external/d/Docs"}],
    )
    tool = FileBatchTool()
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=ws,
        user_id="u",
        conversation_id="c1",
    )
    res = await tool.execute(
        {
            "organize_plan_id": "p1",
            "operations": [
                {"op": "move", "source": "external/d/a", "destination": "external/d/b"}
            ],
        },
        ctx,
    )
    assert not res.success
    assert "方案内" in (res.error or res.output)


@pytest.mark.asyncio
async def test_file_batch_conflict_skips(tmp_path: Path):
    from agentcore.workspace.server import ServerWorkspace

    primary = tmp_path / "ws"
    primary.mkdir()
    (primary / "a.txt").write_text("1", encoding="utf-8")
    (primary / "b.txt").write_text("2", encoding="utf-8")

    class _Sandbox:
        async def execute(self, req):
            from agentcore.tools.sandbox.protocol import ExecutionResult

            return ExecutionResult(
                success=True, stdout="", stderr="", exit_code=0, duration_ms=0
            )

    ws = ServerWorkspace(primary, _Sandbox(), location="local")
    tool = FileBatchTool()
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=ws,
        user_id="u",
        conversation_id="c1",
    )
    res = await tool.execute(
        {
            "operations": [
                {"op": "move", "source": "a.txt", "destination": "b.txt"},
            ]
        },
        ctx,
    )
    assert res.success  # skip is OK
    assert "跳过" in res.output
    assert (primary / "a.txt").exists()
    assert (primary / "b.txt").read_text(encoding="utf-8") == "2"
