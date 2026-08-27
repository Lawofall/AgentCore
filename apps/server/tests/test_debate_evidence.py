"""举证责任·证据状态铁律（P3，辩论编排设计.md §4-2.3 契约②）自测（per-PR 零 LLM）。

方案 A（内联标记）的验收面是【prompt 契约】：辩手被要求给关键事实主张标 `【已核实·出处】`/
`【待核实·推断】`，主持人质询盯 `待核实` 当决定性论据、裁判据标记记分与罚分（诚实标注不罚、
硬拗成事实才罚）。这些是喂给 LLM 的 prompt 里必须在场的约束——记分质量本身需真模型/eval 验，
但「约束是否注入」是可无 LLM 断言的契约。真模型留给 nightly；这里直接调纯函数 + 脚本化假 provider。
"""

import asyncio
import json

from agentcore.llm.provider.protocol import LLMResponse
from agentcore.runtime.debate import (
    DebateBrief,
    DebateConfig,
    DebateForm,
    DebateHandoff,
    DebateResult,
    DebateSide,
    JudgeVerdict,
    Moderator,
    RoundPolicy,
    RoundResult,
    SideTurn,
    normalize_handoff_kind,
)
from agentcore.runtime.debate.constants import CX_LENGTH_HINT
from agentcore.runtime.debate.moderator import (
    _ASSESS_SYSTEM,
    _BRIEF_SYSTEM,
    _CROSS_EXAM_SYSTEM,
)
from agentcore.runtime.debate.moderator_brief import _as_handoffs
from agentcore.runtime.debate.prompt import (
    ARGUMENT_SKELETON_RULE,
    EVIDENCE_NOTES_SPEC,
    EVIDENCE_RULE,
    NO_PREAMBLE_RULE,
    SEARCH_QUERY_RULE,
    closing_task,
    cx_answer_feedback,
    cx_draft_brief,
    debater_task,
    draft_system,
    opening_draft_brief,
    round_draft_brief,
    round_feedback,
    side_system,
)
from agentcore.tools.builtin.debate.schema import DEBATE_DESCRIPTION, DEBATE_PARAMETERS
from agentcore.tools.builtin.web.search import WebSearchTool

# --- 共用夹具 ---------------------------------------------------------------


def _two_sides() -> list[DebateSide]:
    return [
        DebateSide(key="pro", name="正方", stance="支持做 X"),
        DebateSide(key="con", name="反方", stance="反对做 X"),
    ]


def _config(
    *,
    thorough: bool = True,
    background: str = "",
    research_dossier_index: str = "",
) -> DebateConfig:
    return DebateConfig(
        motion="该不该做 X",
        form=DebateForm.DEBATE,
        sides=_two_sides(),
        policy=RoundPolicy(thorough=thorough, max_rounds=5),
        background=background,
        research_dossier_index=research_dossier_index,
    )


def _turns() -> list[SideTurn]:
    return [
        SideTurn(side_key="pro", side_name="正方", run_id="r_pro", content="正方立论。"),
        SideTurn(side_key="con", side_name="反方", run_id="r_con", content="反方立论。"),
    ]


def _last_round() -> RoundResult:
    return RoundResult(
        round_no=1,
        focus="成本是否可控",
        turns=_turns(),
        verdict=JudgeVerdict(real_clash=True, new_arguments=True, converged=False),
        summary="上一轮小结。",
    )


class _CaptureLLM:
    """记录每次 complete 请求、回固定 JSON——供断言 prompt 里注入了什么约束。"""

    def __init__(self) -> None:
        self.requests: list = []

    async def complete(self, request):  # noqa: ANN001
        self.requests.append(request)
        return LLMResponse(content=json.dumps({}))


# --- 辩手侧：证据状态铁律进系统提示、立论/续论/质询作答有提醒 -----------------


def test_side_system_carries_evidence_burden_rule():
    """证据状态铁律进【系统提示】（continue_run 全程生效）：含两种标记 + 举证责任 + 诚实不罚。"""
    text = side_system(_config(), _two_sides()[0])
    assert "举证责任" in text
    assert "【已核实" in text and "【待核实" in text
    # 诚实存疑不罚、硬拗才罚——铁律的核心平衡（否则辩手会因怕扣分而不敢标待核实）。
    assert "诚实标注待核实" in text or "诚实标注待核实【不扣分】" in text


