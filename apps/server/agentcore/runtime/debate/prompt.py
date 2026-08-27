"""辩手 prompt 构造（检索笔记 + 成稿 brief；两阶段发言契约）。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Literal

from agentcore.runtime.debate import (
    DebateConfig,
    DebateForm,
    DebateSide,
    RoundResult,
    UserInterjection,
)
from agentcore.runtime.debate.constants import (
    CLOSING_LENGTH_HINT,
    CX_LENGTH_HINT,
    FORM_LABELS,
    LENGTH_HINT,
    QUICK_DEBATER_HINT,
)
from agentcore.runtime.debate.match_ledger import (
    accumulate_match_ledger,
    format_match_ledger_block,
    format_own_argument_titles,
)
from agentcore.runtime.debate.speech_parse import parse_speech_arguments
from agentcore.runtime.debate.types import LedgerEvent
from agentcore.runtime.runs.types import ContextBlock
from agentcore.workspace.stage_dirs import RESEARCH_DIR

# 后续轮把【对手上一轮发言】喂回本辩手时，每份的头尾截断上限。多方圆桌每轮要塞 N-1 份对手
# 全文，不裁会让 prompt 暴涨、烧钱且稀释焦点（主持人侧 judge/brief 早已 _clip，唯独喂辩手没裁）。
# 头尾保留：对手的立论（头）与结论（尾）都留，只挖中段——辩手看要旨足以针对性回应。
_OPP_CLIP = 1500
# 首轮案件底料（config.background）进 debater_task 前的封顶：CEO 可能塞入长调研笔记，
# 不裁会撑爆首轮 prompt；头尾保留与对手发言裁剪同思路。
_BG_CLIP = 2000
# 结辩 brief 材料裁剪：单条论点要点 / 让步摘要封顶；历轮论点总预算另设硬顶（与 _OPP/_BG 同思路）。
_CLOSING_POINT_CLIP = 400
_CLOSING_ARGS_TOTAL = 2000
_CLOSING_ARGS_MAX = 8
_CLOSING_CONCESSION_CLIP = 280
_CLOSING_CONCESSIONS_MAX = 6

# 质询作答里识别「让步 / 承认」的轻量启发式（防结辩翻供；非裁判语义判定）。
_CONCESSION_HINT_RE = re.compile(
    r"(让步|承认|坦承|部分成立|确实|无法否认|同意|认输|证据不足|拿不出|无法核实|不成立)"
)

BeatKind = Literal[
    "opening",
    "continue",
    "cross_exam",
    "closing",
    "attack",
    "defense",
    "rebuttal",
    "thread",
    "crux",
]


def _clip(text: str, limit: int = _OPP_CLIP) -> str:
    """头尾保留地截断（与主持人 ``moderator._clip`` 同思路）。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    half = max(1, (limit - 20) // 2)
    return f"{text[:half]}\n……（中段略）……\n{text[-half:]}"


def _background_block(config: DebateConfig) -> str:
    """首轮可选案件底料块：空串 → 不注入（零行为变化）；非空 → 裁剪后以主持人名义喂双方。

    底料中的【已核实】标签与 CEO 回合 ``#rN`` 须已由 :func:`preregister_background`
    改写 / 映射为场级 ``#eN``（辩论开场预登记）；本块只负责注入文案。
    """
    bg = (config.background or "").strip()
    if not bg:
        return ""
    clipped = _clip(bg, _BG_CLIP)
    return (
        "\n\n【主持人整理的案件底料·双方共享】\n"
        "以下为开场前已核实的客观事实清单（非观点、非评价；每条应带来源与日期）。"
        "引用其中事实时，【沿用清单中的【已核实·#eN】台账 id】——不得把本底料本身包装成新的"
        "【已核实】来源；清单未写明为既定事实的未决 / 推断状态（如「表示将上诉」≠「已进入二审」）"
        "不得改写成既定事实。"
        f"先读约定文档（{RESEARCH_DIR}/）取证，独立检索仅补约定文档没有的缺口；引用约定文档内容须标注文件来源。\n"
        f"{clipped}\n"
    )


def _research_dossier_block(config: DebateConfig) -> str:
    """首轮可选约定文档索引块：工作区有约定文档文件时注入（文件列表 + 取证纪律，不注全文）。"""
    idx = (config.research_dossier_index or "").strip()
    if not idx:
        return ""
    return (
        f"\n\n{idx}\n"
        "取证纪律：【约定文档优先】按本轮议题焦点选读相关约定文档文件（看索引中的字数/摘要），"
        "【禁止】无差别全量通读所有约定文档——控 token、保立论空间；"
        "先用 file_read / file_list / grep 取证。"
        "独立检索（web_search / read_url）【仅】补约定文档没有的缺口："
        "每次补搜前须在证据笔记中【先声明约定文档缺口】（缺什么、哪份约定文档未覆盖）；"
        "【禁止】重复检索约定文档已覆盖的基础事实（时间线 / 主体 / 已核实数字与案号）。"
        "引用约定文档事实写成【已核实·#eN】——id 须用上方「约定文档预登记台账」列出的号"
        "（已预登记进场级台账，徽章可溯源到约定文档文件与幕1 #rN）；"
        "勿另造自由出处短语代替 #eN。\n"
    )


# 举证责任·证据状态铁律（辩论编排设计.md §4-2.3，证据台账 M1）。放进辩手
# 【系统提示】而非每轮 task：辩手跨轮走 continue_run 复用同一 session（系统提示只发一次却全程生效），
# 故立论 / 续论 / 质询作答一律受此约束。成稿只许沿用本方证据笔记中出现过的 #eN；
# 机械闸校验 id ∈ 本方笔记引用集（结辩 = 历轮并集）。
EVIDENCE_RULE = (
    "\n【举证责任·证据状态铁律】你陈述的每一条【关键事实主张】（具体数字 / 金额 / 日期 / 案号 / "
    "引用 / 先例 / 统计口径）都必须【紧跟一个证据状态标记】，二选一：\n"
    "- 【已核实·#eN】——出处 id 须已出现在【本方本轮证据笔记】（检索时工具结果末尾的台账 id；"
    "沿用底料 #eN 也须写入笔记）；成稿禁止从全场清单盲配 id（如【已核实·#e3】；只写 id，不要写自由出处短语）；\n"
    "- 【待核实·推断】——你拿不出本方笔记里绑定过的台账 id，只是推断 / 常识 / 估算。\n"
    "拿不出已绑定 id 就【诚实标注待核实】，绝不臆造 #eN、绝不把推断伪装成已核实事实；未加标记的关键事实"
    "一律按【待核实】对待。无据主张与「拿待核实当已成立的论据」会在质询里被当面追问、在记分里被扣分——"
    "诚实标注待核实【不扣分】，硬拗成事实才扣。"
)


# 查询取证原则。与 EVIDENCE_RULE 同进辩手【系统提示】（只发一次、跨轮 continue_run 全程生效）。
# 词数/截断唯一所有者是 web_search schema；此处只留辩论三条写法，不复述词数、不钉个案 query。
SEARCH_QUERY_RULE = (
    "\n【web_search 取证原则】\n"
    "- query 须含命题中的当事方 / 案由关键词，禁止只搜抽象文化词"
    "（易命中无关科普 / 词典 / 百科噪声）；\n"
    "- 核实数字 / 案号 / 金额时，用【主体 + 该事实的类别词】去搜，不要把数字本身塞进 query；\n"
    "- 若返回【空结果】，别当成「不存在」——【删掉最具体的那个限定词（案号 / 机构名 / 年份 / 金额）再搜一次】、"
    "改用更泛或同义的词；连搜两次仍空，才按【证据状态铁律】诚实标【待核实·推断】。"
)


# 输出纪律·禁止前言：仅进【成稿】调用的 draft_system（无工具干净上下文）。检索阶段不注入——
# 带工具 ReAct 收工语境下本纪律失效（会混入「信息已足够」）；成稿阶段经 eval 验证有效。
NO_PREAMBLE_RULE = (
    "\n【输出纪律·禁止前言】直接输出内容本身，禁止任何寒暄或过程叙述前言"
    "（如「好的，我已经掌握了充足的材料」「以下是我的立论」「现在我开始作答」）。"
    "首字即正文：立论/续辩以第一个论点标题开头；质询作答以「### 质询一」类标题开头。"
)


# 输出纪律·论点骨架（仅立论/续辩成稿）：进 draft_system，**不进**检索 task / side_system——
# 质询/结辩有各自契约（质询标题体 / 胜负手短陈词），骨架只服务前端 parseSpeechArguments 的 `### ` 切段。
ARGUMENT_SKELETON_RULE = (
    "\n【输出纪律·论点骨架】发言必须直接以第一个论点的 `### ` 短标题行开头"
    "（标题 ≤16 字、一句话点破主张）；【已核实/待核实】证据标记只放正文、不放标题。"
    "禁止「X方立论 / 开场立论」类总标题（界面已渲染方名与阶段）；"
    "禁止用加粗行（如 `**标题**`）冒充章节标题。"
    "按上文「2–3 个」论点口径展开，每个论点一块：`### 标题` + 正文。"
)


# 证据笔记正向产出规格（检索阶段交付物）：ReAct 循环的 stop 正文 = 笔记，不是发言。
# 成稿阶段另起干净调用，从源头消除收工叙述与案情复述混入发言。
# 主张↔来源绑定前移到检索阶段：行尾 #eN 在「刚读完该来源」时写入，禁止成稿盲配。
EVIDENCE_NOTES_SPEC = (
    "\n【证据笔记·本阶段交付物】本阶段只产出【证据笔记】，不是正式发言。"
    "笔记用自由 markdown，按需包含：\n"
    "1. 可引用的事实要点（数字 / 日期 / 案号 / 原文要点）：【每条事实要点行尾标注来源 #eN】"
    "（取自工具结果末尾「已登记来源」列表；刚读完 / 刚决定采用该来源时即绑定，勿留到成稿再猜 id）；"
    "成稿【已核实·#eN】只能沿用本笔记出现过的 id，否则标【待核实·推断】；"
    "沿用开场已登记的底料 #eN 时也须写入本笔记；\n"
    "2. 对本方立场有利的论据线索、先例、对比口径；\n"
    "3. 对方可能攻击点或本轮必须正面回应的缺口（如有）。\n"
    "禁止写正式立论 / 结辩陈词，禁止寒暄与收工汇报（如「信息已足够」「以下是我的发言」），"
    "禁止「案件简介」类背景复述——只记可搬进成稿的要点。"
    "检索完成后直接输出笔记正文。"
)


def role_directive(config: DebateConfig, side: DebateSide) -> str:
    """按形态 / 角色给辩手的差异化指引。"""
    if config.form is DebateForm.RED_TEAM:
        if side.is_subject:
            return (
                "（你是被审视的方案方：红队会单向施压找你的漏洞，你的职责是诚实回应、能修补"
                "就给出修补、修不了的风险要坦白承认，不要嘴硬。）"
            )
        return (
            "（你是红队：职责是尽力挖出该方案的风险、漏洞、失败场景与边界条件，单向施压，"
            "不需要你自己另提方案。）"
        )
    if config.form is DebateForm.ROUNDTABLE:
        return (
            "（这是多方圆桌：你代表一个特定视角，平等陈述并回应他人，目标是铺满观点光谱、"
            "贡献你这一视角独有的洞察，而非压倒对方。）"
        )
    return "（这是正反辩论：直接攻防，针锋相对地回应对方最强论点。）"


def side_system(config: DebateConfig, side: DebateSide) -> str:
    """检索阶段系统提示：角色 + 举证/查询铁律。前言/骨架纪律改挂成稿 draft_system。"""
    base = (
        f"你是一场结构化辩论中的辩手，代表「{side.name}」。坚定但理性地为你的立场辩护："
        "论据具体、直面对方、不偷换概念、不因篇幅长而堆砌；用具体证据 / 例子 / 推理链支撑论点，"
        "而非泛泛断言或空喊口号。"
    )
    return (
        f"{base}{role_directive(config, side)}"
        f"{EVIDENCE_RULE}{SEARCH_QUERY_RULE}"
    )


def draft_system(config: DebateConfig, side: DebateSide, *, beat: BeatKind) -> str:
    """成稿阶段系统提示：角色 + 举证标记 + 禁止前言；立论/续辩另挂论点骨架。"""
    base = (
        f"你是一场结构化辩论中的辩手，代表「{side.name}」。坚定但理性地为你的立场辩护："
        "论据具体、直面对方、不偷换概念、不因篇幅长而堆砌；用具体证据 / 例子 / 推理链支撑论点，"
        "而非泛泛断言或空喊口号。"
    )
    text = f"{base}{role_directive(config, side)}{EVIDENCE_RULE}{NO_PREAMBLE_RULE}"
    if beat in ("opening", "continue", "attack", "defense", "rebuttal", "thread"):
        text += ARGUMENT_SKELETON_RULE
    if beat == "attack":
        text += (
            "\n【红队攻击规格】产出本轮 finding 列表：每条须指向方案具体部位、标严重度"
            "critical/major/minor、带证据标记。格式示例：`- [critical] 指向：… — 主张…`。"
            "红队之间不要求互驳。"
        )
    elif beat == "defense":
        text += (
            "\n【逐条处置规格】对清单中每条 finding 标明接受/缓解/反驳/挂起，并给理由与证据；"
            "禁止笼统回应。"
        )
    elif beat == "rebuttal":
        text += (
            "\n【复核规格】只针对方案方处置复核，禁止重复原刺；每条判 closed/escalated/deadlocked；"
            "被合并方可申诉拆分。"
        )
    elif beat == "thread":
        text += "\n【点名发言规格】先回应本线程已有发言，再补自己的独到视角；禁各说各话。"
    elif beat == "crux":
        text += "\n【crux 短答】2–4 句正面回答分歧驱动（事实/价值/假设），挖到即止、不逼收敛。"
    return text


def _situation_header(
    config: DebateConfig,
    side: DebateSide,
    *,
    focus: str,
    ask_block: str = "",
) -> str:
    return (
        f"你在一场【{FORM_LABELS.get(config.form, '辩论')}】中代表「{side.name}」。\n"
        f"辩论命题：{config.motion}\n"
        f"你的立场 / 视角：{side.stance}\n"
        f"本轮议题：{focus}\n"
        f"{role_directive(config, side)}{ask_block}"
    )


def opening_draft_brief(
    config: DebateConfig,
    side: DebateSide,
    *,
    focus: str,
    interjections: Sequence[UserInterjection] = (),
) -> str:
    """首轮成稿 brief（干净成稿调用的发言任务）。

    案件底料同时进成稿（检索退化跳过时成稿仍可见共享事实；引用规则与检索侧一致）。
    """
    ask_block = _interjection_block(side, interjections)
    quick_suffix = "" if config.policy.thorough else f"\n{QUICK_DEBATER_HINT}"
    bg_block = _background_block(config)
    dossier_block = _research_dossier_block(config)
    return (
        f"{_situation_header(config, side, focus=focus, ask_block=ask_block)}\n\n"
        f"请就本轮议题给出有力、具体、有论据的【开场立论】：聚焦你最能站住的论点，"
        f"用具体证据 / 例子 / 推理链支撑；关键事实主张按【证据状态铁律】标注"
        f"【已核实·#eN】/【待核实·推断】。"
        f"{LENGTH_HINT}{quick_suffix}{bg_block}{dossier_block}"
    )


def debater_task(
    config: DebateConfig,
    side: DebateSide,
    idx: int,
    *,
    round_no: int,
    focus: str,
    interjections: Sequence[UserInterjection] = (),
    turn_model: str = "",
) -> dict[str, Any]:
    """构造首轮单个辩手的 task dict（build_run_plan 入参）。

    两阶段契约：``task`` = 检索阶段（证据笔记）；``draft_brief`` / ``draft_system`` = 成稿阶段。
    ``interjections`` 为开赛嘱咐等首轮预注入的全场/定向用户插话；空则零行为变化。
    ``turn_model`` = 本 turn 主模型（空 side 回退）；side 非空身份优先注入路由键（§7.5）。
    """
    quick_suffix = "" if config.policy.thorough else f"\n{QUICK_DEBATER_HINT}"
    bg_block = _background_block(config)
    dossier_block = _research_dossier_block(config)
    ask_block = _interjection_block(side, interjections)
    has_dossier = bool((config.research_dossier_index or "").strip()) or bool(
        getattr(config, "pretrial_evidence_ready", False)
    )
    dossier_discipline = (
        "【约定文档优先·选读】按本轮议题焦点选读约定文档（看索引字数/摘要），勿全量通读；"
        "补搜前须声明约定文档缺口；禁重复搜约定文档已覆盖的基础事实。"
        if has_dossier
        else f"优先用 file_read / file_list / grep 阅读工作区 {RESEARCH_DIR}/ 约定文档（若有）；"
    )
    research_task = (
        f"{_situation_header(config, side, focus=focus, ask_block=ask_block)}\n\n"
        f"请为开场立论做取证：{dossier_discipline}"
        f"独立检索（web_search / read_url）仅补约定文档没有的缺口；然后产出【证据笔记】。"
        f"关键事实主张按【证据状态铁律】标注。"
        f"{EVIDENCE_NOTES_SPEC}{quick_suffix}{bg_block}{dossier_block}"
    )
    payload: dict[str, Any] = {
        "role": side.name,
        "task": f"代表「{side.name}」就「{focus}」立论。\n\n{research_task}",
        "system_prompt_supplement": side_system(config, side),
        # 真纯丙·H4：不再注入系统只读 tools 名单；默认全开相关工具面。
        "group": f"debate:{config.form.value}",
        "round": round_no,
        "research_then_draft": True,
        # 结构化检索姿态：辩手 speech research 收紧（weak / 商城词典硬剔）。
        "search_policy": "debate_evidence",
        # 证据台账 id 闸：开场立论成稿的【已核实·#eN】须 ∈ 场级台账。
        "evidence_ledger_check": True,
        "side_key": side.key,
        "draft_brief": opening_draft_brief(
            config, side, focus=focus, interjections=interjections
        ),
        "draft_system": draft_system(config, side, beat="opening"),
    }
    # 有约定文档或庭前取证已汇流时：优先用庭前按完整度写下的 per-side 预算（full→0 / 缺口→有界）；
    # 未写入时保留约定文档残搜旧路径（CEO 约定文档、无庭前）。
    side_budgets = getattr(config, "debater_retrieval_budgets", None) or {}
    if side.key in side_budgets:
        payload["retrieval_budget"] = int(side_budgets[side.key])
    elif has_dossier or getattr(config, "pretrial_evidence_ready", False):
        from agentcore.runtime.runs.retrieval_budget import (
            DEFAULT_RETRIEVAL_BUDGET_DEBATER_WITH_DOSSIER,
        )

        payload["retrieval_budget"] = DEFAULT_RETRIEVAL_BUDGET_DEBATER_WITH_DOSSIER
    # §7.5：side 非空 → 路由键；空 → turn 主模型（不跟 Worker）。
    from agentcore.runtime.debate.models import side_route_model

    route = side_route_model(side, turn_model=turn_model)
    if route:
        payload["model"] = route
    # stance 仅正反 2 方有意义（builder 只认 pro/con，display-only）。
    if config.form is DebateForm.DEBATE and len(config.sides) == 2:
        payload["stance"] = "pro" if idx == 0 else "con"
    return payload


def _challenged_lines(config: DebateConfig, side: DebateSide, last_round: RoundResult) -> str:
    """本方上一轮被反驳的命门（``to_key==本方`` 的 clash 边）渲染成「- {反驳方}：{命门}」多行；
    无指向本方的边时返回空串。喂 LLM 的 :func:`_challenged_block` 与展示用的
    :func:`round_context_blocks` 都读它，保证「投喂==展示」同源。"""
    names = {s.key: s.name for s in config.sides}
    against = [c for c in last_round.verdict.clashes if c.to_key == side.key]
    return "\n".join(f"- {names.get(c.from_key, c.from_key)}：{c.point}" for c in against)


def _challenged_block(config: DebateConfig, side: DebateSide, last_round: RoundResult) -> str:
    """上一轮裁判抽出的「谁驳了本方、驳在哪」（``to_key==本方`` 的 clash 边）——喂回辩手让它
    【精准回应被攻击的命门】（B2）。与主持人侧 clash 强化形成正反馈：辩手正面接招 → 下一轮交锋
    更针锋相对 → 裁判抽 clash 更干净。无指向本方的边时返回空串（跳过、不硬塞）。"""
    lines = _challenged_lines(config, side, last_round)
    if not lines:
        return ""
    return (
        "\n\n上一轮裁判记录你被这样反驳（请【优先正面回应】这些命门——能驳回就驳回、"
        f"该让步就坦诚让步，别回避）：\n{lines}"
    )


def _interjection_mine(
    side: DebateSide, interjections: Sequence[UserInterjection]
) -> list[UserInterjection]:
    """本辩手本轮该正面回答的用户追问：定向本方（``target_key==本方``）的 + 未定向（空 target）
    的全场追问。喂 LLM 的 :func:`_interjection_block` 与展示用的 :func:`round_context_blocks`
    都读它，保证「投喂==展示」同源。"""
    return [i for i in interjections if i.ask and (not i.target_key or i.target_key == side.key)]


def _interjection_block(side: DebateSide, interjections: Sequence[UserInterjection]) -> str:
    """把用户【追问】拼进本辩手的 feedback —— 定向某方（``target_key``）的只喂给那一方，未定向
    （空 target）的喂给全场。追问是用户的最高优先级诉求，故明令【本轮优先正面回答】（先答追问、
    再展开），别答非所问。无（指向本方的）追问返回空串（feedback 不变、零行为变化）。"""
    mine = _interjection_mine(side, interjections)
    if not mine:
        return ""
    directed = any(i.target_key == side.key for i in mine)
    who = "向你" if directed else "向全场"
    lines = "\n".join(f"- {i.ask}" for i in mine)
    return (
        f"\n\n⚠️ 用户在本轮追问（{who}提出，请【本轮优先正面回答】，先答这个、再展开你的论点，"
        f"别回避、别答非所问）：\n{lines}"
    )


def _round_engage_and_opponents(
    config: DebateConfig, side: DebateSide, last_round: RoundResult
) -> tuple[str, str]:
    opponents = [t for t in last_round.ok_turns if t.side_key != side.key]
    if opponents:
        opp_block = "\n\n".join(f"### {t.side_name}\n{_clip(t.content)}" for t in opponents)
    else:
        opp_block = "（对方上一轮无有效发言）"
    if config.form is DebateForm.ROUNDTABLE:
        engage = "请【回应并补充】（呼应有道理的、标出你视角下的分歧、贡献你这一视角独有的洞察）"
    else:
        engage = "请【针对性回应】（驳斥站不住的、承认确有道理的、推进你的立场）"
    return engage, opp_block


def _ledger_and_own_blocks(
    config: DebateConfig,
    side: DebateSide,
    *,
    match_ledger: Sequence[LedgerEvent] = (),
    history: Sequence[RoundResult] = (),
    include_own_titles: bool = False,
) -> str:
    """台账摘要 +（可选）己方论点标题一览，供 feedback / brief / 结辩拼接。"""
    names = {s.key: s.name for s in config.sides}
    parts: list[str] = []
    ledger_block = format_match_ledger_block(match_ledger, side_names=names)
    if ledger_block:
        parts.append(ledger_block.rstrip())
    if include_own_titles:
        own = format_own_argument_titles(history, side)
        if own:
            parts.append(own.rstrip())
    if not parts:
        return ""
    return "\n\n".join(parts) + "\n\n"


def round_feedback(
    config: DebateConfig,
    side: DebateSide,
    round_no: int,
    focus: str,
    last_round: RoundResult,
    interjections: Sequence[UserInterjection] = (),
    *,
    match_ledger: Sequence[LedgerEvent] = (),
    history: Sequence[RoundResult] = (),
) -> str:
    """后续轮【检索阶段】feedback：情境 + 对方论点 + 对局台账 + 证据笔记交付物。"""
    engage, opp_block = _round_engage_and_opponents(config, side, last_round)
    challenged = _challenged_block(config, side, last_round)
    ask_block = _interjection_block(side, interjections)
    extra = _ledger_and_own_blocks(config, side, match_ledger=match_ledger, history=history)
    return (
        f"## 第 {round_no} 轮 · 本轮焦点：{focus}\n"
        f"{role_directive(config, side)}{ask_block}\n\n"
        f"{extra}"
        f"对方上一轮的论点如下（成稿时需{engage}）：\n"
        f"{opp_block}{challenged}\n\n"
        f"请为本轮续辩做必要检索取证，然后产出【证据笔记】："
        f"**只记本轮焦点下的新论点 / 新回应所需素材**，不要重述你上一轮已说过的内容、"
        f"不要复述对方原话。"
        f"{EVIDENCE_NOTES_SPEC}"
    )


def round_draft_brief(
    config: DebateConfig,
    side: DebateSide,
    round_no: int,
    focus: str,
    last_round: RoundResult,
    interjections: Sequence[UserInterjection] = (),
    *,
    match_ledger: Sequence[LedgerEvent] = (),
    history: Sequence[RoundResult] = (),
) -> str:
    """后续轮【成稿】brief（含对局台账 + 己方历轮论点标题一览）。"""
    engage, opp_block = _round_engage_and_opponents(config, side, last_round)
    challenged = _challenged_block(config, side, last_round)
    ask_block = _interjection_block(side, interjections)
    extra = _ledger_and_own_blocks(
        config,
        side,
        match_ledger=match_ledger,
        history=history,
        include_own_titles=True,
    )
    return (
        f"## 第 {round_no} 轮 · 本轮焦点：{focus}\n"
        f"{role_directive(config, side)}{ask_block}\n\n"
        f"{extra}"
        f"对方上一轮的论点如下，{engage}：\n"
        f"{opp_block}{challenged}\n\n"
        f"直接输出你本轮的【完整发言】：**只补本轮焦点下的新论点 / 新回应**，用具体证据 / 例子 / "
        f"推理链支撑；关键事实主张按【证据状态铁律】标注【已核实·#eN】/【待核实·推断】；"
        f"不要重述你上一轮已说过的内容、不要复述对方原话、不要罗列改动清单。"
        f"{LENGTH_HINT}"
    )


_CX_ORDINALS = "一二三四五六七八九十"


def _cx_heading(i: int) -> str:
    """质询作答标题：``### 质询一`` …；超出十用阿拉伯数字。"""
    label = _CX_ORDINALS[i - 1] if 1 <= i <= len(_CX_ORDINALS) else str(i)
    return f"### 质询{label}"


def cx_answer_feedback(
    config: DebateConfig,
    side: DebateSide,
    round_no: int,
    focus: str,
    questions: Sequence[str],
) -> str:
    """质询【检索阶段】feedback：必答质询清单 + 证据笔记交付物。"""
    n = len(questions)
    numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, start=1))
    return (
        f"## 第 {round_no} 轮 · 质询环节（本轮焦点：{focus}）\n"
        f"{role_directive(config, side)}\n\n"
        "主持人代表交锋，向你发出以下【必须正面回答】的质询。"
        f"请为作答做必要补证（优先基于已有调研与辩论材料；需要补证就查，"
        f"同一个查不到的事实别反复空搜），然后产出【证据笔记】（共 {n} 条质询）：\n\n"
        f"质询列表（共 {n} 条）：\n{numbered}"
        f"{EVIDENCE_NOTES_SPEC}"
    )


