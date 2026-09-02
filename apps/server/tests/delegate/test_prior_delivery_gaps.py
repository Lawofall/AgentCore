"""跨回合 ``<上轮交付缺口>`` 易变尾已撤：对账仍 stamp ``delivery_status``，不抄进下一轮提示。"""

from __future__ import annotations

import pytest

from agentcore.runtime.pipeline.assemble import build_chat_system_prompt
from agentcore.runtime.skills import build_system_skill_registry


def test_prior_delivery_gaps_module_is_gone():
    with pytest.raises(ImportError):
        from agentcore.runtime.delegate import prior_delivery_gaps  # noqa: F401


def test_ceo_turn_prompt_has_no_prior_delivery_gaps_section():
    out = build_chat_system_prompt(
        ceo_prompt="CEO",
        prior_delegate_retry="",
        attachment_context="",
        registered_sources="",
        soft_cap=None,
    )
    assert "上轮交付缺口" not in out
    assert "prior_delivery_gaps" not in out


def test_ask_user_kickoff_does_not_force_gap_continue():
    skill = build_system_skill_registry().get("asking_the_user")
    assert skill is not None
    body = skill.body
    assert "短确认·只补缺口" not in body
    assert "<上轮交付缺口>" not in body
    assert "整锅重派" not in body
