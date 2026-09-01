"""同对话再发 P0：delivery 必填分流 + 强制排队 + 按项取消 + stop 不撤队。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from agentcore.api.schemas.messages import AgentMention, SendMessageRequest
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.turn.queue import new_queued_turn, turn_queue
from agentcore.runtime.turn.runs import turn_runs


@pytest.fixture(autouse=True)
def _stub_midflight_persist(monkeypatch):
    """send_message 现在会落用户行；本文件用假 conversation_id，不打库。"""
    monkeypatch.setattr(
        "agentcore.conversation.midflight_persist.persist_midflight_user_message",
        AsyncMock(return_value="00000000-0000-0000-0000-000000000001"),
    )
    monkeypatch.setattr(
        "agentcore.conversation.midflight_persist.delete_midflight_user_message",
        AsyncMock(return_value=True),
    )


def test_send_message_request_requires_delivery():
    with pytest.raises(ValidationError) as exc:
        SendMessageRequest(content="hi")
    assert "delivery" in str(exc.value)


def test_send_message_request_accepts_steer_and_queue():
    assert SendMessageRequest(content="hi", delivery="steer").delivery == "steer"
    assert SendMessageRequest(content="hi", delivery="queue").delivery == "queue"


def test_send_message_request_allows_empty_content_with_attachments():
    att = {
        "name": "pic.png",
        "path": "pic.png",
        "text": "",
        "binary": True,
        "workspace_path": "attachments/pic.png",
    }
    body = SendMessageRequest(content="", delivery="steer", attachments=[att])
    assert body.content == ""
    assert len(body.attachments) == 1
    # Whitespace-only caption also OK when attachments present.
    body2 = SendMessageRequest(content="  \n", delivery="queue", attachments=[att])
    assert body2.content == "  \n"


def test_send_message_request_rejects_empty_content_without_attachments():
    with pytest.raises(ValidationError) as exc:
        SendMessageRequest(content="", delivery="steer")
    assert "消息内容与附件不能同时为空" in str(exc.value)
    with pytest.raises(ValidationError):
        SendMessageRequest(content="   ", delivery="queue")


def test_turn_queued_payload_carries_degraded_from():
    from agentcore.runtime.events import turn_queued
    from agentcore.runtime.events.payloads.run import TurnQueuedPayload

    ev = turn_queued(
        queue_id="q1",
        position=1,
        queue_depth=1,
        conversation_id="c1",
        degraded_from="steer",
    )
    assert ev.payload["degraded_from"] == "steer"
    TurnQueuedPayload.model_validate(ev.payload)


def test_turn_queue_cancelled_payload():
    from agentcore.runtime.events import turn_queue_cancelled
    from agentcore.runtime.events.payloads.run import TurnQueueCancelledPayload

    ev = turn_queue_cancelled(queue_id="q1", conversation_id="c1")
    assert ev.type is EventType.TURN_QUEUE_CANCELLED
    TurnQueueCancelledPayload.model_validate(ev.payload)


def test_turn_queue_started_payload():
    from agentcore.runtime.events import turn_queue_started
    from agentcore.runtime.events.payloads.run import TurnQueueStartedPayload

    ev = turn_queue_started(
        queue_id="q1",
        conversation_id="c1",
        remaining_depth=2,
        content="next",
    )
    assert ev.type is EventType.TURN_QUEUE_STARTED
    assert ev.payload["remaining_depth"] == 2
    assert ev.payload["content"] == "next"
    assert "attachments" not in ev.payload
    assert "agent_mentions" not in ev.payload
    TurnQueueStartedPayload.model_validate(ev.payload)

    empty_lists = turn_queue_started(
        queue_id="q1",
        conversation_id="c1",
        remaining_depth=0,
        content="next",
        attachments=[],
        agent_mentions=[],
    )
    assert "attachments" not in empty_lists.payload
    assert "agent_mentions" not in empty_lists.payload

    att = {
        "name": "note.md",
        "path": "note.md",
        "text": "",
        "truncated": False,
        "kind": "file",
        "conversation_id": None,
        "binary": False,
        "workspace_path": "attachments/note.md",
    }
    full = turn_queue_started(
        queue_id="q1",
        conversation_id="c1",
        remaining_depth=0,
        content="with file",
        attachments=[att],
        agent_mentions=[{"agent_id": "a1", "role": "研究员"}],
    )
    assert full.payload["attachments"] == [att]
    assert full.payload["agent_mentions"] == [{"agent_id": "a1", "role": "研究员"}]
    TurnQueueStartedPayload.model_validate(full.payload)


def test_user_interjection_received_payload():
    from agentcore.runtime.events import user_interjection
    from agentcore.runtime.events.payloads.run import UserInterjectionPayload

    ev = user_interjection(
        interjection_id="inj-1",
        execution_id="exec-1",
        content="fix it",
        status="received",
    )
    assert ev.type is EventType.USER_INTERJECTION
    assert ev.payload["status"] == "received"
    UserInterjectionPayload.model_validate(ev.payload)


def test_user_interjection_injected_status_accepted():
    from agentcore.runtime.events import user_interjection
    from agentcore.runtime.events.payloads.run import UserInterjectionPayload

    ev = user_interjection(
        interjection_id="inj-1",
        execution_id="exec-1",
        content="fix it",
        status="injected",
    )
    UserInterjectionPayload.model_validate(ev.payload)


async def _never() -> None:
    await asyncio.Future()


async def test_cancel_removes_pending_and_emits_on_live_sink():
    cid = "c-cancel-ok"
    turn_queue.clear(cid)
    sink = EventSink()
    blocker = asyncio.create_task(_never())
    turn_runs.register(conversation_id=cid, task=blocker, sink=sink)
    try:
        started = asyncio.get_running_loop().create_future()
        item = new_queued_turn(content="queued", user_id="u", started=started)
        turn_queue.enqueue(cid, item)
        removed = turn_queue.cancel(cid, item.queue_id)
        assert removed is item
        assert turn_queue.depth(cid) == 0
        assert turn_queue.cancel(cid, item.queue_id) is None  # already gone → None (404)

        from agentcore.runtime.events import turn_queue_cancelled

        sink.emit(turn_queue_cancelled(queue_id=item.queue_id, conversation_id=cid))
        types = [e.type for e in sink._history]  # noqa: SLF001
        assert EventType.TURN_QUEUE_CANCELLED in types
    finally:
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        turn_queue.clear(cid)


async def test_cancel_after_pop_returns_none():
    """已开始（drain pop 后）→ cancel 找不到 → 路由层 404。"""
    cid = "c-cancel-started"
    turn_queue.clear(cid)
    item = new_queued_turn(content="x", user_id="u")
    turn_queue.enqueue(cid, item)
    assert turn_queue.pop_next(cid) is item
    assert turn_queue.cancel(cid, item.queue_id) is None
    turn_queue.clear(cid)


async def test_stop_does_not_clear_queued_turns():
    """Stop 取消 in-flight，不得清空排队项。"""
    cid = "c-stop-keeps-queue"
    turn_queue.clear(cid)
    blocker = asyncio.create_task(_never())
    turn_runs.register(conversation_id=cid, task=blocker, sink=EventSink())
    item = new_queued_turn(content="still-queued", user_id="u")
    turn_queue.enqueue(cid, item)
    try:
        assert turn_runs.stop(cid) is True
        with pytest.raises(asyncio.CancelledError):
            await blocker
        assert turn_queue.depth(cid) == 1
        assert turn_queue.pop_next(cid) is item
    finally:
        turn_queue.clear(cid)


async def test_coord_queue_skips_interjection(monkeypatch):
    """协调活跃 + delivery=queue → 强制 FIFO，不走 user_interjection。"""
    from agentcore.api.routes.conversations import messages as messages_mod

    cid = "c-coord-queue"
    turn_queue.clear(cid)
    blocker = asyncio.create_task(_never())
    host_sink = EventSink()
    turn_runs.register(conversation_id=cid, task=blocker, sink=host_sink)

    coord = MagicMock()
    coord.active = True
    coord.execution_id = "exec-1"
    coord.post = MagicMock(return_value=True)
    monkeypatch.setattr(
        "agentcore.runtime.coordination.session.active_coordination_for_conversation",
        lambda _cid: coord,
    )
    monkeypatch.setattr(
        messages_mod,
        "enforce_user_message_rate_limit",
        AsyncMock(),
    )
    monkeypatch.setattr(
        messages_mod,
        "_preflight_owned_chat_turn",
        AsyncMock(
            return_value=SimpleNamespace(credentials=None, supports_tools=True),
        ),
    )
    monkeypatch.setattr(messages_mod, "release_request_db_before_sse", AsyncMock())
    monkeypatch.setattr(
        "agentcore.runtime.interaction.default_interaction_registry",
        lambda: SimpleNamespace(list_pending=lambda _c: []),
    )

    body = SendMessageRequest(content="force queue", delivery="queue")
    user = SimpleNamespace(user_id="u1")
    session = MagicMock()

    try:
        resp = await messages_mod.send_message(
            conversation_id=cid,
            body=body,
            user=user,
            session=session,
            x_client_platform=None,
        )
        coord.post.assert_not_called()
        assert turn_queue.depth(cid) == 1
        # SSE queued response body streams turn_queued
        assert resp.media_type == "text/event-stream"
        gen = resp.body_iterator
        first = await gen.__anext__()
        assert "turn_queued" in first
        assert "degraded_from" not in first
        await gen.aclose()
    finally:
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        turn_queue.clear(cid)


async def test_classic_steer_parks_on_live_turn(monkeypatch):
    """经典 in-flight + delivery=steer（accepting）→ 不入 turn_queue，发 user_interjection(received)。"""
    from agentcore.api.routes.conversations import messages as messages_mod
    from agentcore.runtime.turn import steer as turn_steer_mod

    cid = "c-classic-steer-ok"
    turn_queue.clear(cid)
    turn_steer_mod._reset_for_tests()
    blocker = asyncio.create_task(_never())
    host_sink = EventSink()
    turn_runs.register(conversation_id=cid, task=blocker, sink=host_sink)
    turn_steer_mod.begin_accepting(cid, execution_id="exec-classic")

    monkeypatch.setattr(
        "agentcore.runtime.coordination.session.active_coordination_for_conversation",
        lambda _cid: None,
    )
    monkeypatch.setattr(messages_mod, "enforce_user_message_rate_limit", AsyncMock())
    monkeypatch.setattr(
        messages_mod,
        "_preflight_owned_chat_turn",
        AsyncMock(return_value=SimpleNamespace(credentials=None, supports_tools=True)),
    )
    monkeypatch.setattr(messages_mod, "release_request_db_before_sse", AsyncMock())
    monkeypatch.setattr(
        "agentcore.runtime.interaction.default_interaction_registry",
        lambda: SimpleNamespace(list_pending=lambda _c: []),
    )

    body = SendMessageRequest(content="wanted steer", delivery="steer")
    try:
        resp = await messages_mod.send_message(
            conversation_id=cid,
            body=body,
            user=SimpleNamespace(user_id="u1"),
            session=MagicMock(),
            x_client_platform=None,
        )
        assert turn_queue.depth(cid) == 0
        assert turn_steer_mod.peek_count(cid) == 1
        assert any(e.type is EventType.USER_INTERJECTION for e in host_sink._history)  # noqa: SLF001
        gen = resp.body_iterator
        first = await gen.__anext__()
        assert "user_interjection" in first
        assert "wanted steer" in first
        assert "received" in first
        await gen.aclose()
    finally:
        turn_steer_mod.end_accepting(cid)
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        turn_queue.clear(cid)
        turn_steer_mod._reset_for_tests()


async def test_classic_steer_degrades_to_queue_without_accepting(monkeypatch):
    """经典 in-flight + delivery=steer 但无 accepting 窗口 → 回落 queue + degraded_from=steer。"""
    from agentcore.api.routes.conversations import messages as messages_mod
    from agentcore.runtime.turn import steer as turn_steer_mod

    cid = "c-classic-steer-fallback"
    turn_queue.clear(cid)
    turn_steer_mod._reset_for_tests()
    blocker = asyncio.create_task(_never())
    turn_runs.register(conversation_id=cid, task=blocker, sink=EventSink())

    monkeypatch.setattr(
        "agentcore.runtime.coordination.session.active_coordination_for_conversation",
        lambda _cid: None,
    )
    monkeypatch.setattr(messages_mod, "enforce_user_message_rate_limit", AsyncMock())
    monkeypatch.setattr(
        messages_mod,
        "_preflight_owned_chat_turn",
        AsyncMock(return_value=SimpleNamespace(credentials=None, supports_tools=True)),
    )
    monkeypatch.setattr(messages_mod, "release_request_db_before_sse", AsyncMock())
    monkeypatch.setattr(
        "agentcore.runtime.interaction.default_interaction_registry",
        lambda: SimpleNamespace(list_pending=lambda _c: []),
    )

    body = SendMessageRequest(content="wanted steer", delivery="steer")
    try:
        resp = await messages_mod.send_message(
            conversation_id=cid,
            body=body,
            user=SimpleNamespace(user_id="u1"),
            session=MagicMock(),
            x_client_platform=None,
        )
        assert turn_queue.depth(cid) == 1
        gen = resp.body_iterator
        first = await gen.__anext__()
        assert "turn_queued" in first
        assert '"degraded_from": "steer"' in first or '"degraded_from":"steer"' in first
        await gen.aclose()
    finally:
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        turn_queue.clear(cid)
        turn_steer_mod._reset_for_tests()


async def test_cancel_queued_turn_route_404_and_success(monkeypatch):
    from agentcore.api.routes.conversations import messages as messages_mod
    from agentcore.core.errors import NotFoundError

    cid = "c-cancel-route"
    turn_queue.clear(cid)
    sink = EventSink()
    blocker = asyncio.create_task(_never())
    turn_runs.register(conversation_id=cid, task=blocker, sink=sink)

    monkeypatch.setattr(
        messages_mod,
        "_require_owned_conversation",
        AsyncMock(return_value=None),
    )
    started = asyncio.get_running_loop().create_future()
    item = new_queued_turn(content="x", user_id="u", started=started)
    turn_queue.enqueue(cid, item)

    try:
        with pytest.raises(NotFoundError):
            await messages_mod.cancel_queued_turn(
                conversation_id=cid,
                queue_id="missing",
                user=SimpleNamespace(user_id="u1"),
                conv_repo=MagicMock(),
            )

        resp = await messages_mod.cancel_queued_turn(
            conversation_id=cid,
            queue_id=item.queue_id,
            user=SimpleNamespace(user_id="u1"),
            conv_repo=MagicMock(),
        )
        assert resp.status == "ok"
        assert turn_queue.depth(cid) == 0
        assert started.cancelled()
        assert any(e.type is EventType.TURN_QUEUE_CANCELLED for e in sink._history)  # noqa: SLF001
    finally:
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        turn_queue.clear(cid)


async def test_list_queued_turns_route_empty_interjection_and_isolation(monkeypatch):
    """GET queued-turns: empty / interjection_id / cross-conversation isolation."""
    from agentcore.api.routes.conversations import messages as messages_mod

    cid_a = "c-list-queue-a"
    cid_b = "c-list-queue-b"
    turn_queue.clear(cid_a)
    turn_queue.clear(cid_b)

    monkeypatch.setattr(
        messages_mod,
        "_require_owned_conversation",
        AsyncMock(return_value=None),
    )

    empty = await messages_mod.list_queued_turns(
        conversation_id=cid_a,
        user=SimpleNamespace(user_id="u1"),
        conv_repo=MagicMock(),
    )
    assert empty.items == []

    plain = new_queued_turn(content="普通排队", user_id="u")
    from_inj = new_queued_turn(
        content="插话升队",
        user_id="u",
        interjection_id="inj-list-1",
        attachments=[{"name": "note.md", "path": "note.md", "text": "hi"}],
        agent_mentions=[{"agent_id": "agent_research", "role": "研究员"}],
    )
    other = new_queued_turn(content="另一会话", user_id="u", interjection_id="inj-other")
    turn_queue.enqueue(cid_a, plain)
    turn_queue.enqueue(cid_a, from_inj)
    turn_queue.enqueue(cid_b, other)

    try:
        resp_a = await messages_mod.list_queued_turns(
            conversation_id=cid_a,
            user=SimpleNamespace(user_id="u1"),
            conv_repo=MagicMock(),
        )
        assert len(resp_a.items) == 2
        assert resp_a.items[0].queue_id == plain.queue_id
        assert resp_a.items[0].content == "普通排队"
        assert resp_a.items[0].position == 1
        assert resp_a.items[0].interjection_id is None
        assert resp_a.items[1].queue_id == from_inj.queue_id
        assert resp_a.items[1].content == "插话升队"
        assert resp_a.items[1].position == 2
        assert resp_a.items[1].interjection_id == "inj-list-1"
        assert resp_a.items[1].attachments[0].name == "note.md"
        assert resp_a.items[1].agent_mentions == [
            AgentMention(agent_id="agent_research", role="研究员")
        ]
        assert resp_a.items[0].attachments == []
        assert resp_a.items[0].agent_mentions == []

        resp_b = await messages_mod.list_queued_turns(
            conversation_id=cid_b,
            user=SimpleNamespace(user_id="u1"),
            conv_repo=MagicMock(),
        )
        assert len(resp_b.items) == 1
        assert resp_b.items[0].queue_id == other.queue_id
        assert resp_b.items[0].content == "另一会话"
        assert resp_b.items[0].interjection_id == "inj-other"
        assert resp_b.items[0].position == 1
    finally:
        turn_queue.clear(cid_a)
        turn_queue.clear(cid_b)


async def test_coord_steer_posts_interjection(monkeypatch):
    """协调活跃 + delivery=steer → user_interjection，不入 FIFO。"""
    from agentcore.api.routes.conversations import messages as messages_mod

    cid = "c-coord-steer"
    turn_queue.clear(cid)
    blocker = asyncio.create_task(_never())
    host_sink = EventSink()
    turn_runs.register(conversation_id=cid, task=blocker, sink=host_sink)

    coord = MagicMock()
    coord.active = True
    coord.execution_id = "exec-2"
    coord.post = MagicMock(return_value=True)
    coord.stash_interjection = MagicMock()
    monkeypatch.setattr(
        "agentcore.runtime.coordination.session.active_coordination_for_conversation",
        lambda _cid: coord,
    )
    monkeypatch.setattr(messages_mod, "enforce_user_message_rate_limit", AsyncMock())
    monkeypatch.setattr(
        messages_mod,
        "_preflight_owned_chat_turn",
        AsyncMock(return_value=SimpleNamespace(credentials=None, supports_tools=True)),
    )
    monkeypatch.setattr(messages_mod, "release_request_db_before_sse", AsyncMock())
    monkeypatch.setattr(
        messages_mod,
        "_persist_delivered_interjection_attachments",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "agentcore.runtime.interaction.default_interaction_registry",
        lambda: SimpleNamespace(list_pending=lambda _c: []),
    )

    body = SendMessageRequest(content="insert please", delivery="steer")
    try:
        resp = await messages_mod.send_message(
            conversation_id=cid,
            body=body,
            user=SimpleNamespace(user_id="u1"),
            session=MagicMock(),
            x_client_platform=None,
        )
        coord.post.assert_called_once()
        assert turn_queue.depth(cid) == 0
        assert any(e.type is EventType.USER_INTERJECTION for e in host_sink._history)  # noqa: SLF001
        assert resp.media_type == "text/event-stream"
    finally:
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        turn_queue.clear(cid)
