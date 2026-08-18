"""Cursor replay for ``GET …/stream`` with ``Last-Event-ID`` (流式回复持久化 §3.6 · P3).

Builds an attach catch-up segment: a synthetic ``message_start`` (opens + stamps the
bubble), then durable journal facts + process-lane synthetic deltas interleaved in
journal order + single-block deltas for any still-open stream channels not already
covered by ``process_*`` / ``run_process_*``.

**Two段 shapes, one builder.** The judgement is always made over the WHOLE turn's rows —
four of them have to see every row to be right (是否结构化回合 / 已被 ``run_process_*``
覆盖的 run 集 / ``run_started`` 回填的 ``agent_id`` / ``message_final`` 拼出的 worker 全文).
Only *shipping* is narrowed:

- **全量段** (default, and the fallback whenever the cursor cannot be trusted): head
  carries ``full_replay`` — the server's explicit「先重置本回合本地态，再折本段」order —
  followed by the turn from seq 0.
- **增量段** (:func:`_incremental_verdict` says yes): head carries NO ``full_replay``, so
  the client keeps what it holds and folds the段 onto it; the body is the same event
  stream filtered to the facts after the cursor. Real multi-agent turns journal 600+
  rows (派单行到 15KB), and a phone returning to the foreground re-attaches constantly —
  the full段 re-ships the whole scene every time. DB cost is unchanged (the read is one
  ``(turn_id, seq)`` index scan); what the increment saves is bytes on the wire and the
  client's re-fold.

Text frames that carry a WHOLE block rather than an increment say so with
``replace`` (see ``payloads.chat._REPLACE_DOC``): the still-open channel blocks
synthesized from ``stream_state``, and — in an增量段 — the first post-cursor
``process_*`` text row on each channel, whose step the client may已 hold half of
(live deltas carry no ``id:``, so the cursor sits at an earlier durable fact).

The reset是服务端的指令，不是客户端的猜测: a client used to compare the segment head's
``message_id`` against whatever bubble was on screen to decide whether to clear, and a
wrong guess folded the same body twice. :func:`mark_full_replay_segment` puts the same
instruction on the head of the OTHER replay path (the sink's in-memory history), so both
are client-observably identical.
"""

from __future__ import annotations

from typing import Any

from agentcore.runtime.events.chat import message_start
from agentcore.runtime.events.disposition import DURABLE_EVENT_TYPES
from agentcore.runtime.events.stream_checkpointer import (
    CHANNEL_CAPTAIN_CONTENT,
    CHANNEL_CAPTAIN_REASONING,
    parse_run_channel,
)
from agentcore.runtime.events.types import EventType, FinishReason, SSEEvent
from agentcore.runtime.facts import EXECUTION_ONLY_KINDS, FactKind
from agentcore.runtime.journal.entries import _PROCESS_PREFIX, _RUN_PROCESS_PREFIX, KIND_TURN_END
from agentcore.runtime.runs.types import RunKind

_DURABLE_KIND_VALUES = frozenset(t.value for t in DURABLE_EVENT_TYPES)
_RUN_TERMINAL = frozenset({EventType.RUN_COMPLETED.value, EventType.RUN_FAILED.value})

# process_* / run_process_* kinds that mirror DURABLE tool / marker events — attach
# skips them and lets the DURABLE event rebuild the step via client fold.
_PROCESS_STRUCTURAL_SUFFIXES = frozenset(
    {
        "tool",
        "team",
        "checkpoint",
        "ask",
        "plan_review",
        "team_preview",
        "escalation",
        "approval",
        "delegation_authorization",
        "user_interjection",
    }
)


