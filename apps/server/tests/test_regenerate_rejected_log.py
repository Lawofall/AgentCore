"""regenerate 早退拒绝须落库（chat.regenerate_rejected），便于排前端传错 id。"""

from __future__ import annotations

from datetime import UTC
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import agentcore.conversation.turns as turns_mod
from agentcore.api.sse import EventSink


class _FakeSessionCM:
    async def __aenter__(self):
        return SimpleNamespace(expire_all=lambda: None, commit=AsyncMock())

    async def __aexit__(self, *_a):
        return False


@pytest.mark.asyncio
async def test_regenerate_rejects_non_user_message_and_logs(monkeypatch):
    warnings: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs):
        warnings.append((event, kwargs))

    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title="t", folder_id=None)

    class _MsgRepo:
        def __init__(self, _session):
            pass

        async def get_by_id(self, _mid, conversation_id=None):
            return SimpleNamespace(id=_mid, role="assistant", created_at=None)

    monkeypatch.setattr(turns_mod, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(turns_mod, "ConversationRepository", _ConvRepo)
    monkeypatch.setattr(turns_mod, "MessageRepository", _MsgRepo)
    monkeypatch.setattr(turns_mod.logger, "warning", _capture)

    sink = EventSink()
    await turns_mod.regenerate_chat(
        conversation_id="c1",
        message_id="asst-1",
        user_id="u1",
        sink=sink,
    )

    assert any(e == "chat.regenerate_rejected" for e, _ in warnings)
    payload = next(kw for e, kw in warnings if e == "chat.regenerate_rejected")
    assert payload["reason"] == "not_user"
    assert payload["found_role"] == "assistant"
    assert payload["message_id"] == "asst-1"
    assert payload["conversation_id"] == "c1"


@pytest.mark.asyncio
async def test_regenerate_rejects_missing_message_and_logs(monkeypatch):
    warnings: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs):
        warnings.append((event, kwargs))

    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title="t", folder_id=None)

    class _MsgRepo:
        def __init__(self, _session):
            pass

        async def get_by_id(self, _mid, conversation_id=None):
            return None

    monkeypatch.setattr(turns_mod, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(turns_mod, "ConversationRepository", _ConvRepo)
    monkeypatch.setattr(turns_mod, "MessageRepository", _MsgRepo)
    monkeypatch.setattr(turns_mod.logger, "warning", _capture)

    sink = EventSink()
    await turns_mod.regenerate_chat(
        conversation_id="c1",
        message_id="ghost",
        user_id="u1",
        sink=sink,
    )

    payload = next(kw for e, kw in warnings if e == "chat.regenerate_rejected")
    assert payload["reason"] == "missing"
    assert payload["found_role"] is None


class _ExpireBomb:
    """ORM stand-in: reading attrs after expire_all mimics MissingGreenlet."""

    def __init__(self, **fields):
        self.__dict__.update(fields)
        self._expired = False

    def expire(self) -> None:
        self._expired = True

    def __getattribute__(self, name: str):
        if name.startswith("_") or name in {"expire"}:
            return object.__getattribute__(self, name)
        if object.__getattribute__(self, "_expired"):
            raise Exception(
                "greenlet_spawn has not been called; can't call await_only() here. "
                "Was IO attempted in an unexpected place?"
            )
        return object.__getattribute__(self, name)


@pytest.mark.asyncio
async def test_regenerate_does_not_touch_orm_after_expire(monkeypatch):
    """回归：commit/expire 后再读 target.content → 线上 chat.regenerate_error。"""
    from datetime import datetime

    errors: list[tuple[str, dict]] = []
    expire_calls = {"n": 0}

    class _Session:
        def __init__(self):
            self._objs: list[_ExpireBomb] = []

        def track(self, obj: _ExpireBomb) -> _ExpireBomb:
            self._objs.append(obj)
            return obj

        def expire_all(self) -> None:
            expire_calls["n"] += 1
            for o in self._objs:
                o.expire()

        commit = AsyncMock()

    sessions: list[_Session] = []

    class _CM:
        def __init__(self):
            self.session = _Session()
            sessions.append(self.session)

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, *_a):
            return False

    created = datetime(2026, 8, 11, 1, 54, tzinfo=UTC)
    conv = None
    target = None

    class _ConvRepo:
        def __init__(self, session):
            self._s = session

        async def get_by_id_unscoped(self, _cid):
            nonlocal conv
            conv = self._s.track(
                _ExpireBomb(id="c1", title="t", folder_id=None, local_root_id=None)
            )
            return conv

    class _MsgRepo:
        def __init__(self, session):
            self._s = session

        async def get_by_id(self, mid, conversation_id=None):
            nonlocal target
            target = self._s.track(
                _ExpireBomb(
                    id=mid,
                    role="user",
                    content="继续任务",
                    created_at=created,
                )
            )
            return target

        async def update_content(self, *_a, **_k):
            return None

        async def delete_after(self, *_a, **_k):
            return None

    class _BoardRepo:
        def __init__(self, _session):
            pass

        async def get_by_conversation_id(self, *_a, **_k):
            return None

    async def _run_and_persist(**kwargs):
        assert kwargs["user_message"] == "继续任务"
        assert kwargs["history"] == [{"role": "user", "content": "prior"}]

    monkeypatch.setattr(turns_mod, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(turns_mod, "ConversationRepository", _ConvRepo)
    monkeypatch.setattr(turns_mod, "MessageRepository", _MsgRepo)
    monkeypatch.setattr(turns_mod, "BoardRepository", _BoardRepo)
    monkeypatch.setattr(turns_mod, "resolve_local_binding", AsyncMock(return_value=None))
    monkeypatch.setattr(turns_mod, "resolve_profile_set", AsyncMock(return_value=None))

    monkeypatch.setattr(turns_mod, "resolve_permission_axes", AsyncMock(return_value=None))
    monkeypatch.setattr(turns_mod, "maybe_compact_near_ceiling", AsyncMock(return_value=False))
    monkeypatch.setattr(
        turns_mod,
        "load_chat_context",
        AsyncMock(
            return_value=[
                {"role": "user", "content": "prior"},
                {"role": "user", "content": "继续任务"},
            ]
        ),
    )
    monkeypatch.setattr(turns_mod, "build_turn_backend", AsyncMock(return_value=None))
    monkeypatch.setattr(turns_mod, "run_and_persist", _run_and_persist)
    monkeypatch.setattr(turns_mod, "resolve_turn_model", lambda _c: None)
    monkeypatch.setattr(
        turns_mod.logger,
        "error",
        lambda event, **kw: errors.append((event, kw)),
    )

    import agentcore.runtime.coordination as coord_mod

    monkeypatch.setattr(coord_mod, "await_live_detached_drive", AsyncMock())

    sink = EventSink()
    await turns_mod.regenerate_chat(
        conversation_id="c1",
        message_id="u1",
        user_id="user-1",
        sink=sink,
    )

    assert expire_calls["n"] == 0, "regenerate must not expire_all then re-read ORM attrs"
    assert errors == []
    assert len(sessions) == 2  # truncate session + history session
