"""Phase-0 turn latency probe: anchor, first-chunk once, content vs tool race."""

from __future__ import annotations

import asyncio
import time

import pytest

from agentcore.core.log_context import log_context
from agentcore.llm.provider.protocol import LLMChunk, LLMRequest, TokenUsage, ToolCallDelta
from agentcore.runtime.engine.stream import stream_llm_round
from agentcore.runtime.turn import complete_log as complete_mod
from agentcore.runtime.turn.latency import (
    ActiveMeter,
    TurnLatencyProbe,
    bind_active_meter,
    bind_turn_latency,
    fold_active_meter,
    get_turn_latency,
    interrupt_usage_clocks,
    reset_active_meter,
    reset_turn_latency,
    stamp_turn_wall,
    turn_wall_ms,
)
from tests.conftest import LogSpy


def test_probe_as_log_fields_always_has_four_keys_null_not_zero():
    probe = TurnLatencyProbe(anchor_mono=time.monotonic())
    fields = probe.as_log_fields()
    assert set(fields) == {
        "prepare_ms",
        "assemble_ms",
        "ttft_reasoning_ms",
        "ttft_content_ms",
    }
    assert all(v is None for v in fields.values())


def test_prepare_assemble_are_wall_clock_not_fake_zero():
    probe = TurnLatencyProbe(anchor_mono=time.monotonic())
    probe.mark_prepare(12)
    probe.mark_assemble(34)
    # Second mark ignored (first wins).
    probe.mark_prepare(999)
    probe.mark_assemble(999)
    assert probe.prepare_ms == 12
    assert probe.assemble_ms == 34


def test_ttft_relative_to_anchor_and_first_chunk_only():
    anchor = time.monotonic() - 0.05  # ~50ms ago
    probe = TurnLatencyProbe(anchor_mono=anchor)
    assert probe.begin_captain_stream() is True
    probe.note_reasoning_chunk()
    first_r = probe.ttft_reasoning_ms
    assert first_r is not None and first_r >= 40
    probe.note_reasoning_chunk()
    assert probe.ttft_reasoning_ms == first_r  # once only

    probe.note_content_or_tool_chunk()
    first_c = probe.ttft_content_ms
    assert first_c is not None and first_c >= first_r
    probe.note_content_or_tool_chunk()
    assert probe.ttft_content_ms == first_c

    probe.end_captain_stream()
    # Subsequent streams do not re-arm.
    assert probe.begin_captain_stream() is False
    probe.note_reasoning_chunk()
    assert probe.ttft_reasoning_ms == first_r


def test_content_vs_tool_whichever_first():
    probe = TurnLatencyProbe(anchor_mono=time.monotonic())
    assert probe.begin_captain_stream()
    # Tool arrives first → content TTFT set; later content does not overwrite.
    probe.note_content_or_tool_chunk()  # tool path uses same note
    t0 = probe.ttft_content_ms
    assert t0 is not None
    time.sleep(0.01)
    probe.note_content_or_tool_chunk()
    assert probe.ttft_content_ms == t0


def test_clear_ttft_on_retry_within_first_stream():
    probe = TurnLatencyProbe(anchor_mono=time.monotonic())
    assert probe.begin_captain_stream()
    probe.note_reasoning_chunk()
    assert probe.ttft_reasoning_ms is not None
    probe.clear_ttft()
    assert probe.ttft_reasoning_ms is None
    assert probe.ttft_content_ms is None
    probe.note_content_or_tool_chunk()
    assert probe.ttft_content_ms is not None


class _ScriptedProvider:
    def __init__(self, chunks: list[LLMChunk]) -> None:
        self._chunks = chunks
        self._name = "scripted"

    async def stream(self, request: LLMRequest):
        for c in self._chunks:
            yield c


def _req() -> LLMRequest:
    return LLMRequest(messages=[], model="m", scenario="chat")


@pytest.mark.asyncio
async def test_stream_records_ttft_for_captain_first_stream_only():
    probe, token = bind_turn_latency(time.monotonic() - 0.02)
    try:
        with log_context(cost_role="captain"):
            provider = _ScriptedProvider(
                [
                    LLMChunk(delta_reasoning="think"),
                    LLMChunk(delta_content="hi"),
                    LLMChunk(finish_reason="stop"),
                ]
            )
            seen: list[str] = []
            await stream_llm_round(provider, _req(), seen.append, lambda _d: None)
            assert probe.ttft_reasoning_ms is not None
            assert probe.ttft_content_ms is not None
            assert probe.ttft_reasoning_ms <= probe.ttft_content_ms
            r0, c0 = probe.ttft_reasoning_ms, probe.ttft_content_ms

            # Second captain stream must not overwrite.
            provider2 = _ScriptedProvider(
                [
                    LLMChunk(delta_reasoning="later"),
                    LLMChunk(delta_content="later"),
                    LLMChunk(finish_reason="stop"),
                ]
            )
            await stream_llm_round(provider2, _req(), seen.append, lambda _d: None)
            assert probe.ttft_reasoning_ms == r0
            assert probe.ttft_content_ms == c0
    finally:
        reset_turn_latency(token)


