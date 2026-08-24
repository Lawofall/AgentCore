"""Unit tests for demo tape recording / export / pacing / player (dev-only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcore.demo_tape.binding import (
    LOCAL_SESSION_BOUND_MSG,
    conversation_is_cloud,
    peek_binding,
    resolve_binding,
    write_binding,
)
from agentcore.demo_tape.export import (
    TapeExportRefusedError,
    assert_export_allowed,
    build_tape_from_recording,
    load_tape,
    write_tape,
)
from agentcore.demo_tape.identity import (
    INTERACTION_ID_KEYS,
    remint_interaction_ids,
    replay_interaction_id,
)
from agentcore.demo_tape.pacing import sleep_ms_for_gap
from agentcore.demo_tape.recordings_index import (
    format_recording_table,
    list_recordings,
    summarize_recording,
)
from agentcore.demo_tape.sanitize import (
    DEMO_MEMORY_PLACEHOLDER,
    SYNTHETIC_MEMORY_RULES,
    IngestScanError,
    assert_ingest_clean,
    sanitize_and_scan_events,
    sanitize_memory_in_text,
    scan_events_for_ingest_residue,
)
from agentcore.demo_tape.schema import (
    CLIENT_TOOL_REQUIRED_KINDS,
    RECORDING_FORMAT_VERSION,
    TAPE_EXCLUDED_KINDS,
    TAPE_FORMAT_VERSION,
    TAPE_UNWIRED_PAUSE_KINDS,
    TAPE_WIRED_PAUSE_KINDS,
    event_timestamp,
    event_type,
    is_demo_tape_frame,
    normalize_tape_event,
    persisted_captain_content_from_events,
)
from agentcore.runtime.checkpoints import CheckpointDecision, CheckpointResponse
from agentcore.runtime.events import EventSink, EventType, FinishReason
from agentcore.runtime.events.types import SSEEvent
from agentcore.runtime.suspension import (
    AskUserSuspension,
    PlanReviewSuspension,
)
from scripts.demo_tape_bind import build_parser


def _ev(kind: str, payload: dict | None = None) -> SSEEvent:
    return SSEEvent(type=EventType(kind), payload=payload or {})


def test_tape_excluded_kinds_cut_lifecycle_settlements_and_client_ops():
    # Turn lifecycle is the player's own; settlements are re-emitted live.
    assert "message_start" in TAPE_EXCLUDED_KINDS
    assert "message_end" in TAPE_EXCLUDED_KINDS
    assert "team_preview_resolved" in TAPE_EXCLUDED_KINDS
    assert "team_preview_required" in TAPE_EXCLUDED_KINDS
    assert "approval_resolved" in TAPE_EXCLUDED_KINDS
    # followups_generated stays cut — chips ride meta.followups, not the event stream.
    assert "followups_generated" in TAPE_EXCLUDED_KINDS
    # Client-tool requests must never replay (real side effects on the desktop).
    assert "workspace_op_required" in TAPE_EXCLUDED_KINDS
    assert "desktop_notify_required" in TAPE_EXCLUDED_KINDS
    assert "external_mount_readonly_required" in TAPE_EXCLUDED_KINDS
    assert "external_mount_readonly_required" in CLIENT_TOOL_REQUIRED_KINDS
    # Content / liveliness stays. Living cold/hot required cards stay on tape.
    assert "content_delta" not in TAPE_EXCLUDED_KINDS
    assert "tool_progress" not in TAPE_EXCLUDED_KINDS
    assert "checkpoint_required" not in TAPE_EXCLUDED_KINDS
    assert "approval_required" not in TAPE_EXCLUDED_KINDS


def test_conversation_is_cloud_mirrors_desktop_routing():
    ok, reason = conversation_is_cloud(
        local_container_root_id=None,
        local_root_id=None,
        folder_local_root_id=None,
        folder_id=None,
    )
    assert ok and "bare cloud" in reason

    ok, reason = conversation_is_cloud(
        local_container_root_id="root-1",
        local_root_id=None,
        folder_local_root_id=None,
        folder_id=None,
    )
    assert not ok and "local container" in reason

    ok, reason = conversation_is_cloud(
        local_container_root_id=None,
        local_root_id=None,
        folder_local_root_id="folder-root",
        folder_id="f1",
    )
    assert not ok and "local-mode" in reason

    ok, reason = conversation_is_cloud(
        local_container_root_id="ignored",
        local_root_id=None,
        folder_local_root_id=None,
        folder_id="f-cloud",
    )
    assert ok and "cloud-mode" in reason


def test_write_binding_and_bind_parser(tmp_path: Path, monkeypatch):
    from agentcore.demo_tape import binding as binding_mod

    monkeypatch.setattr(binding_mod, "bindings_path", lambda: tmp_path / "bindings.json")
    path = write_binding("cid-1", tape="demos/tapes/x.json", speed=4.0, max_gap_ms=2000)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["cid-1"] == {
        "tape": "demos/tapes/x.json",
        "turn_index": 0,
        "speed": 4.0,
        "max_gap_ms": 2000,
    }

    p = build_parser()
    args = p.parse_args(
        ["--latest", "--tape", "demos/tapes/x.json", "--speed", "4", "--max-gap-ms", "2000"]
    )
    assert args.latest is True
    assert args.conversation_id is None
    assert args.include_local is False

    args2 = p.parse_args(["abc-uuid", "--tape", "demos/tapes/x.json", "--include-local"])
    assert args2.conversation_id == "abc-uuid"
    assert args2.include_local is True


def test_peek_binding_ignores_replay_flag(tmp_path: Path, monkeypatch):
    from agentcore.config import settings
    from agentcore.demo_tape import binding as binding_mod

    monkeypatch.setattr(binding_mod, "bindings_path", lambda: tmp_path / "bindings.json")
    write_binding("cid-local", tape="demos/tapes/x.json")
    monkeypatch.setattr(settings, "demo_tape_replay_enabled", False)
    assert peek_binding("cid-local") is not None
    assert resolve_binding("cid-local") is None
    monkeypatch.setattr(settings, "demo_tape_replay_enabled", True)
    bound = resolve_binding("cid-local")
    assert bound is not None
    assert bound.tape_path.name == "x.json"
    assert "sidecar" in LOCAL_SESSION_BOUND_MSG


def test_list_recordings_indexes_meta_and_snippet(tmp_path: Path):
    rec = {
        "version": RECORDING_FORMAT_VERSION,
        "kind": "demo_tape_recording",
        "meta": {
            "conversation_id": "conv-abc",
            "message_id": "msg-xyz",
            "recorded_at": "2026-07-17T01:02:03Z",
        },
        "segments": [
            {
                "wall_t0_ms": 1000,
                "events": [
                    {
                        "type": "content_delta",
                        "payload": {"delta": "搜索下最新的LV起诉茉莉奶白"},
                        "timestamp": None,
                        "t_ms": 50,
                    },
                    {
                        "type": "content_delta",
                        "payload": {"delta": "更多"},
                        "timestamp": None,
                        "t_ms": 1200,
                    },
                ],
            }
        ],
    }
    path = tmp_path / "msg-xyz.json"
    path.write_text(json.dumps(rec), encoding="utf-8")

    summary = summarize_recording(path)
    assert summary is not None
    assert summary.conversation_id == "conv-abc"
    assert summary.message_id == "msg-xyz"
    assert summary.events == 2
    assert summary.duration_ms == 1200
    assert "茉莉" in summary.snippet

    rows = list_recordings(directory=tmp_path, query="茉莉")
    assert len(rows) == 1
    assert "msg-xyz" in format_recording_table(rows)
    assert list_recordings(directory=tmp_path, query="no-such") == []


@pytest.mark.asyncio
async def test_sidecar_rejects_tape_bound_local_session(tmp_path: Path, monkeypatch):
    """Misbound local session must not silently become a normal AI turn."""
    import asyncio
    import json as _json
    from typing import Any

    from agentcore.config import settings
    from agentcore.demo_tape import binding as binding_mod
    from agentcore.sidecar.server import SidecarServer

    monkeypatch.setattr(binding_mod, "bindings_path", lambda: tmp_path / "bindings.json")
    monkeypatch.setattr(settings, "demo_tape_replay_enabled", True)
    write_binding("conv-bound", tape="demos/tapes/x.json")

    sent: list[dict[str, Any]] = []

    async def write_line(line: str) -> None:
        sent.append(_json.loads(line))

    server = SidecarServer(write_line)
    await server.handle_line(
        _json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "userId": "u",
                    "workspaceRoot": str(tmp_path),
                    "approvalsEnabled": True,
                    "dataDir": str(tmp_path / "data"),
                    "inference": {
                        "baseUrl": "http://test.local/v1/inference/v1",
                        "apiKey": "test-inference-tok",
                        "model": "test-model",
                    },
                },
            }
        )
    )
    await server.handle_line(
        _json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "startTurn",
                "params": {
                    "turnId": "t1",
                    "conversationId": "conv-bound",
                    "userMessage": "hello",
                },
            }
        )
    )
    # Let the error reply land.
    await asyncio.sleep(0)
    err = next((m for m in sent if m.get("id") == 2 and "error" in m), None)
    assert err is not None
    assert "sidecar" in err["error"]["message"]
    assert "x.json" in err["error"]["message"]


def test_pacing_speed_and_cap():
    assert sleep_ms_for_gap(gap_ms=0, speed=1.0, max_gap_ms=3000) == 0.0
    assert sleep_ms_for_gap(gap_ms=1000, speed=1.0, max_gap_ms=3000) == pytest.approx(1.0)
    assert sleep_ms_for_gap(gap_ms=1000, speed=4.0, max_gap_ms=3000) == pytest.approx(0.25)
    assert sleep_ms_for_gap(gap_ms=60_000, speed=1.0, max_gap_ms=2000) == pytest.approx(2.0)


def test_pacing_step_never_rewinds_clock():
    from agentcore.demo_tape.pacing import pacing_step

    gap, prev = pacing_step(prev_t_ms=None, t_ms=0)
    assert gap == 0 and prev == 0

    gap, prev = pacing_step(prev_t_ms=prev, t_ms=3000)
    assert gap == 3000 and prev == 3000

    # Synthetic overshoot then journal jump-back must not rewind.
    gap, prev = pacing_step(prev_t_ms=10_000, t_ms=12_000)
    assert gap == 2000 and prev == 12_000
    gap, prev = pacing_step(prev_t_ms=prev, t_ms=11_000)
    assert gap == 0 and prev == 12_000
    gap, prev = pacing_step(prev_t_ms=prev, t_ms=20_000)
    assert gap == 8000 and prev == 20_000


# ── 回放身份 ≠ 录制身份 ────────────────────────────────────────────────────


def test_remint_interaction_ids_maps_all_interaction_keys_deterministically():
    events = [
        {"kind": "checkpoint_required", "payload": {"checkpoint_id": "cp-1", "question": "q"}},
        {"kind": "approval_required", "payload": {"approval_id": "ap-1"}},
        {"kind": "run_escalation", "payload": {"escalation_id": "esc-1"}},
        {"kind": "interaction_orphaned", "payload": {"interaction_id": "cp-1"}},
        # Execution identities stay AS RECORDED (message-scoped + structured strings).
        {"kind": "run_started", "payload": {"run_id": "debate_x_r1_lv", "kind": "agent"}},
        {"kind": "content_delta", "payload": {"delta": "hi"}},
    ]
    out = remint_interaction_ids(events, message_id="m1")
    by_kind = {e["kind"]: e["payload"] for e in out}

    for kind, key, original in (
        ("checkpoint_required", "checkpoint_id", "cp-1"),
        ("approval_required", "approval_id", "ap-1"),
        ("run_escalation", "escalation_id", "esc-1"),
    ):
        minted = by_kind[kind][key]
        assert minted != original
        assert minted == replay_interaction_id(original, message_id="m1")

    # Same recorded id ⇒ same minted id (orphan still targets the reminted card).
    assert (
        by_kind["interaction_orphaned"]["interaction_id"]
        == by_kind["checkpoint_required"]["checkpoint_id"]
    )
    # Untouched events pass through unchanged (payload identity preserved).
    assert by_kind["run_started"]["run_id"] == "debate_x_r1_lv"
    assert out[5]["payload"] is events[5]["payload"]
    # Non-payload fields survive on touched events.
    assert by_kind["checkpoint_required"]["question"] == "q"
    # A different turn mints different ids.
    assert replay_interaction_id("cp-1", message_id="m1") != replay_interaction_id(
        "cp-1", message_id="m2"
    )
    assert {"checkpoint_id", "approval_id", "escalation_id"}.issubset(INTERACTION_ID_KEYS)


# ── recording → tape builder ─────────────────────────────────────────────


def test_build_tape_from_recording_stitches_segments_and_cuts_excluded():
    # Input uses legacy kind/ts dialect — cut must still work and emit type/timestamp.
    recording = {
        "version": 1,
        "kind": "demo_tape_recording",
        "meta": {"conversation_id": "conv-1", "message_id": "msg-1"},
        "segments": [
            {
                "wall_t0_ms": 1_000_000,
                "events": [
                    {"kind": "message_start", "payload": {}, "ts": None, "t_ms": 0},
                    {"kind": "reasoning_delta", "payload": {"delta": "想"}, "ts": None, "t_ms": 10},
                    {"kind": "content_delta", "payload": {"delta": "简介"}, "ts": None, "t_ms": 500},
                    {
                        "kind": "checkpoint_required",
                        "payload": {"checkpoint_id": "cp-src", "question": "继续？"},
                        "ts": None,
                        "t_ms": 900,
                    },
                    {
                        "kind": "message_end",
                        "payload": {"finish_reason": "paused"},
                        "ts": None,
                        "t_ms": 910,
                    },
                ],
            },
            {
                # 13s later on the wall clock — the human decision gap survives.
                "wall_t0_ms": 1_013_000,
                "events": [
                    {
                        "kind": "checkpoint_resolved",
                        "payload": {"checkpoint_id": "cp-src", "decision": "continue"},
                        "ts": None,
                        "t_ms": 0,
                    },
                    {
                        "kind": "run_output_delta",
                        "payload": {"run_id": "w1", "delta": "观点"},
                        "ts": None,
                        "t_ms": 100,
                    },
                    {"kind": "content_delta", "payload": {"delta": "汇总"}, "ts": None, "t_ms": 400},
                    {
                        "kind": "message_end",
                        "payload": {"finish_reason": "end_turn"},
                        "ts": None,
                        "t_ms": 450,
                    },
                ],
            },
        ],
    }
    doc = build_tape_from_recording(recording, meta={"title": "t"}, user_prompt="go")
    assert doc["version"] == TAPE_FORMAT_VERSION
    types = [e["type"] for e in doc["events"]]
    assert "message_start" not in types
    assert "message_end" not in types
    assert "checkpoint_resolved" not in types
    assert types == [
        "reasoning_delta",
        "content_delta",
        "checkpoint_required",
        "run_output_delta",
        "content_delta",
    ]
    assert all("kind" not in e and "ts" not in e for e in doc["events"])
    assert all("timestamp" in e for e in doc["events"])
    assert "followups" not in doc["meta"]  # no followups_generated in this fixture
    # Recording identities stay verbatim on the tape (the PLAYER remints per replay).
    card = next(e for e in doc["events"] if e["type"] == "checkpoint_required")
    assert card["payload"]["checkpoint_id"] == "cp-src"
    # Global timeline: segment 2 anchored 13s after segment 1's start.
    t = {(e["type"], e["payload"].get("delta")): e["t_ms"] for e in doc["events"]}
    assert t[("reasoning_delta", "想")] == 10
    assert t[("checkpoint_required", None)] == 900
    assert t[("run_output_delta", "观点")] == 13_100
    assert t[("content_delta", "汇总")] == 13_400
    assert doc["meta"]["user_prompt"] == "go"
    assert doc["meta"]["title"] == "t"
    assert doc["meta"]["source_message_id"] == "msg-1"
    assert doc["meta"]["event_count"] == 5
    assert doc["meta"]["duration_ms"] == 13_400
    # t_ms stays monotonic even under wall-clock jitter.
    ts = [e["t_ms"] for e in doc["events"]]
    assert ts == sorted(ts)


def test_build_tape_from_recording_clamps_wall_clock_jitter():
    recording = {
        "meta": {"conversation_id": "c", "message_id": "m"},
        "segments": [
            {
                "wall_t0_ms": 2_000,
                "events": [
                    {"kind": "content_delta", "payload": {"delta": "a"}, "t_ms": 0},
                    {"kind": "content_delta", "payload": {"delta": "b"}, "t_ms": 500},
                ],
            },
            {
                # Wall clock stepped BACK (NTP jitter) — must not rewind t_ms.
                "wall_t0_ms": 1_900,
                "events": [
                    {"kind": "content_delta", "payload": {"delta": "c"}, "t_ms": 0},
                ],
            },
        ],
    }
    doc = build_tape_from_recording(recording, user_prompt="go")
    ts = [e["t_ms"] for e in doc["events"]]
    assert ts == sorted(ts)
    assert "".join(e["payload"]["delta"] for e in doc["events"]) == "abc"


def test_write_and_load_tape(tmp_path: Path):
    doc = build_tape_from_recording(
        {
            "meta": {"conversation_id": "c", "message_id": "m"},
            "segments": [
                {
                    "wall_t0_ms": 0,
                    "events": [
                        {"kind": "content_delta", "payload": {"delta": "hi"}, "t_ms": 0}
                    ],
                }
            ],
        },
        meta={"title": "t"},
        user_prompt="hi",
    )
    path = tmp_path / "t.json"
    write_tape(path, doc)
    loaded = load_tape(path)
    assert loaded["version"] == TAPE_FORMAT_VERSION
    assert loaded["meta"]["user_prompt"] == "hi"
    assert len(loaded["events"]) == 1
    assert loaded["events"][0]["type"] == "content_delta"
    assert "kind" not in loaded["events"][0]


# ── field dialect: contract type/timestamp + legacy kind/ts alias ─────────


def test_normalize_tape_event_aliases_legacy_kind_ts():
    legacy = {
        "kind": "content_delta",
        "payload": {"delta": "x"},
        "ts": "2026-07-16T00:00:00.000Z",
        "t_ms": 10,
    }
    norm = normalize_tape_event(legacy)
    assert norm == {
        "type": "content_delta",
        "payload": {"delta": "x"},
        "timestamp": "2026-07-16T00:00:00.000Z",
        "t_ms": 10,
    }
    assert event_type(legacy) == "content_delta"
    assert event_timestamp(legacy) == "2026-07-16T00:00:00.000Z"
    # Contract fields win when both dialects are present.
    mixed = {"type": "run_started", "kind": "content_delta", "timestamp": "a", "ts": "b"}
    assert event_type(mixed) == "run_started"
    assert event_timestamp(mixed) == "a"


def test_load_tape_alias_compat_legacy_kind_ts_without_rewriting_disk(tmp_path: Path):
    """Stock v1 tapes (kind/ts) load/play via alias; on-disk file is not rewritten."""
    path = tmp_path / "legacy.json"
    on_disk = {
        "version": 1,
        "meta": {"user_prompt": "go", "title": "legacy"},
        "events": [
            {
                "kind": "run_started",
                "payload": {"run_id": "c1", "kind": "captain"},
                "ts": "2026-07-16T01:00:00.000Z",
                "t_ms": 0,
            },
            {
                "kind": "content_delta",
                "payload": {"delta": "hi"},
                "ts": None,
                "t_ms": 50,
            },
        ],
    }
    path.write_text(json.dumps(on_disk, ensure_ascii=False), encoding="utf-8")
    loaded = load_tape(path)
    assert loaded["version"] == 1  # disk version preserved in memory
    assert [e["type"] for e in loaded["events"]] == ["run_started", "content_delta"]
    assert loaded["events"][0]["timestamp"] == "2026-07-16T01:00:00.000Z"
    assert loaded["events"][1]["timestamp"] is None
    assert all("kind" not in e and "ts" not in e for e in loaded["events"])
    # Stock file untouched (read-time compat only — no migration rewrite).
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["events"][0]["kind"] == "run_started"
    assert "type" not in raw["events"][0]


@pytest.mark.asyncio
async def test_player_plays_legacy_kind_ts_events(monkeypatch):
    """Player accepts raw legacy dialect without going through load_tape."""
    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import play_tape_events
    from agentcore.runtime.journal.writer import TurnJournalWriter

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(player_mod, "pacing_sleep", fake_sleep)

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    events = [
        {
            "kind": "run_started",
            "payload": {"run_id": "c1", "kind": "captain"},
            "ts": "2026-07-16T02:00:00.000Z",
            "t_ms": 0,
        },
        {"kind": "content_delta", "payload": {"delta": "正文"}, "ts": None, "t_ms": 10},
    ]
    binding = TapeBinding(
        conversation_id="c", tape_path=Path("unused.json"), speed=1.0, max_gap_ms=50
    )
    # Unbound sink: offline replay must not arm StreamCheckpointer (non-UUID ids).
    sink = EventSink()
    writer = TurnJournalWriter(turn_id="m", conversation_id="c", trace_id="t" * 32)
    result = await play_tape_events(
        sink=sink,
        events=events,
        start_index=0,
        binding=binding,
        message_id="m",
        conversation_id="c",
        user_id="u",
        user_message="go",
        folder_id=None,
        journal_writer=writer,
    )
    assert result["finish_reason"] is FinishReason.END_TURN
    assert result["content"] == "正文"
    assert EventType.RUN_STARTED in [e.type for e in sink._history]
    assert EventType.CONTENT_DELTA in [e.type for e in sink._history]


@pytest.mark.asyncio
async def test_player_result_carries_evidence_ledger_and_cited_citations(monkeypatch):
    """回放落库两列（回归·重开会话 #rN 角标）：磁带只回放 evidence_ledger 事件、
    citations 被剪（TAPE_EXCLUDED_KINDS），旧 player 落库 result 两列皆空 → 纯水合重开时
    #rN 退化成原始文本。player 现须从台账按成稿 cited_ids 重建 citations + evidence_ledger。
    """
    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import play_tape_events
    from agentcore.runtime.journal.writer import TurnJournalWriter

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(player_mod, "pacing_sleep", fake_sleep)

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    def _entry(n: int) -> dict:
        return {
            "id": f"#r{n}",
            "url": f"https://example.com/{n}",
            "title": f"来源 {n}",
            "snippet": "",
            "site": "example.com",
            "date": "",
            "tier": "unknown",
            "query": "q",
            "deep_read": False,
            "registrant": "ceo",
            "citable": True,
        }

    events = [
        {"type": "run_started", "payload": {"run_id": "c1", "kind": "captain"}, "t_ms": 0},
        # 先增量登记 #r1，再用全量快照替换为 #r1/#r2/#r3（#r2 是未被引用的检索痕迹）。
        {"type": "evidence_ledger", "payload": {"delta": [_entry(1)]}, "t_ms": 5},
        {
            "type": "evidence_ledger",
            "payload": {"delta": [], "entries": [_entry(1), _entry(2), _entry(3)]},
            "t_ms": 10,
        },
        {"type": "content_delta", "payload": {"delta": "结论见#r1 与 #r3。"}, "t_ms": 15},
    ]
    binding = TapeBinding(
        conversation_id="c", tape_path=Path("unused.json"), speed=1.0, max_gap_ms=50
    )
    sink = EventSink()
    writer = TurnJournalWriter(turn_id="m", conversation_id="c", trace_id="t" * 32)
    result = await play_tape_events(
        sink=sink,
        events=events,
        start_index=0,
        binding=binding,
        message_id="m",
        conversation_id="c",
        user_id="u",
        user_message="go",
        folder_id=None,
        journal_writer=writer,
    )
    assert result["finish_reason"] is FinishReason.END_TURN

    # 全量台账（含未引用的 #r2）落 evidence_ledger 列 → 重开水合后 knownLedgerIds 命中。
    ledger = result["evidence_ledger"]
    assert ledger is not None
    assert [e["id"] for e in ledger] == ["#r1", "#r2", "#r3"]

    # citations = 成稿引用集（仅 #r1/#r3，按首次出现序）；未引用的 #r2 不进来源卡。
    assert result["cited_ids"] == ["#r1", "#r3"]
    cites = result["citations"]
    assert cites is not None
    assert [c["id"] for c in cites] == ["#r1", "#r3"]
    assert all(c["url"] for c in cites)


def test_build_tape_from_legacy_recording_emits_contract_fields():
    """Cutting a v1 kind/ts recording yields a v2 type/timestamp tape."""
    recording = {
        "version": 1,
        "kind": "demo_tape_recording",
        "meta": {"conversation_id": "c", "message_id": "m"},
        "segments": [
            {
                "wall_t0_ms": 0,
                "events": [
                    {
                        "kind": "content_delta",
                        "payload": {"delta": "a"},
                        "ts": "2026-07-16T03:00:00.000Z",
                        "t_ms": 0,
                    },
                    {
                        "kind": "message_end",
                        "payload": {"finish_reason": "end_turn"},
                        "ts": None,
                        "t_ms": 1,
                    },
                ],
            }
        ],
    }
    doc = build_tape_from_recording(recording, user_prompt="p")
    assert doc["version"] == TAPE_FORMAT_VERSION
    assert doc["events"] == [
        {
            "type": "content_delta",
            "payload": {"delta": "a"},
            "timestamp": "2026-07-16T03:00:00.000Z",
            "t_ms": 0,
        }
    ]


# ── recorder tap ─────────────────────────────────────────────────────────


def _install_recorder_at(monkeypatch, tmp_path: Path):
    from agentcore.config import settings
    from agentcore.demo_tape import recorder

    monkeypatch.setattr(
        settings, "demo_tape_recordings_dir", str(tmp_path / "recordings")
    )
    recorder.install_recorder()
    return recorder


def test_recorder_taps_bound_sinks_and_flushes_on_message_end(monkeypatch, tmp_path):
    from agentcore.runtime.events import message_end, message_start

    recorder = _install_recorder_at(monkeypatch, tmp_path)
    try:
        # Unbound sink (pre-bind route chrome) → not recorded.
        loose = EventSink()
        loose.emit(_ev("turn_saved", {"user_message_id": "u1"}))

        sink = EventSink(conversation_id="conv-r", message_id="msg-r")
        sink.emit(message_start("msg-r", conversation_id="conv-r"))
        sink.emit(_ev("content_delta", {"delta": "你好"}))
        sink.emit(message_end(FinishReason.PAUSED))
        path = recorder.recording_path("msg-r")
        assert path.exists()
        doc = recorder.load_recording(path)
        assert doc["version"] == RECORDING_FORMAT_VERSION
        assert doc["meta"]["conversation_id"] == "conv-r"
        segment = doc["segments"][0]
        types = [e["type"] for e in segment["events"]]
        assert types == ["message_start", "content_delta", "message_end"]
        assert all("kind" not in e and "timestamp" in e for e in segment["events"])
        # On-disk flush also uses contract fields (not just load-time normalize).
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["version"] == RECORDING_FORMAT_VERSION
        assert raw["segments"][0]["events"][0]["type"] == "message_start"
        assert "kind" not in raw["segments"][0]["events"][0]
        # Paused → recording stays open awaiting the resume leg.
        assert "msg-r" in recorder._recordings

        resume_sink = EventSink(conversation_id="conv-r", message_id="msg-r")
        resume_sink.emit(_ev("content_delta", {"delta": "继续"}))
        resume_sink.emit(message_end(FinishReason.END_TURN))
        doc = recorder.load_recording(path)
        assert len(doc["segments"]) == 2
        # Terminal → recording complete and dropped from memory.
        assert "msg-r" not in recorder._recordings
        # The unbound sink produced no recording file at all.
        files = sorted(p.name for p in recorder.recordings_dir().glob("*.json"))
        assert files == ["msg-r.json"]
    finally:
        recorder.uninstall_recorder()


def test_recorder_hydrates_flushed_segments_after_restart(monkeypatch, tmp_path):
    """Server restarted between the paused send leg and the resume leg: the resume
    leg must append to the flushed file, not overwrite it."""
    from agentcore.runtime.events import message_end

    recorder = _install_recorder_at(monkeypatch, tmp_path)
    try:
        sink = EventSink(conversation_id="conv-h", message_id="msg-h")
        sink.emit(_ev("content_delta", {"delta": "前段"}))
        sink.emit(message_end(FinishReason.PAUSED))
        # Simulate restart: in-memory state gone, file remains.
        recorder._recordings.clear()

        resume_sink = EventSink(conversation_id="conv-h", message_id="msg-h")
        resume_sink.emit(_ev("content_delta", {"delta": "后段"}))
        resume_sink.emit(message_end(FinishReason.END_TURN))

        doc = recorder.load_recording(recorder.recording_path("msg-h"))
        assert len(doc["segments"]) == 2
        deltas = [
            e["payload"].get("delta")
            for s in doc["segments"]
            for e in s["events"]
            if e["type"] == "content_delta"
        ]
        assert deltas == ["前段", "后段"]
    finally:
        recorder.uninstall_recorder()


def test_recorder_hydrates_legacy_kind_ts_segments_after_restart(monkeypatch, tmp_path):
    """Prior flushed v1 recording (kind/ts) still appends cleanly on resume."""
    from agentcore.runtime.events import message_end

    recorder = _install_recorder_at(monkeypatch, tmp_path)
    try:
        path = recorder.recording_path("msg-legacy")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "kind": "demo_tape_recording",
                    "meta": {
                        "conversation_id": "conv-l",
                        "message_id": "msg-legacy",
                    },
                    "segments": [
                        {
                            "wall_t0_ms": 1_000,
                            "events": [
                                {
                                    "kind": "content_delta",
                                    "payload": {"delta": "旧段"},
                                    "ts": None,
                                    "t_ms": 0,
                                },
                                {
                                    "kind": "message_end",
                                    "payload": {"finish_reason": "paused"},
                                    "ts": None,
                                    "t_ms": 1,
                                },
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        resume_sink = EventSink(conversation_id="conv-l", message_id="msg-legacy")
        resume_sink.emit(_ev("content_delta", {"delta": "新段"}))
        resume_sink.emit(message_end(FinishReason.END_TURN))

        doc = recorder.load_recording(path)
        assert len(doc["segments"]) == 2
        deltas = [
            e["payload"].get("delta")
            for s in doc["segments"]
            for e in s["events"]
            if e["type"] == "content_delta"
        ]
        assert deltas == ["旧段", "新段"]
        # Hydrated prior segment normalized in memory; new segment written as type/.
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["version"] == RECORDING_FORMAT_VERSION
        assert raw["segments"][0]["events"][0]["type"] == "content_delta"
        assert raw["segments"][1]["events"][0]["type"] == "content_delta"
    finally:
        recorder.uninstall_recorder()


def test_recorder_captures_post_turn_title_after_terminal_message_end(
    monkeypatch, tmp_path
):
    """Terminal message_end flush+pops; persist-after-pipeline tail still lands on disk."""
    from agentcore.runtime.events import message_end, message_start, title_generated

    recorder = _install_recorder_at(monkeypatch, tmp_path)
    try:
        sink = EventSink(conversation_id="conv-fu", message_id="msg-fu")
        sink.emit(message_start("msg-fu", conversation_id="conv-fu"))
        sink.emit(_ev("content_delta", {"delta": "答复"}))
        sink.emit(message_end(FinishReason.END_TURN))
        assert "msg-fu" not in recorder._recordings

        sink.emit(title_generated("新标题", conversation_id="conv-fu"))
        assert "msg-fu" not in recorder._recordings

        doc = recorder.load_recording(recorder.recording_path("msg-fu"))
        assert len(doc["segments"]) == 2
        types = [e["type"] for s in doc["segments"] for e in s["events"]]
        assert types[-1] == "title_generated"
        assert doc["segments"][-1]["events"][-1]["payload"]["title"] == "新标题"
    finally:
        recorder.uninstall_recorder()


def test_build_tape_lifts_followups_generated_into_meta():
    recording = {
        "version": 2,
        "kind": "demo_tape_recording",
        "meta": {"conversation_id": "c", "message_id": "m"},
        "segments": [
            {
                "wall_t0_ms": 0,
                "events": [
                    {"type": "content_delta", "payload": {"delta": "hi"}, "timestamp": None, "t_ms": 0},
                    {
                        "type": "message_end",
                        "payload": {"finish_reason": "end_turn"},
                        "timestamp": None,
                        "t_ms": 1,
                    },
                ],
            },
            {
                "wall_t0_ms": 50,
                "events": [
                    {
                        "type": "followups_generated",
                        "payload": {
                            "conversation_id": "c",
                            "message_id": "m",
                            "followups": ["A", "B"],
                        },
                        "timestamp": None,
                        "t_ms": 0,
                    },
                ],
            },
        ],
    }
    doc = build_tape_from_recording(recording, user_prompt="p")
    assert doc["meta"]["followups"] == ["A", "B"]
    assert "followups_generated" not in [e["type"] for e in doc["events"]]
    # Caller override wins.
    doc2 = build_tape_from_recording(
        recording, meta={"followups": ["X"]}, user_prompt="p"
    )
    assert doc2["meta"]["followups"] == ["X"]


# ── tap 录制 → 回放闭环（合成回合） ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_recording_to_tape_to_replay_closed_loop(monkeypatch, tmp_path: Path):
    """合成回合闭环：真实 EventSink 发流 → tap 录制 → 出磁带 → player 回放。

    覆盖录制层重构的验收面：磁带无生命周期/结算事件、冷闸暂停点如期挂起、回放身份
    重铸（≠录制 id）、resume 后正文/辩手输出逐字节回放、live resolve 恰好一次。
    旧磁带 meta.followups 仍可导出；回放不再挂到 result（chips 已下线）。
    """
    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import continue_tape_turn, play_tape_events
    from agentcore.runtime.events import (
        checkpoint_resolved,
        message_end,
        message_start,
    )
    from agentcore.runtime.journal.writer import TurnJournalWriter

    chips = ["下一步甲", "下一步乙"]
    recorder = _install_recorder_at(monkeypatch, tmp_path)
    try:
        # —— Source run (send leg): brief → checkpoint card → paused. ——
        send_sink = EventSink(conversation_id="src-conv", message_id="src-msg")
        send_sink.emit(message_start("src-msg", conversation_id="src-conv"))
        send_sink.emit(_ev("reasoning_delta", {"delta": "先搜索案件。"}))
        send_sink.emit(
            _ev(
                "tool_use_start",
                {"tool_call_id": "t1", "tool_name": "web_search", "arguments": {}},
            )
        )
        send_sink.emit(_ev("tool_use_end", {"tool_call_id": "t1", "tool_name": "web_search"}))
        send_sink.emit(_ev("content_delta", {"delta": "案情简介。"}))
        send_sink.emit(_ev("tool_progress", {"tool_name": "debate", "chars": 42}))
        send_sink.emit(
            _ev(
                "checkpoint_required",
                {
                    "checkpoint_id": "cp-src",
                    "conversation_id": "src-conv",
                    "question": "继续？",
                },
            )
        )
        send_sink.emit(message_end(FinishReason.PAUSED))

        # —— Source run (resume leg): live resolve → debate → wrap → end. ——
        resume_sink = EventSink(conversation_id="src-conv", message_id="src-msg")
        resume_sink.emit(
            checkpoint_resolved(checkpoint_id="cp-src", decision="continue", note="")
        )
        resume_sink.emit(_ev("run_plan", {"execution_id": "ex1", "runs": []}))
        resume_sink.emit(_ev("run_started", {"run_id": "w1", "agent_id": "w1", "kind": "agent"}))
        resume_sink.emit(_ev("run_output_delta", {"run_id": "w1", "agent_id": "w1", "delta": "辩手观点。"}))
        resume_sink.emit(_ev("run_completed", {"run_id": "w1", "agent_id": "w1"}))
        resume_sink.emit(_ev("content_delta", {"delta": "最终汇总。"}))
        resume_sink.emit(message_end(FinishReason.END_TURN))

        recording = recorder.load_recording(recorder.recording_path("src-msg"))
    finally:
        recorder.uninstall_recorder()

    tape_doc = build_tape_from_recording(
        recording,
        meta={"title": "闭环", "followups": chips},
        user_prompt="搜索并辩论",
    )
    assert tape_doc["version"] == TAPE_FORMAT_VERSION
    assert tape_doc["meta"]["followups"] == chips
    types = [e["type"] for e in tape_doc["events"]]
    assert "message_start" not in types
    assert "message_end" not in types
    assert "checkpoint_resolved" not in types
    assert "followups_generated" not in types
    assert "tool_progress" in types  # EPHEMERAL liveliness recorded verbatim
    assert all("kind" not in e for e in tape_doc["events"])
    tape_path = tmp_path / "closed-loop.json"
    write_tape(tape_path, tape_doc)

    # —— Replay through the real player. ——
    saved: list = []

    async def fake_save(suspension):
        saved.append(suspension)

    monkeypatch.setattr(player_mod, "save_paused_turn", fake_save)

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    binding = TapeBinding(
        conversation_id="replay-conv", tape_path=tape_path, speed=100.0, max_gap_ms=20
    )
    events = list(load_tape(tape_path)["events"])
    sink = EventSink(conversation_id="replay-conv", message_id="replay-msg")
    writer = TurnJournalWriter(
        turn_id="replay-msg", conversation_id="replay-conv", trace_id="c" * 32
    )
    result = await play_tape_events(
        sink=sink,
        events=events,
        start_index=0,
        binding=binding,
        message_id="replay-msg",
        conversation_id="replay-conv",
        user_id="u",
        user_message="go",
        folder_id=None,
        journal_writer=writer,
    )
    assert result["finish_reason"] is FinishReason.PAUSED
    assert result["content"] == "案情简介。"
    card = next(e for e in sink._history if e.type is EventType.CHECKPOINT_REQUIRED)
    assert card.payload["checkpoint_id"] != "cp-src"  # replay identity reminted

    sink2 = EventSink(conversation_id="replay-conv", message_id="replay-msg")
    result2 = await continue_tape_turn(
        suspension=saved[0],
        response=CheckpointResponse(decision=CheckpointDecision.CONTINUE, note=""),
        sink=sink2,
        folder_id=None,
        trace_id="c" * 32,
    )
    assert result2["finish_reason"] is FinishReason.END_TURN
    # Persist-shaped: live finish joins at the durable-pause seam.
    assert result2["content"] == "案情简介。\n\n最终汇总。"
    # Chips offline: player ignores meta.followups (no result attach / no emit).
    assert result2.get("followups") is None
    assert all(e.type.value != "followups_generated" for e in sink2._history)
    types2 = [e.type for e in sink2._history]
    assert types2.count(EventType.CHECKPOINT_RESOLVED) == 1
    deltas = [
        e.payload.get("delta")
        for e in sink2._history
        if e.type is EventType.RUN_OUTPUT_DELTA
    ]
    assert "".join(d for d in deltas if d) == "辩手观点。"


@pytest.mark.asyncio
async def test_tape_followups_ignored_on_persist(monkeypatch, tmp_path: Path):
    """meta.followups on tape is ignored: no set_followups, no followups_generated emit."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agentcore.conversation import turn_persistence
    from agentcore.conversation.store import cloud as cloud_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import play_tape_turn
    from agentcore.runtime.journal.writer import TurnJournalWriter

    chips = [
        "模拟庭审辩论的结论整理成一页摘要",
        "把公共领域抗辩的关键考古证据单独列出来",
        "起草一份茉莉奶白二审上诉的核心论点提纲",
    ]
    tape_path = tmp_path / "fu-tape.json"
    write_tape(
        tape_path,
        {
            "version": 2,
            "meta": {"followups": chips, "user_prompt": "go"},
            "events": [
                {
                    "type": "run_started",
                    "payload": {"run_id": "c1", "kind": "captain"},
                    "timestamp": None,
                    "t_ms": 0,
                },
                {
                    "type": "content_delta",
                    "payload": {"delta": "结案。"},
                    "timestamp": None,
                    "t_ms": 10,
                },
            ],
        },
    )

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    binding = TapeBinding(
        conversation_id="conv-fu", tape_path=tape_path, speed=100.0, max_gap_ms=0
    )
    sink = EventSink(conversation_id="conv-fu", message_id="msg-live")
    result = await play_tape_turn(
        binding=binding,
        sink=sink,
        message_id="msg-live",
        conversation_id="conv-fu",
        user_id="u",
        user_message="go",
        folder_id=None,
        trace_id="d" * 32,
    )
    assert result["finish_reason"] is FinishReason.END_TURN
    assert result.get("followups") is None
    assert all(e.type.value != "followups_generated" for e in sink._history)

    stored: list[tuple] = []

    class FakeRepo:
        def __init__(self, _session):
            pass

        async def upsert_assistant(self, **kwargs):
            return SimpleNamespace(id=kwargs["message_id"])

        async def set_followups(self, message_id, *, conversation_id, followups):
            stored.append((message_id, conversation_id, list(followups)))

    class FakeSessionCM:
        async def __aenter__(self):
            return SimpleNamespace()

        async def __aexit__(self, *_a):
            return False

    class FakeMetrics:
        def __init__(self, _s):
            pass

        async def record(self, **_kw):
            return None

    monkeypatch.setattr(cloud_mod, "MessageRepository", FakeRepo)
    monkeypatch.setattr(cloud_mod, "TurnMetricsRepository", FakeMetrics)
    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: FakeSessionCM())
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.drain_cost_ledger_before_reconcile",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.reconcile_turn_cost_ledger",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", AsyncMock())
    monkeypatch.setattr(cloud_mod, "schedule_consolidation", lambda _c: None)
    monkeypatch.setattr(cloud_mod, "schedule_compaction_if_due", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "agentcore.runtime.kickoff.stage_card.emit_stage_card_for_motion",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        cloud_mod.settings, "workspace_snapshot_enabled", False, raising=False
    )

    class FakeBackend:
        location = "server"
        dirty = False

    persist_sink = EventSink(conversation_id="conv-fu", message_id="msg-live")
    await turn_persistence.persist_turn_result(
        result=result,
        conversation_id="conv-fu",
        user_id="u",
        folder_id=None,
        backend=FakeBackend(),  # type: ignore[arg-type]
        sink=persist_sink,
        user_message="go",
        llm_credentials=None,
        trace_id="d" * 32,
        turn_id="msg-live",
        duration_ms=1,
    )

    assert stored == []
    assert all(
        e.type.value not in ("followups_generated", "followups_unavailable")
        for e in persist_sink._history
    )


