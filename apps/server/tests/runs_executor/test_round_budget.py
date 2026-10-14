"""Pass-local round budget: dedicated write_pass / light_repair cap; no product round fuse."""

from agentcore.llm.provider.protocol import LLMChunk, TokenUsage, ToolCallDelta
from agentcore.runtime.events import EventSink
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.executor import build_agent_executor
from agentcore.runtime.runs.executor.retry import (
    _pass_max_rounds,
    bind_round_budget_on_begin,
)
from agentcore.runtime.runs.types import RunPhase
from agentcore.runtime.runs.wave import WaveScheduler
from agentcore.tools.builtin.handoff import HandoffTool
from agentcore.tools.registry import ToolRegistry
from tests.runs_executor.conftest import _ctx, _FileWriteTool, _ScriptedRounds


def test_light_pass_rounds_are_dedicated():
    assert _pass_max_rounds(light_pass=True, profile_max=0) == 4
    assert _pass_max_rounds(light_pass=True, profile_max=1) == 4
    assert _pass_max_rounds(light_pass=True, profile_max=80, spent=80) == 4


def test_pass_max_rounds_unlimited_when_profile_has_no_fuse():
    assert _pass_max_rounds(light_pass=False, profile_max=0) is None
    assert _pass_max_rounds(light_pass=False, profile_max=0, spent=999) is None
    assert _pass_max_rounds(light_pass=False, profile_max=8) == 8
    assert _pass_max_rounds(light_pass=False, profile_max=8, spent=8) == 8


def test_max_rounds_input_keeps_explicit_stamp():
    plan, errs = build_run_plan(
        [{"role": "A", "task": "a", "max_rounds": 9999}],
        id_prefix="t",
    )
    assert errs == []
    assert plan.nodes[0].max_rounds == 9999
    plan_ok, _ = build_run_plan(
        [{"role": "A", "task": "a", "max_rounds": 8}],
        id_prefix="t",
    )
    assert plan_ok.nodes[0].max_rounds == 8
    plan_low, _ = build_run_plan(
        [{"role": "A", "task": "a", "max_rounds": 0}],
        id_prefix="t",
    )
    assert plan_low.nodes[0].max_rounds is None


def test_bind_round_budget_on_begin_increments():
    used_box = [0]
    limit_box = [8]
    hook = bind_round_budget_on_begin(used_box, limit_box)
    returned = hook()
    assert used_box[0] == 1
    assert returned == []
    hook()
    assert used_box[0] == 2


def test_bind_round_budget_stamps_coord_spend_on_same_channel():
    """Pass-local used/limit + tokens go out on note_coord_worker_busy, not a second bus."""
    from agentcore.runtime.graph.session.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )

    used_box = [0]
    limit_box = [56]
    tokens_box = [2_650_000]
    session = CoordinationSession(execution_id="e-round-stamp", total_workers=1)
    session._running_workers["w1"] = "研究员"
    set_active_coordination(session)
    try:
        hook = bind_round_budget_on_begin(
            used_box,
            limit_box,
            run_id="w1",
            tokens_spent_of=lambda: tokens_box[0],
        )
        hook()
        assert session.worker_budget_facts("w1") == ["已花 2650000"]
        tokens_box[0] = 2_700_000
        hook()
        assert session.worker_budget_facts("w1") == ["已花 2700000"]
        session.clear_worker_busy("w1")
        assert session.worker_budget_facts("w1") == ["已花 2700000"]
    finally:
        clear_active_coordination("e-round-stamp")


async def test_main_pass_honors_explicit_max_rounds_without_extra_investigation():
    """Explicit max_rounds stamps the pass; ceiling does not reopen a second investigation."""
    plan, _ = build_run_plan(
        [{"role": "W", "task": "write file", "max_rounds": 8}],
        id_prefix="t",
    )
    reg = ToolRegistry()
    reg.register(_FileWriteTool())
    reg.register(HandoffTool())
    rounds = [
        [
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id="w1",
                        function_name="write",
                        arguments_delta='{"file_path": "p.txt", "content": "hi"}',
                    )
                ]
            )
        ],
        [
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id="w2",
                        function_name="write",
                        arguments_delta='{"file_path": "q.txt", "content": "hi"}',
                    )
                ]
            )
        ],
        [
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id="h1",
                        function_name="handoff",
                        arguments_delta='{"summary": "done writing"}',
                    )
                ]
            )
        ],
    ]
    provider = _ScriptedRounds(rounds)
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="req",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    state = res["t_1"]
    assert state.phase is RunPhase.COMPLETED
    assert provider.calls == 3


async def test_react_stamps_coord_live_spend_for_ceo_brief():
    """Executor + engine busy channel expose pass-local used and run tokens mid-flight."""
    from agentcore.runtime.graph.session.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )

    plan, _ = build_run_plan(
        [
            {
                "role": "W",
                "task": "write file",
                "max_rounds": 8,
                "token_ceiling": 4_000_000,
            }
        ],
        id_prefix="t",
    )
    run_id = plan.nodes[0].run_id
    session = CoordinationSession(execution_id="e-live-spend", total_workers=1)
    session.live_plan = plan
    session._running_workers[run_id] = "W"
    seen: list[list[str]] = []

    class _Watch(_ScriptedRounds):
        async def stream(self, request):  # noqa: ANN001
            seen.append(list(session.worker_budget_facts(run_id)))
            async for chunk in super().stream(request):
                yield chunk

    usage0 = TokenUsage(input_tokens=1000, cache_miss_tokens=1000, output_tokens=400)
    rounds = [
        [
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id="w1",
                        function_name="write",
                        arguments_delta='{"file_path": "p.txt", "content": "hi"}',
                    )
                ]
            ),
            LLMChunk(usage=usage0),
        ],
        [
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id="h1",
                        function_name="handoff",
                        arguments_delta='{"summary": "done writing"}',
                    )
                ]
            )
        ],
    ]
    provider = _Watch(rounds)
    reg = ToolRegistry()
    reg.register(_FileWriteTool())
    reg.register(HandoffTool())
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="req",
        execution_id="e-live-spend",
        approval_gate=None,
    )
    set_active_coordination(session)
    try:
        res = await WaveScheduler().run(plan, executor)
        state = res[run_id]
        assert state.phase is RunPhase.COMPLETED
        assert len(seen) >= 2
        assert all("已用" not in bit for row in seen for bit in row)
        assert any(any(bit.startswith("已花 1400") for bit in row) for row in seen)
    finally:
        clear_active_coordination("e-live-spend")
