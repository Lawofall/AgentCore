"""核心数据类型 + 协议（评估体系 §三）.

纯模块：仅 dataclass + Protocol，不 import runtime/LLM，故可独立单测。
``EvalCase`` 是黄金用例（数据），``TurnOutcome`` 是 harness 把一次真实运行归一化成的
可断言事实，``Check``/``Judge``/``Harness`` 是三个解耦点。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

EvalCategory = Literal["qa", "retrieval", "team", "tool_use", "no_fabrication", "routing"]
RunPath = Literal["single", "team"]
ToolsetName = Literal["ceo", "worker"]


class EvalConfigError(Exception):
    """用例配置/加载期错误（lint 失败、套件目录缺失、fixture 目录不存在等）.

    纯静态错误，与运行模型无关；CLI 捕获后以非 0 退出，per-PR 硬门禁据此阻断。
    """


@dataclass
class EvalCase:
    """一个黄金用例。从 ``cases/*.json`` 加载（loader 解析为本类型）。

    ``checks`` 是声明式断言 ``[{"name": str, "args": {...}}]``，由 ``checks.build_check``
    解析。``rubric`` 非空走绝对分 LLM 裁判；``milestones`` 非空走 milestone 覆盖裁判（结果维，
    取代 ``RosterMatches`` 等轨迹断言，§六）——两者皆计入判定。``samples`` >1 为重跑取通过率。
    """

    id: str
    category: EvalCategory
    user_message: str
    path: RunPath = "single"
    mode: str = "economy"
    toolset: ToolsetName = "ceo"
    workspace_fixture: str | None = None
    # 用例级预置用户规则 / AI 记忆（DB documents 行）。心智对齐 workspace_fixture：
    # fixtures/<name>/documents.json；harness 每例前后 purge ``_EVAL_USER_ID``。
    documents_fixture: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    rubric: str | None = None
    # 结果维 milestone 覆盖（后端架构.md §五）：把任务的子目标写成可逐条判定的清单
    # ``[{"id", "desc", "weight"}]``，由 LLM milestone 裁判判**交付物**覆盖了哪些（不管谁干的、
    # 走没走某轨迹）。取代轨迹断言 ``RosterMatches``。``milestone_threshold`` 为加权覆盖率阈值。
    milestones: list[dict[str, Any]] = field(default_factory=list)
    milestone_threshold: float = 0.8
    samples: int = 1
    # 方向① 变体注入：命名的 prompt 变体（见 evals/prompt_profiles.py）。None=基线（恒等，
    # 与生产逐字节一致）；非 None 时 harness 在本例运行期 use_profile 注入该变体，A/B 提示词。
    prompt_profile: str | None = None
    # 学·度量 §2.5：本例探测的 MAST 失败模式码（如 ``"1.3"``，见 evals/mast.py 的 14 类）。
    # None=不挂 MAST 标签（非 MAST 套件如 core/routing）；非 None 时 report 据此按 MAST 组/类
    # 聚合通过率，使「某类失败被压低没有」可逐类对照 baseline。seed_lint 校验码已注册。
    mast: str | None = None
    # 协作形状评测（阶段 1）：声明式期望 DAG 形状，由 ``shape_score`` 打 0~1 匹配分（报告指标，
    # 非 L0 硬门）。键集见 ``evals/shape_score.py``；缺省 None=不打形状分。
    expected_shape: dict[str, Any] | None = None
    # 反向指回质量案 ID（可选数组，一题可覆盖多案）。缺省空列表；有则 seed_lint 校验
    # ``qc-<YYYYMMDD>-<slug>``。不设必填——现有用例 JSON 无须改。
    quality_case: list[str] = field(default_factory=list)


@dataclass
class TurnOutcome:
    """harness 把一次真实运行归一化成可断言的事实。

    单 Agent 路径的 ``finish_reason`` 优先取引擎经 ``ReactLoopOut.finish_override`` 抬出的非默认
    终态（``degraded`` / ``unproductive``），无则按轮数推导（``end_turn`` / ``max_rounds``）；
    ``roster`` 取自 ``run_plan.agents[*].role``（team 路径）；``cost_usd`` 单 Agent 现算、
    team 读 ``cost_runs``。
    ``plan_runs`` / ``plan_type`` / ``collab_interactions`` 来自 ``RecordingSink`` 对 SSE 的截获
    （形状与互动辅指标；禁止从 logs/dev.jsonl 反推）。

    ``content`` 保持**聊天正文**（不改写——``NonEmpty`` / ``ContentMatches`` /
    baseline 快照依赖它）。
    ``artifacts`` 是工作区终版成品：``path → 末次 file_write 正文``（从 ``tool_calls`` 还原）。
    裁判口径用 :func:`judged_text`（正文 + 成品），与产品「文件交付」形态对齐。
    """

    content: str
    finish_reason: str
    rounds: int
    tool_calls: list[tuple[str, str]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    delegated: bool = False
    roster: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    latency_ms: int = 0
    error: str | None = None
    # 过滤 captain/CEO 后的计划图节点：id / role / task / depends_on / parent_run_id。
    plan_runs: list[dict[str, Any]] = field(default_factory=list)
    plan_type: str | None = None
    # 协作互动事件计数（升级 / replan / 续派 …），键为短标签、值为次数。
    collab_interactions: dict[str, int] = field(default_factory=dict)
    # 终版成品：path → 该 path 末次 file_write 的 content（空 dict = 无落盘 / 旧 outcome）。
    artifacts: dict[str, str] = field(default_factory=dict)
    # 工作区根（copytree 隔离副本）。``TestExitCode`` / ``TestsUnchanged`` 等盘面 Check 用；
    # 旧 outcome / 无 workspace 路径为 None。
    workspace_root: str | None = None
    # 对照根（vendor 干净树或 seed 前快照）。``TestsUnchanged`` 比「禁改测目录」用；可空。
    reference_root: str | None = None


def artifacts_from_tool_calls(tool_calls: list[tuple[str, str]]) -> dict[str, str]:
    """从 ``tool_calls`` 还原每 path **末次** ``file_write`` 的 content。

    与 P2 手工修正同构：只认 ``file_write``；同 path 多次写入取最后一次；坏 JSON /
    缺 path/content 跳过。不改写聊天 ``content``。
    """
    out: dict[str, str] = {}
    for name, raw in tool_calls:
        if name != "file_write":
            continue
        try:
            args = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            continue
        if not isinstance(args, dict):
            continue
        path = args.get("path")
        body = args.get("content")
        if isinstance(path, str) and path and isinstance(body, str):
            out[path] = body  # 末次覆盖
    return out


def judged_text(outcome: TurnOutcome) -> str:
    """拼「聊天正文 + 各终版成品」给裁判——被评口径与产品文件交付对齐。

    无 ``artifacts`` 时原样返回 ``content``（旧 outcome / 无落盘路径行为不变）。
    有成品时在正文后按 path 排序追加 ``### {path}`` 与正文块（空行分隔）。
    """
    body = outcome.content or ""
    arts = outcome.artifacts or {}
    if not arts:
        return body
    chunks: list[str] = [body] if body else []
    for path in sorted(arts):
        chunks.append(f"### {path}\n{arts[path]}")
    return "\n\n".join(chunks)


@dataclass
class CheckOutcome:
    """一个确定性 Check 的判定结果。

    ``gating`` 区分两类 Check（后端架构.md §五）：``True`` = L0 契约/安全**不变量**，进
    pass/fail 判定；``False`` = **诊断**（轨迹形状，如派没派/roster），仍记录与报告，但**不**计
    入判定——任务是否成功看 L1 rubric 裁判，不看编排机制。归类见 ``checks.DIAGNOSTIC_CHECKS``，
    由 ``runner.apply_checks`` 落标。
    """

    name: str
    passed: bool
    detail: str = ""
    gating: bool = True


@dataclass
class JudgeVerdict:
    """LLM 裁判对一次运行的语义打分（P1）。"""

    score: float
    passed: bool
    rationale: str


@dataclass
class MilestoneItemResult:
    """单条 milestone 子目标的判定（裁判判它在交付物里有没有被覆盖）。"""

    id: str
    desc: str
    weight: float
    covered: bool


@dataclass
class MilestoneVerdict:
    """milestone 覆盖裁判结果（后端架构.md §五：结果维断言，非轨迹）。

    ``coverage`` = **加权命中比** ``Σweight(covered)/Σweight``，
    ``passed = coverage >= threshold``。逐项 ``items`` 留痕，便于看清「哪个子目标没覆盖」，
    比单一 1–5 分更可诊断。
    """

    coverage: float
    passed: bool
    threshold: float
    items: list[MilestoneItemResult] = field(default_factory=list)
    rationale: str = ""


@dataclass
class CaseReport:
    """一次用例运行的完整报告（确定性 Check + 可选裁判 + 可选 milestone + 归一化 outcome）。"""

    case_id: str
    category: str
    outcome: TurnOutcome
    checks: list[CheckOutcome] = field(default_factory=list)
    judge: JudgeVerdict | None = None
    milestone: MilestoneVerdict | None = None
    # 学·度量 §2.5：从 ``EvalCase.mast`` 透传的失败模式码（report 按 MAST 组/类聚合用）。
    mast: str | None = None
    # 协作形状匹配分 0~1（报告指标；None=本例未声明 expected_shape）。不进 pass/fail。
    shape_score: float | None = None

    @property
    def checks_passed(self) -> bool:
        """仅 **gating（L0 不变量）** Check 全过即可；诊断 Check 记录但不计入判定。"""
        return all(c.passed for c in self.checks if c.gating)

    @property
    def passed(self) -> bool:
        """判定口径（后端架构.md §五）：L0 不变量全过 且（无 rubric 裁判 or 通过）且
        （无 milestone or 覆盖达阈）且未报错。

        诊断 Check（轨迹形状）不参与判定——任务是否成功由 L0 安全/契约不变量 + L1 rubric 裁判 +
        milestone 交付物覆盖共同决定，不由「派没派 / roster 对不对」决定。
        """
        judge_ok = self.judge is None or self.judge.passed
        milestone_ok = self.milestone is None or self.milestone.passed
        return self.checks_passed and judge_ok and milestone_ok and self.outcome.error is None


@dataclass
class EvalReport:
    """一次评测跑的聚合报告（一个或多个 ``CaseReport``，samples>1 时同 case_id 多条）。"""

    cases: list[CaseReport] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


@runtime_checkable
class Check(Protocol):
    """确定性断言：判定不调 LLM。"""

    name: str

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome: ...


class Judge(Protocol):
    """LLM 裁判：按 rubric 给语义分（P1，用 ``LLMProvider.complete``）。"""

    async def score(self, case: EvalCase, outcome: TurnOutcome) -> JudgeVerdict: ...


class MilestoneJudge(Protocol):
    """milestone 覆盖裁判：一次结构化打分，判交付物覆盖了哪些子目标（结果维，非轨迹）。"""

    async def score_milestones(self, case: EvalCase, outcome: TurnOutcome) -> MilestoneVerdict: ...


class Harness(Protocol):
    """「怎么离线跑一例」与评分逻辑解耦。"""

    async def run_case(self, case: EvalCase) -> TurnOutcome: ...


# ---------------------------------------------------------------------------
# 对比评估（团队 vs 单体）—— 现状见 docs/02-架构/后端架构.md §五（未落地 P1+ 见 远期规划.md §2.4）
#
# 与上面的「功能评估」正交：那套判单条回合对不对（绝对正确性 + 绝对分裁判）；
# 这套判同一任务下「多 Agent 是否真比单 Agent 好」（多臂对照 + 成对偏好裁判）。
# 本段仍是纯类型（不 import runtime/LLM），故可随 __init__ 静态暴露。
# ---------------------------------------------------------------------------

EvalArchetype = Literal["parallel_research", "debate", "cross_domain", "simple"]


@dataclass
class ComparisonCase:
    """一个对比用例：同一任务跑过多个「臂」（single / team / …），比较产出优劣。

    与 :class:`EvalCase`（单 path、绝对判定）正交：本类一条 = 一道题 × 多臂，runner 为
    每个臂合成一个 :class:`EvalCase` 喂现有 harness（零侵入）。``baseline_arm`` 是被比较的
    基准（默认单体），其余臂逐一与之成对裁判。``checks`` 按臂可选（``{arm: [{name,args}]}``），
    服务 pass^k 可靠性；``rubric`` 非空才走成对裁判（P0 self-test 注入假裁判）。``arms`` 中
    ``matched_single``（等算力单体）为 P1，P0 仅 ``single``/``team``。
    """

    id: str
    archetype: EvalArchetype
    user_message: str
    arms: list[str] = field(default_factory=lambda: ["single", "team"])
    baseline_arm: str = "single"
    mode: str = "economy"
    toolset: ToolsetName = "ceo"
    workspace_fixture: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    checks: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    rubric: str | None = None
    samples: int = 1


@dataclass
class JudgeVote:
    """多评委合议中单票留痕（``PairwiseVerdict.votes``）。"""

    judge_id: str
    winner: str  # 臂名或 "tie"
    margin: int = 0


@dataclass
class PairwiseVerdict:
    """成对裁判对「主臂 vs 基准臂」一次比较的结论（盲评 + 位置对调后的合议）。

    ``votes`` 仅 Ensemble 路径填写（单裁判默认空列表，兼容旧 baseline / 报告）。
    """

    winner: str  # 胜出臂名，或 "tie"
    rationale: str = ""
    margin: int = 0  # 0–3 优势强度（可选）
    votes: list[JudgeVote] = field(default_factory=list)


@dataclass
class ArmResult:
    """一个臂在某对比用例下的全部采样结果（k 次）+ 逐采样的确定性 Check。"""

    arm: str
    outcomes: list[TurnOutcome] = field(default_factory=list)
    checks: list[list[CheckOutcome]] = field(default_factory=list)

    @property
    def passk(self) -> bool | None:
        """pass^k：k 次采样的 Check 全过才 True；该臂未声明 Check 则 None（不判）。"""
        if not self.checks or not any(self.checks):
            return None
        return all(all(c.passed for c in sample) for sample in self.checks)


@dataclass
class ComparisonCaseReport:
    """一道对比用例的完整报告：各臂结果 + 主臂逐对裁判结论。"""

    case_id: str
    archetype: str
    baseline_arm: str
    arms: dict[str, ArmResult] = field(default_factory=dict)
    pairwise: dict[str, list[PairwiseVerdict]] = field(default_factory=dict)

    @property
    def subject_arms(self) -> list[str]:
        """除基准外的「被检验臂」（默认就是 team）。"""
        return [a for a in self.arms if a != self.baseline_arm]


@dataclass
class ComparisonReport:
    """一次对比评测跑的聚合（多道对比用例）。"""

    cases: list[ComparisonCaseReport] = field(default_factory=list)


class PairwiseJudge(Protocol):
    """成对语义裁判：判「主臂 vs 基准臂」哪个更好（盲评、先理由后结论）。

    ``archetype`` / ``case_id`` 可选：供裁判按正/负样本分流 verbosity 准则（缺省 = concise）。
    """

    async def compare(
        self,
        *,
        rubric: str,
        user_message: str,
        subject_arm: str,
        subject_content: str,
        baseline_arm: str,
        baseline_content: str,
        archetype: str | None = None,
        case_id: str | None = None,
    ) -> PairwiseVerdict: ...