def _process_step_to_sse(
    kind: str,
    payload: dict[str, Any],
    *,
    seq: int | None,
    ts: str,
) -> SSEEvent | None:
    """Translate a journaled process / run_process text step into a foldable delta."""
    if kind.startswith(_RUN_PROCESS_PREFIX):
        suffix = kind[len(_RUN_PROCESS_PREFIX) :]
        if suffix in _PROCESS_STRUCTURAL_SUFFIXES:
            return None
        run_id = payload.get("run_id") or ""
        agent_id = payload.get("agent_id") or ""
        if suffix == "reasoning":
            text = payload.get("text") or ""
            if not run_id or not text:
                return None
            return SSEEvent(
                type=EventType.RUN_REASONING_DELTA,
                payload={"run_id": run_id, "agent_id": agent_id, "delta": text},
                timestamp=ts,
                seq=seq,
            )
        if suffix == "content":
            text = payload.get("text") or ""
            if not run_id or not text:
                return None
            return SSEEvent(
                type=EventType.RUN_OUTPUT_DELTA,
                payload={"run_id": run_id, "agent_id": agent_id, "delta": text},
                timestamp=ts,
                seq=seq,
            )
        if suffix == "rework":
            if not run_id:
                return None
            # Journaled rework steps exist ONLY for 交付前核验回炉 (sink persists the
            # trace solely on reason=finish_guard), so the replayed reset says so.
            return SSEEvent(
                type=EventType.RUN_OUTPUT_RESET,
                payload={"run_id": run_id, "agent_id": agent_id, "reason": "finish_guard"},
                timestamp=ts,
                seq=seq,
            )
        return None

    if kind.startswith(_PROCESS_PREFIX):
        suffix = kind[len(_PROCESS_PREFIX) :]
        if suffix in _PROCESS_STRUCTURAL_SUFFIXES:
            return None
        if suffix == "reasoning":
            text = payload.get("text") or ""
            if not text:
                return None
            return SSEEvent(
                type=EventType.REASONING_DELTA,
                payload={"delta": text},
                timestamp=ts,
                seq=seq,
            )
        if suffix == "content":
            text = payload.get("text") or ""
            if not text:
                return None
            return SSEEvent(
                type=EventType.CONTENT_DELTA,
                payload={"delta": text},
                timestamp=ts,
                seq=seq,
            )
        if suffix == "rework":
            return SSEEvent(
                type=EventType.CONTENT_RESET,
                payload={"reason": "finish_guard"},
                timestamp=ts,
                seq=seq,
            )
        return None

    return None


def journal_rows_to_sse(rows: list[dict[str, Any]]) -> list[SSEEvent]:
    """Convert ``load_after`` rows into live-shaped SSE events (with ``seq`` on DURABLE).

    Process-lane facts are emitted as synthetic deltas **in journal order**, interleaved
    with tool/team DURABLE events so a client that reset on the segment head rebuilds the
    CEO / worker timelines with correct interleaving (process progressive persistence
    invariant).
    """
    final_outputs: dict[str, dict[str, str]] = {}
    agent_run_ids: dict[str, str] = {}
    for row in rows:
        kind = str(row.get("kind") or "")
        payload = dict(row.get("payload") or {})
        if kind == FactKind.MESSAGE_FINAL.value:
            run_id = payload.get("run_id")
            if run_id:
                final_outputs[str(run_id)] = {
                    "content": payload.get("content") or "",
                    "reasoning": payload.get("reasoning") or "",
                }
        elif kind == EventType.RUN_STARTED.value and payload.get("kind") == RunKind.AGENT.value:
            run_id = payload.get("run_id")
            if run_id:
                agent_run_ids[str(run_id)] = payload.get("agent_id") or ""

    out: list[SSEEvent] = []
    for row in rows:
        kind = str(row.get("kind") or "")
        payload = dict(row.get("payload") or {})
        ts = row.get("ts") or ""
        seq_raw = row.get("seq")
        seq_i = int(seq_raw) if seq_raw is not None else None

        if kind == FactKind.MESSAGE_FINAL.value:
            continue
        if kind in EXECUTION_ONLY_KINDS:
            continue

        # Progressive process lane — fold as deltas in order (skip structural mirrors).
        if kind.startswith(_PROCESS_PREFIX) or kind.startswith(_RUN_PROCESS_PREFIX):
            # Fill agent_id on run_process text steps when the payload omitted it.
            if kind.startswith(_RUN_PROCESS_PREFIX) and not payload.get("agent_id"):
                rid = payload.get("run_id")
                if rid and str(rid) in agent_run_ids:
                    payload = {**payload, "agent_id": agent_run_ids[str(rid)]}
            synthetic = _process_step_to_sse(kind, payload, seq=seq_i, ts=ts)
            if synthetic is not None:
                out.append(synthetic)
            continue

        if kind not in _DURABLE_KIND_VALUES:
            continue

        if kind in _RUN_TERMINAL:
            run_id = payload.get("run_id")
            final = final_outputs.get(str(run_id)) if run_id else None
            agent_id = None
            if run_id:
                agent_id = agent_run_ids.get(str(run_id)) or payload.get("agent_id") or None
            if final is not None and agent_id is not None and run_id:
                if final["reasoning"]:
                    out.append(
                        SSEEvent(
                            type=EventType.RUN_REASONING_DELTA,
                            payload={
                                "run_id": run_id,
                                "agent_id": agent_id,
                                "delta": final["reasoning"],
                            },
                            timestamp=ts,
                        )
                    )
                if final["content"]:
                    out.append(
                        SSEEvent(
                            type=EventType.RUN_OUTPUT_DELTA,
                            payload={
                                "run_id": run_id,
                                "agent_id": agent_id,
                                "delta": final["content"],
                            },
                            timestamp=ts,
                        )
                    )

        out.append(
            SSEEvent(
                type=EventType(kind),
                payload=payload,
                timestamp=ts,
                seq=seq_i,
            )
        )
    return out


