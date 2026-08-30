"""议题与质询 —— 定本轮焦点 / 开场白 / 质询问题生成。

从 Moderator 拆出的「主持」职责半边（定议题 + 质询 beat）。→ 见设计: docs/03-AI核心/辩论编排设计.md §二、§4-2.1
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.debate.moderator_common import (
    _SUMMARY_CLIP,
    CompleteJson,
    _as_str,
    _as_str_list,
    _clip,
    _sides_block,
    _turns_block,
)
from agentcore.runtime.debate.research_dossier import SYNTHESIZER_FILE
from agentcore.runtime.debate.types import (
    DebateConfig,
    DebateForm,
    RoundResult,
    SideTurn,
)

logger = get_logger(__name__)

# 第 1 轮【开场白】规格（喂给 :func:`frame_round` 的首轮分支）：真实辩论赛主持人面向全场的
# 宣告开场，供前端「会说话的主持人」入场气泡定调。空 / 解析失败时前端不渲染入场气泡（开场由
# 第 1 轮焦点标题承担），故它是【锦上添花】而非硬依赖——别为凑它牺牲 focus 质量。仅首轮产出，
# 后续轮恒 ""（换轮点题由前端模板承担）。
_OPENING_SPEC = (
    "另外附一句【开场白】opening：以【真实辩论赛主持人】身份、面向全场宣告开场——"
    "语体与专业辩论主持同域，庄重、赛制感，不对用户称「你」。连贯 2–3 句（约 80–120 字）："
    "【宣题】用主持人语言概括本场辩题/案由（提炼说法，勿逐字照抄 motion）；"
    "【亮场】介绍各方及其主张——正反辩论用「正方主张…／反方主张…」；红队审查用「方案方提出…／"
    "红队将审查…」；多方圆桌用「各方将从…等视角展开」；"
    "【定焦】点明首轮焦点，并交代为何它是本场关键。"
    "禁网络梗、过度比喻、寒暄客套；不剧透结论、不站队。\n"
)

_FRAME_SYSTEM = (
    "你是一场结构化辩论的主持人。你的职责之一是为每一轮设定一个【具体、可辩、不重复】的争议"
    "焦点，推动各方真正交锋而非各说各话。焦点要精炼成【一句不超过 30 字的短语】、像一个小标题，"
    "而非完整长句或对命题的复述。开场白与专业辩论主持同语域。严格只输出要求的 JSON。"
)
_CROSS_EXAM_SYSTEM = (
    "你是一场结构化辩论的主持人，现在主持【质询环节】：代表交锋，向各方发出必须正面回答的尖锐"
    "质询，逼其暴露论证里最站不住脚、最缺证据、涉嫌逻辑谬误的地方。你不替任何一方说话，而是"
    "客观地把每一方最该被追问的命门问出来——尤其【举证责任】：标了【待核实】却当决定性论据、或给了"
    "具体数字/案号却拿不出出处的主张，都要当面逼问。问题要具体、锋利、可被正面回答。严格只输出要求的 JSON。"
)


def _frame_form_hint(form: DebateForm) -> str:
    """各形态「该把本轮焦点定成什么」的差异指引（喂给 :func:`frame_round`）。

    与裁判向的形态指引（判收敛）正交——这条是议题向：定一个贴合形态、能逼出
    好交锋的焦点。圆桌尤其受益（要的是铺光谱的维度轴，而非二元对立）。"""
    if form is DebateForm.RED_TEAM:
        return (
            "形态=红队挑刺：把焦点对准【被审方案的一个具体风险面】（某失败场景 / 边界条件 / "
            "隐含假设的漏洞），让红队能集中火力施压、方案方能正面回应修补。"
        )
    if form is DebateForm.ROUNDTABLE:
        return (
            "形态=多方圆桌：把焦点定成一个能【摊开观点光谱】的维度轴——各方在此维度上自然分化、"
            "各有独特定位，而非逼出二元对立。好的圆桌焦点让每个视角都有独到的话可说。"
        )
    return (
        "形态=正反辩论：把焦点落在【真正分胜负的 crux】上——双方最根本的那个分歧点，"
        "而非双方其实都同意的外围枝节。"
    )


def _form_guidance(form: DebateForm) -> str:
    """各形态的裁判 / 收敛判据差异（辩论编排设计.md §三表格）。

    质询问题生成也复用此指引（对抗形态下的交锋语义）。"""
    if form is DebateForm.RED_TEAM:
        return (
            "形态=红队挑刺：红队单向攻击「被审方案」、方案方回应修补。收敛judge的重点是"
            "「风险是否已挖尽（无新风险可挖）」与「方案方是否已修补」，而非对称攻防。"
        )
    if form is DebateForm.ROUNDTABLE:
        return (
            "形态=多方圆桌：3+ 视角多边碰撞，无需对称攻防。收敛judge的重点是「观点光谱是否已"
            "铺满（不再冒出本质上的新视角）」，允许各方并非针锋相对。"
        )
    return (
        "形态=正反辩论：正反对称攻防。收敛judge的重点是「是否还有实质新论点」与「分歧是否已"
        "归结为价值/偏好之争（AI 判不了、该交用户）」。"
    )


def cross_exam_enabled(config: DebateConfig) -> bool:
    """质询回合仅在【认真辩透 + 正反 DEBATE】开启（提案 O1/§3.2：红队三拍取代通用质询）。

    快速对碰与多方圆桌跳过。与裁判记分共命运——不开质询也能记分，但开了质询，回避 /
    被戳穿才有据可扣（engagement）。"""
    return config.policy.thorough and config.form is DebateForm.DEBATE


def closing_enabled(config: DebateConfig) -> bool:
    """新场不跑结辩。结辩 runner / ``closing_task`` 留旧场回放与单测。"""
    del config
    return False


async def frame_round(
    complete_json: CompleteJson,
    config: DebateConfig,
    history: list[RoundResult],
) -> tuple[str, str]:
    """定本轮议题焦点；第 1 轮附带一句主持人【开场白】。

    返回 ``(focus, opening)``：``focus`` 是本轮争议焦点；``opening`` 仅【首轮】产出——辩论赛
    主持人口吻的全场宣告，供前端「会说话的主持人」入场气泡渲染（空 / 解析失败前端不渲染入场
    气泡，故非硬依赖）。后续轮恒 ``""``（换轮点题由前端模板承担）。
    """
    if not history:
        kickoff = (config.kickoff_ask or "").strip()
        kickoff_block = (
            f"\n用户开赛嘱咐（定首轮焦点时优先呼应其关心的争议点；勿覆写命题本身）：\n{kickoff}\n"
            if kickoff
            else ""
        )
        dossier = (config.research_dossier_index or "").strip()
        dossier_block = (
            f"\n{dossier}\n"
            f"定首轮焦点时，可参考汇总文件（如 `{SYNTHESIZER_FILE}`）中标出的分歧作议程线索；"
            "索引非全文，勿把文件列表当成已读完的调研正文。\n"
            if dossier
            else ""
        )
        completeness = getattr(config, "evidence_completeness", "full") or "full"
        incomplete_block = ""
        if completeness != "full":
            incomplete_block = (
                f"\n【庭前取证完整度={completeness}】"
                "开场白与首轮焦点须【显式承认证据不完整】，禁止宣称双方已充分取证；"
                "把可核实缺口标进争议焦点，勿用「材料已齐」口吻掩盖。\n"
            )
        user = (
            f"辩论命题：{config.motion}\n\n参与方：\n{_sides_block(config)}\n\n"
            f"{_frame_form_hint(config.form)}\n"
            f"{kickoff_block}{dossier_block}{incomplete_block}\n"
            "请把命题拆成【第一轮】各方应集中交锋的一个最核心争议焦点——挑命题里【最承重】的"
            "那个争议点开场（分量最大、最能带出后续交锋的），别开在边角枝节上。"
            "焦点必须是【一句短语、不超过 30 字】、像一个小标题，聚焦【单一】具体可辩的争议点——"
            "不要复述命题、不要泛泛、不要用分号堆叠多个点。\n"
            f"{_OPENING_SPEC}"
            '只输出 JSON：{"focus": "...", "opening": "..."}'
        )
    else:
        last = history[-1]
        # 已谈焦点清单（全部历史轮，非仅上轮）：喂给主持人做「别换个说法重谈」的防重锚点——
        # 让它往深里钻决定性分歧，而非每轮硬换正交新维度（旧「强制正交」把决策辩论推满上限）。
        covered_lines = [f"- 第 {rr.round_no} 轮：{rr.focus}" for rr in history]
        covered = "\n".join(covered_lines)
        user = (
            f"辩论命题：{config.motion}\n\n已谈过的焦点（别换个说法重谈；往深推、或推进到下一个更决定结论的点）：\n{covered}\n\n"
            f"上一轮小结：{_clip(last.summary, _SUMMARY_CLIP)}\n"
            f"裁判判定：真交锋={last.verdict.real_clash}、新论点={last.verdict.new_arguments}、"
            f"建议焦点={last.verdict.next_focus}\n\n"
            f"{_frame_form_hint(config.form)}\n"
            "请据上一轮仍未决的分歧定【本轮】焦点，目标是【尽快把用户的决策推到能下结论】："
            "优先把真正决定结论的那个分歧【往深里逼、逼它见分晓】，别把上一轮换个说法重谈、"
            "也别急着铺开新枝节；只有当这个决定性分歧确已辩尽（被事实分出高下、或见底成价值"
            "选择）时，才转向下一个【最影响结论】的点。（多方圆桌例外：本就为铺光谱，可转新视角。）"
            "焦点必须是【一句短语、不超过 30 字】、像一个小标题，聚焦单一争议点。"
            '只输出 JSON：{"focus": "..."}'
        )
    data = await complete_json(_FRAME_SYSTEM, user, "frame")
    focus = _as_str(data.get("focus"))
    # opening 仅首轮 prompt 索取；后续轮 data 无此键 → ""（换轮点题走前端模板）。
    opening = _as_str(data.get("opening"))
    if focus:
        return focus, opening
    # 容错：首轮用命题本身，后续用裁判建议焦点 / 上轮焦点兜底。
    # opening 兜底为空（前端不渲染入场气泡）。
    if not history:
        return config.motion, opening
    return history[-1].verdict.next_focus or history[-1].focus or config.motion, ""


async def cross_exam_questions(
    complete_json: CompleteJson,
    config: DebateConfig,
    focus: str,
    turns: Sequence[SideTurn],
) -> dict[str, list[str]]:
    """主持人代表交锋，据本轮立论为【出席各方】生成 2–3 个必须正面回答的尖锐质询。

    质询直指该方【最站不住 / 最缺证据 / 涉嫌谬误】的点（循环论证、拿未定论当已成立的论据、给不出
    出处的具体数字、回避对方命门），逼其正面回应——「让交锋当面发生」的落点。返回 ``{side_key:
    [问题, ...]}``，只保留命中真实 side_key 且非空的方，每方至多 3 问；坏 JSON / 全空返回 {}
    （循环据此跳过质询、零副作用）。调用方应只传入出席（``ok``）发言——缺席方无立论，不质询。
    用 ``scenario=…​.cross_exam`` 单列，与裁判 / 简报调用分开计费。
    """
    present = [t for t in turns if t.ok]
    if not present:
        return {}
    valid_keys = {t.side_key for t in present}
    user = (
        f"辩论命题：{config.motion}\n本轮焦点：{focus}\n{_form_guidance(config.form)}\n\n"
        f"本轮出席各方发言：\n{_turns_block(present)}\n\n"
        "你是主持人，现在进入【质询环节】：代表交锋，为【出席的每一方】拟 2–3 个【必须正面回答】的"
        "尖锐质询，直指该方本轮【最站不住脚 / 最缺证据 / 涉嫌逻辑谬误】的点——例如循环论证、拿"
        "尚无定论的东西当已成立的论据、给不出出处的具体数字 / 事实、回避了对方的命门。"
        "【举证责任】要盯紧：凡该方标了【待核实】却当决定性论据用、或给了具体数字/案号却【未标证据状态】"
        "（默认视为待核实）的主张，都要当面追问「这条你有出处吗？拿不出为何还当论据？」。问题要"
        "【具体、锋利、可被正面回答】（可用是 / 否逼答），别泛泛而问、别复述其发言。"
        "同一方的这 2–3 条质询须【各打一个不同的命门】（分别针对不同的漏洞 / 无据主张 / 谬误），"
        "不得把同一个点换个问法重复问——覆盖面比条数更重要，宁可 2 条各中要害、不凑 3 条问同一件事。"
        "只输出一个 JSON：\n"
        f'{{"questions": {{"<side_key∈[{", ".join(sorted(valid_keys))}]>": ["质询1", "质询2"]}}}}'
    )
    data = await complete_json(_CROSS_EXAM_SYSTEM, user, "cross_exam")
    raw = data.get("questions")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    seen_valid: set[str] = set()
    for key, qs in raw.items():
        k = str(key)
        if k not in valid_keys:
            continue
        seen_valid.add(k)
        questions = _as_str_list(qs)[:3]
        if questions:
            out[k] = questions
        else:
            # 出席方被 LLM 写了却解析为空（如编号对象含非串、空列表）→ 该方整段质询静默丢失；
            # 只记类型/键概要，不落完整正文。
            logger.warning(
                "debate.cross_exam.side_dropped",
                side_key=k,
                value_type=type(qs).__name__,
                keys_preview=_keys_preview(qs),
            )
    # 出席方 key 整方省略：同样丢失质询，用 value_type="missing" 与「值不可用」区分；每方至多告警一次。
    for k in valid_keys - seen_valid:
        logger.warning(
            "debate.cross_exam.side_dropped",
            side_key=k,
            value_type="missing",
            keys_preview=None,
        )
    return out


def _keys_preview(value: Any) -> list[str] | None:
    """dict 键概要（至多 8 个），供 side_dropped 告警；非 dict 返回 None。"""
    if not isinstance(value, dict):
        return None
    return [str(k) for k in list(value.keys())[:8]]
