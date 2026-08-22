"""Honest failure for leftover team_preview frames (开工卡已退役)."""

from __future__ import annotations

from agentcore.core.errors import GoneError

TEAM_PREVIEW_UNRECOVERABLE = "开工卡已退役，不可恢复"


def refuse_team_preview_resume() -> None:
    """Raise :class:`GoneError` — leftover kickoff cards are not recoverable."""
    raise GoneError(TEAM_PREVIEW_UNRECOVERABLE)
