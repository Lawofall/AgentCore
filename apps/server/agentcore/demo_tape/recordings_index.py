"""List / search live-stream recording files under ``demos/recordings/`` (dev-only).

Recordings are named ``<message_id>.json``; this module surfaces meta so operators
can find "which conversation / what content / when" without opening every file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentcore.demo_tape.recorder import recordings_dir
from agentcore.demo_tape.schema import event_type


@dataclass(frozen=True)
class RecordingSummary:
    path: Path
    message_id: str
    conversation_id: str
    recorded_at: str
    segments: int
    events: int
    duration_ms: int
    snippet: str

    def matches(self, query: str) -> bool:
        q = query.strip().lower()
        if not q:
            return True
        hay = " ".join(
            [
                self.message_id,
                self.conversation_id,
                self.recorded_at,
                self.snippet,
                self.path.name,
            ]
        ).lower()
        return q in hay


def _snippet_from_recording(data: dict[str, Any], *, limit: int = 80) -> str:
    """Best-effort text peek: first content_delta / tool result / team title."""
    for segment in data.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        for ev in segment.get("events") or []:
            if not isinstance(ev, dict):
                continue
            et = event_type(ev)
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            text = ""
            if et == "content_delta":
                text = str(payload.get("delta") or payload.get("text") or "")
            elif et in ("tool_use_end", "tool_result"):
                text = str(payload.get("summary") or payload.get("name") or "")
            text = " ".join(text.split())
            if text:
                return text[:limit] + ("…" if len(text) > limit else "")
    return ""


def _duration_ms(data: dict[str, Any]) -> int:
    last = 0
    base_wall: int | None = None
    for segment in data.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        wall_t0 = segment.get("wall_t0_ms")
        offset = 0
        if isinstance(wall_t0, int):
            if base_wall is None:
                base_wall = wall_t0
            offset = max(0, wall_t0 - base_wall)
        for ev in segment.get("events") or []:
            if not isinstance(ev, dict):
                continue
            t = offset + int(ev.get("t_ms") or 0)
            if t > last:
                last = t
    return last


def summarize_recording(path: Path) -> RecordingSummary | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    segments = [s for s in (data.get("segments") or []) if isinstance(s, dict)]
    events = sum(len(s.get("events") or []) for s in segments)
    message_id = str(meta.get("message_id") or path.stem)
    return RecordingSummary(
        path=path,
        message_id=message_id,
        conversation_id=str(meta.get("conversation_id") or ""),
        recorded_at=str(meta.get("recorded_at") or ""),
        segments=len(segments),
        events=events,
        duration_ms=_duration_ms(data),
        snippet=_snippet_from_recording(data),
    )


def list_recordings(
    *,
    directory: Path | None = None,
    query: str = "",
) -> list[RecordingSummary]:
    root = directory if directory is not None else recordings_dir()
    if not root.is_dir():
        return []
    rows: list[RecordingSummary] = []
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        summary = summarize_recording(path)
        if summary is None:
            continue
        if summary.matches(query):
            rows.append(summary)
    return rows


def format_recording_table(rows: list[RecordingSummary]) -> str:
    if not rows:
        return "(no recordings)"
    lines = [
        f"{'recorded_at':<22} {'events':>6} {'segs':>4} {'dur_s':>6}  "
        f"{'message_id':<36}  conversation_id  snippet"
    ]
    for r in rows:
        dur_s = f"{r.duration_ms / 1000:.0f}" if r.duration_ms else "-"
        lines.append(
            f"{(r.recorded_at or '-'):<22} {r.events:>6} {r.segments:>4} {dur_s:>6}  "
            f"{r.message_id:<36}  {r.conversation_id or '-'}  {r.snippet or '-'}"
        )
    return "\n".join(lines)
