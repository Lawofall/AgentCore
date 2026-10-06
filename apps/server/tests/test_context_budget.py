"""Assembly context budget: steps, snap, and the effective window."""

import pytest

from agentcore.core.errors import ValidationError
from agentcore.llm.context_budget import (
    bind_context_budget,
    bind_profile_context_budget,
    coerce_context_budget,
    context_budget_steps,
    current_context_budget,
    effective_context_length,
    normalize_context_budget,
    reset_context_budget,
    snap_context_budget,
)


def test_steps_follow_the_catalog_window() -> None:
    assert context_budget_steps(None) is None
    assert context_budget_steps(128_000) is None
    assert context_budget_steps(200_000) == (128_000, 200_000)
    assert context_budget_steps(1_000_000) == (128_000, 256_000, 512_000, 1_000_000)


def test_normalize_stores_the_window_as_empty() -> None:
    assert normalize_context_budget("deepseek-v4-flash", None) is None
    assert normalize_context_budget("deepseek-v4-flash", 1_000_000) is None
    assert normalize_context_budget("deepseek-v4-flash", 256_000) == 256_000
    with pytest.raises(ValidationError, match="不能调"):
        normalize_context_budget("gpt-4o", 128_000)
    with pytest.raises(ValidationError, match="其中一档"):
        normalize_context_budget("deepseek-v4-flash", 100_000)


def test_snap_drops_a_step_the_new_model_does_not_offer() -> None:
    assert snap_context_budget("deepseek-v4-flash", 512_000) == 512_000
    assert snap_context_budget("deepseek-v4-flash-free", 512_000) is None
    assert snap_context_budget("gpt-4o", 128_000) is None
    assert snap_context_budget("deepseek-v4-flash", 1_000_000) is None


def test_effective_window_is_the_shorter_of_budget_and_catalog() -> None:
    assert effective_context_length("deepseek-v4-flash", None) == 1_000_000
    assert effective_context_length("deepseek-v4-flash", 128_000) == 128_000
    assert effective_context_length("gpt-4o", 512_000) == 128_000
    assert effective_context_length(None, None) is None
    assert effective_context_length("not-a-real-model", 128_000) == 128_000
    assert effective_context_length("not-a-real-model", None) is None


def test_coerce_ignores_junk_and_huge_client_values() -> None:
    assert coerce_context_budget(None) is None
    assert coerce_context_budget(True) is None
    assert coerce_context_budget(128_000) == 128_000
    assert coerce_context_budget(2_000_001) is None


def test_profile_budget_wins_and_empty_keeps_a_bound_value() -> None:
    outer = bind_context_budget(128_000)
    try:
        assert current_context_budget() == 128_000
        token = bind_profile_context_budget(None)
        try:
            assert current_context_budget() == 128_000
        finally:
            reset_context_budget(token)
        token = bind_profile_context_budget(256_000)
        try:
            assert current_context_budget() == 256_000
        finally:
            reset_context_budget(token)
        assert current_context_budget() == 128_000
    finally:
        reset_context_budget(outer)
        assert current_context_budget() is None
