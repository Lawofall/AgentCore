"""引用即出处：对话成稿可挂已登记号（含 search-only）；落盘成文仍走 citation_quality。"""

from __future__ import annotations

from pathlib import Path

from agentcore.llm.provider.protocol import LLMChunk, LLMMessage
from agentcore.runtime.engine import ReactLoopOut, react_loop
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.evidence_ledger import EvidenceLedgerCore
from agentcore.runtime.facts import TurnPausedFact
from agentcore.runtime.pipeline.resume.rehydrate import rehydrate_from_turn_paused
from agentcore.runtime.suspension import AskUserSuspension, turn_evidence_ledger
from agentcore.runtime.turn.paused_capture import build_turn_paused_fact
from agentcore.runtime.verify import finish_guard
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_profile_params


def _content_chunk(text: str) -> LLMChunk:
    return LLMChunk(delta_content=text)


class _ScriptedProvider:
    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk


def _context() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


async def _run_worker(
    provider: _ScriptedProvider,
    *,
    ledger: EvidenceLedgerCore,
    max_rounds: int = 10,
):
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    sink = EventSink()
    resets: list[str] = []
    result = await react_loop(
        messages=messages,
        llm=provider,
        tools=ToolRegistry(),
        sink=sink,
        tool_context=_context(),
        profile=make_profile_params(max_rounds=max_rounds),
        turn_model="m",
        out=ReactLoopOut(citations=[]),
        annotate_citations=False,
        turn_evidence_ledger=ledger,
        on_reset=resets.append,
        approval_gate=None,
    )
    return result, messages, sink, resets


def _seed_ledger(
    *urls: str, weak_url: str | None = None, deep_read: bool = True
) -> EvidenceLedgerCore:
    """默认 deep_read=True，使成稿闸可引；search-only 测例显式传 deep_read=False。"""
    led = EvidenceLedgerCore(id_prefix="#r")
    for url in urls:
        led.register_sync(
            url=url, title=url, registrant="worker:w1", deep_read=deep_read
        )
    if weak_url:
        led.register_sync(
            url=weak_url,
            title="weak",
            registrant="worker:w1",
            tier="weak",
            deep_read=deep_read,
        )
    return led


def test_finish_guard_search_only_passes_chat():
    """search-only 可挂对话成稿；chat finish_guard 不回炉。"""
    assert (
        finish_guard(
            "见 #r1。",
            citation_count=0,
            check_citations=False,
        )
        == []
    )


def test_citation_quality_search_only_blocked():
    from agentcore.runtime.verify import citation_quality_reworks

    reworks = citation_quality_reworks("见 #r1。", citable_ids=frozenset())
    assert reworks and "#r1" in reworks[0]


async def test_worker_search_only_rn_passes_without_reset():
    """仅 search-only 引用：保留 #rN，无 content_reset / Rework / 自动深读。"""
    led = _seed_ledger("https://example.com/a", deep_read=False)
    assert led.draft_citable_ids() == frozenset()
    assert led.citable_ids() == frozenset({"#r1"})
    provider = _ScriptedProvider([[_content_chunk("结论见 #r1。")]])
    (content, _r, _u, rounds), _messages, _sink, resets = await _run_worker(
        provider, ledger=led
    )
    assert content == "结论见 #r1。"
    assert rounds == 1
    assert resets == []
    assert led.draft_citable_ids() == frozenset()
    assert led.get("#r1")["deep_read"] is False


async def test_worker_forged_rn_keeps_body_without_reset():
    """伪造 #rN 留白字，不剥号、不 finish_guard reset。"""
    led = _seed_ledger("https://example.com/a")
    provider = _ScriptedProvider([[_content_chunk("伪造 #r9。")]])
    (content, _r, _u, rounds), messages, _sink, resets = await _run_worker(
        provider, ledger=led
    )
    assert rounds == 1
    assert content == "伪造 #r9。"
    assert resets == []
    steers = [
        m
        for m in messages
        if m.role == "user" and m.content and "核验未通过" in m.content
    ]
    assert steers == []


# --- finish_guard 纯函数 -------------------------------------------------------


def test_finish_guard_valid_ledger_ref_passes():
    assert (
        finish_guard(
            "结论见 #r1。",
            citation_count=0,
            check_citations=False,
        )
        == []
    )


def test_finish_guard_forged_ledger_ref_not_flagged():
    assert (
        finish_guard(
            "伪造 #r9。",
            citation_count=0,
            check_citations=False,
        )
        == []
    )


