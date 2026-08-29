"""CEO rate-limit continue latch: decide, outcome, harvest skip, lock frame."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agentcore.core.error_codes import ErrorCode
from agentcore.runtime.engine.directive import Return
from agentcore.runtime.engine.governance import decide_llm_failure
from agentcore.runtime.events import FinishReason
from agentcore.runtime.turn.ceo_continue import (
    CEO_CONTINUE_CLAIMED_KEY,
    CEO_CONTINUE_KIND,
    ceo_continue_frame,
    is_ceo_continue_frame,
    is_ceo_rate_limit_pause,
    is_claimed_ceo_continue_frame,
    should_pause_ceo_rate_limit,
)
from agentcore.runtime.turn.outcome import coerce_produced_outcome, resolve_turn_outcome


def test_should_pause_captain_rate_limit_on_cloud(monkeypatch):
    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.core.is_sidecar_process",
        lambda: False,
    )
    assert should_pause_ceo_rate_limit(
        role="captain", error_code=ErrorCode.LLM_RATE_LIMIT
    )
    assert not should_pause_ceo_rate_limit(
        role="worker", error_code=ErrorCode.LLM_RATE_LIMIT
    )
    assert not should_pause_ceo_rate_limit(role="captain", error_code=ErrorCode.LLM_ERROR)


def test_should_not_pause_on_sidecar(monkeypatch):
    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.core.is_sidecar_process",
        lambda: True,
    )
    assert not should_pause_ceo_rate_limit(
        role="captain", error_code=ErrorCode.LLM_RATE_LIMIT
    )


def test_decide_llm_failure_pauses_captain_rate_limit(monkeypatch):
    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.core.is_sidecar_process",
        lambda: False,
    )
    directive = decide_llm_failure(
        final_content="已落盘",
        error_code=ErrorCode.LLM_RATE_LIMIT,
        role="captain",
    )
    assert isinstance(directive, Return)
    assert directive.finish_reason is FinishReason.PAUSED


def test_decide_llm_failure_sidecar_stays_degraded(monkeypatch):
    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.core.is_sidecar_process",
        lambda: True,
    )
    directive = decide_llm_failure(
        final_content="已落盘",
        error_code=ErrorCode.LLM_RATE_LIMIT,
        role="captain",
    )
    assert isinstance(directive, Return)
    assert directive.finish_reason is FinishReason.DEGRADED


def test_decide_llm_failure_other_errors_unchanged():
    empty = decide_llm_failure(final_content="", error_code=ErrorCode.LLM_ERROR, role="captain")
    assert isinstance(empty, Return)
    assert empty.finish_reason is FinishReason.ERROR
    degraded = decide_llm_failure(
        final_content="x", error_code=ErrorCode.LLM_ERROR, role="captain"
    )
    assert degraded.finish_reason is FinishReason.DEGRADED


def test_is_ceo_rate_limit_pause_requires_last_error():
    sink = SimpleNamespace(last_turn_error=lambda: {"code": ErrorCode.LLM_RATE_LIMIT})
    assert is_ceo_rate_limit_pause(sink=sink, finish=FinishReason.PAUSED)
    gate = SimpleNamespace(last_turn_error=lambda: None)
    assert not is_ceo_rate_limit_pause(sink=gate, finish=FinishReason.PAUSED)
    other = SimpleNamespace(last_turn_error=lambda: {"code": ErrorCode.LLM_ERROR})
    assert not is_ceo_rate_limit_pause(sink=other, finish=FinishReason.PAUSED)


def test_ceo_continue_frame_is_not_a_card():
    frame = ceo_continue_frame(
        message_id="m1", conversation_id="c1", user_id="u1"
    )
    assert is_ceo_continue_frame(frame)
    assert frame["kind"] == CEO_CONTINUE_KIND
    assert "checkpoint_id" not in frame
    assert not is_claimed_ceo_continue_frame(frame)
    assert is_claimed_ceo_continue_frame({**frame, CEO_CONTINUE_CLAIMED_KEY: True})
    assert not is_ceo_continue_frame({"kind": "ask_user"})
    assert not is_ceo_continue_frame(None)


def test_explicit_paused_is_produced():
    assert coerce_produced_outcome("paused") == "paused"
    assert resolve_turn_outcome(explicit="paused", has_error=True) == "paused"
    assert resolve_turn_outcome(finish_reason=FinishReason.PAUSED) is None


def test_mark_host_turn_paused_sets_live_session(monkeypatch):
    from agentcore.runtime.coordination.session import CoordinationSession
    from agentcore.runtime.turn.ceo_continue import mark_host_turn_paused

    session = CoordinationSession(execution_id="e1", total_workers=0)
    monkeypatch.setattr(
        "agentcore.runtime.coordination.session.active_coordination",
        lambda execution_id=None: session,
    )
    mark_host_turn_paused()
    assert session.host_turn_paused is True


@pytest.mark.anyio
async def test_harvest_detached_skips_closing_when_host_paused():
    from agentcore.runtime.coordination.harvest import settle_detached_execution
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        active_coordination,
        set_active_coordination,
    )

    session = CoordinationSession(
        execution_id="e-pause",
        total_workers=1,
        conversation_id="c-pause",
    )
    session.host_turn_paused = True
    set_active_coordination(session)
    await settle_detached_execution(session)
    assert active_coordination("e-pause") is None


@pytest.mark.anyio
async def test_list_and_load_paused_skip_ceo_continue(monkeypatch):
    from agentcore.runtime.suspension import persistence as persist_mod
    from agentcore.runtime.suspension.persistence import (
        list_paused_turns,
        load_paused_turn,
        paused_turn_exists,
    )

    lock_row = SimpleNamespace(
        frame={"kind": CEO_CONTINUE_KIND, "message_id": "m1"},
        conversation_id="c1",
        message_id="m1",
    )

    class _Repo:
        def __init__(self, _db: object) -> None:
            pass

        async def list_pending(self, _cid: str) -> list[SimpleNamespace]:
            return [lock_row]

        async def get(self, _mid: str) -> SimpleNamespace:
            return lock_row

    class _CM:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_a: object) -> bool:
            return False

    monkeypatch.setattr(persist_mod, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(persist_mod, "PausedTurnRepository", _Repo)
    assert await list_paused_turns("c1") == []
    assert await load_paused_turn("m1", conversation_id="c1") is None
    assert await paused_turn_exists("m1", conversation_id="c1") is False


@pytest.mark.anyio
async def test_finalize_cloud_ceo_pause_writes_metrics_and_lock(monkeypatch):
    from agentcore.conversation.store import CloudStore
    from agentcore.conversation.store import cloud as cloud_mod
    from agentcore.conversation.store.merge import MESSAGE_STATUS_RUNNING

    upserted: dict[str, Any] = {}
    journaled: list[Any] = []
    metrics: dict[str, Any] = {}
    locks: list[dict[str, Any]] = []

    class MsgRepo:
        def __init__(self, _s: object) -> None:
            pass

        async def upsert_assistant(self, **kw: Any) -> SimpleNamespace:
            upserted.update(kw)
            return SimpleNamespace(id=kw["message_id"])

    class MetricsRepo:
        def __init__(self, _s: object) -> None:
            pass

        async def record(self, **kw: Any) -> None:
            metrics.update(kw)

    class CM:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_a: object) -> bool:
            return False

    async def _persist_journal(_session: object, **kw: Any) -> None:
        journaled.append(kw)

    async def _save_lock(**kw: Any) -> None:
        locks.append(kw)

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", MsgRepo)
    monkeypatch.setattr(cloud_mod, "TurnMetricsRepository", MetricsRepo)
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", _persist_journal)
    monkeypatch.setattr(CloudStore, "clear_stream_segments", _async_noop)
    monkeypatch.setattr(
        "agentcore.runtime.turn.ceo_continue.save_ceo_continue_lock",
        _save_lock,
    )

    await CloudStore().finalize(
        mode="cloud",
        result={
            "message_id": "m-pause",
            "content": "已落盘",
            "outcome": "paused",
            "error": "上游限流",
            "error_code": ErrorCode.LLM_RATE_LIMIT,
            "finish_reason": FinishReason.PAUSED,
            "rounds": 2,
            "journal_entries": [
                {"kind": "turn_paused", "payload": {"suspension_kind": "ceo_continue"}}
            ],
        },
        conversation_id="c1",
        user_id="u1",
        folder_id=None,
        backend=SimpleNamespace(location="cloud"),
        sink=SimpleNamespace(emit=lambda *_a, **_k: None),
        user_message="hi",
        llm_credentials=None,
        trace_id="a" * 32,
        turn_id="turn-pause",
        duration_ms=10,
    )

    meta = upserted["metadata"]
    assert meta["status"] == MESSAGE_STATUS_RUNNING
    assert meta["paused"] is True
    assert meta["outcome"] == "paused"
    assert meta["error_code"] == ErrorCode.LLM_RATE_LIMIT
    assert journaled and journaled[0]["entries"][0]["kind"] == "turn_paused"
    assert metrics["status"] == "paused"
    assert metrics["finish_reason"] == FinishReason.PAUSED.value
    assert metrics["mode"] == "cloud"
    assert locks == [
        {
            "message_id": "m-pause",
            "conversation_id": "c1",
            "user_id": "u1",
            "trace_id": "a" * 32,
        }
    ]


@pytest.mark.anyio
async def test_finalize_cloud_gate_pause_does_not_save_continue_lock(monkeypatch):
    from agentcore.conversation.store import CloudStore
    from agentcore.conversation.store import cloud as cloud_mod

    locks: list[dict[str, Any]] = []

    class MsgRepo:
        def __init__(self, _s: object) -> None:
            pass

        async def upsert_assistant(self, **_kw: Any) -> SimpleNamespace:
            return SimpleNamespace(id="m-gate")

    class CM:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_a: object) -> bool:
            return False

    async def _save_lock(**kw: Any) -> None:
        locks.append(kw)

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", MsgRepo)
    monkeypatch.setattr(CloudStore, "clear_stream_segments", _async_noop)
    monkeypatch.setattr(
        "agentcore.runtime.turn.ceo_continue.save_ceo_continue_lock",
        _save_lock,
    )

    await CloudStore().finalize(
        mode="cloud",
        result={
            "message_id": "m-gate",
            "content": "请选择",
            "finish_reason": FinishReason.PAUSED,
        },
        conversation_id="c1",
        user_id="u1",
        folder_id=None,
        backend=SimpleNamespace(location="cloud"),
        sink=SimpleNamespace(emit=lambda *_a, **_k: None),
        user_message="hi",
        llm_credentials=None,
        trace_id="b" * 32,
        turn_id="turn-gate",
        duration_ms=10,
    )
    assert locks == []


@pytest.mark.anyio
async def test_peek_skips_claimed_continue_lock(monkeypatch):
    from agentcore.runtime.turn import ceo_continue as latch

    claimed_row = SimpleNamespace(
        frame={
            "kind": CEO_CONTINUE_KIND,
            "message_id": "m1",
            CEO_CONTINUE_CLAIMED_KEY: True,
        },
        conversation_id="c1",
    )

    class _Repo:
        def __init__(self, _db: object) -> None:
            pass

        async def get(self, _mid: str) -> SimpleNamespace:
            return claimed_row

    monkeypatch.setattr(latch, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(latch, "PausedTurnRepository", _Repo)
    assert await latch.peek_ceo_continue_lock("m1", conversation_id="c1") is None


@pytest.mark.anyio
async def test_claim_paused_without_lock_is_once(monkeypatch):
    """无锁但 ``usage.outcome=paused`` 也必须走原子 claim，两条并发只一人赢。"""
    from agentcore.runtime.turn import ceo_continue as latch

    store: dict[str, dict[str, Any]] = {}
    gate = asyncio.Event()
    serial = asyncio.Lock()

    class _Repo:
        def __init__(self, _db: object) -> None:
            pass

        async def claim_ceo_continue_lock(
            self,
            message_id: str,
            *,
            conversation_id: str,
            user_id: str,
            frame: dict[str, Any],
        ) -> SimpleNamespace | None:
            await gate.wait()
            async with serial:
                row = store.get(message_id)
                if row and row.get(CEO_CONTINUE_CLAIMED_KEY) is True:
                    return None
                if row is None or not row.get(CEO_CONTINUE_CLAIMED_KEY):
                    claimed = {**frame, CEO_CONTINUE_CLAIMED_KEY: True}
                    store[message_id] = claimed
                    return SimpleNamespace(
                        frame=claimed, conversation_id=conversation_id, user_id=user_id
                    )
                return None

    monkeypatch.setattr(latch, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(latch, "PausedTurnRepository", _Repo)

    async def _claim() -> dict[str, Any] | None:
        return await latch.claim_ceo_continue_lock(
            "m-paused", conversation_id="c1", user_id="u1"
        )

    t1 = asyncio.create_task(_claim())
    t2 = asyncio.create_task(_claim())
    await asyncio.sleep(0)
    gate.set()
    first, second = await asyncio.gather(t1, t2)
    winners = [row for row in (first, second) if row is not None]
    assert len(winners) == 1
    assert winners[0][CEO_CONTINUE_CLAIMED_KEY] is True


@pytest.mark.anyio
async def test_continue_route_paused_without_lock_still_requires_claim(monkeypatch):
    """无锁 + paused 不得跳过 claim：claim 落败必须 404，不得开跑。"""
    from agentcore.api.routes.conversations import turns as turns_mod

    started: list[str] = []

    async def _continue_chat(**kw: Any) -> None:
        started.append(str(kw.get("message_id") or ""))

    _stub_continue_route(monkeypatch, turns_mod)
    monkeypatch.setattr(
        "agentcore.runtime.turn.ceo_continue.claim_ceo_continue_lock",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(turns_mod, "continue_chat", _continue_chat)

    with pytest.raises(HTTPException) as ei:
        await turns_mod.continue_message(
            conversation_id="c1",
            message_id="m-paused",
            user=SimpleNamespace(user_id="u1"),
            session=None,
        )
    assert ei.value.status_code == 404
    assert ei.value.detail == {"code": "continue_not_available"}
    assert started == []


@pytest.mark.anyio
async def test_two_concurrent_continues_only_one_proceeds(monkeypatch):
    """两条并发 ``POST …/continue``（含无锁但 paused）只放行一次。"""
    from agentcore.api.routes.conversations import turns as turns_mod

    started: list[str] = []
    gate = asyncio.Event()
    hold = asyncio.Event()
    serial = asyncio.Lock()
    taken = False

    async def _claim(
        message_id: str, *, conversation_id: str, user_id: str
    ) -> dict[str, Any] | None:
        await gate.wait()
        async with serial:
            nonlocal taken
            if taken:
                return None
            taken = True
            return {
                "kind": CEO_CONTINUE_KIND,
                "message_id": message_id,
                "conversation_id": conversation_id,
                "user_id": user_id,
                CEO_CONTINUE_CLAIMED_KEY: True,
            }

    async def _continue_chat(**kw: Any) -> None:
        started.append(str(kw.get("message_id") or ""))
        await hold.wait()

    _stub_continue_route(monkeypatch, turns_mod)
    monkeypatch.setattr(
        "agentcore.runtime.turn.ceo_continue.claim_ceo_continue_lock",
        _claim,
    )
    monkeypatch.setattr(turns_mod, "continue_chat", _continue_chat)

    async def _go() -> object:
        return await turns_mod.continue_message(
            conversation_id="c-race",
            message_id="m-race",
            user=SimpleNamespace(user_id="u1"),
            session=None,
        )

    t1 = asyncio.create_task(_go())
    t2 = asyncio.create_task(_go())
    await asyncio.sleep(0)
    gate.set()
    try:
        results = await asyncio.gather(t1, t2, return_exceptions=True)
        assert started == ["m-race"]
        unexpected = [
            r
            for r in results
            if isinstance(r, BaseException) and not isinstance(r, HTTPException)
        ]
        assert unexpected == []
        losses = [r for r in results if isinstance(r, HTTPException)]
        for err in losses:
            assert err.status_code == 404
    finally:
        hold.set()
        await asyncio.sleep(0)


def _stub_continue_route(monkeypatch: pytest.MonkeyPatch, turns_mod: Any) -> None:
    monkeypatch.setattr(turns_mod, "enforce_user_message_rate_limit", AsyncMock())
    monkeypatch.setattr(
        turns_mod,
        "_preflight_owned_chat_turn",
        AsyncMock(
            return_value=SimpleNamespace(credentials=None, supports_tools=True, warnings=[])
        ),
    )
    monkeypatch.setattr(turns_mod, "release_request_db_before_sse", AsyncMock())
    monkeypatch.setattr(turns_mod, "emit_preflight_warnings", lambda *_a, **_k: None)


class _CM:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_a: object) -> bool:
        return False


async def _async_noop(*_a: object, **_k: object) -> None:
    return None


@pytest.mark.anyio
async def test_continue_ceo_rebuilds_worker_base_not_chat_prompt(monkeypatch):
    """CEO continue must not pass ``turn_started.system_prompt`` into ``wire_crash_turn``."""
    from unittest.mock import AsyncMock

    import agentcore.runtime.pipeline as pipeline_pkg
    from agentcore.runtime.events import EventSink, FinishReason
    from agentcore.runtime.pipeline import continue_ceo as continue_mod
    from agentcore.runtime.pipeline.continue_ceo import continue_ceo_pipeline
    from agentcore.runtime.resolve.prompt import (
        assemble_system_prompt,
        compose_ceo_chat_prompt,
    )
    from agentcore.runtime.resolve.prompt import rebuild as rebuild_mod
    from agentcore.runtime.runs.types import RunPhase, RunState

    ceo_chat_prompt = compose_ceo_chat_prompt(
        assemble_system_prompt(),
        ceo_tool_names={"consult", "delegate"},
    )
    assert "<how_you_work>" in ceo_chat_prompt

    captured: dict[str, str] = {}
    wired = SimpleNamespace(
        bound_execution_id="e1",
        execution_id_token=None,
        chat_tools=[],
        base_tool_context=SimpleNamespace(),
        approval_gate=None,
        delegate_tool=None,
        debate_tool=None,
        vision_cost_sink=[],
    )

    async def _fake_wire(**kwargs):
        captured["base_system_prompt"] = kwargs["base_system_prompt"]
        return wired

    hydrated = SimpleNamespace(
        pre_pause_reasoning="",
        citations=[],
        evidence_ledger=None,
        controller_seed=None,
        pre_pause_content="",
    )

    backend = SimpleNamespace(location="server")
    monkeypatch.setattr(
        rebuild_mod, "collect_outlet_inventory", AsyncMock(return_value=())
    )
    monkeypatch.setattr(
        rebuild_mod, "assemble_turn_rules", AsyncMock(return_value="")
    )
    monkeypatch.setattr(
        rebuild_mod, "resolve_exec_languages", AsyncMock(return_value=())
    )
    monkeypatch.setattr(
        rebuild_mod, "detect_workspace_git", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        rebuild_mod, "build_workspace_context", lambda *_a, **_k: ""
    )
    monkeypatch.setattr(
        "agentcore.memory.rules_injection.load_on_demand_user_rules",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "agentcore.memory.injection.load_memory_topics",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        pipeline_pkg, "build_turn_router", AsyncMock(return_value=SimpleNamespace(close=AsyncMock()))
    )
    monkeypatch.setattr(continue_mod, "wire_crash_turn", _fake_wire)
    monkeypatch.setattr(continue_mod, "wire_roster_for_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(continue_mod, "_seed_continue_display", lambda **_kw: hydrated)
    monkeypatch.setattr(continue_mod, "resumed_captain_window", lambda *_a, **_k: [])
    monkeypatch.setattr(continue_mod, "arm_content_reset_reinjection", lambda *_a, **_k: None)
    monkeypatch.setattr(
        continue_mod,
        "finish_resume_turn",
        AsyncMock(return_value={"content": "ok", "finish_reason": FinishReason.END_TURN}),
    )

    async def _run_captain(_spec, _messages):
        return RunState(phase=RunPhase.COMPLETED, content="ok")

    monkeypatch.setattr(continue_mod, "build_captain_resumer", lambda **_kw: _run_captain)

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return None

    class _FakeJournalRepo:
        def __init__(self, _session):
            pass

        async def max_seq(self, _mid):
            return None

    monkeypatch.setattr("agentcore.db.base.async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(
        "agentcore.db.repositories.TurnJournalRepository", _FakeJournalRepo
    )
    monkeypatch.setattr(
        "agentcore.runtime.interaction_orphan.orphan_registry_pending",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "agentcore.conversation.stage_card_resolve.maybe_orphan_stage_cards_at_turn_end",
        AsyncMock(),
    )

    journal = [
        {
            "kind": "turn_started",
            "payload": {
                "user_message": "继续",
                "system_prompt": ceo_chat_prompt,
            },
            "ts": "t0",
            "seq": 0,
        },
    ]

    await continue_ceo_pipeline(
        conversation_id="c-continue",
        message_id="m-continue",
        user_id="u1",
        user_message="继续",
        journal_entries=journal,
        captain_run_id="cap-1",
        sink=EventSink(),
        backend=backend,
        folder_id=None,
    )

    prompt = captured["base_system_prompt"]
    assert prompt != ceo_chat_prompt
    assert "<how_you_work>" not in prompt
    assert "<按需目录>" in prompt
    assert "- work_discipline" in prompt
