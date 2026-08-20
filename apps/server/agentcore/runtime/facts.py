"""Execution-level Turn Journal facts (§8.3) — the schema + the engine's write port.

The §8.3 Turn Journal is a turn's 唯一事实源: an append-only, per-turn ordered
stream of facts from which everything replayable / resumable is a projection. The
conceptual model is fact kinds including::

    turn_started | run_head | round_boundary | llm_call | tool_call | interaction
                 | note | run_event | message_final | turn_end

Today the journal is **display-level**: it is derived from the SSE stream
(``events._JOURNAL_EVENT_TYPES``), so it carries the team graph / tool cards /
interaction cards (the ``run_event`` / ``tool_call`` / ``interaction`` umbrellas,
stored under their SSE *event-type* kind) + a closing ``turn_end``. That is enough
to **show** a past turn but NOT to **rebuild the engine** (the LLM window, the pause
frame): the captain transcript never enters the stream, ``run_completed`` carries
only a summary, and the system prompt / injected nudges are not facts. Resume bridges
the gap with the旁路 ``paused_turns.frame``.

This module owns the **execution-level facts** that close that gap (the new
kinds), making the journal lossless so the window / frame become projections of it
(执行级事件溯源；as-built 见执行引擎 §8.3):

- :class:`TurnStartedFact` — the turn's head: the *verbatim* system prompt, the user
  message, the model profile. Anchors the **captain** window fold (the system prompt is
  dynamic — date / skill directory — so it is captured, never re-rendered).
- :class:`RunHeadFact` — one worker (or continuation) run's opening task-prompt head:
  its *verbatim* system + opening user message. Anchors that run's window fold so a
  worker is never falsely headed by the turn-level ``turn_started`` (CEO) prompt.
- :class:`RoundBoundaryFact` — one ReAct round edge (round_idx + run/role), the key
  ``round_boundary.fold`` cuts on to rebuild the pause snapshot per round.
- :class:`LlmCallFact` — one LLM call's **output** (content / reasoning_content /
  tool_calls / usage / finish_reason). Execution-保真 core: the call's *input* is
  never stored — it is the fold of all prior facts (correct-by-construction, no
  quadratic window duplication).
- :class:`ToolCallFact` — one completed tool call's **full model-facing result** (the
  text fed back into the window), captured AFTER any post-emit annotation (the CEO
  path folds citation numbers into the tool message after ``tool_use_end`` fires —
  Phase 2 边界①). The window fold reads tool results from THIS fact, not the forwarded
  display ``tool_use_end`` (whose journaled ``result`` is the pre-annotation text,
  capped to the process-lane 8k budget). Carries
  ``run_id`` so a multi-agent turn's tools scope per run.
- :class:`NoteFact` — an engine-injected message (a convergence NUDGE reflection, the
  FINALIZE instruction): part of the real LLM window, so the fold needs it. Carries
  ``run_id`` (Phase 2 边界②) so a captain note injected mid-delegate (while a worker is
  the active run) is still attributed to the captain window.
- :class:`MessageFinalFact` — a run's / the turn's **full** output text (vs the
  ``run_completed`` summary), so resume feeds a worker's product back from facts
  rather than from the frame.
- :class:`TurnPausedFact` — the resumable turn-state snapshot at a durable pause
  (display + control state: process / run_processes / citations / controller /
  deliverable content / reasoning). Resume and salvage read the LAST one via
  :func:`pre_pause_from_journal`.
- ``plan_snapshot`` — a delegate's full DAG (``plan_to_json``: every :class:`RunSpec`
  with its minted run_id + accumulated ``steer`` + policy/contract), recorded at plan
  build and after each ``adjust`` steer. Resume folds the LAST one (``plan_from_journal``)
  to re-drive the unfinished tail, so ``paused_turns.frame`` need not carry the plan
  (执行级事件溯源 Phase 2, the ``frame.plan`` exit). Built by the
  ``runs.serialize.plan_snapshot_fact`` helper (which owns ``plan_to_json``) rather than a
  dataclass here — like ``run_final_fact`` for the worker ``message_final`` — so this
  module stays free of the runs-package import.

The remaining three kinds are sourced elsewhere: ``run_event`` / ``interaction`` keep
riding their SSE event-type entries (from the sink — incl. the display ``tool_use_start``
/ ``tool_use_end`` pair, which stays for the team-graph tool card), and ``turn_end``
stays in :mod:`agentcore.runtime.journal` (``KIND_TURN_END``). The display projection
(``runs_from_entries``) therefore must simply *ignore* the execution kinds
(:data:`EXECUTION_ONLY_KINDS`) so adding them never disturbs replay.

Pure schema + an in-memory recorder here: stdlib only, no DB, no engine import. The
durable side is the §8.6 ``Journal`` port (``db.repositories.TurnJournalRepository``);
a turn's :class:`TurnFactLog` is flattened to journal entries and persisted there at
turn end (and re-projected on read), exactly like the display journal today.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Protocol, runtime_checkable


class FactKind(StrEnum):
    """Execution-level fact kinds this module produces (§8.3).

    These are NEW kinds (no rename of the existing display entries, which keep their
    SSE event-type kind — zero migration). The umbrella ``run_event`` / ``interaction``
    and the closing ``turn_end`` are not listed here: they are sourced elsewhere (the
    sink / ``journal.KIND_TURN_END``). ``TOOL_CALL`` IS listed (the execution fact
    carrying the full result the window folds); the display tool card still rides the
    sink's ``tool_use_start`` / ``tool_use_end`` pair, which keep their SSE kind.
    ``PLAN_SNAPSHOT`` (the delegate's full DAG, the resume seed for ``frame.plan``) uses
    a value DISTINCT from the display ``run_plan`` event so the display projection's
    surface gate is unaffected. ``COORDINATION_SNAPSHOT`` carries CEO 协调模式 Phase 2
    draft / budget for ask_user resume.
    """

    TURN_STARTED = "turn_started"
    RUN_HEAD = "run_head"
    ROUND_BOUNDARY = "round_boundary"
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    NOTE = "note"
    MESSAGE_FINAL = "message_final"
    PLAN_SNAPSHOT = "plan_snapshot"
    # CEO 协调模式 Phase 2: draft / completed / budget for ask_user resume.
    COORDINATION_SNAPSHOT = "coordination_snapshot"
    # P1a 建站风格双闸：ask_user resume / full_auto 默认确认后的结构化 style_id。
    WEBSITE_STYLE_CONFIRMED = "website_style_confirmed"
    # 演讲/PPT 交付形态双闸：ask_user resume / full_auto 默认确认后的结构化 format_id。
    PRESENTATION_FORMAT_CONFIRMED = "presentation_format_confirmed"
    # Agent/自动化开工形态双闸：ask_user resume / full_auto 默认确认后的结构化 format_id。
    AUTOMATION_DELIVERY_CONFIRMED = "automation_delivery_confirmed"
    # 回合态挂起归宿 (P0): the resumable turn-state snapshot recorded at a durable
    # pause — see :class:`TurnPausedFact`.
    TURN_PAUSED = "turn_paused"


# The execution-only kinds the DISPLAY projection (runs_from_entries) must skip: they
# carry engine-rebuild state (window / frame), never client-foldable display events,
# so they must not leak into the projected ``runs.events`` (the client fold would
# choke on an unknown event type). The frozen string values match the table's stored
# ``kind`` column.
EXECUTION_ONLY_KINDS: frozenset[str] = frozenset(k.value for k in FactKind)


@dataclass(frozen=True, slots=True)
class Fact:
    """One journal fact: ``{kind, payload, ts}`` — the unit the recorder accumulates.

    ``ts`` is optional (the table's ``seq`` is the authoritative order; an execution
    fact mirrors the existing process facts in leaving it ``None`` unless a caller
    stamps a time for debugging / time-travel). :meth:`entry` yields the plain dict
    the §8.6 ``Journal`` port persists, identical in shape to the display entries.
    """

    kind: str
    payload: dict[str, Any]
    ts: str | None = None

    def entry(self) -> dict[str, Any]:
        return {"kind": self.kind, "payload": self.payload, "ts": self.ts}


@dataclass(frozen=True, slots=True)
class TurnStartedFact:
    """The turn's head fact — the **captain** window fold's anchor.

    ``system_prompt`` is captured *verbatim* (it is dynamic — date / skill directory —
    so re-rendering it on resume could drift). ``history_len`` is the number of prior
    conversation messages folded into the opening window (the history itself is a
    projection of earlier turns, not duplicated here). Worker windows use
    :class:`RunHeadFact` instead — never this turn-level head.
    """

    system_prompt: str
    user_message: str
    model_profile: str
    history_len: int = 0
    kind: ClassVar[FactKind] = FactKind.TURN_STARTED

    def to_fact(self, ts: str | None = None) -> Fact:
        return Fact(
            kind=self.kind.value,
            payload={
                "system_prompt": self.system_prompt,
                "user_message": self.user_message,
                "model_profile": self.model_profile,
                "history_len": self.history_len,
            },
            ts=ts,
        )


@dataclass(frozen=True, slots=True)
class RunHeadFact:
    """One run's opening task-prompt head — the worker / continuation window anchor.

    Captures the *verbatim* ``system`` + opening ``user`` the executor built for this
    ``run_id`` (cold-start from ContextBlocks, or a续写 beat's feedback wrapper). The
    window fold prefers this over ``turn_started`` whenever present, so a worker's
    diagnostic LLM window is never headed by the CEO turn prompt.

    ``user_origin`` tags the opening user for the diagnostic wire (e.g.
    ``context_blocks`` when that message was rendered from the structured
    ``run_context`` block list) — the UI replaces the concatenated body with those
    segments and offers「查看原始拼接」for the full text stored here.
    """

    run_id: str
    system_prompt: str
    user_message: str
    user_origin: str = "context_blocks"
    kind: ClassVar[FactKind] = FactKind.RUN_HEAD

    def to_fact(self, ts: str | None = None) -> Fact:
        return Fact(
            kind=self.kind.value,
            payload={
                "run_id": self.run_id,
                "system_prompt": self.system_prompt,
                "user_message": self.user_message,
                "user_origin": self.user_origin,
            },
            ts=ts,
        )


@dataclass(frozen=True, slots=True)
class RoundBoundaryFact:
    """One ReAct round edge — what ``round_boundary.fold`` cuts the window on.

    ``run_id`` + ``role`` (captain / worker) scope the round so a multi-agent turn's
    rounds split per run; ``round_idx`` is 0-based within that run.
    """

    round_idx: int
    run_id: str
    role: str
    kind: ClassVar[FactKind] = FactKind.ROUND_BOUNDARY

    def to_fact(self, ts: str | None = None) -> Fact:
        return Fact(
            kind=self.kind.value,
            payload={
                "round_idx": self.round_idx,
                "run_id": self.run_id,
                "role": self.role,
            },
            ts=ts,
        )


@dataclass(frozen=True, slots=True)
class LlmCallFact:
    """One LLM call's OUTPUT — the execution-保真 core.

    Only the output is stored; the input window is the fold of all prior facts (no
    quadratic duplication). ``reasoning_content`` is kept because DeepSeek thinking
    mode requires it echoed back on any assistant turn carrying ``tool_calls`` — the
    window fold must reproduce it byte-for-byte or a resumed request 400s (see
    平台LLM接入 · DeepSeek 易错). ``tool_calls`` / ``usage`` are the already-serialized dict forms (this
    module stays free of the llm.protocol types).
    """

    run_id: str
    round_idx: int
    content: str = ""
    reasoning_content: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None
    kind: ClassVar[FactKind] = FactKind.LLM_CALL

    def to_fact(self, ts: str | None = None) -> Fact:
        return Fact(
            kind=self.kind.value,
            payload={
                "run_id": self.run_id,
                "round_idx": self.round_idx,
                "content": self.content,
                "reasoning_content": self.reasoning_content,
                "tool_calls": list(self.tool_calls) if self.tool_calls else [],
                "usage": dict(self.usage) if self.usage else {},
                "finish_reason": self.finish_reason,
            },
            ts=ts,
        )


class CrossTurnRetry(StrEnum):
    """Whether repeating the same action on a later turn is futile.

    Answers: 跨回合同一动作还值不值得再试. Distinct from loop-controller
    ``error_class``, which answers: 本轮还该不该继续用这个工具/这条路. Do not
    merge the two — e.g. ``liveness_timeout`` is in-turn ``permanent`` (stop
    using this tool this round) but cross-turn ``not_futile`` (the next turn
    may succeed).

    Three-state: ``futile`` / ``not_futile`` / omitted. Unknown must be omitted
    — never default, never guess. This field is a recorded fact, not a gate.
    """

    FUTILE = "futile"
    NOT_FUTILE = "not_futile"


CROSS_TURN_RETRY_KEY = "cross_turn_retry"


def normalize_cross_turn_retry(raw: object) -> str:
    """Known values only; anything else is unknown (empty → omit)."""
    if isinstance(raw, CrossTurnRetry):
        text = raw.value
    elif isinstance(raw, str):
        text = raw.strip()
    else:
        text = ""
    if text in {CrossTurnRetry.FUTILE.value, CrossTurnRetry.NOT_FUTILE.value}:
        return text
    return ""


def cross_turn_retry_meta(value: CrossTurnRetry) -> dict[str, str]:
    """Stamp for ``ToolResult.metadata`` / ``ToolAttempt.meta`` — never infers."""
    return {CROSS_TURN_RETRY_KEY: value.value}


@dataclass(frozen=True, slots=True)
class ToolCallFact:
    """One completed tool call's FULL model-facing result — the window's tool message.

    The window fold reads tool results from this fact, NOT the forwarded display
    ``tool_use_end`` (执行级事件溯源 §8.3 投影边界①): on the CEO chat path the engine
    folds citation numbers into the tool message AFTER emitting ``tool_use_end``, so the
    event's ``result`` is the pre-annotation text while the model actually saw the
    annotated one (journaled display ``result`` is further capped to the process-lane
    8k budget). Recorded after that annotation, so ``result`` is byte-for-byte what
    the next round's window carried. ``run_id`` scopes a multi-agent turn's tools per
    run; ``tool_call_id`` pairs it to the issuing ``llm_call``'s ``tool_calls`` entry.
    NOT recorded for a SUSPENDED call (``ask_user`` / ``delegate`` blocks inside
    ``execute`` before this point) — a missing fact is the window's "result still
    pending" signal, exactly as a missing ``tool_use_end`` was.
    """

    run_id: str
    tool_call_id: str
    name: str = ""
    arguments: str = ""
    result: str = ""
    success: bool = True
    # Coarse failure code for local-turn write-back stats (omitted when empty).
    code: str = ""
    # Cross-turn: same-action retry worth it? See :class:`CrossTurnRetry`.
    # Orthogonal to ``error_class`` (in-turn breaker). Empty = unknown; omit.
    cross_turn_retry: str = ""
    kind: ClassVar[FactKind] = FactKind.TOOL_CALL

    def to_fact(self, ts: str | None = None) -> Fact:
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "arguments": self.arguments,
            "result": self.result,
            "success": self.success,
        }
        # Old journals omit ``code``; only write when non-empty.
        code = (self.code or "").strip()
        if code:
            payload["code"] = code
        retry = normalize_cross_turn_retry(self.cross_turn_retry)
        if retry:
            payload[CROSS_TURN_RETRY_KEY] = retry
        return Fact(
            kind=self.kind.value,
            payload=payload,
            ts=ts,
        )


@dataclass(frozen=True, slots=True)
class NoteFact:
    """An engine-injected message that is part of the real LLM window.

    Convergence governance appends a ``user``-role NUDGE reflection / the FINALIZE
    instruction into the loop's ``messages``; these are not model output nor a tool
    result, so without a fact the window fold would miss them. ``reason`` tags the
    source (``nudge`` / ``finalize`` / …) for time-travel readability. ``run_id`` scopes
    the note to its run (执行级事件溯源 §8.3 投影边界②): a captain note injected while a
    delegated worker is the active run must still fold into the CAPTAIN window, so the
    fold attributes by this id rather than by "the most-recent round_boundary".
    """

    role: str
    content: str
    reason: str = ""
    run_id: str = ""
    kind: ClassVar[FactKind] = FactKind.NOTE

    def to_fact(self, ts: str | None = None) -> Fact:
        return Fact(
            kind=self.kind.value,
            payload={
                "role": self.role,
                "content": self.content,
                "reason": self.reason,
                "run_id": self.run_id,
            },
            ts=ts,
        )


@dataclass(frozen=True, slots=True)
class MessageFinalFact:
    """A run's / the turn's FULL output text (vs the ``run_completed`` summary).

    The authoritative full product, so resume feeds a worker's output back from facts
    (replacing the frame's ``completed`` text) and the captain's reply is reconstructable
    from the journal alone. Execution-only — it is NOT streamed (the live worker text
    rides the transport-only ``run_output_delta``); display keeps using the summary.
    """

    run_id: str
    content: str = ""
    reasoning: str = ""
    kind: ClassVar[FactKind] = FactKind.MESSAGE_FINAL

    def to_fact(self, ts: str | None = None) -> Fact:
        return Fact(
            kind=self.kind.value,
            payload={
                "run_id": self.run_id,
                "content": self.content,
                "reasoning": self.reasoning,
            },
            ts=ts,
        )


@dataclass(frozen=True, slots=True)
class TurnPausedFact:
    """Resumable turn-state snapshot recorded at a durable pause.

    The unique fact-source for display + control state across suspend/resume
    (process timeline, citations, LoopController latches, deliverable content /
    reasoning). Multi-cycle pauses append a new fact each time; readers take the
    LAST one (:func:`pre_pause_from_journal`). Contract: 执行引擎架构设计.md §8.3
    「回合态暂停快照」.
    """

    checkpoint_id: str
    suspension_kind: str
    content: str = ""
    reasoning: str = ""
    process: list[dict[str, Any]] | None = None
    run_processes: dict[str, list[dict[str, Any]]] | None = None
    citations: list[dict[str, Any]] | None = None
    # 回合共享调研台账快照（引用即出处 P1 §七 / §十第 3 步提前）：resume 再水化同一内容。
    evidence_ledger: list[dict[str, Any]] | None = None
    controller: dict[str, Any] | None = None
    # P1a 建站风格确认快照（style_id/label/source）；resume 再水化进 conversation ledger。
    website_style: dict[str, Any] | None = None
    # 演讲/PPT 交付形态确认快照（format_id/label/source）；resume 再水化进 conversation ledger。
    presentation_format: dict[str, Any] | None = None
    # Agent/自动化开工形态确认快照（format_id/label/source）；resume 再水化进 conversation ledger。
    automation_delivery: dict[str, Any] | None = None
    # Optional adjuncts that ride the same fact (e.g. demo-tape frame cursor).
    # Unknown to live faces; readers tolerate absence.
    extras: dict[str, Any] | None = None
    kind: ClassVar[FactKind] = FactKind.TURN_PAUSED

    def to_fact(self, ts: str | None = None) -> Fact:
        payload: dict[str, Any] = {
            "checkpoint_id": self.checkpoint_id,
            "suspension_kind": self.suspension_kind,
            "content": self.content,
            "reasoning": self.reasoning,
            "process": list(self.process) if self.process else [],
            "run_processes": {
                rid: list(steps) for rid, steps in (self.run_processes or {}).items()
            },
            "citations": list(self.citations) if self.citations else [],
            "evidence_ledger": list(self.evidence_ledger) if self.evidence_ledger else [],
            "controller": dict(self.controller) if self.controller else {},
        }
        if self.website_style:
            payload["website_style"] = dict(self.website_style)
        if self.presentation_format:
            payload["presentation_format"] = dict(self.presentation_format)
        if self.automation_delivery:
            payload["automation_delivery"] = dict(self.automation_delivery)
        if self.extras:
            payload["extras"] = dict(self.extras)
        return Fact(
            kind=self.kind.value,
            payload=payload,
            ts=ts,
        )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> TurnPausedFact:
        """Rebuild from a persisted journal payload (tolerant of missing keys)."""
        process = payload.get("process")
        run_processes = payload.get("run_processes")
        citations = payload.get("citations")
        evidence_ledger = payload.get("evidence_ledger")
        controller = payload.get("controller")
        website_style = payload.get("website_style")
        presentation_format = payload.get("presentation_format")
        automation_delivery = payload.get("automation_delivery")
        extras = payload.get("extras")
        return cls(
            checkpoint_id=str(payload.get("checkpoint_id") or ""),
            suspension_kind=str(payload.get("suspension_kind") or ""),
            content=str(payload.get("content") or ""),
            reasoning=str(payload.get("reasoning") or ""),
            process=list(process) if isinstance(process, list) else [],
            run_processes=(
                {str(k): list(v) if isinstance(v, list) else [] for k, v in run_processes.items()}
                if isinstance(run_processes, dict)
                else {}
            ),
            citations=list(citations) if isinstance(citations, list) else [],
            evidence_ledger=(
                list(evidence_ledger) if isinstance(evidence_ledger, list) else []
            ),
            controller=dict(controller) if isinstance(controller, dict) else {},
            website_style=(
                dict(website_style) if isinstance(website_style, dict) else None
            ),
            presentation_format=(
                dict(presentation_format)
                if isinstance(presentation_format, dict)
                else None
            ),
            automation_delivery=(
                dict(automation_delivery)
                if isinstance(automation_delivery, dict)
                else None
            ),
            extras=dict(extras) if isinstance(extras, dict) else None,
        )


@runtime_checkable
class FactRecorder(Protocol):
    """The engine-facing write side of the §8.3 Journal (执行级落地 §4).

    The engine records execution facts as they happen through this port instead of
    deriving them from the SSE sink. Phase 1 impl is the in-memory :class:`TurnFactLog`
    (flushed to the durable §8.6 ``Journal`` at turn end); a Sidecar could supply a
    write-through one without touching the engine.
    """

    def record_fact(self, fact: Fact) -> None: ...


class TurnFactLog:
    """In-memory, per-turn ordered fact accumulator (the default :class:`FactRecorder`).

    Append-only in emission order (insertion order == the journal ``seq``). During a
    turn each fact is also durably appended via :class:`~agentcore.runtime.journal.writer.TurnJournalWriter`
    (emit-on-write); this log is the in-process read cache. Process / run_process steps
    are journaled progressively at semantic boundaries; at turn end only still-open
    trailing steps + ``turn_end`` are appended (no full process dump).
    """

    def __init__(self, inherited_entries: list[dict] | None = None) -> None:
        self._inherited: list[dict] = list(inherited_entries) if inherited_entries else []
        self._facts: list[Fact] = []

    def record_fact(self, fact: Fact) -> None:
        self._facts.append(fact)

    def seed_from_entries(self, entries: list[dict[str, Any]]) -> None:
        """Pre-load a persisted §8.3 journal stream into the segment (legacy/test helper).

        Unlike ``inherited_entries``, seeded entries live in ``_facts`` and are re-written
        by :class:`~agentcore.runtime.journal.writer.TurnJournalWriter` unless
        ``initial_seq`` skips them. Prefer ``inherited_entries`` on resume.
        """
        for entry in entries:
            kind = entry.get("kind")
            if not kind:
                continue
            self.record_fact(
                Fact(kind=kind, payload=entry.get("payload", {}), ts=entry.get("ts"))
            )

    def entries(self) -> list[dict[str, Any]]:
        """The full journal stream: inherited prefix + segment facts."""
        return [*self._inherited, *[f.entry() for f in self._facts]]

    def segment_entries(self) -> list[dict[str, Any]]:
        """Facts recorded in this segment only (excludes the inherited prefix)."""
        return [f.entry() for f in self._facts]

    def __len__(self) -> int:
        return len(self._facts)

    def __bool__(self) -> bool:
        return bool(self._facts)


# The turn's ambient fact log. The pipeline binds a fresh :class:`TurnFactLog` here at
# the start of a turn; the engine / executor / sink record into it via
# :func:`record_turn_fact` WITHOUT threading a recorder through every signature. It is
# task-local and copied into each delegated worker's task on creation, so the captain
# loop and every worker append to the SAME ordered log (single source per turn). Reset
# at turn end. ``None`` outside a turn (standalone engine calls, tests) → recording is
# a no-op, so the engine's behavior is unchanged when no log is bound.
current_fact_log: ContextVar[TurnFactLog | None] = ContextVar("current_fact_log", default=None)


# (retired) 跨回合同图 divert 曾把 §8.3 rebuild facts 续写宿主 journal；现已拆除。


def _settlement_key_in_fact_log(
    log: TurnFactLog, turn_id: str, entry: dict[str, Any]
) -> bool:
    """True when ``entry``'s settlement dedupe key already appears in ``log``."""
    from agentcore.runtime.journal.pending_interactions import settlement_dedupe_key

    key = settlement_dedupe_key(
        turn_id, str(entry.get("kind") or ""), dict(entry.get("payload") or {})
    )
    if key is None:
        return False
    for existing in log.entries():
        ek = settlement_dedupe_key(
            turn_id,
            str(existing.get("kind") or ""),
            dict(existing.get("payload") or {}),
        )
        if ek == key:
            return True
    return False


def _resolved_seq_future(seq: int | None) -> asyncio.Future[int | None] | None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    future: asyncio.Future[int | None] = loop.create_future()
    future.set_result(seq)
    return future


def record_turn_fact(fact: Fact) -> asyncio.Future[int | None] | None:
    """Append ``fact`` to the turn's ambient log and durable journal (no-op if unbound).

    The engine-facing convenience over the :class:`FactRecorder` port: callers build a
    typed fact (``RoundBoundaryFact(...).to_fact()``) and hand it here; whether a log is
    bound is the turn's concern, not the call site's. When a
    :class:`~agentcore.runtime.journal.writer.TurnJournalWriter` is bound, schedules a
    durable append and returns a Future that resolves to the journal seq (SSE barrier
    stamps ``id:`` from it before delivery).

    D8 settlement re-emit: when the writer would skip the durable write, do **not**
    append a second row to ``TurnFactLog`` if that settlement key is already present
    (cold resume: prewrite + claim put ``*_resolved`` in the inherited prefix). A
    phantom log row drifts ``fact_log`` index vs DB ``seq``; finalize's enumerate
    then inserts a duplicate trailing ``process_content``. Hot-path awaiter after
    ``prewrite_settlement`` (which bypasses the fact log) still records once so the
    log catches up with the durable row.
    """
    from agentcore.runtime.journal.writer import (
        current_journal_writer,
        is_seal_overflow_kind,
    )

    entry = fact.entry()
    log = current_fact_log.get()
    writer = current_journal_writer.get()

    if writer is not None and writer.would_dedupe_settlement(entry):
        if log is not None and not _settlement_key_in_fact_log(log, writer.turn_id, entry):
            log.record_fact(fact)
        return _resolved_seq_future(None)

    if writer is not None and writer.sealed:
        # Overflow kinds that cannot be queued must stay out of the fact log so
        # ``coordination.terminal_unsettled`` still sees the durable gap.
        future = writer.schedule_append(entry)
        if log is not None and (
            future is not None or not is_seal_overflow_kind(str(entry.get("kind") or ""))
        ):
            log.record_fact(fact)
        return future

    if log is not None:
        log.record_fact(fact)
    if writer is not None:
        return writer.schedule_append(entry)
    return None


def snapshot_fact_log(
    trailing: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Snapshot the ambient fact log's entries at a pause (+ optional trailing entries).

    The suspending faces (``ask_user`` / ``delegate``) persist the journal-AT-PAUSE to
    the §8.3 turn_journal so a resume can rebuild the window from it. That journal is
    exactly this ambient single ordered log — EXCEPT the suspending display event
    (``checkpoint_required`` / ``plan_review_required``) is emitted only AFTER the frame
    is saved (in the registry's ``on_suspended``), so it is not yet in the log; the face
    passes it as ``trailing`` so the persisted stream still carries the card for the
    reload display (parity with the display ``journal`` the face also builds). Returns a
    fresh list. When no log is bound, returns ``trailing`` alone (or ``[]``) so capture
    callers that only need the pause-trailing facts (e.g. event-source replay) still
    persist a resumable ``turn_paused``.
    """
    log = current_fact_log.get()
    if log is None:
        return list(trailing) if trailing else []
    entries = log.entries()
    if trailing:
        entries.extend(trailing)
    return entries


def pre_pause_from_journal(entries: list[dict[str, Any]] | None) -> TurnPausedFact | None:
    """Return the last ``turn_paused`` snapshot from a journal stream, or ``None``.

    Unified read entry for resume rehydration and sidecar / cloud salvage: takes a
    plain entries list and does not depend on runtime objects. Old journals without
    this kind yield ``None`` (callers keep legacy heuristics).
    """
    if not entries:
        return None
    last: dict[str, Any] | None = None
    for entry in entries:
        if (entry.get("kind") or "") != FactKind.TURN_PAUSED.value:
            continue
        payload = entry.get("payload")
        if isinstance(payload, dict):
            last = payload
    if last is None:
        return None
    return TurnPausedFact.from_payload(last)
