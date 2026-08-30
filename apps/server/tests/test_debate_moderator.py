"""主持人辩论循环自测（辩论编排设计.md §二/§四/§五 · per-PR 零 LLM 硬门禁）。

用**假 provider（脚本化 JSON）+ 假 RoundRunner** 零成本验证主持人循环本身：收敛终止、防过早
收敛最小轮门槛、快速对碰单轮、不收敛跑到安全上限、双产物（决策简报 + 三层叙事线）齐全、辩手
跨轮记忆的 history 输入、裁判坏 JSON 保守容错、全员失败提前终止、收场归因传递、CEO 折算文本与
按形态自适应的呈现顺序。真模型留给 nightly。
"""

import asyncio
import json

from agentcore.llm.provider.protocol import LLMResponse
from agentcore.runtime.debate import (
    STOP_ALL_FAILED,
    STOP_CONVERGED,
    STOP_FOCUS_CLARIFIED,
    STOP_MAX_ROUNDS,
    STOP_USER_CONCLUDED,
    ClosingStatement,
    CrossExamExchange,
    CrossExamQa,
    DebateBrief,
    DebateConfig,
    DebateForm,
    DebateResult,
    DebateSide,
    JudgeVerdict,
    Moderator,
    RoundBoundary,
    RoundDecision,
    RoundPolicy,
    RoundResult,
    RoundScore,
    SideTurn,
    tally_scores,
)

# --- 假 provider / 假 runner -------------------------------------------------

_CONVERGE = {
    "real_clash": True,
    "new_arguments": False,
    "converged": True,
    "stop_reason": "converged",
    "rationale": "各方开始重复",
}
_KEEP_GOING = {
    "real_clash": True,
    "new_arguments": True,
    "converged": False,
    "next_focus": "更深的点",
    "rationale": "仍在产生新论点",
}
# 无【跨轮新论点】但裁判未自判收敛（converged=false）——喂给边际递减断路器（P1）的场景：连续两轮
# 这样即应机械收场，模拟真实 trace 里「裁判逐轮口径漂移、重复轮硬打满」。
_NO_NEW = {
    "real_clash": True,
    "new_arguments": False,
    "converged": False,
    "next_focus": "同一个点再绕",
    "rationale": "开始换措辞复述、无跨轮新论点",
}
_DEFAULT_BRIEF = {
    "crux": "做不做 X 的核心权衡",
    "strongest_points": {"pro": "正方最强论点", "con": "反方最强论点"},
    "value_disputes": ["你更看重速度还是稳妥"],
    "factual_disputes": ["X 的成本到底多少"],
    "leaning": "基于事实反方略稳",
    "confidence": "中（若你更看重速度则正方成立）",
    "recommendation": "先小步验证再决定",
    "open_questions": ["灰度窗口外的政策会不会变"],
}


class _ScriptedLLM:
    """按 scenario 末段（frame/assess/brief）返回脚本化 JSON 的假 provider。

    合并裁判（:meth:`Moderator._judge_and_summarize`）一次调用同产 verdict + 小结，故 ``assess`` 步
    把 ``judge_results`` 的裁判 JSON 叠加一个 ``summary`` 字段一并返回。``judge_results`` 是每轮裁判的
    JSON（用完取最后一个）；``judge_content`` 设了则该步直接返回原始字符串（测坏 JSON 容错）；
    ``brief`` 覆盖默认简报。各步调用计数暴露给断言（``judge_calls`` = 合并裁判调用次数）。
    """

    def __init__(
        self, *, judge_results=None, judge_content=None, brief=None, questions=None
    ):  # noqa: ANN001
        self.judge_results = judge_results or [_KEEP_GOING]
        self.judge_content = judge_content
        self.brief = brief if brief is not None else _DEFAULT_BRIEF
        # 质询回合（P1）：cross_exam 步返回的定向各方质询（side_key → 问题列表）。
        self.questions = (
            questions if questions is not None else {"pro": ["质询正方"], "con": ["质询反方"]}
        )
        self.frame_calls = 0
        self.judge_calls = 0
        self.summary_calls = 0
        self.brief_calls = 0
        self.cross_exam_calls = 0
        # 每次 complete 的 (system, user) prompt，供断言注入内容（如用户追问进了简报 prompt）。
        self.seen: list[tuple[str, str]] = []

    async def complete(self, request):  # noqa: ANN001
        self.seen.append((request.messages[0].content, request.messages[1].content))
        step = request.scenario.rsplit(".", 1)[-1]
        if step == "frame":
            self.frame_calls += 1
            return LLMResponse(content=json.dumps({"focus": f"焦点{self.frame_calls}"}))
        if step == "cross_exam":
            self.cross_exam_calls += 1
            return LLMResponse(content=json.dumps({"questions": self.questions}))
        if step == "assess":
            idx = min(self.judge_calls, len(self.judge_results) - 1)
            self.judge_calls += 1
            if self.judge_content is not None:
                return LLMResponse(content=self.judge_content)
            self.summary_calls += 1
            payload = {**self.judge_results[idx], "summary": f"第{self.summary_calls}轮小结"}
            return LLMResponse(content=json.dumps(payload))
        if step == "brief":
            self.brief_calls += 1
            return LLMResponse(content=json.dumps(self.brief))
        return LLMResponse(content="{}")


class _RecordingCrossExam:
    """假 CrossExamRunner：记录每次被调，返回各方对质询的（成功）作答。

    ``fail`` 模拟作答失败（answer 空）；返回顺序即 ``questions`` 的插入序（与真实实现按 sides 声明序
    一致的近似），供断言 cross_exam 落进 RoundResult 且喂进裁判记分。
    """

    def __init__(self, *, fail=False):  # noqa: ANN001
        self.calls: list[dict] = []
        self.fail = fail

    async def __call__(self, *, round_no, focus, sides, turns, questions):  # noqa: ANN001
        self.calls.append({"round_no": round_no, "focus": focus, "questions": dict(questions)})
        return [
            CrossExamExchange(
                target=key,
                exchanges=[
                    CrossExamQa(
                        question=q,
                        answer="" if self.fail else f"对「{key}」质询「{q}」的正面回答",
                    )
                    for q in qs
                ],
                answer_run_id=f"{key}_cx_r{round_no}",
            )
            for key, qs in questions.items()
        ]


class _RecordingClosing:
    """假 ClosingRunner：记录每次被调（sides / rounds 数），返回各方（成功）结辩陈词。

    ``fail`` 模拟结辩失败（ok=False）；返回顺序即 ``sides`` 声明序（与真实实现一致），供断言 closings
    落进 DebateResult 且随 payload 走。结辩是收场后一次性 beat，故正常只被调 1 次。
    """

    def __init__(self, *, fail=False):  # noqa: ANN001
        self.calls: list[dict] = []
        self.fail = fail

    async def __call__(self, *, sides, rounds):  # noqa: ANN001
        self.calls.append({"sides": [s.key for s in sides], "rounds": len(rounds)})
        return [
            ClosingStatement(
                side_key=s.key,
                side_name=s.name,
                run_id=f"mod_closing_{s.key}",
                content="" if self.fail else f"{s.name}的结辩陈词",
                ok=not self.fail,
            )
            for s in sides
        ]


class _RecordingRunner:
    """假 RoundRunner：记录每次被调的 (round_no, focus, history 长度)，返回各方发言。

    ``history_len`` 序列即「辩手跨轮带记忆」的输入证据：第 k 轮应看到前 k-1 轮。``fail_all``
    模拟某轮全员发言失败（ok=False）。``fail_keys`` 模拟部分缺席（仅这些 side_key 失败）。
    """

    def __init__(self, *, fail_all=False, fail_keys: frozenset[str] | None = None):  # noqa: ANN001
        self.calls = []
        self.fail_all = fail_all
        self.fail_keys = fail_keys or frozenset()

    async def __call__(
        self,
        *,
        round_no,
        focus,
        sides,
        history,
        interjections=(),
        beat="statement",
        materials="",
    ):  # noqa: ANN001
        self.calls.append(
            {
                "round_no": round_no,
                "focus": focus,
                "history_len": len(history),
                "interjections": list(interjections),
                "beat": beat,
                "sides": [s.key for s in sides],
                "materials": materials,
            }
        )
        suffix = "" if beat in ("statement", "attack") else f"_{beat}"
        return [
            SideTurn(
                side_key=s.key,
                side_name=s.name,
                run_id=f"{s.key}_r{round_no}{suffix}",
                content=""
                if self.fail_all or s.key in self.fail_keys
                else f"{s.name}就「{focus}」的第{round_no}轮发言",
                ok=not self.fail_all and s.key not in self.fail_keys,
                beat=beat,
            )
            for s in sides
        ]