@pytest.mark.asyncio
async def test_stream_tool_before_content_sets_ttft_content():
    probe, token = bind_turn_latency(time.monotonic())
    try:
        with log_context(cost_role="captain"):
            provider = _ScriptedProvider(
                [
                    LLMChunk(
                        delta_tool_calls=[
                            ToolCallDelta(index=0, id="c1", function_name="web_search")
                        ]
                    ),
                    LLMChunk(delta_content="ignored-for-ttft"),
                    LLMChunk(finish_reason="tool_calls"),
                ]
            )
            await stream_llm_round(provider, _req(), lambda _d: None, lambda _d: None)
            assert probe.ttft_content_ms is not None
            assert probe.ttft_reasoning_ms is None
    finally:
        reset_turn_latency(token)


@pytest.mark.asyncio
async def test_stream_worker_does_not_record_ttft():
    probe, token = bind_turn_latency(time.monotonic())
    try:
        with log_context(cost_role="member"):
            provider = _ScriptedProvider(
                [
                    LLMChunk(delta_content="worker"),
                    LLMChunk(finish_reason="stop"),
                ]
            )
            await stream_llm_round(provider, _req(), lambda _d: None, lambda _d: None)
            assert probe.ttft_content_ms is None
            assert probe.ttft_reasoning_ms is None
            # Captain stream later still arms (worker did not consume the slot).
        with log_context(cost_role="captain"):
            provider2 = _ScriptedProvider(
                [
                    LLMChunk(delta_content="ceo"),
                    LLMChunk(finish_reason="stop"),
                ]
            )
            await stream_llm_round(provider2, _req(), lambda _d: None, lambda _d: None)
            assert probe.ttft_content_ms is not None
    finally:
        reset_turn_latency(token)


@pytest.mark.asyncio
async def test_stream_records_generation_ms_for_worker_and_captain():
    probe, token = bind_turn_latency()
    try:

        async def _slow_chunks(*deltas: str):
            for i, d in enumerate(deltas):
                if i:
                    await asyncio.sleep(0.04)
                yield LLMChunk(delta_content=d)
            yield LLMChunk(finish_reason="stop")

        class _Slow:
            def __init__(self, deltas: tuple[str, ...]) -> None:
                self._deltas = deltas

            async def stream(self, request: LLMRequest):
                del request
                async for c in _slow_chunks(*self._deltas):
                    yield c

        with log_context(cost_role="member"):
            await stream_llm_round(
                _Slow(("w1", "w2")), _req(), lambda _d: None, lambda _d: None
            )
        worker_ms = probe.generation_ms
        assert worker_ms >= 30
        with log_context(cost_role="captain"):
            await stream_llm_round(
                _Slow(("c1", "c2")), _req(), lambda _d: None, lambda _d: None
            )
        assert probe.generation_ms >= worker_ms + 30
    finally:
        reset_turn_latency(token)


@pytest.mark.asyncio
async def test_stream_reset_drops_discarded_decode_window():
    probe, token = bind_turn_latency()
    try:

        class _ResetThenSlow:
            async def stream(self, request: LLMRequest):
                del request
                yield LLMChunk(delta_content="gone")
                await asyncio.sleep(0.05)
                yield LLMChunk(stream_reset=True)
                yield LLMChunk(delta_content="kept")
                yield LLMChunk(finish_reason="stop")

        with log_context(cost_role="captain"):
            await stream_llm_round(
                _ResetThenSlow(), _req(), lambda _d: None, lambda _d: None
            )
        # Only the post-reset tail counts — not the slept discarded prefix.
        assert probe.generation_ms < 40
    finally:
        reset_turn_latency(token)


def test_bind_get_reset_contextvar():
    assert get_turn_latency() is None
    probe, token = bind_turn_latency()
    assert get_turn_latency() is probe
    reset_turn_latency(token)
    assert get_turn_latency() is None


def test_turn_wall_ms_none_without_probe():
    assert turn_wall_ms() is None


def test_turn_wall_ms_from_bound_probe():
    probe, token = bind_turn_latency()
    try:
        probe.anchor_mono -= 1.0
        assert turn_wall_ms() >= 1000
    finally:
        reset_turn_latency(token)


def test_stamp_turn_wall_keeps_existing_positive():
    target = {"duration_ms": 12_345}
    assert stamp_turn_wall(target, duration_ms=99) == 12_345
    assert target["duration_ms"] == 12_345


def test_stamp_turn_wall_writes_explicit():
    target: dict = {}
    assert stamp_turn_wall(target, duration_ms=57_000) == 57_000
    assert target["duration_ms"] == 57_000


def test_add_generation_ms_skips_non_positive():
    probe = TurnLatencyProbe(anchor_mono=time.monotonic())
    probe.add_generation_ms(0)
    probe.add_generation_ms(-1)
    assert probe.generation_ms == 0
    probe.add_generation_ms(40)
    probe.add_generation_ms(60)
    assert probe.generation_ms == 100


