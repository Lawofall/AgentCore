"""AskUserTool: CEO asking primitive (blocking suspend)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolEffect, ToolFace, new_id
from agentcore.runtime.events import (
    EventSink,
    checkpoint_required,
)
from agentcore.tools.builtin.ask_user.intent import resolve_ask_checkpoint_intent
from agentcore.tools.builtin.ask_user.schema import (
    ASK_LABEL_HOW,
    ASK_PROMPT_HOW,
    ASK_RETRY_HINT,
    ASK_WHEN,
    ListArgError,
    advertised_option_actions,
    card_stem,
    normalize_questions,
    questions_array_schema,
)
from agentcore.tools.builtin.ask_user.suspend import persist_suspension
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_CEO_ONLY,
    CeoWire,
    ToolRegistration,
    ToolSurface,
)

if TYPE_CHECKING:
    from agentcore.runtime.suspension import SuspensionDeleter, SuspensionSaver

logger = get_logger(__name__)


@dataclass
class AskUserTool:
    """The CEO's asking primitive: surface a card, suspend, resume on the answer.

    Constructed per turn where the sink is available (mirrors ``ApprovalGate`` /
    ``GraphTool``): ``sink`` carries the prompt + resolution to the client,
    ``timeout_seconds`` is the ops-configured wait bound (default unlimited / D2 —
    ``None`` at settings maps to a large sentinel).

    结构化挂起 2b + 挂起即收口 (②) / D11: when ``message_id`` + the suspension closures
    are wired (live CEO path), the pause is persisted to ``paused_turns`` and the turn
    ends in place (``ToolEffect.SUSPEND``); resume is the single cold path
    ``POST .../resume``. The frame needs the turn-level constants (``captain_run_id`` /
    ``base_system_prompt`` / ``user_message``) to re-wire the CEO toolset on resume.
    If the durable frame cannot be saved ⇒ **explicit failure** (no in-memory timed
    wait, or auto-continue on timeout — the narrow兜底 was deleted).
    """

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.CHECKPOINT,
        catalog_summary="向用户提问或请拍板",
        blurb="把选择题或填空推到对话里等人回",
    )

    sink: EventSink
    conversation_id: str
    timeout_seconds: float | None
    captain_run_id: str | None = None
    base_system_prompt: str = ""
    user_message: str = ""
    # Prior conversation turns (same shape as GraphTool._history); captured on
    # suspend for resume parity. Leftover team_preview / plan_review resolved
    # facts do not skip a live ask.
    history: list[dict[str, Any]] | None = None
    message_id: str | None = None
    suspension_saver: SuspensionSaver | None = None
    suspension_deleter: SuspensionDeleter | None = None
    # The cloud project (= workspace folder) scope, carried so a durable ask_user pause
    # captures it into the frame — the resumed toolset re-wires consult to the same
    # project (Agent记忆与知识系统 §二). ``None`` for 裸聊 / local. Capture-only (unused live).
    folder_id: str | None = None
    # Unused for schema: open/bind folder is human-side. Kept so assemble / resume
    # signatures stay; ``advertised_option_actions`` is always empty.
    advertise_bind_local_folder: bool = False
    workspace_location: str | None = None

    @property
    def schema(self) -> ToolSchema:
        # Schema: when-to-use + 填卡合同（写参当轮必见）。卡形与 escalate 共用；
        # 推荐 / 桌上结果只叠在本按钮。开夹不进本按钮。
        allowed_actions = advertised_option_actions(
            desktop=self.advertise_bind_local_folder,
            workspace_location=self.workspace_location,
        )
        option_extras: dict[str, Any] = {
            "label": {"type": "string", "description": ASK_LABEL_HOW},
        }
        if allowed_actions:
            bits: list[str] = []
            if "open_local_project" in allowed_actions:
                bits.append("open/register/bind_local_*")
            option_extras["action"] = {
                "type": "string",
                "enum": list(allowed_actions),
                "description": "可选。整题接到工作区才填：" + "；".join(bits) + "。",
            }
        return ToolSchema(
            name="ask_user",
            description=ASK_WHEN,
            parameters={
                "type": "object",
                "properties": {
                    "questions": questions_array_schema(
                        description="问句写 prompt（1–5 道）。",
                        min_items=1,
                        option_properties=option_extras,
                        prompt_description=ASK_PROMPT_HOW,
                    ),
                },
                "required": ["questions"],
            },
            face=ToolFace.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            questions = normalize_questions(arguments.get("questions"))
        except ListArgError as exc:
            logger.info(
                "ask_user.list_arg_rejected",
                conversation_id=self.conversation_id,
                error=str(exc),
            )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=(
                    f"{exc} 请直接传 JSON 数组，不要把数组再序列化成字符串。"
                    f"{ASK_RETRY_HINT}"
                ),
            )
        if not questions:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=(
                    "ask_user 需要至少一道 question（问句写 prompt）。"
                    f"只问一句话也出一道题。{ASK_RETRY_HINT}"
                ),
            )
        stem = card_stem(questions)

        allowed = set(
            advertised_option_actions(
                desktop=self.advertise_bind_local_folder,
                workspace_location=self.workspace_location,
            )
        )
        for q in questions:
            for opt in q.get("options") or []:
                if not isinstance(opt, dict):
                    continue
                action = str(opt.get("action") or "").strip()
                if action not in allowed:
                    opt.pop("action", None)
                opt.pop("well_known", None)
                opt.pop("target_name", None)

        checkpoint_id = new_id()
        from agentcore.runtime.suspension import captain_transcript

        intent = resolve_ask_checkpoint_intent(captain_transcript.get())
        required = checkpoint_required(
            checkpoint_id=checkpoint_id,
            conversation_id=self.conversation_id,
            question=stem,
            questions=questions,
            intent=intent,
        )
        # 结构化挂起 2b + D11: persist the durable frame BEFORE finalize. Save success
        # ⇒ 挂起即收口 (②); save failure ⇒ explicit error (no in-memory wait fallback).
        # CEO 协调模式 Phase 2: snapshot coordination state into the journal before
        # SUSPEND so resume can rebuild draft / completed / budget.
        from agentcore.runtime.graph.session.session import resolve_coordination_session

        coord = resolve_coordination_session(context.execution_id)
        if coord is not None:
            from agentcore.runtime.graph.session.journal import record_coordination_snapshot

            record_coordination_snapshot(coord)
            # Soft-stop the background scheduler — resume re-drives unfinished workers
            # from the journal seed. Cancelling avoids orphan tasks after turn end.
            # Mark soft_stop BEFORE cancel so the drive cancel handler skips wake
            # events (no ALL_COMPLETED / DRIVE_CANCELLED in the hang-frame snapshot).
            if coord.drive_task is not None and not coord.drive_task.done():
                coord.soft_stop = True
                from agentcore.runtime.graph.session.drive_cancel import (
                    cancel_drive_task,
                )

                cancel_drive_task(coord, "soft_stop")
        try:
            saved = await persist_suspension(
                self,
                checkpoint_id=checkpoint_id,
                context=context,
                message=stem,
                questions=questions,
                required_event=required,
                intent=intent,
            )
        except Exception:
            # D11：运行态落帧失败 ⇒ 显式失败终止回合（与配置态不可用同文案）。
            logger.exception(
                "checkpoint.persist_failed",
                checkpoint_id=checkpoint_id,
            )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="无法持久化检查点，回合已终止。请重试。",
            )
        # 挂起即收口 (②): once the durable frame is saved, END the turn in place.
        # D11：删窄兜底——无法落盘则显式失败终止回合（不再假等待）。
        if saved:
            self.sink.emit(required)
            logger.info(
                "checkpoint.finalized",
                checkpoint_id=checkpoint_id,
                intent=intent,
                n_questions=len(questions),
                n_options=sum(len(q.get("options") or []) for q in questions),
            )
            return ToolResult(tool_call_id="", success=True, output="", effect=ToolEffect.SUSPEND)
        logger.error(
            "checkpoint.persist_unavailable",
            checkpoint_id=checkpoint_id,
            reason="no_durable_frame",
        )
        return ToolResult(
            tool_call_id="",
            success=False,
            output="无法持久化检查点，回合已终止。请重试。",
        )
