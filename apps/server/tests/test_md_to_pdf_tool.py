"""md_to_pdf tool — thin shell over docs_export.export_markdown_to_pdf_path."""

from __future__ import annotations

from pathlib import Path

from agentcore.tools.builtin.md_to_docx import MdToDocxTool
from agentcore.tools.builtin.md_to_pdf import MdToPdfTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def _ctx(workspace: Path) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=workspace, sandbox=SubprocessSandbox()),
        user_id="u",
    )


async def test_md_to_pdf_tool_writes_sibling(tmp_path: Path):
    (tmp_path / "note.md").write_text("# Hi\n\n你好世界\n", encoding="utf-8")
    result = await MdToPdfTool().execute({"path": "note.md"}, _ctx(tmp_path))
    assert result.success is True
    assert (tmp_path / "note.pdf").is_file()
    assert (tmp_path / "note.pdf").read_bytes()[:4] == b"%PDF"
    assert "note.pdf" in result.output
    assert "code_execute" not in MdToPdfTool().schema.description
    assert result.metadata is not None
    assert result.metadata["path"] == "note.pdf"
    # 台账事实口径：产物是导出的 pdf，源 md 记在 derived_from（端到端见 test_export_product_ledger）。
    assert [(p.path, p.kind, p.derived_from) for p in result.file_products] == [
        ("note.pdf", "pdf", "note.md")
    ]


async def test_md_to_pdf_tool_rejects_non_md(tmp_path: Path):
    result = await MdToPdfTool().execute({"path": "a.txt"}, _ctx(tmp_path))
    assert result.success is False
    assert result.error and "Markdown" in result.error


async def test_md_to_pdf_tool_rejects_unknown_layout(tmp_path: Path):
    (tmp_path / "note.md").write_text("# Hi\n\n正文。\n", encoding="utf-8")
    result = await MdToPdfTool().execute(
        {"path": "note.md", "layout": "公文"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert result.error and "layout" in result.error
    assert not (tmp_path / "note.pdf").exists()


def test_md_to_pdf_schema_advertises_layout():
    """与 md_to_docx 同口径：同一个档位名、同一段措辞。"""
    layout = MdToPdfTool().schema.parameters["properties"]["layout"]
    assert layout["enum"] == ["standard", "official"]
    assert layout["default"] == "standard"
    assert layout["description"] == MdToDocxTool().schema.parameters["properties"]["layout"][
        "description"
    ]


async def test_md_to_pdf_tool_warns_on_image(tmp_path: Path):
    (tmp_path / "note.md").write_text("# Hi\n\n![x](./gone.png)\n", encoding="utf-8")
    result = await MdToPdfTool().execute({"path": "note.md"}, _ctx(tmp_path))
    assert result.success is True
    assert "不嵌入图片" in result.output
