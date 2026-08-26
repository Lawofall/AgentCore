"""DebateTool — CEO 发起结构化辩论 / 交叉审查的编排原语。"""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.core.types import (
    DEFAULT_PERMISSION_AXES,
    PermissionAxes,
    ToolApproval,
    ToolCategory,
    ToolEffect,
    new_id,
)
from agentcore.llm.profiles import TurnProfiles as ProfileSet
from agentcore.llm.profiles import default_turn_profiles as default_profile_set
from agentcore.llm.provider.protocol import LLMProvider
from agentcore.runtime.debate import (
    DebateConfig,
    Moderator,
    RoundBoundary,
    RoundDecision,
    RoundPolicy,
    RoundResult,
)
from agentcore.runtime.debate.events import moderator_plan_event, settle_moderator_node
from agentcore.runtime.debate.rounds import (
    make_closing_runner,
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
    parse_form,
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

    顶层调用在主持人循环启动前走编排层开工卡（``team_preview``，primitive=debate）；
    嵌套 / 续跑 / full_auto 跳过语义对齐 delegate。
    """

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.ALWAYS,
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
        permission_axes: PermissionAxes | None = None,
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
        # 批 D1：会话级留人 roster（探测幕1 透镜 session）；缺省 = 无证人。
        self._session_store = session_store
        self._session_loader = session_loader
        self._pending_pause = False
        # 每个 side 的可续写 session（跨轮带记忆）：首轮执行后留人，后续轮 continue_run 取用。
        self._debater_sessions: dict[str, RunSession] = {}
        # 批 D1：本场证人席位（key=lens run_id）。
        self._witness_seats: dict[str, Any] = {}
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
        # 推进卡消费 / 点卡直起时携带的卡 payload（宿主三元组优先）。
        self._debate_stage_card: dict[str, Any] | None = None
        # debate.started 后立刻 finalize 的上下文；启动失败保持 None。
        self._stage_card_finalize: dict[str, Any] | None = None
        # 已在开跑边界落 resolved（中途失败不回 pending）。
        self._stage_card_finalized_at_start: bool = False
        # 主持人节点终帧只发一次（``settle_moderator_node`` 的幂等闸）。
        self._moderator_settled: bool = False

    def _kickoff_system_prompt(self) -> str:
        return self._system_prompt

    def _kickoff_tool_name(self) -> str:
        return "debate"

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
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolContext,
        *,
        skip_kickoff: bool = False,
    ) -> ToolResult:
        from agentcore.llm.turn_auth_dead import (
            credential_source_from_llm,
            is_turn_auth_dead,
            turn_auth_dead_reject_message,
        )
        from agentcore.runtime.costing import usage_metadata
        from agentcore.runtime.kickoff.stage_card import (
            clear_turn_keeps_stage_card,
            debate_arguments_from_card,
            mark_turn_keeps_stage_card,
        )
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

        # 本回合调了 debate（含闸失败）→ 收尾不 orphan pending 推进卡。
        # 开辩失败 / STOP 会 clear；仅真正开跑成功才保持 keep + finalize resolve。
        mark_turn_keeps_stage_card()
        motion = str(arguments.get("motion") or "").strip()
        if not motion:
            clear_turn_keeps_stage_card()
            return err("debate 需要 motion（辩论命题 / 要解决的问题）。")

        consume_host_turn_id = ""
        consume_card_id = ""
        consume_override: str | None = None
        consume_note = ""
        self._stage_card_finalize = None
        self._stage_card_finalized_at_start = False

        # 口头开赛 = 消费推进卡（有 pending 时一律走 stage_card 授权路径）。
        if (
            not skip_kickoff
            and self._depth == 0
            and self._debate_authorized_by != "stage_card"
            and (self._conversation_id or "").strip()
        ):
            from agentcore.conversation.stage_card_resolve import (
                consume_pending_stage_card_for_debate,
                list_pending_stage_cards,
            )

            try:
                merged, _override, consume_err = (
                    await consume_pending_stage_card_for_debate(
                        conversation_id=self._conversation_id or "",
                        ceo_motion=motion,
                        sink=self._sink,
                    )
                )
            except Exception as exc:  # noqa: BLE001 — 查卡失败不得阻断冷开辩
                logger.warning(
                    "stage_card.consume_lookup_failed",
                    conversation_id=self._conversation_id,
                    error=str(exc),
                )
                merged, consume_err = None, ""
            if consume_err and merged is None:
                # 有 pending 但 motion 闸失败 → 卡保持 pending（keep 保留，防收尾误 orphan）。
                try:
                    pending = await list_pending_stage_cards(
                        self._conversation_id or ""
                    )
                except Exception:  # noqa: BLE001
                    pending = []
                if pending:
                    return err(consume_err)
                clear_turn_keeps_stage_card()
            elif merged is not None:
                consume_host_turn_id = str(merged.pop("_host_turn_id", "") or "")
                consume_card_id = str(
                    merged.get("stage_card_id") or merged.pop("_card_id", "") or ""
                )
                raw_override = merged.pop("_motion_override", _override)
                consume_override = (
                    str(raw_override) if raw_override is not None else None
                )
                consume_note = str(merged.pop("_resolve_note", "") or "")
                card_args = debate_arguments_from_card(merged)
                # CEO motion（可能为 override）已写入 merged；保留 background / kickoff_ask。
                if arguments.get("background"):
                    card_args["background"] = arguments.get("background")
                if arguments.get("_kickoff_ask"):
                    card_args["_kickoff_ask"] = arguments.get("_kickoff_ask")
                arguments = card_args
                motion = str(arguments.get("motion") or "").strip()
                self._debate_authorized_by = "stage_card"
                self._debate_stage_card = dict(merged)
                skip_kickoff = True

        # 按钮 / 机制直起：卡上已带 host 回合 id（成功边界同为 debate.started）。
        if (
            not consume_card_id
            and self._debate_authorized_by == "stage_card"
            and isinstance(self._debate_stage_card, dict)
        ):
            sc = self._debate_stage_card
            consume_host_turn_id = str(sc.get("_host_turn_id") or "")
            consume_card_id = str(
                sc.get("stage_card_id") or sc.get("_card_id") or ""
            )
            raw_override = sc.get("_motion_override")
            if consume_override is None and raw_override is not None:
                consume_override = str(raw_override) if raw_override else None
            if not consume_note:
                consume_note = str(sc.get("_resolve_note") or "")

        sides, side_err = parse_sides(arguments.get("sides"))
        if side_err:
            clear_turn_keeps_stage_card()
            return err(side_err)
        form = parse_form(arguments.get("form"))
        thorough = arguments.get("thorough", True)
        if not isinstance(thorough, bool):
            thorough = True
        policy = RoundPolicy.for_form(form, thorough=thorough)
        try:
            max_rounds_arg = int(arguments["max_rounds"])  # type: ignore[index]
            if max_rounds_arg >= 1:
                policy = RoundPolicy(thorough=thorough, max_rounds=max_rounds_arg)
        except (KeyError, TypeError, ValueError):
            pass
        # `_kickoff_ask` 为 resume 注入的内部键（非 schema / 非 wire），开赛嘱咐进首轮插话管道。
        kickoff_ask = str(arguments.get("_kickoff_ask") or "").strip()
        mod_model, mod_origin, mod_provider_id, mod_err = parse_moderator_fields(
            arguments.get("moderator_model"),
            arguments.get("moderator_origin"),
            arguments.get("moderator_provider_id"),
        )
        if mod_err:
            clear_turn_keeps_stage_card()
            return err(mod_err)
        config = DebateConfig(
            motion=motion,
            form=form,
            sides=sides,
            policy=policy,
            background=parse_background(arguments.get("background")),
            kickoff_ask=kickoff_ask,
            moderator_model=mod_model,
            moderator_origin=mod_origin,
            moderator_provider_id=mod_provider_id,
            moderator_run_id=str(arguments.get("moderator_run_id") or "").strip(),
        )

        # §7.5：校验非空目录身份 + 解析裁判（点名优先；空=系统默认，可同模）。
        from agentcore.runtime.debate.models import (
            collect_debate_identities,
            ensure_debate_route_extras,
            prepare_debate_model_plan,
        )

        turn_model = (self._profile_set.model or "").strip()
        user_id = (self._base_tool_context.user_id or "").strip()
        cross_model = arguments.get("cross_model", False) is True
        model_err = ""
        try:
            from agentcore.db.base import async_session_factory

            if user_id:
                async with async_session_factory() as session:
                    model_err = await prepare_debate_model_plan(
                        config,
                        user_id=user_id,
                        turn_model=turn_model,
                        session=session,
                        cross_model=cross_model,
                        user_message=self._user_message or "",
                    )
            else:
                model_err = await prepare_debate_model_plan(
                    config,
                    user_id="",
                    turn_model=turn_model,
                    session=None,
                    catalog=None,
                    cross_model=cross_model,
                    user_message=self._user_message or "",
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("debate.model_plan_failed", error=str(exc))
            model_err = await prepare_debate_model_plan(
                config,
                user_id=user_id,
                turn_model=turn_model,
                session=None,
                catalog=None,
                cross_model=cross_model,
                user_message=self._user_message or "",
            )
        if model_err:
            clear_turn_keeps_stage_card()
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

        # 开赛前预分配稳定 run_id（开工卡 wire + model_overrides 键）；resume 复用。
        from agentcore.runtime.debate.models import allocate_debate_run_ids

        allocate_debate_run_ids(config, arguments)

        if not skip_kickoff:
            early = await self._kickoff_before_moderator(config, arguments)
            if early is not None:
                # STOP / research_first / pause — 未真正开跑则清 keep。
                if not self._pending_pause:
                    clear_turn_keeps_stage_card()
                return early
        elif self._debate_authorized_by is None:
            # skip_kickoff 未显式授权：新路径缺省 = auto。
            self._debate_authorized_by = "auto"

        # 成功边界 = debate.started：开跑即 finalize（见 _run_moderator）。
        if (
            consume_card_id
            and consume_host_turn_id
            and self._debate_authorized_by == "stage_card"
        ):
            self._stage_card_finalize = {
                "host_turn_id": consume_host_turn_id,
                "stage_card_id": consume_card_id,
                "note": consume_note,
                "motion_override": consume_override,
            }

        result = await self._run_moderator(config, usage_metadata)
        if not result.success:
            clear_turn_keeps_stage_card()
            return result
        return result

    async def _kickoff_before_moderator(
        self,
        config: DebateConfig,
        arguments: dict[str, Any],
    ) -> ToolResult | None:
        """No new team_preview card before ``debate.started``. Nested still no-op.

        ``skip_kickoff`` callers (stage_card / resume CONTINUE) never enter here.
        ``deep_research_auto`` still records when the flag allows — it no longer
        means "skip a card that would have hung".
        """
        _ = (config, arguments)
        if self._depth != 0:
            return None
        from agentcore.runtime.deep_research_auto import (
            record_auto_debate,
            tool_may_auto_debate,
        )

        auto_adopt = tool_may_auto_debate(self)
        if auto_adopt:
            await record_auto_debate(self)
        self._debate_authorized_by = "auto"
        return None

    async def _resolve_host_attach(self, config: DebateConfig):
        """开工决议后：尝试把辩论新幕链到幕 1 MLR；失败则保持独立图。

        推进卡路径优先用卡上直传三元组（host_execution_id / synthesizer_run_id /
        host_message_id），找不到再回落 resolve_debate_host_attach。
        命中后由 mint 点按 ``same_turn`` 决定：同回合复用宿主 execution 加一幕；
        跨回合 mint 新图 + prev_execution_id（不 divert 宿主）。
        """
        from agentcore.runtime.debate.constants import FORM_LABELS
        from agentcore.runtime.debate.research_dossier import workspace_has_research_artifacts
        from agentcore.runtime.facts import snapshot_fact_log
        from agentcore.runtime.kickoff.debate_host import resolve_debate_host_attach
        from agentcore.runtime.kickoff.stage_card import resolve_host_attach_from_card

        attach = await resolve_host_attach_from_card(
            self._debate_stage_card,
            append_message_id=self._message_id,
        )
        if attach is None:
            has_research = False
            try:
                has_research = await workspace_has_research_artifacts(
                    self._base_tool_context.backend
                )
            except Exception:
                logger.exception("debate.research_dossier_probe_failed")
                has_research = False
            attach = await resolve_debate_host_attach(
                conversation_id=self._conversation_id or "",
                append_message_id=self._message_id,
                journal_entries=snapshot_fact_log(),
                has_research_artifacts=has_research,
            )
        if attach is None:
            return None
        label = FORM_LABELS.get(config.form, "辩论")
        self._debate_act_id = attach.act_id
        self._debate_act_title = f"{label}对抗"
        self._debate_anchor_run_id = attach.anchor_run_id
        self._debate_host_message_id = attach.host_message_id
        # prev / execution_id 由 mint 点按 same_turn 写入；此处只钉幕元数据。
        # parent 用本回合 captain；幕因果靠 act.anchor（跨回合另加 prev）。
        self._debate_graph_parent_run_id = None
        return attach

    async def _run_moderator(self, config: DebateConfig, usage_metadata) -> ToolResult:
        if self._debate_authorized_by is None:
            self._debate_authorized_by = "auto"

        # 底料预登记：【已核实·出处】→ 台账条目 + 改写为 #eN（咬合点 1）
        from agentcore.runtime.debate.evidence_ledger import (
            preregister_background,
            preregister_turn_research_entries,
        )
        from agentcore.runtime.debate.research_dossier import (
            format_research_dossier_index,
            list_research_artifact_paths,
        )

        # 约定文档桥无条件化：CEO 回合 #rN → 场级 #eN（不论是否写入 background）。
        try:
            from agentcore.runtime.suspension import turn_evidence_ledger as _turn_led

            turn_core = _turn_led.get()
            if turn_core is not None:
                preregister_turn_research_entries(
                    self._evidence_ledger, turn_core.all_entries()
                )
        except Exception:  # noqa: BLE001
            logger.exception("debate.turn_ledger_preregister_failed")

        if (config.background or "").strip():
            config.background = preregister_background(
                self._evidence_ledger, config.background
            )

        # 幕1 约定文档：预登记进场级台账（#rN 锚 → #eN）+ 注入索引（含 #eN 映射）。
        try:
            from agentcore.runtime.debate.research_dossier import (
                preregister_research_dossier,
            )

            config.research_dossier_index = await preregister_research_dossier(
                self._evidence_ledger, self._base_tool_context.backend
            )
        except Exception:
            logger.exception("debate.research_dossier_index_failed")
            config.research_dossier_index = ""
            # 兜底一层：预登记失败时仍给路径索引（无台账映射）。
            try:
                paths = await list_research_artifact_paths(
                    self._base_tool_context.backend
                )
                config.research_dossier_index = format_research_dossier_index(paths)
            except Exception:
                logger.exception("debate.research_dossier_probe_failed")
                config.research_dossier_index = ""

        # 批 A2：命中宿主 → 同回合复用 eid 加一幕；跨回合新图 + prev；找不到则独立成图。
        from agentcore.runtime.kickoff.debate_host import host_graph_binding

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
            if host_attach is not None:
                logger.info("debate.started", **started_fields)
            else:
                # 独立图 = 未链到 MLR；warning 级可观测（禁止静默降级）。
                logger.warning(
                    "debate.started",
                    **started_fields,
                    host_attach_miss="independent_graph",
                )

            # 推进卡成功边界 = debate.started（主持人/计划落地、真正开跑）。
            # 口头消费与按钮直起同构；中途失败不回 pending。
            finalize_ctx = self._stage_card_finalize
            if (
                finalize_ctx
                and self._debate_authorized_by == "stage_card"
                and not self._stage_card_finalized_at_start
            ):
                try:
                    from agentcore.conversation.stage_card_resolve import (
                        finalize_stage_card_start_debate,
                    )

                    await finalize_stage_card_start_debate(
                        conversation_id=self._conversation_id or "",
                        host_turn_id=str(finalize_ctx.get("host_turn_id") or ""),
                        stage_card_id=str(finalize_ctx.get("stage_card_id") or ""),
                        note=str(finalize_ctx.get("note") or ""),
                        motion_override=finalize_ctx.get("motion_override"),
                        sink=self._sink,
                    )
                    self._stage_card_finalized_at_start = True
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "stage_card.finalize_at_started_failed",
                        stage_card_id=str(finalize_ctx.get("stage_card_id") or ""),
                        error=str(exc),
                    )

            moderator = Moderator(
                provider=self._llm,
                model=moderator_model,
                run_id=moderator_run_id,
                parent_run_id=graph_parent,
            )
            # 掌舵窗口开在主持人开跑处（庭前取证期入的队也能被首轮边界捞到）；无活跃用户
            # 时不开——没挂 on_round_boundary，谁都捞不走，收下就是骗人。
            if self._ambient_armed:
                open_steer_window(execution_id)
            runner = make_round_runner(self, execution_id, moderator_run_id, config)
            cross_exam_runner = make_cross_exam_runner(
                self, execution_id, moderator_run_id, config
            )
            closing_runner = make_closing_runner(
                self, execution_id, moderator_run_id, config
            )

            # 批 D1：开赛探测幕1 透镜 session → 辩论幕内证人席位；无则零行为变化。
            from agentcore.runtime.debate.witness import (
                build_witness_seats,
                make_witness_runner,
                probe_witness_sessions,
                seats_to_info,
                witness_plan_event,
            )

            lens_sessions = probe_witness_sessions(self._session_store)
            self._witness_seats = build_witness_seats(
                lens_sessions,
                moderator_run_id=moderator_run_id,
                depth=self._depth + 2,
            )
            witness_runner = None
            witness_roster = ()
            if self._witness_seats:
                self._sink.emit(
                    witness_plan_event(
                        self, execution_id, moderator_run_id, self._witness_seats
                    )
                )
                witness_runner = make_witness_runner(
                    self, execution_id, moderator_run_id, self._witness_seats
                )
                witness_roster = tuple(seats_to_info(self._witness_seats))
                logger.info(
                    "debate.witness.probed",
                    count=len(self._witness_seats),
                    keys=list(self._witness_seats.keys()),
                )

            from agentcore.runtime.debate.moderator_agenda import cross_exam_enabled

            cx_enabled = cross_exam_enabled(config)

            # 庭前取证（§二之二）：首轮立论前；fast 档秒过。
            from agentcore.runtime.debate.pretrial import run_pretrial_phase
            from agentcore.runtime.events import (
                debate_pretrial_completed,
                debate_pretrial_orders,
                debate_pretrial_started,
            )

            async def _pt_started(p: dict) -> None:
                self._sink.emit(debate_pretrial_started(**p))

            async def _pt_orders(p: dict) -> None:
                self._sink.emit(debate_pretrial_orders(**p))

            async def _pt_completed(p: dict) -> None:
                self._sink.emit(debate_pretrial_completed(**p))

            await run_pretrial_phase(
                self,
                execution_id=execution_id,
                moderator_run_id=moderator_run_id,
                config=config,
                complete_json=moderator._complete_json,
                on_started=_pt_started,
                on_orders=_pt_orders,
                on_completed=_pt_completed,
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
                # 再有 ⇒ 立刻关窗：其后的结辩 + 简报可达数十秒，那期间收下的掌舵永不生效。
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
                    run_witness_exam=witness_runner,
                    witness_roster=witness_roster,
                    run_closing=closing_runner,
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
            # 双产物机制性落盘（约定文档 ``AgentCore/文档/debate/``）；失败不阻断收口，路径附 CEO 输出尾部。
            from agentcore.runtime.debate.persist import (
                artifact_stamp,
                format_artifact_footer,
                persist_debate_artifacts,
            )

            ceo_output = result.to_ceo_output()
            paths = await persist_debate_artifacts(
                self._base_tool_context.backend,
                result,
                stamp=artifact_stamp(moderator_run_id),
            )
            if paths is not None:
                ceo_output += format_artifact_footer(paths)
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
