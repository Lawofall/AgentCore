"""Unit tests for the sidecar local-turn write-back (双模式工作区 §十).

``record_local_turn`` routes through ``CloudStore.finalize(mode="local")`` — content +
status + journal, no cost ledger. All DB collaborators are faked (镜像 ``test_handoff_job``).

Covered:

* a full turn persists the user + assistant messages AND the turn journal;
* an empty reply with no process state persists only the user row (noop);
* an empty reply with journal/runs still settles the assistant (+ journal);
* an empty ERROR still settles the assistant row (failed + error_code; content empty);
* paused/running assistant + empty final settles (no ghost noop);
* **no cost ledger is ever written**;
* the user row is pinned to the client-minted id;
* a retried write-back is an idempotent D7 merge upsert (no early-return abandon);
* ``finish_reason=paused`` upserts an assistant snapshot without title / consolidation,
  and still persists the journal snapshot (``team_batch`` / ``obs.turn_spans``);
* resume completion updates a paused snapshot in place;
* a re-pause write-back with a fresh client user id reuses the paired user row;
* a non-UUID ``user_message_id`` (sidecar ``resume-{turn_id}``) does not throw
  and reuses the assistant-paired user row (miss before PG UUID bind);
* unpaired ``resume-*`` is not pinned as ``messages.id`` on create.
* complete / pause / cancel write-backs land a ``turn_metrics`` row;
  ``delegated`` / ``workers`` use ``turn_worker_stats`` (journal ``message_final``);
  tokens match the same finalize fields as ``messages.usage``.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import asyncpg.exceptions
import pytest
from sqlalchemy.exc import DBAPIError

from agentcore.conversation import local_turn as local_turn_mod
from agentcore.conversation.service import record_local_turn
from agentcore.conversation.store import cloud as cloud_mod
from agentcore.conversation.store.cloud import _local_metrics_status
from agentcore.conversation.store.merge import MESSAGE_STATUS_FAILED
from agentcore.conversation.turn_stats import turn_worker_stats
from agentcore.runtime.events import FinishReason
from agentcore.runtime.facts import FactKind
from agentcore.runtime.journal.team_batch import team_batch_from_entries
from agentcore.runtime.runs.types import RunPhase

pytestmark = pytest.mark.anyio

_TRACE = "0123456789abcdef0123456789abcdef"


def _completed_final(run_id: str) -> dict:
    return {
        "kind": FactKind.MESSAGE_FINAL.value,
        "payload": {"run_id": run_id, "phase": RunPhase.COMPLETED.value},
    }


async def test_local_metrics_status_from_settle_facts():
    assert (
        _local_metrics_status(
            is_paused=True,
            terminal_status="running",
            local_outcome=None,
        )
        == "paused"
    )
    assert (
        _local_metrics_status(
            is_paused=False,
            terminal_status=MESSAGE_STATUS_FAILED,
            local_outcome="ok",
        )
        == "error"
    )
    assert (
        _local_metrics_status(
            is_paused=False,
            terminal_status="complete",
            local_outcome="partial",
        )
        == "partial"
    )
    assert (
        _local_metrics_status(
            is_paused=False,
            terminal_status="incomplete",
            local_outcome=None,
        )
        == "ok"
    )


_USER_MSG_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_PINNED_USER_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _pg_uuid_bind_error(message_id: str) -> DBAPIError:
    """Production asyncpg UUID bind failure (not CPython ``uuid.UUID`` ValueError)."""
    orig = asyncpg.exceptions.DataError(
        f"invalid input for query argument $1: {message_id!r}"
    )
    return DBAPIError(
        "SELECT messages.id FROM messages WHERE messages.id = $1::UUID",
        (message_id,),
        orig,
    )


async def test_pg_uuid_bind_error_matches_production_shape():
    """0.6.5 mocked CPython ValueError; production is DBAPIError(asyncpg DataError)."""
    from sqlalchemy.exc import DataError as SADataError

    err = _pg_uuid_bind_error("resume-72b2662b-eec1-4954-af03-941d9d04352a")
    assert type(err) is DBAPIError
    assert isinstance(err.orig, asyncpg.exceptions.DataError)
    assert not isinstance(err, SADataError)
    assert not isinstance(err, ValueError)


class _FakeSession:
    async def rollback(self) -> None:
        pass


class _FakeSessionCM:
    async def __aenter__(self) -> _FakeSession:
        return _FakeSession()

    async def __aexit__(self, *_exc) -> bool:
        return False


def _patch_persistence(
    monkeypatch,
    events: list,
    *,
    existing_title: str | None = None,
    existing_ids: set[str] | None = None,
    existing_usage: dict[str, dict] | None = None,
    existing_content: dict[str, str] | None = None,
    paired_user_by_assistant: dict[str, str] | None = None,
    raise_on_invalid_uuid: bool = False,
    user_create_error: BaseException | None = None,
    harvest_claimed: SimpleNamespace | None = None,
    harvest_following: SimpleNamespace | None = None,
    harvest_in_flight: bool = False,
):
    """Fake CloudStore DB collaborators, recording calls into ``events``."""
    seeded = existing_ids or set()
    usage_by_id = existing_usage or {}
    content_by_id = existing_content or {}
    paired_user_by_assistant: dict[str, str] = paired_user_by_assistant or {}

    class _FakeMsgRepo:
        def __init__(self, _session):
            pass

        async def create(self, **kw):
            if user_create_error is not None:
                raise user_create_error
            role = kw.get("role")
            events.append(("msg", role, kw.get("conversation_id")))
            events.append(("msg_id", role, kw.get("message_id")))
            events.append(("trace", role, kw.get("trace_id")))
            events.append(("user_usage", role, kw.get("metadata")))
            events.append(("mentions", role, kw.get("agent_mentions")))
            return SimpleNamespace(id=f"{role}-id")

        async def upsert_assistant(self, **kw):
            events.append(("upsert", "assistant", kw.get("conversation_id")))
            events.append(("msg_id", "assistant", kw.get("message_id")))
            events.append(("trace", "assistant", kw.get("trace_id")))
            events.append(("usage", "assistant", kw.get("metadata")))
            events.append(("content", "assistant", kw.get("content")))
            return SimpleNamespace(id="assistant-id")

        async def get_by_id(self, message_id, *, conversation_id):
            events.append(("get_by_id", message_id))
            if raise_on_invalid_uuid:
                try:
                    UUID(str(message_id))
                except ValueError:
                    raise _pg_uuid_bind_error(str(message_id)) from None
            if message_id in seeded:
                role = (
                    "assistant"
                    if (
                        str(message_id).startswith("m")
                        or message_id in paired_user_by_assistant
                    )
                    else "user"
                )
                return SimpleNamespace(
                    id=message_id,
                    conversation_id=conversation_id,
                    role=role,
                    usage=usage_by_id.get(message_id),
                    content=content_by_id.get(message_id, ""),
                )
            return None

        async def get_execution_harvest_user(self, *, conversation_id, execution_id):
            return harvest_claimed

        async def get_first_assistant_after(self, *, conversation_id, after, after_id):
            return harvest_following

        async def user_message_for_assistant(self, *, conversation_id, assistant_message_id):
            paired_id = paired_user_by_assistant.get(assistant_message_id)
            if not paired_id:
                return None
            return SimpleNamespace(
                id=paired_id,
                conversation_id=conversation_id,
                role="user",
                usage=None,
            )

        async def set_followups(self, message_id, *, conversation_id, followups):
            events.append(("followups", message_id, list(followups)))

    class _FakeConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id_unscoped(self, _conversation_id):
            return SimpleNamespace(title=existing_title)

        async def update_title_if_empty(self, conversation_id, title):
            if existing_title and str(existing_title).strip():
                return None
            events.append(("title", conversation_id, title))
            return SimpleNamespace(title=title)

        async def update_title_unscoped(self, conversation_id, title):
            events.append(("title_unscoped", conversation_id, title))
            return SimpleNamespace(title=title)

    async def _fake_journal(_session, **kw):
        events.append(("journal", kw.get("message_id")))

    class _FakeMetricsRepo:
        def __init__(self, _session):
            pass

        async def record(self, **kw):
            events.append(("metrics", kw))

    consolidation_calls: list[str] = []

    class _FakeLeaseRepo:
        def __init__(self, _session):
            pass

        async def exists_fresh_for_conversation(self, conversation_id, *, after):
            return harvest_in_flight

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", _FakeMsgRepo)
    monkeypatch.setattr(
        "agentcore.runtime.leases.repo.TurnLeaseRepository",
        _FakeLeaseRepo,
    )
    monkeypatch.setattr(cloud_mod, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", _fake_journal)
    monkeypatch.setattr(cloud_mod, "TurnMetricsRepository", _FakeMetricsRepo)
    monkeypatch.setattr(
        cloud_mod,
        "schedule_consolidation",
        lambda cid: consolidation_calls.append(cid),
    )
    monkeypatch.setattr(cloud_mod, "schedule_compaction_if_due", AsyncMock(return_value=None))
    # Local derived mint (title/followups) now goes through run_background_llm.
    async def _run_bg(user_id, *, purpose="title", runner):
        from agentcore.billing.gate import BackgroundLlmResult
        from agentcore.llm.credentials import LLMCredentials

        creds = LLMCredentials(
            api_key="sk", base_url="https://x", default_model="m", source="platform"
        )
        value = await runner(creds)
        return BackgroundLlmResult(value=value, credentials=creds)

    monkeypatch.setattr(cloud_mod, "run_background_llm", _run_bg)
    monkeypatch.setattr(cloud_mod, "resolve_user_model", lambda *_a, **_k: "m")
    monkeypatch.setattr(
        cloud_mod, "build_provider", lambda *_a, **_k: SimpleNamespace(close=_noop_close)
    )

    from agentcore.memory.conversation_title import TitleResult

    async def _fake_title(**kw):
        events.append(
            (
                "title_mint",
                kw.get("assistant_reply"),
                kw.get("user_message"),
            )
        )
        return TitleResult(title="本地回合标题")

    monkeypatch.setattr(cloud_mod, "mint_title", _fake_title)
    # Keep local_turn import path stable for any residual patches.
    monkeypatch.setattr(local_turn_mod, "get_cloud_store", cloud_mod.get_cloud_store)
    monkeypatch.setattr(
        "agentcore.runtime.kickoff.stage_card.emit_stage_card_for_motion",
        AsyncMock(return_value=None),
    )
    return consolidation_calls


async def _noop_close():
    return None


async def test_record_local_turn_persists_messages_and_journal(monkeypatch):
    events: list = []
    _patch_persistence(monkeypatch, events, existing_title=None)

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="列出本地文件",
        assistant_content="已列出。",
        assistant_reasoning="思考…",
        citations=[{"url": "https://x"}],
        runs={"events": [], "finish_reason": "end_turn"},
        user_message_id=_USER_MSG_ID,
        message_id="m1",
        input_tokens=10,
        output_tokens=4,
        rounds=2,
        trace_id=_TRACE,
    )

    assert ("msg", "user", "c1") in events
    assert ("upsert", "assistant", "c1") in events
    assert ("journal", "assistant-id") in events
    assert ("title", "c1", "本地回合标题") in events
    # Fallback mint uses user message only (align cloud early path).
    assert ("title_mint", "", "列出本地文件") in events
    assert not any(e[0] == "title_unscoped" for e in events)
    assert result["user_message_id"] == "user-id"
    assert result["assistant_message_id"] == "assistant-id"
    assert result["title"] == "本地回合标题"
    usage = next(e for e in events if e[0] == "usage")
    assert usage[2]["status"] == "complete"


async def test_record_local_turn_persists_agent_mentions(monkeypatch):
    events: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")
    mentions = [{"agent_id": "w1", "role": "研究员"}]

    await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="ok",
        user_message_id=_USER_MSG_ID,
        message_id="m-mentions",
        trace_id=_TRACE,
        agent_mentions=mentions,
    )

    created = next(e for e in events if e[0] == "mentions" and e[1] == "user")
    assert created[2] == mentions


async def test_record_local_turn_skips_title_when_inflight(monkeypatch):
    """Desktop auto-title in flight → write-back must not start a second mint."""
    import agentcore.conversation.common as common

    events: list = []
    _patch_persistence(monkeypatch, events, existing_title=None)
    common._title_inflight.add("c-inflight")
    try:
        result = await record_local_turn(
            conversation_id="c-inflight",
            user_id="u1",
            user_message="hi",
            assistant_content="ok",
            runs={"events": [], "finish_reason": "end_turn"},
            user_message_id=_USER_MSG_ID,
            message_id="m-inflight",
            trace_id=_TRACE,
        )
    finally:
        common._title_inflight.discard("c-inflight")

    assert not any(e[0] == "title_mint" for e in events)
    assert not any(e[0] == "title" for e in events)
    assert result["title"] is None


async def test_record_local_turn_skips_title_when_already_named(monkeypatch):
    events: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="ok",
        runs={"events": [], "finish_reason": "end_turn"},
        user_message_id=_USER_MSG_ID,
        message_id="m-named",
        trace_id=_TRACE,
    )

    assert not any(e[0] == "title_mint" for e in events)
    assert not any(e[0] == "title" for e in events)
    assert result["title"] == "已有标题"


async def test_record_local_turn_skips_title_write_when_mint_degrades(monkeypatch):
    """LLM miss must not persist fallback_title — column stays empty for retry."""
    events: list = []
    _patch_persistence(monkeypatch, events, existing_title=None)

    from agentcore.memory.conversation_title import TitleResult

    async def _degraded(**kw):
        events.append(
            ("title_mint", kw.get("assistant_reply"), kw.get("user_message"))
        )
        return TitleResult(title="兜底短标题", degraded_reason="rate_limit")

    monkeypatch.setattr(cloud_mod, "mint_title", _degraded)

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="列出本地文件",
        assistant_content="已列出。",
        runs={"events": [], "finish_reason": "end_turn"},
        user_message_id=_USER_MSG_ID,
        message_id="m-degraded-title",
        trace_id=_TRACE,
    )

    assert ("title_mint", "", "列出本地文件") in events
    assert not any(e[0] == "title" for e in events)
    assert result["title"] is None


async def test_record_local_turn_empty_reply_skips_assistant_and_journal(monkeypatch):
    events: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="",
        user_message_id=_USER_MSG_ID,
        message_id="m2",
        trace_id=_TRACE,
    )

    assert ("msg", "user", "c1") in events
    assert not any(e[0] == "upsert" for e in events)
    assert not any(e[0] == "journal" for e in events)
    assert not any(e[0] == "title" for e in events)  # no title mint
    assert result["assistant_message_id"] is None
    assert result["noop"] is True
    assert result["title"] == "已有标题"  # echo existing title (D7 merge response)


async def test_record_local_turn_paused_then_empty_final_settles(monkeypatch):
    """BUG-4: paused/running row + empty final must settle, not noop (no ghost)."""
    events: list = []
    consolidation = _patch_persistence(
        monkeypatch,
        events,
        existing_title="已有标题",
        existing_ids={_USER_MSG_ID, "m-pause-empty"},
        existing_usage={"m-pause-empty": {"status": "running", "paused": True}},
        existing_content={"m-pause-empty": "partial"},
    )

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="",
        user_message_id=_USER_MSG_ID,
        message_id="m-pause-empty",
        trace_id=_TRACE,
        finish_reason=FinishReason.END_TURN.value,
    )

    assert not any(e[0] == "msg" for e in events)
    assert ("upsert", "assistant", "c1") in events
    usage = next(e for e in events if e[0] == "usage")
    assert usage[2]["status"] == "complete"
    assert "paused" not in usage[2]
    assert result["assistant_message_id"] == "assistant-id"
    assert result["noop"] is False
    assert consolidation == ["c1"]


async def test_record_local_turn_running_then_empty_final_settles(monkeypatch):
    """BUG-4: non-paused running row + empty final must settle too."""
    events: list = []
    _patch_persistence(
        monkeypatch,
        events,
        existing_title="已有标题",
        existing_ids={_USER_MSG_ID, "m-run-empty"},
        existing_usage={"m-run-empty": {"status": "running"}},
        existing_content={"m-run-empty": ""},
    )

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="",
        user_message_id=_USER_MSG_ID,
        message_id="m-run-empty",
        trace_id=_TRACE,
        finish_reason=FinishReason.END_TURN.value,
    )

    assert ("upsert", "assistant", "c1") in events
    usage = next(e for e in events if e[0] == "usage")
    assert usage[2]["status"] == "complete"
    assert result["assistant_message_id"] == "assistant-id"
    assert result["noop"] is False


async def test_record_local_turn_empty_with_journal_settles(monkeypatch):
    """Empty bubble + journal process state must settle (align cloud live)."""
    events: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="",
        journal=[
            {"kind": "run_started", "payload": {"id": "r1"}, "ts": "t0"},
            {"kind": "run_completed", "payload": {"id": "r1"}, "ts": None},
        ],
        user_message_id=_USER_MSG_ID,
        message_id="m-journal",
        trace_id=_TRACE,
        finish_reason="end_turn",
    )

    assert ("upsert", "assistant", "c1") in events
    assert any(e[0] == "journal" for e in events)
    assert result["assistant_message_id"] == "assistant-id"
    assert result["noop"] is False


async def test_record_local_turn_empty_with_runs_settles(monkeypatch):
    """Empty bubble + runs display payload must settle assistant + journal."""
    events: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="",
        runs={
            "events": [{"type": "run_started", "run_id": "r1"}],
            "finish_reason": "end_turn",
            "process": [{"kind": "team", "label": "调研"}],
        },
        user_message_id=_USER_MSG_ID,
        message_id="m-runs",
        trace_id=_TRACE,
        finish_reason="end_turn",
    )

    assert ("upsert", "assistant", "c1") in events
    assert result["assistant_message_id"] == "assistant-id"
    assert result["noop"] is False


async def test_record_local_turn_empty_error_settles_assistant(monkeypatch):
    """Empty ERROR still upserts failed + error_code; content stays empty."""
    from agentcore.core.error_codes import ErrorCode

    events: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="",
        runs={
            "events": [],
            "finish_reason": "error",
            "error": {"code": ErrorCode.LLM_TIMEOUT, "message": "超时"},
        },
        user_message_id=_USER_MSG_ID,
        message_id="m-err",
        trace_id=_TRACE,
        finish_reason="error",
    )

    assert ("upsert", "assistant", "c1") in events
    usage = next(e for e in events if e[0] == "usage")
    assert usage[2]["status"] == "failed"
    assert usage[2]["error_code"] == ErrorCode.LLM_TIMEOUT
    assert usage[2]["error"] == {"code": ErrorCode.LLM_TIMEOUT, "message": "超时"}
    content = next(e for e in events if e[0] == "content")
    assert content[2] == ""
    assert result["assistant_message_id"] == "assistant-id"
    assert result["noop"] is False
    metrics = next(e[1] for e in events if e[0] == "metrics")
    assert metrics["status"] == "error"
    assert metrics["finish_reason"] == "error"
    assert metrics["input_tokens"] == 0
    assert metrics["output_tokens"] == 0


async def test_record_local_turn_records_no_cost_ledger(monkeypatch):
    events: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="ok",
        runs={"events": [], "finish_reason": "end_turn"},
        user_message_id=_USER_MSG_ID,
        message_id="m3",
        input_tokens=99,
        output_tokens=42,
        trace_id=_TRACE,
    )

    assert ("upsert", "assistant", "c1") in events
    assert ("journal", "assistant-id") in events
    assert result["assistant_message_id"] == "assistant-id"


async def test_record_local_turn_pins_user_row_to_client_id(monkeypatch):
    events: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="ok",
        user_message_id=_PINNED_USER_ID,
        message_id="m9",
        trace_id=_TRACE,
    )

    assert ("msg_id", "user", _PINNED_USER_ID) in events
    assert ("msg_id", "assistant", "m9") in events


async def test_record_local_turn_reuses_client_trace_id(monkeypatch):
    events: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="ok",
        user_message_id=_USER_MSG_ID,
        message_id="m1",
        trace_id="0123456789abcdef0123456789abcdef",
    )

    assert ("trace", "assistant", "0123456789abcdef0123456789abcdef") in events


async def test_record_local_turn_retry_is_idempotent_merge(monkeypatch):
    """D7: existing rows → merge upsert (not early-return abandon)."""
    events: list = []
    _patch_persistence(
        monkeypatch,
        events,
        existing_title="已有标题",
        existing_ids={_PINNED_USER_ID, "m9"},
        existing_usage={"m9": {"status": "complete", "input_tokens": 1}},
        existing_content={"m9": "ok"},
    )

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="ok",
        user_message_id=_PINNED_USER_ID,
        message_id="m9",
        trace_id=_TRACE,
    )

    assert not any(e[0] == "msg" for e in events)  # no duplicate user create
    assert ("upsert", "assistant", "c1") in events  # merge upsert, not early return
    assert not any(e[0] == "title" for e in events)
    assert result["user_message_id"] == _PINNED_USER_ID
    assert result["assistant_message_id"] == "assistant-id"
    assert result["title"] == "已有标题"


async def test_record_local_turn_paused_skips_title_and_consolidation(monkeypatch):
    events: list = []
    consolidation = _patch_persistence(monkeypatch, events, existing_title=None)

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="partial",
        user_message_id=_USER_MSG_ID,
        message_id="m-pause",
        trace_id=_TRACE,
        finish_reason=FinishReason.PAUSED.value,
    )

    assert ("msg", "user", "c1") in events
    assert ("upsert", "assistant", "c1") in events
    assert (
        "usage",
        "assistant",
        {
            "status": "running",
            "paused": True,
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
            "rounds": 0,
        },
    ) in events
    assert not any(e[0] == "journal" for e in events)
    assert not any(e[0] == "title" for e in events)
    assert consolidation == []
    assert result["title"] is None


async def test_record_local_turn_paused_persists_in_flight_journal(monkeypatch):
    """Sidecar pause snapshots journal like cloud ``save_paused_turn``.

    ``ask_user`` / ``plan_review`` after ``run_started`` must stay ``in_flight``
    on GET messages — empty journal would project ``no_batch``.
    """
    events: list = []
    journal_entries: list = []
    consolidation = _patch_persistence(monkeypatch, events, existing_title="已有标题")

    async def _capture_journal(_session, **kw):
        events.append(("journal", kw.get("message_id")))
        journal_entries.append(kw.get("entries"))

    monkeypatch.setattr(cloud_mod, "persist_turn_journal", _capture_journal)

    facts = [
        {
            "kind": "run_plan",
            "payload": {
                "execution_id": "e1",
                "runs": [
                    {"id": "cap", "kind": "captain"},
                    {"id": "w1", "kind": "agent"},
                ],
            },
            "ts": "t0",
        },
        {"kind": "run_started", "payload": {"run_id": "w1", "kind": "agent"}, "ts": "t1"},
        {"kind": "checkpoint_required", "payload": {"id": "cp"}, "ts": "t2"},
    ]
    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="要继续吗？",
        journal=facts,
        user_message_id=_USER_MSG_ID,
        message_id="m-pause-inflight",
        trace_id=_TRACE,
        finish_reason=FinishReason.PAUSED.value,
    )

    assert ("upsert", "assistant", "c1") in events
    assert ("journal", "assistant-id") in events
    assert journal_entries == [facts]
    assert team_batch_from_entries(journal_entries[0]) == {
        "kind": "in_flight",
        "worker_count": 1,
    }
    assert not any(e[0] == "title" for e in events)
    assert consolidation == []
    assert result["title"] is None


async def test_record_local_turn_paused_preview_journal_is_no_batch(monkeypatch):
    """Kickoff preview (plan, no ``run_started``) still projects ``no_batch`` after persist."""
    events: list = []
    journal_entries: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    async def _capture_journal(_session, **kw):
        events.append(("journal", kw.get("message_id")))
        journal_entries.append(kw.get("entries"))

    monkeypatch.setattr(cloud_mod, "persist_turn_journal", _capture_journal)

    facts = [
        {
            "kind": "run_plan",
            "payload": {
                "execution_id": "e1",
                "runs": [
                    {"id": "cap", "kind": "captain"},
                    {"id": "w1", "kind": "agent"},
                ],
            },
            "ts": "t0",
        },
        {"kind": "team_preview_required", "payload": {"id": "tp"}, "ts": "t1"},
    ]
    await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="开工前确认编制",
        journal=facts,
        user_message_id=_USER_MSG_ID,
        message_id="m-pause-preview",
        trace_id=_TRACE,
        finish_reason=FinishReason.PAUSED.value,
    )

    assert ("journal", "assistant-id") in events
    assert journal_entries == [facts]
    assert team_batch_from_entries(journal_entries[0]) == {"kind": "no_batch"}
    metrics = next(e[1] for e in events if e[0] == "metrics")
    assert metrics["delegated"] is False
    assert metrics["workers"] == 0
    assert metrics["status"] == "paused"
    assert metrics["mode"] == "local"


async def test_record_local_turn_writes_metrics_on_complete(monkeypatch):
    """Sidecar complete lands turn_metrics with the same tokens as messages.usage."""
    events: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="done",
        runs={"events": [], "finish_reason": "end_turn", "duration_ms": 1200},
        user_message_id=_USER_MSG_ID,
        message_id="m-metrics-ok",
        input_tokens=99,
        output_tokens=42,
        rounds=3,
        trace_id=_TRACE,
        finish_reason=FinishReason.END_TURN.value,
    )

    metrics = next(e[1] for e in events if e[0] == "metrics")
    assert metrics["turn_id"] == "assistant-id"
    assert metrics["conversation_id"] == "c1"
    assert metrics["user_id"] == "u1"
    assert metrics["trace_id"] == _TRACE
    assert metrics["kind"] == "turn"
    assert metrics["status"] == "ok"
    assert metrics["finish_reason"] == FinishReason.END_TURN.value
    assert metrics["rounds"] == 3
    assert metrics["duration_ms"] == 1200
    assert metrics["delegated"] is False
    assert metrics["workers"] == 0
    assert metrics["mode"] == "local"
    assert metrics["input_tokens"] == 99
    assert metrics["output_tokens"] == 42


async def test_record_local_turn_metrics_follows_turn_worker_stats(monkeypatch):
    """delegated / workers use turn_worker_stats (journal message_final), not team_batch."""
    events: list = []
    journal_entries: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    async def _capture_journal(_session, **kw):
        events.append(("journal", kw.get("message_id")))
        journal_entries.append(kw.get("entries"))

    monkeypatch.setattr(cloud_mod, "persist_turn_journal", _capture_journal)

    facts = [
        {
            "kind": "run_plan",
            "payload": {
                "execution_id": "e1",
                "runs": [
                    {"id": "cap", "kind": "captain"},
                    {"id": "w1", "kind": "agent"},
                    {"id": "w2", "kind": "agent"},
                ],
            },
            "ts": "t0",
        },
        {"kind": "run_started", "payload": {"run_id": "w1", "kind": "agent"}, "ts": "t1"},
        {"kind": "run_started", "payload": {"run_id": "w2", "kind": "agent"}, "ts": "t2"},
        {"kind": "run_completed", "payload": {"run_id": "w1"}, "ts": "t3"},
        {"kind": "run_completed", "payload": {"run_id": "w2"}, "ts": "t4"},
        _completed_final("w1"),
        _completed_final("w2"),
    ]
    await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="团队收工",
        journal=facts,
        user_message_id=_USER_MSG_ID,
        message_id="m-metrics-batch",
        trace_id=_TRACE,
        finish_reason=FinishReason.END_TURN.value,
    )

    batch = team_batch_from_entries(journal_entries[0])
    assert batch == {"kind": "settled", "worker_count": 2}
    metrics = next(e[1] for e in events if e[0] == "metrics")
    assert turn_worker_stats({"journal_entries": facts}) == (True, 2)
    assert metrics["delegated"] is True
    assert metrics["workers"] == 2
    assert metrics["mode"] == "local"


async def test_record_local_turn_paused_writes_metrics(monkeypatch):
    events: list = []
    journal_entries: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    async def _capture_journal(_session, **kw):
        events.append(("journal", kw.get("message_id")))
        journal_entries.append(kw.get("entries"))

    monkeypatch.setattr(cloud_mod, "persist_turn_journal", _capture_journal)

    facts = [
        {
            "kind": "run_plan",
            "payload": {
                "execution_id": "e1",
                "runs": [
                    {"id": "cap", "kind": "captain"},
                    {"id": "w1", "kind": "agent"},
                ],
            },
            "ts": "t0",
        },
        {"kind": "run_started", "payload": {"run_id": "w1", "kind": "agent"}, "ts": "t1"},
        {"kind": "checkpoint_required", "payload": {"id": "cp"}, "ts": "t2"},
    ]
    await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="要继续吗？",
        journal=facts,
        user_message_id=_USER_MSG_ID,
        message_id="m-metrics-pause",
        input_tokens=50,
        output_tokens=10,
        trace_id=_TRACE,
        finish_reason=FinishReason.PAUSED.value,
    )

    batch = team_batch_from_entries(journal_entries[0])
    assert batch == {"kind": "in_flight", "worker_count": 1}
    metrics = next(e[1] for e in events if e[0] == "metrics")
    assert metrics["status"] == "paused"
    assert metrics["finish_reason"] == FinishReason.PAUSED.value
    assert metrics["kind"] == "turn"
    # In-flight plan is team_batch, not completed members — same as cloud pause.
    assert turn_worker_stats({"journal_entries": facts}) == (False, 0)
    assert metrics["delegated"] is False
    assert metrics["workers"] == 0
    assert metrics["mode"] == "local"
    assert metrics["input_tokens"] == 50
    assert metrics["output_tokens"] == 10


async def test_record_local_turn_cancelled_writes_metrics(monkeypatch):
    events: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    facts = [
        {"kind": "run_started", "payload": {"id": "r1"}, "ts": "t0"},
        {"kind": "run_completed", "payload": {"id": "r1"}, "ts": None},
    ]
    await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="partial reply",
        runs=None,
        journal=facts,
        user_message_id=_USER_MSG_ID,
        message_id="m-metrics-cancel",
        trace_id=_TRACE,
        finish_reason=FinishReason.CANCELLED.value,
    )

    metrics = next(e[1] for e in events if e[0] == "metrics")
    assert metrics["status"] == "ok"
    assert metrics["finish_reason"] == FinishReason.CANCELLED.value
    assert metrics["kind"] == "turn"
    assert metrics["delegated"] is False
    assert metrics["workers"] == 0
    assert metrics["mode"] == "local"


async def test_record_local_turn_empty_reply_skips_metrics(monkeypatch):
    events: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="",
        user_message_id=_USER_MSG_ID,
        message_id="m-metrics-noop",
        trace_id=_TRACE,
    )

    assert not any(e[0] == "metrics" for e in events)


async def test_record_local_turn_resume_after_pause_updates_assistant(monkeypatch):
    events: list = []
    consolidation = _patch_persistence(
        monkeypatch,
        events,
        existing_title=None,
        existing_ids={_USER_MSG_ID, "m-pause"},
        existing_usage={"m-pause": {"status": "running", "paused": True}},
        existing_content={"m-pause": "partial"},
    )

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="done",
        runs={"events": [], "finish_reason": "end_turn"},
        user_message_id=_USER_MSG_ID,
        message_id="m-pause",
        trace_id=_TRACE,
        finish_reason=FinishReason.END_TURN.value,
    )

    assert not any(e[0] == "msg" for e in events)
    assert ("upsert", "assistant", "c1") in events
    assert ("journal", "assistant-id") in events
    assert ("title", "c1", "本地回合标题") in events
    assert consolidation == ["c1"]
    assert result["assistant_message_id"] == "assistant-id"
    usage = next(e for e in events if e[0] == "usage")
    assert usage[2]["status"] == "complete"
    assert "paused" not in usage[2]
    metrics = next(e[1] for e in events if e[0] == "metrics")
    assert metrics["kind"] == "resume"
    assert metrics["status"] == "ok"


async def test_record_local_turn_non_uuid_umid_reuses_paired_user(monkeypatch):
    """Legacy sidecar ``resume-{turn_id}`` must not throw; pair via assistant id.

    Production ``Message.id`` is a PG UUID column. asyncpg raises
    ``DataError`` at bind; SQLAlchemy wraps it as ``DBAPIError`` (not
    ``sqlalchemy.exc.DataError``). The consume path must miss *before* bind.
    """
    events: list = []
    assistant_id = "11111111-1111-4111-8111-111111111111"
    paired_user_id = "22222222-2222-4222-8222-222222222222"
    resume_umid = f"resume-{assistant_id}"
    assert len(resume_umid) == 43
    _patch_persistence(
        monkeypatch,
        events,
        existing_title="已有标题",
        existing_ids={assistant_id},
        existing_usage={assistant_id: {"status": "running", "paused": True}},
        existing_content={assistant_id: "partial"},
        paired_user_by_assistant={assistant_id: paired_user_id},
        raise_on_invalid_uuid=True,
    )

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="原始问题",
        assistant_content="续跑完成",
        journal=[{"kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": "t1"}],
        user_message_id=resume_umid,
        message_id=assistant_id,
        trace_id=_TRACE,
        finish_reason=FinishReason.END_TURN.value,
    )

    assert not any(e[0] == "get_by_id" and e[1] == resume_umid for e in events)
    assert not any(e[0] == "msg" and e[1] == "user" for e in events)
    assert not any(e[0] == "msg_id" and e[1] == "user" and e[2] == resume_umid for e in events)
    assert ("upsert", "assistant", "c1") in events
    assert any(e[0] == "journal" for e in events)
    assert result["user_message_id"] == paired_user_id
    assert result["assistant_message_id"] == "assistant-id"
    assert result["noop"] is False


async def test_record_local_turn_non_uuid_umid_create_drops_illegal_pin(monkeypatch):
    """Unpaired ``resume-*`` must not be used as ``messages.id`` on create."""
    events: list = []
    resume_umid = "resume-72b2662b-eec1-4954-af03-941d9d04352a"
    assistant_id = "33333333-3333-4333-8333-333333333333"
    _patch_persistence(monkeypatch, events, raise_on_invalid_uuid=True)

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="原始问题",
        assistant_content="续跑完成",
        user_message_id=resume_umid,
        message_id=assistant_id,
        trace_id=_TRACE,
        finish_reason=FinishReason.END_TURN.value,
    )

    assert not any(e[0] == "get_by_id" and e[1] == resume_umid for e in events)
    assert ("msg", "user", "c1") in events
    user_pin = next(e for e in events if e[0] == "msg_id" and e[1] == "user")
    assert user_pin[2] is None
    assert result["assistant_message_id"] == "assistant-id"
    assert result["noop"] is False


async def test_record_local_turn_repause_reuses_paired_user_row(monkeypatch):
    events: list = []
    _patch_persistence(
        monkeypatch,
        events,
        existing_title=None,
        existing_ids={"m-pause"},
        existing_usage={"m-pause": {"status": "running", "paused": True}},
        existing_content={"m-pause": "partial"},
        paired_user_by_assistant={"m-pause": _USER_MSG_ID},
    )

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="partial again",
        user_message_id="fresh-client-user-id",
        message_id="m-pause",
        trace_id=_TRACE,
        finish_reason=FinishReason.PAUSED.value,
    )

    assert not any(e[0] == "msg" for e in events)
    assert ("upsert", "assistant", "c1") in events
    assert result["user_message_id"] == _USER_MSG_ID


async def test_record_local_turn_cancelled_incomplete_persists_journal(monkeypatch):
    """Salvage write-back: cancelled → incomplete + suffix; raw journal when runs missing."""
    events: list = []
    journal_entries: list = []

    consolidation = _patch_persistence(monkeypatch, events, existing_title="已有标题")

    async def _capture_journal(_session, **kw):
        events.append(("journal", kw.get("message_id")))
        journal_entries.append(kw.get("entries"))

    monkeypatch.setattr(cloud_mod, "persist_turn_journal", _capture_journal)

    facts = [
        {"kind": "run_started", "payload": {"id": "r1"}, "ts": "t0"},
        {"kind": "run_completed", "payload": {"id": "r1"}, "ts": None},
    ]
    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="partial reply",
        runs=None,
        journal=facts,
        user_message_id=_USER_MSG_ID,
        message_id="m-salvage",
        trace_id=_TRACE,
        finish_reason=FinishReason.CANCELLED.value,
    )

    assert ("upsert", "assistant", "c1") in events
    usage = next(e for e in events if e[0] == "usage")
    assert usage[2]["status"] == "incomplete"
    assert usage[2]["incomplete"] is True
    assert usage[2]["finish_reason"] == "cancelled"
    assert ("journal", "assistant-id") in events
    assert journal_entries == [facts]
    assert not any(e[0] == "title" for e in events)
    assert not any(e[0] == "followups" for e in events)
    assert consolidation == []
    assert result["title"] is None


async def test_record_local_turn_prefers_progressive_journal_over_runs(monkeypatch):
    """Local finalize: non-empty progressive journal is sole fact source (not runs 投影)."""
    events: list = []
    journal_entries: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    async def _capture_journal(_session, **kw):
        events.append(("journal", kw.get("message_id")))
        journal_entries.append(kw.get("entries"))

    monkeypatch.setattr(cloud_mod, "persist_turn_journal", _capture_journal)

    progressive = [
        {"kind": "llm_call", "payload": {"model": "m", "round": 1}, "ts": "t0"},
        {
            "kind": "run_completed",
            "payload": {"run_id": "r1", "agent_id": "w1", "output_summary": "done"},
            "ts": "t1",
        },
    ]
    # Display runs omit execution-only facts (llm_call) — old path would drop them.
    display_runs = {
        "events": [
            {"type": "run_started", "payload": {"run_id": "r1", "agent_id": "w1"}},
            {
                "type": "run_completed",
                "payload": {"run_id": "r1", "agent_id": "w1", "output_summary": "done"},
            },
        ],
        "finish_reason": "end_turn",
    }
    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="ok",
        runs=display_runs,
        journal=progressive,
        user_message_id=_USER_MSG_ID,
        message_id="m-prog-journal",
        trace_id=_TRACE,
        finish_reason=FinishReason.END_TURN.value,
    )

    assert ("journal", "assistant-id") in events
    assert journal_entries == [progressive]
    assert result["assistant_message_id"] == "assistant-id"


async def test_record_local_turn_does_not_persist_followups(monkeypatch):
    events: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="ok reply",
        runs={"events": [], "finish_reason": "end_turn"},
        user_message_id=_USER_MSG_ID,
        message_id="m-fu",
        trace_id=_TRACE,
        finish_reason=FinishReason.END_TURN.value,
    )

    assert not any(e[0] == "followups" for e in events)
    assert result["followups"] is None


async def test_record_local_turn_empty_um_with_journal_skips_user_create(monkeypatch):
    """ffafc42b: empty um + process settles assistant/journal; no visible user row."""
    events: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="",
        assistant_content="",
        journal=[
            {"kind": "run_started", "payload": {"id": "r1"}, "ts": "t0"},
            {"kind": "run_completed", "payload": {"id": "r1"}, "ts": None},
        ],
        user_message_id=_USER_MSG_ID,
        message_id="m-empty-um",
        trace_id=_TRACE,
        finish_reason=FinishReason.CANCELLED.value,
    )

    assert not any(e[0] == "msg" and e[1] == "user" for e in events)
    assert ("upsert", "assistant", "c1") in events
    assert any(e[0] == "journal" for e in events)
    assert result["assistant_message_id"] == "assistant-id"
    assert result["user_message_id"] == _USER_MSG_ID
    assert result["noop"] is False


async def test_record_local_turn_placeholder_um_skips_user_create(monkeypatch):
    """Legacy ``[local-turn recovery]`` must not become a new user bubble."""
    from agentcore.conversation.store.cloud import LOCAL_TURN_RECOVERY_PLACEHOLDER

    events: list = []
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message=LOCAL_TURN_RECOVERY_PLACEHOLDER,
        assistant_content="",
        journal=[{"kind": "run_started", "payload": {"id": "r1"}, "ts": "t0"}],
        user_message_id=_USER_MSG_ID,
        message_id="m-placeholder",
        trace_id=_TRACE,
        finish_reason=FinishReason.CANCELLED.value,
    )

    assert not any(e[0] == "msg" and e[1] == "user" for e in events)
    assert ("upsert", "assistant", "c1") in events
    assert result["assistant_message_id"] == "assistant-id"


async def test_record_local_turn_placeholder_umid_eq_message_id_skips_user(
    monkeypatch,
):
    """Dirty sample: umid≈message_id + placeholder must not insert a user row."""
    from agentcore.conversation.store.cloud import LOCAL_TURN_RECOVERY_PLACEHOLDER

    events: list = []
    collision_id = "4bb278a6-5557-4c70-a01e-0b77305b7aca"
    _patch_persistence(monkeypatch, events, existing_title="已有标题")

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message=LOCAL_TURN_RECOVERY_PLACEHOLDER,
        assistant_content="",
        journal=[{"kind": "run_started", "payload": {"id": "r1"}, "ts": "t0"}],
        user_message_id=collision_id,
        message_id=collision_id,
        trace_id=_TRACE,
        finish_reason=FinishReason.CANCELLED.value,
    )

    assert not any(e[0] == "msg" and e[1] == "user" for e in events)
    assert ("upsert", "assistant", "c1") in events
    assert any(e[0] == "journal" for e in events)
    assert result["assistant_message_id"] == "assistant-id"
    assert result["noop"] is False


async def test_record_local_turn_placeholder_still_reuses_paired_user(monkeypatch):
    """Paired-user reuse remains when assistant exists; placeholder ignored."""
    from agentcore.conversation.store.cloud import LOCAL_TURN_RECOVERY_PLACEHOLDER

    events: list = []
    _patch_persistence(
        monkeypatch,
        events,
        existing_title="已有标题",
        existing_ids={"m-pause"},
        existing_usage={"m-pause": {"status": "running", "paused": True}},
        existing_content={"m-pause": "partial"},
        paired_user_by_assistant={"m-pause": _USER_MSG_ID},
    )

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message=LOCAL_TURN_RECOVERY_PLACEHOLDER,
        assistant_content="partial again",
        user_message_id="fresh-client-user-id",
        message_id="m-pause",
        trace_id=_TRACE,
        finish_reason=FinishReason.PAUSED.value,
    )

    assert not any(e[0] == "msg" for e in events)
    assert ("upsert", "assistant", "c1") in events
    assert result["user_message_id"] == _USER_MSG_ID


async def test_record_local_turn_writes_harvest_origin_on_user_usage(monkeypatch):
    """local-turns stamps synthetic harvest user ``usage.origin`` and skips title mint."""
    events: list = []
    _patch_persistence(monkeypatch, events, existing_title=None)

    result = await record_local_turn(
        conversation_id="c-harvest",
        user_id="u1",
        user_message="【系统收口】后台团队任务已全部完成。",
        assistant_content="终稿。",
        user_message_id=_USER_MSG_ID,
        message_id="m-harvest",
        input_tokens=1,
        output_tokens=2,
        rounds=1,
        trace_id=_TRACE,
        origin="execution_harvest",
        execution_id="exec-1",
        harvest_kind="success",
    )

    usage = next(e for e in events if e[0] == "user_usage" and e[1] == "user")
    assert usage[2]["origin"] == "execution_harvest"
    assert usage[2]["execution_id"] == "exec-1"
    assert usage[2]["harvest_kind"] == "success"
    assert not any(e[0] == "title_mint" for e in events)
    assert result["assistant_message_id"] == "assistant-id"


async def test_record_local_turn_strips_harvest_origin_for_title_skip(monkeypatch):
    events: list = []
    _patch_persistence(monkeypatch, events, existing_title=None)

    await record_local_turn(
        conversation_id="c-harvest-ws",
        user_id="u1",
        user_message="【系统收口】后台团队任务已全部完成。",
        assistant_content="终稿。",
        user_message_id=_USER_MSG_ID,
        message_id="m-harvest-ws",
        input_tokens=1,
        output_tokens=2,
        rounds=1,
        trace_id=_TRACE,
        origin="  execution_harvest  ",
        execution_id="  exec-1  ",
        harvest_kind="  success  ",
    )

    usage = next(e for e in events if e[0] == "user_usage" and e[1] == "user")
    assert usage[2]["origin"] == "execution_harvest"
    assert usage[2]["execution_id"] == "exec-1"
    assert usage[2]["harvest_kind"] == "success"
    assert not any(e[0] == "title_mint" for e in events)


async def test_record_local_turn_skips_settled_harvest_execution(monkeypatch):
    """Claim + settled assistant → do not write a second closing draft."""
    from sqlalchemy.exc import IntegrityError

    from agentcore.db.models.conversations import UQ_MESSAGES_EXECUTION_HARVEST

    events: list = []
    _patch_persistence(
        monkeypatch,
        events,
        existing_title="已有标题",
        user_create_error=IntegrityError(
            "INSERT", {}, Exception(UQ_MESSAGES_EXECUTION_HARVEST)
        ),
        harvest_claimed=SimpleNamespace(
            id="user-harvest-existing",
            created_at="t0",
        ),
        harvest_following=SimpleNamespace(id="asst-done", usage={"status": "complete"}),
    )

    result = await record_local_turn(
        conversation_id="c-harvest-dup",
        user_id="u1",
        user_message="【系统收口】后台团队任务已全部完成。",
        assistant_content="第二份终稿不应落库。",
        user_message_id="user-harvest-dup",
        message_id="m-harvest-dup",
        input_tokens=1,
        output_tokens=2,
        rounds=1,
        trace_id=_TRACE,
        origin="execution_harvest",
        execution_id="exec-1",
        harvest_kind="success",
    )

    assert result["noop"] is True
    assert result["assistant_message_id"] is None
    assert not any(e[0] == "upsert" for e in events)


async def test_record_local_turn_harvest_conflict_still_writes_assistant(monkeypatch):
    """Crash after the harvest user row still persists the closing assistant."""
    from sqlalchemy.exc import IntegrityError

    from agentcore.db.models.conversations import UQ_MESSAGES_EXECUTION_HARVEST

    events: list = []
    _patch_persistence(
        monkeypatch,
        events,
        existing_title="已有标题",
        user_create_error=IntegrityError(
            "INSERT", {}, Exception(UQ_MESSAGES_EXECUTION_HARVEST)
        ),
        harvest_claimed=SimpleNamespace(
            id="user-harvest-existing",
            created_at="t0",
        ),
    )

    result = await record_local_turn(
        conversation_id="c-harvest-retry",
        user_id="u1",
        user_message="【系统收口】后台团队任务已全部完成。",
        assistant_content="终稿。",
        user_message_id="user-harvest-retry",
        message_id="m-harvest-retry",
        input_tokens=1,
        output_tokens=2,
        rounds=1,
        trace_id=_TRACE,
        origin="execution_harvest",
        execution_id="exec-1",
        harvest_kind="success",
    )

    assert result["assistant_message_id"] == "assistant-id"
    assert any(e[0] == "upsert" for e in events)


async def test_record_local_turn_harvest_pk_race_still_settles(monkeypatch):
    """Same user_message_id retry is not a harvest-execution conflict."""
    from sqlalchemy.exc import IntegrityError

    events: list = []
    _patch_persistence(
        monkeypatch,
        events,
        existing_title="已有标题",
        user_create_error=IntegrityError("INSERT", {}, Exception("messages_pkey")),
    )

    result = await record_local_turn(
        conversation_id="c-harvest-pk",
        user_id="u1",
        user_message="【系统收口】后台团队任务已全部完成。",
        assistant_content="终稿。",
        user_message_id="user-harvest-pk",
        message_id="m-harvest-pk",
        input_tokens=1,
        output_tokens=2,
        rounds=1,
        trace_id=_TRACE,
        origin="execution_harvest",
        execution_id="exec-1",
        harvest_kind="success",
    )

    assert result["assistant_message_id"] == "assistant-id"
    assert any(e[0] == "upsert" for e in events)