def test_evidence_rule_constant_is_the_single_source():
    """side_system 的证据段来自共享常量 EVIDENCE_RULE（口径单一、防漂移）。"""
    assert EVIDENCE_RULE in side_system(_config(), _two_sides()[0])


def test_side_system_carries_search_query_rule():
    """查询取证原则进【系统提示】：当事方/案由、禁抽象文化词、空结果删词重搜。

    词数不在辩论常驻复述（唯一所有者 = web_search schema）；个案 query 不出常驻。
    """
    text = side_system(_config(), _two_sides()[0])
    assert SEARCH_QUERY_RULE in text  # 来自共享常量、口径单一
    assert "schema" not in SEARCH_QUERY_RULE
    assert "见 web_search" not in SEARCH_QUERY_RULE
    assert "2–4 个核心词" not in text
    assert "2–4 核心词" not in SEARCH_QUERY_RULE
    assert "2–3 个核心词" not in SEARCH_QUERY_RULE
    assert "茉莉奶白" not in SEARCH_QUERY_RULE
    assert "当事方" in text or "案由" in text
    assert "抽象文化词" in text
    assert "空结果" in text and "再搜一次" in text  # 空→删词重搜，别当「不存在」
    schema = WebSearchTool().schema
    blob = schema.description + schema.parameters["properties"]["query"]["description"]
    assert "2–3" in blob  # 词数唯一所有者 = 工具 schema


def test_red_team_brief_omits_retired_risk_severities_name():
    """红队简报提示不再点名已退役的按方 risk_severities。"""
    from agentcore.runtime.debate.moderator_brief import _brief_form_hint

    hint = _brief_form_hint(DebateForm.RED_TEAM)
    assert "risk_severities" not in hint
    assert "conditional_pass" in hint


def test_witness_answer_keeps_unknown_without_fabrication_ban():
    """证人留不知就说不知；编造禁令归基座，答问纪律不再抄。"""
    from agentcore.runtime.debate.witness import witness_answer_feedback

    class _Seat:
        display_name = "证人·法律"
        origin_caption = "来自幕1·法律"

    text = witness_answer_feedback(_Seat(), round_no=1, focus="焦点", questions=["何时立案？"])
    assert "不知就说不知" in text
    assert "禁止编造" not in text


def test_side_system_omits_preamble_and_skeleton():
    """检索侧 side_system 不含前言/骨架（二者迁入成稿 draft_system）。"""
    text = side_system(_config(), _two_sides()[0])
    assert NO_PREAMBLE_RULE not in text
    assert ARGUMENT_SKELETON_RULE not in text
    assert EVIDENCE_RULE in text and SEARCH_QUERY_RULE in text


def test_draft_system_carries_no_preamble_and_opening_skeleton():
    """成稿 draft_system：全 beat 禁前言；立论/续辩另挂论点骨架。"""
    cfg, side = _config(), _two_sides()[0]
    opening = draft_system(cfg, side, beat="opening")
    cont = draft_system(cfg, side, beat="continue")
    cx = draft_system(cfg, side, beat="cross_exam")
    closing = draft_system(cfg, side, beat="closing")
    assert NO_PREAMBLE_RULE in opening
    assert ARGUMENT_SKELETON_RULE in opening
    assert ARGUMENT_SKELETON_RULE in cont
    assert ARGUMENT_SKELETON_RULE not in cx
    assert ARGUMENT_SKELETON_RULE not in closing
    assert "### 质询一" in NO_PREAMBLE_RULE


def test_research_tasks_ask_for_evidence_notes_not_speech():
    """检索阶段交付物 = 证据笔记；成稿 brief 才是发言任务。"""
    cfg, sides = _config(), _two_sides()
    task_payload = debater_task(cfg, sides[0], 0, round_no=1, focus="成本")
    assert task_payload["research_then_draft"] is True
    assert EVIDENCE_NOTES_SPEC in task_payload["task"]
    assert ARGUMENT_SKELETON_RULE not in task_payload["task"]
    assert ARGUMENT_SKELETON_RULE in task_payload["draft_system"]
    assert "开场立论" in task_payload["draft_brief"] or "立论" in task_payload["draft_brief"]

    fb = round_feedback(cfg, sides[0], 2, "风险", _last_round())
    assert EVIDENCE_NOTES_SPEC in fb
    assert ARGUMENT_SKELETON_RULE not in fb
    brief = round_draft_brief(cfg, sides[0], 2, "风险", _last_round())
    assert "完整发言" in brief
    assert EVIDENCE_NOTES_SPEC not in brief