def cx_draft_brief(
    config: DebateConfig,
    side: DebateSide,
    round_no: int,
    focus: str,
    questions: Sequence[str],
) -> str:
    """质询【成稿】brief：markdown 标题体逐条作答。"""
    n = len(questions)
    numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, start=1))
    skeleton = "\n\n".join(
        f"{_cx_heading(i)}\n对该条的正面回答（先表态再论证）……" for i in range(1, n + 1)
    )
    return (
        f"## 第 {round_no} 轮 · 质询环节（本轮焦点：{focus}）\n"
        f"{role_directive(config, side)}\n\n"
        "主持人代表交锋，向你发出以下【必须正面回答】的质询。请按下方标题**逐条正面作答**"
        f"（共 {n} 条，与质询列表一一对应；直接以第一个 ``### 质询…`` 标题开头，"
        "不要寒暄前言、不要输出 JSON 或代码围栏）：\n\n"
        f"{skeleton}\n\n"
        "作答要求：\n"
        "- 每条先用「是 / 否 / 部分成立」明确表态，再用具体证据或推理支撑；\n"
        "- 凡涉及具体事实的前提都按【证据状态铁律】标注【已核实·#eN】/【待核实·推断】，"
        "拿不出台账 id 就诚实标【待核实·推断】、别含糊带过或硬拗成已核实；\n"
        "- 若该认输 / 让步就坦诚承认，别答非所问、打太极或复述已说过的立论来回避；\n"
        f"- {CX_LENGTH_HINT}\n\n"
        f"质询列表（共 {n} 条）：\n{numbered}"
    )