def _two_sides():
    return [
        DebateSide(key="pro", name="正方", stance="支持做 X"),
        DebateSide(key="con", name="反方", stance="反对做 X"),
    ]


def _config(*, form=DebateForm.DEBATE, policy=None, sides=None):
    return DebateConfig(
        motion="该不该做 X",
        form=form,
        sides=sides or _two_sides(),
        policy=policy or RoundPolicy(max_rounds=5),
    )


def _run(llm, runner, config):
    return asyncio.run(Moderator(provider=llm, model="m").run(config, run_round=runner))


# --- 收敛 / 轮次治理 ---------------------------------------------------------


def test_for_form_thorough_false_is_single_round_for_all_forms():
    """thorough=False 对所有形态（含圆桌）都降为快速单轮（max=1）——「测试/简单看看」不被强制多轮。

    回归：旧实现圆桌恒多轮、忽略 thorough，trivial 命题也跑满多轮、产出冗余「修订 v2」。
    thorough=True 时形态默认仅【安全上限】各异（圆桌 4、正反/红队 5）；轮数由主持人逐轮自判收敛。
    """
    for form in (DebateForm.DEBATE, DebateForm.RED_TEAM, DebateForm.ROUNDTABLE):
        quick = RoundPolicy.for_form(form, thorough=False)
        assert (quick.thorough, quick.max_rounds) == (False, 1), form
    assert RoundPolicy.for_form(DebateForm.ROUNDTABLE).max_rounds == 4
    assert RoundPolicy.for_form(DebateForm.DEBATE).max_rounds == 5
    assert RoundPolicy.for_form(DebateForm.DEBATE).thorough is True


def test_node_summary_is_rounds_and_stop_label():
    """主持人节点预览 = 「N 轮 · 收敛归因」（复用 stop_reason 词表），取代旧的 brief.crux 近空预览。"""

    def _result(rounds_n: int, stop_reason: str) -> DebateResult:
        verdict = JudgeVerdict(real_clash=True, new_arguments=False, converged=True)
        rounds = [
            RoundResult(i, f"焦点{i}", [], verdict, summary=f"小结{i}")
            for i in range(1, rounds_n + 1)
        ]
        return DebateResult(
            config=_config(),
            rounds=rounds,
            brief=DebateBrief(crux="争议焦点"),
            stop_reason=stop_reason,
        )

    assert _result(2, STOP_CONVERGED).node_summary == "2 轮 · 已收敛"
    assert _result(3, STOP_FOCUS_CLARIFIED).node_summary == "3 轮 · 焦点已澄清为价值之争"
    assert _result(5, STOP_MAX_ROUNDS).node_summary == "5 轮 · 达轮数上限"


def test_frame_followup_injects_covered_focuses_and_drills_crux():
    """后续轮定焦点：注入【全部历史轮】已谈焦点清单（防「换汤不换药」）+ 指令钻决定性分歧、不巡游新维度。

    回归重设计（收敛北极星）：旧「每轮必须换一个正交新维度」把决策辩论推成维度巡游、几乎必然撞满
    上限，已换成「钻真正决定结论的那个分歧、往深里逼」；已谈焦点清单仍在（禁重谈），但不再要「正交换维度」。
    """
    captured: list = []

    class _CaptureLLM:
        async def complete(self, request):  # noqa: ANN001
            captured.append(request)
            return LLMResponse(content=json.dumps({"focus": "新焦点"}))

    mod = Moderator(provider=_CaptureLLM(), model="m")
    verdict = JudgeVerdict(real_clash=True, new_arguments=True, converged=False)
    history = [
        RoundResult(1, "焦点甲", [], verdict, summary="一轮小结"),
        RoundResult(2, "焦点乙", [], verdict, summary="二轮小结"),
    ]
    focus, opening = asyncio.run(mod._frame(_config(), history))

    assert focus == "新焦点"
    # 后续轮不产开场白（opening 只首轮索取）：换轮点题由前端模板承担，此处恒空。
    assert opening == ""
    prompt = captured[-1].messages[-1].content
    # 已谈焦点清单仍注入（防「换汤不换药」重谈）；指令从「换正交新维度」改成「钻决定性分歧、往深里逼」。
    assert "已谈过的焦点" in prompt and "往深里逼" in prompt
    assert "正交" not in prompt
    # 全部历史轮的焦点都在清单里（非仅上一轮），主持人才能据整场已谈决定往深钻还是推进到下一个点。
    assert "焦点甲" in prompt and "焦点乙" in prompt


def test_judge_gate_hint_round1_continue_and_quick_converge():
    """「别过早收敛」内化进裁判标准：多轮模式第 1 轮默认继续（除非命题空泛）；快速单轮则一次即收。"""
    captured: list = []

    class _CaptureLLM:
        async def complete(self, request):  # noqa: ANN001
            captured.append(request)
            return LLMResponse(content=json.dumps(_KEEP_GOING))

    mod = Moderator(provider=_CaptureLLM(), model="m")
    turns = [
        SideTurn("pro", "正方", "r1_pro", "正方开场"),
        SideTurn("con", "反方", "r1_con", "反方开场"),
    ]
    # 多轮模式第 1 轮：默认继续、仅命题空泛才收（楼层智慧搬进了 prompt）。
    asyncio.run(mod._judge_and_summarize(_config(policy=RoundPolicy(max_rounds=5)), "焦点", turns, []))
    multi = captured[-1].messages[-1].content
    assert "第 1 轮" in multi and "默认" in multi and "继续" in multi and "空泛" in multi
    # 快速单轮（max=1）：一次对碰即判收敛（避免错误兜底成 达轮数上限）。
    asyncio.run(mod._judge_and_summarize(_config(policy=RoundPolicy.quick()), "焦点", turns, []))
    assert "快速单轮" in captured[-1].messages[-1].content


def test_judge_thorough_gate_treats_value_dispute_as_stop_signal():
    """回归（收敛北极星）：thorough 档裁判标准把「价值之争见底」当收场信号，不再「有未决交锋就别收」。

    旧 gate（『仍有未决的关键交锋时不要轻易收敛』）与终止条件 2『分歧归结为价值之争即收』自相矛盾，
    价值承重的题每场撞满上限。钉死新口径：thorough 提示出现「价值之争见底」+「不是继续信号」。
    """
    captured: list = []

    class _CaptureLLM:
        async def complete(self, request):  # noqa: ANN001
            captured.append(request)
            return LLMResponse(content=json.dumps(_KEEP_GOING))

    mod = Moderator(provider=_CaptureLLM(), model="m")
    turns = [
        SideTurn("pro", "正方", "r2_pro", "正方续论"),
        SideTurn("con", "反方", "r2_con", "反方续论"),
    ]
    history = [RoundResult(1, "焦点", turns, JudgeVerdict(True, True, False), summary="一轮小结")]
    # 第 2 轮 thorough：gate 把「价值之争见底」列为收场信号（旧口径把它当继续信号 → 每场撞满上限）。
    asyncio.run(
        mod._judge_and_summarize(_config(policy=RoundPolicy(max_rounds=5)), "焦点", turns, history)
    )
    prompt = captured[-1].messages[-1].content
    assert "价值之争见底" in prompt and "不是继续信号" in prompt


def test_judge_converged_stops_immediately_no_floor():
    """裁判第 1 轮即判收敛 → 立即收场（无最小轮门槛强制多轮）——收敛治理交给裁判逐轮自判。

    回归：旧实现有机械楼层（min_rounds），裁判首轮判收敛也被逼跑满 N 轮。现在拆掉楼层，
    「别过早收敛」内化进裁判标准（第 1 轮默认继续，由 _judge prompt 注入，假 LLM 不受其约束）。
    """
    llm = _ScriptedLLM(judge_results=[_CONVERGE])
    runner = _RecordingRunner()
    result = _run(llm, runner, _config(policy=RoundPolicy(max_rounds=5)))

    assert len(result.rounds) == 1
    assert len(runner.calls) == 1
    assert result.stop_reason == STOP_CONVERGED


def test_quick_single_round():
    """快速对碰（max=1）：第 1 轮收敛即收场。"""
    llm = _ScriptedLLM(judge_results=[_CONVERGE])
    runner = _RecordingRunner()
    result = _run(llm, runner, _config(policy=RoundPolicy.quick()))

    assert len(result.rounds) == 1
    assert result.stop_reason == STOP_CONVERGED


def test_runs_to_max_when_never_converges():
    """裁判持续不收敛 → 跑到安全上限兜底停，归因 max_rounds。"""
    llm = _ScriptedLLM(judge_results=[_KEEP_GOING])
    runner = _RecordingRunner()
    result = _run(llm, runner, _config(policy=RoundPolicy(max_rounds=4)))

    assert len(result.rounds) == 4
    assert result.stop_reason == STOP_MAX_ROUNDS