def _journal_covers_captain_channels(rows: list[dict[str, Any]]) -> tuple[bool, bool]:
    """Whether journal process_* already carries captain content / reasoning text."""
    has_content = False
    has_reasoning = False
    for row in rows:
        kind = str(row.get("kind") or "")
        if kind == f"{_PROCESS_PREFIX}content":
            has_content = True
        elif kind == f"{_PROCESS_PREFIX}reasoning":
            has_reasoning = True
    return has_content, has_reasoning


def journal_is_structured(rows: list[dict[str, Any]]) -> bool:
    """True when the turn has (or will have) a process lane — not prose-only.

    Structured turns must not stitch CEO 旁白 from flat ``captain:content`` segments
    (journal ``process_*`` is the sole narration source). Prose-only turns keep the
    segment accelerate path.
    """
    for row in rows:
        kind = str(row.get("kind") or "")
        if kind.startswith(_PROCESS_PREFIX) or kind.startswith(_RUN_PROCESS_PREFIX):
            return True
        if kind in (
            EventType.TOOL_USE_START.value,
            EventType.TOOL_USE_END.value,
            EventType.RUN_PLAN.value,
            EventType.RUN_STARTED.value,
            EventType.CHECKPOINT_REQUIRED.value,
            EventType.QUESTION_POSTED.value,
            EventType.PLAN_REVIEW_REQUIRED.value,
            EventType.TEAM_PREVIEW_REQUIRED.value,
        ):
            return True
    return False


def _journal_covered_run_ids(rows: list[dict[str, Any]]) -> set[str]:
    """Run ids that already have run_process_* text steps in the journal."""
    covered: set[str] = set()
    for row in rows:
        kind = str(row.get("kind") or "")
        if not kind.startswith(_RUN_PROCESS_PREFIX):
            continue
        suffix = kind[len(_RUN_PROCESS_PREFIX) :]
        if suffix not in ("content", "reasoning"):
            continue
        rid = (row.get("payload") or {}).get("run_id")
        if rid:
            covered.add(str(rid))
    return covered


