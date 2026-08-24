"""Cold GET list slimming: keep-set is derived; list drops bulky events; GET one is full."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentcore.api.routes.conversations import messages as messages_route
from agentcore.core.errors import NotFoundError
from agentcore.runtime.events.types import EventType
from agentcore.runtime.interaction import INTERACTION_KIND_SPECS
from agentcore.runtime.journal.slim import list_retained_event_types, slim_runs_payload

_MID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def test_list_retained_event_types_derived_not_hand_copied():
    retained = list_retained_event_types()
    assert EventType.RUN_PLAN.value in retained
    assert EventType.MESSAGE_END.value in retained
    assert EventType.DELIVERY_STATUS.value in retained
    for spec in INTERACTION_KIND_SPECS.values():
        if spec.journal_surface:
            assert spec.required_event in retained
        if spec.resolved_event:
            assert spec.resolved_event in retained
    assert EventType.RUN_OUTPUT_DELTA.value not in retained
    assert EventType.TOOL_USE_START.value not in retained


def test_slim_runs_payload_drops_bulky_events_and_run_processes():
    process = [{"kind": "reasoning", "text": "think"}]
    error = {"code": "LLM_ERROR", "message": "boom"}
    runs = {
        "events": (
            [
                {"type": "run_plan", "payload": {"execution_id": "e1"}},
            ]
            + [
                {"type": "run_output_delta", "payload": {"text": str(i)}}
                for i in range(1000)
            ]
            + [
                {"type": "tool_use_start", "payload": {"id": "t1"}},
                {"type": "message_end", "payload": {"finish_reason": "stop"}},
            ]
        ),
        "finish_reason": "stop",
        "process": process,
        "run_processes": {"r1": [{"kind": "content", "text": "w"}]},
        "error": error,
    }
    out = slim_runs_payload(runs)
    assert out["events_complete"] is False
    assert out["run_processes"] is None
    assert out["process"] == process
    assert out["error"] == error
    assert out["finish_reason"] == "stop"
    types = {ev["type"] for ev in out["events"]}
    assert types == {"run_plan", "message_end"}
    assert len(out["events"]) == 2


def test_slim_runs_payload_keeps_complete_when_all_retained():
    runs = {
        "events": [
            {"type": "run_plan", "payload": {"execution_id": "e1"}},
            {"type": "approval_required", "payload": {"approval_id": "a1"}},
            {"type": "approval_resolved", "payload": {"approval_id": "a1"}},
            {"type": "message_end", "payload": {"finish_reason": "stop"}},
        ],
        "finish_reason": "stop",
        "process": [{"kind": "team", "execution_id": "e1"}],
        "run_processes": {"r1": [{"kind": "content", "text": "w"}]},
    }
    out = slim_runs_payload(runs)
    assert out["events_complete"] is True
    assert out["run_processes"] == runs["run_processes"]
    assert len(out["events"]) == 4


def _assistant_row(*, usage=None):
    return SimpleNamespace(
        id=_MID,
        conversation_id="c1",
        role="assistant",
        content="hello",
        reasoning_content=None,
        usage=usage or {"status": "complete"},
        created_at=datetime.now(UTC),
        attachments=[],
        agent_mentions=[],
        citations=[],
        evidence_ledger=[],
        followups=[],
        cost=None,
        feedback=None,
        trace_id=None,
    )


def _list_repos(row):
    class ConvRepo:
        async def get_by_id(self, _cid, *, user_id):
            return SimpleNamespace(id="c1", user_id=user_id)

    class MsgRepo:
        async def count_by_conversation(self, _cid):
            return 1

        async def list_latest(self, _cid, limit):
            return [row], False

        async def get_by_id(self, message_id, *, conversation_id):
            if message_id == row.id and conversation_id == "c1":
                return row
            return None

    class JournalRepo:
        async def load_map(self, _ids):
            return {row.id: [{"kind": "x"}]}

    class MemRepo:
        async def list_for_conversation(self, _cid):
            return []

    return ConvRepo(), MsgRepo(), JournalRepo(), MemRepo()


_FAT_RUNS = {
    "events": (
        [{"type": "run_plan", "payload": {"execution_id": "e1"}}]
        + [{"type": "run_output_delta", "payload": {"text": str(i)}} for i in range(50)]
        + [{"type": "message_end", "payload": {"finish_reason": "stop"}}]
    ),
    "finish_reason": "stop",
    "process": [{"kind": "reasoning", "text": "think"}],
    "run_processes": {"r1": [{"kind": "content", "text": "w"}]},
    "error": None,
}


def _patch_fold(monkeypatch):
    monkeypatch.setattr(
        messages_route,
        "runs_from_entries_cached",
        lambda _mid, _entries: dict(_FAT_RUNS),
    )
    monkeypatch.setattr(
        messages_route,
        "overlay_runs_with_segments",
        lambda runs, _segments, usage=None: runs,
    )
    store = SimpleNamespace(
        list_stream_segments_map=AsyncMock(return_value={}),
    )
    monkeypatch.setattr(messages_route, "get_conversation_store", lambda: store)


@pytest.mark.anyio
async def test_list_messages_slims_runs(monkeypatch):
    _patch_fold(monkeypatch)
    row = _assistant_row()
    conv, repo, journal, mem = _list_repos(row)
    page = await messages_route.list_messages(
        conversation_id="c1",
        user=SimpleNamespace(user_id="u1"),
        limit=100,
        before=None,
        after=None,
        around=None,
        conv_repo=conv,
        repo=repo,
        journal_repo=journal,
        mem_update_repo=mem,
    )
    runs = page.data[0].runs
    assert runs is not None
    assert runs.events_complete is False
    assert runs.run_processes is None
    assert runs.process == _FAT_RUNS["process"]
    assert runs.finish_reason == "stop"
    assert {ev["type"] for ev in runs.events} == {"run_plan", "message_end"}


@pytest.mark.anyio
async def test_get_message_returns_full_runs(monkeypatch):
    _patch_fold(monkeypatch)
    row = _assistant_row()
    conv, repo, journal, _mem = _list_repos(row)
    detail = await messages_route.get_message(
        conversation_id="c1",
        message_id=_MID,
        user=SimpleNamespace(user_id="u1"),
        conv_repo=conv,
        repo=repo,
        journal_repo=journal,
    )
    assert detail.runs is not None
    assert detail.runs.events_complete is True
    assert len(detail.runs.events) == len(_FAT_RUNS["events"])
    assert detail.runs.run_processes == _FAT_RUNS["run_processes"]


@pytest.mark.anyio
async def test_get_message_missing_404(monkeypatch):
    _patch_fold(monkeypatch)
    row = _assistant_row()
    conv, _repo, journal, _mem = _list_repos(row)

    class EmptyMsg:
        async def get_by_id(self, message_id, *, conversation_id):
            return None

    with pytest.raises(NotFoundError, match="消息不存在"):
        await messages_route.get_message(
            conversation_id="c1",
            message_id="missing",
            user=SimpleNamespace(user_id="u1"),
            conv_repo=conv,
            repo=EmptyMsg(),
            journal_repo=journal,
        )


@pytest.mark.anyio
async def test_get_message_unowned_conversation_404():
    class ConvRepo:
        async def get_by_id(self, _cid, *, user_id):
            return None

    with pytest.raises(NotFoundError, match="对话不存在"):
        await messages_route.get_message(
            conversation_id="c-other",
            message_id=_MID,
            user=SimpleNamespace(user_id="u1"),
            conv_repo=ConvRepo(),
            repo=SimpleNamespace(),
            journal_repo=SimpleNamespace(),
        )
