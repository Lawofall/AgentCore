"""Tests for WaveScheduler: ready-selection, dep-context handoff, the four
on_failure strategies (degrade / skip / abort / retry), exception capture, the
pause/resume substrate (seed_completed / should_stop / on_progress), and the
continuous-dispatch properties (downstream starts before a slow sibling; the
tree-wide budget isn't multiplied by nesting).

Uses a fake RunExecutor (a plain async callable) — no LLM, no engine — so the
scheduler's control flow is exercised in isolation.
"""

import asyncio

import pytest

from agentcore.runtime.runs.concurrency import reset_budget, set_budget
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.scheduler import BoundaryOutcome, BoundaryReason
from agentcore.runtime.runs.types import BatchMetrics, RunPhase, RunPolicy, RunSpec, RunState
from agentcore.runtime.runs.wave import WaveScheduler


def _spec(
    run_id: str,
    deps: tuple[str, ...] = (),
    *,
    on_failure: str = "degrade",
    checkpoint_after: bool = False,
    bind_after_deps: bool = False,
) -> RunSpec:
    return RunSpec(
        run_id=run_id,
        task="t",
        agent_id=run_id,
        role=run_id,
        depends_on=list(deps),
        checkpoint_after=checkpoint_after,
        bind_after_deps=bind_after_deps,
        policy=RunPolicy(on_failure=on_failure),
    )


async def _ok(spec: RunSpec, _completed) -> RunState:
    return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)


async def test_parallel_all_complete():
    plan = RunPlan()
    for x in ("a", "b", "c"):
        plan.add(_spec(x))
    res = await WaveScheduler().run(plan, _ok)
    assert set(res) == {"a", "b", "c"}
    assert all(s.phase is RunPhase.COMPLETED for s in res.values())


async def test_dag_runs_in_order_and_sees_completed_deps():
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",)))
    seen: dict[str, set[str]] = {}

    async def ex(spec: RunSpec, completed) -> RunState:
        seen[spec.run_id] = set(completed)
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    await WaveScheduler().run(plan, ex)
    assert seen["a"] == set()
    assert "a" in seen["b"]


async def test_skip_cascades_to_dependents():
    plan = RunPlan()
    plan.add(_spec("a", on_failure="skip"))
    plan.add(_spec("b", ("a",)))

    async def ex(spec: RunSpec, _completed) -> RunState:
        if spec.run_id == "a":
            return RunState(phase=RunPhase.FAILED, error="boom")
        return RunState(phase=RunPhase.COMPLETED, content="b")

    skipped: list[tuple[str, str, str]] = []
    res = await WaveScheduler().run(
        plan, ex, on_skipped=lambda rid, aid, reason: skipped.append((rid, aid, reason))
    )
    assert res["a"].phase is RunPhase.FAILED
    assert res["b"].phase is RunPhase.SKIPPED
    assert skipped == [("b", "b", "cascade")]


async def test_abort_stops_later_waves():
    plan = RunPlan()
    plan.add(_spec("a", on_failure="abort"))
    plan.add(_spec("b", ("a",)))

    async def ex(spec: RunSpec, _completed) -> RunState:
        if spec.run_id == "a":
            return RunState(phase=RunPhase.FAILED, error="boom")
        return RunState(phase=RunPhase.COMPLETED, content="b")

    skipped: list[tuple[str, str, str]] = []
    res = await WaveScheduler().run(
        plan, ex, on_skipped=lambda rid, aid, reason: skipped.append((rid, aid, reason))
    )
    assert res["a"].phase is RunPhase.FAILED
    # The unrun tail materialises as SKIPPED (graceful abort), not absent.
    assert res["b"].phase is RunPhase.SKIPPED
    assert skipped == [("b", "b", "abort")]


async def test_degrade_lets_dependents_proceed():
    plan = RunPlan()
    plan.add(_spec("a", on_failure="degrade"))
    plan.add(_spec("b", ("a",)))

    async def ex(spec: RunSpec, _completed) -> RunState:
        if spec.run_id == "a":
            return RunState(phase=RunPhase.FAILED, error="boom")
        return RunState(phase=RunPhase.COMPLETED, content="b")

    res = await WaveScheduler().run(plan, ex)
    assert res["a"].phase is RunPhase.FAILED
    assert res["b"].phase is RunPhase.COMPLETED


async def test_retry_failure_cascades_skip_like_skip_policy():
    """Default retry (after infra retries) must not feed dependents a failed product."""
    plan = RunPlan()
    plan.add(_spec("a", on_failure="retry"))
    plan.add(_spec("synth", ("a",)))

    async def ex(spec: RunSpec, _completed) -> RunState:
        if spec.run_id == "a":
            return RunState(phase=RunPhase.FAILED, error="contract miss")
        return RunState(phase=RunPhase.COMPLETED, content="should-not-run")

    skipped: list[tuple[str, str, str]] = []
    res = await WaveScheduler().run(
        plan, ex, on_skipped=lambda rid, aid, reason: skipped.append((rid, aid, reason))
    )
    assert res["a"].phase is RunPhase.FAILED
    assert res["synth"].phase is RunPhase.SKIPPED
    assert skipped == [("synth", "synth", "cascade")]


async def test_multi_upstream_partial_failure_runs_synth():
    """Lenient fan-in: ≥1 upstream COMPLETED → synth runs (not cascade-skip)."""
    plan = RunPlan()
    for rid in ("legal", "business", "pr", "culture"):
        plan.add(_spec(rid, on_failure="retry"))
    plan.add(_spec("synth", ("legal", "business", "pr", "culture")))

    async def ex(spec: RunSpec, completed) -> RunState:
        if spec.run_id in ("legal", "business", "pr"):
            return RunState(
                phase=RunPhase.FAILED,
                error="contract.failed",
                content="fake dsml body",
                error_retryable=False,
            )
        if spec.run_id == "culture":
            return RunState(phase=RunPhase.COMPLETED, content="culture_ok")
        # synth must see the successful upstream and may see failed absences
        assert "culture" in completed
        assert completed["culture"].phase is RunPhase.COMPLETED
        return RunState(phase=RunPhase.COMPLETED, content="synth_ok")

    skipped: list[tuple[str, str, str]] = []
    res = await WaveScheduler().run(
        plan, ex, on_skipped=lambda rid, aid, reason: skipped.append((rid, aid, reason))
    )
    assert res["synth"].phase is RunPhase.COMPLETED
    assert res["synth"].content == "synth_ok"
    assert skipped == []
    assert res["culture"].phase is RunPhase.COMPLETED
    assert all(res[r].phase is RunPhase.FAILED for r in ("legal", "business", "pr"))


async def test_multi_upstream_require_upstream_still_cascade_skips():
    """require_upstream=True restores strict cascade-skip on any retry-failure."""
    plan = RunPlan()
    for rid in ("legal", "business", "pr", "culture"):
        plan.add(_spec(rid, on_failure="retry"))
    plan.add(
        RunSpec(
            run_id="synth",
            task="t",
            agent_id="synth",
            role="synth",
            depends_on=["legal", "business", "pr", "culture"],
            require_upstream=True,
            policy=RunPolicy(on_failure="degrade"),
        )
    )

    async def ex(spec: RunSpec, _completed) -> RunState:
        if spec.run_id in ("legal", "business", "pr"):
            return RunState(phase=RunPhase.FAILED, error="contract.failed")
        if spec.run_id == "culture":
            return RunState(phase=RunPhase.COMPLETED, content="culture_ok")
        return RunState(phase=RunPhase.COMPLETED, content="synth_must_not_run")

    skipped: list[tuple[str, str, str]] = []
    res = await WaveScheduler().run(
        plan, ex, on_skipped=lambda rid, aid, reason: skipped.append((rid, aid, reason))
    )
    assert res["synth"].phase is RunPhase.SKIPPED
    assert skipped == [("synth", "synth", "cascade")]
    assert res["culture"].phase is RunPhase.COMPLETED


