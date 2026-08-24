"""Chapter table + seek snap helpers derived from demo-tape events (dev-only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentcore.demo_tape.schema import event_type


@dataclass(frozen=True)
class TapeChapter:
    id: str
    label: str
    t_ms: int
    event_index: int


def build_chapters(events: list[dict[str, Any]]) -> list[TapeChapter]:
    """Pre-generate chapters from structured tape events.

    Rules (lv-molihua / debate tapes):
    - 开场检索 — index 0
    - 第 N 轮·立论 — ``debate_round_started``
    - 第 N 轮·质询 — first ``run_started`` with ``_cx_`` in ``run_id`` in that round
    - 第 N 轮·打分 — ``debate_round`` (round verdict)
    - 终审 — ``debate_result``
    """
    chapters: list[TapeChapter] = [TapeChapter("opening", "开场检索", 0, 0)]
    seen: set[str] = {"opening"}

    current_round: int | None = None
    cross_exam_marked: set[int] = set()

    for i, ev in enumerate(events):
        et = event_type(ev)
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        t_ms = int(ev.get("t_ms") or 0)

        if et == "debate_round_started":
            round_no = int(payload.get("round_no") or 0)
            if round_no <= 0:
                continue
            current_round = round_no
            cid = f"r{round_no}_argument"
            if cid not in seen:
                chapters.append(
                    TapeChapter(cid, f"第{round_no}轮·立论", t_ms, i)
                )
                seen.add(cid)
            continue

        if et == "run_started" and current_round is not None:
            run_id = str(payload.get("run_id") or "")
            if "_cx_" in run_id and current_round not in cross_exam_marked:
                cid = f"r{current_round}_cross"
                if cid not in seen:
                    chapters.append(
                        TapeChapter(cid, f"第{current_round}轮·质询", t_ms, i)
                    )
                    seen.add(cid)
                cross_exam_marked.add(current_round)
            continue

        if et == "debate_round":
            round_no = int(payload.get("round_no") or current_round or 0)
            if round_no <= 0:
                continue
            cid = f"r{round_no}_score"
            if cid not in seen:
                chapters.append(
                    TapeChapter(cid, f"第{round_no}轮·打分", t_ms, i)
                )
                seen.add(cid)
            continue

        if et == "debate_result":
            cid = "verdict"
            if cid not in seen:
                chapters.append(TapeChapter(cid, "终审", t_ms, i))
                seen.add(cid)

    chapters.sort(key=lambda c: (c.t_ms, c.event_index))
    return chapters


def snap_to_event_index(events: list[dict[str, Any]], target_t_ms: int) -> int:
    """Snap a timeline scrubber position to the nearest event boundary."""
    if not events:
        return 0
    best_i = 0
    best_dist: int | None = None
    target = int(target_t_ms)
    for i, ev in enumerate(events):
        t = int(ev.get("t_ms") or 0)
        dist = abs(t - target)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_i = i
    return best_i


def chapter_by_id(chapters: list[TapeChapter], chapter_id: str) -> TapeChapter | None:
    for ch in chapters:
        if ch.id == chapter_id:
            return ch
    return None