def _row_is_stamped(row: dict[str, Any]) -> bool:
    """Whether a replay of this row would carry an SSE ``id:`` line.

    That is the ONLY way a client can come to hold a given seq as its
    ``Last-Event-ID``: DURABLE facts keep their seq on the live stream, and
    ``process_*`` text steps keep theirs when a segment replays them. Everything else
    (``llm_call`` and the other execution-only kinds, ``message_final``, ``turn_end``,
    the structural ``process_*`` mirrors) produces no frame at all, so no client can
    ever have named it.
    """
    kind = str(row.get("kind") or "")
    if kind == FactKind.MESSAGE_FINAL.value or kind in EXECUTION_ONLY_KINDS:
        return False
    if kind.startswith(_PROCESS_PREFIX) or kind.startswith(_RUN_PROCESS_PREFIX):
        payload = dict(row.get("payload") or {})
        return _process_step_to_sse(kind, payload, seq=None, ts="") is not None
    return kind in _DURABLE_KIND_VALUES


def _incremental_verdict(
    rows: list[dict[str, Any]],
    *,
    after_seq: int,
    turn_id: str,
    cursor_turn_id: str | None,
) -> str | None:
    """``None`` = safe to ship only the post-cursor facts; else the reason to send全量.

    Incremental is a pure optimization on top of a path already proven correct, so the
    bar is「能证明这次对得上」, not「想不出反例」. Four conditions, all cheap (the rows
    are already in hand for the whole-turn judgements):

    1. **``after_seq >= 1``** — both clients send ``Last-Event-ID: 0`` when they have no
       cursor at all (``lastEventIds.get(id) ?? "0"``), so ``0`` cannot be read as「我有
       seq 0 之前的一切」. Seq 0 is one row anyway; refusing it costs nothing.
    2. **The cursor names THIS turn.** Seq is numbered per turn from 0 while the clients
       keep one cursor per CONVERSATION, so a cursor carried over from the previous turn
       lands on a seq this turn also has — 「命中本回合某个 stamped 行」cannot separate
       foreign from own, it only ever agreed with both. The turn id rides in the SSE
       ``id:`` itself (``<turn_id>:<seq>``) so the cursor answers the question directly;
       one with no turn at all (``cursor_unversioned`` — an old client, or a value minted
       before the format carried it) is exactly as untrustworthy.
    3. **The turn has no ``turn_end`` row** — a settled turn (finished / paused) has been
       through the wholesale ``TurnJournalRepository.record`` rewrite (delete-then-insert
       renumbers the turn from 0) or is about to be, and a resumed turn inherits that
       prefix, so a cursor minted before the rewrite may now name a different fact —
       within the SAME turn, which is why matching turn ids cannot stand in for this.
    4. **``after_seq`` names a fact this turn actually stamped** (:func:`_row_is_stamped`)
       — a cursor that matches no such row is stale (renumbered) or fabricated.

    Deliberately NOT built: a generation counter / journal-vs-cursor reconciliation, or
    any client-version branch (the id format negotiates itself). The fallback is the
    whole story, so「说不准就整段重发」is both the cheap and the correct answer.
    """
    if after_seq < 1:
        return "no_cursor"
    if cursor_turn_id is None:
        return "cursor_unversioned"
    if cursor_turn_id != turn_id:
        return "cursor_foreign_turn"
    stamped = False
    for row in rows:
        if str(row.get("kind") or "") == KIND_TURN_END:
            return "turn_settled"
        seq = row.get("seq")
        if seq is not None and int(seq) == after_seq:
            stamped = _row_is_stamped(row)
    if not stamped:
        return "cursor_unknown"
    return None


def _slice_after_cursor(events: list[SSEEvent], *, after_seq: int) -> list[SSEEvent]:
    """Keep the journal-derived events that land after ``after_seq``.

    A frame's position is its own ``seq`` when it has one. The ``message_final`` splices
    have none — they are the worker's full text spliced in front of the run's terminal
    fact — so they inherit the position of the frame they lead, and travel with it.
    """
    kept: list[SSEEvent] = []
    anchor: int | None = None
    for event in reversed(events):
        if event.seq is not None:
            anchor = event.seq
        if anchor is None or anchor > after_seq:
            kept.append(event)
    kept.reverse()
    return kept