async def test_multi_upstream_cancel_runs_synth_by_default():
    """TRACE shape: one of N upstreams cancelled → summarizer still runs (≥1 success)."""
    plan = RunPlan()
    for rid in ("a", "b", "c"):
        plan.add(_spec(rid))
    plan.add(_spec("synth", ("a", "b", "c")))
    cancel_targets: set[str] = set()

    async def slow_ex(spec: RunSpec, completed) -> RunState:
        if spec.run_id == "a":
            await asyncio.sleep(0.1)
            return RunState(phase=RunPhase.COMPLETED, content="a_ok")
        if spec.run_id in ("b", "c"):
            await asyncio.sleep(0.01)
            return RunState(phase=RunPhase.COMPLETED, content=f"{spec.run_id}_ok")
        assert completed["b"].phase is RunPhase.COMPLETED
        return RunState(phase=RunPhase.COMPLETED, content="synth_ok")

    async def _schedule_cancel():
        await asyncio.sleep(0.02)
        cancel_targets.add("a")

    asyncio.create_task(_schedule_cancel())
    res = await WaveScheduler().run(
        plan, slow_ex, cancel_run_ids=lambda: frozenset(cancel_targets)
    )
    assert res["a"].phase is RunPhase.CANCELLED
    assert res["b"].phase is RunPhase.COMPLETED
    assert res["c"].phase is RunPhase.COMPLETED
    assert res["synth"].phase is RunPhase.COMPLETED
    assert res["synth"].content == "synth_ok"


async def test_multi_upstream_cancel_require_upstream_cascade_skips():
    """require_upstream=True + cancel → wave.cancel_cascade_skip on summarizer."""
    plan = RunPlan()
    for rid in ("a", "b"):
        plan.add(_spec(rid))
    plan.add(
        RunSpec(
            run_id="synth",
            task="t",
            agent_id="synth",
            role="synth",
            depends_on=["a", "b"],
            require_upstream=True,
            policy=RunPolicy(on_failure="degrade"),
        )
    )
    cancel_targets: set[str] = set()

    async def slow_ex(spec: RunSpec, _completed) -> RunState:
        if spec.run_id == "a":
            await asyncio.sleep(0.1)
        else:
            await asyncio.sleep(0.01)
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    async def _schedule_cancel():
        await asyncio.sleep(0.02)
        cancel_targets.add("a")

    asyncio.create_task(_schedule_cancel())
    skipped: list[tuple[str, str, str]] = []
    res = await WaveScheduler().run(
        plan,
        slow_ex,
        cancel_run_ids=lambda: frozenset(cancel_targets),
        on_skipped=lambda rid, aid, reason: skipped.append((rid, aid, reason)),
    )
    assert res["a"].phase is RunPhase.CANCELLED
    assert res["synth"].phase is RunPhase.SKIPPED
    assert skipped == [("synth", "synth", "cascade")]


async def test_merge_without_replaces_keeps_cascade_skip():
    """Secondary delegate without replaces_run_id must not revive a cascade-skipped synth."""
    plan = RunPlan()
    plan.add(_spec("pr", on_failure="retry"))
    plan.add(_spec("synth", ("pr",)))

    async def ex(spec: RunSpec, _completed) -> RunState:
        if spec.run_id == "pr":
            return RunState(phase=RunPhase.FAILED, error="contract.failed")
        if spec.run_id == "pr_solo":
            return RunState(phase=RunPhase.COMPLETED, content="solo_ok")
        return RunState(phase=RunPhase.COMPLETED, content="synth_must_not_run")

    def on_progress(completed) -> None:
        if "pr" not in completed or plan.by_id("pr_solo") is not None:
            return
        if completed["pr"].phase is RunPhase.FAILED:
            plan.add(_spec("pr_solo", on_failure="retry"))

    skipped: list[tuple[str, str, str]] = []
    res = await WaveScheduler().run(
        plan, ex, on_progress=on_progress,
        on_skipped=lambda rid, aid, reason: skipped.append((rid, aid, reason)),
    )
    assert plan.by_id("synth").depends_on == ["pr"]
    assert res["synth"].phase is RunPhase.SKIPPED
    assert res["pr_solo"].phase is RunPhase.COMPLETED
    assert skipped == [("synth", "synth", "cascade")]


async def test_replaces_mid_run_revives_cascade_skipped_dependent():
    """Failed upstream + replaces_run_id rewrite → skipped synth waits on replacement."""
    plan = RunPlan()
    plan.add(_spec("pr", on_failure="retry"))
    plan.add(_spec("synth", ("pr",)))
    replaced = {"done": False}
    seen_deps: dict[str, set[str]] = {}

    async def ex(spec: RunSpec, completed) -> RunState:
        seen_deps[spec.run_id] = set(completed)
        if spec.run_id == "pr":
            return RunState(phase=RunPhase.FAILED, error="contract.failed")
        if spec.run_id == "pr_b":
            return RunState(phase=RunPhase.COMPLETED, content="pr_ok")
        # synth: must see replacement product, not the failed original alone
        assert "pr_b" in completed
        assert completed["pr_b"].phase is RunPhase.COMPLETED
        return RunState(phase=RunPhase.COMPLETED, content="synth_ok")

    def on_progress(completed) -> None:
        if replaced["done"] or "pr" not in completed:
            return
        if completed["pr"].phase is not RunPhase.FAILED:
            return
        plan.add(
            RunSpec(
                run_id="pr_b",
                task="retry pr",
                agent_id="pr_b",
                role="pr_b",
                depends_on=[],
                replaces_run_id="pr",
                policy=RunPolicy(on_failure="retry"),
            )
        )
        replaced["done"] = True

    res = await WaveScheduler().run(plan, ex, on_progress=on_progress)
    assert replaced["done"] is True
    assert plan.by_id("synth").depends_on == ["pr_b"]
    assert res["pr"].phase is RunPhase.FAILED
    assert res["pr_b"].phase is RunPhase.COMPLETED
    assert res["synth"].phase is RunPhase.COMPLETED
    assert res["synth"].content == "synth_ok"
    assert "pr_b" in seen_deps["synth"]


async def test_retry_then_succeeds():
    """Wave dispatches once: a terminal FAILED is not remounted."""
    plan = RunPlan()
    plan.add(_spec("a", on_failure="retry"))
    calls = {"n": 0}

    async def ex(_spec: RunSpec, _completed) -> RunState:
        calls["n"] += 1
        if calls["n"] < 2:
            return RunState(
                phase=RunPhase.FAILED, error="prompt too long", error_retryable=False
            )
        return RunState(phase=RunPhase.COMPLETED, content="ok")

    res = await WaveScheduler().run(plan, ex)
    # BL-6: non-retryable still does not 整跑.
    assert res["a"].phase is RunPhase.FAILED
    assert calls["n"] == 1


