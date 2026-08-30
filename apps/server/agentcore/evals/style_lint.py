"""输出风格的确定性 linter（方向④：anti-slop 违规可度量，先可观测）.

纯文本启发式、**零额外 LLM**：检测
[`runtime/resolve/prompt._DEFAULT_SYSTEM_PROMPT`](../runtime/resolve/prompt.py)
的 ``<输出>`` 明令禁止的几类「AI 腔」——套话开场 / 客套收尾 / 未授权 emoji。与 LLM
裁判（语义质量）正交：那判「答得好不好」，本模块判「有没有犯这几条**确定可判**的风格戒律」，
故可零成本单测（见 ``tests/test_evals_style.py``）。

**两种用法**：(1) 作为确定性 :class:`~agentcore.evals.checks.StyleCleanCheck` 让用例就地断言
「这条回复零 slop」；(2) 作为 :func:`style_metrics` 聚合器在整套用例上算**违规率**，回答
[路线图摘要](../../../docs/01-产品/产品路线图摘要.md) /
[后端架构 §五](../../../docs/02-架构/后端架构.md)
方向「提示词优化」的「`<输出>`
那段到底有没有用、能不能瘦」——这是「先可观测」的产出。

**确定性边界**：linter 规则本身是纯函数，可零 LLM 单测；但要**产生**被检文本，仍需把用例跑过
真实 pipeline（真模型回合）才有 ``outcome.content`` 可 lint——故违规率出数依赖
真跑评测（详细提案不在公开仓；现状见后端架构 §五）。

**低噪优先**：规则刻意只收**锚定**信号（开场短语锚回复首、客套短语锚回复尾、emoji 走
Unicode 块），宁可漏报不误报——observability linter 的误报会毒化信任。过度加粗 / 滥用列表
等**密度类**启发式噪声大、需先有真实语料校准阈值，列为 v2（见 ``docs`` 方向④）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agentcore.evals.types import CaseReport

RULE_OPENING = "opening_boilerplate"
RULE_CLOSING = "closing_pleasantry"
RULE_EMOJI = "emoji"

# 套话开场：锚回复**首**（剥掉 markdown 标记后）。只收近乎必为 slop 的固定开场——光秃的
# 「当然 / 好的」可能是合法短答，故仅在带感叹号（「当然！」式）或本身就是寒暄填充时才收。
_OPENING_PHRASES = (
    "好问题",
    "问得好",
    "这是一个好问题",
    "这是个好问题",
    "非常好的问题",
    "很高兴为你",
    "很高兴能帮",
    "感谢你的提问",
    "感谢您的提问",
    "当然！",
    "当然可以！",
    "当然!",
    "当然可以!",
    "好的！",
    "好的!",
    "没问题！",
    "没问题!",
    "great question",
    "good question",
    "excellent question",
    "certainly!",
    "of course!",
    "sure!",
    "i'd be happy to",
    "i would be happy to",
    "happy to help",
)

# 客套收尾：锚回复**尾**。
_CLOSING_PHRASES = (
    "希望对你有帮助",
    "希望这对你有帮助",
    "希望这能帮到你",
    "希望对您有帮助",
    "希望能帮到你",
    "如有疑问",
    "如有任何疑问",
    "如有其他问题",
    "随时告诉我",
    "随时问我",
    "随时联系我",
    "hope this helps",
    "let me know if",
    "feel free to ask",
    "don't hesitate to ask",
)

# emoji：主流 emoji Unicode 块。刻意**不含**箭头（→←↑↓, U+2190–21FF）与数学符号
# （U+2200–22FF）——它们在技术散文里合法，收进来会误报。涵盖 prompt 点名的 ✅🚀✨🔧。
_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001faff"  # 符号与象形文字（含补充 / 扩展-A、交通🚀、表情😀）
    "\U00002600-\U000027bf"  # 杂项符号 + dingbats（✅✨✓✗）
    "\U00002b00-\U00002bff"  # 杂项符号箭头区里的星标 ⭐ 等
    "\U0001f000-\U0001f0ff"  # 麻将 / 多米诺 / 扑克
    "\U0000fe00-\U0000fe0f"  # 变体选择符（emoji 呈现）
    "\U0000200d"  # 零宽连接（emoji 序列）
    "]"
)

# 剥掉回复首部的 markdown 噪声（标题井号 / 引用号 / 列表符 / 加粗星号 / 空白），
# 好让开场短语锚定在「真正的第一句」上。
_LEADING_MD = re.compile(r"^[\s#>*\-•\d.、)）]+")

# 切句/行：客套收尾只看**最后一句**是否就是寒暄，故 mid-text 提及不误报（不靠 endswith，
# 因英文 sign-off 多是「Let me know if …」这类领起整句、短语并不在句尾）。
_SENT_SPLIT = re.compile(r"[。.!！?？\n]+")


@dataclass(frozen=True)
class StyleViolation:
    """一条风格违规：命中的规则 + 触发片段（供逐条排查 / 报告）。"""

    rule: str
    snippet: str


def style_violations(text: str | None) -> list[StyleViolation]:
    """返回一段回复正文命中的全部风格违规（空列表 = 干净）。纯函数、零 LLM。"""
    out: list[StyleViolation] = []
    if not text or not text.strip():
        return out
    stripped = text.strip()

    head = _LEADING_MD.sub("", stripped).lstrip().lower()
    for p in _OPENING_PHRASES:
        if head.startswith(p):
            out.append(StyleViolation(RULE_OPENING, p))
            break

    segments = [s for s in _SENT_SPLIT.split(stripped) if s.strip()]
    last_sentence = segments[-1].strip().lower() if segments else ""
    for p in _CLOSING_PHRASES:
        if p in last_sentence:
            out.append(StyleViolation(RULE_CLOSING, p))
            break

    emojis = _EMOJI_RE.findall(stripped)
    if emojis:
        # 去重保序，最多列 5 个，避免片段过长。
        seen: list[str] = []
        for e in emojis:
            if e not in seen:
                seen.append(e)
        out.append(StyleViolation(RULE_EMOJI, "".join(seen[:5])))

    return out


@dataclass
class StyleMetrics:
    """一套用例的风格违规聚合（按规则计数 + 整体干净率）。"""

    total: int = 0  # 计入的（非 errored、有正文）回复数
    clean: int = 0  # 零违规的回复数
    per_rule: dict[str, int] = field(default_factory=dict)  # rule -> 命中回复数
    # (case_id, [命中的 rule]) ——仅收有违规的，供逐条排查。
    offenders: list[tuple[str, list[str]]] = field(default_factory=list)

    @property
    def clean_rate(self) -> float | None:
        return self.clean / self.total if self.total else None

    def violation_rate(self, rule: str) -> float | None:
        """某规则的违规率 = 命中该规则的回复数 / 计入总数。"""
        return self.per_rule.get(rule, 0) / self.total if self.total else None


def style_metrics(reports: list[CaseReport]) -> StyleMetrics:
    """在一套 :class:`CaseReport` 的 ``outcome.content`` 上跑 linter、聚合违规率（纯算术）.

    跳过 errored（跑挂 ≠ 风格问题）与空正文（无可 lint）。同 case_id 多采样各自计入
    （与 ``EvalReport`` 同口径）。
    """
    m = StyleMetrics()
    for r in reports:
        if r.outcome.error is not None:
            continue
        content = r.outcome.content
        if not content or not content.strip():
            continue
        m.total += 1
        violations = style_violations(content)
        if not violations:
            m.clean += 1
            continue
        rules = sorted({v.rule for v in violations})
        for rule in rules:
            m.per_rule[rule] = m.per_rule.get(rule, 0) + 1
        m.offenders.append((r.case_id, rules))
    return m


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def style_metrics_to_dict(m: StyleMetrics) -> dict:
    """JSON-able dict（落盘 baseline / 回归对比）。"""
    return {
        "total": m.total,
        "clean": m.clean,
        "clean_rate": m.clean_rate,
        "per_rule": dict(m.per_rule),
        "violation_rates": {
            rule: m.violation_rate(rule) for rule in (RULE_OPENING, RULE_CLOSING, RULE_EMOJI)
        },
        "offenders": [{"case_id": cid, "rules": rules} for (cid, rules) in m.offenders],
    }


def format_style_report(m: StyleMetrics) -> str:
    """控制台文本：干净率 + 各规则违规率 + 逐条 offender。ASCII 标记防 Windows 乱码。"""
    lines: list[str] = ["=" * 64, "AgentCore 输出风格违规（anti-slop linter）", "=" * 64]
    lines.append(f"  计入回复 {m.total}    干净 {m.clean}    干净率 {_pct(m.clean_rate)}")
    lines.append("-" * 64)
    lines.append(
        f"  套话开场   {m.per_rule.get(RULE_OPENING, 0)}    "
        f"率 {_pct(m.violation_rate(RULE_OPENING))}"
    )
    lines.append(
        f"  客套收尾   {m.per_rule.get(RULE_CLOSING, 0)}    "
        f"率 {_pct(m.violation_rate(RULE_CLOSING))}"
    )
    lines.append(
        f"  未授权emoji {m.per_rule.get(RULE_EMOJI, 0)}    "
        f"率 {_pct(m.violation_rate(RULE_EMOJI))}"
    )
    if m.offenders:
        lines.append("-" * 64)
        lines.append("  违规逐条:")
        for cid, rules in m.offenders:
            lines.append(f"    {cid}: {', '.join(rules)}")
    lines.append("=" * 64)
    return "\n".join(lines)
