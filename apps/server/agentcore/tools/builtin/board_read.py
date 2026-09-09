"""board_read — let the AI 看懂 hand-drawn / screenshot board elements (AI协作白板.md §九).

The read half of the whiteboard brief (§九 砸 briefRegion: brief = 选区). Structured
elements (便签 / 文字 / 形状) reach the model directly as scene JSON, but free-hand drawings
and pasted screenshots are just pixels — they must be rasterized and read by a vision
model. ``board_read`` asks the bound desktop (via the same :class:`BoardChannel` that
``board_ops`` uses) to rasterize a subset of elements to a PNG, then hands that PNG to a
``VisionReader`` port for a text reading the CEO can reason over.

Two clean-failure gates (引擎纯化 — never hang, never pretend):

- off a board (``board_channel is None``) → 仅在白板会话可用.
- no vision provider wired (``vision_reader is None``) → 读图能力未配置. This is the current
  skeleton state: DeepSeek V4 无多模态, so a vision provider is an独立依赖决策 (§九.4). The
  transport (rasterize + channel) is built and ready; only the reader slot is empty, and the
  tool says so plainly rather than inventing what the drawing showed.
"""

from __future__ import annotations

from typing import Any

from agentcore.board.channel import BoardReadError
from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolFace
from agentcore.runtime.costing import vision_run_cost
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_CEO_ONLY,
    CeoWire,
    ToolRegistration,
    ToolSurface,
)
from agentcore.vision.protocol import VisionReading

logger = get_logger(__name__)

BOARD_READ_TOOL_NAME = "board_read"

# The brief framing handed to the vision model — read the image AS a requirement brief, not
# as a generic "describe this picture".
_READ_PROMPT = (
    "把这张手绘 / 截图当作需求 brief，用中文分点说清：画了哪些元素、它们之间的关系 / 结构、"
    "以及作者可能的意图。只描述图里有的内容，不要臆测图外信息。"
)


class BoardReadTool:
    """Read hand-drawn / screenshot board elements as text via a vision model."""

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.BOARD,
        resident=False,
        catalog_summary="读白板手绘/截图",
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=BOARD_READ_TOOL_NAME,
            description=(
                "读懂【当前白板】上的手绘草图 / 截图：传入要看的元素 id 列表，画布会把它们"
                "栅格化成图片交给视觉模型，返回对其结构与意图的文字解读。仅在白板会话可用。\n"
                "便签 / 文字 / 形状这类结构化元素【无需】本工具——它们已能直接从场景读出；"
                "本工具只用于「看不懂的像素」（手绘 freedraw / 图片 / 截图）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "要读取（栅格化）的元素真实 id 列表，通常是选区里的手绘 / 截图子集。"
                        ),
                    },
                },
                "required": ["ids"],
            },
            face=ToolFace.BOARD,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        channel = context.board_channel
        if channel is None:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="board_read 仅在白板会话中可用：当前会话没有绑定白板，无法读图。",
            )
        reader = context.vision_reader
        if reader is None:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=(
                    "读图能力未配置：本实例未接入视觉模型，无法读取手绘 / 截图。"
                    "可改用结构化元素（便签 / 文字）表达，或告知用户该能力暂未开启。"
                ),
            )

        ids = arguments.get("ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="board_read 需要非空的 ids 数组（要读取的元素 id 列表）。",
            )

        logger.info(
            "board.read",
            run_id=context.run_id,
            board_id=channel.board_id,
            id_count=len(ids),
        )
        try:
            value = await channel.read(ids)
        except BoardReadError as e:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=f"白板读图未完成：{e}。可告知用户或稍后重试。",
            )

        png = value.get("pngBase64") if isinstance(value, dict) else None
        if not isinstance(png, str) or not png:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="白板读图返回为空（未取得图像），无法解读。",
            )

        try:
            reading = await reader.read(png, _READ_PROMPT)
        except Exception as e:
            logger.warning("board.read_vision_failed", error=str(e), exc_info=True)
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=f"视觉模型解读失败：{e}。可稍后重试或告知用户。",
            )

        self._bill_vision(reading, context)
        return ToolResult(tool_call_id="", success=True, output=reading.text)

    @staticmethod
    def _bill_vision(reading: VisionReading, context: ToolContext) -> None:
        """Price the vision sub-call into the turn's ledger (AI协作白板.md §九.4 Gap ②).

        The vision model (qwen-vl) ≠ the run's DeepSeek, so its spend can't fold into the
        run usage — it becomes its own ``role=vision`` ledger row, parented to the calling
        run so it nests under that captain in the turn's run tree. No sink (tests / no board)
        or no usage signal (a stub reader) ⇒ nothing to bill; never let accounting break a
        read that already succeeded.
        """
        sink = context.cost_sink
        if sink is None or not reading.model or reading.usage.total_tokens == 0:
            return
        try:
            sink.append(
                vision_run_cost(
                    reading.model,
                    reading.usage,
                    parent_run_id=context.run_id,
                    credential_source="platform",
                )
            )
        except Exception:  # noqa: BLE001 — billing must never break a successful read
            logger.warning("board.read_billing_failed", exc_info=True)
