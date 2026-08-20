"""裁判校准回路（后端架构.md §五：裁判是测量仪器，先校准再用）.

新评测体系把 ``LLMJudge`` 的 pass_rate 当作相对基线观测的主数字。但**未校准的裁判 =
没刻度的尺子**：拿没刻度的尺子对照基线只会误导（行业头号 pitfall）。本模块吃一批人工标注
的 gold-set（任务 + rubric + **一份具体答案** + **人工分**），用生产裁判过一遍，算**判↔人一致度**：

- **Cohen's kappa（pass/fail 二分）** —— 主指标：直接对应观测对照吃的 pass_rate；
- **二次加权 kappa（序数 1–5）** —— 差 1 分 vs 差 4 分按平方罚，更贴合 1–5 档；
- **Spearman 秩相关** —— 单调一致度；
- **平均偏置 / 分歧样本** —— 供「读分歧找模式（偏宽松？偏自信？）→ 改 rubric/prompt → 重跑」。

刻意**不用 exact-match 原始一致率**作校准结论（会高估 33–41pp，重设计 §五）；``kappa>0.6`` 才算
裁判可信、才值得拿它的 pass_rate 去对照基线（对照本身仍是观测，不是合并门禁）。

纯统计为独立纯函数（无 numpy/scipy 依赖），可零 LLM 单测；``calibrate`` 复用生产 ``Judge``
路径（单测注入假裁判，真模型留给手动/夜跑校准）。本模块只 import ``types``（纯类型），不拖
runtime/LLM，故与 ``routing.py`` 同构、不进 ``__init__`` 静态面。
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agentcore.evals.types import (
    EvalCase,
    EvalConfigError,
    Judge,
    JudgeVerdict,
    TurnOutcome,
)

# --- 纯统计：判↔人一致度（无第三方依赖，可零成本单测） -----------------------


def cohens_kappa(a: Sequence[int], b: Sequence[int]) -> float:
    """无加权 Cohen's kappa（扣除偶然一致的判↔人一致度；pass/fail 二分或名义分类用）。

    ``po`` 观测一致率、``pe`` 偶然一致率，``kappa=(po-pe)/(1-pe)``。退化情形（``pe>=1``，即
    两序各自落在同一类）：完全一致返 ``1.0``，否则 ``0.0``。完全反一致可低至 ``-1.0``。
    """
    if len(a) != len(b):
        raise ValueError("两序列长度必须一致")
    n = len(a)
    if n == 0:
        raise ValueError("空序列无法计算 kappa")
    cats = sorted(set(a) | set(b))
    agree = sum(1 for x, y in zip(a, b, strict=True) if x == y)
    po = agree / n
    count_a = Counter(a)
    count_b = Counter(b)
    pe = sum((count_a[k] / n) * (count_b[k] / n) for k in cats)
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else 0.0
    return (po - pe) / (1.0 - pe)


def quadratic_weighted_kappa(
    a: Sequence[int], b: Sequence[int], *, min_cat: int = 1, max_cat: int = 5
) -> float:
    """二次加权 Cohen's kappa（序数 1–5：错得越远罚越重；判↔人序数一致度）。

    权重 ``w_ij=(i-j)^2/(max_cat-min_cat)^2``，``kappa=1 - Σw·O / Σw·E``（``O`` 观测、``E``
    行列边际外积期望）。越界分（如解析失败的 0）压到最近端点。退化（仅一档或 ``Σw·E==0``）：
    完全一致返 ``1.0``，否则 ``0.0``。二分数据上与无加权 :func:`cohens_kappa` 等价。
    """
    if len(a) != len(b):
        raise ValueError("两序列长度必须一致")
    n = len(a)
    if n == 0:
        raise ValueError("空序列无法计算 kappa")
    cats = list(range(min_cat, max_cat + 1))
    idx = {c: i for i, c in enumerate(cats)}
    k = len(cats)
    denom_range = (max_cat - min_cat) ** 2

    def _clamp(v: int) -> int:
        return min(max(v, min_cat), max_cat)

    aa = [_clamp(x) for x in a]
    bb = [_clamp(y) for y in b]
    if denom_range == 0:
        return 1.0 if all(x == y for x, y in zip(aa, bb, strict=True)) else 0.0

    obs = [[0] * k for _ in range(k)]
    for x, y in zip(aa, bb, strict=True):
        obs[idx[x]][idx[y]] += 1
    row = [sum(obs[i]) for i in range(k)]
    col = [sum(obs[i][j] for i in range(k)) for j in range(k)]

    num = 0.0
    den = 0.0
    for i in range(k):
        for j in range(k):
            w = (cats[i] - cats[j]) ** 2 / denom_range
            num += w * obs[i][j]
            den += w * (row[i] * col[j] / n)
    if den == 0:
        return 1.0 if num == 0 else 0.0
    return 1.0 - num / den


def _average_ranks(values: Sequence[float]) -> list[float]:
    """1-based 平均秩（并列取平均秩），供 Spearman 容并列。"""
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for t in range(i, j + 1):
            ranks[order[t]] = avg
        i = j + 1
    return ranks


def spearman_rho(a: Sequence[float], b: Sequence[float]) -> float:
    """Spearman 秩相关（判↔人单调一致；并列用平均秩；任一方零方差 → ``0.0``）。"""
    if len(a) != len(b):
        raise ValueError("两序列长度必须一致")
    n = len(a)
    if n == 0:
        raise ValueError("空序列无法计算相关")
    ra = _average_ranks(a)
    rb = _average_ranks(b)
    mean_a = sum(ra) / n
    mean_b = sum(rb) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(ra, rb, strict=True))
    var_a = sum((x - mean_a) ** 2 for x in ra)
    var_b = sum((y - mean_b) ** 2 for y in rb)
    if var_a == 0 or var_b == 0:
        return 0.0
    return cov / (var_a * var_b) ** 0.5


# --- gold-set 数据 + 加载 ----------------------------------------------------


@dataclass
class GoldLabel:
    """一条人工标注的 gold 样本：(任务 + rubric + 一份具体答案 + 人工分)。

    校准测「裁判在**同一批答案**上判得与人一致吗」，故必须含**答案**与**人工分**——这与
    :class:`~agentcore.evals.types.EvalCase` 不同（后者答案由真跑产生、无人工分）。
    ``human_pass`` 缺省由 ``human_score >= pass_threshold`` 推导（校准时用统一阈值，保证
    判↔人 pass/fail 可比）；显式给出则尊重之。
    """

    id: str
    user_message: str
    rubric: str
    answer: str
    human_score: float
    human_pass: bool | None = None
    note: str = ""


def load_gold_set(path: Path | str) -> list[GoldLabel]:
    """从单个 JSON 文件加载 gold-set（一个标注对象的数组）。

    结构错误（非数组 / 缺必填 / ``human_score`` 越界 / ``answer`` 空 / id 重复）即 raise
    :class:`~agentcore.evals.types.EvalConfigError`，与套件 loader 同口径（带病数据绝不开跑、
    CLI 据此以退出码 2 阻断）。多出的键被忽略（便于加注释字段）。
    """
    p = Path(path)
    if not p.is_file():
        raise EvalConfigError(f"gold-set 文件不存在: {p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise EvalConfigError(f"gold-set 不是合法 JSON: {p} ({e})") from e
    if not isinstance(raw, list):
        raise EvalConfigError(f"gold-set 顶层必须是数组: {p}")
    if not raw:
        raise EvalConfigError(f"gold-set 为空: {p}")

    labels: list[GoldLabel] = []
    seen: set[str] = set()
    required = {"id", "user_message", "rubric", "answer", "human_score"}
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise EvalConfigError(f"gold-set[{i}] 不是对象")
        missing = required - item.keys()
        if missing:
            raise EvalConfigError(f"gold-set[{i}] 缺字段: {sorted(missing)}")
        cid = str(item["id"])
        if cid in seen:
            raise EvalConfigError(f"gold-set id 重复: {cid!r}")
        seen.add(cid)
        try:
            score = float(item["human_score"])
        except (TypeError, ValueError) as e:
            bad = item["human_score"]
            raise EvalConfigError(f"gold-set[{cid}] human_score 非数值: {bad!r}") from e
        if not 1.0 <= score <= 5.0:
            raise EvalConfigError(f"gold-set[{cid}] human_score 需在 1–5: {score}")
        if not str(item["answer"]).strip():
            raise EvalConfigError(f"gold-set[{cid}] answer 不能为空")
        human_pass = item.get("human_pass")
        labels.append(
            GoldLabel(
                id=cid,
                user_message=str(item["user_message"]),
                rubric=str(item["rubric"]),
                answer=str(item["answer"]),
                human_score=score,
                human_pass=None if human_pass is None else bool(human_pass),
                note=str(item.get("note", "")),
            )
        )
    return labels


# --- 校准结果 + 聚合 ---------------------------------------------------------


@dataclass
class JudgeOnLabel:
    """裁判在一条 gold 样本上的判定 vs 人工标注（逐条留痕，供算一致度 + 列分歧）。"""

    id: str
    human_score: float
    judge_score: float
    human_pass: bool
    judge_pass: bool
    rationale: str = ""


@dataclass
class CalibrationMetrics:
    """裁判校准结果：判↔人一致度（kappa 三件 + 偏置）+ 分歧样本 + kappa 门判定。"""

    pass_threshold: float
    kappa_gate: float
    per_label: list[JudgeOnLabel] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.per_label)

    @property
    def cohens_kappa(self) -> float:
        """二分 pass/fail 的无加权 kappa —— L1 裁判可信度主指标（相对基线观测也靠这把尺）。"""
        if not self.per_label:
            return 0.0
        a = [1 if x.human_pass else 0 for x in self.per_label]
        b = [1 if x.judge_pass else 0 for x in self.per_label]
        return cohens_kappa(a, b)

    @property
    def weighted_kappa(self) -> float:
        """序数 1–5 二次加权 kappa（分数四舍五入到档；差几分按平方罚）。"""
        if not self.per_label:
            return 0.0
        a = [round(x.human_score) for x in self.per_label]
        b = [round(x.judge_score) for x in self.per_label]
        return quadratic_weighted_kappa(a, b)

    @property
    def spearman(self) -> float:
        """判↔人分的 Spearman 秩相关（单调一致度）。"""
        if not self.per_label:
            return 0.0
        return spearman_rho(
            [x.human_score for x in self.per_label],
            [x.judge_score for x in self.per_label],
        )

    @property
    def raw_agreement(self) -> float:
        """exact pass/fail 一致率（**仅参考**：会高估 33–41pp，不作门禁，重设计 §五）。"""
        if not self.per_label:
            return 0.0
        return sum(1 for x in self.per_label if x.human_pass == x.judge_pass) / self.n

    @property
    def mean_bias(self) -> float:
        """``mean(judge - human)``：>0 = 裁判系统性偏宽松、<0 偏严苛（找『偏自信』等模式）。"""
        if not self.per_label:
            return 0.0
        return sum(x.judge_score - x.human_score for x in self.per_label) / self.n

    @property
    def disagreements(self) -> list[JudgeOnLabel]:
        """判↔人 pass/fail 不一致的样本，按分差降序（『读分歧找模式』先看错得最离谱的）。"""
        return sorted(
            (x for x in self.per_label if x.human_pass != x.judge_pass),
            key=lambda x: abs(x.human_score - x.judge_score),
            reverse=True,
        )

    @property
    def trustworthy(self) -> bool:
        """Cohen's kappa >= 门 才算裁判可信、才值得拿它的 pass_rate 对照基线。"""
        return self.cohens_kappa >= self.kappa_gate


