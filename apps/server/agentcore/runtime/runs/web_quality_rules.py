"""Shared anti-slop / web-quality rule catalog (prompt text ↔ scan fingerprints).

Single source for ``web_quality`` deliverable injection and
:mod:`agentcore.runtime.runs.web_quality_scan` pattern labels — keep the blacklist
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


def anti_slop_prompt_block(*, domain: str = "marketing") -> str:
    """Worker-facing blacklist block for playbook task injection.

    ``domain``: ``marketing`` (官网/落地页) vs ``tool`` (产品/控制台工具页) — shared
    blacklist, with a one-line domain hint for aesthetic hard rules.
    """
    domain_line = (
        "【审美域·营销页】视觉 thesis 先行：鲜明品牌气质、少而准的动效、拒绝模板皮。"
        if domain == "marketing"
        else "【审美域·工具页】清晰信息架构与可读性优先；装饰克制，勿套营销着陆页皮。"
    )
    lines = "；".join(r.prompt_line for r in SOFT_ANTI_SLOP_RULES)
    return (
        f"{domain_line}"
        f"【anti-slop 黑名单】{lines}。"
        "用户明示要求某条黑名单风格时，可在该条上豁免并在交接说明。"
    )


def soft_rule_labels() -> frozenset[str]:
    return frozenset(r.label for r in SOFT_ANTI_SLOP_RULES)
