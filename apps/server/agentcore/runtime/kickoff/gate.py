"""Kickoff trigger rules — shared helpers for leftover fold / governance."""

from __future__ import annotations

import re

# Short verbal affirmations (governance filters these out of "real user intent"
# chunks). Never used to skip team_preview — ask ⊥ kickoff.
_AFFIRM_RE = re.compile(
    r"^(好的?|可以|行|没问题|同意|认可|就这样|按这个|按此|按方案|开干|继续|开始吧?|"
    r"ok|okay|yes|yep|sure|go|lgtm)[.!！。…]*$",
    re.IGNORECASE,
)


def is_short_affirmation(text: str) -> bool:
    """True for short verbal affirmations (e.g. 「好的」「认可」).

    Used by governance to filter non-intent user turns; does **not** skip kickoff.
    """
    compact = re.sub(r"\s+", "", text)
    if not compact or len(compact) > 24:
        return False
    return _AFFIRM_RE.match(compact) is not None