def test_diminishing_returns_converges_after_two_stale_rounds():
    """边际递减断路器（P1）：连续两轮判不出【跨轮新论点】→ 即便裁判仍 converged=false 也机械收场。

    治真实 trace「3–4 轮基本复述却硬打满 max_rounds」：裁判逐轮口径可能漂移（new_arguments=false
    却 converged=false），这里用已有 new_arguments 信号兜一个确定性下限（落 STOP_CONVERGED 定义：
    「各方无实质新论点·开始重复」），与 max_rounds 硬上限同属断路器但更早、更省。
    """
    llm = _ScriptedLLM(judge_results=[_NO_NEW])  # 每轮都「无跨轮新论点、未自判收敛」
    runner = _RecordingRunner()
    result = _run(llm, runner, _config(policy=RoundPolicy(max_rounds=5)))

    # 第 1 轮 history 空 → 不触发；第 2 轮 history[-1] 也 new=false → 断路器判收敛，第 2 轮即停。
    assert len(result.rounds) == 2
    assert len(runner.calls) == 2
    assert result.stop_reason == STOP_CONVERGED
    # 机械改写落进本轮 verdict（前端「为何收场」/ journal 据此显示已收敛，而非达上限）。
    assert result.rounds[-1].verdict.converged is True


def test_single_stale_round_does_not_trigger_diminishing_returns():
    """单独一轮无新论点【不】触发断路器——须【连续两轮】才收，避免一次波动就过早收场。"""
    # 轮次 new_arguments：真→假→真→真（无两轮连续 stale）→ 断路器不触发，跑满安全上限。
    llm = _ScriptedLLM(judge_results=[_KEEP_GOING, _NO_NEW, _KEEP_GOING, _KEEP_GOING])
    runner = _RecordingRunner()
    result = _run(llm, runner, _config(policy=RoundPolicy(max_rounds=4)))

    assert len(result.rounds) == 4
    assert result.stop_reason == STOP_MAX_ROUNDS


def test_converged_stop_reason_propagates():
    """裁判给的终止归因（focus_clarified）透传到结果（§五 终止条件）。"""
    verdict = {**_CONVERGE, "stop_reason": "focus_clarified"}
    llm = _ScriptedLLM(judge_results=[verdict])
    runner = _RecordingRunner()
    result = _run(llm, runner, _config(policy=RoundPolicy(max_rounds=5)))

    assert len(result.rounds) == 1
    assert result.stop_reason == STOP_FOCUS_CLARIFIED


def test_judge_normalizes_stop_reason_to_converged_invariant():
    """回归：``stop_reason`` 仅在收敛时有意义，_judge_and_summarize 须据此归一，不逐字透传 LLM 误填。

    真实 trace 里第 1 轮 ``converged=false`` 却带 ``stop_reason=focus_clarified``，随本轮
    verdict 流进 journal / 前端（口径错位）。根因＝裁判逐字透传 LLM 的 stop_reason。这里
    双向钉死：① 未收敛 → stop_reason 恒空；② 收敛但取值非法 → 回落 STOP_CONVERGED。
    """
    turns = [
        SideTurn("pro", "正方", "r1_pro", "正方开场"),
        SideTurn("con", "反方", "r1_con", "反方开场"),
    ]

    class _ScriptedJudge:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        async def complete(self, request):  # noqa: ANN001
            return LLMResponse(content=json.dumps(self._payload))

    # ① 未收敛却误填 stop_reason → 必须被丢弃（留空）。
    stray = {**_KEEP_GOING, "stop_reason": "focus_clarified"}
    mod = Moderator(provider=_ScriptedJudge(stray), model="m")
    v1, _ = asyncio.run(
        mod._judge_and_summarize(_config(policy=RoundPolicy(max_rounds=5)), "焦点", turns, [])
    )
    assert v1.converged is False
    assert v1.stop_reason == ""

    # ② 收敛但 stop_reason 非法 → 回落 STOP_CONVERGED（与循环层同口径）。
    bad = {**_CONVERGE, "stop_reason": "not_a_real_reason"}
    mod2 = Moderator(provider=_ScriptedJudge(bad), model="m")
    v2, _ = asyncio.run(
        mod2._judge_and_summarize(_config(policy=RoundPolicy(max_rounds=5)), "焦点", turns, [])
    )
    assert v2.converged is True
    assert v2.stop_reason == STOP_CONVERGED


# --- 跨轮记忆 / 容错 / 失败 --------------------------------------------------


def test_round_runner_receives_growing_history():
    """第 k 轮 run_round 应看到前 k-1 轮 —— 辩手跨轮带记忆的输入（§7.2）。"""
    llm = _ScriptedLLM(judge_results=[_KEEP_GOING])
    runner = _RecordingRunner()
    _run(llm, runner, _config(policy=RoundPolicy(max_rounds=3)))

    assert [c["history_len"] for c in runner.calls] == [0, 1, 2]


def test_judge_bad_json_is_conservative():
    """裁判输出坏 JSON → 保守判未收敛（宁可多辩一轮也不草草收场），跑到上限。"""
    llm = _ScriptedLLM(judge_content="嗯……我觉得还能再辩，但这里没有 JSON。")
    runner = _RecordingRunner()
    result = _run(llm, runner, _config(policy=RoundPolicy(max_rounds=3)))

    assert len(result.rounds) == 3
    assert result.stop_reason == STOP_MAX_ROUNDS
    assert result.rounds[0].verdict.converged is False


def test_all_failed_early_stop():
    """某轮全员发言失败 → 不调裁判、提前终止，归因 all_failed。"""
    llm = _ScriptedLLM()
    runner = _RecordingRunner(fail_all=True)
    result = _run(llm, runner, _config())

    assert len(result.rounds) == 1
    assert result.stop_reason == STOP_ALL_FAILED
    assert llm.judge_calls == 0  # 无可裁判内容，跳过裁判
    # 全员失败不标 absent（早停路径，非缺席续赛）。
    assert all(not t.absent for t in result.rounds[0].turns)


def test_absent_round_skips_cx_and_adversarial_scores():
    """部分失败续赛：缺席方显式标 absent；跳过对其质询；本轮不做对抗记分；赛程不阻断。"""
    scored = {
        **_KEEP_GOING,
        "scores": {
            "pro": {
                "argument": 4,
                "engagement": 3,
                "evidence": 3,
                "penalties": [],
                "note": "出席",
            },
            "con": {
                "argument": 0,
                "engagement": 0,
                "evidence": 0,
                "penalties": ["缺席"],
                "note": "不应保留",
            },
        },
    }
    llm = _ScriptedLLM(
        judge_results=[scored, _CONVERGE],
        questions={"pro": ["逼问正方"], "con": ["逼问缺席方——不应发出"]},
    )
    runner = _PartialAbsentRunner(absent_round=1, absent_key="con")
    cx = _RecordingCrossExam()
    result = asyncio.run(
        Moderator(provider=llm, model="m").run(
            _config(policy=RoundPolicy(max_rounds=5)),
            run_round=runner,
            run_cross_exam=cx,
        )
    )
    assert len(result.rounds) >= 2
    r1 = result.rounds[0]
    by_key = {t.side_key: t for t in r1.turns}
    assert by_key["pro"].ok and not by_key["pro"].absent
    assert not by_key["con"].ok and by_key["con"].absent
    # 只质询出席方
    assert len(cx.calls) >= 1
    assert set(cx.calls[0]["questions"]) == {"pro"}
    assert all(e.target != "con" for e in r1.cross_exam)
    # 缺席轮不做对抗记分（即便裁判 JSON 带回了 scores 也清空）
    assert r1.verdict.scores == {}
    payload_sides = {s["key"]: s for s in r1.to_event_payload()["sides"]}
    assert payload_sides["con"]["absent"] is True
    assert payload_sides["pro"]["absent"] is False
    # 赛程继续（未因单方缺席早停）
    assert result.stop_reason != STOP_ALL_FAILED


class _PartialAbsentRunner:
    """第 ``absent_round`` 轮仅 ``absent_key`` 失败；其余轮全员成功。"""

    def __init__(self, *, absent_round: int, absent_key: str) -> None:
        self.absent_round = absent_round
        self.absent_key = absent_key
        self.calls: list[dict] = []

    async def __call__(self, *, round_no, focus, sides, history, interjections=()):  # noqa: ANN001
        self.calls.append({"round_no": round_no, "focus": focus})
        out = []
        for s in sides:
            fail = round_no == self.absent_round and s.key == self.absent_key
            out.append(
                SideTurn(
                    side_key=s.key,
                    side_name=s.name,
                    run_id=f"{s.key}_r{round_no}",
                    content="" if fail else f"{s.name}就「{focus}」的第{round_no}轮发言",
                    ok=not fail,
                )
            )
        return out