def test_debater_task_and_round_feedback_carry_argument_skeleton():
    """论点骨架只进立论/续辩成稿 draft_system；质询/结辩不套立论骨架。"""
    cfg, sides = _config(), _two_sides()
    assert "### " in ARGUMENT_SKELETON_RULE
    assert "X方立论" in ARGUMENT_SKELETON_RULE or "开场立论" in ARGUMENT_SKELETON_RULE
    assert ARGUMENT_SKELETON_RULE in draft_system(cfg, sides[0], beat="opening")
    assert ARGUMENT_SKELETON_RULE in draft_system(cfg, sides[0], beat="continue")

    cx = cx_answer_feedback(cfg, sides[0], 1, "成本", ["出处？"])
    closing = closing_task(cfg, sides[0])
    assert ARGUMENT_SKELETON_RULE not in cx
    assert ARGUMENT_SKELETON_RULE not in closing
    assert ARGUMENT_SKELETON_RULE not in draft_system(cfg, sides[0], beat="cross_exam")
    assert ARGUMENT_SKELETON_RULE not in draft_system(cfg, sides[0], beat="closing")


def test_debater_task_reminds_evidence_markers():
    """首轮立论 task 提醒按证据状态标注（系统提示扛全量、task 只轻提醒）。"""
    task = debater_task(_config(), _two_sides()[0], 0, round_no=1, focus="成本")["task"]
    assert "【已核实" in task and "【待核实" in task


def test_round_feedback_reminds_evidence_markers():
    """后续轮续论 feedback 同样提醒证据状态标注。"""
    fb = round_feedback(_config(), _two_sides()[0], 2, "风险", _last_round())
    assert "【已核实" in fb and "【待核实" in fb


def test_cx_answer_feedback_uses_canonical_markers():
    """质询检索 feedback 要笔记；成稿 brief 要求 markdown 标题体 + 证据标记。"""
    cfg, side = _config(), _two_sides()[0]
    qs = ["你这条有出处吗？"]
    research = cx_answer_feedback(cfg, side, 1, "成本", qs)
    assert EVIDENCE_NOTES_SPEC in research
    assert "需要补证就查" in research
    assert "别反复空搜" in research
    assert "至多 1–2 次检索" not in research

    brief = cx_draft_brief(cfg, side, 1, "成本", qs)
    assert "【已核实" in brief and "【待核实" in brief
    assert "### 质询一" in brief
    assert "question_index" not in brief
    assert "JSON" not in brief or "不要输出 JSON" in brief
    assert "directly_addressed" not in brief
    assert "【证据状态铁律】" in brief


def test_opening_draft_brief_is_speech_not_notes():
    """首轮成稿 brief 是发言任务，不含证据笔记规格。"""
    brief = opening_draft_brief(_config(), _two_sides()[0], focus="成本")
    assert EVIDENCE_NOTES_SPEC not in brief
    assert "立论" in brief
    assert "【已核实" in brief


# --- 主持人侧：质询盯待核实、裁判据标记记分/罚且诚实不罚 ---------------------


def test_cross_exam_questions_prompt_targets_unverified_claims():
    """质询 prompt（system + user）都要求盯【待核实】当决定性论据 / 未标证据状态的主张追问。"""
    llm = _CaptureLLM()
    mod = Moderator(provider=llm, model="m")
    asyncio.run(mod._cross_exam_questions(_config(), "成本是否可控", _turns()))

    assert "举证责任" in _CROSS_EXAM_SYSTEM
    user = llm.requests[-1].messages[-1].content
    assert "待核实" in user  # 盯待核实当决定性论据
    assert "出处" in user


def test_cross_exam_questions_prompt_demands_distinct_targets():
    """源头质询去重：同一方多条质询须各打不同命门，不得换问法重复问同一点。"""
    llm = _CaptureLLM()
    mod = Moderator(provider=llm, model="m")
    asyncio.run(mod._cross_exam_questions(_config(), "成本是否可控", _turns()))

    user = llm.requests[-1].messages[-1].content
    assert "各打一个不同的命门" in user
    assert "重复问" in user


