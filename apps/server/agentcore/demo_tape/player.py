"""Play a demo tape through a live EventSink (dev-only)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import new_id
from agentcore.demo_tape.binding import (
    TapeBinding,
    advance_after_act_complete,
)
from agentcore.demo_tape.export import load_tape, tape_turns
from agentcore.demo_tape.identity import replay_interaction_id
from agentcore.demo_tape.pacing import pacing_step, sleep_ms_for_gap
from agentcore.demo_tape.schema import (
    DEMO_TAPE_FRAME_KEY,
    PAUSE_REQUIRED_KINDS,
    PAUSE_RESOLVED_KINDS,
    TAPE_HOT_PAUSE_KINDS,
    TAPE_WIRED_PAUSE_KINDS,
    event_timestamp,
    event_type,
    is_demo_tape_frame,
    tape_frame_meta,
)
from agentcore.demo_tape.transport import PlaybackTransport, transport_registry
from agentcore.llm.provider.protocol import LLMMessage
from agentcore.replay import (
    ConsumerKind,
    assert_sink_consumer,
    prepare_replay_source,
)
from agentcore.runtime.approvals import ApprovalDecision
from agentcore.runtime.checkpoints import CheckpointDecision, CheckpointResponse
from agentcore.runtime.citations import extract_ledger_ref_ids, project_cited_citations
from agentcore.runtime.engine.segments import join_segments
from agentcore.runtime.events import (
    EventSink,
    EventType,
    FinishReason,
    approval_required,
    approval_resolved,
    checkpoint_required,
    checkpoint_resolved,
    message_end,
    message_start,
    plan_review_required,
    plan_review_resolved,
    team_preview_required,
    team_preview_resolved,
)
from agentcore.runtime.events.types import SSEEvent
from agentcore.runtime.facts import TurnFactLog, current_fact_log, pre_pause_from_journal
from agentcore.runtime.interaction import InteractionKind, default_interaction_registry
from agentcore.runtime.journal.entries import journal_entries_from_display_runs
from agentcore.runtime.journal.writer import TurnJournalWriter, current_journal_writer
from agentcore.runtime.pipeline.finalize import _build_runs_payload
from agentcore.runtime.pipeline.resume.rehydrate import (
    arm_content_reset_reinjection,
    bootstrap_resume_display,
)
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.suspension import (
    AskUserSuspension,
    PlanReviewSuspension,
    TeamPreviewSuspension,
    TurnSuspension,
    captain_transcript,
)
from agentcore.runtime.suspension.capture import SuspensionPersistError, persist_suspension_capture
from agentcore.runtime.suspension.persistence import save_paused_turn

# Patchable pacing wait — tests must NOT monkeypatch ``asyncio.sleep`` itself.
# StreamCheckpointer's flush loop also sleeps on the stdlib alias; a process-wide
# no-op sleep turns permanent flush failures into a busy-loop that starves playback.
pacing_sleep = asyncio.sleep

# Event type → (suspension_kind, resolved emitter name for logs).
_WIRED_PAUSE_BY_EVENT: dict[str, str] = {
    "team_preview_required": "team_preview",
    "checkpoint_required": "ask_user",
    "plan_review_required": "plan_review",
}

logger = get_logger(__name__)


def _as_event_type(name: str) -> EventType | None:
    try:
        return EventType(name)
    except ValueError:
        return None


def _iso_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


async def _emit(
    sink: EventSink, et_name: str, payload: dict[str, Any], *, ts: str | None
) -> None:
    et = _as_event_type(et_name)
    if et is None:
        logger.debug("demo_tape.skip_unknown_type", type=et_name)
        return
    sink.emit(SSEEvent(type=et, payload=payload, timestamp=ts or _iso_now()))


def _accumulate_text(buf: list[str], et_name: str, payload: dict[str, Any]) -> None:
    if et_name in ("content_delta", "reasoning_delta"):
        delta = payload.get("delta") or ""
        if delta:
            buf.append(str(delta))


def _fold_ledger_entries(
    current: list[dict[str, Any]], payload: dict[str, Any]
) -> list[dict[str, Any]]:
    """Accumulate one ``evidence_ledger`` event into the running turn ledger.

    Mirrors the client fold (``attachEvidenceLedgerToLastMessage``): a full
    ``entries`` snapshot replaces; a ``delta`` merges by id (append-only order).
    The tape re-plays these events live but its recorded ``citations`` are cut
    (``TAPE_EXCLUDED_KINDS``) and the player never re-derived them — so the
    authoritative turn ledger is rebuilt here for ``_result_from_sink`` to persist,
    letting a reloaded replay resolve ``#rN`` chips instead of leaking raw text.
    """
    entries = payload.get("entries")
    if isinstance(entries, list):
        return [dict(e) for e in entries if isinstance(e, dict)]
    delta = payload.get("delta") or []
    if not delta:
        return current
    order: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    for e in (*current, *delta):
        if not isinstance(e, dict):
            continue
        eid = str(e.get("id") or "")
        if not eid:
            continue
        if eid not in by_id:
            order.append(eid)
        by_id[eid] = dict(e)
    return [by_id[i] for i in order]


def _message_finals_from_sink(sink: EventSink) -> list[dict[str, Any]]:
    """Build ``message_final`` facts from coalesced per-run process text.

    Reload splices ``run_output_delta`` from these (deltas are DERIVED / not journaled).
    Joining every content/reasoning step is correct even if mid-play fragmentation
    left multiple steps per run.
    """
    processes = sink.run_process_timelines() or {}
    finals: list[dict[str, Any]] = []
    for run_id, steps in processes.items():
        content = "".join(
            str(s.get("text") or "") for s in (steps or []) if s.get("kind") == "content"
        )
        reasoning = "".join(
            str(s.get("text") or "") for s in (steps or []) if s.get("kind") == "reasoning"
        )
        if not content and not reasoning:
            continue
        finals.append(
            {
                "kind": "message_final",
                "payload": {
                    "run_id": run_id,
                    "content": content,
                    "reasoning": reasoning,
                },
                "ts": None,
            }
        )
    return finals


def _result_from_sink(
    *,
    sink: EventSink,
    message_id: str,
    finish: FinishReason,
    content: str,
    reasoning: str,
    ledger_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    runs = _build_runs_payload(sink, finish)
    if runs:
        # Close open trailing captain text (e.g. the CEO summary content after the
        # collaboration graph) into the durable journal via append-on-emit — mirrors
        # pipeline.finalize._journal_entries_for_turn so a pure hydrate reload replays
        # it rather than only the live sink seeing it (process_content 落库).
        sink.flush_process_to_journal()
    journal_entries = journal_entries_from_display_runs(runs) if runs else None
    # Finalize persist replaces the turn journal with this list — include message_final
    # so runs_from_entries can splice worker output on reload (oracle parity).
    if journal_entries is not None:
        finals = _message_finals_from_sink(sink)
        if finals:
            body = [e for e in journal_entries if e.get("kind") != "turn_end"]
            tail = [e for e in journal_entries if e.get("kind") == "turn_end"]
            journal_entries = body + finals + tail
    # 引用即出处（回放落库对齐真实 settle）：磁带只回放 evidence_ledger 事件、剪掉了
    # citations（TAPE_EXCLUDED_KINDS），此前落库 result 又两列皆空 → 重开会话纯水合时
    # #rN 台账角标退化成原始文本。这里用与 settle.emit_turn_evidence_ledger 同一套派生
    # （按成稿 cited_ids 从台账投影来源卡），把 evidence_ledger / citations 带进
    # persist_turn_result，使回放重开也能还原来源角标与来源卡。None 值不覆盖既有列
    # （messages.create_or_update 语义），故 pause 段部分台账 → resume 段全量覆盖。
    entries = ledger_entries or []
    cited_ids = extract_ledger_ref_ids(content)
    cited_cards = project_cited_citations(entries, cited_ids) if entries else []
    return {
        "message_id": message_id,
        "content": content,
        "reasoning_content": reasoning or None,
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
        "rounds": 0,
        "finish_reason": finish,
        "citations": cited_cards or None,
        "evidence_ledger": entries or None,
        "cited_ids": cited_ids,
        "cost_runs": [],  # skip cost ledger for demo replay
        "journal_entries": journal_entries,
        "collab": {},
        "audit_drops": 0,
    }


def _attach_turn_followups(
    result: dict[str, Any], act: dict[str, Any]
) -> dict[str, Any]:
    """No-op: CEO→user followups chips are offline (stage_card is the open-debate path).

    Older tapes may still carry ``act.followups`` / ``meta.followups``; they are ignored
    on replay so persist never set_followups / emit ``followups_generated``.
    """
    del act  # legacy tape field ignored
    return result


def _finalize_act_cursor(
    result: dict[str, Any],
    *,
    conversation_id: str,
    turn_index: int,
    turn_count: int,
) -> dict[str, Any]:
    """After a completed act, advance the binding cursor or unbind on the last act."""
    if result.get("finish_reason") is not FinishReason.END_TURN:
        return result
    advance_after_act_complete(
        conversation_id, turn_index=turn_index, turn_count=turn_count
    )
    return result


def _tape_revision(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 1
    return value if value >= 1 else 1


def _ensure_checkpoint_id(payload: dict[str, Any], *, message_id: str) -> str:
    cid = str(payload.get("checkpoint_id") or "")
    if not cid:
        cid = replay_interaction_id("", message_id=message_id)
        payload["checkpoint_id"] = cid
    return cid


def _build_required_event(
    et_name: str,
    payload: dict[str, Any],
    *,
    conversation_id: str,
    message_id: str,
    ts: str | None,
) -> SSEEvent:
    """Build a live ``*_required`` SSEEvent from tape payload (minimal seed)."""
    checkpoint_id = _ensure_checkpoint_id(payload, message_id=message_id)
    payload["conversation_id"] = conversation_id
    if et_name == "team_preview_required":
        required = team_preview_required(
            checkpoint_id=checkpoint_id,
            conversation_id=conversation_id,
            workers=list(payload.get("workers") or []),
            tools=list(payload.get("tools") or []),
            primitive=str(payload.get("primitive") or "debate"),
            motion=str(payload.get("motion") or ""),
            form=str(payload.get("form") or ""),
            sides=list(payload.get("sides") or []),
            max_rounds=int(payload.get("max_rounds") or 0),
            thorough=bool(payload.get("thorough", True)),
            headline=str(payload.get("headline") or ""),
            revision=_tape_revision(payload.get("revision")),
            revised_from=str(payload.get("revised_from") or ""),
            revision_note=str(payload.get("revision_note") or ""),
        )
    elif et_name == "checkpoint_required":
        intent = payload.get("intent")
        required = checkpoint_required(
            checkpoint_id=checkpoint_id,
            conversation_id=conversation_id,
            question=str(payload.get("question") or ""),
            assumptions=list(payload.get("assumptions") or []),
            questions=list(payload.get("questions") or []),
            intent=intent if isinstance(intent, str) else None,
        )
    elif et_name == "plan_review_required":
        required = plan_review_required(
            checkpoint_id=checkpoint_id,
            conversation_id=conversation_id,
            steps=list(payload.get("steps") or []),
            pending=list(payload.get("pending") or []),
        )
    else:
        raise ValueError(f"not a wired pause event: {et_name}")
    if ts:
        return SSEEvent(type=required.type, payload=required.payload, timestamp=ts)
    return required


def _emit_resolved_for_kind(
    sink: EventSink,
    *,
    kind: str,
    checkpoint_id: str,
    decision: str,
    note: str = "",
    selected: list[str] | None = None,
) -> None:
    if kind == "team_preview":
        sink.emit(
            team_preview_resolved(
                checkpoint_id=checkpoint_id, decision=decision, note=note
            )
        )
    elif kind == "ask_user":
        sink.emit(
            checkpoint_resolved(
                checkpoint_id=checkpoint_id,
                decision=decision,
                note=note,
                selected=list(selected or []),
            )
        )
    elif kind == "plan_review":
        sink.emit(
            plan_review_resolved(
                checkpoint_id=checkpoint_id, decision=decision, note=note
            )
        )
    else:
        raise ValueError(f"unknown suspension kind for resolve: {kind}")


def _suspension_kind_of(suspension: TurnSuspension) -> str:
    if isinstance(suspension, TeamPreviewSuspension):
        return "team_preview"
    if isinstance(suspension, AskUserSuspension):
        return "ask_user"
    if isinstance(suspension, PlanReviewSuspension):
        return "plan_review"
    raise TypeError(f"unsupported tape suspension: {type(suspension).__name__}")


def _ensure_approval_id(payload: dict[str, Any], *, message_id: str) -> str:
    aid = str(payload.get("approval_id") or "")
    if not aid:
        aid = replay_interaction_id("", message_id=message_id)
        payload["approval_id"] = aid
    return aid


def _decision_value(decision: Any) -> str:
    return decision.value if hasattr(decision, "value") else str(decision)


async def _emit_auto_resolved_approval(
    *,
    sink: EventSink,
    conversation_id: str,
    message_id: str,
    payload: dict[str, Any],
    ts: str | None,
) -> None:
    """Emit approval required+resolved without awaiting (director seek past the card)."""
    approval_id = _ensure_approval_id(payload, message_id=message_id)
    tool_call_id = str(payload.get("tool_call_id") or approval_id)
    tool_name = str(payload.get("tool_name") or "unknown")
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    required = approval_required(
        approval_id=approval_id,
        conversation_id=conversation_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        arguments=arguments,
    )
    if ts:
        required = SSEEvent(type=required.type, payload=required.payload, timestamp=ts)
    sink.emit(required)
    sink.emit(
        approval_resolved(
            approval_id=approval_id,
            tool_call_id=tool_call_id,
            decision=ApprovalDecision.APPROVE.value,
        )
    )
    logger.info(
        "demo_tape.auto_resolved_approval",
        conversation_id=conversation_id,
        approval_id=approval_id,
        tool_name=tool_name,
    )


async def _await_hot_approval(
    *,
    sink: EventSink,
    conversation_id: str,
    message_id: str,
    payload: dict[str, Any],
    ts: str | None,
    transport: PlaybackTransport | None,
    event_index: int,
    t_ms: int,
) -> None:
    """Register reminted approval into InteractionRegistry and await hot resolve.

    Keeps the turn running (no paused frame). Live ``POST …/interactions/{id}``
    settles the Future; we then re-emit ``approval_resolved`` (recorded resolve
    was cut at export). APPROVE / DENY / ALWAYS all continue the tape stream.
    """
    approval_id = _ensure_approval_id(payload, message_id=message_id)
    tool_call_id = str(payload.get("tool_call_id") or approval_id)
    tool_name = str(payload.get("tool_name") or "unknown")
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    registry = default_interaction_registry()
    if transport is not None:
        transport.mark_awaiting_interaction(event_index=event_index, t_ms=t_ms)

    def _on_suspended() -> None:
        required = approval_required(
            approval_id=approval_id,
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
        )
        if ts:
            required = SSEEvent(
                type=required.type, payload=required.payload, timestamp=ts
            )
        sink.emit(required)

    try:
        decision = await registry.suspend(
            approval_id,
            conversation_id,
            kind=InteractionKind.APPROVAL,
            payload={
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "approval_id": approval_id,
                "conversation_id": conversation_id,
            },
            timeout=None,
            on_suspended=_on_suspended,
        )
    except asyncio.CancelledError:
        # Stop / orphan cancels the Future; registry.suspend finally discards.
        if transport is not None:
            transport.clear_awaiting_interaction()
        raise
    if transport is not None:
        transport.clear_awaiting_interaction()

    decision_str = _decision_value(decision)
    logger.info(
        "demo_tape.approval_hot_resolved",
        conversation_id=conversation_id,
        message_id=message_id,
        approval_id=approval_id,
        tool_name=tool_name,
        decision=decision_str,
    )
    # Decision does not fork the recorded stream — only logged (cold-path parity).
    sink.emit(
        approval_resolved(
            approval_id=approval_id,
            tool_call_id=tool_call_id,
            decision=decision_str,
        )
    )


async def _emit_auto_resolved_pause(
    *,
    sink: EventSink,
    et_name: str,
    conversation_id: str,
    message_id: str,
    payload: dict[str, Any],
    ts: str | None,
) -> None:
    """Emit required + resolved without durable pause (director seek past the card)."""
    kind = _WIRED_PAUSE_BY_EVENT[et_name]
    required = _build_required_event(
        et_name,
        payload,
        conversation_id=conversation_id,
        message_id=message_id,
        ts=ts,
    )
    sink.emit(required)
    _emit_resolved_for_kind(
        sink,
        kind=kind,
        checkpoint_id=str(required.payload["checkpoint_id"]),
        decision=CheckpointDecision.CONTINUE.value,
        note="demo_tape.director_auto_resolve",
    )
    logger.info(
        "demo_tape.auto_resolved_pause",
        kind=kind,
        conversation_id=conversation_id,
        checkpoint_id=required.payload.get("checkpoint_id"),
    )


def _build_tape_frame(
    *,
    kind: str,
    capture: Any,
    message_id: str,
    conversation_id: str,
    user_id: str,
    user_message: str,
    folder_id: str | None,
    checkpoint_id: str,
    payload: dict[str, Any],
    tape_meta: dict[str, Any],
) -> TurnSuspension:
    """Minimal suspension seed from tape payload (shared capture skeleton)."""
    common = dict(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        captain_run_id=message_id,
        checkpoint_id=checkpoint_id,
        base_system_prompt="__demo_tape__",
        user_message=user_message,
        folder_id=folder_id,
        transcript=list(capture.transcript),
        history=list(capture.history),
        journal_entries=capture.journal_entries,
        citations=capture.citations,
        trace_id=capture.trace_id,
    )
    if kind == "team_preview":
        return TeamPreviewSuspension(
            **common,
            tool_call_id=f"tape_debate_{checkpoint_id[:8]}",
            plan=RunPlan(),
            completed={},
            workers=list(payload.get("workers") or []),
            tools=list(payload.get("tools") or []),
            primitive=str(payload.get("primitive") or "debate"),
            motion=str(payload.get("motion") or ""),
            form=str(payload.get("form") or ""),
            sides=list(payload.get("sides") or []),
            max_rounds=int(payload.get("max_rounds") or 0),
            thorough=bool(payload.get("thorough", True)),
            # Divert marker mirror — content lives on turn_paused; cursor also on extras.
            debate_arguments={
                DEMO_TAPE_FRAME_KEY: dict(tape_meta),
                "motion": payload.get("motion") or "",
                "form": payload.get("form") or "",
                "sides": list(payload.get("sides") or []),
                "thorough": bool(payload.get("thorough", True)),
            },
        )
    if kind == "ask_user":
        intent = payload.get("intent") or "decision"
        return AskUserSuspension(
            **common,
            tool_call_id=f"tape_ask_user_{checkpoint_id[:8]}",
            question=str(payload.get("question") or ""),
            assumptions=list(payload.get("assumptions") or []),
            questions=list(payload.get("questions") or []),
            intent=intent if isinstance(intent, str) else "decision",
        )
    if kind == "plan_review":
        raw_review = payload.get("ceo_review")
        return PlanReviewSuspension(
            **common,
            tool_call_id=f"tape_plan_review_{checkpoint_id[:8]}",
            plan=RunPlan(),
            completed={},
            steps=list(payload.get("steps") or []),
            pending=list(payload.get("pending") or []),
            ceo_review=dict(raw_review) if isinstance(raw_review, dict) else None,
        )
    raise ValueError(f"unknown tape pause kind: {kind}")


async def _pause_durable(
    *,
    et_name: str,
    sink: EventSink,
    binding: TapeBinding,
    message_id: str,
    conversation_id: str,
    user_id: str,
    user_message: str,
    folder_id: str | None,
    required: SSEEvent,
    next_index: int,
    journal_writer: TurnJournalWriter,
    transport: PlaybackTransport | None = None,
    turn_index: int = 0,
    ledger_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Durable pause via the live suspension-capture skeleton (no tape content channel)."""
    kind = _WIRED_PAUSE_BY_EVENT[et_name]
    checkpoint_id = str(required.payload.get("checkpoint_id") or new_id())
    payload = dict(required.payload)
    payload["checkpoint_id"] = checkpoint_id
    payload["conversation_id"] = conversation_id
    required = SSEEvent(
        type=required.type,
        payload=payload,
        timestamp=required.timestamp,
    )

    speed = transport.speed if transport is not None else binding.speed
    # Frame cursor = event index within the current act; turn_index freezes which
    # act (binding cursor stays put until END_TURN — no conflict with DEMO_TAPE_FRAME_KEY).
    tape_meta = {
        "tape": str(binding.tape_path),
        "next_index": next_index,
        "turn_index": int(turn_index),
        "speed": speed,
        "max_gap_ms": binding.max_gap_ms,
    }
    paused_content = ""
    paused_reasoning = ""

    def build_frame(capture):  # type: ignore[no-untyped-def]
        nonlocal paused_content, paused_reasoning
        paused_content = capture.paused_content
        fact = pre_pause_from_journal(capture.journal_entries)
        paused_reasoning = fact.reasoning if fact is not None else ""
        return _build_tape_frame(
            kind=kind,
            capture=capture,
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            user_message=user_message,
            folder_id=folder_id,
            checkpoint_id=checkpoint_id,
            payload=payload,
            tape_meta=tape_meta,
        )

    await journal_writer.flush()
    # Live capture requires a non-empty captain transcript; tape has no CEO loop —
    # seed a minimal window so the shared skeleton proceeds (content comes from sink).
    tr_token = captain_transcript.set([LLMMessage(role="user", content=user_message)])
    try:
        saved = await persist_suspension_capture(
            checkpoint_id=checkpoint_id,
            required_event=required,
            build_frame=build_frame,
            saver=save_paused_turn,
            sink=sink,
            suspension_kind=kind,
            turn_paused_extras={DEMO_TAPE_FRAME_KEY: dict(tape_meta)},
        )
    except SuspensionPersistError:
        logger.exception(
            "demo_tape.pause_persist_failed",
            kind=kind,
            message_id=message_id,
            checkpoint_id=checkpoint_id,
        )
        raise
    finally:
        captain_transcript.reset(tr_token)

    if not saved:
        raise RuntimeError(
            f"demo tape pause capture unavailable (no transcript) for {checkpoint_id}"
        )

    # Live order: persist frame, then emit the required card, then pause-end.
    sink.emit(required)
    sink.emit(message_end(FinishReason.PAUSED))
    if not paused_content:
        paused_content = sink.streamed_content() or ""
    if not paused_reasoning:
        paused_reasoning = sink.streamed_reasoning() or ""
    logger.info(
        "demo_tape.paused",
        kind=kind,
        message_id=message_id,
        checkpoint_id=checkpoint_id,
        next_index=next_index,
        content_chars=len(paused_content),
    )
    return _result_from_sink(
        sink=sink,
        message_id=message_id,
        finish=FinishReason.PAUSED,
        content=paused_content,
        reasoning=paused_reasoning,
        ledger_entries=ledger_entries,
    )


