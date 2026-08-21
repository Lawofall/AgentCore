"""Sidecar durable pause/resume tests (双模式工作区 §十, 路 A).

Two layers:

- **store** — :class:`LocalPausedTurnStore` round-trips a frame to disk, lists by
  conversation, claims exactly once, scopes by conversation, and deletes — the local
  impl of the §18.6 paused-turn port (the Sidecar has no DB).
- **server** — ``initialize`` advertises ``durablePause`` from the data dir; ``startTurn``
  wires the local saver/deleter; ``listPaused`` surfaces a seeded frame; ``resume``
  claims it and drives ``resume_chat_pipeline``; a missing frame 404s.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import pytest

from agentcore.conversation.store import reset_conversation_store_for_tests
from agentcore.runtime.suspension import AskUserSuspension
from agentcore.sidecar import protocol
from agentcore.sidecar.paused_store import LocalPausedTurnStore
from agentcore.sidecar.server import SidecarServer

_CLIENT_TURN_IDS = {
    "userMessageId": "11111111-1111-4111-8111-111111111111",
    "messageId": "22222222-2222-4222-8222-222222222222",
    "traceId": "a" * 32,
}


@pytest.fixture(autouse=True)
def _reset_conversation_store():
    """Sidecar initialize swaps the process-wide store; restore CloudStore default after."""
    yield
    reset_conversation_store_for_tests()


@pytest.fixture(autouse=True)
def _stub_conversation_folder_id(monkeypatch: pytest.MonkeyPatch):
    """Unit tests without Postgres: bare folder_id=None on startTurn."""

    async def _none(_conversation_id: str) -> None:
        return None

    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.turns.load_conversation_folder_id",
        _none,
    )


def _suspension(
    message_id: str,
    conversation_id: str,
    *,
    journal_entries: list[dict[str, Any]] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> AskUserSuspension:
    """A minimal durable ask_user frame (empty transcript is enough for round-trips).

    ``journal_entries`` / ``history`` are the window-rebuild inputs the Sidecar persists
    inline (it has no DB) — defaulted empty for the bare round-trip tests.
    """
    susp = AskUserSuspension(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id="u1",
        captain_run_id="r1",
        checkpoint_id=f"cp-{message_id}",
        tool_call_id="tc1",
        base_system_prompt="sys",
        user_message="原始问题",
        transcript=[],
        history=history or [],
        question="要继续吗？\n背景",
    )
    # journal_entries is the 唯一权威载体 (P0-B Phase 3); the display ``journal`` resume seed is
    # a DERIVED property of it. Default to a single checkpoint_required fact so the round-trip's
    # derived seed carries the card.
    susp.journal_entries = (
        journal_entries
        if journal_entries is not None
        else [{"kind": "checkpoint_required", "payload": {"id": "cp"}, "ts": None}]
    )
    return susp


# --- store -------------------------------------------------------------------


def test_store_save_list_claim_round_trip(tmp_path):
    store = LocalPausedTurnStore(tmp_path / "paused")

    async def drive() -> tuple[list[Any], Any, Any]:
        await store.save(_suspension("m1", "c1"))
        listed = await store.list_pending("c1")
        first = await store.claim("m1", conversation_id="c1")
        second = await store.claim("m1", conversation_id="c1")  # one-shot
        return listed, first, second

    listed, first, second = asyncio.run(drive())
    assert [s.message_id for s in listed] == ["m1"]
    assert listed[0].question == "要继续吗？\n背景"  # ask_user card content survives
    assert first is not None
    assert first.message_id == "m1"
    assert first.journal == [
        {"type": "checkpoint_required", "payload": {"id": "cp"}, "timestamp": None}
    ]
    assert second is None  # claimed once → gone


def test_store_round_trips_journal_entries_and_history(tmp_path):
    """The Sidecar has no DB, so its local frame record IS its turn_journal + message DB:
    ``journal_entries`` (folded by ``window_from_journal`` on resume) and ``history`` (the
    window's prior-turn prefix) must survive save→claim intact (执行级事件溯源 Phase 2 ⑤)."""
    store = LocalPausedTurnStore(tmp_path / "paused")
    entries = [
        {"kind": "turn_started", "payload": {"user_message": "原始问题"}},
        {"kind": "round_boundary", "payload": {"run_id": "r1", "role": "captain", "round": 1}},
        {"type": "checkpoint_required", "payload": {"id": "cp"}},
    ]
    history = [
        {"role": "user", "content": "上一轮问题"},
        {"role": "assistant", "content": "上一轮回答"},
    ]

    async def drive() -> Any:
        await store.save(_suspension("m1", "c1", journal_entries=entries, history=history))
        return await store.claim("m1", conversation_id="c1")

    claimed = asyncio.run(drive())
    assert claimed is not None
    # NOT in the to_json frame (resume control meta only) — carried in the record instead.
    assert "transcript" not in claimed.to_json()
    assert "journal_entries" not in claimed.to_json()
    assert claimed.journal_entries == entries
    assert claimed.history == history


def test_store_save_pins_display_runs_for_desktop_reopen(tmp_path):
    """Pause frames pin ``display_runs`` so desktop hydrate can rebuild the collab graph
    before local-turns journal writeback lands."""
    store = LocalPausedTurnStore(tmp_path / "paused")
    entries = [
        {"kind": "run_plan", "payload": {"execution_id": "e1"}, "ts": "t0"},
        {"kind": "run_started", "payload": {"run_id": "w1"}, "ts": "t1"},
    ]

    async def drive() -> None:
        await store.save(_suspension("m_graph", "c1", journal_entries=entries))

    asyncio.run(drive())
    raw = json.loads((tmp_path / "paused" / "m_graph.json").read_text(encoding="utf-8"))
    runs = raw.get("display_runs")
    assert runs is not None
    assert runs.get("finish_reason") == "paused"
    types = [e.get("type") for e in (runs.get("events") or [])]
    assert "run_plan" in types


def test_store_claimed_frame_excluded_from_list_pending(tmp_path):
    """A claimed-but-not-confirmed frame lives as ``.claimed`` and is invisible to list."""
    store = LocalPausedTurnStore(tmp_path / "paused")

    async def drive() -> tuple[list[str], list[str]]:
        await store.save(_suspension("m1", "c1"))
        before = [s.message_id for s in await store.list_pending("c1")]
        await store.claim("m1", conversation_id="c1")
        during = [s.message_id for s in await store.list_pending("c1")]
        return before, during

    before, during = asyncio.run(drive())
    assert before == ["m1"]
    assert during == []


def test_store_rollback_claim_restores_pending(tmp_path):
    store = LocalPausedTurnStore(tmp_path / "paused")

    async def drive() -> list[str]:
        await store.save(_suspension("m1", "c1"))
        await store.claim("m1", conversation_id="c1")
        await store.rollback_claim("m1")
        return [s.message_id for s in await store.list_pending("c1")]

    assert asyncio.run(drive()) == ["m1"]


def test_store_confirm_claim_drops_frame(tmp_path):
    store = LocalPausedTurnStore(tmp_path / "paused")

    async def drive() -> list[str]:
        await store.save(_suspension("m1", "c1"))
        await store.claim("m1", conversation_id="c1")
        await store.confirm_claim("m1")
        return [s.message_id for s in await store.list_pending("c1")]

    assert asyncio.run(drive()) == []


@pytest.mark.asyncio
async def test_sidecar_settlement_stamps_outcome_classify_resume_miss_settled(
    tmp_path, monkeypatch
):
    """本机结算留下结论行后，云端扑空判据是 settled（不是 regenerated 404）。"""
    import contextlib
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from agentcore.conversation.store.outbox import OutboxStore
    from agentcore.db.models import PAUSED_TURN_SETTLED
    from agentcore.runtime.suspension import consumed as consumed_mod
    from agentcore.runtime.suspension.consumed import classify_resume_miss
    from agentcore.sidecar.settlement_prewrite import prewrite_sidecar_resume_settlement

    mid, cid = str(uuid4()), str(uuid4())
    outcomes: dict[tuple[str, str], SimpleNamespace] = {}

    @contextlib.asynccontextmanager
    async def _session():
        yield None

    class _Paused:
        def __init__(self, _db) -> None:  # noqa: ANN001
            pass

        async def stamp_settled(
            self,
            *,
            message_id: str,
            conversation_id: str,
            frame: dict,
            decision: str,
            settled_by: str = "",
        ) -> None:
            data = frame if isinstance(frame, dict) else {}
            outcomes[(message_id, conversation_id)] = SimpleNamespace(
                outcome=PAUSED_TURN_SETTLED,
                card_kind=str(data.get("kind") or ""),
                checkpoint_id=str(data.get("checkpoint_id") or ""),
                decision=decision,
                settled_by=settled_by,
                decided_at=datetime(2026, 8, 19, 3, 0, tzinfo=UTC),
            )

        async def get_outcome(self, message_id: str, *, conversation_id: str):
            return outcomes.get((message_id, conversation_id))

    class _Messages:
        def __init__(self, _db) -> None:  # noqa: ANN001
            pass

        async def get_by_id(self, _message_id: str, *, conversation_id: str):  # noqa: ANN001
            return None

    monkeypatch.setattr("agentcore.db.base.async_session_factory", _session)
    monkeypatch.setattr("agentcore.db.repositories.PausedTurnRepository", _Paused)
    monkeypatch.setattr(
        "agentcore.fulfill.origin.current_origin_device", lambda: "dev-sidecar"
    )
    monkeypatch.setattr(consumed_mod, "async_session_factory", _session)
    monkeypatch.setattr(consumed_mod, "PausedTurnRepository", _Paused)
    monkeypatch.setattr(consumed_mod, "MessageRepository", _Messages)

    data = tmp_path / "data"
    store = LocalPausedTurnStore(data / "paused", outbox_base=data / "outbox")
    outbox = OutboxStore(data / "outbox")
    susp = _team_preview_suspension(mid, cid)
    await store.save(susp)
    claimed = await store.claim(mid, conversation_id=cid)
    assert claimed is not None
    outbox.bind_turn(
        conversation_id=cid,
        user_message_id="u1",
        user_message="开工",
        message_id=mid,
        trace_id="a" * 32,
    )
    await outbox.begin_turn(conversation_id=cid, message_id=mid, trace_id="a" * 32)
    await prewrite_sidecar_resume_settlement(
        outbox,
        claimed,
        decision="continue",
        user_message_id="u1",
        trace_id="a" * 32,
    )
    # Settlement prewrite itself stamps (deferred path has no confirm yet).
    assert (mid, cid) in outcomes
    await store.confirm_claim(mid)

    miss = await classify_resume_miss(conversation_id=cid, message_id=mid)
    assert miss.kind == "settled"
    assert miss.card_kind == "team_preview"
    assert miss.checkpoint_id == f"ck-{mid}"
    assert miss.decision == "continue"
    assert miss.settled_by == "dev-sidecar"
    assert await store.list_pending(cid) == []


def test_store_save_raises_on_write_failure(tmp_path, monkeypatch):
    """D11：sidecar save 失败必须抛错（消灭假 saved），不得吞异常。"""
    store = LocalPausedTurnStore(tmp_path / "paused")

    def boom(_message_id: str, _record: dict) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write_sync", boom)

    with pytest.raises(OSError, match="disk full"):
        asyncio.run(store.save(_suspension("m1", "c1")))


def test_store_recovers_stale_claims_on_init(tmp_path):
    """A sidecar crash mid-resume leaves ``.claimed`` orphans; startup rolls them back."""
    paused_dir = tmp_path / "paused"
    paused_dir.mkdir()
    record = {
        "message_id": "m1",
        "conversation_id": "c1",
        "frame": _suspension("m1", "c1").to_json(),
        "journal_entries": [],
        "history": [],
        "summary": {},
        "created_at": 0.0,
    }
    claimed = paused_dir / "m1.json.claimed"
    claimed.write_text(json.dumps(record), encoding="utf-8")

    store = LocalPausedTurnStore(paused_dir)

    assert (paused_dir / "m1.json").is_file()
    assert not claimed.exists()

    async def drive() -> list[str]:
        return [s.message_id for s in await store.list_pending("c1")]

    assert asyncio.run(drive()) == ["m1"]


def test_store_claim_wrong_conversation_does_not_consume(tmp_path):
    """A claim scoped to the wrong conversation returns None AND leaves the frame
    intact — a stray / cross-conversation resume can't destroy a valid pause."""
    store = LocalPausedTurnStore(tmp_path / "paused")

    async def drive() -> tuple[Any, Any]:
        await store.save(_suspension("m1", "c1"))
        wrong = await store.claim("m1", conversation_id="other")
        right = await store.claim("m1", conversation_id="c1")
        return wrong, right

    wrong, right = asyncio.run(drive())
    assert wrong is None
    assert right is not None  # the mismatch restored it, so the owner still resumes


def test_store_list_scopes_by_conversation_and_delete(tmp_path):
    store = LocalPausedTurnStore(tmp_path / "paused")

    async def drive() -> tuple[list[str], list[str], list[str]]:
        await store.save(_suspension("m1", "c1"))
        await store.save(_suspension("m2", "c2"))
        c1 = [s.message_id for s in await store.list_pending("c1")]
        await store.delete("m1")
        c1_after = [s.message_id for s in await store.list_pending("c1")]
        c2 = [s.message_id for s in await store.list_pending("c2")]
        return c1, c1_after, c2

    c1, c1_after, c2 = asyncio.run(drive())
    assert c1 == ["m1"]
    assert c1_after == []  # deleted
    assert c2 == ["m2"]  # other conversation untouched


# --- server ------------------------------------------------------------------


def _recorder() -> tuple[list[dict[str, Any]], Any]:
    sent: list[dict[str, Any]] = []

    async def write_line(line: str) -> None:
        sent.append(json.loads(line))

    return sent, write_line


def _response(sent: list[dict[str, Any]], request_id: Any) -> dict[str, Any]:
    return next(m for m in sent if m.get("id") == request_id)


async def _initialize(server: SidecarServer, tmp_path, *, data_dir: str | None) -> None:
    params: dict[str, Any] = {
        "userId": "u",
        "workspaceRoot": str(tmp_path),
        "approvalsEnabled": True,
        # Unit tests mock the pipeline; still need inference so the early-reject
        # gate does not fire (production always mints a cloud-proxy JWT).
        "inference": {
            "baseUrl": "http://test.local/v1/inference/v1",
            "apiKey": "test-inference-tok",
            "model": "test-model",
        },
    }
    if data_dir is not None:
        params["dataDir"] = data_dir
    await server.handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params})
    )


