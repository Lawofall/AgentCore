"""辩手轮次驱动：首轮并行派工 + 后续轮 continue_run。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, replace
from typing import TYPE_CHECKING

from agentcore.core.logging import get_logger
from agentcore.runtime.costing import ROLE_ARENA
from agentcore.runtime.debate import (
    ClosingStatement,
    CrossExamExchange,
    DebateConfig,
    DebateSide,
    RoundResult,
    SideTurn,
)
from agentcore.runtime.debate.cross_exam_parse import (
    build_cross_exam_exchanges,
    looks_incomplete_cross_exam_answer,
    merge_cx_continuation,
    parse_cross_exam_response,
)
from agentcore.runtime.debate.evidence_ledger import side_cited_ledger_ids
from agentcore.runtime.debate.match_ledger import accumulate_match_ledger
from agentcore.runtime.debate.prompt import (
    closing_context_blocks,
    closing_task,
    cx_answer_feedback,
    cx_completion_brief,
    cx_completion_feedback,
    cx_context_blocks,
    cx_draft_brief,
    debater_task,
    draft_system,
    round_context_blocks,
    round_draft_brief,
    round_feedback,
)
from agentcore.runtime.debate.speech_parse import parse_speech_arguments
from agentcore.runtime.events import batch_metrics as batch_metrics_event

if TYPE_CHECKING:
    from agentcore.tools.builtin.debate.tool import DebateTool

logger = get_logger(__name__)


def failed_turn(side: DebateSide, run_id: str, *, beat: str = "statement") -> SideTurn:
    return SideTurn(side.key, side.name, run_id, "", ok=False, beat=beat)  # type: ignore[arg-type]


def ok_turn(
    side: DebateSide, run_id: str, content: str, *, beat: str = "statement"
) -> SideTurn:
    """成功发言：正文进 content，论点大纲结构化进 arguments（载荷单一源）。"""
    args = [a.to_payload() for a in parse_speech_arguments(content)]
    return SideTurn(
        side.key, side.name, run_id, content, ok=True, arguments=args, beat=beat  # type: ignore[arg-type]
    )


async def _gather_settled(
    coros, *, fallback: tuple, beat: str, round_no: int
) -> list:
    """一波辩手并发跑到底，逐项吞异常 —— 单个 ``continue_run`` 抛错只让【该项】落失败位。

    默认 ``asyncio.gather`` 会让第一个异常直接掀翻整波：同波已跑完的其他方发言随之作废，
    异常再一路冲出 ``Moderator.run``，把前面所有轮次一起带走。这里把异常项换成下游早已
    在处理的「没跑出来」哨兵（``state is None`` → ``failed_turn`` / 空答 / ``ok=False``
    结辩），语义与「网关重试耗尽、该方缺席」逐字一致，不新增降级分支。

    非 ``Exception``（``CancelledError`` / ``KeyboardInterrupt``）照旧传播——整轮停止不得
    被当成某一方失败吞掉。外层取消时先 ``cancel("stop")`` 并 ``shield`` 等到子任务拆完
    （与 WaveScheduler 同口径），让 ``continue_run`` 的 ``finally`` 补上 ``run_cancelled``。
    """
    tasks = [asyncio.ensure_future(c) for c in coros]
    try:
        raw = await asyncio.gather(*tasks, return_exceptions=True)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel("stop")
        if tasks:
            await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))
        raise
    settled: list = []
    for item in raw:
        if isinstance(item, BaseException):
            if not isinstance(item, Exception):
                raise item
            logger.warning(
                "debate.wave.side_crashed",
                beat=beat,
                round_no=round_no,
                error=str(item),
            )
            settled.append(fallback)
        else:
            settled.append(item)
    return settled


def _log_gather_batch(
    event: str,
    *,
    nodes: int,
    max_parallel: int,
    wall_ms: int,
    busy_ms: int,
    completed: int,
    failed: int,
    round_no: int | None = None,
) -> None:
    """Emit a WaveScheduler-shaped batch-health line for gather + semaphore waves.

    Field names mirror ``debate.round1.completed`` (nodes / width / peak / wall_ms /
    busy_ms / avg_parallelism / slot_starved / completed / failed / skipped) so
    later rounds stay comparable without inventing a second schema.
    """
    width = min(nodes, max_parallel) if nodes else 0
    payload: dict = {
        "nodes": nodes,
        "width": width,
        "peak": width,
        "wall_ms": wall_ms,
        "busy_ms": busy_ms,
        "avg_parallelism": round(busy_ms / wall_ms, 2) if wall_ms else 0.0,
        "slot_starved": nodes > max_parallel,
        "completed": completed,
        "failed": failed,
        "skipped": 0,
    }
    if round_no is not None:
        payload["round_no"] = round_no
    logger.info(event, **payload)


def make_round_runner(
    tool: DebateTool, execution_id: str, moderator_run_id: str, config: DebateConfig
):
    async def run_round(
        *,
        round_no,
        focus,
        sides,
        history,
        interjections=(),
        beat: str = "statement",
        materials: str = "",
    ):
        # 形态专属拍（defense/rebuttal/thread/crux）或已有 session 的续轮 → continue 路径；
        # 攻击波 / 正反首轮：对尚无 session 的方走 first_round。
        sides = list(sides)
        need_first = [s for s in sides if s.key not in tool._debater_sessions]
        need_cont = [s for s in sides if s.key in tool._debater_sessions]
        turns: list = []
        if need_first:
            turns.extend(
                await first_round(
                    tool,
                    execution_id,
                    moderator_run_id,
                    config,
                    focus,
                    need_first,
                    interjections,
                    round_no=round_no,
                    beat=beat,
                    materials=materials,
                )
            )
        if need_cont:
            turns.extend(
                await next_round(
                    tool,
                    execution_id,
                    moderator_run_id,
                    config,
                    round_no,
                    focus,
                    need_cont,
                    history,
                    interjections,
                    beat=beat,
                    materials=materials,
                )
            )
        # 保持 sides 声明序
        by_key = {t.side_key: t for t in turns}
        return [by_key[s.key] for s in sides if s.key in by_key]

    return run_round


async def first_round(
    tool: DebateTool,
    execution_id: str,
    moderator_run_id: str,
    config: DebateConfig,
    focus: str,
    sides,
    interjections=(),
    *,
    round_no: int,
    beat: str = "statement",
    materials: str = "",
) -> list[SideTurn]:
    """开场波：build_run_plan 一波并行辩手 → executor → 留人 → 折算 → SideTurn。

    「开场」是**这些方各自的第一次发言**，未必是全场第 1 轮：圆桌第 2/3 轮才被点名的方、
    以及首次发言失败（未留下 session）后在后续轮重来的方，都从这里起跑。故 ``round_no``
    是必填实参（本方本次发言所属的真实轮号）——run_id / 节点 ``round`` 标签 / 辩手 prompt
    的轮号三者同源于它。写死 1 会让第 N 轮的发言顶掉第 1 轮那格（同 run_id 二次
    ``run_started``），协作图上前一轮发言随之消失。
    """
    from agentcore.runtime.runs import (
        BatchMetrics,
        RunPhase,
        RunSession,
        WaveScheduler,
        build_agent_executor,
        build_run_plan,
        resolve_max_parallel,
    )

    sides = list(sides)
    mat_suffix = f"\n\n{materials}" if (materials or "").strip() else ""
    turn_model = tool._profile_set.model
    tasks_raw = [
        debater_task(
            config,
            side,
            idx,
            round_no=round_no,
            focus=focus,
            interjections=interjections,
            turn_model=turn_model,
        )
        for idx, side in enumerate(sides)
    ]
    if mat_suffix:
        for t in tasks_raw:
            t["task"] = (t.get("task") or "") + mat_suffix
            if t.get("draft_brief"):
                t["draft_brief"] = t["draft_brief"] + mat_suffix
    valid_tools = {s.name for s in tool._tools.list_all()}
    plan, errors = build_run_plan(
        tasks_raw,
        valid_tools=valid_tools,
        id_prefix=f"{moderator_run_id}_r{round_no}",
        parent_run_id=moderator_run_id,
        depth=tool._depth + 2,
    )
    if errors or not plan.nodes:
        logger.warning(
            "debate.round1.build_failed", errors=errors, round_no=round_no
        )
        return [
            failed_turn(
                side, _beat_run_id(moderator_run_id, round_no, side.key, beat), beat=beat
            )
            for side in sides
        ]

    # run_id 命名统一：开场波辩手改用语义后缀 `_r{n}_{side.key}`，与后续轮 continue_run
    # 同构。形态专属拍（defense/rebuttal/thread/crux）追加 beat 后缀，避免同轮多拍撞 id。
    # 检索预算：builder 只填全员统一默认；有约定文档残搜 2 由 debater_task 写入 payload，
    # 此处在 apply 之后补写到 RunSpec（CEO/schema 不可配置该字段）。
    from agentcore.runtime.runs.retrieval_budget import parse_retrieval_budget

    patched: list = []
    for side, node, raw in zip(sides, plan.nodes, tasks_raw, strict=False):
        rb = parse_retrieval_budget(raw.get("retrieval_budget"))
        node_run_id = _beat_run_id(moderator_run_id, round_no, side.key, beat)
        kwargs: dict = {"run_id": node_run_id, "agent_id": node_run_id}
        if rb is not None:
            kwargs["retrieval_budget"] = rb
        patched.append(replace(node, **kwargs))
    plan.nodes = patched

    tool._sink.emit(debater_plan_event(tool, execution_id, moderator_run_id, plan))
    # Plan-only eval: opening debater DAG is on the wire; abort before WaveScheduler.
    from agentcore.runtime.plan_only import PlanOnlyAbortError, is_plan_only

    if is_plan_only():
        logger.info(
            "debate.plan_only",
            sides=len(sides),
            nodes=len(plan.nodes),
        )
        raise PlanOnlyAbortError()
    # 上游不预判：有门就往下传，「这次调用该不该弹卡」由 tool_exec 那个唯一收口点按
    # sandbox_approval 判（它拿得到工具名 / 参数 / 会话轴，这里拿不到）。
    worker_gate = tool._approval_gate
    executor = build_agent_executor(
        plan=plan,
        llm=tool._llm,
        tools=tool._tools,
        sink=tool._sink,
        base_tool_context=tool._base_tool_context,
        profile_set=tool._profile_set,
        cost_role=ROLE_ARENA,
        system_prompt=tool._system_prompt,
        user_message=tool._user_message,
        execution_id=execution_id,
        approval_gate=worker_gate,
        # 辩手是对手不是协作团队：不配团队便签墙（否则正反方会经便签互读对方立论、面板还冒出
        # 莫名的「团队便签」）。跨方信息由主持人按轮喂 round_feedback，才是辩论正当的跨方通道。
        collaboration=False,
        evidence_ledger=tool._evidence_ledger,
    )
    scheduler = WaveScheduler(tool._max_parallel or resolve_max_parallel())
    batch_metrics: list[BatchMetrics] = []
    from agentcore.runtime.events import run_skipped

    results = await scheduler.run(
        plan,
        executor,
        on_skipped=lambda rid, aid, reason: tool._sink.emit(
            run_skipped(rid, aid, reason=reason)
        ),
        metrics_sink=batch_metrics,
    )
    if batch_metrics:
        # 调度埋点量化: the debaters fan out as one parallel wave per round — same
        # batch-health read as delegate (avg_parallelism = busy/wall, slot_starved).
        m = batch_metrics[0]
        logger.info(
            "debate.round1.completed",
            round_no=round_no,
            nodes=m.nodes,
            width=m.width,
            peak=m.peak_running,
            wall_ms=m.wall_ms,
            busy_ms=m.busy_ms,
            avg_parallelism=round(m.busy_ms / m.wall_ms, 2) if m.wall_ms else 0.0,
            slot_starved=m.slot_starved,
            completed=m.completed,
            failed=m.failed,
            skipped=m.skipped,
        )
        # 深层诊断指标 (前端UX设计.md §十): also hand the scheduler snapshot to the client so
        # 诊断模式 shows the debaters' fan-out in run detail (journaled → replays on reload),
        # mirroring the delegate drive path. Whole-batch verbatim; the host already logged it.
        tool._sink.emit(batch_metrics_event(execution_id=execution_id, metrics=asdict(m)))

    turns: list[SideTurn] = []
    for side, node in zip(sides, plan.nodes, strict=False):
        state = results.get(node.run_id)
        if state is not None:
            tool._acc.add_run(
                node, state, parent_run_id=moderator_run_id, role=ROLE_ARENA
            )
        if state and state.phase is RunPhase.COMPLETED and state.content.strip():
            tool._debater_sessions[side.key] = RunSession(
                run_id=node.run_id,
                spec=node,
                transcript=state.transcript,
                content=state.content,
            )
            turns.append(ok_turn(side, node.run_id, state.content, beat=beat))
        else:
            turns.append(failed_turn(side, node.run_id, beat=beat))
    return turns


def _beat_run_id(moderator_run_id: str, round_no: int, side_key: str, beat: str) -> str:
    """同轮多拍时追加 beat 后缀，避免撞 run_id；正反 statement / 红队首攻不加后缀。"""
    base = f"{moderator_run_id}_r{round_no}_{side_key}"
    if beat in ("statement", "attack", ""):
        return base
    return f"{base}_{beat}"


def _phase_context_channel(beat: str) -> str:
    """形态专属拍 → ContextChannel（协作图 / 详情 beat 折叠）。"""
    if beat in ("attack", "defense", "rebuttal", "thread", "crux", "cross_exam", "closing"):
        return beat
    return "round_focus"


async def next_round(
    tool: DebateTool,
    execution_id: str,
    moderator_run_id: str,
    config: DebateConfig,
    round_no: int,
    focus: str,
    sides,
    history,
    interjections=(),
    *,
    beat: str = "statement",
    materials: str = "",
) -> list[SideTurn]:
    """后续轮：各辩手【并行】continue_run 续写（注入对方上轮论点），收齐后按序留人 + 折算。

    与首轮一致地并发派各方（受 ``max_parallel`` 约束）：各方续写各自独立 session、本轮
    feedback 只取上一轮对方论点、互不依赖，故可并发——根治旧法「后续轮逐个 await，墙钟随
    方数线性叠加」。账目 / 留人 / SideTurn 在 gather 收齐后按 ``sides`` 顺序串行回写，与
    串行版的落账次序完全一致（并发只发生在 LLM 调用本身，不碰共享态）。
    """
    from agentcore.runtime.runs import RunPhase, continue_run, resolve_max_parallel

    sides = list(sides)
    last_round: RoundResult = history[-1] if history else None
    match_ledger = accumulate_match_ledger(history) if history else []
    # 上游不预判：有门就往下传，「这次调用该不该弹卡」由 tool_exec 那个唯一收口点按
    # sandbox_approval 判（它拿得到工具名 / 参数 / 会话轴，这里拿不到）。
    worker_gate = tool._approval_gate
    max_parallel = tool._max_parallel or resolve_max_parallel()
    semaphore = asyncio.Semaphore(max_parallel)
    mat = (materials or "").strip()
    draft_beat = (
        "continue"
        if beat in ("statement", "attack", "defense", "rebuttal", "thread")
        else beat
        if beat in ("cross_exam", "closing", "crux")
        else "continue"
    )

    async def _continue_side(side: DebateSide):
        session = tool._debater_sessions.get(side.key)
        if session is None:
            return None, 0
        revision_run_id = _beat_run_id(moderator_run_id, round_no, side.key, beat)
        if last_round is not None and beat in ("statement", "attack", ""):
            research_fb = round_feedback(
                config,
                side,
                round_no,
                focus,
                last_round,
                interjections,
                match_ledger=match_ledger,
                history=history,
            )
            speech_brief = round_draft_brief(
                config,
                side,
                round_no,
                focus,
                last_round,
                interjections,
                match_ledger=match_ledger,
                history=history,
            )
            context_blocks = round_context_blocks(
                config, side, round_no, focus, last_round, speech_brief, interjections
            )
        else:
            # 形态专属拍：材料注入为主（finding 清单 / 线程全文 / crux 问）
            research_fb = (
                f"本轮焦点：{focus}\n{mat}\n请按本拍职责发言。"
                if mat
                else f"本轮焦点：{focus}\n请按本拍职责发言。"
            )
            speech_brief = research_fb
            from agentcore.runtime.runs.types import ContextBlock

            context_blocks = [
                ContextBlock(
                    channel="task",
                    heading=f"第 {round_no} 轮·{beat}",
                    body=speech_brief,
                ),
                ContextBlock(
                    channel=_phase_context_channel(beat),  # type: ignore[arg-type]
                    heading=beat,
                    body=mat or focus,
                ),
            ]
        if mat and beat in ("statement", "attack", ""):
            research_fb = f"{research_fb}\n\n{mat}"
            speech_brief = f"{speech_brief}\n\n{mat}"
        async with semaphore:
            # occupancy = slot acquire → finish（对齐 WaveScheduler busy_ms，不含排队等待）
            t0 = time.monotonic()
            state = await continue_run(
                session=session,
                feedback=research_fb,
                continuation_run_id=revision_run_id,
                llm=tool._llm,
                tools=tool._tools,
                sink=tool._sink,
                base_tool_context=tool._base_tool_context,
                execution_id=execution_id,
                profile_set=tool._profile_set,
                cost_role=ROLE_ARENA,
                approval_gate=worker_gate,
                # 单一轮次投影: carry this side's TRUE round onto the continuation's run_started
                # (辩论逐轮), so every fold reads 第几轮 from the wire, not a version number.
                round_no=round_no,
                side_key=side.key,
                context_blocks=context_blocks,
                parent_run_id=moderator_run_id,
                draft_brief=speech_brief,
                draft_system=draft_system(
                    config, side, beat="continue" if draft_beat == "crux" else draft_beat  # type: ignore[arg-type]
                ),
                allow_research=True,
                evidence_ledger=tool._evidence_ledger,
                check_evidence_ledger=True,
            )
            return state, int((time.monotonic() - t0) * 1000)

    wall_start = time.monotonic()
    pairs = await _gather_settled(
        (_continue_side(side) for side in sides),
        fallback=(None, 0),
        beat=beat,
        round_no=round_no,
    )
    wall_ms = int((time.monotonic() - wall_start) * 1000)
    states = [state for state, _elapsed in pairs]
    busy_ms = sum(elapsed for _state, elapsed in pairs)

    turns: list[SideTurn] = []
    for side, state in zip(sides, states, strict=False):
        session = tool._debater_sessions.get(side.key)
        revision_run_id = _beat_run_id(moderator_run_id, round_no, side.key, beat)
        if session is None or state is None:
            turns.append(failed_turn(side, revision_run_id, beat=beat))
            continue
        rev_spec = replace(session.spec, run_id=revision_run_id, agent_id=revision_run_id)
        tool._acc.add_run(
            rev_spec, state, parent_run_id=moderator_run_id, role=ROLE_ARENA
        )
        if state.phase is RunPhase.COMPLETED and state.content.strip():
            # 续写成功：把延展后的 transcript 提交回 session，供下一轮再续写。
            session.transcript = state.transcript
            session.content = state.content
            session.recall_count += 1
            turns.append(ok_turn(side, revision_run_id, state.content, beat=beat))
        else:
            turns.append(failed_turn(side, revision_run_id, beat=beat))
    _log_gather_batch(
        f"debate.round{round_no}.completed",
        round_no=round_no,
        nodes=len(sides),
        max_parallel=max_parallel,
        wall_ms=wall_ms,
        busy_ms=busy_ms,
        completed=sum(1 for t in turns if t.ok),
        failed=sum(1 for t in turns if not t.ok),
    )
    return turns


def make_cross_exam_runner(
    tool: DebateTool, execution_id: str, moderator_run_id: str, config: DebateConfig
):
    """质询回合（P1）的 :class:`~agentcore.runtime.debate.CrossExamRunner` 实现工厂。

    主持人已据本轮立论生成【定向各方的必答质询】（``questions``: side_key → 问题列表），本 runner 让每个
    被质询方用 ``continue_run`` 在【自己的 transcript】上正面作答（受 ``max_parallel`` 并发约束）：答复
    进入该方 session 记忆（下一轮立论续写可见）、折算进回合账目，返回各方 :class:`CrossExamExchange`
    （问答对喂回主持人裁判记分）。仅在主持人判定开启质询（认真辩透 + 对抗形态）时被调，与 :func:`next_round`
    共用同一批辩手 session。"""

    async def run_cross_exam(*, round_no, focus, sides, turns, questions):  # noqa: ANN001, ARG001
        from agentcore.runtime.runs import RunPhase, continue_run, resolve_max_parallel

        sides_by_key = {s.key: s for s in sides}
        # 上游不预判（同 next_round）：弹不弹卡交给 tool_exec 收口点。
        worker_gate = tool._approval_gate
        max_parallel = tool._max_parallel or resolve_max_parallel()
        semaphore = asyncio.Semaphore(max_parallel)
        # 只质询「首轮已成功立论（有 session）+ 主持人给了问题」的方；顺序固定为 sides 声明序，账目 /
        # 留人回写次序一致（并发只发生在 continue_run 本身，不碰共享态，与 next_round 同辙）。
        targets = [
            (s.key, list(questions[s.key]))
            for s in sides
            if s.key in questions and questions[s.key] and s.key in tool._debater_sessions
        ]

        async def _answer(side_key: str, qs: list[str]):
            session = tool._debater_sessions.get(side_key)
            side = sides_by_key.get(side_key)
            if session is None or side is None:
                return None, None, 0
            cx_run_id = f"{moderator_run_id}_r{round_no}_cx_{side_key}"
            research_fb = cx_answer_feedback(config, side, round_no, focus, qs)
            speech_brief = cx_draft_brief(config, side, round_no, focus, qs)
            # 收到的上下文：task 块展示成稿 brief；cross_exam 清单块保留（beat presence）。
            context_blocks = cx_context_blocks(round_no, qs, speech_brief)
            async with semaphore:
                t0 = time.monotonic()
                state = await continue_run(
                    session=session,
                    feedback=research_fb,
                    continuation_run_id=cx_run_id,
                    llm=tool._llm,
                    tools=tool._tools,
                    sink=tool._sink,
                    base_tool_context=tool._base_tool_context,
                    execution_id=execution_id,
                    profile_set=tool._profile_set,
                    cost_role=ROLE_ARENA,
                    approval_gate=worker_gate,
                    round_no=round_no,
                    side_key=side_key,
                    context_blocks=context_blocks,
                    parent_run_id=moderator_run_id,
                    draft_brief=speech_brief,
                    draft_system=draft_system(config, side, beat="cross_exam"),
                    allow_research=True,
                    evidence_ledger=tool._evidence_ledger,
                    check_evidence_ledger=True,
                )
                repair_state = None
                # 生成端停写悬垂（冒号 / 未闭合列表）：装配前自动续写补全一次（禁再检索）。
                if (
                    state is not None
                    and state.phase is RunPhase.COMPLETED
                    and looks_incomplete_cross_exam_answer(state.content)
                ):
                    session.transcript = state.transcript
                    session.content = state.content
                    complete_fb = cx_completion_feedback(qs, state.content)
                    complete_brief = cx_completion_brief(qs, state.content)
                    complete_run_id = f"{cx_run_id}_complete"
                    # 补全文本随后并入正式答复、并进结辩允许集 —— 它必须过与主答同一道
                    # 证据台账 id 闸，否则未绑定的 #eN 从这条「续写」溜进正文，还会在结辩
                    # 里被当成合法引用。补全禁检索（不产新笔记），故允许集 = 本方 transcript
                    # 里已引用过的 id（与结辩闸同基准）。
                    prior_cited = side_cited_ledger_ids(
                        (), side_key, transcript=session.transcript
                    )
                    cont_state = await continue_run(
                        session=session,
                        feedback=complete_fb,
                        continuation_run_id=complete_run_id,
                        llm=tool._llm,
                        tools=tool._tools,
                        sink=tool._sink,
                        base_tool_context=tool._base_tool_context,
                        execution_id=execution_id,
                        profile_set=tool._profile_set,
                        cost_role=ROLE_ARENA,
                        approval_gate=worker_gate,
                        round_no=round_no,
                        side_key=side_key,
                        context_blocks=cx_context_blocks(round_no, qs, complete_brief),
                        parent_run_id=moderator_run_id,
                        draft_brief=complete_brief,
                        draft_system=draft_system(config, side, beat="cross_exam"),
                        allow_research=False,
                        evidence_ledger=tool._evidence_ledger,
                        check_evidence_ledger=True,
                        allowed_ledger_ids=prior_cited,
                    )
                    if (
                        cont_state is not None
                        and cont_state.phase is RunPhase.COMPLETED
                        and cont_state.content.strip()
                    ):
                        prior_len = len(state.content)
                        merged = merge_cx_continuation(state.content, cont_state.content)
                        state = replace(
                            state, content=merged, transcript=cont_state.transcript
                        )
                        repair_state = (complete_run_id, cont_state)
                        logger.info(
                            "debate.cross_exam.completed_after_repair",
                            side_key=side_key,
                            round_no=round_no,
                            prior_len=prior_len,
                            merged_len=len(merged),
                        )
                    else:
                        logger.info(
                            "debate.cross_exam.repair_skipped",
                            side_key=side_key,
                            round_no=round_no,
                        )
                return state, repair_state, int((time.monotonic() - t0) * 1000)

        wall_start = time.monotonic()
        triples = await _gather_settled(
            (_answer(k, qs) for k, qs in targets),
            fallback=(None, None, 0),
            beat="cross_exam",
            round_no=round_no,
        )
        wall_ms = int((time.monotonic() - wall_start) * 1000)
        busy_ms = sum(elapsed for _state, _repair, elapsed in triples)

        exchanges: list[CrossExamExchange] = []
        completed = 0
        failed = 0
        for (side_key, qs), (state, repair_state, _elapsed) in zip(
            targets, triples, strict=False
        ):
            cx_run_id = f"{moderator_run_id}_r{round_no}_cx_{side_key}"
            session = tool._debater_sessions.get(side_key)
            if session is None or state is None:
                failed += 1
                exchanges.append(
                    CrossExamExchange(
                        target=side_key,
                        exchanges=build_cross_exam_exchanges(qs, ""),
                        answer_run_id=cx_run_id,
                    )
                )
                continue
            rev_spec = replace(session.spec, run_id=cx_run_id, agent_id=cx_run_id)
            tool._acc.add_run(
                rev_spec, state, parent_run_id=moderator_run_id, role=ROLE_ARENA
            )
            if repair_state is not None:
                repair_run_id, repair_run_state = repair_state
                repair_spec = replace(
                    session.spec, run_id=repair_run_id, agent_id=repair_run_id
                )
                tool._acc.add_run(
                    repair_spec, repair_run_state, parent_run_id=moderator_run_id, role=ROLE_ARENA
                )
            if state.phase is RunPhase.COMPLETED and state.content.strip():
                # 作答成功：延展后的 transcript 提交回 session，下一轮立论续写在其之上（带质询记忆）。
                session.transcript = state.transcript
                session.content = state.content
                session.recall_count += 1
                qa_pairs = parse_cross_exam_response(
                    qs, state.content, side_key=side_key
                )
                completed += 1
                exchanges.append(
                    CrossExamExchange(
                        target=side_key,
                        exchanges=qa_pairs,
                        answer_run_id=cx_run_id,
                    )
                )
            else:
                failed += 1
                exchanges.append(
                    CrossExamExchange(
                        target=side_key,
                        exchanges=build_cross_exam_exchanges(qs, ""),
                        answer_run_id=cx_run_id,
                    )
                )
        _log_gather_batch(
            "debate.cross_exam.completed",
            round_no=round_no,
            nodes=len(targets),
            max_parallel=max_parallel,
            wall_ms=wall_ms,
            busy_ms=busy_ms,
            completed=completed,
            failed=failed,
        )
        return exchanges

    return run_cross_exam


def make_closing_runner(
    tool: DebateTool, execution_id: str, moderator_run_id: str, config: DebateConfig
):
    """结辩收束（阶段化发言角色 P4）的 :class:`~agentcore.runtime.debate.ClosingRunner` 实现工厂。

    辩论收场后主持人请各方做结辩：本 runner 让每个仍有 session 的方用 ``continue_run`` 走【干净
    成稿】（``allow_research=False``），brief 携带本场材料（历轮论点 / 质询让步 / clash 命门，见
    :func:`closing_task`），并启用证据台账 id 闸。受 ``max_parallel`` 并发约束，
    折算进账目，返回各方 :class:`ClosingStatement`（全文进该方 run 事件）。对称于
    :func:`make_cross_exam_runner`；未成功立论 / 无 session 的方不参与结辩。仅在主持人判定开启结辩时被调。"""

    async def run_closing(*, sides, rounds):  # noqa: ANN001
        from agentcore.runtime.runs import RunPhase, continue_run, resolve_max_parallel

        sides = list(sides)
        rounds = list(rounds)
        # 上游不预判（同 next_round）：弹不弹卡交给 tool_exec 收口点。
        worker_gate = tool._approval_gate
        max_parallel = tool._max_parallel or resolve_max_parallel()
        semaphore = asyncio.Semaphore(max_parallel)
        # 结辩 run 的逐轮标记沿用末轮号（结辩是收场收束、非新一轮）：让画布把结辩修订挂到该方末轮
        # 修订链尾，前端辩论视图仍按 run_id 直取结辩全文（与轮号解耦）。无轮次（理论不可达，防御）→ 0。
        final_round_no = rounds[-1].round_no if rounds else 0
        # 只让「已成功立论（有 session）」的方结辩，顺序固定为 sides 声明序（账目 / 留人回写次序一致，
        # 并发只发生在 continue_run 本身，与 next_round / cross_exam 同辙）。
        targets = [s for s in sides if s.key in tool._debater_sessions]

        async def _close(side: DebateSide):
            session = tool._debater_sessions.get(side.key)
            if session is None:
                return None, 0
            closing_run_id = f"{moderator_run_id}_closing_{side.key}"
            feedback = closing_task(config, side, rounds)
            # 收到的上下文：task 块 body 逐字复用 feedback；材料孪生块与 brief 同源。
            context_blocks = closing_context_blocks(config, side, feedback, rounds)
            # 结辩无检索：闸基准 = 本方历轮发言 / 质询 / transcript 已引用 #eN 并集。
            prior_cited = side_cited_ledger_ids(
                rounds, side.key, transcript=session.transcript
            )
            async with semaphore:
                t0 = time.monotonic()
                state = await continue_run(
                    session=session,
                    feedback=feedback,
                    continuation_run_id=closing_run_id,
                    llm=tool._llm,
                    tools=tool._tools,
                    sink=tool._sink,
                    base_tool_context=tool._base_tool_context,
                    execution_id=execution_id,
                    profile_set=tool._profile_set,
                    cost_role=ROLE_ARENA,
                    approval_gate=worker_gate,
                    round_no=final_round_no,
                    side_key=side.key,
                    context_blocks=context_blocks,
                    parent_run_id=moderator_run_id,
                    draft_brief=feedback,
                    draft_system=draft_system(config, side, beat="closing"),
                    allow_research=False,
                    evidence_ledger=tool._evidence_ledger,
                    check_evidence_ledger=True,
                    allowed_ledger_ids=prior_cited,
                )
                return state, int((time.monotonic() - t0) * 1000)

        wall_start = time.monotonic()
        pairs = await _gather_settled(
            (_close(s) for s in targets),
            fallback=(None, 0),
            beat="closing",
            round_no=final_round_no,
        )
        wall_ms = int((time.monotonic() - wall_start) * 1000)
        states = [state for state, _elapsed in pairs]
        busy_ms = sum(elapsed for _state, elapsed in pairs)

        closings: list[ClosingStatement] = []
        for side, state in zip(targets, states, strict=False):
            closing_run_id = f"{moderator_run_id}_closing_{side.key}"
            session = tool._debater_sessions.get(side.key)
            if session is None or state is None:
                closings.append(
                    ClosingStatement(side.key, side.name, closing_run_id, ok=False)
                )
                continue
            rev_spec = replace(session.spec, run_id=closing_run_id, agent_id=closing_run_id)
            tool._acc.add_run(
                rev_spec, state, parent_run_id=moderator_run_id, role=ROLE_ARENA
            )
            if state.phase is RunPhase.COMPLETED and state.content.strip():
                # 结辩成功：延展后的 transcript 提交回 session（结辩是本方 transcript 的最后一段）。
                session.transcript = state.transcript
                session.content = state.content
                session.recall_count += 1
                closings.append(
                    ClosingStatement(
                        side.key,
                        side.name,
                        closing_run_id,
                        content=state.content,
                        ok=True,
                    )
                )
            else:
                closings.append(
                    ClosingStatement(side.key, side.name, closing_run_id, ok=False)
                )
        _log_gather_batch(
            "debate.closing.completed",
            round_no=final_round_no,
            nodes=len(targets),
            max_parallel=max_parallel,
            wall_ms=wall_ms,
            busy_ms=busy_ms,
            completed=sum(1 for c in closings if c.ok),
            failed=sum(1 for c in closings if not c.ok),
        )
        return closings

    return run_closing


def debater_plan_event(tool: DebateTool, execution_id: str, moderator_run_id: str, plan):
    """声明本轮辩手节点（parent=主持人）。前端 dedupe 跨轮重复声明。"""
    from agentcore.runtime.debate.events import debate_act_payload, run_payload, side_card

    agents = [side_card(tool, n) for n in plan.nodes]
    runs = [run_payload(n) for n in plan.nodes]
    from agentcore.runtime.events import run_plan

    prev_execution_id = getattr(tool, "_debate_prev_execution_id", None)
    return run_plan(
        execution_id=execution_id,
        plan_type="debate",
        task_summary="",
        agents=agents,
        runs=runs,
        # 同幕补派：与主持人共享本场辩论幕；prev 仅语义回显（同 execution_id）。
        prev_execution_id=prev_execution_id,
        act=debate_act_payload(tool),
    )