async def test_retryable_failure_does_not_rerun_node():
    """Transient FAILED must not remount the worker (no second executor hop)."""
    plan = RunPlan()
    plan.add(_spec("a", on_failure="retry"))
    calls = {"n": 0}

    async def ex(_spec: RunSpec, _completed) -> RunState:
        calls["n"] += 1
        return RunState(phase=RunPhase.FAILED, error="5xx transient")

    res = await WaveScheduler().run(plan, ex)
    assert res["a"].phase is RunPhase.FAILED
    assert calls["n"] == 1


async def test_retry_merges_billing_including_string_annotations():
    """B-deep 失败计费 survives retries: numeric usage/cost fields from attempt 1 are
    summed into the returned state, while string annotations (currency / pricing_source /
    credential_source) must pass through untouched — regression for the int() crash."""
    plan = RunPlan()
    plan.add(_spec("a", on_failure="retry"))
    calls = {"n": 0}

    async def ex(_spec: RunSpec, _completed) -> RunState:
        calls["n"] += 1
        if calls["n"] == 1:
            return RunState(
                phase=RunPhase.FAILED,
                error="transient",
                usage={"input_tokens": 10, "output_tokens": 5},
                cost={"total_microusd": 700, "currency": "USD", "pricing_source": "curated"},
            )
        return RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            usage={"input_tokens": 20, "output_tokens": 8},
            cost={"total_microusd": 1300, "currency": "USD", "pricing_source": "curated"},
        )

    res = await WaveScheduler().run(plan, ex)
    # Transient no longer 整跑s; billing merge is the executor's in-node continue.
    assert res["a"].phase is RunPhase.FAILED
    assert calls["n"] == 1
    assert res["a"].usage == {"input_tokens": 10, "output_tokens": 5}
    assert res["a"].cost == {
        "total_microusd": 700,
        "currency": "USD",
        "pricing_source": "curated",
    }


async def test_deterministic_failure_skips_retry():
    """确定性失败区分 (BL-6): a FAILED state flagged ``error_retryable=False`` (prompt 超长 /
    鉴权 / 余额) is NOT re-run even under ``on_failure="retry"`` — re-running just re-fails."""
    plan = RunPlan()
    plan.add(_spec("a", on_failure="retry"))
    calls = {"n": 0}

    async def ex(_spec: RunSpec, _completed) -> RunState:
        calls["n"] += 1
        return RunState(phase=RunPhase.FAILED, error="prompt too long", error_retryable=False)

    res = await WaveScheduler().run(plan, ex)
    assert res["a"].phase is RunPhase.FAILED
    assert res["a"].error_retryable is False
    assert calls["n"] == 1  # ran once, no futile retries


async def test_retryable_failure_still_exhausts_retries():
    """Wave no longer 整跑s a transient FAILED — continue budget lives in the executor."""
    plan = RunPlan()
    plan.add(_spec("a", on_failure="retry"))
    calls = {"n": 0}

    async def ex(_spec: RunSpec, _completed) -> RunState:
        calls["n"] += 1
        return RunState(phase=RunPhase.FAILED, error="5xx transient")

    res = await WaveScheduler().run(plan, ex)
    assert res["a"].phase is RunPhase.FAILED
    assert calls["n"] == 1


async def test_retry_hot_continues_prior_transcript():
    """Wave does not remount a retryable FAILED — seed-continue is the executor's job."""
    from agentcore.llm.provider.protocol import LLMMessage

    plan = RunPlan()
    plan.add(_spec("a", on_failure="retry"))
    prior = [
        LLMMessage(role="system", content="SYS"),
        LLMMessage(role="user", content="做A"),
        LLMMessage(role="assistant", content="半成品"),
    ]
    seen: list[list | None] = []

    async def ex(spec: RunSpec, completed) -> RunState:
        seeded = completed.get(spec.run_id)
        seen.append(list(seeded.transcript) if seeded is not None else None)
        return RunState(
            phase=RunPhase.FAILED,
            error="upstream disconnect",
            transcript=prior,
            content="半成品",
        )

    res = await WaveScheduler().run(plan, ex)
    assert res["a"].phase is RunPhase.FAILED
    assert seen == [None]


async def test_retry_without_transcript_stays_cold():
    """FAILED with empty transcript → retry still cold (no site to seed)."""
    plan = RunPlan()
    plan.add(_spec("a", on_failure="retry"))
    seeded_self = []

    async def ex(spec: RunSpec, completed) -> RunState:
        seeded_self.append(spec.run_id in completed)
        return RunState(phase=RunPhase.FAILED, error="boom before turns")

    res = await WaveScheduler().run(plan, ex)
    assert res["a"].phase is RunPhase.FAILED
    assert seeded_self == [False]


async def test_executor_exception_becomes_failed_state():
    plan = RunPlan()
    plan.add(_spec("a"))

    async def ex(_spec: RunSpec, _completed) -> RunState:
        raise RuntimeError("kaboom")

    res = await WaveScheduler().run(plan, ex)
    assert res["a"].phase is RunPhase.FAILED
    assert "kaboom" in res["a"].error


async def test_on_progress_fires_after_each_node():
    # Continuous dispatch fires on_progress once per completed node (smoother than
    # the old per-wave cadence). A pipeline finishes a→b, so the snapshots grow by
    # one each time.
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",)))
    snaps: list[set[str]] = []
    await WaveScheduler().run(plan, _ok, on_progress=lambda c: snaps.append(set(c)))
    assert snaps == [{"a"}, {"a", "b"}]


async def test_on_node_done_fires_per_executed_node_not_cascade_skip():
    """Optional on_node_done: one call per executed terminal node (additive)."""
    plan = RunPlan()
    plan.add(_spec("a", on_failure="skip"))
    plan.add(_spec("b", ("a",)))

    done: list[tuple[str, str]] = []

    async def ex(spec: RunSpec, _completed) -> RunState:
        if spec.run_id == "a":
            return RunState(phase=RunPhase.FAILED, error="boom")
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    async def on_node_done(run_id: str, state: RunState) -> None:
        done.append((run_id, state.phase.value))

    res = await WaveScheduler().run(plan, ex, on_node_done=on_node_done)
    assert done == [("a", "failed")]
    assert res["b"].phase is RunPhase.SKIPPED
    assert all(rid != "b" for rid, _ in done)


async def test_seed_completed_skips_finished_nodes():
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",)))
    ran: list[str] = []

    async def ex(spec: RunSpec, _completed) -> RunState:
        ran.append(spec.run_id)
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    seed = {"a": RunState(phase=RunPhase.COMPLETED, content="cached")}
    res = await WaveScheduler().run(plan, ex, seed_completed=seed)
    assert ran == ["b"]
    assert res["a"].content == "cached"


async def test_should_stop_pauses_between_waves():
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",)))
    res = await WaveScheduler().run(plan, _ok, should_stop=lambda: True)
    assert res == {}


async def test_max_parallel_caps_concurrency():
    # 4 independent nodes, width 2 → never more than 2 run at once, but all finish.
    plan = RunPlan()
    for x in ("a", "b", "c", "d"):
        plan.add(_spec(x))
    state = {"active": 0, "peak": 0}

    async def ex(spec: RunSpec, _completed) -> RunState:
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    res = await WaveScheduler(max_parallel=2).run(plan, ex)
    assert set(res) == {"a", "b", "c", "d"}
    assert state["peak"] <= 2


