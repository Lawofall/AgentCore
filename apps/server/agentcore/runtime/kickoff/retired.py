"""Honest failure for leftover team_preview frames (开工卡已退役)."""

from __future__ import annotations

from typing import Any

from agentcore.core.errors import GoneError

TEAM_PREVIEW_UNRECOVERABLE = "开工卡已退役，不可恢复"
# Leftover DB / sidecar frame discriminator — not a live SuspensionKind.
LEFTOVER_TEAM_PREVIEW_KIND = "team_preview"


def is_leftover_team_preview_frame(frame: object) -> bool:
    """True when a persisted pause frame is a leftover开工卡 (kind string only)."""
    return isinstance(frame, dict) and frame.get("kind") == LEFTOVER_TEAM_PREVIEW_KIND


def refuse_team_preview_resume() -> None:
    """Raise :class:`GoneError` — leftover kickoff cards are not recoverable."""
    raise GoneError(TEAM_PREVIEW_UNRECOVERABLE)


def refuse_if_leftover_team_preview(frame: dict[str, Any] | object | None) -> None:
    """410 when ``frame`` is a leftover开工卡; no-op otherwise."""
    if is_leftover_team_preview_frame(frame):
        refuse_team_preview_resume()
