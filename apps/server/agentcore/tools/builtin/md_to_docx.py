"""md_to_docx — deterministic Markdown → Word export into the workspace.

Shares ``agentcore.docs_export`` with the desktop workspace「导出 Word」HTTP path.
Never shells out to pandoc / code_execute / LLM scripting.
"""

from __future__ import annotations

import time
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.docs_export.layout import (
    DOC_LAYOUTS,
    LAYOUT_INVALID_MESSAGE,
    LAYOUT_PARAM_DESCRIPTION,
    LAYOUT_STANDARD,
    parse_layout,
)
from agentcore.docs_export.workspace_export import ExportMarkdownError, export_markdown_path
from agentcore.tools.file_products import file_product
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)

logger = get_logger(__name__)

MD_TO_DOCX_TOOL_NAME = "md_to_docx"


class MdToDocxTool:
    """Export a workspace Markdown file to a sibling ``.docx``."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        # 漏账事故的原点：它从注册那天起就没进过任何一份工具名白名单。
        file_products=FileProductsContract.SELF_REPORT,
        produces_formats=(".docx",),
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=MD_TO_DOCX_TOOL_NAME,
            description=(
                "把工作区内的 Markdown 文件确定性导出为同目录同名 Word（.docx）。"
                "例：`报告.md` → `报告.docx`。覆盖标题 #–####、段落、有序/无序列表、"
                "表格、围栏代码、相对路径图片（嵌入）与链接；缺图会在回执中明确警告。"
                "路径必须是相对于工作区的 .md / .markdown 相对路径。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "工作区内的 Markdown 相对路径（如 `docs/报告.md`）",
                    },
                    "layout": {
                        "type": "string",
                        "enum": list(DOC_LAYOUTS),
                        "default": LAYOUT_STANDARD,
                        "description": LAYOUT_PARAM_DESCRIPTION,
                    },
                },
                "required": ["path"],
            },
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        rel_path = str(arguments.get("path") or "").strip()
        if not rel_path:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="path 不能为空：请提供工作区内的 .md 相对路径",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        layout = parse_layout(arguments.get("layout"))
        if layout is None:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=LAYOUT_INVALID_MESSAGE,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        try:
            result = await export_markdown_path(context.backend, rel_path, layout=layout)
        except ExportMarkdownError as e:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=e.message,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        logger.info(
            "md_to_docx.exported",
            source=result.source_path,
            output=result.output_path,
            bytes=result.size_bytes,
            layout=layout,
            warnings=len(result.warnings),
            run_id=context.run_id,
        )

        lines = [
            f"已导出 Word：{result.output_path}（{result.size_bytes} 字节）",
            "【artifact manifest】",
            f"path: {result.output_path}",
            "kind: docx",
            f"bytes: {result.size_bytes}",
            f"source: {result.source_path}",
        ]
        if result.warnings:
            lines.append("warnings:")
            lines.extend(f"  - {w}" for w in result.warnings)
        else:
            lines.append("warnings: （无）")
        lines.append("【验真】请以本 manifest 确认落盘；可用工作区下载打开 .docx。")

        return ToolResult(
            tool_call_id="",
            success=True,
            output="\n".join(lines),
            duration_ms=int((time.monotonic() - start) * 1000),
            metadata={
                "path": result.output_path,
                "source": result.source_path,
                "bytes": result.size_bytes,
                "warnings": list(result.warnings),
            },
            # 台账事实口径：产物是导出的 .docx（入参那份 md 是它的源）——自报后源 md 才能
            # 在用户面被折叠为中间稿，答复不会再把 .md 当成「Word 文档」的位置。
            file_products=[
                file_product(result.output_path, derived_from=result.source_path)
            ],
        )