# --- 双产物 / 呈现 -----------------------------------------------------------


def test_dual_products_present():
    """收场交付双产物：结论（决策简报字段齐全）+ 过程（每轮含小结 L1 与各方发言 L2/L3）。"""
    llm = _ScriptedLLM(judge_results=[_CONVERGE])
    runner = _RecordingRunner()
    result = _run(llm, runner, _config(policy=RoundPolicy(max_rounds=5)))

    # 结论产物
    assert result.brief.crux
    assert result.brief.strongest_points == {"pro": "正方最强论点", "con": "反方最强论点"}
    kinds = {h.kind for h in result.brief.handoffs}
    assert "fact" in kinds and "value" in kinds
    assert result.brief.handoffs
    assert result.brief.recommendation == ""
    # 过程产物（叙事线）
    assert all(r.summary for r in result.rounds)  # L1
    assert all(len(r.turns) == 2 for r in result.rounds)  # L2/L3
    assert all(t.content for r in result.rounds for t in r.turns)


def test_to_ceo_output_has_brief_and_narrative():
    """CEO 折算文本同时含决策简报与交锋叙事线（双产物都交回 CEO 收尾）。"""
    llm = _ScriptedLLM(judge_results=[_CONVERGE])
    runner = _RecordingRunner()
    result = _run(llm, runner, _config(policy=RoundPolicy(max_rounds=5)))
    out = result.to_ceo_output()

    assert "决策简报" in out
    assert "交锋叙事线" in out
    assert "争议焦点" in out
    assert "正方最强论点" in out


def test_roundtable_narrative_first():
    """探讨/学习类（圆桌）过程叙事线先行、简报收尾（§4.3 自适应呈现）。"""
    sides = [
        DebateSide(key="a", name="视角A", stance="A"),
        DebateSide(key="b", name="视角B", stance="B"),
        DebateSide(key="c", name="视角C", stance="C"),
    ]
    llm = _ScriptedLLM(judge_results=[_CONVERGE])
    runner = _RecordingRunner()
    result = _run(
        llm,
        runner,
        _config(
            form=DebateForm.ROUNDTABLE,
            sides=sides,
            policy=RoundPolicy(max_rounds=3),
        ),
    )
    out = result.to_ceo_output()

    assert result.narrative_first is True
    assert out.index("交锋叙事线") < out.index("决策简报")
    assert len(result.rounds[0].turns) == 3


# --- 逐轮增量回调（debate_round_started / debate_round 的注入点） -----------------


def test_round_hooks_order_start_before_speak_before_round():
    """逐轮增量回调注入点：每轮 on_round_start(发言【前】, 携本轮焦点 + 首轮 opening) → 辩手发言 → on_round
    (裁判 + 小结【后】, 携完整 RoundResult)。DebateTool 据此 emit debate_round_started /
    debate_round，让前端进行中先亮焦点/开场白、再流式发言、收尾叠裁判小结。"""
    order: list[str] = []
    starts: list[tuple[int, str, str]] = []
    seen: list = []

    class _OrderRunner:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def __call__(self, *, round_no, focus, sides, history, interjections=()):  # noqa: ANN001
            order.append(f"speak{round_no}")
            self.calls.append({"round_no": round_no, "focus": focus})
            return [
                SideTurn(
                    side_key=s.key,
                    side_name=s.name,
                    run_id=f"{s.key}_r{round_no}",
                    content=f"{s.name} r{round_no}",
                    ok=True,
                )
                for s in sides
            ]

    runner = _OrderRunner()

    async def on_start(round_no, focus, opening):  # noqa: ANN001
        order.append(f"start{round_no}")
        starts.append((round_no, focus, opening))

    async def on_round(rr):  # noqa: ANN001
        order.append(f"round{rr.round_no}")
        seen.append(rr)

    # 裁判持续不收敛（无楼层强制多轮了）→ 跑到 max_rounds=2 兜底，拿到稳定的 2 轮做钩子序断言。
    llm = _ScriptedLLM(judge_results=[_KEEP_GOING])
    config = _config(policy=RoundPolicy(max_rounds=2))
    asyncio.run(
        Moderator(provider=llm, model="m").run(
            config, run_round=runner, on_round_start=on_start, on_round=on_round
        )
    )

    # 2 轮，每轮严格 start → speak → round（焦点先于发言，裁判小结后于发言）。
    assert order == ["start1", "speak1", "round1", "start2", "speak2", "round2"]
    # on_round_start 携本轮焦点，与 run_round 收到的一致（同一焦点先报后用）。
    assert [s[0] for s in starts] == [1, 2]
    assert starts[0][1] == runner.calls[0]["focus"]
    # 默认脚本化 frame 无 opening → 首轮也空；后续轮契约恒空。
    assert starts[0][2] == ""
    assert starts[1][2] == ""
    # on_round 携完整 RoundResult（含小结，可直接折算事件 payload）。
    assert all(r.summary for r in seen)
    assert seen[0].to_event_payload()["round_no"] == 1


def test_round_start_hook_carries_opening_on_first_round_only():
    """首轮 on_round_start 携 opening（发言前上车）；后续轮 opening 恒空，不被覆盖。"""
    starts: list[tuple[int, str, str]] = []

    class _Runner:
        async def __call__(self, *, round_no, focus, sides, history, interjections=()):  # noqa: ANN001
            return [
                SideTurn(
                    side_key=s.key,
                    side_name=s.name,
                    run_id=f"{s.key}_r{round_no}",
                    content=f"{s.name} r{round_no}",
                    ok=True,
                )
                for s in sides
            ]

    async def on_start(round_no, focus, opening):  # noqa: ANN001
        starts.append((round_no, focus, opening))

    class _OpeningLLM(_ScriptedLLM):
        async def complete(self, request):  # noqa: ANN001
            step = request.scenario.rsplit(".", 1)[-1]
            if step == "frame" and self.frame_calls == 0:
                self.frame_calls += 1
                self.seen.append((request.messages[0].content, request.messages[1].content))
                return LLMResponse(
                    content=json.dumps(
                        {"focus": "首轮焦点", "opening": "先帮你把最要紧的事说清。"}
                    )
                )
            return await super().complete(request)

    llm = _OpeningLLM(judge_results=[_KEEP_GOING])
    asyncio.run(
        Moderator(provider=llm, model="m").run(
            _config(policy=RoundPolicy(max_rounds=2)),
            run_round=_Runner(),
            on_round_start=on_start,
        )
    )
    assert starts[0] == (1, "首轮焦点", "先帮你把最要紧的事说清。")
    assert starts[1][0] == 2
    assert starts[1][2] == ""


def test_round_to_event_payload_matches_result_round_unit():
    """RoundResult.to_event_payload 是 debate_round 事件与 debate_result.rounds 的【同源】逐轮
    单元：round_no/focus/summary/verdict/各方→辩手 run_id，且与收场全量 payload 的该轮逐字一致。"""
    llm = _ScriptedLLM(judge_results=[_CONVERGE])
    runner = _RecordingRunner()
    result = _run(llm, runner, _config(policy=RoundPolicy(max_rounds=1)))
    payload = result.rounds[0].to_event_payload()

    assert payload["round_no"] == 1
    assert payload["focus"]
    assert set(payload["verdict"]) == {
        "real_clash",
        "new_arguments",
        "converged",
        "stop_reason",
        "rationale",
    }
    assert [s["run_id"] for s in payload["sides"]] == ["pro_r1", "con_r1"]
    # 收场全量 payload 的逐轮单元由同一方法产出 → 必逐字相等（单一源，防漂移）。
    assert result.to_event_payload()["rounds"][0] == payload


# --- 交互式逐轮边界钩子（opt-in，辩论编排设计.md §逐轮交互） -----------------------


def _run_interactive(llm, runner, config, boundary):  # noqa: ANN001
    return asyncio.run(
        Moderator(provider=llm, model="m").run(
            config, run_round=runner, on_round_boundary=boundary
        )
    )


def test_round_boundary_conclude_overrides_judge_keep_going():
    """用户在第 1 轮边界选「够了出结论」→ 即便裁判判未收敛也立即收场，归因 user_concluded。"""
    seen: list = []

    async def boundary(*, round_no, result, converged, max_rounds):  # noqa: ANN001
        seen.append((round_no, converged, max_rounds))
        return RoundBoundary(decision=RoundDecision.CONCLUDE)

    llm = _ScriptedLLM(judge_results=[_KEEP_GOING])  # 裁判想继续
    runner = _RecordingRunner()
    result = _run_interactive(llm, runner, _config(policy=RoundPolicy(max_rounds=5)), boundary)

    assert len(result.rounds) == 1
    assert len(runner.calls) == 1
    assert result.stop_reason == STOP_USER_CONCLUDED
    # 钩子入参携本轮裁判判读（converged=False）与硬上限，供卡片渲染默认建议。
    assert seen == [(1, False, 5)]