# Back-compat alias for director tests that patch ``_pause_team_preview``.
async def _pause_team_preview(**kwargs: Any) -> dict[str, Any]:
    return await _pause_durable(et_name="team_preview_required", **kwargs)


async def play_tape_events(
    *,
    sink: EventSink,
    events: list[dict[str, Any]],
    start_index: int,
    binding: TapeBinding,
    message_id: str,
    conversation_id: str,
    user_id: str,
    user_message: str,
    folder_id: str | None,
    journal_writer: TurnJournalWriter,
    content_seed: str = "",
    reasoning_seed: str = "",
    emit_message_start: bool = True,
    trace_id: str | None = None,
    transport: PlaybackTransport | None = None,
    turn_index: int = 0,
) -> dict[str, Any]:
    """Play events from ``start_index``; pause on the next required card.

    Event prep (normalize / remint / legacy captain ``run_id`` strip) is the shared
    SINK source adapter (:mod:`agentcore.replay`). This player keeps demo-tape
    application decoration: pacing, wired durable-pause cards (team_preview /
    ask_user / plan_review), hot-path approval await via InteractionRegistry,
    message lifecycle alignment with shared bootstrap. When ``transport`` is set
    (director console), speed / pause / burst-seek are read live from that metronome.
    ``turn_index`` is frozen into pause-frame meta so resume continues the same act.
    """
    source = prepare_replay_source(
        {"events": events},
        consumer=ConsumerKind.SINK,
        message_id=message_id,
    )
    assert_sink_consumer(source)
    events = list(source.events)
    # Content mirrors live finish: ``join_segments`` at each durable pause seam.
    # Reasoning stays raw-concat (fidelity oracle = journal process_reasoning bursts).
    content_acc = content_seed or ""
    content_buf: list[str] = []
    reasoning_parts: list[str] = [reasoning_seed] if reasoning_seed else []
    # Running turn evidence ledger, folded from the tape's ``evidence_ledger`` events
    # so the finalize result carries it (persist → reload resolves ``#rN`` chips).
    ledger_entries: list[dict[str, Any]] = []

    def _flush_content_seam() -> None:
        nonlocal content_acc, content_buf
        content_acc = join_segments(content_acc, "".join(content_buf))
        content_buf = []

    if transport is not None:
        transport.begin_play(message_id=message_id, start_index=start_index)

    if emit_message_start:
        sink.emit(
            message_start(
                message_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
            )
        )

    prev_t = None
    i = start_index
    while i < len(events):
        ev = events[i]
        et_name = event_type(ev)
        payload = dict(ev.get("payload") or {})
        t_ms = int(ev.get("t_ms") or 0)
        ts = event_timestamp(ev)

        # Skip non-emitted types *before* pacing so recorded pause/hesitation gaps
        # (turn_paused, *_resolved, …) do not sleep or advance the clock — resume's
        # first live event then fires immediately (prev_t still None → gap 0).
        if et_name in PAUSE_RESOLVED_KINDS or _as_event_type(et_name) is None:
            i += 1
            continue

        if transport is not None:
            transport.report_position(event_index=i, t_ms=t_ms)
            transport.clear_burst_if_reached(i)

        gap, prev_t = pacing_step(prev_t_ms=prev_t, t_ms=t_ms)
        if transport is not None:
            await transport.await_gap(gap, event_index=i)
        else:
            delay = sleep_ms_for_gap(
                gap_ms=gap, speed=binding.speed, max_gap_ms=binding.max_gap_ms
            )
            if delay > 0:
                await pacing_sleep(delay)

        if et_name in TAPE_HOT_PAUSE_KINDS:
            # Hot-path approval: register + await live interactions resolve; turn
            # stays running. Director seek past the card: emit required+resolved.
            if transport is not None and transport.should_auto_resolve_at(i):
                await _emit_auto_resolved_approval(
                    sink=sink,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    payload=payload,
                    ts=ts,
                )
                # Skip recorded human-decision gap (same as cold-path resume).
                prev_t = None
                i += 1
                continue

            await _await_hot_approval(
                sink=sink,
                conversation_id=conversation_id,
                message_id=message_id,
                payload=payload,
                ts=ts,
                transport=transport,
                event_index=i,
                t_ms=t_ms,
            )
            # Wall-clock wait must not re-sleep the recorded decision gap.
            prev_t = None
            i += 1
            continue

        if et_name in TAPE_WIRED_PAUSE_KINDS:
            # Director seek past this card: emit required+resolved, keep injecting.
            if transport is not None and transport.should_auto_resolve_at(i):
                await _emit_auto_resolved_pause(
                    sink=sink,
                    et_name=et_name,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    payload=payload,
                    ts=ts,
                )
                # Same persist seam as cold resume: joiner lands between segments.
                _flush_content_seam()
                i += 1
                continue

            # Live order: capture+persist first, then emit the card. Remint id here.
            required = _build_required_event(
                et_name,
                payload,
                conversation_id=conversation_id,
                message_id=message_id,
                ts=ts,
            )
            result = await _pause_durable(
                et_name=et_name,
                sink=sink,
                binding=binding,
                message_id=message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                user_message=user_message,
                folder_id=folder_id,
                required=required,
                next_index=i + 1,
                journal_writer=journal_writer,
                transport=transport,
                turn_index=turn_index,
                ledger_entries=ledger_entries,
            )
            if transport is not None:
                transport.mark_awaiting_interaction(event_index=i, t_ms=t_ms)
            return result

        if et_name in PAUSE_REQUIRED_KINDS:
            # Unwired durable pause (none today among PAUSE_REQUIRED; kept as safety).
            logger.warning("demo_tape.unhandled_pause_type", type=et_name)
            await _emit(sink, et_name, payload, ts=ts)
            i += 1
            continue

        await _emit(sink, et_name, payload, ts=ts)
        if et_name == "content_delta":
            delta = payload.get("delta") or ""
            if delta:
                content_buf.append(str(delta))
        elif et_name == "reasoning_delta":
            _accumulate_text(reasoning_parts, et_name, payload)
        elif et_name == "evidence_ledger":
            ledger_entries = _fold_ledger_entries(ledger_entries, payload)
        i += 1

    content = join_segments(content_acc, "".join(content_buf))
    reasoning = "".join(reasoning_parts)
    sink.emit(message_end(FinishReason.END_TURN))
    logger.info(
        "demo_tape.complete",
        message_id=message_id,
        events_played=i - start_index,
        content_chars=len(content),
    )
    if transport is not None:
        transport.report_position(event_index=max(0, i - 1), t_ms=int(prev_t or 0))
        transport.mark_finished()
    return _result_from_sink(
        sink=sink,
        message_id=message_id,
        finish=FinishReason.END_TURN,
        content=content,
        reasoning=reasoning,
        ledger_entries=ledger_entries,
    )


