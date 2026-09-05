"""检索预算 A1: structured defaults (CEO 不可配置), tool_exec charge / exhaust."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentcore.core.types import ToolCategory
from agentcore.llm.provider.protocol import (
    LLMChunk,
    LLMMessage,
    ToolCall,
    ToolCallDelta,
    ToolCallFunction,
)
from agentcore.runtime.engine.loop import react_loop
from agentcore.runtime.engine.tool_exec import execute_tools
from agentcore.runtime.events import EventSink
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.retrieval_budget import (
    BUDGET_EXHAUSTED_FEEDBACK,
    DEFAULT_RETRIEVAL_BUDGET,
    DEFAULT_RETRIEVAL_BUDGET_DEBATER_WITH_DOSSIER,
    RETRIEVAL_BUDGET_AWARENESS_PREFIX,
    RETRIEVAL_BUDGET_CRITICAL_REMAINING,
    RETRIEVAL_TOOL_NAMES,
    charges_retrieval_budget,
    default_retrieval_budget,
    drop_retrieval_budget_awareness,
    format_retrieval_budget_line,
    format_retrieval_budget_receipt,
    is_retrieval_budget_critical,
    rework_refill_slots,
    sync_retrieval_budget_awareness,
)
from agentcore.runtime.runs.types import Deliverable, RunSpec
from agentcore.tools.protocol import RetrievalBudgetState, ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_profile_params


def _spec(
    *,
    deps: list[str] | None = None,
    form: str | None = None,
    budget: int | None = None,
    tools: list[str] | None = None,
) -> RunSpec:
    deliverable = Deliverable(form=form) if form else None  # type: ignore[arg-type]
    return RunSpec(
        run_id="n1",
        task="t",
        role="r",
        depends_on=deps or [],
        deliverable=deliverable,
        retrieval_budget=budget,
        tools=tools,
    )


def test_structured_default_unified_for_all_workers():
    """全员统一默认 14（含 prose / root / light / files / 下游）。"""
    assert DEFAULT_RETRIEVAL_BUDGET == 14
    assert default_retrieval_budget(_spec()) == DEFAULT_RETRIEVAL_BUDGET
    assert (
        default_retrieval_budget(_spec(), complexity_hint="light")
        == DEFAULT_RETRIEVAL_BUDGET
    )
    assert default_retrieval_budget(_spec(form="files")) == DEFAULT_RETRIEVAL_BUDGET
    assert (
        default_retrieval_budget(_spec(deps=["u"], form="files"))
        == DEFAULT_RETRIEVAL_BUDGET
    )
    assert default_retrieval_budget(_spec(deps=["u"])) == DEFAULT_RETRIEVAL_BUDGET
    assert default_retrieval_budget(_spec(form="prose")) == DEFAULT_RETRIEVAL_BUDGET
    assert (
        default_retrieval_budget(_spec(deps=["up"], form="prose"))
        == DEFAULT_RETRIEVAL_BUDGET
    )


def test_debate_dossier_narrow_exception_constant():
    """有约定文档辩手残搜 2：窄硬例外，不是结构猜档。"""
    assert DEFAULT_RETRIEVAL_BUDGET_DEBATER_WITH_DOSSIER == 2
    assert DEFAULT_RETRIEVAL_BUDGET_DEBATER_WITH_DOSSIER < DEFAULT_RETRIEVAL_BUDGET


def test_retrieval_budget_critical_helpers():
    """临界谓词仍给埋点用；教案函数已撤。"""
    from agentcore.runtime.runs import retrieval_budget as rb_mod

    assert RETRIEVAL_BUDGET_CRITICAL_REMAINING == 2
    assert is_retrieval_budget_critical(2, limit=14) is True
    assert is_retrieval_budget_critical(1, limit=14) is True
    assert is_retrieval_budget_critical(3, limit=14) is False
    assert is_retrieval_budget_critical(0, limit=14) is False
    assert is_retrieval_budget_critical(1, limit=0) is False
    assert not hasattr(rb_mod, "format_retrieval_budget_critical_prompt")
    assert not hasattr(rb_mod, "format_retrieval_budget_awareness_prompt")


def test_build_plan_applies_unified_default_including_prose():
    """prose 与非 prose 均得统一默认；builder 不因 prose 剥离检索工具。"""
    valid = {"web_search", "web_fetch", "file_read", "handoff", "escalate"}
    plan, errors = build_run_plan(
        [
            {"id": "r1", "role": "研究员", "task": "调研竞品"},
            {
                "id": "w1",
                "role": "写手",
                "task": "综合成文",
                "depends_on": ["r1"],
                "deliverable": {"form": "prose"},
            },
        ],
        valid_tools=valid,
    )
    assert errors == []
    by_role = {n.role: n for n in plan.nodes}
    assert by_role["研究员"].retrieval_budget == DEFAULT_RETRIEVAL_BUDGET
    assert by_role["写手"].retrieval_budget == DEFAULT_RETRIEVAL_BUDGET
    # prose 不再因检索预算剥离工具（form=prose 撤写文件工具是 registry 另路）
    assert by_role["写手"].tools is None


def test_build_plan_ignores_task_level_retrieval_budget():
    """CEO/task 传入 retrieval_budget 不再作为覆盖——统一默认。"""
    plan, errors = build_run_plan(
        [
            {"id": "r", "role": "研究员", "task": "深挖", "retrieval_budget": 20},
            {
                "id": "w",
                "role": "写手",
                "task": "写",
                "depends_on": ["r"],
                "deliverable": {"form": "prose"},
                "retrieval_budget": 0,
            },
        ],
        valid_tools={"web_search", "web_fetch", "file_read"},
    )
    assert errors == []
    by_role = {n.role: n for n in plan.nodes}
    assert by_role["研究员"].retrieval_budget == DEFAULT_RETRIEVAL_BUDGET
    assert by_role["写手"].retrieval_budget == DEFAULT_RETRIEVAL_BUDGET
    assert by_role["写手"].tools is None


def test_charges_skips_cache_and_failures():
    ok_live = ToolResult(tool_call_id="", success=True, output="x", metadata={})
    ok_cached = ToolResult(
        tool_call_id="", success=True, output="x", metadata={"cached": True}
    )
    failed = ToolResult(tool_call_id="", success=False, output="", error="A3 reject")
    assert charges_retrieval_budget(ok_live) is True
    assert charges_retrieval_budget(ok_cached) is False
    assert charges_retrieval_budget(failed) is False


def test_budget_line_describes_limit_without_ceo_override_hint():
    line = format_retrieval_budget_line(5)
    assert "5" in line
    assert "retrieval_budget" not in line
    assert "continue_from_run_id" not in line
    zero = format_retrieval_budget_line(0)
    assert "0" in zero
    assert "不装配" in zero


def _ctx(*, budget: RetrievalBudgetState | None) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="r1",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        retrieval_budget=budget,
    )


def _call(tool_id: str, name: str = "web_search") -> ToolCall:
    return ToolCall(
        id=tool_id,
        function=ToolCallFunction(name=name, arguments='{"query":"q"}'),
    )


class _SearchStub:
    def __init__(
        self,
        *,
        name: str = "web_search",
        cached: bool = False,
        fail: bool = False,
        crash: bool = False,
    ) -> None:
        self.calls = 0
        self._name = name
        self._cached = cached
        self._fail = fail
        self._crash = crash

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.SEARCH,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.calls += 1
        if self._crash:
            raise RuntimeError("stub blew up")
        if self._fail:
            return ToolResult(tool_call_id="", success=False, error="bad query")
        meta = {"cached": True} if self._cached else {}
        return ToolResult(tool_call_id="", success=True, output="hits", metadata=meta)


@pytest.mark.asyncio
async def test_tool_exec_exhausts_and_returns_structured_feedback():
    stub = _SearchStub()
    reg = ToolRegistry()
    reg.register(stub)
    state = RetrievalBudgetState(limit=1)
    sink = EventSink()

    msgs1, _, _ = await execute_tools(
        [_call("c1")], reg, _ctx(budget=state), sink, approval_gate=None, run_id="r1"
    )
    assert stub.calls == 1
    assert state.used == 1
    assert "hits" in (msgs1[0].content or "")

    msgs2, _, _ = await execute_tools(
        [_call("c2")], reg, _ctx(budget=state), sink, approval_gate=None, run_id="r1"
    )
    assert stub.calls == 1  # second call blocked
    assert BUDGET_EXHAUSTED_FEEDBACK in (msgs2[0].content or "")
    assert "retrieval_budget" not in (msgs2[0].content or "")
    assert "continue_from_run_id" not in (msgs2[0].content or "")
    assert state.used == 1


@pytest.mark.asyncio
async def test_tool_exec_cache_hit_does_not_consume_budget():
    stub = _SearchStub(cached=True)
    reg = ToolRegistry()
    reg.register(stub)
    state = RetrievalBudgetState(limit=1)
    sink = EventSink()

    await execute_tools(
        [_call("c1")], reg, _ctx(budget=state), sink, approval_gate=None, run_id="r1"
    )
    assert stub.calls == 1
    assert state.used == 0

    # still have budget for a live call
    stub2 = _SearchStub(cached=False)
    reg2 = ToolRegistry()
    reg2.register(stub2)
    await execute_tools(
        [_call("c2")], reg2, _ctx(budget=state), sink, approval_gate=None, run_id="r1"
    )
    assert stub2.calls == 1
    assert state.used == 1


@pytest.mark.asyncio
async def test_tool_exec_failed_call_does_not_consume_budget():
    stub = _SearchStub(fail=True)
    reg = ToolRegistry()
    reg.register(stub)
    state = RetrievalBudgetState(limit=1)
    sink = EventSink()

    await execute_tools(
        [_call("c1")], reg, _ctx(budget=state), sink, approval_gate=None, run_id="r1"
    )
    assert stub.calls == 1
    assert state.used == 0


def test_retrieval_tool_names_cover_search_and_read():
    assert frozenset({"web_search", "web_fetch"}) == RETRIEVAL_TOOL_NAMES


def test_rework_refill_slots_zero_after_wind_down():
    assert rework_refill_slots(original_limit=8, wind_down_entered=True) == 0
    assert rework_refill_slots(original_limit=0, wind_down_entered=False) == 0
    assert rework_refill_slots(original_limit=8, wind_down_entered=False) == 4
    assert rework_refill_slots(original_limit=1, wind_down_entered=False) == 1


def test_rework_refill_slots_zero_for_write_disk_form():
    """写盘形态合同返工：不补检索预算（缺的是 file_write，不是阅读额度）。"""
    assert rework_refill_slots(
        original_limit=14, wind_down_entered=False, write_disk_form=True
    ) == 0
    assert rework_refill_slots(
        original_limit=8, wind_down_entered=False, write_disk_form=False
    ) == 4


# ── 分工具记账 + 预算感知注入 ──────────────────────────────────


@pytest.mark.asyncio
async def test_split_ledger_counts_and_refunds_by_tool():
    """共享池语义不变，只是分项记账；退款按工具回退，∑分项 == used。"""
    rb = RetrievalBudgetState(limit=14)
    assert await rb.try_reserve("web_search")
    assert await rb.try_reserve("web_search")
    assert await rb.try_reserve("web_fetch")
    assert rb.used == 3
    assert rb.searches_used == 2
    assert rb.reads_used == 1
    assert rb.remaining == 11

    await rb.refund("web_fetch")
    assert rb.used == 2
    assert rb.searches_used == 2
    assert rb.reads_used == 0
    assert "web_fetch" not in rb.used_by_tool
    assert rb.searches_used + rb.reads_used == rb.used

    # 未记账过的工具退款不把分项压成负数（也不动共享池以外的语义）。
    await rb.refund("web_fetch")
    assert rb.used == 1
    assert rb.reads_used == 0
    assert rb.used_by_tool == {"web_search": 2}


@pytest.mark.asyncio
async def test_tool_exec_splits_usage_and_refunds_cache_hit_per_tool():
    """web_fetch 走同一池；缓存命中退款只回退 web_fetch 分项。"""
    state = RetrievalBudgetState(limit=4)
    sink = EventSink()

    live = ToolRegistry()
    live.register(_SearchStub(name="web_fetch"))
    await execute_tools(
        [_call("c1", "web_fetch")], live, _ctx(budget=state), sink, approval_gate=None, run_id="r1"
    )
    assert (state.used, state.searches_used, state.reads_used) == (1, 0, 1)

    search = ToolRegistry()
    search.register(_SearchStub())
    await execute_tools(
        [_call("c2")], search, _ctx(budget=state), sink, approval_gate=None, run_id="r1"
    )
    assert (state.used, state.searches_used, state.reads_used) == (2, 1, 1)

    cached = ToolRegistry()
    cached.register(_SearchStub(name="web_fetch", cached=True))
    await execute_tools(
        [_call("c3", "web_fetch")],
        cached,
        _ctx(budget=state),
        sink,
        approval_gate=None,
        run_id="r1",
    )
    assert (state.used, state.searches_used, state.reads_used) == (2, 1, 1)


@pytest.mark.asyncio
async def test_tool_exec_crash_and_reject_refund_split_ledger():
    """崩溃 / 契约拒绝都不计费，分项计数同步回退到零。"""
    state = RetrievalBudgetState(limit=4)
    sink = EventSink()

    crashed = ToolRegistry()
    crashed.register(_SearchStub(name="web_fetch", crash=True))
    await execute_tools(
        [_call("c1", "web_fetch")],
        crashed,
        _ctx(budget=state),
        sink,
        approval_gate=None,
        run_id="r1",
    )
    assert (state.used, state.reads_used) == (0, 0)

    rejected = ToolRegistry()
    rejected.register(_SearchStub(fail=True))
    await execute_tools(
        [_call("c2")], rejected, _ctx(budget=state), sink, approval_gate=None, run_id="r1"
    )
    assert (state.used, state.searches_used) == (0, 0)
    assert state.used_by_tool == {}


def _awareness_lines(messages: list[LLMMessage]) -> list[str]:
    return [
        m.content
        for m in messages
        if isinstance(m.content, str)
        and m.content.startswith(RETRIEVAL_BUDGET_AWARENESS_PREFIX)
    ]


@pytest.mark.asyncio
async def test_awareness_not_injected_without_charged_retrieval():
    """一次都没扣费的 worker（生产上占多数）不注入——纯噪音。"""
    messages = [LLMMessage(role="system", content="sys")]
    rb = RetrievalBudgetState(limit=DEFAULT_RETRIEVAL_BUDGET)
    assert sync_retrieval_budget_awareness(messages, rb) is None
    assert _awareness_lines(messages) == []

    # 只有缓存命中（reserve 后退款）同样不算发生过检索。
    assert await rb.try_reserve("web_search")
    await rb.refund("web_search")
    assert sync_retrieval_budget_awareness(messages, rb) is None
    assert _awareness_lines(messages) == []
    assert len(messages) == 1


def test_format_retrieval_budget_receipt_is_numbers_only():
    rb = RetrievalBudgetState(limit=DEFAULT_RETRIEVAL_BUDGET)
    text = format_retrieval_budget_receipt(rb)
    assert "检索余额：已用 0 次（web_search 0 · web_fetch 0）" in text
    assert f"剩余 14/{DEFAULT_RETRIEVAL_BUDGET}" in text
    assert "[系统提示]" not in text
    assert "请" not in text


@pytest.mark.asyncio
async def test_awareness_sync_never_injects_and_drops_stale():
    """扣费后也不再灌 [系统提示]；顺手清掉简历窗口里的旧教案。"""
    messages = [
        LLMMessage(role="system", content="sys"),
        LLMMessage(
            role="user",
            content=f"{RETRIEVAL_BUDGET_AWARENESS_PREFIX}：已用 1 次…",
        ),
    ]
    rb = RetrievalBudgetState(limit=DEFAULT_RETRIEVAL_BUDGET)
    assert await rb.try_reserve("web_search")
    assert sync_retrieval_budget_awareness(messages, rb) is None
    assert _awareness_lines(messages) == []
    assert messages[0].role == "system"


@pytest.mark.asyncio
async def test_awareness_silent_when_exhausted_and_leaves_wind_down_intact():
    """耗尽后 sync 仍不注入，且不误删 wind_down 收尾指令。"""
    messages = [LLMMessage(role="system", content="sys")]
    rb = RetrievalBudgetState(limit=2)
    assert await rb.try_reserve("web_search")
    assert sync_retrieval_budget_awareness(messages, rb) is None

    assert await rb.try_reserve("web_fetch")
    assert rb.remaining == 0
    wind_down = LLMMessage(
        role="user",
        content=(
            "[系统提示] 检索预算已用尽。本轮起进入收尾窗口："
            "web_search / web_fetch 已停用。"
        ),
    )
    messages.append(wind_down)

    assert sync_retrieval_budget_awareness(messages, rb) is None
    assert _awareness_lines(messages) == []
    assert messages == [LLMMessage(role="system", content="sys"), wind_down]


class _LoopProvider:
    """Scripted rounds; records the balance lines the model actually saw each round."""

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.seen: list[list[str]] = []

    async def stream(self, request):  # noqa: ANN001
        self.seen.append(
            [
                m.content
                for m in request.messages
                if m.role == "user"
                and isinstance(m.content, str)
                and m.content.startswith(RETRIEVAL_BUDGET_AWARENESS_PREFIX)
            ]
        )
        idx = len(self.seen) - 1
        for chunk in self._rounds[idx] if idx < len(self._rounds) else []:
            yield chunk


def _tool_round(call_id: str, tool: str) -> list[LLMChunk]:
    return [
        LLMChunk(
            delta_tool_calls=[
                ToolCallDelta(
                    index=0,
                    id=call_id,
                    function_name=tool,
                    arguments_delta='{"query":"q"}',
                )
            ]
        )
    ]


async def _run_worker_loop(
    provider: _LoopProvider, registry: ToolRegistry, budget: RetrievalBudgetState
) -> list[LLMMessage]:
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    await react_loop(
        messages=messages,
        llm=provider,
        tools=registry,
        sink=EventSink(),
        tool_context=_ctx(budget=budget),
        profile=make_profile_params(max_rounds=4),
        turn_model="m",
        role="worker",
        deliverable_only=True,
        approval_gate=None,
    )
    return messages


@pytest.mark.asyncio
async def test_react_loop_skips_balance_for_worker_that_never_retrieves():
    """非检索工具跑几轮也不注入——生产上多数 worker 走这条路。"""
    reg = ToolRegistry()
    reg.register(_SearchStub(name="code_search"))
    provider = _LoopProvider(
        [_tool_round("c1", "code_search"), [LLMChunk(delta_content="交付")]]
    )
    budget = RetrievalBudgetState(limit=DEFAULT_RETRIEVAL_BUDGET)
    messages = await _run_worker_loop(provider, reg, budget)
    assert provider.seen == [[], []]
    assert _awareness_lines(messages) == []
    assert budget.used == 0


@pytest.mark.asyncio
async def test_react_loop_puts_balance_on_tool_receipts():
    """扣费后数字挂工具回执，不再灌每轮 [系统提示]。"""
    reg = ToolRegistry()
    reg.register(_SearchStub())
    provider = _LoopProvider(
        [
            _tool_round("c1", "web_search"),
            _tool_round("c2", "web_search"),
            [LLMChunk(delta_content="交付")],
        ]
    )
    budget = RetrievalBudgetState(limit=DEFAULT_RETRIEVAL_BUDGET)
    messages = await _run_worker_loop(provider, reg, budget)

    assert budget.searches_used == 2
    assert provider.seen == [[], [], []]
    assert _awareness_lines(messages) == []
    receipts = [
        m.content
        for m in messages
        if m.role == "tool" and isinstance(m.content, str) and "检索余额：" in m.content
    ]
    assert len(receipts) == 2
    assert "已用 1 次（web_search 1 · web_fetch 0）" in receipts[0]
    assert f"剩余 13/{DEFAULT_RETRIEVAL_BUDGET}" in receipts[0]
    assert "已用 2 次（web_search 2 · web_fetch 0）" in receipts[1]
    assert f"剩余 12/{DEFAULT_RETRIEVAL_BUDGET}" in receipts[1]


@pytest.mark.asyncio
async def test_react_loop_drops_balance_when_budget_exhausted():
    """耗尽 → wind_down 收尾话术接管，余额播报撤走（行为与改动前一致）。"""
    reg = ToolRegistry()
    reg.register(_SearchStub())
    provider = _LoopProvider(
        [
            _tool_round("c1", "web_search"),
            _tool_round("c2", "web_search"),
            [LLMChunk(delta_content="交付")],
        ]
    )
    budget = RetrievalBudgetState(limit=2)
    messages = await _run_worker_loop(provider, reg, budget)

    assert budget.remaining == 0
    assert _awareness_lines(messages) == []
    wind_down = [
        m.content
        for m in messages
        if m.role == "user"
        and isinstance(m.content, str)
        and m.content.startswith("[系统提示] 检索预算已用尽")
    ]
    assert len(wind_down) == 1
    # 最后一轮模型只看到收尾指令，没有自相矛盾的「还剩」播报。
    assert provider.seen[-1] == []


class _LogSpy:
    """Swaps the loop's module logger (bound structlog loggers resist capture_logs)."""

    def __init__(self) -> None:
        self.infos: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.infos.append((event, kwargs))

    def __getattr__(self, _name: str):  # debug / warning / exception → no-op
        def _noop(*args: Any, **kwargs: Any) -> None:
            return None

        return _noop


