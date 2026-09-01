"""Mid-flight user row: persist / reuse / cancel-delete."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from agentcore.conversation.midflight_persist import (
    delete_midflight_user_message,
    load_or_create_turn_user_message,
    persist_midflight_user_message,
)
from agentcore.core.types import new_id


@pytest.mark.asyncio
async def test_persist_midflight_user_message_inserts_pinned_id(monkeypatch):
    pinned = new_id()
    created: dict[str, object] = {}

    class Repo:
        def __init__(self, _session: object) -> None:
            pass

        async def create(self, **kwargs: object) -> object:
            created.update(kwargs)
            return SimpleNamespace(id=kwargs["message_id"])

    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=object())
    session_cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "agentcore.conversation.midflight_persist.async_session_factory",
        lambda: session_cm,
    )
    monkeypatch.setattr(
        "agentcore.conversation.midflight_persist.MessageRepository",
        Repo,
    )

    out = await persist_midflight_user_message(
        conversation_id="c1",
        content="排队一句",
        user_message_id=pinned,
        attachments=[{"name": "a.txt", "path": "a.txt", "truncated": False}],
        agent_mentions=[{"agent_id": "ag1", "role": "研究员"}],
    )
    assert out == pinned
    assert created["conversation_id"] == "c1"
    assert created["role"] == "user"
    assert created["content"] == "排队一句"
    assert created["message_id"] == pinned
    assert created["attachments"]
    assert created["agent_mentions"]


@pytest.mark.asyncio
async def test_persist_midflight_user_message_idempotent_on_same_user_row(
    monkeypatch,
):
    pinned = new_id()

    class Repo:
        def __init__(self, _session: object) -> None:
            pass

        async def create(self, **_kwargs: object) -> object:
            raise IntegrityError("dup", {}, Exception())

        async def get_by_id(self, message_id: str, *, conversation_id: str) -> object:
            assert message_id == pinned
            assert conversation_id == "c1"
            return SimpleNamespace(id=pinned, role="user")

    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=object())
    session_cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "agentcore.conversation.midflight_persist.async_session_factory",
        lambda: session_cm,
    )
    monkeypatch.setattr(
        "agentcore.conversation.midflight_persist.MessageRepository",
        Repo,
    )

    out = await persist_midflight_user_message(
        conversation_id="c1",
        content="再发一次",
        user_message_id=pinned,
    )
    assert out == pinned


@pytest.mark.asyncio
async def test_delete_midflight_user_message_skips_non_uuid(monkeypatch):
    called = False

    class Repo:
        def __init__(self, _session: object) -> None:
            pass

        async def delete_by_id(self, *_a: object, **_k: object) -> bool:
            nonlocal called
            called = True
            return True

    monkeypatch.setattr(
        "agentcore.conversation.midflight_persist.MessageRepository",
        Repo,
    )
    assert await delete_midflight_user_message("c1", "not-a-uuid") is False
    assert called is False


@pytest.mark.asyncio
async def test_load_or_create_reuses_existing_user_row():
    pinned = new_id()
    existing = SimpleNamespace(id=pinned, role="user")
    created = False

    class Repo:
        def __init__(self, _session: object) -> None:
            pass

        async def get_by_id(self, message_id: str, *, conversation_id: str) -> object:
            assert message_id == pinned
            return existing

        async def create(self, **_kwargs: object) -> object:
            nonlocal created
            created = True
            return SimpleNamespace(id="new")

        async def update_content(self, *_a: object, **_k: object) -> None:
            return None

    import agentcore.conversation.midflight_persist as persist_mod

    original = persist_mod.MessageRepository
    persist_mod.MessageRepository = Repo  # type: ignore[misc, assignment]
    try:
        row = await load_or_create_turn_user_message(
            object(),
            conversation_id="c1",
            user_message="正文",
            existing_user_message_id=pinned,
            attachments=None,
            agent_mentions=None,
        )
    finally:
        persist_mod.MessageRepository = original  # type: ignore[misc]
    assert row is existing
    assert created is False


@pytest.mark.asyncio
async def test_start_queued_turn_forwards_existing_user_message_id(monkeypatch):
    from agentcore.runtime.events import EventSink
    from agentcore.runtime.turn.queue import _start_queued_turn, new_queued_turn

    seen: dict[str, object] = {}
    done = asyncio.Event()

    async def fake_stream_chat(**kwargs: object) -> None:
        seen.update(kwargs)
        sink = kwargs["sink"]
        assert isinstance(sink, EventSink)
        sink.close()
        done.set()

    monkeypatch.setattr(
        "agentcore.conversation.service.stream_chat", fake_stream_chat
    )
    monkeypatch.setattr(
        "agentcore.runtime.turn.runs.turn_runs.register",
        lambda **_k: "run",
    )
    item = new_queued_turn(
        content="下一问",
        user_id="u",
        user_message_id="u-queued",
    )
    await _start_queued_turn("c-fwd", item)
    await asyncio.wait_for(done.wait(), timeout=2.0)
    assert seen["existing_user_message_id"] == "u-queued"
    assert seen["user_message"] == "下一问"
