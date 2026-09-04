"""Moderator —— 主持人辩论循环（辩论编排设计.md §二 支点）。

主持人是「主持 + 裁判 + 书记」三合一的有状态编排角色，不是独立执行引擎：每轮循环四步——

1. **定本轮议题**：首轮 :meth:`_frame` 拆用户问题为争议焦点（并产开场白）；后续轮优先采用上轮
   裁判 ``verdict.next_focus``（assess 兼产、真去重），缺失 / 空串时才回退 :meth:`_frame`；用户掌舵
   ``focus_override`` 始终最高优先。
2. **派各方发言**（注入的 :class:`~agentcore.runtime.debate.types.RoundRunner`）：一波并行辩手，
   底层复用 ``build_agent_executor`` / ``continue_run``（辩手跨轮带记忆）——本类不关心怎么派。
3. **裁判 + 写小结**（:meth:`_judge_and_summarize`）：一次结构化调用同时产出交锋质量与收敛判定
   （真交锋？还在产生新论点？可收场？）、本轮小结，以及未收敛时的 ``next_focus``——同读本轮发言，
   合并去掉冗余 round-trip（辩论编排设计.md §二：真去重、非节流补丁）。
4. **决策下一步**（:meth:`run` 循环体）：裁判判收敛 → 结辩与简报并行收场；否则进下一轮 / 触安全上限兜底。

裁判 / 小结 / 简报 / 定议题都走 ``provider.complete`` 出结构化 JSON + 坏 JSON 容错（借鉴
``evals/judge.py``）；单测注入返回脚本化 JSON 的 fake provider，零成本验证循环 / 收敛 / 双产物。

实现按职责拆到同包子模块（纯结构，公开调用面不变）：

- :mod:`moderator_common` —— 截断 / JSON 容错 / prompt 块
- :mod:`moderator_agenda` —— 定议题 / 质询 / 结辩门槛
- :mod:`moderator_judge` —— 裁判 + 小结 + 记分
- :mod:`moderator_brief` —— 收场简报
- :mod:`moderator_timeline` —— 判定 complete → 既有 run delta

→ 见设计: docs/03-AI核心/辩论编排设计.md §二、§四、§五
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from agentcore.core.log_context import log_context
from agentcore.core.logging import get_logger
from agentcore.llm.provider.protocol import LLMMessage, LLMProvider, LLMRequest, TokenUsage
from agentcore.runtime.debate.findings import accumulate_findings, derive_gate
from agentcore.runtime.debate.form_profile import form_profile
from agentcore.runtime.debate.moderator_agenda import (
    _CROSS_EXAM_SYSTEM,
    closing_enabled,
    cross_exam_enabled,
    cross_exam_questions,
    frame_round,
)
from agentcore.runtime.debate.moderator_brief import (
    _BRIEF_SYSTEM,
    build_brief,
    degraded_brief,
)
from agentcore.runtime.debate.moderator_common import (
    RoundBoundaryHook,
    RoundHook,
    RoundStartHook,
    _parse_json_object,
)
from agentcore.runtime.debate.moderator_judge import _ASSESS_SYSTEM, judge_and_summarize
from agentcore.runtime.debate.moderator_phases import (
    frame_subtopics,
    run_red_team_round,
    run_roundtable_round,
)
from agentcore.runtime.debate.moderator_timeline import emit_moderator_complete
from agentcore.runtime.debate.types import (
    STOP_ALL_FAILED,
    STOP_CONVERGED,
    STOP_MAX_ROUNDS,
    STOP_REASONS,
    STOP_USER_CONCLUDED,
    ClosingRunner,
    ClosingStatement,
    ConsensusMapItem,
    CrossExamExchange,
    CrossExamRunner,
    DebateBrief,
    DebateConfig,
    DebateForm,
    DebateResult,
    Finding,
    JudgeVerdict,
    RoundDecision,
    RoundResult,
    RoundRunner,
    SideTurn,
    ThreadTurn,
    UserInterjection,
    WitnessExamExchange,
    WitnessExamRunner,
    WitnessSeatInfo,
)

if TYPE_CHECKING:
    from agentcore.runtime.events import EventSink

logger = get_logger(__name__)

# 单测契约：test_debate_evidence 从本模块 import 系统 prompt 常量。
__all__ = [
    "Moderator",
    "RoundHook",
    "RoundStartHook",
    "RoundBoundaryHook",
    "_ASSESS_SYSTEM",
    "_BRIEF_SYSTEM",
    "_CROSS_EXAM_SYSTEM",
]


def _settled_closings(raw: Any) -> list[ClosingStatement]:
    """结辩这一半的收场结算：整体抛错 → 无结辩，但简报与叙事线照常交付。

    逐方失败早已在 runner 内降级成 ``ok=False`` 的 :class:`ClosingStatement`；能走到这里
    的是整个 runner 崩了，此时结辩区留空即诚实（``to_ceo_output`` 本就不渲染结辩，前端
    据空列表不出结辩区）。非 ``Exception``（``CancelledError`` 等）照旧传播——整轮停止
    不得被当成结辩失败吞掉。
    """
    if isinstance(raw, BaseException):
        if not isinstance(raw, Exception):
            raise raw
        logger.warning("debate.closing.failed", error=str(raw))
        return []
    return list(raw)


def _settled_brief(
    raw: Any, config: DebateConfig, rounds: Sequence[RoundResult]
) -> DebateBrief:
    """简报这一半的收场结算：抛错 → 诚实降级简报（明说缺了什么），已跑轮次照常交付。"""
    if isinstance(raw, BaseException):
        if not isinstance(raw, Exception):
            raise raw
        logger.warning("debate.brief.failed", error=str(raw), rounds=len(rounds))
        return degraded_brief(config, rounds, reason=str(raw))
    return raw


class Moderator:
    """主持人辩论循环（辩论编排设计.md §二）。

    ``provider`` 注入便于单测（返回脚本化 JSON 的 fake）；``model`` 是裁判 / 小结 / 简报等
    主持人内部 LLM 调用所用模型（DebateTool 传该回合质量档的 strong 档）。``sink`` 为可选
    EventSink：判定 complete 的思考 / 人读产物挂本节点 run_id；缺 sink 或 run_id 静默跳过。
    ``run`` 接收一个 :class:`RoundRunner` 注入「怎么派一轮辩手」，本类只负责编排与判定。
    """

    def __init__(
        self,
        *,
        provider: LLMProvider,
        model: str,
        scenario_prefix: str = "debate",
        run_id: str | None = None,
        parent_run_id: str | None = None,
        sink: EventSink | None = None,
    ) -> None:
        self._llm = provider
        self._model = model
        self._scenario = scenario_prefix
        # 与辩手 executor 同辙：绑定 run 上下文后，unary complete → attribution 头透传到
        # inference proxy，成本明细才能挂到主持人 run_id（否则 proxy 侧 mint 随机 UUID）。
        self._run_id = run_id
        self._parent_run_id = parent_run_id
        # 判定过程挂主持人 run：有 sink + run_id 才发既有 delta；缺一则静默跳过。
        self._sink = sink
        self._round_no = 0
        # 主持人自身 LLM 调用（议题 / 裁判 / 小结 / 简报）的累计用量与轮数，供 DebateTool
        # 折算成主持人节点的一条 ledger 行（与辩手 run 一样计入回合总账）。
        self._usage = TokenUsage()
        self._llm_rounds = 0
        # 结辩 ∥ 简报并发时两侧都可能走 :meth:`_complete_json`，锁住用量累计防丢失更新。
        self._usage_lock = asyncio.Lock()

    @property
    def usage(self) -> TokenUsage:
        """主持人自身 LLM 调用的累计 token 用量（DebateTool 据此计主持人节点账目）。"""
        return self._usage

    @property
    def llm_rounds(self) -> int:
        """主持人发起的 LLM 调用次数（议题 + 裁判 + 小结 + 简报）。"""
        return self._llm_rounds

    async def run(
        self,
        config: DebateConfig,
        *,
        run_round: RoundRunner,
        run_cross_exam: CrossExamRunner | None = None,
        run_witness_exam: WitnessExamRunner | None = None,
        witness_roster: Sequence[WitnessSeatInfo] = (),
        run_closing: ClosingRunner | None = None,
        on_round_start: RoundStartHook | None = None,
        on_round: RoundHook | None = None,
        on_round_boundary: RoundBoundaryHook | None = None,
        evidence_ledger: Any | None = None,
    ) -> DebateResult:
        """驱动整场辩论到收敛 / 上限，返回双产物（决策简报 + 交锋叙事线）。

        收敛【默认完全由裁判逐轮自判】（``verdict.converged`` 即收场）——无最小轮门槛强制多轮。
        「别过早收敛」的智慧在裁判标准里（:meth:`_judge_and_summarize`：第 1 轮开场默认继续、
        除非命题空泛），不再靠外部计数。``policy.max_rounds`` 是纯安全上限（裁判持续不收敛时的
        断路器兜底）。每轮成功产出后触发 ``on_round``（emit 事件 / 老板检查点）。

        ambient 掌舵（辩论编排设计.md §六）：当注入 ``on_round_boundary`` 时，每轮判完 + 小结后
        **非阻塞**捞 steer 队列——``CONTINUE`` 再辩一轮（可带「加角度」焦点覆写）、``CONCLUDE``
        在该边界出结论（即便裁判判收敛也以用户为准；反之用户也可在裁判判收敛时续辩）。钩子返回
        ``None``（空队列）则回退到裁判自动收敛。未接钩子时循环逐字不变（与 ``checkpoint`` marker
        无 hook 即惰性同辙）。``max_rounds`` 始终是硬上限：用户连续 ``CONTINUE`` 也不会越过它。
        钩子**永不 block**——掌舵是 fire-and-forget，conclude 等当前轮跑完再生效。

        质询回合（P1，辩论编排设计.md §4-2.1）：注入 ``run_cross_exam`` 且【认真辩透 + 对抗形态】
        （:meth:`_cross_exam_enabled`）时，每轮立论后插入一个【质询 beat】——主持人据立论生成定向各方
        的必答质询（:meth:`_cross_exam_questions`），被质询方经 runner 用 ``continue_run`` 正面作答，
        问答喂进裁判【记分】（P2，:meth:`_judge_and_summarize`）。未注入 / 快速对碰 / 圆桌时跳过，
        循环逐字回退到「立论→裁判」，零行为变化。

        证人答问（批 D1）：注入 ``run_witness_exam`` 且 ``witness_roster`` 非空、质询档开启时，
        质询 beat 后追加主持人点名判定——仅事实性问题；无可用透镜 session 时不注入，零行为变化。

        结辩收束（P4）：新场 :meth:`_closing_enabled` 恒假，收场后直接出简报。结辩 runner /
        ``closing_task`` 留旧场回放与单测；注入 ``run_closing`` 且闸开时仍与简报 ``asyncio.gather``
        并行（互不依赖）。未注入 / 闸关 / 全员失败时跳过。

        下轮焦点优先级：用户掌舵 ``focus_override`` > 上轮 ``verdict.next_focus`` > :meth:`_frame` 兜底。
        ``_frame`` 保留首轮（开场白 + 首焦点）与 ``next_focus`` 缺失 / 空串时的零风险降级路径。
        """
        rounds: list[RoundResult] = []
        stop_reason = STOP_MAX_ROUNDS  # 循环跑满未 break ⇒ 触上限兜底
        # 主持人开场白（第 1 轮 _frame 顺带产出，全场只取一次）：供前端顶部「会说话的主持人」气泡。
        opening = ""
        # 交互式「加角度」：用户在上一轮边界给的下一轮焦点覆写（空=主持人自动定焦点）。
        focus_override = ""
        # 交互式「追问」：用户在上一轮边界注入、待【本轮】辩手正面回应的问题（消费后清空）。
        # 开赛嘱咐（config.kickoff_ask）预注入为首轮全场定向插话——与中途追问同管道。
        pending_interjections: list[UserInterjection] = []
        kickoff_ask = (config.kickoff_ask or "").strip()
        if kickoff_ask:
            pending_interjections = [UserInterjection(ask=kickoff_ask, target_key="")]
        profile = form_profile(config)
        # 圆桌子题轴（O3 快速档 = 单子题）
        subtopics: list[str] = []
        spoken_keys: set[str] = set()
        if config.form is DebateForm.ROUNDTABLE:
            subtopics = await frame_subtopics(self._complete_json, config)
            if not config.policy.thorough:
                subtopics = subtopics[:1]
        for round_no in range(1, config.policy.max_rounds + 1):
            self._round_no = round_no
            # 焦点优先级：掌舵覆写 > 圆桌子题轴 > 上轮 next_focus > _frame
            if focus_override:
                focus = focus_override
            elif config.form is DebateForm.ROUNDTABLE and subtopics:
                idx = min(round_no - 1, len(subtopics) - 1)
                focus = subtopics[idx]
                if round_no == 1 and not opening:
                    opening = f"本场圆桌将沿子题轴展开：{'；'.join(subtopics)}。"
            else:
                prior_focus = (rounds[-1].verdict.next_focus if rounds else "").strip()
                if prior_focus:
                    focus = prior_focus
                else:
                    focus, framed_opening = await self._frame(config, rounds)
                    # 首轮 _frame 顺带产出开场白（后续轮为空）；全场只认第一句，不被后续覆盖。
                    if framed_opening and not opening:
                        opening = framed_opening
            focus_override = ""
            interjections = pending_interjections
            pending_interjections = []
            # 焦点既定、发言之前先报本轮开场（前端据此亮出焦点头 + 首轮开场白，再流式各方发言）。
            # opening 仅首轮上车（后续轮 ""）；前端 sticky 取第一个非空。
            if on_round_start is not None:
                await on_round_start(round_no, focus, opening if round_no == 1 else "")

            findings: list[Finding] = []
            thread_turns: list[ThreadTurn] = []
            if profile.unit == "finding":
                turns, findings = await run_red_team_round(
                    complete_json=self._complete_json,
                    config=config,
                    profile=profile,
                    run_round=run_round,
                    round_no=round_no,
                    focus=focus,
                    history=rounds,
                    interjections=interjections,
                    prior_findings=accumulate_findings(rounds),
                )
            elif profile.unit == "thread_turn":
                turns, thread_turns, spoken_keys = await run_roundtable_round(
                    complete_json=self._complete_json,
                    config=config,
                    run_round=run_round,
                    round_no=round_no,
                    focus=focus,
                    history=rounds,
                    interjections=interjections,
                    spoken_keys=spoken_keys,
                )
            else:
                turns = list(
                    await run_round(
                        round_no=round_no,
                        focus=focus,
                        sides=config.sides,
                        history=rounds,
                        interjections=interjections,
                    )
                )
            # 追问被本轮承接（无论发言成败，本轮确已带着它跑过）⇒ 标记 answered，随本轮留痕复盘。
            answered = [replace(i, answered=True) for i in interjections]
            if not any(t.ok for t in turns):
                # 全员失败：无可裁判内容，主持人提前终止并出降级简报（别假装辩成了）。
                verdict = JudgeVerdict(
                    real_clash=False,
                    new_arguments=False,
                    converged=True,
                    stop_reason=STOP_ALL_FAILED,
                    rationale="本轮所有辩手均未产出有效发言。",
                )
                rr = RoundResult(
                    round_no,
                    focus,
                    turns,
                    verdict,
                    summary="本轮各方均未产出有效发言，辩论提前终止。",
                    user_interjections=answered,
                    findings=findings,
                    thread_turns=thread_turns,
                )
                rounds.append(rr)
                if on_round is not None:
                    await on_round(rr)
                stop_reason = STOP_ALL_FAILED
                break

            # 缺席轮一等语义：部分失败续赛 → 失败方显式标缺席；跳过对其质询与对抗记分。
            # 红队：方案方 defense 已在 phase 内标 absent；攻击侧失败方此处补标。
            turns = [replace(t, absent=True) if not t.ok else t for t in turns]
            absent_keys = {t.side_key for t in turns if t.absent}
            present_turns = [t for t in turns if t.ok]

            # 质询 beat：仅 DEBATE profile（O1：红队三拍取代通用质询）。
            cross_exam: list[CrossExamExchange] = []
            witness_exam: list[WitnessExamExchange] = []
            if (
                profile.cross_exam
                and run_cross_exam is not None
                and self._cross_exam_enabled(config)
                and present_turns
            ):
                questions = await self._cross_exam_questions(
                    config, focus, present_turns
                )
                # Belt: drop any hallucinated keys targeting absent sides.
                questions = {
                    k: qs for k, qs in questions.items() if k not in absent_keys
                }
                if questions:
                    cross_exam = list(
                        await run_cross_exam(
                            round_no=round_no,
                            focus=focus,
                            sides=config.sides,
                            turns=turns,
                            questions=questions,
                        )
                    )
                # 证人点名（批 D1）：质询档内、有席位时；失败不阻断主流程。
                if run_witness_exam is not None and witness_roster:
                    try:
                        wit_qs = await self._witness_exam_questions(
                            config, focus, present_turns, witness_roster
                        )
                        if wit_qs:
                            witness_exam = list(
                                await run_witness_exam(
                                    round_no=round_no,
                                    focus=focus,
                                    questions=wit_qs,
                                )
                            )
                    except Exception as exc:  # noqa: BLE001
                        from agentcore.core.logging import get_logger

                        get_logger(__name__).warning(
                            "debate.witness.exam_skipped",
                            round_no=round_no,
                            error=str(exc),
                        )
                        witness_exam = []

            # rounds 此刻是【已完成的历史轮】（本轮 rr 尚未 append）——喂给合并裁判作上一轮小结锚点。
            # 裁判判定 + 本轮小结 + 记分读同一份发言（含质询问答），合并成一次结构化调用去冗余（§二）。
            # M2：场级 evidence_ledger 注入记分（tier 锚定）；缺省=旧软约束零回归。
            verdict, summary = await self._judge_and_summarize(
                config,
                focus,
                turns,
                rounds,
                cross_exam=cross_exam,
                evidence_ledger=evidence_ledger,
            )
            if absent_keys:
                # 不做对抗性记分：缺席方不入分；有缺席则本轮清空 scores（无公平对照）。
                verdict.scores = {}
                verdict.clashes = [
                    c
                    for c in verdict.clashes
                    if c.from_key not in absent_keys and c.to_key not in absent_keys
                ]
            # 圆桌：子题轴铺满 → 收敛
            if (
                config.form is DebateForm.ROUNDTABLE
                and subtopics
                and round_no >= len(subtopics)
            ):
                verdict.converged = True
                if not verdict.stop_reason:
                    verdict.stop_reason = STOP_CONVERGED
            rr = RoundResult(
                round_no,
                focus,
                turns,
                verdict,
                summary,
                user_interjections=answered,
                cross_exam=cross_exam,
                witness_exam=witness_exam,
                findings=findings,
                thread_turns=thread_turns,
            )
            rounds.append(rr)
            if on_round is not None:
                await on_round(rr)

            # ambient 掌舵边界：非阻塞捞 steer；有则折进 focus_override / pending_interjections /
            # CONCLUDE。钩子返回 None（空队列）则回退裁判自动收敛。用户选择凌驾裁判——CONCLUDE
            # 即便裁判未收敛也收场，CONTINUE 即便裁判已收敛也续辩（focus 非空则覆写下一轮议题）。
            if on_round_boundary is not None:
                boundary = await on_round_boundary(
                    round_no=round_no,
                    result=rr,
                    converged=verdict.converged,
                    max_rounds=config.policy.max_rounds,
                )
                if boundary is not None:
                    if boundary.decision is RoundDecision.CONCLUDE:
                        stop_reason = STOP_USER_CONCLUDED
                        # 收场仍带追问 ⇒ 无后续轮可答，挂到本轮记为未应答（honest gap，别静默丢）。
                        if boundary.ask:
                            rr.user_interjections.append(
                                UserInterjection(
                                    ask=boundary.ask,
                                    target_key=boundary.ask_target,
                                    answered=False,
                                )
                            )
                        break
                    focus_override = boundary.focus  # CONTINUE：续辩（可带「加角度」焦点）
                    if boundary.ask:  # CONTINUE 带追问 ⇒ 待下一轮承接（消费时翻 answered）。
                        pending_interjections = [
                            UserInterjection(ask=boundary.ask, target_key=boundary.ask_target)
                        ]
                    continue

            if verdict.converged:
                stop_reason = (
                    verdict.stop_reason if verdict.stop_reason in STOP_REASONS else STOP_CONVERGED
                )
                break

        # 用户在轮数上限边界仍追问 CONTINUE 但已无后续轮承接 ⇒ 挂到最后一轮记未应答（别静默丢）。
        if pending_interjections and rounds:
            rounds[-1].user_interjections.extend(pending_interjections)

        # 结辩 beat（P4）：新场闸关；runner 留旧场回放。
        closings: list[ClosingStatement] = []
        do_closing = (
            profile.closing
            and run_closing is not None
            and self._closing_enabled(config)
            and stop_reason != STOP_ALL_FAILED
            and bool(rounds)
        )
        # 收场抗抖（结辩 ∥ 简报）：二者互不依赖，任一抛错都只作废它自己那一半，绝不让
        # 已跑完的 N 轮发言 / 质询 / 裁判 / 小结陪葬——``debate_result`` 是收场的 journal
        # 权威，它不发辩论室就永远停在「进行中」。
        if do_closing:
            assert run_closing is not None  # 收窄 Optional，供类型检查
            closings_raw, brief_raw = await asyncio.gather(
                run_closing(sides=config.sides, rounds=rounds),
                self._brief(config, rounds, evidence_ledger=evidence_ledger),
                return_exceptions=True,
            )
            closings = _settled_closings(closings_raw)
            brief = _settled_brief(brief_raw, config, rounds)
        else:
            try:
                brief = await self._brief(config, rounds, evidence_ledger=evidence_ledger)
            except Exception as exc:  # noqa: BLE001 — 简报抖动不得吞掉已跑完的轮次
                brief = _settled_brief(exc, config, rounds)
        # 红队：台账权威快照 + 门决挂 brief；圆桌：共识地图挂 brief（LLM 简报可再润色）
        if config.form is DebateForm.RED_TEAM:
            ledger = accumulate_findings(rounds)
            gate, must_fix = derive_gate(ledger)
            brief.findings = ledger
            brief.gate = gate
            brief.must_fix = must_fix
            brief.risk_severities = {}  # 退役：新场次恒空
        elif config.form is DebateForm.ROUNDTABLE and not brief.consensus_map:
            brief.consensus_map = [
                ConsensusMapItem(topic=rr.focus, consensus=[], divergences=[], crux="")
                for rr in rounds
                if rr.focus
            ]
        return DebateResult(
            config=config,
            rounds=rounds,
            brief=brief,
            stop_reason=stop_reason,
            opening=opening,
            closings=closings,
            witnesses=list(witness_roster),
            subtopics=subtopics,
        )

    # ── 第1步：定本轮议题 ────────────────────────────────────────────────
    async def _frame(
        self, config: DebateConfig, history: list[RoundResult]
    ) -> tuple[str, str]:
        """定本轮议题焦点；第 1 轮附带一句主持人【开场白】。"""
        return await frame_round(self._complete_json, config, history)

    # ── 第2.5步：质询回合（质询回合 P1，辩论编排设计.md §4-2.1）──────────────
    @staticmethod
    def _cross_exam_enabled(config: DebateConfig) -> bool:
        """质询回合仅在【认真辩透 + 对抗形态】开启。"""
        return cross_exam_enabled(config)

    @staticmethod
    def _closing_enabled(config: DebateConfig) -> bool:
        """结辩收束（P4）仅在【认真辩透 + 对抗形态】开启。"""
        return closing_enabled(config)

    async def _cross_exam_questions(
        self, config: DebateConfig, focus: str, turns: Sequence[SideTurn]
    ) -> dict[str, list[str]]:
        """主持人代表交锋，据本轮立论为【每一方】生成 2–3 个必须正面回答的尖锐质询。"""
        return await cross_exam_questions(self._complete_json, config, focus, turns)

    async def _witness_exam_questions(
        self,
        config: DebateConfig,
        focus: str,
        turns: Sequence[SideTurn],
        roster: Sequence[WitnessSeatInfo],
    ) -> dict[str, list[str]]:
        """主持人判定是否点名证人及事实性问题（批 D1）。"""
        from agentcore.runtime.debate.witness import witness_exam_questions

        labels = {
            w.key: f"{w.name}（{w.origin_caption or w.lens_label or w.key}）"
            for w in roster
        }
        return await witness_exam_questions(
            self._complete_json, config, focus, turns, labels
        )

    # ── 第3+4步：裁判本轮 + 写本轮小结 + 记分（一次结构化调用）─────────────
    async def _judge_and_summarize(
        self,
        config: DebateConfig,
        focus: str,
        turns: Sequence[SideTurn],
        history: list[RoundResult],
        *,
        cross_exam: Sequence[CrossExamExchange] = (),
        evidence_ledger: Any | None = None,
    ) -> tuple[JudgeVerdict, str]:
        """一次 LLM 调用同时产出【裁判判定】与【本轮小结】，返回 ``(verdict, summary)``。"""
        return await judge_and_summarize(
            self._complete_json,
            config,
            focus,
            turns,
            history,
            cross_exam=cross_exam,
            evidence_ledger=evidence_ledger,
        )

    # ── 收场：决策简报（结论产物） ───────────────────────────────────────
    async def _brief(
        self,
        config: DebateConfig,
        rounds: list[RoundResult],
        *,
        evidence_ledger: Any | None = None,
    ) -> DebateBrief:
        return await build_brief(
            self._complete_json, config, rounds, evidence_ledger=evidence_ledger
        )

    async def _complete_json(self, system: str, user: str, step: str) -> dict[str, Any]:
        request = LLMRequest(
            messages=[
                LLMMessage(role="system", content=system),
                LLMMessage(role="user", content=user),
            ],
            model=self._model,
            temperature=0.0,
            stream=False,
            scenario=f"{self._scenario}.{step}",
        )

        async def _call() -> dict[str, Any]:
            response = await self._llm.complete(request)
            async with self._usage_lock:
                self._usage = self._usage + (response.usage or TokenUsage())
                self._llm_rounds += 1
            data = _parse_json_object(response.content or "")
            self._emit_complete_timeline(step, data, response.reasoning_content)
            return data

        if not self._run_id:
            return await _call()
        # cost_role=arena + persona=主持人：与 account_moderator → arena 落账口径对齐，
        # sidecar proxy 保持主模型（不跟 Worker）；proxy cost_calls 与回合 run 聚合按同一 run_id。
        with log_context(
            run_id=self._run_id,
            agent_id=self._run_id,
            cost_role="arena",
            persona="主持人",
            parent_run_id=self._parent_run_id,
        ):
            return await _call()

    def _emit_complete_timeline(
        self, step: str, data: dict[str, Any], reasoning: str | None
    ) -> None:
        """结构化 complete 的思考 + 人读产物挂主持人 run；无 sink / 无 run_id 静默跳过。"""
        if self._sink is None or not self._run_id:
            return
        emit_moderator_complete(
            self._sink,
            run_id=self._run_id,
            step=step,
            data=data,
            reasoning=reasoning,
            round_no=self._round_no,
        )