@pytest.mark.asyncio
async def test_react_loop_logs_per_tool_spend_with_final_row(monkeypatch):
    """埋点足以事后统计分工具用量：轨迹行按变化记，final 行每个 run 一条。"""
    from agentcore.runtime.engine import loop as loop_mod
    from agentcore.runtime.engine import tool_exec_call as call_mod

    spy = _LogSpy()
    monkeypatch.setattr(loop_mod, "logger", spy)
    monkeypatch.setattr(call_mod, "logger", spy)

    reg = ToolRegistry()
    reg.register(_SearchStub())
    reg.register(_SearchStub(name="web_fetch"))
    provider = _LoopProvider(
        [
            _tool_round("c1", "web_search"),
            _tool_round("c2", "web_fetch"),
            [LLMChunk(delta_content="交付")],
        ]
    )
    budget = RetrievalBudgetState(limit=DEFAULT_RETRIEVAL_BUDGET)
    await _run_worker_loop(provider, reg, budget)

    rows = [kw for ev, kw in spy.infos if ev == "engine.retrieval_budget_awareness"]
    trajectory = [(r["searches"], r["reads"]) for r in rows if not r["final"]]
    assert trajectory == [(1, 0), (1, 1)]
    final = [r for r in rows if r["final"]]
    assert len(final) == 1
    assert (final[0]["searches"], final[0]["reads"], final[0]["used"]) == (1, 1, 2)
    assert final[0]["limit"] == DEFAULT_RETRIEVAL_BUDGET
    assert final[0]["remaining"] == DEFAULT_RETRIEVAL_BUDGET - 2
    assert final[0]["critical"] is False