# ── player ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_player_pathological_gaps_do_not_double_sleep(monkeypatch):
    """Overshoot then jump-back must not re-sleep the overshot window."""
    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import play_tape_events
    from agentcore.runtime.journal.writer import TurnJournalWriter

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(player_mod, "pacing_sleep", fake_sleep)

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    events = [
        {"kind": "run_started", "payload": {"run_id": "c1"}, "t_ms": 0},
        {"kind": "run_output_delta", "payload": {"run_id": "w1", "delta": "a"}, "t_ms": 1000},
        {"kind": "run_output_delta", "payload": {"run_id": "w1", "delta": "b"}, "t_ms": 5000},
        # jump back (chunk overshoot artifact)
        {"kind": "run_completed", "payload": {"run_id": "w1"}, "t_ms": 2000},
        {"kind": "run_started", "payload": {"run_id": "c2"}, "t_ms": 8000},
    ]
    binding = TapeBinding(
        conversation_id="conv",
        tape_path=Path("unused.json"),
        speed=1.0,
        max_gap_ms=600_000,
    )
    sink = EventSink(conversation_id="conv", message_id="msg")
    writer = TurnJournalWriter(turn_id="msg", conversation_id="conv", trace_id="t" * 32)
    await play_tape_events(
        sink=sink,
        events=events,
        start_index=0,
        binding=binding,
        message_id="msg",
        conversation_id="conv",
        user_id="u",
        user_message="go",
        folder_id=None,
        journal_writer=writer,
    )
    # 0→1000 (1s) + 1000→5000 (4s) + 5000→2000 (0) + 5000→8000 (3s) = 8s total
    # Without never-rewind: last gap would be 8000-2000=6s → 11s total.
    assert sum(sleeps) == pytest.approx(8.0)
    assert max(sleeps) == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_player_skips_leftover_team_preview(monkeypatch, tmp_path: Path):
    """旧磁带 leftover team_preview_* skip：不进 PAUSE、不 persist 开工卡帧。"""
    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import play_tape_events
    from agentcore.runtime.journal.writer import TurnJournalWriter

    saved: list = []

    async def fake_save(suspension):
        saved.append(suspension)

    monkeypatch.setattr(player_mod, "save_paused_turn", fake_save)

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    events = [
        {"kind": "run_started", "payload": {"run_id": "c1", "kind": "captain"}, "t_ms": 0},
        {"kind": "content_delta", "payload": {"delta": "开场"}, "t_ms": 50},
        {
            "kind": "team_preview_required",
            "payload": {
                "checkpoint_id": "cp-tape",
                "form": "debate",
                "sides": [{"key": "lv", "name": "LV"}],
                "workers": [],
                "tools": [],
                "primitive": "debate",
                "motion": "m",
                "max_rounds": 4,
                "thorough": True,
            },
            "t_ms": 100,
        },
        {"kind": "team_preview_resolved", "payload": {"decision": "continue"}, "t_ms": 200},
        {
            "kind": "run_output_delta",
            "payload": {"run_id": "w1", "agent_id": "w1", "delta": "hello"},
            "t_ms": 300,
        },
        {"kind": "content_delta", "payload": {"delta": "汇总"}, "t_ms": 400},
    ]
    tape_path = tmp_path / "mini.json"
    tape_path.write_text(
        json.dumps({"version": 1, "meta": {}, "events": events}, ensure_ascii=False),
        encoding="utf-8",
    )
    binding = TapeBinding(
        conversation_id="conv1",
        tape_path=tape_path,
        speed=100.0,
        max_gap_ms=50,
    )
    sink = EventSink(conversation_id="conv1", message_id="msg1")
    writer = TurnJournalWriter(turn_id="msg1", conversation_id="conv1", trace_id="t" * 32)

    result = await play_tape_events(
        sink=sink,
        events=events,
        start_index=0,
        binding=binding,
        message_id="msg1",
        conversation_id="conv1",
        user_id="user1",
        user_message="go",
        folder_id=None,
        journal_writer=writer,
    )
    assert result["finish_reason"] is FinishReason.END_TURN
    assert saved == []
    assert result["content"] == "开场汇总"
    types = [e.type.value for e in sink._history]
    assert "team_preview_required" not in types
    assert "team_preview_resolved" not in types
    assert EventType.RUN_OUTPUT_DELTA in [e.type for e in sink._history]
    assert types.count("content_delta") == 2