def test_round_boundary_continue_overrides_convergence_with_focus():
    """裁判第 1 轮即判收敛，但用户选「加角度（带焦点）继续」→ 续辩；下一轮焦点用用户的覆写
    （跳过主持人自动定焦点），第 2 轮再选「够了」收场。"""
    decisions = [
        RoundBoundary(decision=RoundDecision.CONTINUE, focus="用户加的角度"),
        RoundBoundary(decision=RoundDecision.CONCLUDE),
    ]
    calls: list = []

    async def boundary(*, round_no, result, converged, max_rounds):  # noqa: ANN001
        calls.append((round_no, converged))
        return decisions[round_no - 1]

    llm = _ScriptedLLM(judge_results=[_CONVERGE])  # 裁判每轮都判收敛
    runner = _RecordingRunner()
    result = _run_interactive(llm, runner, _config(policy=RoundPolicy(max_rounds=5)), boundary)

    # 用户的 CONTINUE 凌驾裁判的收敛 → 真的辩了第 2 轮；第 2 轮的 CONCLUDE 收场。
    assert len(result.rounds) == 2
    assert result.stop_reason == STOP_USER_CONCLUDED
    assert [c[0] for c in calls] == [1, 2]
    assert calls[0][1] is True  # 裁判第 1 轮就判收敛，但被用户覆盖
    # 「加角度」：第 2 轮 run_round 收到的焦点是用户覆写值（非主持人 _frame 自动定的「焦点N」）。
    assert runner.calls[1]["focus"] == "用户加的角度"
    assert llm.frame_calls == 1  # 第 2 轮跳过 _frame（焦点被覆写），故只定过 1 次焦点


def test_round_boundary_continue_without_focus_uses_auto_frame():
    """CONTINUE 但不带焦点，且上轮 verdict 无 next_focus（收敛判）→ 下一轮回落主持人 _frame。"""
    decisions = [
        RoundBoundary(decision=RoundDecision.CONTINUE),  # 不加角度
        RoundBoundary(decision=RoundDecision.CONCLUDE),
    ]

    async def boundary(*, round_no, result, converged, max_rounds):  # noqa: ANN001
        return decisions[round_no - 1]

    llm = _ScriptedLLM(judge_results=[_CONVERGE])
    runner = _RecordingRunner()
    result = _run_interactive(llm, runner, _config(policy=RoundPolicy(max_rounds=5)), boundary)

    assert len(result.rounds) == 2
    # 上轮收敛 verdict 无 next_focus → 两轮都走 _frame，焦点为「焦点1」「焦点2」。
    assert llm.frame_calls == 2
    assert [c["focus"] for c in runner.calls] == ["焦点1", "焦点2"]


# --- assess 兼产 next_focus（真去重）：焦点优先级 focus_override > next_focus > _frame --------


def test_assess_next_focus_adopted_on_next_round():
    """未收敛时 assess 产出的 next_focus 被下一轮直接采用，跳过 _frame（真去重）。"""
    llm = _ScriptedLLM(judge_results=[_KEEP_GOING, _CONVERGE])
    runner = _RecordingRunner()
    result = _run(llm, runner, _config(policy=RoundPolicy(max_rounds=5)))

    assert len(result.rounds) == 2
    assert llm.frame_calls == 1  # 仅首轮定议题；第 2 轮用 next_focus
    assert runner.calls[0]["focus"] == "焦点1"
    assert runner.calls[1]["focus"] == "更深的点"  # _KEEP_GOING.next_focus
    assert result.rounds[0].verdict.next_focus == "更深的点"


def test_missing_or_blank_next_focus_falls_back_to_frame():
    """next_focus 缺失或空串 → 回退 _frame（零风险降级）；收敛 verdict 同理。"""
    no_focus = {**_KEEP_GOING}
    del no_focus["next_focus"]
    blank = {**_KEEP_GOING, "next_focus": "   "}

    for judge in (no_focus, blank):
        llm = _ScriptedLLM(judge_results=[judge, _CONVERGE])
        runner = _RecordingRunner()
        result = _run(llm, runner, _config(policy=RoundPolicy(max_rounds=5)))
        assert len(result.rounds) == 2
        assert llm.frame_calls == 2  # 两轮都走 _frame
        assert [c["focus"] for c in runner.calls] == ["焦点1", "焦点2"]


def test_focus_override_beats_assess_next_focus():
    """用户掌舵 focus_override 优先级高于上轮 next_focus。"""
    decisions = [
        RoundBoundary(decision=RoundDecision.CONTINUE, focus="用户加的角度"),
        RoundBoundary(decision=RoundDecision.CONCLUDE),
    ]

    async def boundary(*, round_no, result, converged, max_rounds):  # noqa: ANN001
        return decisions[round_no - 1]

    # 第 1 轮未收敛且带 next_focus；用户 CONTINUE 覆写焦点 → 第 2 轮用覆写值，不采纳 next_focus。
    llm = _ScriptedLLM(judge_results=[_KEEP_GOING, _CONVERGE])
    runner = _RecordingRunner()
    result = _run_interactive(llm, runner, _config(policy=RoundPolicy(max_rounds=5)), boundary)

    assert len(result.rounds) == 2
    assert runner.calls[1]["focus"] == "用户加的角度"
    assert runner.calls[1]["focus"] != "更深的点"
    assert llm.frame_calls == 1  # 第 2 轮跳过 _frame（覆写）


def test_first_round_still_frames_opening():
    """首轮仍走 _frame：开场白 + 首焦点；开场白全场只取一次。"""
    class _OpeningLLM(_ScriptedLLM):
        async def complete(self, request):  # noqa: ANN001
            step = request.scenario.rsplit(".", 1)[-1]
            if step == "frame":
                self.frame_calls += 1
                self.seen.append((request.messages[0].content, request.messages[1].content))
                return LLMResponse(
                    content=json.dumps({"focus": "首轮焦点", "opening": "先帮你把最要紧的事说清。"})
                )
            return await super().complete(request)

    llm = _OpeningLLM(judge_results=[_CONVERGE])
    result = _run(llm, _RecordingRunner(), _config(policy=RoundPolicy(max_rounds=1)))
    assert llm.frame_calls == 1
    assert result.opening == "先帮你把最要紧的事说清。"
    assert result.rounds[0].focus == "首轮焦点"


def test_kickoff_ask_seeds_round1_interjection():
    """开赛嘱咐（kickoff_ask）预注入为首轮全场插话：跑进 run_round、verbatim 进 rounds[0]。"""
    class _KickoffFrameLLM(_ScriptedLLM):
        async def complete(self, request):  # noqa: ANN001
            step = request.scenario.rsplit(".", 1)[-1]
            if step == "frame":
                self.frame_calls += 1
                self.seen.append((request.messages[0].content, request.messages[1].content))
                return LLMResponse(
                    content=json.dumps({"focus": "成本谁买单", "opening": "先看成本。"})
                )
            return await super().complete(request)

    ask = "最关心成本谁买单"
    cfg = DebateConfig(
        motion="该不该做 X",
        form=DebateForm.DEBATE,
        sides=_two_sides(),
        policy=RoundPolicy(max_rounds=1),
        kickoff_ask=ask,
    )
    llm = _KickoffFrameLLM(judge_results=[_CONVERGE])
    runner = _RecordingRunner()
    result = _run(llm, runner, cfg)

    assert [i.ask for i in runner.calls[0]["interjections"]] == [ask]
    assert runner.calls[0]["interjections"][0].target_key == ""
    assert len(result.rounds[0].user_interjections) == 1
    inter = result.rounds[0].user_interjections[0]
    assert inter.ask == ask
    assert inter.target_key == ""
    assert inter.answered is True
    # 主持人定首轮焦点时可见嘱咐。
    assert ask in llm.seen[0][1]
    assert "用户开赛嘱咐" in llm.seen[0][1]


def test_round_boundary_none_falls_back_to_judge_convergence():
    """钩子返回 None（超时 / 无活跃用户）→ 回退裁判自动收敛：裁判判收敛即收场（与非交互同辙）。"""

    async def boundary(*, round_no, result, converged, max_rounds):  # noqa: ANN001
        return None

    llm = _ScriptedLLM(judge_results=[_CONVERGE])
    runner = _RecordingRunner()
    result = _run_interactive(llm, runner, _config(policy=RoundPolicy(max_rounds=5)), boundary)

    assert len(result.rounds) == 1
    assert result.stop_reason == STOP_CONVERGED


