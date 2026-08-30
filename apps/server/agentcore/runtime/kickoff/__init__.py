"""Orchestration-layer kickoff helpers — shared by ``delegate`` and ``debate``.

New ``team_preview`` cards are not emitted. Leftover hung frames are not
recovered (honest fail). Leftover ``stage_card`` resolve is 410；开辩须用户点名。
"""

from __future__ import annotations

from agentcore.runtime.kickoff.adjust_guidance import (
    KICKOFF_ADJUST_GUIDANCE_DEBATE,
    KICKOFF_ADJUST_GUIDANCE_DELEGATE,
    format_kickoff_adjust_result,
)
from agentcore.runtime.kickoff.cancel_guidance import (
    KICKOFF_CANCEL_GUIDANCE,
    KICKOFF_TIMEOUT_GUIDANCE,
    format_kickoff_cancel_result,
    format_kickoff_timeout_result,
)
from agentcore.runtime.kickoff.debate_host import (
    DebateHostAttach,
    resolve_debate_host_attach,
)
from agentcore.runtime.kickoff.gate import is_short_affirmation
from agentcore.runtime.kickoff.research_first import research_first_tool_result
from agentcore.runtime.kickoff.retired import (
    LEFTOVER_TEAM_PREVIEW_KIND,
    STAGE_CARD_UNRECOVERABLE,
    TEAM_PREVIEW_UNRECOVERABLE,
    is_leftover_team_preview_frame,
    refuse_if_leftover_team_preview,
    refuse_stage_card_resolve,
    refuse_team_preview_resume,
)
from agentcore.runtime.kickoff.revision import (
    KickoffAdjustState,
    has_unfulfilled_kickoff_adjust,
    kickoff_adjust_state,
    kickoff_turn_journal,
)
from agentcore.runtime.kickoff.stage_card import (
    apply_motion_override,
    build_stage_card_payload,
    clear_turn_keeps_stage_card,
    emit_stage_card_for_motion,
    mark_turn_keeps_stage_card,
    turn_keeps_stage_card,
)
from agentcore.runtime.kickoff.summary import (
    SESSION_DESK_LABEL,
    UNNAMED_DESK_LABEL,
    KickoffPrimitive,
    KickoffSummary,
    debate_kickoff_summary,
    delegate_kickoff_summary,
    enrich_worker_desk_names,
    format_kickoff_headline,
    intensity_short_label,
    worker_rows,
)

__all__ = [
    "DebateHostAttach",
    "KickoffAdjustState",
    "KickoffPrimitive",
    "KickoffSummary",
    "LEFTOVER_TEAM_PREVIEW_KIND",
    "SESSION_DESK_LABEL",
    "TEAM_PREVIEW_UNRECOVERABLE",
    "STAGE_CARD_UNRECOVERABLE",
    "UNNAMED_DESK_LABEL",
    "apply_motion_override",
    "build_stage_card_payload",
    "clear_turn_keeps_stage_card",
    "debate_kickoff_summary",
    "delegate_kickoff_summary",
    "emit_stage_card_for_motion",
    "enrich_worker_desk_names",
    "format_kickoff_headline",
    "format_kickoff_adjust_result",
    "format_kickoff_cancel_result",
    "has_unfulfilled_kickoff_adjust",
    "kickoff_adjust_state",
    "kickoff_turn_journal",
    "format_kickoff_timeout_result",
    "intensity_short_label",
    "KICKOFF_ADJUST_GUIDANCE_DEBATE",
    "KICKOFF_ADJUST_GUIDANCE_DELEGATE",
    "KICKOFF_CANCEL_GUIDANCE",
    "KICKOFF_TIMEOUT_GUIDANCE",
    "worker_rows",
    "mark_turn_keeps_stage_card",
    "is_leftover_team_preview_frame",
    "is_short_affirmation",
    "refuse_if_leftover_team_preview",
    "refuse_stage_card_resolve",
    "refuse_team_preview_resume",
    "research_first_tool_result",
    "resolve_debate_host_attach",
    "turn_keeps_stage_card",
]