def test_initialize_advertises_durable_pause_from_data_dir(tmp_path):
    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    asyncio.run(_initialize(server, tmp_path, data_dir=str(tmp_path / "data")))
    caps = _response(sent, 1)["result"]["capabilities"]
    assert caps["durablePause"] is True


def test_initialize_without_data_dir_disables_durable_pause(tmp_path):
    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    asyncio.run(_initialize(server, tmp_path, data_dir=None))
    caps = _response(sent, 1)["result"]["capabilities"]
    assert caps["durablePause"] is False


def test_start_turn_wires_local_suspension_hooks(tmp_path, monkeypatch):
    """With a data dir, startTurn hands the pipeline the local saver/deleter so a
    plan_review / ask_user pause persists durably."""
    captured: dict[str, Any] = {}

    async def fake_pipeline(**kwargs: Any) -> dict[str, Any]:
        captured["saver"] = kwargs.get("suspension_saver")
        captured["deleter"] = kwargs.get("suspension_deleter")
        captured["session_saver"] = kwargs.get("session_saver")
        captured["session_loader"] = kwargs.get("session_loader")
        kwargs["sink"].close()
        return {"finish_reason": "end_turn", "content": "ok", "rounds": 1}

    monkeypatch.setattr("agentcore.sidecar.server.run_chat_pipeline", fake_pipeline)

    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> None:
        await _initialize(server, tmp_path, data_dir=str(tmp_path / "data"))
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "startTurn",
                    "params": {
                        "turnId": "t1",
                        "conversationId": "c1",
                        "userMessage": "改个文件",
                        **_CLIENT_TURN_IDS,
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    asyncio.run(drive())
    assert captured["saver"] is not None
    assert captured["deleter"] is not None
    # 留人 roster: dataDir ⇒ local run_session store wired (parity with cloud callbacks).
    assert captured["session_saver"] is not None
    assert captured["session_loader"] is not None


def test_initialize_advertises_durable_roster_from_data_dir(tmp_path):
    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    asyncio.run(_initialize(server, tmp_path, data_dir=str(tmp_path / "data")))
    caps = _response(sent, 1)["result"]["capabilities"]
    assert caps["durableRoster"] is True


def test_initialize_without_data_dir_disables_durable_roster(tmp_path):
    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    asyncio.run(_initialize(server, tmp_path, data_dir=None))
    caps = _response(sent, 1)["result"]["capabilities"]
    assert caps["durableRoster"] is False


def test_start_turn_without_data_dir_leaves_session_hooks_none(tmp_path, monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_pipeline(**kwargs: Any) -> dict[str, Any]:
        captured["session_saver"] = kwargs.get("session_saver")
        captured["session_loader"] = kwargs.get("session_loader")
        kwargs["sink"].close()
        return {"finish_reason": "end_turn", "content": "ok", "rounds": 1}

    monkeypatch.setattr("agentcore.sidecar.server.run_chat_pipeline", fake_pipeline)
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> None:
        await _initialize(server, tmp_path, data_dir=None)
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "startTurn",
                    "params": {
                        **_CLIENT_TURN_IDS,
                        "turnId": "t1",
                        "conversationId": "c1",
                        "userMessage": "hi",
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    asyncio.run(drive())
    assert captured["session_saver"] is None
    assert captured["session_loader"] is None


def test_list_paused_returns_seeded_frames(tmp_path):
    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    store = LocalPausedTurnStore(tmp_path / "data" / "paused")

    async def drive() -> None:
        await _initialize(server, tmp_path, data_dir=str(tmp_path / "data"))
        await store.save(_suspension("m1", "c1"))
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "listPaused",
                    "params": {"conversationId": "c1"},
                }
            )
        )

    asyncio.run(drive())
    data = _response(sent, 5)["result"]["data"]
    assert len(data) == 1
    assert data[0]["message_id"] == "m1"
    assert data[0]["kind"] == "ask_user"
    assert data[0]["question"] == "要继续吗？\n背景"


