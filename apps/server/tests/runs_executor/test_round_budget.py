"""Pass-local round budget: dedicated light_repair cap; no product round fuse."""

from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, TokenUsage, ToolCallDelta
from agentcore.runtime.events import EventSink
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.executor import build_agent_executor
from agentcore.runtime.runs.executor.retry import (
    ROUND_BUDGET_AWARENESS_PREFIX,
    _pass_max_rounds,
    bind_round_budget_on_begin,
    drop_round_budget_awareness,
    format_round_budget_awareness,
    sync_round_budget_awareness,
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


def test_round_budget_awareness_is_cap_only():
    text = format_round_budget_awareness(limit=56)
    assert text.startswith(ROUND_BUDGET_AWARENESS_PREFIX)
    assert "本段上限 56 轮" in text
    assert "已用" not in text
    assert "剩余" not in text
    # No completion / quality / write-the-report steer.
    assert "报告" not in text
    assert "handoff" not in text
    assert "请" not in text


def test_sync_round_budget_awareness_replaces_stale_copy():
    messages = [
        LLMMessage(role="system", content="sys"),
        LLMMessage(role="user", content="task"),
    ]
    first = sync_round_budget_awareness(messages, limit=8)
    assert first is not None
    assert sum(1 for m in messages if m.content.startswith(ROUND_BUDGET_AWARENESS_PREFIX)) == 1
    second = sync_round_budget_awareness(messages, limit=4)
    assert "本段上限 4 轮" in (second or "")
    assert "本段上限 8 轮" not in (second or "")
    facts = [m for m in messages if _is_fact(m)]
    assert len(facts) == 1
    assert facts[0].content == second
    messages.append(LLMMessage(role="user", content="请补全缺失章节：结论"))
    parked = sync_round_budget_awareness(
        messages, limit=4, before_last_user=True
    )
    assert parked is not None
    assert messages[-1].content == "请补全缺失章节：结论"
    assert _is_fact(messages[-2])
    assert "本段上限 4 轮" in (messages[-2].content or "")
    assert drop_round_budget_awareness(messages) is True
    assert not any(_is_fact(m) for m in messages)
    assert messages[-1].content == "请补全缺失章节：结论"
    assert sync_round_budget_awareness(messages, limit=0) is None


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
    from agentcore.runtime.coordination.session import (
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


def _is_fact(msg: LLMMessage) -> bool:
    return (
        msg.role == "user"
        and isinstance(msg.content, str)
        and msg.content.startswith(ROUND_BUDGET_AWARENESS_PREFIX)
    )


def _fact_texts(state) -> list[str]:  # noqa: ANN001
    return [
        m.content
        for m in (state.transcript or [])
        if m.role == "user"
        and isinstance(m.content, str)
        and m.content.startswith(ROUND_BUDGET_AWARENESS_PREFIX)
    ]


async def test_main_pass_does_not_inject_round_budget_awareness():
    """Main produce pass must not tick remaining-rounds into the worker window."""
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
                        function_name="file_write",
                        arguments_delta='{"path": "p.txt", "content": "hi"}',
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
                        function_name="file_write",
                        arguments_delta='{"path": "q.txt", "content": "hi"}',
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
    assert _fact_texts(state) == []


async def test_react_stamps_coord_live_spend_for_ceo_brief():
    """Executor + engine busy channel expose pass-local used and run tokens mid-flight."""
    from agentcore.runtime.coordination.session import (
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
                        function_name="file_write",
                        arguments_delta='{"path": "p.txt", "content": "hi"}',
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


async def test_light_repair_does_not_announce_round_cap_after_exhaustion():
    """Main pass hits max_rounds=1; light_repair still runs, without 本段上限 inject."""
    plan, _ = build_run_plan(
        [
            {
                "role": "W",
                "task": "写报告",
                "max_rounds": 1,
                "deliverable": {"required_sections": ["结论"]},
            }
        ],
        id_prefix="t",
    )
    provider = _ContentProviderWithRequests(
        ["草稿里没有那个章节", "# 结论\n补上了必备章节"]
    )
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=ToolRegistry(),
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
    assert provider.calls == 2
    repair_users = [
        content
        for req in provider.requests[1:]
        for _role, content in req
        if content.startswith(ROUND_BUDGET_AWARENESS_PREFIX)
    ]
    assert repair_users == []


async def test_light_repair_runs_two_rounds_after_main_exhaustion():
    """Main max_rounds=1 leftover would stop after 1 repair call; dedicated cap runs 2+."""
    plan, _ = build_run_plan(
        [
            {
                "role": "W",
                "task": "写报告",
                "max_rounds": 1,
                "deliverable": {"required_sections": ["结论"]},
            }
        ],
        id_prefix="t",
    )
    provider = _ScriptedRoundsWithRequests(
        [
            [LLMChunk(delta_content="草稿里没有那个章节")],
            [
                LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0,
                            id="w1",
                            function_name="file_write",
                            arguments_delta='{"path": "notes.txt", "content": "scratch"}',
                        )
                    ]
                )
            ],
            [LLMChunk(delta_content="# 结论\n补上了必备章节")],
        ]
    )
    reg = ToolRegistry()
    reg.register(_FileWriteTool())
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
    repair_facts = [
        content
        for req in provider.requests[1:]
        for _role, content in req
        if content.startswith(ROUND_BUDGET_AWARENESS_PREFIX)
    ]
    assert repair_facts == []


async def test_contract_retry_skipped_after_round_ceiling():
    """Hitting an explicit max_rounds stamp skips a full investigation retry."""
    plan, _ = build_run_plan(
        [
            {
                "role": "W",
                "task": "emit json",
                "max_rounds": 4,
                "deliverable": {"output_format": "json"},
            }
        ],
        id_prefix="t",
    )
    provider = _AlwaysWriteProvider()
    reg = ToolRegistry()
    reg.register(_FileWriteTool())
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
    # Produce rounds: ceiling salvage may add a model call after the cap, but
    # leftover under total=5 is not spent on a second investigation.
    assert state.rounds == 4
    assert state.phase is RunPhase.COMPLETED


class _AlwaysWriteProvider:
    """Burn ReAct rounds with file_write so each pass actually hits max_rounds."""

    base_url = "http://test.invalid/v1"

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001
        self.calls += 1
        n = self.calls
        yield LLMChunk(
            delta_tool_calls=[
                ToolCallDelta(
                    index=0,
                    id=f"w{n}",
                    function_name="file_write",
                    arguments_delta=f'{{"path": "s{n}.txt", "content": "x"}}',
                )
            ]
        )


class _ScriptedRoundsWithRequests:
    """Scripted ReAct rounds that record every request's messages."""

    base_url = "http://test.invalid/v1"

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0
        self.requests: list[list[tuple[str, str]]] = []

    async def stream(self, request):  # noqa: ANN001
        self.requests.append([(m.role, m.content or "") for m in request.messages])
        chunks = (
            self._rounds[self.calls]
            if self.calls < len(self._rounds)
            else [LLMChunk(delta_content="done")]
        )
        self.calls += 1
        for chunk in chunks:
            yield chunk


class _ContentProviderWithRequests:
    """Scripted content provider that records every request's messages."""

    base_url = "http://test.invalid/v1"

    def __init__(self, contents: list[str]) -> None:
        self._contents = contents
        self.calls = 0
        self.requests: list[list[tuple[str, str]]] = []

    async def stream(self, request):  # noqa: ANN001
        self.requests.append([(m.role, m.content or "") for m in request.messages])
        text = self._contents[self.calls] if self.calls < len(self._contents) else "done"
        self.calls += 1
        yield LLMChunk(delta_content=text)
