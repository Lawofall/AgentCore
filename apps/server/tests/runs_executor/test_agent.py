"""End-to-end wiring test: build_run_plan → build_agent_executor → WaveScheduler.

Drives the real ``engine.react_loop`` with a scripted fake provider (no network)
to prove the executor builds correct worker messages, folds results into
RunState, injects an upstream product into a downstream node's prompt, and emits
the ``run_*`` graph events.
"""

from agentcore.llm.provider.protocol import LLMChunk
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.facts import FactKind, TurnFactLog, current_fact_log
from agentcore.runtime.journal import completed_from_journal
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.executor import build_agent_executor
from agentcore.runtime.runs.types import RunPhase
from agentcore.runtime.runs.wave import WaveScheduler
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.runs_executor.conftest import (
    _WS_ROOT,
    _ContentProvider,
    _ctx,
    _executor,
    _FileWriteTool,
    _flash_profiles,
    _gate,
    _GrantableTool,
    _MeteredRoundThenBoom,
    _OfferRecorder,
    _ResearchTool,
    _ToolCallThenContent,
    _UsageProvider,
)


async def test_parallel_workers_complete_with_usage():
    plan, errs = build_run_plan(
        [{"role": "A", "task": "做A"}, {"role": "B", "task": "做B"}], id_prefix="t"
    )
    assert errs == []
    provider = _ContentProvider(["AOUT", "BOUT"])
    res = await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    assert {s.phase for s in res.values()} == {RunPhase.COMPLETED}
    assert {s.content for s in res.values()} == {"AOUT", "BOUT"}
    assert all("input" in s.usage for s in res.values())


async def test_worker_usage_split_and_cost_priced():
    # Worker strong tier → DeepSeek V4 Flash (pinned via profile_set; platform default may differ).
    # Flash has curated CNY card (中文官价 ¥0.02 / ¥1 / ¥2) → nano-CNY ledger.
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    res = await WaveScheduler().run(
        plan,
        _executor(plan, _UsageProvider(), EventSink(), profile_set=_flash_profiles()),
    )
    state = res["t_1"]
    assert state.phase is RunPhase.COMPLETED
    # The cache split survives into RunState.usage (not collapsed to one input).
    assert state.usage["cache_hit"] == 1_000_000
    assert state.usage["cache_miss"] == 1_000_000
    # Cost is computed once, in nano-CNY, on the state (1M tokens × ¥/1M × 1000).
    assert state.cost["cached"] == 20_000_000  # ¥0.02
    assert state.cost["output"] == 2_000_000_000  # ¥2
    assert state.cost["total"] == 20_000_000 + 1_000_000_000 + 2_000_000_000
    assert state.cost["currency"] == "CNY"
    assert state.cost["pricing_source"] == "curated"


