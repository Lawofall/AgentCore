"""Orchestration-layer kickoff helpers — shared by ``delegate`` and ``debate``.

New ``team_preview`` cards are not emitted. Leftover hung frames are not
recovered (honest fail). ``stage_card`` still starts debate via
``skip_kickoff=True``.
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
from agentcore.runtime.kickoff.gate import (
    is_short_affirmation,
    should_preview_delegate_plan,
)
from agentcore.runtime.kickoff.pause import kickoff_tools
from agentcore.runtime.kickoff.research_first import research_first_tool_result
from agentcore.runtime.kickoff.retired import (
    TEAM_PREVIEW_UNRECOVERABLE,
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
    consume_mlr_preauth,
    discard_mlr_preauth,
    emit_stage_card_for_motion,
    grant_mlr_preauth,
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
from agentcore.runtime.kickoff.team_veto import (
    WriteCapabilityOverride,
    apply_debate_model_overrides,
    apply_team_preview_veto,
    should_apply_debate_model_overrides,
    should_apply_team_veto,
    validate_debate_model_overrides,
    validate_team_preview_veto,
    validate_team_preview_veto_workers,
)

__all__ = [
    "DebateHostAttach",
    "KickoffAdjustState",
    "KickoffPrimitive",
    "KickoffSummary",
    "SESSION_DESK_LABEL",
    "TEAM_PREVIEW_UNRECOVERABLE",
    "UNNAMED_DESK_LABEL",
    "WriteCapabilityOverride",
    "apply_debate_model_overrides",
    "apply_motion_override",
    "apply_team_preview_veto",
    "build_stage_card_payload",
    "clear_turn_keeps_stage_card",
    "consume_mlr_preauth",
    "debate_kickoff_summary",
    "delegate_kickoff_summary",
    "discard_mlr_preauth",
    "emit_stage_card_for_motion",
    "enrich_worker_desk_names",
    "format_kickoff_headline",
    "grant_mlr_preauth",
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
    "kickoff_tools",
    "worker_rows",
    "mark_turn_keeps_stage_card",
    "is_short_affirmation",
    "refuse_team_preview_resume",
    "research_first_tool_result",
    "resolve_debate_host_attach",
    "should_apply_debate_model_overrides",
    "should_apply_team_veto",
    "should_preview_delegate_plan",
    "turn_keeps_stage_card",
    "validate_debate_model_overrides",
    "validate_team_preview_veto",
    "validate_team_preview_veto_workers",
]
