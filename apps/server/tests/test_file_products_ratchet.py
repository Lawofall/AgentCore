"""落盘产物自报棘轮——「工具产了文件却没自报」必须在开发期变红。

## 为什么有这道棘轮

交付物台账（``files_touched`` / ``file_acceptance``）的事实口径是**工具自报**：落盘的
工具在 ``ToolResult.file_products`` 上声明自己真正写出的路径（契约见
``agentcore/tools/file_products.py``）。在此之前的口径是引擎按**工具名白名单**猜谁产了
文件——``md_to_docx`` 从注册那天起就没进过任何一份名单，四份副本之间的对齐测试全绿、线上
静默漏账数月，直到用户看见 AI 报出错误的文件路径才暴露。

**致命的从来不是名单少了谁，是默认不安全**：漏登记 = 静默通过。翻转成自报之后，同一个
形状会变成「新工具忘了填 ``file_products``」，所以这里加一道棘轮，让它必然变红：

1. **谁必须表态**（结构判据，不扫工具名、不猜语义）：``ToolCategory.FILESYSTEM``（拿
   工作区相对路径干活的那一族）∪ ``registration.execution_class``（在工作区 / 沙箱里跑
   东西的那一族）= 「落盘面」。落盘面上的每个工具都必须在 ``ToolRegistration.file_products``
   上显式声明契约（:class:`FileProductsContract`），漏声明 = ``UNDECLARED`` = 红。
2. **声明不许空口**：声明 ``SELF_REPORT`` 的工具，必须在本文件的 ``_CASES`` 里有一条**真跑**
   用例，在临时工作区里跑一次成功调用、逐字钉住它自报了什么。只声明不实现照样红。
3. **只读要显式**：只读工具声明 ``READ_ONLY``，可被 grep 检索到，而不是靠遗漏通过；且与
   审批面互锁——要了写盘授权（``GRANTABLE``）还自称只读，红。
4. **漏报也拦**：每条用例跑完会对比磁盘快照，本次调用新落到盘上的文件必须都在自报里。

只是开发期护栏：运行时（引擎 / 台账 / 模型）永远只读 ``ToolResult.file_products``，
不读这里的声明，更不认工具名。

## 红了怎么办

- **「落盘面工具未声明落盘产物契约」**：给它的 ``registration`` 加一行
  ``file_products=FileProductsContract.X``。四选一：真会产文件且已自报 → ``SELF_REPORT``
  （再来本文件加一条用例）；纯读不写 → ``READ_ONLY``；会动盘但落的不是交付物（只建目录 /
  只删文件 / 截图关键帧 / 跑构建的副产物 / ``git`` 换工作树）→ ``NO_PRODUCT``；会产文件但
  自报暂时没接 → ``SELF_REPORT_PENDING``（还要进 ``_PENDING_SELF_REPORT`` 待接清单 + 一条
  待接用例；该清单**已清空**，再往里加要先推翻那条断言）。
- **「声明了 SELF_REPORT 却没有真跑用例」**：往 ``_CASES`` 加一条——在 ``tmp_path`` 里造出
  最小场景、真跑一次成功调用、把期望产物 ``(path, kind, derived_from)`` 逐字写出来。
- **「待接工具已经自报了」**：恭喜，接上了。三步收尾：用例的 ``expect`` 从 ``None`` 改成
  期望产物、把名字从 ``_PENDING_SELF_REPORT`` 删掉、工具的声明改成 ``SELF_REPORT``。
- **「落了盘却没自报」**：工具在这条路径上写了文件但没写进 ``ToolResult.file_products``。
  补自报（报**真正落盘**的路径，不是模型请求的原始 path）。
- **「只读工具要了写盘授权」**：二选一——它其实会写（改声明 + 补自报），或者审批面标错了。
"""

from __future__ import annotations

