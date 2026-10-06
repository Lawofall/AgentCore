"""Turn-level latency probe.

``elapsed_ms`` / ``turn_wall_ms`` is a **product settle fact**: active time on
this assistant message, stamped onto ``message_end.duration_ms`` and persisted
(``messages.usage.duration_ms``). That number is the bubble-footer duration
(``12s`` / ``2m 34s``; no 「用时」 prefix — the completion clock is on the same row).
A pause (ask / approval / rate-limit) stops the clock; resume adds the next
segment. Time the user spends answering is not in the sum. The
collaboration-graph strip uses a different clock (fact-stream span after
``run_plan``) — two surfaces, two clocks; do not merge them here.

``generation_ms`` is the decode clock for the same message: sum of each LLM
stream's decode window (first reasoning / content / tool-call chunk → that
stream's end), across every segment. It excludes prefill, tools, waiting on
teammates, and time spent waiting on the user. Output speed is this message's
output tokens ÷ this sum. Persist on ``message_end`` / ``messages.usage``.
Missing stays absent — never a fake ``0``.

``ttft_ms`` is the usage-panel clock: the earlier positive value of
``ttft_reasoning_ms`` and ``ttft_content_ms`` (captain first stream only).
Persist on ``message_end`` / ``messages.usage``. The two raw clocks stay
observation-only on ``chat.turn_complete`` — do not write the derived number
back onto those keys. Missing stays absent — never a fake ``0``.

Four Phase-0 fields remain observation-only on ``chat.turn_complete``:

- ``prepare_ms`` / ``assemble_ms`` — wall-clock duration of the existing
  ``prepare_fresh_turn`` / ``assemble_ceo_turn`` calls (not LLM latency).
- ``ttft_reasoning_ms`` / ``ttft_content_ms`` — elapsed ms from the turn anchor
  (monotonic fixed early at user-message handling) to the **first** CEO/captain
  LLM stream's first reasoning chunk / first content-or-tool chunk (whichever of
  content delta vs tool_call delta arrives first for content).

Missing paths stay ``None`` (JSON null) — never a fake ``0``. Subsequent captain
rounds and all worker streams do not overwrite the TTFT pair.

Emitted on ``chat.turn_complete`` by whichever process ran the captain stream
(cloud ``turn_runner`` or sidecar ``startTurn``). Cloud write-back of a sidecar
turn does **not** copy these onto ``chat.local_turn_recorded``.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass

from agentcore.llm.provider.protocol import TokenUsage


@dataclass
class TurnLatencyProbe:
    """Mutable accumulator for one turn attempt's Phase-0 latency fields."""

    anchor_mono: float
    prepare_ms: int | None = None
    assemble_ms: int | None = None
    ttft_reasoning_ms: int | None = None
    ttft_content_ms: int | None = None
    generation_ms: int = 0
    # Active wall already closed on this message (earlier segments). Human wait
    # is not included; it never entered those segments.
    carried_duration_ms: int = 0
    _captain_stream_armed: bool = False
    _captain_first_stream_done: bool = False
    _recording_this_stream: bool = False

    def elapsed_ms(self) -> int:
        segment = int((time.monotonic() - self.anchor_mono) * 1000)
        return self.carried_duration_ms + max(segment, 0)

    def mark_prepare(self, wall_ms: int) -> None:
        """Record prepare wall-clock once (first call wins)."""
        if self.prepare_ms is None:
            self.prepare_ms = wall_ms

    def mark_assemble(self, wall_ms: int) -> None:
        """Record assemble wall-clock once (first call wins)."""
        if self.assemble_ms is None:
            self.assemble_ms = wall_ms

    def begin_captain_stream(self) -> bool:
        """Arm TTFT recording for the first captain stream only.

        Returns True when this stream should note first chunks.
        """
        if self._captain_first_stream_done or self._captain_stream_armed:
            return False
        self._captain_stream_armed = True
        self._recording_this_stream = True
        return True

    def end_captain_stream(self) -> None:
        """Close the armed first-stream window (idempotent)."""
        if not self._recording_this_stream and not self._captain_stream_armed:
            return
        self._recording_this_stream = False
        self._captain_stream_armed = False
        self._captain_first_stream_done = True

    def clear_ttft(self) -> None:
        """Drop in-flight TTFT marks (stream_reset / pre-commit stall retry)."""
        if not self._recording_this_stream:
            return
        self.ttft_reasoning_ms = None
        self.ttft_content_ms = None

    def note_reasoning_chunk(self) -> None:
        if self._recording_this_stream and self.ttft_reasoning_ms is None:
            self.ttft_reasoning_ms = self.elapsed_ms()

    def note_content_or_tool_chunk(self) -> None:
        if self._recording_this_stream and self.ttft_content_ms is None:
            self.ttft_content_ms = self.elapsed_ms()

    def add_generation_ms(self, ms: int) -> None:
        """Accumulate one LLM stream's decode window (captain and workers)."""
        if ms > 0:
            self.generation_ms += ms

    def earliest_ttft_ms(self) -> int | None:
        """Earlier positive captain first-stream chunk, or None.

        Reasoning and content/tool are separate log clocks. The usage panel
        shows one number: whichever of those two arrived first. A zero elapsed
        is not a measurement.
        """
        vals = [
            n
            for n in (
                _positive_int(self.ttft_reasoning_ms),
                _positive_int(self.ttft_content_ms),
            )
            if n is not None
        ]
        return min(vals) if vals else None

    def as_log_fields(self) -> dict[str, int | None]:
        """Always emit the four keys; absent paths are ``None`` (not ``0``)."""
        return {
            "prepare_ms": self.prepare_ms,
            "assemble_ms": self.assemble_ms,
            "ttft_reasoning_ms": self.ttft_reasoning_ms,
            "ttft_content_ms": self.ttft_content_ms,
        }