def cx_completion_feedback(questions: Sequence[str], prior_answer: str) -> str:
    """质询作答悬垂时的【一次补全】feedback（禁再检索，只续写收束）。"""
    n = len(questions)
    numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, start=1))
    tail = (prior_answer or "").rstrip()
    tail_preview = tail[-240:] if len(tail) > 240 else tail
    return (
        f"## 质询作答补全（共 {n} 条须全部写完）\n"
        "你上一轮质询作答在句末【悬垂截断】（停在冒号 / 未闭合列表 /「理由是」等引导语后）。"
        "请从截断处【续写补全】，把未写完的那一条（及若有尚未作答的后续条目）写到完整句子收束；"
        "保留已有 ``### 质询…`` 标题体，不要寒暄、不要重复已写完的完整条目、不要新开检索。\n\n"
        f"质询列表（共 {n} 条）：\n{numbered}\n\n"
        f"截断处原文尾部：\n…{tail_preview}"
    )


def cx_completion_brief(questions: Sequence[str], prior_answer: str) -> str:
    """质询作答悬垂时的成稿 brief（与 :func:`cx_completion_feedback` 同情境）。"""
    return cx_completion_feedback(questions, prior_answer)



def round_context_blocks(
    config: DebateConfig,
    side: DebateSide,
    round_no: int,
    focus: str,
    last_round: RoundResult,
    feedback: str,
    interjections: Sequence[UserInterjection] = (),
) -> list[ContextBlock]:
    """后续轮 continue_run 的【收到的上下文】展示投影（上下文传递可视化）。

    首块 ``channel=task`` 的 ``body`` **逐字复用** 展示用 feedback（成稿 brief，用户看见的发言任务），
    与 LLM 检索阶段指令同源情境、不同交付物说明。其后为浓缩孪生材料块。"""
    blocks: list[ContextBlock] = [
        ContextBlock(channel="task", heading=f"第 {round_no} 轮任务", body=feedback),
        ContextBlock(channel="round_focus", heading=f"第 {round_no} 轮 · 本轮焦点", body=focus),
    ]
    mine = _interjection_mine(side, interjections)
    if mine:
        directed = any(i.target_key == side.key for i in mine)
        who = "向你" if directed else "向全场"
        blocks.append(
            ContextBlock(
                channel="interjection",
                heading=f"用户本轮追问（{who}提出 · 最高优先级）",
                body="\n".join(f"- {i.ask}" for i in mine),
            )
        )
    opponents = [t for t in last_round.ok_turns if t.side_key != side.key]
    if opponents:
        for t in opponents:
            over = len(t.content.strip()) > _OPP_CLIP
            blocks.append(
                ContextBlock(
                    channel="opponent",
                    heading=f"对方上一轮 · {t.side_name}",
                    body=_clip(t.content),
                    source_role=t.side_name,
                    source_run_id=t.run_id,
                    fidelity="summarize" if over else "",
                    truncated=over,
                )
            )
    else:
        blocks.append(
            ContextBlock(channel="opponent", heading="对方上一轮", body="（对方上一轮无有效发言）")
        )
    challenged = _challenged_lines(config, side, last_round)
    if challenged:
        blocks.append(
            ContextBlock(channel="challenge", heading="上一轮你被反驳的命门", body=challenged)
        )
    return blocks


