"""Turn-level token ceiling: settings + meter + reject / wave short-circuit."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentcore.config.engine import EngineSettings
from agentcore.llm.observability import log_llm_call
from agentcore.llm.provider.protocol import TokenUsage
from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState
from agentcore.runtime.turn.token_budget import (
    REASON_TURN_TOKEN_BUDGET,
    bind_turn_token_meter,
    current_turn_tokens,
    is_turn_token_ceiling_hit,
    record_turn_tokens,
    reset_turn_token_meter,
    resolve_turn_token_ceiling,
    tokens_from_journal_entries,
    turn_token_ceiling_reject_message,
)


def test_engine_turn_token_ceiling_default():
    s = EngineSettings()
    assert s.engine_turn_token_ceiling == 30_000_000
    assert s.engine_turn_token_delivery_reserve == 400_000
    assert s.engine_nested_turn_token_ceiling == 8_000_000
    assert s.engine_worker_token_ceiling == 4_000_000  # orthogonal


def test_engine_nested_turn_token_ceiling_disable():
    s = EngineSettings(engine_nested_turn_token_ceiling=0)
    assert s.engine_nested_turn_token_ceiling == 0


def test_engine_turn_token_ceiling_disable():
    s = EngineSettings(engine_turn_token_ceiling=0)
    assert s.engine_turn_token_ceiling == 0


def test_delivery_reserve_hit_window(monkeypatch):
    from agentcore.runtime.turn.token_budget import is_turn_token_delivery_reserve_hit

    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_ceiling",
        lambda: 1000,
    )
    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_delivery_reserve",
        lambda: 200,
    )
    token = bind_turn_token_meter(seed=0)
    try:
        assert not is_turn_token_delivery_reserve_hit()
        record_turn_tokens(799)
        assert not is_turn_token_delivery_reserve_hit()
        record_turn_tokens(1)  # 800 = ceiling - reserve
        assert is_turn_token_delivery_reserve_hit()
        assert not is_turn_token_ceiling_hit()
        record_turn_tokens(200)  # 1000 = hard ceiling
        assert is_turn_token_ceiling_hit()
        assert not is_turn_token_delivery_reserve_hit()  # hard owns the stop
    finally:
        reset_turn_token_meter(token)


def test_delivery_reserve_off_when_reserve_ge_ceiling(monkeypatch):
    from agentcore.runtime.turn.token_budget import is_turn_token_delivery_reserve_hit

    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_ceiling",
        lambda: 100,
    )
    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_delivery_reserve",
        lambda: 100,
    )
    token = bind_turn_token_meter(seed=50)
    try:
        assert not is_turn_token_delivery_reserve_hit()
    finally:
        reset_turn_token_meter(token)


def test_meter_records_and_hits(monkeypatch):
    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_ceiling",
        lambda: 100,
    )
    token = bind_turn_token_meter(seed=0)
    try:
        assert current_turn_tokens() == 0
        assert not is_turn_token_ceiling_hit()
        record_turn_tokens(60)
        assert current_turn_tokens() == 60
        assert not is_turn_token_ceiling_hit()
        record_turn_tokens(50)
        assert current_turn_tokens() == 110
        assert is_turn_token_ceiling_hit()
        assert "110" in turn_token_ceiling_reject_message()
        assert "100" in turn_token_ceiling_reject_message()
    finally:
        reset_turn_token_meter(token)


def test_meter_off_when_ceiling_zero(monkeypatch):
    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_ceiling",
        lambda: 0,
    )
    token = bind_turn_token_meter(seed=999_999)
    try:
        assert not is_turn_token_ceiling_hit()
    finally:
        reset_turn_token_meter(token)


def test_record_noop_without_bound_meter():
    before = current_turn_tokens()
    record_turn_tokens(50_000)
    assert current_turn_tokens() == before


def test_log_llm_call_feeds_turn_meter(monkeypatch):
    monkeypatch.setattr(
        "agentcore.llm.observability.settings.log_llm_bodies",
        False,
    )
    monkeypatch.setattr(
        "agentcore.llm.pricing.calculate_cost",
        lambda *a, **k: MagicMock(
            total=0, credential_source="platform", pricing_source="curated"
        ),
    )
    monkeypatch.setattr(
        "agentcore.llm.pricing.resolve_credential_source",
        lambda **k: "platform",
    )
    token = bind_turn_token_meter(seed=0)
    try:
        log_llm_call(
            scenario="agent",
            model="test-model",
            usage=TokenUsage(input_tokens=40, output_tokens=10),
            finish_reason="stop",
            latency_ms=1,
            stream=False,
        )
        assert current_turn_tokens() == 50
    finally:
        reset_turn_token_meter(token)


def test_tokens_from_journal_entries():
    entries = [
        {"kind": "turn_started", "payload": {}},
        {
            "kind": "llm_call",
            "payload": {"usage": {"input": 100, "output": 20}},
        },
        {
            "kind": "llm_call",
            "payload": {"usage": {"input_tokens": 30, "output_tokens": 5}},
        },
    ]
    assert tokens_from_journal_entries(entries) == 155
    assert tokens_from_journal_entries(None) == 0


@pytest.mark.asyncio
async def test_delegate_execute_rejects_when_ceiling_hit(monkeypatch):
    from agentcore.tools.builtin.delegate.tool import DelegateTool

    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_ceiling",
        lambda: 100,
    )
    token = bind_turn_token_meter(seed=100)
    try:
        assert is_turn_token_ceiling_hit()
        tool = DelegateTool(
            llm=MagicMock(),
            sink=MagicMock(),
            system_prompt="",
            user_message="hi",
            history=[],
            tools=MagicMock(),
            base_tool_context=MagicMock(),
            approval_gate=None,
        )
        tool._tools.list_all = MagicMock(return_value=[])
        result = await tool.execute(
            {"playbook_none_reason": "x" * 20, "tasks": []},
            MagicMock(),
        )
        assert result.success is False
        assert "累计 token" in (result.error or "")
        assert result.contract_failure is True
    finally:
        reset_turn_token_meter(token)


@pytest.mark.asyncio
async def test_wave_should_stop_blocks_new_dispatch(monkeypatch):
    """Turn ceiling hit → WaveScheduler admits no new nodes (in-flight drain only)."""
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunState
    from agentcore.runtime.runs.wave import WaveScheduler

    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_ceiling",
        lambda: 50,
    )
    token = bind_turn_token_meter(seed=50)
    try:
        assert is_turn_token_ceiling_hit()
        plan = RunPlan()
        plan.add(RunSpec(run_id="a", role="调研", task="t1", agent_id="a"))
        plan.add(
            RunSpec(run_id="b", role="写作", task="t2", agent_id="b", depends_on=["a"])
        )
        dispatched: list[str] = []

        async def executor(spec, completed):
            dispatched.append(spec.run_id)
            return RunState(phase=RunPhase.COMPLETED, content="ok")

        results = await WaveScheduler().run(
            plan,
            executor,
            should_stop=is_turn_token_ceiling_hit,
        )
        assert dispatched == []
        assert "a" not in results
        assert "b" not in results
    finally:
        reset_turn_token_meter(token)


@pytest.mark.asyncio
async def test_materialise_turn_token_budget_skips():
    from agentcore.runtime.delegate.drive import _materialise_turn_token_budget_skips
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import Deliverable

    plan = RunPlan()
    plan.add(RunSpec(run_id="done", role="a", task="t", agent_id="done"))
    plan.add(RunSpec(run_id="pending", role="b", task="t2", agent_id="pending"))
    plan.add(
        RunSpec(
            run_id="qa",
            role="页面 QA",
            task="qa",
            agent_id="qa",
            deliverable=Deliverable(
                form="files",
                artifacts=["site/QA.md"],
                web_quality_scan=True,
                visual_critic=True,
            ),
        )
    )
    results = {"done": RunState(phase=RunPhase.COMPLETED)}
    sink = MagicMock()
    tool = MagicMock()
    tool._sink = sink
    _materialise_turn_token_budget_skips(tool, plan, results)
    assert results["pending"].phase is RunPhase.SKIPPED
    assert results["pending"].delivery_gaps
    assert results["pending"].delivery_gaps[0]["reason"] == REASON_TURN_TOKEN_BUDGET
    assert results["qa"].phase is RunPhase.SKIPPED
    qa_descs = [g["description"] for g in results["qa"].delivery_gaps]
    assert any("验收" in d or "未目验" in d or "视觉" in d for d in qa_descs)
    assert any("续派" in d or "下一回合" in d for d in qa_descs)
    assert any(g.get("reason") == "qa_deferred_budget" for g in results["qa"].delivery_gaps)
    assert not any("未跑 web_quality" in d for d in qa_descs)
    assert sink.emit.call_count == 2


@pytest.mark.asyncio
async def test_priority_reserve_admits_qa_cuts_secondary(monkeypatch):
    """Reserve window: after ≥1 section done, cut remaining sections; still run QA."""
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import Deliverable, RunState
    from agentcore.runtime.runs.wave import WaveScheduler
    from agentcore.runtime.turn.token_budget import is_turn_token_delivery_reserve_hit

    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_ceiling",
        lambda: 1000,
    )
    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_delivery_reserve",
        lambda: 400,
    )
    token = bind_turn_token_meter(seed=0)
    try:
        plan = RunPlan()
        plan.add(RunSpec(run_id="s0", role="区0", task="t", agent_id="s0"))
        plan.add(RunSpec(run_id="s1", role="区1", task="t", agent_id="s1"))
        plan.add(
            RunSpec(
                run_id="qa",
                role="QA",
                task="qa",
                agent_id="qa",
                depends_on=["s0", "s1"],
                ceiling_priority=True,
                deliverable=Deliverable(
                    form="files",
                    web_quality_scan=True,
                    visual_critic=True,
                ),
            )
        )
        dispatched: list[str] = []

        async def executor(spec, completed):
            dispatched.append(spec.run_id)
            # After first section, enter reserve window (spent ≥ 600).
            if spec.run_id == "s0":
                record_turn_tokens(650)
                assert is_turn_token_delivery_reserve_hit()
            return RunState(phase=RunPhase.COMPLETED, content="ok")

        results = await WaveScheduler(max_parallel=1).run(
            plan,
            executor,
            should_stop=is_turn_token_ceiling_hit,
            priority_reserve_hit=is_turn_token_delivery_reserve_hit,
        )
        assert "s0" in dispatched
        assert "qa" in dispatched
        assert "s1" not in dispatched
        assert results["s1"].phase is RunPhase.SKIPPED
        assert results["qa"].phase is RunPhase.COMPLETED
    finally:
        reset_turn_token_meter(token)


@pytest.mark.asyncio
async def test_priority_reserve_admits_assemble_and_qa_cuts_secondary(monkeypatch):
    """Wave3 D: assemble+QA both ceiling_priority; reserve cuts leftover sections."""
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import Deliverable, RunState
    from agentcore.runtime.runs.wave import WaveScheduler
    from agentcore.runtime.turn.token_budget import is_turn_token_delivery_reserve_hit

    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_ceiling",
        lambda: 1000,
    )
    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_delivery_reserve",
        lambda: 400,
    )
    token = bind_turn_token_meter(seed=0)
    try:
        plan = RunPlan()
        plan.add(RunSpec(run_id="s0", role="区0", task="t", agent_id="s0"))
        plan.add(RunSpec(run_id="s1", role="区1", task="t", agent_id="s1"))
        plan.add(
            RunSpec(
                run_id="assemble",
                role="组装",
                task="assemble",
                agent_id="assemble",
                depends_on=["s0", "s1"],
                ceiling_priority=True,
            )
        )
        plan.add(
            RunSpec(
                run_id="qa",
                role="QA",
                task="qa",
                agent_id="qa",
                depends_on=["assemble"],
                ceiling_priority=True,
                deliverable=Deliverable(
                    form="files",
                    web_quality_scan=True,
                    visual_critic=True,
                ),
            )
        )
        dispatched: list[str] = []

        async def executor(spec, completed):
            dispatched.append(spec.run_id)
            if spec.run_id == "s0":
                record_turn_tokens(650)
                assert is_turn_token_delivery_reserve_hit()
            return RunState(phase=RunPhase.COMPLETED, content="ok")

        results = await WaveScheduler(max_parallel=1).run(
            plan,
            executor,
            should_stop=is_turn_token_ceiling_hit,
            priority_reserve_hit=is_turn_token_delivery_reserve_hit,
        )
        assert "s0" in dispatched
        assert "assemble" in dispatched
        assert "qa" in dispatched
        assert "s1" not in dispatched
        assert results["s1"].phase is RunPhase.SKIPPED
        assert results["assemble"].phase is RunPhase.COMPLETED
        assert results["qa"].phase is RunPhase.COMPLETED
    finally:
        reset_turn_token_meter(token)


@pytest.mark.asyncio
async def test_skip_qa_delivery_status_partial_with_honesty_gaps():
    from agentcore.runtime.delegate.delivery_status import build_delivery_status
    from agentcore.runtime.delegate.drive import _materialise_turn_token_budget_skips
    from agentcore.runtime.runs.file_acceptance import build_file_acceptance
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import Deliverable

    plan = RunPlan()
    plan.add(
        RunSpec(
            run_id="s0",
            role="区0",
            task="t",
            agent_id="s0",
        )
    )
    plan.add(
        RunSpec(
            run_id="qa",
            role="页面 QA",
            task="qa",
            agent_id="qa",
            depends_on=["s0"],
            ceiling_priority=True,
            deliverable=Deliverable(
                form="files",
                artifacts=["site/QA.md"],
                web_quality_scan=True,
                visual_critic=True,
            ),
        )
    )
    results = {
        "s0": RunState(
            phase=RunPhase.COMPLETED,
            files_touched=["site/index.html"],
            file_acceptance=build_file_acceptance(
                ["site/index.html"], phase=RunPhase.COMPLETED
            ),
        )
    }
    tool = MagicMock()
    tool._sink = MagicMock()
    _materialise_turn_token_budget_skips(tool, plan, results)
    payload = build_delivery_status(plan, results, execution_id="e1")
    assert payload is not None
    assert payload["state"] == "partial"
    descs = " ".join(g["description"] for g in payload["gaps"])
    assert "验收" in descs or "未目验" in descs or "视觉" in descs
    assert "续派" in descs or "下一回合" in descs
    assert "web_quality" not in descs  # section gates already ran; don't false-claim
    assert any(g.get("reason") == "qa_deferred_budget" for g in payload["gaps"])
    assert not any(a.get("kind") == "website_verify" for a in payload["actions"])
    kinds = {a.get("kind") for a in payload["actions"]}
    assert "continue_skipped_runs" in kinds
    assert "continue_writing" not in kinds


def test_reason_constant_reserved_in_cutoff():
    from agentcore.runtime.runs.cutoff import REASON_TURN_TOKEN_BUDGET as CUTOFF_REASON

    assert CUTOFF_REASON == REASON_TURN_TOKEN_BUDGET
    assert resolve_turn_token_ceiling() in (0, 30_000_000) or resolve_turn_token_ceiling() >= 0


def test_wrap_prompt_is_explicit_close_not_fake_done(monkeypatch):
    from agentcore.runtime.turn.token_budget import turn_token_budget_wrap_prompt

    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_ceiling",
        lambda: 100,
    )
    token = bind_turn_token_meter(seed=100)
    try:
        text = turn_token_budget_wrap_prompt()
        assert text.startswith("[系统提示]")
        assert "触顶" in text
        assert "100" in text
        assert "delegate" in text or "派" in text
        assert "假" in text or "伪装" in text
        assert REASON_TURN_TOKEN_BUDGET in text
        assert "续派" in text or "验收" in text
        assert "下一回合" in text and "续跑" in text
        assert "禁止假装" in text or "伪装" in text
    finally:
        reset_turn_token_meter(token)


def test_maybe_inject_turn_token_budget_gate_one_shot(monkeypatch):
    from agentcore.llm.provider.protocol import LLMMessage
    from agentcore.runtime.engine.governance import (
        create_loop_controller,
        maybe_inject_turn_token_budget_gate,
        should_turn_token_budget_gate,
    )

    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_ceiling",
        lambda: 50,
    )
    token = bind_turn_token_meter(seed=50)
    try:
        controller = create_loop_controller(frozenset())
        assert should_turn_token_budget_gate(controller, role="captain") is True
        assert should_turn_token_budget_gate(controller, role="worker") is False

        messages: list[LLMMessage] = []
        assert (
            maybe_inject_turn_token_budget_gate(
                controller,
                messages=messages,
                run_id="r1",
                round_idx=2,
                role="captain",
            )
            is True
        )
        assert len(messages) == 1
        assert "触顶" in (messages[0].content or "")
        assert controller.turn_token_budget_gate_fired is True

        # One-shot latch
        assert should_turn_token_budget_gate(controller, role="captain") is False
        assert (
            maybe_inject_turn_token_budget_gate(
                controller,
                messages=messages,
                run_id="r1",
                round_idx=3,
                role="captain",
            )
            is False
        )
        assert len(messages) == 1
    finally:
        reset_turn_token_meter(token)


def test_audit_and_debate_gates_suppressed_when_ceiling_hit(monkeypatch):
    """触顶后不可再派审计/辩论 —— soft gate 不得反向催派。"""
    from agentcore.llm.provider.protocol import LLMMessage
    from agentcore.runtime.engine.governance import (
        should_audit_gate,
        should_debate_gate,
    )
    from agentcore.runtime.loop_controller import LoopController

    debate_msgs = [
        LLMMessage(role="tool", content="用户选择：辩论（正反攻防）"),
    ]

    # Ceiling off → gates eligible.
    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_ceiling",
        lambda: 0,
    )
    controller = LoopController()
    controller.mark_post_delegate(node_count=3, has_deps=True, audit_hard=True)
    assert should_audit_gate(controller, role="captain") is True
    assert should_debate_gate(controller, role="captain", messages=debate_msgs) is True

    # Ceiling hit → both suppressed.
    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_ceiling",
        lambda: 10,
    )
    token = bind_turn_token_meter(seed=10)
    try:
        assert is_turn_token_ceiling_hit()
        assert should_audit_gate(controller, role="captain") is False
        assert should_debate_gate(controller, role="captain", messages=debate_msgs) is False
    finally:
        reset_turn_token_meter(token)


def test_should_audit_gate_skips_without_hard_when_ceiling_off(monkeypatch):
    """Substantial parallel_brief-style batch must not soft-nudge without audit_hard."""
    from agentcore.runtime.engine.governance import should_audit_gate
    from agentcore.runtime.loop_controller import LoopController

    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_ceiling",
        lambda: 0,
    )
    controller = LoopController()
    controller.mark_post_delegate(node_count=3, has_deps=True)
    assert should_audit_gate(controller, role="captain") is False


def test_turn_token_budget_gate_seed_round_trip():
    from agentcore.runtime.engine.governance import create_loop_controller
    from agentcore.runtime.loop_controller import LoopController

    c = LoopController()
    c.mark_turn_token_budget_gate_fired()
    seed = c.export_seed()
    assert seed["turn_token_budget_gate_fired"] is True
    restored = create_loop_controller(frozenset(), seed=seed)
    assert restored.turn_token_budget_gate_fired is True


def test_nested_envelope_isolates_from_parent_ceiling(monkeypatch):
    """Wave hooks bind nested envelope stop; parent reserve is off while nested."""
    from agentcore.runtime.turn.token_budget import (
        bind_nested_envelope,
        is_nested_envelope_hit,
        is_turn_token_ceiling_hit,
        release_nested_envelope,
        reset_nested_envelope,
        resolve_wave_budget_hooks,
        try_reserve_nested_envelope,
    )

    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_ceiling",
        lambda: 1000,
    )
    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_nested_turn_token_ceiling",
        lambda: 400,
    )
    token = bind_turn_token_meter(seed=100)
    try:
        env = try_reserve_nested_envelope(depth=1)
        assert env is not None
        assert env.envelope == 400
        assert env.baseline == 100
        nest_tok = bind_nested_envelope(env)
        try:
            should_stop, priority = resolve_wave_budget_hooks(credential_source="user")
            assert priority is None  # nested disables parent reserve
            # Composed stop (= nested envelope OR turn auth-dead); identity may wrap.
            assert should_stop() is is_nested_envelope_hit()
            assert not is_nested_envelope_hit()
            record_turn_tokens(399)
            assert not is_nested_envelope_hit()
            assert not is_turn_token_ceiling_hit()
            record_turn_tokens(1)  # nested used=400 → envelope hit
            assert is_nested_envelope_hit()
            assert should_stop() is True
        finally:
            reset_nested_envelope(nest_tok)
            release_nested_envelope(env)
    finally:
        reset_turn_token_meter(token)


def test_nested_envelope_rejects_when_parent_remaining_zero(monkeypatch):
    from agentcore.runtime.turn.token_budget import (
        NestedEnvelopeRejected,
        nested_turn_envelope_scope,
        try_reserve_nested_envelope,
    )

    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_ceiling",
        lambda: 1000,
    )
    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_nested_turn_token_ceiling",
        lambda: 400,
    )
    token = bind_turn_token_meter(seed=1000)
    try:
        assert try_reserve_nested_envelope(depth=1) is None
        with pytest.raises(NestedEnvelopeRejected), nested_turn_envelope_scope(depth=1):
            pass
    finally:
        reset_turn_token_meter(token)


def test_nested_envelope_parallel_reserve_no_double_claim(monkeypatch):
    from agentcore.runtime.turn.token_budget import (
        release_nested_envelope,
        try_reserve_nested_envelope,
    )

    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_ceiling",
        lambda: 1000,
    )
    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_nested_turn_token_ceiling",
        lambda: 800,
    )
    token = bind_turn_token_meter(seed=200)
    try:
        a = try_reserve_nested_envelope(depth=1)
        b = try_reserve_nested_envelope(depth=1)
        assert a is not None and a.envelope == 800  # min(800, remaining 800)
        # After A took 800, remaining=0 → B must fail.
        assert b is None
        release_nested_envelope(a)
        c = try_reserve_nested_envelope(depth=1)
        assert c is not None and c.envelope == 800
        release_nested_envelope(c)
    finally:
        reset_turn_token_meter(token)


@pytest.mark.asyncio
async def test_nested_wave_ignores_parent_ceiling_until_envelope(monkeypatch):
    """Parent ceiling appearing mid-flight must not cut nested tail before envelope."""
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.wave import WaveScheduler
    from agentcore.runtime.turn.token_budget import (
        bind_nested_envelope,
        is_nested_envelope_hit,
        is_turn_token_ceiling_hit,
        release_nested_envelope,
        reset_nested_envelope,
        resolve_wave_budget_hooks,
        try_reserve_nested_envelope,
    )

    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_ceiling",
        lambda: 1000,
    )
    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_nested_turn_token_ceiling",
        lambda: 500,
    )
    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_delivery_reserve",
        lambda: 200,
    )
    token = bind_turn_token_meter(seed=0)
    try:
        env = try_reserve_nested_envelope(depth=1)
        assert env is not None
        assert env.envelope == 500
        nest_tok = bind_nested_envelope(env)
        try:
            plan = RunPlan()
            plan.add(RunSpec(run_id="a", role="基建", task="t", agent_id="a"))
            plan.add(
                RunSpec(
                    run_id="b",
                    role="整合",
                    task="t2",
                    agent_id="b",
                    depends_on=["a"],
                )
            )
            dispatched: list[str] = []
            parent_hit = {"v": False}

            def _parent_hit() -> bool:
                return parent_hit["v"] or is_turn_token_ceiling_hit()

            async def executor(spec, completed):
                dispatched.append(spec.run_id)
                if spec.run_id == "a":
                    record_turn_tokens(100)
                    # Simulate parent ceiling already true (e.g. sibling burn /
                    # admission race) while nested envelope still has room.
                    parent_hit["v"] = True
                    assert _parent_hit()
                    assert not is_nested_envelope_hit()
                return RunState(phase=RunPhase.COMPLETED, content="ok")

            should_stop, priority = resolve_wave_budget_hooks(credential_source="user")
            # Nested hooks must NOT use parent ceiling — even if parent is "hit".
            assert priority is None
            assert should_stop() is is_nested_envelope_hit()
            results = await WaveScheduler(max_parallel=1).run(
                plan,
                executor,
                should_stop=should_stop,
                priority_reserve_hit=priority,
            )
            assert dispatched == ["a", "b"]
            assert results["b"].phase is RunPhase.COMPLETED
        finally:
            reset_nested_envelope(nest_tok)
            release_nested_envelope(env)
    finally:
        reset_turn_token_meter(token)


@pytest.mark.asyncio
async def test_nested_disables_parent_priority_reserve_cut(monkeypatch):
    """Nested path must not cut secondary nodes via parent delivery reserve."""
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import Deliverable
    from agentcore.runtime.runs.wave import WaveScheduler
    from agentcore.runtime.turn.token_budget import (
        bind_nested_envelope,
        is_turn_token_delivery_reserve_hit,
        release_nested_envelope,
        reset_nested_envelope,
        resolve_wave_budget_hooks,
        try_reserve_nested_envelope,
    )

    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_ceiling",
        lambda: 1000,
    )
    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_nested_turn_token_ceiling",
        lambda: 800,
    )
    monkeypatch.setattr(
        "agentcore.runtime.turn.token_budget.resolve_turn_token_delivery_reserve",
        lambda: 400,
    )
    token = bind_turn_token_meter(seed=500)
    try:
        env = try_reserve_nested_envelope(depth=1)
        assert env is not None
        nest_tok = bind_nested_envelope(env)
        try:
            # Enter parent reserve window (spent ≥ 600) without hitting nested envelope.
            record_turn_tokens(150)
            assert is_turn_token_delivery_reserve_hit()
            should_stop, priority = resolve_wave_budget_hooks(credential_source="user")
            assert priority is None

            plan = RunPlan()
            plan.add(RunSpec(run_id="s0", role="区0", task="t", agent_id="s0"))
            plan.add(RunSpec(run_id="s1", role="区1", task="t", agent_id="s1"))
            plan.add(
                RunSpec(
                    run_id="qa",
                    role="QA",
                    task="qa",
                    agent_id="qa",
                    depends_on=["s0", "s1"],
                    ceiling_priority=True,
                    deliverable=Deliverable(
                        form="files",
                        web_quality_scan=True,
                        visual_critic=True,
                    ),
                )
            )
            dispatched: list[str] = []

            async def executor(spec, completed):
                dispatched.append(spec.run_id)
                return RunState(phase=RunPhase.COMPLETED, content="ok")

            results = await WaveScheduler(max_parallel=1).run(
                plan,
                executor,
                should_stop=should_stop,
                priority_reserve_hit=priority,
            )
            # Without parent reserve cut, all nodes run.
            assert set(dispatched) == {"s0", "s1", "qa"}
            assert results["s1"].phase is RunPhase.COMPLETED
        finally:
            reset_nested_envelope(nest_tok)
            release_nested_envelope(env)
    finally:
        reset_turn_token_meter(token)


def test_delivery_status_continue_skipped_runs_not_continue_writing():
    from agentcore.runtime.delegate.delivery_status import build_delivery_status
    from agentcore.runtime.runs.file_acceptance import build_file_acceptance
    from agentcore.runtime.runs.plan import RunPlan

    plan = RunPlan()
    plan.add(RunSpec(run_id="done", role="基建", task="t"))
    plan.add(RunSpec(run_id="tail", role="整合", task="t2", depends_on=["done"]))
    results = {
        "done": RunState(
            phase=RunPhase.COMPLETED,
            files_touched=["app/App.tsx"],
            file_acceptance=build_file_acceptance(
                ["app/App.tsx"], phase=RunPhase.COMPLETED
            ),
        ),
        "tail": RunState(
            phase=RunPhase.SKIPPED,
            delivery_gaps=[
                {
                    "description": "本回合累计 token 已触顶，未派发节点已跳过",
                    "reason": REASON_TURN_TOKEN_BUDGET,
                }
            ],
        ),
    }
    payload = build_delivery_status(plan, results, execution_id="e-skip")
    assert payload is not None
    assert payload["state"] == "partial"
    kinds = {a.get("kind") for a in payload.get("actions") or []}
    assert "continue_skipped_runs" in kinds
    assert "continue_writing" not in kinds
    action = next(a for a in payload["actions"] if a["kind"] == "continue_skipped_runs")
    assert "整合" in action["description"] or "整合" in action["prompt"]
    assert "续跑" in action["description"]
    assert "prompt" in action