def test_round_boundary_continue_respects_max_rounds_safety_cap():
    """用户连续 CONTINUE 也不越过 max_rounds 硬上限：到顶兜底停，归因 max_rounds（非 user_concluded）。"""

    async def boundary(*, round_no, result, converged, max_rounds):  # noqa: ANN001
        return RoundBoundary(decision=RoundDecision.CONTINUE)  # 永远想继续

    llm = _ScriptedLLM(judge_results=[_CONVERGE])  # 裁判判收敛也被用户压住
    runner = _RecordingRunner()
    result = _run_interactive(llm, runner, _config(policy=RoundPolicy(max_rounds=3)), boundary)

    assert len(result.rounds) == 3
    assert result.stop_reason == STOP_MAX_ROUNDS


# --- 追问（user_interjections，交互式逐轮 / Phase 2） ----------------------------


def test_followup_ask_injected_into_next_round_and_recorded_answered():
    """用户在第 1 轮边界【追问】+ 续辩 → 追问注入【第 2 轮】run_round（辩手据此回应），并作为
    UserInterjection 随第 2 轮 RoundResult 留痕（answered=True，结构事实：后续轮已承接）。"""
    decisions = [
        RoundBoundary(
            decision=RoundDecision.CONTINUE, ask="灰度期数据口径不一致谁兜底？", ask_target="pro"
        ),
        RoundBoundary(decision=RoundDecision.CONCLUDE),
    ]

    async def boundary(*, round_no, result, converged, max_rounds):  # noqa: ANN001
        return decisions[round_no - 1]

    llm = _ScriptedLLM(judge_results=[_CONVERGE])
    runner = _RecordingRunner()
    result = _run_interactive(llm, runner, _config(policy=RoundPolicy(max_rounds=5)), boundary)

    # 第 1 轮无追问，第 2 轮 run_round 收到该追问（注入辩手 prompt 的入口）。
    assert runner.calls[0]["interjections"] == []
    assert [i.ask for i in runner.calls[1]["interjections"]] == ["灰度期数据口径不一致谁兜底？"]
    assert runner.calls[1]["interjections"][0].target_key == "pro"
    # 追问随【第 2 轮】RoundResult 留痕，answered 翻 True（结构事实：本轮已承接它）。
    assert result.rounds[0].user_interjections == []
    assert len(result.rounds[1].user_interjections) == 1
    inter = result.rounds[1].user_interjections[0]
    assert inter.ask == "灰度期数据口径不一致谁兜底？"
    assert inter.target_key == "pro"
    assert inter.answered is True
    # 进 debate_result.rounds[*].user_interjections（唯一耐久痕迹，verbatim 复盘）。
    payload = result.to_event_payload()
    assert payload["rounds"][1]["user_interjections"] == [
        {"ask": "灰度期数据口径不一致谁兜底？", "target_key": "pro", "answered": True}
    ]
    assert payload["rounds"][0]["user_interjections"] == []


def test_followup_ask_on_conclude_recorded_unanswered():
    """用户在收场时仍带追问（无后续轮可答）→ 挂到本轮记为 answered=False（honest gap，别静默丢）。"""

    async def boundary(*, round_no, result, converged, max_rounds):  # noqa: ANN001
        return RoundBoundary(
            decision=RoundDecision.CONCLUDE, ask="那合规边界怎么算？", ask_target=""
        )

    llm = _ScriptedLLM(judge_results=[_KEEP_GOING])
    runner = _RecordingRunner()
    result = _run_interactive(llm, runner, _config(policy=RoundPolicy(max_rounds=5)), boundary)

    assert len(result.rounds) == 1
    assert result.stop_reason == STOP_USER_CONCLUDED
    inter = result.rounds[0].user_interjections[0]
    assert inter.ask == "那合规边界怎么算？"
    assert inter.target_key == ""
    assert inter.answered is False  # 收场无后续轮承接


def test_followup_ask_at_max_rounds_cap_recorded_unanswered():
    """用户在轮数上限边界仍追问 CONTINUE，但循环已无后续轮 → 挂到最后一轮记为未应答（不丢）。"""

    async def boundary(*, round_no, result, converged, max_rounds):  # noqa: ANN001
        return RoundBoundary(decision=RoundDecision.CONTINUE, ask=f"第{round_no}轮后的追问")

    llm = _ScriptedLLM(judge_results=[_CONVERGE])
    runner = _RecordingRunner()
    result = _run_interactive(llm, runner, _config(policy=RoundPolicy(max_rounds=2)), boundary)

    assert len(result.rounds) == 2
    assert result.stop_reason == STOP_MAX_ROUNDS
    # 第 1 轮边界的追问被【第 2 轮】承接（attach 到 round 2, answered=True）；第 2 轮边界的追问无
    # 后续轮 → orphan 兜底挂到末轮（round 2）记未应答。故 round 1 无痕、round 2 携两条。
    assert result.rounds[0].user_interjections == []
    last = result.rounds[1].user_interjections
    assert [i.ask for i in last] == ["第1轮后的追问", "第2轮后的追问"]
    assert [i.answered for i in last] == [True, False]


def test_brief_prompt_carries_user_followups():
    """收场简报 prompt 携全场用户追问（让结论交代是否已回应）；无追问则不出现该块（零行为变化）。"""
    decisions = [
        RoundBoundary(decision=RoundDecision.CONTINUE, ask="回滚阈值怎么定？"),
        RoundBoundary(decision=RoundDecision.CONCLUDE),
    ]

    async def boundary(*, round_no, result, converged, max_rounds):  # noqa: ANN001
        return decisions[round_no - 1]

    llm = _ScriptedLLM(judge_results=[_CONVERGE])
    _run_interactive(llm, _RecordingRunner(), _config(policy=RoundPolicy(max_rounds=5)), boundary)

    brief_prompts = [u for (s, u) in llm.seen if "请据此产出简报" in u]
    assert brief_prompts and "回滚阈值怎么定？" in brief_prompts[0]
    assert "用户在本轮追问" not in brief_prompts[0]  # 简报块用「过程中用户提出的【追问】」抬头


# --- 质询回合（P1，辩论编排设计.md §4-2.1）--------------------------------------


def _red_team_sides():
    return [
        DebateSide(key="plan", name="方案方", stance="推行该方案", is_subject=True),
        DebateSide(key="red", name="红队", stance="挑该方案的刺"),
    ]


def test_cross_exam_enabled_only_for_thorough_debate():
    """质询回合仅在【认真辩透 + 正反 DEBATE】开启（O1：红队三拍取代通用质询）。"""
    assert Moderator._cross_exam_enabled(_config(policy=RoundPolicy(max_rounds=5))) is True
    assert (
        Moderator._cross_exam_enabled(
            _config(form=DebateForm.RED_TEAM, sides=_red_team_sides(), policy=RoundPolicy(max_rounds=5))
        )
        is False
    )
    # 快速对碰（thorough=False）→ 关
    assert Moderator._cross_exam_enabled(_config(policy=RoundPolicy.quick())) is False
    # 多方圆桌（即便 thorough）→ 关
    rt = [DebateSide(key=k, name=k, stance=k) for k in ("a", "b", "c")]
    assert (
        Moderator._cross_exam_enabled(
            _config(form=DebateForm.ROUNDTABLE, sides=rt, policy=RoundPolicy(max_rounds=4))
        )
        is False
    )


def test_cross_exam_questions_parsed_and_filters_hallucinated_sides():
    """主持人质询问题解析：命中真实 side_key 的保留（每方≤3 问），幻觉 side 丢弃；全员失败发言 →
    不调 LLM、返回 {}（循环据此跳过质询）。"""
    llm = _ScriptedLLM(questions={"pro": ["q1", "q2"], "con": ["q3"], "ghost": ["x"]})
    mod = Moderator(provider=llm, model="m")
    turns = [SideTurn("pro", "正方", "r1_pro", "正方开场"), SideTurn("con", "反方", "r1_con", "反方开场")]
    qs = asyncio.run(mod._cross_exam_questions(_config(), "焦点", turns))
    assert set(qs) == {"pro", "con"}  # ghost 幻觉 side 被过滤
    assert qs["pro"] == ["q1", "q2"] and qs["con"] == ["q3"]
    assert llm.cross_exam_calls == 1
    # 全员发言失败 → 早退，不再调 LLM。
    failed = [SideTurn("pro", "正方", "r1_pro", "", ok=False), SideTurn("con", "反方", "r1_con", "", ok=False)]
    assert asyncio.run(mod._cross_exam_questions(_config(), "焦点", failed)) == {}
    assert llm.cross_exam_calls == 1


