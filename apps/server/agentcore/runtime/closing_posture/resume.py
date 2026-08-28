"""Resume / continuity: process-kickoff strip, stale-ask rewrite, join, steer.

resume / plan_review：派工过程 kickoff（方向：派团队…）不进用户可见续写基底与 G6 重灌，
终稿另写交付说明，避免过程流水账（ce1ecfc2）。
"""

from __future__ import annotations

import re

from .core import (
    claims_posture_a,
    claims_posture_c,
)

# 派工/过程开工段（plan_review 前常见）：不得当「已交付前文」拼接或 G6 重灌进终稿气泡。
# 近零误报：显式派工话术；「阶段成果如下」等半交付续写故意不进。
_PROCESS_DISPATCH_PREAMBLE = re.compile(
    r"(?:"
    r"方向：派团队|"
    r"派团队\s*[—\-\u2013\u2014]|"
    r"直接开委派|"
    r"组建团队|"
    r"我(?:先)?派(?:出)?(?:\d+路|三路|各路|团队|队员)|"
    r"并行(?:开)?(?:派|调研)"
    r")"
)

# Retired engine pause fill（方案 2：不再注入）；旧 transcript 整段仍当空壳，避免冲掉实质续写。
_LEGACY_ASK_PAUSE_HOLLOW = "等待确认后再派工；此前尚未真正开工。"


def is_process_dispatch_preamble(content: str) -> bool:
    """True when prose is a dispatch/kickoff process note, not a deliverable half."""
    text = (content or "").strip()
    if not text:
        return False
    return bool(_PROCESS_DISPATCH_PREAMBLE.search(text))


def _is_hollow_pause_text(text: str) -> bool:
    """Empty or retired wait-confirm constant alone — not a deliverable base."""
    stripped = (text or "").strip()
    return (not stripped) or stripped == _LEGACY_ASK_PAUSE_HOLLOW


def pre_pause_for_user_visible_continuity(pre_pause: str) -> str:
    """Strip process kickoff so it cannot seed bubble reinject / join as deliverable base.

    Ask-confirm framing is handled separately（卡片承载）；派工 kickoff 同理：过程已发生，
    终稿应另写交付说明，而非接着「方向：派团队」续写。

    Hollow / legacy pause-only text is not a deliverable base either —
    reinjecting it would freeze the bubble on empty confirm copy.
    """
    text = pre_pause or ""
    if is_process_dispatch_preamble(text):
        return ""
    if _is_hollow_pause_text(text):
        return ""
    return text


# 案 20260803-ask-empty-continue-default-dispatch · C：已派工后剥掉与事实互斥的「先问你」残留。
_STALE_ASK_ROUTE_LINE = re.compile(r"(?m)^[ \t]*方向：先问你[^\n]*\n+")
_STALE_PLEASE_CHOOSE_LINE = re.compile(r"(?m)^[ \t]*请选择[：:][^\n]*\n+")


def rewrite_stale_ask_after_dispatch(content: str) -> str:
    """Soft：已派工/按确认默认时剥掉「先问你」叠写，改为「已按默认开工」（不丢交付正文）。"""
    text = content or ""
    if not text.strip():
        return text
    has_ask_residual = ("方向：先问你" in text) or bool(
        re.search(r"(?m)^[ \t]*请选择[：:]", text)
    )
    if not has_ask_residual:
        return text
    dispatched = (
        is_process_dispatch_preamble(text)
        or "按确认默认" in text
        or "已按默认开工" in text
        or bool(re.search(r"派(?:一名|出)?(?:队员|团队)", text))
    )
    if not dispatched:
        return text
    dispatch_at = text.find("方向：派团队")
    ask_at = text.find("方向：先问你")
    if dispatch_at >= 0 and ask_at >= 0 and ask_at < dispatch_at:
        rest = text[dispatch_at:]
        if "已按默认开工" not in rest and "按确认默认" not in rest:
            return "已按默认开工。\n\n" + rest
        return rest
    rewritten = _STALE_ASK_ROUTE_LINE.sub("", text)
    rewritten = _STALE_PLEASE_CHOOSE_LINE.sub("", rewritten)
    if rewritten == text:
        return text
    if "已按默认开工" not in rewritten and "按确认默认" not in rewritten:
        return "已按默认开工。\n\n" + rewritten.lstrip()
    return rewritten


