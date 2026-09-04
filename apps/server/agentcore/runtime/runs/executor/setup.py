"""AGENT-node setup: registry / identity / opening messages.

Split from ``.node`` — pure move; consumed only by the node facade.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.llm.profiles import ProfileParams
from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.events import (
    escalation_raised,
    run_context,
)
from agentcore.runtime.facts import RunHeadFact, record_turn_fact
from agentcore.runtime.runs.constants import (
    DEFAULT_CONTRACT_RETRIES,
    ESCALATE_TOOL_NAME,
    HANDOFF_TOOL_NAME,
    MAX_CONTRACT_RETRIES,
    MAX_DELEGATION_DEPTH,
)
from agentcore.runtime.runs.contract import node_has_dependents
from agentcore.runtime.runs.executor.context import (
    _build_messages,
    _context_block_payloads,
    load_context_inject_files,
)
from agentcore.runtime.runs.executor.env import AgentExecutorEnv
from agentcore.runtime.runs.executor.escalation import build_escalation_channel
from agentcore.runtime.runs.executor.identities import (
    LeadSubteam,
    build_worker_identity,
)
from agentcore.runtime.runs.executor.retry import _files_expected
from agentcore.runtime.runs.executor.shared import (
    _continuation_message,
    _registry_with,
    _registry_without,
)
from agentcore.runtime.runs.retrieval_budget import RETRIEVAL_TOOL_NAMES
from agentcore.runtime.runs.types import ContextBlock, RunPhase, RunSpec, RunState
from agentcore.tools.protocol import (
    RetrievalBudgetState,
    ToolContext,
    fork_explore_write_scope,
)
from agentcore.tools.registry import ToolRegistry

logger = get_logger(__name__)


def _emit_prepare_phase(phase: str, started: float) -> None:
    """One ``worker.prepare_phase`` line (phase + ms) at info — default jsonl keeps it.

    debug is dropped in default jsonl (see ``cost.prefix_cache``); do not downgrade.
    """
    logger.info(
        "worker.prepare_phase",
        phase=phase,
        ms=int((time.monotonic() - started) * 1000),
    )


@contextmanager
def _prepare_phase(phase: str) -> Iterator[None]:
    started = time.monotonic()
    try:
        yield
    finally:
        _emit_prepare_phase(phase, started)


async def _timed_phase[T](phase: str, awaitable: Awaitable[T]) -> T:
    with _prepare_phase(phase):
        return await awaitable


@dataclass
class AgentNodePrepared:
    """Immutable-ish bag of setup outputs for the contract loop."""

    profile: ProfileParams
    priced_model: str
    request_model: str
    tool_ctx: ToolContext
    worker_tools: ToolRegistry
    allowed_tools: list[str] | None
    lead_subteam: LeadSubteam | None
    deliverable: Any
    deliverable_form: Any
    files_expected: bool
    report_delivery: bool
    product_landing_artifacts: list[str] | None
    short_write_posture: bool
    tighten_verify_exec_thrash: bool
    received_blocks: list[ContextBlock]
    token_ceiling: int
    attempts: int
    two_phase: bool


async def prepare_agent_node(
    env: AgentExecutorEnv,
    spec: RunSpec,
    completed: Mapping[str, RunState],
    agent_id: str,
    *,
    messages: list[LLMMessage],
    resolutions: dict[str, dict[str, Any]],
) -> AgentNodePrepared:
    """Resolve profile/tools/identity and assemble the worker opening transcript."""
    started = time.monotonic()
    try:
        return await _prepare_agent_node(
            env,
            spec,
            completed,
            agent_id,
            messages=messages,
            resolutions=resolutions,
        )
    finally:
        _emit_prepare_phase("total", started)


async def _prepare_agent_node(
    env: AgentExecutorEnv,
    spec: RunSpec,
    completed: Mapping[str, RunState],
    agent_id: str,
    *,
    messages: list[LLMMessage],
    resolutions: dict[str, dict[str, Any]],
) -> AgentNodePrepared:
    """Inner cold-open body; wall-clock wrapped by ``prepare_agent_node``."""
    deliverable = spec.deliverable
    profile = env.profiles.agent()
    if spec.max_rounds is not None and spec.max_rounds > 0:
        profile = replace(profile, max_rounds=int(spec.max_rounds))
    from agentcore.runtime.costing import resolve_run_models

    priced_model, request_model = resolve_run_models(
        env.profiles, spec.model, cost_role=env.cost_role
    )
    # 跨文件夹指挥 · 形状甲：有目标 folder → 换 backend + 记忆跟桌（不改会话挂载）。
    worker_tools_base = env.tools
    system_prompt = env.system_prompt
    base_ctx = env.base_tool_context
    if spec.target_folder_id:
        from agentcore.runtime.delegate.target_desktop import apply_target_desktop

        applied = await _timed_phase(
            "target_desktop",
            apply_target_desktop(
                target_folder_id=spec.target_folder_id,
                session_folder_id=env.session_folder_id,
                env_system_prompt=env.system_prompt,
                base_tool_context=env.base_tool_context,
                worker_tools=env.tools,
                sink=env.sink,
                local_root_claims=env.local_root_claims,
                permission_axes=env.permission_axes_obj,
            ),
        )
        worker_tools_base = applied.worker_tools
        system_prompt = applied.system_prompt
        base_ctx = applied.tool_ctx

    # 方案 C：无出生且无 target → 坐会话 scratch，默认禁写（冷启动 explore_memory 例外）。
    from agentcore.runtime.delegate.target_desktop import (
        SCRATCH_NO_WRITE_IDENTITY_HINT,
        resolve_bare_chat_write_scope,
    )

    worker_write_scope = resolve_bare_chat_write_scope(
        target_folder_id=spec.target_folder_id,
        session_folder_id=env.session_folder_id,
        base_write_scope=getattr(base_ctx, "write_scope", "project") or "project",
        turn_created_folder_ids=getattr(base_ctx, "turn_created_folder_ids", None),
    )
    tool_ctx = replace(
        base_ctx,
        run_id=spec.run_id,
        agent_id=agent_id,
        execution_id=env.execution_id,
        write_coordinator=env.write_coordinator,
        write_ancestors=env.ancestors_by_id.get(spec.run_id, frozenset()),
        ownership_desk_id=(
            str(spec.target_folder_id or env.session_folder_id or "").strip() or None
        ),
        _explore_gate=fork_explore_write_scope(base_ctx, worker_write_scope),
        # 升级实时可见: give this worker's escalate tool a live channel back to the
        # run's SSE stream. The executor owns event shape (引擎纯化) — escalate just
        # hands it the (question, assumption, blocking) triple. run_id/agent_id are
        # bound here so the team UI attributes the escalation to the right node.
        on_escalate=lambda question, assumption, blocking, kind="normal", _rid=spec.run_id, _aid=agent_id: (  # noqa: E501
            env.sink.emit(
                escalation_raised(
                    _rid,
                    _aid,
                    question=question,
                    assumption=assumption,
                    blocking=blocking,
                    kind=kind,
                )
            )
        ),
        # 阻塞式求决策: the suspend-for-the-user channel for escalate(blocking=true).
        # None when no bridge (CEO / tests) → escalate stays non-blocking.
        escalation=build_escalation_channel(env, spec.run_id, agent_id, resolutions),
        agent_role=spec.role or "",
        # 检索预算 (提案 A1): per-run counter for tool_exec; None = no enforce.
        retrieval_budget=(
            RetrievalBudgetState(limit=spec.retrieval_budget)
            if spec.retrieval_budget is not None
            else None
        ),
        # Debate evidence posture: structured run signal → web_search filter.
        search_policy=spec.search_policy or "",
        # Investigate/review posture: refuse outer typecheck/build on test_run.
        verify_policy=spec.verify_policy or "",
        # 成篇交接：空交不再硬拒；下游靠指针 / 缺席标注消费已有内容。
        handoff_requires_body=False,
        handoff_min_body_chars=0,
        handoff_deliverable_form=(
            deliverable.form if deliverable is not None else None
        ),
    )
    # 阶段2 嵌套子任务: hand this worker delegation tools when opted in.
    _trim_started = time.monotonic()
    worker_tools = worker_tools_base
    # 真纯丙：不再用 spec.tools 做 allow-list；默认全开相关工具面。
    allowed_tools = None
    # A worker may nest a sub-team purely by tree position: any depth below the
    # cap is a captain (delegation is on by default); depth-3 sub-workers are
    # leaves because the executor withholds the delegate tools here.
    lead_subteam: LeadSubteam | None = None
    is_captain = (
        env.delegate_factory is not None
        and spec.depth < MAX_DELEGATION_DEPTH
    )
    if is_captain:
        # Bundle still mints delegate + companion replan (dispose / 波边界 binding,
        # 受监督子计划 B 去特例). Opening offer is delegate only — same idea as
        # CEO idle vs coordination. replan lands after nested delegate sets
        # _supervised, via promote_coordination_surface_if_needed. Turn-end
        # dispose still runs in the finally below.
        lead_subteam = env.delegate_factory(spec.run_id, spec.depth)
        # §4.2b·3：子派默认继承父目标桌（再点名才换）。
        child_delegate = lead_subteam.tools[0]
        parent_desk = spec.target_folder_id or env.session_folder_id
        if parent_desk:
            child_delegate._default_target_folder_id = parent_desk  # type: ignore[attr-defined]
        opening = tuple(t for t in lead_subteam.tools if t.schema.name != "replan")
        worker_tools = _registry_with(worker_tools, *opening)
        from agentcore.runtime.runs.executor.captain_consult import (
            offer_nested_lead_consult,
        )

        worker_tools, system_prompt = await offer_nested_lead_consult(
            worker_tools,
            system_prompt,
            user_id=str(getattr(tool_ctx, "user_id", "") or ""),
        )
        # allowed_tools stays None — "offer all" already includes the opening
        # lead_subteam tools now living in worker_tools.
    # Topology-split handoff wording + deliverable.form: DAG is known at identity
    # build — upstream nodes get imperative「必须 handoff」; leaves get conditional
    # 「有增量才写」. form=prose/files/workspace selects the landing block
    # (omit = files). Non-empty artifacts with form omitted → files block.
    deliverable_form = deliverable.form if deliverable is not None else "files"
    from agentcore.runtime.engine.governance import registry_can_execute

    identity = build_worker_identity(
        has_dependents=node_has_dependents(env.plan, spec.run_id),
        captain=is_captain,
        depth=spec.depth,
        form=deliverable_form,
        artifacts=list(deliverable.artifacts) if deliverable else None,
        # 能写≠能跑: 只看注册表是否装配 ``run``（环境死不再卸工具）。
        can_execute=registry_can_execute(worker_tools, tool_ctx),
    )
    if worker_write_scope == "none" and not (
        spec.target_folder_id or env.session_folder_id
    ):
        identity = f"{identity.rstrip()}\n\n{SCRATCH_NO_WRITE_IDENTITY_HINT}"
    # 真纯丙·H2：form=prose 不再硬卸写盘工具；形态靠 identity 提示自觉守岗。
    # Short-round repair posture tool strip retired.
    # CEO may still stamp max_rounds; tools stay full surface.
    files_expected = _files_expected(deliverable)
    from agentcore.runtime.runs.research_quality import deliverable_is_report_delivery

    report_delivery = deliverable_is_report_delivery(deliverable)
    product_landing_artifacts: list[str] | None = (
        list(deliverable.artifacts)
        if deliverable is not None and deliverable.artifacts
        else None
    )
    from agentcore.runtime.delegate.completion import node_holds_execution_tools
    from agentcore.runtime.runs.worker_budget import (
        is_short_write_posture,
        should_tighten_verify_exec_thrash,
    )

    short_write_posture = is_short_write_posture(max_rounds=spec.max_rounds)
    tighten_verify_exec_thrash = should_tighten_verify_exec_thrash(
        short_write_posture=short_write_posture,
        files_expected=files_expected,
        has_execution_tools=node_holds_execution_tools(spec),
    )
    # 检索预算 0 (提案 A1): strip web_search/read_url even for unrestricted workers
    # (builder already tightens tasks[].tools when valid_tools is known).
    if spec.retrieval_budget == 0:
        worker_tools = _registry_without(worker_tools, *RETRIEVAL_TOOL_NAMES)
        if allowed_tools is not None:
            allowed_tools = [t for t in allowed_tools if t not in RETRIEVAL_TOOL_NAMES]
    # escalate is a worker's always-available upward channel — a safety primitive,
    # not a capability the CEO restricts away. An unrestricted worker (None) is
    # already offered it; a least-privilege worker (non-empty allow-list) must
    # keep it explicitly, so it can still flag a blocker instead of guessing.
    if allowed_tools is not None and ESCALATE_TOOL_NAME not in allowed_tools:
        allowed_tools = [*allowed_tools, ESCALATE_TOOL_NAME]
    # handoff is the worker's always-available finish/brief channel — same posture as
    # escalate. CEO omit must not allowlist_deny a depth≥1 worker that still has tools.
    # Leaf nodes need not *call* it, but the tool must stay on the surface.
    if allowed_tools is not None and HANDOFF_TOOL_NAME not in allowed_tools:
        allowed_tools = [*allowed_tools, HANDOFF_TOOL_NAME]
    _emit_prepare_phase("tool_trim", _trim_started)
    from agentcore.runtime.audit.hooks import on_permission_effective

    on_permission_effective(
        execution_id=env.execution_id,
        run_id=spec.run_id,
        parent_run_id=spec.parent_run_id,
        declared_tools=None if spec.tools is None else list(spec.tools),
        effective_tools=None if allowed_tools is None else list(allowed_tools),
        depth=spec.depth,
    )

    # Wave3 B: force-inject skeleton/contract summaries before first file_read.
    context_inject = await load_context_inject_files(
        tool_ctx.backend,
        list(getattr(spec, "context_inject_files", None) or []),
    )
    # Build the worker's opening (system + task) ONCE; auto-rework then
    # CONTINUES on this SAME transcript (append the shortfall, re-run)
    # instead of rebuilding from scratch — so the worker sees its own prior
    # draft when correcting (修隐患), and the finished transcript is captured
    # as a recoverable RunSession for 定向唤回 (统一「续写」原语, 见 §三).
    # received_blocks captures the SAME ContextBlocks the opening was rendered
    # from (单一源), so the run_context event ships exactly what the LLM was fed.
    #
    # Wave infra-retry 热续: when the scheduler seeds ``completed[self]`` with the
    # prior FAILED+transcript attempt, resume that site (continue semantics) instead
    # of cold ``_build_messages`` — same run_id, consume the hung transcript.
    received_blocks: list[ContextBlock] = []
    prior_attempt = completed.get(spec.run_id)
    # Cold-open vs infra-retry continuation: hung FAILED+transcript resumes in place.
    cold_open = not (
        prior_attempt is not None
        and prior_attempt.phase is RunPhase.FAILED
        and prior_attempt.transcript
    )
    if not cold_open:
        from agentcore.runtime.runs.executor.continuation import (
            _record_continuation_run_head,
            _strip_historical_reasoning,
        )

        messages[:] = _strip_historical_reasoning(list(prior_attempt.transcript))
        messages.append(
            _continuation_message(
                "上一跳因临时上游失败中断。请在已有现场与产出上继续完成原任务。"
            )
        )
        _record_continuation_run_head(
            spec.run_id, messages, from_context_blocks=False
        )
    else:
        with _prepare_phase("build_messages"):
            messages[:] = _build_messages(
                env.plan,
                spec,
                completed,
                system_prompt,
                env.user_message,
                deliverable,
                identity=identity,
                blocks_sink=received_blocks,
                team_brief=env.team_brief,
                context_inject=context_inject or None,
            )
        # Worker window head (§8.3): journal the opening task-prompt so
        # ``window_from_journal(run_id=…)`` anchors on THIS run's system+user, not the
        # turn-level CEO ``turn_started``. ``user_origin=context_blocks`` marks the
        # opening user as the ContextBlock join (diagnostic UI replaces it with the
        # structured ``run_context`` segments).
        record_turn_fact(
            RunHeadFact(
                run_id=spec.run_id,
                system_prompt=messages[0].content or "",
                user_message=messages[1].content or "",
                user_origin="context_blocks",
            ).to_fact()
        )
        # 上下文传递可视化: emit the received context right after assembly (before the
        # LLM react loop) so the frontend's run detail lights up its「收到的上下文」as
        # soon as the worker starts thinking. Bodies capped + journaled (see run_context).
        env.sink.emit(
            run_context(spec.run_id, agent_id, _context_block_payloads(received_blocks))
        )

    # Worker 累计 token 硬顶 (loose backstop · 真执行): compaction (tool_clear)
    # 挑大梁做上下文瘦身,这只在失控时收口。≤0 = 关闭。
    # react_loop 每轮末比对累计 usage。CEO / solo 路径不经此分支,保持 0。
    # 辩论辩手两阶段检索与普通 worker 共用 ``engine_worker_token_ceiling``：
    # 优先 ``spec.token_ceiling``（派单统一 backstop）；未解析则回落全局默认。
    # 全局 ``engine_worker_token_ceiling≤0`` 仍表示关闭硬顶（已回填的 spec 值亦忽略）。
    if settings.engine_worker_token_ceiling <= 0:
        token_ceiling = 0
    elif spec.token_ceiling is not None and spec.token_ceiling > 0:
        token_ceiling = spec.token_ceiling
    else:
        token_ceiling = settings.engine_worker_token_ceiling

    attempts = 1 + min(DEFAULT_CONTRACT_RETRIES, MAX_CONTRACT_RETRIES)

    from agentcore.runtime.runs.executor.hooks import _two_phase_citation

    two_phase = _two_phase_citation(deliverable)

    return AgentNodePrepared(
        profile=profile,
        priced_model=priced_model,
        request_model=request_model,
        tool_ctx=tool_ctx,
        worker_tools=worker_tools,
        allowed_tools=allowed_tools,
        lead_subteam=lead_subteam,
        deliverable=deliverable,
        deliverable_form=deliverable_form,
        files_expected=files_expected,
        report_delivery=report_delivery,
        product_landing_artifacts=product_landing_artifacts,
        short_write_posture=short_write_posture,
        tighten_verify_exec_thrash=tighten_verify_exec_thrash,
        received_blocks=received_blocks,
        token_ceiling=token_ceiling,
        attempts=attempts,
        two_phase=two_phase,
    )