async def test_continuous_dispatch_starts_downstream_before_slow_sibling():
    # a ∥ b independent; c depends only on a. b is slow. Continuous dispatch lets c
    # run the moment a finishes, instead of waiting for the whole「wave」(slow b) —
    # the latency win this scheduler exists for. (The old barrier scheduler would
    # finish b before c could start.)
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b"))
    plan.add(_spec("c", ("a",)))
    order: list[str] = []

    async def ex(spec: RunSpec, _completed) -> RunState:
        await asyncio.sleep(0.05 if spec.run_id == "b" else 0.005)
        order.append(spec.run_id)
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    res = await WaveScheduler().run(plan, ex)
    assert all(s.phase is RunPhase.COMPLETED for s in res.values())
    assert order.index("c") < order.index("b")


async def test_nested_fanout_respects_tree_budget():
    # An outer node whose executor itself runs a nested WaveScheduler must not let
    # the tree's concurrent leaf count exceed the budget (分而不乘): with budget 4,
    # the 4 outer nodes each get child budget 1, so each nested scheduler runs its
    # leaves serially → at most 4 leaves run at once, not 4 × 4.
    peak = {"active": 0, "max": 0}

    async def leaf(spec: RunSpec, _completed) -> RunState:
        peak["active"] += 1
        peak["max"] = max(peak["max"], peak["active"])
        await asyncio.sleep(0.01)
        peak["active"] -= 1
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    async def outer(spec: RunSpec, _completed) -> RunState:
        nested = RunPlan()
        for i in range(4):
            nested.add(_spec(f"{spec.run_id}{i}"))
        await WaveScheduler().run(nested, leaf)
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    plan = RunPlan()
    for x in ("p", "q", "r", "s"):
        plan.add(_spec(x))
    token = set_budget(4)
    try:
        res = await WaveScheduler().run(plan, outer)
    finally:
        reset_budget(token)
    assert all(s.phase is RunPhase.COMPLETED for s in res.values())
    assert peak["max"] <= 4


async def test_child_budget_divides_by_ready_width_not_unready_sink():
    # 4 independent auditors + 1 supervisor depending on all four. First-wave
    # parallel width is 4 → each child's set_budget is budget//4 (≥3 with 12),
    # not budget//5 from counting the still-blocked sink (旧 bug: 12//5=2).
    from agentcore.runtime.runs.concurrency import current_budget

    seen: dict[str, int] = {}

    async def ex(spec: RunSpec, _completed) -> RunState:
        seen[spec.run_id] = current_budget()
        await asyncio.sleep(0.01)
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    plan = RunPlan()
    for x in ("a", "b", "c", "d"):
        plan.add(_spec(x))
    plan.add(_spec("lead", ("a", "b", "c", "d")))
    token = set_budget(12)
    try:
        res = await WaveScheduler().run(plan, ex)
    finally:
        reset_budget(token)
    assert set(res) == {"a", "b", "c", "d", "lead"}
    for x in ("a", "b", "c", "d"):
        assert seen[x] >= 3, f"{x} budget={seen[x]} (want ≥ 12//4=3, not 12//5=2)"
    assert seen["lead"] >= 1


# --- 受监督的波循环: on_boundary CHECKPOINT arm (结构化挂起 2a) ------------------


async def test_on_checkpoint_fires_after_marked_node_with_downstream():
    # a (checkpoint_after) → b: the hook fires once after a's wave with reason
    # CHECKPOINT, seeing a as completed and a downstream node still pending; PROCEED
    # runs b.
    plan = RunPlan()
    plan.add(_spec("a", checkpoint_after=True))
    plan.add(_spec("b", ("a",)))
    seen: list[tuple] = []

    async def hook(reason, nodes, completed):
        seen.append((reason, [n.run_id for n in nodes], set(completed)))
        return BoundaryOutcome.PROCEED

    res = await WaveScheduler().run(plan, _ok, on_boundary=hook)
    assert seen == [(BoundaryReason.CHECKPOINT, ["a"], {"a"})]
    assert res["a"].phase is RunPhase.COMPLETED
    assert res["b"].phase is RunPhase.COMPLETED


async def test_on_checkpoint_stop_halts_downstream():
    # ABORT ends scheduling at the wave boundary: a is kept, b never runs.
    plan = RunPlan()
    plan.add(_spec("a", checkpoint_after=True))
    plan.add(_spec("b", ("a",)))

    async def hook(_reason, _nodes, _completed):
        return BoundaryOutcome.ABORT

    res = await WaveScheduler().run(plan, _ok, on_boundary=hook)
    assert res["a"].phase is RunPhase.COMPLETED
    # The gated downstream is materialised as SKIPPED (clean graph/overview).
    assert res["b"].phase is RunPhase.SKIPPED


async def test_on_checkpoint_not_fired_on_last_wave():
    # A marked node with nothing downstream must NOT pause — no pending work to gate.
    plan = RunPlan()
    plan.add(_spec("a", checkpoint_after=True))
    calls = {"n": 0}

    async def hook(_reason, _nodes, _completed):
        calls["n"] += 1
        return BoundaryOutcome.PROCEED

    res = await WaveScheduler().run(plan, _ok, on_boundary=hook)
    assert calls["n"] == 0
    assert res["a"].phase is RunPhase.COMPLETED


async def test_on_checkpoint_skips_failed_marked_node():
    # A checkpoint node that FAILED does not pause — its on_failure governs instead.
    plan = RunPlan()
    plan.add(_spec("a", checkpoint_after=True, on_failure="degrade"))
    plan.add(_spec("b", ("a",)))
    calls = {"n": 0}

    async def ex(spec: RunSpec, _completed) -> RunState:
        if spec.run_id == "a":
            return RunState(phase=RunPhase.FAILED, error="boom")
        return RunState(phase=RunPhase.COMPLETED, content="b")

    async def hook(_reason, _nodes, _completed):
        calls["n"] += 1
        return BoundaryOutcome.PROCEED

    res = await WaveScheduler().run(plan, ex, on_boundary=hook)
    assert calls["n"] == 0
    assert res["a"].phase is RunPhase.FAILED
    assert res["b"].phase is RunPhase.COMPLETED  # degrade lets it proceed


async def test_checkpoint_after_inert_without_hook():
    # The marker is fully inert when no hook is injected (autonomous jobs / tests).
    plan = RunPlan()
    plan.add(_spec("a", checkpoint_after=True))
    plan.add(_spec("b", ("a",)))
    res = await WaveScheduler().run(plan, _ok)
    assert res["a"].phase is RunPhase.COMPLETED
    assert res["b"].phase is RunPhase.COMPLETED


# --- 受监督的波循环: on_boundary BIND arm (晚绑定) ---------


async def test_bind_boundary_fires_then_proceeds_after_host_binds():
    # b is late-bound (bind_after_deps): once a completes and work is quiescent, the
    # boundary fires with reason=BIND for b (never dispatched unbound); the host
    # finalises it in place (clears the marker) and PROCEEDs, so b then runs.
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",), bind_after_deps=True))
    seen: list[tuple] = []

    async def hook(reason, nodes, completed):
        seen.append((reason, [n.run_id for n in nodes], set(completed)))
        for n in nodes:
            n.bind_after_deps = False  # host finalises the spec in place
        return BoundaryOutcome.PROCEED

    res = await WaveScheduler().run(plan, _ok, on_boundary=hook)
    assert seen == [(BoundaryReason.BIND, ["b"], {"a"})]
    assert res["a"].phase is RunPhase.COMPLETED
    assert res["b"].phase is RunPhase.COMPLETED