def test_resume_claims_frame_and_drives_resume_pipeline(tmp_path, monkeypatch):
    """resume claims the durable frame (one-shot) and runs ``resume_chat_pipeline``
    with the decision + local hooks, replying with the final result for write-back."""
    captured: dict[str, Any] = {}

    async def fake_resume(**kwargs: Any) -> dict[str, Any]:
        captured["message_id"] = kwargs["suspension"].message_id
        captured["decision"] = kwargs["decision"].value
        captured["note"] = kwargs["note"]
        captured["saver"] = kwargs.get("suspension_saver")
        # The Sidecar has no DB → history must come from the claimed local frame record.
        captured["history"] = kwargs.get("history")
        # The conversation's CURRENT permission axes ride the resume params.
        captured["autonomy"] = kwargs.get("permission_axes")
        captured["x_client_platform"] = kwargs.get("x_client_platform")
        kwargs["sink"].close()
        return {
            "finish_reason": "end_turn",
            "content": "续跑完成",
            "rounds": 1,
            "message_id": kwargs["suspension"].message_id,
        }

    monkeypatch.setattr("agentcore.sidecar.server.resume_chat_pipeline", fake_resume)

    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    store = LocalPausedTurnStore(tmp_path / "data" / "paused")

    history = [{"role": "user", "content": "上一轮"}, {"role": "assistant", "content": "回答"}]

    async def drive() -> list[Any]:
        await _initialize(server, tmp_path, data_dir=str(tmp_path / "data"))
        await store.save(_suspension("m1", "c1", history=history))
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "resume",
                    "params": {
                        "messageId": "m1",
                        "conversationId": "c1",
                        "decision": "adjust",
                        "note": "换个方向",
                        "permissionAxes": {
                            "file_write": "ask",
                            "command": "ask",
                            "team_kickoff": "rules",
                        },
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))
        return await store.list_pending("c1")

    remaining = asyncio.run(drive())
    done = _response(sent, 7)
    assert done["result"]["content"] == "续跑完成"
    assert done["result"]["messageId"] == "m1"
    assert captured["message_id"] == "m1"
    assert captured["decision"] == "adjust"
    assert captured["note"] == "换个方向"
    assert captured["saver"] is not None
    # Partial axes (no host) reach the pipeline; missing host defaults to SESSION.
    from agentcore.core.types import (
        CommandAxis,
        FileWriteAxis,
        HostAxis,
        PermissionAxes,
        TeamKickoffAxis,
    )

    assert captured["autonomy"] == PermissionAxes(
        FileWriteAxis.ASK,
        CommandAxis.ASK,
        TeamKickoffAxis.RULES,
        HostAxis.SESSION,
    )
    # the reloaded history (from the local frame) is threaded into the resume pipeline so
    # window_from_journal can splice it ahead of the folded rounds (Phase 2 ⑤).
    assert captured["history"] == history
    assert captured["x_client_platform"] == "desktop"
    assert remaining == []  # the frame was claimed (one-shot), so nothing is left