async def play_tape_turn(
    *,
    binding: TapeBinding,
    sink: EventSink,
    message_id: str,
    conversation_id: str,
    user_id: str,
    user_message: str,
    folder_id: str | None,
    trace_id: str | None,
) -> dict[str, Any]:
    tape = load_tape(binding.tape_path)
    acts = tape_turns(tape)
    turn_count = len(acts)
    turn_index = int(binding.turn_index or 0)
    if turn_index < 0 or turn_index >= turn_count:
        logger.error(
            "demo_tape.turn_index_out_of_range",
            conversation_id=conversation_id,
            turn_index=turn_index,
            turn_count=turn_count,
            tape=str(binding.tape_path),
        )
        raise RuntimeError(
            f"demo tape turn_index {turn_index} out of range for {turn_count} acts"
        )
    act = acts[turn_index]
    events = list(act.get("events") or [])
    duration_ms = max((int(ev.get("t_ms") or 0) for ev in events), default=0)
    logger.info(
        "demo_tape.play_start",
        conversation_id=conversation_id,
        message_id=message_id,
        tape=str(binding.tape_path),
        turn_index=turn_index,
        turn_count=turn_count,
        events=len(events),
        duration_ms=duration_ms,
        speed=binding.speed,
        max_gap_ms=binding.max_gap_ms,
    )
    transport = transport_registry.attach(
        conversation_id=conversation_id,
        tape_path=binding.tape_path,
        speed=binding.speed,
        max_gap_ms=binding.max_gap_ms,
        event_count=len(events),
        duration_ms=duration_ms,
        tape_id=binding.tape_path.stem,
    )
    writer = TurnJournalWriter(
        turn_id=message_id,
        conversation_id=conversation_id,
        trace_id=trace_id,
    )
    fact_log = TurnFactLog()
    token = current_journal_writer.set(writer)
    fact_token = current_fact_log.set(fact_log)
    try:
        result = await play_tape_events(
            sink=sink,
            events=events,
            start_index=0,
            binding=binding,
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            user_message=user_message,
            folder_id=folder_id,
            journal_writer=writer,
            trace_id=trace_id,
            transport=transport,
            turn_index=turn_index,
        )
        await writer.flush()
        result = _attach_turn_followups(result, act)
        return _finalize_act_cursor(
            result,
            conversation_id=conversation_id,
            turn_index=turn_index,
            turn_count=turn_count,
        )
    except Exception as e:
        transport.mark_error(str(e))
        raise
    finally:
        current_fact_log.reset(fact_token)
        current_journal_writer.reset(token)


