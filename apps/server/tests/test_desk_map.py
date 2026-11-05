"""Bounded desk map: outline / map densities, ignore rules, conversation marks."""

from __future__ import annotations

from pathlib import Path

from agentcore.runtime.context.conversation_edits import ConversationEdit
from agentcore.runtime.context.envelope_switches import (
    bind_envelope_omissions,
    reset_envelope_omissions,
)
from agentcore.runtime.resolve.prompt.envelope import (
    render_ceo_turn_envelope,
    render_worker_turn_envelope,
)
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.desk_map import (
    EMPTY_NOTE,
    LOCAL_NO_PEEK,
    MAP_ENTRY_BUDGET,
    MAP_HEADER,
    OUTLINE_TOP_MAX,
    OVERFLOW_HEADER,
    TRUNCATED_NOTE,
    build_desk_map,
    build_desk_map_at_root,
    render_desk_map,
    render_desk_map_with_marks,
    render_outline_line,
)
from agentcore.workspace.server import ServerWorkspace


def _ws(root: Path) -> ServerWorkspace:
    return ServerWorkspace(root=root, sandbox=SubprocessSandbox())


def _tree(root: Path) -> None:
    (root / "readme.md").write_text("hi", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("a", encoding="utf-8")
    (root / "src" / "pkg").mkdir()
    (root / "src" / "pkg" / "b.py").write_text("b", encoding="utf-8")
    (root / "src" / "pkg" / "deep").mkdir()
    (root / "src" / "pkg" / "deep" / "c.py").write_text("c", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "lib.js").write_text("x", encoding="utf-8")
    (root / "AgentCore").mkdir()
    (root / "AgentCore" / "index").mkdir()
    (root / "AgentCore" / "index" / "x.json").write_text("{}", encoding="utf-8")
    (root / "AgentCore" / "rules").mkdir()
    (root / "AgentCore" / "rules" / "always.md").write_text("r", encoding="utf-8")


async def test_outline_is_one_layer_and_hides_noise(tmp_path: Path):
    _tree(tmp_path)
    peeked = await build_desk_map_at_root(tmp_path, density="outline")
    names = {node.path for node in peeked.entries}
    assert "readme.md" in names
    assert "src" in names
    assert "AgentCore" in names
    assert "src/a.py" not in names
    assert "node_modules" not in names
    assert "AgentCore/index" not in names
    line = render_outline_line(peeked)
    assert "readme.md" in line
    assert "src/" in line
    assert "node_modules" not in line


async def test_map_is_budgeted_bfs_not_full_tree(tmp_path: Path):
    _tree(tmp_path)
    peeked = await build_desk_map(_ws(tmp_path), density="map")
    paths = {node.path for node in peeked.entries}
    assert "readme.md" in paths
    assert "src/a.py" in paths
    assert "src/pkg/b.py" in paths
    assert "src/pkg/deep/c.py" not in paths
    assert "node_modules" not in paths
    assert "AgentCore/index" not in paths
    text = render_desk_map(peeked)
    assert text.startswith(MAP_HEADER)
    assert "a.py" in text
    assert "c.py" not in text


async def test_outline_truncation_is_visible(tmp_path: Path):
    for i in range(OUTLINE_TOP_MAX + 3):
        (tmp_path / f"f{i:02d}.txt").write_text("x", encoding="utf-8")
    peeked = await build_desk_map_at_root(tmp_path, density="outline")
    assert peeked.truncated
    assert len(peeked.entries) == OUTLINE_TOP_MAX
    assert "…" in render_outline_line(peeked)


async def test_map_entry_budget_truncates(tmp_path: Path):
    for i in range(MAP_ENTRY_BUDGET + 5):
        (tmp_path / f"n{i:02d}.txt").write_text("x", encoding="utf-8")
    peeked = await build_desk_map(_ws(tmp_path), density="map")
    assert peeked.truncated
    assert len(peeked.entries) == MAP_ENTRY_BUDGET
    assert TRUNCATED_NOTE in render_desk_map(peeked)


def test_local_outline_does_not_pretend_to_have_peeked():
    from agentcore.workspace.desk_map import empty_desk_map

    assert render_outline_line(None, local=True) == LOCAL_NO_PEEK
    assert (
        render_outline_line(empty_desk_map("outline", unavailable=True), local=True)
        == LOCAL_NO_PEEK
    )
    assert render_outline_line(empty_desk_map("outline")) == EMPTY_NOTE


def test_marks_land_on_the_tree_leftover_is_not_a_second_census():
    from agentcore.workspace.desk_map import DeskMap, DeskMapNode

    desk = DeskMap(
        density="map",
        entries=(
            DeskMapNode(path="src", is_dir=True, depth=1),
            DeskMapNode(path="src/a.py", is_dir=False, depth=2),
            DeskMapNode(path="readme.md", is_dir=False, depth=1),
        ),
        truncated=False,
        empty=False,
    )
    marks = [
        ConversationEdit(path="src/a.py", label="写过"),
        ConversationEdit(path="gone.md", label="已删除"),
        ConversationEdit(path="readme.md", label="更新"),
    ]
    tree, leftover = render_desk_map_with_marks(desk, marks)
    assert "a.py（写过）" in tree
    assert "readme.md（更新）" in tree
    assert "gone.md" not in tree
    assert leftover.startswith(OVERFLOW_HEADER)
    assert "- gone.md（已删除）" in leftover
    assert "本对话：" not in leftover
    assert "另有" not in leftover


def test_envelope_puts_map_before_leftover_and_workers_get_no_marks():
    facts = "<工作区>\n执行：云端\n</工作区>"
    tree = f"{MAP_HEADER}\nreadme.md（写过）"
    leftover = f"{OVERFLOW_HEADER}\n- gone.md（已删除）"
    ceo = render_ceo_turn_envelope(
        workspace_context=facts,
        desk_map=tree,
        conversation_edits=leftover,
        include_runtime=False,
    )
    worker = render_worker_turn_envelope(
        workspace_context=facts,
        desk_map=f"{MAP_HEADER}\nreadme.md",
        include_runtime=False,
    )
    assert ceo.index("执行：云端") < ceo.index(MAP_HEADER) < ceo.index("gone.md") < ceo.index("</工作区>")
    assert "readme.md（写过）" in ceo
    assert MAP_HEADER in worker
    assert OVERFLOW_HEADER not in worker
    assert "gone.md" not in worker


def test_omitting_desk_drops_the_map_keeps_leftover():
    token = bind_envelope_omissions(["desk"])
    try:
        text = render_ceo_turn_envelope(
            workspace_context="<工作区>\n执行：云端\n桌：白板\n</工作区>",
            desk_map=f"{MAP_HEADER}\nreadme.md",
            conversation_edits=f"{OVERFLOW_HEADER}\n- gone.md（已删除）",
            include_runtime=False,
        )
    finally:
        reset_envelope_omissions(token)
    assert MAP_HEADER not in text
    assert "gone.md" in text
    assert "执行：云端" in text
