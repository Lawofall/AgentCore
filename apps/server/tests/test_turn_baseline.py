"""Lazy turn baseline: capture before file mutation, not on greeting."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agentcore.runtime.engine.tool_exec_gates import _maybe_capture_mutation_baseline
from agentcore.runtime.journal.writer import TurnJournalWriter, current_journal_writer
from agentcore.workspace.turn_baseline import (
    local_baseline_ready,
    maybe_capture_turn_baseline,
    tool_warrants_turn_baseline,
)


def test_tool_warrants_file_mutation_not_reads():
    assert tool_warrants_turn_baseline("write", {"file_path": "a.ts"}) is True
    assert tool_warrants_turn_baseline("edit", {"file_path": "a.ts"}) is True
    assert tool_warrants_turn_baseline("file_delete", {"path": "a.ts"}) is True
    assert tool_warrants_turn_baseline("read", {"file_path": "a.ts"}) is False
    assert tool_warrants_turn_baseline("web_search", {"query": "hi"}) is False
    assert tool_warrants_turn_baseline("run", {"command": "ls"}) is False


def test_tool_warrants_ignores_retired_git_tool_name():
    assert tool_warrants_turn_baseline("git", {"subcommand": "status"}) is False
    assert tool_warrants_turn_baseline("git", {"subcommand": "commit"}) is False


@pytest.mark.asyncio
async def test_maybe_capture_skips_empty_message_id(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    sid = await maybe_capture_turn_baseline(
        user_id="u1",
        folder_id=None,
        conversation_id="c1",
        message_id="  ",
        backend=SimpleNamespace(location="local"),
        workspace_root=root,
    )
    assert sid is None
    assert not list(root.glob("**/GAgent/baselines/*.zip"))


@pytest.mark.asyncio
async def test_maybe_capture_reuses_existing_zip(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("x", encoding="utf-8")
    sid = await maybe_capture_turn_baseline(
        user_id="u1",
        folder_id=None,
        conversation_id="c1",
        message_id="msg-1",
        backend=SimpleNamespace(location="local"),
        workspace_root=root,
    )
    assert sid == "msg-1"
    assert local_baseline_ready(root, "msg-1")

    with patch(
        "agentcore.workspace.turn_baseline._zip_local_baseline_sync",
        side_effect=AssertionError("must not re-zip"),
    ):
        again = await maybe_capture_turn_baseline(
            user_id="u1",
            folder_id=None,
            conversation_id="c1",
            message_id="msg-1",
            backend=SimpleNamespace(location="local"),
            workspace_root=root,
        )
    assert again == "msg-1"


@pytest.mark.asyncio
async def test_mutation_gate_captures_on_file_write_not_read(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("x", encoding="utf-8")
    backend = SimpleNamespace(location="local", root=root)
    context = SimpleNamespace(
        backend=backend,
        user_id="u1",
        conversation_id="c1",
        ownership_desk_id=None,
    )
    writer = TurnJournalWriter(turn_id="turn-w", conversation_id="c1", trace_id=None)
    token = current_journal_writer.set(writer)
    try:
        await _maybe_capture_mutation_baseline(
            tool_name="read",
            args={"file_path": "a.txt"},
            context=context,  # type: ignore[arg-type]
        )
        assert not local_baseline_ready(root, "turn-w")

        await _maybe_capture_mutation_baseline(
            tool_name="write",
            args={"file_path": "b.txt", "content": "y"},
            context=context,  # type: ignore[arg-type]
        )
        assert local_baseline_ready(root, "turn-w")
    finally:
        current_journal_writer.reset(token)


@pytest.mark.asyncio
async def test_mutation_gate_noops_without_journal(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("x", encoding="utf-8")
    spy = AsyncMock(return_value="turn-x")
    context = SimpleNamespace(
        backend=SimpleNamespace(location="local", root=root),
        user_id="u1",
        conversation_id="c1",
        ownership_desk_id=None,
    )
    with patch(
        "agentcore.workspace.turn_baseline.maybe_capture_turn_baseline",
        spy,
    ):
        await _maybe_capture_mutation_baseline(
            tool_name="write",
            args={"file_path": "a.txt"},
            context=context,  # type: ignore[arg-type]
        )
    spy.assert_not_called()
    assert not local_baseline_ready(root, "turn-x")