def cx_context_blocks(
    round_no: int,
    questions: Sequence[str],
    feedback: str,
) -> list[ContextBlock]:
    """质询环节 continue_run 的【收到的上下文】展示投影。

    首块 ``channel=task`` 的 ``body`` **逐字复用** 展示用 feedback（成稿 brief）；
    其后保留 ``cross_exam`` 问题清单块（前端靠通道 presence 判 beat / chip 标签）。"""
    return [
        ContextBlock(channel="task", heading="质询环节", body=feedback),
        ContextBlock(
            channel="cross_exam",
            heading=f"第 {round_no} 轮 · 质询（必须正面回答）",
            body="\n".join(f"- {q}" for q in questions),
        ),
    ]


# 结辩通道块的环节标记（纯 presence / chip 标签用）：真实指令走 task 块复用 closing_task，
# 本句不再复述胜负手 / 禁新论据等约束（那些只在 feedback / task.body 里出现一次）。
_CLOSING_CONTEXT_BODY = "本场辩论已充分交锋，现请做结辩陈词。"


def _closing_own_argument_lines(
    rounds: Sequence[RoundResult], side: DebateSide
) -> str:
    """本方历轮论点（标题 + 要点），裁剪封顶；无有效论点时返回空串。"""
    lines: list[str] = []
    total = 0
    for rr in rounds:
        turn = next((t for t in rr.ok_turns if t.side_key == side.key), None)
        if turn is None:
            continue
        args = list(turn.arguments or [])
        if not args and turn.content.strip():
            args = [a.to_payload() for a in parse_speech_arguments(turn.content)]
        for arg in args:
            if len(lines) >= _CLOSING_ARGS_MAX:
                break
            title = (arg.get("title") or "").strip() or "（无标题）"
            body = _clip((arg.get("body") or "").strip(), _CLOSING_POINT_CLIP)
            if not body:
                continue
            line = f"- 【第{rr.round_no}轮】{title}：{body}"
            if total + len(line) > _CLOSING_ARGS_TOTAL and lines:
                break
            lines.append(line)
            total += len(line)
        if len(lines) >= _CLOSING_ARGS_MAX or total >= _CLOSING_ARGS_TOTAL:
            break
    return "\n".join(lines)