@pytest.mark.asyncio
async def test_resume_keeps_pre_pause_content_visible_across_collab_graph(
    monkeypatch, tmp_path: Path
):
    """授权恢复进入协作图后，fold 可见正文仍含挂起前 CEO 正文（跨挂起边界）。

    覆盖此前盲区：fidelity 只比 player→sink 字节，不查客户端 fold 可见性；
    live 用 G6 重灌挡住 content_reset，磁带旁路曾漏掉导致气泡被清空。
    """
    from agentcore.conformance.projection import project_turn
    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import continue_tape_turn, play_tape_events
    from agentcore.runtime.events import content_reset
    from agentcore.runtime.facts import TurnFactLog, current_fact_log
    from agentcore.runtime.journal.writer import TurnJournalWriter

    saved: list = []

    async def fake_save(suspension):
        saved.append(suspension)

    monkeypatch.setattr(player_mod, "save_paused_turn", fake_save)

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    pre_pause_body = "案情简介已讲清，启动模拟庭审。"
    events = [
        {"kind": "run_started", "payload": {"run_id": "c1", "kind": "captain"}, "t_ms": 0},
        {"kind": "content_delta", "payload": {"delta": pre_pause_body}, "t_ms": 50},
        {
            "kind": "checkpoint_required",
            "payload": {"checkpoint_id": "cp-vis", "question": "继续？"},
            "t_ms": 100,
        },
        {"kind": "checkpoint_resolved", "payload": {"decision": "continue"}, "t_ms": 150},
        {
            "kind": "run_plan",
            "payload": {
                "execution_id": "ex1",
                "plan_type": "debate",
                "runs": [{"run_id": "w1", "agent_id": "w1"}],
                "agents": [{"id": "w1", "role": "辩手"}],
            },
            "t_ms": 200,
        },
        {
            "kind": "run_started",
            "payload": {"run_id": "w1", "agent_id": "w1", "kind": "agent"},
            "t_ms": 250,
        },
        {
            "kind": "run_output_delta",
            "payload": {"run_id": "w1", "agent_id": "w1", "delta": "辩方观点。"},
            "t_ms": 300,
        },
        {"kind": "content_delta", "payload": {"delta": "庭审汇总。"}, "t_ms": 400},
    ]
    tape_path = tmp_path / "vis.json"
    tape_path.write_text(
        json.dumps({"version": 1, "meta": {}, "events": events}, ensure_ascii=False),
        encoding="utf-8",
    )
    binding = TapeBinding(
        conversation_id="conv-vis",
        tape_path=tape_path,
        speed=100.0,
        max_gap_ms=50,
    )
    sink = EventSink(conversation_id="conv-vis", message_id="msg-vis")
    writer = TurnJournalWriter(
        turn_id="msg-vis", conversation_id="conv-vis", trace_id="v" * 32
    )
    fact_token = current_fact_log.set(TurnFactLog())
    try:
        result = await play_tape_events(
            sink=sink,
            events=events,
            start_index=0,
            binding=binding,
            message_id="msg-vis",
            conversation_id="conv-vis",
            user_id="u",
            user_message="go",
            folder_id=None,
            journal_writer=writer,
        )
    finally:
        current_fact_log.reset(fact_token)

    assert result["finish_reason"] is FinishReason.PAUSED
    assert pre_pause_body in (result.get("content") or "")
    turn_paused = next(
        e for e in (saved[0].journal_entries or []) if e.get("kind") == "turn_paused"
    )
    assert pre_pause_body in str((turn_paused.get("payload") or {}).get("content") or "")

    sink2 = EventSink(conversation_id="conv-vis", message_id="msg-vis")
    result2 = await continue_tape_turn(
        suspension=saved[0],
        response=CheckpointResponse(decision=CheckpointDecision.CONTINUE, note=""),
        sink=sink2,
        folder_id=None,
        trace_id="v" * 32,
    )
    assert result2["finish_reason"] is FinishReason.END_TURN
    assert pre_pause_body in (result2.get("content") or "")
    assert EventType.RUN_PLAN in [e.type for e in sink2._history]

    # Client-visible fold across pause→resume at collab-graph stage (before any reset).
    wire: list[dict] = []
    for e in sink._history:
        wire.append({"type": e.type.value, "payload": e.payload})
    for e in sink2._history:
        wire.append({"type": e.type.value, "payload": e.payload})
    projected = project_turn(wire)
    assert pre_pause_body in (projected.get("content") or "")

    # G6: content_reset after resume must reinject pre_pause (display-only).
    assert sink2._content_reset_reinjection == pre_pause_body + "\n\n"
    sink2.emit(content_reset("finish_guard"))
    reinjected = [
        e.payload.get("delta")
        for e in sink2._history
        if e.type is EventType.CONTENT_DELTA
    ]
    assert any(pre_pause_body in str(d) for d in reinjected if d)