def _text_channel(event: SSEEvent) -> str | None:
    """The text channel a delta grows, or ``None`` for non-text frames."""
    if event.type == EventType.CONTENT_DELTA:
        return "captain:content"
    if event.type == EventType.REASONING_DELTA:
        return "captain:reasoning"
    if event.type == EventType.RUN_OUTPUT_DELTA:
        return f"{event.payload.get('run_id') or ''}:content"
    if event.type == EventType.RUN_REASONING_DELTA:
        return f"{event.payload.get('run_id') or ''}:reasoning"
    return None


def _closes_text_channel(event: SSEEvent) -> str | None:
    """The channel a ``*_reset`` frame empties (its next delta starts a fresh block)."""
    if event.type == EventType.CONTENT_RESET:
        return "captain:content"
    if event.type == EventType.RUN_OUTPUT_RESET:
        return f"{event.payload.get('run_id') or ''}:content"
    return None


def _mark_block_replacements(events: list[SSEEvent]) -> list[SSEEvent]:
    """Flag the first post-cursor text frame per channel as a whole-block replacement.

    A journaled ``process_*`` step is written when it CLOSES, so the first one to land
    after the cursor is the step the client was watching when it dropped: live deltas
    carry no ``id:``, so the cursor sits at an earlier durable fact and the client's own
    tail block holds a prefix of this same step. Appending would stutter the text;
    ``replace`` swaps the block for its finished self. Every later frame on that channel
    is genuinely new (two text steps of one kind are never adjacent — a tool / marker /
    reset separates them, and it ships in this same段), so it stays a plain append.

    A ``*_reset`` empties its channel, so whatever follows it is a fresh block too.
    """
    settled: set[str] = set()
    out: list[SSEEvent] = []
    for event in events:
        closed = _closes_text_channel(event)
        if closed is not None:
            settled.add(closed)
            out.append(event)
            continue
        channel = _text_channel(event)
        if channel is None or channel in settled:
            out.append(event)
            continue
        settled.add(channel)
        out.append(
            SSEEvent(
                type=event.type,
                payload={**event.payload, "replace": True},
                timestamp=event.timestamp,
                seq=event.seq,
            )
        )
    return out


def synthesize_segment_deltas(
    *,
    by_channel: dict[str, str],
    agent_run_ids: dict[str, str],
    covered_run_ids: set[str],
    skip_captain_content: bool = False,
    skip_captain_reasoning: bool = False,
) -> list[SSEEvent]:
    """Single-block deltas from stream_state / memory (P1 overlay isomorphic).

    When journal already has ``process_*`` / ``run_process_*`` text, skip the matching
    flat channels so mid-run refresh does not duplicate or reorder narration.

    Every frame here is a WHOLE still-open block (the channel's text so far), never an
    increment, so all of them carry ``replace`` — an增量段 client may already hold part
    of that block from live deltas. In a全量段 the client has just reset, so replacing
    nothing and appending are the same fold; the flag describes the frame, not the段.
    """
    extra: list[SSEEvent] = []
    cap_reasoning = by_channel.get(CHANNEL_CAPTAIN_REASONING) or ""
    cap_content = by_channel.get(CHANNEL_CAPTAIN_CONTENT) or ""
    if cap_reasoning and not skip_captain_reasoning:
        extra.append(
            SSEEvent(
                type=EventType.REASONING_DELTA,
                payload={"delta": cap_reasoning, "replace": True},
            )
        )
    if cap_content and not skip_captain_content:
        extra.append(
            SSEEvent(
                type=EventType.CONTENT_DELTA,
                payload={"delta": cap_content, "replace": True},
            )
        )

    partial: dict[str, dict[str, str]] = {}
    for channel, text in by_channel.items():
        parsed = parse_run_channel(channel)
        if parsed is None or not text:
            continue
        run_id, kind = parsed
        slot = partial.setdefault(run_id, {"content": "", "reasoning": ""})
        if kind == "output":
            slot["content"] = text
        else:
            slot["reasoning"] = text

    for run_id, texts in partial.items():
        if run_id in covered_run_ids or run_id not in agent_run_ids:
            continue
        agent_id = agent_run_ids.get(run_id) or ""
        if texts.get("reasoning"):
            extra.append(
                SSEEvent(
                    type=EventType.RUN_REASONING_DELTA,
                    payload={
                        "run_id": run_id,
                        "agent_id": agent_id,
                        "delta": texts["reasoning"],
                        "replace": True,
                    },
                )
            )
        if texts.get("content"):
            extra.append(
                SSEEvent(
                    type=EventType.RUN_OUTPUT_DELTA,
                    payload={
                        "run_id": run_id,
                        "agent_id": agent_id,
                        "delta": texts["content"],
                        "replace": True,
                    },
                )
            )
    return extra


