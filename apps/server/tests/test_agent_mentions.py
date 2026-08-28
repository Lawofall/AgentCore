"""Conversation-page ``agent_mentions`` soft prompt (非强制派单)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentcore.api.schemas.messages import (
    AgentMention,
    MessageAttachment,
    QueuedTurnItem,
    RecordTurnRequest,
    SendMessageRequest,
)
from agentcore.runtime.pipeline import (
    _build_agent_mention_context,
    merge_attachment_and_mention_context,
)
from agentcore.runtime.turn.queue import new_queued_turn
from agentcore.runtime.turn.steer import _reset_for_tests as reset_steer
from agentcore.runtime.turn.steer import begin_accepting, try_enqueue


def test_send_message_request_agent_mentions_default_empty():
    body = SendMessageRequest(content="hi", delivery="steer")
    assert body.agent_mentions == []


def test_send_message_request_agent_mentions_max_length():
    ok = [
        AgentMention(agent_id=f"a{i}", role=f"role-{i}") for i in range(10)
    ]
    body = SendMessageRequest(content="hi", delivery="queue", agent_mentions=ok)
    assert len(body.agent_mentions) == 10

    too_many = ok + [AgentMention(agent_id="x", role="extra")]
    with pytest.raises(ValidationError):
        SendMessageRequest(content="hi", delivery="queue", agent_mentions=too_many)


def test_agent_mention_field_bounds():
    with pytest.raises(ValidationError):
        AgentMention(agent_id="", role="r")
    with pytest.raises(ValidationError):
        AgentMention(agent_id="a" * 129, role="r")
    with pytest.raises(ValidationError):
        AgentMention(agent_id="a", role="")
    with pytest.raises(ValidationError):
        AgentMention(agent_id="a", role="r" * 201)


def test_build_agent_mention_context_empty():
    assert _build_agent_mention_context(None) is None
    assert _build_agent_mention_context([]) is None
    assert _build_agent_mention_context([{"agent_id": "", "role": "x"}]) is None


def test_build_agent_mention_context_renders_soft_hint():
    out = _build_agent_mention_context(
        [
            {"agent_id": "agent_research", "role": "研究员"},
            {"agent_id": "agent_writer", "role": "写手"},
        ]
    )
    assert out is not None
    assert "用户点名关注以下 Agent（软提示，非强制派单/非硬路由）" in out
    assert "- 研究员 (id=agent_research)" in out
    assert "- 写手 (id=agent_writer)" in out
    assert "<agent_mentions>" in out


def test_merge_empty_mentions_keeps_attachment_only():
    att = "<attached_files>\nbody\n</attached_files>"
    assert merge_attachment_and_mention_context(att, None) == att
    assert merge_attachment_and_mention_context(att, []) == att
    assert merge_attachment_and_mention_context(None, None) is None


def test_merge_mentions_only_and_with_attachments():
    mentions = [{"agent_id": "a1", "role": "法务"}]
    only = merge_attachment_and_mention_context(None, mentions)
    assert only is not None
    assert "法务 (id=a1)" in only
    assert "<attached_files>" not in only

    att = "<attached_files>\nfile\n</attached_files>"
    both = merge_attachment_and_mention_context(att, mentions)
    assert both is not None
    assert both.startswith(att)
    assert "法务 (id=a1)" in both
    assert "软提示，非强制派单/非硬路由" in both


def test_queued_turn_preserves_agent_mentions():
    mentions = [{"agent_id": "a1", "role": "研究员"}]
    item = new_queued_turn(
        content="go",
        user_id="u1",
        attachments=[{"name": "a.txt", "path": "a.txt"}],
        agent_mentions=mentions,
    )
    assert item.agent_mentions == mentions
    assert item.attachments[0]["name"] == "a.txt"


def test_queued_turn_item_schema_returns_attachments_and_mentions():
    row = QueuedTurnItem(
        queue_id="q1",
        content="go",
        position=1,
        attachments=[
            MessageAttachment(name="a.txt", path="a.txt", text="hi"),
        ],
        agent_mentions=[AgentMention(agent_id="a1", role="研究员")],
    )
    dumped = row.model_dump()
    assert dumped["attachments"][0]["name"] == "a.txt"
    assert dumped["agent_mentions"] == [{"agent_id": "a1", "role": "研究员"}]
    legacy = QueuedTurnItem(queue_id="q2", content="plain", position=1)
    assert legacy.attachments == []
    assert legacy.agent_mentions == []


def test_record_turn_request_agent_mentions_optional():
    empty = RecordTurnRequest(
        user_message="hi",
        user_message_id="u1",
        trace_id="0123456789abcdef0123456789abcdef",
    )
    assert empty.agent_mentions == []
    body = RecordTurnRequest(
        user_message="hi",
        user_message_id="u1",
        trace_id="0123456789abcdef0123456789abcdef",
        agent_mentions=[AgentMention(agent_id="w1", role="写手")],
    )
    assert body.agent_mentions == [AgentMention(agent_id="w1", role="写手")]


def test_steer_enqueue_preserves_agent_mentions():
    reset_steer()
    begin_accepting("c1")
    mentions = [{"agent_id": "a1", "role": "写手"}]
    parked = try_enqueue(
        conversation_id="c1",
        content="nudge",
        user_id="u1",
        attachments=[],
        agent_mentions=mentions,
    )
    assert parked is not None
    assert parked.agent_mentions == mentions
    reset_steer()


def test_to_stored_agent_mentions_sanitizes_and_caps():
    from agentcore.conversation.mentions import to_stored_agent_mentions

    assert to_stored_agent_mentions(None) == []
    assert to_stored_agent_mentions([]) == []
    assert to_stored_agent_mentions([{"agent_id": "", "role": "x"}]) == []
    stored = to_stored_agent_mentions(
        [
            {"agent_id": "a1", "role": "研究员", "extra": "drop"},
            {"agent_id": "a2", "role": "写手"},
        ]
    )
    assert stored == [
        {"agent_id": "a1", "role": "研究员"},
        {"agent_id": "a2", "role": "写手"},
    ]
    too_many = [{"agent_id": f"a{i}", "role": f"r{i}"} for i in range(12)]
    assert len(to_stored_agent_mentions(too_many)) == 10


def test_resolve_interjection_mentions_payload_wins_over_stash():
    from agentcore.conversation.mentions import resolve_interjection_mentions

    payload = {
        "interjection_id": "i1",
        "agent_mentions": [{"agent_id": "from_payload", "role": "写手"}],
    }
    stashed = {"agent_mentions": [{"agent_id": "from_stash", "role": "研究员"}]}
    assert resolve_interjection_mentions(payload, stashed) == [
        {"agent_id": "from_payload", "role": "写手"}
    ]
    assert resolve_interjection_mentions({"interjection_id": "i1"}, stashed) == [
        {"agent_id": "from_stash", "role": "研究员"}
    ]
    assert resolve_interjection_mentions({}, None) is None


def test_message_detail_roundtrips_agent_mentions():
    from datetime import UTC, datetime

    from agentcore.api.schemas.messages import MessageDetail

    d = MessageDetail.model_validate(
        {
            "id": "m1",
            "conversation_id": "c1",
            "role": "user",
            "content": "帮我调研",
            "created_at": datetime.now(UTC),
            "agent_mentions": [{"agent_id": "w1", "role": "研究员"}],
        }
    )
    assert d.agent_mentions == [AgentMention(agent_id="w1", role="研究员")]

    legacy = MessageDetail.model_validate(
        {
            "id": "m2",
            "conversation_id": "c1",
            "role": "user",
            "content": "hi",
            "created_at": datetime.now(UTC),
        }
    )
    assert legacy.agent_mentions == []

    junk = MessageDetail.model_validate(
        {
            "id": "m3",
            "conversation_id": "c1",
            "role": "user",
            "content": "hi",
            "created_at": datetime.now(UTC),
            "agent_mentions": [{"agent_id": "", "role": "x"}, "nope"],
        }
    )
    assert junk.agent_mentions == []


@pytest.mark.asyncio
async def test_stream_chat_persists_agent_mentions(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agentcore.api.sse import EventSink
    from agentcore.conversation import turns as turns_mod
    from agentcore.core.types import AutonomyPolicy, recipe_to_axes

    created: list[dict] = []

    class _FakeSessionCM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title="t", folder_id=None)

    class _MsgRepo:
        def __init__(self, _session):
            pass

        async def create(self, **kwargs):
            created.append(kwargs)
            return SimpleNamespace(id="um1")

    class _BoardRepo:
        def __init__(self, _session):
            pass

        async def get_by_conversation_id(self, *_a, **_k):
            return None

    monkeypatch.setattr(turns_mod, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(turns_mod, "ConversationRepository", _ConvRepo)
    monkeypatch.setattr(turns_mod, "MessageRepository", _MsgRepo)
    monkeypatch.setattr(turns_mod, "BoardRepository", _BoardRepo)
    monkeypatch.setattr(turns_mod, "resolve_local_binding", AsyncMock(return_value=None))
    monkeypatch.setattr(turns_mod, "resolve_profile_set", AsyncMock(return_value=None))

    monkeypatch.setattr(
        turns_mod,
        "resolve_permission_axes",
        AsyncMock(return_value=recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT)),
    )
    monkeypatch.setattr(
        turns_mod,
        "build_turn_backend",
        AsyncMock(return_value=SimpleNamespace(location="server")),
    )
    monkeypatch.setattr(turns_mod, "persist_attachments", AsyncMock(return_value=[]))
    monkeypatch.setattr(turns_mod, "to_stored_metadata", lambda _a: [])
    monkeypatch.setattr(
        turns_mod,
        "load_chat_context",
        AsyncMock(return_value=[{"role": "user", "content": "hi"}]),
    )
    monkeypatch.setattr(turns_mod, "compact_before_turn", AsyncMock())
    monkeypatch.setattr(turns_mod, "maybe_delete_zero_output_send", AsyncMock())
    monkeypatch.setattr(turns_mod, "run_and_persist", AsyncMock())
    monkeypatch.setattr(turns_mod, "schedule_title_generation", lambda **_k: None)
    monkeypatch.setattr(
        "agentcore.runtime.coordination.await_live_detached_drive",
        AsyncMock(),
    )

    mentions = [{"agent_id": "w1", "role": "研究员"}]
    sink = EventSink()
    await turns_mod.stream_chat(
        conversation_id="c1",
        user_message="帮我调研",
        user_id="u1",
        sink=sink,
        agent_mentions=mentions,
    )

    assert created
    assert created[0]["agent_mentions"] == mentions


@pytest.mark.asyncio
async def test_regenerate_forwards_stored_agent_mentions(monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agentcore.api.sse import EventSink
    from agentcore.conversation import turns as turns_mod
    from agentcore.core.types import AutonomyPolicy, recipe_to_axes

    captured: list[dict] = []
    mentions = [{"agent_id": "w1", "role": "写手"}]

    class _FakeSessionCM:
        async def __aenter__(self):
            return SimpleNamespace(expire_all=lambda: None, commit=AsyncMock())

        async def __aexit__(self, *_a):
            return False

    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title="t", folder_id=None)

    class _MsgRepo:
        def __init__(self, _session):
            pass

        async def get_by_id(self, _mid, conversation_id=None):
            return SimpleNamespace(
                id=_mid,
                role="user",
                content="帮我写",
                created_at=datetime.now(UTC),
                agent_mentions=mentions,
            )

        async def delete_after(self, *_a, **_k):
            return None

        async def update_content(self, *_a, **_k):
            return None

    class _BoardRepo:
        def __init__(self, _session):
            pass

        async def get_by_conversation_id(self, *_a, **_k):
            return None

    async def _run(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(turns_mod, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(turns_mod, "ConversationRepository", _ConvRepo)
    monkeypatch.setattr(turns_mod, "MessageRepository", _MsgRepo)
    monkeypatch.setattr(turns_mod, "BoardRepository", _BoardRepo)
    monkeypatch.setattr(turns_mod, "resolve_local_binding", AsyncMock(return_value=None))
    monkeypatch.setattr(turns_mod, "resolve_profile_set", AsyncMock(return_value=None))

    monkeypatch.setattr(
        turns_mod,
        "resolve_permission_axes",
        AsyncMock(return_value=recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT)),
    )
    monkeypatch.setattr(
        turns_mod,
        "build_turn_backend",
        AsyncMock(return_value=SimpleNamespace(location="server")),
    )
    monkeypatch.setattr(
        turns_mod,
        "load_chat_context",
        AsyncMock(return_value=[{"role": "user", "content": "帮我写"}]),
    )
    monkeypatch.setattr(turns_mod, "compact_before_turn", AsyncMock())
    monkeypatch.setattr(turns_mod, "run_and_persist", _run)
    monkeypatch.setattr(
        "agentcore.runtime.coordination.await_live_detached_drive",
        AsyncMock(),
    )

    sink = EventSink()
    await turns_mod.regenerate_chat(
        conversation_id="c1",
        message_id="u1",
        user_id="u1",
        sink=sink,
    )

    assert captured
    assert captured[0]["agent_mentions"] == mentions


@pytest.mark.asyncio
async def test_regenerate_replaces_agent_mentions_when_edit_sends_empty(monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agentcore.api.sse import EventSink
    from agentcore.conversation import turns as turns_mod
    from agentcore.core.types import AutonomyPolicy, recipe_to_axes

    captured: list[dict] = []
    updates: list[dict] = []
    mentions = [{"agent_id": "w1", "role": "写手"}]

    class _FakeSessionCM:
        async def __aenter__(self):
            return SimpleNamespace(expire_all=lambda: None, commit=AsyncMock())

        async def __aexit__(self, *_a):
            return False

    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title="t", folder_id=None)

    class _MsgRepo:
        def __init__(self, _session):
            pass

        async def get_by_id(self, _mid, conversation_id=None):
            return SimpleNamespace(
                id=_mid,
                role="user",
                content="帮我写",
                created_at=datetime.now(UTC),
                agent_mentions=mentions,
            )

        async def delete_after(self, *_a, **_k):
            return None

        async def update_content(self, message_id, content=None, **kwargs):
            updates.append({"message_id": message_id, "content": content, **kwargs})

    class _BoardRepo:
        def __init__(self, _session):
            pass

        async def get_by_conversation_id(self, *_a, **_k):
            return None

    async def _run(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(turns_mod, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(turns_mod, "ConversationRepository", _ConvRepo)
    monkeypatch.setattr(turns_mod, "MessageRepository", _MsgRepo)
    monkeypatch.setattr(turns_mod, "BoardRepository", _BoardRepo)
    monkeypatch.setattr(turns_mod, "resolve_local_binding", AsyncMock(return_value=None))
    monkeypatch.setattr(turns_mod, "resolve_profile_set", AsyncMock(return_value=None))
    monkeypatch.setattr(
        turns_mod,
        "resolve_permission_axes",
        AsyncMock(return_value=recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT)),
    )
    monkeypatch.setattr(
        turns_mod,
        "build_turn_backend",
        AsyncMock(return_value=SimpleNamespace(location="server")),
    )
    monkeypatch.setattr(
        turns_mod,
        "load_chat_context",
        AsyncMock(return_value=[{"role": "user", "content": "帮我写"}]),
    )
    monkeypatch.setattr(turns_mod, "compact_before_turn", AsyncMock())
    monkeypatch.setattr(turns_mod, "run_and_persist", _run)
    monkeypatch.setattr(
        "agentcore.runtime.coordination.await_live_detached_drive",
        AsyncMock(),
    )

    sink = EventSink()
    await turns_mod.regenerate_chat(
        conversation_id="c1",
        message_id="u1",
        user_id="u1",
        sink=sink,
        edited_content="帮我写",
        edited_attachments=[],
        edited_agent_mentions=[],
    )

    assert updates
    assert updates[0]["agent_mentions"] == []
    assert updates[0]["attachments"] == []
    assert captured
    assert captured[0]["agent_mentions"] is None
