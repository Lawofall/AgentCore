"""辩论编排数据模型 —— 主持人（Moderator）循环的类型地基。

把辩论从「`delegate` 上的 stance/round 展示标记 + CEO 手搓跨轮 DAG」重设计为「主持人
驱动、过程与结论双产物」的能力（见 docs/03-AI核心/辩论编排设计.md）。本模块只定形状：

- :class:`DebateForm` / :class:`DebateSide` / :class:`DebateConfig` —— 一场辩论的配置（三
  形态统一模型：参与方泛化为「立场标签」，破二元 pro/con）。
- :class:`SideTurn` / :class:`JudgeVerdict` / :class:`RoundResult` —— 一轮的产物（各方发
  言 + 收敛裁判 + 主持人小结），是过程产物「交锋叙事线」的逐轮单元。
- :class:`DebateBrief` / :class:`DebateResult` —— 双产物（决策简报 + 叙事线），主持人交回
  CEO 收尾的最终交付。
- :class:`RoundRunner` —— 主持人「派一轮辩手发言」的注入接口；真实实现用现有
  ``build_agent_executor`` / ``continue_run`` 执行，单测注入 fake 零成本驱动循环。

本模块刻意 import-light（只依赖标准库），让编排循环与裁判逻辑可脱离 LLM / 执行器单测。

→ 见设计: docs/03-AI核心/辩论编排设计.md
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol

# 交接清单条目的解决路径分类（辩论编排设计.md §4.1）：证据能闭合 → fact；
# 只有用户价值观/偏好能闭合 → value；两者都闭合不了 → question。
HandoffKind = Literal["value", "fact", "question"]
HANDOFF_KINDS: frozenset[str] = frozenset({"value", "fact", "question"})


def normalize_handoff_kind(raw: str) -> HandoffKind:
    """坏 / 未知 kind 归 ``question``，不丢内容（解析容错铁律）。"""
    token = (raw or "").strip().lower()
    if token in ("value", "fact", "question"):
        return token  # type: ignore[return-value]
    return "question"

# ── 轮次治理默认（辩论编排设计.md §五） ──────────────────────────────────────
# 轮数永不暴露给用户设定：用户只选形态，收敛由主持人逐轮自判（无最小轮门槛强制多轮）。这些
# 是纯「安全上限」（防失控的断路器），不是目标值——收敛永远可早于它发生。
DEFAULT_MAX_ROUNDS = 5  # 安全上限（正反/红队）：达到即停（防失控兜底，非目标）
DEFAULT_MAX_ROUNDS_QUICK = 1  # 「快速对碰」上限：单轮即收
# 圆桌（探讨型）上限略紧于辩论（铺满观点光谱即可，无需正反那种对抗强度的轮数）。
DEFAULT_MAX_ROUNDS_ROUNDTABLE = 4


class DebateForm(StrEnum):
    """辩论的三形态（辩论编排设计.md §三）——一套主持人循环参数化而成，非独立执行路径。"""

    DEBATE = "debate"  # 正反辩论：正/反 2 方对称攻防
    RED_TEAM = "red_team"  # 红队挑刺：被审方案 + 1~N 红队单向攻击 → 方案方回应
    ROUNDTABLE = "roundtable"  # 多方圆桌：3~N 个视角多边碰撞


@dataclass(frozen=True)
class DebateSide:
    """一个参与方 —— 把 pro/con 泛化为「立场标签」（辩论编排设计.md §三）。

    ``key`` 是机器标识（用于 run_id / 前端分桶 / 跨轮续写定位），``name`` 是展示名
    （正方 / 红队A / 经济学视角），``stance`` 是喂给辩手的立场定位（拼进它的角色补充）。
    ``is_subject`` 标记红队形态里那个「被审方案方」（单向攻击的承受方），其余形态恒 False。

    模型身份（Phase 3 · 真·多模型辩手，§7.5）为三元组：``model`` + ``origin``
    （platform|byok）+ ``provider_id``（byok 必填）。空 ``model`` = 回退 turn 主模型；
    非空须过目录校验后注入路由键。见 ``runtime.debate.models``。

    ``run_id`` 是开赛前预分配的稳定槽位 id（开工卡 wire / ``model_overrides`` 键）；
    与各拍发言 run（``{moderator}_rN_{key}``）不同。空 = 旧帧 / 未分配。
    """

    key: str
    name: str
    stance: str
    is_subject: bool = False
    model: str = ""
    origin: str = ""  # platform | byok | ""
    provider_id: str = ""
    run_id: str = ""


@dataclass(frozen=True)
class RoundPolicy:
    """轮次治理参数（辩论编排设计.md §五）。

    收敛由主持人【每轮自判】（:meth:`Moderator._judge` 的 ``converged``）决定——本类【不再设
    最小轮门槛】强制多轮（旧法的机械楼层不看内容、把 trivial 命题也逼满 N 轮、产出冗余「修订
    v2」）。「别过早收敛」的智慧已搬进裁判的逐轮标准（第 1 轮开场默认继续、除非命题空泛无可
    再辩），不再靠外部计数兜底。

    ``thorough`` 是喂给裁判的【深度偏好】：True=盯住决定性分歧往深里辩、逼到分出高下或见底成
    价值选择才收（不是把每个角度都辩一遍），False=核心交锋清晰即收；``max_rounds`` 是纯【安全
    上限】（防失控的断路器，非目标值、罕见兜底），收敛永远可早于它发生。
    """

    thorough: bool = True
    max_rounds: int = DEFAULT_MAX_ROUNDS

    def __post_init__(self) -> None:
        # 安全上限至少 1 轮（否则循环空转）。
        object.__setattr__(self, "max_rounds", max(1, self.max_rounds))

    @classmethod
    def quick(cls) -> RoundPolicy:
        """「快速对碰」：单轮即收（上限 1）——裁判一次对碰即判收敛，不强制多轮。"""
        return cls(thorough=False, max_rounds=DEFAULT_MAX_ROUNDS_QUICK)

    @classmethod
    def for_form(cls, form: DebateForm, *, thorough: bool = True) -> RoundPolicy:
        """形态默认 policy。

        ``thorough=False`` 对**所有形态**（含圆桌）一律快速单轮——「测试一下 / 简单看看 / 随便
        聊聊」不该被强制多轮（旧实现圆桌恒多轮、忽略 ``thorough``，trivial 命题也产出冗余「修订
        v2」）。``thorough=True`` 时圆桌探讨上限略紧（铺光谱即可）、正反/红队认真辩透；轮数仍由
        主持人逐轮自判收敛，``max_rounds`` 只是安全上限。"""
        if not thorough:
            return cls.quick()
        if form is DebateForm.ROUNDTABLE:
            return cls(thorough=True, max_rounds=DEFAULT_MAX_ROUNDS_ROUNDTABLE)
        return cls(thorough=True, max_rounds=DEFAULT_MAX_ROUNDS)


@dataclass
class DebateConfig:
    """一场辩论的完整配置 —— 用户只抛「问题」或选「形态」，参与方/轮数由系统定。

    ``motion`` 是辩论命题（用户问题）；``sides`` 是泛化后的参与方（≥2）；``policy`` 收敛/轮
    次治理。辩手与主持人均跑统一 turn model（无质量档）。
    """

    motion: str
    form: DebateForm
    sides: list[DebateSide]
    policy: RoundPolicy = field(default_factory=RoundPolicy)
    # 可选案件底料（CEO 发起前已核实的客观事实清单）。空串 = 未传，首轮 debater_task
    # 零行为变化；非空时仅首轮以主持人名义喂给全部辩手（后续轮靠 session 记忆，不重复注入）。
    background: str = ""
    # 开赛嘱咐（开工卡 CONTINUE+note）——内部字段，非 wire。非空时作首轮全场定向
    # 用户插话：主持人定首轮焦点可见、首轮辩手 prompt 可见、verbatim 进 rounds[0].user_interjections。
    # 不覆写 motion / 不改 sides。
    kickoff_ask: str = ""
    # 工作区 AgentCore/文档/research/ 约定文档文件索引（开工时机制性探测后填入；空串 = 无约定文档，不注入）。
    # 仅文本通道：辩手底料 / 主持人议题 brief；非 wire 事件字段。
    research_dossier_index: str = ""
    # 庭前取证已汇流（§二之二）：True → 首轮辩手检索预算按有约定文档下调，引用台账为主。
    pretrial_evidence_ready: bool = False
    # 共享证据包（附件已在主持人上下文时由庭前组装；非 wire 必填；空 = 未走 pack 路径）。
    evidence_pack: Any | None = None
    # 庭前证据完整度（一等公民）：full / partial / empty；非 full 时主持人 frame / 辩手须显式感知缺口。
    evidence_completeness: Literal["full", "partial", "empty"] = "full"
    # 庭前解析后的辩手 per-side 检索预算（None 键不写 = 沿用约定文档残搜旧路径；0 = 禁外证）。
    debater_retrieval_budgets: dict[str, int] = field(default_factory=dict)
    # 外证计划观测：庭前舰队已删后恒为 skip + reason（非 wire 必填）。
    external_evidence_mode: str = ""
    external_evidence_reason: str = ""
    # 裁判选型（§7.5）：开赛前 resolve；用户点名优先，可与辩手同模；route 写入主持人 LLM 调用。
    moderator_model: str = ""
    moderator_origin: str = ""
    moderator_provider_id: str = ""
    moderator_route: str = ""
    # 开赛前预分配的主持人稳定 run_id（开工卡 / model_overrides 键；开赛后沿用，不重铸）。
    moderator_run_id: str = ""
    # True = 目录只剩一模型，本场降级同模型并在开赛卡明示。
    same_model_debate: bool = False
    # §7.5 D：消歧零/多候选时挂结构化候选（开赛卡 / 工具错误载荷）；旧帧缺省空。
    model_candidates: list[dict[str, Any]] = field(default_factory=list)

    @property
    def subject_side(self) -> DebateSide | None:
        """红队形态里的「被审方案方」（其余形态返回 None）。"""
        return next((s for s in self.sides if s.is_subject), None)


# 发言 beat（轮内拓扑）：正反默认 statement；红队攻/应/复；圆桌线程 turn / crux 短答。
# 缺字段（老 journal）→ 前端按 statement。
SpeechBeat = Literal["statement", "attack", "defense", "rebuttal", "thread", "crux"]


@dataclass
class SideTurn:
    """某方在某一轮的发言产物 —— 叙事线 L3「论点级全文」的承载单元。

    ``ok`` 标记该方本轮是否成功产出（辩手 run 失败 / 空产出时 False）：裁判与小结基于成功
    发言进行，全员失败则主持人提前终止（出降级简报）。``absent`` 是部分失败时的一等语义：
    网关重试耗尽仍无发言、但同轮仍有他方成功 → 该方标缺席（跳过对其质询与对抗记分），
    赛程继续；全员失败走 ``all_failed`` 早停，不标 ``absent``。``run_id`` 让前端把发言挂到
    图节点、并供跨轮续写定位同一辩手。``arguments`` 为后端解析的论点大纲（进 SSE 载荷；
    全文仍随 run 事件走）。

    ``beat`` 区分轮内多拍（红队攻/应/复、圆桌线程）——正反攻防恒 ``statement``（零回归）；
    同一 ``side_key`` 可在 ``RoundResult.turns`` 出现多条（每拍一条）。
    """

    side_key: str
    side_name: str
    run_id: str
    content: str
    ok: bool = True
    absent: bool = False
    # 结构化论点大纲 ``[{id, title, body}]``；失败 / 空产出恒空。缺字段（老 journal）→ 前端回退。
    arguments: list[dict[str, str]] = field(default_factory=list)
    beat: SpeechBeat = "statement"


@dataclass(frozen=True)
class DebateClash:
    """论点级交锋边（叙事线 L3「谁驳谁」）—— 裁判逐轮抽取的针锋相对关系。

    ``from_key`` 一方针对性反驳了 ``to_key`` 一方，``point`` 是这条反驳的要点（一句话）。
    ``from_key``/``to_key`` 是 :class:`DebateSide` 的 ``key``（语义键，非 run_id）。只抽**真正
    针锋相对**的边（各说各话不算），让前端把「平铺发言」升级为可读的交锋图（而非靠用户脑补）。
    """

    from_key: str
    to_key: str
    point: str


class LedgerEventKind(StrEnum):
    """对局台账事件种类（P0 对局记忆）——只收发言里【显式】发生的状态变化，宁缺勿滥。

    服务端内部流转；不上 ``debate_round`` / ``debate_result`` wire。
    """

    WITHDRAWAL = "withdrawal"  # 撤回：明确收回某论据 / 数据
    CORRECTION = "correction"  # 更正：用新值替换旧主张
    DISPUTED_FACT = "disputed_fact"  # 争议事实：双方对同一事实给出冲突的【已核实】或明确分歧
    CONCESSION = "concession"  # 关键让步：正面承认弱点 / 抗辩不成立等


@dataclass(frozen=True)
class LedgerEvent:
    """对局台账一条事件 —— 裁判从本轮立论 + 质询问答中提取。

    ``side`` 为当事方 ``side_key``；争议事实可为空（描述双方分歧时）。``content`` 一句话。
    ``round_no`` 由主持人写入累积时标注来源轮。不上 SSE 契约。
    """

    kind: LedgerEventKind
    side: str
    content: str
    round_no: int = 0


@dataclass
class CrossExamQa:
    """质询环节的一条 Q↔A（质询回合 P1 最小单元）。

    ``question`` verbatim 进 SSE payload；``answer`` 为从辩手作答中解析出的该条摘要（完整流仍随
    ``answer_run_id`` 的 run 事件走）。是否正面回应 / 回避由裁判据原文裁定（engagement +
    ``brief.decisive``），本结构不携带二元褒贬字段。
    """

    question: str
    answer: str = ""


@dataclass
class CrossExamExchange:
    """一轮【质询环节】对某方的逐条交换组（质询回合，辩论编排设计.md §4-2.1）。

    质询环节由主持人代表交锋、向某方（``target``= :class:`DebateSide` 的 key）发出【必须正面回答】
    的尖锐质询（``exchanges``，通常 2–3 条、可含是/否逼答），被质询方在【自己的 transcript】上逐条
    作答（``answer_run_id`` 挂到执行图节点、全文随 run 事件走）。``questioner`` 是提问方 side_key，
    空=主持人代表交锋（当前实现）——保留字段以便日后切到「辩手互相质询」而不改契约。质询问答随本轮
    :class:`RoundResult` 留痕并喂进裁判记分（答非所问 / 打太极 → 扣 engagement，可进 decisive
    点名；诚实认输 / 让步不算回避）——这正是「让交锋当面发生、把回避与被戳穿变成可记分」的落点。
    """

    target: str
    exchanges: list[CrossExamQa] = field(default_factory=list)
    answer_run_id: str = ""
    questioner: str = ""


@dataclass(frozen=True)
class WitnessSeatInfo:
    """本场证人席位（批 D1）——幕1 透镜调研员进入幕2 辩论的图上节点身份。"""

    key: str
    name: str
    lens_run_id: str
    seat_run_id: str
    lens_label: str = ""
    origin_caption: str = ""


@dataclass
class WitnessExamExchange:
    """一轮【证人答问】（批 D1）：主持人对证人的事实性点名 + 答问。

    对称于 :class:`CrossExamExchange`，但 ``witness_key`` 指向幕1 透镜 / 席位，不占辩席。
    答问全文随 ``answer_run_id`` 的 run 事件走；摘要进 ``exchanges``。
    """

    witness_key: str
    lens_run_id: str
    name: str
    exchanges: list[CrossExamQa] = field(default_factory=list)
    answer_run_id: str = ""
    seat_run_id: str = ""
    origin_caption: str = ""


class FindingSeverity(StrEnum):
    """红队 finding 严重度（提案 §3.2）——替换按方 ``risk_severities`` 的粒度。"""

    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class FindingStatus(StrEnum):
    """finding 生命周期：open → answered → closed / escalated / deadlocked；
    ``unanswered`` = 方案方回应拍失败（O7）。"""

    OPEN = "open"
    ANSWERED = "answered"
    CLOSED = "closed"
    ESCALATED = "escalated"
    DEADLOCKED = "deadlocked"
    UNANSWERED = "unanswered"


class FindingDisposition(StrEnum):
    """方案方对 finding 的处置口径（提案 §3.2 回应拍）。"""

    ACCEPT = "accept"
    MITIGATE = "mitigate"
    REBUT = "rebut"
    DEFER = "defer"


@dataclass
class Finding:
    """红队交互单元 —— 一条刺及其处置/复核状态（提案 §3.2）。

    wire 只带结构（id / status / severity / target / 各方 run_id，O2）；主张全文 / 处置理由 /
    复攻说明靠对应 ``*_run_id`` 关联执行事件。``claim`` 等全文仅服务端内部（裁判 / 注入）。
    """

    id: str
    severity: FindingSeverity
    target: str  # 指向方案的哪个环节/假设
    attacker_key: str
    status: FindingStatus = FindingStatus.OPEN
    disposition: str = ""  # FindingDisposition value 或空
    attack_run_id: str = ""
    response_run_id: str = ""
    rebuttal_run_id: str = ""
    claim: str = ""  # 内部全文，不上 wire
    response_note: str = ""  # 内部
    rebuttal_note: str = ""  # 内部
    merged_from: list[str] = field(default_factory=list)  # O5 被合并的源 id

    def to_wire(self) -> dict:
        """O2：只带结构；全文靠 run_id。"""
        return {
            "id": self.id,
            "severity": self.severity.value if isinstance(self.severity, FindingSeverity) else str(self.severity),
            "target": self.target,
            "attacker_key": self.attacker_key,
            "status": self.status.value if isinstance(self.status, FindingStatus) else str(self.status),
            "disposition": self.disposition,
            "attack_run_id": self.attack_run_id,
            "response_run_id": self.response_run_id,
            "rebuttal_run_id": self.rebuttal_run_id,
            "merged_from": list(self.merged_from),
        }


@dataclass
class ThreadTurn:
    """圆桌交互单元 —— 线程内一次发言（提案 §3.3）。

    wire 只带 speaker / reply_to / run_id / ok（O2）；全文靠 run_id。
    """

    speaker: str
    run_id: str
    reply_to: str = ""  # 空 = 开题
    ok: bool = True
    content: str = ""  # 内部全文
    beat: SpeechBeat = "thread"

    def to_wire(self) -> dict:
        return {
            "speaker": self.speaker,
            "reply_to": self.reply_to,
            "run_id": self.run_id,
            "ok": self.ok,
            "beat": self.beat,
        }


class GateDecision(StrEnum):
    """红队门决（提案 §3.2）——收场产物，非比分。"""

    CONDITIONAL_PASS = "conditional_pass"
    NEEDS_MAJOR_REWORK = "needs_major_rework"
    NOT_VIABLE = "not_viable"


@dataclass
class ConsensusMapItem:
    """圆桌子题级共识/分歧地图条目（提案 §3.3）。"""

    topic: str
    consensus: list[str] = field(default_factory=list)
    divergences: list[str] = field(default_factory=list)
    crux: str = ""  # 分歧驱动：事实 / 价值 / 假设

    def to_wire(self) -> dict:
        return {
            "topic": self.topic,
            "consensus": list(self.consensus),
            "divergences": list(self.divergences),
            "crux": self.crux,
        }


@dataclass
class ClosingStatement:
    """某方的【结辩陈词】（阶段化发言角色 P4 · 结辩收束，辩论编排设计.md §4-2.4 契约④）。

    辩已辩尽（收敛 / 用户 conclude / 达上限）后、简报前，主持人请各方做一段收尾陈词：辩手经
    ``continue_run`` 走【干净成稿】（``allow_research=False``），brief 携带本场材料（历轮论点 /
    质询让步 / clash 命门），**只讲胜负手**（本方最强 1–2 点 + 为何对方最关键的反驳不成立）、
    **不得引入新论据 / 新事实**、长度收紧（见 :data:`~agentcore.tools.builtin.debate.
    schema.CLOSING_LENGTH_HINT`）。全文随 ``run_id`` 的 run 事件走（不塞 payload，与各方发言 / 质询作答
    同策），``ok`` 标记是否成功产出。收场后**一次性**发生（非逐轮），供前端「结辩」区渲染——这一层是
    辩手自己的 advocacy 收尾，与裁判中立的 ``brief.decisive`` 正交并存（真人辩论：结辩 + 裁决并存）。
    仅【认真辩透 + 对抗形态】开启；未开启 / 快速对碰 / 圆桌恒空，零行为变化。
    """

    side_key: str
    side_name: str
    run_id: str
    content: str = ""
    ok: bool = True


@dataclass(frozen=True)
class UserInterjection:
    """直播中用户向某轮辩论注入的「追问」—— verbatim 复盘单元（辩论编排设计.md §逐轮交互 / 交锋
    叙事直播态设计 Phase 2）。

    用户在第 N 轮边界选 ``CONTINUE`` 时可附带一个问题（``ask``），可选定向某方（``target_key``
    = :class:`DebateSide` 的 key，空=问全场）。该追问被注入【下一轮】辩手 prompt（见
    :func:`round_feedback`）令其正面回应——故它逻辑上归属「被它驱动的那一轮」（round N+1）。
    ``answered`` 记录【结构事实】：是否真有后续轮跑起来承接它（追问即续辩，正常恒 True；若在轮数
    上限边界追问、其后无轮，或紧接超时/异常无下一轮，则 False）——非「答得好不好」的语义判断。

    这是收场复盘侧的耐久追问痕迹：随 ``RoundResult`` 进
    ``debate_result.rounds[*].user_interjections``，重载后复盘可见（逐轮决策事件本身
    D3 起亦 DURABLE 入 journal，但只承载 decision/focus——verbatim 追问以本结构为准）。
    """

    ask: str
    target_key: str = ""
    answered: bool = False


class RoundDecision(StrEnum):
    """用户在一轮辩论边界的抉择（ambient 掌舵，辩论编排设计.md §六）。

    辩论默认由裁判逐轮自判收敛、永不硬停；有活跃用户时掌舵恒可用——用户随时 fire-and-forget
    送入 steer 队列，主持人在下一轮边界非阻塞捞起：``CONTINUE`` 再辩一轮（``RoundBoundary.focus``
    留空=主持人自动定下一轮焦点，非空=用户「加的角度」覆写焦点），``CONCLUDE`` 在该边界出结论
    （即便裁判未判收敛；当前轮先跑完，不腰斩）。无第三态——v1 不做「加辩方 / 换辩手」。
    空队列回落裁判自动收敛（见 Moderator.run）。
    """

    CONTINUE = "continue"
    CONCLUDE = "conclude"


@dataclass(frozen=True)
class RoundBoundary:
    """一轮边界的处置 —— :class:`RoundDecision` + 可选的下一轮焦点覆写（「加角度」）+ 追问。

    ``focus`` 仅在 ``decision is CONTINUE`` 且非空时生效：作为下一轮的议题覆写主持人自动定焦点
    （用户把辩论引向自己在意的维度=「引导」）；``CONCLUDE`` 时忽略。

    ``ask`` 是用户的【追问】（与 ``focus`` 正交：焦点改的是议题，追问是一个要辩手正面回答的问题）：
    非空时注入【下一轮】辩手 prompt 令其回应（``ask_target`` 指定方 key，空=问全场），并作为
    :class:`UserInterjection` 随下一轮 :class:`RoundResult` 留痕复盘。追问即续辩，故仅 ``CONTINUE``
    时被承接；``CONCLUDE`` 时若仍带 ``ask`` 则记为未应答（无后续轮）。

    主持人收到 ``None``（未接钩子 / 空 steer 队列）则回退到裁判的自动收敛判定。
    """

    decision: RoundDecision
    focus: str = ""
    ask: str = ""
    ask_target: str = ""


@dataclass
class RoundScore:
    """某方在某一轮的【记分】（记分裁判，辩论编排设计.md §4-2.2）。

    裁判在**辩论领域内**给各方本轮打分（不是判「谁文笔好」的通用质量门，见设计 §二 / 提案 §2.3）：
    ``argument`` 论点强度、``engagement`` 回应完整度（是否正面回应对方命门与质询；诚实认输 /
    让步不算回避，答非所问 / 打太极才压低）、
    ``evidence`` 证据充分度，各 0–5；``penalties`` 记本轮的谬误与未支撑主张（每条一句话，如「循环论证：
    拿未生效判决当论据」），每条计 -1；``note`` 一句话记分理由。收场倾向由逐轮记分累计推导
    （:func:`tally_scores`），而非收场一次性拍脑袋——让 leaning 与实际交锋对齐。
    """

    argument: int = 0
    engagement: int = 0
    evidence: int = 0
    penalties: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def total(self) -> int:
        """本轮净得分：三维之和减去谬误 / 无据的罚分（每条 -1），可为负。"""
        return self.argument + self.engagement + self.evidence - len(self.penalties)


@dataclass
class JudgeVerdict:
    """收敛裁判结果（辩论编排设计.md §二 第3步 + §五）。

    主持人的「裁判」是**辩论领域内**的交锋质量与收敛判定（非通用产物质量门，见设计 §二）：
    ``real_clash`` 各方是否真针锋相对（而非各说各话）、``new_arguments`` 本轮是否还在产生新
    论点。``converged`` 是裁判判「可终止」——主持人循环【直接据此收场】（无最小轮门槛二次约束，
    「别过早收敛」已内化进裁判的逐轮标准）；终止时 ``stop_reason`` 取 :data:`STOP_REASONS` 之一，
    继续时 ``next_focus`` 给下一轮焦点。

    ``scores`` 是本轮【记分裁判】的各方得分（side_key → :class:`RoundScore`，记分裁判 P2）：与收敛
    判定同一遍推理产出，逐轮累计后驱动收场倾向。空 dict = 未开启记分（快速对碰 / 坏 JSON 容错 /
    未升级路径），此时行为逐字回退到「只判交锋与收敛」，零变化。

    ``ledger_events`` 是本轮提取的【对局台账】事件（P0 对局记忆）：撤回 / 更正 / 争议事实 /
    关键让步。主持人跨轮累积后注入下一轮辩手 brief/feedback；**不上 wire**（``to_event_payload``
    不带）。缺省 / 坏 JSON → 空列表。
    """

    real_clash: bool
    new_arguments: bool
    converged: bool
    stop_reason: str = ""
    next_focus: str = ""
    rationale: str = ""
    clashes: list[DebateClash] = field(default_factory=list)
    scores: dict[str, RoundScore] = field(default_factory=dict)
    ledger_events: list[LedgerEvent] = field(default_factory=list)


# 终止条件词表（辩论编排设计.md §五）——裁判判收敛时给出的归因，前端可据此呈现「为何收场」。
STOP_CONVERGED = "converged"  # 各方无实质新论点（开始重复）
STOP_FOCUS_CLARIFIED = "focus_clarified"  # 分歧已归结为价值/偏好之争（AI 判不了，交用户）
STOP_RED_TEAM_EXHAUSTED = "red_team_exhausted"  # 无新风险可挖（红队专用）
STOP_MAX_ROUNDS = "max_rounds"  # 达轮数硬上限（兜底，由循环而非裁判判定）
STOP_ALL_FAILED = "all_failed"  # 某轮全员发言失败，主持人提前终止
STOP_USER_CONCLUDED = "user_concluded"  # 交互式逐轮：用户在轮边界选择「够了，出结论」
STOP_REASONS = frozenset(
    {
        STOP_CONVERGED,
        STOP_FOCUS_CLARIFIED,
        STOP_RED_TEAM_EXHAUSTED,
        STOP_MAX_ROUNDS,
        STOP_ALL_FAILED,
        STOP_USER_CONCLUDED,
    }
)


@dataclass
class RoundResult:
    """一轮完整结果 —— 交锋叙事线 L2「逐轮攻防」的单元。

    ``focus`` 本轮议题（主持人第1步所定）；``turns`` 各方发言（L3 全文）；``verdict`` 收敛
    裁判（第3步）；``summary`` 主持人本轮小结（第4步，叙事线 L1「焦点小结流」的单元）。
    """

    round_no: int
    focus: str
    turns: list[SideTurn]
    verdict: JudgeVerdict
    summary: str = ""
    # 驱动本轮的用户追问（交互式逐轮，opt-in）：用户在【上一轮】边界注入、本轮辩手须正面回应的
    # 问题（verbatim 复盘单元）。非交互 / 无追问恒空。详见 :class:`UserInterjection`。
    user_interjections: list[UserInterjection] = field(default_factory=list)
    # 本轮【质询环节】的问答（质询回合 P1，opt-in：仅认真辩透 + 正反 DEBATE）。主持人代表交锋向
    # 各方发出必答质询、被质询方 continue_run 作答，喂进裁判记分。红队/圆桌/快速档恒空。
    cross_exam: list[CrossExamExchange] = field(default_factory=list)
    # 本轮【证人答问】（批 D1，additive）：主持人点名幕1 透镜证人的事实性问答。无证人 / 未点名恒空。
    witness_exam: list[WitnessExamExchange] = field(default_factory=list)
    # 红队 finding 台账增量（本轮合并后权威快照；对齐 evidence_ledger_delta 先例）。非红队恒空。
    findings: list[Finding] = field(default_factory=list)
    # 圆桌本轮线程 turn 序（点名串行 + 可选 crux）。非圆桌恒空。
    thread_turns: list[ThreadTurn] = field(default_factory=list)

    @property
    def ok_turns(self) -> list[SideTurn]:
        """本轮成功产出的发言（裁判 / 小结的输入）。"""
        return [t for t in self.turns if t.ok]

    def to_event_payload(self) -> dict:
        """本轮结构化为 SSE 事件 payload —— ``debate_round`` 事件与 :meth:`DebateResult.
        to_event_payload` 的逐轮单元共用此一处（单一源，防漂移）。

        承载叙事线一轮（focus / verdict / summary）+ 各方→辩手 ``run_id`` 映射；发言【全文】
        不在此（体量大、随辩手 run 走执行事件），靠 ``sides[*].run_id`` 关联执行图辩手节点。
        finding / 线程 turn 只带结构（O2）。
        """
        return {
            "round_no": self.round_no,
            "focus": self.focus,
            "summary": self.summary,
            "verdict": {
                "real_clash": self.verdict.real_clash,
                "new_arguments": self.verdict.new_arguments,
                "converged": self.verdict.converged,
                "stop_reason": self.verdict.stop_reason,
                "rationale": self.verdict.rationale,
            },
            "sides": [
                {
                    "key": t.side_key,
                    "name": t.side_name,
                    "run_id": t.run_id,
                    "ok": t.ok,
                    # 缺席轮一等语义：仅部分失败续赛时为 true；全员失败 / 成功方恒 false。
                    # 缺字段（老 journal）→ 前端按 false。
                    "absent": t.absent,
                    # 结构化论点大纲（后端 speech_parse）；缺字段（老 journal）→ 前端启发式回退。
                    "arguments": list(t.arguments or []),
                    # 轮内 beat；缺字段（老 journal）→ statement。
                    "beat": t.beat,
                }
                for t in self.turns
            ],
            # L3 论点级交锋边（谁驳谁）—— 裁判逐轮抽取，与 sides 平级（key 引 sides[*].key）。
            "clashes": [
                {"from_key": c.from_key, "to_key": c.to_key, "point": c.point}
                for c in self.verdict.clashes
            ],
            # 驱动本轮的用户追问（verbatim 复盘）：恒带（无追问为空列表），载荷形状统一。
            "user_interjections": [
                {"ask": i.ask, "target_key": i.target_key, "answered": i.answered}
                for i in self.user_interjections
            ],
            # 质询环节（质询回合 P1）：逐条 Q↔A verbatim 进载荷（answer 为解析摘要；完整流随
            # answer_run_id 的 run 事件走，与各方发言全文同策），恒带（无质询为空列表），载荷形状统一。
            "cross_exam": [
                {
                    "target": cx.target,
                    "questioner": cx.questioner,
                    "exchanges": [
                        {
                            "question": ex.question,
                            "answer": ex.answer,
                        }
                        for ex in cx.exchanges
                    ],
                    "answer_run_id": cx.answer_run_id,
                }
                for cx in self.cross_exam
            ],
            # 证人答问（批 D1，additive）：缺字段（老 journal）→ 前端按 []。
            "witness_exam": [
                {
                    "witness_key": wx.witness_key,
                    "lens_run_id": wx.lens_run_id,
                    "seat_run_id": wx.seat_run_id,
                    "name": wx.name,
                    "origin_caption": wx.origin_caption,
                    "exchanges": [
                        {"question": ex.question, "answer": ex.answer}
                        for ex in wx.exchanges
                    ],
                    "answer_run_id": wx.answer_run_id,
                }
                for wx in self.witness_exam
            ],
            # 记分裁判（P2）：本轮各方得分（side_key → 三维 + 罚分 + 净分），前端渲染逐轮比分条。
            # 与 verdict 平级（不塞进 verdict 子 dict，守其既有键集不漂移）；无记分为空 dict。
            "scores": {
                key: {
                    "argument": sc.argument,
                    "engagement": sc.engagement,
                    "evidence": sc.evidence,
                    "penalties": list(sc.penalties),
                    "note": sc.note,
                    "total": sc.total,
                }
                for key, sc in self.verdict.scores.items()
            },
            # 红队 finding 台账（结构 only，O2）；非红队 / 老载荷恒 []。
            "findings": [f.to_wire() for f in self.findings],
            # 圆桌线程 turn 序（结构 only）；非圆桌 / 老载荷恒 []。
            "thread_turns": [t.to_wire() for t in self.thread_turns],
        }


@dataclass(frozen=True)
class DebateHandoff:
    """交接清单条目 —— 按「解决路径」分类，交给用户收场后继续处理的一项。

    ``kind`` 定死三档：``value``（需你定夺）/ ``fact``（事实分歧）/ ``question``（待解问题）。
    关键事实的证据状态语（待核实 / 仅二手来源）内联在 ``text`` 里，不另开结构化字段。
    """

    kind: HandoffKind
    text: str


@dataclass
class DebateBrief:
    """决策简报 —— 结论产物（辩论编排设计.md §4.1）。

    辩论的「为决策负责到底」落点：不只把正反并排甩给用户，而是去水提炼 + 按解决路径分流的
    交接清单（``handoffs``）+ 给出带置信度的倾向判断。分类铁律：证据能闭合 → fact；只有用户
    价值观/偏好能闭合 → value；两者都闭合不了（等外部事件 / 预测验证 / 后续观察）→ question。
    """

    crux: str  # 争议焦点：双方真正分歧在哪
    strongest_points: dict[str, str] = field(default_factory=dict)  # side_key → 去水最强论点
    # 退役迁移（提案 §3.2）：按方 risk_severities → finding 粒度严重度。新场次恒空；旧载荷降级
    # 渲染仍可读本字段（兼容向量钉住）。
    risk_severities: dict[str, str] = field(default_factory=dict)
    # 红队 finding 台账权威快照（收场）+ 门决 / must-fix。非红队恒空 / 空串。
    findings: list[Finding] = field(default_factory=list)
    gate: str = ""  # GateDecision value
    must_fix: list[str] = field(default_factory=list)  # 未关闭 critical/major 的 finding id
    # 圆桌共识/分歧地图（按子题）。非圆桌恒空。
    consensus_map: list[ConsensusMapItem] = field(default_factory=list)
    # 交接清单（统一模型）：按解决路径分类的「留给你的」条目；旧三平行字段已退役。
    handoffs: list[DebateHandoff] = field(default_factory=list)
    # 胜负手（记分裁判 P2）：对抗形态下一句话点名【谁的哪个论点被 drop / 被证伪 / 无据 /
    # 或谁在质询中回避命门】，据此定倾向——让 leaning 由实际交锋记分驱动。诚实认输 / 让步
    # 不算回避。圆桌不裁胜负（记分仅 momentum），decisive 可空。红队可点名定门决的 finding。
    decisive: str = ""
    leaning: str = ""  # 主持人倾向性判断（圆桌=观点光谱小结，非裁赢家）
    confidence: str = ""  # 置信度（含成立条件，如「若你更看重 X 则反向」）
    recommendation: str = ""  # 给用户的建议


@dataclass
class DebateResult:
    """辩论总产物 —— 双产物（决策简报 + 交锋叙事线），主持人交回 CEO 收尾。

    ``rounds`` 是过程产物（叙事线全部逐轮单元）；``brief`` 是结论产物。``stop_reason`` 记录
    整场为何收场（取 :data:`STOP_REASONS`）。:meth:`to_ceo_output` 渲染成 CEO 循环可读的
    markdown（简报 + L1 焦点小结流；L2/L3 全文走 SSE 事件给前端，不塞进 CEO 上下文）。
    """

    config: DebateConfig
    rounds: list[RoundResult]
    brief: DebateBrief
    stop_reason: str = STOP_CONVERGED
    # 主持人开场白（第 1 轮 :meth:`Moderator._frame` 顺带产出）：辩论赛主持人口吻的全场宣告，
    # 供前端「会说话的主持人」入场气泡渲染。空（未产出 / 解析失败）时前端不渲染入场气泡（开场
    # 由第 1 轮焦点标题承担），故是锦上添花、非硬依赖。
    opening: str = ""
    # 各方【结辩陈词】（阶段化发言角色 P4）：辩已辩尽后各方的收尾 advocacy，全文随 run_id 走执行事件
    # （不塞 payload）。仅认真辩透 + 正反 DEBATE（O1：红队结辩移除）；快速对碰 / 红队 / 圆桌 /
    # 全员失败恒空。详见 :class:`ClosingStatement`。
    closings: list[ClosingStatement] = field(default_factory=list)
    # 本场证人席位花名册（批 D1，additive）；无幕1 透镜 / 单独辩论恒空。
    witnesses: list[WitnessSeatInfo] = field(default_factory=list)
    # 圆桌子题轴（frame 升级，提案 §3.3）；非圆桌恒空。
    subtopics: list[str] = field(default_factory=list)

    @property
    def narrative_first(self) -> bool:
        """呈现顺序（辩论编排设计.md §4.3）：探讨/学习类（圆桌）过程叙事线先行，决策类
        （正反/红队）决策简报先行。前端据此排版，CEO 收尾文本也据此调整侧重。"""
        return self.config.form is DebateForm.ROUNDTABLE

    @property
    def node_summary(self) -> str:
        """团队图上主持人节点的一行预览：「N 轮 · 收敛归因」。

        节点是「一眼概览」位——详尽的争议焦点 / 倾向 / 建议在 debate_result 卡片里，故节点只
        给【轮数 + 为何收场】（``stop_reason`` 的人话）：比塞 ``brief.crux`` 更稳定、信息密度更
        高（crux 可能为空 / 冗长、且已在卡片重复，旧法 crux 落空时退化成「辩论收场：N 轮」的
        近空预览）。复用 :func:`_stop_label`（与 CEO 文本头、前端「为何收场」同一词表）。"""
        return f"{len(self.rounds)} 轮 · {_stop_label(self.stop_reason)}"

    def to_ceo_output(self) -> str:
        """折算回 CEO 循环的 markdown：决策简报 + L1 焦点小结流（按形态调顺序）。

        尾部只提醒用自己的声音收尾并指向 skill；铁律与跨维骨架正文留在
        ``debate_and_review`` / ``deep_multi_lens_research``，不贴进 tool result。
        """
        brief_md = _render_brief(self.brief, self.config)
        narrative_md = _render_narrative_l1(self.rounds)
        rounds_n = len(self.rounds)
        head = (
            f"## 辩论结果（{_form_label(self.config.form)} · {rounds_n} 轮 · "
            f"{_stop_label(self.stop_reason)}）\n"
        )
        body = [narrative_md, brief_md] if self.narrative_first else [brief_md, narrative_md]
        tail = (
            "\n\n---\n以上为本场辩论的**决策简报 + 交锋叙事线**（用户可在界面展开逐轮攻防与"
            "各方全文）。用自己的声音收尾，不要粘贴本段指令。"
            "收尾铁律与跨维骨架见 skill `debate_and_review` / `deep_multi_lens_research`。"
        )
        return head + "\n\n".join(p for p in body if p.strip()) + tail

    def to_event_payload(self) -> dict:
        """结构化为 SSE 事件 payload（前端辩论视图渲染用）。

        承载交锋叙事线（rounds 的 focus / verdict / summary）+ 决策简报 + 参与方定义；各方
        发言【全文】不在此（体量大、且已随辩手 run 走执行事件），靠 ``rounds[*].sides[*].
        run_id`` 关联执行图的辩手节点取回。``narrative_first`` 供前端按形态调呈现顺序。
        """
        return {
            "form": self.config.form.value,
            "motion": self.config.motion,
            "stop_reason": self.stop_reason,
            # 主持人开场白：前端「会说话的主持人」入场气泡；空则不渲染入场。
            "opening": self.opening,
            "narrative_first": self.narrative_first,
            "sides": [
                {
                    "key": s.key,
                    "name": s.name,
                    "stance": s.stance,
                    "is_subject": s.is_subject,
                    **(
                        {
                            "model": s.model,
                            **({"origin": s.origin} if s.origin else {}),
                            **(
                                {"provider_id": s.provider_id}
                                if s.provider_id
                                else {}
                            ),
                        }
                        if (s.model or "").strip()
                        else {}
                    ),
                }
                for s in self.config.sides
            ],
            **(
                {
                    "moderator_model": self.config.moderator_model,
                    **(
                        {"moderator_origin": self.config.moderator_origin}
                        if self.config.moderator_origin
                        else {}
                    ),
                    **(
                        {
                            "moderator_provider_id": self.config.moderator_provider_id
                        }
                        if self.config.moderator_provider_id
                        else {}
                    ),
                }
                if (self.config.moderator_model or "").strip()
                else {}
            ),
            **(
                {"same_model_debate": True}
                if self.config.same_model_debate
                else {}
            ),
            "rounds": [rr.to_event_payload() for rr in self.rounds],
            # 各方结辩陈词（阶段化发言角色 P4）：问题/身份 verbatim 进载荷、陈词全文随 run_id 的 run
            # 事件走（不塞载荷，与各方发言 / 质询作答同策），恒带（无结辩为空列表），载荷形状统一。
            "closings": [
                {
                    "key": c.side_key,
                    "name": c.side_name,
                    "run_id": c.run_id,
                    "ok": c.ok,
                }
                for c in self.closings
            ],
            # 证人花名册（批 D1，additive）；缺字段（老 journal）→ []。
            "witnesses": [
                {
                    "key": w.key,
                    "name": w.name,
                    "lens_run_id": w.lens_run_id,
                    "seat_run_id": w.seat_run_id,
                    "lens_label": w.lens_label,
                    "origin_caption": w.origin_caption,
                }
                for w in self.witnesses
            ],
            "brief": {
                "crux": self.brief.crux,
                "strongest_points": dict(self.brief.strongest_points),
                # 退役字段：新场次恒空；旧载荷降级渲染可读。
                "risk_severities": dict(self.brief.risk_severities),
                # 红队 finding 台账权威快照 + 门决（结构 only，O2）。
                "findings": [f.to_wire() for f in self.brief.findings],
                "gate": self.brief.gate,
                "must_fix": list(self.brief.must_fix),
                # 圆桌共识/分歧地图。
                "consensus_map": [m.to_wire() for m in self.brief.consensus_map],
                # 交接清单：kind 再过一遍 normalize（坏 kind → question），不丢条目。
                "handoffs": [
                    {"kind": normalize_handoff_kind(h.kind), "text": h.text}
                    for h in self.brief.handoffs
                    if h.text
                ],
                # 胜负手（记分裁判 P2）：一句话点名谁的哪点被 drop / 证伪 / 无据；空=未开启记分。
                "decisive": self.brief.decisive,
                "leaning": self.brief.leaning,
                "confidence": self.brief.confidence,
                "recommendation": self.brief.recommendation,
            },
            # 圆桌子题轴；非圆桌 / 老载荷恒 []。
            "subtopics": list(self.subtopics),
        }


class RoundRunner(Protocol):
    """主持人「派一轮辩手发言」的注入接口 —— 隔离编排循环与执行器。

    真实实现（DebateTool）：首轮用 ``build_agent_executor`` + ``WaveScheduler`` 派各方并行
    发言，后续轮用 ``continue_run`` 让同一辩手在自己 transcript 上续写（把对方上轮论点当
    feedback 注入）——这正是「辩手跨轮带记忆」的落点。单测注入 fake，零成本驱动循环。

    入参 ``history`` 是已完成的各轮（含各方上轮发言），实现据此给辩手注入对方论点；
    ``interjections`` 是用户在上一轮边界注入、本轮须正面回应的【追问】（交互式逐轮，opt-in；
    非交互 / 无追问恒空）——实现把它拼进辩手 feedback（见 :func:`round_feedback`）。返回本轮
    各方发言（与 ``sides`` 一一对应，失败方 ``ok=False``）。
    """

    async def __call__(
        self,
        *,
        round_no: int,
        focus: str,
        sides: Sequence[DebateSide],
        history: Sequence[RoundResult],
        interjections: Sequence[UserInterjection] = (),
        beat: SpeechBeat = "statement",
        materials: str = "",
    ) -> list[SideTurn]: ...


class CrossExamRunner(Protocol):
    """主持人「派一轮质询作答」的注入接口 —— 对称于 :class:`RoundRunner`，隔离编排循环与执行器。

    主持人先据本轮立论生成【定向各方的必答质询】（:meth:`Moderator._cross_exam_questions`），再把
    ``questions``（side_key → 问题列表）交给本 runner：真实实现（DebateTool）让每个被质询方用
    ``continue_run`` 在自己 transcript 上正面作答，返回各方的 :class:`CrossExamExchange`（作答全文进
    该方 session 记忆、下一轮立论续写可见）；单测注入 fake 零成本驱动。仅在【认真辩透 + 对抗形态】
    开启（快速对碰 / 圆桌跳过，见 :meth:`Moderator._cross_exam_enabled`），故为**可选**注入——
    未注入 / 未开启时循环逐字回退到「立论→裁判」，零行为变化。
    """

    async def __call__(
        self,
        *,
        round_no: int,
        focus: str,
        sides: Sequence[DebateSide],
        turns: Sequence[SideTurn],
        questions: dict[str, list[str]],
    ) -> list[CrossExamExchange]: ...


class WitnessExamRunner(Protocol):
    """主持人「派一轮证人答问」的注入接口（批 D1）——对称于 :class:`CrossExamRunner`。

    主持人据本轮立论判定是否点名证人、问什么事实性问题；runner 对席位 session 做窄
    ``continue_run``。无席位 / 未注入时跳过，零行为变化。失败不阻塞辩论主流程。
    """

    async def __call__(
        self,
        *,
        round_no: int,
        focus: str,
        questions: dict[str, list[str]],
    ) -> list[WitnessExamExchange]: ...


class ClosingRunner(Protocol):
    """主持人「派一轮结辩陈词」的注入接口 —— 对称于 :class:`CrossExamRunner`（阶段化发言角色 P4）。

    辩论收场后（收敛 / 用户 conclude / 达上限）、简报前，主持人请各方做收尾陈词：真实实现（DebateTool）
    让每个仍有 session 的方用 ``continue_run`` 干净成稿一段结辩（brief = :func:`closing_task`，携带
    ``rounds`` 材料 + 【已核实】标签闸），返回各方 :class:`ClosingStatement`（陈词全文进该方 run
    事件）；单测注入 fake 零成本驱动。仅在【认真辩透 + 对抗形态】开启（快速对碰 / 圆桌跳过，见
    :meth:`Moderator._closing_enabled`），故为**可选**注入——未注入 / 未开启时循环收场后逐字回退到
    「直接出简报」，零行为变化。``rounds`` 是全部已完成轮（brief 材料与白名单的唯一来源）。
    """

    async def __call__(
        self,
        *,
        sides: Sequence[DebateSide],
        rounds: Sequence[RoundResult],
    ) -> list[ClosingStatement]: ...


def tally_scores(rounds: Sequence[RoundResult]) -> dict[str, RoundScore]:
    """把各轮各方的 :class:`RoundScore` 累加成每方一个【累计分】（记分裁判 P2）。

    三维逐轮相加、``penalties`` 全场并起（``note`` 累计无意义、留空）。某方某轮无记分则跳过。
    对抗形态下收场简报据此让 leaning / decisive 与交锋对齐；圆桌仅作 momentum 展示、不驱动
    leaning。无任何记分（未开启 P2）返回空 dict——简报逐字回退，零变化。
    """
    tally: dict[str, RoundScore] = {}
    for rr in rounds:
        for key, sc in rr.verdict.scores.items():
            agg = tally.get(key)
            if agg is None:
                tally[key] = RoundScore(
                    argument=sc.argument,
                    engagement=sc.engagement,
                    evidence=sc.evidence,
                    penalties=list(sc.penalties),
                )
            else:
                agg.argument += sc.argument
                agg.engagement += sc.engagement
                agg.evidence += sc.evidence
                agg.penalties.extend(sc.penalties)
    return tally


# ── 渲染辅助（CEO 文本折算用；前端走 SSE 事件，不复用这些） ─────────────────────


def _form_label(form: DebateForm) -> str:
    # 展示名单源 = constants.FORM_LABELS（lazy：本模块保持 stdlib-only 顶层 import）。
    from agentcore.runtime.debate.constants import FORM_LABELS

    return FORM_LABELS.get(form, str(form))


def _severity_label(sev: str) -> str:
    """红队风险严重度枚举 → 中文（与前端风险看板同口径）；未知值原样回显。"""
    return {"high": "高", "medium": "中", "low": "低"}.get(sev, sev)


def _stop_label(reason: str) -> str:
    return {
        STOP_CONVERGED: "已收敛",
        STOP_FOCUS_CLARIFIED: "焦点已澄清为价值之争",
        STOP_RED_TEAM_EXHAUSTED: "风险已挖尽",
        STOP_MAX_ROUNDS: "达轮数上限",
        STOP_ALL_FAILED: "辩手发言失败提前终止",
        STOP_USER_CONCLUDED: "用户选择出结论",
    }.get(reason, reason or "已结束")


def _render_brief(brief: DebateBrief, config: DebateConfig) -> str:
    lines = ["### 决策简报"]
    if brief.crux:
        lines.append(f"- **争议焦点**：{brief.crux}")
    for side in config.sides:
        point = brief.strongest_points.get(side.key)
        if point:
            sev = brief.risk_severities.get(side.key)
            sev_tag = f"（风险严重度：{_severity_label(sev)}）" if sev else ""
            lines.append(f"- **{side.name}最强论点**{sev_tag}：{point}")
    if brief.findings:
        lines.append("- **风险台账（finding）**：")
        for f in brief.findings:
            sev = f.severity.value if isinstance(f.severity, FindingSeverity) else f.severity
            st = f.status.value if isinstance(f.status, FindingStatus) else f.status
            lines.append(f"  - [{f.id}|{sev}|{st}] {f.target}" + (f"：{f.claim}" if f.claim else ""))
    if brief.gate:
        mf = f"（must-fix：{', '.join(brief.must_fix)}）" if brief.must_fix else ""
        lines.append(f"- **门决**：{brief.gate}{mf}")
    if brief.consensus_map:
        lines.append("- **共识/分歧地图**：")
        for m in brief.consensus_map:
            lines.append(f"  - **{m.topic}**")
            if m.consensus:
                lines.append(f"    - 共识：{'；'.join(m.consensus)}")
            if m.divergences:
                lines.append(f"    - 分歧：{'；'.join(m.divergences)}")
            if m.crux:
                lines.append(f"    - crux：{m.crux}")
    values = [h.text for h in brief.handoffs if h.kind == "value" and h.text]
    facts = [h.text for h in brief.handoffs if h.kind == "fact" and h.text]
    questions = [h.text for h in brief.handoffs if h.kind == "question" and h.text]
    if values:
        lines.append("- **需你定夺（只有你的价值观/偏好能闭合）**：")
        lines.extend(f"  - {d}" for d in values)
    if facts:
        lines.append("- **事实分歧（证据能闭合；证据状态语勿抹平）**：")
        lines.extend(f"  - {d}" for d in facts)
    if questions:
        lines.append("- **待解问题（等外部事件 / 预测验证 / 后续观察）**：")
        lines.extend(f"  - {q}" for q in questions)
    if brief.decisive:
        lines.append(f"- **胜负手（据逐轮记分）**：{brief.decisive}")
    if brief.leaning:
        conf = f"（置信度：{brief.confidence}）" if brief.confidence else ""
        lines.append(f"- **倾向判断**{conf}：{brief.leaning}")
    if brief.recommendation:
        lines.append(f"- **建议**：{brief.recommendation}")
    return "\n".join(lines)


def _render_narrative_l1(rounds: list[RoundResult]) -> str:
    lines = ["### 交锋叙事线（焦点小结）"]
    for rr in rounds:
        focus = rr.focus or "（本轮焦点未定）"
        summary = rr.summary or rr.verdict.rationale or "（本轮小结缺失）"
        lines.append(f"- **第 {rr.round_no} 轮 · {focus}**：{summary}")
    return "\n".join(lines)
