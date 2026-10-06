"""Worker mid-run window compact: fold selection, projection, due check, wiring."""

from __future__ import annotations

import json

import pytest

from agentcore.config import settings
from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.engine.round import build_request_window
from agentcore.runtime.engine.window_compact import (
    BRIDGE_USER,
    SUMMARY_LEAD,
    apply_stored_window_compact,
    assistant_round_spans,
    estimate_span_tokens,
    head_end,
    maybe_compact_worker_window,
    near_window_ceiling,
    preamble_end,
    project_compacted_window,
    recency_keep_rounds,
    render_window_fold,
    select_new_fold_spans,
    window_compact_due,
)
from agentcore.runtime.facts import (
    LlmCallFact,
    RoundBoundaryFact,
    RunHeadFact,
    ToolCallFact,
    TurnFactLog,
    WindowCompactFact,
    current_fact_log,
    record_turn_fact,
)

CLEARABLE = frozenset({"read", "grep"})


def _round(i: int, *, path: str | None = None, body: str = "x") -> list[LLMMessage]:
    name = "read"
    p = path or f"f{i}.py"
    call = ToolCall(
        id=f"c{i}",
        function=ToolCallFunction(name=name, arguments=json.dumps({"file_path": p})),
    )
    return [
        LLMMessage(role="assistant", content=f"read {p}", tool_calls=[call]),
        LLMMessage(role="tool", content=body, tool_call_id=call.id),
    ]


def _worker_window(n: int, *, body: str | None = None) -> list[LLMMessage]:
    msgs = [
        LLMMessage(role="system", content="sys"),
        LLMMessage(role="user", content="task"),
    ]
    for i in range(n):
        msgs.extend(_round(i, body=body if body is not None else f"body-{i}"))
    return msgs


def test_production_window_compact_defaults() -> None:
    assert settings.engine_window_compact_enabled is True
    assert settings.engine_window_compact_prompt_tokens == 64_000
    assert settings.engine_window_compact_recency_token_budget == 24_000
    assert settings.engine_window_compact_min_fold_rounds == 4
    assert settings.engine_window_compact_trigger_fold_rounds == 8
    assert settings.engine_window_compact_max_fold_rounds == 12
    assert settings.engine_window_compact_summary_char_budget == 4_000
    assert settings.engine_window_compact_near_ratio == 0.8
    assert settings.engine_window_compact_near_tokens == 200_000
    assert settings.engine_window_compact_cooldown_rounds == 2


def test_head_end_and_spans() -> None:
    msgs = _worker_window(3)
    assert head_end(msgs) == 2
    assert preamble_end(msgs, 2) == 2
    spans = assistant_round_spans(msgs, start=2)
    assert len(spans) == 3
    assert spans[0] == (2, 4)
    assert spans[-1] == (6, 8)


def test_select_keeps_two_rounds_when_both_fit_budget() -> None:
    msgs = _worker_window(8)
    first = select_new_fold_spans(msgs, already_folded=0, min_fold_rounds=4)
    assert len(first) == 6
    assert first[0][0] == 2
    nxt = select_new_fold_spans(msgs, already_folded=6, min_fold_rounds=1)
    assert nxt == []
    too_few = select_new_fold_spans(
        _worker_window(5), already_folded=0, min_fold_rounds=4
    )
    assert too_few == []
    projected = project_compacted_window(msgs, summary="摘要", folded_rounds=6)
    tool_bodies = [str(m.content) for m in projected if m.role == "tool"]
    assert tool_bodies == ["body-6", "body-7"]


def test_select_folds_earlier_round_when_two_exceed_budget() -> None:
    """A pair of fat rounds does not get a hard keep-2; only the newest stays."""
    fat = "Z" * 50_000
    msgs = _worker_window(6, body=fat)
    spans = assistant_round_spans(msgs, start=head_end(msgs))
    newest = estimate_span_tokens(msgs, spans[-1])
    previous = estimate_span_tokens(msgs, spans[-2])
    assert newest + previous > settings.engine_window_compact_recency_token_budget
    assert recency_keep_rounds(
        msgs, spans, token_budget=settings.engine_window_compact_recency_token_budget
    ) == 1
    folded = select_new_fold_spans(msgs, already_folded=0, min_fold_rounds=4)
    assert len(folded) == 5
    assert folded[-1][1] == spans[-2][1]
    projected = project_compacted_window(
        msgs, summary="折了超预算的更早轮", folded_rounds=5
    )
    tool_bodies = [str(m.content) for m in projected if m.role == "tool"]
    assert tool_bodies == [fat]
    assert any("read f5.py" in str(m.content) for m in projected if m.role == "assistant")


