"""Cold PAUSED must not hold ``turn_runs`` while code-index flush drains.

Regression: ``stream_chat`` / ``regenerate`` / ``resume`` finally awaited
``flush_code_index_maintenance`` even after ``FinishReason.PAUSED``, so a slow
index rebuild kept the slot live for minutes and ``POST …/resume`` drain → 409.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentcore.conversation import turns as turns_mod
from agentcore.runtime.events import EventSink, FinishReason, message_end
from agentcore.runtime.turn.runs import turn_runs


class _FakeSessionCM:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


@pytest.fixture(autouse=True)
def _clear_turn_runs():
    turn_runs._runs.clear()
    yield
    turn_runs._runs.clear()


async def test_flush_blocks_until_complete_when_not_paused():
    """Non-PAUSED terminals still await flush (BY-DESIGN)."""
    release = asyncio.Event()
    finished = asyncio.Event()

    async def slow_flush():
        await release.wait()
        finished.set()

    backend = SimpleNamespace(flush_code_index_maintenance=slow_flush)
    task = asyncio.create_task(
        turns_mod._flush_code_index_before_close(backend, block=True)
    )
    await asyncio.sleep(0)
    assert not task.done()
    assert not finished.is_set()
    release.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert finished.is_set()


async def test_flush_deferred_when_paused_does_not_await():
    """PAUSED path schedules flush without awaiting it."""
    release = asyncio.Event()
    started = asyncio.Event()
    finished = asyncio.Event()

    async def slow_flush():
        started.set()
        await release.wait()
        finished.set()

    backend = SimpleNamespace(flush_code_index_maintenance=slow_flush)
    await turns_mod._flush_code_index_before_close(backend, block=False)
    await asyncio.wait_for(started.wait(), timeout=1.0)
    # Caller returned while flush is still blocked.
    assert not finished.is_set()
    release.set()
    await asyncio.wait_for(finished.wait(), timeout=1.0)


async def test_block_code_index_flush_false_only_for_paused():
    sink = EventSink()
    assert turns_mod._block_code_index_flush(sink) is True
    sink.emit(message_end(FinishReason.END_TURN))
    assert turns_mod._block_code_index_flush(sink) is True
    sink.emit(message_end(FinishReason.PAUSED))
    assert turns_mod._block_code_index_flush(sink) is False


def _patch_stream_chat_deps(monkeypatch, *, backend, run_and_persist):
    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title="t", folder_id=None)

    class _MsgRepo:
        def __init__(self, _session):
            pass

        async def create(self, **_kwargs):
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
    monkeypatch.setattr(
        "agentcore.conversation.midflight_persist.MessageRepository",
        _MsgRepo,
    )
    monkeypatch.setattr(turns_mod, "resolve_local_binding", AsyncMock(return_value=None))
    monkeypatch.setattr(turns_mod, "resolve_profile_set", AsyncMock(return_value=None))

    from agentcore.core.types import AutonomyPolicy, recipe_to_axes

    monkeypatch.setattr(
        turns_mod,
        "resolve_permission_axes",
        AsyncMock(return_value=recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT)),
    )
    monkeypatch.setattr(turns_mod, "build_turn_backend", AsyncMock(return_value=backend))
    monkeypatch.setattr(turns_mod, "persist_attachments", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "agentcore.conversation.midflight_persist.to_stored_metadata",
        lambda _a: None,
    )
    monkeypatch.setattr(
        turns_mod,
        "load_chat_context",
        AsyncMock(return_value=[{"role": "user", "content": "hi"}]),
    )
    monkeypatch.setattr(turns_mod, "run_and_persist", run_and_persist)

    async def _no_drive(_cid):
        return None

    monkeypatch.setattr(
        "agentcore.runtime.coordination.await_live_detached_drive",
        _no_drive,
    )


async def test_stream_chat_paused_releases_slot_before_flush_completes(monkeypatch):
    """PAUSED finally: registry drainable while flush is still in flight."""
    flush_release = asyncio.Event()
    flush_started = asyncio.Event()

    async def slow_flush():
        flush_started.set()
        await flush_release.wait()

    backend = SimpleNamespace(
        location="local",
        flush_code_index_maintenance=slow_flush,
    )

    async def _run_paused(**kwargs):
        kwargs["sink"].emit(message_end(FinishReason.PAUSED))

    _patch_stream_chat_deps(monkeypatch, backend=backend, run_and_persist=_run_paused)

    conversation_id = "c-paused-flush"
    sink = EventSink()
    task = asyncio.create_task(
        turns_mod.stream_chat(
            conversation_id=conversation_id,
            user_message="hi",
            user_id="u1",
            sink=sink,
        )
    )
    turn_runs.register(conversation_id=conversation_id, task=task, sink=sink)

    await asyncio.wait_for(flush_started.wait(), timeout=1.0)
    # Turn task must finish (and free the slot) while flush is still blocked.
    assert await turn_runs.drain(conversation_id, timeout=1.0)
    assert turn_runs.get(conversation_id) is None
    assert not flush_release.is_set()
    await asyncio.wait_for(task, timeout=1.0)

    flush_release.set()
    # Let the deferred flush finish cleanly.
    await asyncio.sleep(0)


async def test_stream_chat_end_turn_awaits_flush_before_slot_release(monkeypatch):
    """Non-PAUSED finally still awaits flush before the turn task completes."""
    flush_release = asyncio.Event()
    flush_started = asyncio.Event()

    async def slow_flush():
        flush_started.set()
        await flush_release.wait()

    backend = SimpleNamespace(
        location="local",
        flush_code_index_maintenance=slow_flush,
    )

    async def _run_end(**kwargs):
        kwargs["sink"].emit(message_end(FinishReason.END_TURN))

    _patch_stream_chat_deps(monkeypatch, backend=backend, run_and_persist=_run_end)

    conversation_id = "c-end-flush"
    sink = EventSink()
    task = asyncio.create_task(
        turns_mod.stream_chat(
            conversation_id=conversation_id,
            user_message="hi",
            user_id="u1",
            sink=sink,
        )
    )
    turn_runs.register(conversation_id=conversation_id, task=task, sink=sink)

    await asyncio.wait_for(flush_started.wait(), timeout=1.0)
    # Slot still held: flush is awaited inside finally.
    assert turn_runs.get(conversation_id) is not None
    assert not task.done()

    flush_release.set()
    assert await turn_runs.drain(conversation_id, timeout=1.0)
    await asyncio.wait_for(task, timeout=1.0)
