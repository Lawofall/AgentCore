"""read_image — CEO on-demand deep-read of a workspace-resident image (P2).

``board_read`` rasterizes whiteboard pixels; this tool reads a workspace-relative
image file via ``backend.read_bytes`` → base64 → ``VisionReader.read(prompt)`` and
returns text the CEO can reason over. Same clean-failure posture as ``board_read``:
no reader →「读图能力未配置」; non-image / missing path / vision failure → failed
``ToolResult``, never hang the turn.

Billing matches attachment eye→text: ``vision_run_cost`` + ``reader.credential_source``.
"""

from __future__ import annotations

import base64
from pathlib import PurePosixPath
from typing import Any, cast

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolFace
from agentcore.llm.pricing import CredentialSource
from agentcore.runtime.costing import vision_run_cost
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_CEO_ONLY,
    CeoWire,
    ToolRegistration,
    ToolSurface,
)
from agentcore.vision.protocol import VisionReading
from agentcore.workspace.protocol import WorkspaceError

logger = get_logger(__name__)

READ_IMAGE_TOOL_NAME = "read_image"

# Align with conversation attachment eye→text raster set (prepare._IMAGE_EXTENSIONS).
_IMAGE_EXTENSIONS = frozenset({
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".avif",
    ".heic",
    ".heif",
})


class ReadImageTool:
    """Deep-read a workspace image with a caller-supplied vision prompt."""

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.ALWAYS,
        resident=False,
        catalog_summary="读工作区图片",
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=READ_IMAGE_TOOL_NAME,
            description=(
                "按需深读【工作区】里的图片：传入工作区相对路径与关注点 prompt，"
                "用视觉模型返回文字解读。用于对话贴图已注入后仍需带着具体问题再看、"
                "或读工作区里未作为本回合附件的图。\n"
                "与 board_read 分工：board_read=白板元素栅格化读图；本工具=工作区文件读图。"
                "非图片路径或读图能力未配置时会干净失败。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "工作区相对路径（如 attachments/shot.png 或 docs/fig.png）。"
                        ),
                    },
                    "prompt": {
                        "type": "string",
                        "description": (
                            "读图关注点 / 用户问题（例如「图里有几个按钮？」「提取表格文字」）。"
                        ),
                    },
                },
                "required": ["path", "prompt"],
            },
            face=ToolFace.BOARD,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        reader = context.vision_reader
        if reader is None:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=(
                    "读图能力未配置：本实例未接入视觉模型，无法读取工作区图片。"
                    "可告知用户该能力暂未开启。"
                ),
            )

        path = arguments.get("path")
        prompt = arguments.get("prompt")
        if not isinstance(path, str) or not path.strip():
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="read_image 需要非空的 path（工作区相对路径）。",
            )
        if not isinstance(prompt, str) or not prompt.strip():
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="read_image 需要非空的 prompt（读图关注点 / 用户问题）。",
            )
        rel_path = path.strip()
        prompt_text = prompt.strip()

        ext = PurePosixPath(rel_path.replace("\\", "/")).suffix.lower()
        if ext not in _IMAGE_EXTENSIONS:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=(
                    f"不是可识读的图片路径（扩展名 {ext or '无'}）：{rel_path}。"
                    "支持 png/jpg/jpeg/gif/webp/bmp/avif/heic/heif。"
                ),
            )

        logger.info("read_image.read", run_id=context.run_id, path=rel_path)
        try:
            raw = await context.backend.read_bytes(rel_path)
        except WorkspaceError as e:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=f"无法读取工作区图片：{e}。请确认路径存在且在工作区内。",
            )
        except Exception as e:  # noqa: BLE001 — never hang the turn on FS surprises
            logger.warning("read_image.read_failed", path=rel_path, error=str(e), exc_info=True)
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=f"无法读取工作区图片：{e}。请确认路径存在且在工作区内。",
            )

        if not raw:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=f"工作区图片为空：{rel_path}，无法解读。",
            )

        b64 = base64.b64encode(raw).decode("ascii")
        try:
            reading = await reader.read(b64, prompt_text)
        except Exception as e:
            logger.warning("read_image.vision_failed", path=rel_path, error=str(e), exc_info=True)
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=f"视觉模型解读失败：{e}。可稍后重试或告知用户。",
            )

        self._bill_vision(reading, context, reader=reader)
        return ToolResult(tool_call_id="", success=True, output=reading.text)

    @staticmethod
    def _bill_vision(reading: VisionReading, context: ToolContext, *, reader: object) -> None:
        """Price the vision sub-call (role=vision); never break a successful read."""
        sink = context.cost_sink
        if sink is None or not reading.model or reading.usage.total_tokens == 0:
            return
        src = getattr(reader, "credential_source", None)
        credential_source: CredentialSource | None = (
            cast(CredentialSource, src) if src in ("user", "platform", "vendor") else None
        )
        try:
            sink.append(
                vision_run_cost(
                    reading.model,
                    reading.usage,
                    parent_run_id=context.run_id,
                    credential_source=credential_source,
                )
            )
        except Exception:  # noqa: BLE001 — billing must never break a successful read
            logger.warning("read_image.billing_failed", exc_info=True)