def test_cross_exam_questions_tolerates_numbered_object_preserving_doc_order():
    """真实 trace 形态：一方正常数组、另一方编号对象 ``{"1":…,"2":…}`` —— 编号对象方须被解析，
    且顺序保持文档序（禁止按 key 字典序，避免 ``"10"`` 排到 ``"2"`` 前）。"""
    # 插入序：先 "2" 再 "10"；若误按 key 排序会变成 ["q10", "q2"]。
    numbered = {"2": "q2", "10": "q10", "3": "q3"}
    llm = _ScriptedLLM(questions={"pro": ["正常问1", "正常问2"], "con": numbered})
    mod = Moderator(provider=llm, model="m")
    turns = [SideTurn("pro", "正方", "r1_pro", "正方开场"), SideTurn("con", "反方", "r1_con", "反方开场")]
    qs = asyncio.run(mod._cross_exam_questions(_config(), "焦点", turns))
    assert qs["pro"] == ["正常问1", "正常问2"]
    assert qs["con"] == ["q2", "q10", "q3"]


def test_cross_exam_side_dropped_warns_when_present_side_unusable(monkeypatch):
    """出席方 questions 值不可用（空列表 / 非全串 dict）时打 side_dropped 告警，不落正文。"""
    from agentcore.runtime.debate import moderator_agenda as agenda_mod
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(agenda_mod, "logger", spy)
    # con：空列表；pro：混入非串值的编号对象 → 双方均解析为空并告警。
    llm = _ScriptedLLM(
        questions={"pro": {"1": "ok", "2": 2}, "con": []},
    )
    mod = Moderator(provider=llm, model="m")
    turns = [SideTurn("pro", "正方", "r1_pro", "正方开场"), SideTurn("con", "反方", "r1_con", "反方开场")]
    qs = asyncio.run(mod._cross_exam_questions(_config(), "焦点", turns))
    assert qs == {}
    dropped = [kw for name, kw in spy.events if name == "debate.cross_exam.side_dropped"]
    assert {d["side_key"] for d in dropped} == {"pro", "con"}
    pro = next(d for d in dropped if d["side_key"] == "pro")
    assert pro["value_type"] == "dict"
    assert pro["keys_preview"] == ["1", "2"]
    con = next(d for d in dropped if d["side_key"] == "con")
    assert con["value_type"] == "list"
    assert con["keys_preview"] is None


def test_cross_exam_side_dropped_warns_when_present_side_omitted(monkeypatch):
    """出席方 key 整方省略于 LLM 返回时也打 side_dropped（value_type=missing），且不与值不可用重复告警。"""
    from agentcore.runtime.debate import moderator_agenda as agenda_mod
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(agenda_mod, "logger", spy)
    # 只写了 pro；con 整方缺失 → missing；ghost 幻觉不影响。
    llm = _ScriptedLLM(questions={"pro": ["逼问正方"], "ghost": ["x"]})
    mod = Moderator(provider=llm, model="m")
    turns = [SideTurn("pro", "正方", "r1_pro", "正方开场"), SideTurn("con", "反方", "r1_con", "反方开场")]
    qs = asyncio.run(mod._cross_exam_questions(_config(), "焦点", turns))
    assert qs == {"pro": ["逼问正方"]}
    dropped = [kw for name, kw in spy.events if name == "debate.cross_exam.side_dropped"]
    assert len(dropped) == 1
    assert dropped[0] == {"side_key": "con", "value_type": "missing", "keys_preview": None}


def test_cross_exam_beat_populates_round_and_feeds_judge():
    """质询 beat（thorough+正反）：主持人生成质询 → runner 作答 → 问答落进 RoundResult.cross_exam
    并喂进裁判 prompt（裁判据此记 engagement）。"""
    llm = _ScriptedLLM(judge_results=[_CONVERGE], questions={"pro": ["逼问正方"], "con": ["逼问反方"]})
    runner = _RecordingRunner()
    cx = _RecordingCrossExam()
    result = asyncio.run(
        Moderator(provider=llm, model="m").run(
            _config(policy=RoundPolicy(max_rounds=1)), run_round=runner, run_cross_exam=cx
        )
    )
    assert len(cx.calls) == 1  # 质询 runner 被调
    assert cx.calls[0]["questions"] == {"pro": ["逼问正方"], "con": ["逼问反方"]}
    got = result.rounds[0].cross_exam
    assert [e.target for e in got] == ["pro", "con"]
    assert all(ex.answer for e in got for ex in e.exchanges)
    # 裁判 prompt（「请一次性完成三件事」）看到质询问答块。
    assess = [u for (s, u) in llm.seen if "请一次性完成三件事" in u]
    assert assess and "本轮【质询环节】问答" in assess[0]


def test_cross_exam_skipped_for_quick_and_roundtable():
    """闸关（快速对碰 / 圆桌）时质询 runner 从不被调用（零额外开销、行为回退）。"""
    cx_quick = _RecordingCrossExam()
    asyncio.run(
        Moderator(provider=_ScriptedLLM(judge_results=[_CONVERGE]), model="m").run(
            _config(policy=RoundPolicy.quick()), run_round=_RecordingRunner(), run_cross_exam=cx_quick
        )
    )
    assert cx_quick.calls == []

    cx_rt = _RecordingCrossExam()
    rt = [DebateSide(key=k, name=k, stance=k) for k in ("a", "b", "c")]
    asyncio.run(
        Moderator(provider=_ScriptedLLM(judge_results=[_CONVERGE]), model="m").run(
            _config(form=DebateForm.ROUNDTABLE, sides=rt, policy=RoundPolicy(max_rounds=1)),
            run_round=_RecordingRunner(),
            run_cross_exam=cx_rt,
        )
    )
    assert cx_rt.calls == []


def test_run_without_cross_exam_runner_is_unchanged():
    """不注入质询 runner（默认）→ 无质询、无记分依赖，逐字回退到「立论→裁判」，零行为变化。"""
    llm = _ScriptedLLM(judge_results=[_CONVERGE])
    result = _run(llm, _RecordingRunner(), _config(policy=RoundPolicy(max_rounds=1)))
    assert result.rounds[0].cross_exam == []
    assert llm.cross_exam_calls == 0


# --- 结辩收束（P4·阶段化发言角色，辩论编排设计.md §4-2.4）--------------------


def test_closing_enabled_skipped_for_new_debates():
    """新场不跑结辩：认真正反 / 红队 / 快速 / 圆桌闸均关。"""
    assert Moderator._closing_enabled(_config(policy=RoundPolicy(max_rounds=5))) is False
    assert (
        Moderator._closing_enabled(
            _config(form=DebateForm.RED_TEAM, sides=_red_team_sides(), policy=RoundPolicy(max_rounds=5))
        )
        is False
    )
    assert Moderator._closing_enabled(_config(policy=RoundPolicy.quick())) is False
    rt = [DebateSide(key=k, name=k, stance=k) for k in ("a", "b", "c")]
    assert (
        Moderator._closing_enabled(
            _config(form=DebateForm.ROUNDTABLE, sides=rt, policy=RoundPolicy(max_rounds=4))
        )
        is False
    )


def test_closing_skipped_for_thorough_debate():
    """认真正反：结辩 runner 不被调用，closings 空；简报仍落地。"""
    llm = _ScriptedLLM(judge_results=[_CONVERGE])
    closing = _RecordingClosing()
    result = asyncio.run(
        Moderator(provider=llm, model="m").run(
            _config(policy=RoundPolicy(max_rounds=1)),
            run_round=_RecordingRunner(),
            run_closing=closing,
        )
    )
    assert closing.calls == []
    assert result.closings == []
    assert result.brief.crux == _DEFAULT_BRIEF["crux"]
    assert llm.brief_calls == 1


def test_closing_skipped_for_quick_and_roundtable():
    """闸关（快速对碰 / 圆桌）时结辩 runner 从不被调用（零额外开销、行为回退）。"""
    quick = _RecordingClosing()
    asyncio.run(
        Moderator(provider=_ScriptedLLM(judge_results=[_CONVERGE]), model="m").run(
            _config(policy=RoundPolicy.quick()), run_round=_RecordingRunner(), run_closing=quick
        )
    )
    assert quick.calls == []

    rt_closing = _RecordingClosing()
    rt = [DebateSide(key=k, name=k, stance=k) for k in ("a", "b", "c")]
    asyncio.run(
        Moderator(provider=_ScriptedLLM(judge_results=[_CONVERGE]), model="m").run(
            _config(form=DebateForm.ROUNDTABLE, sides=rt, policy=RoundPolicy(max_rounds=1)),
            run_round=_RecordingRunner(),
            run_closing=rt_closing,
        )
    )
    assert rt_closing.calls == []


def test_closing_skipped_when_all_failed():
    """全员发言失败（STOP_ALL_FAILED）→ 无可收束的立场，结辩 beat 跳过（不 advocacy 空气）。"""
    closing = _RecordingClosing()
    result = asyncio.run(
        Moderator(provider=_ScriptedLLM(judge_results=[_CONVERGE]), model="m").run(
            _config(policy=RoundPolicy(max_rounds=1)),
            run_round=_RecordingRunner(fail_all=True),
            run_closing=closing,
        )
    )
    assert result.stop_reason == STOP_ALL_FAILED
    assert closing.calls == []
    assert result.closings == []


