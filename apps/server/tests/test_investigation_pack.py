"""Investigation pack: required file set + schema_version."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcore.observability.query.decision_spine import SCHEMA_VERSION as SPINE_SCHEMA
from agentcore.observability.query.pack import (
    PACK_SCHEMA_VERSION,
    required_pack_files,
    sanitize_timeline_event,
    write_investigation_pack,
)
from agentcore.observability.query.store import ExportConversationStore
from agentcore.observability.query.timeline import TimelineQueryResult


def _events(trace_id: str = "b" * 32) -> list[dict]:
    return [
        {
            "type": "log",
            "event": "chat.turn_start",
            "timestamp": "2026-07-31T10:00:00Z",
            "trace_id": trace_id,
            "conversation_id": "conv-pack",
            "preview": "pack me",
        },
        {
            "type": "log",
            "event": "delegate.started",
            "timestamp": "2026-07-31T10:00:01Z",
            "trace_id": trace_id,
            "agents": ["writer"],
            "nodes": 1,
        },
        {
            "type": "log",
            "event": "llm.request",
            "timestamp": "2026-07-31T10:00:02Z",
            "trace_id": trace_id,
            "model": "demo",
            "scenario": "captain",
            "prompt": "SECRET_SHOULD_NOT_SHIP",
        },
        {
            "type": "log",
            "event": "chat.turn_complete",
            "timestamp": "2026-07-31T10:00:10Z",
            "trace_id": trace_id,
            "finish_reason": "stop",
            "delegated": True,
            "workers": 1,
            "rounds": 1,
            "duration_ms": 1000,
            "input_tokens": 1,
            "output_tokens": 2,
            "boundary_yields": 0,
            "scope_signals": 0,
            "revises": 0,
            "escalations": 0,
        },
    ]


def _spine(trace_id: str = "b" * 32) -> dict:
    from agentcore.observability.query.decision_spine import build_decision_spine

    return build_decision_spine(
        _events(trace_id),
        trace_id=trace_id,
        conversation_id="conv-pack",
        traffic=None,
        jsonl_gap={"reason": "timestamp_gap", "gap_seconds": 150},
    )


@pytest.mark.asyncio
async def test_write_investigation_pack_required_files(tmp_path: Path) -> None:
    tid = "b" * 32
    spine = _spine(tid)
    result = TimelineQueryResult(
        mode="trace",
        trace_id=tid,
        log_events=_events(tid),
        decision_spine=spine,
        meta={
            "conversation_id": "conv-pack",
            "traffic": None,
            "files": ["/tmp/dev.jsonl"],
            "bad_lines": 0,
        },
    )
    meta = await write_investigation_pack(result, out_dir=tmp_path / "pack")
    out = tmp_path / "pack"
    for name in required_pack_files():
        assert (out / name).is_file(), name
    assert meta["schema_version"] == PACK_SCHEMA_VERSION
    assert meta["decision_spine_schema"] == SPINE_SCHEMA
    assert meta["jsonl_gap"]["reason"] == "timestamp_gap"
    assert set(meta["files"]) >= set(required_pack_files())

    loaded_spine = json.loads((out / "decision_spine.json").read_text(encoding="utf-8"))
    assert loaded_spine["schema_version"] == SPINE_SCHEMA
    assert loaded_spine["trace_id"] == tid
    assert loaded_spine["decisions"]

    timeline = (out / "timeline.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(timeline) == 4
    llm_row = next(
        json.loads(line) for line in timeline if json.loads(line)["event"] == "llm.request"
    )
    assert "prompt" not in llm_row
    assert llm_row["model"] == "demo"

    loaded_meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert loaded_meta["schema_version"] == PACK_SCHEMA_VERSION
    assert loaded_meta["exported_at"]
    assert "messages.json" not in loaded_meta["files"]
    assert loaded_meta["journal"]["mode"] == "absent"
    assert not (out / "turn_journal.jsonl").exists()


@pytest.mark.asyncio
async def test_write_investigation_pack_optional_and_full(tmp_path: Path) -> None:
    tid = "c" * 32
    export = tmp_path / "export"
    export.mkdir()
    (export / "turn_metrics.jsonl").write_text(
        json.dumps(
            {
                "id": "m1",
                "turn_id": "t1",
                "conversation_id": "conv-pack",
                "user_id": "u1",
                "trace_id": tid,
                "kind": "turn",
                "status": "ok",
                "finish_reason": "stop",
                "delegated": True,
                "workers": 1,
                "rounds": 1,
                "duration_ms": 100,
                "input_tokens": 1,
                "output_tokens": 2,
                "boundary_yields": 0,
                "scope_signals": 0,
                "revises": 0,
                "escalations": 0,
                "created_at": "2026-07-31T10:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (export / "messages.jsonl").write_text(
        json.dumps(
            {
                "id": "msg1",
                "conversation_id": "conv-pack",
                "role": "assistant",
                "content": "full reply body for pack",
                "created_at": "2026-07-31T10:00:10Z",
                "trace_id": tid,
                "reasoning_content": "should not dump as required",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (export / "conversations.jsonl").write_text(
        json.dumps(
            {
                "id": "conv-pack",
                "title": "t",
                "agent_id": "ceo",
                "created_at": "2026-07-31T09:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    store = ExportConversationStore(export)
    spine = _spine(tid)
    spine["health"]["turn_metrics_joined"] = True
    result = TimelineQueryResult(
        mode="trace",
        trace_id=tid,
        log_events=_events(tid),
        decision_spine=spine,
        meta={"conversation_id": "conv-pack", "traffic": "eval"},
    )
    out = tmp_path / "pack-full"
    meta = await write_investigation_pack(
        result,
        out_dir=out,
        store=store,
        full=True,
        export_dir=export,
    )
    assert "turn_metrics.json" in meta["files"]
    assert "messages.preview.json" in meta["files"]
    assert "messages.json" in meta["files"]
    assert meta["full"] is True
    assert meta["traffic"] == "eval"

    metrics = json.loads((out / "turn_metrics.json").read_text(encoding="utf-8"))
    assert metrics["trace_id"] == tid
    preview = json.loads((out / "messages.preview.json").read_text(encoding="utf-8"))
    assert preview["messages"][0]["content_preview"]
    assert "content" not in preview["messages"][0]
    full = json.loads((out / "messages.json").read_text(encoding="utf-8"))
    assert full["messages"][0]["content"] == "full reply body for pack"
    assert "reasoning_content" not in full["messages"][0]


@pytest.mark.asyncio
async def test_pack_journal_is_always_redacted_even_with_full(tmp_path: Path) -> None:
    tid = "d" * 32
    export = tmp_path / "export"
    export.mkdir()
    secret = "USER_SAID_THIS_IN_THE_PROMPT"
    (export / "turn_journal.jsonl").write_text(
        json.dumps(
            {
                "turn_id": "msg1",
                "seq": 0,
                "band": "live",
                "kind": "turn_started",
                "trace_id": tid,
                "conversation_id": "conv-pack",
                "payload": {
                    "system_prompt": secret,
                    "user_message": secret,
                    "model_profile": "chat",
                    "history_len": 1,
                },
            }
        )
        + "\n"
        + json.dumps(
            {
                "turn_id": "msg1",
                "seq": 1,
                "kind": "llm_call",
                "trace_id": tid,
                "conversation_id": "conv-pack",
                "payload": {
                    "run_id": "cap",
                    "content": secret,
                    "usage": {"input": 3, "output": 4},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (export / "conversations.jsonl").write_text(
        json.dumps({"id": "conv-pack", "title": "t", "agent_id": "ceo"}) + "\n",
        encoding="utf-8",
    )
    store = ExportConversationStore(export)
    result = TimelineQueryResult(
        mode="trace",
        trace_id=tid,
        log_events=_events(tid),
        decision_spine=_spine(tid),
        meta={"conversation_id": "conv-pack"},
    )
    out = tmp_path / "pack-journal"
    meta = await write_investigation_pack(
        result, out_dir=out, store=store, full=True, export_dir=export
    )
    assert "journal.redacted.jsonl" in meta["files"]
    assert "journal.summary.json" in meta["files"]
    assert meta["journal"]["mode"] == "redacted"
    assert meta["journal"]["rows"] == 2
    assert not (out / "turn_journal.jsonl").exists()
    blob = (out / "journal.redacted.jsonl").read_text(encoding="utf-8")
    assert secret not in blob
    rows = [json.loads(line) for line in blob.splitlines() if line]
    assert rows[0]["payload"]["model_profile"] == "chat"
    assert "user_message" not in rows[0]["payload"]
    assert rows[1]["payload"]["usage"]["input"] == 3
    summary = json.loads((out / "journal.summary.json").read_text(encoding="utf-8"))
    assert summary["llm_facts"] == 1
    assert secret not in json.dumps(summary)


def test_sanitize_drops_llm_body() -> None:
    cleaned = sanitize_timeline_event(
        {
            "event": "llm.response",
            "model": "x",
            "prompt": "nope",
            "completion": "nope",
            "timestamp": "t",
        }
    )
    assert "prompt" not in cleaned
    assert "completion" not in cleaned
    assert cleaned["model"] == "x"
