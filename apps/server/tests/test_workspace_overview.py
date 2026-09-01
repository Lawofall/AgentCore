"""Tests for the CEO workspace file index (``runtime.context.workspace_overview``).

Pins the untagged body spliced into ``<工作区>``: best-effort (no backend / failing /
index-less → "" so the caller omits the 文件节), empty workspace → ``文件：空``
(no tool HOW, no cloud/local essay), sparse listing, and bounded by BOTH a file
count and a char budget. The second XML tag ``<工作区文件>`` is gone.
"""

from agentcore.runtime.context import attach_workspace_file_index, build_workspace_overview
from agentcore.runtime.context.workspace_overview import (
    FILE_INDEX_EMPTY,
    OVERVIEW_CHAR_BUDGET,
    OVERVIEW_MAX_FILES,
)


class _FakeBackend:
    """Minimal WorkspaceBackend stand-in answering ``index_files`` and optional convention files."""

    def __init__(
        self,
        paths: list[str],
        *,
        fail: bool = False,
        files: dict[str, str] | None = None,
    ) -> None:
        self._paths = paths
        self._fail = fail
        self._files = files or {}

    async def index_files(self, cap: int | None = None, *, order: str = "path"):
        assert order == "recent"  # the overview asks for newest-first
        if self._fail:
            raise RuntimeError("backend unavailable")
        return list(self._paths), False

    async def exists(self, path: str) -> bool:
        return path in self._files

    async def read(self, path: str) -> str:
        if path not in self._files:
            raise FileNotFoundError(path)
        return self._files[path]


async def test_none_backend_yields_empty():
    assert await build_workspace_overview(None) == ""


async def test_empty_workspace_yields_empty_file_fact():
    out = await build_workspace_overview(_FakeBackend([]))
    assert out == FILE_INDEX_EMPTY
    assert "file_list" not in out
    assert "会话云端草稿" not in out
    assert "<工作区文件>" not in out
    assert "<工作区>" not in out


async def test_listing_failure_degrades_to_empty():
    assert await build_workspace_overview(_FakeBackend([], fail=True)) == ""


async def test_backend_without_index_support_yields_empty():
    assert await build_workspace_overview(object()) == ""  # no index_files attr


async def test_lists_files_in_backend_order_under_caps():
    paths = ["报告.md", "data/input.csv", "src/main.py"]
    out = await build_workspace_overview(_FakeBackend(paths))
    assert out.startswith("文件：")
    assert "<工作区文件>" not in out
    for p in paths:
        assert f"- {p}" in out
        assert "工作区已有" in out
    assert out.index("报告.md") < out.index("input.csv") < out.index("main.py")
    assert "另有" not in out
    assert "必须" not in out
    assert "file_read" not in out


async def test_labels_attachments():
    out = await build_workspace_overview(
        _FakeBackend(["attachments/a.pdf", "notes.md"])
    )
    assert "attachments/a.pdf（附件·含历轮）" in out
    assert "notes.md（工作区已有）" in out


async def test_project_mode_summarizes_shared_files():
    paths = [f"src/f{i}.py" for i in range(12)]
    out = await build_workspace_overview(
        _FakeBackend(paths), shared_workspace=True
    )
    listed = [ln for ln in out.splitlines() if ln.startswith("- ")]
    assert len(listed) == 5
    assert "最近触达" in out
    assert "另有 7 个文件" in out
    assert "file_list" not in out
    assert "attachments/" not in out or "附件" in out


async def test_project_mode_keeps_attachments_and_summarizes_rest():
    paths = ["attachments/brief.md", *[f"lib/{i}.py" for i in range(10)]]
    out = await build_workspace_overview(
        _FakeBackend(paths), shared_workspace=True
    )
    assert "attachments/brief.md（附件·含历轮）" in out
    assert "另有 5 个文件" in out
    assert "file_list" not in out


async def test_count_cap_elides_remaining():
    paths = [f"f{i}.py" for i in range(OVERVIEW_MAX_FILES + 10)]
    out = await build_workspace_overview(_FakeBackend(paths))
    listed = [ln for ln in out.splitlines() if ln.startswith("- ")]
    assert len(listed) == OVERVIEW_MAX_FILES
    assert "另有 10 个文件" in out


async def test_char_budget_binds_before_count():
    paths = [f"deep/nested/dir/{'x' * 180}/file{i}.py" for i in range(OVERVIEW_MAX_FILES)]
    out = await build_workspace_overview(_FakeBackend(paths))
    listed = [ln for ln in out.splitlines() if ln.startswith("- ")]
    assert len(listed) < OVERVIEW_MAX_FILES
    body_chars = sum(len(ln) + 1 for ln in listed)
    assert body_chars <= OVERVIEW_CHAR_BUDGET
    assert "另有" in out


async def test_package_json_alone_does_not_inject_fingerprint():
    out = await build_workspace_overview(
        _FakeBackend(
            ["package.json"],
            files={"package.json": '{"name":"whiteboard"}'},
        )
    )
    assert "工程约定" not in out
    assert "当前工作区工程概览" not in out
    assert "常用命令" not in out
    assert "typescript" not in out.lower()
    assert "文件：" in out
    assert "package.json" in out


def test_attach_inserts_before_workspace_close():
    facts = "<工作区>\n执行位置：用户本机\n</工作区>"
    out = attach_workspace_file_index(facts, FILE_INDEX_EMPTY)
    assert out == "<工作区>\n执行位置：用户本机\n文件：空\n</工作区>"
    assert out.count("<工作区>") == 1
    assert "<工作区文件>" not in out


def test_attach_noops_without_facts_or_index():
    facts = "<工作区>\n执行位置：用户本机\n</工作区>"
    assert attach_workspace_file_index(facts, "") == facts
    assert attach_workspace_file_index("", FILE_INDEX_EMPTY) == ""
    assert attach_workspace_file_index("no tag here", FILE_INDEX_EMPTY) == "no tag here"


async def test_convention_file_is_name_pointer_not_excerpt_or_fingerprint():
    out = await build_workspace_overview(
        _FakeBackend(
            ["src/main.ts"],
            files={
                "AGENTS.md": (
                    "WhiteBoard 白板开发工程（TS monorepo 内核 + 规划中的 React UI 壳）。"
                    "常用命令 pnpm test / pnpm dev。"
                ),
                "package.json": '{"name":"whiteboard","packageManager":"pnpm@9.0.0"}',
            },
        )
    )
    assert "工程约定：`AGENTS.md`" in out
    assert "文件：" in out
    assert "src/main.ts" in out
    assert "WhiteBoard" not in out
    assert "工程约定摘录" not in out
    assert "当前工作区工程概览" not in out
    assert "常用命令" not in out
    assert "file_read" not in out


async def test_convention_pointer_survives_index_failure():
    out = await build_workspace_overview(
        _FakeBackend([], fail=True, files={"CLAUDE.md": "do not dump this body"})
    )
    assert out == "工程约定：`CLAUDE.md`"
    assert "do not dump" not in out