def _team_preview_suspension(
    message_id: str,
    conversation_id: str,
) -> Any:
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.suspension import TeamPreviewSuspension

    susp = TeamPreviewSuspension(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id="u1",
        captain_run_id="r1",
        checkpoint_id=f"ck-{message_id}",
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
        {
            "kind": "team_preview_required",
            "payload": {"checkpoint_id": f"ck-{message_id}"},
            "ts": None,
        }
    ]
    return susp


def test_resume_forwards_team_preview_veto_to_pipeline(tmp_path, monkeypatch):
    """本机 resume：excluded_run_ids / write_capability_overrides 贯通到 pipeline。"""
    captured: dict[str, Any] = {}

    async def fake_resume(**kwargs: Any) -> dict[str, Any]:
        captured["excluded_run_ids"] = kwargs.get("excluded_run_ids")
        captured["write_capability_overrides"] = kwargs.get("write_capability_overrides")
        kwargs["sink"].close()
        return {
            "finish_reason": "end_turn",
            "content": "ok",
            "rounds": 1,
            "message_id": kwargs["suspension"].message_id,
        }

    monkeypatch.setattr("agentcore.sidecar.server.resume_chat_pipeline", fake_resume)

    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    store = LocalPausedTurnStore(tmp_path / "data" / "paused")

    async def drive() -> None:
        await _initialize(server, tmp_path, data_dir=str(tmp_path / "data"))
        await store.save(_team_preview_suspension("m-tp", "c1"))
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "resume",
                    "params": {
                        "messageId": "m-tp",
                        "conversationId": "c1",
                        "decision": "continue",
                        "note": "",
                        "excluded_run_ids": ["b"],
                        "write_capability_overrides": [
                            {"run_id": "a", "capability": "text_only"}
                        ],
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    asyncio.run(drive())
    done = _response(sent, 11)
    assert done["result"]["content"] == "ok"
    assert captured["excluded_run_ids"] == ["b"]
    assert captured["write_capability_overrides"] == [
        {"run_id": "a", "capability": "text_only"}
    ]


def test_resume_rejects_illegal_team_preview_veto(tmp_path, monkeypatch):
    """非法否决：resume 在 settlement 前 422 等价（INVALID_PARAMS）并恢复帧。"""

    async def fake_resume(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("pipeline must not run on invalid veto")

    monkeypatch.setattr("agentcore.sidecar.server.resume_chat_pipeline", fake_resume)

    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    store = LocalPausedTurnStore(tmp_path / "data" / "paused")

    async def drive() -> list[Any]:
        await _initialize(server, tmp_path, data_dir=str(tmp_path / "data"))
        await store.save(_team_preview_suspension("m-bad", "c1"))
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 12,
                    "method": "resume",
                    "params": {
                        "messageId": "m-bad",
                        "conversationId": "c1",
                        "decision": "continue",
                        "note": "",
                        "excluded_run_ids": ["nope"],
                    },
                }
            )
        )
        return await store.list_pending("c1")

    remaining = asyncio.run(drive())
    err = _response(sent, 12)
    assert "error" in err
    assert err["error"]["code"] == protocol.INVALID_PARAMS
    assert remaining  # frame restored for retry
    assert remaining[0].message_id == "m-bad"
    scope = server.folder_scope_for("c1")
    assert scope is None or scope.folder_id != "should-not-stick"