current_turn_latency: ContextVar[TurnLatencyProbe | None] = ContextVar(
    "current_turn_latency", default=None
)


def bind_turn_latency(
    anchor_mono: float | None = None,
    *,
    carried_duration_ms: int = 0,
    carried_generation_ms: int = 0,
    first_stream_done: bool = False,
) -> tuple[TurnLatencyProbe, Token]:
    """Install a probe for this attempt; caller must ``reset`` the token.

    ``carried_*`` is active time already stored on this assistant message.
    Resume passes the pause snapshot so this attempt continues the same meter.
    ``first_stream_done`` keeps the original TTFT: a later segment must not
    replace the first token's clock.
    """
    probe = TurnLatencyProbe(
        anchor_mono=time.monotonic() if anchor_mono is None else anchor_mono,
        carried_duration_ms=max(int(carried_duration_ms or 0), 0),
        generation_ms=max(int(carried_generation_ms or 0), 0),
        _captain_first_stream_done=bool(first_stream_done),
    )
    return probe, current_turn_latency.set(probe)


def get_turn_latency() -> TurnLatencyProbe | None:
    return current_turn_latency.get()


def _nonneg_int(raw: object) -> int:
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return raw if raw > 0 else 0
    if isinstance(raw, float) and raw.is_integer() and raw > 0:
        return int(raw)
    return 0


@dataclass(frozen=True, slots=True)
class ActiveMeter:
    """Active work already on one assistant message.

    Wall, decode window, and tokens are one meter. A later segment adds to
    all three. Human wait is absent because it was never stored.
    """

    duration_ms: int = 0
    generation_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    rounds: int = 0
    ttft_ms: int = 0

    @property
    def first_stream_done(self) -> bool:
        return self.generation_ms > 0 or self.ttft_ms > 0

    @classmethod
    def from_usage(cls, usage: Mapping[str, object] | None) -> ActiveMeter:
        if not isinstance(usage, Mapping):
            return cls()
        return cls(
            duration_ms=_nonneg_int(usage.get("duration_ms")),
            generation_ms=_nonneg_int(usage.get("generation_ms")),
            input_tokens=_nonneg_int(usage.get("input_tokens")),
            output_tokens=_nonneg_int(usage.get("output_tokens")),
            reasoning_tokens=_nonneg_int(usage.get("reasoning_tokens")),
            cache_hit_tokens=_nonneg_int(usage.get("cache_hit_tokens")),
            cache_miss_tokens=_nonneg_int(usage.get("cache_miss_tokens")),
            rounds=_nonneg_int(usage.get("rounds")),
            ttft_ms=_nonneg_int(usage.get("ttft_ms")),
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "duration_ms": self.duration_ms,
            "generation_ms": self.generation_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
            "rounds": self.rounds,
            "ttft_ms": self.ttft_ms,
        }