async def test_bind_boundary_yield_soft_pauses_for_resume():
    # YIELD soft-pauses like should_stop: a is kept, the late-bound tail b is LEFT
    # OUT of the result (so a resume re-runs it), not materialised as SKIPPED.
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",), bind_after_deps=True))

    async def hook(_reason, _nodes, _completed):
        return BoundaryOutcome.YIELD

    res = await WaveScheduler().run(plan, _ok, on_boundary=hook)
    assert res["a"].phase is RunPhase.COMPLETED
    assert "b" not in res  # soft pause leaves the tail for a resume


async def test_bind_boundary_abort_materialises_skip():
    # ABORT ends scheduling gracefully: the un-run late-bound tail is materialised as
    # SKIPPED (same shape as a plan_review stop), not left absent.
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",), bind_after_deps=True))

    async def hook(_reason, _nodes, _completed):
        return BoundaryOutcome.ABORT

    res = await WaveScheduler().run(plan, _ok, on_boundary=hook)
    assert res["a"].phase is RunPhase.COMPLETED
    assert res["b"].phase is RunPhase.SKIPPED


async def test_bind_after_deps_inert_without_hook():
    # No on_boundary hook (autonomous / tests): the marker is inert — the node
    # dispatches normally, exactly like checkpoint_after without a hook.
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",), bind_after_deps=True))
    res = await WaveScheduler().run(plan, _ok)
    assert res["a"].phase is RunPhase.COMPLETED
    assert res["b"].phase is RunPhase.COMPLETED


async def test_bind_boundary_not_fired_until_deps_resolve():
    # The bind boundary waits for the late-bound node's deps: while a is still running
    # it must not fire; it fires exactly once, after a lands (quiescent).
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",), bind_after_deps=True))
    fires = {"n": 0}

    async def slow_a(spec: RunSpec, _completed) -> RunState:
        if spec.run_id == "a":
            await asyncio.sleep(0.02)
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    async def hook(_reason, nodes, _completed):
        fires["n"] += 1
        for n in nodes:
            n.bind_after_deps = False
        return BoundaryOutcome.PROCEED

    res = await WaveScheduler().run(plan, slow_a, on_boundary=hook)
    assert fires["n"] == 1
    assert res["b"].phase is RunPhase.COMPLETED


async def test_bind_proceed_without_clearing_skips_no_spin():
    # Defense (audit F4): host PROCEEDs but leaves bind_after_deps set → no progress.
    # Scheduler must warn once, SKIP the stuck node, and NOT re-fire the BIND boundary
    # (busy-wait / livelock). Current production host always YIELDs; this guards a
    # future host that PROCEEDs without finalising.
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",), bind_after_deps=True))
    fires = {"n": 0}

    async def hook(_reason, _nodes, _completed):
        fires["n"] += 1
        return BoundaryOutcome.PROCEED  # deliberately does NOT clear bind_after_deps

    res = await WaveScheduler().run(plan, _ok, on_boundary=hook)
    assert fires["n"] == 1  # fired once, never spun
    assert res["a"].phase is RunPhase.COMPLETED
    assert res["b"].phase is RunPhase.SKIPPED


async def test_run_rejects_cyclic_plan():
    # Defense (audit F5): entry topology self-check — a cycle must raise, not silently
    # drop the cycle nodes from the completed map.
    from agentcore.runtime.runs.plan import RunPlanError

    plan = RunPlan()
    plan.add(_spec("a", ("b",)))
    plan.add(_spec("b", ("a",)))
    with pytest.raises(RunPlanError, match="dependency cycle"):
        await WaveScheduler().run(plan, _ok)


async def test_run_rejects_dangling_depends_on():
    # Defense (audit F5): unknown depends_on edge fails explicitly at run entry.
    from agentcore.runtime.runs.plan import RunPlanError

    plan = RunPlan()
    plan.add(_spec("a", ("missing",)))
    with pytest.raises(RunPlanError, match="unknown run"):
        await WaveScheduler().run(plan, _ok)


# --- 受监督的波循环: on_boundary SCOPE arm (偏离信号) -----


def _scope_state(run_id: str) -> RunState:
    """A COMPLETED state carrying a scope-deviation escalation (escalate kind=scope)."""
    return RunState(
        phase=RunPhase.COMPLETED,
        content=run_id,
        escalations=[{"question": "真问题是X不是Y", "assumption": "暂按X", "kind": "scope"}],
    )


async def _scope_exec(spec: RunSpec, _completed) -> RunState:
    # The upstream node "a" flags a scope deviation; everything else completes plainly.
    if spec.run_id == "a":
        return _scope_state("a")
    return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)


async def test_scope_boundary_fires_then_yields_leaving_tail():
    # a flags a scope deviation while downstream b is still pending: once a lands and work
    # is quiescent, the SCOPE boundary fires with a; a YIELD soft-pauses, leaving b OUT of
    # the result (the CEO re-steers it via replan, then a resume re-runs it).
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",)))
    seen: list[tuple] = []

    async def hook(reason, nodes, completed):
        seen.append((reason, [n.run_id for n in nodes], set(completed)))
        return BoundaryOutcome.YIELD

    res = await WaveScheduler().run(plan, _scope_exec, on_boundary=hook)
    assert seen == [(BoundaryReason.SCOPE, ["a"], {"a"})]
    assert res["a"].phase is RunPhase.COMPLETED
    assert "b" not in res  # soft pause leaves the tail for the CEO's replan resume


async def test_scope_boundary_proceed_runs_tail_and_fires_once():
    # PROCEED keeps scheduling so b runs — and the signal is consumed on surfacing, so the
    # boundary never re-fires on the next quiescent cycle (no spin).
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",)))
    fires = {"n": 0}

    async def hook(_reason, _nodes, _completed):
        fires["n"] += 1
        return BoundaryOutcome.PROCEED

    res = await WaveScheduler().run(plan, _scope_exec, on_boundary=hook)
    assert fires["n"] == 1  # surfaced once → consumed → no respin
    assert res["a"].phase is RunPhase.COMPLETED
    assert res["b"].phase is RunPhase.COMPLETED


async def test_scope_boundary_abort_materialises_skip():
    # ABORT ends scheduling gracefully: the un-run tail b is materialised SKIPPED.
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",)))

    async def hook(_reason, _nodes, _completed):
        return BoundaryOutcome.ABORT

    res = await WaveScheduler().run(plan, _scope_exec, on_boundary=hook)
    assert res["a"].phase is RunPhase.COMPLETED
    assert res["b"].phase is RunPhase.SKIPPED


async def test_scope_boundary_not_fired_without_downstream():
    # A scope deviation with NO not-yet-run downstream to redirect must NOT pause: there is
    # nothing for the CEO to re-steer, so the escalation just rides to synthesis (today's
    # behaviour). a + an independent b both finish → quiescent but pending_remains is False.
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b"))
    seen: list[tuple] = []

    async def hook(reason, nodes, _completed):
        seen.append((reason, [n.run_id for n in nodes]))
        return BoundaryOutcome.PROCEED

    res = await WaveScheduler().run(plan, _scope_exec, on_boundary=hook)
    assert seen == []  # no downstream → no SCOPE boundary
    assert res["a"].phase is RunPhase.COMPLETED
    assert res["b"].phase is RunPhase.COMPLETED


async def test_scope_escalation_inert_without_hook():
    # No on_boundary hook (autonomous / tests): a scope escalation is inert — the tail runs
    # straight through, exactly like bind_after_deps / checkpoint_after without a hook.
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",)))
    res = await WaveScheduler().run(plan, _scope_exec)
    assert res["a"].phase is RunPhase.COMPLETED
    assert res["b"].phase is RunPhase.COMPLETED


