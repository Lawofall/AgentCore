"""Unit tests for the per-run cost ledger builders (runtime/costing.py).

Pure and DB-free: they pin how a finished run becomes a ``cost_events`` row.
Both a delegated member and the captain root read the price the executor already
stamped onto their ``RunState`` (neither is re-priced — pricing happens once, in
the executor, via :func:`calculate_cost`); these builders only reshape that priced
state into a ledger row. Money is integer nano-CNY throughout.
"""

from dataclasses import asdict

from agentcore.llm.pricing import QWEN_VL_MAX, calculate_cost
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH, DEEPSEEK_V4_PRO
from agentcore.llm.provider.protocol import TokenUsage
from agentcore.runtime.costing import (
    ROLE_CAPTAIN,
    ROLE_MEMBER,
    ROLE_VISION,
    WorkerResultAccumulator,
    aggregate_cost,
    aggregate_usage_tokens,
    captain_run_cost_from_state,
    member_run_cost,
    vision_run_cost,
)
from agentcore.runtime.runs.types import RunSpec, RunState


def test_member_run_cost_reads_state_without_repricing():
    # The executor already priced this run onto state.cost; the builder must read
    # it verbatim (a member is never re-priced) and parent it to the captain.
    spec = RunSpec(run_id="run-1", task="做调研", agent_id="agent-1", role="研究员")
    state = RunState(
        model=DEEPSEEK_V4_PRO,
        rounds=2,
        duration_ms=1234,
        usage={"input": 100, "output": 50, "reasoning": 10, "cache_hit": 60, "cache_miss": 40},
        cost={"input": 999, "cached": 111, "output": 222, "total": 1221},
    )

    row = member_run_cost(spec, state, parent_run_id="cap-1")

    assert row.role == ROLE_MEMBER
    assert row.persona == "研究员"
    assert row.run_id == "run-1"
    assert row.parent_run_id == "cap-1"
    assert row.agent_id == "agent-1"
    assert row.model == DEEPSEEK_V4_PRO
    assert row.tokens == {
        "input": 100,
        "output": 50,
        "reasoning": 10,
        "cache_hit": 60,
        "cache_miss": 40,
    }
    # Cost read straight off the state (no recompute); total mirrored to the
    # redundant scalar the account-window SUM runs on.
    assert row.cost["input"] == 999
    assert row.cost["cached"] == 111
    assert row.cost["output"] == 222
    assert row.cost["total"] == 1221
    assert row.cost_total_nano == 1221
    assert row.cost_estimated_nano == 0
    assert row.rounds == 2
    assert row.duration_ms == 1234
    assert row.currency == "CNY"


def test_member_run_cost_defaults_agent_id_to_run_id():
    # 阶段1: agent_id == run_id; when a spec omits agent_id the row falls back to it.
    spec = RunSpec(run_id="run-x", task="t", role="r")
    state = RunState(model=DEEPSEEK_V4_FLASH, usage={"input": 1}, cost={"total": 0})

    row = member_run_cost(spec, state, parent_run_id=None)

    assert row.agent_id == "run-x"
    assert row.parent_run_id is None


def test_arena_run_cost_uses_arena_role():
    from agentcore.runtime.costing import ROLE_ARENA, arena_run_cost

    spec = RunSpec(run_id="mod-1", task="主持辩论", agent_id="mod-1", role="主持人")
    state = RunState(
        model=DEEPSEEK_V4_PRO,
        usage={"input": 10, "output": 5},
        cost={"input": 1, "cached": 0, "output": 2, "total": 3},
    )
    row = arena_run_cost(spec, state, parent_run_id="cap-1")
    assert row.role == ROLE_ARENA
    assert row.persona == "主持人"
    assert row.parent_run_id == "cap-1"


def test_resolve_run_models_arena_falls_back_to_main_not_worker():
    from agentcore.llm.profiles import TurnProfiles
    from agentcore.runtime.costing import ROLE_ARENA, ROLE_MEMBER, resolve_run_models

    profiles = TurnProfiles(
        model="main-pro",
        model_overrides={"agent": "worker-flash"},
    )
    priced, request = resolve_run_models(profiles, "", cost_role=ROLE_ARENA)
    assert priced == "main-pro"
    assert request == "main-pro"
    priced_m, request_m = resolve_run_models(profiles, "", cost_role=ROLE_MEMBER)
    assert priced_m == "worker-flash"
    assert request_m == "worker-flash"
    # Explicit spec.model still wins (Phase 3 / injected main).
    priced_x, _ = resolve_run_models(profiles, "injected-main", cost_role=ROLE_ARENA)
    assert priced_x == "injected-main"
    # Route-key injection: price bare id, request keeps prefix.
    priced_r, request_r = resolve_run_models(profiles, "platform/gpt-4o", cost_role=ROLE_ARENA)
    assert priced_r == "gpt-4o"
    assert request_r == "platform/gpt-4o"
    priced_b, request_b = resolve_run_models(profiles, "prov-1/deepseek-chat", cost_role=ROLE_ARENA)
    assert priced_b == "deepseek-chat"
    assert request_b == "prov-1/deepseek-chat"


