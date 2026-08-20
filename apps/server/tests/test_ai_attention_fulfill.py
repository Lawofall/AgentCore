"""Account-level「哪些对话停着等你」on GET /v1/fulfill.

Connect seed ``ai_attention_snapshot`` (client replace, empty included) +
incremental ``ai_attention``. Authority is paused_turns by user + this process
registry hot cards. No FCM, no N-conversation recovery scan.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.api.routes.fulfill import (
    _attention_entries_for_seed,
    _seed_registered_session,
)
from agentcore.attention.snapshot import (
    attention_entry,
    entries_from_registry_hot_cards,
    entry_from_paused_row,
    merge_attention_entries,
)
from agentcore.fulfill.hub import FulfillerHub
from agentcore.fulfill.user_signal import (
    FRAME_ATTENTION,
    FRAME_ATTENTION_SNAPSHOT,
    FRAME_QUEUE_ACCOUNT_SNAPSHOT,
    FRAME_TURN_ACTIVITY_SNAPSHOT,
    attention_frame,
    attention_snapshot_frame,
)
from agentcore.runtime.events import EventSink
from agentcore.runtime.interaction import InteractionKind, InteractionRegistry
from agentcore.runtime.turn.queue import TurnQueue


def _row(
    *,
    conversation_id: str = "c1",
    message_id: str = "turn-1",
    checkpoint_id: str = "ck-1",
    kind: str = "ask_user",
    question: str = "用哪套方案？",
) -> SimpleNamespace:
    return SimpleNamespace(
        conversation_id=conversation_id,
        message_id=message_id,
        frame={"kind": kind, "checkpoint_id": checkpoint_id, "question": question},
    )


def test_attention_frames_match_the_wire_contract():
    snap = attention_snapshot_frame(
        [
            {
                "conversation_id": "c1",
                "turn_id": "t1",
                "interaction_id": "i1",
                "kind": "approval",
                "title": "需要授权：终端",
            }
        ]
    )
    assert snap == {
        "type": FRAME_ATTENTION_SNAPSHOT,
        "payload": {
            "entries": [
                {
                    "conversation_id": "c1",
                    "turn_id": "t1",
                    "interaction_id": "i1",
                    "kind": "approval",
                    "title": "需要授权：终端",
                }
            ]
        },
    }
    empty = attention_snapshot_frame([])
    assert empty == {"type": FRAME_ATTENTION_SNAPSHOT, "payload": {"entries": []}}
    inc = attention_frame(
        state="required",
        conversation_id="c1",
        turn_id="t1",
        interaction_id="i1",
        kind="approval",
        title="需要授权：终端",
    )
    assert inc == {
        "type": FRAME_ATTENTION,
        "payload": {
            "state": "required",
            "conversation_id": "c1",
            "turn_id": "t1",
            "interaction_id": "i1",
            "kind": "approval",
            "title": "需要授权：终端",
        },
    }


def test_entry_from_paused_row_maps_blocking_kind_and_skips_progress():
    entry = entry_from_paused_row(_row())
    assert entry == {
        "conversation_id": "c1",
        "turn_id": "turn-1",
        "interaction_id": "ck-1",
        "kind": "ask_user",
        "title": "用哪套方案？",
    }
    assert entry_from_paused_row(_row(kind="client_tool", checkpoint_id="op-1")) is None
    assert attention_entry(conversation_id="", turn_id="t", interaction_id="i", kind="x", title="") is None


def test_merge_paused_then_registry_dedupes_by_interaction_id(monkeypatch):
    monkeypatch.setattr(
        "agentcore.attention.snapshot.entries_from_registry_hot_cards",
        lambda user_id: [
            {
                "conversation_id": "c1",
                "turn_id": "hot-turn",
                "interaction_id": "ck-1",
                "kind": "approval",
                "title": "热卡不应覆盖冷帧",
            },
            {
                "conversation_id": "c2",
                "turn_id": "hot-2",
                "interaction_id": "hot-2",
                "kind": "approval",
                "title": "需要授权：写文件",
            },
        ],
    )
    merged = merge_attention_entries([_row()], user_id="u1")
    assert [e["interaction_id"] for e in merged] == ["ck-1", "hot-2"]
    assert merged[0]["title"] == "用哪套方案？"


async def test_registry_hot_cards_only_for_this_users_live_runs(monkeypatch):
    registry = InteractionRegistry()
    monkeypatch.setattr(
        "agentcore.attention.snapshot.default_interaction_registry",
        lambda: registry,
    )
    mine = MagicMock()
    mine.user_id = "u1"
    mine.conversation_id = "c-mine"
    mine.sink = EventSink()
    mine.sink._message_id = "turn-mine"
    other = MagicMock()
    other.user_id = "u2"
    other.conversation_id = "c-other"
    other.sink = EventSink()
    other.sink._message_id = "turn-other"
    monkeypatch.setattr(
        "agentcore.attention.snapshot.turn_runs.live_runs",
        lambda: [mine, other],
    )
    registry.create(
        "appr-mine",
        "c-mine",
        kind=InteractionKind.APPROVAL,
        payload={"tool_name": "file_write"},
    )
    registry.create(
        "appr-other",
        "c-other",
        kind=InteractionKind.APPROVAL,
        payload={"tool_name": "shell"},
    )
    registry.create(
        "ceo-esc",
        "c-mine",
        kind=InteractionKind.ESCALATION,
        payload={"awaiting": "ceo", "question": "内部仲裁"},
    )
    entries = entries_from_registry_hot_cards("u1")
    assert [e["interaction_id"] for e in entries] == ["appr-mine"]
    assert entries[0]["title"] == "需要授权：file_write"
    assert entries[0]["turn_id"] == "turn-mine"


async def test_connect_seed_empty_attention_still_replace(monkeypatch):
    monkeypatch.setattr("agentcore.api.routes.fulfill.turn_queue", TurnQueue())
    monkeypatch.setattr(
        "agentcore.runtime.events.client_tool_reattach.rehang_pending_client_tools",
        lambda user_id: 0,
    )
    hub = FulfillerHub()
    session = hub.register("u1", "d1", caps=["workspace"], roots=["r1"])
    _seed_registered_session(
        session, hub, running_conversation_ids=[], attention_entries=[]
    )
    assert (await session.get())["type"] == FRAME_QUEUE_ACCOUNT_SNAPSHOT
    assert (await session.get())["type"] == FRAME_TURN_ACTIVITY_SNAPSHOT
    frame = await session.get()
    assert frame == {"type": FRAME_ATTENTION_SNAPSHOT, "payload": {"entries": []}}


async def test_connect_seed_delivers_attention_entries(monkeypatch):
    monkeypatch.setattr("agentcore.api.routes.fulfill.turn_queue", TurnQueue())
    monkeypatch.setattr(
        "agentcore.runtime.events.client_tool_reattach.rehang_pending_client_tools",
        lambda user_id: 0,
    )
    hub = FulfillerHub()
    session = hub.register("u1", "web-1", caps=[], roots=[], platform="web")
    entries = [
        {
            "conversation_id": "c-live",
            "turn_id": "t1",
            "interaction_id": "ck1",
            "kind": "plan_review",
            "title": "AI 计划待你确认",
        }
    ]
    _seed_registered_session(
        session, hub, running_conversation_ids=[], attention_entries=entries
    )
    await session.get()
    await session.get()
    frame = await session.get()
    assert frame["type"] == FRAME_ATTENTION_SNAPSHOT
    assert frame["payload"]["entries"] == entries


async def test_soft_deleted_conversation_not_in_attention_snapshot(monkeypatch):
    """已软删不进 snapshot：播种只采用 list_pending_for_user 留下的活会话。"""
    repo = MagicMock()
    repo.list_pending_for_user = AsyncMock(
        return_value=[_row(conversation_id="c-live", checkpoint_id="ck-live")]
    )
    monkeypatch.setattr(
        "agentcore.api.routes.fulfill.PausedTurnRepository",
        lambda session: repo,
    )
    monkeypatch.setattr(
        "agentcore.attention.snapshot.entries_from_registry_hot_cards",
        lambda user_id: [],
    )
    entries = await _attention_entries_for_seed(MagicMock(), "u-seed")
    assert entries is not None
    snap = attention_snapshot_frame(entries)
    assert [e["conversation_id"] for e in snap["payload"]["entries"]] == ["c-live"]


async def test_list_pending_for_user_sql_excludes_deleted_conversations():
    from sqlalchemy.dialects import postgresql

    from agentcore.db.repositories import PausedTurnRepository

    class _Scalars:
        def all(self):
            return []

    class _Result:
        def scalars(self):
            return _Scalars()

    class _Session:
        def __init__(self) -> None:
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return _Result()

    session = _Session()
    await PausedTurnRepository(session).list_pending_for_user("u1")
    sql = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "JOIN conversations" in sql
    assert "LEFT OUTER JOIN" not in sql
    assert "conversations.deleted_at IS NULL" in sql


async def test_seed_ids_read_paused_turns_not_recovery(monkeypatch):
    rows = [_row(conversation_id="from-paused")]
    repo = MagicMock()
    repo.list_pending_for_user = AsyncMock(return_value=rows)
    monkeypatch.setattr(
        "agentcore.api.routes.fulfill.PausedTurnRepository",
        lambda session: repo,
    )
    monkeypatch.setattr(
        "agentcore.attention.snapshot.entries_from_registry_hot_cards",
        lambda user_id: [],
    )
    ids = await _attention_entries_for_seed(MagicMock(), "u-seed")
    repo.list_pending_for_user.assert_awaited_once_with("u-seed")
    assert ids == [
        {
            "conversation_id": "from-paused",
            "turn_id": "turn-1",
            "interaction_id": "ck-1",
            "kind": "ask_user",
            "title": "用哪套方案？",
        }
    ]


async def test_seed_paused_query_failure_does_not_replace(monkeypatch):
    """查库失败返回 None：该路不发 snapshot replace，不能拿空表灭灯。"""
    repo = MagicMock()
    repo.list_pending_for_user = AsyncMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(
        "agentcore.api.routes.fulfill.PausedTurnRepository",
        lambda session: repo,
    )
    monkeypatch.setattr(
        "agentcore.attention.snapshot.entries_from_registry_hot_cards",
        lambda user_id: [
            {
                "conversation_id": "c-hot",
                "turn_id": "t",
                "interaction_id": "hot",
                "kind": "approval",
                "title": "需要授权：终端",
            }
        ],
    )
    assert await _attention_entries_for_seed(MagicMock(), "u1") is None


async def test_connect_seed_query_failure_does_not_send_empty_attention_replace(
    monkeypatch,
):
    monkeypatch.setattr("agentcore.api.routes.fulfill.turn_queue", TurnQueue())
    monkeypatch.setattr(
        "agentcore.runtime.events.client_tool_reattach.rehang_pending_client_tools",
        lambda user_id: 0,
    )
    hub = FulfillerHub()
    session = hub.register("u1", "d1", caps=["workspace"], roots=["r1"])
    _seed_registered_session(
        session, hub, running_conversation_ids=[], attention_entries=None
    )
    assert (await session.get())["type"] == FRAME_QUEUE_ACCOUNT_SNAPSHOT
    assert (await session.get())["type"] == FRAME_TURN_ACTIVITY_SNAPSHOT
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(session.get(), timeout=0.05)


async def test_required_and_resolved_also_broadcast_fulfill(monkeypatch):
    from agentcore.attention import (
        AttentionKind,
        signal_attention_required,
        signal_attention_resolved,
    )

    frames: list[dict] = []

    def capture(**kwargs) -> int:
        frames.append(kwargs)
        return 1

    # Lazy import inside signal_* — patch the source module before the call.
    monkeypatch.setattr("agentcore.fulfill.user_signal.push_attention", capture)

    async def _noop_publish(user_id: str, event: dict) -> None:
        return None

    monkeypatch.setattr("agentcore.attention.signal._publish", _noop_publish)

    await signal_attention_required(
        user_id="u1",
        conversation_id="c1",
        turn_id="t1",
        interaction_id="i1",
        kind=AttentionKind.APPROVAL,
        title="需要授权：终端",
        push=False,
    )
    await signal_attention_resolved(
        user_id="u1",
        conversation_id="c1",
        turn_id="t1",
        interaction_id="i1",
        kind=AttentionKind.APPROVAL,
    )
    assert [f["state"] for f in frames] == ["required", "resolved"]
    assert frames[0]["kind"] == "approval"
    assert frames[0]["interaction_id"] == "i1"
