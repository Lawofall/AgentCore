"""board_ops — the AI's hands on the collaborative whiteboard (AI协作白板.md §六 M2).

Available ONLY in a 白板会话 (a conversation bound to a board): the assembler sets
``ToolContext.board_channel`` for those runs and leaves it ``None`` everywhere else, so
calling ``board_ops`` off a board returns a clean error instead of touching anything.

The tool is a thin, stateless mapper. It validates the op batch and hands it to the
:class:`BoardChannel`, which suspends the run, asks the bound desktop to apply the ops to
the open whiteboard canvas (convert → updateScene → CAS autosave), and returns what
landed. The tool never sees a ``Path`` or the scene JSON — the canvas lives in the
desktop, the server only speaks the structured op vocabulary (空间 JSON 为真相 §七).

The op vocabulary (one closed set, shared with the desktop applier):

- ``add_node``   — a shape (sticky / rectangle / ellipse / diamond / text) with text.
                   Give it a ``ref`` (a handle of YOUR choosing) so later ops in the SAME
                   call can wire to it before real ids exist.
- ``connect``    — an arrow ``from`` → ``to`` (each a ``ref`` from this call or a real
                   ``id`` already on the board), optional ``label``.
- ``move``       — reposition an existing element (``id``) or a just-added ``ref`` to x,y.
- ``set_text``   — replace the text of an element (``id``) or ``ref``.
- ``delete``     — remove an element (``id``) or ``ref``.
- ``group``      — group ``members`` (refs / ids) so they move together.
"""

from __future__ import annotations

from typing import Any

from agentcore.board.channel import BoardOpError
from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolFace
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_CEO_ONLY,
    CeoWire,
    ToolRegistration,
    ToolSurface,
)

logger = get_logger(__name__)

BOARD_OPS_TOOL_NAME = "board_ops"

# The closed set of op verbs (kept in sync with the desktop applier's dispatch).
_OP_KINDS = ("add_node", "connect", "move", "set_text", "delete", "group")
_NODE_KINDS = ("sticky", "rectangle", "ellipse", "diamond", "text")


class BoardOpsTool:
    """Apply a batch of structured ops to the open whiteboard via the bound desktop."""

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.BOARD,
        resident=False,
        catalog_summary="白板上作画",
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=BOARD_OPS_TOOL_NAME,
            description=(
                "在当前白板作画：新增节点、连线、移动、改文字、删除、成组。"
                "仅白板会话可用。一次调用一批 ops，按序应用到用户正打开的画布。"
                "一次表达完整结构，勿为同一目标反复小步调用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "ops": {
                        "type": "array",
                        "description": "要应用的操作批次，按顺序执行。",
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": {
                                    "type": "string",
                                    "enum": list(_OP_KINDS),
                                    "description": "操作类型。",
                                },
                                "ref": {
                                    "type": "string",
                                    "description": (
                                        "本次调用内你自取的节点句柄。add_node 时给它命名，"
                                        "后续 connect/move/group 可用它引用这个新节点。"
                                    ),
                                },
                                "id": {
                                    "type": "string",
                                    "description": (
                                        "画布上【已存在】元素的真实 id（move/set_text/delete/"
                                        "connect 指向旧元素时用）。新建元素用 ref，不用 id。"
                                    ),
                                },
                                "kind": {
                                    "type": "string",
                                    "enum": list(_NODE_KINDS),
                                    "description": "add_node：节点形状，默认 sticky（便利贴）。",
                                },
                                "text": {
                                    "type": "string",
                                    "description": "add_node / set_text：节点上的文字。",
                                },
                                "x": {"type": "number", "description": "可选：左上角 x 坐标。"},
                                "y": {"type": "number", "description": "可选：左上角 y 坐标。"},
                                "width": {"type": "number", "description": "可选：宽度。"},
                                "height": {"type": "number", "description": "可选：高度。"},
                                "color": {
                                    "type": "string",
                                    "description": "可选：颜色提示（如 'blue'、'#e64980'）。",
                                },
                                "from": {
                                    "type": "string",
                                    "description": "connect：起点的 ref 或 id。",
                                },
                                "to": {
                                    "type": "string",
                                    "description": "connect：终点的 ref 或 id。",
                                },
                                "label": {
                                    "type": "string",
                                    "description": "connect：可选的箭头标签。",
                                },
                                "members": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "group：要成组的元素（ref 或 id）列表。",
                                },
                            },
                            "required": ["op"],
                        },
                    },
                    "summary": {
                        "type": "string",
                        "description": "一句话说明你这次在白板上做了什么（给用户看）。",
                    },
                },
                "required": ["ops"],
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
                error="board_ops 仅在白板会话中可用：当前会话没有绑定白板，无法作画。",
            )

        ops = arguments.get("ops")
        if not isinstance(ops, list) or not ops:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="board_ops 需要非空的 ops 数组（至少一个操作）。",
            )
        invalid = self._validate(ops)
        if invalid:
            return ToolResult(tool_call_id="", success=False, output="", error=invalid)

        summary = str(arguments.get("summary") or "").strip()
        logger.info(
            "board.ops_apply",
            run_id=context.run_id,
            board_id=channel.board_id,
            op_count=len(ops),
        )
        try:
            result = await channel.request(ops, summary=summary)
        except BoardOpError as e:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=f"白板操作未应用：{e}。可告知用户或稍后重试。",
            )
        return ToolResult(
            tool_call_id="",
            success=True,
            output=self._format_result(len(ops), result),
        )

    @staticmethod
    def _validate(ops: list[Any]) -> str | None:
        """Return an error string if any op is malformed, else None.

        Lightweight, model-facing validation: catch the mistakes that would make the
        desktop applier reject the batch (unknown verb, an edit/connect with no target)
        and tell the model how to fix it, rather than round-tripping a doomed batch.
        """
        for i, op in enumerate(ops):
            if not isinstance(op, dict):
                return f"ops[{i}] 必须是对象。"
            verb = op.get("op")
            if verb not in _OP_KINDS:
                return f"ops[{i}].op 非法：{verb!r}，应为 {list(_OP_KINDS)} 之一。"
            if verb in ("move", "set_text", "delete") and not (op.get("id") or op.get("ref")):
                return f"ops[{i}]（{verb}）需要 id 或 ref 指向目标元素。"
            if verb == "connect" and not (op.get("from") and op.get("to")):
                return f"ops[{i}]（connect）需要 from 和 to（端点的 ref 或 id）。"
            if verb == "group" and not op.get("members"):
                return f"ops[{i}]（group）需要 members（要成组的 ref/id 列表）。"
        return None

    @staticmethod
    def _format_result(op_count: int, result: dict[str, Any]) -> str:
        """Summarise what landed for the model (so it can confirm to the user)."""
        applied = result.get("applied", op_count)
        created = result.get("created") or []
        version = result.get("version")
        parts = [f"已在白板应用 {applied} 个操作"]
        if created:
            parts.append(f"，新增 {len(created)} 个元素")
        if version is not None:
            parts.append(f"（白板版本 {version}）")
        return "".join(parts) + "。"