import io
import shutil
import subprocess
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.tools.builtin.archive_create import ArchiveCreateTool
from agentcore.tools.builtin.archive_extract import ArchiveExtractTool
from agentcore.tools.builtin.code_execute import CodeExecuteTool
from agentcore.tools.builtin.file_ops import (
    FileAppendTool,
    FileBatchTool,
    FileCopyTool,
    FileMoveTool,
    FileWriteTool,
    StrReplaceTool,
    WriteSectionTool,
)
from agentcore.tools.builtin.git_ops import GitTool
from agentcore.tools.builtin.md_to_docx import MdToDocxTool
from agentcore.tools.builtin.md_to_pdf import MdToPdfTool
from agentcore.tools.builtin.web import download_url as download_mod
from agentcore.tools.builtin.web.download_url import DownloadUrlTool
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    FileProductsContract,
    declared_tool_schema,
    declared_tools,
    tool_registration,
)
from agentcore.tools.sandbox.protocol import ExecutionRequest, ExecutionResult
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace._paths import is_internal_zone_relpath
from agentcore.workspace.server import ServerWorkspace
from agentcore.workspace.write_claims import WriteCoordinator

# 待接清单：**只减不增**，现已清空（最后一项 ``git`` 定性为 NO_PRODUCT，见本文件末尾那条
# 断言）。列在这里 = 「会往工作区落盘、自报还没接」，并且下面必须有一条真跑用例证明它此刻
# 确实一件都没报（所以没法拿已接好的工具来凑数，也没法让接完的名字赖着不走）。往这里加一个
# 名字 = 明知故犯地放一支会漏账的笔进生产——先想清楚再加。
_PENDING_SELF_REPORT: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _FaceTool:
    """落盘面上的一个工具：名字 + 它的 schema + 它声明的产物契约。"""

    name: str
    schema: ToolSchema
    contract: FileProductsContract


def _landing_face() -> tuple[_FaceTool, ...]:
    """会往工作区落盘的工具面（结构判据，见模块 docstring §1）。"""
    face: list[_FaceTool] = []
    for cls in declared_tools():
        reg = tool_registration(cls)
        schema = declared_tool_schema(cls)
        if schema.category is ToolCategory.FILESYSTEM or reg.execution_class:
            face.append(_FaceTool(schema.name, schema, reg.file_products))
    return tuple(face)


def _names_with(*contracts: FileProductsContract) -> frozenset[str]:
    return frozenset(t.name for t in _landing_face() if t.contract in contracts)


def _ctx(root: Path, **fields) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=root, sandbox=SubprocessSandbox()),
        user_id="u",
        **fields,
    )


def _snapshot(root: Path) -> frozenset[str]:
    """工作区里现有文件的相对路径集合（系统噪音不算产物）。

    两类噪音排除在外：``.git/`` 是 git 自己的账本；``AgentCore/{index,trash,baselines}``
    是工作区内部区——任一次写盘都会顺带调度后台代码索引（落 ``index/code_search.db``），
    可逆删除会把文件挪进 ``trash/``。它们是可再生的派生态，``list_dir`` 本就按同一套规则
    剪掉、桌面镜像也不显示，永远不该被要求「自报」。
    """
    out: set[str] = set()
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if ".git" in rel.parts:
            continue
        posix = rel.as_posix()
        if is_internal_zone_relpath(posix):
            continue
        out.add(posix)
    return frozenset(out)


# --- 真跑用例 -------------------------------------------------------------------
# 每条用例在自己的 tmp 工作区里造最小场景、跑一次**成功**调用，由测试逐字比对它自报了
# 什么、以及本次调用新落到盘上的文件有没有被漏报。
#
# 场景（``setup``）与调用（``run``）分两段：源 md / 待搬的文件 / zip / git 仓都是**场景**
# 不是产物，磁盘快照的基线要在场景摆好之后才取——否则夹具会被当成「工具落了盘却没自报」，
# 这道棘轮就会对着自己的布景报警。


async def _run_file_write(root: Path, _mp: pytest.MonkeyPatch) -> ToolResult:
    return await FileWriteTool().execute({"path": "报告.md", "content": "# 标题"}, _ctx(root))


def _seed_report(root: Path) -> None:
    (root / "报告.md").write_text("# 标题\n", encoding="utf-8")