def test_project_replaces_prefix_with_summary_and_bridge() -> None:
    msgs = _worker_window(6)
    out = project_compacted_window(
        msgs, summary="已读 f0–f3", folded_rounds=4
    )
    assert out is not msgs
    assert out[0].role == "system"
    assert out[1].role == "user"
    assert out[1].content == "task"
    assert out[2].role == "assistant"
    assert str(out[2].content).startswith(SUMMARY_LEAD)
    assert "已读 f0–f3" in str(out[2].content)
    assert out[3].role == "user"
    assert out[3].content == BRIDGE_USER
    assert out[4].role == "assistant"
    assert out[-1].role == "tool"
    # Folded 4 of 6: summary/bridge + last 2 rounds (4 messages).
    assert len(out) == 2 + 2 + 4
    roles = [m.role for m in out]
    for i in range(len(roles) - 1):
        if roles[i] == "assistant" and roles[i + 1] == "assistant":
            raise AssertionError("consecutive assistants")


def test_project_does_not_eat_newest_round_when_watermark_is_high() -> None:
    msgs = _worker_window(5)
    out = project_compacted_window(
        msgs, summary="x", folded_rounds=99
    )
    # 5 rounds, floor keep 1 → fold at most 4. No token estimate on project.
    assert len([m for m in out if m.role == "assistant" and m.tool_calls]) == 1
    assert any("read f4.py" in str(m.content) for m in out if m.role == "assistant")


def test_project_honors_watermark_without_reestimating() -> None:
    """Projection is the window loop: stored folded_rounds only, never a token recut."""
    fat = "Z" * 50_000
    msgs = _worker_window(6, body=fat)
    out = project_compacted_window(msgs, summary="s", folded_rounds=4)
    tool_bodies = [str(m.content) for m in out if m.role == "tool"]
    assert tool_bodies == [fat, fat]


