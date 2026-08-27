"""INTERACTION_KIND_SPECS is the complete interaction-form declaration.

Derived frozensets (hot / gate / recovery / journal surface / attention /
durable / settlement) must stay in lockstep with the table — no parallel
hand-copied kind lists.
"""

from __future__ import annotations

from agentcore.attention.signal import AttentionKind
from agentcore.runtime.events import _JOURNAL_SURFACE_TYPES, EventType
from agentcore.runtime.interaction import (
    ATTENTION_KINDS,
    DURABLE_INTERACTION_KINDS,
    GATE_KINDS,
    HOT_KINDS,
    INTERACTION_KIND_SPECS,
    JOURNAL_SURFACE_EVENTS,
    RECOVERY_PENDING_KINDS,
    InteractionKind,
    is_hot_user_pending_kind,
    is_user_answerable,
)
from agentcore.runtime.settlement import SETTLEMENT_EVENT_KINDS
from agentcore.runtime.suspension import DURABLE_INTERACTION_KINDS as SUSPENSION_DURABLE


def test_specs_cover_every_user_facing_kind() -> None:
    assert set(INTERACTION_KIND_SPECS) == set(InteractionKind) - {InteractionKind.CLIENT_TOOL}


def test_derived_behavior_sets_match_declared_flags() -> None:
    assert frozenset({"approval", "escalation"}) == HOT_KINDS
    assert frozenset({"approval", "ask_user", "plan_review"}) == GATE_KINDS
    assert frozenset({"approval", "escalation", "stage_card"}) == RECOVERY_PENDING_KINDS
    assert frozenset({"approval", "escalation", "ask_user", "plan_review"}) == ATTENTION_KINDS
    assert frozenset({InteractionKind.ASK_USER, InteractionKind.PLAN_REVIEW}) == DURABLE_INTERACTION_KINDS
    assert SUSPENSION_DURABLE is DURABLE_INTERACTION_KINDS


def test_journal_surface_includes_every_spec_required_event() -> None:
    assert EventType.STAGE_CARD_REQUIRED.value in JOURNAL_SURFACE_EVENTS
    assert EventType.STAGE_CARD_REQUIRED.value in _JOURNAL_SURFACE_TYPES
    for spec in INTERACTION_KIND_SPECS.values():
        if spec.journal_surface:
            assert spec.required_event in _JOURNAL_SURFACE_TYPES


def test_settlement_event_kinds_are_derived_from_specs() -> None:
    expected = frozenset(
        {
            EventType.APPROVAL_RESOLVED.value,
            EventType.ESCALATION_RESOLVED.value,
            EventType.CHECKPOINT_RESOLVED.value,
            EventType.PLAN_REVIEW_RESOLVED.value,
            EventType.STAGE_CARD_REQUIRED.value,
            EventType.STAGE_CARD_RESOLVED.value,
            EventType.INTERACTION_ORPHANED.value,
        }
    )
    assert expected == SETTLEMENT_EVENT_KINDS


def test_attention_kind_enum_matches_spec() -> None:
    assert {k.value for k in AttentionKind} == ATTENTION_KINDS


def test_awaiting_ceo_is_instance_filter_not_kind_set() -> None:
    assert is_hot_user_pending_kind("escalation", {"awaiting": "user"})
    assert not is_hot_user_pending_kind("escalation", {"awaiting": "ceo"})
    assert is_hot_user_pending_kind("approval", {})
    assert not is_hot_user_pending_kind("stage_card", {})
    assert is_user_answerable("escalation", {"awaiting": "user"})
    assert not is_user_answerable("escalation", {"awaiting": "ceo"})
    assert not is_user_answerable("client_tool", {})