async def _run_file_append(root: Path, _mp: pytest.MonkeyPatch) -> ToolResult:
    return await FileAppendTool().execute({"path": "报告.md", "content": "\n更多"}, _ctx(root))


def _seed_src_txt(root: Path) -> None:
    (root / "src.txt").write_text("alpha\n", encoding="utf-8")


async def _run_str_replace(root: Path, _mp: pytest.MonkeyPatch) -> ToolResult:
    return await StrReplaceTool().execute(
        {"path": "src.txt", "old_string": "alpha", "new_string": "beta"}, _ctx(root)
    )


def _seed_page_html(root: Path) -> None:
    (root / "page.html").write_text(
        "<!-- SECTION:s0 START -->\n<!-- SECTION:s0 END -->\n", encoding="utf-8"
    )


async def _run_write_section(root: Path, _mp: pytest.MonkeyPatch) -> ToolResult:
    return await WriteSectionTool().execute(
        {"path": "page.html", "section": "s0", "content": "<p>x</p>"}, _ctx(root)
    )


async def _run_file_copy(root: Path, _mp: pytest.MonkeyPatch) -> ToolResult:
    return await FileCopyTool().execute(
        {"source": "src.txt", "destination": "out/copy.py"}, _ctx(root)
    )


async def _run_file_move(root: Path, _mp: pytest.MonkeyPatch) -> ToolResult:
    return await FileMoveTool().execute(
        {"source": "src.txt", "destination": "out/moved.docx"}, _ctx(root)
    )


def _seed_batch_sources(root: Path) -> None:
    (root / "a.md").write_text("batch\n", encoding="utf-8")
    (root / "keep.md").write_text("keep\n", encoding="utf-8")


async def _run_file_batch(root: Path, _mp: pytest.MonkeyPatch) -> ToolResult:
    return await FileBatchTool().execute(
        {
            "operations": [
                {"op": "move", "source": "a.md", "destination": "out/b.md"},
                {"op": "copy", "source": "keep.md", "destination": "out/keep.md"},
            ]
        },
        _ctx(root),
    )


def _seed_note_md(root: Path) -> None:
    (root / "note.md").write_text("# Hi\n\n你好世界\n", encoding="utf-8")


async def _run_md_to_docx(root: Path, _mp: pytest.MonkeyPatch) -> ToolResult:
    return await MdToDocxTool().execute({"path": "note.md"}, _ctx(root))


async def _run_md_to_pdf(root: Path, _mp: pytest.MonkeyPatch) -> ToolResult:
    return await MdToPdfTool().execute({"path": "note.md"}, _ctx(root))