def reconcile_resume_closing(
    pre_pause: str, new: str, *, ask_settled: bool = False
) -> str:
    """Join resume segments without creating A∪C or process-kickoff∪交付流水账.

    When pre-pause still carries「请确认」 framing (often leftover ask prose) and the
    post-resume segment claims posture A, keep only the post-resume segment —
    the question already lived on the ask_user card; splicing recreates cef27dfa /
    e8fb470c dishonest closings.

    When this resume **settled an ask_user card** (``ask_settled=True``), keep only
    the post-resume segment whenever it has substance — even if leftover confirm
    prose misses the posture-C closed set (``先确认几个关键`` vs ``先确认(?:一个)?关键``).
    Do not expand that closed set; the card settlement is the join signal (67a9e6d6).

    When pre-pause is ask framing（方向：先问你…）and post-resume already dispatched
    （方向：派团队… / 按确认默认）, keep only the post-resume segment — empty continue
    accepted the default; stacking「先问你」+「派团队」is dishonest（0cb83288）.

    When pre-pause is a dispatch/process kickoff（方向：派团队…）and post-resume has
    content, keep only the post-resume segment — kickoff must not become the opening
    of the user-visible交付说明（ce1ecfc2 过程流水账）.

    When post-resume is only hollow / legacy pause fill and pre-pause still has
    structured confirm substance, keep pre-pause（禁空确认壳冲掉上轮选项）.
    """
    left = pre_pause or ""
    right = new or ""
    # Hollow-only pre_pause is not substance — treat as empty so later content wins.
    if _is_hollow_pause_text(left):
        left = ""
    if not left.strip():
        return rewrite_stale_ask_after_dispatch(right)
    if not right.strip():
        return left
    # Empty / legacy pause shell must not wipe structured confirm substance.
    if _is_hollow_pause_text(right):
        return left
    if ask_settled:
        return rewrite_stale_ask_after_dispatch(right)
    if claims_posture_c(left) and claims_posture_a(right):
        return rewrite_stale_ask_after_dispatch(right)
    if claims_posture_a(left) and claims_posture_c(right):
        # Rare: prior claimed done, resume asks again — prefer the later ask.
        return right
    if claims_posture_c(left) and (
        is_process_dispatch_preamble(right)
        or "按确认默认" in right
        or "已按默认开工" in right
    ):
        return rewrite_stale_ask_after_dispatch(right)
    if is_process_dispatch_preamble(left):
        return rewrite_stale_ask_after_dispatch(right)
    from agentcore.runtime.engine.segments import join_segments

    return rewrite_stale_ask_after_dispatch(join_segments(left, right))


def resume_continuity_steer(*, prior_deliverable: str) -> str:
    """Steer the resumed CEO round; avoid amplifying stale confirm / kickoff framing."""
    prior = (prior_deliverable or "").strip()
    if prior and claims_posture_c(prior) and not claims_posture_a(prior):
        return (
            "[系统提示] 用户已通过确认卡作答。请基于用户答复推进下一步。"
            "若卡上有预填 default 且用户空 continue = 确认该 default："
            "派工/正文须用该 default 并标「按确认默认」。"
            "上轮已给出确认选项时须承接，【禁止】空转确认、不承接选项。"
            "有交付对账卡时以档位为准；非正式完成不得姿势 A。"
        )
    if prior and is_process_dispatch_preamble(prior):
        return (
            "[系统提示] 用户已确认计划/委派，派工过程段不要续进终稿。"
            "请另写一份给用户的交付说明；不要工作日志。"
            "有交付对账卡时以档位为准；非正式完成不得姿势 A。"
        )
    from agentcore.runtime.engine.segments import deliverable_continuity_instruction

    return deliverable_continuity_instruction(prior_deliverable=prior_deliverable)
