"""裁判 + 书记 —— 本轮判定 / 小结 / 记分（一次结构化调用）。

从 Moderator 拆出的「裁判 + 书记」职责。→ 见设计: docs/03-AI核心/辩论编排设计.md §二、§五
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.debate.match_ledger import as_ledger_events
from agentcore.runtime.debate.moderator_agenda import _form_guidance
from agentcore.runtime.debate.moderator_common import (
    _LEDGER_CLASHES_PER_ROUND,
    _LEDGER_SUMMARY_CLIP,
    _SUMMARY_CLIP,
    CompleteJson,
    _as_bool,
    _as_str,
    _as_str_list,
    _clip,
    _turns_block,
)
from agentcore.runtime.debate.types import (
    STOP_CONVERGED,
    STOP_REASONS,
    CrossExamExchange,
    DebateClash,
    DebateConfig,
    DebateForm,
    JudgeVerdict,
    RoundResult,
    RoundScore,
    SideTurn,
)

logger = get_logger(__name__)

_ASSESS_SYSTEM = (
    "你是一场结构化辩论的主持人，一身兼裁判、书记与记分。裁判：是否真交锋、是否还有新论点、能否收场——"
    "不是评文笔，不因发言更长就认为更有料。书记：一句精炼小结；未收敛时把仍存的决定性分歧压成 "
    "next_focus，已收敛则留空。记分：论证有效性 / 证据 / 是否正面回应（含质询）；证据分必须按"
    "【来源等级】挂钩——优先读 user 里【本轮引用证据台账】的 tier"
    "（official=司法文书/官方原文 > media=权威媒体 > weak=自媒体/百科/转述；"
    "unknown=来源待评、不是弱源实锤），勿臆造等级；无台账块时仍按同阶梯从出处猜。"
    "【已核实】若挂弱源，须在 note 或 penalties 点名。把待核实硬拗成既定事实必须扣分；"
    "诚实标注待核实不罚。诚实认输 / 让步 ≠ 回避。严格只输出要求的 JSON。"
)


def _prior_ledger(history: Sequence[RoundResult]) -> str:
    """把【前几轮】压成紧凑的「已辩论点账本」喂给裁判（收敛校准 §三 H2）。

    :func:`judge_and_summarize` 本只看【当前轮】发言，``history`` 只贡献 round_no 与
    小结锚点——故「跨轮重复」（老论点换个说法重述）对裁判不可见，会被误判成「还在产生新论点」而
    永不收敛。本账本把前几轮的 focus + 小结 + 交锋要点串成一份紧凑摘要（用已压缩的 summary /
    clashes、非全文，守 §二 token 预算），让裁判能判「本轮相比账本是否还有【跨轮】新论点」。

    只收有实质内容的轮（有小结或有交锋）——占位 / 空轮不入账本；无可入账内容时返回空串，裁判
    退化为只看当前轮（首轮天然如此）。
    """
    lines: list[str] = []
    for rr in history:
        summary = _clip(rr.summary, _LEDGER_SUMMARY_CLIP)
        clashes = rr.verdict.clashes[:_LEDGER_CLASHES_PER_ROUND]
        if not summary and not clashes:
            continue
        line = f"第 {rr.round_no} 轮（{rr.focus}）：{summary}"
        if clashes:
            edges = "；".join(f"{c.from_key}驳{c.to_key}「{c.point}」" for c in clashes)
            line += f"　[交锋：{edges}]"
        lines.append(line)
    if not lines:
        return ""
    body = "\n".join(lines)
    return (
        "【前几轮已辩论点账本】（判断本轮是否还有【跨轮】新论点用——账本里已有的论点，本轮换个"
        f"说法 / 换个例子重述都【不算】新论点）：\n{body}\n\n"
    )


def _cross_exam_block(config: DebateConfig, cross_exam: Sequence[CrossExamExchange]) -> str:
    """把本轮质询问答渲染进裁判 prompt（记分裁判据此判「是否正面回应质询」，质询回合 P1）。

    每条 = 对某方的质询（问题列表）+ 该方回答（头尾裁剪防爆 prompt）；空答如实标注未作答。
    裁判须区分：【诚实认输 / 让步】= 正面回应（不因认输扣 engagement）；【答非所问 / 打太极 /
    复述立论不接招】= 回避（压低 engagement，可在简报 decisive 点名）。无质询返回空串。
    """
    if not cross_exam:
        return ""
    names = {s.key: s.name for s in config.sides}
    blocks: list[str] = []
    for cx in cross_exam:
        name = names.get(cx.target, cx.target)
        lines: list[str] = []
        for ex in cx.exchanges:
            ans = _clip(ex.answer) if ex.answer.strip() else "（未作答 / 作答失败）"
            lines.append(f"  Q: {ex.question}\n  A: {ans}")
        if not lines:
            continue
        qa = "\n".join(lines)
        blocks.append(f"### 对「{name}」的质询\n{qa}")
    body = "\n\n".join(blocks)
    return (
        "本轮【质询环节】问答（由你裁定各方是否【正面】回答——直接影响 engagement；"
        "【诚实认输 / 让步 ≠ 回避】：正面接招但承认弱点 / 缺证据算正面回应，不因认输扣 engagement；"
        "只有答非所问 / 打太极 / 复述立论不接招才算回避、才压低 engagement）：\n"
        f"{body}\n\n"
    )


def _as_clashes(value: Any, valid_keys: set[str], *, limit: int = 4) -> list[DebateClash]:
    """把裁判返回的 clashes 规整为校验过的 :class:`DebateClash` 列表（L3 谁驳谁）。

    防 LLM 幻觉：``from``/``to`` 必须命中真实 side_key、且 ``from≠to``、``point`` 非空；同一
    (from,to) 去重、整体截到 ``limit`` 条（保叙事线轻量）。容忍 ``from_key``/``to_key`` 别名。
    """
    if not isinstance(value, list):
        return []
    out: list[DebateClash] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        frm = _as_str(item.get("from") or item.get("from_key"))
        to = _as_str(item.get("to") or item.get("to_key"))
        point = _as_str(item.get("point") or item.get("rebuttal"))
        if frm not in valid_keys or to not in valid_keys or frm == to or not point:
            continue
        if (frm, to) in seen:
            continue
        seen.add((frm, to))
        out.append(DebateClash(from_key=frm, to_key=to, point=point))
        if len(out) >= limit:
            break
    return out


def _as_score(item: dict[str, Any]) -> RoundScore:
    """把裁判返回的单方记分规整为 :class:`RoundScore`（三维 clamp 到 0–5、penalties 去空）。"""

    def _dim(v: Any) -> int:
        try:
            n = int(v)
        except (TypeError, ValueError):
            n = 0
        return max(0, min(5, n))

    return RoundScore(
        argument=_dim(item.get("argument")),
        engagement=_dim(item.get("engagement")),
        evidence=_dim(item.get("evidence")),
        penalties=_as_str_list(item.get("penalties")),
        note=_as_str(item.get("note")),
    )


def _as_scores(value: Any, valid_keys: set[str]) -> dict[str, RoundScore]:
    """把裁判返回的 scores 规整为 {side_key: RoundScore}（记分裁判 P2），只收命中真实 side_key 的方。

    防 LLM 幻觉出不存在的 side；非 dict / 缺失 → 空 dict（记分未开启 or 坏 JSON）：:func:`tally_scores`
    据此退化、简报逐字回退，零副作用（与 clashes / severities 的容错同口径）。
    """
    if not isinstance(value, dict):
        return {}
    out: dict[str, RoundScore] = {}
    for key, item in value.items():
        if str(key) in valid_keys and isinstance(item, dict):
            out[str(key)] = _as_score(item)
    return out


async def judge_and_summarize(
    complete_json: CompleteJson,
    config: DebateConfig,
    focus: str,
    turns: Sequence[SideTurn],
    history: list[RoundResult],
    *,
    cross_exam: Sequence[CrossExamExchange] = (),
    evidence_ledger: Any | None = None,
) -> tuple[JudgeVerdict, str]:
    """一次 LLM 调用同时产出【裁判判定】与【本轮小结】，返回 ``(verdict, summary)``。

    二者读的是同一份本轮发言，背靠背两次 ``thinking`` 调用是冗余 round-trip（辩论编排设计.md §二：真去重、非节流补丁）。主持人本就是「裁判 + 书记」二合一角色，
    合并成一遍推理天然贴合：裁判位判交锋质量与收敛、书记位写认知推进线小结。

    **裁判语义与拆分实现逐字不变**——gate_hint（首轮默认继续 / 快速单轮即收 / thorough 调松
    紧）、clash 上限、``stop_reason`` 归一（仅收敛有意义、非法回落 ``STOP_CONVERGED``）、坏 JSON
    保守判未收敛全部保留；小结叠加上一轮锚点写成 delta 推进线，坏 JSON 时回落裁判 rationale。

    **跨轮论点账本（收敛校准 §三 H2）**：``history`` 除定 round_no / 小结锚点外，还经
    :func:`_prior_ledger` 压成【前几轮已辩论点账本】喂进裁判——让 ``new_arguments`` 能判「本轮
    相比前几轮是否还有【跨轮】新论点」（老论点换措辞重述=false），根治「只看当前轮→跨轮重复不
    可见→永不因边际递减收敛」。首轮 / 无实质前轮时账本空、退化为只看当前轮（行为同旧）。

    **证据台账锚定（M2）**：``evidence_ledger`` 非空时，把本轮发言/质询里引用的 ``#eN`` 条目
    （含规则表 ``tier``）注入 user prompt——evidence 维优先读台账 tier，替代纯自由文本猜等级。
    """
    round_no = len(history) + 1
    max_rounds = config.policy.max_rounds
    # clash 上限随参与方数放宽：2 方正反 4 条够，3+ 方圆桌要容得下跨对的交锋边（A驳B、C驳A），
    # 否则多方场景的交锋图被腰斩。仍设硬顶（_as_clashes 去重 + 截断），保叙事线轻量。
    clash_limit = max(4, len(config.sides) + 2)
    # 「别过早收敛」从机械楼层搬进裁判标准：第 1 轮开场各方往往尚未接火（real_clash=false
    # 是常态），默认继续以逼出下一轮交锋，仅当命题空泛到开场即无新论点才收；快速单轮模式
    # （max=1）本就一次对碰即收；多轮模式按 thorough 调收敛松紧。
    if max_rounds <= 1:
        gate_hint = "这是【快速单轮】：用户只想一次对碰即收，核心立场已亮出即可判【收敛】。"
    elif round_no == 1:
        gate_hint = (
            "这是第 1 轮（开场立论）：各方通常只是各自亮出立场、尚未真正接火（这正常），"
            "默认判【未收敛、继续】以逼出下一轮真交锋；【仅当】命题空泛到开场就无新论点、"
            "无可再辩时才判收敛。"
        )
    elif config.policy.thorough:
        gate_hint = (
            "【认真辩透】≠ 把每个角度都辩一遍。盯住【真正决定用户问题的那个分歧】往深里辩；"
            "一旦它要么被事实/逻辑分出高下、要么已见底成一个【只能由用户拍板的价值/偏好选择】，"
            "就判【收敛】——价值之争见底是收场信号，不是继续信号。仅当还能冒出【会改变结论】的"
            "实质新论点时才继续；只把旧分歧换个说法、或转去边角枝节，都应收敛。"
        )
    else:
        gate_hint = "核心交锋一旦清晰、无强未决分歧即可收敛，不必恋战。"
    gate_note = f"注意：当前是第 {round_no} 轮（安全上限 {max_rounds} 轮）。{gate_hint}"
    # 小结锚点：喂上一轮小结 → 本轮小结写成连贯的【认知推进线】（带 delta），而非孤立摘要。
    prev = _clip(history[-1].summary, _SUMMARY_CLIP) if history else ""
    prev_block = f"上一轮小结（供小结续写认知推进线）：{prev}\n\n" if prev else ""
    # 跨轮论点账本（收敛校准 §三 H2）：让裁判据前几轮已辩论点判「本轮是否还有跨轮新论点」，
    # 而非只看当前轮把老论点换措辞误判成新论点。首轮 / 无实质前轮时为空、裁判退化为只看本轮。
    ledger_block = _prior_ledger(history)
    summary_touch = (
        "（多方圆桌：侧重点出本轮【新增 / 凸显了哪个视角】、观点光谱往哪铺。）"
        if config.form is DebateForm.ROUNDTABLE
        else "（点出相比上一轮，本轮交锋【推进 / 澄清了什么】，与上轮串成一条推进线。）"
    )
    # 质询问答喂进裁判记分（回避 / 答非所问 → 扣 engagement）；未开启质询恒空块，记分退化为只看立论。
    cx_block = _cross_exam_block(config, cross_exam)
    # M2：本轮引用 #eN → 带 tier 的结构化块（无台账/无引用 → 空，退化为软约束）。
    from agentcore.runtime.debate.evidence_ledger import format_evidence_ledger_for_judge

    evidence_block = format_evidence_ledger_for_judge(
        evidence_ledger, turns, cross_exam=cross_exam
    )
    sides_keys = ", ".join(s.key for s in config.sides)
    clash_note = (
        "- clashes：第 1 轮是开场立论——各方同时独立发言、互不知道对方说了什么，"
        "不可能存在「针对性反驳」，恒给 []。\n"
        if round_no == 1
        else (
            f"- clashes：本轮谁【针对性反驳】了谁、驳的命门（只列真正针锋相对的边，各说各话别列；"
            f"要点一句话抓住要害、别复述原话）。**覆盖本轮主要交锋别遗漏**；多方时鼓励列出跨对的"
            f"边（如 A 驳 B、C 驳 A）。最多 {clash_limit} 条；from/to 用发言标题里的 [side_key]，"
            f"from≠to；本轮无真交锋则给 []。\n"
        )
    )
    engagement_note = (
        "engagement 论点展开完整度"
        "（第 1 轮无对方可回应——改评论点展开是否完整、有无遗漏本应覆盖的核心论域）"
        if round_no == 1
        else "engagement 回应完整度"
        "（是否正面回应对方命门与【质询】；【诚实认输 / 让步 ≠ 回避】——正面接招但承认弱点算正面"
        "回应，不因认输扣分；只有答非所问 / 打太极 / 复述立论不接招 / drop 掉对方要害才压低）"
    )
    user = (
        f"辩论命题：{config.motion}\n本轮焦点：{focus}\n{_form_guidance(config.form)}\n{gate_note}\n\n"
        f"{ledger_block}{prev_block}{evidence_block}"
        f"本轮各方发言：\n{_turns_block(turns)}\n\n{cx_block}"
        "请一次性完成三件事——① 做【辩论领域内】的交锋质量与收敛判定（不是判谁写得好）；"
        "② 写一句【本轮小结】（推进 / 共识 / 仍存分歧）；③ 给各方【本轮记分】（辩论领域内、不评文笔）。"
        "若判定【未收敛】，把小结里【仍存的决定性分歧】同时压成 next_focus（下一轮争议焦点短语）——"
        "与小结同源、不是额外任务；已收敛则 next_focus 给空串。"
        "只输出一个 JSON：\n"
        '{"real_clash": true/false, "new_arguments": true/false, "converged": true/false, '
        '"stop_reason": "converged|focus_clarified|red_team_exhausted", '
        '"next_focus": "未收敛时必填：仍存分歧压成的下一轮焦点；已收敛给空串", '
        '"rationale": "一句话点出本轮的实质推进：谁让步 / 谁补强 / 谁被驳倒", '
        '"clashes": [{"from": "<side_key>", "to": "<被反驳方 side_key>", '
        '"point": "这条反驳的命门（一句话、锋利具体、抓住要害）"}], '
        f'"scores": {{"<side_key∈[{sides_keys}]>": {{"argument": 0, "engagement": 0, '
        '"evidence": 0, "penalties": ["谬误/无据主张，一句话"], "note": "一句话记分理由"}}}, '
        '"summary": "本轮小结（≤80 字）", '
        '"ledger_events": [{"kind": "withdrawal|correction|disputed_fact|concession", '
        '"side": "<side_key 或争议事实可空>", "content": "一句话"}]}\n'
        "- real_clash：各方是否真针锋相对回应了彼此（而非各说各话）。\n"
        "- new_arguments：本轮相比【前几轮已辩论点账本】是否还在产生【跨轮新论点】——把账本里"
        "已有的论点换措辞 / 换例子重述【不算】新论点（=false），只有出现账本里没有、且会推进交锋"
        "的论点才算 true；无账本（首轮）时看本轮是否亮出实质立论。\n"
        "- converged：是否可以收场（无新论点 / 焦点已澄清为价值之争 / 红队风险已挖尽）。\n"
        "- next_focus：仅【未收敛】时需要——把本轮小结里【仍存的决定性分歧】压成一句 ≤30 字的"
        "下一轮焦点短语（与定议题同规格、像小标题），供下一轮直接采用、避免再读一遍本轮发言；"
        "已收敛则给空串。\n"
        "- rationale：别写空话套话，点出本轮交锋的【实质推进】（哪一方在哪个点上让步 / 补强 / "
        "被驳倒），并点明【真正的分歧现在收窄到哪个决定性点，或已见底成哪个价值选择】，"
        "让人一句话读懂本轮的胜负手与还剩什么待决。\n"
        f"{clash_note}"
        f"- scores：给每一方本轮打分（各项 0–5 整数）：argument 论点强度、{engagement_note}、evidence 证据"
        "充分度——据【举证责任】判，且对【已核实】的出处再按【来源等级】挂钩打分（高→低）；"
        "若上方有【本轮引用证据台账】，【优先读条目 tier】（official/media/weak/unknown），"
        "勿凭自由文本臆造等级；无台账块时仍按下述阶梯从出处猜："
        "①【司法文书/官方原文】（tier=official；判决书 / 裁定书 / 官方公告 / 一手档案 / 原始数据 / 财报）"
        "= 强、可给满；"
        "②【权威媒体】（tier=media；主流通讯社 / 严肃报刊原发报道）= 中高，决定性事实若仅单一权威媒体"
        "且无交叉印证则封顶中等；③【自媒体/百科/转述】（tier=weak）= 弱、evidence 封顶打低；"
        "unknown=来源待评、【不是】弱源实锤——不单独惩罚，但决定性事实仅靠单一 unknown/weak 仍封顶打低。"
        "【已核实】挂在弱源上时，必须在 note 或 penalties 中【点名该弱源】（如「已核实但出处为百科/转述」）；"
        "关键事实标【待核实】或【未标证据状态】（默认视为待核实）却撑着结论 = 证据弱、evidence 打低；"
        "penalties 列本轮的【逻辑谬误】（循环论证 / 稻草人 / 诉诸情绪…）与【无据硬拗】（把【待核实】/"
        "未标记的主张当成【已核实】的决定性论据、或臆造出处），每条一句话——circular 与无据硬拗【必须】"
        "计入、别手软；但【诚实标注待核实】本身【不是】罚项（只罚硬拗成事实，不罚诚实存疑）；"
        "note 一句话理由。记分只对【论证有效性 / 证据 / 是否回应】，不因发言更长 / 文采更好给高分。\n"
        f"- summary：本轮交锋推进了什么、达成了什么共识、仍存什么分歧。{summary_touch}"
        "面向速读者、串起认知推进线。\n"
        "- ledger_events：【对局台账·宁缺勿滥】只收录本轮发言 / 质询问答里【显式发生】的事件，"
        "不收推断、不收你替辩手脑补的意图。四类 kind：\n"
        "  · withdrawal（撤回）——某方明确收回某论据 / 数据 / 主张；\n"
        "  · correction（更正）——某方用新值替换旧主张（如错误数字→正确数字）；\n"
        "  · disputed_fact（争议事实）——双方对同一关键事实给出冲突的【已核实】标注或明确各执一词；\n"
        "  · concession（关键让步）——某方正面承认弱点 / 某抗辩不成立 / 认输某点"
        "（诚实认输算让步，答非所问不算）。\n"
        "  side 用发言标题里的 [side_key]（争议事实可空）；content 一句话、锋利具体；"
        "本轮无显式事件则给 []——宁可空也不要灌水。"
    )
    data = await complete_json(_ASSESS_SYSTEM, user, "assess")
    if not data:
        # 坏 JSON 容错：保守地判「未收敛」（安全侧——解析失败时宁可多辩一轮也不草草收场）；
        # 小结无从生成，回落裁判 rationale（与拆分时 _summarize 的兜底同口径）。
        logger.warning("debate.assess.parse_failed", round_no=round_no)
        verdict = JudgeVerdict(
            real_clash=True,
            new_arguments=True,
            converged=False,
            rationale="裁判输出无法解析，保守判未收敛。",
        )
        return verdict, verdict.rationale
    converged = _as_bool(data.get("converged"), False)
    # stop_reason 仅在【收敛】时有意义（见 JudgeVerdict 契约）：未收敛时强制留空，杜绝
    # 「converged=false 却带 stop_reason」的口径错位随本轮 verdict 流入 journal / 前端
    # （真实 trace 曾出现第 1 轮未收敛却标 focus_clarified）。收敛时校验取值落在词表内，
    # 否则回落 STOP_CONVERGED——与循环层归一（下方 verdict.converged 分支）同一口径。
    raw_stop = _as_str(data.get("stop_reason"))
    stop_reason = (raw_stop if raw_stop in STOP_REASONS else STOP_CONVERGED) if converged else ""
    side_keys = {s.key for s in config.sides}
    verdict = JudgeVerdict(
        real_clash=_as_bool(data.get("real_clash"), True),
        new_arguments=_as_bool(data.get("new_arguments"), True),
        converged=converged,
        stop_reason=stop_reason,
        next_focus=_as_str(data.get("next_focus")),
        rationale=_as_str(data.get("rationale")),
        clashes=_as_clashes(data.get("clashes"), side_keys, limit=clash_limit),
        # 记分裁判（P2）：缺省 / 坏 JSON → 空 dict（tally 据此退化、简报零变化）。
        scores=_as_scores(data.get("scores"), side_keys),
        # 对局台账（P0）：缺省 / 坏 JSON → []；宁缺勿滥由 as_ledger_events 过滤。
        ledger_events=as_ledger_events(
            data.get("ledger_events"), side_keys, round_no=round_no
        ),
    )
    # 边际递减断路器（收敛校准 P1，辩论编排设计.md §五）：连续两轮都判不出【跨轮新论点】
    # ⇒ 交锋已进入复述、再打只是换措辞。即便裁判本轮仍给 converged=false 也机械收场——这落的正是
    # STOP_CONVERGED 的定义（「各方无实质新论点·开始重复」），用已有的 new_arguments 信号把该定义
    # 兑成一个【确定性下限】：与 max_rounds 硬上限同属断路器，但更早、更省（真实 trace 3–4 轮基本
    # 复述却硬打满）。只补下限、不改裁判语义（裁判本就该在此收敛，这里兜住其逐轮口径漂移）。首轮
    # history 空 → 恒不触发；交互式逐轮下用户仍可在边界 CONTINUE 覆写（续辩优先，见 run 循环）。
    if (
        not verdict.converged
        and not verdict.new_arguments
        and history
        and not history[-1].verdict.new_arguments
    ):
        verdict = replace(verdict, converged=True, stop_reason=STOP_CONVERGED)
        logger.info("debate.converge.diminishing_returns", round_no=round_no)
    summary = _as_str(data.get("summary")) or verdict.rationale or "（本轮小结生成失败）"
    return verdict, summary