async def continue_tape_turn(
    *,
    suspension: TurnSuspension,
    response: CheckpointResponse,
    sink: EventSink,
    folder_id: str | None,
    trace_id: str | None,
) -> dict[str, Any]:
    """Resume a tape paused at a wired durable card after a real frontend resolve.

    Display open goes through the shared resume bootstrap (message_start +
    turn_paused rehydrate + G6 arm). Tape only answers which event index to
    continue from — no private content channel. ``selected`` / ``adjust`` notes
    are logged but do not rewrite the recorded event stream.
    """
    if not is_demo_tape_frame(suspension):
        raise RuntimeError("continue_tape_turn called on non-tape suspension")

    kind = _suspension_kind_of(suspension)
    meta = tape_frame_meta(suspension)
    if not meta.get("tape"):
        raise RuntimeError("demo tape frame missing tape path in turn_paused extras")
    tape_path = Path(str(meta["tape"]))
    next_index = int(meta.get("next_index") or 0)
    turn_index = int(meta.get("turn_index") or 0)
    speed = float(meta.get("speed") or 1.0)
    max_gap_ms = int(meta.get("max_gap_ms") or 3000)

    # Prefer live director speed over the frozen pause-frame meta.
    live = transport_registry.get(suspension.conversation_id)
    if live is not None:
        speed = live.speed
    binding = TapeBinding(
        conversation_id=suspension.conversation_id,
        tape_path=tape_path,
        speed=speed,
        max_gap_ms=max_gap_ms,
        turn_index=turn_index,
    )
    tape = load_tape(tape_path)
    acts = tape_turns(tape)
    turn_count = len(acts)
    if turn_index < 0 or turn_index >= turn_count:
        raise RuntimeError(
            f"demo tape frame turn_index {turn_index} out of range for {turn_count} acts"
        )
    act = acts[turn_index]
    events = list(act.get("events") or [])
    duration_ms = max((int(ev.get("t_ms") or 0) for ev in events), default=0)
    transport = transport_registry.attach(
        conversation_id=suspension.conversation_id,
        tape_path=tape_path,
        speed=speed,
        max_gap_ms=max_gap_ms,
        event_count=len(events),
        duration_ms=duration_ms,
        tape_id=tape_path.stem,
    )

    # Shared resume display open (parity with resume_chat_pipeline).
    hydrated = bootstrap_resume_display(
        sink=sink,
        suspension=suspension,
        conversation_id=suspension.conversation_id,
    )
    content_seed = hydrated.pre_pause_content or ""
    reasoning_seed = hydrated.pre_pause_reasoning or ""
    arm_content_reset_reinjection(sink, content_seed)

    decision = response.decision
    if decision is CheckpointDecision.STOP:
        _emit_resolved_for_kind(
            sink,
            kind=kind,
            checkpoint_id=suspension.checkpoint_id,
            decision=decision.value,
            note=response.note or "",
            selected=list(response.selected or []),
        )
        sink.emit(message_end(FinishReason.CANCELLED))
        transport.mark_finished()
        return _result_from_sink(
            sink=sink,
            message_id=suspension.message_id,
            finish=FinishReason.CANCELLED,
            content=content_seed,
            reasoning=reasoning_seed,
        )

    # Tape continue: inject by recorded stream only. selected / adjust do not fork.
    if decision is CheckpointDecision.ADJUST or response.selected:
        logger.info(
            "demo_tape.resume_decision_ignored_for_stream",
            kind=kind,
            decision=decision.value,
            selected=list(response.selected or []),
            note_chars=len(response.note or ""),
            message_id=suspension.message_id,
        )

    _emit_resolved_for_kind(
        sink,
        kind=kind,
        checkpoint_id=suspension.checkpoint_id,
        decision=decision.value,
        note=response.note or "",
        selected=list(response.selected or []),
    )

    # Writer continues after sealed pause — new writer with seq after prior facts.
    initial_seq = len(suspension.journal_entries or [])
    writer = TurnJournalWriter(
        turn_id=suspension.message_id,
        conversation_id=suspension.conversation_id,
        trace_id=trace_id,
        initial_seq=initial_seq,
    )
    fact_log = TurnFactLog(inherited_entries=list(suspension.journal_entries or []))
    token = current_journal_writer.set(writer)
    fact_token = current_fact_log.set(fact_log)
    try:
        result = await play_tape_events(
            sink=sink,
            events=events,
            start_index=next_index,
            binding=binding,
            message_id=suspension.message_id,
            conversation_id=suspension.conversation_id,
            user_id=suspension.user_id,
            user_message=suspension.user_message,
            folder_id=folder_id if folder_id is not None else suspension.folder_id,
            journal_writer=writer,
            content_seed=content_seed,
            reasoning_seed=reasoning_seed,
            emit_message_start=False,  # bootstrap already emitted message_start
            trace_id=trace_id,
            transport=transport,
            turn_index=turn_index,
        )
        await writer.flush()
        result = _attach_turn_followups(result, act)
        return _finalize_act_cursor(
            result,
            conversation_id=suspension.conversation_id,
            turn_index=turn_index,
            turn_count=turn_count,
        )
    except Exception as e:
        transport.mark_error(str(e))
        raise
    finally:
        current_fact_log.reset(fact_token)
        current_journal_writer.reset(token)
