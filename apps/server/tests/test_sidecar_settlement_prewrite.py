"""Sidecar local settlement prewrite (回合恢复状态机收口 · D1)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agentcore.conversation.store.outbox import OutboxStore, journal_entries_from_map
from agentcore.runtime.suspension import AskUserSuspension
from agentcore.sidecar.paused_store import LocalPausedTurnStore
from agentcore.sidecar.settlement_prewrite import (
    outbox_has_settlement_for_frame,
    prewrite_sidecar_resume_settlement,
)


def _ask(message_id: str = "m1", conversation_id: str = "c1") -> AskUserSuspension:
    susp = AskUserSuspension(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id="u1",
        captain_run_id="r1",
        checkpoint_id="cp1",
        tool_call_id="tc1",
        base_system_prompt="sys",
        user_message="原始问题",
        transcript=[],
        history=[],
        question="要继续吗？\n背景",
        questions=[
            {
                "id": "q0",
                "prompt": "要继续吗？",
                "kind": "choice",
                "options": ["是", "否"],
                "multiple": False,
                "default": "",
            }
        ],
    )
    susp.journal_entries = [
        {
            "kind": "process_reasoning",
            "payload": {"kind": "reasoning", "text": "先想清楚范围"},
            "ts": None,
        },
        {
            "kind": "process_content",
            "payload": {"kind": "content", "text": "旁白"},
            "ts": None,
        },
        {"kind": "checkpoint_required", "payload": {"checkpoint_id": "cp1"}, "ts": None},
    ]
    return susp


@pytest.mark.asyncio
async def test_sidecar_settlement_prewrite_embeds_resume_frame(tmp_path) -> None:
    outbox = OutboxStore(tmp_path / "outbox")
    outbox.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="原始问题",
        message_id="m1",
        trace_id="a" * 32,
    )
    await outbox.begin_turn(conversation_id="c1", message_id="m1", trace_id="a" * 32)
    susp = _ask()
    entry = await prewrite_sidecar_resume_settlement(
        outbox,
        susp,
        decision="continue",
        note="ok",
        selected=["是"],
        user_message_id="u1",
        trace_id="a" * 32,
    )
    assert entry["kind"] == "checkpoint_resolved"
    assert entry["payload"]["resume_frame"]["frame"]["checkpoint_id"] == "cp1"
    record = outbox.find_record_by_message_id("m1")
    assert record is not None
    entries = journal_entries_from_map(record.get("journal")) or []
    assert any(
        isinstance((e.get("payload") or {}).get("resume_frame"), dict)
        and (e.get("payload") or {}).get("resume_frame", {}).get("frame")
        for e in entries
        if isinstance(e, dict)
    )
    # Hang-frame process_* seeded before settlement (empty-outbox GAP fix).
    kinds = [e.get("kind") for e in entries]
    assert "process_reasoning" in kinds
    assert "process_content" in kinds
    assert kinds.index("process_reasoning") < kinds.index("checkpoint_resolved")
    assert kinds.index("checkpoint_required") < kinds.index("checkpoint_resolved")
    assert kinds[-1] == "checkpoint_resolved"
    # Idempotent re-prewrite does not fan out rows.
    await prewrite_sidecar_resume_settlement(
        outbox,
        _ask(),
        decision="continue",
        note="ok",
        selected=["是"],
        user_message_id="u1",
        trace_id="a" * 32,
    )
    record2 = outbox.find_record_by_message_id("m1")
    entries2 = journal_entries_from_map(record2.get("journal")) or []
    resolved = [e for e in entries2 if e.get("kind") == "checkpoint_resolved"]
    assert len(resolved) == 1
    assert sum(1 for e in entries2 if e.get("kind") == "process_reasoning") == 1


def _team_preview() -> Any:
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.suspension import TeamPreviewSuspension

    susp = TeamPreviewSuspension(
        message_id="m-tp",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="r1",
        checkpoint_id="ck-tp",
        tool_call_id="tc1",
        base_system_prompt="sys",
        user_message="开工",
        transcript=[],
        history=[],
        plan=RunPlan(),
        workers=[
            {"run_id": "a", "depends_on": []},
            {"run_id": "b", "depends_on": []},
        ],
        primitive="delegate",
    )
    susp.journal_entries = [
        {"kind": "team_preview_required", "payload": {"checkpoint_id": "ck-tp"}, "ts": None},
    ]
    return susp


@pytest.mark.asyncio
async def test_sidecar_settlement_prewrite_team_preview_veto_fields(tmp_path) -> None:
    """开工否决字段进入 team_preview_resolved + resume_frame（对齐云 cold settlement）。"""
    outbox = OutboxStore(tmp_path / "outbox")
    outbox.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="开工",
        message_id="m-tp",
        trace_id="a" * 32,
    )
    await outbox.begin_turn(conversation_id="c1", message_id="m-tp", trace_id="a" * 32)
    susp = _team_preview()
    entry = await prewrite_sidecar_resume_settlement(
        outbox,
        susp,
        decision="continue",
        note="",
        selected=[],
        user_message_id="u1",
        trace_id="a" * 32,
        excluded_run_ids=["b"],
        write_capability_overrides=[{"run_id": "a", "capability": "text_only"}],
    )
    assert entry["kind"] == "team_preview_resolved"
    payload = entry["payload"]
    assert payload.get("excluded_run_ids") == ["b"]
    assert payload.get("write_capability_overrides") == [
        {"run_id": "a", "capability": "text_only"}
    ]
    frame = payload["resume_frame"]
    assert frame["excluded_run_ids"] == ["b"]
    assert frame["write_capability_overrides"] == [
        {"run_id": "a", "capability": "text_only"}
    ]


@pytest.mark.asyncio
async def test_sidecar_settlement_prewrite_seeds_hang_frame_on_empty_outbox(
    tmp_path,
) -> None:
    """Empty outbox resume: hang-frame process_* + settlement, not settlement@seq0 alone."""
    outbox = OutboxStore(tmp_path / "outbox")
    outbox.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="原始问题",
        message_id="m1",
        trace_id="a" * 32,
    )
    await outbox.begin_turn(conversation_id="c1", message_id="m1", trace_id="a" * 32)
    susp = _ask()
    await prewrite_sidecar_resume_settlement(
        outbox,
        susp,
        decision="continue",
        selected=["是"],
        user_message_id="u1",
        trace_id="a" * 32,
    )
    record = outbox.find_record_by_message_id("m1")
    assert record is not None
    journal = record.get("journal") or {}
    assert journal["0"]["kind"] == "process_reasoning"
    assert journal["1"]["kind"] == "process_content"
    assert journal["2"]["kind"] == "checkpoint_required"
    assert journal["3"]["kind"] == "checkpoint_resolved"
    from agentcore.runtime.journal import runs_from_entries

    runs = runs_from_entries(journal_entries_from_map(journal) or [])
    assert runs is not None
    process = runs.get("process") or []
    assert any(
        isinstance(s, dict) and s.get("kind") == "reasoning" for s in process
    ), process


@pytest.mark.asyncio
async def test_resume_cancel_salvage_keeps_pre_pause_process(tmp_path) -> None:
    """Cancel after prewrite: salvage journal projects runs.process with thinking steps."""
    from agentcore.conversation.turn_persistence import compose_salvage_journal
    from agentcore.runtime.journal import KIND_TURN_END, runs_from_entries
    from agentcore.sidecar.server_pkg.turns import _ensure_cancelled_turn_end

    outbox = OutboxStore(tmp_path / "outbox")
    outbox.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="原始问题",
        message_id="m1",
        trace_id="a" * 32,
    )
    await outbox.begin_turn(conversation_id="c1", message_id="m1", trace_id="a" * 32)
    susp = _ask()
    await prewrite_sidecar_resume_settlement(
        outbox,
        susp,
        decision="continue",
        selected=["是"],
        user_message_id="u1",
        trace_id="a" * 32,
    )
    # Live post-resume journal (team only) — without merge, process_* would be lost.
    live = [
        {
            "kind": "run_started",
            "payload": {"run_id": "w1", "agent_id": "researcher", "name": "研"},
            "ts": None,
        },
    ]
    merged = _ensure_cancelled_turn_end(
        compose_salvage_journal(live, susp.journal_entries)
    )
    await outbox.salvage(
        journal=merged,
        content="续跑半段",
        conversation_id="c1",
        trace_id="a" * 32,
        message_id="m1",
    )
    record = outbox.find_record_by_message_id("m1")
    assert record is not None
    assert record["phase"] == "ready"
    entries = journal_entries_from_map(record.get("journal")) or []
    kinds = [e.get("kind") for e in entries]
    assert "process_reasoning" in kinds
    assert "run_started" in kinds
    assert kinds.index("process_reasoning") < kinds.index("run_started")
    assert kinds[-1] == KIND_TURN_END
    assert (entries[-1].get("payload") or {}).get("finish_reason") == "cancelled"
    runs = runs_from_entries(entries)
    assert runs is not None
    assert runs.get("finish_reason") == "cancelled"
    process = runs.get("process") or []
    assert any(isinstance(s, dict) and s.get("kind") == "reasoning" for s in process)
    # Not team-only: process lane carries pre-resume thinking.
    assert not (process == [] and (runs.get("events") or []))

def test_recover_stale_claims_consumes_when_settlement_present(tmp_path) -> None:
    paused = tmp_path / "paused"
    outbox_dir = tmp_path / "outbox"
    paused.mkdir()
    outbox_dir.mkdir()
    susp = _ask()
    record = {
        "message_id": "m1",
        "conversation_id": "c1",
        "frame": susp.to_json(),
        "journal_entries": susp.journal_entries,
        "history": [],
        "summary": {},
        "created_at": 0.0,
    }
    (paused / "m1.json.claimed").write_text(json.dumps(record), encoding="utf-8")
    # Seed outbox journal with settlement (D1 prewrite succeeded, crash before confirm).
    outbox_record = {
        "schema_version": 1,
        "user_message_id": "u1",
        "conversation_id": "c1",
        "message_id": "m1",
        "trace_id": "a" * 32,
        "user_message": "q",
        "journal": {
            "0": {
                "kind": "checkpoint_resolved",
                "payload": {
                    "checkpoint_id": "cp1",
                    "decision": "continue",
                    "resume_frame": {"frame": susp.to_json()},
                },
            }
        },
        "phase": "open",
        "updated_at": 1.0,
        "ops": ["settlement_prewrite"],
    }
    (outbox_dir / "u1.json").write_text(json.dumps(outbox_record), encoding="utf-8")

    store = LocalPausedTurnStore(paused, outbox_base=outbox_dir)
    assert not (paused / "m1.json.claimed").exists()
    assert not (paused / "m1.json").exists()  # consumed, not restored

    async def pending() -> list[str]:
        return [s.message_id for s in await store.list_pending("c1")]

    assert asyncio.run(pending()) == []


def test_recover_stale_claims_restores_without_settlement(tmp_path) -> None:
    paused = tmp_path / "paused"
    outbox_dir = tmp_path / "outbox"
    paused.mkdir()
    outbox_dir.mkdir()
    susp = _ask()
    record = {
        "message_id": "m1",
        "conversation_id": "c1",
        "frame": susp.to_json(),
        "journal_entries": [],
        "history": [],
        "summary": {},
        "created_at": 0.0,
    }
    (paused / "m1.json.claimed").write_text(json.dumps(record), encoding="utf-8")
    LocalPausedTurnStore(paused, outbox_base=outbox_dir)
    assert (paused / "m1.json").is_file()
    assert not outbox_has_settlement_for_frame(
        outbox_dir,
        message_id="m1",
        checkpoint_id="cp1",
        suspension_kind="ask_user",
    )


def test_resume_failure_after_prewrite_does_not_restore_frame(tmp_path, monkeypatch) -> None:
    """D1: pipeline crash after settlement confirm must not resurrect the decision card."""
    from agentcore.sidecar import protocol
    from agentcore.sidecar.server import SidecarServer

    async def fake_resume(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("simulated resume crash after settlement")

    monkeypatch.setattr("agentcore.sidecar.server.resume_chat_pipeline", fake_resume)

    sent: list[dict[str, Any]] = []

    async def write_line(line: str) -> None:
        sent.append(json.loads(line))

    server = SidecarServer(write_line)
    data = tmp_path / "data"

    async def drive() -> tuple[list[Any], dict[str, Any]]:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "u",
                        "workspaceRoot": str(tmp_path),
                        "dataDir": str(data),
                        "approvalsEnabled": True,
                        "inference": {
                            "baseUrl": "http://test.local/v1/inference/v1",
                            "apiKey": "test-inference-tok",
                            "model": "test-model",
                        },
                    },
                }
            )
        )
        assert server._paused_store is not None
        await server._paused_store.save(_ask())
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "resume",
                    "params": {
                        "messageId": "m1",
                        "conversationId": "c1",
                        "decision": "continue",
                        "note": "",
                        "userMessageId": "u1",
                        "traceId": "a" * 32,
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))
        remaining = await server._paused_store.list_pending("c1")
        err = next(m for m in sent if m.get("id") == 9)
        return remaining, err

    remaining, err = asyncio.run(drive())
    assert remaining == []  # frame consumed; not restored
    assert err["error"]["code"] == protocol.INTERNAL_ERROR