async def test_dag_injects_upstream_product_downstream():
    from tests.delegate.conftest import _upstream_body

    tasks = [
        {"id": "s1", "role": "研究员", "task": "调研"},
        {"id": "s2", "role": "写手", "task": "撰写", "depends_on": ["s1"]},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs == []
    upstream = _upstream_body("UPSTREAM-FACT")
    final = _upstream_body("FINAL")
    provider = _ContentProvider([upstream, final])
    res = await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    assert res["t_s1"].content == upstream
    assert res["t_s2"].content == final
    downstream_user = provider.user_messages[1]
    assert "UPSTREAM-FACT" in downstream_user
    assert "研究员" in downstream_user
    assert "原始请求" in downstream_user


async def test_worker_prompt_carries_role_and_task():
    plan, _ = build_run_plan([{"role": "分析师", "task": "拆解需求"}], id_prefix="t")
    provider = _ContentProvider(["X"])
    await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    # The single request's user message carries the original request + the task.
    user = provider.user_messages[0]
    assert "原始请求" in user
    assert "拆解需求" in user


async def test_worker_identity_states_output_is_user_visible():
    """Worker identity still tells it prose is self-contained user-facing copy
    (drillable in the UI) — P2, to motivate user-ready quality rather than
    writing only for the CEO. The old「直接展示给用户」line moved into the
    compressed form block as「可独立阅读」/「自包含」."""
    plan, _ = build_run_plan([{"role": "分析师", "task": "拆解需求"}], id_prefix="t")
    provider = _ContentProvider(["X"])
    await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    system = provider.system_messages[0]
    assert "可独立阅读" in system
    assert "自包含" in system


async def test_run_lifecycle_events_emitted():
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    sink = EventSink()
    provider = _ContentProvider(["X"])
    await WaveScheduler().run(plan, _executor(plan, provider, sink))
    sink.close()
    types = [e.type async for e in sink]
    assert EventType.RUN_STARTED in types
    assert EventType.RUN_OUTPUT_DELTA in types
    assert EventType.RUN_COMPLETED in types


async def test_worker_reasoning_streamed_as_run_reasoning_delta():
    """A thinking worker's reasoning is streamed run-scoped (run_reasoning_delta),
    not discarded — so the team UI can show 思考全文 per run. The thinking stays on
    its own channel and never leaks into run_output_delta."""
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    sink = EventSink()

    class _ReasoningProvider:
        async def stream(self, request):
            yield LLMChunk(delta_reasoning="先拆解")
            yield LLMChunk(delta_reasoning="再对比")
            yield LLMChunk(delta_content="结论")

    await WaveScheduler().run(plan, _executor(plan, _ReasoningProvider(), sink))
    sink.close()
    events = [e async for e in sink]
    started = next(e for e in events if e.type == EventType.RUN_STARTED)
    agent_id = started.payload["agent_id"]

    reasoning = [e for e in events if e.type == EventType.RUN_REASONING_DELTA]
    assert [e.payload["delta"] for e in reasoning] == ["先拆解", "再对比"]
    assert all(e.payload["run_id"] == "t_1" for e in reasoning)
    assert all(e.payload["agent_id"] == agent_id for e in reasoning)
    # Thinking must not bleed into the output channel.
    output = [e for e in events if e.type == EventType.RUN_OUTPUT_DELTA]
    assert [e.payload["delta"] for e in output] == ["结论"]


async def test_completed_worker_run_final_fact_carries_full_output_and_reasoning():
    # 执行级事件溯源 (deltas 退场): a worker's FULL output + thinking are captured onto its
    # terminal RunState → its run_final_fact (``message_final``), so the reload rebuilds
    # the node's 输出/思考 from the fact (synthesizing the delta block) instead of the
    # no-longer-journaled per-token deltas. Drives the REAL executor under a bound fact
    # log and asserts the recorded fact carries both, full-text — closing the gap where
    # the worker's reasoning was previously discarded (only streamed, never a fact).
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")

    class _ReasoningProvider:
        async def stream(self, request):
            yield LLMChunk(delta_reasoning="先拆解")
            yield LLMChunk(delta_reasoning="再对比")
            yield LLMChunk(delta_content="结论")

    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        res = await WaveScheduler().run(plan, _executor(plan, _ReasoningProvider(), EventSink()))
    finally:
        current_fact_log.reset(token)

    # The terminal RunState now carries the worker's thinking (previously left empty).
    assert res["t_1"].phase is RunPhase.COMPLETED
    assert res["t_1"].content == "结论"
    assert res["t_1"].reasoning == "先拆解再对比"

    finals = [
        e
        for e in log.entries()
        if e["kind"] == FactKind.MESSAGE_FINAL.value and e["payload"].get("run_id") == "t_1"
    ]
    assert len(finals) == 1
    assert finals[0]["payload"]["content"] == "结论"
    assert finals[0]["payload"]["reasoning"] == "先拆解再对比"


async def test_run_started_carries_parent_and_kind_slots():
    """run_started pre-wires parent_run_id / kind (阶段2 声明位): a 阶段1 flat
    worker is a top-level ``agent`` — parent_run_id is None, kind == 'agent' —
    so nested delegation + synthesis need no event change."""
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    sink = EventSink()
    await WaveScheduler().run(plan, _executor(plan, _ContentProvider(["X"]), sink))
    sink.close()
    started = [e async for e in sink if e.type == EventType.RUN_STARTED]
    assert len(started) == 1
    payload = started[0].payload
    assert payload["parent_run_id"] is None
    assert payload["kind"] == "agent"
    assert payload["run_id"] == "t_1"


async def test_executor_failure_emits_run_failed_and_state():
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    sink = EventSink()

    class _Boom:
        async def stream(self, request):
            raise RuntimeError("provider down")
            yield  # pragma: no cover - makes this an async generator

    executor = build_agent_executor(
        plan=plan,
        llm=_Boom(),
        tools=ToolRegistry(),
        sink=sink,
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    sink.close()
    assert res["t_1"].phase is RunPhase.FAILED
    assert "provider down" in res["t_1"].error
    events = [e async for e in sink]
    types = [e.type for e in events]
    assert types.count(EventType.RUN_STARTED) == 1
    assert types.count(EventType.RUN_FAILED) == 1
    failed = next(e for e in events if e.type == EventType.RUN_FAILED)
    assert failed.payload.get("retryable") is False


async def test_deterministic_llm_error_marks_state_not_retryable():
    # 确定性失败区分 (BL-6): a worker whose LLM raises a non-retryable upstream error
    # (LLMError.retryable=False — prompt 超长 / 400 / 鉴权 / 余额) surfaces a FAILED state
    # flagged error_retryable=False, so the scheduler can skip a futile infra retry.
    from agentcore.core.errors import LLMError

    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")

    class _PromptTooLong:
        async def stream(self, request):  # noqa: ANN001
            raise LLMError("上下文超长（400）")
            yield  # pragma: no cover - makes this an async generator

    executor = build_agent_executor(
        plan=plan,
        llm=_PromptTooLong(),
        tools=ToolRegistry(),
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    assert res["t_1"].phase is RunPhase.FAILED
    assert res["t_1"].error_retryable is False


async def test_closed_llm_client_marks_state_not_retryable():
    """Turn-teardown closed httpx client must not burn WaveScheduler retries."""
    from agentcore.core.errors import LLMClientClosedError

    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")

    class _ClosedClient:
        async def stream(self, request):  # noqa: ANN001
            raise RuntimeError("Cannot send a request, as the client has been closed.")
            yield  # pragma: no cover

    executor = build_agent_executor(
        plan=plan,
        llm=_ClosedClient(),
        tools=ToolRegistry(),
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    assert res["t_1"].phase is RunPhase.FAILED
    assert res["t_1"].error_retryable is False
    assert "client has been closed" in (res["t_1"].error or "").lower()
    # Typed path also covered:
    class _TypedClosed:
        async def stream(self, request):  # noqa: ANN001
            raise LLMClientClosedError()
            yield  # pragma: no cover

    plan2, _ = build_run_plan([{"role": "B", "task": "做B"}], id_prefix="u")
    executor2 = build_agent_executor(
        plan=plan2,
        llm=_TypedClosed(),
        tools=ToolRegistry(),
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e2",
        approval_gate=None,
    )
    res2 = await WaveScheduler().run(plan2, executor2)
    assert res2["u_1"].error_retryable is False


async def test_unknown_crash_is_terminal():
    # ``llm_failure_class`` maps an unknown crash to terminal — not leaf
    # ``exc.retryable`` (which a bare RuntimeError does not carry).
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")

    class _Boom:
        async def stream(self, request):  # noqa: ANN001
            raise RuntimeError("provider down")
            yield  # pragma: no cover

    executor = build_agent_executor(
        plan=plan,
        llm=_Boom(),
        tools=ToolRegistry(),
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    assert res["t_1"].phase is RunPhase.FAILED
    assert res["t_1"].error_retryable is False


async def test_headerless_rate_limit_emits_one_run_failed_no_rerun():
    """无 attested 头的 429：一帧 run_failed，不整节点重跑。"""
    from agentcore.core.error_codes import ErrorCode
    from agentcore.core.errors import RETRY_AFTER_FROM_BACKOFF, upstream_rate_limit_error

    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    sink = EventSink()

    class _HeaderlessLimit:
        def __init__(self) -> None:
            self.calls = 0

        async def stream(self, request):  # noqa: ANN001
            self.calls += 1
            raise upstream_rate_limit_error(
                2.0, credential_source="user", retry_after_source=RETRY_AFTER_FROM_BACKOFF
            )
            yield  # pragma: no cover

    provider = _HeaderlessLimit()
    res = await WaveScheduler().run(plan, _executor(plan, provider, sink))
    sink.close()
    events = [e async for e in sink]
    failed = [e for e in events if e.type == EventType.RUN_FAILED]
    assert res["t_1"].phase is RunPhase.FAILED
    assert res["t_1"].error_retryable is True
    assert res["t_1"].error_code == ErrorCode.LLM_RATE_LIMIT
    assert provider.calls == 1
    assert [e.type for e in events].count(EventType.RUN_STARTED) == 1
    assert len(failed) == 1
    assert failed[0].payload["error_code"] == ErrorCode.LLM_RATE_LIMIT
    assert failed[0].payload["retryable"] is True


async def test_transient_exhausted_emits_one_run_failed_with_signal():
    """瞬时预算用尽：全链路只有一帧 run_failed，并带 error_code / retryable。

    未 attested 的退避秒数不上 ``retry_after``（该字段是上游 Retry-After）。
    """
    from agentcore.core.error_codes import ErrorCode
    from agentcore.core.errors import LLMRateLimitError

    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    sink = EventSink()

    class _AlwaysLimit:
        def __init__(self) -> None:
            self.calls = 0

        async def stream(self, request):  # noqa: ANN001
            self.calls += 1
            raise LLMRateLimitError(retry_after=0.0)
            yield  # pragma: no cover

    provider = _AlwaysLimit()
    res = await WaveScheduler().run(plan, _executor(plan, provider, sink))
    sink.close()
    events = [e async for e in sink]
    failed = [e for e in events if e.type == EventType.RUN_FAILED]
    assert res["t_1"].phase is RunPhase.FAILED
    assert res["t_1"].error_retryable is True
    assert res["t_1"].error_code == ErrorCode.LLM_RATE_LIMIT
    assert provider.calls == 1
    assert [e.type for e in events].count(EventType.RUN_STARTED) == 1
    assert len(failed) == 1
    payload = failed[0].payload
    assert payload["error_code"] == ErrorCode.LLM_RATE_LIMIT
    assert payload["retryable"] is True
    assert "retry_after" not in payload


async def test_leaf_exhausted_rate_limit_stays_transient_on_wire():
    """叶层用尽就地重试后限流仍是瞬时：不整跑、run_failed.retryable=True。"""
    from agentcore.core.error_codes import ErrorCode
    from agentcore.core.errors import LLMRateLimitError, mark_llm_leaf_exhausted

    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    sink = EventSink()

    class _LeafExhausted:
        def __init__(self) -> None:
            self.calls = 0

        async def stream(self, request):  # noqa: ANN001
            self.calls += 1
            exc = LLMRateLimitError(retry_after=4.0)
            mark_llm_leaf_exhausted(exc)
            raise exc
            yield  # pragma: no cover

    provider = _LeafExhausted()
    res = await WaveScheduler().run(plan, _executor(plan, provider, sink))
    sink.close()
    events = [e async for e in sink]
    failed = [e for e in events if e.type == EventType.RUN_FAILED]
    assert provider.calls == 1
    assert res["t_1"].phase is RunPhase.FAILED
    assert res["t_1"].error_retryable is True
    assert res["t_1"].error_code == ErrorCode.LLM_RATE_LIMIT
    assert [e.type for e in events].count(EventType.RUN_STARTED) == 1
    assert len(failed) == 1
    assert failed[0].payload["retryable"] is True
    assert failed[0].payload["error_code"] == ErrorCode.LLM_RATE_LIMIT
    assert "retry_after" not in failed[0].payload


async def test_worker_hard_failure_bills_completed_rounds():
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    reg = ToolRegistry()
    reg.register(_GrantableTool("noop"))  # un-gated here → the metered round runs
    executor = build_agent_executor(
        plan=plan,
        llm=_MeteredRoundThenBoom(),
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    state = res["t_1"]
    assert state.phase is RunPhase.FAILED
    assert "provider down" in state.error
    # The round that completed before the crash is billed, not silently dropped.
    assert state.usage["cache_miss"] == 1000
    assert state.usage["output"] == 400
    assert state.cost["total"] > 0
    # Exception path hangs the in-flight transcript (合同硬失败同契约 → 可登记).
    assert state.transcript
    assert any(m.role in ("assistant", "tool") for m in state.transcript)


async def test_executor_infra_retry_consumes_seeded_transcript():
    """A transient FAILED+transcript in completed[self] → executor 热续, not cold open."""
    from agentcore.llm.provider.protocol import LLMMessage
    from agentcore.runtime.runs.types import RunState

    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    prior = [
        LLMMessage(role="system", content="SYS"),
        LLMMessage(role="user", content="做A"),
        LLMMessage(role="assistant", content="半成品草稿"),
    ]
    provider = _ContentProvider(["续写完成"])
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=ToolRegistry(),
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    seeded = {
        "t_1": RunState(
            phase=RunPhase.FAILED,
            error="upstream disconnect",
            transcript=prior,
            content="半成品草稿",
            error_retryable=True,
        )
    }
    state = await executor(plan.nodes[0], seeded)
    assert state.phase is RunPhase.COMPLETED
    assert state.content == "续写完成"
    assert provider.calls == 1
    roles = [r for r, _ in provider.requests[0]]
    # Prior assistant draft is still in the window (hot), and a 续干 user was appended.
    assert "assistant" in roles
    assert any(c == "半成品草稿" for r, c in provider.requests[0] if r == "assistant")
    assert any("续干指令" in c for r, c in provider.requests[0] if r == "user")


async def test_failed_worker_run_final_fact_reseeds_from_journal():
    # 执行级事件溯源 Phase 2 ⑥ golden (FAILED arm): a FAILED worker journals its terminal
    # RunState at the SAME `execute` choke point as a COMPLETED one (run_final_fact covers
    # every phase), so `completed_from_journal` re-seeds it on resume — phase + error +
    # the billed pre-crash usage — not only COMPLETED nodes. Drives the REAL executor under
    # a bound fact log so the recording site + the projector are exercised together.
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    reg = ToolRegistry()
    reg.register(_GrantableTool("noop"))  # un-gated → the metered round runs before the boom
    executor = build_agent_executor(
        plan=plan,
        llm=_MeteredRoundThenBoom(),
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        res = await WaveScheduler().run(plan, executor)
    finally:
        current_fact_log.reset(token)
    assert res["t_1"].phase is RunPhase.FAILED

    seed = completed_from_journal(log.entries())
    assert set(seed) == {"t_1"}
    assert seed["t_1"].phase is RunPhase.FAILED
    assert "provider down" in seed["t_1"].error
    # The billed pre-crash round survives the journal round-trip (a resume bills it once).
    assert seed["t_1"].usage["cache_miss"] == 1000


async def test_worker_failure_before_any_usage_has_no_ledger_row():
    # A run that dies before metering any tokens carries empty usage/cost, so the
    # per-run accumulator's `if state.usage` guard skips it — no spurious zero row.
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")

    class _BoomFirst:
        async def stream(self, request):  # noqa: ANN001
            raise RuntimeError("down")
            yield  # pragma: no cover

    executor = build_agent_executor(
        plan=plan,
        llm=_BoomFirst(),
        tools=ToolRegistry(),
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    state = res["t_1"]
    assert state.phase is RunPhase.FAILED
    assert not state.usage  # empty → accumulator skips → no ledger row
    assert not state.cost


async def test_contract_retired_min_length_ignored_no_soft_tip():
    # S3：已删 min_length 不再产生 soft tip / retry；短文照常 COMPLETED。
    plan, _ = build_run_plan(
        [{"role": "A", "task": "做A", "deliverable": {"min_length": 8}}], id_prefix="t"
    )
    provider = _ContentProvider(["短"])
    res = await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    assert provider.calls == 1
    assert res["t_1"].phase is RunPhase.COMPLETED
    assert res["t_1"].content == "短"
    assert not any("少于" in w for w in (res["t_1"].warnings or []))


async def test_contract_retired_must_contain_ignored_no_soft_tip():
    # S3：已删 must_contain 不再 soft tip / 续写。
    plan, _ = build_run_plan(
        [{"role": "A", "task": "做A", "deliverable": {"must_contain": ["风险"]}}], id_prefix="t"
    )
    provider = _ContentProvider(["没有那个词"])
    res = await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    assert provider.calls == 1
    assert res["t_1"].phase is RunPhase.COMPLETED
    assert not any("风险" in w for w in (res["t_1"].warnings or []))


async def test_contract_section_retry_continues_on_same_transcript():
    # required_sections 仍硬拦：续写同 transcript，worker 可见旧稿。
    plan, _ = build_run_plan(
        [{"role": "A", "task": "做A", "deliverable": {"required_sections": ["结论"]}}],
        id_prefix="t",
    )
    provider = _ContentProvider(["没有章节", "# 结论\n已补上"])
    await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    assert provider.calls == 2
    second = provider.requests[1]
    assert any(role == "assistant" and content == "没有章节" for role, content in second)
    last_role, last_content = second[-1]
    assert last_role == "user"
    assert ("修正" in last_content) or ("补全" in last_content)
    assert "结论" in last_content


async def test_completed_run_captures_full_transcript():
    # T1/T2: a finished worker's full transcript is captured on RunState so the run
    # is recoverable (留人). It ends with the worker's final answer (react_loop omits
    # that append; the executor adds it) — the starting point for a 续写.
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    provider = _ContentProvider(["最终产出"])
    res = await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    transcript = res["t_1"].transcript
    assert transcript  # captured, not discarded
    assert transcript[0].role == "system"
    assert transcript[1].role == "user"
    assert "做A" in (transcript[1].content or "")
    # the final assistant answer is appended, so the transcript is replayable
    assert transcript[-1].role == "assistant"
    assert transcript[-1].content == "最终产出"


async def test_contract_requirements_stated_in_first_prompt():
    plan, _ = build_run_plan(
        [{"role": "A", "task": "做A", "deliverable": {"required_sections": ["结论"]}}], id_prefix="t"
    )
    provider = _ContentProvider(["# 结论\n好的"])
    await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    assert "交付物规格" in provider.user_messages[0]
    assert "结论" in provider.user_messages[0]


async def test_worker_system_prompt_grants_structure_ownership():
    # 认知分工的接收端（L3，worker 侧所有权）: the CEO brake (test_prompt.py) tells the
    # CEO not to design the deliverable's structure; this is the counterpart that
    # reaches the WORKER — its system prompt must empower it to OWN the professional
    # structure and treat any skeleton leaked into the task as a starting suggestion
    # (checked against the 原始用户请求, also in its prompt) rather than a fill-in
    # template. Pins the fix so a refactor of the shared deliverable policy can't
    # silently revert the worker to a「填字员」. Verified end-to-end (assembled system
    # message), not just the constant, so the block must actually land in the prompt.
    plan, _ = build_run_plan([{"role": "写作者", "task": "写一篇文章"}], id_prefix="t")
    provider = _ContentProvider(["正文"])
    await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    sys = provider.system_messages[0]
    assert "专业结构" in sys
    assert "填字" in sys
    # 对称解锁：task 关注点是起点线索不是答题边界（审查类「指路不代答」的 worker 侧）。
    assert "起点线索" in sys
    assert "答题边界" in sys


async def test_contract_retired_min_length_even_strict_ignored():
    # S3：已删 min_length；strict 也不因字数 FAILED / soft tip。
    plan, _ = build_run_plan(
        [{"role": "A", "task": "做A", "deliverable": {"min_length": 50, "strict": True}}],
        id_prefix="t",
    )
    assert plan.nodes[0].policy.on_failure == "retry"
    sink = EventSink()
    provider = _ContentProvider(["短"])
    res = await WaveScheduler().run(plan, _executor(plan, provider, sink))
    sink.close()
    assert provider.calls == 1
    assert res["t_1"].phase is RunPhase.COMPLETED
    assert not any("少于" in w for w in (res["t_1"].warnings or []))


async def test_contract_empty_hard_fail_not_wave_retried():
    # Empty product is always a hard fail; after in-executor contract retries the
    # wave must not re-dispatch (failures=['产出为空'] burned tokens twice already).
    # Use whitespace (not "") so the fake provider still yields a stop chunk that
    # ends the react round in one call — "" can leave the loop taking an extra round.
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    assert plan.nodes[0].policy.on_failure == "retry"
    provider = _ContentProvider(["   ", "\n\t", "第三次冷启不应发生"])
    res = await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    assert provider.calls == 2
    assert res["t_1"].phase is RunPhase.FAILED
    assert res["t_1"].error_retryable is False
    assert "空" in res["t_1"].error


async def test_contract_retired_min_length_dict_ignored_completes():
    # S3：派单 JSON 仍带已删 min_length → 忽略，短文首轮 COMPLETED、无字数 soft tip。
    plan, _ = build_run_plan(
        [{"role": "A", "task": "做A", "deliverable": {"min_length": 50}}], id_prefix="t"
    )
    provider = _ContentProvider(["短"])
    res = await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    assert provider.calls == 1
    assert res["t_1"].phase is RunPhase.COMPLETED
    assert res["t_1"].content == "短"
    assert not any("少于" in w for w in (res["t_1"].warnings or []))


async def test_no_contract_passes_first_try_without_extra_call():
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    provider = _ContentProvider(["一个正常的非空产出"])
    res = await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    assert provider.calls == 1  # no needless retry when the baseline is met
    assert res["t_1"].phase is RunPhase.COMPLETED


async def test_files_form_soft_completes_without_forcing_write():
    """甲⁺：form=files 粘贴正文不落盘 → soft-complete；不再 write_pass 逼写。"""
    plan, _ = build_run_plan(
        [{"role": "前端", "task": "建页面", "deliverable": {"form": "files"}}],
        id_prefix="t",
    )
    reg = ToolRegistry()
    fw = _FileWriteTool()
    reg.register(fw)
    provider = _ContentProvider(["<html>整份贴在聊天里</html>"])
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    state = res["t_1"]
    assert state.phase is RunPhase.COMPLETED
    assert fw.calls == 0
    assert state.files_touched == []
    assert any("未把产物写入工作区" in w for w in (state.warnings or []))


async def test_files_form_soft_completes_without_write_pass():
    """甲⁺：form=files 零落盘 soft-complete，不触发 write_pass，不 FAILED。"""
    plan, _ = build_run_plan(
        [{"role": "前端", "task": "建页面", "deliverable": {"form": "files"}}],
        id_prefix="t",
    )
    provider = _ContentProvider(["只有文字一", "只有文字二"])
    res = await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    assert provider.calls == 1  # 无 write_pass 第二轮
    state = res["t_1"]
    assert state.phase is RunPhase.COMPLETED
    assert state.files_touched == []
    assert any("未把产物写入工作区" in w for w in (state.warnings or []))


async def test_files_form_strict_soft_completes_when_never_written():
    """甲⁺：即使 strict，form=files 零落盘 alone 也不 fail（已降 soft warning，verdict.ok）。"""
    plan, _ = build_run_plan(
        [
            {
                "role": "前端",
                "task": "建页面",
                "deliverable": {"form": "files", "strict": True},
            }
        ],
        id_prefix="t",
    )
    sink = EventSink()
    provider = _ContentProvider(["只有文字一", "只有文字二"])
    res = await WaveScheduler().run(plan, _executor(plan, provider, sink))
    sink.close()
    state = res["t_1"]
    assert state.phase is RunPhase.COMPLETED
    assert any("未把产物写入工作区" in w for w in (state.warnings or []))
    types = [e.type async for e in sink]
    assert EventType.RUN_FAILED not in types


async def test_retired_requires_files_alone_ignored_no_zero_disk_soft():
    """S3：仅 legacy requires_files、无 form=files → 不产生零落盘 soft tip。"""
    plan, _ = build_run_plan(
        [{"role": "前端", "task": "建页面", "deliverable": {"requires_files": True}}],
        id_prefix="t",
    )
    provider = _ContentProvider(["只有文字"])
    res = await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    state = res["t_1"]
    assert state.phase is RunPhase.COMPLETED
    assert not any("未把产物写入工作区" in w for w in (state.warnings or []))


async def test_worker_grantable_tool_gated_when_gate_denies():
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    tool = _GrantableTool()
    reg = ToolRegistry()
    reg.register(tool)
    provider = _ToolCallThenContent("code_execute", "{}", "done")
    # Local backend: workers share the turn gate for all GRANTABLE tools.
    # (Cloud workers only gate desktop-touch tools when the gate is MCP-shared.)
    local_ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(
            root=_WS_ROOT, sandbox=SubprocessSandbox(), location="local"
        ),
        user_id="u",
    )
    # A 0.01s gate that nobody answers auto-denies — the worker must NOT run the
    # tool on the user's machine, and adapts to a denial tool-message.
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=local_ctx,
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=_gate(0.01),
    )
    res = await WaveScheduler().run(plan, executor)
    assert res["t_1"].phase is RunPhase.COMPLETED
    assert tool.calls == 0  # denied → never executed


async def test_worker_grantable_tool_runs_without_gate():
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    tool = _GrantableTool()
    reg = ToolRegistry()
    reg.register(tool)
    provider = _ToolCallThenContent("code_execute", "{}", "done")
    # No gate (the cloud default): the worker runs the tool un-gated, as before.
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    assert res["t_1"].phase is RunPhase.COMPLETED
    assert tool.calls == 1  # un-gated → executed


async def test_worker_with_omitted_tools_is_offered_all_team_tools():
    # Regression for the root bug: a delegated task that omits ``tools`` must NOT be
    # stranded tool-less. builder._tools → None → react_loop offers the whole
    # registry with tool_choice=auto, so a file/exec worker can actually act.
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    assert plan.nodes[0].tools is None
    reg = ToolRegistry()
    reg.register(_GrantableTool("code_execute"))
    provider = _OfferRecorder()
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    assert res["t_1"].phase is RunPhase.COMPLETED
    assert provider.offered and "code_execute" in provider.offered[0]
    assert provider.choices[0] == "auto"


async def test_worker_with_explicit_tools_is_not_restricted():
    """真纯丙：入参 tools 填了也不收窄；执行层仍 offer 全 registry。"""
    plan, _ = build_run_plan(
        [{"role": "A", "task": "做A", "tools": ["code_execute"]}],
        id_prefix="t",
        valid_tools={"code_execute", "web_search"},
    )
    assert plan.nodes[0].tools is None
    reg = ToolRegistry()
    reg.register(_GrantableTool("code_execute"))
    reg.register(_GrantableTool("web_search"))
    provider = _OfferRecorder()
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    await WaveScheduler().run(plan, executor)
    offered = set(provider.offered[0])
    assert "code_execute" in offered
    assert "web_search" in offered


async def test_debater_path_offers_file_write_without_readonly_box():
    """真纯丙·H4：辩手路径不再靠系统只读箱；遗留 tools 声明仍被忽略，写盘可执行。"""
    legacy_readonly = ("web_search", "read_url", "file_read", "file_list", "grep")

    plan, errs = build_run_plan(
        [{"role": "正方", "task": "立论取证", "tools": list(legacy_readonly)}],
        id_prefix="debate_r1",
        valid_tools=set(legacy_readonly) | {"file_write", "escalate"},
    )
    assert errs == []
    assert plan.nodes[0].tools is None

    fw = _FileWriteTool()
    reg = ToolRegistry()
    for name in legacy_readonly:
        reg.register(_ResearchTool(name))
    reg.register(fw)
    reg.register(_GrantableTool("escalate"))
    provider = _ToolCallThenContent(
        "file_write",
        '{"path":"AgentCore/文档/research/证据笔记.md","content":"should-not-land"}',
        "DONE",
    )
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
        collaboration=False,
    )
    res = await WaveScheduler().run(plan, executor)
    state = next(iter(res.values()))
    assert state.phase is RunPhase.COMPLETED
    assert fw.calls == 1


async def test_collaboration_off_denies_note_tools_to_unrestricted_debater():
    # 真纯丙下辩手亦为 unrestricted；collaboration=False 仍从 registry 卸便签。
    plan, _ = build_run_plan(
        [{"role": "正方", "task": "立论", "tools": ["web_search"]}],
        id_prefix="t",
        valid_tools={"web_search"},
    )
    assert plan.nodes[0].tools is None
    reg = ToolRegistry()
    reg.register(_GrantableTool("web_search"))
    reg.register(_GrantableTool("post_note"))
    reg.register(_GrantableTool("read_notes"))
    reg.register(_GrantableTool("amend_note"))
    provider = _OfferRecorder()
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
        collaboration=False,
    )
    await WaveScheduler().run(plan, executor)
    offered = set(provider.offered[0])
    assert "web_search" in offered
    assert "post_note" not in offered
    assert "read_notes" not in offered
    assert "amend_note" not in offered


async def test_collaboration_off_denies_note_tools_to_unrestricted_worker():
    # The switch means "no collaboration", not "no collaboration only if least-privilege": even
    # an UNRESTRICTED worker (tools omitted → "offer all team tools") is not handed the 团队便签
    # tools when collaboration=False — they are stripped from the offered registry.
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    assert plan.nodes[0].tools is None
    reg = ToolRegistry()
    reg.register(_GrantableTool("code_execute"))
    reg.register(_GrantableTool("post_note"))
    provider = _OfferRecorder()
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
        collaboration=False,
    )
    await WaveScheduler().run(plan, executor)
    assert "post_note" not in provider.offered[0]
    assert "code_execute" in provider.offered[0]


async def test_collaboration_on_grants_note_tools_when_tools_declared():
    # 真纯丙：声明 tools 无效；协作批仍 offer 便签（全开面）。
    plan, _ = build_run_plan(
        [{"role": "A", "task": "做A", "tools": ["web_search"]}],
        id_prefix="t",
        valid_tools={"web_search"},
    )
    assert plan.nodes[0].tools is None
    reg = ToolRegistry()
    reg.register(_GrantableTool("web_search"))
    reg.register(_GrantableTool("post_note"))
    provider = _OfferRecorder()
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    await WaveScheduler().run(plan, executor)
    assert "post_note" in provider.offered[0]
    assert "web_search" in provider.offered[0]


async def test_worker_always_granted_handoff_even_if_tools_declared_without_it():
    """真纯丙：tools 声明无效；handoff/escalate 仍在全开面上。"""
    plan, _ = build_run_plan(
        [{"role": "A", "task": "做A", "tools": ["web_search"]}],
        id_prefix="t",
        valid_tools={"web_search"},
    )
    assert plan.nodes[0].tools is None
    reg = ToolRegistry()
    reg.register(_GrantableTool("web_search"))
    reg.register(_GrantableTool("handoff"))
    reg.register(_GrantableTool("escalate"))
    provider = _OfferRecorder()
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    await WaveScheduler().run(plan, executor)
    assert "handoff" in provider.offered[0]
    assert "escalate" in provider.offered[0]
    assert "web_search" in provider.offered[0]


async def test_worker_collects_web_citations_onto_runstate():
    cites = [{"url": "https://a.com", "title": "A", "snippet": "", "site": "a.com"}]
    plan, _ = build_run_plan([{"role": "研究员", "task": "调研"}], id_prefix="t")
    reg = ToolRegistry()
    reg.register(_ResearchTool(citations=cites))
    provider = _ToolCallThenContent("search", "{}", "FINAL")
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    state = res["t_1"]
    assert state.phase is RunPhase.COMPLETED
    # the worker's sources are aggregated onto RunState for the shared card.
    # Citation contract may stamp optional ``tier`` (M1 证据台账) — assert core fields.
    assert len(state.citations) == 1
    got = state.citations[0]
    assert got["url"] == "https://a.com"
    assert got["title"] == "A"
    assert got["snippet"] == ""
    assert got["site"] == "a.com"
    assert got.get("tier") in (None, "unknown", "official", "media", "weak")
    # the worker's own answer text is clean (un-numbered — annotation is CEO-only)
    assert state.content == "FINAL"


async def test_whole_turn_stop_emits_run_cancelled_reason_stop():
    """整轮 stop: cancel the wave → each in-flight worker emits run_cancelled(reason=stop)."""
    import asyncio
    import contextlib

    from agentcore.llm.provider.protocol import LLMChunk

    class _HangProvider:
        async def stream(self, request):  # noqa: ANN001
            yield LLMChunk(delta_content="半成品")
            await asyncio.sleep(10)
            yield LLMChunk(delta_content="…")

    plan, _ = build_run_plan(
        [
            {"role": "A", "task": "做A"},
            {"role": "B", "task": "做B"},
        ],
        id_prefix="t",
    )
    sink = EventSink()
    wave_task = asyncio.create_task(
        WaveScheduler().run(plan, _executor(plan, _HangProvider(), sink))
    )
    # Wait until both workers have started (and ideally streamed a delta).
    for _ in range(100):
        started = [
            e for e in sink._history if e.type is EventType.RUN_STARTED
        ]
        if len(started) >= 2:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    wave_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await wave_task

    cancelled = [
        e for e in sink._history if e.type is EventType.RUN_CANCELLED
    ]
    assert len(cancelled) == 2
    assert {e.payload.get("reason") for e in cancelled} == {"stop"}
    assert {e.payload.get("run_id") for e in cancelled} == {"t_1", "t_2"}
