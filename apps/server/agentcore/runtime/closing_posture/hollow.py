"""Hollow teach-invite / in-progress claim detectors.

ceiling / cutoff 后空心「请开讲」——把硬顶/空交接伪装成可继续教。
超席/空交接后禁止「仍在进行」空悬终态（须 PARTIAL + 缺口）。
User-visible 【收口说明】 prefixes are gone; detectors remain for finish_guard.
"""

from __future__ import annotations

import re

_HOLLOW_TEACH_INVITES = re.compile(
    r"(?:"
    r"请(?:你)?(?:开始)?(?:开讲|讲)|"
    r"请讲(?:吧|啊|，|,)|"
    r"我在听|"
    r"请你开始教|"
    r"开始教我|"
    r"好[，,]\s*我在听"
    r")"
)

_HOLLOW_IN_PROGRESS_CLAIMS = re.compile(
    r"(?:"
    r"仍在进行|"
    r"尚未形成最终(?:审查)?结论|"
    r"最终审查结论尚未|"
    r"目前仍在"
    r")"
)


def claims_hollow_teach_invite(content: str) -> bool:
    """True when prose hollow-invites the user to start teaching / speaking."""
    return bool(_HOLLOW_TEACH_INVITES.search(content or ""))


def claims_hollow_in_progress(content: str) -> bool:
    """True when prose hangs on『仍在进行』without a final partial delivery."""
    return bool(_HOLLOW_IN_PROGRESS_CLAIMS.search(content or ""))
