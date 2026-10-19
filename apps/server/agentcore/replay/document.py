"""超集事件文档读入：conformance 向量 / 演示磁带 / 录制原片 → 归一事件列表。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agentcore.conformance.recording_cut import stitch_recording_events
from agentcore.demo_tape.schema import normalize_tape_events


class DocumentKind(StrEnum):
    TURN_FIXTURE = "turn_fixture"  # conformance / #/preview 向量
    TAPE = "tape"  # demos/tapes 剪辑磁带
    RECORDING = "recording"  # demos/recordings 原片（含 segments）
    BARE_EVENTS = "bare_events"  # 仅 events[]（player 内存腿）


@dataclass(slots=True)
class EventDocument:
    """归一后的超集事件文档（契约字段 ``type``/``timestamp``；pacing 超集保留）。"""

    kind: DocumentKind
    events: list[dict[str, Any]]
    name: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    has_pacing: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def _events_have_pacing(events: list[dict[str, Any]]) -> bool:
    return any(isinstance(ev, dict) and "t_ms" in ev for ev in events)


def open_event_document(raw: dict[str, Any]) -> EventDocument:
    """读入超集文档并归一事件元素（契约 ``type``/``timestamp``；不改写调用方输入）。

    判别顺序：录制原片（``segments``）→ 带 ``projected``/巡检名的向量 → 磁带
    （``version`` + ``events``）→ 裸 ``events`` 列表。
    """
    if not isinstance(raw, dict):
        raise TypeError(f"event document must be a dict, got {type(raw).__name__}")

    segments = raw.get("segments")
    # Recorder shape: ``kind: demo_tape_recording`` + ``segments[]``, no top-level events.
    if raw.get("kind") == "demo_tape_recording" or (
        isinstance(segments, list) and "events" not in raw
    ):
        events = stitch_recording_events(raw)
        meta = dict(raw.get("meta") or {}) if isinstance(raw.get("meta"), dict) else {}
        return EventDocument(
            kind=DocumentKind.RECORDING,
            events=events,
            name=str(raw["name"]) if isinstance(raw.get("name"), str) else None,
            meta=meta,
            has_pacing=_events_have_pacing(events),
            raw=raw,
        )

    events_raw = raw.get("events")
    if not isinstance(events_raw, list):
        raise ValueError("event document requires an events[] list (or recording segments[])")

    events = normalize_tape_events(events_raw)
    has_pacing = _events_have_pacing(events)
    meta = dict(raw.get("meta") or {}) if isinstance(raw.get("meta"), dict) else {}
    name = str(raw["name"]) if isinstance(raw.get("name"), str) else None

    if isinstance(raw.get("projected"), dict) or (
        name is not None and "version" not in raw and raw.get("kind") != "demo_tape"
    ):
        return EventDocument(
            kind=DocumentKind.TURN_FIXTURE,
            events=events,
            name=name,
            meta=meta,
            has_pacing=has_pacing,
            raw=raw,
        )

    if "version" in raw or isinstance(raw.get("meta"), dict):
        return EventDocument(
            kind=DocumentKind.TAPE,
            events=events,
            name=name or (str(meta["title"]) if isinstance(meta.get("title"), str) else None),
            meta=meta,
            has_pacing=has_pacing,
            raw=raw,
        )

    return EventDocument(
        kind=DocumentKind.BARE_EVENTS,
        events=events,
        name=name,
        meta=meta,
        has_pacing=has_pacing,
        raw=raw,
    )