def test_finish_guard_uncitable_not_flagged():
    assert (
        finish_guard(
            "幽灵 #r2。",
            citation_count=0,
            check_citations=False,
        )
        == []
    )


def test_finish_guard_weak_citable_passes():
    assert (
        finish_guard(
            "弱源亦可引 #r2。",
            citation_count=0,
            check_citations=False,
        )
        == []
    )


def test_finish_guard_q5_no_marker_skips_ledger_gate():
    assert (
        finish_guard(
            "普通调研结论，无约定引用标记。",
            citation_count=0,
            check_citations=False,
        )
        == []
    )


def test_finish_guard_legacy_bracket_not_reworked():
    assert finish_guard("见 [9]。", citation_count=1, check_citations=True) == []


# --- react_loop 集成：worker 分路径 --------------------------------------------


async def test_worker_valid_rn_finishes_without_rework():
    led = _seed_ledger("https://example.com/a")
    provider = _ScriptedProvider([[_content_chunk("结论见 #r1。")]])
    (content, _r, _u, rounds), _messages, _sink, resets = await _run_worker(
        provider, ledger=led
    )
    assert content == "结论见 #r1。"
    assert rounds == 1
    assert resets == []


async def test_worker_bibliography_does_not_rework_chat():
    """书目形态不再清空气泡；search-only 可挂、无 reset。"""
    led = _seed_ledger("https://example.com/paper", deep_read=False)
    provider = _ScriptedProvider(
        [[_content_chunk("李四. 某某研究[J]. #r1")]]
    )
    (content, _r, _u, rounds), messages, _sink, resets = await _run_worker(
        provider, ledger=led
    )
    assert content == "李四. 某某研究[J]. #r1"
    assert rounds == 1
    assert resets == []
    steers = [
        m
        for m in messages
        if m.role == "user" and m.content and "核验未通过" in m.content
    ]
    assert steers == []


async def test_worker_no_marker_does_not_rework():
    led = _seed_ledger("https://example.com/a")
    provider = _ScriptedProvider([[_content_chunk("worker 产出无引用。")]])
    (content, _r, _u, rounds), _messages, _sink, resets = await _run_worker(
        provider, ledger=led
    )
    assert content == "worker 产出无引用。"
    assert rounds == 1
    assert resets == []


async def test_worker_legacy_bracket_still_skipped():
    led = _seed_ledger("https://example.com/a")
    provider = _ScriptedProvider([[_content_chunk("worker 产出 [1]。")]])
    (content, _r, _u, rounds), _messages, _sink, resets = await _run_worker(
        provider, ledger=led
    )
    assert content == "worker 产出 [1]。"
    assert rounds == 1
    assert resets == []


async def test_ceo_forged_ledger_ref_keeps_body_without_reset():
    led = _seed_ledger("https://example.com/a")
    provider = _ScriptedProvider([[_content_chunk("见 #r9。")]])
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    sink = EventSink()
    content, _r, _u, rounds = await react_loop(
        messages=messages,
        llm=provider,
        tools=ToolRegistry(),
        sink=sink,
        tool_context=_context(),
        profile=make_profile_params(max_rounds=10),
        turn_model="m",
        out=ReactLoopOut(citations=[]),
        annotate_citations=True,
        turn_evidence_ledger=led,
        approval_gate=None,
    )
    assert rounds == 1
    assert content == "见 #r9。"
    resets = [e for e in sink._history if e.type == EventType.CONTENT_RESET]
    assert resets == []


async def test_ceo_bracket_does_not_rework():
    provider = _ScriptedProvider([[_content_chunk("见 [9]。")]])
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    sink = EventSink()
    content, _r, _u, rounds = await react_loop(
        messages=messages,
        llm=provider,
        tools=ToolRegistry(),
        sink=sink,
        tool_context=_context(),
        profile=make_profile_params(max_rounds=10),
        turn_model="m",
        out=ReactLoopOut(citations=[]),
        annotate_citations=True,
        turn_evidence_ledger=None,
        approval_gate=None,
    )
    assert content == "见 [9]。"
    assert rounds == 1
    resets = [e for e in sink._history if e.type == EventType.CONTENT_RESET]
    assert resets == []