def test_drop_awareness_only_touches_balance_line():
    messages = [
        LLMMessage(role="system", content="sys"),
        LLMMessage(role="user", content="[系统提示] 检索预算已用尽。收尾窗口。"),
        LLMMessage(role="user", content=f"{RETRIEVAL_BUDGET_AWARENESS_PREFIX}：已用 1 次…"),
    ]
    assert drop_retrieval_budget_awareness(messages) is True
    assert len(messages) == 2
    assert messages[-1].content.startswith("[系统提示] 检索预算已用尽")
    assert drop_retrieval_budget_awareness(messages) is False


@pytest.mark.asyncio
async def test_refill_within_cap_does_not_raise_past_original():
    rb = RetrievalBudgetState(limit=4)
    assert await rb.try_reserve("web_search")
    assert await rb.try_reserve("web_search")
    assert await rb.try_reserve("web_fetch")
    assert await rb.try_reserve("web_fetch")
    assert rb.remaining == 0
    # Exhausted within original — within_cap cannot grow past cap=4.
    remaining = await rb.refill_within_cap(2, cap=4)
    assert remaining == 0
    assert rb.limit == 4
    # Headroom below cap still works (simulate partial spend after a lower limit).
    rb2 = RetrievalBudgetState(limit=2)
    await rb2.try_reserve("web_search")
    await rb2.try_reserve("web_search")
    rem2 = await rb2.refill_within_cap(3, cap=5)
    assert rb2.limit == 5
    assert rem2 == 3