async def test_scope_boundary_consumed_not_refired_on_resume():
    # The YIELD-then-resume loop terminates: surfacing the signal marks it consumed, so a
    # resume (seed_completed re-seeds a, escalation and all) does NOT re-fire the boundary —
    # b runs to terminal. Guards the infinite-boundary risk across the replan resume.
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",)))

    async def yield_hook(_reason, _nodes, _completed):
        return BoundaryOutcome.YIELD

    paused = await WaveScheduler().run(plan, _scope_exec, on_boundary=yield_hook)
    assert "b" not in paused
    assert paused["a"].escalations[0]["consumed"] is True  # surfaced → consumed

    refires = {"n": 0}

    async def count_hook(_reason, _nodes, _completed):
        refires["n"] += 1
        return BoundaryOutcome.YIELD

    resumed = await WaveScheduler().run(
        plan, _scope_exec, seed_completed=paused, on_boundary=count_hook
    )
    assert refires["n"] == 0  # consumed signal never re-fires
    assert resumed["b"].phase is RunPhase.COMPLETED


# --- 依赖缺口·卡在缺输入 (escalate kind=dep, §2.4 变·worker 的「拉」) ---


def _dep_state(run_id: str) -> RunState:
    """A COMPLETED state carrying a dependency-gap escalation (escalate kind=dep)."""
    return RunState(
        phase=RunPhase.COMPLETED,
        content=run_id,
        escalations=[{"question": "缺错误返回结构才能写测试", "assumption": "暂按X", "kind": "dep"}],
    )


async def _dep_exec(spec: RunSpec, _completed) -> RunState:
    # Node "a" flags a dependency gap (卡在缺输入); everything else completes plainly.
    if spec.run_id == "a":
        return _dep_state("a")
    return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)


async def test_dep_escalation_rides_reactive_boundary_and_is_consumed():
    # §2.4: a worker卡在缺输入 (escalate kind=dep) is a reactive-boundary trigger just like a
    # scope deviation — it yields the CEO/lead (to replan(add) a producer) when un-run downstream
    # remains, and surfacing it marks it consumed so the resume loop terminates (no respin).
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",)))
    seen: list = []

    async def yield_hook(reason, nodes, completed):
        seen.append((reason, [n.run_id for n in nodes], set(completed)))
        return BoundaryOutcome.YIELD

    paused = await WaveScheduler().run(plan, _dep_exec, on_boundary=yield_hook)
    assert seen == [(BoundaryReason.SCOPE, ["a"], {"a"})]
    assert "b" not in paused  # soft pause leaves the tail for the CEO's replan(add) resume
    assert paused["a"].escalations[0]["consumed"] is True  # surfaced → consumed


async def test_dep_escalation_not_in_scope_drift_tally():
    # 学·度量 §2.5 漂移率 must stay scope-only: a dep (依赖缺口) is counted in the TOTAL
    # escalation tally but NOT in scope_escalations, so it can't pollute the drift metric.
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",)))

    async def hook(_reason, _nodes, _completed):
        return BoundaryOutcome.PROCEED

    sink: list[BatchMetrics] = []
    await WaveScheduler().run(plan, _dep_exec, on_boundary=hook, metrics_sink=sink)
    m = sink[0]
    assert m.scope_boundaries == 1  # the dep rode the reactive boundary
    assert (m.escalations, m.scope_escalations) == (1, 0)  # counted in total, not in drift


# --- 调度埋点量化 (BatchMetrics) ---


async def _slow_ok(spec: RunSpec, _completed) -> RunState:
    await asyncio.sleep(0.02)
    return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)


async def test_metrics_sink_reports_batch_health():
    plan = RunPlan()
    for x in ("a", "b", "c"):
        plan.add(_spec(x))
    sink: list[BatchMetrics] = []
    await WaveScheduler().run(plan, _ok, metrics_sink=sink)
    assert len(sink) == 1
    m = sink[0]
    assert m.nodes == 3
    assert (m.completed, m.failed, m.skipped) == (3, 0, 0)
    assert m.peak_running >= 1
    assert m.slot_starved == 0  # default width (12) ≥ 3 → no starvation
    assert m.wall_ms >= 0 and m.busy_ms >= 0


async def test_metrics_not_appended_without_sink():
    # No sink → no metrics work surfaced (the scheduler return shape is unchanged).
    plan = RunPlan()
    plan.add(_spec("a"))
    res = await WaveScheduler().run(plan, _ok)
    assert res["a"].phase is RunPhase.COMPLETED


async def test_metrics_peak_running_reflects_parallelism():
    plan = RunPlan()
    for x in ("a", "b", "c"):
        plan.add(_spec(x))
    sink: list[BatchMetrics] = []
    await WaveScheduler(max_parallel=8).run(plan, _slow_ok, metrics_sink=sink)
    m = sink[0]
    assert m.peak_running == 3  # all three overlap under a wide cap
    assert m.slot_starved == 0


async def test_metrics_slot_starved_when_width_capped():
    plan = RunPlan()
    for x in ("a", "b", "c"):
        plan.add(_spec(x))
    sink: list[BatchMetrics] = []
    # width 1 → siblings can't all start; ready nodes starve on the cap.
    await WaveScheduler(max_parallel=1).run(plan, _slow_ok, metrics_sink=sink)
    m = sink[0]
    assert m.width == 1
    assert m.peak_running == 1
    assert m.slot_starved > 0
    assert m.completed == 3


async def test_metrics_excludes_seeded_nodes():
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",)))
    seed = {"a": RunState(phase=RunPhase.COMPLETED, content="a")}
    sink: list[BatchMetrics] = []
    await WaveScheduler().run(plan, _ok, seed_completed=seed, metrics_sink=sink)
    m = sink[0]
    assert m.nodes == 1  # only b actually ran here
    assert m.completed == 1


# --- 多任务并行图 (并行时间线 · per-node timing windows) ---


async def test_metrics_timeline_records_overlapping_windows():
    # 真并发：three independent slow nodes under a wide cap all overlap, so their timeline
    # windows mutually overlap (latest start < earliest end) — the gantt's「重叠＝真并行」.
    plan = RunPlan()
    for x in ("a", "b", "c"):
        plan.add(_spec(x))
    sink: list[BatchMetrics] = []
    await WaveScheduler(max_parallel=8).run(plan, _slow_ok, metrics_sink=sink)
    tl = sink[0].timeline
    assert {n.run_id for n in tl} == {"a", "b", "c"}
    assert all(n.outcome == "completed" for n in tl)
    assert all(0 <= n.start_ms <= n.end_ms for n in tl)
    assert max(n.start_ms for n in tl) < min(n.end_ms for n in tl)


async def test_metrics_timeline_serialized_under_width_one():
    # 串行化：width 1 forces the three nodes sequential, so their windows do NOT overlap —
    # each starts at/after the previous one ends (the gantt's gap that exposes the cap).
    plan = RunPlan()
    for x in ("a", "b", "c"):
        plan.add(_spec(x))
    sink: list[BatchMetrics] = []
    await WaveScheduler(max_parallel=1).run(plan, _slow_ok, metrics_sink=sink)
    tl = sorted(sink[0].timeline, key=lambda n: n.start_ms)
    assert len(tl) == 3
    for prev, nxt in zip(tl, tl[1:], strict=False):
        assert nxt.start_ms >= prev.end_ms