def test_judge_prompt_scores_evidence_by_markers_and_spares_honest_hedging():
    """裁判 prompt：evidence 据标记判、penalties 罚『无据硬拗』但【诚实标注待核实不罚】。"""
    llm = _CaptureLLM()
    mod = Moderator(provider=llm, model="m")
    asyncio.run(mod._judge_and_summarize(_config(), "成本是否可控", _turns(), []))

    # 系统词与 user 词都得带上「诚实标注待核实不罚」的平衡，否则辩手会因怕罚不敢诚实存疑。
    assert "待核实" in _ASSESS_SYSTEM
    user = llm.requests[-1].messages[-1].content
    assert "已核实" in user and "待核实" in user
    assert "诚实标注待核实" in user  # 只罚硬拗、不罚诚实存疑


def test_judge_prompt_still_penalizes_unsupported_when_passed_off_as_fact():
    """无据硬拗仍必罚——举证护栏不能因『诚实不罚』而放水到『无据也不罚』。"""
    llm = _CaptureLLM()
    mod = Moderator(provider=llm, model="m")
    asyncio.run(mod._judge_and_summarize(_config(), "成本是否可控", _turns(), []))
    user = llm.requests[-1].messages[-1].content
    assert "无据硬拗" in user or "硬拗" in user


# --- 决定性事实要一手来源（A）+ 结论继承置信标注（B）----------------------------
# 方案 A+B（辩论编排设计.md §4-2.2/§4-2.3·grounding）验收面同样是【prompt 契约】：记分对来源分级
# （一手/权威 vs 单一二手）、简报把【待核实/二手】证据状态继承进结论、CEO 收尾不得抹平保留语。
# 命中率本身留真模型/eval，这里只断言约束是否注入（可无 LLM）。


def _brief_user_prompt(*, background: str = "", research_dossier_index: str = "") -> str:
    """跑一次 _brief 并取回喂给 LLM 的 user prompt（假 provider 回 {} 触发降级、但请求已被捕获）。"""
    llm = _CaptureLLM()
    mod = Moderator(provider=llm, model="m")
    asyncio.run(
        mod._brief(
            _config(background=background, research_dossier_index=research_dossier_index),
            [_last_round()],
        )
    )
    return llm.requests[-1].messages[-1].content


def test_judge_prompt_grades_evidence_by_source_tier():
    """裁判 evidence 记分按来源等级挂钩：司法文书/官方原文 > 权威媒体 > 自媒体/百科/转述。"""
    llm = _CaptureLLM()
    mod = Moderator(provider=llm, model="m")
    asyncio.run(mod._judge_and_summarize(_config(), "成本是否可控", _turns(), []))
    user = llm.requests[-1].messages[-1].content
    assert "来源等级" in user
    assert "司法文书" in user and "官方原文" in user
    assert "权威媒体" in user
    assert "自媒体" in user and "百科" in user and "转述" in user
    assert "封顶打低" in user
    # 【已核实】挂弱源须在 note/penalties 点名。
    assert "弱源" in user and ("note" in user or "penalties" in user)
    # M2：优先读台账 tier，勿臆造等级。
    assert "优先读条目 tier" in user or "本轮引用证据台账" in user
    assert "unknown" in user and "弱源实锤" in user


def test_assess_system_carries_source_tier():
    """裁判系统提示锚定来源等级阶梯（口径与 user 细则一致，别只在 user 单侧交代）。"""
    assert "来源等级" in _ASSESS_SYSTEM
    assert "司法文书" in _ASSESS_SYSTEM and "官方原文" in _ASSESS_SYSTEM
    assert "权威媒体" in _ASSESS_SYSTEM
    assert "自媒体" in _ASSESS_SYSTEM and "百科" in _ASSESS_SYSTEM
    assert "弱源" in _ASSESS_SYSTEM
    assert "本轮引用证据台账" in _ASSESS_SYSTEM
    assert "勿臆造等级" in _ASSESS_SYSTEM