def replay_open_event(*, turn_id: str, conversation_id: str, full_replay: bool = True) -> SSEEvent:
    """Synthesize the ``message_start`` that opens, stamps + RESETS the replayed bubble.

    ``message_start`` is EPHEMERAL (never journaled, so :func:`journal_rows_to_sse`
    cannot produce it) yet it is the ONLY frame carrying the server assistant
    ``message_id`` — the key a durable interaction is claimed by (``POST …/messages/
    {id}/resume``). The no-cursor path replays it out of the sink's ``_history``; the
    journal cursor path had no source for it, and clients always send ``Last-Event-ID``,
    so an attaching client kept its client-side bubble id (or, cross-turn, the PREVIOUS
    turn's id). A turn that then stopped at ask_user / plan_review / team_preview painted
    no resume card at all (the durable-card surface refuses an unstamped bubble) or one
    bound to a stale id whose submit 404s — the user had to leave the conversation and
    come back.

    ``full_replay=True`` is the segment's own instruction: everything after this frame is
    the turn from its start, so the client resets what it holds for ``message_id``
    (streamed content / reasoning / process timeline) and folds the segment as the whole
    story. Without it the client had to GUESS from the id whether to clear — guessing
    wrong folded the body twice. A plain (unflagged) repeat of the same ``message_id``
    still means「同回合重开」and keeps the accumulated bubble — which is exactly what an
    增量段 head wants: the stamp still binds durable cards to this turn, and the absence
    of the flag is the server saying「你手里那半场是对的，往后接」.

    ``trace_id=""`` suppresses the factory's ambient fill: the turn's own trace is not in
    the journal projection, and stamping the *attach request's* trace would point the
    bubble's log link at this GET instead of the turn; the clients' truthy-guarded stamp
    then keeps whatever they already have.
    """
    return message_start(
        turn_id, conversation_id=conversation_id, trace_id="", full_replay=full_replay
    )


def mark_full_replay_segment(
    events: list[SSEEvent], *, turn_id: str | None, conversation_id: str
) -> list[SSEEvent]:
    """Put the ``full_replay`` instruction on the head of a sink-history replay segment.

    The cursor path mints its own head (:func:`replay_open_event`); the no-cursor path
    replays the turn's ORIGINAL live frames, whose ``message_start`` says nothing about
    being a replay. Rewrite that frame into a flagged COPY — history entries share their
    payload dict with the live event, so mutating in place would retro-flag the frame
    other端 are still streaming — and synthesize a head when the snapshot has none (the
    turn had not opened its bubble yet when this端 attached; the real ``message_start``
    arrives on the live tail right after, unflagged, as 同回合重开).

    Both replay paths therefore hand the client the same「先重置本回合本地态，再折本段」
    instruction. Two cases have nothing to stamp:

    - **An EMPTY segment.** ``full_replay`` orders the client to drop what it holds for
      this ``message_id``, and the段 is what replaces it — with nothing in the段 the
      client clears and never gets anything back. An empty ``history_snapshot()`` does
      NOT mean the client holds nothing: on the resume-settled join the continuation's
      sink was just created (empty history) while the client still holds the whole
      pre-pause transcript, and this path never consults the journal, so the wipe is
      unrecoverable. No frames to replay = no「本段是全量重放」to declare; the live
      ``message_start`` arrives right after on the tail, unflagged, as 同回合重开.
    - **``turn_id`` unset** — no bubble to address, so a segment for a turn the client
      cannot key anything to anyway.
    """
    if not events:
        return []
    out: list[SSEEvent] = []
    stamped = False
    for event in events:
        if not stamped and event.type == EventType.MESSAGE_START:
            out.append(
                SSEEvent(
                    type=EventType.MESSAGE_START,
                    payload={**event.payload, "full_replay": True},
                    timestamp=event.timestamp,
                    seq=event.seq,
                )
            )
            stamped = True
            continue
        out.append(event)
    if not stamped and turn_id:
        out.insert(0, replay_open_event(turn_id=turn_id, conversation_id=conversation_id))
    return out