def test_captain_run_cost_from_state_reads_priced_state():
    # The captain is now a real Run node priced once in the executor (onto
    # state.cost); this builder reads that verbatim (no re-price) and stamps the
    # captain role with no parent (it is the turn's root). Build the state the way
    # the executor does — cost = the single calculate_cost — so the row must carry
    # exactly those nano-CNY.
    usage = TokenUsage(
        input_tokens=2_000_000,
        output_tokens=1_000_000,
        reasoning_tokens=0,
        cache_hit_tokens=1_000_000,
        cache_miss_tokens=1_000_000,
    )
    priced = calculate_cost(DEEPSEEK_V4_FLASH, usage)
    state = RunState(
        model=DEEPSEEK_V4_FLASH,
        rounds=3,
        duration_ms=4321,
        usage=usage.as_dict(),
        cost=asdict(priced),
    )

    row = captain_run_cost_from_state("cap-1", state)

    assert row.role == ROLE_CAPTAIN
    assert row.persona == "CEO"
    assert row.run_id == "cap-1"
    assert row.parent_run_id is None  # the root run
    assert row.agent_id is None
    assert row.model == DEEPSEEK_V4_FLASH
    assert row.tokens == usage.as_dict()
    assert row.cost["input"] == priced.input
    assert row.cost["cached"] == priced.cached
    assert row.cost["output"] == priced.output
    assert row.cost["total"] == priced.total
    # Flash CNY curated（中文定价页 ¥0.02 / ¥1 / ¥2）。
    assert row.cost["pricing_source"] == "curated"
    assert row.cost_total_nano == priced.total
    assert row.cost["cached"] == 20_000_000  # ¥0.02
    assert row.cost["input"] == 20_000_000 + 1_000_000_000  # hit + miss ¥1
    assert row.cost["output"] == 2_000_000_000  # ¥2
    assert row.cost["total"] == 3_020_000_000
    assert row.rounds == 3
    assert row.duration_ms == 4321
    assert row.currency == "CNY"


def test_captain_cost_values_are_integers():
    # Money is integer nano-CNY end to end — no Decimal/float may leak out.
    usage = TokenUsage(input_tokens=123, output_tokens=45, cache_miss_tokens=123)
    state = RunState(
        model=DEEPSEEK_V4_FLASH,
        rounds=1,
        duration_ms=0,
        usage=usage.as_dict(),
        cost=asdict(calculate_cost(DEEPSEEK_V4_FLASH, usage)),
    )

    row = captain_run_cost_from_state("c", state)

    assert all(
        isinstance(v, int)
        for k, v in row.cost.items()
        if k in ("input", "cached", "output", "total")
    )
    assert isinstance(row.cost_total_nano, int)


def test_vision_run_cost_prices_subcall_under_vision_role():
    # AI 协作白板 读图入账 (§九.4 Gap ②): a board_read sub-call to a SEPARATE vision model
    # gets its own priced ledger row under role=vision — priced once here via the one
    # calculate_cost (a stub state would misprice it at the run's DeepSeek tier), parented
    # to the calling run so it nests under that captain in the turn's run tree.
    usage = TokenUsage(input_tokens=1200, output_tokens=40)
    priced = calculate_cost(QWEN_VL_MAX, usage)

    row = vision_run_cost(QWEN_VL_MAX, usage, parent_run_id="cap-1", duration_ms=210)

    assert row.role == ROLE_VISION
    assert row.run_id.startswith("vis_")  # unique id keeps the upsert-by-run_id honest
    assert row.parent_run_id == "cap-1"
    assert row.agent_id is None  # not a Run/Agent, just a tool-layer sub-call
    assert row.model == QWEN_VL_MAX
    assert row.rounds == 1
    assert row.duration_ms == 210
    assert row.tokens == usage.as_dict()
    assert row.cost["input"] == priced.input
    assert row.cost["cached"] == priced.cached
    assert row.cost["output"] == priced.output
    assert row.cost["total"] == priced.total
    assert row.cost_total_nano == priced.total
    # qwen-vl-max: no curated CNY card → community snapshot (same numeric rates);
    # input billed as a miss (no cache split) 1200×0.80/1M = 960_000 nano;
    # output 40×3.20/1M = 128_000.
    assert row.cost["input"] == 960_000
    assert row.cost["cached"] == 0
    assert row.cost["output"] == 128_000
    assert row.cost["total"] == 1_088_000
    assert row.cost["pricing_source"] == "estimated"
    assert all(
        isinstance(v, int)
        for k, v in row.cost.items()
        if k in ("input", "cached", "output", "total")
    )


