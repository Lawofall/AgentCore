"""Progressive local-turn cloud projection (begin / journal / segments / abort).

Does not expand ``POST …/local-turns`` finalize semantics. DB collaborators are faked.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError

from agentcore.api.routes.conversations import messages as messages_route
from agentcore.api.schemas.messages import (
    AbortLocalTurnRequest,
    BeginLocalTurnRequest,
    LocalTurnHeartbeatRequest,
    LocalTurnJournalRequest,
    LocalTurnStreamSegmentsRequest,
)
from agentcore.conversation import local_turn as local_turn_mod
from agentcore.conversation.local_turn import (
    abort_local_turn,
    append_local_turn_journal,
    begin_local_turn,
    upsert_local_turn_stream_segments,
)
from agentcore.core.errors import ValidationError
from agentcore.runtime.events.stream_checkpointer import CHANNEL_CAPTAIN_CONTENT

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _stub_local_turn_lease(monkeypatch):
    monkeypatch.setattr(local_turn_mod, "_acquire_local_turn_lease", AsyncMock())
    monkeypatch.setattr(local_turn_mod, "_release_local_turn_lease", AsyncMock())

_TRACE = "0123456789abcdef0123456789abcdef"
_UMID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_MID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class _FakeSessionCM:
    async def __aenter__(self):
        return SimpleNamespace(commit=AsyncMock())

    async def __aexit__(self, *_exc):
        return False


def _user_ns(*, role="user", usage=None):
    return SimpleNamespace(id=_UMID, role=role, usage=usage)


def _assistant_ns(*, role="assistant", usage=None):
    return SimpleNamespace(id=_MID, role=role, usage=usage or {"status": "running"})


def _patch_user_insert(monkeypatch, *, existing=None, create_error=None, creates=None):
    class Repo:
        def __init__(self, _s):
            pass

        async def create(self, **kw):
            if creates is not None:
                creates.append(kw)
            if create_error is not None:
                raise create_error
            return SimpleNamespace(id=kw["message_id"])

        async def get_by_id(self, message_id, *, conversation_id):
            if existing is not None:
                return existing
            return None

    monkeypatch.setattr(local_turn_mod, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(local_turn_mod, "MessageRepository", Repo)


async def test_begin_pins_user_and_running_placeholder(monkeypatch):
    creates: list[dict] = []
    begin_calls: list[dict] = []
    _patch_user_insert(monkeypatch, creates=creates)
    store = SimpleNamespace(
        begin_turn=AsyncMock(side_effect=lambda **kw: begin_calls.append(kw)),
        finalize=AsyncMock(side_effect=AssertionError("begin must not finalize")),
    )
    monkeypatch.setattr(local_turn_mod, "get_cloud_store", lambda: store)

    result = await begin_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hello",
        user_message_id=_UMID,
        message_id=_MID,
        trace_id=_TRACE,
    )
    assert result == {"user_message_id": _UMID, "assistant_message_id": _MID}
    assert creates[0]["role"] == "user"
    assert creates[0]["message_id"] == _UMID
    assert creates[0]["content"] == "hello"
    assert begin_calls == [
        {"conversation_id": "c1", "message_id": _MID, "trace_id": _TRACE}
    ]
    store.finalize.assert_not_called()


async def test_begin_retry_is_idempotent(monkeypatch):
    creates: list[dict] = []
    begin_calls: list[dict] = []
    _patch_user_insert(
        monkeypatch,
        creates=creates,
        create_error=IntegrityError("INSERT", {}, Exception("messages_pkey")),
        existing=_user_ns(),
    )
    store = SimpleNamespace(begin_turn=AsyncMock(side_effect=lambda **kw: begin_calls.append(kw)))
    monkeypatch.setattr(local_turn_mod, "get_cloud_store", lambda: store)

    result = await begin_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hello",
        user_message_id=_UMID,
        message_id=_MID,
        trace_id=_TRACE,
    )
    assert result["assistant_message_id"] == _MID
    assert len(creates) == 1
    assert begin_calls[0]["message_id"] == _MID


async def test_begin_user_id_collision_with_assistant_raises(monkeypatch):
    _patch_user_insert(
        monkeypatch,
        create_error=IntegrityError("INSERT", {}, Exception("messages_pkey")),
        existing=_user_ns(role="assistant"),
    )
    monkeypatch.setattr(
        local_turn_mod,
        "get_cloud_store",
        lambda: SimpleNamespace(begin_turn=AsyncMock()),
    )
    with pytest.raises(IntegrityError):
        await begin_local_turn(
            conversation_id="c1",
            user_id="u1",
            user_message="hello",
            user_message_id=_UMID,
            message_id=_MID,
            trace_id=_TRACE,
        )


async def test_begin_regenerate_patches_user_and_truncates(monkeypatch):
    updates: list[dict] = []
    deletes: list[dict] = []
    created_at = datetime(2026, 1, 2, tzinfo=UTC)

    class Repo:
        def __init__(self, _s):
            pass

        async def create(self, **_kw):
            raise AssertionError("regenerate must not insert a new user row")

        async def get_by_id(self, message_id, *, conversation_id):
            assert message_id == _UMID
            assert conversation_id == "c1"
            return SimpleNamespace(id=_UMID, role="user", created_at=created_at)

        async def update_content(self, message_id, content=None, **kwargs):
            updates.append({"message_id": message_id, "content": content, **kwargs})

        async def delete_after(self, conversation_id, *, after_created_at, commit=True):
            deletes.append(
                {
                    "conversation_id": conversation_id,
                    "after_created_at": after_created_at,
                    "commit": commit,
                }
            )
            return 1

    monkeypatch.setattr(local_turn_mod, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(local_turn_mod, "MessageRepository", Repo)
    begin_calls: list[dict] = []
    monkeypatch.setattr(
        local_turn_mod,
        "get_cloud_store",
        lambda: SimpleNamespace(
            begin_turn=AsyncMock(side_effect=lambda **kw: begin_calls.append(kw))
        ),
    )

    result = await begin_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="edited",
        user_message_id=_UMID,
        message_id=_MID,
        trace_id=_TRACE,
        regenerate=True,
        attachments=[{"name": "a.txt", "path": "a.txt"}],
        agent_mentions=[],
    )
    assert result["assistant_message_id"] == _MID
    assert updates[0]["content"] == "edited"
    assert updates[0]["attachments"] == [{"name": "a.txt", "path": "a.txt"}]
    assert updates[0]["agent_mentions"] == []
    assert deletes[0]["after_created_at"] == created_at
    assert begin_calls[0]["message_id"] == _MID


async def test_begin_regenerate_rejects_missing_user(monkeypatch):
    class Repo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, *_a, **_k):
            return None

    monkeypatch.setattr(local_turn_mod, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(local_turn_mod, "MessageRepository", Repo)
    monkeypatch.setattr(
        local_turn_mod,
        "get_cloud_store",
        lambda: SimpleNamespace(begin_turn=AsyncMock()),
    )
    with pytest.raises(ValidationError, match="只能从用户消息重新生成"):
        await begin_local_turn(
            conversation_id="c1",
            user_id="u1",
            user_message="hello",
            user_message_id=_UMID,
            message_id=_MID,
            trace_id=_TRACE,
            regenerate=True,
        )


async def test_journal_requires_seq_and_does_not_settle(monkeypatch):
    appended: list[dict] = []
    store = SimpleNamespace(
        append_journal=AsyncMock(
            side_effect=lambda **kw: appended.append(kw) or 0,
        ),
        finalize=AsyncMock(side_effect=AssertionError("journal must not finalize")),
    )
    monkeypatch.setattr(local_turn_mod, "get_cloud_store", lambda: store)

    await append_local_turn_journal(
        conversation_id="c1",
        user_id="u1",
        message_id=_MID,
        trace_id=_TRACE,
        entries=[
            (0, {"kind": "run_plan", "payload": {}}),
            (1, {"kind": "llm_call", "payload": {}}),
        ],
    )
    assert [row["seq"] for row in appended] == [0, 1]
    assert all(row["turn_id"] == _MID for row in appended)
    store.finalize.assert_not_called()

    with pytest.raises(PydanticValidationError):
        LocalTurnJournalRequest.model_validate(
            {"message_id": _MID, "entries": [{"entry": {"kind": "x"}}]}
        )


async def test_journal_failure_does_not_pretend_settle(monkeypatch):
    appended: list[int] = []

    async def _append(**kw):
        if kw["seq"] == 1:
            raise RuntimeError("telemetry down")
        appended.append(kw["seq"])
        return kw["seq"]

    store = SimpleNamespace(
        append_journal=_append,
        finalize=AsyncMock(side_effect=AssertionError("must not settle")),
    )
    monkeypatch.setattr(local_turn_mod, "get_cloud_store", lambda: store)

    with pytest.raises(RuntimeError, match="telemetry down"):
        await append_local_turn_journal(
            conversation_id="c1",
            user_id="u1",
            message_id=_MID,
            entries=[(0, {"kind": "a"}), (1, {"kind": "b"})],
        )
    assert appended == [0]
    store.finalize.assert_not_called()


async def test_stream_segments_upsert_without_touching_content(monkeypatch):
    upserted: list[dict] = []
    store = SimpleNamespace(
        upsert_stream_segments=AsyncMock(side_effect=lambda **kw: upserted.append(kw)),
        clear_stream_segments=AsyncMock(
            side_effect=AssertionError("must not clear segments")
        ),
        finalize=AsyncMock(side_effect=AssertionError("must not finalize")),
    )
    monkeypatch.setattr(local_turn_mod, "get_cloud_store", lambda: store)

    await upsert_local_turn_stream_segments(
        conversation_id="c1",
        user_id="u1",
        message_id=_MID,
        segments=[(CHANNEL_CAPTAIN_CONTENT, "半截正文", 0)],
    )
    assert upserted == [
        {
            "turn_id": _MID,
            "segments": [(CHANNEL_CAPTAIN_CONTENT, "半截正文", 0)],
        }
    ]
    store.clear_stream_segments.assert_not_called()
    store.finalize.assert_not_called()


async def test_get_overlay_stacks_running_and_segments(monkeypatch):
    """GET messages overlays captain text onto a begin() running placeholder."""
    from agentcore.conversation.store.overlay import overlay_message_fields

    usage = {"status": "running"}
    segments = [
        {"channel": CHANNEL_CAPTAIN_CONTENT, "text": "叠上去的正文", "generation": 0},
    ]
    content, _reasoning = overlay_message_fields(
        content="",
        reasoning_content=None,
        segments=segments,
        usage=usage,
    )
    assert content == "叠上去的正文"
    assert usage.get("paused") is None
    assert usage["status"] == "running"

    assistant = SimpleNamespace(
        id=_MID,
        conversation_id="c1",
        role="assistant",
        content="",
        reasoning_content=None,
        usage=usage,
        created_at=datetime.now(UTC),
        attachments=[],
        agent_mentions=[],
        citations=[],
        evidence_ledger=[],
        followups=[],
        cost=None,
        feedback=None,
        trace_id=_TRACE,
    )
    user = SimpleNamespace(user_id="u1")

    class ConvRepo:
        async def get_by_id(self, _cid, *, user_id):
            return SimpleNamespace(id="c1", user_id=user_id)

    class MsgRepo:
        async def count_by_conversation(self, _cid):
            return 1

        async def list_latest(self, _cid, limit):
            return [assistant], False

    class JournalRepo:
        async def load_map(self, _ids):
            return {}

    class MemRepo:
        async def list_for_conversation(self, _cid):
            return []

    store = SimpleNamespace(
        list_stream_segments_map=AsyncMock(
            return_value={_MID: segments},
        )
    )
    monkeypatch.setattr(messages_route, "get_conversation_store", lambda: store)

    page = await messages_route.list_messages(
        conversation_id="c1",
        user=user,
        limit=100,
        before=None,
        after=None,
        around=None,
        conv_repo=ConvRepo(),
        repo=MsgRepo(),
        journal_repo=JournalRepo(),
        mem_update_repo=MemRepo(),
    )
    assert page.data[0].status == "running"
    assert page.data[0].paused is None
    assert page.data[0].content == "叠上去的正文"


async def test_new_endpoints_do_not_write_paused_or_empty_complete():
    BeginLocalTurnRequest.model_validate(
        {
            "user_message": "hi",
            "user_message_id": _UMID,
            "message_id": _MID,
            "trace_id": _TRACE,
        }
    )
    dumped = BeginLocalTurnRequest.model_validate(
        {
            "user_message": "hi",
            "user_message_id": _UMID,
            "message_id": _MID,
            "trace_id": _TRACE,
        }
    ).model_dump()
    assert "finish_reason" not in dumped
    assert "paused" not in dumped
    journal = LocalTurnJournalRequest.model_validate(
        {"message_id": _MID, "entries": [{"seq": 0, "entry": {"kind": "x"}}]}
    )
    assert journal.entries[0].seq == 0
    segs = LocalTurnStreamSegmentsRequest.model_validate(
        {
            "message_id": _MID,
            "segments": [
                {"channel": CHANNEL_CAPTAIN_CONTENT, "text": "t", "generation": 0}
            ],
        }
    )
    assert segs.segments[0].generation == 0
    beat = LocalTurnHeartbeatRequest.model_validate({"message_id": _MID})
    assert beat.message_id == _MID


async def test_abort_deletes_running_pair(monkeypatch):
    deleted: list[tuple[str, str]] = []

    class Repo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, message_id, *, conversation_id):
            return _assistant_ns(usage={"status": "running"})

    async def _delete(**kw):
        deleted.append((kw["assistant_message_id"], kw["user_message_id"]))

    monkeypatch.setattr(local_turn_mod, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(local_turn_mod, "MessageRepository", Repo)
    monkeypatch.setattr(local_turn_mod, "delete_assistant_and_paired_user", _delete)

    result = await abort_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message_id=_UMID,
        message_id=_MID,
    )
    assert result == {"aborted": True}
    assert deleted == [(_MID, _UMID)]


async def test_abort_running_ignores_class_b_content(monkeypatch):
    """Abort deletes a running row even when it has body — no Class B predicate."""
    deleted: list[str] = []

    class Repo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, message_id, *, conversation_id):
            return _assistant_ns(
                usage={"status": "running", "input_tokens": 12},
            )

    async def _delete(**kw):
        deleted.append(kw["assistant_message_id"])

    monkeypatch.setattr(local_turn_mod, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(local_turn_mod, "MessageRepository", Repo)
    monkeypatch.setattr(local_turn_mod, "delete_assistant_and_paired_user", _delete)

    result = await abort_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message_id=_UMID,
        message_id=_MID,
    )
    assert result["aborted"] is True
    assert deleted == [_MID]


@pytest.mark.parametrize(
    "usage",
    [
        {"status": "complete"},
        {"status": "failed"},
        {"status": "incomplete"},
        {"status": "running", "paused": True},
    ],
)
async def test_abort_does_not_delete_settled(monkeypatch, usage):
    class Repo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, message_id, *, conversation_id):
            return _assistant_ns(usage=usage)

    monkeypatch.setattr(local_turn_mod, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(local_turn_mod, "MessageRepository", Repo)
    monkeypatch.setattr(
        local_turn_mod,
        "delete_assistant_and_paired_user",
        AsyncMock(side_effect=AssertionError("must not delete settled")),
    )

    result = await abort_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message_id=_UMID,
        message_id=_MID,
    )
    assert result == {"aborted": False}


async def test_begin_rejects_non_uuid_ids():
    with pytest.raises(PydanticValidationError):
        BeginLocalTurnRequest.model_validate(
            {
                "user_message": "hi",
                "user_message_id": "resume-not-a-uuid",
                "message_id": _MID,
                "trace_id": _TRACE,
            }
        )
    with pytest.raises(PydanticValidationError):
        AbortLocalTurnRequest.model_validate(
            {"user_message_id": _UMID, "message_id": "resume-x"}
        )
