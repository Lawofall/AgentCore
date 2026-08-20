"""Unit tests for tick replay assembly (BE-28)."""

from __future__ import annotations

import pytest

from agentcore.core.errors import ValidationError
from agentcore.runtime.events import EventType
from agentcore.simulation.service import _replay_event_type


def test_replay_event_type_maps_sim_and_interaction_kinds():
    assert _replay_event_type("sim.tick_started") == EventType.SIM_TICK_STARTED
    assert _replay_event_type("trade") == EventType.SIM_INTERACTION


def test_replay_event_type_skips_retired_sim_show():
    assert _replay_event_type("sim.show.heart_pick") is None
    assert _replay_event_type("sim.show.episode_gate") is None


def test_replay_event_type_skips_retired_question_posted():
    from agentcore.runtime.events.types import RETIRED_EVENT_TYPE_VALUES

    for name in RETIRED_EVENT_TYPE_VALUES:
        assert _replay_event_type(name) is None


def test_replay_event_type_rejects_unknown():
    with pytest.raises(ValidationError):
        _replay_event_type("not.a.real.event")
