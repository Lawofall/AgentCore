"""Dev-only live-stream tape recorder (录制层).

Under ``DEMO_TAPE_RECORD_ENABLED`` an :class:`~agentcore.runtime.events.EventSink`
emit tap captures every live turn's SSE stream — exactly what the client saw, wall-
clock timed, INCLUDING the EPHEMERAL liveliness (typing deltas, ``tool_progress``
composing heartbeats, ``tool_use_progress`` phases) that ``turn_journal`` never
stores. That recording is the tape source: ``build_tape_from_recording`` cuts it
verbatim, replacing the retired journal-reconstruction heuristics.

Shapes
------
- One recording per turn, keyed by the assistant ``message_id``; a SEGMENT per
  stream leg (send, then each resume — every leg is its own EventSink).
- On-disk path: cloud default ``demos/recordings/<message_id>.json``; sidecar
  ``install_recorder(path=…)`` → ``<userData>/sidecar/recordings/<message_id>.json``.
  Same document shape::

      {"version": 2, "kind": "demo_tape_recording",
       "meta": {"conversation_id", "message_id", "recorded_at"},
       "segments": [{"wall_t0_ms", "events": [{"type","payload","timestamp","t_ms"}]}]}

  Document discriminator ``kind: demo_tape_recording`` is unrelated to SSE event
  fields. Event elements align with the online SSE / conformance contract
  (``type`` + ``timestamp``) plus pacing supersets (``t_ms``). Legacy v1
  recordings used ``kind``/``ts``; ``load_recording`` alias-compat on read.
- ``t_ms`` is the offset from the segment's start; ``wall_t0_ms`` anchors segments
  on one global timeline (the human decision gap at a pause survives as a real gap).

The recording is flushed on every ``message_end`` (pause AND terminal), so a paused
turn is already on disk before resume; a resume after a server restart hydrates the
flushed segments and appends. Terminal ``message_end`` still flush+pops the in-memory
recording (and drops the sink→segment binding); a persist-after-pipeline tail
(``title_generated`` / workspace snapshot, emitted after ``message_end``)
re-hydrates from disk, appends a new segment, and flushes again so the tail
lands in ``demos/recordings/``. Purely
observational: a tap failure never reaches the turn (the sink guards the call).
Recordings never carry a ``projected`` oracle.
"""

from __future__ import annotations

import copy
import json
import time
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentcore.config import settings
from agentcore.config.paths import PROJECT_ROOT
from agentcore.core.logging import get_logger
from agentcore.demo_tape.schema import RECORDING_FORMAT_VERSION, normalize_tape_event
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.events import sink as sink_module
from agentcore.runtime.events.types import SSEEvent

logger = get_logger(__name__)


@dataclass(eq=False)
class _Segment:
    wall_t0_ms: int
    mono_t0: float
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"wall_t0_ms": self.wall_t0_ms, "events": self.events}


@dataclass(eq=False)
class _Recording:
    conversation_id: str
    message_id: str
    # Segments hydrated from a previously-flushed file (server restarted between
    # the paused send leg and the resume leg) — already in serialized form.
    prior_segments: list[dict[str, Any]] = field(default_factory=list)
    segments: list[_Segment] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": RECORDING_FORMAT_VERSION,
            "kind": "demo_tape_recording",
            "meta": {
                "conversation_id": self.conversation_id,
                "message_id": self.message_id,
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            "segments": [*self.prior_segments, *(s.to_json() for s in self.segments)],
        }


_recordings: dict[str, _Recording] = {}
# One segment per EventSink instance (per stream leg). Weak keys: a leaked sink
# never pins recorder state.
_segments: weakref.WeakKeyDictionary[EventSink, _Segment] = weakref.WeakKeyDictionary()
# Optional absolute/relative override from ``install_recorder(path=…)``
# (sidecar lands under ``<userData>/sidecar/recordings``). When set, wins over
# ``settings.demo_tape_recordings_dir`` and the cloud repo default.
_recordings_dir_override: Path | None = None


def recordings_dir() -> Path:
    if _recordings_dir_override is not None:
        return _recordings_dir_override
    raw = (settings.demo_tape_recordings_dir or "").strip()
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else PROJECT_ROOT / p
    return PROJECT_ROOT / "demos" / "recordings"