async def test_metrics_timeline_excludes_skipped_and_marks_failure():
    # Only DISPATCHED nodes get a window: a fails (skip cascade), so b never ran and carries
    # no bar — but a's window stays, stamped with its failed outcome.
    plan = RunPlan()
    plan.add(_spec("a", on_failure="skip"))
    plan.add(_spec("b", ("a",)))

    async def ex(spec: RunSpec, _completed) -> RunState:
        if spec.run_id == "a":
            return RunState(phase=RunPhase.FAILED, error="boom")
        return RunState(phase=RunPhase.COMPLETED, content="b")

    sink: list[BatchMetrics] = []
    await WaveScheduler().run(plan, ex, metrics_sink=sink)
    m = sink[0]
    assert m.skipped == 1  # b cascade-skipped
    assert [n.run_id for n in m.timeline] == ["a"]
    assert m.timeline[0].outcome == "failed"


# --- 受监督波循环埋点 (boundary + escalation tallies) ---


async def test_metrics_boundary_counts_zero_for_ordinary_plan():
    # 成本纪律: a plain DAG with a wired hook that never trips a marker tallies no
    # boundaries and no escalations — the埋点 stays quiet for零新增回合 plans.
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",)))

    async def hook(_reason, _nodes, _completed):
        return BoundaryOutcome.PROCEED

    sink: list[BatchMetrics] = []
    await WaveScheduler().run(plan, _ok, on_boundary=hook, metrics_sink=sink)
    m = sink[0]
    assert (m.bind_boundaries, m.scope_boundaries, m.checkpoint_boundaries) == (0, 0, 0)
    assert (m.escalations, m.scope_escalations) == (0, 0)


async def test_metrics_counts_bind_boundary():
    # 晚绑定触发次数: a late-bound b fires one BIND boundary (and nothing else).
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",), bind_after_deps=True))

    async def hook(_reason, nodes, _completed):
        for n in nodes:
            n.bind_after_deps = False
        return BoundaryOutcome.PROCEED

    sink: list[BatchMetrics] = []
    await WaveScheduler().run(plan, _ok, on_boundary=hook, metrics_sink=sink)
    m = sink[0]
    assert m.bind_boundaries == 1
    assert (m.scope_boundaries, m.checkpoint_boundaries) == (0, 0)


async def test_metrics_counts_checkpoint_boundary():
    # checkpoint_after → one CHECKPOINT boundary fired (user plan_review arm).
    plan = RunPlan()
    plan.add(_spec("a", checkpoint_after=True))
    plan.add(_spec("b", ("a",)))

    async def hook(_reason, _nodes, _completed):
        return BoundaryOutcome.PROCEED

    sink: list[BatchMetrics] = []
    await WaveScheduler().run(plan, _ok, on_boundary=hook, metrics_sink=sink)
    m = sink[0]
    assert m.checkpoint_boundaries == 1
    assert (m.bind_boundaries, m.scope_boundaries) == (0, 0)


async def test_metrics_counts_scope_boundary_and_escalations():
    # 计划漂移返工触发数 + scope 信号占比: a flags a scope deviation; PROCEED runs the
    # tail so both nodes ran. One SCOPE boundary fired; one of one escalation is scope.
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",)))

    async def hook(_reason, _nodes, _completed):
        return BoundaryOutcome.PROCEED

    sink: list[BatchMetrics] = []
    await WaveScheduler().run(plan, _scope_exec, on_boundary=hook, metrics_sink=sink)
    m = sink[0]
    assert m.scope_boundaries == 1
    assert (m.escalations, m.scope_escalations) == (1, 1)  # → host derives 占比 = 1.0
    assert (m.bind_boundaries, m.checkpoint_boundaries) == (0, 0)


async def test_metrics_scope_escalation_counted_without_hook():
    # The escalation tally is hook-independent (it reads terminal state): a scope
    # escalation is counted even when no hook is wired (so no boundary fires).
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",)))
    sink: list[BatchMetrics] = []
    await WaveScheduler().run(plan, _scope_exec, metrics_sink=sink)
    m = sink[0]
    assert m.scope_boundaries == 0  # no hook ⇒ no boundary fired
    assert (m.escalations, m.scope_escalations) == (1, 1)  # but the signal is still tallied


# --- Phase 2a: per-run cancel (redirect) ---


async def test_cancel_single_run_siblings_continue():
    """Parallel a + b; cancel a mid-flight → b still completes, a is CANCELLED."""
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b"))
    cancel_targets: set[str] = set()

    async def slow_ex(spec: RunSpec, _completed) -> RunState:
        if spec.run_id == "a":
            await asyncio.sleep(0.1)  # slow — will be cancelled
        else:
            await asyncio.sleep(0.01)
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    # After a short delay, request cancel of "a"
    async def _schedule_cancel():
        await asyncio.sleep(0.02)
        cancel_targets.add("a")

    asyncio.create_task(_schedule_cancel())
    res = await WaveScheduler().run(
        plan, slow_ex, cancel_run_ids=lambda: frozenset(cancel_targets)
    )
    assert res["a"].phase is RunPhase.CANCELLED
    assert res["b"].phase is RunPhase.COMPLETED


async def test_cancel_pending_withdraws_before_dispatch():
    """cancel_run_ids for a not-yet-launched node → SKIPPED, executor never called."""
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b"))  # independent; width=1 so b stays queued while a runs
    cancel_targets: set[str] = set()
    ran: list[str] = []

    async def slow_ex(spec: RunSpec, _completed) -> RunState:
        ran.append(spec.run_id)
        if spec.run_id == "a":
            await asyncio.sleep(0.08)
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    async def _schedule_cancel():
        await asyncio.sleep(0.02)
        cancel_targets.add("b")

    asyncio.create_task(_schedule_cancel())
    skipped: list[tuple[str, str, str]] = []
    res = await WaveScheduler(max_parallel=1).run(
        plan,
        slow_ex,
        cancel_run_ids=lambda: frozenset(cancel_targets),
        on_skipped=lambda rid, aid, reason: skipped.append((rid, aid, reason)),
    )
    assert res["a"].phase is RunPhase.COMPLETED
    assert res["b"].phase is RunPhase.SKIPPED
    assert "b" not in ran
    assert skipped == [("b", "b", "abort")]


async def test_cancel_cascades_skip_by_default():
    """Cancel sole upstream a → dependent b skips (zero successful upstreams)."""
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b", ("a",)))
    cancel_targets: set[str] = set()

    async def slow_ex(spec: RunSpec, _completed) -> RunState:
        if spec.run_id == "a":
            await asyncio.sleep(0.1)
        else:
            await asyncio.sleep(0.01)
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    async def _schedule_cancel():
        await asyncio.sleep(0.02)
        cancel_targets.add("a")

    asyncio.create_task(_schedule_cancel())
    res = await WaveScheduler().run(
        plan, slow_ex, cancel_run_ids=lambda: frozenset(cancel_targets)
    )
    assert res["a"].phase is RunPhase.CANCELLED
    assert res["b"].phase is RunPhase.SKIPPED


async def test_cancel_metrics_counts_cancelled():
    """Cancelled nodes are tallied in BatchMetrics.cancelled, not failed/skipped."""
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b"))
    cancel_targets: set[str] = set()

    async def slow_ex(spec: RunSpec, _completed) -> RunState:
        if spec.run_id == "a":
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.01)
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    async def _schedule_cancel():
        await asyncio.sleep(0.02)
        cancel_targets.add("a")

    asyncio.create_task(_schedule_cancel())
    sink: list[BatchMetrics] = []
    await WaveScheduler().run(
        plan, slow_ex,
        cancel_run_ids=lambda: frozenset(cancel_targets),
        metrics_sink=sink,
    )
    m = sink[0]
    assert m.cancelled == 1
    assert m.completed == 1
    assert m.failed == 0


