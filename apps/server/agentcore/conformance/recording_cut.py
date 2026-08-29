"""录制文档 → conformance 巡检向量的裁切管道（录制回放通用化提案 步② 第二波）。

A live-turn recording (``demo_tape/recorder.py`` — the EventSink dev tap's verbatim
SSE capture, EPHEMERAL liveliness included) is cut OFFLINE into the exact turn-fixture
shape the protocol巡检 judges (``packages/protocol-conformance/fixtures``)::

    {name, description, events[{type, payload, timestamp}], projected}

已拍板（提案 §6 问题 3）: recordings never carry a ``projected`` oracle — the golden is
produced HERE, at cut time, by the backend projection (:func:`project_turn`). 录制 =
事实记录、oracle = 裁判产物：协议投影改版只需对既有录制重跑裁切，不必重录。

Pipeline (pure offline read → artifact; no runtime semantics — 提案 §3.3 红线):

1. **stitch** — flatten the recording's segments (send leg, then each resume leg) in
   stream order; demo pacing (``t_ms`` / ``wall_t0_ms``) is dropped.
2. **durable-face filter** — a conformance vector is the turn's durable face, not the
   raw直播原片. Axis = :data:`EVENT_DISPOSITION` (处置单一源): DURABLE + DERIVED kept,
   EPHEMERAL dropped — except the explicit :data:`CUT_KEEP_EPHEMERAL` whitelist.
3. **timestamp stabilization** — stamps are reassigned deterministically via
   :mod:`agentcore.conformance.timestamps` (wall-clock from ``duration_ms``; same
   scheme as :mod:`agentcore.conformance.export`), so re-cutting the same recording
   is byte-identical (golden 可复现; the projection ignores timestamps).
4. **ingest sanitize + scan** — shared with tape export (``demo_tape/sanitize.py``):
   strip long-term user memory from ``run_context`` system bodies; refuse residue.
5. **oracle projection** — ``projected = project_turn(events)``.

Coexistence with the hand-authored VECTORS builders (two sources, one judge): cut
fixtures are named ``recorded_*.json`` and live in the SAME top-level fixtures dir —
the harnesses' ``loadFixtures`` / ``#/preview`` glob read top-level only, so cut
vectors are judged and previewable exactly like hand vectors — while
``conformance.export``'s stale-golden sweep skips the ``recorded_`` prefix, so neither
source ever deletes the other's files.

Tapes (``demos/tapes/``) are NOT a valid input: their cut (``TAPE_EXCLUDED_KINDS``)
already removed ``message_start`` / ``message_end``, losing the finish_reason the
projected ``status`` derives from. Always cut from the recording原片.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from agentcore.conformance.projection import project_turn
from agentcore.conformance.timestamps import (
    format_stable_timestamp,
    wall_clock_ms_sequence,
)
from agentcore.demo_tape.sanitize import sanitize_and_scan_events
from agentcore.demo_tape.schema import normalize_tape_event
from agentcore.runtime.events import EventType
from agentcore.runtime.events.disposition import EVENT_DISPOSITION, Disposition
from agentcore.runtime.events.types import RETIRED_EVENT_TYPE_VALUES

# Cut fixtures own this name prefix; conformance.export's sweep skips it (and the
# hand-authored VECTORS must never use it — pinned by tests/test_recording_cut.py).
RECORDED_FIXTURE_PREFIX = "recorded_"

# EPHEMERAL event types the cut KEEPS（裁切白名单 · 步② B 口径，2026-07-17 拍板）。
#
# 入表原则：仅当 :func:`project_turn` oracle 或各端 fold 对该事件有**语义处理/结构依赖**、
# 丢弃会改变折叠裁判态或破坏向量可回放性时才进表。不是「为让 diff 过」的兜底名单——无
# fold 语义的事件（心跳/传输控制帧/客户端工具请求）一律丢弃；:data:`EVENT_DISPOSITION`
# 本身不新增分类。
CUT_KEEP_EPHEMERAL: frozenset[EventType] = frozenset(
    {
        # oracle 清正文标量并弹掉尾部 content 步——丢弃会把「违规版+修正版」拼在一起，
        # fold 出错误正文（projection.py content_reset 分支）。所有 reason 都不折过程痕迹。
        EventType.CONTENT_RESET,
        # content_reset 的 worker 对偶：oracle 清 run 卡片已流式产出（run_output_reset 分支）。
        EventType.RUN_OUTPUT_RESET,
        # oracle no-op，但各端 fold 以它初始化流式回合、既有手写向量均以其开场（#/preview
        # replay 同样依赖）——拍板初始集合成员。
        EventType.MESSAGE_START,
        # oracle 写 agent.toolProgress（run_tool_progress 分支）。终态会被 run_completed /
        # run_failed / run_cancelled 清除，但录制停在工具中途（暂停/中断原片）时丢弃它会让
        # projected 丢 toolProgress——按入表原则补充（拍板预留的 run_tool_progress 情形）。
        EventType.RUN_TOOL_PROGRESS,
        # oracle 写 run.phase / phaseTool（run_phase 分支）——mid-flight 活动相位单一源。
        EventType.RUN_PHASE,
    }
)

_DISPOSITION_BY_VALUE: dict[str, Disposition] = {
    event.value: disposition for event, (disposition, _reason) in EVENT_DISPOSITION.items()
}
_KEEP_EPHEMERAL_VALUES = frozenset(event.value for event in CUT_KEEP_EPHEMERAL)


def _keep(event_type_value: str) -> bool:
    disposition = _DISPOSITION_BY_VALUE.get(event_type_value)
    if disposition is None:
        # Retired names may still appear on old recordings; drop them
        # rather than treating version skew as a hard cut failure.
        if event_type_value in RETIRED_EVENT_TYPE_VALUES:
            return False
        raise ValueError(
            f"recording event type {event_type_value!r} is not a known EventType — "
            "recording/code version skew; refusing to cut silently"
        )
    if disposition is Disposition.EPHEMERAL:
        return event_type_value in _KEEP_EPHEMERAL_VALUES
    return True


def stitch_recording_events(recording: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a recording's segments into one ordered, normalized event list.

    Segment list order IS the stream order (the recorder appends send leg, then each
    resume leg chronologically). Legacy v1 elements (``kind``/``ts``) come through the
    normalize alias; pacing fields survive here and are dropped at cut.
    """
    out: list[dict[str, Any]] = []
    for segment in recording.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        for ev in segment.get("events") or []:
            if isinstance(ev, dict):
                out.append(normalize_tape_event(ev))
    return out