def test_window_from_journal_ignores_compact_watermark() -> None:
    """Resume rebuilds the fat canonical window; compact is projection-only."""
    from agentcore.runtime.journal.fold import window_from_journal

    entries = [
        RunHeadFact(run_id="w1", system_prompt="SYS", user_message="task").to_fact().entry(),
        RoundBoundaryFact(round_idx=0, run_id="w1", role="worker").to_fact().entry(),
        LlmCallFact(
            run_id="w1",
            round_idx=0,
            content="read",
            tool_calls=[
                {
                    "id": "c0",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        )
        .to_fact()
        .entry(),
        ToolCallFact(
            run_id="w1",
            tool_call_id="c0",
            name="read",
            arguments="{}",
            result="FULL-0",
            success=True,
        )
        .to_fact()
        .entry(),
        WindowCompactFact(run_id="w1", summary="should-not-fold", folded_rounds=1)
        .to_fact()
        .entry(),
        RoundBoundaryFact(round_idx=1, run_id="w1", role="worker").to_fact().entry(),
        LlmCallFact(
            run_id="w1",
            round_idx=1,
            content="read again",
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        )
        .to_fact()
        .entry(),
        ToolCallFact(
            run_id="w1",
            tool_call_id="c1",
            name="read",
            arguments="{}",
            result="FULL-1",
            success=True,
        )
        .to_fact()
        .entry(),
    ]
    window = window_from_journal(entries, run_id="w1")
    assert window is not None
    texts = [str(m.content or "") for m in window]
    assert "SYS" in texts[0]
    assert "task" in texts[1]
    assert "FULL-0" in texts
    assert "FULL-1" in texts
    assert not any("should-not-fold" in t for t in texts)
    assert not any(SUMMARY_LEAD.strip() in t for t in texts)


def test_project_noop_without_summary() -> None:
    msgs = _worker_window(6)
    assert project_compacted_window(msgs, summary="", folded_rounds=4) is msgs


def test_render_includes_paths_and_prior() -> None:
    folded = _round(0, path="src/a.py", body="hello")
    text = render_window_fold("旧摘要", folded)
    assert "旧摘要" in text
    assert "src/a.py" in text
    assert "read" in text
    assert "hello" in text


def test_due_token_rounds_and_near() -> None:
    spans = [(0, 2)] * 4
    assert window_compact_due(
        new_spans=spans,
        last_prompt_tokens=64_000,
        near=False,
        min_fold_rounds=4,
        trigger_fold_rounds=8,
        trigger_prompt_tokens=64_000,
    )
    assert not window_compact_due(
        new_spans=spans,
        last_prompt_tokens=10,
        near=False,
        min_fold_rounds=4,
        trigger_fold_rounds=8,
        trigger_prompt_tokens=64_000,
    )
    many = [(0, 2)] * 8
    assert window_compact_due(
        new_spans=many,
        last_prompt_tokens=10,
        near=False,
        min_fold_rounds=4,
        trigger_fold_rounds=8,
        trigger_prompt_tokens=64_000,
    )
    assert window_compact_due(
        new_spans=[(0, 2)],
        last_prompt_tokens=10,
        near=True,
        min_fold_rounds=4,
        trigger_fold_rounds=8,
        trigger_prompt_tokens=64_000,
    )


@pytest.mark.asyncio
async def test_assembly_budget_folds_a_short_window_before_round_triggers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentcore.llm.context_budget import bind_context_budget, reset_context_budget
    from agentcore.runtime.engine import window_compact as wc

    wc._cooldown_until_round.clear()

    async def _fake(old: str, folded, **_kw) -> str:
        return f"sum:{len(folded)}"

    monkeypatch.setattr(wc, "_summarize_worker_fold", _fake)
    msgs = _worker_window(3, body="Z" * 50_000)
    log = TurnFactLog()
    fact = current_fact_log.set(log)
    try:
        assert (
            await maybe_compact_worker_window(
                msgs,
                run_id="w-budget-off",
                role="worker",
                round_idx=3,
                last_prompt_tokens=110_000,
                conversation_id="c1",
                user_id="u1",
                model_id="deepseek-v4-flash",
            )
            is False
        )
        token = bind_context_budget(128_000)
        try:
            wrote = await maybe_compact_worker_window(
                msgs,
                run_id="w-budget-on",
                role="worker",
                round_idx=3,
                last_prompt_tokens=110_000,
                conversation_id="c1",
                user_id="u1",
                model_id="deepseek-v4-flash",
            )
        finally:
            reset_context_budget(token)
        assert wrote is True
    finally:
        current_fact_log.reset(fact)
        wc._cooldown_until_round.clear()


def test_near_window_ceiling_ratio_and_absolute() -> None:
    assert near_window_ceiling(0, 100_000) is False
    assert near_window_ceiling(79_999, 100_000) is False
    assert near_window_ceiling(80_000, 100_000) is True
    assert near_window_ceiling(199_999, None) is False
    assert near_window_ceiling(200_000, None) is True


def test_apply_stored_compact_from_fact_log() -> None:
    msgs = _worker_window(6)
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        record_turn_fact(
            WindowCompactFact(run_id="w1", summary="折了前四轮", folded_rounds=4).to_fact()
        )
        out = apply_stored_window_compact(msgs, "w1")
        assert "折了前四轮" in str(out[2].content)
        assert apply_stored_window_compact(msgs, "other") is msgs
    finally:
        current_fact_log.reset(token)


def test_build_request_window_applies_compact_after_clears() -> None:
    msgs = [
        LLMMessage(role="system", content="sys"),
        LLMMessage(role="user", content="task"),
    ]
    for i in range(6):
        msgs.extend(_round(i, body="Z" * 3000))
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        record_turn_fact(
            WindowCompactFact(run_id="w1", summary="摘要", folded_rounds=4).to_fact()
        )
        out = build_request_window(msgs, CLEARABLE, 3, run_id="w1")
        assert out[2].role == "assistant"
        assert "摘要" in str(out[2].content)
        # Recency tail still present; old fat rounds not in the projected window.
        assert not any(
            m.role == "tool" and m.content and "Z" * 100 in str(m.content) and m.tool_call_id == "c0"
            for m in out
        )
    finally:
        current_fact_log.reset(token)


@pytest.mark.asyncio
async def test_maybe_compact_records_captain_and_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentcore.runtime.engine import window_compact as wc

    wc._cooldown_until_round.clear()
    calls: list[int] = []

    async def _fake(old: str, folded, **_kw) -> str:
        calls.append(len(folded))
        return f"sum:{len(folded)}"

    monkeypatch.setattr(wc, "_summarize_worker_fold", _fake)
    msgs = _worker_window(8)
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        wrote = await maybe_compact_worker_window(
            msgs,
            run_id="w1",
            role="captain",
            round_idx=3,
            last_prompt_tokens=80_000,
            conversation_id="c1",
            user_id="u1",
            model_id=None,
        )
        assert wrote is True
        assert calls and calls[0] > 0
        kinds = [e["kind"] for e in log.entries()]
        assert "window_compact" in kinds
        payload = log.entries()[-1]["payload"]
        assert payload["folded_rounds"] == 6
        assert payload["summary"].startswith("sum:")
    finally:
        current_fact_log.reset(token)
        wc._cooldown_until_round.clear()


@pytest.mark.asyncio
async def test_maybe_compact_folds_over_budget_earlier_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentcore.runtime.engine import window_compact as wc

    wc._cooldown_until_round.clear()

    async def _fake(old: str, folded, **_kw) -> str:
        return f"sum:{len(folded)}"

    monkeypatch.setattr(wc, "_summarize_worker_fold", _fake)
    fat = "Z" * 50_000
    msgs = _worker_window(8, body=fat)
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        wrote = await maybe_compact_worker_window(
            msgs,
            run_id="w-fat",
            role="worker",
            round_idx=3,
            last_prompt_tokens=80_000,
            conversation_id="c1",
            user_id="u1",
            model_id=None,
        )
        assert wrote is True
        payload = log.entries()[-1]["payload"]
        assert payload["folded_rounds"] == 7
        out = apply_stored_window_compact(msgs, "w-fat")
        tool_bodies = [str(m.content) for m in out if m.role == "tool"]
        assert tool_bodies == [fat]
        assert any("read f7.py" in str(m.content) for m in out if m.role == "assistant")
        assert str(out[2].content).startswith(SUMMARY_LEAD)
    finally:
        current_fact_log.reset(token)
        wc._cooldown_until_round.clear()


@pytest.mark.asyncio
async def test_maybe_compact_failure_cools_down(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentcore.runtime.engine import window_compact as wc

    wc._cooldown_until_round.clear()

    async def _empty(*_a, **_k) -> str:
        return ""

    monkeypatch.setattr(wc, "_summarize_worker_fold", _empty)
    msgs = _worker_window(8)
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        assert (
            await maybe_compact_worker_window(
                msgs,
                run_id="w2",
                role="worker",
                round_idx=4,
                last_prompt_tokens=80_000,
                conversation_id="c1",
                user_id="u1",
                model_id=None,
            )
            is False
        )
        assert (
            await maybe_compact_worker_window(
                msgs,
                run_id="w2",
                role="worker",
                round_idx=5,
                last_prompt_tokens=80_000,
                conversation_id="c1",
                user_id="u1",
                model_id=None,
            )
            is False
        )
        assert not any(e["kind"] == "window_compact" for e in log.entries())
    finally:
        current_fact_log.reset(token)
        wc._cooldown_until_round.clear()


def test_react_loop_skips_window_compact_on_debate_research() -> None:
    import inspect

    from agentcore.runtime.engine import loop as loop_mod

    src = inspect.getsource(loop_mod.react_loop)
    assert "maybe_compact_worker_window" in src
    assert "turn_evidence_ledger is None" in src


@pytest.mark.asyncio
async def test_summarize_worker_fold_reuses_live_window_and_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from agentcore.runtime.engine.window_compact import _summarize_worker_fold
    from agentcore.runtime.resolve.prompt.envelope import TURN_ENVELOPE_FENCE

    captured: dict = {}

    def fake_build(selected, messages, **kwargs):
        captured["messages"] = messages
        captured["tools"] = kwargs.get("tools")
        captured["tool_choice"] = kwargs.get("tool_choice")
        captured["model"] = selected.model
        return MagicMock()

    async def fake_complete(provider, request, **kwargs):
        return SimpleNamespace(content="## 已确立的事实\n- z")

    async def fake_run(user_id, conversation_id, *, runner):
        return SimpleNamespace(value=await runner(MagicMock()))

    monkeypatch.setattr("agentcore.llm.model_selection.build_selected_request", fake_build)
    monkeypatch.setattr("agentcore.llm.provider.call_budget.complete_within_budget", fake_complete)
    monkeypatch.setattr(
        "agentcore.llm.factory.build_provider",
        lambda *a, **k: SimpleNamespace(close=AsyncMock()),
    )
    monkeypatch.setattr("agentcore.billing.gate.run_compaction_llm", fake_run)

    live = [
        LLMMessage(role="system", content="WORKER SYS"),
        LLMMessage(role="user", content="task"),
        LLMMessage(role="assistant", content="did"),
    ]
    tools = [{"type": "function", "function": {"name": "read", "parameters": {}}}]
    out = await _summarize_worker_fold(
        "old",
        live[2:],
        conversation_id="c-win",
        user_id="u1",
        window=live,
        tools=tools,
        model_id="deepseek-v4-pro",
    )
    assert out == "## 已确立的事实\n- z"
    assert captured["messages"][0].content == "WORKER SYS"
    tail = captured["messages"][-1].content or ""
    assert tail.startswith(TURN_ENVELOPE_FENCE)
    assert "不要调用工具" not in tail
    assert captured["tools"] == tools
    assert captured["tool_choice"] == "none"
    assert captured["model"] == "deepseek-v4-pro"