async def test_external_stop_cancels_inflight_with_stop_reason():
    """整轮 stop: wave outer cancel uses cancel(\"stop\") so children see reason=stop.

    Pins the dual-cancel contract: redirect cancel keeps msg=\"redirect\"; whole-turn
    abort (cancel the wave task) drains in-flight workers with msg=\"stop\".
    """
    import contextlib

    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b"))
    cancel_msgs: list[tuple[str, str | None]] = []
    started = asyncio.Event()

    async def slow_ex(spec: RunSpec, _completed) -> RunState:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError as e:
            msg = str(e.args[0]) if e.args else None
            cancel_msgs.append((spec.run_id, msg))
            raise
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    wave_task = asyncio.create_task(WaveScheduler().run(plan, slow_ex))
    await started.wait()
    await asyncio.sleep(0.02)  # both workers in flight
    wave_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await wave_task

    assert sorted(rid for rid, _ in cancel_msgs) == ["a", "b"]
    assert {msg for _, msg in cancel_msgs} == {"stop"}


async def test_hard_timeout_target_cancels_with_worker_timeout_msg():
    """硬超时强杀与「改派」共用 cancel 通道，但 msg 必须说实话（→ run_cancelled.reason）。"""
    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b"))
    cancel_targets: set[str] = set()
    cancel_msgs: list[tuple[str, str | None]] = []
    a_started = asyncio.Event()

    async def slow_ex(spec: RunSpec, _completed) -> RunState:
        if spec.run_id == "a":
            a_started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError as e:
                cancel_msgs.append((spec.run_id, str(e.args[0]) if e.args else None))
                raise
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    async def _schedule_cancel():
        await a_started.wait()
        cancel_targets.add("a")

    asyncio.create_task(_schedule_cancel())
    res = await WaveScheduler().run(
        plan,
        slow_ex,
        cancel_run_ids=lambda: frozenset(cancel_targets),
        timeout_run_ids=lambda: frozenset({"a"}),
    )
    assert cancel_msgs == [("a", "worker_timeout")]
    assert res["a"].phase is RunPhase.CANCELLED  # absorbed like redirect, sibling unaffected
    assert res["b"].phase is RunPhase.COMPLETED


async def test_redirect_path_does_not_swallow_stop_cancel():
    """A worker already stop-cancelled must not be absorbed via redirect marker.

    Repro: cancel worker with msg=stop first, then list it in cancel_run_ids.
    Old bug: cancel(\"redirect\") returns False but still entered cancelled_by_redirect,
    then suppress(CancelledError) treated stop as success and kept dispatching (zombie).
    """

    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b"))
    cancel_targets: set[str] = set()
    workers: dict[str, asyncio.Task] = {}
    a_started = asyncio.Event()
    b_started = asyncio.Event()

    async def ex(spec: RunSpec, _completed) -> RunState:
        task = asyncio.current_task()
        assert task is not None
        workers[spec.run_id] = task
        if spec.run_id == "a":
            a_started.set()
            await asyncio.sleep(10)
        else:
            b_started.set()
            await asyncio.sleep(0.01)
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    wave_task = asyncio.create_task(
        WaveScheduler(max_parallel=1).run(
            plan, ex, cancel_run_ids=lambda: frozenset(cancel_targets)
        )
    )
    await a_started.wait()
    workers["a"].cancel("stop")  # external/stop wins first
    cancel_targets.add("a")  # redirect hook fires next cycle; must not claim absorb
    with pytest.raises(asyncio.CancelledError):
        await wave_task
    assert not b_started.is_set(), "wave must not keep scheduling after stop cancel"
    assert wave_task.done()


async def test_external_cancel_after_redirect_terminates_wave():
    """Outer wave cancel during/after redirect must end scheduling (no zombie)."""
    import contextlib

    plan = RunPlan()
    plan.add(_spec("a"))
    plan.add(_spec("b"))
    plan.add(_spec("c"))
    cancel_targets: set[str] = set()
    a_started = asyncio.Event()

    async def ex(spec: RunSpec, _completed) -> RunState:
        if spec.run_id == "a":
            a_started.set()
            await asyncio.sleep(10)
        await asyncio.sleep(0.05)
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    wave_task = asyncio.create_task(
        WaveScheduler(max_parallel=2).run(
            plan, ex, cancel_run_ids=lambda: frozenset(cancel_targets)
        )
    )
    await a_started.wait()
    cancel_targets.add("a")
    await asyncio.sleep(0.08)  # let redirect absorb a; siblings may start
    wave_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await wave_task
    assert wave_task.done()
    assert wave_task.cancelled() or wave_task.exception() is not None


@pytest.mark.asyncio
async def test_external_cancel_materialises_undispatched_as_skipped():
    """1B: parent/nested cancel must not silently LEFT OUT never-dispatched siblings.

    Repro shape: fan-out 4, width 2 → _1/_2 in flight, _3/_4 not yet dispatched;
    outer cancel drains in-flight (run_cancelled) and must emit on_skipped(abort)
    for the undispatched tail so the graph shows「未执行」instead of ghost pending.
    """
    import contextlib

    plan = RunPlan()
    for x in ("_1", "_2", "_3", "_4"):
        plan.add(_spec(x))
    started: set[str] = set()
    gate = asyncio.Event()
    skipped: list[tuple[str, str, str]] = []

    async def ex(spec: RunSpec, _completed) -> RunState:
        started.add(spec.run_id)
        if len(started) >= 2:
            gate.set()
        await asyncio.sleep(10)
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    wave_task = asyncio.create_task(
        WaveScheduler(max_parallel=2).run(
            plan,
            ex,
            on_skipped=lambda rid, aid, reason: skipped.append((rid, aid, reason)),
        )
    )
    await gate.wait()
    await asyncio.sleep(0.02)  # settle: only width=2 should be in flight
    assert started == {"_1", "_2"}
    wave_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await wave_task

    skipped_ids = {rid for rid, _, _ in skipped}
    assert skipped_ids == {"_3", "_4"}
    assert all(reason == "abort" for _, _, reason in skipped)
    assert "_1" not in skipped_ids and "_2" not in skipped_ids


@pytest.mark.asyncio
async def test_soft_stop_cancel_leaves_undispatched_for_resume(monkeypatch):
    """ask_user soft_stop cancel must NOT durable-skip the tail (resume re-drives it)."""
    import contextlib
    from types import SimpleNamespace

    import agentcore.runtime.coordination.session as coord_session

    monkeypatch.setattr(
        coord_session,
        "active_coordination",
        lambda *_a, **_k: SimpleNamespace(soft_stop=True),
    )

    plan = RunPlan()
    for x in ("a", "b", "c"):
        plan.add(_spec(x))
    started: set[str] = set()
    gate = asyncio.Event()
    skipped: list[tuple[str, str, str]] = []

    async def ex(spec: RunSpec, _completed) -> RunState:
        started.add(spec.run_id)
        if len(started) >= 1:
            gate.set()
        await asyncio.sleep(10)
        return RunState(phase=RunPhase.COMPLETED, content=spec.run_id)

    wave_task = asyncio.create_task(
        WaveScheduler(max_parallel=1).run(
            plan,
            ex,
            on_skipped=lambda rid, aid, reason: skipped.append((rid, aid, reason)),
        )
    )
    await gate.wait()
    await asyncio.sleep(0.02)
    wave_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await wave_task

    assert skipped == []