def test_persisted_captain_content_joins_at_durable_pause_seam():
    """暂停接缝：流内无 joiner，持久化正文须经 join_segments（对齐 messages.content）。

    回归：正反对决。|checkpoint|--- 不得粘成「。---」；须为「。\\n\\n---」。
    """
    from agentcore.runtime.engine.segments import join_segments

    pre = "展开正反对决。"
    post = "---\n\n## 模拟庭审辩论结果"
    events = [
        {"type": "content_delta", "payload": {"delta": pre}, "t_ms": 0},
        {
            "type": "checkpoint_required",
            "payload": {"checkpoint_id": "cp-seam", "question": "继续？"},
            "t_ms": 10,
        },
        {"type": "content_delta", "payload": {"delta": post}, "t_ms": 20},
    ]
    raw = pre + post
    persisted = persisted_captain_content_from_events(events)
    assert raw == "展开正反对决。---\n\n## 模拟庭审辩论结果"
    assert persisted == join_segments(pre, post)
    assert persisted == "展开正反对决。\n\n---\n\n## 模拟庭审辩论结果"
    assert len(persisted) == len(raw) + 2


@pytest.mark.asyncio
async def test_player_result_content_joins_at_pause_seam(monkeypatch, tmp_path: Path):
    """player result[\"content\"] 跨 checkpoint 须用 join_segments，对齐 live finish。"""
    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import continue_tape_turn, play_tape_events
    from agentcore.runtime.engine.segments import join_segments
    from agentcore.runtime.facts import TurnFactLog, current_fact_log
    from agentcore.runtime.journal.writer import TurnJournalWriter

    saved: list = []

    async def fake_save(suspension):
        saved.append(suspension)

    monkeypatch.setattr(player_mod, "save_paused_turn", fake_save)

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    pre = "展开正反对决。"
    post = "---\n\n## 模拟庭审辩论结果"
    events = [
        {"kind": "run_started", "payload": {"run_id": "c1", "kind": "captain"}, "t_ms": 0},
        {"kind": "content_delta", "payload": {"delta": pre}, "t_ms": 50},
        {
            "kind": "checkpoint_required",
            "payload": {"checkpoint_id": "cp-seam", "question": "继续？"},
            "t_ms": 100,
        },
        {"kind": "checkpoint_resolved", "payload": {"decision": "continue"}, "t_ms": 150},
        {"kind": "content_delta", "payload": {"delta": post}, "t_ms": 400},
    ]
    tape_path = tmp_path / "seam.json"
    tape_path.write_text(
        json.dumps({"version": 1, "meta": {}, "events": events}, ensure_ascii=False),
        encoding="utf-8",
    )
    binding = TapeBinding(
        conversation_id="conv-seam",
        tape_path=tape_path,
        speed=100.0,
        max_gap_ms=50,
    )
    sink = EventSink(conversation_id="conv-seam", message_id="msg-seam")
    writer = TurnJournalWriter(
        turn_id="msg-seam", conversation_id="conv-seam", trace_id="s" * 32
    )
    fact_token = current_fact_log.set(TurnFactLog())
    try:
        result = await play_tape_events(
            sink=sink,
            events=events,
            start_index=0,
            binding=binding,
            message_id="msg-seam",
            conversation_id="conv-seam",
            user_id="u",
            user_message="go",
            folder_id=None,
            journal_writer=writer,
        )
    finally:
        current_fact_log.reset(fact_token)

    assert result["finish_reason"] is FinishReason.PAUSED

    sink2 = EventSink(conversation_id="conv-seam", message_id="msg-seam")
    result2 = await continue_tape_turn(
        suspension=saved[0],
        response=CheckpointResponse(decision=CheckpointDecision.CONTINUE, note=""),
        sink=sink2,
        folder_id=None,
        trace_id="s" * 32,
    )
    assert result2["finish_reason"] is FinishReason.END_TURN
    expected = join_segments(pre, post)
    assert result2.get("content") == expected
    assert "。\n\n---" in (result2.get("content") or "")


