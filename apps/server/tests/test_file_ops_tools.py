"""Tests for the file_write, file_delete and file_move tools (mutating file ops).

Hermetic: every test runs against a throwaway ``ServerWorkspace`` rooted at
``tmp_path`` and inspects the real on-disk result, mirroring the str_replace tool
tests. These tools are thin shells, so the focus is argument handling and the
typed-failure → user-message mapping (the heavy I/O lives in the backend).
"""

from dataclasses import replace
from pathlib import Path

from agentcore.tools.builtin.file_ops import (
    FileAppendTool,
    FileBatchTool,
    FileCopyTool,
    FileDeleteTool,
    FileListTool,
    FileMoveTool,
    FileReadTool,
    FileWriteTool,
    MkdirTool,
    StrReplaceTool,
    expand_brace_globs,
)
from agentcore.tools.protocol import ToolContext, isolate_file_read_ceiling
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from agentcore.workspace.stage_dirs import REVIEWS_PREFIX


def _ctx(workspace: Path, *, agent_id: str = "a") -> ToolContext:
    # These tests are not empty-desk cases. A visible user file keeps project-shell
    # strip from rewriting nested paths the assertions pin.
    keep = workspace / "README.md"
    if not keep.exists():
        keep.write_text("desk\n", encoding="utf-8")
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id=agent_id,
        backend=ServerWorkspace(root=workspace, sandbox=SubprocessSandbox()),
        user_id="u",
    )


def _assert_same_window_hit(result, *, path: str) -> None:
    assert result.success is True
    assert not result.contract_failure
    out = result.output or ""
    assert f"`{path}`" in out
    assert "已多次读取" in out
    assert "不重复灌入全文" in out
    assert "已有正文" in out
    assert "勿再读" in out


# --- file_write ---