async def test_settle_keeps_search_only_and_unregistered_body():
    """settle 不剥正文；search-only 可挂来源卡；假 #rN 不进 cited_ids。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agentcore.llm.provider.protocol import TokenUsage
    from agentcore.runtime.events import FinishReason
    from agentcore.runtime.pipeline.settle import settle_successful_turn
    from agentcore.runtime.suspension import turn_evidence_ledger

    led = _seed_ledger("https://example.com/a", deep_read=False)
    token = turn_evidence_ledger.set(led)
    sink = EventSink()
    try:
        result = await settle_successful_turn(
            message_id="m-cite",
            captain_run_id="cap",
            captain_state=SimpleNamespace(
                content="见 #r1 与幽灵 #r9。",
                reasoning="",
                rounds=1,
                usage=TokenUsage().as_dict(),
                cost={"total": 0, "currency": "USD"},
                model="m",
                duration_ms=0,
                finish_override=FinishReason.END_TURN,
            ),
            delegate_tool=SimpleNamespace(
                usage={},
                run_ledger=[],
                citations=[],
                collab={"boundary_yields": 0, "scope_signals": 0, "escalations": 0},
                continuation_count=0,
                user_continuation_count=0,
                dispose_open_supervised=AsyncMock(),
            ),
            debate_tool=SimpleNamespace(usage={}, run_ledger=[], citations=[]),
            profile=SimpleNamespace(max_rounds=20),
            citations=[],
            vision_cost_sink=[],
            sink=sink,
            fact_log=None,
            audit_recorder=SimpleNamespace(drops=0, flush=AsyncMock()),
            roster_writer=None,
            journal_writer=SimpleNamespace(flush=AsyncMock()),
        )
    finally:
        turn_evidence_ledger.reset(token)

    assert result["content"] == "见 #r1 与幽灵 #r9。"
    assert result["cited_ids"] == ["#r1"]
    assert [c["id"] for c in result["citations"]] == ["#r1"]
    resets = [e for e in sink._history if e.type == EventType.CONTENT_RESET]
    assert resets == []


# --- pause / resume 台账快照 ---------------------------------------------------


def test_turn_paused_captures_and_rehydrates_ledger():
    led = EvidenceLedgerCore(id_prefix="#r")
    led.register_sync(
        url="https://example.com/a", title="A", registrant="ceo", deep_read=True
    )
    led.register_sync(url="https://example.com/b", title="B", registrant="worker:w1")
    token = turn_evidence_ledger.set(led)
    try:
        fact = build_turn_paused_fact(
            checkpoint_id="ck1",
            suspension_kind="ask_user",
            required_event=type("E", (), {"type": "ask_user_required", "payload": {}})(),
            journal_entries_before_trailing=[],
            sink=None,
        )
    finally:
        turn_evidence_ledger.reset(token)

    assert len(fact.evidence_ledger or []) == 2
    assert fact.evidence_ledger[0]["id"] == "#r1"
    assert fact.evidence_ledger[1]["id"] == "#r2"

    # rehydrate → load_entries → id 连续；成稿闸仅 deep_read∪selected
    restored = EvidenceLedgerCore(id_prefix="#r")
    restored.load_entries(fact.evidence_ledger or [])
    assert restored.citable_ids() == frozenset({"#r1", "#r2"})
    assert restored.draft_citable_ids() == frozenset({"#r1"})
    assert (
        finish_guard(
            "挂起前已引用 #r1。",
            citation_count=0,
            check_citations=False,
        )
        == []
    )
    nxt = restored.register_sync(
        url="https://example.com/c", title="C", registrant="ceo"
    )
    assert nxt == "#r3"  # 不重号


def test_rehydrate_state_exposes_evidence_ledger():
    sink = EventSink()
    entry = (
        TurnPausedFact(
            checkpoint_id="ck1",
            suspension_kind="ask_user",
            content="见 #r1",
            evidence_ledger=[
                {
                    "id": "#r1",
                    "url": "https://example.com/a",
                    "title": "A",
                    "tier": "unknown",
                    "citable": True,
                    "registrant": "ceo",
                }
            ],
        )
        .to_fact()
        .entry()
    )
    frame = AskUserSuspension(
        message_id="m1",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap1",
        checkpoint_id="ck1",
        tool_call_id="call1",
        base_system_prompt="sys",
        user_message="go",
        journal_entries=[entry],
        question="q",
        questions=[],
    )
    hydrated = rehydrate_from_turn_paused(sink=sink, suspension=frame)
    assert hydrated.from_turn_paused is True
    assert len(hydrated.evidence_ledger) == 1
    assert hydrated.evidence_ledger[0]["id"] == "#r1"
