"""跨回合 ``<上轮交付缺口>`` 易变尾已撤：对账仍 stamp ``delivery_status``，不抄进下一轮提示。"""

from __future__ import annotations

import pytest

from agentcore.runtime.resolve.prompt import render_ceo_turn_envelope


def test_prior_delivery_gaps_module_is_gone():
    with pytest.raises(ImportError):
        from agentcore.runtime.delegate import prior_delivery_gaps  # noqa: F401


def test_ceo_turn_prompt_has_no_prior_delivery_gaps_section():
    out = render_ceo_turn_envelope(
        attachment_context="",
        include_runtime=False,
    )
    assert "上轮交付缺口" not in out
    assert "prior_delivery_gaps" not in out
