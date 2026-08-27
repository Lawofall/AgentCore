"""Vertical / feature toggles (opt-in capability packs layered on the core)."""

from pydantic import BaseModel


class FeatureSettings(BaseModel):
    # Legal vertical deployment gate (法律垂直「答辩状作战室」). When off, the legal
    # capability pack is invisible and not registered — same outward posture as before.
    # When on, the pack appears in GET /v1/capabilities packs[] and its skills are
    # registered into every user's runtime (no per-user toggle).
    legal_vertical_enabled: bool = False
