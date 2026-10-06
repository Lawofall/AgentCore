"""DebateTool — CEO 发起结构化正反辩论的编排原语。"""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.core.types import (
    DEFAULT_PERMISSION_AXES,
    ToolApproval,
    ToolEffect,
    ToolFace,
    WorkspaceBoundary,
    new_id,
)
from agentcore.llm.profiles import TurnProfiles as ProfileSet
from agentcore.llm.profiles import default_turn_profiles as default_profile_set
from agentcore.llm.provider.protocol import LLMProvider
from agentcore.runtime.debate import (
    DebateConfig,
    DebateForm,
    Moderator,
    RoundBoundary,
    RoundDecision,
    RoundPolicy,
    RoundResult,
)
from agentcore.runtime.debate.events import moderator_plan_event, settle_moderator_node
from agentcore.runtime.debate.rounds import (
    make_cross_exam_runner,
    make_round_runner,
)
from agentcore.runtime.debate.steer_queue import (
    close_steer_window,
    fold_steers,
    open_steer_window,
    take_steers,
)
from agentcore.runtime.events import (
    EventSink,
    debate_result,
    debate_round,
    debate_round_started,
    run_started,
)
from agentcore.runtime.plan_only import PlanOnlyAbortError
from agentcore.tools.builtin.debate.schema import (
    DEBATE_DESCRIPTION,
    DEBATE_OUTPUT_LIMIT,
    DEBATE_PARAMETERS,
    err,
    parse_background,
    parse_moderator_fields,
    parse_sides,
)
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_CEO_ONLY,
    CeoWire,
    ToolRegistration,
    ToolSurface,
)
from agentcore.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from agentcore.runtime.approvals import ApprovalGate
    from agentcore.runtime.costing import RunCost
    from agentcore.runtime.ports import ClientRequestBridge
    from agentcore.runtime.runs.session import RunSession
    from agentcore.runtime.suspension import SuspensionDeleter, SuspensionSaver

logger = get_logger(__name__)


