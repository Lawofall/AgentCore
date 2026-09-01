"""Outlet directory inventory for ``<工作区>`` fact lines."""

from __future__ import annotations

from pathlib import Path

from agentcore.runtime.context.outlet_inventory import (
    OUTLET_DIRS,
    OutletDirListing,
    collect_outlet_inventory,
    format_outlet_line,
    format_outlet_suffix,
)
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from agentcore.workspace.stage_dirs import REVIEWS_DIR


def test_format_outlet_suffix_empty_and_names():
    assert format_outlet_suffix(OutletDirListing()) == "（当前为空）"
    assert (
        format_outlet_suffix(OutletDirListing(names=("a.md", "b.md")))
        == "（现有：a.md；b.md）"
    )
    many = tuple(f"f{i}.md" for i in range(10))
    assert "等共 10" in format_outlet_suffix(OutletDirListing(names=many))
    assert "列举未完" in format_outlet_suffix(
        OutletDirListing(names=("a.md",), truncated=True)
    )


def test_format_outlet_line_none_inventory_omits_line():
    line = format_outlet_line("约定文档出口·审查：", REVIEWS_DIR, None)
    assert line is None


def test_format_outlet_line_empty_inventory_omits_line():
    inv = {d: OutletDirListing() for d in OUTLET_DIRS}
    line = format_outlet_line("约定文档出口·审查：", REVIEWS_DIR, inv)
    assert line is None


def test_format_outlet_line_named_inventory():
    inv = {REVIEWS_DIR: OutletDirListing(names=("a.md",))}
    line = format_outlet_line("约定文档出口·审查：", REVIEWS_DIR, inv)
    assert line == f"约定文档出口·审查：`{REVIEWS_DIR}/`（现有：a.md）"


async def test_collect_outlet_inventory_lists_reviews(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    backend = ServerWorkspace(root=root, sandbox=SubprocessSandbox())
    await backend.write(f"{REVIEWS_DIR}/协作图审计-架构.md", "# a\n")
    await backend.write(f"{REVIEWS_DIR}/协作图审计-渲染链路.md", "# b\n")
    inv = await collect_outlet_inventory(backend)
    reviews = inv[REVIEWS_DIR]
    assert reviews.names == ("协作图审计-架构.md", "协作图审计-渲染链路.md")
    assert not reviews.truncated
    for other in OUTLET_DIRS:
        if other != REVIEWS_DIR:
            assert inv[other].names == ()