def _seed_zip(root: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.md", "# hi")
        zf.writestr("docs/note.txt", "alpha")
    (root / "pkg.zip").write_bytes(buf.getvalue())


async def _run_archive_extract(root: Path, _mp: pytest.MonkeyPatch) -> ToolResult:
    return await ArchiveExtractTool().execute({"archive": "pkg.zip", "dest": "out"}, _ctx(root))


def _seed_pack_tree(root: Path) -> None:
    src = root / "src"
    src.mkdir()
    (src / "a.txt").write_text("alpha", encoding="utf-8")


async def _run_archive_create(root: Path, _mp: pytest.MonkeyPatch) -> ToolResult:
    return await ArchiveCreateTool().execute(
        {"sources": ["src"], "dest": "out/pkg.zip"}, _ctx(root)
    )


async def _run_download_url(root: Path, monkeypatch: pytest.MonkeyPatch) -> ToolResult:
    async def _fake_request(_client, _method, url, **_kwargs):
        return httpx.Response(
            200,
            content=b"hello-download",
            headers={"content-type": "text/plain", "content-length": "14"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(download_mod, "_safe_request", _fake_request)
    return await DownloadUrlTool().execute(
        {"url": "https://example.com/file.bin", "path": "uploads/file.bin"}, _ctx(root)
    )


class _CopyOutBackend:
    """沙箱替身：只回一份带 copy-out 路径的执行结果（真跑 gVisor 不属于单测）。"""

    def __init__(self, written: list[str]) -> None:
        self._written = written

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        del request
        return ExecutionResult(
            success=True,
            stdout="done\n",
            stderr="",
            exit_code=0,
            duration_ms=5,
            written_files=list(self._written),
        )


async def _run_code_execute(root: Path, _mp: pytest.MonkeyPatch) -> ToolResult:
    del root
    backend = _CopyOutBackend(["out/a、b.md", "out/chart.png"])
    context = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=backend,  # type: ignore[arg-type]
        user_id="u",
    )
    return await CodeExecuteTool(location="server").execute(
        {"code": "make()", "language": "python"}, context
    )


@dataclass(frozen=True)
class _Case:
    """一条真跑用例。``expect=None`` = 待接（跑得通、但此刻一件都不报）。

    ``setup`` 摆场景、``run`` 只跑那一次调用——磁盘快照的基线在两者之间取，夹具才不会
    被算成本次调用的落盘。
    """

    tool: str
    run: Callable[[Path, pytest.MonkeyPatch], Awaitable[ToolResult]]
    expect: tuple[tuple[str, str, str | None], ...] | None
    setup: Callable[[Path], None] | None = None


_CASES: tuple[_Case, ...] = (
    _Case("file_write", _run_file_write, (("报告.md", "md", None),)),
    _Case("file_append", _run_file_append, (("报告.md", "md", None),), _seed_report),
    _Case("str_replace", _run_str_replace, (("src.txt", "txt", None),), _seed_src_txt),
    _Case(
        "write_section",
        _run_write_section,
        (("page.html", "html", None),),
        _seed_page_html,
    ),
    _Case("file_copy", _run_file_copy, (("out/copy.py", "code", None),), _seed_src_txt),
    _Case(
        "file_move", _run_file_move, (("out/moved.docx", "docx", None),), _seed_src_txt
    ),
    _Case(
        "file_batch",
        _run_file_batch,
        (("out/b.md", "md", None), ("out/keep.md", "md", None)),
        _seed_batch_sources,
    ),
    # 导出件：产物是 .docx / .pdf，入参那份 md 是它的源（``derived_from``），不是产物。
    _Case("md_to_docx", _run_md_to_docx, (("note.docx", "docx", "note.md"),), _seed_note_md),
    _Case("md_to_pdf", _run_md_to_pdf, (("note.pdf", "pdf", "note.md"),), _seed_note_md),
    _Case(
        "archive_extract",
        _run_archive_extract,
        (("out/readme.md", "md", None), ("out/docs/note.txt", "txt", None)),
        _seed_zip,
    ),
    _Case(
        "archive_create",
        _run_archive_create,
        (("out/pkg.zip", "archive", None),),
        _seed_pack_tree,
    ),
    _Case("download_url", _run_download_url, (("uploads/file.bin", "file", None),)),
    # 间接落盘（沙箱 copy-out）：报的是 copy-out 的 EXACT 路径，含中文顿号也不会被散文切错。
    _Case(
        "code_execute",
        _run_code_execute,
        (("out/a、b.md", "md", None), ("out/chart.png", "image", None)),
    ),
)


def test_landing_face_declares_its_file_products_contract():
    """落盘面上的每个工具都必须显式表态——漏声明不再是静默通过。"""
    undeclared = sorted(
        t.name for t in _landing_face() if t.contract is FileProductsContract.UNDECLARED
    )
    assert not undeclared, (
        f"落盘面工具未声明落盘产物契约：{undeclared}。\n"
        "给它的 registration 加一行 file_products=FileProductsContract.X："
        "会产文件且已自报 → SELF_REPORT（并来 tests/test_file_products_ratchet.py 加一条真跑"
        "用例）；纯读不写 → READ_ONLY；会动盘但落的不是交付物 → NO_PRODUCT；"
        "会产文件但自报还没接 → SELF_REPORT_PENDING（另需进待接清单 + 一条待接用例）。\n"
        "这道棘轮存在的原因见本测试模块 docstring：md_to_docx 就是靠「没人登记」静默漏账数月的。"
    )


def test_read_only_declaration_is_locked_to_the_approval_face():
    """要了写盘授权就不许自称只读——``READ_ONLY`` 不能当逃生舱。"""
    lying = sorted(
        t.name
        for t in _landing_face()
        if t.contract is FileProductsContract.READ_ONLY
        and t.schema.approval is not ToolApproval.NEVER
    )
    assert not lying, (
        f"这些工具声明 READ_ONLY 却要了写盘授权（approval≠NEVER）：{lying}。\n"
        "二选一：它其实会写盘 → 改声明（SELF_REPORT / NO_PRODUCT）并补自报；"
        "或者它真的只读 → 审批面标错了，改回 ToolApproval.NEVER。"
    )


def test_self_report_declarations_are_pinned_by_a_live_case():
    """声明 ``SELF_REPORT`` = 必须有一条真跑用例钉住自报内容，不许空口声明。"""
    declared = _names_with(FileProductsContract.SELF_REPORT)
    cased = frozenset(c.tool for c in _CASES if c.expect is not None)
    assert declared == cased, (
        f"声明 SELF_REPORT 但没有真跑用例：{sorted(declared - cased)}；"
        f"有真跑用例但没声明 SELF_REPORT：{sorted(cased - declared)}。\n"
        "前者请来 _CASES 加一条（造最小场景 → 真跑一次成功调用 → 逐字写出期望产物）；"
        "后者说明声明退化了，把工具的 registration 改回 SELF_REPORT。"
    )


def test_pending_self_report_ledger_only_shrinks():
    """待接清单只减不增：声明、清单、待接用例三者对齐，且下界已经压到空。"""
    declared = _names_with(FileProductsContract.SELF_REPORT_PENDING)
    pending_cases = frozenset(c.tool for c in _CASES if c.expect is None)
    assert declared == _PENDING_SELF_REPORT == pending_cases, (
        f"待接清单对不上：声明 SELF_REPORT_PENDING 的是 {sorted(declared)}，"
        f"清单里写的是 {sorted(_PENDING_SELF_REPORT)}，有待接用例的是 {sorted(pending_cases)}。\n"
        "接完自报了 → 三处一起收尾：用例 expect 填上期望产物、清单删名、声明改 SELF_REPORT。\n"
        "想新增一项待接 → 先想清楚：那等于明知故犯地放一支会漏账的笔进生产。"
    )
    assert not _PENDING_SELF_REPORT, (
        f"待接清单又长出了 {sorted(_PENDING_SELF_REPORT)}——它在 git 收尾后已清空，"
        "「只减不增」的下界就是空。\n"
        "先走这两条：真会产交付物 → 让工具自报（SELF_REPORT + 一条真跑用例）；"
        "只是动盘不产交付物 → 声明 NO_PRODUCT。\n"
        "两条都不成立才回来放宽这条断言，并写清为什么必须让一支会漏账的笔先进生产。"
    )


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c.tool)
async def test_tool_self_reports_what_it_landed(
    case: _Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """真跑一次成功调用：自报必须逐字对上，且本次新落盘的文件一件都不许漏。"""
    keep = tmp_path / "README.md"
    if not keep.exists():
        keep.write_text("desk\n", encoding="utf-8")
    if case.setup is not None:
        case.setup(tmp_path)
    before = _snapshot(tmp_path)
    result = await case.run(tmp_path, monkeypatch)
    appeared = _snapshot(tmp_path) - before

    assert result.success is True, f"{case.tool} 用例本身没跑成功：{result.error}"
    reported = [(p.path, p.kind, p.derived_from) for p in result.file_products]

    if case.expect is None:
        assert appeared, (
            f"{case.tool} 是待接项，但这次调用一个文件都没落盘——"
            "这条用例证明不了它是产文件的工具，请把场景改成真会落盘的那条路径。"
        )
        assert reported == [], (
            f"{case.tool} 已经会自报产物了（{reported}）——待接清单该缩短了：\n"
            "1) 把这条用例的 expect 从 None 改成期望产物；"
            "2) 从 _PENDING_SELF_REPORT 删掉这个名字；"
            "3) 工具的 registration 改成 file_products=FileProductsContract.SELF_REPORT。"
        )
        return

    assert reported == list(case.expect), (
        f"{case.tool} 自报的产物与期望不一致。\n实际：{reported}\n期望：{list(case.expect)}\n"
        "自报的 path 必须是**真正落盘**的路径（已过 sanitize），kind 由路径推断，"
        "derived_from 只在「本产物是某源文件的导出件」时填。"
    )
    unreported = sorted(appeared - {p.path for p in result.file_products})
    assert not unreported, (
        f"{case.tool} 把 {unreported} 落到了盘上却没自报——台账会漏账（正是 .docx 那次事故）。\n"
        "在 ToolResult.file_products 里补上这些路径；确实不该进台账的副产物，"
        "请说明理由并把它挪出工作区可见面。"
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _seed_git_branches(root: Path) -> None:
    """两条分支：``feature/work`` 上有 ``note.md``，当前停在没有它的 ``feature/base``。"""
    _git(root, "init", "-b", "feature/base")
    _git(root, "config", "user.email", "tester@example.com")
    _git(root, "config", "user.name", "Tester")
    (root / "base.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "base.md")
    _git(root, "commit", "-m", "base")
    _git(root, "checkout", "-b", "feature/work")
    (root / "note.md").write_text("note\n", encoding="utf-8")
    _git(root, "add", "note.md")
    _git(root, "commit", "-m", "note")
    _git(root, "checkout", "feature/base")
    assert not (root / "note.md").exists()


async def _run_git_checkout(root: Path) -> ToolResult:
    """切分支把 ``note.md`` 换回工作树——git 确实会让文件落到盘上（但不是本 run 的产物）。"""
    # Worker 上下文（协调通道在场即解除 CEO 写禁）；审批是引擎层的事，不在工具里。
    return await GitTool().execute(
        {"subcommand": "checkout", "branch": "feature/work"},
        _ctx(root, write_coordinator=WriteCoordinator()),
    )


async def test_git_swaps_the_worktree_but_lands_no_products(tmp_path: Path):
    """``git`` 换工作树 ≠ 产交付物：文件真换了，台账里一件都没有（定案，非待接）。

    台账语义是「本 run 产出的交付物」而不是「盘上多了什么」：checkout / pull / merge 落下的
    是别人或过去已提交的版本，一次切分支能带上千个 worker 根本没碰过的文件。更硬的理由在
    ``runtime/runs/executor/terminal.py``：对账用 ``files_touched`` 判有没有落盘产物
    （blocked vs partial）。若换工作树算落盘，一个毫无产出的 worker 只要切一次分支就能把
    无产物批次刷成有落盘——正是自报重设计要防的假装交付。想推翻这条定案，先答：那次假装交付怎么防。
    """
    if not shutil.which("git"):
        pytest.skip("git not installed")

    _seed_git_branches(tmp_path)
    before = _snapshot(tmp_path)
    result = await _run_git_checkout(tmp_path)

    assert result.success is True, f"git checkout 没跑成功：{result.error}"
    # 前提：这次调用确实换了工作树上的文件，否则下面那条断言什么都没钉住。
    assert "note.md" in (_snapshot(tmp_path) - before), (
        "git checkout 没把 note.md 换上工作树——场景失效了，请修夹具而不是删断言。"
    )
    assert result.file_products == [], (
        f"git 自报了产物 {[p.path for p in result.file_products]}，但它被定性为不产交付物。\n"
        "切分支带上来的是别人或过去已提交的版本，不是本 run 的产出；接进台账会让"
        "「切一次分支就把 blocked 刷成 delivered」重新成立（见本用例 docstring）。"
    )
    assert tool_registration(GitTool).file_products is FileProductsContract.NO_PRODUCT, (
        "git 的契约声明被改了。它不是待接项（SELF_REPORT_PENDING），是定案的 NO_PRODUCT："
        "会动盘，但落的不是本 run 产出的交付物。"
    )