def _closing_concession_lines(
    rounds: Sequence[RoundResult], side: DebateSide
) -> str:
    """本方质询作答中带让步迹象的问答摘要；无则空串。"""
    lines: list[str] = []
    for rr in rounds:
        for cx in rr.cross_exam:
            if cx.target != side.key:
                continue
            for qa in cx.exchanges:
                answer = (qa.answer or "").strip()
                if not answer or not _CONCESSION_HINT_RE.search(answer):
                    continue
                q = _clip((qa.question or "").strip(), _CLOSING_CONCESSION_CLIP)
                a = _clip(answer, _CLOSING_CONCESSION_CLIP)
                lines.append(f"- 问：{q}\n  答（含让步）：{a}")
                if len(lines) >= _CLOSING_CONCESSIONS_MAX:
                    return "\n".join(lines)
    return "\n".join(lines)


def _closing_clash_lines(
    config: DebateConfig, side: DebateSide, rounds: Sequence[RoundResult]
) -> str:
    """对方对本方的 clash 命门（跨轮汇总）；无则空串。与续轮 challenge 同源渲染。"""
    names = {s.key: s.name for s in config.sides}
    lines: list[str] = []
    seen: set[str] = set()
    for rr in rounds:
        for c in rr.verdict.clashes:
            if c.to_key != side.key:
                continue
            point = (c.point or "").strip()
            if not point:
                continue
            key = f"{c.from_key}:{point}"
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- {names.get(c.from_key, c.from_key)}：{point}")
    return "\n".join(lines)


