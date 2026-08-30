"""Tests for the CEO workspace overview (``runtime.context.workspace_overview``).

Pins the ``<工作区文件>`` contract: best-effort (no backend / failing /
index-less → "" so the caller omits the block), empty workspace → guidance hint,
sparse listing (attachments + scratch; project shared trees summarized), and
bounded by BOTH a file count and a char budget.
"""

from agentcore.runtime.context import build_workspace_overview
from agentcore.runtime.context.workspace_overview import (
    OVERVIEW_CHAR_BUDGET,
    OVERVIEW_MAX_FILES,
)


class _FakeBackend:
    """Minimal WorkspaceBackend stand-in answering only ``index_files``."""

    def __init__(self, paths: list[str], *, fail: bool = False) -> None:
        self._paths = paths
        self._fail = fail

    async def index_files(self, cap: int | None = None, *, order: str = "path"):
        assert order == "recent"  # the overview asks for newest-first
        if self._fail:
            raise RuntimeError("backend unavailable")
        return list(self._paths), False


async def test_none_backend_yields_empty():
    assert await build_workspace_overview(None) == ""


async def test_empty_workspace_yields_guidance_hint():
    out = await build_workspace_overview(_FakeBackend([]))
    assert out.startswith("<工作区文件>")
    assert "工作区当前为空" in out
    assert "会话云端草稿" in out
    assert "不是本机或已打开的仓库工程" in out
    assert "file_list" in out
    # Environment mismatch guidance moved to ``<工作区>`` — no guessing prose.
    assert "云端/本地工作区未对齐" not in out
    assert "<工作区>" in out


async def test_listing_failure_degrades_to_empty():
    assert await build_workspace_overview(_FakeBackend([], fail=True)) == ""


async def test_backend_without_index_support_yields_empty():
    assert await build_workspace_overview(object()) == ""  # no index_files attr


async def test_lists_files_in_backend_order_under_caps():
    paths = ["报告.md", "data/input.csv", "src/main.py"]
    out = await build_workspace_overview(_FakeBackend(paths))
    assert out.startswith("<工作区文件>")
    assert out.rstrip().endswith("</工作区文件>")
    for p in paths:
        assert f"- {p}" in out
        assert "工作区已有" in out
    # Order preserved (backend already sorted newest-first).
    assert out.index("报告.md") < out.index("input.csv") < out.index("main.py")
    assert "另有" not in out  # nothing elided under the caps


async def test_labels_attachments():
    out = await build_workspace_overview(
        _FakeBackend(["attachments/a.pdf", "notes.md"])
    )
    assert "attachments/a.pdf（附件·含历轮）" in out
    assert "notes.md（工作区已有）" in out


async def test_project_mode_summarizes_shared_files():
    # Newest-first: first 5 non-attachments kept as 最近触达; rest summarized.
    paths = [f"src/f{i}.py" for i in range(12)]
    out = await build_workspace_overview(
        _FakeBackend(paths), shared_workspace=True
    )
    listed = [ln for ln in out.splitlines() if ln.startswith("- ")]
    assert len(listed) == 5
    assert "最近触达" in out
    assert "另有 7 个文件，需要时用 file_list / grep" in out
    assert "attachments/" not in out or "附件" in out  # no attachment rows here


async def test_project_mode_keeps_attachments_and_summarizes_rest():
    paths = ["attachments/brief.md", *[f"lib/{i}.py" for i in range(10)]]
    out = await build_workspace_overview(
        _FakeBackend(paths), shared_workspace=True
    )
    assert "attachments/brief.md（附件·含历轮）" in out
    assert "另有 5 个文件，需要时用 file_list / grep" in out


async def test_count_cap_elides_remaining():
    paths = [f"f{i}.py" for i in range(OVERVIEW_MAX_FILES + 10)]
    out = await build_workspace_overview(_FakeBackend(paths))
    listed = [ln for ln in out.splitlines() if ln.startswith("- ")]
    assert len(listed) == OVERVIEW_MAX_FILES
    assert "另有 10 个文件" in out


async def test_char_budget_binds_before_count():
    # Long paths blow the char budget well before the 40-file count cap.
    paths = [f"deep/nested/dir/{'x' * 180}/file{i}.py" for i in range(OVERVIEW_MAX_FILES)]
    out = await build_workspace_overview(_FakeBackend(paths))
    listed = [ln for ln in out.splitlines() if ln.startswith("- ")]
    assert len(listed) < OVERVIEW_MAX_FILES  # budget bound first
    body_chars = sum(len(ln) + 1 for ln in listed)
    assert body_chars <= OVERVIEW_CHAR_BUDGET
    assert "另有" in out