def durable_face(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter normalized events down to the conformance-relevant durable face."""
    return [ev for ev in events if _keep(str(ev.get("type") or ""))]


def cut_recording_to_fixture(
    recording: dict[str, Any],
    *,
    name: str,
    description: str = "",
) -> dict[str, Any]:
    """Cut one recording document into a committable conformance turn fixture.

    Output is self-contained and detached from the input (payloads deep-copied):
    ``{name, description, events[{type, payload, timestamp}], projected}`` with the
    ``recorded_`` ownership prefix enforced on ``name``.
    """
    if not name.startswith(RECORDED_FIXTURE_PREFIX):
        name = RECORDED_FIXTURE_PREFIX + name
    kept = durable_face(stitch_recording_events(recording))
    pairs = [
        (str(ev["type"]), copy.deepcopy(ev.get("payload") or {})) for ev in kept
    ]
    stamps = wall_clock_ms_sequence(pairs)
    events = [
        {
            "type": typ,
            "payload": payload,
            "timestamp": format_stable_timestamp(ms),
        }
        for (typ, payload), ms in zip(pairs, stamps, strict=True)
    ]
    # Shared ingest sanitize + scan (tape export uses the same helpers): strip
    # long-term user memory from run_context system bodies; refuse PII residue.
    events = sanitize_and_scan_events(events)
    return {
        "name": name,
        "description": description,
        "events": events,
        "projected": project_turn(events),
    }


def serialize_fixture(fixture: dict[str, Any]) -> str:
    """Byte-stable fixture JSON — the exact format conformance.export commits."""
    return json.dumps(fixture, ensure_ascii=False, indent=2, default=str) + "\n"


def write_fixture(fixture: dict[str, Any], out_dir: Path | None = None) -> Path:
    """Write ``<name>.json`` into ``out_dir`` (default: the shared fixtures dir)."""
    if out_dir is None:
        # Lazy import: single source for the fixtures dir without paying the VECTORS
        # package import on pure-filter uses (and monkeypatched dirs stay honored).
        from agentcore.conformance import export as export_module

        out_dir = export_module._FIXTURES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{fixture['name']}.json"
    path.write_text(serialize_fixture(fixture), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Cut a demo-tape recording (demos/recordings/<message_id>.json) into a "
            "conformance turn fixture (recorded_<name>.json)."
        )
    )
    parser.add_argument("recording", help="录制文件路径（demos/recordings/<message_id>.json）")
    parser.add_argument("--name", required=True, help="向量名（自动补 recorded_ 前缀）")
    parser.add_argument("--description", default="", help="向量描述（进 fixture description）")
    parser.add_argument(
        "--out",
        default=None,
        help="输出目录（默认 packages/protocol-conformance/fixtures）",
    )
    args = parser.parse_args(argv)

    from agentcore.demo_tape.recorder import load_recording

    recording = load_recording(Path(args.recording))
    fixture = cut_recording_to_fixture(
        recording, name=args.name, description=args.description
    )
    path = write_fixture(fixture, out_dir=Path(args.out) if args.out else None)
    print(
        f"recording-cut: {len(fixture['events'])} durable-face events "
        f"(projected status={fixture['projected'].get('status')}) → {path}"
    )


if __name__ == "__main__":
    main()