def test_judge_prompt_injects_ledger_tiers():
    """M2：裁判 user prompt 注入本轮引用 #eN 的结构化 tier（替代纯软约束猜等级）。"""
    from agentcore.runtime.debate.evidence_ledger import EvidenceLedger

    led = EvidenceLedger()
    eid_off = led.register(
        url="https://wenshu.court.gov.cn/case/1",
        title="判决书",
        site="wenshu.court.gov.cn",
        side_key="pro",
    )
    eid_weak = led.register(
        url="https://wenku.baidu.com/view/x",
        title="文库摘录",
        site="wenku.baidu.com",
        side_key="con",
    )
    turns = [
        SideTurn(
            side_key="pro",
            side_name="正方",
            run_id="r_pro",
            content=f"一审判赔【已核实·{eid_off}】成立。",
        ),
        SideTurn(
            side_key="con",
            side_name="反方",
            run_id="r_con",
            content=f"行业规模【已核实·{eid_weak}】很大。",
        ),
    ]
    llm = _CaptureLLM()
    mod = Moderator(provider=llm, model="m")
    asyncio.run(
        mod._judge_and_summarize(
            _config(), "成本是否可控", turns, [], evidence_ledger=led
        )
    )
    user = llm.requests[-1].messages[-1].content
    assert "本轮引用证据台账" in user
    assert f"{eid_off} · tier=official" in user
    assert f"{eid_weak} · tier=weak" in user
    assert "官方原文" in user
    assert "弱源/自媒体" in user
    assert "勿臆造等级" in user


def test_brief_prompt_injects_ledger_tiers():
    """M2：简报 user prompt 携带本场台账 tier，并约束不得抹平弱源/待核实。"""
    from agentcore.runtime.debate.evidence_ledger import EvidenceLedger

    led = EvidenceLedger()
    eid = led.register(
        url="https://www.reuters.com/world/foo",
        title="路透报道",
        site="reuters.com",
        side_key="pro",
    )
    rr = RoundResult(
        round_no=1,
        focus="成本是否可控",
        turns=[
            SideTurn(
                side_key="pro",
                side_name="正方",
                run_id="r_pro",
                content=f"成本可控【已核实·{eid}】。",
            ),
            SideTurn(side_key="con", side_name="反方", run_id="r_con", content="反对。"),
        ],
        verdict=JudgeVerdict(real_clash=True, new_arguments=True, converged=False),
        summary="上一轮小结。",
    )
    llm = _CaptureLLM()
    mod = Moderator(provider=llm, model="m")
    asyncio.run(mod._brief(_config(), [rr], evidence_ledger=led))
    user = llm.requests[-1].messages[-1].content
    assert "本场证据台账" in user
    assert f"{eid} · tier=media" in user
    assert "tier=weak" in user or "不得抹平" in user
    assert "待核实" in user


def _assert_ceo_short_tail(out: str) -> None:
    """to_ceo_output 短尾：用自己的声音 + 指向 skill，不整段贴铁律/骨架。"""
    assert "用自己的声音收尾" in out
    assert "不要粘贴本段指令" in out
    assert "debate_and_review" in out
    assert "deep_multi_lens_research" in out
    assert "【收尾铁律·别抹平证据状态】" not in out
    assert "【收尾铁律·原样传达裁决】" not in out
    assert "【收尾铁律·不引入场外量化】" not in out
    assert "【收尾模板·多视角调研起源】" not in out


def test_ceo_output_preserves_weak_tier_status():
    """简报里的弱源 / tier=weak 原样出现在 CEO 折算文本；短尾不贴铁律全文。"""
    result = DebateResult(
        config=_config(),
        rounds=[_last_round()],
        brief=DebateBrief(
            crux="成本可控性",
            strongest_points={"pro": "多家媒体称成本可控【弱源·tier=weak】"},
        ),
    )
    out = result.to_ceo_output()
    assert "弱源" in out
    assert "tier=weak" in out
    _assert_ceo_short_tail(out)


def test_brief_prompt_inherits_evidence_status_into_conclusion():
    """简报 prompt：decisive/leaning 依赖的【待核实/仅二手】事实不得当既定，须降置信或移进分歧。"""
    user = _brief_user_prompt()
    assert "继承到结论" in user
    assert "需一手核实" in user
    assert "二手来源" in user  # 单一二手来源不当既定事实
    # 要么显式降级、要么移进交接清单（factual_disputes / open_questions；别在收尾抹平）。
    assert "factual_disputes" in user and "open_questions" in user
    assert "交接清单" in user or "value_disputes" in user