def replay_close_event(
    finish_reason: FinishReason,
    *,
    outcome: str | None = None,
    team_batch: dict[str, Any] | None = None,
) -> SSEEvent:
    """The minimal ``message_end`` an attach segment closes with.

    Carries ``finish_reason`` (+ optional ``outcome`` / ``team_batch``).
    Usage / cost live on the Message columns a reload rehydrates; clients'
    undefined-guarded meta merge leaves any hydrated values intact.
    """
    payload: dict[str, Any] = {"finish_reason": finish_reason.value}
    if outcome in ("ok", "partial", "paused", "error"):
        payload["outcome"] = outcome
    if isinstance(team_batch, dict) and team_batch.get("kind"):
        payload["team_batch"] = team_batch
    return SSEEvent(type=EventType.MESSAGE_END, payload=payload)


def _turn_end_close_event(rows: list[dict[str, Any]]) -> SSEEvent | None:
    """Synthesize the stream-close ``message_end`` the attach replay otherwise lacks.

    ``message_end`` is DERIVED (never journaled, so :func:`journal_rows_to_sse` drops it)
    and a *detached* turn emits it while the sink is detached — it lands in neither
    ``_history`` nor the re-armed live queue. A client that attaches inside the turn's
    post-completion persist window (``task`` not yet done → the endpoint does not 204)
    therefore replays the durable journal, then the live tail closes immediately
    (sink already closed) with **no** close frame, and the client can only finalize via
    the reconnect-banner error salvage (spurious「重连中」+ bubble stuck streaming).

    When the journal carries ``turn_end`` (the turn is finished) replay a synthetic
    :func:`replay_close_event` so the client finalizes the bubble + turn phase normally —
    ``paused`` still routes to the durable resume card, other reasons complete the turn.
    Returns ``None`` when the turn is still running (no ``turn_end`` yet) so the live tail
    delivers the real ``message_end`` unchanged.
    """
    for row in reversed(rows):
        if str(row.get("kind") or "") != KIND_TURN_END:
            continue
        finish_raw = (row.get("payload") or {}).get("finish_reason")
        try:
            finish = FinishReason(finish_raw)
        except ValueError:
            finish = FinishReason.END_TURN
        raw_outcome = (row.get("payload") or {}).get("outcome")
        outcome = raw_outcome if raw_outcome in ("ok", "partial", "paused", "error") else None
        from agentcore.runtime.journal.team_batch import team_batch_from_entries

        return replay_close_event(finish, outcome=outcome, team_batch=team_batch_from_entries(rows))
    return None


