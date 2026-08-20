"""Unit tests for workspace destructive_fs heuristics + Local baseline gate (P0a/b+P2).

Honest positioning: heuristics are a narrow blacklist, not a complete boundary.
Cloud staging deletes-not-written-back and registry_egress rw-bind exceptions are
out of scope here (maintained as-is).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agentcore.runtime.engine.tool_exec import _apply_local_destructive_baseline_gate
from agentcore.runtime.journal.writer import TurnJournalWriter, current_journal_writer
from agentcore.runtime.safety_breaker import BreakerVerdict, evaluate_tool_call
from agentcore.workspace.destructive_fs import (
    CLEANUP_WHITELIST,
    requires_destructive_baseline_gate,
    requires_top_level_tree_gate,
    scan_destructive_fs,
)
from agentcore.workspace.turn_baseline import (
    ensure_local_baseline_for_destructive,
    local_baseline_path,
    local_baseline_ready,
    maybe_capture_turn_baseline,
)


def test_whitelist_names_cover_p2_contract():
    assert frozenset(
        {
            "node_modules",
            ".venv",
            "venv",
            "dist",
            "build",
            ".next",
            "__pycache__",
            ".git",
        }
    ) == CLEANUP_WHITELIST


@pytest.mark.parametrize(
    "text",
    [
        'shutil.rmtree("node_modules")',
        "shutil.rmtree('.venv')",
        "rm -rf dist",
        "rm -rf ./build",
        "Remove-Item -Recurse -Force .next",
        "rimraf __pycache__",
        "npx rimraf .git",
    ],
)
def test_scan_whitelist_only(text: str):
    hit = scan_destructive_fs(text)
    assert hit is not None
    assert hit.whitelist_only is True
    assert hit.top_level_project is False
    assert requires_destructive_baseline_gate(hit) is False
    assert requires_top_level_tree_gate(hit) is False


@pytest.mark.parametrize(
    "text,expect_top",
    [
        ('shutil.rmtree(cwd / "ai-team-workbench")', True),
        ('shutil.rmtree("my-app")', True),
        ("rm -rf ./project-root", True),
        ("Remove-Item -Recurse -Force .\\legacy-app", True),
        ('shutil.rmtree("src/legacy")', False),
        ("rm -rf apps/web/tmp", False),
        ("shutil.rmtree(path)", False),  # variable — destructive, not proven top-level
    ],
)
def test_scan_top_level_vs_nested(text: str, expect_top: bool):
    hit = scan_destructive_fs(text)
    assert hit is not None
    assert hit.whitelist_only is False
    assert hit.top_level_project is expect_top
    assert requires_destructive_baseline_gate(hit) is True
    assert requires_top_level_tree_gate(hit) is expect_top


def test_scan_benign_code_misses():
    assert scan_destructive_fs("print(1+1)\nx = open('a').read()") is None
    assert scan_destructive_fs("rm file.txt") is None


@pytest.mark.asyncio
async def test_ensure_local_baseline_reuses_existing_zip(tmp_path: Path):
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
        "agentcore.workspace.turn_baseline._capture_local_baseline",
        new_callable=AsyncMock,
    ) as capture:
        ok = await ensure_local_baseline_for_destructive(
            user_id="u1",
            conversation_id="c1",
            message_id="msg-1",
            workspace_root=root,
        )
        assert ok is True
        capture.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_local_baseline_captures_when_missing(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("x", encoding="utf-8")
    assert not local_baseline_ready(root, "msg-2")
    ok = await ensure_local_baseline_for_destructive(
        user_id="u1",
        conversation_id="c1",
        message_id="msg-2",
        workspace_root=root,
    )
    assert ok is True
    assert local_baseline_path(root, "msg-2").is_file()


@pytest.mark.asyncio
async def test_ordinary_baseline_still_non_blocking_on_cap(tmp_path: Path, monkeypatch):
    """P0a invariant: regular maybe_capture failure must not raise / block."""
    root = tmp_path / "ws"
    root.mkdir()
    for i in range(5):
        (root / f"f{i}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "agentcore.workspace.turn_baseline.LOCAL_BASELINE_MAX_FILES",
        2,
    )
    sid = await maybe_capture_turn_baseline(
        user_id="u1",
        folder_id=None,
        conversation_id="c1",
        message_id="msg-cap",
        backend=SimpleNamespace(location="local"),
        workspace_root=root,
    )
    assert sid is None
    assert not local_baseline_ready(root, "msg-cap")
    # Destructive ensure also returns False (caller upgrades to FORCE_APPROVAL).
    ok = await ensure_local_baseline_for_destructive(
        user_id="u1",
        conversation_id="c1",
        message_id="msg-cap",
        workspace_root=root,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_local_gate_forces_without_baseline(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("x", encoding="utf-8")
    backend = SimpleNamespace(location="local", root=root)
    context = SimpleNamespace(
        backend=backend,
        user_id="u1",
        conversation_id="c1",
    )
    writer = TurnJournalWriter(turn_id="turn-no-zip", conversation_id="c1", trace_id=None)
    token = current_journal_writer.set(writer)
    try:
        with patch(
            "agentcore.workspace.turn_baseline.ensure_local_baseline_for_destructive",
            new_callable=AsyncMock,
            return_value=False,
        ):
            hit = await _apply_local_destructive_baseline_gate(
                tool_name="code_execute",
                args={
                    "language": "python",
                    "code": 'shutil.rmtree("src/legacy")\n',
                },
                context=context,  # type: ignore[arg-type]
                existing=None,
            )
        assert hit is not None
        assert hit.verdict is BreakerVerdict.FORCE_APPROVAL
        assert hit.rule_id == "destructive.no_turn_baseline"
    finally:
        current_journal_writer.reset(token)


@pytest.mark.asyncio
async def test_local_gate_passes_with_baseline(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("x", encoding="utf-8")
    await ensure_local_baseline_for_destructive(
        user_id="u1",
        conversation_id="c1",
        message_id="turn-ok",
        workspace_root=root,
    )
    backend = SimpleNamespace(location="local", root=root)
    context = SimpleNamespace(
        backend=backend,
        user_id="u1",
        conversation_id="c1",
    )
    writer = TurnJournalWriter(turn_id="turn-ok", conversation_id="c1", trace_id=None)
    token = current_journal_writer.set(writer)
    try:
        hit = await _apply_local_destructive_baseline_gate(
            tool_name="code_execute",
            args={
                "language": "python",
                "code": 'shutil.rmtree("src/legacy")\n',
            },
            context=context,  # type: ignore[arg-type]
            existing=None,
        )
        assert hit is None
    finally:
        current_journal_writer.reset(token)


@pytest.mark.asyncio
async def test_local_gate_skips_server_location():
    """Cloud staging: deletes not written back — baseline gate does not apply."""
    backend = SimpleNamespace(location="server", root=Path("/tmp/unused"))
    context = SimpleNamespace(backend=backend, user_id="u1", conversation_id="c1")
    hit = await _apply_local_destructive_baseline_gate(
        tool_name="code_execute",
        args={"language": "python", "code": 'shutil.rmtree("src")\n'},
        context=context,  # type: ignore[arg-type]
        existing=None,
    )
    assert hit is None


@pytest.mark.asyncio
async def test_local_gate_skips_whitelist_cleanup(tmp_path: Path):
    backend = SimpleNamespace(location="local", root=tmp_path)
    context = SimpleNamespace(backend=backend, user_id="u1", conversation_id="c1")
    hit = await _apply_local_destructive_baseline_gate(
        tool_name="code_execute",
        args={"language": "python", "code": 'shutil.rmtree("node_modules")\n'},
        context=context,  # type: ignore[arg-type]
        existing=None,
    )
    assert hit is None


@pytest.mark.asyncio
async def test_local_gate_does_not_stack_on_existing_force(tmp_path: Path):
    """P2 top-tree already FORCE_APPROVAL — keep that card, do not replace."""
    from agentcore.runtime.safety_breaker import BreakerHit

    existing = BreakerHit(
        verdict=BreakerVerdict.FORCE_APPROVAL,
        rule_id="destructive.workspace_top_tree",
        reason="top",
    )
    backend = SimpleNamespace(location="local", root=tmp_path)
    context = SimpleNamespace(backend=backend, user_id="u1", conversation_id="c1")
    writer = TurnJournalWriter(turn_id="turn-x", conversation_id="c1", trace_id=None)
    token = current_journal_writer.set(writer)
    try:
        with patch(
            "agentcore.workspace.turn_baseline.ensure_local_baseline_for_destructive",
            new_callable=AsyncMock,
            return_value=False,
        ):
            hit = await _apply_local_destructive_baseline_gate(
                tool_name="code_execute",
                args={
                    "language": "python",
                    "code": 'shutil.rmtree("ai-team-workbench")\n',
                },
                context=context,  # type: ignore[arg-type]
                existing=existing,
            )
        assert hit is existing
        assert hit.rule_id == "destructive.workspace_top_tree"
    finally:
        current_journal_writer.reset(token)


@pytest.mark.asyncio
async def test_local_gate_channel_ready_skips_no_baseline_upgrade():
    """轨 3: channel LocalWorkspace (no Path.root) + ready → no no_turn_baseline."""
    backend = SimpleNamespace(
        location="local",
        # No Path.root — channel-only desktop Local.
        ensure_turn_baseline_ready=AsyncMock(return_value=True),
    )
    context = SimpleNamespace(
        backend=backend,
        user_id="u1",
        conversation_id="c1",
    )
    writer = TurnJournalWriter(turn_id="turn-ch-ok", conversation_id="c1", trace_id=None)
    token = current_journal_writer.set(writer)
    try:
        hit = await _apply_local_destructive_baseline_gate(
            tool_name="code_execute",
            args={
                "language": "python",
                "code": 'shutil.rmtree("src/legacy")\n',
            },
            context=context,  # type: ignore[arg-type]
            existing=None,
        )
        assert hit is None
        backend.ensure_turn_baseline_ready.assert_awaited_once_with("turn-ch-ok")
    finally:
        current_journal_writer.reset(token)


@pytest.mark.asyncio
async def test_local_gate_channel_not_ready_forces():
    """轨 3: channel Local without ready still FORCE_APPROVAL (fail-closed)."""
    backend = SimpleNamespace(
        location="local",
        ensure_turn_baseline_ready=AsyncMock(return_value=False),
    )
    context = SimpleNamespace(
        backend=backend,
        user_id="u1",
        conversation_id="c1",
    )
    writer = TurnJournalWriter(turn_id="turn-ch-miss", conversation_id="c1", trace_id=None)
    token = current_journal_writer.set(writer)
    try:
        hit = await _apply_local_destructive_baseline_gate(
            tool_name="code_execute",
            args={
                "language": "python",
                "code": 'shutil.rmtree("src/legacy")\n',
            },
            context=context,  # type: ignore[arg-type]
            existing=None,
        )
        assert hit is not None
        assert hit.verdict is BreakerVerdict.FORCE_APPROVAL
        assert hit.rule_id == "destructive.no_turn_baseline"
    finally:
        current_journal_writer.reset(token)


@pytest.mark.asyncio
async def test_local_gate_channel_ready_still_keeps_top_tree_force():
    """轨 3: ready removes no_turn_baseline only — workspace_top_tree still FORCE."""
    from agentcore.runtime.safety_breaker import BreakerHit

    existing = BreakerHit(
        verdict=BreakerVerdict.FORCE_APPROVAL,
        rule_id="destructive.workspace_top_tree",
        reason="top",
    )
    backend = SimpleNamespace(
        location="local",
        ensure_turn_baseline_ready=AsyncMock(return_value=True),
    )
    context = SimpleNamespace(backend=backend, user_id="u1", conversation_id="c1")
    writer = TurnJournalWriter(turn_id="turn-ch-top", conversation_id="c1", trace_id=None)
    token = current_journal_writer.set(writer)
    try:
        hit = await _apply_local_destructive_baseline_gate(
            tool_name="code_execute",
            args={
                "language": "python",
                "code": 'shutil.rmtree("ai-team-workbench")\n',
            },
            context=context,  # type: ignore[arg-type]
            existing=existing,
        )
        assert hit is existing
        assert hit.rule_id == "destructive.workspace_top_tree"
        backend.ensure_turn_baseline_ready.assert_awaited()
    finally:
        current_journal_writer.reset(token)


@pytest.mark.asyncio
async def test_local_gate_channel_whitelist_still_skips():
    """轨 3: whitelist cleanup never asks ensure / never upgrades."""
    backend = SimpleNamespace(
        location="local",
        ensure_turn_baseline_ready=AsyncMock(return_value=False),
    )
    context = SimpleNamespace(backend=backend, user_id="u1", conversation_id="c1")
    hit = await _apply_local_destructive_baseline_gate(
        tool_name="code_execute",
        args={"language": "python", "code": 'shutil.rmtree("node_modules")\n'},
        context=context,  # type: ignore[arg-type]
        existing=None,
    )
    assert hit is None
    backend.ensure_turn_baseline_ready.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_channel_backend_ready_without_path_root():
    backend = SimpleNamespace(
        location="local",
        ensure_turn_baseline_ready=AsyncMock(return_value=True),
    )
    ok = await ensure_local_baseline_for_destructive(
        user_id="u1",
        conversation_id="c1",
        message_id="msg-ch",
        backend=backend,
    )
    assert ok is True
    backend.ensure_turn_baseline_ready.assert_awaited_once_with("msg-ch")


@pytest.mark.asyncio
async def test_maybe_capture_uses_channel_capture_when_no_root():
    backend = SimpleNamespace(
        location="local",
        capture_turn_baseline=AsyncMock(return_value="msg-cap-ch"),
    )
    sid = await maybe_capture_turn_baseline(
        user_id="u1",
        folder_id=None,
        conversation_id="c1",
        message_id="msg-cap-ch",
        backend=backend,
    )
    assert sid == "msg-cap-ch"
    backend.capture_turn_baseline.assert_awaited_once_with("msg-cap-ch")


def test_evaluate_still_denies_host_shell_fuse_without_double_card():
    """fuse⊆DENY must remain a single DENY card (no top-tree overlay)."""
    hit = evaluate_tool_call("host", {"action": "shell", "command": "rm -rf /"})
    assert hit is not None
    assert hit.verdict is BreakerVerdict.DENY
    assert hit.rule_id == "destructive.rm_root"