def recording_path(message_id: str) -> Path:
    return recordings_dir() / f"{message_id}.json"


def load_recording(path: Path) -> dict[str, Any]:
    """Load a recording; normalize event elements to ``type``/``timestamp`` in memory.

    Does not rewrite the on-disk file (legacy v1 ``kind``/``ts`` stays as stored).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "segments" not in data:
        raise ValueError(f"invalid recording file: {path}")
    segments_out: list[dict[str, Any]] = []
    for segment in data.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        events = [
            normalize_tape_event(ev)
            for ev in (segment.get("events") or [])
            if isinstance(ev, dict)
        ]
        segments_out.append({**segment, "events": events})
    return {**data, "segments": segments_out}


def _hydrate_prior_segments(message_id: str) -> list[dict[str, Any]]:
    path = recording_path(message_id)
    if not path.exists():
        return []
    try:
        return list(load_recording(path).get("segments") or [])
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.warning(
            "demo_tape.recording_hydrate_failed", message_id=message_id, error=str(e)
        )
        return []


def _flush(recording: _Recording) -> None:
    path = recording_path(recording.message_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(recording.to_json(), ensure_ascii=False) + "\n", encoding="utf-8"
    )
    logger.info(
        "demo_tape.recording_flushed",
        message_id=recording.message_id,
        segments=len(recording.prior_segments) + len(recording.segments),
        events=sum(len(s.events) for s in recording.segments),
        path=str(path),
    )


def _tap(sink: EventSink, event: SSEEvent) -> None:
    message_id = sink.message_id
    conversation_id = sink.conversation_id
    if not message_id or not conversation_id:
        return  # pre-bind route chrome (turn_saved / early errors) — not tape material
    recording = _recordings.get(message_id)
    if recording is None:
        recording = _Recording(
            conversation_id=conversation_id,
            message_id=message_id,
            prior_segments=_hydrate_prior_segments(message_id),
        )
        _recordings[message_id] = recording
    segment = _segments.get(sink)
    if segment is None:
        segment = _Segment(wall_t0_ms=int(time.time() * 1000), mono_t0=time.monotonic())
        recording.segments.append(segment)
        _segments[sink] = segment
    segment.events.append(
        {
            "type": event.type.value,
            "payload": copy.deepcopy(event.payload),
            "timestamp": event.timestamp or None,
            "t_ms": int((time.monotonic() - segment.mono_t0) * 1000),
        }
    )
    if event.type is EventType.MESSAGE_END:
        _flush(recording)
        finish = str((event.payload or {}).get("finish_reason") or "")
        if finish != "paused":  # terminal → flush+pop; post-turn may re-hydrate
            _recordings.pop(message_id, None)
            # Drop sink→segment so a persist-after-pipeline tail opens a fresh
            # segment on the re-hydrated recording (otherwise events would append
            # to an orphaned segment no longer listed on the new _Recording).
            _segments.pop(sink, None)
    elif event.type in (
        EventType.TITLE_GENERATED,
        EventType.WORKSPACE_SNAPSHOT_DONE,
        EventType.WORKSPACE_SNAPSHOT_FAILED,
    ):
        # Post-turn tail after terminal message_end (re-hydrated recording).
        _flush(recording)
        _recordings.pop(message_id, None)


def install_recorder(*, path: str | Path | None = None) -> None:
    """Arm the emit tap (call from app startup when DEMO_TAPE_RECORD_ENABLED).

    ``path`` overrides the cloud default (``demos/recordings`` / env
    ``DEMO_TAPE_RECORDINGS_DIR``). Sidecar passes ``<dataDir>/recordings``.
    """
    global _recordings_dir_override
    if path is not None:
        p = Path(path)
        _recordings_dir_override = p if p.is_absolute() else PROJECT_ROOT / p
    else:
        _recordings_dir_override = None
    target = recordings_dir()
    target.mkdir(parents=True, exist_ok=True)
    sink_module.set_emit_tap(_tap)
    logger.info("demo_tape.recorder_installed", dir=str(target))


def uninstall_recorder() -> None:
    """Disarm the tap and drop in-memory state (shutdown / tests)."""
    global _recordings_dir_override
    sink_module.set_emit_tap(None)
    _recordings.clear()
    _recordings_dir_override = None