async def build_cursor_replay(
    *,
    turn_id: str,
    conversation_id: str,
    after_seq: int,
    cursor_turn_id: str | None = None,
    memory_channels: dict[str, str],
    memory_agent_ids: dict[str, str],
) -> list[SSEEvent]:
    """Durable journal + in-flight segment synthesis, shipped全量 or 增量.

    Leads with the synthetic ``message_start`` (:func:`replay_open_event`) so the
    segment opens and STAMPS the bubble before any durable card lands on it —
    everything after it binds to ``turn_id``, the id a resume/approval submit uses.
    The head carries ``full_replay`` on the全量 path only; on the增量 path its absence
    is the instruction「别清，接着折」。

    ``after_seq`` / ``cursor_turn_id`` are the two halves of the client's
    ``Last-Event-ID`` (``<turn_id>:<seq>``). The seq never narrows the **read**: the
    whole turn is loaded and judged (structured-turn test, covered-run set, ``agent_id``
    backfill, ``message_final`` splices all need every row, and a ``seq > cursor`` query
    would silently flip them). It narrows only what is **shipped**, and only when
    :func:`_incremental_verdict` can vouch for the cursor — which starts with the cursor
    naming this very turn.
    """
    from agentcore.conversation.store import get_conversation_store
    from agentcore.core.logging import get_logger
    from agentcore.db.base import telemetry_session_factory
    from agentcore.db.repositories.runs import TurnJournalRepository

    async with telemetry_session_factory() as db:
        # Full turn from seq 0 (``seq > -1``) — the判定 needs every row.
        rows = await TurnJournalRepository(db).load_after(turn_id, -1)

    journal_events = journal_rows_to_sse(rows)
    skip_cap_content, skip_cap_reasoning = _journal_covers_captain_channels(rows)
    # Structured turns: never stitch 旁白 from flat segments (process_* is the source).
    # Prose-only keeps segment accelerate for captain content / reasoning.
    if journal_is_structured(rows):
        skip_cap_content = True
    process_covered_runs = _journal_covered_run_ids(rows)

    agent_ids = dict(memory_agent_ids)
    covered: set[str] = set(process_covered_runs)
    for ev in journal_events:
        if ev.type == EventType.RUN_STARTED and ev.payload.get("kind") == RunKind.AGENT.value:
            rid = ev.payload.get("run_id")
            if rid:
                agent_ids.setdefault(str(rid), ev.payload.get("agent_id") or "")
        if ev.type in (EventType.RUN_OUTPUT_DELTA, EventType.RUN_REASONING_DELTA):
            rid = ev.payload.get("run_id")
            if rid:
                covered.add(str(rid))

    # Judgements above saw the whole turn; only shipping is narrowed from here.
    skip_reason = _incremental_verdict(
        rows, after_seq=after_seq, turn_id=turn_id, cursor_turn_id=cursor_turn_id
    )
    incremental = skip_reason is None
    if incremental:
        journal_events = _mark_block_replacements(
            _slice_after_cursor(journal_events, after_seq=after_seq)
        )
    get_logger(__name__).debug(
        "attach.cursor_replay",
        turn_id=turn_id,
        cursor_turn_id=cursor_turn_id,
        last_event_id=after_seq,
        journal_rows=len(rows),
        incremental=incremental,
        full_replay_reason=skip_reason,
        shipped_events=len(journal_events),
    )

    events = [
        replay_open_event(
            turn_id=turn_id, conversation_id=conversation_id, full_replay=not incremental
        )
    ]
    events.extend(journal_events)

    by_channel = dict(memory_channels)
    if not by_channel:
        store = get_conversation_store()
        segments = await store.list_stream_segments(turn_id=turn_id)
        by_channel = {
            str(s["channel"]): str(s.get("text") or "")
            for s in segments
            if s.get("channel") and s.get("text")
        }

    events.extend(
        synthesize_segment_deltas(
            by_channel=by_channel,
            agent_run_ids=agent_ids,
            covered_run_ids=covered,
            skip_captain_content=skip_cap_content,
            skip_captain_reasoning=skip_cap_reasoning,
        )
    )
    # Close a finished detached turn so a client attaching in the persist window
    # finalizes normally instead of via the reconnect-banner salvage (收口事实回放).
    close = _turn_end_close_event(rows)
    if close is not None:
        events.append(close)
    return events