def test_stamp_turn_wall_stamps_generation_from_probe():
    probe, token = bind_turn_latency()
    try:
        probe.add_generation_ms(1_200)
        target: dict = {}
        stamp_turn_wall(target, duration_ms=5_000)
        assert target["duration_ms"] == 5_000
        assert target["generation_ms"] == 1_200
        probe.add_generation_ms(300)
        stamp_turn_wall(target, duration_ms=99)
        # duration stays; generation grows
        assert target["duration_ms"] == 5_000
        assert target["generation_ms"] == 1_500
    finally:
        reset_turn_latency(token)


def test_interrupt_usage_clocks_from_probe():
    probe, token = bind_turn_latency()
    try:
        probe.add_generation_ms(1_900)
        clocks = interrupt_usage_clocks(duration_ms=5_000)
        assert clocks["duration_ms"] == 5_000
        assert clocks["generation_ms"] == 1_900
    finally:
        reset_turn_latency(token)


def test_interrupt_usage_clocks_omits_missing_generation():
    clocks = interrupt_usage_clocks(duration_ms=12)
    assert clocks["duration_ms"] == 12
    assert "generation_ms" not in clocks
    assert "ttft_ms" not in clocks


def test_earliest_ttft_ms_picks_the_earlier_positive_clock():
    probe = TurnLatencyProbe(anchor_mono=time.monotonic())
    assert probe.earliest_ttft_ms() is None
    probe.ttft_reasoning_ms = 1_800
    assert probe.earliest_ttft_ms() == 1_800
    probe.ttft_content_ms = 400
    assert probe.earliest_ttft_ms() == 400
    probe.ttft_reasoning_ms = 0
    assert probe.earliest_ttft_ms() == 400


def test_stamp_turn_wall_stamps_ttft_and_keeps_existing():
    probe, token = bind_turn_latency()
    try:
        probe.ttft_reasoning_ms = 2_200
        probe.ttft_content_ms = 1_800
        target: dict = {}
        stamp_turn_wall(target, duration_ms=5_000)
        assert target["ttft_ms"] == 1_800
        probe.ttft_content_ms = 200
        stamp_turn_wall(target, duration_ms=99)
        assert target["ttft_ms"] == 1_800
    finally:
        reset_turn_latency(token)


def test_interrupt_usage_clocks_includes_ttft_from_probe():
    probe, token = bind_turn_latency()
    try:
        probe.ttft_content_ms = 900
        clocks = interrupt_usage_clocks(duration_ms=5_000)
        assert clocks["ttft_ms"] == 900
    finally:
        reset_turn_latency(token)


def test_log_chat_turn_complete_emits_phase0_keys(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(complete_mod, "logger", spy)
    probe, token = bind_turn_latency(time.monotonic() - 0.05)
    try:
        with log_context(cost_role="captain"):
            probe.mark_prepare(12)
            probe.begin_captain_stream()
            probe.note_reasoning_chunk()
            complete_mod.log_chat_turn_complete(
                {"finish_reason": "end_turn", "content": "hi", "rounds": 1},
                duration_ms=100,
            )
    finally:
        reset_turn_latency(token)
    kw = spy.get("chat.turn_complete")
    assert kw["duration_ms"] == 100
    assert kw["prepare_ms"] == 12
    assert kw["assemble_ms"] is None
    assert kw["ttft_reasoning_ms"] is not None
    assert kw["ttft_content_ms"] is None
    assert kw["generation_ms"] is None
    assert kw["reply_preview"]


def test_resume_probe_adds_carried_active_time_and_keeps_first_ttft():
    probe, token = bind_turn_latency(
        time.monotonic(),
        carried_duration_ms=10_000,
        carried_generation_ms=4_000,
        first_stream_done=True,
    )
    try:
        elapsed = probe.elapsed_ms()
        assert 10_000 <= elapsed < 10_500
        assert probe.generation_ms == 4_000
        assert probe.begin_captain_stream() is False
        probe.add_generation_ms(300)
        assert probe.generation_ms == 4_300
    finally:
        reset_turn_latency(token)


def test_fold_active_meter_adds_the_same_message():
    meter = ActiveMeter.from_usage(
        {
            "input_tokens": 50,
            "output_tokens": 100,
            "rounds": 2,
            "generation_ms": 4_000,
            "ttft_ms": 800,
        }
    )
    assert meter.first_stream_done is True
    token = bind_active_meter(meter)
    try:
        usage, rounds = fold_active_meter(
            TokenUsage(input_tokens=3, output_tokens=7, last_prompt_tokens=9),
            1,
        )
    finally:
        reset_active_meter(token)
    assert usage.input_tokens == 53
    assert usage.output_tokens == 107
    assert usage.last_prompt_tokens == 9
    assert rounds == 3


def test_fold_active_meter_passes_through_a_fresh_turn():
    usage, rounds = fold_active_meter(TokenUsage(output_tokens=7), 1)
    assert usage.output_tokens == 7
    assert rounds == 1
