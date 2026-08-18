"""AskUserTool: CEO asking primitive (blocking suspend + non-blocking surface)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory, ToolEffect, new_id
from agentcore.runtime.events import (
    EventSink,
    checkpoint_required,
    question_posted,
)
from agentcore.tools.builtin.ask_user.card import (
    CARD_KINDS,
    CARD_RETRY_HINT,
    card_max_options,
    card_overrides_intent,
    parse_card,
    validate_card_shape,
)
from agentcore.tools.builtin.ask_user.intent import resolve_ask_checkpoint_intent
from agentcore.tools.builtin.ask_user.schema import (
    ListArgError,
    OptionLabelError,
    normalize_assumptions,
    normalize_questions,
    normalize_unlocks,
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
    ``DelegateTool``): ``sink`` carries the prompt + resolution to the client,
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
    )

    sink: EventSink
    conversation_id: str
    timeout_seconds: float | None
    captain_run_id: str | None = None
    base_system_prompt: str = ""
    user_message: str = ""
    # Prior conversation turns (same shape as DelegateTool._history); captured on
    # suspend for resume parity. ask_user ⊥ kickoff/team_preview — no skip via
    # journal checkpoint_resolved.
    history: list[dict[str, Any]] | None = None
    message_id: str | None = None
    suspension_saver: SuspensionSaver | None = None
    suspension_deleter: SuspensionDeleter | None = None
    # The cloud project (= workspace folder) scope, carried so a durable ask_user pause
    # captures it into the frame — the resumed toolset re-wires consult to the same
    # project (Agent记忆与知识系统 §二). ``None`` for 裸聊 / local. Capture-only (unused live).
    folder_id: str | None = None
    # Caller-supplied memory gate, captured so resume re-wires consult as this
    # turn did (False ⇒ stays off). Capture-only; defaults True (product always-on).
    memory_enabled: bool = True
    # Caller-supplied conversation-log access gate, captured for resume wire parity.
    conversation_history_access: bool = True
    # Advertise desktop-only ask_user option actions (open_local_project /
    # register_local_project / bind_local_folder / grant_readonly_folder /
    # grant_organize_folder) when the desktop client can fulfil them.
    advertise_bind_local_folder: bool = False

    @property
    def schema(self) -> ToolSchema:
        option_properties: dict[str, Any] = {
            "label": {
                "type": "string",
                "description": (
                    "选项名（即用户选它时回传的答案）。禁止写入「（推荐）」等推荐标记；"
                    "倾向只设 recommended。"
                ),
            },
            "detail": {
                "type": "string",
                "description": (
                    "可选：这个选项的一行权衡 / 代价，展示在选项下方，帮用户看懂「为什么选它」。"
                ),
            },
            "recommended": {
                "type": "boolean",
                "description": (
                    "可选：建议项（至多一个）。灰字「推荐」、不预选；预选用 default。"
                    "勿把「（推荐）」写入 label。"
                ),
            },
        }
        # Schema: short trigger. HOW → ask_user_kickoff / ask_user_midtask skills.
        questions_desc = (
            "可选：要用户拍板的问题（最多 5）。关键岔路通常预填或省略 default。"
            "choice 可配 detail / recommended。"
        )
        tool_desc = (
            "向用户发问（唯一问用户原语）。默认 blocking 暂停回合；"
            "blocking=false 非阻塞（须写 unlocks；后半等人）。"
            "登录拦截：browser_login=true（强制阻塞）。"
            "挡路才问；能按默认推进则不当检查点。"
            "HOW→consult(ask_user_kickoff / ask_user_midtask)。"
        )
        if self.advertise_bind_local_folder:
            # Short discriminators only — HOW lives in ask_user_* skills.
            option_properties["action"] = {
                "type": "string",
                "enum": [
                    "open_local_project",
                    "register_local_project",
                    "bind_local_folder",
                    "grant_readonly_folder",
                    "grant_organize_folder",
                ],
                "description": (
                    "可选。open/register/bind_local_*=本机传统（合法非默认，云仍推荐）；"
                    "grant_organize_folder=区外整理授权（口头同意须立刻发卡）；"
                    "grant_readonly_folder 禁止新发（只读用 external_mount_readonly）。"
                ),
            }
            option_properties["well_known"] = {
                "type": "string",
                "enum": ["desktop", "downloads", "documents"],
                "description": (
                    "仅 grant_*。点名桌面/下载/文档时填；模糊可省略。解析失败即找不到。"
                ),
            }
            option_properties["target_name"] = {
                "type": "string",
                "description": (
                    "仅 grant_*。子目录/压缩包模糊名（禁 / \\）；有 well_known 时在其下匹配。"
                ),
            }
            option_properties["path"] = {
                "type": "string",
                "description": (
                    "仅 grant_*。已知运输 path（与 well_known/target_name 互补；"
                    "歧义候选宜各不同）。"
                ),
            }
            # Discriminators stay on the tool description so the model sees them
            # without opening options.action; HOW still lives in ask_user_* skills.
            tool_desc += (
                " 桌面 options.action：open/register/bind_local_*（本机传统）/"
                "grant_organize_folder（口头同意须立刻发卡；歧义 2～3）/"
                "grant_readonly_folder 禁止新发（只读用 external_mount_readonly）。"
            )

        return ToolSchema(
            name="ask_user",
            description=tool_desc,
            parameters={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "必填。卡片顶部说明（问什么、为何需拍板）。",
                    },
                    "context": {
                        "type": "string",
                        "description": "可选：背景补充。",
                    },
                    "assumptions": {
                        "type": "array",
                        "description": (
                            "可选：低影响默认可逆决策（只读陈列）。高杠杆放 questions。"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {
                                    "type": "string",
                                    "description": "决策项。",
                                },
                                "value": {
                                    "type": "string",
                                    "description": "默认值。",
                                },
                            },
                            "required": ["label", "value"],
                        },
                    },
                    "questions": {
                        "type": "array",
                        "description": questions_desc,
                        "items": {
                            "type": "object",
                            "properties": {
                                "prompt": {
                                    "type": "string",
                                    "description": "问题正文。",
                                },
                                "kind": {
                                    "type": "string",
                                    "enum": ["choice", "text"],
                                    "description": "choice 或 text，默认 choice。",
                                },
                                "options": {
                                    "type": "array",
                                    "description": "kind=choice 候选项（最多 6）。",
                                    "items": {
                                        "type": "object",
                                        "properties": option_properties,
                                        "required": ["label"],
                                    },
                                },
                                "multiple": {
                                    "type": "boolean",
                                    "description": "可选：允许多选，默认 false。",
                                },
                                "default": {
                                    "type": "string",
                                    "description": "可选默认答案（choice=某 label）。",
                                },
                            },
                            "required": ["prompt"],
                        },
                    },
                    "blocking": {
                        "type": "boolean",
                        "description": (
                            "可选，默认 true。false=非阻塞（须在 assumptions/default 写明默认，"
                            "且须写 unlocks）。"
                        ),
                    },
                    "unlocks": {
                        "type": "string",
                        "description": (
                            "非阻塞必填：这个答案回来后解锁哪批活。"
                            "纯知会写正文，勿走本工具。"
                        ),
                    },
                    "browser_login": {
                        "type": "boolean",
                        "description": (
                            "true=请用户在右坞登录（AI 不经手密码）；强制 blocking。"
                            "典型：password 框硬拒（code=password_blocked）。"
                        ),
                    },
                    "card": {
                        "type": "string",
                        "enum": ["proposal_pick", "risk_ack", "organize_plan", "daily_review"],
                        "description": (
                            "可选卡型（须 blocking 且恰好 1 题）：proposal_pick 单选方案；"
                            "risk_ack/organize_plan/daily_review 多选。"
                            "多问题勿用 card（普通 ask_user，questions≤5）。"
                        ),
                    },
                },
                "required": ["message"],
            },
            category=ToolCategory.INTERACTION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        message = str(arguments.get("message") or "").strip()
        if not message:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="ask_user 需要非空的 message 参数（向用户说明你在问什么）。",
            )
        card_parsed = parse_card(arguments.get("card"))
        # Success returns a known card literal (also a str); errors return a Chinese
        # guidance string not in CARD_KINDS.
        if card_parsed is None:
            card = None
        elif card_parsed in CARD_KINDS:
            card = card_parsed  # type: ignore[assignment]
        else:
            logger.info(
                "ask_user.card_rejected",
                conversation_id=self.conversation_id,
                card=str(arguments.get("card")),
                reason="unknown",
            )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=str(card_parsed) + CARD_RETRY_HINT,
            )

        ctx_text = str(arguments.get("context") or "")
        try:
            assumptions = normalize_assumptions(arguments.get("assumptions"))
            questions = normalize_questions(
                arguments.get("questions"),
                max_options=card_max_options(card),
            )
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
                    f"{CARD_RETRY_HINT}"
                ),
            )
        except OptionLabelError as exc:
            logger.info(
                "ask_user.option_label_rejected",
                conversation_id=self.conversation_id,
                error=str(exc),
            )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=f"{exc}{CARD_RETRY_HINT}",
            )
        if not self.advertise_bind_local_folder:
            for q in questions:
                for opt in q.get("options") or []:
                    if isinstance(opt, dict):
                        opt.pop("action", None)
                        opt.pop("well_known", None)
                        opt.pop("target_name", None)
                        opt.pop("path", None)

        # 非阻塞发问 (Cursor 式): surface + proceed, never freeze the turn. Branch BEFORE
        # any suspend / durable-frame machinery — it shares none of it.
        # browser_login forces blocking (CEO dual of escalate browser_login).
        browser_login = bool(arguments.get("browser_login"))
        blocking_arg = arguments.get("blocking")
        blocking = True if blocking_arg is None else bool(blocking_arg)
        if browser_login:
            blocking = True

        if card is not None:
            card_err = validate_card_shape(card, blocking=blocking, questions=questions)
            if card_err:
                # Observability for the recurring model fumble (e.g. kickoff-style
                # multi-question asks tagged card=proposal_pick): count + shape details.
                logger.info(
                    "ask_user.card_rejected",
                    conversation_id=self.conversation_id,
                    card=card,
                    reason="shape",
                    questions=len(questions),
                    blocking=blocking,
                )
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=card_err + CARD_RETRY_HINT,
                )

        if not blocking:
            unlocks = normalize_unlocks(arguments.get("unlocks"))
            return self._post_nonblocking(
                message, ctx_text, assumptions, questions, unlocks
            )

        checkpoint_id = new_id()
        from agentcore.runtime.suspension import captain_transcript

        intent = (
            card_overrides_intent(card)
            if card is not None
            else resolve_ask_checkpoint_intent(captain_transcript.get())
        )
        required = checkpoint_required(
            checkpoint_id=checkpoint_id,
            conversation_id=self.conversation_id,
            question=message,
            context=ctx_text,
            assumptions=assumptions,
            questions=questions,
            intent=intent,
            browser_login=True if browser_login else None,
        )
        # 结构化挂起 2b + D11: persist the durable frame BEFORE finalize. Save success
        # ⇒ 挂起即收口 (②); save failure ⇒ explicit error (no in-memory wait fallback).
        # CEO 协调模式 Phase 2: snapshot coordination state into the journal before
        # SUSPEND so resume can rebuild draft / completed / budget.
        from agentcore.runtime.coordination.session import active_coordination

        coord = active_coordination(context.execution_id)
        if coord is not None:
            from agentcore.runtime.coordination.journal import record_coordination_snapshot

            record_coordination_snapshot(coord)
            # Soft-stop the background scheduler — resume re-drives unfinished workers
            # from the journal seed. Cancelling avoids orphan tasks after turn end.
            # Mark soft_stop BEFORE cancel so the drive cancel handler skips wake
            # events (no ALL_COMPLETED / DRIVE_CANCELLED in the hang-frame snapshot).
            if coord.drive_task is not None and not coord.drive_task.done():
                coord.soft_stop = True
                coord.drive_task.cancel()
        try:
            saved = await persist_suspension(
                self,
                checkpoint_id=checkpoint_id,
                context=context,
                message=message,
                ctx_text=ctx_text,
                assumptions=assumptions,
                questions=questions,
                required_event=required,
                intent=intent,
                browser_login=browser_login,
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
                card=card,
                browser_login=browser_login,
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

    def _post_nonblocking(
        self,
        message: str,
        ctx_text: str,
        assumptions: list[dict[str, Any]],
        questions: list[dict[str, Any]],
        unlocks: str,
    ) -> ToolResult:
        """非阻塞发问：抛出确认但不挂起——按默认做前半，或声明后半等人。

        The counterpart to suspend+resume: rather than freezing the turn on the user's
        answer, surface the question as a non-gating ``question_posted`` card (the client
        renders chips that 回填 the composer; the answer rides an ordinary next-turn
        message) and feed the CEO a ``CONTINUE``: keep the independent work going on the
        stated default; do **not** dispatch the ``unlocks`` batch until the answer
        returns. Guarded at this same checkpoint: a non-blocking ask MUST carry a
        fallback (an assumption, or a question ``default``) **and** an ``unlocks``
        declaration (which later work this answer unlocks) — without the former the user
        can't trust the CEO to proceed; without the latter the answer cannot re-enter
        the flow. Missing fallback steers to ``blocking=true``; missing ``unlocks``
        steers to fill the field or write a notify in prose (not a card). No suspend /
        frame / extra round, so it costs nothing the worker-side ``escalate`` doesn't.
        """
        has_fallback = bool(assumptions) or any(q.get("default") for q in questions)
        if not has_fallback:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=(
                    "非阻塞发问（blocking=false）必须写明你将先采用的默认：在 assumptions "
                    "列出你的暂定决策，或给某个 question 填 default。否则用户无从判断能否放心"
                    "不管。若你确实要等用户拍板再动，请改用 blocking=true。"
                ),
            )
        if not unlocks:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=(
                    "非阻塞发问（blocking=false）必须写 unlocks：这个答案回来后你要据此派哪批活。"
                    "缺了它答案无法回到流程。纯知会不要走本工具，写进正文。"
                ),
            )
        ask_id = new_id()
        self.sink.emit(
            question_posted(
                ask_id=ask_id,
                conversation_id=self.conversation_id,
                question=message,
                context=ctx_text,
                assumptions=assumptions,
                questions=questions,
                unlocks=unlocks,
            )
        )
        from agentcore.attention import AttentionKind, signal_hot_card_required

        signal_hot_card_required(
            interaction_id=ask_id,
            kind=AttentionKind.QUESTION_POSTED,
            conversation_id=self.conversation_id,
            payload={"question": message},
        )
        logger.info(
            "ask_user.nonblocking",
            conversation_id=self.conversation_id,
            questions=len(questions),
            assumptions=len(assumptions),
        )
        return ToolResult(
            tool_call_id="",
            success=True,
            output=(
                "已（非阻塞）把这个确认抛给用户。不挂起本回合。"
                "按你写明的默认，只继续 unlocks 影响不到的活；unlocks 那批先不派，答案回来再追加。"
                "收口须明说「能做的做完了，后半等你」。"
                "禁止偷偷按默认把依赖批做完，禁止把半程说成交付。"
                "用户答复随后续消息到达。"
            ),
        )