def test_run_without_closing_runner_is_unchanged():
    """不注入结辩 runner（默认）→ 无结辩，收场后逐字回退到「直接出简报」，零行为变化。"""
    llm = _ScriptedLLM(judge_results=[_CONVERGE])
    result = _run(llm, _RecordingRunner(), _config(policy=RoundPolicy(max_rounds=1)))
    assert result.closings == []


def test_closings_in_event_payload():
    """新场不跑结辩：payload.closings 空列表（形状统一）；旧场回放仍可带 closings。"""
    llm = _ScriptedLLM(judge_results=[_CONVERGE])
    result = asyncio.run(
        Moderator(provider=llm, model="m").run(
            _config(policy=RoundPolicy(max_rounds=1)),
            run_round=_RecordingRunner(),
            run_closing=_RecordingClosing(),
        )
    )
    payload = result.to_event_payload()
    assert payload["closings"] == []
    assert _run(llm, _RecordingRunner(), _config(policy=RoundPolicy(max_rounds=1))).to_event_payload()["closings"] == []


# --- 记分裁判（P2，辩论编排设计.md §4-2.2）--------------------------------------


def test_judge_parses_scores_clamps_and_filters():
    """裁判记分解析：三维 clamp 到 0–5、penalties 去空、幻觉 side 丢弃；total = 三维和 - 罚分数。"""
    judge = {
        **_CONVERGE,
        "scores": {
            "pro": {"argument": 9, "engagement": 3, "evidence": -2, "penalties": ["循环论证"], "note": "x"},
            "con": {"argument": 4, "engagement": 4, "evidence": 4},
            "ghost": {"argument": 5},
        },
    }
    llm = _ScriptedLLM(judge_results=[judge])
    mod = Moderator(provider=llm, model="m")
    turns = [SideTurn("pro", "正方", "r1_pro", "a"), SideTurn("con", "反方", "r1_con", "b")]
    verdict, _ = asyncio.run(mod._judge_and_summarize(_config(), "焦点", turns, []))
    assert set(verdict.scores) == {"pro", "con"}  # ghost 幻觉 side 被过滤
    pro = verdict.scores["pro"]
    assert (pro.argument, pro.engagement, pro.evidence) == (5, 3, 0)  # 9→5 clamp、-2→0 clamp
    assert pro.penalties == ["循环论证"]
    assert pro.total == 5 + 3 + 0 - 1  # 罚 1 条
    assert verdict.scores["con"].total == 12


def test_judge_without_scores_is_backward_compatible():
    """裁判 JSON 无 scores（旧路径 / 坏 JSON）→ verdict.scores 为空 dict，行为逐字回退。"""
    llm = _ScriptedLLM(judge_results=[_KEEP_GOING])  # 无 scores 字段
    mod = Moderator(provider=llm, model="m")
    turns = [SideTurn("pro", "正方", "r1_pro", "a"), SideTurn("con", "反方", "r1_con", "b")]
    verdict, _ = asyncio.run(mod._judge_and_summarize(_config(), "焦点", turns, []))
    assert verdict.scores == {}


def test_tally_scores_accumulates_across_rounds():
    """累计记分：三维逐轮相加、penalties 全场并起（driving 收场倾向）。"""
    v1 = JudgeVerdict(True, True, False, scores={"pro": RoundScore(3, 3, 3, ["p1"]), "con": RoundScore(2, 2, 2, [])})
    v2 = JudgeVerdict(True, False, True, scores={"pro": RoundScore(4, 4, 4, ["p2", "p3"]), "con": RoundScore(1, 1, 1, [])})
    tally = tally_scores([RoundResult(1, "f1", [], v1), RoundResult(2, "f2", [], v2)])
    assert (tally["pro"].argument, tally["pro"].evidence) == (7, 7)
    assert tally["pro"].penalties == ["p1", "p2", "p3"]
    assert tally["pro"].total == 7 + 7 + 7 - 3
    assert tally["con"].total == 3 + 3 + 3
    assert tally_scores([RoundResult(1, "f", [], JudgeVerdict(True, True, True))]) == {}  # 无记分→空


def test_brief_carries_cumulative_scores_and_parses_decisive():
    """收场简报：累计记分喂进 prompt（decisive/leaning 据此），decisive 解析进 brief / CEO 文本 / payload。"""
    judge = {
        **_CONVERGE,
        "scores": {
            "pro": {"argument": 2, "engagement": 2, "evidence": 2, "penalties": ["循环论证"]},
            "con": {"argument": 4, "engagement": 4, "evidence": 4},
        },
    }
    decisive = "正方核心论据是循环论证、且拿未生效判决当已成立"
    llm = _ScriptedLLM(judge_results=[judge], brief={**_DEFAULT_BRIEF, "decisive": decisive})
    result = _run(llm, _RecordingRunner(), _config(policy=RoundPolicy(max_rounds=1)))
    brief_prompts = [u for (s, u) in llm.seen if "请据此产出简报" in u]
    assert brief_prompts and "累计记分" in brief_prompts[0] and "净分" in brief_prompts[0]
    assert "须与它一致" in brief_prompts[0]
    assert "方向" in brief_prompts[0]  # 「一致」= 倾向方向，禁抄记分数字进正文
    assert "单句≤50字" in brief_prompts[0]
    assert result.brief.decisive == decisive
    assert "胜负手" in result.to_ceo_output() and decisive in result.to_ceo_output()
    assert result.to_event_payload()["brief"]["decisive"] == decisive


def test_roundtable_brief_scores_are_momentum_only():
    """圆桌：累计记分仍喂进简报（momentum），但不要求 decisive/leaning 对齐、不裁胜负。"""
    sides = [
        DebateSide(key="a", name="视角A", stance="A"),
        DebateSide(key="b", name="视角B", stance="B"),
        DebateSide(key="c", name="视角C", stance="C"),
    ]
    judge = {
        **_CONVERGE,
        "scores": {
            "a": {"argument": 3, "engagement": 3, "evidence": 3},
            "b": {"argument": 4, "engagement": 4, "evidence": 4},
            "c": {"argument": 2, "engagement": 2, "evidence": 2},
        },
    }
    llm = _ScriptedLLM(judge_results=[judge])
    _run(
        llm,
        _RecordingRunner(),
        _config(form=DebateForm.ROUNDTABLE, sides=sides, policy=RoundPolicy(max_rounds=1)),
    )
    brief_prompts = [u for (s, u) in llm.seen if "请据此产出简报" in u]
    assert brief_prompts and "累计记分" in brief_prompts[0]
    assert "momentum" in brief_prompts[0]
    assert "须与它一致" not in brief_prompts[0]
    assert "不】驱动 leaning" in brief_prompts[0] or "不驱动 leaning" in brief_prompts[0]


def test_round_payload_has_cross_exam_and_scores_without_polluting_verdict():
    """round payload 新增 cross_exam + scores（顶层，与 verdict 平级）：verdict 子 dict 键集不被
    记分污染；质询逐条 exchanges 进 payload（answer 为解析摘要；完整流走 answer_run_id 的 run 事件）。"""
    judge = {
        **_CONVERGE,
        "scores": {
            "pro": {"argument": 3, "engagement": 3, "evidence": 3},
            "con": {"argument": 2, "engagement": 2, "evidence": 2},
        },
    }
    llm = _ScriptedLLM(judge_results=[judge], questions={"pro": ["q"], "con": ["q2"]})
    result = asyncio.run(
        Moderator(provider=llm, model="m").run(
            _config(policy=RoundPolicy(max_rounds=1)),
            run_round=_RecordingRunner(),
            run_cross_exam=_RecordingCrossExam(),
        )
    )
    payload = result.rounds[0].to_event_payload()
    # verdict 子 dict 键集【不变】（记分放 round 顶层 scores，不塞进 verdict）——防既有前端/契约漂移。
    assert set(payload["verdict"]) == {
        "real_clash",
        "new_arguments",
        "converged",
        "stop_reason",
        "rationale",
    }
    assert set(payload["scores"]) == {"pro", "con"}
    assert payload["scores"]["pro"]["total"] == 9
    assert [c["target"] for c in payload["cross_exam"]] == ["pro", "con"]
    assert "answer_run_id" in payload["cross_exam"][0]
    assert payload["cross_exam"][0]["answer_run_id"] == "pro_cx_r1"
    assert payload["cross_exam"][0]["exchanges"][0]["question"] == "q"
    assert "answer" in payload["cross_exam"][0]["exchanges"][0]
    # 与收场全量 payload 的该轮逐字一致（单一源，防漂移）——含新增字段。
    assert result.to_event_payload()["rounds"][0] == payload
