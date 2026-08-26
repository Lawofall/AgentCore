"""DelegateTool — CEO main-agent orchestration primitive."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.core.text import clip_preview
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
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.delegate.drive import drive
from agentcore.runtime.delegate.graph_identity import resolve_graph_identity
from agentcore.runtime.delegate.plan_events import plan_event
from agentcore.runtime.delegate.prelude import (
    DelegateCallFlags,
    DelegatePreludeReject,
    resolve_delegate_prelude,
)
from agentcore.runtime.delegate.steer import apply_steer, record_plan_snapshot
from agentcore.runtime.delegate.supervised import (
    SupervisedRun,
    apply_replan,
    finalize_stopped,
)
from agentcore.runtime.events import EventSink, plan_revised
from agentcore.tools.builtin.delegate.schema import (
    DELEGATE_DESCRIPTION,
    DELEGATE_PARAMETERS,
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
    from agentcore.runtime.runs.notewall import NoteWall
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.scheduler import BoundaryReason
    from agentcore.runtime.runs.types import RunSpec, RunState
    from agentcore.runtime.sessions import SessionLoader, SessionSaver, SessionStore
    from agentcore.runtime.suspension import SuspensionDeleter, SuspensionSaver

logger = get_logger(__name__)


# Cap on how many nodes `delegate.started` lists by name/task — a big fan-out shouldn't
# balloon one log line; `nodes` still carries the true total.
_DELEGATE_LOG_AGENTS_CAP = 12


def _waves_ids_for_log(
    plan: RunPlan,
    *,
    host_for_cross_batch: RunPlan | None = None,
) -> list[list[str]]:
    """Wave id lists for ``delegate.started``; tolerate new-batch edges into host."""
    from agentcore.runtime.runs.plan import RunPlan as Plan
    from agentcore.runtime.runs.plan import RunPlanError

    try:
        return [[n.run_id for n in wave] for wave in plan.waves()]
    except RunPlanError:
        if host_for_cross_batch is None:
            raise
        combined = Plan(
            nodes=[*host_for_cross_batch.nodes, *plan.nodes],
            origin=host_for_cross_batch.origin,
        )
        return [[n.run_id for n in wave] for wave in combined.waves()]


class DelegateTool:
    """CEO-agent tool that delegates sub-tasks to a Run plan and returns their
    products for the CEO to synthesize (non-terminal, Option 1).
    """

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.ALWAYS,
    )

    def __init__(
        self,
        *,
        llm: OpenAICompatibleProvider,
        sink: EventSink,
        system_prompt: str,
        user_message: str,
        history: list[dict],
        tools: ToolRegistry,
        base_tool_context: ToolContext,
        profile_set: ProfileSet | None = None,
        max_parallel: int | None = None,
        captain_run_id: str | None = None,
        approval_gate: ApprovalGate | None,
        session_store: SessionStore | None = None,
        session_saver: SessionSaver | None = None,
        session_loader: SessionLoader | None = None,
        conversation_id: str | None = None,
        registry: ClientRequestBridge | None = None,
        checkpoint_timeout_seconds: float | None = None,
        checkpoint_enabled: bool = False,
        message_id: str | None = None,
        suspension_saver: SuspensionSaver | None = None,
        suspension_deleter: SuspensionDeleter | None = None,
        folder_id: str | None = None,
        permission_axes: PermissionAxes | None = None,
        depth: int = 0,
    ) -> None:
        self._llm = llm
        self._sink = sink
        self._system_prompt = system_prompt
        self._user_message = user_message
        self._history = history
        self._tools = tools
        self._base_tool_context = base_tool_context
        self._profile_set = profile_set or default_profile_set()
        self._max_parallel = max_parallel
        self._approval_gate = approval_gate
        self._permission_axes = permission_axes or DEFAULT_PERMISSION_AXES
        self._auto_grant_pending = False
        self._captain_run_id = captain_run_id
        self._session_store = session_store
        self._session_saver = session_saver
        self._session_loader = session_loader
        self._depth = depth
        self._conversation_id = conversation_id
        self._registry = registry
        self._checkpoint_timeout_seconds = checkpoint_timeout_seconds
        self._checkpoint_enabled = checkpoint_enabled
        self._message_id = message_id
        self._suspension_saver = suspension_saver
        self._suspension_deleter = suspension_deleter
        # Turn-level project scope, carried purely so a durable plan_review pause captures it
        # into the frame — the resumed toolset re-wires consult_memory to the same project
        # (Agent记忆与知识系统 §二). Not used by the delegate drive itself.
        self._folder_id = folder_id
        # 跨文件夹指挥 · 嵌套默认目标桌（父 worker 的 target / 出生）；tasks 省略时继承。
        self._default_target_folder_id: str | None = None
        # 同回合多 local 认领簿（drive 入口 seed）；嵌套子派共享同一簿。
        self._local_root_claims = None
        self._children: list[DelegateTool] = []
        self._calls = 0
        # 同回合上一张协作图 execution_id + plan/seed 快照（成功 kickoff/drive 后写入）；
        # 二次 delegate 无显式 append / 无活跃 live_plan 时自动合入。
        # plan/seed 供无 journal 时（单测 / journal 未落盘）仍能解析 depends_on。
        self._last_graph_execution_id: str | None = None
        self._last_graph_plan: RunPlan | None = None
        self._last_graph_seed: dict[str, RunState] | None = None
        # 上一段 drive 收尾时波调度器给出的终态映射（run_id → 真实 RunState）——同回合
        # 二次合入的 seed 只从这里取相，绝不按 plan 节点凭空判定完成。让出 / 软停时未跑的
        # 尾节点本就不在映射里（缺席 = 还没跑），二次派发才会再调度它们。
        self._last_drive_results: dict[str, RunState] | None = None
        # Cumulative sub-workers spawned by this captain (worker leads only).
        self._sub_workers_spawned = 0
        from agentcore.runtime.costing import WorkerResultAccumulator

        self._acc = WorkerResultAccumulator()
        self._supervised: SupervisedRun | None = None
        self._pending_boundary: tuple[BoundaryReason, list[RunSpec]] | None = None
        # 挂起即收口 (②): set by the CHECKPOINT boundary hook when it finalizes the turn at a
        # plan_review pause (frame saved) — ``drive`` reads it after the scheduler soft-pauses
        # and returns a SUSPEND ToolResult. False on every ordinary drive.
        self._pending_pause: bool = False
        # 团队便签墙 (§2.2 通 / §2.3 合·对账): the most recent batch's wall,
        # set by ``drive`` when it
        # builds the executor so the CEO finalize (``format_for_ceo``, both the normal-终态 and the
        # ``replan(stop)`` finalize_stopped paths) can fold the team's outstanding 决定 / 认领 into
        # 语义边界对账. None until a batch runs (a CEO that never delegated has no wall).
        self._note_wall: NoteWall | None = None
        # Turn-level team consensus (team_brief): survives across delegate calls in one CEO turn.
        self._team_brief: str | None = None
        # Last resolved note-wall coordination mode (wall|none); resume/replan reuse it.
        self._coordination: str = "none"
        # 本批 CEO 预贴便签（execute 解析后暂存），供同回合续派 / 挂起帧回灌。
        self._seed_notes: list[dict[str, str]] = []
        # 当前 execute 展开的 playbook 名（team_preview pre-auth 判定用）。
        self._active_playbook: str | None = None
        # 当前 playbook_args（kickoff headline 只读 intensity；手写 tasks 为 None）。
        self._active_playbook_args: dict[str, Any] | None = None
        # 父 worker 带 code_audit_gate 时：嵌套手写 tasks 继承收工纪律（见 audit.apply_*）。
        self._inherit_code_audit_discipline: bool = False
        # 本次调用点名放行的闸（execute / replan 各自在入口无条件重解析——旧的单个
        # `_delegate_force` 既一键全开四道闸，又会被后续 replan 读到残值）。
        from agentcore.runtime.delegate.force_scopes import EMPTY_FORCE_SCOPES

        self._force_scopes = EMPTY_FORCE_SCOPES
        # Turn user-message provenance (harvest closing stamps execution_harvest).
        from agentcore.runtime.delegate.post_close_gate import current_user_message_origin

        self._user_message_origin: str = current_user_message_origin()

    def effective_default_target_folder_id(self) -> str | None:
        """Nested lead inheritance, else bare-chat turn hint from create/resolve.

        Birth-bound sessions ignore the turn hint (omit → workers sit birth desk).
        Does not rewrite ``_default_target_folder_id`` so a later multi-project
        clear on ``turn_target_desk`` still forces explicit targets.
        """
        nested = getattr(self, "_default_target_folder_id", None)
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
        if self._folder_id:
            return None
        hint = getattr(self._base_tool_context, "turn_target_desk", None)
        hinted = getattr(hint, "folder_id", None) if hint is not None else None
        if isinstance(hinted, str) and hinted.strip():
            return hinted.strip()
        return None

    def spawn_lead_subteam(self, captain_run_id: str, captain_depth: int):
        """Mint a nested lead handle (阶段2); construction stays in the tools package."""
        from agentcore.tools.builtin.delegate.nesting import make_lead_subteam

        return make_lead_subteam(self, captain_run_id, captain_depth)

    def _kickoff_system_prompt(self) -> str:
        return self._system_prompt

    def _kickoff_tool_name(self) -> str:
        return "delegate"

    @property
    def usage(self) -> dict[str, int]:
        return self._acc.usage

    @property
    def run_ledger(self) -> list[RunCost]:
        return self._acc.run_ledger

    @property
    def citations(self) -> list[dict[str, Any]]:
        return self._acc.citations

    @property
    def continuation_count(self) -> int:
        """续派次数（continue_from + redirect 热修；不计辩论编排续写）。

        本级 = 累加器里的条目（含已被 ``absorb_children`` 折进来的子团队），加上尚未
        被折叠的在册 children；子工具一旦 absorb 就出列，两边不会重复计。
        """
        n = len(self._acc.continuations)
        for child in self._children:
            n += child.continuation_count
        return n

    @property
    def user_continuation_count(self) -> int:
        """上面那批里用户亲手促成的子集（「立即改此人」的 redirect 热修）。

        用户面的「队友互相把关」= ``continuation_count - user_continuation_count``：
        用户自己点的返工不是队友互检。运营口径 ``revises`` 仍取总数，不受影响。
        """
        n = len(self._acc.user_continuations)
        for child in self._children:
            n += child.user_continuation_count
        return n

    def note_continuation(self, run_id: str, *, by_user: bool = False) -> None:
        """Record a successful continuation for turn_metrics.revises.

        Lives on the accumulator so a nested lead's 续派 rolls up the SAME merge path
        as usage / ledger / collab — the tool object it happened on is discarded.

        ``by_user`` 标出这次返工是谁要的：队友续派（``continue_from``）缺省为否，
        用户点「立即改此人」的 redirect 热修传真。
        """
        self._acc.continuations.append(run_id)
        if by_user:
            self._acc.user_continuations.append(run_id)

    @property
    def collab(self) -> dict[str, int]:
        """Turn-level 协作质量 tally (学·度量 §2.5): boundary_yields / scope_signals /
        escalations, rolled up across this turn's batches (and nested sub-teams).

        ``boundary_yields_by_user`` 是 ``boundary_yields`` 的子集（用户拍板的
        ``checkpoint`` 那部分），供用户面剔除，不改总数口径。"""
        return self._acc.collab

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="delegate",
            description=DELEGATE_DESCRIPTION,
            parameters=DELEGATE_PARAMETERS,
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    def _apply_call_flags(self, flags: DelegateCallFlags | None) -> None:
        """把前奏解析出的 per-call 标记镜像到实例上（抽出前这几行内联在 execute 里）。"""
        if flags is None:
            return
        self._active_playbook = flags.playbook
        self._active_playbook_args = flags.playbook_args

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        from agentcore.llm.turn_auth_dead import credential_source_from_llm
        from agentcore.runtime.delegate.force_scopes import parse_force_scopes
        from agentcore.runtime.runs import build_run_plan

        # 逐闸 force 的唯一写入点：每次调用（含随后被前奏硬拒的调用）都重解析，
        # 实例上不留残值，后续 delegate / replan 拿不到上一次的放行。
        self._force_scopes = parse_force_scopes(arguments.get("force"))

        prelude = resolve_delegate_prelude(
            arguments,
            tools=self._tools,
            user_message=self._user_message,
            conversation_id=self._conversation_id,
            depth=self._depth,
            sub_workers_spawned=self._sub_workers_spawned,
            credential_source=credential_source_from_llm(self._llm),
        )
        self._apply_call_flags(prelude.flags)
        if isinstance(prelude, DelegatePreludeReject):
            return prelude.result
        playbook = prelude.playbook
        tasks_raw = prelude.tasks_raw
        playbook_notes = prelude.playbook_notes
        valid_tools = prelude.valid_tools
        complexity_hint = prelude.complexity_hint
        consumer_deps_warn = prelude.consumer_deps_warn
        design_impl_warn = prelude.design_impl_warn
        root_slice_warn = prelude.root_slice_warn

        # §4.2b·2b / 改法④A：无出生且写盘缺目标 → 先静默建云桌，再闸。
        # 裸聊同回合唯一 create/resolve / auto 可经 turn_target_desk 继承缺省目标。
        from agentcore.runtime.delegate.target_desktop import (
            ensure_bare_chat_auto_cloud_desk,
            gate_bare_chat_requires_target,
        )

        tasks_for_gate = tasks_raw if isinstance(tasks_raw, list) else []
        await ensure_bare_chat_auto_cloud_desk(
            session_folder_id=self._folder_id,
            tasks_raw=tasks_for_gate,
            default_target_folder_id=self.effective_default_target_folder_id(),
            turn_target_desk=getattr(
                self._base_tool_context, "turn_target_desk", None
            ),
            user_id=getattr(self._base_tool_context, "user_id", "") or "",
            conversation_id=self._conversation_id
            or getattr(self._base_tool_context, "conversation_id", None),
            tool_context=self._base_tool_context,
            sink=self._sink,
        )
        default_target = self.effective_default_target_folder_id()
        bare_gate = gate_bare_chat_requires_target(
            session_folder_id=self._folder_id,
            tasks_raw=tasks_for_gate,
            default_target_folder_id=default_target,
        )
        if bare_gate:
            logger.info(
                "delegate.bare_chat_no_target_rejected",
                session_folder_id=self._folder_id,
            )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=bare_gate,
                contract_failure=True,
            )
        if (
            not self._folder_id
            and default_target
            and not getattr(self, "_default_target_folder_id", None)
        ):
            # 观测：本批靠回合 hint 过闸（模型未显式 target_folder_id）
            logger.info(
                "delegate.turn_target_desk_inherited",
                folder_id=default_target,
            )

        identity = await resolve_graph_identity(
            arguments,
            depth=self._depth,
            context_execution_id=self._base_tool_context.execution_id,
            message_id=self._message_id,
            conversation_id=self._conversation_id,
            captain_run_id=self._captain_run_id,
            calls=self._calls,
            last_graph_execution_id=self._last_graph_execution_id,
            last_graph_plan=self._last_graph_plan,
            last_graph_seed=self._last_graph_seed,
        )
        if isinstance(identity, ToolResult):
            return identity
        append_to = identity.append_to
        prev_execution_id = identity.prev_execution_id
        append_seed = identity.append_seed
        host_plan_for_append = identity.host_plan_for_append
        host_captain_run_id = identity.host_captain_run_id
        latest_miss_degraded_note = identity.latest_miss_degraded_note

        self._calls += 1
        # 冻结本次委派调用的序号：同回合并发的多个 delegate 调用共享 self._calls，若在完成侧
        # 惰性读取会把每个批次的 completed / synthesis 日志都错记到「最后自增到的序号」。这里
        # 立刻定格，透传给 drive → format_for_ceo 用于完成侧日志。
        call_idx = self._calls
        prefix = f"del_{new_id()}"
        if getattr(self, "_inherit_code_audit_discipline", False) and isinstance(
            tasks_raw, list
        ):
            from agentcore.runtime.runs.playbooks.audit import (
                apply_inherited_code_audit_discipline,
            )

            tasks_raw = apply_inherited_code_audit_discipline(tasks_raw)
            logger.info(
                "delegate.nested_code_audit_discipline",
                tasks=len(tasks_raw),
                depth=self._depth,
            )
        elif playbook is None and isinstance(tasks_raw, list):
            from agentcore.runtime.runs.playbooks.audit import (
                apply_inherited_code_audit_discipline,
            )

            tasks_raw = apply_inherited_code_audit_discipline(
                tasks_raw, only_shaped=True
            )
        if isinstance(tasks_raw, list) and tasks_raw:
            from agentcore.runtime.delegate.task_models import (
                ensure_delegate_route_extras,
                inherit_model_from_tool,
                prepare_task_model_fields,
            )

            model_errors, model_idents = await prepare_task_model_fields(
                tasks_raw,
                user_id=getattr(self._base_tool_context, "user_id", "") or "",
                where_prefix="tasks",
                inherit_model=lambda rid: inherit_model_from_tool(self, rid),
            )
            if model_errors:
                msg = "委派任务无效：" + "；".join(model_errors)
                logger.info("delegate.rejected", errors=model_errors, reason="task_model")
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=msg,
                    contract_failure=True,
                )
            await ensure_delegate_route_extras(
                self._llm,
                model_idents,
                user_id=getattr(self._base_tool_context, "user_id", "") or "",
            )
        plan, errors = build_run_plan(
            tasks_raw,
            valid_tools=valid_tools,
            id_prefix=prefix,
            parent_run_id=host_captain_run_id or self._captain_run_id,
            depth=self._depth + 1,
            complexity_hint=complexity_hint,
            existing_plan=host_plan_for_append,
            default_target_folder_id=self.effective_default_target_folder_id(),
        )
        if errors:
            msg = "委派任务无效：" + "；".join(errors)
            logger.info("delegate.rejected", errors=errors)
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=msg,
                # 参数/依赖校验打回是零成本可自纠——勿进熔断。
                contract_failure=True,
            )
        from agentcore.workspace.project_shell import rewrite_plan_project_shell

        await rewrite_plan_project_shell(plan, self._base_tool_context)
        if getattr(self, "_topology_lock", False):
            plan.topology_lock = True
            wid = getattr(self, "_workflow_id", None)
            if isinstance(wid, str) and wid.strip():
                plan.workflow_id = wid.strip()
            wv = getattr(self, "_workflow_version", None)
            if isinstance(wv, int):
                plan.workflow_version = wv
        from agentcore.runtime.delegate.continuation import apply_continuation_tool_merges
        from agentcore.runtime.runs.research_quality import (
            batch_declares_review_files,
        )

        # 真纯丙：续派 tools 声明已忽略；merge 保留兼容旧 session 字段（执行层不收窄）。
        await apply_continuation_tool_merges(plan, self)

        batch_includes_review = (
            playbook == "research_report" or batch_declares_review_files(tasks_raw)
        )
        # 成篇硬门只认 playbook==research_report（及既有非字数结构腿由 includes_review 覆盖）。
        batch_audit_hard = playbook == "research_report"
        from agentcore.runtime.delegate.completion import (
            execution_capability_warning,
            validate_repair_how_fixed,
        )

        playbook_name_early = (
            playbook.strip() if isinstance(playbook, str) and playbook.strip() else None
        )
        how_fixed_err = validate_repair_how_fixed(
            playbook=playbook_name_early,
            playbook_args=arguments.get("playbook_args"),
        )
        if how_fixed_err:
            logger.info(
                "delegate.rejected",
                errors=[how_fixed_err],
                reason="repair_how_fixed",
            )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=how_fixed_err,
                contract_failure=True,
            )

        capability_warning = execution_capability_warning(
            plan,
            self._base_tool_context.backend,
            self._permission_axes,
        )
        if capability_warning:
            logger.info(
                "delegate.capability_warning",
                backend_location=getattr(self._base_tool_context.backend, "location", None),
            )
        # execution_id when already known at kickoff (append host / same-turn graph)
        kickoff_execution_id = append_to or self._base_tool_context.execution_id
        logger.info(
            "delegate.acceptance_resolved",
            criteria=None,
            source=None,
            **(
                {"execution_id": kickoff_execution_id}
                if kickoff_execution_id
                else {}
            ),
        )
        if self._depth >= 1:
            self._sub_workers_spawned += len(plan.nodes)

        from agentcore.runtime.delegate.seed_notes import (
            materialize_brief_as_seed_notes,
            parse_seed_notes,
            parse_team_brief,
            resolve_coordination,
        )

        seed_notes, seed_err = parse_seed_notes(
            arguments.get("seed_notes"),
            execution_id=kickoff_execution_id,
        )
        if seed_err:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=seed_err,
                contract_failure=True,
            )
        brief_raw = arguments.get("team_brief")
        if brief_raw is not None:
            brief, brief_err = parse_team_brief(
                brief_raw, execution_id=kickoff_execution_id
            )
            if brief_err:
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=brief_err,
                    contract_failure=True,
                )
            self._team_brief = brief

        playbook_name = playbook.strip() if isinstance(playbook, str) and playbook.strip() else None
        coordination = resolve_coordination(
            raw=arguments.get("coordination") if "coordination" in arguments else None,
            complexity_hint=complexity_hint,
            seed_notes=seed_notes,
            team_brief=self._team_brief,
            playbook=playbook_name,
        )
        self._coordination = coordination
        # 升墙 ⇒ 墙上有字：非空 brief 且经理未另给内部 seed 时，按行物化开局便签。
        # 不写回 CEO 可见 schema；light→none 不建墙，不必播种。
        if coordination == "wall" and not seed_notes and self._team_brief:
            seed_notes = materialize_brief_as_seed_notes(
                self._team_brief,
                execution_id=kickoff_execution_id,
            )
        self._seed_notes = seed_notes

        # 同回合 / 热图合入：先对「仅新批次」入闸，再合并进旧图。
        # 禁止先 merge 再 sibling 整图——会把已完成同座+同路径误判成同批交叉。
        added_nodes_for_anchor: list = list(plan.nodes)

        from agentcore.runtime.coordination.host import (
            admit_before_run_plan_emit,
            should_defer_run_plan_emit_to_merge,
        )

        if append_to:
            from agentcore.runtime.coordination.session import current_execution_id
            from agentcore.runtime.runs.plan import RunPlanError

            # Workers / registry must see host eid before admit / emit.
            self._base_tool_context.execution_id = append_to
            # Turn teardown clears via current_execution_id — keep it on the host
            # so the append coordination session is not orphaned under a fresh id.
            current_execution_id.set(append_to)

            admitted_reject = admit_before_run_plan_emit(
                self,
                plan,
                execution_id=append_to,
                call_idx=call_idx,
                host_plan=host_plan_for_append,
                seed_completed=append_seed,
            )
            if admitted_reject is not None:
                return admitted_reject

            old_plan = host_plan_for_append
            assert old_plan is not None  # loaded before build_run_plan
            added_nodes_for_anchor = []
            for node in plan.nodes:
                try:
                    old_plan.add(node)
                    added_nodes_for_anchor.append(node)
                except RunPlanError as exc:
                    logger.warning(
                        "delegate.graph_append_skip_node",
                        execution_id=append_to,
                        run_id=node.run_id,
                        error=str(exc),
                    )
            if not added_nodes_for_anchor:
                msg = "合入未并入任何新节点（可能与旧图 run_id 冲突）。请调整 tasks。"
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=msg,
                    contract_failure=True,
                )
            plan = old_plan
            execution_id = append_to
            logger.info(
                "delegate.same_turn_memory_append",
                execution_id=append_to,
                added=len(added_nodes_for_anchor),
                total=len(plan.nodes),
            )
        else:
            execution_id = self._base_tool_context.execution_id or new_id()
            # 准入→提交→执行：sibling / 追加重叠 / 同构闸在 durable run_plan 之前。
            admitted_reject = admit_before_run_plan_emit(
                self,
                plan,
                execution_id=execution_id,
                call_idx=call_idx,
            )
            if admitted_reject is not None:
                return admitted_reject

        record_plan_snapshot(plan)

        from agentcore.runtime.audit.hooks import on_delegate_plan

        on_delegate_plan(
            execution_id=execution_id,
            plan=plan,
            captain_run_id=self._captain_run_id,
        )
        # 同回合合入活跃协调时由 merge 在准入后发出成长后的 run_plan（提交点）。
        if not should_defer_run_plan_emit_to_merge(
            self, execution_id=execution_id
        ):
            self._sink.emit(
                plan_event(
                    self,
                    execution_id,
                    plan,
                    prev_execution_id=prev_execution_id,
                )
            )
        # 决策可观测: who + what got delegated (the「派了谁、干什么」input basis), not just a
        # node count. `parallel` = first-wave width (nodes with no deps → run concurrently), so
        # 扇出 vs 串行 is visible offline. `agents` is capped to keep the line bounded on a big fan-out.
        logger.info(
            "delegate.started",
            nodes=len(plan.nodes),
            call=call_idx,
            parallel=sum(1 for n in plan.nodes if not n.depends_on),
            complexity_hint=complexity_hint,
            coordination=coordination,
            append_to=append_to,
            plan=[
                {"id": n.run_id, "role": n.role, "depends_on": n.depends_on}
                for n in plan.nodes
            ],
            waves=_waves_ids_for_log(
                plan,
                host_for_cross_batch=(
                    host_plan_for_append
                    if host_plan_for_append is not None and not append_to
                    else None
                ),
            ),
            agents=[
                f"{n.role or n.agent_name or n.run_id}: {clip_preview(n.task, 80)}"
                for n in plan.nodes[:_DELEGATE_LOG_AGENTS_CAP]
            ],
            task_chars=[len(n.task or "") for n in plan.nodes],
        )
        # Plan-only eval: real plan path done (build + validate + run_plan). Skip drive
        # so workers / coordination never start; HANDOFF ends the CEO loop immediately.
        from agentcore.runtime.plan_only import is_plan_only

        if is_plan_only():
            # S3: no acceptance_echo (completion_criteria retired).
            summary = f"[plan-only] 已记录计划（{len(plan.nodes)} 节点），跳过执行。"
            if playbook_notes:
                summary = summary + "\n\n" + "\n\n".join(playbook_notes)
            logger.info("delegate.plan_only", nodes=len(plan.nodes), call=call_idx)
            from agentcore.runtime.delegate.batch_shape import annotate_batch_meta

            return annotate_batch_meta(
                ToolResult(
                    tool_call_id="",
                    success=True,
                    output=summary,
                    effect=ToolEffect.HANDOFF,
                    final_text=summary,
                ),
                node_count=len(added_nodes_for_anchor),
                has_deps=any(n.depends_on for n in added_nodes_for_anchor),
                playbook=playbook_name,
                audit_hard=batch_audit_hard,
                includes_review=batch_includes_review,
            )
        from agentcore.runtime.delegate.batch_shape import annotate_batch_meta

        # Live merge seeds from host journal / memory; fresh graphs (incl. prev) start without.
        seed_completed = append_seed if append_to else None

        result = await drive(
            self,
            plan,
            execution_id=execution_id,
            seed_completed=seed_completed,
            seed_notes=seed_notes,
            complexity_hint=complexity_hint,
            coordination=coordination,
            call_idx=call_idx,
            # Omit → True（默认协调）；显式 false → 经典阻塞。勿用 bool(get())，
            # 否则缺省会落成 False，与 schema default 不一致。
            coordinate=(
                bool(arguments["coordinate"])
                if "coordinate" in arguments
                else True
            ),
        )

        # Soft warnings：挂在委派结果尾部，CEO 当轮可见。
        # SUSPEND（开工卡挂起）无 output 可挂，跳过——不改挂起语义。
        if result.output and result.effect is ToolEffect.CONTINUE:
            tails: list[str] = []
            if capability_warning:
                tails.append(capability_warning)
            if playbook_notes:
                tails.extend(playbook_notes)
            if latest_miss_degraded_note:
                tails.append(latest_miss_degraded_note)
            if consumer_deps_warn:
                tails.append(consumer_deps_warn)
            if design_impl_warn:
                tails.append(design_impl_warn)
            if root_slice_warn:
                tails.append(root_slice_warn)
            if prev_execution_id:
                tails.append(
                    "【协作图·续接】本回合新开一队、接续上一张图；"
                    "进度与节点仅计本图，不混入上一张已完成节点。"
                    "向用户汇报请用「新开一队、接续上一张图」口径；不要说成同图追加。"
                )
            elif append_to:
                tails.append(
                    f"【同回合合入】已往本回合协作图追加 "
                    f"{len(added_nodes_for_anchor)} 名成员。"
                )
            elif self._depth == 0:
                # 跨回合接续：latest 解析为主路径（不含图 id）。仅根协调者。
                tails.append(
                    "【协作图】本次已开本回合团队。"
                    '跨回合接续上一张图：delegate 传 append_to_execution_id="latest" '
                    "→ 新开一队并链回；未命中可接续图时引擎自动新建并写明。"
                )
            result.output = f"{result.output}\n\n" + "\n\n".join(tails)
        if result.success and execution_id:
            self._last_graph_execution_id = execution_id
            # 同回合二次合入：保留本图节点快照（journal 未命中时仍可作 existing_plan）。
            from agentcore.runtime.runs.plan import RunPlan as _RunPlan

            self._last_graph_plan = _RunPlan(
                nodes=list(plan.nodes),
                origin=plan.origin,
            )
            # 阻塞跑完才记 seed；协调 kickoff 时队员未完成，勿伪造成完成。
            # 勿仅看 coordinate 入参：默认 true 时 ≥1 worker（含 solo）走协调臂，
            # 须以活跃 session 为准；否则同回合二次合入会把未完成节点当成已完成。
            from agentcore.runtime.coordination.session import active_coordination

            active = active_coordination(execution_id)
            if active is None or not active.active:
                # 每个节点的相直接抄波调度器终态（完成 / 失败 / 跳过 / 取消照抄，
                # 失败原因随行供二次名册点名）；让出或软停后从未跑的节点不在映射里，
                # 也就不进 seed——二次派发仍会调度它们，不会静默漏跑。
                # 协调态 kickoff 会话仍活跃时不 stamp（队员未终态）；后台 drive
                # 收口走 finalize_drive → stamp_last_graph_seed，避免同构闸读到空 seed。
                from agentcore.runtime.delegate.drive_finalize import stamp_last_graph_seed

                stamp_last_graph_seed(self, plan, self._last_drive_results)
        return annotate_batch_meta(
            result,
            node_count=len(added_nodes_for_anchor),
            has_deps=any(n.depends_on for n in added_nodes_for_anchor),
            playbook=playbook if isinstance(playbook, str) else None,
            audit_hard=batch_audit_hard,
            includes_review=batch_includes_review,
        )

    async def _drive(
        self,
        plan: RunPlan,
        *,
        execution_id: str,
        seed_completed: dict[str, RunState] | None,
        seed_notes: list[dict[str, str]] | None = None,
        complexity_hint: str = "standard",
    ) -> ToolResult:
        return await drive(
            self,
            plan,
            execution_id=execution_id,
            seed_completed=seed_completed,
            seed_notes=seed_notes or [],
            complexity_hint=complexity_hint,
            coordination=self._coordination,
        )

    async def resume_plan(
        self,
        plan: RunPlan,
        seed_completed: dict[str, RunState],
        *,
        decision: CheckpointDecision,
        note: str,
        checkpoint_run_ids: set[str],
        execution_id: str,
        coordinate: bool = False,
        apply_kickoff_grant: bool = False,
        coordination: str | None = None,
        team_brief: str | None = None,
        seed_notes: list[dict[str, str]] | None = None,
        ceo_review: dict | None = None,
    ) -> ToolResult:
        # 耐久恢复的批次协作参数回灌：挂起帧带回 coordination / team_brief / seed_notes
        # （挂起点在 setup_note_wall 之前，这批参数只活在工具实例上；恢复走全新实例，
        # 不回灌则 wall 批降级 none → worker 被剥便签三件套）。缺省 None = 在进程内
        # 热续跑，沿用实例现值。
        if coordination in ("wall", "none"):
            self._coordination = coordination
        if team_brief:
            self._team_brief = team_brief
        if seed_notes:
            self._seed_notes = list(seed_notes)
        if decision is CheckpointDecision.STOP:
            # team_preview STOP → soft guidance; plan_review STOP keeps format_for_ceo.
            return await finalize_stopped(
                self,
                plan,
                seed_completed,
                kickoff_cancelled=apply_kickoff_grant,
                note=note,
            )
        if decision is CheckpointDecision.TIMEOUT and apply_kickoff_grant:
            # team_preview TIMEOUT ≠ CONTINUE：不 grant、不开工，回灌 CEO 自行收尾。
            # plan_review TIMEOUT（apply_kickoff_grant=False）本轮仍走下方 drive。
            return await finalize_stopped(
                self,
                plan,
                seed_completed,
                kickoff_timeout=True,
                note=note,
            )
        if decision is CheckpointDecision.ADJUST and apply_kickoff_grant:
            # team_preview ADJUST：不 grant、不开工，意见回灌 CEO 修订后重出卡。
            # plan_review ADJUST（apply_kickoff_grant=False）仍走下方 steer + drive。
            return await finalize_stopped(
                self,
                plan,
                seed_completed,
                kickoff_adjusted=True,
                note=note,
            )

        # Steer: plan_review ADJUST; kickoff CONTINUE+note ≡ 嘱咐注入未跑队员.
        # plan_review CONTINUE+note does not steer (apply_kickoff_grant=False; UI still has 调整).
        if note.strip() and (
            decision is CheckpointDecision.ADJUST
            or (decision is CheckpointDecision.CONTINUE and apply_kickoff_grant)
        ):
            apply_steer(plan, seed_completed, checkpoint_run_ids, note.strip())
        # plan_review CONTINUE：读帧上 llm ceo_review → 压缩 REPLACE 注入 gate_notes。
        # 开工卡路径 (apply_kickoff_grant) 不走；deterministic / 无 review → 不下发。
        if (
            decision is CheckpointDecision.CONTINUE
            and not apply_kickoff_grant
            and ceo_review is not None
        ):
            from agentcore.runtime.delegate.steer import (
                apply_gate_notes,
                compress_ceo_review_for_gate,
            )

            gate_body = compress_ceo_review_for_gate(ceo_review)
            if gate_body:
                apply_gate_notes(plan, seed_completed, checkpoint_run_ids, gate_body)
        # Kickoff (开工卡): continue → grant. ADJUST / TIMEOUT / STOP already returned above.
        # apply_kickoff_grant is True only when resuming a team_preview suspension.
        if (
            apply_kickoff_grant
            and self._approval_gate is not None
            and decision is CheckpointDecision.CONTINUE
        ):
            self._approval_gate.grant_delegation(execution_id)
        # Resume never re-runs the original execute() path, so re-emit run_plan here:
        # FE Option A keeps the same pause bubble + projection key on message_start
        # (reuses the existing assistant; never delete+create) — re-bind the DAG under
        # that same key before worker frames arrive.
        self._sink.emit(plan_event(self, execution_id, plan))
        logger.info(
            "delegate.resume_plan",
            execution_id=execution_id,
            decision=decision.value,
            nodes=len(plan.nodes),
        )
        # plan_review：仅经典路径 durable 挂起（协调态波边界只发 BOUNDARY_YIELD），续跑保持
        # coordinate=False。team_preview：挂在 coordinate fork **之前**，开做后续跑须默认
        # 臂后台（coordinate=True）；显式经典由调用方传 coordinate=False。
        from agentcore.runtime.delegate.batch_shape import annotate_batch_meta

        result = await drive(
            self,
            plan,
            execution_id=execution_id,
            seed_completed=seed_completed,
            # 开工卡恢复补种 CEO 预贴便签（挂起时尚未上墙）；plan_review 恢复不带（已上墙）。
            seed_notes=list(seed_notes or []),
            coordination=self._coordination,
            coordinate=coordinate,
        )
        return annotate_batch_meta(
            result,
            node_count=len(plan.nodes),
            has_deps=any(n.depends_on for n in plan.nodes),
        )

    async def replan(self, arguments: dict[str, Any]) -> ToolResult:
        from agentcore.runtime.delegate.force_scopes import parse_force_scopes
        from agentcore.runtime.runs import BoundaryReason

        # 与 execute 对称：replan 只吃自己这次的 force，绝不沿用上一次 delegate 的
        # 放行（旧实现读实例上的 `_delegate_force`，一次冷派的 force 会一路漏到这里）。
        self._force_scopes = parse_force_scopes(arguments.get("force"))

        sup = self._supervised
        if sup is None:
            msg = (
                "当前没有待续跑的受监督计划。replan 仅在 delegate 让出边界（输出『计划已"
                "让出』）或部分队员失败/跳过后可用。批次已收口后要动同一支团队，改调 "
                "delegate 并在 tasks[] 上点名上一批的 run_id："
                "让原作者接着干填 continue_from_run_id，补失败/跳过缺口填 replaces_run_id；"
                "真发起新任务同样用 delegate。"
            )
            return ToolResult(tool_call_id="", success=False, output="", error=msg)

        binds = arguments.get("binds") or []
        steers = arguments.get("steers") or []
        adds = arguments.get("add") or []
        stop = bool(arguments.get("stop"))
        if (
            not isinstance(binds, list)
            or not isinstance(steers, list)
            or not isinstance(adds, list)
        ):
            msg = "replan 的 binds / steers / add 必须是数组。"
            return ToolResult(tool_call_id="", success=False, output="", error=msg)
        locked = bool(getattr(sup.plan, "topology_lock", False)) or bool(
            getattr(self, "_topology_lock", False)
        )
        if locked and adds:
            msg = (
                "当前为工作流拓扑锁：禁止 replan(add=…) 新增步骤；"
                "可用 steers 改未跑步骤说明，或 stop=true 收口。"
            )
            return ToolResult(tool_call_id="", success=False, output="", error=msg)
        if sup.reason is BoundaryReason.BIND and not stop and not binds:
            msg = (
                "replan 需要 binds 定稿至少一个『待定稿』步骤，或设 stop=true 收口"
                "（仅 steers / add 不能让待定稿步骤运行起来）。"
            )
            return ToolResult(tool_call_id="", success=False, output="", error=msg)

        # Snapshot the pre-add node ids so we can tell which nodes apply_replan appended
        # (it mutates the plan in place) — those drive the re-emitted run_plan below.
        ids_before = {n.run_id for n in sup.plan.nodes}
        errors = await apply_replan(self, sup.plan, sup.completed, binds, steers, adds)
        if errors:
            # Seat/artifact rejects share append's message family — surface verbatim.
            if len(errors) == 1 and str(errors[0]).startswith("【队员追加已拒绝"):
                logger.info("replan.rejected", errors=errors, via="seat_admit")
                from agentcore.core.types import ToolEffect

                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=str(errors[0]),
                    effect=ToolEffect.CONTINUE,
                    contract_failure=True,
                )
            msg = "replan 无效：" + "；".join(errors)
            logger.info("replan.rejected", errors=errors)
            return ToolResult(tool_call_id="", success=False, output="", error=msg)

        self._supervised = None
        record_plan_snapshot(sup.plan)
        added_nodes = [n for n in sup.plan.nodes if n.run_id not in ids_before]
        # 波边界追加节点 (设计 §7.1): re-emit run_plan so the grown DAG's new nodes merge onto
        # the live graph (same execution_id → the frontend folds merge, never reset, exactly
        # like a second delegate batch). Journaled, so the appended nodes replay on reload;
        # without this their run_started/run_completed would target unknown ids and be dropped.
        if added_nodes:
            self._sink.emit(plan_event(self, sup.execution_id, sup.plan))
        # 「计划已调整」轻痕迹 (设计 §7.2): surface the autonomous re-bind / re-steer onto the
        # affected graph nodes (bind=据上游证据定稿待绑定步骤; steer=偏离后操舵未跑步骤). A node
        # both bound AND steered reads as the bigger event (bind). Emitted only when something
        # changed — a no-op SCOPE resume (replan() 续跑) sends nothing. Appended nodes are NEW
        # (not revised), so they ride the run_plan merge above, not this trace.
        revised: dict[str, str] = {}
        for b in binds:
            rid = str(b.get("run_id") or "").strip() if isinstance(b, dict) else ""
            if rid:
                revised[rid] = "bind"
        for s in steers:
            rid = str(s.get("run_id") or "").strip() if isinstance(s, dict) else ""
            if rid and rid not in revised:
                revised[rid] = "steer"
        if revised:
            self._sink.emit(
                plan_revised(
                    execution_id=sup.execution_id,
                    revisions=[{"run_id": rid, "kind": kind} for rid, kind in revised.items()],
                )
            )
        logger.info(
            "replan.applied",
            binds=len(binds),
            steers=len(steers),
            adds=len(added_nodes),
            stop=stop,
        )
        from agentcore.runtime.audit.hooks import on_replan

        on_replan(
            execution_id=sup.execution_id,
            binds=binds,
            steers=steers,
            adds=len(added_nodes),
            stop=stop,
        )
        if stop:
            return await finalize_stopped(self, sup.plan, sup.completed)
        return await drive(
            self,
            sup.plan,
            execution_id=sup.execution_id,
            seed_completed=sup.completed,
            coordination=self._coordination,
            coordinate=False,
        )

    async def dispose_open_supervised(self) -> ToolResult | None:
        """Turn-end disposition of a plan the CEO yielded at a boundary but never resumed
        (受监督的波循环 P5「Edge」: turn 末仍开着的 supervised run).

        The yield path returns the boundary brief WITHOUT folding the已完成 workers' usage /
        ledger / citations — those fold on the resume's terminal drive. If the captain loop
        ends first (the CEO answered without a ``replan``, hit MAX_ROUNDS, errored upstream…),
        that spend would be stranded (unbilled, sources unshown). Treat it as an implicit
        ``stop``: fold the completed work in and materialise the un-run tail SKIPPED — the
        exact ``replan(stop=true)`` path — then release the dangling state. No-op when nothing
        is paused. The host calls this once at turn end; the returned ToolResult is unused (the
        CEO already moved on), it exists only to reuse the stop path verbatim.
        """
        sup = self._supervised
        if sup is None:
            return None
        self._supervised = None
        logger.info(
            "delegate.supervised_disposed",
            reason=sup.reason.value,
            completed=len(sup.completed),
            pending=sum(1 for n in sup.plan.nodes if n.run_id not in sup.completed),
        )
        return await finalize_stopped(self, sup.plan, sup.completed)
