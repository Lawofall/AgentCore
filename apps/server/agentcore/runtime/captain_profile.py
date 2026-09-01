"""CEO captain profile tweaks (round budget, etc.)."""

from __future__ import annotations

from dataclasses import replace

from agentcore.config import settings
from agentcore.llm.profiles import ProfileParams


def apply_captain_max_rounds(profile: ProfileParams) -> ProfileParams:
    """Optional ops raise: only when ``engine_captain_max_rounds`` is configured >0.

    Product default is 0 (no captain round fuse). Explicit positive profile
    caps (tests / eval clamps) are left unchanged when already at or above the knob.
    """
    cap = settings.engine_captain_max_rounds
    if cap <= 0 or profile.max_rounds >= cap:
        return profile
    return replace(profile, max_rounds=cap)
