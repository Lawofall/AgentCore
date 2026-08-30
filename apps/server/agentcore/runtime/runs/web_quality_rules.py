"""Shared anti-slop / web-quality rule catalog (prompt text ↔ scan fingerprints).

Single source for :mod:`agentcore.runtime.runs.web_quality_scan` pattern labels — keep the blacklist
wording and the detector vocabulary in lockstep.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WebQualityRule:
    """One named rule: CEO/worker-facing line + stable ``label`` used by the scanner."""

    label: str
    prompt_line: str
    kind: str  # "soft" (anti-slop) | "hard" (fabricated contact / syntax — docs only for hard)


# Soft visual anti-slop — scan fingerprints must use the same ``label`` strings.
SOFT_ANTI_SLOP_RULES: tuple[WebQualityRule, ...] = (
    WebQualityRule(
        label="默认系统字体栈/Inter·Poppins味",
        prompt_line=(
            "禁默认系统字体栈套路与 Inter / Poppins / Roboto / Arial 味；"
            "营销页须有明确展示字体方向"
        ),
        kind="soft",
    ),
    WebQualityRule(
        label="紫蓝渐变+glow默认皮",
        prompt_line="禁紫蓝渐变 + glow / 霓虹光晕默认皮（未获用户明示时）",
        kind="soft",
    ),
    WebQualityRule(
        label="三等分feature卡八股",
        prompt_line="禁三等分 feature 卡八股（三列等宽图标+短标题+两行说明）",
        kind="soft",
    ),
    WebQualityRule(
        label="pill badge+渐变字堆叠",
        prompt_line="禁 pill badge + 渐变字标题堆叠开场",
        kind="soft",
    ),
    WebQualityRule(
        label="emoji当图标",
        prompt_line="禁用 emoji 充当功能图标 / 卖点图标",
        kind="soft",
    ),
    WebQualityRule(
        label="装饰粒子canvas",
        prompt_line="禁装饰性粒子 / 星尘 canvas 背景凑气氛",
        kind="soft",
    ),
    WebQualityRule(
        label="重复数字墙",
        prompt_line="禁重复数字墙（一排假指标 / 假用户数堆砌）",
        kind="soft",
    ),
    WebQualityRule(
        label="首屏多构图堆叠",
        prompt_line="首屏须单一构图：品牌 + 一句主文案 + 短支持句 + CTA，勿塞统计条/日程/多卡",
        kind="soft",
    ),
    WebQualityRule(
        label="lorem/假联系方式板块",
        # Wording avoids the literal "lorem ipsum" digraph so DESIGN.md / task briefs
        # that restate this blacklist are not themselves flagged by placeholder_scan.
        prompt_line=(
            "禁假拉丁文填充段；无真实联系方式则【不设】联系我们 / 备案 / 电话邮箱板块"
            "（禁编造）"
        ),
        kind="soft",
    ),
)


def soft_rule_labels() -> frozenset[str]:
    return frozenset(r.label for r in SOFT_ANTI_SLOP_RULES)
