from agentcore.llm.composer_knobs import (
    MAX_COMPOSER_MODEL_KNOBS,
    knobs_by_model,
    merge_composer_last_used,
    snapshot_without_knobs,
)


def test_seeds_current_snapshot_when_map_missing() -> None:
    raw = {
        "main": {
            "origin": "byok",
            "provider_id": "p1",
            "model": "deepseek-v4-flash",
        },
        "worker": None,
        "reasoning_effort": "low",
        "context_budget": 128000,
    }
    assert knobs_by_model(raw) == {
        "@byok/p1/deepseek-v4-flash": {
            "reasoning_effort": "low",
            "context_budget": 128000,
        }
    }


def test_merge_keeps_other_leaves_and_strips_map_from_snapshot() -> None:
    existing = {
        "main": {"origin": "platform", "provider_id": None, "model": "glm-5.2"},
        "reasoning_effort": "high",
        "context_budget": None,
        "knobs_by_model": {
            "@platform/glm-5.2": {"reasoning_effort": "high", "context_budget": None},
        },
    }
    stored = merge_composer_last_used(
        existing,
        {
            "main": {
                "origin": "byok",
                "provider_id": "p1",
                "model": "deepseek-v4-flash",
            },
            "worker": None,
            "reasoning_effort": "low",
            "context_budget": None,
        },
    )
    assert stored["main"]["model"] == "deepseek-v4-flash"
    assert stored["knobs_by_model"]["@platform/glm-5.2"]["reasoning_effort"] == "high"
    assert stored["knobs_by_model"]["@byok/p1/deepseek-v4-flash"] == {
        "reasoning_effort": "low",
        "context_budget": None,
    }
    assert "knobs_by_model" not in (snapshot_without_knobs(stored) or {})


def test_merge_drops_oldest_when_over_cap() -> None:
    knobs = {
        f"@platform/m{i}": {"reasoning_effort": None, "context_budget": None}
        for i in range(MAX_COMPOSER_MODEL_KNOBS)
    }
    stored = merge_composer_last_used(
        {"knobs_by_model": knobs},
        {
            "main": {"origin": "platform", "provider_id": None, "model": "new"},
            "reasoning_effort": "low",
            "context_budget": None,
        },
    )
    mapped = stored["knobs_by_model"]
    assert len(mapped) == MAX_COMPOSER_MODEL_KNOBS
    assert "@platform/m0" not in mapped
    assert mapped["@platform/new"]["reasoning_effort"] == "low"