def test_brief_prompt_includes_background_and_handoff_reconcile():
    """简报生成输入携带底料原文，并约束 handoffs 事实项与底料逐条对账。"""
    bg = (
        "9. 2024-03-01 · 被告表示将上诉【来源：庭后记者会纪要】"
        "——不得据此写成「已进入二审」。"
    )
    user = _brief_user_prompt(background=bg)
    assert "赛前底料" in user
    assert bg in user
    assert "逐条对账" in user
    assert "未涉及" in user  # 禁止声称底料未涉及已覆盖项


def test_brief_prompt_includes_research_dossier_index():
    """收场简报：有约定文档索引则注入（文本通道，非新事件字段）。"""
    from agentcore.runtime.debate.research_dossier import format_research_dossier_index

    idx = format_research_dossier_index(["AgentCore/文档/research/汇总与命题卡.md"])
    user = _brief_user_prompt(research_dossier_index=idx)
    assert "【工作区约定文档索引·AgentCore/文档/research/】" in user
    assert "AgentCore/文档/research/汇总与命题卡.md" in user
    assert "工作区约定文档索引" not in _brief_user_prompt()


def test_brief_prompt_keeps_reversal_condition_after_grounding_insert():
    """插入 grounding 约束后，原有『反转条件』要求仍在场（不被覆盖回归）。"""
    user = _brief_user_prompt()
    assert "反转条件" in user


def test_brief_prompt_handoffs_questionify_value_and_length_discipline():
    """交接清单：value 问句化 + 影响结论；三键去水压成单句（对齐 strongest_points）。"""
    user = _brief_user_prompt()
    assert "问句" in user
    assert "你的选择如何影响结论" in user
    assert "去水压成单句" in user and "只留命门" in user
    assert "禁复合长句堆叠" in user
    # 判别铁律与证据状态内联仍在场（不被问句化覆盖回归）。
    assert "按解决路径互斥归类" in user
    assert "内联在条目文本" in user or "证据状态语内联" in user


def test_brief_prompt_field_mutex_and_length_discipline():
    """简报字段互斥 + 对抗形态字数纪律：禁互相复述 / 禁抄记分 / 单句上限。"""
    user = _brief_user_prompt()
    assert "字段互斥" in user and "互不复述" in user
    assert "单句≤50字" in user
    assert "≤60字" in user and "禁分号堆叠" in user
    assert "禁复述记分数字" in user or "禁】把记分数字" in user
    assert "下一步动作单句" in user and "不复述判断理由" in user
    assert "方向" in user  # 与累计记分仅方向一致，不是抄数字
    # crux 生成要求仍在场（其他消费方仍用）。
    assert '"crux"' in user and "争议焦点" in user


def test_brief_system_carries_grounding_principle():
    """简报系统提示带上『二手/待核实的决定性事实须保留证据状态、不抹成既定事实』。"""
    assert "既定事实" in _BRIEF_SYSTEM
    assert "二手来源" in _BRIEF_SYSTEM or "待核实" in _BRIEF_SYSTEM


def test_ceo_output_preserves_unverified_reservations():
    """简报【待核实】原样出现在 CEO 折算文本；短尾不贴「升格/既定事实」铁律全文。"""
    result = DebateResult(
        config=_config(),
        rounds=[_last_round()],
        brief=DebateBrief(
            crux="成本可控性",
            handoffs=[DebateHandoff(kind="fact", text="真实成本【待核实】")],
        ),
    )
    out = result.to_ceo_output()
    assert "待核实" in out
    assert "真实成本【待核实】" in out
    _assert_ceo_short_tail(out)
    assert "升格" not in out
    assert "既定事实" not in out


def test_ceo_output_requires_verbatim_verdict_conveyance():
    """简报倾向 / 置信度仍渲染；短尾不贴「原样传达裁决」铁律全文。"""
    result = DebateResult(
        config=_config(),
        rounds=[_last_round()],
        brief=DebateBrief(crux="赔偿合理性", leaning="略偏反方", confidence="中等"),
    )
    out = result.to_ceo_output()
    assert "略偏反方" in out
    assert "置信度" in out
    _assert_ceo_short_tail(out)
    assert "原样传达裁决" not in out
    assert "一边倒" not in out


