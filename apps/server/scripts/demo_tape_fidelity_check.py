"""Offline fidelity + pacing acceptance: tape vs oracle, and tape → player replay.

Replaces the journal-era "fresh re-export" check (that layer is retired): fidelity
is now asserted on BOTH the tape file (content/reasoning byte-equal vs the oracle
conversation in the DB, ordering/debate-structure invariants, monotonic t_ms) and an
actual in-process REPLAY through the player (leftover team_preview_* skip — no
kickoff pause / no persist; play-through complete; replayed captain
content/reasoning byte-equal).

Usage (from apps/server)::

    uv run python scripts/demo_tape_fidelity_check.py
    uv run python scripts/demo_tape_fidelity_check.py --tape <path> --message-id <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from agentcore.db.base import async_session_factory
from agentcore.demo_tape.export import load_tape
from agentcore.demo_tape.schema import event_type, persisted_captain_content_from_events

DEFAULT_ORACLE_MID = "69262466-c868-4f53-a6a2-6d626c5c0c19"
DEFAULT_TAPE = (
    Path(__file__).resolve().parents[3] / "demos" / "tapes" / "lv-molihua-trademark.json"
)
OUT_DIR = Path(__file__).resolve().parents[3] / "apps" / "desktop" / "demo-tape-out"


async def _oracle(message_id: str) -> tuple[str, str]:
    """Captain content + reasoning truth from the source conversation.

    Content oracle = ``messages.content`` (persist applies ``join_segments`` at the
    durable-pause seam). Reasoning oracle = concat of captain ``process_reasoning``
    bursts (differs from ``messages.reasoning_content`` only by the pause-boundary
    joiner).
    """
    async with async_session_factory() as s:
        msg = (
            await s.execute(
                text("SELECT content FROM messages WHERE id=:mid"),
                {"mid": message_id},
            )
        ).mappings().one()
        rows = (
            await s.execute(
                text(
                    "SELECT payload FROM turn_journal "
                    "WHERE turn_id=:mid AND kind='process_reasoning' ORDER BY seq"
                ),
                {"mid": message_id},
            )
        ).mappings().all()
    reasoning = "".join(str((r["payload"] or {}).get("text") or "") for r in rows)
    return msg["content"] or "", reasoning


def _check_tape_order(events: list[dict]) -> list[str]:
    errs: list[str] = []
    # For every run_id, first run_started must precede first run_context.
    first_started: dict[str, int] = {}
    first_context: dict[str, int] = {}
    for i, e in enumerate(events):
        rid = str((e.get("payload") or {}).get("run_id") or "")
        if not rid:
            continue
        et = event_type(e)
        if et == "run_started" and rid not in first_started:
            first_started[rid] = i
        if et == "run_context" and rid not in first_context:
            first_context[rid] = i
    for rid, ci in first_context.items():
        si = first_started.get(rid)
        if si is None:
            errs.append(f"run_context without run_started: {rid}")
        elif si > ci:
            errs.append(f"run_context before run_started: {rid} (ctx@{ci} > started@{si})")
    return errs


def _check_debate_structure(events: list[dict]) -> list[str]:
    errs: list[str] = []
    started = [e for e in events if event_type(e) == "run_started"]
    # moderator id is debate_<uuid> with a single underscore after "debate"
    mods = [
        e
        for e in started
        if (p := e.get("payload") or {})
        and str(p.get("run_id", "")).startswith("debate_")
        and "_r" not in str(p.get("run_id"))
        and "_closing" not in str(p.get("run_id"))
        and "_cx_" not in str(p.get("run_id"))
        and p.get("kind") == "agent"
    ]
    if len(mods) < 1:
        errs.append("missing moderator run_started")
    closings = [e for e in started if "closing" in str((e.get("payload") or {}).get("run_id", ""))]
    if len(closings) < 2:
        errs.append(f"expected 2 closing run_started, got {len(closings)}")
    cx_ctx = 0
    closing_ctx = 0
    for e in events:
        if event_type(e) != "run_context":
            continue
        ch = [b.get("channel") for b in ((e.get("payload") or {}).get("blocks") or [])]
        if "cross_exam" in ch:
            cx_ctx += 1
        if "closing" in ch:
            closing_ctx += 1
    # Existence lower bounds — recorded round counts vary per re-recording, so we assert
    # the debate projection is present/complete rather than pinning an exact round count.
    if cx_ctx < 1:
        errs.append(f"expected >=1 cross_exam run_context, got {cx_ctx}")
    if closing_ctx < 2:
        errs.append(f"expected 2 closing run_context, got {closing_ctx}")
    rounds = sum(1 for e in events if event_type(e) == "debate_round_started")
    if rounds < 1:
        errs.append(f"expected >=1 debate_round_started, got {rounds}")
    return errs


def _check_monotonic(events: list[dict]) -> list[str]:
    last = 0
    for i, e in enumerate(events):
        t = int(e.get("t_ms") or 0)
        if t < last:
            return [f"t_ms rewinds at index {i}: {t} < {last}"]
        last = t
    return []


async def _replay_check(tape_path: Path, report: dict) -> None:
    """Play the tape through the real player (leftover kickoff skip → complete)."""
    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import play_tape_events
    from agentcore.runtime.events import EventSink, FinishReason
    from agentcore.runtime.journal.writer import TurnJournalWriter

    saved: list = []

    async def fake_save(suspension):
        saved.append(suspension)

    async def noop_flush(self):
        return None

    player_mod.save_paused_turn = fake_save  # offline: no DB frame writes
    TurnJournalWriter.flush = noop_flush  # type: ignore[method-assign]

    tape = load_tape(tape_path)
    events = list(tape.get("events") or [])

    binding = TapeBinding(
        conversation_id="fidelity-conv",
        tape_path=tape_path,
        speed=100.0,
        max_gap_ms=0,  # no sleeps — pacing math itself is covered by unit tests
    )
    # Unbound sinks: no StreamCheckpointer → the offline replay never touches the DB.
    # The deliberately non-UUID ids are a second belt: any stray persist path fails the
    # UUID cast fail-soft (one tolerated warning) instead of writing garbage rows.
    sink = EventSink()
    writer = TurnJournalWriter(
        turn_id="fidelity-msg", conversation_id="fidelity-conv", trace_id="f" * 32
    )
    result = await play_tape_events(
        sink=sink,
        events=events,
        start_index=0,
        binding=binding,
        message_id="fidelity-msg",
        conversation_id="fidelity-conv",
        user_id="fidelity-user",
        user_message="fidelity",
        folder_id=None,
        journal_writer=writer,
        trace_id="f" * 32,
    )

    done = result["finish_reason"] is FinishReason.END_TURN
    report["checks"]["replay_completes_without_kickoff_pause"] = done
    if not done:
        report["errors"].append(f"replay did not complete: {result['finish_reason']}")
        return

    leftover_emitted = [
        e.type.value
        for e in sink._history
        if e.type.value in ("team_preview_required", "team_preview_resolved")
    ]
    ok_skip = leftover_emitted == [] and saved == []
    report["checks"]["replay_skips_leftover_team_preview"] = ok_skip
    if not ok_skip:
        report["errors"].append(
            f"leftover team_preview not skipped: emitted={leftover_emitted!r} "
            f"saved_frames={len(saved)}"
        )

    # Chips product-offline: replay must not re-attach meta.followups onto result.
    actual_followups = list(result.get("followups") or [])
    ok_followups = actual_followups == []
    report["checks"]["replay_followups_ignored"] = ok_followups
    if not ok_followups:
        report["errors"].append(
            f"replay unexpectedly attached followups: {actual_followups!r} "
            f"(meta had {(tape.get('meta') or {}).get('followups')!r})"
        )

    from agentcore.conformance.projection import project_turn

    wire = [{"type": e.type.value, "payload": e.payload} for e in sink._history]
    folded = project_turn(wire)
    folded_content = folded.get("content") or ""

    report["replay"] = {
        "content": result.get("content") or "",
        "reasoning": result.get("reasoning_content") or "",
        "folded_content": folded_content,
        "followups": actual_followups,
    }


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--message-id", default=DEFAULT_ORACLE_MID)
    p.add_argument("--tape", default=str(DEFAULT_TAPE))
    args = p.parse_args()

    tape_path = Path(args.tape)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    oracle_content, oracle_reasoning = await _oracle(args.message_id)
    tape = load_tape(tape_path)
    events = list(tape.get("events") or [])

    report: dict = {
        "oracle_message_id": args.message_id,
        "tape": str(tape_path),
        "checks": {},
        "errors": [],
    }

    # Persist-shaped: live finish joins segments at durable pauses; raw delta concat
    # omits that joiner (never present on the wire). Match messages.content oracle.
    tape_content = persisted_captain_content_from_events(events)
    ok_content = tape_content == oracle_content
    report["checks"]["captain_content_byte_equal"] = ok_content
    if not ok_content:
        report["errors"].append(
            f"captain content mismatch: oracle={len(oracle_content)} tape={len(tape_content)}"
        )

    tape_reasoning = "".join(
        (e.get("payload") or {}).get("delta") or ""
        for e in events
        if event_type(e) == "reasoning_delta"
    )
    ok_reasoning = tape_reasoning == oracle_reasoning
    report["checks"]["captain_reasoning_byte_equal"] = ok_reasoning
    if not ok_reasoning:
        report["errors"].append(
            f"captain reasoning mismatch: oracle={len(oracle_reasoning)} tape={len(tape_reasoning)}"
        )

    order_errs = _check_tape_order(events)
    report["checks"]["started_before_context"] = not order_errs
    report["errors"].extend(order_errs)

    struct_errs = _check_debate_structure(events)
    report["checks"]["debate_structure"] = not struct_errs
    report["errors"].extend(struct_errs)

    mono_errs = _check_monotonic(events)
    report["checks"]["t_ms_monotonic"] = not mono_errs
    report["errors"].extend(mono_errs)

    # End-to-end: the tape must REPLAY faithfully through the real player.
    await _replay_check(tape_path, report)
    replay = report.pop("replay", None)
    if replay is not None:
        ok_replay_content = replay["content"] == oracle_content
        report["checks"]["replay_content_byte_equal"] = ok_replay_content
        if not ok_replay_content:
            report["errors"].append(
                f"replayed content mismatch: oracle={len(oracle_content)} "
                f"replay={len(replay['content'])}"
            )
        ok_replay_reasoning = replay["reasoning"] == oracle_reasoning
        report["checks"]["replay_reasoning_byte_equal"] = ok_replay_reasoning
        if not ok_replay_reasoning:
            report["errors"].append(
                f"replayed reasoning mismatch: oracle={len(oracle_reasoning)} "
                f"replay={len(replay['reasoning'])}"
            )

    report["ok"] = len(report["errors"]) == 0
    out = OUT_DIR / "fidelity-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"wrote {out}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
