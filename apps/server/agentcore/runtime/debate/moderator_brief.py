"""收场简报 —— 决策简报产物。

从 Moderator 拆出的「书记收场」职责。→ 见设计: docs/03-AI核心/辩论编排设计.md §二
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.debate.moderator_common import (
    _SUMMARY_CLIP,
    _TURN_CLIP,
    CompleteJson,
    _as_str,
    _as_str_list,
    _clip,
    _sides_block,
    _turns_block,
)
from agentcore.runtime.debate.types import (
    DebateBrief,
    DebateConfig,
    DebateForm,
    DebateHandoff,
    HandoffKind,
    RoundResult,
    RoundScore,
    normalize_handoff_kind,
    tally_scores,
)

logger = get_logger(__name__)

_BRIEF_SYSTEM = (
    "你是一场结构化辩论的主持人。辩论收场时你产出【决策简报】，为用户的决策负责到底：去水提炼"
    "各方最强论点、按【解决路径】分流交接清单（证据能闭合的事实分歧 / 只有用户价值观能闭合的需你"
    "定夺 / 两者都闭合不了的待解问题）、给出带置信度与成立条件的倾向判断。【决定性事实若只有二手"
    "来源 / 弱源 / 仍待核实，须在结论里保留证据状态人话（【待核实】/【二手来源】/弱源）、不抹成既定"
    "事实】——宁可诚实降置信度，不可拿未核实的事实当定论；unknown 不是弱源实锤，但单一 unknown 撑"
    "决定性事实同样不得写成既定。务实、诚实，不回避不确定性。严格只输出要求的 JSON。"
)


def _brief_form_hint(form: DebateForm) -> str:
    """各形态「简报该产出什么」的差异指引（喂给 :func:`build_brief`）。

    呼应 :attr:`DebateResult.narrative_first`：决策类（正反/红队）简报先行、为决策负责；
    探讨类（圆桌）过程先行、简报是观点地图小结。"""
    if form is DebateForm.RED_TEAM:
        return (
            "这是【红队挑刺】：简报应是【finding 台账视图 + 门决】——围绕刺→处置→复核全线程，"
            "给出 conditional_pass / needs_major_rework / not_viable；must-fix 来自未关闭的 "
            "critical/major。"
        )
    if form is DebateForm.ROUNDTABLE:
        return (
            "这是【多方圆桌】：简报应是【共识/分歧地图】——按子题组织各方主张、收敛处与分裂处"
            "（标注 crux：事实/价值/假设），而非强行裁谁对谁错；末尾点出开放问题。"
        )
    return (
        "这是【正反辩论】：一句倾向（可带「若…则翻」）、一个胜负手、置信档 high|medium|low、"
        "按路径分流的交接。不要并排甩观点，不要另写建议复述倾向。"
    )


def _interjections_block(rounds: Sequence[RoundResult]) -> str:
    """全场用户追问块（喂给 :func:`build_brief`）—— 把各轮承接的用户追问按轮汇总，让简报
    【交代是否已回应】（未应答的进交接清单）。无追问返回空串（简报 prompt 不变、零变化）。"""
    items: list[str] = []
    for rr in rounds:
        for i in rr.user_interjections:
            target = f"（向 {i.target_key}）" if i.target_key else "（向全场）"
            state = "已在该轮请辩手回应" if i.answered else "未及回应"
            items.append(f"- 第 {rr.round_no} 轮{target}：{i.ask} — {state}")
    if not items:
        return ""
    body = "\n".join(items)
    return (
        "辩论过程中用户提出的【追问】（你的简报须交代是否已被回应；仍未答清的【必须收编进交接"
        "清单】——按解决路径归入 value_disputes / factual_disputes / open_questions 之一，别让"
        f"用户的问题石沉大海）：\n{body}\n\n"
    )


def _scores_block(config: DebateConfig, tally: dict[str, RoundScore]) -> str:
    """把全场累计记分渲染进简报 prompt（记分裁判 P2）。

    对抗形态：收场 decisive / leaning 须与累计记分对齐。圆桌：仅作 momentum 展示，
    不驱动 leaning、不裁胜负（与质询/结辩同属形态门控口径）。无记分返回空串。
    """
    if not tally:
        return ""
    lines: list[str] = []
    for s in config.sides:
        sc = tally.get(s.key)
        if sc is None:
            continue
        pen = f"，罚 {len(sc.penalties)}（{'；'.join(sc.penalties)}）" if sc.penalties else ""
        lines.append(
            f"- {s.name}[{s.key}]：论点 {sc.argument} + 回应 {sc.engagement} + 证据 {sc.evidence}"
            f"{pen} = 净分 {sc.total}"
        )
    if not lines:
        return ""
    body = "\n".join(lines)
    if config.form is DebateForm.ROUNDTABLE:
        return (
            "各方【累计记分】（裁判逐轮打分之和；仅作【momentum 展示】——"
            "圆桌不裁胜负，勿用记分驱动 leaning / decisive、勿据此点名赢家）：\n"
            f"{body}\n\n"
        )
    return (
        "各方【累计记分】（裁判逐轮打分之和；你的 decisive / leaning 须与它一致——净分更高 / 罚分"
        f"更少的一方更站得住，相悖须说明为何）：\n{body}\n\n"
    )


def _normalize_confidence(raw: str) -> str:
    """收成 high|medium|low；旧场散文按含词归一，认不出则原样（前端 pill 回落 medium）。"""
    text = (raw or "").strip()
    if not text:
        return ""
    low = text.lower()
    if low in ("high", "medium", "low"):
        return low
    if "high" in low or "高" in text:
        return "high"
    if "low" in low or "低" in text:
        return "low"
    if "medium" in low or "中" in text:
        return "medium"
    return text


def _as_str_dict(value: Any) -> dict[str, str]:
    """把 strongest_points 规整为 {side_key: str}（容忍 LLM 返回 list[{key,point}] 等变体）。"""
    if isinstance(value, dict):
        return {str(k): _as_str(v) for k, v in value.items() if _as_str(v)}
    if isinstance(value, list):
        out: dict[str, str] = {}
        for item in value:
            if isinstance(item, dict):
                key = _as_str(item.get("key") or item.get("side") or item.get("side_key"))
                point = _as_str(item.get("point") or item.get("argument") or item.get("value"))
                if key and point:
                    out[key] = point
        return out
    return {}


def _as_handoffs(data: dict[str, Any]) -> list[DebateHandoff]:
    """把 LLM 三键 JSON 规整为统一 ``handoffs``（键名即分类指令 → kind）。

    判别铁律（单一来源）：证据能闭合 → fact；只有用户价值观/偏好能闭合 → value；
    两者都闭合不了 → question。坏 / 未知 kind（若 LLM 另给 handoffs 数组）归 question，不丢内容。
    """
    # 优先吃规范 handoffs 数组（容错）；否则从三键映射。
    raw = data.get("handoffs")
    if isinstance(raw, list) and raw:
        out: list[DebateHandoff] = []
        for item in raw:
            if isinstance(item, dict):
                text = _as_str(item.get("text") or item.get("item") or item.get("content"))
                if not text:
                    continue
                kind: HandoffKind = normalize_handoff_kind(_as_str(item.get("kind")))
                out.append(DebateHandoff(kind=kind, text=text))
            else:
                text = _as_str(item)
                if text:
                    out.append(DebateHandoff(kind="question", text=text))
        if out:
            return out
    mapped: list[DebateHandoff] = []
    key_to_kind: tuple[tuple[HandoffKind, str], ...] = (
        ("value", "value_disputes"),
        ("fact", "factual_disputes"),
        ("question", "open_questions"),
    )
    for kind, key in key_to_kind:
        for text in _as_str_list(data.get(key)):
            mapped.append(DebateHandoff(kind=kind, text=text))
    return mapped


def _background_block_for_brief(config: DebateConfig) -> str:
    """把赛前底料原文喂进简报（空串 → 省略）。handoffs 事实项须与底料逐条对账。"""
    bg = (config.background or "").strip()
    if not bg:
        return ""
    clipped = _clip(bg, _TURN_CLIP * 2)
    return (
        "【赛前底料·双方共享原文】（handoffs 中事实类条目须与下列清单【逐条对账】——"
        "底料已明确覆盖的事实【不得】声称「清单未涉及 / 未交代 / 未覆盖」；"
        "只能指出底料未写明、或辩论后仍待核实的缺口；未决/推断状态不得改写成既定事实）：\n"
        f"{clipped}\n\n"
    )


def _research_dossier_block_for_brief(config: DebateConfig) -> str:
    """收场简报可选约定文档索引（空串 → 省略）；提醒事实交接可对照幕1 落盘路径。"""
    idx = (config.research_dossier_index or "").strip()
    if not idx:
        return ""
    return (
        f"{idx}\n"
        "（简报事实类 handoffs 可对照上述约定文档路径标注来源；索引非全文。）\n\n"
    )


def degraded_brief(
    config: DebateConfig, rounds: Sequence[RoundResult], *, reason: str
) -> DebateBrief:
    """收场简报调用**抛异常**时的诚实降级 —— 保住已跑轮次，明说缺了什么。

    与 :func:`build_brief` 内的坏 JSON 降级是两回事：那条是「模型回了不可解析的话」，
    这条是「这次调用根本没回来」。收场是双产物的最后一步，一次网关抖动不该让前面 N 轮
    发言 / 质询 / 裁判 / 小结全部作废——但也不许拿半成品冒充完整简报，故 ``leaning`` /
    ``confidence`` / ``handoffs`` 一律留空（不编结论），只在 ``recommendation`` 里逐项
    交代缺失面。CEO 收尾文本、前端简报区、落盘产物读的都是这同一段话。
    """
    if not rounds:
        return DebateBrief(
            crux=config.motion,
            recommendation=(
                f"【收场简报缺失】辩论未产生有效轮次，且收场简报调用失败（{_clip(reason, 120)}），"
                "本场无可交付结论。"
            ),
        )
    return DebateBrief(
        crux=rounds[0].focus or config.motion,
        recommendation=(
            f"【收场简报缺失】收场时简报生成调用失败（{_clip(reason, 120)}）。"
            f"已完成的 {len(rounds)} 轮交锋、各方发言与逐轮小结【完整保留】（见交锋叙事线），"
            "但【争议焦点提炼 / 各方最强论点 / 胜负手 / 倾向判断与置信度 / 交接清单】"
            "本场均未产出——请据逐轮小结自行判断，或重开一场以取回决策简报。"
            "转述时不得把逐轮小结当成终审结论。"
        ),
    )


async def build_brief(
    complete_json: CompleteJson,
    config: DebateConfig,
    rounds: list[RoundResult],
    *,
    evidence_ledger: Any | None = None,
) -> DebateBrief:
    """收场产出决策简报（结论产物）。"""
    if not rounds:
        return DebateBrief(crux=config.motion, recommendation="辩论未产生有效轮次，无法形成简报。")
    timeline = "\n".join(
        f"第 {rr.round_no} 轮（{rr.focus}）：{_clip(rr.summary, _SUMMARY_CLIP)}" for rr in rounds
    )
    # 用户追问（交互式逐轮）：把全场用户注入的问题喂进简报，让结论【交代是否已回应】——未应答的
    # 追问必须收编进交接清单（按解决路径归类），别让用户的问题石沉大海。无追问则省略。
    followups_block = _interjections_block(rounds)
    # 记分裁判（P2）：全场累计记分喂进简报。对抗形态让 decisive / leaning 与交锋对齐；
    # 圆桌仅作 momentum（见 _scores_block）。无记分则空块，简报零变化。
    scores_block = _scores_block(config, tally_scores(rounds))
    background_block = _background_block_for_brief(config)
    dossier_block = _research_dossier_block_for_brief(config)
    # M2：场级台账 tier 注入简报抽查（无台账 → 空块，零回归）。
    from agentcore.runtime.debate.evidence_ledger import format_evidence_ledger_for_brief

    evidence_block = format_evidence_ledger_for_brief(evidence_ledger, rounds)
    last_turns = _turns_block(rounds[-1].ok_turns, clip=_TURN_CLIP)
    sides_keys = ", ".join(s.key for s in config.sides)
    is_roundtable = config.form is DebateForm.ROUNDTABLE
    is_debate = config.form is DebateForm.DEBATE
    if is_roundtable:
        score_align_note = (
            "若上方给了【累计记分】，仅作 momentum 参考、【不】驱动 leaning / decisive、"
            "【不】裁谁对谁错；decisive 可留空或写「无胜负手（圆桌）」；leaning 写观点光谱"
            "小结而非点名赢家。"
        )
        decisive_field = '  "decisive": "圆桌无胜负手：可留空或写「无胜负手（圆桌）」",\n'
        leaning_field = '  "leaning": "观点光谱小结（各视角成立前提与张力，非裁出赢家；可稍长）",\n'
        confidence_field = (
            '  "confidence": "置信度及其成立条件（说明在什么前提下倾向会反转）",\n'
        )
        recommendation_field = (
            '  "recommendation": "给用户的下一步动作单句，不复述判断理由",\n'
        )
        field_mutex = (
            "【字段互斥·各司其职、互不复述】："
            "crux = 争议焦点；strongest_points = 各方命门单句；"
            "leaning = 观点光谱小结（不裁赢家）；confidence = 置信与前提；"
            "recommendation = 下一步动作单句。"
        )
    elif is_debate:
        score_align_note = (
            "若上方给了【累计记分】，decisive / leaning 须与它【方向】一致"
            "（净分更高 / 罚分更少的一方更站得住）；禁把记分数字 / 罚分明细抄进正文；"
            "相悖在 leaning 里说明为何。"
        )
        decisive_field = (
            '  "decisive": "定局的那一个交锋点（单句≤50字：谁的哪点被证伪 / 无据 / 回避；'
            '诚实认输不算回避）；不重讲倾向、禁抄记分",\n'
        )
        leaning_field = (
            '  "leaning": "倾向方向（正方/反方）+ 命题一句；反转用「若…则翻」紧跟句号或分号后；'
            '禁复述记分数字",\n'
        )
        confidence_field = '  "confidence": "high 或 medium 或 low，只填档、不写散文",\n'
        recommendation_field = (
            '  "recommendation": "仅当交接三键都空时写一句下一步，否则空串",\n'
        )
        field_mutex = (
            "【正反简报·各司其职】："
            "leaning = 倾向方向 + 命题一句，反转用「若…则翻」紧跟句号或分号后；"
            "decisive = 一个交锋点，不重讲倾向；"
            "confidence = 只填 high|medium|low；"
            "交接三键 = 该你拍 / 去查证 / 只能等；"
            "recommendation = 仅三键都空时写一句，否则空；"
            "crux 正反留空；strongest_points 可写各方命门单句供存档。"
        )
    else:
        score_align_note = (
            "若上方给了【累计记分】，decisive / leaning 须与它【方向】一致；禁抄记分数字。"
        )
        decisive_field = (
            '  "decisive": "定门决的那一个 finding / 交锋点（单句≤50字）",\n'
        )
        leaning_field = '  "leaning": "门决倾向一句话",\n'
        confidence_field = (
            '  "confidence": "置信度及其成立条件（说明在什么前提下倾向会反转）",\n'
        )
        recommendation_field = (
            '  "recommendation": "加固建议单句，不复述判断理由",\n'
        )
        field_mutex = (
            "【字段互斥】：leaning / decisive / recommendation 各写一件事，互不复述。"
        )
    # 交接清单三键：键名即分类指令（解析层规整为 handoffs）。判别铁律单一来源，消除旧
    # open_questions「仅剩需用户拍板」与 value_disputes 的重叠。条目写法：value 问句化；
    # 三键均对齐 strongest_points「去水压成单句、只留命门」。
    handoff_taxonomy = (
        "【交接清单三键·按解决路径互斥归类，勿重叠】："
        "value_disputes = 只有用户的价值观/偏好能闭合（需你定夺）——每条须是用户可直接回答的"
        "【一个问句】；"
        "factual_disputes = 证据能闭合的事实分歧（可查证；关键事实的【待核实】/【二手来源】"
        "状态语【内联在条目文本里】、不得抹平）；"
        "open_questions = 两者都闭合不了——等外部事件 / 预测验证 / 后续观察（待解问题）。"
        "三键每条均【去水压成单句、只留命门】（与 strongest_points 同口径），禁复合长句堆叠。"
        "用户追问未答清的必须收编进上述三键之一，不得石沉大海。"
        "【底料对账】若上方给了【赛前底料】，factual_disputes / open_questions 中凡声称"
        "「底料未涉及 / 未交代 / 未覆盖」的条目，必须先对照底料原文——底料已写明的【禁止】再声称未涉及。"
    )
    user = (
        f"辩论命题：{config.motion}\n参与方：\n{_sides_block(config)}\n\n"
        f"{background_block}{dossier_block}{evidence_block}"
        f"各轮推进：\n{timeline}\n\n{scores_block}{followups_block}最后一轮各方发言：\n{last_turns}\n\n"
        f"{_brief_form_hint(config.form)}\n"
        "请据此产出简报，为用户负责到底（不要只把各方观点并排甩给他）："
        f"{field_mutex}"
        f"{score_align_note}"
        + (
            "【反转条件】写在 leaning（「若…则翻」），不要写进 confidence。"
            if is_debate
            else "leaning / confidence 还要写清【反转条件】（在什么前提下倾向会翻）。"
        )
        + "【关键事实的证据状态必须继承到结论、不得在收尾抹平】：若 decisive / leaning 依赖的某个"
        "关键事实在辩论里是【待核实】、仅【单一二手来源】、或台账 tier=weak，不得把它当既定事实"
        "来定倾向——"
        + (
            "要么在 leaning 的「若…则翻」里标【需一手核实】，要么移进交接清单"
            if is_debate
            else "要么在 confidence 里显式降级并标【需一手核实】，要么把它移进交接清单"
        )
        + "（factual_disputes 或 open_questions，证据状态语人话内联在条目文本）；"
        "结论文字里引用这类事实时【保留证据状态词】（如「若 X 属实——目前仅二手报道 / "
        "弱源、待一手核实——则…」）、别写成板上钉钉。"
        f"{handoff_taxonomy}只输出 JSON：\n"
        "{\n"
        + (
            '  "crux": "",\n'
            if is_debate
            else '  "crux": "双方真正的争议焦点在哪",\n'
        )
        + f'  "strongest_points": {{"<side_key∈[{sides_keys}]>": '
        '"该方命门单句≤60字，禁分号堆叠"}},\n'
        '  "value_disputes": ["用户可直接回答的一个问句？"],\n'
        '  "factual_disputes": ["可查证的事实分歧单句（【待核实】/【二手来源】内联）"],\n'
        f"{decisive_field}"
        f"{leaning_field}"
        f"{confidence_field}"
        f"{recommendation_field}"
        '  "open_questions": ["等外部事件/预测验证/后续观察的单句命门"]\n'
        "}"
    )
    data = await complete_json(_BRIEF_SYSTEM, user, "brief")
    if not data:
        # 容错降级：用最后一轮小结拼一个最小简报，别让坏 JSON 吞掉整场结论。
        logger.warning("debate.brief.parse_failed", rounds=len(rounds))
        return DebateBrief(
            crux=rounds[0].focus or config.motion,
            recommendation=rounds[-1].summary or "简报生成失败，请查看逐轮交锋。",
        )
    handoffs = _as_handoffs(data)
    recommendation = _as_str(data.get("recommendation"))
    if is_debate and handoffs:
        recommendation = ""
    return DebateBrief(
        crux=_as_str(data.get("crux")) or config.motion,
        strongest_points=_as_str_dict(data.get("strongest_points")),
        handoffs=handoffs,
        decisive=_as_str(data.get("decisive")),
        leaning=_as_str(data.get("leaning")),
        confidence=_normalize_confidence(_as_str(data.get("confidence"))),
        recommendation=recommendation,
    )
