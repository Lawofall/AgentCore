"""同人续派：``continue_from_run_id`` 校验、执行与单 run 完成即登记。"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.runtime.runs.constants import DEFAULT_RECALL_LIMIT
from agentcore.runtime.runs.types import ContextBlock, RunPhase, RunSpec, RunState

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.session import RunSession

DelegateTool = Any

logger = get_logger(__name__)


class ContinuationRejectedError(Exception):
    """输入校验失败：该项拒绝续派，驱动层折成 FAILED RunState 交回 CEO。

    ``cause`` 枚举（观测 / CEO 文案分流，勿暗示「id 抄错」）：
    ``empty`` / ``self`` / ``in_progress`` / ``cancelled`` / ``never_ran`` /
    ``recall_limit`` / ``evicted`` / ``loader_absent`` / ``loader_miss`` / ``not_found``.
    ``in_progress`` 只给真·未终局的目标——终局但无现场的 CANCELLED / SKIPPED 各有其 cause，
    否则文案会叫 CEO 去等一个不会完成的节点。
    """

    def __init__(self, message: str, *, cause: str = "not_found") -> None:
        super().__init__(message)
        self.message = message
        self.cause = cause


def merge_continuation_tools(
    prior: list[str] | None,
    declared: list[str] | None,
) -> list[str] | None:
    """续写有效工具面：新 task ``tools`` 只增不减（超集 merge）。

    - ``declared is None``（未声明）→ 沿用 ``prior``
    - ``prior is None``（原现场无限制）→ 保持 ``None``（不得减面成白名单）
    - 双方皆为列表 → 并集（先 prior 序，再追加新名）；子集声明不减面
    """
    if declared is None:
        return prior
    if prior is None:
        return None
    seen = set(prior)
    out = list(prior)
    for name in declared:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


async def apply_continuation_tool_merges(plan: RunPlan, tool: DelegateTool) -> None:
    """入闸前：对 ``continue_from`` 节点把声明 tools merge 进现场有效面（只增不减）。

    同步回写 ``session.spec.tools``，使 ``continue_run`` 与能力闸看到同一超集。
    双 miss 的节点跳过（后续 ``resolve_session`` 仍会明确拒绝）。
    """
    for node in plan.nodes:
        cf = (node.continue_from_run_id or "").strip()
        if not cf:
            continue
        session = None
        if tool._session_store is not None:
            session = tool._session_store.get(cf)
        if session is None and tool._session_loader is not None:
            session = await tool._session_loader(cf)
            if session is not None and tool._session_store is not None:
                tool._session_store.put(session)
        if session is None:
            continue
        merged = merge_continuation_tools(session.spec.tools, node.tools)
        node.tools = merged
        if merged != session.spec.tools:
            session.spec = replace(session.spec, tools=merged)


async def resolve_session(
    tool: DelegateTool,
    continue_from_run_id: str,
    *,
    own_run_id: str,
    completed: Mapping[str, RunState] | None = None,
) -> RunSession:
    """校验并取回目标现场。失败抛 :class:`ContinuationRejectedError`（明确报错，不静默降级）。"""
    target = continue_from_run_id.strip()
    if not target:
        raise ContinuationRejectedError("continue_from_run_id 不能为空。", cause="empty")
    if target == own_run_id:
        raise ContinuationRejectedError(
            f"continue_from_run_id 不能自指（`{target}`）。请填已完成的其它 run，"
            "或去掉该字段走冷委派。",
            cause="self",
        )
    # 终局 run（COMPLETED / FAILED）可带现场续写；FAILED 放行——CEO 用 continue_from 让原作者
    # 在失败草稿上改写正是同人续派的用途。CANCELLED / SKIPPED 同样是【终局】，只是没有登记
    # 可续现场：把它们混进「仍在进行中，请用 depends_on 等它完成」会把 CEO 指向一个永远不会
    # 再完成的节点，白等一整波。三种情形各自说实话，各给一条真的走得通的路。
    if completed is not None and target in completed:
        st = completed[target]
        if st.phase is RunPhase.CANCELLED:
            raise ContinuationRejectedError(
                f"目标 run `{target}` 已中断（cancelled：改方向 / 超时强杀 / 用户只停这项），"
                "它不会再完成，也没有登记可续写现场。请改冷委派并设 `replaces_run_id` 标接手"
                "（**不要**用 depends_on 等它）。",
                cause="cancelled",
            )
        if st.phase is RunPhase.SKIPPED:
            raise ContinuationRejectedError(
                f"目标 run `{target}` 从未执行（skipped：级联跳过 / 中止），没有现场可续。"
                "请改冷委派并设 `replaces_run_id` 标接手。",
                cause="never_ran",
            )
        if st.phase not in (RunPhase.COMPLETED, RunPhase.FAILED):
            raise ContinuationRejectedError(
                f"目标 run `{target}` 仍在进行中（{st.phase.value}），无法带现场续派。"
                "请用 depends_on 等它完成后再续，或改冷委派。",
                cause="in_progress",
            )
    elif completed is not None:
        # 同批尚未出现在 completed：若 plan 里存在该节点且本节点依赖它，调度保证先跑完；
        # 否则视为「进行中 / 未完成」拒绝，避免竞态读半成品。
        pass

    session = None
    if tool._session_store is not None:
        session = tool._session_store.get(target)
    loader = tool._session_loader
    if session is None and loader is not None:
        session = await loader(target)
        if session is not None and tool._session_store is not None:
            tool._session_store.put(session)
            # Loader hit on a tip id is rare (DB keys are roots); if tip somehow
            # persisted as root, no alias needed. If we resolved via in-memory
            # alias already, session is non-None above.
    if session is None and tool._session_store is not None and loader is not None:
        # Tip id miss in memory: try loading the aliased root if we still have the map
        # after a partial prune (alias present, root session flushed to DB only).
        root = tool._session_store.root_for_alias(target)
        if root:
            session = await loader(root)
            if session is not None:
                tool._session_store.put(session)
                tool._session_store.link_alias(target, session.run_id)
                logger.info(
                    "delegate.continuation_alias_rehydrated",
                    tip_run_id=target,
                    root_run_id=session.run_id,
                )
    if session is None:
        raise _miss_rejected(tool, target)
    if session.recall_count >= DEFAULT_RECALL_LIMIT:
        # 与其它 cause 不同：这条不是「参数填错」，而是业务上限，CEO 会拿它向用户解释
        # 为什么这块还没改好（案 b25bdb59 实测被转述进用户气泡）。故用人话写——万一
        # 原样外露，用户读到的仍是一句能懂的话，不是内部编排术语。
        raise ContinuationRejectedError(
            f"队员 `{target}` 已经返工 {DEFAULT_RECALL_LIMIT} 次，不能再找同一个人改了。"
            "换一位队员接手：`delegate` 时设 `replaces_run_id` 指向它。",
            cause="recall_limit",
        )
    return session


def _miss_rejected(tool: DelegateTool, target: str) -> ContinuationRejectedError:
    """Build a roster-miss rejection with a distinguishable cause + honest copy."""
    store = tool._session_store
    evict_reason = store.eviction_reason(target) if store is not None else None
    if evict_reason is None and store is not None:
        root = store.root_for_alias(target)
        if root:
            evict_reason = store.eviction_reason(root)
    if evict_reason is not None:
        return ContinuationRejectedError(
            f"run_id `{target}` 的可续写现场已被内存 roster 淘汰"
            f"（reason={evict_reason}）。这不是 id 填错——请改用冷委派并设 "
            "`replaces_run_id` 标接手。",
            cause="evicted",
        )
    if tool._session_loader is None:
        return ContinuationRejectedError(
            f"找不到 run_id 为 `{target}` 的可续写现场（仅查了内存；本回合未装配落盘 "
            "loader，不能写成「落盘未命中」）。现场可能已淘汰或从未登记——请改用冷委派并设 "
            "`replaces_run_id` 标接手。",
            cause="loader_absent",
        )
    return ContinuationRejectedError(
        f"找不到 run_id 为 `{target}` 的可续写现场（内存与落盘均未命中）。"
        "若该 id 是图上续派链末端，请改填现场根（wire `continues_run_id` / "
        "首次冷开的 run_id）；或改用冷委派并设 `replaces_run_id` 标接手。",
        cause="loader_miss",
    )


async def run_continuation(
    tool: DelegateTool,
    spec: RunSpec,
    completed: Mapping[str, RunState],
    *,
    execution_id: str,
    approval_gate: Any,
) -> RunState:
    """执行带现场续派：校验 → continue_run → 提交 session → 计入续派账。"""
    from agentcore.runtime.runs import continue_run

    assert spec.continue_from_run_id
    try:
        session = await resolve_session(
            tool,
            spec.continue_from_run_id,
            own_run_id=spec.run_id,
            completed=completed,
        )
    except ContinuationRejectedError as exc:
        logger.info(
            "delegate.continuation_rejected",
            run_id=spec.run_id,
            continue_from=spec.continue_from_run_id,
            reason=exc.message,
            cause=exc.cause,
        )
        # 图态接缝：plan 已入图的节点若无 run_failed，前端会卡在「排队中」。
        # 拒续在进 continue_run / run_started 之前，须显式下发终态帧。
        if tool._sink is not None:
            from agentcore.runtime.events.run import run_failed

            agent_id = (spec.agent_id or spec.run_id or "").strip() or spec.run_id
            tool._sink.emit(
                run_failed(
                    spec.run_id,
                    agent_id,
                    exc.message,
                    failure_kind="call",
                    execution_id=execution_id,
                )
            )
        # 折成 FAILED 交回 CEO，并标记调度层不可重试（复用 executor contract.failed 的
        # non-retryable 机制）：输入校验是确定性的，重跑必再拒——避免同错在日志里重放两次。
        return RunState(phase=RunPhase.FAILED, error=exc.message, error_retryable=False)

    # 工具面只增不减：新 task 声明的 tools merge 进现场（入闸可能已做过，此处幂等）。
    merged_tools = merge_continuation_tools(session.spec.tools, spec.tools)
    if merged_tools != session.spec.tools:
        session.spec = replace(session.spec, tools=merged_tools)
    if merged_tools != spec.tools:
        spec.tools = merged_tools

    # Per-worker 模型：本次显式改则覆盖 session；省略则继承该 run 已解析模型。
    explicit_model = (spec.model or "").strip()
    if explicit_model and explicit_model != (session.spec.model or "").strip():
        session.spec = replace(session.spec, model=explicit_model)

    # 依赖产物：与冷开局同构，写入续干 feedback 正文（LLM）+ continuation 通道块（UI）。
    # 本轮 team_brief 冷开局会进 system；续写回放旧 system，故改挂续干 user 指令。
    feedback, context_blocks = _continuation_prompt(
        spec, completed, team_brief=getattr(tool, "_team_brief", None)
    )
    try:
        state = await continue_run(
            session=session,
            feedback=feedback,
            continuation_run_id=spec.run_id,
            llm=tool._llm,
            tools=tool._tools,
            sink=tool._sink,
            base_tool_context=tool._base_tool_context,
            execution_id=execution_id,
            profile_set=tool._profile_set,
            approval_gate=approval_gate,
            context_blocks=context_blocks,
            parent_run_id=spec.parent_run_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "delegate.continuation_failed",
            run_id=spec.run_id,
            continue_from=spec.continue_from_run_id,
        )
        return RunState(phase=RunPhase.FAILED, error=str(exc))

    # 续派完成后回写现场，对齐「有 transcript 的终局 run 现场可续」：只要续写产生了可续
    # transcript（终局 COMPLETED / FAILED）就更新，不再要求聊天正文非空——文件形态交付常以
    # 空正文 + 落盘文件收尾。content 为空则保留旧值供展示。异常崩溃无 transcript ⇒ 不回写。
    if state.phase in (RunPhase.COMPLETED, RunPhase.FAILED) and state.transcript:
        session.recall_count += 1
        session.transcript = state.transcript
        session.content = state.content or session.content
        session.updated_at = time.time()
        if tool._session_store is not None:
            tool._session_store.put(session)
            # 星型存：链末端 id 别名到根，使下一跳 continue_from 填图上可见节点仍可溯根。
            if spec.run_id != session.run_id:
                tool._session_store.link_alias(spec.run_id, session.run_id)
        if tool._session_saver is not None:
            await tool._session_saver(session)
        tool.note_continuation(spec.run_id)
        logger.info(
            "delegate.continuation_ok",
            run_id=spec.run_id,
            continues_run_id=session.run_id,
            recall_count=session.recall_count,
        )
    return state


def _continuation_prompt(
    spec: RunSpec,
    completed: Mapping[str, RunState],
    *,
    team_brief: str | None = None,
) -> tuple[str, list[ContextBlock]]:
    """组装续干指令正文 + UI 上下文块（task + 上游依赖 + 本轮团队共识）。"""
    parts = [spec.task.strip()]
    blocks = [
        ContextBlock(channel="continuation", heading="续干指令", body=spec.task.strip()),
    ]
    brief = (team_brief or "").strip()
    if brief:
        heading = "团队共识（主协调为本回合设定，全员遵循）"
        parts.append(f"## {heading}\n{brief}")
        blocks.append(ContextBlock(channel="team_brief", heading=heading, body=brief))
    for dep_id in spec.depends_on:
        st = completed.get(dep_id)
        if st is None or st.phase is not RunPhase.COMPLETED or not (st.content or "").strip():
            continue
        heading = f"上游产物（{dep_id}）"
        body = st.content.strip()
        parts.append(f"## {heading}\n{body}")
        blocks.append(
            ContextBlock(
                channel="dependency",
                heading=heading,
                body=body,
                source_run_id=dep_id,
                fidelity="pass_through",
            )
        )
    return "\n\n".join(parts), blocks


def register_completed_session(
    tool: DelegateTool,
    plan: RunPlan,
    run_id: str,
    state: RunState,
    *,
    author_sessions: dict[str, RunSession] | None = None,
) -> RunSession | None:
    """单个 run 到达终局即登记现场（使同批 depends_on X + continue_from X 成立）。

    验收失败的 run 也保留现场：终局（COMPLETED 或 FAILED）且 transcript 非空即登记并持久化，
    这样 CEO 可用 continue_from 让原作者在失败草稿上改写（否则「找不到可续写现场」）。CANCELLED
    走 redirect/salvage 的 partial session，SKIPPED 从未产出，均不在此登记。

    已登记则跳过（续派 / redirect 自行更新同一 session，避免用冷开局态覆盖延展 transcript）。
    """
    if tool._session_store is None:
        return None
    if state.phase not in (RunPhase.COMPLETED, RunPhase.FAILED) or not state.transcript:
        return None
    node = plan.by_id(run_id)
    if node is None:
        return None
    # 续派节点：现场仍挂在根上（continue_from），不另开 session 键。
    if node.continue_from_run_id:
        return None
    if tool._session_store.get(run_id) is not None:
        return None
    from agentcore.runtime.runs import RunSession

    recall = 0
    if author_sessions is not None and run_id in author_sessions:
        recall = author_sessions[run_id].recall_count
    session = RunSession(
        run_id=run_id,
        spec=node,
        transcript=state.transcript,
        content=state.content,
        recall_count=recall,
    )
    tool._session_store.put(session)
    if author_sessions is not None:
        author_sessions[run_id] = session
    return session
