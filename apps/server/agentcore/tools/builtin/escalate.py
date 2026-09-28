"""escalate — worker 向上请示：等拍板，或自己这份做完但后面要主管看。

Worker-only. 不进 CEO 工具表。工人不能问用户，挡路时走这里；主管用
``ask_user`` / ``replan`` 的 tell 接。

``reason`` 二选一（缺省 / 无法识别 = ``wait``，宁停不留言）：

- ``wait``：猜错后面白干 → 原地挂起。经典路径直挂用户；协调模式等主管
  用 ``replan`` 的 tell 把决定说回来。须写 ``assumption``（「按假设继续」、未武装 /
  并发满退化、或运维超时回落都落在这句上）。
- ``adjust``：自己这份做完，但后面的安排要主管看。人不停。

不认旧参数 ``blocking`` / ``kind``。留言式上报已撤：小假设写进交差。
机制（挂起 / 并发帽 / SSE / RunState）在 ``ToolContext.escalation`` 与
``on_escalate``，本工具只做理由分流与回执。
"""

from __future__ import annotations

from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.text import clip_preview
from agentcore.core.types import ToolApproval, ToolFace
from agentcore.runtime.runs.constants import ESCALATE_TOOL_NAME
from agentcore.runtime.runs.escalate_reason import parse_escalate_reason
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_WORKER_ONLY,
    ToolRegistration,
    ToolSurface,
)

logger = get_logger(__name__)


class EscalateTool:
    """Worker's upward channel: wait, or flag a plan correction while finishing."""

    registration = ToolRegistration(
        surface=ToolSurface.WORKER_ONLY,
        audience=AUDIENCE_WORKER_ONLY,
        catalog_summary="队员向上请示",
        blurb="队员把卡住的事交给 CEO 定",
    )

    @property
    def schema(self) -> ToolSchema:
        from agentcore.tools.builtin.ask_user.schema import questions_array_schema

        return ToolSchema(
            name=ESCALATE_TOOL_NAME,
            description=(
                "向上请示：必须等人定，或自己这份做完但后面的安排要主管看。"
                "小假设写进交差，不要用这工具留言。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "要拍板或要改安排的问题。",
                    },
                    "reason": {
                        "type": "string",
                        "enum": ["wait", "adjust"],
                        "description": (
                            "缺省 wait。wait=停下等拍板。adjust=自己这份做完，"
                            "后面的安排要主管看。已拒凭据不要 wait。"
                        ),
                    },
                    "assumption": {
                        "type": "string",
                        "description": (
                            "wait 必填。对方按假设继续时用。adjust 写你打算怎么做完自己这份。"
                        ),
                    },
                    "questions": questions_array_schema(
                        description="仅 wait 出卡。",
                    ),
                },
                "required": ["question"],
            },
            face=ToolFace.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        question = str(arguments.get("question") or "").strip()
        if not question:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="escalate 需要非空的 question（写清你要上级拍板或改安排的问题）。",
            )
        assumption = str(arguments.get("assumption") or "").strip()
        reason = parse_escalate_reason(arguments.get("reason"))
        if reason == "wait" and not assumption:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=(
                    "escalate(reason=wait) 必须写明 assumption：对方点「按假设继续」、"
                    "未能挂起、或运维超时未答复时，你将按它继续。"
                ),
            )
        logger.info(
            "worker.escalate",
            run_id=context.run_id,
            reason=reason,
            has_assumption=bool(assumption),
            question=clip_preview(question, 200),
            assumption=clip_preview(assumption, 160),
        )
        from agentcore.tools.builtin.ask_user.schema import (
            ListArgError,
            normalize_questions,
        )

        channel = context.escalation
        if reason == "wait" and channel is not None and channel.armed:
            try:
                questions = normalize_questions(arguments.get("questions"))
            except ListArgError as exc:
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=(
                        f"{exc} 请直接传 JSON 数组，不要把数组再序列化成字符串。"
                    ),
                )
            awaiting = "user"
            try:
                from agentcore.runtime.coordination.session import (
                    resolve_coordination_session,
                )

                coord = resolve_coordination_session(context.execution_id)
                if coord is not None and coord.active:
                    awaiting = "ceo"
            except Exception:  # noqa: BLE001
                awaiting = "user"
            if awaiting == "ceo":
                from agentcore.runtime.coordination.session import (
                    note_coord_worker_busy,
                )

                note_coord_worker_busy(context.run_id, "arbitrate")
            outcome = await channel.request(
                question,
                assumption,
                questions,
                reason,
                awaiting,
            )
            if outcome.status != "degraded":
                return escalate_tool_result(
                    outcome.status,
                    outcome.answer,
                    assumption,
                    arbitrated_by=awaiting,
                )
        if reason == "adjust" and context.on_escalate is not None:
            try:
                context.on_escalate(question, assumption, reason)
            except Exception:  # noqa: BLE001 — liveliness only; never break the worker
                logger.warning("worker.escalate.emit_failed", run_id=context.run_id)
        try:
            from agentcore.runtime.coordination.bridge import post_escalation_to_coordination

            post_escalation_to_coordination(
                run_id=context.run_id,
                role=context.agent_role or "",
                reason=reason,
                question=question,
                assumption=assumption,
                source="escalate",
                execution_id=context.execution_id,
            )
        except Exception:  # noqa: BLE001
            logger.warning("worker.escalate.coordination_route_failed", run_id=context.run_id)
        if reason == "adjust":
            note = "已记下。请把你这份做完；后面的安排主管会看。"
        else:
            note = (
                "未能原地挂起，请按你写明的假设把这份做完。"
                if assumption
                else "未能原地挂起。请把假设写进交差，方便主管纠偏。"
            )
        if assumption and reason == "adjust":
            note += "你已写明假设，主管能据此判断要不要返工。"
        return ToolResult(tool_call_id="", success=True, output=note)


def escalate_tool_result(
    status: str,
    answer: str | None,
    assumption: str,
    *,
    arbitrated_by: str = "user",
) -> ToolResult:
    """Map a wait-escalate outcome to the CONTINUE result the worker loop consumes.

    - ``"resolved"`` → feed the ``answer`` back into the worker's loop;
    - ``"assumed"`` → explicit 按假设继续 (user or CEO);
    - ``"timed_out"`` → wall-clock miss;
    - both assumed and timed_out steer the worker onto its stated ``assumption``.

    Never terminal: wait resumes the worker; ending the user turn is CEO
    ``ask_user`` / 对话级, never a single worker's call.
    """
    if status == "resolved":
        ans = (answer or "").strip()
        if arbitrated_by == "ceo":
            output = (
                f"主管就你的升级问题裁决：\n{ans}\n"
                "请据此继续；与你的暂定假设冲突处以裁决为准，并回改已按假设做出的部分。"
            )
        else:
            output = (
                f"用户就你的升级问题答复：\n{ans}\n"
                "请据此继续；与你的暂定假设冲突处以用户答复为准，并回改已按假设做出的部分。"
            )
        return ToolResult(tool_call_id="", success=True, output=output)
    who = "主管" if arbitrated_by == "ceo" else "用户"
    lead = (
        f"{who}选择按你的假设继续。"
        if status == "assumed"
        else f"未在时限内得到{who}答复。"
    )
    return ToolResult(
        tool_call_id="",
        success=True,
        output=f"{lead}请按你写明的假设把任务继续做完：{assumption}。",
    )