@pytest.mark.asyncio
async def test_tape_cancel_salvages_incomplete_turn(monkeypatch):
    """磁带回放中途被取消（断流/停服）走 salvage 收口，不留 status=running 僵尸行。"""
    import asyncio
    from types import SimpleNamespace

    from agentcore.conversation import turn_runner
    from agentcore.demo_tape import hooks as tape_hooks

    salvaged: list[dict] = []

    async def fake_placeholder(**kwargs):
        return None

    def fake_salvage(**kwargs):
        salvaged.append(kwargs)

    async def cancelled_tape(**kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(turn_runner, "create_assistant_placeholder", fake_placeholder)
    monkeypatch.setattr(turn_runner, "salvage_incomplete_turn", fake_salvage)
    monkeypatch.setattr(tape_hooks, "run_tape_turn_if_bound", cancelled_tape)

    sink = EventSink()
    monkeypatch.setattr(
        sink, "bind_content_checkpoint", lambda **kw: None, raising=False
    )
    with pytest.raises(asyncio.CancelledError):
        await turn_runner.run_and_persist(
            conversation_id="conv-x",
            user_message="go",
            user_id="u1",
            folder_id=None,
            sink=sink,
            history=[],
            attachments=None,
            backend=SimpleNamespace(location="server"),
            llm_credentials=None,
        )
    assert len(salvaged) == 1
    assert salvaged[0]["conversation_id"] == "conv-x"
    assert salvaged[0]["message_id"]


@pytest.mark.asyncio
async def test_demo_tape_import_failure_does_not_block_live_turn(monkeypatch):
    """Optional demo_tape ImportError (e.g. missing tape_frame_meta) must fall through."""
    import sys
    from types import SimpleNamespace

    from agentcore.conversation import turn_runner
    from agentcore.runtime.events import FinishReason

    pipeline_calls: list[dict] = []

    async def fake_placeholder(**kwargs):
        return None

    async def fake_pipeline(**kwargs):
        pipeline_calls.append(kwargs)
        return {
            "content": "live-ok",
            "finish_reason": FinishReason.END_TURN,
            "rounds": 1,
        }

    async def fake_persist(**kwargs):
        return None

    monkeypatch.setattr(turn_runner, "create_assistant_placeholder", fake_placeholder)
    monkeypatch.setattr(turn_runner, "run_chat_pipeline", fake_pipeline)
    monkeypatch.setattr(turn_runner, "persist_turn_result", fake_persist)
    monkeypatch.setattr(turn_runner.settings, "turn_lease_enabled", False)
    # Simulate partial deploy: hooks module missing (ImportError on divert import).
    monkeypatch.setitem(sys.modules, "agentcore.demo_tape.hooks", None)

    sink = EventSink()
    monkeypatch.setattr(
        sink, "bind_content_checkpoint", lambda **kw: None, raising=False
    )
    await turn_runner.run_and_persist(
        conversation_id="conv-live",
        user_message="go",
        user_id="u1",
        folder_id=None,
        sink=sink,
        history=[],
        attachments=None,
        backend=SimpleNamespace(location="server"),
        llm_credentials=None,
    )
    assert len(pipeline_calls) == 1


def test_tape_frame_meta_importable():
    """Regression: player imports tape_frame_meta — symbol must stay exportable from schema."""
    from agentcore.demo_tape.schema import tape_frame_meta

    assert callable(tape_frame_meta)


def test_captain_run_id_finds_first_captain_run():
    from agentcore.replay.legacy import captain_run_id_from_events

    assert (
        captain_run_id_from_events(
            [
                {"type": "message_start", "payload": {}},
                {"type": "run_started", "payload": {"run_id": "cap1", "kind": "captain"}},
                {"type": "run_started", "payload": {"run_id": "w1", "kind": "agent"}},
            ]
        )
        == "cap1"
    )
    # Legacy dialect still resolves.
    assert (
        captain_run_id_from_events(
            [{"kind": "run_started", "payload": {"run_id": "cap1", "kind": "captain"}}]
        )
        == "cap1"
    )
    # No captain run → empty (nothing to normalize).
    assert (
        captain_run_id_from_events(
            [{"type": "run_started", "payload": {"run_id": "w1"}}]
        )
        == ""
    )


@pytest.mark.asyncio
async def test_player_inlines_captain_tools_by_stripping_run_id(monkeypatch):
    """CEO self-tools (run_id == captain run) replay inline (run_id dropped) so the
    search phase renders instead of a silent「正在思考」; worker tools keep run_id."""
    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import play_tape_events
    from agentcore.runtime.journal.writer import TurnJournalWriter

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(player_mod, "pacing_sleep", fake_sleep)

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    events = [
        {"kind": "run_started", "payload": {"run_id": "cap1", "kind": "captain"}, "t_ms": 0},
        {
            "kind": "tool_use_start",
            "payload": {"tool_call_id": "t1", "tool_name": "web_search", "arguments": {}, "run_id": "cap1"},
            "t_ms": 100,
        },
        {
            "kind": "tool_use_end",
            "payload": {"tool_call_id": "t1", "tool_name": "web_search", "run_id": "cap1"},
            "t_ms": 200,
        },
        {"kind": "run_started", "payload": {"run_id": "w1", "kind": "agent"}, "t_ms": 300},
        {
            "kind": "tool_use_start",
            "payload": {"tool_call_id": "t2", "tool_name": "read_url", "arguments": {}, "run_id": "w1"},
            "t_ms": 400,
        },
        {"kind": "content_delta", "payload": {"delta": "done"}, "t_ms": 500},
    ]
    binding = TapeBinding(
        conversation_id="c", tape_path=Path("unused.json"), speed=1.0, max_gap_ms=50
    )
    sink = EventSink(conversation_id="c", message_id="m")
    writer = TurnJournalWriter(turn_id="m", conversation_id="c", trace_id="t" * 32)
    await play_tape_events(
        sink=sink,
        events=events,
        start_index=0,
        binding=binding,
        message_id="m",
        conversation_id="c",
        user_id="u",
        user_message="go",
        folder_id=None,
        journal_writer=writer,
    )
    starts = {
        e.payload.get("tool_name"): e.payload
        for e in sink._history
        if e.type is EventType.TOOL_USE_START
    }
    # CEO's own web_search: run_id stripped → turn-level inline step.
    assert "run_id" not in starts["web_search"]
    # Worker's read_url: run_id preserved → its own run node in the graph.
    assert starts["read_url"].get("run_id") == "w1"
    ends = [e.payload for e in sink._history if e.type is EventType.TOOL_USE_END]
    assert all("run_id" not in p for p in ends if p.get("tool_name") == "web_search")
    # Rendering outcome: the CEO's web_search is now a turn-level process step.
    inline_tools = [s for s in sink._process if s.get("kind") == "tool"]
    assert any(s.get("tool_name") == "web_search" for s in inline_tools)
    assert all(s.get("tool_name") != "read_url" for s in inline_tools)


@pytest.mark.asyncio
async def test_player_skip_kinds_do_not_advance_pacing_clock(monkeypatch):
    """resume 后 turn_paused / resolved 等 skip 事件不睡、不推进时钟 → 首拍 sleep=0。"""
    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import play_tape_events
    from agentcore.runtime.journal.writer import TurnJournalWriter

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(player_mod, "pacing_sleep", fake_sleep)

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    # Simulate post-pause resume: skip events carry the recorder's hesitation gap,
    # then the first real event is 11s later on the tape clock.
    events = [
        {"kind": "turn_paused", "payload": {"checkpoint_id": "cp"}, "t_ms": 34_000},
        {
            "kind": "team_preview_required",
            "payload": {"checkpoint_id": "cp", "primitive": "debate"},
            "t_ms": 34_000,
        },
        {
            "kind": "team_preview_resolved",
            "payload": {"decision": "continue"},
            "t_ms": 34_000,
        },
        {
            "kind": "run_started",
            "payload": {"run_id": "w1", "kind": "agent"},
            "t_ms": 45_000,
        },
        {
            "kind": "run_output_delta",
            "payload": {"run_id": "w1", "agent_id": "w1", "delta": "hi"},
            "t_ms": 45_100,
        },
    ]
    binding = TapeBinding(
        conversation_id="c",
        tape_path=Path("unused.json"),
        speed=1.0,
        max_gap_ms=600_000,
    )
    sink = EventSink(conversation_id="c", message_id="m")
    writer = TurnJournalWriter(turn_id="m", conversation_id="c", trace_id="t" * 32)
    await play_tape_events(
        sink=sink,
        events=events,
        start_index=0,
        binding=binding,
        message_id="m",
        conversation_id="c",
        user_id="u",
        user_message="go",
        folder_id=None,
        journal_writer=writer,
        emit_message_start=False,
    )
    # Skip + first real event: no sleep (prev_t stays None → gap 0, delay not awaited).
    # Only the 100ms gap between the two real events is slept.
    assert sleeps == [pytest.approx(0.1)]
    assert sum(sleeps) == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_resume_folds_checkpoint_into_resolved(monkeypatch, tmp_path: Path):
    """磁带回放冷闸授权后，client fold（reload + live 两路）必须把 ask_user 判为
    resolved，而不是停在 pending 横条。

    回归钉子：曾出现「协作图已长满、顶部仍残留待确认横条」的旁路 bug。修复靠
    ① continue_tape_turn 结算时 emit checkpoint_resolved，② identity.remint 让
    send/resume 两腿共用同一 checkpoint_id。此测试锁死两点，且覆盖 reload
    （journal_entries → runs_from_entries）与 live（send._history + resume._history）
    两条 fold 路径。桌面 fold 逻辑同源见 stores/interactions。
    """
    from agentcore.conformance.projection import project_turn
    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import continue_tape_turn, play_tape_events
    from agentcore.runtime.journal.fold import runs_from_entries
    from agentcore.runtime.journal.writer import TurnJournalWriter

    saved: list = []

    async def fake_save(suspension):
        saved.append(suspension)

    async def noop_flush(self):
        return None

    monkeypatch.setattr(player_mod, "save_paused_turn", fake_save)
    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    events = [
        {"kind": "run_started", "payload": {"run_id": "c1", "kind": "captain"}, "t_ms": 0},
        {
            "kind": "checkpoint_required",
            "payload": {"checkpoint_id": "cp-tape", "question": "继续？"},
            "t_ms": 100,
        },
        {"kind": "checkpoint_resolved", "payload": {"decision": "continue"}, "t_ms": 200},
        {
            "kind": "run_plan",
            "payload": {
                "execution_id": "ex1",
                "plan_type": "debate",
                "runs": [{"run_id": "w1", "agent_id": "w1"}],
                "agents": [{"id": "w1", "role": "辩手"}],
            },
            "t_ms": 250,
        },
        {
            "kind": "run_started",
            "payload": {"run_id": "w1", "agent_id": "w1", "kind": "agent"},
            "t_ms": 260,
        },
        {
            "kind": "run_output_delta",
            "payload": {"run_id": "w1", "agent_id": "w1", "delta": "观点"},
            "t_ms": 300,
        },
        {"kind": "content_delta", "payload": {"delta": "汇总"}, "t_ms": 400},
    ]
    tape_path = tmp_path / "cp_resolve.json"
    tape_path.write_text(
        json.dumps({"version": 1, "meta": {}, "events": events}, ensure_ascii=False),
        encoding="utf-8",
    )
    binding = TapeBinding(
        conversation_id="conv1", tape_path=tape_path, speed=100.0, max_gap_ms=50
    )

    sink = EventSink(conversation_id="conv1", message_id="msg1")
    writer = TurnJournalWriter(turn_id="msg1", conversation_id="conv1", trace_id="t" * 32)
    result = await play_tape_events(
        sink=sink,
        events=events,
        start_index=0,
        binding=binding,
        message_id="msg1",
        conversation_id="conv1",
        user_id="user1",
        user_message="go",
        folder_id=None,
        journal_writer=writer,
    )
    assert result["finish_reason"] is FinishReason.PAUSED
    required_ids = {
        e.payload["checkpoint_id"]
        for e in sink._history
        if e.type is EventType.CHECKPOINT_REQUIRED
    }
    assert len(required_ids) == 1
    assert "cp-tape" not in required_ids  # reminted, never the recorded id

    sink2 = EventSink(conversation_id="conv1", message_id="msg1")
    result2 = await continue_tape_turn(
        suspension=saved[0],
        response=CheckpointResponse(decision=CheckpointDecision.CONTINUE, note=""),
        sink=sink2,
        folder_id=None,
        trace_id="t" * 32,
    )
    assert result2["finish_reason"] is FinishReason.END_TURN
    resolved_ids = {
        e.payload.get("checkpoint_id")
        for e in sink2._history
        if e.type is EventType.CHECKPOINT_RESOLVED
    }
    # send/resume legs settle the SAME reminted checkpoint (else the pending card lingers).
    assert resolved_ids == required_ids

    def _ask_user(proj: dict) -> dict:
        cards = [i for i in proj.get("interactions", []) if i.get("kind") == "ask_user"]
        assert len(cards) == 1, f"expected 1 ask_user, got {cards}"
        return cards[0]

    # Reload fold: message-detail projects turn_journal via runs_from_entries.
    runs = runs_from_entries(list(result2.get("journal_entries") or []))
    reload_wire = [
        {"type": ev["type"], "payload": ev.get("payload") or {}}
        for ev in (runs or {}).get("events", [])
    ]
    assert _ask_user(project_turn(reload_wire))["status"] == "resolved"

    # Live fold: desktop folds send leg + resume leg SSE histories back-to-back.
    live_wire = [
        {"type": e.type.value, "payload": e.payload}
        for e in (*sink._history, *sink2._history)
    ]
    assert _ask_user(project_turn(live_wire))["status"] == "resolved"


# ── 入库脱敏双防线 + 导出门禁 + 客户端工具断言 ─────────────────────────────


_REAL_MEMORY_RULES = """<rules>
以下条目请一并遵循；与本回合用户直接指令冲突时，以本回合指令为准。
硬约束：题材/领域偏好与历史任务不得改变本回合路由（直答/委派/调研/辩论以用户当前话为准）。

## 沟通偏好
- 倾向用中文交流 <!-- ts:2026-07-13 -->

## 关于用户的事实
- 正在测试秘密功能 <!-- ts:2026-07-16 -->
</rules>"""


def test_sanitize_memory_keeps_rules_block_structure():
    prompt = f"前置\n{_REAL_MEMORY_RULES}\n<role>\n你是 CEO\n</role>"
    out = sanitize_memory_in_text(prompt)
    assert DEMO_MEMORY_PLACEHOLDER in out
    assert "<!-- ts:" not in out
    assert "正在测试秘密功能" not in out
    assert out.startswith("前置\n")
    assert "<role>\n你是 CEO\n</role>" in out
    assert SYNTHETIC_MEMORY_RULES in out


def test_sanitize_and_scan_run_context_clears_memory():
    events = [
        {
            "type": "run_context",
            "payload": {
                "run_id": "r1",
                "blocks": [
                    {"channel": "system", "body": f"head\n{_REAL_MEMORY_RULES}\ntail"},
                    {"channel": "request", "body": "用户问题"},
                ],
            },
            "timestamp": None,
            "t_ms": 0,
        }
    ]
    cleaned = sanitize_and_scan_events(events)
    body = cleaned[0]["payload"]["blocks"][0]["body"]
    assert DEMO_MEMORY_PLACEHOLDER in body
    assert "秘密功能" not in body
    assert cleaned[0]["payload"]["blocks"][1]["body"] == "用户问题"
    assert_ingest_clean(cleaned)


def test_ingest_scan_rejects_unsanitized_memory_and_system_contacts():
    dirty = [
        {
            "type": "run_context",
            "payload": {
                "blocks": [
                    {
                        "channel": "system",
                        "body": (
                            "x\n## 沟通偏好\n- 真偏好 <!-- ts:2026-01-01 -->\n"
                            "mail me@example.com phone 13812345678"
                        ),
                    }
                ]
            },
        }
    ]
    hits = scan_events_for_ingest_residue(dirty)
    assert any("timestamp marker" in h or "沟通偏好" in h for h in hits)
    with pytest.raises(IngestScanError):
        assert_ingest_clean(dirty)

    # Public contacts in tool results must NOT trip the gate (demo web search noise).
    toolish = [
        {
            "type": "tool_use_end",
            "payload": {
                "result": "见 https://www.sohu.com/a/1050304127_121811866 ipc@court.gov.cn"
            },
        }
    ]
    assert scan_events_for_ingest_residue(toolish) == []


def test_export_allows_wired_cold_and_hot_approval_pauses():
    assert "checkpoint_required" in TAPE_WIRED_PAUSE_KINDS
    assert "plan_review_required" in TAPE_WIRED_PAUSE_KINDS
    assert "team_preview_required" not in TAPE_WIRED_PAUSE_KINDS
    assert "checkpoint_required" not in TAPE_UNWIRED_PAUSE_KINDS
    from agentcore.demo_tape.schema import TAPE_HOT_PAUSE_KINDS

    assert "approval_required" in TAPE_HOT_PAUSE_KINDS
    recording = {
        "meta": {"conversation_id": "c", "message_id": "m"},
        "segments": [
            {
                "events": [
                    {
                        "type": "content_delta",
                        "payload": {"delta": "hi"},
                        "timestamp": None,
                        "t_ms": 0,
                    },
                    {
                        "type": "checkpoint_required",
                        "payload": {"checkpoint_id": "cp1", "question": "ok?"},
                        "timestamp": None,
                        "t_ms": 10,
                    },
                    {
                        "type": "plan_review_required",
                        "payload": {
                            "checkpoint_id": "pr1",
                            "steps": [{"run_id": "r1"}],
                            "pending": [],
                        },
                        "timestamp": None,
                        "t_ms": 20,
                    },
                ]
            }
        ],
    }
    doc = build_tape_from_recording(recording, user_prompt="p")
    assert [e["type"] for e in doc["events"]] == [
        "content_delta",
        "checkpoint_required",
        "plan_review_required",
    ]

    approval_rec = {
        "meta": {},
        "segments": [
            {
                "events": [
                    {
                        "type": "approval_required",
                        "payload": {
                            "approval_id": "a1",
                            "tool_call_id": "tc1",
                            "tool_name": "file_write",
                            "arguments": {"path": "x"},
                        },
                        "timestamp": None,
                        "t_ms": 0,
                    },
                    {
                        "type": "approval_resolved",
                        "payload": {
                            "approval_id": "a1",
                            "tool_call_id": "tc1",
                            "decision": "approve",
                        },
                        "timestamp": None,
                        "t_ms": 50,
                    },
                    {
                        "type": "content_delta",
                        "payload": {"delta": "after"},
                        "timestamp": None,
                        "t_ms": 100,
                    },
                ]
            }
        ],
    }
    # Hot approval is wired — export allows; recorded resolve is cut.
    approval_doc = build_tape_from_recording(approval_rec, user_prompt="p")
    assert [e["type"] for e in approval_doc["events"]] == [
        "approval_required",
        "content_delta",
    ]


def test_export_asserts_client_tool_required_not_forceable():
    # Defense beyond the cut table: if a client-tool event slips into the cut
    # result, export must refuse even with force=True.
    leaked = [
        {
            "type": "workspace_op_required",
            "payload": {"op_id": "op1"},
            "timestamp": None,
            "t_ms": 0,
        }
    ]
    with pytest.raises(TapeExportRefusedError) as ei:
        assert_export_allowed(leaked, force=True)
    assert any("client-tool" in r for r in ei.value.reasons)
    assert CLIENT_TOOL_REQUIRED_KINDS <= TAPE_EXCLUDED_KINDS


def test_build_tape_sanitizes_run_context_memory():
    recording = {
        "meta": {"conversation_id": "c", "message_id": "m"},
        "segments": [
            {
                "events": [
                    {
                        "type": "run_context",
                        "payload": {
                            "run_id": "r1",
                            "blocks": [
                                {"channel": "system", "body": _REAL_MEMORY_RULES},
                            ],
                        },
                        "timestamp": None,
                        "t_ms": 0,
                    },
                    {
                        "type": "content_delta",
                        "payload": {"delta": "ok"},
                        "timestamp": None,
                        "t_ms": 5,
                    },
                ]
            }
        ],
    }
    doc = build_tape_from_recording(recording, user_prompt="p")
    body = doc["events"][0]["payload"]["blocks"][0]["body"]
    assert DEMO_MEMORY_PLACEHOLDER in body
    assert "<!-- ts:" not in body
    assert_ingest_clean(doc["events"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("required_kind", "resolved_kind", "payload", "suspension_cls", "event_required", "event_resolved"),
    [
        (
            "checkpoint_required",
            "checkpoint_resolved",
            {
                "checkpoint_id": "cp-ask",
                "question": "要继续吗？",
                "assumptions": [],
                "questions": [{"id": "q1", "prompt": "选一项"}],
                "intent": "decision",
            },
            AskUserSuspension,
            EventType.CHECKPOINT_REQUIRED,
            EventType.CHECKPOINT_RESOLVED,
        ),
        (
            "plan_review_required",
            "plan_review_resolved",
            {
                "checkpoint_id": "cp-plan",
                "steps": [{"run_id": "r1", "role": "researcher", "summary": "done"}],
                "pending": [{"run_id": "r2", "role": "writer"}],
            },
            PlanReviewSuspension,
            EventType.PLAN_REVIEW_REQUIRED,
            EventType.PLAN_REVIEW_RESOLVED,
        ),
    ],
)
async def test_player_pauses_and_continues_cold_path_kinds(
    monkeypatch,
    tmp_path: Path,
    required_kind: str,
    resolved_kind: str,
    payload: dict,
    suspension_cls: type,
    event_required: EventType,
    event_resolved: EventType,
):
    """checkpoint / plan_review: 挂起 → 用户提交 → 现场重发 resolved → 续播闭环。"""
    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import continue_tape_turn, play_tape_events
    from agentcore.runtime.journal.writer import TurnJournalWriter

    saved: list = []

    async def fake_save(suspension):
        saved.append(suspension)

    monkeypatch.setattr(player_mod, "save_paused_turn", fake_save)

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    events = [
        {"kind": "run_started", "payload": {"run_id": "c1", "kind": "captain"}, "t_ms": 0},
        {"kind": required_kind, "payload": payload, "t_ms": 100},
        {"kind": resolved_kind, "payload": {"decision": "continue"}, "t_ms": 200},
        {"kind": "content_delta", "payload": {"delta": "after"}, "t_ms": 300},
    ]
    tape_path = tmp_path / f"{required_kind}.json"
    tape_path.write_text(
        json.dumps({"version": 1, "meta": {}, "events": events}, ensure_ascii=False),
        encoding="utf-8",
    )
    binding = TapeBinding(
        conversation_id="conv1",
        tape_path=tape_path,
        speed=100.0,
        max_gap_ms=50,
    )
    sink = EventSink(conversation_id="conv1", message_id="msg1")
    writer = TurnJournalWriter(turn_id="msg1", conversation_id="conv1", trace_id="t" * 32)

    result = await play_tape_events(
        sink=sink,
        events=events,
        start_index=0,
        binding=binding,
        message_id="msg1",
        conversation_id="conv1",
        user_id="user1",
        user_message="go",
        folder_id=None,
        journal_writer=writer,
    )
    assert result["finish_reason"] is FinishReason.PAUSED
    assert len(saved) == 1
    assert isinstance(saved[0], suspension_cls)
    assert is_demo_tape_frame(saved[0])
    types = [e.type for e in sink._history]
    assert event_required in types

    # selected / adjust must not fork the recorded stream — still end with "after".
    resume_decision = (
        CheckpointDecision.ADJUST
        if required_kind == "plan_review_required"
        else CheckpointDecision.CONTINUE
    )
    selected = ["q1"] if required_kind == "checkpoint_required" else []
    sink2 = EventSink(conversation_id="conv1", message_id="msg1")
    result2 = await continue_tape_turn(
        suspension=saved[0],
        response=CheckpointResponse(
            decision=resume_decision, note="steer-ignored", selected=selected
        ),
        sink=sink2,
        folder_id=None,
        trace_id="t" * 32,
    )
    assert result2["finish_reason"] is FinishReason.END_TURN
    assert result2.get("content") == "after"
    types2 = [e.type for e in sink2._history]
    assert event_resolved in types2
    assert types2.count(event_resolved) == 1
    resolved = next(e for e in sink2._history if e.type is event_resolved)
    assert resolved.payload["checkpoint_id"] == saved[0].checkpoint_id
    assert resolved.payload["decision"] == resume_decision.value
    if selected:
        assert resolved.payload.get("selected") == selected


@pytest.mark.asyncio
async def test_cold_path_kinds_remint_distinct_across_replays(monkeypatch, tmp_path: Path):
    """二次回放 remint 不串卡：checkpoint / plan_review 身份互不相同。"""
    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import play_tape_events
    from agentcore.runtime.journal.writer import TurnJournalWriter

    saved: list = []

    async def fake_save(suspension):
        saved.append(suspension)

    monkeypatch.setattr(player_mod, "save_paused_turn", fake_save)

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    events = [
        {
            "kind": "checkpoint_required",
            "payload": {"checkpoint_id": "cp-recorded", "question": "q"},
            "t_ms": 0,
        },
        {
            "kind": "plan_review_required",
            "payload": {
                "checkpoint_id": "pr-recorded",
                "steps": [{"run_id": "r1"}],
                "pending": [],
            },
            "t_ms": 50,
        },
    ]
    tape_path = tmp_path / "remint-cold.json"
    tape_path.write_text(
        json.dumps({"version": 1, "meta": {}, "events": events}, ensure_ascii=False),
        encoding="utf-8",
    )

    emitted: list[str] = []
    for message_id in ("msg-a", "msg-b"):
        binding = TapeBinding(
            conversation_id=f"c-{message_id}",
            tape_path=tape_path,
            speed=100.0,
            max_gap_ms=50,
        )
        sink = EventSink(conversation_id=f"c-{message_id}", message_id=message_id)
        writer = TurnJournalWriter(
            turn_id=message_id, conversation_id=f"c-{message_id}", trace_id="t" * 32
        )
        result = await play_tape_events(
            sink=sink,
            events=events,
            start_index=0,
            binding=binding,
            message_id=message_id,
            conversation_id=f"c-{message_id}",
            user_id="u",
            user_message="go",
            folder_id=None,
            journal_writer=writer,
        )
        # First wired pause stops playback.
        assert result["finish_reason"] is FinishReason.PAUSED
        card = next(
            e for e in sink._history if e.type is EventType.CHECKPOINT_REQUIRED
        )
        emitted.append(str(card.payload["checkpoint_id"]))

    assert emitted[0] != emitted[1]
    assert "cp-recorded" not in emitted
    assert emitted[0] == replay_interaction_id("cp-recorded", message_id="msg-a")
    assert emitted[1] == replay_interaction_id("cp-recorded", message_id="msg-b")
    assert [s.checkpoint_id for s in saved] == emitted

@pytest.mark.asyncio
async def test_player_hot_approval_awaits_resolve_and_continues(monkeypatch, tmp_path: Path):
    """approval 挂起 → 热 resolve → 现场重发 resolved → 续播闭环（回合不收口）。"""
    import asyncio

    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import play_tape_events
    from agentcore.demo_tape.transport import PlaybackState, PlaybackTransport
    from agentcore.runtime.approvals import ApprovalDecision
    from agentcore.runtime.interaction import InteractionKind, default_interaction_registry
    from agentcore.runtime.journal.writer import TurnJournalWriter

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    events = [
        {"kind": "content_delta", "payload": {"delta": "before"}, "t_ms": 0},
        {
            "kind": "approval_required",
            "payload": {
                "approval_id": "ap-src",
                "tool_call_id": "tc-src",
                "tool_name": "file_write",
                "arguments": {"path": "a.txt"},
            },
            "t_ms": 100,
        },
        {
            "kind": "approval_resolved",
            "payload": {
                "approval_id": "ap-src",
                "tool_call_id": "tc-src",
                "decision": "approve",
            },
            "t_ms": 5000,
        },
        {"kind": "content_delta", "payload": {"delta": "after"}, "t_ms": 5100},
    ]
    tape_path = tmp_path / "hot-approval.json"
    tape_path.write_text(
        json.dumps({"version": 1, "meta": {}, "events": events}, ensure_ascii=False),
        encoding="utf-8",
    )
    binding = TapeBinding(
        conversation_id="conv-hot",
        tape_path=tape_path,
        speed=100.0,
        max_gap_ms=50,
    )
    transport = PlaybackTransport(
        conversation_id="conv-hot",
        tape_path=tape_path,
        speed=100.0,
        max_gap_ms=50,
        event_count=len(events),
        duration_ms=5100,
    )
    sink = EventSink(conversation_id="conv-hot", message_id="msg-hot")
    writer = TurnJournalWriter(
        turn_id="msg-hot", conversation_id="conv-hot", trace_id="t" * 32
    )
    registry = default_interaction_registry()

    async def resolve_soon() -> None:
        for _ in range(200):
            pending = [
                r
                for r in registry.list_pending("conv-hot")
                if r.kind is InteractionKind.APPROVAL
            ]
            if pending:
                assert transport.state is PlaybackState.AWAITING_INTERACTION
                ok = registry.resolve(
                    pending[0].id,
                    ApprovalDecision.DENY,
                    conversation_id="conv-hot",
                )
                assert ok
                return
            await asyncio.sleep(0.01)
        raise AssertionError("approval never registered")

    resolver = asyncio.create_task(resolve_soon())
    result = await play_tape_events(
        sink=sink,
        events=events,
        start_index=0,
        binding=binding,
        message_id="msg-hot",
        conversation_id="conv-hot",
        user_id="u",
        user_message="go",
        folder_id=None,
        journal_writer=writer,
        transport=transport,
    )
    await resolver
    assert result["finish_reason"] is FinishReason.END_TURN
    assert result.get("content") == "beforeafter"
    types = [e.type for e in sink._history]
    assert EventType.APPROVAL_REQUIRED in types
    assert EventType.APPROVAL_RESOLVED in types
    assert types.count(EventType.APPROVAL_RESOLVED) == 1
    resolved = next(e for e in sink._history if e.type is EventType.APPROVAL_RESOLVED)
    required = next(e for e in sink._history if e.type is EventType.APPROVAL_REQUIRED)
    assert required.payload["approval_id"] != "ap-src"
    assert resolved.payload["approval_id"] == required.payload["approval_id"]
    assert resolved.payload["decision"] == ApprovalDecision.DENY.value
    # Recorded resolve skipped — only the live re-emit.
    assert registry.list_pending("conv-hot") == []
    assert transport.state is PlaybackState.FINISHED


@pytest.mark.asyncio
async def test_hot_approval_remint_distinct_across_replays(monkeypatch, tmp_path: Path):
    """二次回放 remint 不串卡：approval_id 按 message_id 重铸。"""
    import asyncio

    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import play_tape_events
    from agentcore.runtime.approvals import ApprovalDecision
    from agentcore.runtime.interaction import InteractionKind, default_interaction_registry
    from agentcore.runtime.journal.writer import TurnJournalWriter

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    events = [
        {
            "kind": "approval_required",
            "payload": {
                "approval_id": "ap-recorded",
                "tool_call_id": "tc-recorded",
                "tool_name": "file_write",
                "arguments": {},
            },
            "t_ms": 0,
        },
        {"kind": "content_delta", "payload": {"delta": "x"}, "t_ms": 10},
    ]
    tape_path = tmp_path / "remint-hot.json"
    tape_path.write_text(
        json.dumps({"version": 1, "meta": {}, "events": events}, ensure_ascii=False),
        encoding="utf-8",
    )
    registry = default_interaction_registry()
    minted: list[str] = []

    for message_id in ("msg-a", "msg-b"):
        cid = f"c-{message_id}"
        binding = TapeBinding(
            conversation_id=cid, tape_path=tape_path, speed=100.0, max_gap_ms=50
        )
        sink = EventSink(conversation_id=cid, message_id=message_id)
        writer = TurnJournalWriter(
            turn_id=message_id, conversation_id=cid, trace_id="t" * 32
        )

        async def resolve_soon(conv_id: str = cid) -> None:
            for _ in range(200):
                pending = [
                    r
                    for r in registry.list_pending(conv_id)
                    if r.kind is InteractionKind.APPROVAL
                ]
                if pending:
                    registry.resolve(
                        pending[0].id,
                        ApprovalDecision.APPROVE,
                        conversation_id=conv_id,
                    )
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("approval never registered")

        task = asyncio.create_task(resolve_soon())
        result = await play_tape_events(
            sink=sink,
            events=events,
            start_index=0,
            binding=binding,
            message_id=message_id,
            conversation_id=cid,
            user_id="u",
            user_message="go",
            folder_id=None,
            journal_writer=writer,
        )
        await task
        assert result["finish_reason"] is FinishReason.END_TURN
        card = next(e for e in sink._history if e.type is EventType.APPROVAL_REQUIRED)
        minted.append(str(card.payload["approval_id"]))

    assert minted[0] != minted[1]
    assert "ap-recorded" not in minted
    assert minted[0] == replay_interaction_id("ap-recorded", message_id="msg-a")
    assert minted[1] == replay_interaction_id("ap-recorded", message_id="msg-b")


@pytest.mark.asyncio
async def test_hot_approval_wait_does_not_drift_pacing(monkeypatch, tmp_path: Path):
    """等待期不计入节奏：resolve 后下一拍按录制间隔（首拍 gap=0，不重睡决策空窗）。"""
    import asyncio

    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import play_tape_events
    from agentcore.runtime.approvals import ApprovalDecision
    from agentcore.runtime.interaction import InteractionKind, default_interaction_registry
    from agentcore.runtime.journal.writer import TurnJournalWriter

    pacing_delays: list[float] = []
    _real_sleep = asyncio.sleep
    _orig_gap = player_mod.sleep_ms_for_gap

    def track_gap(**kwargs):  # noqa: ANN003
        delay = _orig_gap(**kwargs)
        if delay > 0:
            pacing_delays.append(delay)
        return delay

    monkeypatch.setattr(player_mod, "sleep_ms_for_gap", track_gap)

    async def no_sleep(seconds: float) -> None:
        await _real_sleep(0)

    monkeypatch.setattr(player_mod, "pacing_sleep", no_sleep)

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    # Large recorded gap between approval and next event (= human decision window).
    events = [
        {"kind": "content_delta", "payload": {"delta": "a"}, "t_ms": 0},
        {
            "kind": "approval_required",
            "payload": {
                "approval_id": "ap1",
                "tool_call_id": "tc1",
                "tool_name": "file_write",
                "arguments": {},
            },
            "t_ms": 100,
        },
        {"kind": "content_delta", "payload": {"delta": "b"}, "t_ms": 50_100},
        {"kind": "content_delta", "payload": {"delta": "c"}, "t_ms": 50_200},
    ]
    tape_path = tmp_path / "pacing-hot.json"
    tape_path.write_text(
        json.dumps({"version": 1, "meta": {}, "events": events}, ensure_ascii=False),
        encoding="utf-8",
    )
    binding = TapeBinding(
        conversation_id="conv-pace",
        tape_path=tape_path,
        speed=1.0,
        max_gap_ms=60_000,
    )
    sink = EventSink(conversation_id="conv-pace", message_id="msg-pace")
    writer = TurnJournalWriter(
        turn_id="msg-pace", conversation_id="conv-pace", trace_id="t" * 32
    )
    registry = default_interaction_registry()

    async def resolve_soon() -> None:
        for _ in range(200):
            pending = [
                r
                for r in registry.list_pending("conv-pace")
                if r.kind is InteractionKind.APPROVAL
            ]
            if pending:
                registry.resolve(
                    pending[0].id,
                    ApprovalDecision.APPROVE_ALWAYS,
                    conversation_id="conv-pace",
                )
                return
            await _real_sleep(0.01)
        raise AssertionError("approval never registered")

    task = asyncio.create_task(resolve_soon())
    result = await play_tape_events(
        sink=sink,
        events=events,
        start_index=0,
        binding=binding,
        message_id="msg-pace",
        conversation_id="conv-pace",
        user_id="u",
        user_message="go",
        folder_id=None,
        journal_writer=writer,
    )
    await task
    assert result["finish_reason"] is FinishReason.END_TURN
    assert result.get("content") == "abc"
    # After hot resolve prev_t resets → first post-approval beat gap=0 (not 50s).
    # Pacing delays: ~0.1s (a→approval) then ~0.1s (b→c). Never the 50s decision window.
    assert pacing_delays == pytest.approx([0.1, 0.1], abs=0.02), pacing_delays


@pytest.mark.asyncio
async def test_hot_approval_cancel_clears_registry(monkeypatch, tmp_path: Path):
    """取消回合：等待中的热路登记须被 orphan/cancel 清理。"""
    import asyncio

    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import play_tape_events
    from agentcore.runtime.interaction import InteractionKind, default_interaction_registry
    from agentcore.runtime.interaction_orphan import orphan_registry_pending
    from agentcore.runtime.journal.writer import TurnJournalWriter

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    events = [
        {
            "kind": "approval_required",
            "payload": {
                "approval_id": "ap-cancel",
                "tool_call_id": "tc-cancel",
                "tool_name": "file_write",
                "arguments": {},
            },
            "t_ms": 0,
        },
        {"kind": "content_delta", "payload": {"delta": "never"}, "t_ms": 10},
    ]
    tape_path = tmp_path / "cancel-hot.json"
    tape_path.write_text(
        json.dumps({"version": 1, "meta": {}, "events": events}, ensure_ascii=False),
        encoding="utf-8",
    )
    binding = TapeBinding(
        conversation_id="conv-cancel",
        tape_path=tape_path,
        speed=100.0,
        max_gap_ms=50,
    )
    sink = EventSink(conversation_id="conv-cancel", message_id="msg-cancel")
    writer = TurnJournalWriter(
        turn_id="msg-cancel", conversation_id="conv-cancel", trace_id="t" * 32
    )
    registry = default_interaction_registry()

    play_task = asyncio.create_task(
        play_tape_events(
            sink=sink,
            events=events,
            start_index=0,
            binding=binding,
            message_id="msg-cancel",
            conversation_id="conv-cancel",
            user_id="u",
            user_message="go",
            folder_id=None,
            journal_writer=writer,
        )
    )
    for _ in range(200):
        pending = [
            r
            for r in registry.list_pending("conv-cancel")
            if r.kind is InteractionKind.APPROVAL
        ]
        if pending:
            break
        await asyncio.sleep(0.01)
    else:
        play_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await play_task
        raise AssertionError("approval never registered")

    await orphan_registry_pending("conv-cancel", turn_id="msg-cancel")
    assert registry.list_pending("conv-cancel") == []
    with pytest.raises(asyncio.CancelledError):
        await play_task


# ── multi-act (turns[]) scripts ─────────────────────────────────────────────


def _act_doc(prompt: str, *deltas: str, followups: list[str] | None = None) -> dict:
    events = [
        {
            "type": "run_started",
            "payload": {"run_id": "c1", "kind": "captain"},
            "timestamp": None,
            "t_ms": 0,
        },
    ]
    t = 10
    for d in deltas:
        events.append(
            {
                "type": "content_delta",
                "payload": {"delta": d},
                "timestamp": None,
                "t_ms": t,
            }
        )
        t += 10
    meta: dict = {"user_prompt": prompt, "event_count": len(events), "duration_ms": t - 10}
    if followups:
        meta["followups"] = list(followups)
    return {"version": 2, "meta": meta, "events": events}


def test_load_tape_normalizes_stock_single_act_to_one_turn(tmp_path: Path):
    path = tmp_path / "stock.json"
    on_disk = {
        "version": 2,
        "meta": {"user_prompt": "开场", "followups": ["下一步A"], "title": "t"},
        "events": [
            {
                "type": "content_delta",
                "payload": {"delta": "hi"},
                "timestamp": None,
                "t_ms": 0,
            }
        ],
    }
    path.write_text(json.dumps(on_disk, ensure_ascii=False), encoding="utf-8")
    loaded = load_tape(path)
    assert loaded["meta"]["turn_count"] == 1
    assert len(loaded["turns"]) == 1
    assert loaded["turns"][0]["user_prompt"] == "开场"
    assert loaded["turns"][0]["followups"] == ["下一步A"]
    assert loaded["events"][0]["type"] == "content_delta"
    # Disk untouched.
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "turns" not in raw
    assert "events" in raw


def test_load_tape_multi_act_and_assemble(tmp_path: Path):
    from agentcore.demo_tape.export import assemble_multi_turn_tape, tape_turns

    a = _act_doc("第一幕", "act1", followups=["f1"])
    b = _act_doc("第二幕", "act2")
    multi = assemble_multi_turn_tape([a, b], meta={"title": "剧本"})
    assert multi["meta"]["turn_count"] == 2
    assert multi["meta"]["user_prompt"] == "第一幕"
    assert "events" not in multi
    path = tmp_path / "multi.json"
    write_tape(path, multi)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "turns" in raw and "events" not in raw

    loaded = load_tape(path)
    acts = tape_turns(loaded)
    assert len(acts) == 2
    assert acts[0]["user_prompt"] == "第一幕"
    assert acts[0]["followups"] == ["f1"]
    assert acts[1]["user_prompt"] == "第二幕"
    assert "".join(
        e["payload"]["delta"] for e in acts[0]["events"] if e["type"] == "content_delta"
    ) == "act1"


def test_assemble_runs_per_act_gates_independently():
    """Each act is cut+gated before assemble; a clean act is not poisoned by another."""
    from agentcore.demo_tape.export import (
        TapeExportRefusedError,
        assert_export_allowed,
        build_tape_from_recording,
    )

    clean = build_tape_from_recording(
        {
            "meta": {"conversation_id": "c", "message_id": "m1"},
            "segments": [
                {
                    "wall_t0_ms": 0,
                    "events": [
                        {
                            "type": "content_delta",
                            "payload": {"delta": "ok"},
                            "timestamp": None,
                            "t_ms": 0,
                        }
                    ],
                }
            ],
        },
        user_prompt="p1",
    )
    dirty_events = [
        {
            "type": "workspace_op_required",
            "payload": {"op_id": "x"},
            "timestamp": None,
            "t_ms": 0,
        }
    ]
    with pytest.raises(TapeExportRefusedError):
        assert_export_allowed(dirty_events)
    # Clean single-act export still succeeds on its own.
    assert_export_allowed(clean["events"])


def test_catalog_exposes_turn_count_and_first_act_prompt(tmp_path: Path, monkeypatch):
    from agentcore.demo_tape import catalog as catalog_mod
    from agentcore.demo_tape.catalog import list_tapes

    tapes = tmp_path / "demos" / "tapes"
    tapes.mkdir(parents=True)
    (tapes / "multi.json").write_text(
        json.dumps(
            {
                "version": 2,
                "meta": {"title": "多幕"},
                "turns": [
                    {
                        "user_prompt": "幕一首句",
                        "events": [{"type": "run_started", "payload": {}, "t_ms": 0}],
                    },
                    {
                        "user_prompt": "幕二",
                        "events": [{"type": "run_started", "payload": {}, "t_ms": 0}],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tapes / "single.json").write_text(
        json.dumps(
            {
                "version": 2,
                "meta": {"title": "单幕", "user_prompt": "只有一句"},
                "events": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog_mod, "PROJECT_ROOT", tmp_path)
    found = {t.id: t for t in list_tapes()}
    assert found["multi"].turn_count == 2
    assert found["multi"].user_prompt == "幕一首句"
    assert found["single"].turn_count == 1
    assert found["single"].user_prompt == "只有一句"


@pytest.mark.asyncio
async def test_multi_act_advances_cursor_and_unbinds_on_last(
    tmp_path: Path, monkeypatch
):
    from agentcore.demo_tape import binding as binding_mod
    from agentcore.demo_tape.binding import (
        peek_binding,
        write_binding,
    )
    from agentcore.demo_tape.player import play_tape_turn
    from agentcore.runtime.journal.writer import TurnJournalWriter

    monkeypatch.setattr(binding_mod, "bindings_path", lambda: tmp_path / "bindings.json")
    monkeypatch.setattr(binding_mod.settings, "demo_tape_replay_enabled", True)

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    tape_path = tmp_path / "script.json"
    write_tape(
        tape_path,
        {
            "version": 2,
            "meta": {"title": "两幕", "user_prompt": "一"},
            "turns": [
                {
                    "user_prompt": "一",
                    "events": [
                        {
                            "type": "content_delta",
                            "payload": {"delta": "A"},
                            "timestamp": None,
                            "t_ms": 0,
                        }
                    ],
                    "followups": ["chip-a"],
                },
                {
                    "user_prompt": "二",
                    "events": [
                        {
                            "type": "content_delta",
                            "payload": {"delta": "B"},
                            "timestamp": None,
                            "t_ms": 0,
                        }
                    ],
                },
            ],
        },
    )
    write_binding("conv-multi", tape=str(tape_path), speed=100.0, max_gap_ms=0)
    assert peek_binding("conv-multi").turn_index == 0

    b0 = peek_binding("conv-multi")
    assert b0 is not None
    r0 = await play_tape_turn(
        binding=b0,
        sink=EventSink(conversation_id="conv-multi", message_id="m0"),
        message_id="m0",
        conversation_id="conv-multi",
        user_id="u",
        user_message="anything",
        folder_id=None,
        trace_id="a" * 32,
    )
    assert r0["finish_reason"] is FinishReason.END_TURN
    assert r0["content"] == "A"
    assert r0.get("followups") is None  # chips offline; per-turn followups ignored
    mid = peek_binding("conv-multi")
    assert mid is not None and mid.turn_index == 1

    r1 = await play_tape_turn(
        binding=mid,
        sink=EventSink(conversation_id="conv-multi", message_id="m1"),
        message_id="m1",
        conversation_id="conv-multi",
        user_id="u",
        user_message="next",
        folder_id=None,
        trace_id="b" * 32,
    )
    assert r1["finish_reason"] is FinishReason.END_TURN
    assert r1["content"] == "B"
    assert peek_binding("conv-multi") is None


@pytest.mark.asyncio
async def test_prepare_resets_turn_cursor(tmp_path: Path, monkeypatch):
    from agentcore.demo_tape import binding as binding_mod
    from agentcore.demo_tape.binding import (
        peek_binding,
        set_binding_turn_index,
        write_binding,
    )

    monkeypatch.setattr(binding_mod, "bindings_path", lambda: tmp_path / "bindings.json")
    write_binding("cid", tape="demos/tapes/x.json")
    set_binding_turn_index("cid", 2)
    assert peek_binding("cid").turn_index == 2
    write_binding("cid", tape="demos/tapes/x.json", speed=4.0)
    assert peek_binding("cid").turn_index == 0


@pytest.mark.asyncio
async def test_multi_act_cold_pause_and_resume_keeps_act_cursor(
    tmp_path: Path, monkeypatch
):
    """幕内冷路暂停：帧游标推进，幕游标不变；resume 后 END_TURN 才推进幕。"""
    from agentcore.demo_tape import binding as binding_mod
    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import peek_binding, write_binding
    from agentcore.demo_tape.player import continue_tape_turn, play_tape_turn
    from agentcore.demo_tape.schema import tape_frame_meta
    from agentcore.runtime.checkpoints import CheckpointDecision, CheckpointResponse
    from agentcore.runtime.journal.writer import TurnJournalWriter

    monkeypatch.setattr(binding_mod, "bindings_path", lambda: tmp_path / "bindings.json")
    monkeypatch.setattr(binding_mod.settings, "demo_tape_replay_enabled", True)

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)
    saved: list = []

    async def fake_save(suspension):
        saved.append(suspension)
        return True

    monkeypatch.setattr(player_mod, "save_paused_turn", fake_save)

    tape_path = tmp_path / "pause-multi.json"
    write_tape(
        tape_path,
        {
            "version": 2,
            "meta": {"title": "pause-script"},
            "turns": [
                {
                    "user_prompt": "幕一",
                    "events": [
                        {
                            "type": "content_delta",
                            "payload": {"delta": "brief"},
                            "timestamp": None,
                            "t_ms": 0,
                        },
                        {
                            "type": "checkpoint_required",
                            "payload": {
                                "checkpoint_id": "cp-m",
                                "question": "继续？",
                            },
                            "timestamp": None,
                            "t_ms": 100,
                        },
                        {
                            "type": "content_delta",
                            "payload": {"delta": "after"},
                            "timestamp": None,
                            "t_ms": 200,
                        },
                    ],
                },
                {
                    "user_prompt": "幕二",
                    "events": [
                        {
                            "type": "content_delta",
                            "payload": {"delta": "second"},
                            "timestamp": None,
                            "t_ms": 0,
                        }
                    ],
                },
            ],
        },
    )
    write_binding("conv-pause-m", tape=str(tape_path), speed=100.0, max_gap_ms=0)
    binding = peek_binding("conv-pause-m")
    assert binding is not None and binding.turn_index == 0

    result = await play_tape_turn(
        binding=binding,
        sink=EventSink(conversation_id="conv-pause-m", message_id="msg-p"),
        message_id="msg-p",
        conversation_id="conv-pause-m",
        user_id="u",
        user_message="幕一",
        folder_id=None,
        trace_id="c" * 32,
    )
    assert result["finish_reason"] is FinishReason.PAUSED
    assert peek_binding("conv-pause-m").turn_index == 0  # 幕游标未动
    assert len(saved) == 1
    meta = tape_frame_meta(saved[0])
    assert meta.get("turn_index") == 0
    assert meta.get("next_index") == 2

    result2 = await continue_tape_turn(
        suspension=saved[0],
        response=CheckpointResponse(decision=CheckpointDecision.CONTINUE, note=""),
        sink=EventSink(conversation_id="conv-pause-m", message_id="msg-p"),
        folder_id=None,
        trace_id="c" * 32,
    )
    assert result2["finish_reason"] is FinishReason.END_TURN
    assert "after" in (result2.get("content") or "")
    assert peek_binding("conv-pause-m").turn_index == 1


@pytest.mark.asyncio
async def test_multi_act_hot_approval_within_act(tmp_path: Path, monkeypatch):
    """幕内热路 approval：挂起等待 resolve，幕游标不变直至 END_TURN。"""
    import asyncio

    from agentcore.demo_tape import binding as binding_mod
    from agentcore.demo_tape.binding import peek_binding, write_binding
    from agentcore.demo_tape.player import play_tape_turn
    from agentcore.runtime.approvals import ApprovalDecision
    from agentcore.runtime.interaction import InteractionKind, default_interaction_registry
    from agentcore.runtime.journal.writer import TurnJournalWriter

    monkeypatch.setattr(binding_mod, "bindings_path", lambda: tmp_path / "bindings.json")
    monkeypatch.setattr(binding_mod.settings, "demo_tape_replay_enabled", True)

    async def noop_flush(self):
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    tape_path = tmp_path / "hot-multi.json"
    write_tape(
        tape_path,
        {
            "version": 2,
            "meta": {"title": "hot-script"},
            "turns": [
                {
                    "user_prompt": "幕一",
                    "events": [
                        {
                            "type": "approval_required",
                            "payload": {
                                "approval_id": "ap-m",
                                "tool_call_id": "tc-m",
                                "tool_name": "file_write",
                                "arguments": {},
                            },
                            "timestamp": None,
                            "t_ms": 0,
                        },
                        {
                            "type": "content_delta",
                            "payload": {"delta": "done"},
                            "timestamp": None,
                            "t_ms": 10,
                        },
                    ],
                },
                {
                    "user_prompt": "幕二",
                    "events": [
                        {
                            "type": "content_delta",
                            "payload": {"delta": "x"},
                            "timestamp": None,
                            "t_ms": 0,
                        }
                    ],
                },
            ],
        },
    )
    write_binding("conv-hot-m", tape=str(tape_path), speed=100.0, max_gap_ms=0)
    binding = peek_binding("conv-hot-m")
    assert binding is not None
    registry = default_interaction_registry()

    async def resolve_soon() -> None:
        for _ in range(200):
            pending = [
                r
                for r in registry.list_pending("conv-hot-m")
                if r.kind is InteractionKind.APPROVAL
            ]
            if pending:
                assert peek_binding("conv-hot-m").turn_index == 0
                registry.resolve(
                    pending[0].id,
                    ApprovalDecision.APPROVE,
                    conversation_id="conv-hot-m",
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("approval never registered")

    resolve_task = asyncio.create_task(resolve_soon())
    result = await play_tape_turn(
        binding=binding,
        sink=EventSink(conversation_id="conv-hot-m", message_id="msg-hm"),
        message_id="msg-hm",
        conversation_id="conv-hot-m",
        user_id="u",
        user_message="幕一",
        folder_id=None,
        trace_id="d" * 32,
    )
    await resolve_task
    assert result["finish_reason"] is FinishReason.END_TURN
    assert result["content"] == "done"
    assert peek_binding("conv-hot-m").turn_index == 1
