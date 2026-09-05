"""md_to_docx tool — thin shell over docs_export.export_markdown_path."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from agentcore.tools.builtin.md_to_docx import MdToDocxTool
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


async def test_md_to_docx_tool_writes_sibling_and_warns_missing_image(tmp_path: Path):
    (tmp_path / "note.md").write_text("# Hi\n\n![x](./gone.png)\n", encoding="utf-8")
    result = await MdToDocxTool().execute({"path": "note.md"}, _ctx(tmp_path))
    assert result.success is True
    assert (tmp_path / "note.docx").is_file()
    assert "note.docx" in result.output
    assert "缺图" in result.output
    assert result.metadata is not None
    assert result.metadata["path"] == "note.docx"
    # 台账事实口径：产物是导出的 docx，源 md 记在 derived_from（端到端见 test_export_product_ledger）。
    assert [(p.path, p.kind, p.derived_from) for p in result.file_products] == [
        ("note.docx", "docx", "note.md")
    ]


async def test_md_to_docx_tool_rejects_non_md(tmp_path: Path):
    result = await MdToDocxTool().execute({"path": "a.txt"}, _ctx(tmp_path))
    assert result.success is False
    assert result.error and "Markdown" in result.error


def _document_xml(path: Path) -> str:
    with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as zf:
        return zf.read("word/document.xml").decode("utf-8")


async def test_md_to_docx_tool_layout_official_opts_into_first_line_indent(tmp_path: Path):
    (tmp_path / "起诉状.md").write_text("# 民事起诉状\n\n原告：张三。\n", encoding="utf-8")

    plain = await MdToDocxTool().execute({"path": "起诉状.md"}, _ctx(tmp_path))
    assert plain.success is True
    assert "w:firstLine" not in _document_xml(tmp_path / "起诉状.docx")

    official = await MdToDocxTool().execute(
        {"path": "起诉状.md", "layout": "official"}, _ctx(tmp_path)
    )
    assert official.success is True
    xml = _document_xml(tmp_path / "起诉状.docx")
    assert 'w:firstLineChars="200"' in xml
    assert '<w:jc w:val="center"/>' in xml


async def test_md_to_docx_tool_rejects_unknown_layout(tmp_path: Path):
    (tmp_path / "note.md").write_text("# Hi\n\n正文。\n", encoding="utf-8")
    result = await MdToDocxTool().execute(
        {"path": "note.md", "layout": "公文"}, _ctx(tmp_path)
    )
    assert result.success is False
    assert result.error and "layout" in result.error
    # 未知档位不得静默降级成默认档后照常落盘。
    assert not (tmp_path / "note.docx").exists()


def test_md_to_docx_schema_advertises_layout():
    schema = MdToDocxTool().schema
    layout = schema.parameters["properties"]["layout"]
    assert layout["enum"] == ["standard", "official"]
    assert layout["default"] == "standard"
    assert "正式文书" in layout["description"]
    assert "两端对齐" in layout["description"]
    assert "页码" in layout["description"]
    assert "Word 另加" in layout["description"]
    assert "layout" not in schema.parameters["required"]