def _closing_materials(
    config: DebateConfig, side: DebateSide, rounds: Sequence[RoundResult]
) -> tuple[str, str, str]:
    """结辩三类材料正文（论点 / 让步 / 命门），供 brief 与展示投影同源读取。"""
    return (
        _closing_own_argument_lines(rounds, side),
        _closing_concession_lines(rounds, side),
        _closing_clash_lines(config, side, rounds),
    )


def closing_task(
    config: DebateConfig,
    side: DebateSide,
    rounds: Sequence[RoundResult] = (),
) -> str:
    """结辩成稿 brief（结辩禁新论据 → 退化为单次成稿，无检索阶段）。

    与 :func:`round_draft_brief` 同构：指令 + 本场材料（本方历轮论点 / 质询让步 /
    对方对本方 clash / 对局台账）。成稿走干净上下文，材料只经本 brief 携带，不读 session transcript。
    """
    own_args, concessions, clashes = _closing_materials(config, side, rounds)
    material_parts: list[str] = []
    ledger = accumulate_match_ledger(rounds)
    ledger_block = format_match_ledger_block(
        ledger, side_names={s.key: s.name for s in config.sides}
    )
    if ledger_block:
        material_parts.append(ledger_block.rstrip())
    if own_args:
        material_parts.append(f"【本方历轮论点（标题+要点，结辩只准收束这些）】\n{own_args}")
    if concessions:
        material_parts.append(
            f"【本方在交叉质询中做过的关键让步（结辩不得翻供）】\n{concessions}"
        )
    if clashes:
        material_parts.append(f"【对方对本方的交锋命门（须正面收束）】\n{clashes}")
    materials = ("\n\n".join(material_parts) + "\n\n") if material_parts else ""
    return (
        f"## 结辩环节（本场辩论已充分交锋，现在请你做【结辩陈词】）\n"
        f"{role_directive(config, side)}\n\n"
        f"{materials}"
        "这是你的**最后陈词**，不是新一轮立论——请【只讲胜负手】：\n"
        "- 你这一方最强的 1–2 个论点，为何它们站得住；\n"
        "- 对方针对你最关键的那条反驳，为何【不成立 / 已被你回应】。\n"
        "【不得引入任何新论据 / 新事实 / 新案例】、不复述你之前的全文、不逐条罗列改动；"
        "结辩里引用的既有事实沿用你此前的证据状态标记（不把待核实的东西临门包装成已核实当胜负手）；"
        "【已核实·#eN】只能沿用本方历轮发言 / 笔记中已出现过的 id，禁止临门臆造新 id、盲配未用过的台账号或自由出处短语；"
        "已撤回论据禁止再当胜负手。"
        f"{CLOSING_LENGTH_HINT}\n\n"
        "直接输出你的结辩陈词。"
    )