def test_resume_veto_rolls_back_stamped_folder(tmp_path, monkeypatch):
    """resume veto 必须回滚刚 stamp 的 folder_scope。"""

    async def fake_resume(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("pipeline must not run on invalid veto")

    monkeypatch.setattr("agentcore.sidecar.server.resume_chat_pipeline", fake_resume)

    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    store = LocalPausedTurnStore(tmp_path / "data" / "paused")

    async def drive() -> None:
        await _initialize(server, tmp_path, data_dir=str(tmp_path / "data"))
        server.stamp_folder_scope(
            "c1",
            folder_id="prior-folder",
            binding_injected=True,
            local_root_id="root-prior",
            local_subpath="sub",
        )
        await store.save(_team_preview_suspension("m-bad", "c1"))
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 13,
                    "method": "resume",
                    "params": {
                        "messageId": "m-bad",
                        "conversationId": "c1",
                        "decision": "continue",
                        "note": "",
                        "excluded_run_ids": ["nope"],
                    },
                }
            )
        )

    asyncio.run(drive())
    err = _response(sent, 13)
    assert "error" in err
    scope = server.folder_scope_for("c1")
    assert scope is not None
    assert scope.folder_id == "prior-folder"
    assert scope.local_root_id == "root-prior"


def test_resume_failure_after_settlement_does_not_restore_frame(tmp_path, monkeypatch):
    """D1: pipeline crash after settlement prewrite must not resurrect the decision card."""

    async def fake_resume(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("simulated resume crash")

    monkeypatch.setattr("agentcore.sidecar.server.resume_chat_pipeline", fake_resume)

    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    data = tmp_path / "data"

    async def drive() -> tuple[list[Any], dict[str, Any]]:
        await _initialize(server, tmp_path, data_dir=str(data))
        assert server._paused_store is not None
        await server._paused_store.save(_suspension("m1", "c1"))
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "resume",
                    "params": {
                        "messageId": "m1",
                        "conversationId": "c1",
                        "decision": "adjust",
                        "note": "retry me",
                        "userMessageId": "u1",
                        "traceId": "a" * 32,
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))
        remaining = await server._paused_store.list_pending("c1")
        return remaining, _response(sent, 9)

    remaining, err = asyncio.run(drive())
    assert remaining == []
    assert err["error"]["code"] == protocol.INTERNAL_ERROR


