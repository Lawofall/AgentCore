"""Tier truth source + posture A/C/draft closed sets + honesty rework orchestration.

真源是 ``delivery_verdict.state``（非完成话术词表）：

- ``delivered`` = 正式完成（允许姿势 A）
- ``partial`` / ``notes`` ≈ 草稿·部分（禁止姿势 A；``requires_draft_ack`` 时另须正文承认缺口）
- ``blocked`` = 阻塞（禁止姿势 A；``requires_draft_ack`` 时另须承认缺口）

姿势 A = 宣称完整交付 / 全员收卷 / 完整可用 / 修好验绿。
探测用**闭集**正则，仅作「是否在说 A」的薄信号；**禁止**靠案面加完成话术词修案。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from agentcore.core.logging import get_logger

if TYPE_CHECKING:
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

logger = get_logger(__name__)

# 正式完成档（唯一允许姿势 A 的对账态）。
_FORMAL_COMPLETE_TIERS = frozenset({"delivered"})
# 非正式完成：草稿·部分·阻塞 —— 不得姿势 A。
_INFORMAL_TIERS = frozenset({"partial", "notes", "blocked"})

# (A) 完整交付宣称闭集。故意不含裸「已完成 / 已交付 / 弱可用」——修码/建站正常收口不得误伤。
# ✅ 已撤回 20260803「复核/审查/验收通过、已修复、可玩」扩面；乙（修好/验绿）仍在。
# 禁止再往本表加案面词（「综述已完成」「站点做好了」等）；漏拦应回到档位/产物结构，而非加词。
_POSTURE_A_CLAIMS = re.compile(
    r"(?:"
    r"已全部收卷|全部收卷|已收卷|"
    r"已全部收齐|全部收齐|已收齐|"
    r"已完成交付|交付已完成|完成交付|交付完成|已经交付完成|"
    r"已全部(?:完成|交付|就位|成功|就绪)|"
    r"全部(?:完成|交付|就位|成功|就绪)|"
    r"均已(?:完成|交付|就绪|成功|落盘)|"
    r"都已(?:完成|交付|就绪|成功)|"
    r"所有(?:任务|队员|节点)(?:已|都已)(?:完成|交付|就绪)|"
    r"已完整可用|已可以使用|已经可以使用|"
    r"已完全可用|已可直接使用|已经可以直接使用|"
    r"已修好|修复已完成|bug\s*已修复|缺陷已修复|问题已修复|"
    r"已验证通过|验证通过|验证已绿|验证已通过|"
    r"测试已通过|已跑通测试|测试已跑通"
    r")"
)

# (C) 需用户确认 / 关键缺口阻塞——resume 确认姿势用；保持极窄。
# 无对账卡时不再用 A∪C 拦正文。
_POSTURE_C_CLAIMS = re.compile(
    r"(?:"
    r"请确认|"
    r"需要先确认|"
    r"先确认(?:一个)?关键|"
    r"关键(?:信息|缺口|事实)(?:未定|未明确|未齐)|"
    r"关键缺口|"
    r"方向：先问你|"
    r"先问你\s*/\s*关键|"
    r"未明确——"
    r")"
)

# partial/blocked 时正文须出现的「草稿/缺口承认」闭集（正向要求，不是完成话术黑名单）。
# 漏拦「综述已完成」等变体 → 扩本表或靠档位+承认，禁止往姿势 A 加案面词。
_DRAFT_ACK_CLAIMS = re.compile(
    r"(?:"
    r"草稿|"
    r"证据不足|证据不够|证据差|"
    r"部分完成|部分未完成|尚未完成|仍未完成|"
    r"未完成项|关键缺口|仍有缺口|存在缺口|"
    r"待补|待核实|待完善|需改进|"
    r"仅供参考|非正式(?:版|稿)|"
    r"未能(?:检索|完成|交付)|搜不到|"
    r"靠先验|基于先验|基于对该领域的了解"
    r")"
)

_GAP_NEGATION_PREFIXES = ("尚未", "没有", "并未", "未", "没", "无", "勿", "禁止", "不要")

_TIER_LABEL = {
    "blocked": "未满足/阻塞",
    "partial": "部分未满足",
    "notes": "草稿/备注",
}

# 硬降档（evidence_deficit / thin_review / verify_failed / node_failed /
# artifact_rejected → requires_draft_ack）时须承认缺口；普通 partial 不强制。
# （notes 仅软提醒时仍只拦姿势 A；FAILED soft 投影保留 node_failed 则亦闩。）


def is_formal_complete_tier(state: str | None) -> bool:
    """True when delivery_verdict.state allows posture A (正式完成)."""
    return (state or "") in _FORMAL_COMPLETE_TIERS


def tier_forbids_posture_a(state: str | None) -> bool:
    """True when tier is partial/notes/blocked — posture A is dishonest."""
    return (state or "") in _INFORMAL_TIERS


def _positive_hits(pattern: re.Pattern[str], content: str) -> bool:
    """True when pattern matches a non-negated claim."""
    for match in pattern.finditer(content or ""):
        start = match.start()
        # Always honor negation prefixes（尚未全部完成 / 尚未完成交付…）——
        # even when the matched token itself starts with「全部/已」.
        prefix = content[max(0, start - 2) : start]
        if any(prefix.endswith(neg) for neg in _GAP_NEGATION_PREFIXES):
            continue
        return True
    return False


def claims_posture_a(content: str) -> bool:
    """True when prose asserts formal-complete delivery (posture A). Closed set — do not expand."""
    return _positive_hits(_POSTURE_A_CLAIMS, content or "")


def claims_posture_c(content: str) -> bool:
    """True when prose asks the user to confirm a blocking gap (posture C)."""
    return _positive_hits(_POSTURE_C_CLAIMS, content or "")


def claims_draft_acknowledgment(content: str) -> bool:
    """True when prose acknowledges draft / gap / evidence shortfall (partial/blocked)."""
    return bool(_DRAFT_ACK_CLAIMS.search(content or ""))


# Resume / rehydrate 兼容别名（语义 = 姿势 A / C）。
claims_full_delivery = claims_posture_a
claims_needs_confirm = claims_posture_c


def closing_honesty_verdict_hit(
    content: str,
    delivery_verdict: DeliveryVerdict | None,
) -> Literal["posture_a", "draft_ack"] | None:
    """档位诚实性命中（姿势 A / draft_ack 缺失）。不扫 B1、不改词表。"""
    if delivery_verdict is None:
        return None
    text = content or ""
    if not text.strip():
        return None
    state = delivery_verdict.state
    if not tier_forbids_posture_a(state):
        return None
    if claims_posture_a(text):
        return "posture_a"
    if getattr(delivery_verdict, "requires_draft_ack", False) and (
        not claims_draft_acknowledgment(text)
    ):
        return "draft_ack"
    return None


def _log_honesty_shadow(
    hit: Literal["posture_a", "draft_ack", "overview_length"],
    delivery_verdict: DeliveryVerdict,
) -> None:
    """Observe a would-rework hit without applying it (团队路径闸从未生效过).

    ``hit`` 区分闸：``posture_a`` / ``draft_ack``（档位诚实性）或
    ``overview_length``（概览篇幅）。同一事件、同一套字段，不另开影子通道。
    """
    logger.info(
        "engine.finish_guard_honesty_shadow",
        verdict_state=delivery_verdict.state,
        hit=hit,
        has_delivered_files=bool(delivery_verdict.delivered_files),
        gap_reasons=list(getattr(delivery_verdict, "gap_reasons", ()) or ()),
        requires_draft_ack=bool(getattr(delivery_verdict, "requires_draft_ack", False)),
        execution_id=delivery_verdict.execution_id or None,
        tier_label=_TIER_LABEL.get(delivery_verdict.state, delivery_verdict.state),
    )


def closing_honesty_rework(
    content: str,
    delivery_verdict: DeliveryVerdict | None = None,
) -> str | None:
    """档位驱动的收口诚实性回炉项；无档位时不拦正文。

    主路径：``delivery_verdict.state`` ∉ 正式完成 → 不得姿势 A；
    ``requires_draft_ack``（evidence_deficit / thin_review / verify_failed /
    node_failed / artifact_rejected）另须正文出现草稿/缺口承认（正向要求，不靠加完成词）。
    B1：浏览器声称须 tool 成功；超席/空交接/cancel·0 须 PARTIAL 缺口清单。
    零写落盘声称扫词硬回炉已撤（2026-08-09 定案 B）。
    无对账卡（含本轮 ``no_batch``）：不扫完成话术、不回炉；团队状态以结构面为准。

    档位命中（姿势 A / draft_ack）本轮只打影子日志、不回炉——闸在团队路径从未
    真正跑过，须先观测误伤面。B1 结构轴仍回炉（它们不依赖跨 Task verdict）。
    """
    # Late imports: B1 probe axes live in sibling latch modules (avoid import cycles).
    from .b1 import (
        _browser_claim_rework,
        _ceiling_hollow_teach_rework,
        _partial_storm_rework,
        _verify_budget_hollow_rework,
    )

    text = content or ""
    if not text.strip():
        return None

    # B1 structural axes first（真源=装配/tool/对账 latch，不扫用户气泡）。
    browser_hit = _browser_claim_rework(text)
    if browser_hit:
        return browser_hit
    for probe in (
        _partial_storm_rework,
        _verify_budget_hollow_rework,
        _ceiling_hollow_teach_rework,
    ):
        hit = probe(text)
        if hit:
            return hit

    verdict_hit = closing_honesty_verdict_hit(text, delivery_verdict)
    if verdict_hit is not None and delivery_verdict is not None:
        _log_honesty_shadow(verdict_hit, delivery_verdict)
        return None
    return None


def mutual_exclusion_rework(content: str) -> str | None:
    """兼容旧调用：无档位不再拦 A∪C；请用 :func:`closing_honesty_rework`。"""
    return closing_honesty_rework(content, delivery_verdict=None)