def closing_context_blocks(
    config: DebateConfig,
    side: DebateSide,
    feedback: str,
    rounds: Sequence[RoundResult] = (),
) -> list[ContextBlock]:
    """结辩环节 continue_run 的【收到的上下文】展示投影（上下文传递可视化）。

    首块 ``channel=task`` 的 ``body`` **逐字复用** :func:`closing_task` 返回值（投喂==展示）。
    其后保留 ``closing`` 通道块作 beat 标记；材料孪生块与 brief 同源（论点 / 让步 / 命门），
    复用既有 ``history`` / ``cross_exam`` / ``challenge`` 通道（不新增 wire channel）。
    """
    own_args, concessions, clashes = _closing_materials(config, side, rounds)
    blocks: list[ContextBlock] = [
        ContextBlock(channel="task", heading="结辩环节", body=feedback),
        ContextBlock(channel="closing", heading="结辩环节", body=_CLOSING_CONTEXT_BODY),
    ]
    if own_args:
        over = len(own_args) >= _CLOSING_ARGS_TOTAL
        blocks.append(
            ContextBlock(
                channel="history",
                heading="本方历轮论点",
                body=own_args,
                fidelity="summarize" if over else "",
                truncated=over,
            )
        )
    if concessions:
        blocks.append(
            ContextBlock(
                channel="cross_exam",
                heading="本方质询让步（结辩不得翻供）",
                body=concessions,
            )
        )
    if clashes:
        blocks.append(
            ContextBlock(
                channel="challenge",
                heading="对方对本方的交锋命门",
                body=clashes,
            )
        )
    return blocks