class DebateTool:
    """CEO-agent tool：发起主持人驱动的结构化辩论，返回双产物供 CEO 收尾（非终结）。

    持有与 ``DelegateTool`` 同形的「用量 + 账目 + 引用」累加器（``_acc``），辩手 run（首轮
    executor、后续轮 continue_run）与主持人自身 LLM 调用都折算进去，由 pipeline 折回回合总账。
    ``_debater_sessions`` 按 side.key 留住每个辩手的可续写 session，支撑跨轮带记忆。

    顶层开辩直接开跑；遗留编制确认帧 resume 为 410。
    嵌套 / 续跑 / full_auto 跳过语义对齐 delegate。
    """

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.ALWAYS,
        catalog_summary="开一场正反辩论",
        blurb="正反两边对碰，再把结论收回来",
    )

    def __init__(
        self,
        *,
        llm: LLMProvider,
        sink: EventSink,
        system_prompt: str,
        user_message: str,
        tools: ToolRegistry,
        base_tool_context: ToolContext,
        profile_set: ProfileSet | None = None,
        max_parallel: int | None = None,
        captain_run_id: str | None = None,
        approval_gate: ApprovalGate | None,
        depth: int = 0,
        conversation_id: str = "",
        ambient_armed: bool = False,
        message_id: str | None = None,
        suspension_saver: SuspensionSaver | None = None,
        suspension_deleter: SuspensionDeleter | None = None,
        folder_id: str | None = None,
        permission_axes: WorkspaceBoundary | None = None,
        registry: ClientRequestBridge | None = None,
        session_store: Any = None,
        session_loader: Any = None,
    ) -> None:
        self._llm = llm
        self._sink = sink
        self._system_prompt = system_prompt
        self._user_message = user_message
        self._tools = tools
        self._base_tool_context = base_tool_context
        self._profile_set = profile_set or default_profile_set()
        self._max_parallel = max_parallel
        self._captain_run_id = captain_run_id
        self._approval_gate = approval_gate
        self._depth = depth
        # ambient 掌舵闸：有活跃用户即武装（同 ask_user 的 checkpoint 闸）——无活跃用户
        # （自治 / handoff）不挂 on_round_boundary，辩论纯裁判自判；有用户则轮次边界非阻塞
        # drain steer 队列（永不硬停）。
        self._conversation_id = conversation_id
        self._ambient_armed = ambient_armed
        self._message_id = message_id
        self._suspension_saver = suspension_saver
        self._suspension_deleter = suspension_deleter
        self._folder_id = folder_id
        self._permission_axes = permission_axes or DEFAULT_PERMISSION_AXES
        self._registry = registry
        # 构造签名保留（ceo_toolset 仍传入）；热路不探测证人席位。
        self._session_store = session_store
        self._session_loader = session_loader
        self._pending_pause = False
        # 每个 side 的可续写 session（跨轮带记忆）：首轮执行后留人，后续轮 continue_run 取用。
        self._debater_sessions: dict[str, RunSession] = {}
        from agentcore.runtime.costing import WorkerResultAccumulator
        from agentcore.runtime.debate.evidence_ledger import EvidenceLedger

        self._acc = WorkerResultAccumulator()
        self._evidence_ledger = EvidenceLedger()
        # 批 A2：挂宿主新幕时由决议机制写入；缺省 = 独立辩论图（act-1）。
        self._debate_act_id: str = "act-1"
        self._debate_act_title: str | None = None
        self._debate_anchor_run_id: str | None = None
        # 内部：解析宿主 journal 用；不再写入 run_plan.host_message_id。
        self._debate_host_message_id: str | None = None
        self._debate_prev_execution_id: str | None = None
        # 新图+prev：parent 用本回合 captain；act.anchor_run_id 仍指向上一图汇总员。
        self._debate_graph_parent_run_id: str | None = None
        # 批 B：幕授权来源 stage_card / auto / preview；缺省新路径补 auto（preview 仅存量 leftover）。
        self._debate_authorized_by: str | None = None
        # 主持人节点终帧只发一次（``settle_moderator_node`` 的幂等闸）。
        self._moderator_settled: bool = False

    @property
    def usage(self) -> dict[str, int]:
        """本回合辩论累计 token 用量（辩手 + 主持人；pipeline 折回回合总账）。"""
        return self._acc.usage

    @property
    def run_ledger(self) -> list[RunCost]:
        """每个计费 run 一行账目（辩手各一行 + 主持人一行，决策②）。"""
        return self._acc.run_ledger

    @property
    def citations(self) -> list[dict[str, Any]]:
        """辩手查阅的网页来源（去重，折入回合共享来源卡）。"""
        return self._acc.citations

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="debate",
            description=DEBATE_DESCRIPTION,
            parameters=DEBATE_PARAMETERS,
            face=ToolFace.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        from agentcore.llm.turn_auth_dead import (
            credential_source_from_llm,
            is_turn_auth_dead,
            turn_auth_dead_reject_message,
        )
        from agentcore.runtime.costing import usage_metadata
        from agentcore.runtime.turn.token_budget import (
            current_turn_tokens,
            is_turn_token_ceiling_hit,
            resolve_turn_token_ceiling,
            turn_token_ceiling_reject_message,
        )

        self._pending_pause = False
        payer = credential_source_from_llm(self._llm)
        # Turn 级硬顶：禁新开辩（与 delegate 同闸）。
        if is_turn_token_ceiling_hit():
            msg = turn_token_ceiling_reject_message()
            logger.info(
                "debate.turn_token_ceiling_rejected",
                spent=current_turn_tokens(),
                ceiling=resolve_turn_token_ceiling(),
            )
            return err(msg)

        if is_turn_auth_dead(payer):
            logger.info("debate.turn_auth_dead_rejected")
            return err(turn_auth_dead_reject_message(payer))

        # 开辩是独立重活：不消费推进卡。
        motion = str(arguments.get("motion") or "").strip()
        if not motion:
            return err("debate 需要 motion（辩论命题 / 要解决的问题）。")

        sides, side_err = parse_sides(arguments.get("sides"))
        if side_err:
            return err(side_err)
        form = DebateForm.DEBATE
        policy = RoundPolicy.for_form(form)
        try:
            max_rounds_arg = int(arguments["max_rounds"])  # type: ignore[index]
            if max_rounds_arg >= 1:
                policy = RoundPolicy(max_rounds=max_rounds_arg)
        except (KeyError, TypeError, ValueError):
            pass
        # `_opening_ask` 为内部键（非 schema / 非 wire），开赛嘱咐进首轮插话管道。
        opening_ask = str(arguments.get("_opening_ask") or "").strip()
        mod_model, mod_origin, mod_provider_id, mod_err = parse_moderator_fields(
            arguments.get("moderator_model"),
            arguments.get("moderator_origin"),
            arguments.get("moderator_provider_id"),
        )
        if mod_err:
            return err(mod_err)
        config = DebateConfig(
            motion=motion,
            form=form,
            sides=sides,
            policy=policy,
            background=parse_background(arguments.get("background")),
            opening_ask=opening_ask,
            moderator_model=mod_model,
            moderator_origin=mod_origin,
            moderator_provider_id=mod_provider_id,
            moderator_run_id=str(arguments.get("moderator_run_id") or "").strip(),
        )

        # §7.5：校验非空目录身份 + 解析裁判（点名优先；空=系统默认，可同模）。
        # 同模型场不读目录。要目录时：有窄票走 account HTTP，无票走本机 session。
        # 读不到就硬失败，不退回空目录。
        from agentcore.llm.turn_catalog import (
            DEBATE_CATALOG_UNAVAILABLE,
            require_turn_model_catalog,
        )
        from agentcore.runtime.debate.models import (
            collect_debate_identities,
            debate_needs_model_catalog,
            ensure_debate_route_extras,
            prepare_debate_model_plan,
        )

        turn_model = (self._profile_set.model or "").strip()
        user_id = (self._base_tool_context.user_id or "").strip()
        cross_model = arguments.get("cross_model", False) is True
        catalog = None
        if debate_needs_model_catalog(config, cross_model=cross_model):
            catalog, load_err = await require_turn_model_catalog(
                user_id, unavailable=DEBATE_CATALOG_UNAVAILABLE
            )
            if load_err:
                logger.warning("debate.model_plan_failed", error="catalog_unavailable")
                return err(load_err)
        model_err = await prepare_debate_model_plan(
            config,
            user_id=user_id,
            turn_model=turn_model,
            session=None,
            catalog=catalog,
            cross_model=cross_model,
            user_message=self._user_message or "",
        )
        if model_err:
            candidates = list(getattr(config, "model_candidates", None) or [])
            if candidates:
                from agentcore.tools.protocol import ToolResult

                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output=model_err,
                    error=model_err,
                    display={"model_candidates": candidates},
                    metadata={"model_candidates": candidates},
                    contract_failure=True,
                )
            return err(model_err)

        await ensure_debate_route_extras(
            self._llm,
            collect_debate_identities(config, turn_model=turn_model),
            user_id=user_id or None,
        )

        # 开赛前预分配稳定 run_id（model_overrides 键）；resume 复用。
        from agentcore.runtime.debate.models import allocate_debate_run_ids

        allocate_debate_run_ids(config, arguments)

        if self._depth == 0:
            from agentcore.runtime.deep_research_auto import (
                record_auto_debate,
                tool_may_auto_debate,
            )

            if tool_may_auto_debate(self):
                await record_auto_debate(self)
            self._debate_authorized_by = "auto"

        result = await self._run_moderator(config, usage_metadata)
        if not result.success:
            return result
        return result

    async def _resolve_host_attach(self, config: DebateConfig):
        """开辩独立成图，不链调研宿主。"""
        _ = config
        return None

    async def _run_moderator(self, config: DebateConfig, usage_metadata) -> ToolResult:
        if self._debate_authorized_by is None:
            self._debate_authorized_by = "auto"

        # 底料预登记：无 id 的【已核实·出处】→ 台账 #rN
        from agentcore.runtime.debate.evidence_ledger import (
            EvidenceLedger,
            preregister_background,
        )

        try:
            from agentcore.runtime.suspension import turn_evidence_ledger as _turn_led

            turn_core = _turn_led.get()
            if turn_core is not None:
                self._evidence_ledger = EvidenceLedger(core=turn_core)
        except Exception:  # noqa: BLE001
            logger.exception("debate.turn_ledger_attach_failed")

        if (config.background or "").strip():
            config.background = preregister_background(
                self._evidence_ledger, config.background
            )

        from agentcore.runtime.debate.host import host_graph_binding

        host_attach = await self._resolve_host_attach(config)
        if host_attach is not None:
            execution_id, prev = host_graph_binding(host_attach, mint_id=new_id)
            self._debate_prev_execution_id = prev
            self._base_tool_context.execution_id = execution_id
        else:
            execution_id = self._base_tool_context.execution_id or new_id()

        moderator_run_id = (getattr(config, "moderator_run_id", "") or "").strip() or (
            f"debate_{new_id()}"
        )
        config.moderator_run_id = moderator_run_id
        # §7.5：裁判选型（prepare_debate_model_plan）；无则回退 turn 主模型。
        moderator_model = (
            (config.moderator_route or "").strip()
            or (self._profile_set.model or "").strip()
        )
        graph_parent = self._debate_graph_parent_run_id or self._captain_run_id

        # 终帧兜底所需：节点是否已开播、主持人实例（用量来源）、开播时刻、失败文案。
        node_started = False
        moderator: Moderator | None = None
        started_at = time.monotonic()
        node_error = ""
        try:
            # 先声明主持人节点（CEO 之下 / 汇总员锚点经 act），辩手节点逐轮声明。
            self._sink.emit(
                moderator_plan_event(self, execution_id, moderator_run_id, config)
            )
            # 主持人作为完成态节点：开播 run_started，收尾必发一帧（见 settle_moderator_node）。
            self._sink.emit(
                run_started(
                    moderator_run_id,
                    moderator_run_id,
                    parent_run_id=graph_parent,
                )
            )
            node_started = True
            started_at = time.monotonic()
            started_fields = {
                "form": config.form.value,
                "sides": len(config.sides),
                "motion": config.motion[:80],
                "execution_id": execution_id,
                "act_id": self._debate_act_id,
                "host_attach": bool(host_attach),
                "prev_execution_id": self._debate_prev_execution_id,
            }
            logger.info("debate.started", **started_fields)

            moderator = Moderator(
                provider=self._llm,
                model=moderator_model,
                run_id=moderator_run_id,
                parent_run_id=graph_parent,
                sink=self._sink,
            )
            # 掌舵窗口开在主持人开跑处（开赛后入的队也能被首轮边界捞到）；无活跃用户
            # 时不开——没挂 on_round_boundary，谁都捞不走，收下就是骗人。
            if self._ambient_armed:
                open_steer_window(execution_id)
            runner = make_round_runner(self, execution_id, moderator_run_id, config)
            cross_exam_runner = make_cross_exam_runner(
                self, execution_id, moderator_run_id, config
            )
            # 新场不接线结辩 / 证人：runner 与席位探测留旧场回放与单测。

            from agentcore.runtime.debate.moderator_agenda import cross_exam_enabled

            cx_enabled = cross_exam_enabled(config)

            from agentcore.runtime.debate.evidence_pack import apply_opening_materials

            apply_opening_materials(
                system_prompt=getattr(self, "_system_prompt", "") or "",
                config=config,
                ledger=self._evidence_ledger,
            )

            async def _emit_round_start(round_no: int, focus: str, opening: str) -> None:
                self._sink.emit(
                    debate_round_started(
                        execution_id=execution_id,
                        moderator_run_id=moderator_run_id,
                        round_no=round_no,
                        focus=focus,
                        cross_exam_enabled=cx_enabled,
                        opening=opening,
                        form=config.form.value,
                    )
                )

            async def _emit_round(rr: RoundResult) -> None:
                payload = rr.to_event_payload()
                payload["evidence_ledger_delta"] = self._evidence_ledger.drain_delta()
                self._sink.emit(
                    debate_round(
                        execution_id=execution_id,
                        moderator_run_id=moderator_run_id,
                        payload=payload,
                    )
                )

            async def _round_boundary(
                *, round_no: int, result: RoundResult, converged: bool, max_rounds: int
            ) -> RoundBoundary | None:
                steers = take_steers(execution_id)
                boundary = fold_steers(steers)
                if boundary is not None:
                    logger.info(
                        "debate.steer.applied",
                        execution_id=execution_id,
                        round_no=round_no,
                        decision=boundary.decision.value,
                        n=len(steers),
                    )
                # 本边界之后还会不会再有一个边界来捞 steer —— 与 Moderator.run 的收场判定
                # 同源（用户 conclude 凌驾裁判；否则裁判 converged；轮数上限是硬顶）。不会
                # 再有 ⇒ 立刻关窗：其后的简报可达数十秒，那期间收下的掌舵永不生效。
                last_boundary = round_no >= max_rounds or (
                    boundary.decision is RoundDecision.CONCLUDE
                    if boundary is not None
                    else converged
                )
                if last_boundary:
                    dropped = close_steer_window(execution_id)
                    logger.info(
                        "debate.steer.window_closed",
                        execution_id=execution_id,
                        round_no=round_no,
                        dropped=dropped,
                    )
                return boundary

            try:
                result = await moderator.run(
                    config,
                    run_round=runner,
                    run_cross_exam=cross_exam_runner,
                    on_round_start=_emit_round_start,
                    on_round=_emit_round,
                    on_round_boundary=_round_boundary if self._ambient_armed else None,
                    evidence_ledger=self._evidence_ledger,
                )
            except PlanOnlyAbortError:
                # First-round run_plan already emitted; end the CEO turn without debaters.
                summary = "[plan-only] 已记录辩论计划，跳过辩手执行。"
                logger.info("debate.plan_only_done", motion=config.motion[:80])
                settle_moderator_node(
                    self,
                    moderator,
                    moderator_run_id,
                    moderator_model,
                    summary=summary,
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                )
                return ToolResult(
                    tool_call_id="",
                    success=True,
                    output=summary,
                    effect=ToolEffect.HANDOFF,
                    final_text=summary,
                )
            except Exception as exc:  # noqa: BLE001 — 辩论崩溃降级为工具失败，让 CEO 回落
                logger.exception("debate.failed", motion=config.motion[:80])
                # 终帧交给 finally 统一发（异常路径此前只 return，节点永久转圈）。
                node_error = f"辩论执行失败：{exc}"
                return err(f"{node_error}。可重试，或改用 delegate 单独处理。")

            settle_moderator_node(
                self,
                moderator,
                moderator_run_id,
                moderator_model,
                summary=result.node_summary,
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )
            result_payload = result.to_event_payload()
            result_payload["evidence_ledger"] = self._evidence_ledger.all_entries()
            self._sink.emit(
                debate_result(
                    execution_id=execution_id,
                    moderator_run_id=moderator_run_id,
                    payload=result_payload,
                )
            )
            ceo_output = result.to_ceo_output()
            logger.info("debate.done", rounds=len(result.rounds), stop=result.stop_reason)
            return ToolResult(
                tool_call_id="",
                success=True,
                output=ceo_output,
                output_limit=DEBATE_OUTPUT_LIMIT,
                metadata=usage_metadata(self._acc.usage),
            )
        finally:
            # 掌舵窗口归还：正常收场已在末轮边界关过（幂等），这里兜住其余出口——全员失败
            # 早停（不走边界钩子）、setup / moderator.run 崩溃、plan-only 提前 return。
            # 不关则条目连同 key 常驻进程内存，且辩论早已结束还在照单全收。
            close_steer_window(execution_id)
            # 主持人节点终帧必发：正常收场 / plan-only 已在上面提前 settle（钉住
            # run_completed → debate_result 的线序），这里兜住其余一切出口——moderator.run
            # 崩溃、开播后到开跑前的 setup 抛错、乃至向上逃逸的异常。不兜则协作图上的主持人
            # 节点永久转圈（CEO 回合仍以 completed 收口，前端「整回合失败冻结」兜底不生效），
            # 且主持人自身几次 LLM 调用整笔丢账。
            if node_started and not self._moderator_settled:
                inflight = sys.exc_info()[1]
                settle_moderator_node(
                    self,
                    moderator,
                    moderator_run_id,
                    moderator_model,
                    summary="",
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                    error=(
                        node_error
                        or (str(inflight) if inflight else "")
                        or "辩论异常中止，未产出结果。"
                    ),
                )