async def calibrate(
    judge: Judge,
    labels: list[GoldLabel],
    *,
    pass_threshold: float = 4.0,
    kappa_gate: float = 0.6,
) -> CalibrationMetrics:
    """拿生产裁判过一遍 gold-set，逐条收 (人工分, 裁判分)，聚成 :class:`CalibrationMetrics`。

    每条 gold 样本适配成 ``(EvalCase, TurnOutcome)`` 喂**真实裁判路径**（``judge.score``，零侵入
    复用生产 rubric/CoT/多采样）。判↔人 pass 用**同一** ``pass_threshold`` 推导以保证二分 kappa
    可比（``human_pass`` 显式给出则尊重之）。单测注入假裁判，真模型留给手动/夜跑校准。
    """
    if not labels:
        raise EvalConfigError("gold-set 为空，无法校准")
    per: list[JudgeOnLabel] = []
    for lb in labels:
        case = EvalCase(id=lb.id, category="qa", user_message=lb.user_message, rubric=lb.rubric)
        outcome = TurnOutcome(content=lb.answer, finish_reason="end_turn", rounds=1)
        verdict: JudgeVerdict = await judge.score(case, outcome)
        human_pass = lb.human_score >= pass_threshold if lb.human_pass is None else lb.human_pass
        per.append(
            JudgeOnLabel(
                id=lb.id,
                human_score=lb.human_score,
                judge_score=verdict.score,
                human_pass=human_pass,
                judge_pass=verdict.score >= pass_threshold,
                rationale=verdict.rationale,
            )
        )
    return CalibrationMetrics(pass_threshold=pass_threshold, kappa_gate=kappa_gate, per_label=per)