current_active_meter: ContextVar[ActiveMeter | None] = ContextVar(
    "current_active_meter", default=None
)


def bind_active_meter(meter: ActiveMeter) -> Token:
    """Install the message meter for this attempt; caller must ``reset``."""
    return current_active_meter.set(meter)


def reset_active_meter(token: Token) -> None:
    current_active_meter.reset(token)


def fold_active_meter(usage: TokenUsage, rounds: int) -> tuple[TokenUsage, int]:
    """Add the bound message meter onto this segment's tokens and rounds.

    No meter bound → the segment numbers pass through (a fresh turn).
    ``last_prompt`` stays the segment's watermark. Cost rows are not touched.
    """
    meter = current_active_meter.get()
    if meter is None:
        return usage, rounds
    carried = TokenUsage(
        input_tokens=meter.input_tokens,
        output_tokens=meter.output_tokens,
        reasoning_tokens=meter.reasoning_tokens,
        cache_hit_tokens=meter.cache_hit_tokens,
        cache_miss_tokens=meter.cache_miss_tokens,
    )
    return carried + usage, rounds + meter.rounds


def turn_wall_ms() -> int | None:
    """Whole-turn product-AI wall clock (ms). None when no probe is bound.

    Vectors / un-bound paths stay ``None`` — never invent ``0``.
    """
    probe = get_turn_latency()
    if probe is None:
        return None
    return probe.elapsed_ms()


def _positive_int(raw: object) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        n = raw
    elif isinstance(raw, float) and raw.is_integer():
        n = int(raw)
    else:
        return None
    return n if n > 0 else None


def stamp_turn_ttft(target: dict) -> int | None:
    """Write ``ttft_ms``. An existing positive value wins so a later stamp cannot stretch it."""
    existing = _positive_int(target.get("ttft_ms"))
    if existing is not None:
        return existing
    probe = get_turn_latency()
    if probe is None:
        return None
    ms = probe.earliest_ttft_ms()
    if ms is not None:
        target["ttft_ms"] = ms
    return ms


def stamp_turn_generation(target: dict) -> int | None:
    """Write ``generation_ms`` (decode-window sum). Larger of existing vs probe wins."""
    probe = get_turn_latency()
    incoming = probe.generation_ms if probe is not None else 0
    prev = _positive_int(target.get("generation_ms")) or 0
    ms = max(prev, incoming)
    if ms > 0:
        target["generation_ms"] = ms
        return ms
    return None


def stamp_turn_wall(
    target: dict, *, duration_ms: int | None = None
) -> int | None:
    """Write ``duration_ms`` onto a settle / persist dict once.

    Existing positive values win so later sidecar harvest wait cannot stretch
    the number already shown on ``message_end``. Also stamps ``generation_ms``
    (decode windows may still grow; max wins) and ``ttft_ms`` (existing wins).
    """
    existing = _positive_int(target.get("duration_ms"))
    if existing is not None:
        stamp_turn_generation(target)
        stamp_turn_ttft(target)
        return existing
    ms = duration_ms if duration_ms is not None else turn_wall_ms()
    if ms is not None:
        target["duration_ms"] = ms
    stamp_turn_generation(target)
    stamp_turn_ttft(target)
    return ms


def interrupt_usage_clocks(
    *,
    duration_ms: int | None = None,
    generation_ms: int | None = None,
) -> dict[str, int]:
    """Clocks to persist on stop / interrupt salvage.

    Bound probe fills gaps. Missing stays absent — never a fake ``0``.
    """
    target: dict = {}
    if duration_ms is not None:
        target["duration_ms"] = duration_ms
    if generation_ms is not None:
        target["generation_ms"] = generation_ms
    stamp_turn_wall(target)
    out: dict[str, int] = {}
    for key in ("duration_ms", "generation_ms", "ttft_ms"):
        val = _positive_int(target.get(key))
        if val is not None:
            out[key] = val
    return out


def reset_turn_latency(token: Token) -> None:
    current_turn_latency.reset(token)