def test_aggregate_cost_sums_priced_rows_across_tiers():
    # The turn total (message_end.cost) is the SUM of the already-priced rows, NOT
    # a re-price of the combined usage — a captain on Flash + a member on Pro must
    # add up component-wise, each at its own tier.
    cap_usage = TokenUsage(cache_miss_tokens=1_000_000, output_tokens=1_000_000)
    captain = asdict(
        captain_run_cost_from_state(
            "cap",
            RunState(
                model=DEEPSEEK_V4_FLASH,
                rounds=1,
                duration_ms=0,
                usage=cap_usage.as_dict(),
                cost=asdict(calculate_cost(DEEPSEEK_V4_FLASH, cap_usage)),
            ),
        )
    )
    member = {
        "run_id": "mem",
        "parent_run_id": "cap",
        "agent_id": "mem",
        "role": ROLE_MEMBER,
        "model": DEEPSEEK_V4_PRO,
        "tokens": {"input": 1, "output": 1, "reasoning": 0, "cache_hit": 0, "cache_miss": 1},
        "cost": {"input": 100, "cached": 0, "output": 200, "total": 300},
        "cost_total_nano": 300,
        "currency": "CNY",
        "rounds": 1,
        "duration_ms": 5,
    }

    agg = aggregate_cost([captain, member])

    assert agg["input"] == captain["cost"]["input"] + 100
    assert agg["cached"] == captain["cost"]["cached"] + 0
    assert agg["output"] == captain["cost"]["output"] + 200
    # Total mirrors the redundant scalar the account-window SUM runs on.
    assert agg["total"] == captain["cost_total_nano"] + 300
    assert agg["total"] == agg["input"] + agg["output"]
    assert agg["currency"] == "CNY"


def test_aggregate_cost_empty_is_zero():
    # A turn with no priced runs (nothing metered) aggregates to a clean zero
    # block rather than an empty dict, so message_end.cost is always well-formed.
    assert aggregate_cost([]) == {
        "input": 0,
        "cached": 0,
        "output": 0,
        "total": 0,
        "estimated_total": 0,
        "currency": "CNY",
        "estimated_currency": "CNY",
        "pricing_source": "curated",
    }


def test_aggregate_usage_tokens_sums_ledger_rows():
    """Bubble ``messages.usage`` long-keys must match summed ``cost_events.tokens``."""
    rows = [
        {"tokens": {"input": 10, "output": 2, "cache_hit": 3, "cache_miss": 7}},
        {"tokens": {"input": 50, "output": 8, "reasoning": 1, "cache_hit": 4}},
        {"tokens": None},
    ]
    assert aggregate_usage_tokens(rows) == {
        "input_tokens": 60,
        "output_tokens": 10,
        "reasoning_tokens": 1,
        "cache_hit_tokens": 7,
        "cache_miss_tokens": 7,
    }
    assert aggregate_usage_tokens([]) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
    }


def test_collab_tally_starts_at_zero():
    # 协作质量 (学·度量 §2.5): a fresh accumulator carries a zeroed tally, so a plain
    # single-agent / no-boundary turn persists zeros (byte-for-byte unchanged behavior).
    acc = WorkerResultAccumulator()
    assert acc.collab == {
        "boundary_yields": 0,
        "boundary_yields_by_user": 0,
        "scope_signals": 0,
        "escalations": 0,
    }
    assert acc.continuations == []
    assert acc.user_continuations == []


def test_collab_tally_rolls_up_nested_subteams_via_merge():
    # A nested lead's sub-team signals must roll up to the captain the SAME parent/child
    # path as usage — merge() folds the child accumulator's collab counters in, so a
    # depth-2 boundary/drift is not lost from the turn-level 方向盘.
    captain = WorkerResultAccumulator()
    captain.collab["boundary_yields"] += 1
    captain.collab["scope_signals"] += 2

    lead = WorkerResultAccumulator()
    lead.collab["boundary_yields"] += 1
    # 子团队里那次让出是用户在计划复核上拍板的：总数照记，同时记进「用户促成」子集，
    # 这样卷到 captain 后用户面仍减得掉，不会把用户自己的拍板说成队友互检。
    lead.collab["boundary_yields_by_user"] += 1
    lead.collab["scope_signals"] += 3
    lead.collab["escalations"] += 4

    captain.merge(lead)

    assert captain.collab == {
        "boundary_yields": 2,
        "boundary_yields_by_user": 1,
        "scope_signals": 5,
        "escalations": 4,
    }