# --- 序列化 + 控制台报告（与 report.py / routing.py 风格一致：ASCII 标记防乱码） ----


def calibration_to_dict(m: CalibrationMetrics) -> dict:
    """JSON-able dict（落盘 / 月度趋势对比；与 report_to_dict 风格一致）。"""
    return {
        "n": m.n,
        "pass_threshold": m.pass_threshold,
        "kappa_gate": m.kappa_gate,
        "cohens_kappa": round(m.cohens_kappa, 4),
        "weighted_kappa": round(m.weighted_kappa, 4),
        "spearman": round(m.spearman, 4),
        "raw_agreement": round(m.raw_agreement, 4),
        "mean_bias": round(m.mean_bias, 4),
        "trustworthy": m.trustworthy,
        "disagreements": [
            {
                "id": x.id,
                "human_score": x.human_score,
                "judge_score": x.judge_score,
                "human_pass": x.human_pass,
                "judge_pass": x.judge_pass,
                "rationale": x.rationale,
            }
            for x in m.disagreements
        ],
    }


def format_calibration_report(m: CalibrationMetrics) -> str:
    """控制台文本：一致度三件 + 偏置 + kappa 门判定 + 分歧逐条。ASCII 标记避免 Windows 乱码。"""
    lines: list[str] = ["=" * 64, "AgentCore 裁判校准（判↔人一致度）", "=" * 64]
    lines.append(f"  样本 {m.n}    pass 阈值 {m.pass_threshold:.1f}    kappa 门 {m.kappa_gate:.2f}")
    if m.n < 30:
        lines.append(f"  [!] 样本仅 {m.n} 条，kappa 小样本不稳；建议 >=100（重设计 §五）")
    lines.append("-" * 64)
    verdict = "可信(准上门禁)" if m.trustworthy else "不可信(勿用它卡门)"
    lines.append(f"  Cohen's kappa(pass/fail) {m.cohens_kappa:.3f}  -> {verdict}  [门禁主指标]")
    lines.append(f"  加权 kappa(序数1-5)      {m.weighted_kappa:.3f}")
    lines.append(f"  Spearman 秩相关          {m.spearman:.3f}")
    lines.append(f"  raw 一致率(仅参考·高估)  {m.raw_agreement * 100:.0f}%")
    bias_dir = "偏宽松" if m.mean_bias > 0 else "偏严苛" if m.mean_bias < 0 else "无系统偏置"
    lines.append(f"  平均偏置(judge-human)    {m.mean_bias:+.2f} ({bias_dir})")
    dis = m.disagreements
    if dis:
        lines.append("-" * 64)
        lines.append(f"  判↔人分歧 {len(dis)} 条（按分差降序·读分歧找模式）:")
        for x in dis[:10]:
            hp = "过" if x.human_pass else "否"
            jp = "过" if x.judge_pass else "否"
            lines.append(
                f"    [{x.id}] 人{x.human_score:.0f}/判{x.judge_score:.1f}"
                f" (人{hp}·判{jp}): {x.rationale[:54]}"
            )
        if len(dis) > 10:
            lines.append(f"    ... 另 {len(dis) - 10} 条（见 JSON）")
    lines.append("=" * 64)
    return "\n".join(lines)