def test_resume_missing_frame_reports_not_found(tmp_path):
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> None:
        await _initialize(server, tmp_path, data_dir=str(tmp_path / "data"))
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "resume",
                    "params": {"messageId": "nope", "conversationId": "c1"},
                }
            )
        )

    asyncio.run(drive())
    assert _response(sent, 8)["error"]["code"] == protocol.PAUSED_TURN_NOT_FOUND


def test_resolve_resume_user_message_id_mints_uuid_when_missing():
    from agentcore.sidecar.server_pkg.turns import resolve_resume_user_message_id

    umid = resolve_resume_user_message_id("", None)
    UUID(umid)
    assert not umid.startswith("resume-")
    assert resolve_resume_user_message_id("client-id", "frame-id") == "client-id"
    assert resolve_resume_user_message_id("", "frame-id") == "frame-id"
    assert resolve_resume_user_message_id("  ", "frame-id") == "frame-id"


def test_resume_without_user_message_id_mints_uuid_outbox_key(tmp_path, monkeypatch):
    """Cold resume must not mint ``resume-{turn_id}`` as the outbox / finalize key."""

    async def fake_resume(**kwargs: Any) -> dict[str, Any]:
        kwargs["sink"].close()
        return {
            "finish_reason": "end_turn",
            "content": "续跑完成",
            "rounds": 1,
            "message_id": kwargs["suspension"].message_id,
        }

    monkeypatch.setattr("agentcore.sidecar.server.resume_chat_pipeline", fake_resume)

    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    data = tmp_path / "data"
    store = LocalPausedTurnStore(data / "paused")
    turn_id = "11111111-1111-4111-8111-111111111111"

    async def drive() -> None:
        await _initialize(server, tmp_path, data_dir=str(data))
        await store.save(_suspension(turn_id, "c1"))
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "resume",
                    "params": {
                        "messageId": turn_id,
                        "conversationId": "c1",
                        "decision": "continue",
                        "traceId": "a" * 32,
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    asyncio.run(drive())
    done = _response(sent, 7)
    assert done["result"]["content"] == "续跑完成"

    from agentcore.conversation.store.outbox import list_outbox_records

    records = list_outbox_records(data / "outbox")
    assert records
    umid = str(records[0]["user_message_id"])
    UUID(umid)
    assert not umid.startswith("resume-")
    assert umid != f"resume-{turn_id}"
