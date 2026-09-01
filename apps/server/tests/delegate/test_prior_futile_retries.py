"""上轮徒劳重试提示块已撤：失败回执在历史里，不再注入易变尾。"""

from __future__ import annotations

import pytest


def test_prior_futile_retries_module_is_gone():
    with pytest.raises(ImportError):
        from agentcore.runtime.delegate import prior_futile_retries  # noqa: F401


def test_ceo_turn_prompt_has_no_futile_retry_section():
    from agentcore.runtime.pipeline.assemble import build_chat_system_prompt

    out = build_chat_system_prompt(
        ceo_prompt="CEO",
        working_set="",
        recent_team_graph="",
        prior_delivery_gaps="",
        prior_delegate_retry="",
        attachment_context="",
        registered_sources="",
        soft_cap=None,
    )
    assert "上轮徒劳重试" not in out