async def test_write_creates_file(tmp_path: Path):
    result = await FileWriteTool().execute(
        {"path": "notes/report.md", "content": "# Hi"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert (tmp_path / "notes" / "report.md").read_text(encoding="utf-8") == "# Hi"


async def test_write_rejects_cleared_stub_content(tmp_path: Path):
    """过渡硬拒：content=[已清理] / 已落盘摘要不得写盘；正常长文仍成功。"""
    target = tmp_path / "doc.md"
    target.write_text("keep-me", encoding="utf-8")

    stub = await FileWriteTool().execute(
        {"path": "doc.md", "content": "[已清理]"}, _ctx(tmp_path)
    )
    assert stub.success is False
    assert stub.contract_failure is True
    assert "不能写入磁盘" in (stub.error or "")
    assert target.read_text(encoding="utf-8") == "keep-me"

    landed = await FileWriteTool().execute(
        {
            "path": "doc.md",
            "_landed_summary": "【已落盘摘要·只读】file_write 已成功写入",
            "status": "landed",
        },
        _ctx(tmp_path),
    )
    assert landed.success is False
    assert landed.contract_failure is True
    assert target.read_text(encoding="utf-8") == "keep-me"

    # Prose mentioning 已清理 must not be blocked.
    prose = "本节已清理历史遗留问题。" + ("正文。" * 200)
    ok = await FileWriteTool().execute({"path": "doc.md", "content": prose}, _ctx(tmp_path))
    assert ok.success is True
    assert target.read_text(encoding="utf-8") == prose


async def test_str_replace_rejects_cleared_stub_new_string(tmp_path: Path):
    (tmp_path / "notes.md").write_text("alpha\nbeta\n", encoding="utf-8")
    result = await StrReplaceTool().execute(
        {
            "path": "notes.md",
            "old_string": "alpha",
            "new_string": "[已清理·须重填]",
        },
        _ctx(tmp_path),
    )
    assert result.success is False
    assert result.contract_failure is True
    assert (tmp_path / "notes.md").read_text(encoding="utf-8") == "alpha\nbeta\n"


async def test_write_allows_substantial_overwrite(tmp_path: Path):
    """成篇 md/html 整盖允许（不再硬拒）；磁盘被新内容覆盖。"""
    body = "成篇正文。" * 80  # well over substantial threshold
    target = tmp_path / "site" / "index.html"
    target.parent.mkdir(parents=True)
    target.write_text(body, encoding="utf-8")
    new_body = "<html><body>rewrite complete page</body></html>"
    result = await FileWriteTool().execute(
        {"path": "site/index.html", "content": new_body},
        _ctx(tmp_path),
    )
    assert result.success is True
    assert target.read_text(encoding="utf-8") == new_body


async def test_write_allows_tiny_overwrite(tmp_path: Path):
    (tmp_path / "stub.txt").write_text("tiny", encoding="utf-8")
    result = await FileWriteTool().execute(
        {"path": "stub.txt", "content": "still small"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert (tmp_path / "stub.txt").read_text(encoding="utf-8") == "still small"


async def test_write_allows_non_empty_code_overwrite(tmp_path: Path):
    """非空代码整盖允许（优先局部劝导；不再硬拒）。"""
    body = "export function TopBar() {\n  return <header>App</header>;\n}\n"
    target = tmp_path / "src" / "TopBar.tsx"
    target.parent.mkdir(parents=True)
    target.write_text(body, encoding="utf-8")
    rewritten = "export function TopBar() {\n  return <header>New</header>;\n}\n"
    result = await FileWriteTool().execute(
        {"path": "src/TopBar.tsx", "content": rewritten},
        _ctx(tmp_path),
    )
    assert result.success is True
    assert target.read_text(encoding="utf-8") == rewritten


async def test_write_allows_css_js_overwrite(tmp_path: Path):
    """非空 css/js 整盖成功。"""
    css_old = "body { color: red; }\n" + "/* pad */\n" * 20
    js_old = "export const x = 1;\n" + "// pad\n" * 20
    (tmp_path / "styles").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "styles" / "main.css").write_text(css_old, encoding="utf-8")
    (tmp_path / "scripts" / "app.js").write_text(js_old, encoding="utf-8")
    css_new = "body { color: blue; }\n"
    js_new = "export const x = 2;\n"
    css_r = await FileWriteTool().execute(
        {"path": "styles/main.css", "content": css_new}, _ctx(tmp_path)
    )
    js_r = await FileWriteTool().execute(
        {"path": "scripts/app.js", "content": js_new}, _ctx(tmp_path)
    )
    assert css_r.success is True
    assert js_r.success is True
    assert (tmp_path / "styles" / "main.css").read_text(encoding="utf-8") == css_new
    assert (tmp_path / "scripts" / "app.js").read_text(encoding="utf-8") == js_new


async def test_write_allows_empty_code_shell(tmp_path: Path):
    """真·空壳（空白）代码文件仍可用 file_write 写入。"""
    target = tmp_path / "src" / "NewWidget.tsx"
    target.parent.mkdir(parents=True)
    target.write_text("   \n", encoding="utf-8")
    content = "export function NewWidget() {\n  return null;\n}\n"
    result = await FileWriteTool().execute(
        {"path": "src/NewWidget.tsx", "content": content},
        _ctx(tmp_path),
    )
    assert result.success is True
    assert target.read_text(encoding="utf-8") == content


async def test_write_allows_new_code_file(tmp_path: Path):
    content = "export const x = 1;\n"
    result = await FileWriteTool().execute(
        {"path": "src/fresh.ts", "content": content},
        _ctx(tmp_path),
    )
    assert result.success is True
    assert (tmp_path / "src" / "fresh.ts").read_text(encoding="utf-8") == content


async def test_write_rejects_empty_path(tmp_path: Path):
    # A worker that omits/empties ``path`` must get a crisp required-arg error — NOT
    # a backend write onto the workspace root dir (the real-world file_write failure:
    # path=None → root → "[Errno 13] Permission denied: <abs server path>").
    (tmp_path / "keep.txt").write_text("keep", encoding="utf-8")
    result = await FileWriteTool().execute({"path": "", "content": "x" * 5000}, _ctx(tmp_path))
    assert result.success is False
    assert "path 不能为空" in result.error
    # the root must be untouched (no clobber, no stray file)
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "keep"


async def test_write_rejects_missing_path(tmp_path: Path):
    result = await FileWriteTool().execute({"content": "body"}, _ctx(tmp_path))
    assert result.success is False
    assert "path 不能为空" in result.error


async def test_write_rejects_path_outside_workspace(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    result = await FileWriteTool().execute({"path": "../escaped.md", "content": "leak"}, _ctx(ws))
    assert result.success is False
    assert "超出了工作区范围" in result.error
    assert not (tmp_path / "escaped.md").exists()


async def test_write_normalizes_absolute_workspace_path(tmp_path: Path):
    # A worker passing an absolute /workspace/... path now succeeds (normalized at the
    # path-resolution seam) instead of failing OutsideWorkspace and retrying.
    result = await FileWriteTool().execute(
        {"path": "/workspace/research/x.md", "content": "hi"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert (tmp_path / "research" / "x.md").read_text(encoding="utf-8") == "hi"


async def test_outside_workspace_error_is_actionable(tmp_path: Path):
    # The rejection tells the model exactly how to fix it (relative path + example),
    # not just that the path was out of range. Cloud workspace also nudges the bind card.
    ws = tmp_path / "ws"
    ws.mkdir()
    result = await FileWriteTool().execute({"path": "../escaped.md", "content": "x"}, _ctx(ws))
    assert result.success is False
    assert "超出了工作区范围" in result.error
    assert "相对路径" in result.error
    assert "AgentCore/文档/research/report.md" in result.error
    assert "bind_local_folder" in result.error or "open_local_project" in result.error
    assert "open_local_project" in result.error or "本机传统" in result.error
    assert "导入到云" in result.error or "连接 Git" in result.error
    assert "合法非默认" in result.error or "非默认" in result.error
    assert "推荐" in result.error or "导入到云" in result.error


# --- file_read (Wave3 B same-path ceiling) ---


async def test_file_read_docx_transparent_extract(tmp_path: Path):
    from unittest.mock import patch

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "brief.docx").write_bytes(b"PK-fake-docx")
    ctx = _ctx(tmp_path)
    with patch(
        "agentcore.workspace.attachment_parse._convert_with_markitdown",
        return_value="# Brief\n\nHello from docx with enough alphanumeric body for scan.",
    ):
        result = await FileReadTool().execute({"path": "docs/brief.docx"}, ctx)
    assert result.success is True
    assert "Hello from docx" in (result.output or "")
    # Read-time extract must not write a *.md sidecar.
    assert not (tmp_path / "docs" / "brief.docx.md").exists()
    assert ctx.file_read_counts.get("docs/brief.docx") == 1


async def test_file_read_pdf_transparent_extract(tmp_path: Path):
    from unittest.mock import patch

    (tmp_path / "paper.pdf").write_bytes(b"%PDF-fake")
    with patch(
        "agentcore.workspace.attachment_parse._convert_with_markitdown",
        return_value="Abstract\n\nThis paper studies agents." + ("x" * 20),
    ):
        result = await FileReadTool().execute({"path": "paper.pdf"}, _ctx(tmp_path))
    assert result.success is True
    assert "This paper studies agents" in (result.output or "")
    assert not (tmp_path / "paper.pdf.md").exists()


async def test_file_read_xlsx_does_not_extract(tmp_path: Path):
    from unittest.mock import patch

    (tmp_path / "report.xlsx").write_bytes(b"PK\x03\x04")
    with patch(
        "agentcore.tools.builtin.file_ops.read._code_execute_assembled",
        return_value=True,
    ):
        result = await FileReadTool().execute({"path": "report.xlsx"}, _ctx(tmp_path))
    assert result.success is False
    assert result.error is not None
    assert "code_execute" in result.error
    assert not (tmp_path / "report.xlsx.md").exists()


async def test_file_read_table_without_code_execute_omits_tool_name(tmp_path: Path):
    from unittest.mock import patch

    (tmp_path / "report.xlsx").write_bytes(b"PK\x03\x04")
    (tmp_path / "upload.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    with patch(
        "agentcore.tools.builtin.file_ops.read._code_execute_assembled",
        return_value=False,
    ):
        xlsx = await FileReadTool().execute({"path": "report.xlsx"}, _ctx(tmp_path))
        csv = await FileReadTool().execute({"path": "upload.csv"}, _ctx(tmp_path))
    assert xlsx.success is False
    assert csv.success is False
    assert "code_execute" not in (xlsx.error or "")
    assert "code_execute" not in (csv.error or "")
    assert "手抄" in (xlsx.error or "")
    assert "结构报告" in (xlsx.error or "")
    assert "待跑" in (xlsx.error or "")
    assert "无法可靠处理" not in (xlsx.error or "")


async def test_file_read_landed_csv_is_readable(tmp_path: Path):
    """Worker 自产表格认落盘台账，可 file_read 回读；未入账的同扩展名仍拒。"""
    ctx = _ctx(tmp_path)
    written = await FileWriteTool().execute(
        {"path": "out/data.csv", "content": "name,n\nalice,1\n"},
        ctx,
    )
    assert written.success is True
    assert ctx.landed_artifact_kinds.get("out/data.csv") is not None

    ok = await FileReadTool().execute({"path": "out/data.csv"}, ctx)
    assert ok.success is True
    assert "alice,1" in (ok.output or "")

    (tmp_path / "foreign.csv").write_text("x,y\n9,8\n", encoding="utf-8")
    blocked = await FileReadTool().execute({"path": "foreign.csv"}, ctx)
    assert blocked.success is False
    assert "code_execute" not in (blocked.error or "")


async def test_file_read_scanned_pdf_notice(tmp_path: Path):
    from unittest.mock import patch

    (tmp_path / "scan.pdf").write_bytes(b"%PDF" + b"\x00" * 100)
    with patch(
        "agentcore.workspace.attachment_parse._convert_with_markitdown",
        return_value="   \n",
    ):
        result = await FileReadTool().execute({"path": "scan.pdf"}, _ctx(tmp_path))
    assert result.success is True
    out = result.output or ""
    assert "scanned" in out.lower() or "OCR" in out
    assert not (tmp_path / "scan.pdf.md").exists()


async def test_file_read_office_offset_limit_on_extracted_lines(tmp_path: Path):
    from unittest.mock import patch

    body = "\n".join(f"line-{i}" for i in range(1, 11))
    # enough alnum so not scanned
    body = body + "\n" + ("word " * 20)
    (tmp_path / "notes.docx").write_bytes(b"PK")
    ctx = _ctx(tmp_path)
    with patch(
        "agentcore.workspace.attachment_parse._convert_with_markitdown",
        return_value=body,
    ):
        result = await FileReadTool().execute(
            {"path": "notes.docx", "offset": 2, "limit": 3},
            ctx,
        )
    assert result.success is True
    out = result.output or ""
    assert "line-2" in out
    assert "line-4" in out
    assert "line-1" not in out
    assert "共 " in out
    assert ctx.file_read_counts.get("notes.docx", 0) == 0


async def test_file_read_prefers_existing_md_sidecar(tmp_path: Path):
    from unittest.mock import patch

    (tmp_path / "memo.docx").write_bytes(b"PK-original")
    (tmp_path / "memo.docx.md").write_text(
        "Sidecar text already prepared with enough body.\n", encoding="utf-8"
    )
    with patch(
        "agentcore.workspace.attachment_parse._convert_with_markitdown",
        side_effect=AssertionError("markitdown must not run when sidecar exists"),
    ):
        result = await FileReadTool().execute({"path": "memo.docx"}, _ctx(tmp_path))
    assert result.success is True
    assert "Sidecar text already prepared" in (result.output or "")


async def test_file_read_office_extract_failure_soft(tmp_path: Path):
    from unittest.mock import patch

    (tmp_path / "broken.docx").write_bytes(b"not-a-docx")
    with patch(
        "agentcore.workspace.attachment_parse._convert_with_markitdown",
        side_effect=RuntimeError("boom"),
    ):
        result = await FileReadTool().execute({"path": "broken.docx"}, _ctx(tmp_path))
    assert result.success is False
    assert result.error is not None
    assert "抽取" in result.error or "convert" in result.error


async def test_file_read_allows_up_to_same_path_max(tmp_path: Path):
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX

    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "CONTRACT.md").write_text("# CONTRACT\nbody", encoding="utf-8")
    ctx = _ctx(tmp_path)
    tool = FileReadTool()
    for i in range(FILE_READ_SAME_PATH_MAX):
        result = await tool.execute({"path": "site/CONTRACT.md"}, ctx)
        assert result.success is True, i
        assert "CONTRACT" in (result.output or "")
    assert ctx.file_read_counts.get("site/CONTRACT.md") == FILE_READ_SAME_PATH_MAX
    assert "再开窗" not in (result.output or "")
    assert "已在对话正文中" in (result.output or "")
    assert "勿再读此文件" not in (result.output or "")
    assert "已被清理" in (result.output or "")


async def test_file_read_same_window_hit_over_max(tmp_path: Path):
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX

    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "DESIGN.md").write_text("tokens", encoding="utf-8")
    (tmp_path / "site" / "OTHER.md").write_text("other body", encoding="utf-8")
    ctx = _ctx(tmp_path)
    tool = FileReadTool()
    for _ in range(FILE_READ_SAME_PATH_MAX):
        assert (await tool.execute({"path": "site/DESIGN.md"}, ctx)).success is True
    hit = await tool.execute({"path": "site/DESIGN.md"}, ctx)
    _assert_same_window_hit(hit, path="site/DESIGN.md")
    assert "tokens" not in (hit.output or "")
    other = await tool.execute({"path": "site/OTHER.md"}, ctx)
    assert other.success is True
    assert "other body" in (other.output or "")


async def test_file_read_same_path_limit_is_per_path(tmp_path: Path):
    (tmp_path / "a.md").write_text("A", encoding="utf-8")
    (tmp_path / "b.md").write_text("B", encoding="utf-8")
    ctx = _ctx(tmp_path)
    tool = FileReadTool()
    assert (await tool.execute({"path": "a.md"}, ctx)).success is True
    assert (await tool.execute({"path": "b.md"}, ctx)).success is True
    assert (await tool.execute({"path": "a.md"}, ctx)).success is True
    assert ctx.file_read_counts["a.md"] == 2
    assert ctx.file_read_counts["b.md"] == 1


async def test_file_read_reread_after_clear_allows_recovery(tmp_path: Path):
    """Cleared ledger: recovery succeeds and does not consume same-path quota."""
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX

    (tmp_path / "doc.md").write_text("# Doc\nbody", encoding="utf-8")
    (tmp_path / "peer.md").write_text("peer", encoding="utf-8")
    ctx = _ctx(tmp_path)
    tool = FileReadTool()
    for _ in range(FILE_READ_SAME_PATH_MAX):
        assert (await tool.execute({"path": "doc.md"}, ctx)).success is True
    ctx.file_read_verbatim_paths = frozenset()
    ctx.file_read_cleared_paths = frozenset({"doc.md"})
    ok = await tool.execute({"path": "doc.md"}, ctx)
    assert ok.success is True
    assert ctx.file_read_counts["doc.md"] == FILE_READ_SAME_PATH_MAX
    assert "再读授额已用尽" not in (ok.output or "")
    recovered = await tool.execute({"path": "doc.md"}, ctx)
    assert recovered.success is True
    assert "body" in (recovered.output or "")
    assert ctx.file_read_counts["doc.md"] == FILE_READ_SAME_PATH_MAX
    peer = await tool.execute({"path": "peer.md"}, ctx)
    assert peer.success is True


async def test_file_read_after_tool_clear_does_not_consume_quota(tmp_path: Path):
    """Read → tool_clear fully drops the path → re-read succeeds and does not count."""
    import json

    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
    from agentcore.runtime.engine.tool_clear import (
        apply_file_read_clear_state,
        project_cleared_window,
    )
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX

    def pair(call_id: str, path: str, result: str) -> list[LLMMessage]:
        return [
            LLMMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id=call_id,
                        function=ToolCallFunction(
                            name="file_read",
                            arguments=json.dumps({"path": path}),
                        ),
                    )
                ],
            ),
            LLMMessage(role="tool", content=result, tool_call_id=call_id),
        ]

    body = "\n".join(f"line-{i} " + ("x" * 80) for i in range(80))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "target.py").write_text(body + "\n", encoding="utf-8")
    (tmp_path / "src" / "other.py").write_text(body + "\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    tool = FileReadTool()
    outputs: list[str] = []
    for _ in range(FILE_READ_SAME_PATH_MAX):
        result = await tool.execute({"path": "src/target.py"}, ctx)
        assert result.success is True
        outputs.append(result.output or "")
        assert len(result.output or "") >= 100
    assert ctx.file_read_counts["src/target.py"] == FILE_READ_SAME_PATH_MAX

    msgs: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    for i, output in enumerate(outputs):
        msgs += pair(f"t{i}", "src/target.py", output)
    for i in range(2):
        other = await tool.execute({"path": "src/other.py"}, ctx)
        assert other.success is True
        msgs += pair(f"o{i}", "src/other.py", other.output or "")

    projected = project_cleared_window(
        msgs,
        clearable_tools=frozenset({"file_read"}),
        keep_recent=2,
        min_chars=100,
        summary_max_chars=0,
    )
    stub = next(m for m in projected if m.tool_call_id == "t0")
    assert (stub.content or "").startswith("[已清理")
    assert "path='src/target.py'" in (stub.content or "")
    assert "status=content_cleared" in (stub.content or "")
    assert "reread=allowed" in (stub.content or "")

    synced = apply_file_read_clear_state(
        ctx,
        msgs,
        investigation_tools=frozenset({"file_read"}),
        keep_recent=2,
        min_chars=100,
        summary_max_chars=0,
    )
    assert "src/target.py" not in (synced.file_read_verbatim_paths or frozenset())
    assert "src/target.py" in (synced.file_read_cleared_paths or frozenset())

    recovered = await tool.execute({"path": "src/target.py"}, synced)
    assert recovered.success is True
    assert "line-0" in (recovered.output or "")
    assert synced.file_read_counts["src/target.py"] == FILE_READ_SAME_PATH_MAX

    msgs += pair("t-recover", "src/target.py", recovered.output or "")
    after = apply_file_read_clear_state(
        synced,
        msgs,
        investigation_tools=frozenset({"file_read"}),
        keep_recent=2,
        min_chars=100,
        summary_max_chars=0,
    )
    assert "src/target.py" in (after.file_read_verbatim_paths or frozenset())
    assert "src/target.py" not in (after.file_read_cleared_paths or frozenset())
    blocked = await tool.execute({"path": "src/target.py"}, after)
    _assert_same_window_hit(blocked, path="src/target.py")
    assert after.file_read_counts["src/target.py"] == FILE_READ_SAME_PATH_MAX


async def test_file_read_reread_grant_overrides_verbatim(tmp_path: Path):
    """remaining > 0 allows full re-read even while verbatim body is still present."""
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX

    (tmp_path / "keep.md").write_text("keep-body", encoding="utf-8")
    (tmp_path / "next.md").write_text("next", encoding="utf-8")
    ctx = _ctx(tmp_path)
    tool = FileReadTool()
    for _ in range(FILE_READ_SAME_PATH_MAX):
        assert (await tool.execute({"path": "keep.md"}, ctx)).success is True
    ctx.file_read_verbatim_paths = frozenset({"keep.md"})
    ctx.file_read_reread_remaining["keep.md"] = 1
    ok = await tool.execute({"path": "keep.md"}, ctx)
    assert ok.success is True
    assert "keep-body" in (ok.output or "")
    assert ctx.file_read_reread_remaining["keep.md"] == 0
    assert ctx.file_read_counts["keep.md"] == FILE_READ_SAME_PATH_MAX + 1
    # Grant spent + verbatim still present → cheap hit (use existing body).
    blocked = await tool.execute({"path": "keep.md"}, ctx)
    _assert_same_window_hit(blocked, path="keep.md")
    next_ok = await tool.execute({"path": "next.md"}, ctx)
    assert next_ok.success is True


async def test_file_read_reread_refresh_after_citation_rework(tmp_path: Path):
    """Citation refresh grant overrides verbatim ceiling after prior grant spent."""
    from agentcore.runtime.engine.tool_clear import refresh_file_read_reread_grant
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX

    (tmp_path / "draft.md").write_text("# Draft\nbody with #r1", encoding="utf-8")
    ctx = _ctx(tmp_path)
    tool = FileReadTool()
    for _ in range(FILE_READ_SAME_PATH_MAX):
        assert (await tool.execute({"path": "draft.md"}, ctx)).success is True
    ctx.file_read_verbatim_paths = frozenset({"draft.md"})
    ctx.file_read_reread_issued["draft.md"] = True
    ctx.file_read_reread_remaining["draft.md"] = 0
    blocked = await tool.execute({"path": "draft.md"}, ctx)
    _assert_same_window_hit(blocked, path="draft.md")

    refresh_file_read_reread_grant(ctx, ["draft.md"])
    ok = await tool.execute({"path": "draft.md"}, ctx)
    assert ok.success is True
    assert ctx.file_read_reread_remaining["draft.md"] == 0


async def test_file_read_pagination_new_windows_never_hit_ceiling(tmp_path: Path):
    """Consecutive new offset/limit windows neither increment nor reject."""
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX

    lines = "\n".join(f"L{i}" for i in range(1, 81))
    (tmp_path / "page.md").write_text(lines + "\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    tool = FileReadTool()
    first = await tool.execute({"path": "page.md", "offset": 1, "limit": 5}, ctx)
    assert first.success is True
    assert ctx.file_read_counts.get("page.md", 0) == 0
    # Extending past the delivered span is a new range (not a repeat).
    expand = await tool.execute({"path": "page.md", "offset": 3, "limit": 8}, ctx)
    assert expand.success is True
    assert ctx.file_read_counts.get("page.md", 0) == 0
    # More new windows than MAX, each starting after the last delivered end.
    starts = list(range(11, 11 + 5 * (FILE_READ_SAME_PATH_MAX + 4), 5))
    for start in starts:
        result = await tool.execute(
            {"path": "page.md", "offset": start, "limit": 5}, ctx
        )
        assert result.success is True, start
        assert f"L{start}" in (result.output or "")
    assert ctx.file_read_counts.get("page.md", 0) == 0


async def test_file_read_repeat_delivered_window_hits_ceiling(tmp_path: Path):
    """Same already-delivered window + body still in projection → counts then cheap-hit."""
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX

    lines = "\n".join(f"L{i}" for i in range(1, 21))
    (tmp_path / "rep.md").write_text(lines + "\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    tool = FileReadTool()
    first = await tool.execute({"path": "rep.md", "offset": 3, "limit": 4}, ctx)
    assert first.success is True
    assert ctx.file_read_counts.get("rep.md", 0) == 0
    for i in range(FILE_READ_SAME_PATH_MAX):
        ok = await tool.execute({"path": "rep.md", "offset": 3, "limit": 4}, ctx)
        assert ok.success is True, i
        if i == FILE_READ_SAME_PATH_MAX - 1:
            assert "再开窗" not in (ok.output or "")
            assert "已在对话正文中" in (ok.output or "")
    assert ctx.file_read_counts["rep.md"] == FILE_READ_SAME_PATH_MAX
    blocked = await tool.execute({"path": "rep.md", "offset": 3, "limit": 4}, ctx)
    _assert_same_window_hit(blocked, path="rep.md")
    assert "L3" not in (blocked.output or "")
    # Subset of an already-delivered span also cheap-hits.
    subset = await tool.execute({"path": "rep.md", "offset": 4, "limit": 2}, ctx)
    _assert_same_window_hit(subset, path="rep.md")
    # A new range is still pagination and must return the body.
    nxt = await tool.execute({"path": "rep.md", "offset": 10, "limit": 3}, ctx)
    assert nxt.success is True
    assert "L10" in (nxt.output or "")
    assert ctx.file_read_counts["rep.md"] == FILE_READ_SAME_PATH_MAX


async def test_file_read_window_inside_prior_full_read_counts(tmp_path: Path):
    """After a fill-cap read, a window inside that delivered span counts."""
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX

    lines = "\n".join(f"L{i}" for i in range(1, 21))
    (tmp_path / "full.md").write_text(lines + "\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    tool = FileReadTool()
    assert (await tool.execute({"path": "full.md"}, ctx)).success is True
    assert ctx.file_read_counts.get("full.md") == 1
    for _ in range(FILE_READ_SAME_PATH_MAX - 1):
        assert (
            await tool.execute({"path": "full.md", "offset": 5, "limit": 3}, ctx)
        ).success is True
    assert ctx.file_read_counts["full.md"] == FILE_READ_SAME_PATH_MAX
    blocked = await tool.execute({"path": "full.md", "offset": 5, "limit": 3}, ctx)
    _assert_same_window_hit(blocked, path="full.md")


async def test_file_read_cleared_verbatim_window_does_not_hit(tmp_path: Path):
    """Projection dropped the body: repeating a delivered window is recovery."""
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX

    lines = "\n".join(f"L{i}" for i in range(1, 16))
    (tmp_path / "clr.md").write_text(lines + "\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    tool = FileReadTool()
    assert (await tool.execute({"path": "clr.md", "offset": 2, "limit": 3}, ctx)).success
    for _ in range(FILE_READ_SAME_PATH_MAX):
        assert (
            await tool.execute({"path": "clr.md", "offset": 2, "limit": 3}, ctx)
        ).success is True
    assert ctx.file_read_counts["clr.md"] == FILE_READ_SAME_PATH_MAX
    ctx.file_read_verbatim_paths = frozenset()
    ctx.file_read_cleared_paths = frozenset({"clr.md"})
    recovered = await tool.execute({"path": "clr.md", "offset": 2, "limit": 3}, ctx)
    assert recovered.success is True
    assert "L2" in (recovered.output or "")
    assert ctx.file_read_counts["clr.md"] == FILE_READ_SAME_PATH_MAX


async def test_file_read_reread_grant_allows_delivered_window(tmp_path: Path):
    """Grant remaining > 0: delivered-window re-read succeeds even at max."""
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX

    lines = "\n".join(f"L{i}" for i in range(1, 16))
    (tmp_path / "grant.md").write_text(lines + "\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    tool = FileReadTool()
    assert (await tool.execute({"path": "grant.md", "offset": 1, "limit": 4}, ctx)).success
    for _ in range(FILE_READ_SAME_PATH_MAX):
        assert (
            await tool.execute({"path": "grant.md", "offset": 1, "limit": 4}, ctx)
        ).success is True
    ctx.file_read_verbatim_paths = frozenset({"grant.md"})
    ctx.file_read_reread_remaining["grant.md"] = 1
    ok = await tool.execute({"path": "grant.md", "offset": 1, "limit": 4}, ctx)
    assert ok.success is True
    assert ctx.file_read_reread_remaining["grant.md"] == 0
    assert ctx.file_read_counts["grant.md"] == FILE_READ_SAME_PATH_MAX + 1
    blocked = await tool.execute({"path": "grant.md", "offset": 1, "limit": 4}, ctx)
    _assert_same_window_hit(blocked, path="grant.md")


async def test_file_read_write_success_allows_same_window_reread(tmp_path: Path):
    """Write clears counts + delivered ranges so the same window is not rejected."""
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX

    (tmp_path / "draft.md").write_text(
        "\n".join(f"L{i}" for i in range(1, 12)) + "\n", encoding="utf-8"
    )
    ctx = _ctx(tmp_path)
    tool = FileReadTool()
    assert (await tool.execute({"path": "draft.md", "offset": 2, "limit": 3}, ctx)).success
    for _ in range(FILE_READ_SAME_PATH_MAX):
        assert (
            await tool.execute({"path": "draft.md", "offset": 2, "limit": 3}, ctx)
        ).success is True
    blocked = await tool.execute({"path": "draft.md", "offset": 2, "limit": 3}, ctx)
    _assert_same_window_hit(blocked, path="draft.md")

    w = await FileWriteTool().execute(
        {"path": "draft.md", "content": "\n".join(f"N{i}" for i in range(1, 12)) + "\n"},
        ctx,
    )
    assert w.success is True
    assert ctx.file_read_counts.get("draft.md", -1) == 0
    assert "draft.md" not in ctx.file_read_delivered_ranges
    assert "draft.md" not in ctx.file_read_line_totals
    verify = await tool.execute({"path": "draft.md", "offset": 2, "limit": 3}, ctx)
    assert verify.success is True
    assert "N2" in (verify.output or "")
    assert ctx.file_read_counts.get("draft.md", 0) == 0


async def test_file_read_long_file_window_footer_and_output_limit(tmp_path: Path):
    """超行顶：编号窗 + 「共 N 行」+ 行顶页脚；无「中间省略」；output_limit 覆盖全文。"""
    from agentcore.tools.builtin.file_ops import FILE_READ_SAFETY_LINE_CAP

    total = FILE_READ_SAFETY_LINE_CAP + 50
    body = "\n".join(f"line-{i}" for i in range(1, total + 1)) + "\n"
    (tmp_path / "long.md").write_text(body, encoding="utf-8")
    result = await FileReadTool().execute({"path": "long.md"}, _ctx(tmp_path))
    assert result.success is True
    out = result.output or ""
    assert f"共 {total} 行" in out
    assert f"第 1–{FILE_READ_SAFETY_LINE_CAP} 行" in out
    assert "已达行顶" in out
    assert f"全文 {total} 行" not in out
    assert f"line-{FILE_READ_SAFETY_LINE_CAP}" in out
    assert f"line-{total}" not in out  # beyond window
    assert "中间省略" not in out
    assert "已保留首尾" not in out
    assert "系统视图截断" not in out
    assert result.output_limit is not None
    assert result.output_limit >= len(out)
    assert "     1|line-1" in out


async def test_file_read_800_lines_returns_full_text(tmp_path: Path):
    """800 行一次全文；未截断页脚「全文 N 行」。"""
    total = 800
    body = "\n".join(f"line-{i}" for i in range(1, total + 1)) + "\n"
    (tmp_path / "mid.md").write_text(body, encoding="utf-8")
    ctx = _ctx(tmp_path)
    result = await FileReadTool().execute({"path": "mid.md"}, ctx)
    assert result.success is True
    out = result.output or ""
    assert f"全文 {total} 行" in out
    assert "已达行顶" not in out
    assert "已达字符顶" not in out
    assert "     1|line-1" in out
    assert f"line-{total}" in out
    assert ctx.file_read_counts.get("mid.md") == 1
    assert result.output_limit is not None
    assert result.output_limit >= len(out)


async def test_file_read_char_cap_truncates_complete_lines(tmp_path: Path):
    """超 8 万字符：先到先停、完整行；页脚标明字符顶。"""
    from agentcore.tools.builtin.file_ops import FILE_READ_SAFETY_CHAR_CAP

    line = "x" * 1000
    total = 90
    (tmp_path / "wide.md").write_text("\n".join([line] * total) + "\n", encoding="utf-8")
    result = await FileReadTool().execute({"path": "wide.md"}, _ctx(tmp_path))
    assert result.success is True
    out = result.output or ""
    assert "已达字符顶" in out
    assert f"共 {total} 行" in out
    assert "中间省略" not in out
    assert "已保留首尾" not in out
    # 1001*n - 1 > cap → n > 79.92, so 79 complete lines.
    expected_end = 0
    chars = 0
    for n in range(1, total + 1):
        extra = len(line) if n == 1 else 1 + len(line)
        if chars + extra > FILE_READ_SAFETY_CHAR_CAP:
            break
        chars += extra
        expected_end = n
    assert expected_end == 79
    assert f"第 1–{expected_end} 行" in out
    assert f"{expected_end:>6}|{line}" in out
    assert f"{expected_end + 1:>6}|{line}" not in out
    for raw in out.splitlines():
        if "|" in raw and raw.rsplit("|", 1)[-1].startswith("x"):
            assert raw.rsplit("|", 1)[-1] == line
    assert result.output_limit is not None
    assert result.output_limit >= len(out)


async def test_file_read_oversized_single_line_kept_whole(tmp_path: Path):
    """单行超字符顶：整行留下并标字符顶，禁止半行静默切。"""
    from agentcore.tools.builtin.file_ops import FILE_READ_SAFETY_CHAR_CAP

    line = "y" * (FILE_READ_SAFETY_CHAR_CAP + 50)
    (tmp_path / "one.md").write_text(line + "\n", encoding="utf-8")
    result = await FileReadTool().execute({"path": "one.md"}, _ctx(tmp_path))
    assert result.success is True
    out = result.output or ""
    assert f"     1|{line}" in out
    assert "已达字符顶" in out
    assert "中间省略" not in out
    assert result.output_limit is not None
    assert result.output_limit >= len(out)


async def test_file_read_from_line1_fill_cap_counts_toward_ceiling(tmp_path: Path):
    """从第 1 行要满安全顶（双省 / 只传 offset=1 / 只传 limit=顶）计次。"""
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX
    from agentcore.tools.builtin.file_ops import FILE_READ_SAFETY_LINE_CAP

    (tmp_path / "cap.md").write_text("body\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    tool = FileReadTool()
    for _ in range(FILE_READ_SAME_PATH_MAX):
        assert (await tool.execute({"path": "cap.md"}, ctx)).success is True
    _assert_same_window_hit(
        await tool.execute({"path": "cap.md"}, ctx), path="cap.md"
    )
    _assert_same_window_hit(
        await tool.execute({"path": "cap.md", "offset": 1}, ctx), path="cap.md"
    )
    _assert_same_window_hit(
        await tool.execute({"path": "cap.md", "limit": FILE_READ_SAFETY_LINE_CAP}, ctx),
        path="cap.md",
    )
    _assert_same_window_hit(
        await tool.execute(
            {"path": "cap.md", "offset": 1, "limit": FILE_READ_SAFETY_LINE_CAP},
            ctx,
        ),
        path="cap.md",
    )
    # Covered window after fill-cap reads: already delivered + body present → cheap hit.
    covered = await tool.execute({"path": "cap.md", "offset": 1, "limit": 1}, ctx)
    _assert_same_window_hit(covered, path="cap.md")
    assert ctx.file_read_counts.get("cap.md") == FILE_READ_SAME_PATH_MAX


async def test_file_read_oversized_message_does_not_teach_offset_limit(tmp_path: Path):
    from agentcore.workspace.limits import WORKSPACE_READ_MAX_BYTES

    (tmp_path / "huge.txt").write_bytes(b"a" * (WORKSPACE_READ_MAX_BYTES + 1))
    result = await FileReadTool().execute({"path": "huge.txt"}, _ctx(tmp_path))
    assert result.success is False
    err = (result.error or "").lower()
    assert "offset" not in err
    assert "limit" not in err
    assert "mib" in err


async def test_file_read_office_default_uses_same_full_window(tmp_path: Path):
    from unittest.mock import patch

    from agentcore.tools.builtin.file_ops import FILE_READ_SAFETY_LINE_CAP

    body = "\n".join(f"line-{i} word extra" for i in range(1, 801))
    (tmp_path / "notes.docx").write_bytes(b"PK")
    ctx = _ctx(tmp_path)
    with patch(
        "agentcore.workspace.attachment_parse._convert_with_markitdown",
        return_value=body,
    ):
        result = await FileReadTool().execute({"path": "notes.docx"}, ctx)
    assert result.success is True
    out = result.output or ""
    assert "全文 800 行" in out
    assert "line-800" in out
    assert ctx.file_read_counts.get("notes.docx") == 1

    over = "\n".join(
        f"line-{i} word extra" for i in range(1, FILE_READ_SAFETY_LINE_CAP + 51)
    )
    (tmp_path / "big.docx").write_bytes(b"PK")
    with patch(
        "agentcore.workspace.attachment_parse._convert_with_markitdown",
        return_value=over,
    ):
        truncated = await FileReadTool().execute({"path": "big.docx"}, ctx)
    tout = truncated.output or ""
    assert truncated.success is True
    assert f"第 1–{FILE_READ_SAFETY_LINE_CAP} 行" in tout
    assert "已达行顶" in tout
    assert f"line-{FILE_READ_SAFETY_LINE_CAP + 50}" not in tout


def test_file_read_schema_teaches_default_full_read():
    from agentcore.tools.builtin.file_ops import FILE_READ_SAFETY_LINE_CAP

    schema = FileReadTool().schema
    limit = schema.parameters["properties"]["limit"]
    assert limit["maximum"] == FILE_READ_SAFETY_LINE_CAP
    assert "省略则尽量整读" in limit["description"]
    assert "超安全顶截断" in limit["description"]
    desc = schema.description
    assert "默认整读" in desc
    assert "grep" in desc or "code_search" in desc
    assert "开窗" in desc
    assert "默认不抽文本" in desc
    assert "请用 code_execute。" not in desc


async def test_write_hard_rejects_substantial_prose_with_omission(tmp_path: Path):
    """成篇体量 + 省略标记 → 硬拒（非 soft nudge）。"""
    body = ("完整段落内容填充字。" * 50) + "\n……（中间省略，已保留首尾）……\n" + ("尾段续写。" * 30)
    assert len(body.strip()) >= 400
    result = await FileWriteTool().execute(
        {"path": "essay.md", "content": body}, _ctx(tmp_path)
    )
    assert result.success is False
    assert result.contract_failure is True
    assert "省略标记" in (result.error or "")
    assert not (tmp_path / "essay.md").exists()


async def test_file_write_resets_read_ceiling_for_verify(tmp_path: Path):
    """Successful write zeros counts + refreshes grant so post-write verify reads work."""
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX

    (tmp_path / "draft.md").write_text("v1\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    tool = FileReadTool()
    for _ in range(FILE_READ_SAME_PATH_MAX):
        assert (await tool.execute({"path": "draft.md"}, ctx)).success is True
    blocked = await tool.execute({"path": "draft.md"}, ctx)
    _assert_same_window_hit(blocked, path="draft.md")

    w = await FileWriteTool().execute(
        {"path": "draft.md", "content": "v2 rewritten\n"}, ctx
    )
    assert w.success is True
    assert ctx.file_read_counts.get("draft.md", -1) == 0
    assert "draft.md" not in ctx.file_read_delivered_ranges
    assert "draft.md" not in ctx.file_read_line_totals
    assert ctx.file_read_reread_remaining.get("draft.md") == 1

    verify = await tool.execute({"path": "draft.md"}, ctx)
    assert verify.success is True
    assert "v2 rewritten" in (verify.output or "")
    assert ctx.file_read_counts.get("draft.md") == 1


async def test_str_replace_success_resets_read_ceiling(tmp_path: Path):
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX

    (tmp_path / "edit.md").write_text("hello world\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    tool = FileReadTool()
    for _ in range(FILE_READ_SAME_PATH_MAX):
        assert (await tool.execute({"path": "edit.md"}, ctx)).success is True
    _assert_same_window_hit(
        await tool.execute({"path": "edit.md"}, ctx), path="edit.md"
    )

    ok = await StrReplaceTool().execute(
        {"path": "edit.md", "old_string": "world", "new_string": "AgentCore"},
        ctx,
    )
    assert ok.success is True
    assert ctx.file_read_counts.get("edit.md", -1) == 0
    assert "edit.md" not in ctx.file_read_delivered_ranges
    assert ctx.file_read_reread_remaining.get("edit.md") == 1
    verify = await tool.execute({"path": "edit.md"}, ctx)
    assert verify.success is True
    assert "AgentCore" in (verify.output or "")


async def test_str_replace_failure_does_not_refresh_reread_grant(tmp_path: Path):
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX

    (tmp_path / "edit.md").write_text("hello world\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    tool = FileReadTool()
    for _ in range(FILE_READ_SAME_PATH_MAX):
        assert (await tool.execute({"path": "edit.md"}, ctx)).success is True
    ctx.file_read_reread_remaining["edit.md"] = 0
    ctx.file_read_reread_issued["edit.md"] = True
    before = dict(ctx.file_read_reread_remaining)
    before_counts = int(ctx.file_read_counts["edit.md"])

    fail = await StrReplaceTool().execute(
        {"path": "edit.md", "old_string": "no-such-token", "new_string": "x"},
        ctx,
    )
    assert fail.success is False
    assert ctx.file_read_counts["edit.md"] == before_counts
    assert ctx.file_read_reread_remaining.get("edit.md") == before.get("edit.md")


async def test_file_read_ceiling_does_not_retire_tool(tmp_path: Path):
    """Same-path cheap-hit is success and does not disable file_read."""
    from agentcore.runtime.loop_controller import LoopController, ToolAttempt
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX

    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "PRD.md").write_text("# PRD\n" + ("x" * 200), encoding="utf-8")
    (tmp_path / "attachments" / "SPEC.md").write_text("# SPEC\nok", encoding="utf-8")
    ctx = _ctx(tmp_path)
    tool = FileReadTool()
    for _ in range(FILE_READ_SAME_PATH_MAX):
        assert (await tool.execute({"path": "attachments/PRD.md"}, ctx)).success is True
    hit = await tool.execute({"path": "attachments/PRD.md"}, ctx)
    _assert_same_window_hit(hit, path="attachments/PRD.md")
    assert "retire_tools" not in (hit.metadata or {})

    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record(
        [
            ToolAttempt(
                "ceil",
                "file_read",
                success=True,
                error_summary="",
                meta=dict(hit.metadata),
            )
        ]
    )
    assert not c.tool_circuit_breaker()
    assert c.tool_failure_count("file_read") == 0

    other = await tool.execute({"path": "attachments/SPEC.md"}, ctx)
    assert other.success is True
    assert "SPEC" in (other.output or "")


async def test_file_read_missing_does_not_trip_circuit_breaker(tmp_path: Path):
    """PathNotFound (env / wrong path) must not warn or disable file_read."""
    from agentcore.runtime.loop_controller import LoopController, ToolAttempt

    tool = FileReadTool()
    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    for i in range(6):
        result = await tool.execute({"path": f"ghost/missing-{i}.md"}, _ctx(tmp_path))
        assert result.success is False
        assert result.contract_failure is True
        c.record(
            [
                ToolAttempt(
                    f"miss-{i}",
                    "file_read",
                    success=False,
                    error_summary=result.error or "",
                    contract_failure=result.contract_failure,
                    meta=dict(result.metadata or {}),
                )
            ]
        )
    cb = c.tool_circuit_breaker()
    assert cb.disabled == ()
    assert cb.warned == ()
    assert c.tool_failure_count("file_read") == 0


# --- file_append ---


async def test_append_creates_file_when_missing(tmp_path: Path):
    result = await FileAppendTool().execute(
        {"path": "draft.md", "content": "# Intro"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert (tmp_path / "draft.md").read_text(encoding="utf-8") == "# Intro"


async def test_append_adds_to_existing_file(tmp_path: Path):
    (tmp_path / "draft.md").write_text("# Intro", encoding="utf-8")
    result = await FileAppendTool().execute(
        {"path": "draft.md", "content": "\n\n## Section 2"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert (tmp_path / "draft.md").read_text(encoding="utf-8") == "# Intro\n\n## Section 2"


async def test_append_rejects_empty_path(tmp_path: Path):
    result = await FileAppendTool().execute({"path": "", "content": "x"}, _ctx(tmp_path))
    assert result.success is False
    assert "path 不能为空" in result.error


async def test_append_rejects_directory_target(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    result = await FileAppendTool().execute({"path": "pkg", "content": "x"}, _ctx(tmp_path))
    assert result.success is False
    assert "不是文件" in result.error


async def test_append_receipt_echoes_merged_tail(tmp_path: Path):
    # append 回执改为 artifact manifest（含 end_preview），免掉纯回读自检。
    (tmp_path / "draft.md").write_text("# Intro", encoding="utf-8")
    result = await FileAppendTool().execute(
        {"path": "draft.md", "content": "\n\n## Section 2"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert "## Section 2" in result.output  # end_preview / title_tree
    assert "artifact manifest" in result.output
    assert "优先用 manifest 验真" in result.output


async def test_write_receipt_notes_persisted(tmp_path: Path):
    # file_write 回执 = artifact manifest；优先 manifest 验真（非身份硬闸）。
    result = await FileWriteTool().execute(
        {"path": "report.md", "content": "# Hi\n\n## A\n"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert "artifact manifest" in result.output
    assert "content_sha256:" in result.output
    assert "title_tree:" in result.output
    assert "优先用 manifest 验真" in result.output
    assert "kind: skeleton" in result.output


async def test_write_receipt_reports_chars_not_bytes(tmp_path: Path):
    """规模按字符报：中文正文标成「字节」会让作者以为写少了、回读空转。"""
    body = "# 民事起诉状\n\n原告：昝雯，住青海省西宁市。\n"
    assert len(body.encode("utf-8")) != len(body)  # 中文下两口径必然分叉
    result = await FileWriteTool().execute(
        {"path": "诉状.md", "content": body}, _ctx(tmp_path)
    )
    assert result.success is True
    assert f"已写入 {len(body)} 字符到 诉状.md" in result.output
    assert f"chars: {len(body)}" in result.output
    assert "字节" not in result.output


async def test_write_prose_then_append_rejected(tmp_path: Path):
    """成篇 file_write 后同 path file_append 硬拒（Artifact-first）。"""
    prose = "# 报告\n\n" + ("这是实质正文段落。" * 50)  # well over substantial
    assert len(prose) >= 400
    ctx = _ctx(tmp_path)
    w = await FileWriteTool().execute({"path": "essay.md", "content": prose}, ctx)
    assert w.success is True
    assert "kind: prose" in w.output
    assert ctx.landed_artifact_kinds.get("essay.md") == "prose"
    blocked = await FileAppendTool().execute(
        {"path": "essay.md", "content": "\n\n## 续章\n更多。"}, ctx
    )
    assert blocked.success is False
    assert blocked.contract_failure is True
    assert "拒绝追加" in (blocked.error or "")
    assert "str_replace" in (blocked.error or "")
    assert "骨架填空" in (blocked.error or "") or "骨架" in (blocked.error or "")
    assert "应先短骨架" not in (blocked.error or "")
    assert "长交付物应先" not in (blocked.error or "")


async def test_write_skeleton_then_append_allowed(tmp_path: Path):
    skeleton = "# 报告\n\n## 一\n\n## 二\n\n<!-- OUTLINE -->\n"
    ctx = _ctx(tmp_path)
    w = await FileWriteTool().execute({"path": "report.md", "content": skeleton}, ctx)
    assert w.success is True
    assert ctx.landed_artifact_kinds.get("report.md") == "skeleton"
    a = await FileAppendTool().execute(
        {"path": "report.md", "content": "\n\n## 一\n\n正文填空。\n"}, ctx
    )
    assert a.success is True
    assert "artifact manifest" in a.output


async def test_file_read_allows_author_self_product(tmp_path: Path):
    """作者写后 body file_read 允许（与读者同 path cap）；计入成功读次数。"""
    ctx = _ctx(tmp_path)
    w = await FileWriteTool().execute(
        {"path": "out.md", "content": "# Title\n\n## Sec\nbody line\n"}, ctx
    )
    assert w.success is True
    assert ctx.landed_artifact_authors.get("out.md") == "a"
    ok = await FileReadTool().execute({"path": "out.md"}, ctx)
    assert ok.success is True
    assert "body line" in (ok.output or "")
    assert ctx.file_read_counts.get("out.md", 0) == 1


async def test_isolate_file_read_ceiling_does_not_share_counters(tmp_path: Path):
    author = _ctx(tmp_path, agent_id="writer")
    author.file_read_counts["x.md"] = 5
    author.file_read_delivered_ranges["x.md"] = [(1, 10)]
    reader = isolate_file_read_ceiling(replace(author, agent_id="ceo", run_id="ceo-run"))
    assert reader.file_read_counts is not author.file_read_counts
    assert reader.file_read_counts == {}
    assert reader.file_read_delivered_ranges == {}
    assert author.file_read_counts["x.md"] == 5
    assert reader.landed_artifact_kinds is author.landed_artifact_kinds


async def test_file_read_author_and_reader_isolated_same_path_cap(tmp_path: Path):
    """同 execution 共享 landed 表；file_read 计数 per-run，读者不被作者顶满。"""
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX

    author_ctx = _ctx(tmp_path, agent_id="writer")
    w = await FileWriteTool().execute(
        {"path": "shared.md", "content": "# Shared\n\nbody for downstream\n"},
        author_ctx,
    )
    assert w.success is True
    assert author_ctx.landed_artifact_kinds.get("shared.md") is not None
    assert author_ctx.file_read_reread_remaining.get("shared.md") == 1

    reader_ctx = isolate_file_read_ceiling(
        replace(author_ctx, agent_id="ceo", run_id="ceo-run")
    )
    assert reader_ctx.landed_artifact_kinds is author_ctx.landed_artifact_kinds
    assert reader_ctx.file_read_counts is not author_ctx.file_read_counts

    allowed = await FileReadTool().execute({"path": "shared.md"}, reader_ctx)
    assert allowed.success is True
    assert "body for downstream" in allowed.output
    assert reader_ctx.file_read_counts.get("shared.md", 0) == 1

    while int(author_ctx.file_read_counts.get("shared.md", 0)) < FILE_READ_SAME_PATH_MAX:
        assert (
            await FileReadTool().execute({"path": "shared.md"}, author_ctx)
        ).success is True
    grant_ok = await FileReadTool().execute({"path": "shared.md"}, author_ctx)
    assert grant_ok.success is True
    assert "body for downstream" in (grant_ok.output or "")
    author_hit = await FileReadTool().execute({"path": "shared.md"}, author_ctx)
    _assert_same_window_hit(author_hit, path="shared.md")

    still = await FileReadTool().execute({"path": "shared.md"}, reader_ctx)
    assert still.success is True
    assert "body for downstream" in (still.output or "")


# --- file_write overwrite integrity nudge ---


async def test_write_nudge_on_omission_marker(tmp_path: Path):
    # ≥400 成篇整盖成功，省略标记仅 soft nudge（不硬拒）。
    old = "A" * 200 + "\n完整中段内容\n" + "B" * 200
    assert len(old) >= 400
    (tmp_path / "draft.md").write_text(old, encoding="utf-8")
    truncated = (
        "A" * 40
        + "\n……（中间省略，已保留首尾）……\n"
        + "B" * 40
    )
    result = await FileWriteTool().execute(
        {"path": "draft.md", "content": truncated}, _ctx(tmp_path)
    )
    assert result.success is True
    assert (tmp_path / "draft.md").read_text(encoding="utf-8") == truncated
    assert "产物疑似不完整" in result.output
    assert "省略标记" in result.output
    assert "绝不代派" in result.output
    assert "绝不拦截本次写入" in result.output


async def test_write_nudge_on_severe_shrink(tmp_path: Path):
    """Near-threshold abs drop stays soft-nudge (not hard reject)."""
    old = "字" * 500
    assert len(old) >= 400
    (tmp_path / "essay.md").write_text(old, encoding="utf-8")
    short = "字" * 100  # ratio <60% but abs drop 400 < 800 → soft only
    result = await FileWriteTool().execute(
        {"path": "essay.md", "content": short}, _ctx(tmp_path)
    )
    assert result.success is True
    assert (tmp_path / "essay.md").read_text(encoding="utf-8") == short
    assert "产物疑似不完整" in result.output
    assert "字数骤降" in result.output
    assert "绝不代派" in result.output


async def test_write_hard_rejects_longdoc_severe_shrink(tmp_path: Path):
    """Longdoc revise thrash sample shape: ~2k→~300 must not land."""
    from agentcore.tools.builtin.file_ops import is_hard_severe_shrink

    old = "字" * 2000
    short = "字" * 300
    assert is_hard_severe_shrink(len(old), len(short))
    (tmp_path / "报告.md").write_text(old, encoding="utf-8")
    result = await FileWriteTool().execute(
        {"path": "报告.md", "content": short}, _ctx(tmp_path)
    )
    assert result.success is False
    assert result.contract_failure is True
    assert "拒绝整篇截断覆盖" in (result.error or "")
    assert "allow_shrink" in (result.error or "")
    assert (tmp_path / "报告.md").read_text(encoding="utf-8") == old


async def test_write_allow_shrink_overrides_hard_reject(tmp_path: Path):
    old = "字" * 2000
    short = "字" * 300
    (tmp_path / "essay.md").write_text(old, encoding="utf-8")
    result = await FileWriteTool().execute(
        {"path": "essay.md", "content": short, "allow_shrink": True},
        _ctx(tmp_path),
    )
    assert result.success is True
    assert (tmp_path / "essay.md").read_text(encoding="utf-8") == short
    # Soft nudge still fires on success path when ratio < 60%.
    assert "产物疑似不完整" in (result.output or "")


async def test_write_no_nudge_on_new_file(tmp_path: Path):
    # New file — even with omission-looking text — must not false-positive.
    body = "开头\n……（中间省略，已保留首尾）……\n结尾"
    result = await FileWriteTool().execute(
        {"path": "new.md", "content": body}, _ctx(tmp_path)
    )
    assert result.success is True
    assert "产物疑似不完整" not in result.output


async def test_write_no_nudge_on_modest_edit(tmp_path: Path):
    old = "字" * 500
    assert len(old) >= 400
    (tmp_path / "essay.md").write_text(old, encoding="utf-8")
    # ~80% of old length, no omission markers — normal small revision.
    modest = "字" * 400
    result = await FileWriteTool().execute(
        {"path": "essay.md", "content": modest}, _ctx(tmp_path)
    )
    assert result.success is True
    assert "产物疑似不完整" not in result.output


def test_has_omission_marker_covers_en_and_cn():
    from agentcore.tools.builtin.file_ops import has_omission_marker, integrity_nudge_text

    assert has_omission_marker("……（中间省略，已保留首尾）……")
    assert has_omission_marker("正文（略）续")
    assert has_omission_marker("see ... omitted details")
    assert has_omission_marker("Truncated for brevity here")
    assert not has_omission_marker("正常全文无省略")
    text = integrity_nudge_text(
        path="a.md", reasons=["正文含省略标记"], old_chars=100, new_chars=40
    )
    assert "产物疑似不完整" in text
    assert "绝不代派" in text


# --- file_write / append / str_replace: no hard length gate ---


async def test_write_allows_oversized_prose(tmp_path: Path):
    """超阈值正文一次 file_write 须成功落盘（不再硬拒）。"""
    # Former hard-gate threshold was ≈2000 tokens × 4 chars ≈ 8000 chars.
    body = "x" * 8000
    result = await FileWriteTool().execute(
        {"path": "big.html", "content": body}, _ctx(tmp_path)
    )
    assert result.success is True
    assert result.contract_failure is not True
    assert (tmp_path / "big.html").read_text(encoding="utf-8") == body
    assert "拒绝整篇一次写入" not in (result.error or "")
    assert "拒绝整篇一次写入" not in (result.output or "")
    assert "已拦截" not in (result.error or "")


async def test_append_allows_oversized_chunk(tmp_path: Path):
    from agentcore.tools.builtin.file_ops import FileAppendTool

    (tmp_path / "a.md").write_text("# skeleton\n", encoding="utf-8")
    body = "y" * 8000
    result = await FileAppendTool().execute(
        {"path": "a.md", "content": body}, _ctx(tmp_path)
    )
    assert result.success is True
    assert result.contract_failure is not True
    assert "拒绝单次过大写入" not in (result.error or "")
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "# skeleton\n" + body


async def test_str_replace_allows_oversized_new_string(tmp_path: Path):
    from agentcore.tools.builtin.file_ops import StrReplaceTool

    (tmp_path / "a.md").write_text("## 参考文献\n", encoding="utf-8")
    new = "z" * 8000
    result = await StrReplaceTool().execute(
        {
            "path": "a.md",
            "old_string": "## 参考文献",
            "new_string": new,
        },
        _ctx(tmp_path),
    )
    assert result.success is True
    assert result.contract_failure is not True
    assert "拒绝单次过大写入" not in (result.error or "")
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == new + "\n"


async def test_write_allows_medium_prose_body(tmp_path: Path):
    body = "x" * 4000
    result = await FileWriteTool().execute(
        {"path": "ok.md", "content": body}, _ctx(tmp_path)
    )
    assert result.success is True
    assert (tmp_path / "ok.md").read_text(encoding="utf-8") == body
    assert "拒绝整篇一次写入" not in (result.output or "")


async def test_write_allows_short_skeleton_with_section_markers(tmp_path: Path):
    skeleton = (
        "# Outline\n\n<!-- OUTLINE -->\n"
        "<!-- SECTION:s0 START -->\n<!-- SECTION:s0 END -->\n"
    )
    result = await FileWriteTool().execute(
        {"path": "report.md", "content": skeleton}, _ctx(tmp_path)
    )
    assert result.success is True
    assert "kind: skeleton" in result.output


async def test_write_then_append_segmented_path(tmp_path: Path):
    """建站 HTML 短骨架 + SECTION 填空：append 仍放行（勿误伤）。"""
    skeleton = (
        "<!doctype html>\n<html>\n<head></head>\n<body>\n"
        "<!-- SECTION:s0 START -->\n<!-- SECTION:s0 END -->\n"
    )
    section = "  <section>hello</section>\n"
    closing = "</body>\n</html>\n"

    ctx = _ctx(tmp_path)
    w = await FileWriteTool().execute(
        {"path": "site/index.html", "content": skeleton}, ctx
    )
    assert w.success is True
    assert ctx.landed_artifact_kinds.get("site/index.html") == "skeleton"

    a1 = await FileAppendTool().execute(
        {"path": "site/index.html", "content": section}, ctx
    )
    assert a1.success is True
    assert "已追加" in a1.output

    a2 = await FileAppendTool().execute(
        {"path": "site/index.html", "content": closing}, ctx
    )
    assert a2.success is True
    merged = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert merged == skeleton + section + closing


def test_write_schema_teaches_hard_rejects():
    """硬拒留在按钮上；Artifact-first HOW 在 identity / consult(long_form_landing)。"""
    write_desc = FileWriteTool().schema.description
    assert "主路径" in write_desc and "完整正文" in write_desc
    assert "短骨架" in write_desc or "骨架" in write_desc
    assert "可选" in write_desc or "按节" in write_desc or "分段" in write_desc
    assert "不硬拒字数" in write_desc
    assert "成篇省略硬拒" in write_desc or "省略标记" in write_desc
    assert "硬拒绝" in write_desc
    assert "中间省略" in write_desc
    assert "优先" in write_desc and "str_replace" in write_desc
    assert "整文件覆盖亦允许" in write_desc or "整盖" in write_desc
    assert "禁止整篇一次" not in write_desc and "仍建议分段" not in write_desc
    assert "_landed_summary" not in write_desc
    assert "已落盘短状态" in write_desc
    assert "清参后改稿" in write_desc
    assert "写盘参数" in write_desc or "重发" in write_desc
    assert "真文" in write_desc
    assert "file_read" in write_desc and "str_replace" in write_desc
    # HOW / 回执百科不在按钮上（身份段 + consult 已有）。
    assert "Artifact-first" not in write_desc
    assert "次数上限" not in write_desc
    assert "NoMatch" not in write_desc
    content_desc = FileWriteTool().schema.parameters["properties"]["content"]["description"]
    assert "一次写完" in content_desc or "完整正文" in content_desc
    assert "骨架" in content_desc
    assert "_landed_summary" not in content_desc
    assert "已落盘短状态" in content_desc or "清理占位" in content_desc

    append_desc = FileAppendTool().schema.description
    assert "骨架" in append_desc
    assert "file_write" in append_desc
    assert "成篇" in append_desc or "禁止" in append_desc
    assert "str_replace" in append_desc
    assert "不硬拒" in append_desc
    assert "次数上限" not in append_desc
    assert "Artifact-first" not in append_desc

    replace_desc = StrReplaceTool().schema.description
    assert "优先" in replace_desc
    assert "整文件覆盖亦允许" in replace_desc or "整盖" in replace_desc
    assert "完整正文" in replace_desc
    assert "禁止改用骨架 file_write" not in replace_desc
    assert "_landed_summary" not in replace_desc
    assert "已落盘短状态" in replace_desc
    assert "清参后改稿" in replace_desc
    assert "真文" in replace_desc
    assert "file_read" in replace_desc
    assert "Artifact-first" not in replace_desc
    assert "manifest" not in replace_desc
    new_desc = StrReplaceTool().schema.parameters["properties"]["new_string"]["description"]
    assert "不硬拒" in new_desc
    assert "_landed_summary" not in new_desc
    assert "已落盘短状态" in new_desc or "清理占位" in new_desc


def test_classify_write_kind_helpers():
    from agentcore.tools.builtin.file_ops import (
        classify_write_kind,
        extract_title_tree,
        has_skeleton_markers,
    )

    assert has_skeleton_markers("<!-- SECTION:s0 START -->")
    assert has_skeleton_markers("<!-- OUTLINE -->")
    assert classify_write_kind("# A\n\n## B\n\n<!-- OUTLINE -->\n") == "skeleton"
    assert classify_write_kind("短") == "skeleton"
    prose = "# T\n\n" + ("正文内容。" * 80)
    assert classify_write_kind(prose) == "prose"
    assert extract_title_tree("# Hello\n\n## World\n") == ["# Hello", "## World"]


# --- file_delete ---


async def test_delete_file(tmp_path: Path):
    (tmp_path / "f.txt").write_text("bye", encoding="utf-8")
    result = await FileDeleteTool().execute({"path": "f.txt"}, _ctx(tmp_path))
    assert result.success is True
    assert "可逆删除" in result.output
    assert not (tmp_path / "f.txt").exists()
    # Soft-deleted into workspace trash with restore metadata.
    trash = tmp_path / "AgentCore" / "trash"
    assert trash.is_dir()
    entries = list(trash.iterdir())
    assert len(entries) == 1
    assert (entries[0] / "meta.json").is_file()
    assert (entries[0] / "content").read_text(encoding="utf-8") == "bye"


async def test_delete_directory_recursive(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "pkg" / "sub").mkdir()
    (tmp_path / "pkg" / "sub" / "b.txt").write_text("b", encoding="utf-8")
    result = await FileDeleteTool().execute({"path": "pkg"}, _ctx(tmp_path))
    assert result.success is True
    assert not (tmp_path / "pkg").exists()
    assert (tmp_path / "AgentCore" / "trash").is_dir()


async def test_delete_permanent_hard_removes(tmp_path: Path):
    (tmp_path / "f.txt").write_text("bye", encoding="utf-8")
    result = await FileDeleteTool().execute(
        {"path": "f.txt", "permanent": True}, _ctx(tmp_path)
    )
    assert result.success is True
    assert "永久删除" in result.output
    assert not (tmp_path / "f.txt").exists()
    trash = tmp_path / "AgentCore" / "trash"
    assert not trash.exists() or not any(trash.iterdir())


async def test_delete_not_found(tmp_path: Path):
    result = await FileDeleteTool().execute({"path": "nope.txt"}, _ctx(tmp_path))
    assert result.success is False
    assert "路径不存在" in result.error
    assert result.contract_failure is True


async def test_delete_rejects_path_outside_workspace(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    result = await FileDeleteTool().execute({"path": "../secret.txt"}, _ctx(ws))
    assert result.success is False
    assert "超出了工作区范围" in result.error
    # the out-of-tree file must be untouched
    assert (tmp_path / "secret.txt").read_text(encoding="utf-8") == "top secret"


async def test_delete_refuses_workspace_root(tmp_path: Path):
    (tmp_path / "keep.txt").write_text("keep", encoding="utf-8")
    result = await FileDeleteTool().execute({"path": ""}, _ctx(tmp_path))
    assert result.success is False
    # Empty path is rejected up-front (成篇 delete gate pre-read); "." still hits
    # OutsideWorkspace at the backend for genuine root deletes.
    assert "path 不能为空" in result.error or "超出了工作区范围" in result.error
    # nothing in the root was removed
    assert (tmp_path / "keep.txt").exists()


async def test_delete_refuses_dot_workspace_root(tmp_path: Path):
    (tmp_path / "keep.txt").write_text("keep", encoding="utf-8")
    result = await FileDeleteTool().execute({"path": "."}, _ctx(tmp_path))
    assert result.success is False
    assert "超出了工作区范围" in result.error or "工作区根" in (result.error or "")
    assert (tmp_path / "keep.txt").exists()


# --- file_move ---


async def test_move_renames_file(tmp_path: Path):
    (tmp_path / "old.txt").write_text("data", encoding="utf-8")
    result = await FileMoveTool().execute(
        {"source": "old.txt", "destination": "new.txt"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert "已把 old.txt 移动到 new.txt" in result.output
    assert not (tmp_path / "old.txt").exists()
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "data"


async def test_move_creates_destination_parents(tmp_path: Path):
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    result = await FileMoveTool().execute(
        {"source": "f.txt", "destination": "deep/nested/f.txt"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert (tmp_path / "deep" / "nested" / "f.txt").read_text(encoding="utf-8") == "x"


async def test_move_directory(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.txt").write_text("a", encoding="utf-8")
    result = await FileMoveTool().execute({"source": "src", "destination": "dst"}, _ctx(tmp_path))
    assert result.success is True
    assert (tmp_path / "dst" / "a.txt").read_text(encoding="utf-8") == "a"
    assert not (tmp_path / "src").exists()


async def test_move_refuses_to_overwrite(tmp_path: Path):
    (tmp_path / "a.txt").write_text("from", encoding="utf-8")
    (tmp_path / "b.txt").write_text("to", encoding="utf-8")
    result = await FileMoveTool().execute(
        {"source": "a.txt", "destination": "b.txt"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert "已存在" in result.error
    # both files must be untouched
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "from"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "to"


async def test_move_source_not_found(tmp_path: Path):
    result = await FileMoveTool().execute(
        {"source": "ghost.txt", "destination": "x.txt"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert "源路径不存在" in result.error
    assert result.contract_failure is True


async def test_move_rejects_path_outside_workspace(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "inside.txt").write_text("inside", encoding="utf-8")
    result = await FileMoveTool().execute(
        {"source": "inside.txt", "destination": "../escaped.txt"}, _ctx(ws)
    )
    assert result.success is False
    assert "超出了工作区范围" in result.error
    assert (ws / "inside.txt").read_text(encoding="utf-8") == "inside"
    assert not (tmp_path / "escaped.txt").exists()


async def test_move_requires_both_args(tmp_path: Path):
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    result = await FileMoveTool().execute({"source": "f.txt"}, _ctx(tmp_path))
    assert result.success is False
    assert "必填" in result.error


async def test_move_identical_paths_is_idempotent(tmp_path: Path):
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    result = await FileMoveTool().execute(
        {"source": "f.txt", "destination": "f.txt"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert "相同" in result.output or "无需" in result.output
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "x"


async def test_move_identical_after_dossier_flatten(tmp_path: Path):
    """Source already flat; nested reviews dest sanitizes to same path → idempotent."""
    flat = f"{REVIEWS_PREFIX}a_b_c.md"
    nested = f"{REVIEWS_PREFIX}a/b/c.md"
    flat_path = tmp_path.joinpath(*flat.split("/"))
    flat_path.parent.mkdir(parents=True, exist_ok=True)
    flat_path.write_text("review", encoding="utf-8")

    result = await FileMoveTool().execute(
        {"source": flat, "destination": nested}, _ctx(tmp_path)
    )
    assert result.success is True
    assert "相同" in result.output or "无需" in result.output
    assert flat_path.read_text(encoding="utf-8") == "review"
    assert flat_path.exists()
    assert not tmp_path.joinpath(*nested.split("/")).exists()


# --- file_copy / mkdir / file_batch ---


async def test_copy_file_and_tree(tmp_path: Path):
    (tmp_path / "a.txt").write_text("data", encoding="utf-8")
    result = await FileCopyTool().execute(
        {"source": "a.txt", "destination": "b/c.txt"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "data"
    assert (tmp_path / "b" / "c.txt").read_text(encoding="utf-8") == "data"

    (tmp_path / "tree" / "sub").mkdir(parents=True)
    (tmp_path / "tree" / "sub" / "x.bin").write_bytes(b"\x00\xff")
    result = await FileCopyTool().execute(
        {"source": "tree", "destination": "tree2"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert (tmp_path / "tree2" / "sub" / "x.bin").read_bytes() == b"\x00\xff"


async def test_copy_identical_paths_is_idempotent(tmp_path: Path):
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    result = await FileCopyTool().execute(
        {"source": "f.txt", "destination": "f.txt"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert "相同" in result.output or "无需" in result.output
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "x"


async def test_copy_identical_after_dossier_flatten(tmp_path: Path):
    """Source already flat; nested reviews dest sanitizes to same path → idempotent."""
    flat = f"{REVIEWS_PREFIX}a_b_c.md"
    nested = f"{REVIEWS_PREFIX}a/b/c.md"
    flat_path = tmp_path.joinpath(*flat.split("/"))
    flat_path.parent.mkdir(parents=True, exist_ok=True)
    flat_path.write_text("review", encoding="utf-8")

    result = await FileCopyTool().execute(
        {"source": flat, "destination": nested}, _ctx(tmp_path)
    )
    assert result.success is True
    assert "相同" in result.output or "无需" in result.output
    assert flat_path.read_text(encoding="utf-8") == "review"
    assert flat_path.exists()
    assert not tmp_path.joinpath(*nested.split("/")).exists()


async def test_copy_refuses_overwrite(tmp_path: Path):
    (tmp_path / "a.txt").write_text("from", encoding="utf-8")
    (tmp_path / "b.txt").write_text("to", encoding="utf-8")
    result = await FileCopyTool().execute(
        {"source": "a.txt", "destination": "b.txt"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert "已存在" in result.error


async def test_mkdir_creates_and_refuses_existing(tmp_path: Path):
    result = await MkdirTool().execute({"path": "out/docs"}, _ctx(tmp_path))
    assert result.success is True
    assert (tmp_path / "out" / "docs").is_dir()
    result = await MkdirTool().execute({"path": "out/docs"}, _ctx(tmp_path))
    assert result.success is False
    assert "已存在" in result.error


async def test_file_batch_partial_failure_continues(tmp_path: Path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    result = await FileBatchTool().execute(
        {
            "operations": [
                {"op": "mkdir", "path": "out"},
                {"op": "copy", "source": "a.txt", "destination": "out/a.txt"},
                {"op": "move", "source": "missing.txt", "destination": "out/m.txt"},
                {"op": "delete", "path": "ghost.txt"},
                {"op": "mkdir", "path": "out"},  # already exists → skip
            ]
        },
        _ctx(tmp_path),
    )
    assert result.success is False  # one hard failure (move missing)
    assert "本次共 5 项" in result.output
    assert "成功" in result.output
    assert "跳过" in result.output
    assert "失败" in result.output
    assert (tmp_path / "out" / "a.txt").read_text(encoding="utf-8") == "a"
    assert result.metadata["ok"] >= 2
    assert result.metadata["fail"] >= 1


# --- file_list ---


def test_expand_brace_globs_basic():
    assert expand_brace_globs("*.{ts,tsx}") == ["*.ts", "*.tsx"]
    assert expand_brace_globs("**/*.{py,pyi}") == ["**/*.py", "**/*.pyi"]
    assert expand_brace_globs("*.py") == ["*.py"]
    assert expand_brace_globs("*") == ["*"]


async def test_file_list_pattern_miss_does_not_say_empty_dir(tmp_path: Path):
    """Trace f69e97…: CEO `*.py` on `.` returned「空目录」though server/ client/ existed."""
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "main.py").write_text("x", encoding="utf-8")
    (tmp_path / "client").mkdir()
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    result = await FileListTool().execute(
        {"directory": ".", "pattern": "*.py"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert "空目录" not in result.output
    assert "无匹配 pattern='*.py'" in result.output
    assert "目录非空" in result.output
    assert "recursive=true" in result.output
    # Top-level sample should surface real dirs/files
    assert "server" in result.output or "client" in result.output


async def test_file_list_truly_empty_dir_still_says_empty(tmp_path: Path):
    empty = tmp_path / "blank"
    empty.mkdir()
    result = await FileListTool().execute(
        {"directory": "blank", "pattern": "*.py"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert result.output == "（空目录）"


async def test_file_list_brace_glob_matches_either_extension(tmp_path: Path):
    """pathlib does not expand `{a,b}` — without help, `*.{ts,tsx}` falsely empties."""
    src = tmp_path / "client" / "src"
    src.mkdir(parents=True)
    (src / "App.tsx").write_text("export {}", encoding="utf-8")
    (src / "api.ts").write_text("export {}", encoding="utf-8")
    (src / "readme.md").write_text("x", encoding="utf-8")

    result = await FileListTool().execute(
        {"directory": "client/src", "pattern": "*.{ts,tsx}"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert "空目录" not in result.output
    assert "App.tsx" in result.output
    assert "api.ts" in result.output
    assert "readme.md" not in result.output


async def test_file_list_recursive_pattern_miss_hint(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x", encoding="utf-8")
    result = await FileListTool().execute(
        {"directory": "pkg", "pattern": "*.rs", "recursive": True}, _ctx(tmp_path)
    )
    assert result.success is True
    assert "空目录" not in result.output
    assert "无匹配 pattern='*.rs'" in result.output
    assert "a.py" in result.output or "目录非空" in result.output


async def test_file_read_missing_with_parent_gives_landmark(tmp_path: Path):
    """父目录存在时：同层样本 + 更宽查找建议（对齐 file_list 空匹配 hint）。"""
    app = tmp_path / "apps" / "desktop"
    app.mkdir(parents=True)
    (app / "README.md").write_text("# desk", encoding="utf-8")
    (app / "src").mkdir()

    result = await FileReadTool().execute(
        {"path": "apps/desktop/package.json"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert result.error is not None
    assert result.error.startswith("文件不存在：apps/desktop/package.json")
    assert result.contract_failure is True
    assert "父目录" in result.error
    assert "apps/desktop/" in result.error
    assert "可见同层示例" in result.error
    assert "README.md" in result.error or "src" in result.error
    assert "file_list" in result.error
    assert "更宽查找" in result.error
    assert "已知路径" in result.error
    assert "反复重试" in result.error


async def test_file_list_missing_with_parent_gives_landmark(tmp_path: Path):
    """列目录路径不存在但上级可列：同层样本 + 勿反复重试。"""
    server = tmp_path / "apps" / "server"
    server.mkdir(parents=True)
    (server / "agentcore").mkdir()
    (server / "README.md").write_text("x", encoding="utf-8")
    result = await FileListTool().execute(
        {"directory": "apps/server/src", "pattern": "*"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert result.error is not None
    assert result.error.startswith("不是目录：apps/server/src")
    assert result.contract_failure is True
    assert "父目录" in result.error
    assert "apps/server/" in result.error
    assert "agentcore" in result.error or "README.md" in result.error
    assert "反复重试" in result.error


async def test_file_read_missing_parent_gives_root_tip(tmp_path: Path):
    """路径与父目录都不在：不编造同层样本，但给根查找 / 勿反复重试提示。"""
    (tmp_path / "apps").mkdir()
    result = await FileReadTool().execute(
        {"path": "apps/ghost/package.json"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert result.error is not None
    assert result.error.startswith("文件不存在：apps/ghost/package.json")
    assert result.contract_failure is True
    assert "上级目录也找不到" in result.error
    assert "禁止凭通用目录名" not in result.error
    assert "反复重试" in result.error
    assert "可见同层示例" not in result.error


async def test_file_list_latent_stage_dir_returns_empty_not_error(tmp_path: Path):
    """约定出口尚未创建：file_list 成功空态（写入会自动创建），不报 NotADirectory。"""
    from agentcore.workspace.stage_dirs import RESEARCH_DIR

    assert not (tmp_path / "AgentCore").exists()
    result = await FileListTool().execute(
        {"directory": RESEARCH_DIR, "pattern": "*"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert result.error is None
    assert "空目录" in (result.output or "")
    assert "写入时会自动创建" in (result.output or "")
    assert not (tmp_path / "AgentCore").exists()  # 不预创建


async def test_file_list_latent_attachments_returns_empty_not_error(tmp_path: Path):
    """attachments/ 尚未创建：列目录空态成功。"""
    result = await FileListTool().execute(
        {"directory": "attachments", "pattern": "*"}, _ctx(tmp_path)
    )
    assert result.success is True
    assert "写入时会自动创建" in (result.output or "")
    assert not (tmp_path / "attachments").exists()


async def test_file_list_guessed_missing_path_still_errors(tmp_path: Path):
    """真·乱猜路径仍报错（不得因 latent 口径放成空目录）。"""
    result = await FileListTool().execute(
        {"directory": "apps/server/src", "pattern": "*"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert result.error is not None
    assert result.error.startswith("不是目录：apps/server/src")
    assert result.contract_failure is True
    assert "写入时会自动创建" not in result.error


async def test_file_read_missing_top_level_uses_root_landmark(tmp_path: Path):
    """顶层缺失文件：父目录为根，仍给同层样本。"""
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")
    (tmp_path / "src").mkdir()
    result = await FileReadTool().execute({"path": "package.json"}, _ctx(tmp_path))
    assert result.success is False
    assert result.error is not None
    assert result.error.startswith("文件不存在：package.json")
    assert result.contract_failure is True
    assert "父目录 ./" in result.error
    assert "可见同层示例" in result.error
    assert "README.md" in result.error or "src" in result.error
    assert "file_list" in result.error


async def test_file_list_shows_attachment_zip_hides_elsewhere(tmp_path: Path):
    """attachments/ only-zip must not render「（空目录）」; root zip stays hidden."""
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "pack.zip").write_bytes(b"PK\x03\x04")
    (tmp_path / "noise.zip").write_bytes(b"PK\x03\x04")

    att = await FileListTool().execute(
        {"directory": "attachments", "pattern": "*"}, _ctx(tmp_path)
    )
    assert att.success is True
    assert "空目录" not in att.output
    assert "pack.zip" in att.output

    root = await FileListTool().execute(
        {"directory": ".", "pattern": "*"}, _ctx(tmp_path)
    )
    assert root.success is True
    assert "noise.zip" not in root.output
    assert "attachments" in root.output

    tree = await FileListTool().execute(
        {"directory": ".", "pattern": "*", "recursive": True, "max_depth": 2},
        _ctx(tmp_path),
    )
    assert tree.success is True
    assert "pack.zip" in tree.output
    assert "noise.zip" not in tree.output


async def test_file_list_reveals_material_png(tmp_path: Path):
    """Materials path (e.g. src/shot.png) visible; sibling AI-noise still hidden."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "shot.png").write_bytes(b"png")
    (tmp_path / "src" / "other.png").write_bytes(b"png")
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "pack.zip").write_bytes(b"PK\x03\x04")

    ctx = _ctx(tmp_path)
    ctx.material_paths = frozenset({"src/shot.png"})
    ctx.backend.ai_list_materials = ctx.material_paths

    listed = await FileListTool().execute(
        {"directory": "src", "pattern": "*"}, ctx
    )
    assert listed.success is True
    assert "shot.png" in listed.output
    assert "other.png" not in listed.output

    tree = await FileListTool().execute(
        {"directory": ".", "pattern": "*", "recursive": True, "max_depth": 2},
        ctx,
    )
    assert tree.success is True
    assert "shot.png" in tree.output
    assert "other.png" not in tree.output
    assert "pack.zip" in tree.output  # attachments/ still exempt

    # Without materials, same png stays hidden
    bare = await FileListTool().execute(
        {"directory": "src", "pattern": "*"}, _ctx(tmp_path)
    )
    assert bare.success is True
    assert "shot.png" not in bare.output
    assert "空目录" in bare.output or bare.output.strip() == "" or "shot" not in bare.output


async def test_file_list_external_mount_shows_archive_zip(tmp_path: Path):
    """external/<alias>/ 下列出可见用户压缩包；工作区根 zip 仍隐藏。"""
    from agentcore.workspace.external_mounts import ExternalMount

    ext = tmp_path / "ext_root"
    ext.mkdir()
    (ext / "咨询.sy.zip").write_bytes(b"PK\x03\x04")
    (ext / "note.txt").write_text("hi", encoding="utf-8")
    (tmp_path / "noise.zip").write_bytes(b"PK\x03\x04")

    ctx = _ctx(tmp_path)
    ctx.backend.attach_external_mounts(
        {
            "desk": ExternalMount(
                alias="desk",
                root_id="r",
                label="桌面",
                abs_path=str(ext),
                mode="readonly",
            )
        }
    )

    listed = await FileListTool().execute(
        {"directory": "external/desk", "pattern": "*"}, ctx
    )
    assert listed.success is True
    assert "咨询.sy.zip" in listed.output
    assert "note.txt" in listed.output

    tree = await FileListTool().execute(
        {
            "directory": "external/desk",
            "pattern": "*",
            "recursive": True,
            "max_depth": 2,
        },
        ctx,
    )
    assert tree.success is True
    assert "咨询.sy.zip" in tree.output

    root = await FileListTool().execute(
        {"directory": ".", "pattern": "*"}, ctx
    )
    assert root.success is True
    assert "noise.zip" not in root.output


async def test_file_list_pattern_zip_reveals_workspace_archive(tmp_path: Path):
    """pattern 指向压缩包后缀时，工作区根 zip 也应列出。"""
    (tmp_path / "pack.zip").write_bytes(b"PK\x03\x04")
    (tmp_path / "readme.md").write_text("x", encoding="utf-8")

    hidden = await FileListTool().execute(
        {"directory": ".", "pattern": "*"}, _ctx(tmp_path)
    )
    assert hidden.success is True
    assert "pack.zip" not in hidden.output

    revealed = await FileListTool().execute(
        {"directory": ".", "pattern": "*.zip"}, _ctx(tmp_path)
    )
    assert revealed.success is True
    assert "pack.zip" in revealed.output


async def test_file_list_bare_external_actionable_hint_no_mounts(tmp_path: Path):
    result = await FileListTool().execute(
        {"directory": "external"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert "external/<别名>/" in result.error
    assert "尚无" in result.error or "授权" in result.error


async def test_file_list_bare_external_lists_current_mounts(tmp_path: Path):
    from agentcore.workspace.external_mounts import ExternalMount

    ctx = _ctx(tmp_path)
    ctx.backend.attach_external_mounts(
        {
            "desk": ExternalMount(
                alias="desk",
                root_id="r",
                label="桌面",
                abs_path=str(tmp_path),
                mode="readonly",
            )
        }
    )
    result = await FileListTool().execute({"directory": "external"}, ctx)
    assert result.success is False
    assert "external/<别名>/" in result.error
    assert "`external/desk/`" in result.error


# --- write_scope (冷启动 explore_memory) ---


def _explore_ctx(workspace: Path) -> ToolContext:
    ctx = _ctx(workspace)
    ctx.write_scope = "explore_memory"
    return ctx


async def test_write_scope_explore_memory_allows_research_note(tmp_path: Path):
    ctx = _explore_ctx(tmp_path)
    result = await FileWriteTool().execute(
        {
            "path": "AgentCore/文档/research/摸底笔记.md",
            "content": "# 笔记\n",
        },
        ctx,
    )
    assert result.success is True
    assert (tmp_path / "AgentCore" / "文档" / "research" / "摸底笔记.md").is_file()


async def test_write_scope_explore_memory_rejects_user_project_path(tmp_path: Path):
    ctx = _explore_ctx(tmp_path)
    result = await FileWriteTool().execute(
        {"path": "src/main.py", "content": "print(1)\n"},
        ctx,
    )
    assert result.success is False
    assert result.contract_failure is True
    assert "AgentCore/" in (result.error or "")
    assert "src/main.py" in (result.error or "")


async def test_write_scope_explore_memory_has_no_inner_path_ban(tmp_path: Path):
    """步 3：闸只判「在不在 AgentCore/ 下」——厚约定文档已是条目，worker 无工具可写。"""
    ctx = _explore_ctx(tmp_path)
    result = await FileWriteTool().execute(
        {
            "path": "AgentCore/文档/背景/架构详解.md",
            "content": "# 背景资料\n",
        },
        ctx,
    )
    assert result.success is True
    assert (tmp_path / "AgentCore" / "文档" / "背景" / "架构详解.md").is_file()


async def test_write_scope_none_rejects_all(tmp_path: Path):
    ctx = _ctx(tmp_path)
    ctx.write_scope = "none"
    result = await FileAppendTool().execute(
        {"path": "AgentCore/文档/research/x.md", "content": "x"},
        ctx,
    )
    assert result.success is False
    assert "write_scope=none" in (result.error or "")


async def test_write_scope_explore_memory_str_replace_rejects_outside(tmp_path: Path):
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    ctx = _explore_ctx(tmp_path)
    result = await StrReplaceTool().execute(
        {"path": "app.py", "old_string": "a = 1", "new_string": "a = 2"},
        ctx,
    )
    assert result.success is False
    assert "AgentCore/" in (result.error or "")