def test_ceo_output_bans_off_brief_quantification():
    """短尾不贴【不引入场外量化】铁律全文（正文留在 skill）。"""
    result = DebateResult(
        config=_config(),
        rounds=[_last_round()],
        brief=DebateBrief(crux="赔偿合理性"),
    )
    out = result.to_ceo_output()
    _assert_ceo_short_tail(out)
    assert "不引入场外量化" not in out
    assert "量化估算" not in out


def test_ceo_output_injects_multi_lens_skeleton_template():
    """短尾指向 skill，不把跨维骨架正文贴进 to_ceo_output。"""
    result = DebateResult(
        config=_config(),
        rounds=[_last_round()],
        brief=DebateBrief(crux="商标近似争议", leaning="略偏正方", confidence="中等"),
    )
    out = result.to_ceo_output()
    _assert_ceo_short_tail(out)
    assert "跨维度决策简报" not in out
    assert "辩论收报" not in out
    assert "正反拍板" not in out
    assert "分维简报" not in out
    assert "各透镜各一小节" not in out
    assert "别抹平证据状态" not in out
    assert "原样传达裁决" not in out
    assert "不引入场外量化" not in out


def test_as_handoffs_maps_three_keys_and_normalizes_bad_kind():
    """LLM 三键 → handoffs；坏 kind 归 question，不丢内容。"""
    items = _as_handoffs(
        {
            "value_disputes": ["更看重速度还是稳妥"],
            "factual_disputes": ["成本口径【待核实·推断】"],
            "open_questions": ["政策窗口会不会变"],
        }
    )
    assert [(h.kind, h.text) for h in items] == [
        ("value", "更看重速度还是稳妥"),
        ("fact", "成本口径【待核实·推断】"),
        ("question", "政策窗口会不会变"),
    ]
    assert normalize_handoff_kind("weird") == "question"
    assert normalize_handoff_kind("FACT") == "fact"
    bad = _as_handoffs({"handoffs": [{"kind": "mystery", "text": "仍要保留"}]})
    assert bad == [DebateHandoff(kind="question", text="仍要保留")]


def test_ceo_output_renders_handoffs_by_kind():
    """CEO 简报文本按三类交接清单分组渲染。"""
    result = DebateResult(
        config=_config(),
        rounds=[_last_round()],
        brief=DebateBrief(
            crux="成本",
            handoffs=[
                DebateHandoff(kind="value", text="速度优先？"),
                DebateHandoff(kind="fact", text="真实成本【待核实】"),
                DebateHandoff(kind="question", text="明年监管会不会收紧？"),
            ],
        ),
    )
    out = result.to_ceo_output()
    assert "需你定夺" in out and "速度优先？" in out
    assert "事实分歧" in out and "真实成本【待核实】" in out
    assert "待解问题" in out and "明年监管会不会收紧？" in out
    assert "仅剩需你拍板的点" not in out


def test_cx_draft_brief_carries_output_budget():
    """质询成稿 brief 注入明确输出预算（逐条写完、禁冒号悬垂截断）。"""
    brief = cx_draft_brief(_config(), _two_sides()[0], 1, "成本", ["出处？", "口径？"])
    assert CX_LENGTH_HINT in brief
    assert "逐条写完" in brief
    assert "冒号" in brief or "截断" in brief


def test_background_schema_requires_source_date_and_bans_inference_as_fact():
    """background：schema 短触发留来源/日期/未决；硬化反例在 debate_and_review skill。"""
    from agentcore.runtime.skills import build_system_skill_registry

    bg_desc = DEBATE_PARAMETERS["properties"]["background"]["description"]
    assert "来源" in bg_desc and "日期" in bg_desc
    assert "未决" in bg_desc or "推断" in bg_desc
    assert "debate_and_review" in bg_desc or "debate_and_review" in DEBATE_DESCRIPTION

    skill = build_system_skill_registry().get("debate_and_review")
    assert skill is not None
    body = skill.body
    assert "二审" in body  # 反例：表示将上诉 ≠ 已进入二审
    assert "来源" in body and "日期" in body


def test_background_block_prompt_bans_rewriting_pending_as_fact():
    """辩手底料块：未决/推断状态不得改写成既定事实。"""
    cfg = _config(background="- 2024-01-01 · 被告表示将上诉【来源：声明】")
    task = debater_task(cfg, _two_sides()[0], 0, round_no=1, focus="风险")["task"]
    assert "未决" in task or "推断" in task
    assert "既定事实" in task
