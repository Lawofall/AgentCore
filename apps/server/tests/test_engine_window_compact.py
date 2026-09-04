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
    head_end,
    maybe_compact_worker_window,
    near_window_ceiling,
    preamble_end,
    project_compacted_window,
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

CLEARABLE = frozenset({"file_read", "grep"})


def _round(i: int, *, path: str | None = None, body: str = "x") -> list[LLMMessage]:
    name = "file_read"
    p = path or f"f{i}.py"
    call = ToolCall(
        id=f"c{i}",
        function=ToolCallFunction(name=name, arguments=json.dumps({"path": p})),
    )
    return [
        LLMMessage(role="assistant", content=f"read {p}", tool_calls=[call]),
        LLMMessage(role="tool", content=body, tool_call_id=call.id),
    ]


def _worker_window(n: int) -> list[LLMMessage]:
    msgs = [
        LLMMessage(role="system", content="sys"),
        LLMMessage(role="user", content="task"),
    ]
    for i in range(n):
        msgs.extend(_round(i, body=f"body-{i}"))
    return msgs


def test_production_window_compact_defaults() -> None:
    assert settings.engine_window_compact_enabled is True
    assert settings.engine_window_compact_prompt_tokens == 64_000
    assert settings.engine_window_compact_recency_rounds == 2
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


def test_select_keeps_recency_and_skips_already_folded() -> None:
    msgs = _worker_window(8)
    first = select_new_fold_spans(
        msgs, recency_rounds=2, already_folded=0, min_fold_rounds=4
    )
    assert len(first) == 6
    assert first[0][0] == 2
    nxt = select_new_fold_spans(
        msgs, recency_rounds=2, already_folded=6, min_fold_rounds=1
    )
    assert nxt == []
    too_few = select_new_fold_spans(
        _worker_window(5), recency_rounds=2, already_folded=0, min_fold_rounds=4
    )
    assert too_few == []


def test_project_replaces_prefix_with_summary_and_bridge() -> None:
    msgs = _worker_window(6)
    out = project_compacted_window(
        msgs, summary="已读 f0–f3", folded_rounds=4, recency_rounds=2
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
    # Recency = last 2 rounds (4 messages) + summary/bridge.
    assert len(out) == 2 + 2 + 4
    roles = [m.role for m in out]
    for i in range(len(roles) - 1):
        if roles[i] == "assistant" and roles[i + 1] == "assistant":
            raise AssertionError("consecutive assistants")


def test_project_does_not_eat_recency_when_watermark_is_high() -> None:
    msgs = _worker_window(5)
    out = project_compacted_window(
        msgs, summary="x", folded_rounds=99, recency_rounds=2
    )
    # 5 rounds, recency 2 → fold at most 3.
    assert len([m for m in out if m.role == "assistant" and m.tool_calls]) == 2


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
                    "function": {"name": "file_read", "arguments": "{}"},
                }
            ],
        )
        .to_fact()
        .entry(),
        ToolCallFact(
            run_id="w1",
            tool_call_id="c0",
            name="file_read",
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
                    "function": {"name": "file_read", "arguments": "{}"},
                }
            ],
        )
        .to_fact()
        .entry(),
        ToolCallFact(
            run_id="w1",
            tool_call_id="c1",
            name="file_read",
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
    assert "file_read" in text
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
async def test_maybe_compact_skips_captain_and_records_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentcore.runtime.engine import window_compact as wc

    wc._cooldown_until_round.clear()
    calls: list[int] = []

    async def _fake(old: str, folded, **_kw) -> str:
        calls.append(len(folded))
        return f"sum:{len(folded)}"

    monkeypatch.setattr(wc, "_summarize_worker_fold", _fake)
    msgs = _worker_window(8)
    assert (
        await maybe_compact_worker_window(
            msgs,
            run_id="w1",
            role="captain",
            round_idx=3,
            last_prompt_tokens=80_000,
            conversation_id="c1",
            user_id="u1",
            model_id=None,
        )
        is False
    )
    assert calls == []

    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        wrote = await maybe_compact_worker_window(
            msgs,
            run_id="w1",
            role="worker",
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
