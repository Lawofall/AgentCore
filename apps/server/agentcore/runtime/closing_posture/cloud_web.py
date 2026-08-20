"""Cloud Web install/verify honesty latch + soft banner.

案 20260803-cloud-web-install-deny-claim-verified · A+B
Soft only: prefix banner when this turn had structured install deny / verify gap
and prose claims「自检全过 / 跑绿 / 全部通过」. Never discard/reject.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Any

from .core import _positive_hits

_turn_cloud_web_verify_gap: ContextVar[bool] = ContextVar(
    "turn_cloud_web_verify_gap", default=False
)

_CLOUD_WEB_VERIFY_GREEN_CLAIMS = re.compile(
    r"(?:"
    r"自检(?:链路)?全部通过|自检全过|"
    r"(?:后端)?单测已(?:跑)?绿|测试已跑绿|已跑绿|跑绿|"
    r"测试全部通过|全部测试通过|"
    r"沙箱内.{0,32}全部通过|"
    r"全部通过|"
    # 案 88625：记分板假绿（不进姿势 A；仅 verify_gap 闩锁时软横幅）
    r"\d+\s*/\s*\d+\s*OK|"
    r"\d+\s+passed|"
    r"(?:unittest|pytest).{0,48}(?:passed|OK)"
    r")"
)

_CLOUD_WEB_VERIFY_HONESTY_BANNER = (
    "【验证说明】本回合有装包被拒或外环验证缺口——下文若写「自检全过 / 跑绿 / 全部通过 / "
    "N/N OK / passed」，可能把结构自检或失败工具卡说成了外环验绿。云端不能代跑 "
    "install→build/test 或本轮 test_run 未成功时，请标明未装包/未外环验绿或工具卡未通过，"
    "并给出本机命令或 export_to_local。\n\n"
)


def note_cloud_web_verify_gap() -> None:
    """Latch turn-scoped install-deny / verify-gap evidence (survives batch overwrite)."""
    _turn_cloud_web_verify_gap.set(True)


def clear_cloud_web_verify_gap() -> None:
    """Reset at turn entry (fresh arm / resume wire)."""
    _turn_cloud_web_verify_gap.set(False)


def turn_has_cloud_web_verify_gap() -> bool:
    """True when this turn noted structured install deny or verify gap."""
    return bool(_turn_cloud_web_verify_gap.get())


def claims_cloud_web_verify_green(content: str) -> bool:
    """True when prose asserts install→build/test all-green (closed set)."""
    return _positive_hits(_CLOUD_WEB_VERIFY_GREEN_CLAIMS, content or "")


def note_cloud_web_verify_gap_from_delivery(
    gaps: list[Any] | None = None,
    *,
    criteria_gaps: list[str] | None = None,
) -> None:
    """Stamp latch from delivery_status gaps / soft verify overlay notes."""
    for gap in gaps or []:
        if not isinstance(gap, dict):
            text = str(gap or "")
            if _delivery_text_implies_verify_gap(text):
                note_cloud_web_verify_gap()
                return
            continue
        reason = str(gap.get("reason") or "").strip()
        if reason in ("verify_failed", "verify_budget"):
            note_cloud_web_verify_gap()
            return
        if _delivery_text_implies_verify_gap(str(gap.get("description") or "")):
            note_cloud_web_verify_gap()
            return
    for note in criteria_gaps or []:
        if _delivery_text_implies_verify_gap(str(note or "")):
            note_cloud_web_verify_gap()
            return


def _delivery_text_implies_verify_gap(text: str) -> bool:
    t = text or ""
    if not t:
        return False
    if "无法装包" in t:
        return True
    if "建议补一次验证" in t:
        return True
    if "测试未通过" in t or "验证命令未通过" in t:
        return True
    if "预算耗尽" in t or "验证未完成" in t:
        return True
    return "未成功打开目标页" in t or "browser_navigate 未成功" in t


def enforce_cloud_web_verify_honesty(content: str) -> str:
    """Prefix honesty banner when green claims meet install-deny / verify-gap latch.

    Does not rewrite or block the turn — soft backstop only (案 B).
    """
    text = content or ""
    if not turn_has_cloud_web_verify_gap():
        return text
    if not claims_cloud_web_verify_green(text):
        return text
    stripped = text.lstrip()
    if stripped.startswith("【验证说明】"):
        return text
    return _CLOUD_WEB_VERIFY_HONESTY_BANNER + text
