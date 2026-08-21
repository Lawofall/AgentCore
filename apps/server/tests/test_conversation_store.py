"""ConversationStore Protocol + D7 merge-rule unit tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from agentcore.conversation.store import CloudStore, get_cloud_store
from agentcore.conversation.store import cloud as cloud_mod
from agentcore.conversation.store.merge import (
    MESSAGE_STATUS_COMPLETE,
    MESSAGE_STATUS_FAILED,
    MESSAGE_STATUS_INCOMPLETE,
    MESSAGE_STATUS_RUNNING,
    merge_usage_status,
    pick_merged_content,
    pick_monotonic_content,
    should_advance_status,
    status_rank,
    visible_failed_assistant_content,
)
from agentcore.folders.placement import FolderPlacement
from agentcore.runtime.events import FinishReason
from agentcore.runtime.ports import ConversationStore

pytestmark = pytest.mark.anyio


class _NoopMetricsRepo:
    def __init__(self, _s):
        pass

    async def record(self, **_kw):
        return None


# --- D7 pure helpers ---


def test_d7_status_gate_only_advances():
    assert should_advance_status(MESSAGE_STATUS_RUNNING, MESSAGE_STATUS_COMPLETE)
    assert should_advance_status(MESSAGE_STATUS_RUNNING, MESSAGE_STATUS_FAILED)
    assert not should_advance_status(MESSAGE_STATUS_COMPLETE, MESSAGE_STATUS_RUNNING)
    assert not should_advance_status(MESSAGE_STATUS_COMPLETE, MESSAGE_STATUS_INCOMPLETE)
    assert status_rank(MESSAGE_STATUS_COMPLETE) > status_rank(MESSAGE_STATUS_RUNNING)


def test_d7_merge_usage_status_keeps_terminal():
    merged = merge_usage_status(
        {"status": MESSAGE_STATUS_COMPLETE, "input_tokens": 10},
        {"status": MESSAGE_STATUS_RUNNING, "input_tokens": 12},
    )
    assert merged["status"] == MESSAGE_STATUS_COMPLETE
    assert merged["input_tokens"] == 12


def test_merge_usage_keeps_interrupt_chrome_when_stamping_tokens():
    """Ledger token backfill must not drop user_stop incomplete chrome."""
    merged = merge_usage_status(
        {
            "status": MESSAGE_STATUS_INCOMPLETE,
            "incomplete": True,
            "finish_reason": "cancelled",
            "interrupt_reason": "user_stop",
            "input_tokens": 400_000,
        },
        {
            "input_tokens": 20_405_391,
            "output_tokens": 355_915,
            "reasoning_tokens": 150,
            "cache_hit_tokens": 16_728_448,
            "cache_miss_tokens": 3_676_943,
        },
    )
    assert merged["status"] == MESSAGE_STATUS_INCOMPLETE
    assert merged["incomplete"] is True
    assert merged["finish_reason"] == "cancelled"
    assert merged["interrupt_reason"] == "user_stop"
    assert merged["input_tokens"] == 20_405_391
    assert merged["output_tokens"] == 355_915
    assert merged["cache_hit_tokens"] == 16_728_448


def test_d7_merge_usage_clears_paused_on_terminal():
    """终态必非暂停：二次 merge 不得从 existing 复活 paused latch。"""
    paused_running = {
        "status": MESSAGE_STATUS_RUNNING,
        "paused": True,
        "input_tokens": 1,
    }
    terminal = {
        "status": MESSAGE_STATUS_COMPLETE,
        "input_tokens": 2,
        "rounds": 4,
    }
    # First merge (finalize) + second merge (upsert_assistant merge=True) both clear.
    once = merge_usage_status(paused_running, terminal)
    assert once["status"] == MESSAGE_STATUS_COMPLETE
    assert "paused" not in once
    twice = merge_usage_status(paused_running, once)
    assert twice["status"] == MESSAGE_STATUS_COMPLETE
    assert "paused" not in twice


def test_d7_merge_usage_keeps_paused_while_running():
    merged = merge_usage_status(
        {"status": MESSAGE_STATUS_RUNNING},
        {"status": MESSAGE_STATUS_RUNNING, "paused": True},
    )
    assert merged["status"] == MESSAGE_STATUS_RUNNING
    assert merged["paused"] is True


def test_d7_merge_usage_clears_paused_on_explicit_false_while_running():
    """Resume continuation writes paused:false while still running → latch cleared."""
    paused_running = {
        "status": MESSAGE_STATUS_RUNNING,
        "paused": True,
        "input_tokens": 1,
    }
    resumed = {"status": MESSAGE_STATUS_RUNNING, "paused": False, "input_tokens": 2}
    merged = merge_usage_status(paused_running, resumed)
    assert merged["status"] == MESSAGE_STATUS_RUNNING
    assert "paused" not in merged
    assert merged["input_tokens"] == 2


def test_hardkill_harvest_empty_close_only_fills_unstamped_harvest_prefix():
    """Leg 2: hard-kill harvest (prefix, no origin) gets the same compose as salvage."""
    from agentcore.conversation.store.cloud import _compose_hardkill_harvest_empty_close
    from agentcore.runtime.turn.interrupt import INTERRUPTED_EMPTY_USER_VISIBLE

    filled = _compose_hardkill_harvest_empty_close(
        user_message="【系统收口】后台团队任务已全部完成。",
        origin=None,
    )
    assert filled == INTERRUPTED_EMPTY_USER_VISIBLE
    assert "已完成" not in filled
    assert "已交付" not in filled

    # Live salvage already stamped origin — USER_STOP silence / composed body stay.
    assert (
        _compose_hardkill_harvest_empty_close(
            user_message="【系统收口】后台团队任务已全部完成。",
            origin="execution_harvest",
        )
        == ""
    )
    assert (
        _compose_hardkill_harvest_empty_close(
            user_message="【系统收口】",
            origin=None,
            harvest_kind="success",
        )
        == ""
    )
    # Ordinary startTurn empty cancelled — client synthesizes; do not fill.
    assert _compose_hardkill_harvest_empty_close(user_message="hi", origin=None) == ""


def test_hardkill_harvest_prefix_is_history_prefix():
    """Cloud closer must share history's prefix object — a second literal would drift."""
    from agentcore.conversation import history
    from agentcore.conversation.store import cloud

    assert cloud._HARVEST_USER_PREFIX is history._HARVEST_USER_PREFIX
    assert cloud._HARVEST_USER_PREFIX == "【系统收口】"


def test_d7_pick_monotonic_content_prefers_longer():
    assert pick_monotonic_content("short", "much longer text") == "much longer text"
    assert pick_monotonic_content("already long enough", "short") == "already long enough"


def test_d7_complete_finalize_overrides_longer_midstream():
    """Complete delivery is authoritative even when shorter than a checkpoint draft."""
    assert (
        pick_merged_content(
            "a long mid-stream draft that spilled past the final answer",
            "final answer",
            incoming_status=MESSAGE_STATUS_COMPLETE,
        )
        == "final answer"
    )


def test_d7_salvage_paths_keep_monotonic_protection():
    long_draft = "checkpoint body that is longer than salvage"
    short_salvage = "salvage"
    for status in (MESSAGE_STATUS_INCOMPLETE, MESSAGE_STATUS_FAILED, MESSAGE_STATUS_RUNNING):
        assert pick_merged_content(long_draft, short_salvage, incoming_status=status) == long_draft
    assert (
        pick_merged_content(
            "short",
            "much longer salvage text",
            incoming_status=MESSAGE_STATUS_INCOMPLETE,
        )
        == "much longer salvage text"
    )


def test_visible_failed_assistant_content_keeps_partial_only():
    """FAILED content keeps half-finished prose; pure failure stays empty (error ≠ content)."""
    assert (
        visible_failed_assistant_content(content="已有半成品", error="模型流式响应停滞")
        == "已有半成品"
    )
    assert (
        visible_failed_assistant_content(
            content="", error="模型流式响应停滞（长时间无输出），请稍后重试"
        )
        == ""
    )
    assert visible_failed_assistant_content(content="   ", error=None) == ""


# --- Protocol shape ---


def test_cloud_store_satisfies_conversation_store_protocol():
    store = CloudStore()
    assert isinstance(store, ConversationStore)
    assert get_cloud_store() is get_cloud_store()


async def test_begin_turn_creates_placeholder(monkeypatch):
    calls: list[dict] = []
    settled: list[dict] = []

    class Repo:
        def __init__(self, _s):
            pass

        async def create_assistant_placeholder(self, **kw):
            calls.append(kw)
            return SimpleNamespace(id=kw["message_id"])

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    async def _settle(**kw):
        settled.append(kw)
        return 0

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", Repo)
    monkeypatch.setattr(
        "agentcore.runtime.turn.interrupt.settle_prior_running_assistants",
        _settle,
    )

    await CloudStore().begin_turn(conversation_id="c1", message_id="m1", trace_id="t" * 32)
    assert settled == [{"conversation_id": "c1", "keep_message_id": "m1"}]
    assert calls == [{"conversation_id": "c1", "message_id": "m1", "trace_id": "t" * 32}]


async def test_begin_turn_propagates_placeholder_failure(monkeypatch):
    """Placeholder insert must not be swallowed — turn must not proceed without a row."""

    class Repo:
        def __init__(self, _s):
            pass

        async def create_assistant_placeholder(self, **_kw):
            raise RuntimeError("db down")

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    async def _settle(**_kw):
        return 0

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", Repo)
    monkeypatch.setattr(
        "agentcore.runtime.turn.interrupt.settle_prior_running_assistants",
        _settle,
    )

    with pytest.raises(RuntimeError, match="db down"):
        await CloudStore().begin_turn(conversation_id="c1", message_id="m1", trace_id="t" * 32)


async def test_begin_turn_same_assistant_id_is_idempotent(monkeypatch):
    creates = 0

    class Repo:
        def __init__(self, _s):
            pass

        async def create_assistant_placeholder(self, **kw):
            nonlocal creates
            creates += 1
            if creates > 1:
                raise IntegrityError("INSERT", {}, Exception("messages_pkey"))
            return SimpleNamespace(id=kw["message_id"])

        async def get_by_id(self, message_id, *, conversation_id):
            return SimpleNamespace(
                id=message_id,
                conversation_id=conversation_id,
                role="assistant",
                usage={"status": "running"},
            )

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", Repo)
    monkeypatch.setattr(
        "agentcore.runtime.turn.interrupt.settle_prior_running_assistants",
        AsyncMock(return_value=0),
    )

    store = CloudStore()
    await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="t" * 32)
    await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="t" * 32)
    assert creates == 2


async def test_begin_turn_id_conflict_with_user_row_still_raises(monkeypatch):
    class Repo:
        def __init__(self, _s):
            pass

        async def create_assistant_placeholder(self, **_kw):
            raise IntegrityError("INSERT", {}, Exception("messages_pkey"))

        async def get_by_id(self, message_id, *, conversation_id):
            return SimpleNamespace(
                id=message_id,
                conversation_id=conversation_id,
                role="user",
                usage=None,
            )

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", Repo)
    monkeypatch.setattr(
        "agentcore.runtime.turn.interrupt.settle_prior_running_assistants",
        AsyncMock(return_value=0),
    )

    with pytest.raises(IntegrityError):
        await CloudStore().begin_turn(conversation_id="c1", message_id="m1", trace_id="t" * 32)


async def test_finalize_cloud_settles_empty_error_with_error_code(monkeypatch):
    """Empty content ERROR upserts failed + structured error; content stays empty."""
    from agentcore.core.error_codes import ErrorCode

    upserted: dict[str, Any] = {}
    journaled: list[Any] = []
    metrics: dict[str, Any] = {}

    class MsgRepo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, *_a, **_k):
            return None

        async def upsert_assistant(self, **kw):
            upserted.update(kw)
            return SimpleNamespace(id=kw["message_id"])

    class MetricsRepo:
        def __init__(self, _s):
            pass

        async def record(self, **kw):
            metrics.update(kw)

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    async def _persist_journal(_session, **kw):
        journaled.append(kw)

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", MsgRepo)
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.drain_cost_ledger_before_reconcile",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.reconcile_turn_cost_ledger",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(cloud_mod, "TurnMetricsRepository", MetricsRepo)
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", _persist_journal)
    monkeypatch.setattr(cloud_mod, "schedule_consolidation", lambda _c: None)
    monkeypatch.setattr(cloud_mod, "schedule_compaction_if_due", AsyncMock(return_value=None))
    monkeypatch.setattr(CloudStore, "clear_stream_segments", AsyncMock(return_value=None))

    sink = SimpleNamespace(emit=lambda *_a, **_k: None)

    await CloudStore().finalize(
        mode="cloud",
        result={
            "message_id": "m-first",
            "content": "",
            "error": "连接超时",
            "error_code": ErrorCode.LLM_TIMEOUT,
            "finish_reason": FinishReason.ERROR,
            "rounds": 0,
            "input_tokens": 11,
            "output_tokens": 5,
            "journal_entries": [
                {
                    "kind": "turn_end",
                    "payload": {
                        "finish_reason": "error",
                        "error": {"code": ErrorCode.LLM_TIMEOUT, "message": "连接超时"},
                    },
                    "ts": None,
                }
            ],
        },
        conversation_id="c1",
        user_id="u1",
        folder_id=None,
        backend=SimpleNamespace(location="cloud"),
        sink=sink,
        user_message="hi",
        llm_credentials=None,
        trace_id="a" * 32,
        turn_id="turn1",
        duration_ms=10,
    )

    assert upserted["message_id"] == "m-first"
    assert upserted["content"] == ""
    meta = upserted["metadata"]
    assert meta["status"] == MESSAGE_STATUS_FAILED
    assert meta["error_code"] == ErrorCode.LLM_TIMEOUT
    assert meta["error"] == {"code": ErrorCode.LLM_TIMEOUT, "message": "连接超时"}
    assert meta["finish_reason"] == FinishReason.ERROR.value
    assert journaled and journaled[0]["entries"][0]["payload"]["error"] == {
        "code": ErrorCode.LLM_TIMEOUT,
        "message": "连接超时",
    }
    assert metrics["status"] == "error"
    assert metrics["delegated"] is False
    assert metrics["workers"] == 0
    assert metrics["mode"] == "cloud"
    assert metrics["input_tokens"] == 11
    assert metrics["output_tokens"] == 5
    assert metrics["duration_ms"] == 10


async def test_finalize_cloud_synthesizes_error_when_missing(monkeypatch):
    """finish_reason=error with no error payload still lands structured error on usage/journal."""
    from agentcore.conversation.store.merge import DEFAULT_FAILED_ERROR_MESSAGE
    from agentcore.core.error_codes import ErrorCode

    upserted: dict[str, Any] = {}
    journaled: list[Any] = []

    class MsgRepo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, *_a, **_k):
            return None

        async def upsert_assistant(self, **kw):
            upserted.update(kw)
            return SimpleNamespace(id=kw["message_id"])

    class MetricsRepo:
        def __init__(self, _s):
            pass

        async def record(self, **_kw):
            return None

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    async def _persist_journal(_session, **kw):
        journaled.append(kw)

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", MsgRepo)
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.drain_cost_ledger_before_reconcile",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.reconcile_turn_cost_ledger",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(cloud_mod, "TurnMetricsRepository", MetricsRepo)
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", _persist_journal)
    monkeypatch.setattr(cloud_mod, "schedule_consolidation", lambda _c: None)
    monkeypatch.setattr(cloud_mod, "schedule_compaction_if_due", AsyncMock(return_value=None))
    monkeypatch.setattr(CloudStore, "clear_stream_segments", AsyncMock(return_value=None))

    await CloudStore().finalize(
        mode="cloud",
        result={
            "message_id": "m-synth",
            "content": "",
            "finish_reason": FinishReason.ERROR,
            "rounds": 0,
        },
        conversation_id="c1",
        user_id="u1",
        folder_id=None,
        backend=SimpleNamespace(location="cloud"),
        sink=SimpleNamespace(emit=lambda *_a, **_k: None),
        user_message="hi",
        llm_credentials=None,
        trace_id="e" * 32,
        turn_id="turn-synth",
        duration_ms=10,
    )

    assert upserted["content"] == ""
    meta = upserted["metadata"]
    assert meta["status"] == MESSAGE_STATUS_FAILED
    assert meta["error_code"] == ErrorCode.PIPELINE_ERROR
    assert meta["error"] == {
        "code": ErrorCode.PIPELINE_ERROR,
        "message": DEFAULT_FAILED_ERROR_MESSAGE,
    }
    assert journaled
    turn_end = next(e for e in journaled[0]["entries"] if e.get("kind") == "turn_end")
    assert turn_end["payload"]["error"] == {
        "code": ErrorCode.PIPELINE_ERROR,
        "message": DEFAULT_FAILED_ERROR_MESSAGE,
    }


def test_ensure_structured_run_error_preserves_context():
    """BYOK deconfigured (etc.): synthesize must keep existing.context."""
    from agentcore.core.error_codes import ErrorCode

    out = cloud_mod._ensure_structured_run_error(
        existing={
            "code": ErrorCode.LLM_KEY_REQUIRED,
            "message": "请先配置 API Key",
            "context": {"remedy": "open_byok_settings", "provider": "deepseek"},
        }
    )
    assert out == {
        "code": ErrorCode.LLM_KEY_REQUIRED,
        "message": "请先配置 API Key",
        "context": {"remedy": "open_byok_settings", "provider": "deepseek"},
    }


def test_merge_run_error_into_journal_completes_sparse_turn_end():
    """Missing / partial turn_end.error is filled; other error fields stay."""
    from agentcore.core.error_codes import ErrorCode

    entries = [
        {"kind": "llm_call", "payload": {"model": "m"}, "ts": "t0"},
        {
            "kind": "turn_end",
            "payload": {
                "finish_reason": "error",
                "error": {
                    "code": "",
                    "context": {"remedy": "open_byok_settings"},
                },
            },
            "ts": None,
        },
    ]
    merged = cloud_mod._merge_run_error_into_journal_entries(
        entries,
        {
            "code": ErrorCode.LLM_KEY_REQUIRED,
            "message": "请先配置 API Key",
            "context": {"remedy": "should_not_overwrite"},
        },
        finish_reason="error",
    )
    turn_end = next(e for e in merged if e.get("kind") == "turn_end")
    assert turn_end["payload"]["error"] == {
        "code": ErrorCode.LLM_KEY_REQUIRED,
        "message": "请先配置 API Key",
        "context": {"remedy": "open_byok_settings"},
    }
    # Non-turn_end facts preserved.
    assert merged[0]["kind"] == "llm_call"


def test_merge_run_error_into_journal_appends_turn_end_when_absent():
    """Progressive journal with no turn_end still gets one carrying structured error."""
    from agentcore.core.error_codes import ErrorCode

    merged = cloud_mod._merge_run_error_into_journal_entries(
        [{"kind": "llm_call", "payload": {"model": "m"}, "ts": "t0"}],
        {"code": ErrorCode.PIPELINE_ERROR, "message": "管线崩溃"},
        finish_reason="error",
    )
    assert [e["kind"] for e in merged] == ["llm_call", "turn_end"]
    assert merged[1]["payload"] == {
        "finish_reason": "error",
        "error": {"code": ErrorCode.PIPELINE_ERROR, "message": "管线崩溃"},
    }


async def test_finalize_cloud_merges_error_into_incomplete_progressive_journal(
    monkeypatch,
):
    """Progressive journal with turn_end lacking error still gets concrete error on persist."""
    from agentcore.core.error_codes import ErrorCode

    upserted: dict[str, Any] = {}
    journaled: list[Any] = []

    class MsgRepo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, *_a, **_k):
            return None

        async def upsert_assistant(self, **kw):
            upserted.update(kw)
            return SimpleNamespace(id=kw["message_id"])

    class MetricsRepo:
        def __init__(self, _s):
            pass

        async def record(self, **_kw):
            return None

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    async def _persist_journal(_session, **kw):
        journaled.append(kw)

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", MsgRepo)
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.drain_cost_ledger_before_reconcile",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.reconcile_turn_cost_ledger",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(cloud_mod, "TurnMetricsRepository", MetricsRepo)
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", _persist_journal)
    monkeypatch.setattr(cloud_mod, "schedule_consolidation", lambda _c: None)
    monkeypatch.setattr(cloud_mod, "schedule_compaction_if_due", AsyncMock(return_value=None))
    monkeypatch.setattr(CloudStore, "clear_stream_segments", AsyncMock(return_value=None))

    await CloudStore().finalize(
        mode="cloud",
        result={
            "message_id": "m-prog-sparse",
            "content": "",
            "error": {
                "code": ErrorCode.LLM_TIMEOUT,
                "message": "连接超时，请稍后重试",
            },
            "error_code": ErrorCode.LLM_TIMEOUT,
            "finish_reason": FinishReason.ERROR,
            "rounds": 1,
            "journal_entries": [
                {"kind": "llm_call", "payload": {"model": "m", "round": 1}, "ts": "t0"},
                {
                    "kind": "turn_end",
                    "payload": {"finish_reason": "error"},
                    "ts": None,
                },
            ],
        },
        conversation_id="c1",
        user_id="u1",
        folder_id=None,
        backend=SimpleNamespace(location="cloud"),
        sink=SimpleNamespace(emit=lambda *_a, **_k: None),
        user_message="hi",
        llm_credentials=None,
        trace_id="d" * 32,
        turn_id="turn-prog-sparse",
        duration_ms=10,
    )

    assert upserted["content"] == ""
    assert journaled
    entries = journaled[0]["entries"]
    assert entries[0]["kind"] == "llm_call"
    turn_end = next(e for e in entries if e.get("kind") == "turn_end")
    assert turn_end["payload"]["error"] == {
        "code": ErrorCode.LLM_TIMEOUT,
        "message": "连接超时，请稍后重试",
    }


async def test_finalize_cloud_keeps_existing_partial_on_empty_error(monkeypatch):
    """Stream-stall ERROR with empty salvage must not erase a longer pause checkpoint."""
    from agentcore.core.error_codes import ErrorCode

    upserted: dict[str, Any] = {}

    class MsgRepo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, *_a, **_k):
            return SimpleNamespace(content="暂停前已交付的半成品正文", usage={"status": "running"})

        async def upsert_assistant(self, **kw):
            upserted.update(kw)
            return SimpleNamespace(id=kw["message_id"])

    class MetricsRepo:
        def __init__(self, _s):
            pass

        async def record(self, **_kw):
            return None

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", MsgRepo)
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.drain_cost_ledger_before_reconcile",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.reconcile_turn_cost_ledger",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(cloud_mod, "TurnMetricsRepository", MetricsRepo)
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", AsyncMock())
    monkeypatch.setattr(cloud_mod, "schedule_consolidation", lambda _c: None)
    monkeypatch.setattr(cloud_mod, "schedule_compaction_if_due", AsyncMock(return_value=None))
    monkeypatch.setattr(CloudStore, "clear_stream_segments", AsyncMock(return_value=None))

    await CloudStore().finalize(
        mode="cloud",
        result={
            "message_id": "m-partial",
            "content": "",
            "error": "模型流式响应停滞（长时间无输出），请稍后重试",
            "error_code": ErrorCode.LLM_TIMEOUT,
            "finish_reason": FinishReason.ERROR,
            "rounds": 1,
        },
        conversation_id="c1",
        user_id="u1",
        folder_id=None,
        backend=SimpleNamespace(location="cloud"),
        sink=SimpleNamespace(emit=lambda *_a, **_k: None),
        user_message="hi",
        llm_credentials=None,
        trace_id="c" * 32,
        turn_id="turn-partial",
        duration_ms=10,
    )

    # Incoming stayed empty so upsert merge (not pre-fill) keeps the checkpoint body.
    assert upserted["content"] == ""
    assert upserted["metadata"]["status"] == MESSAGE_STATUS_FAILED


async def test_finalize_cloud_auto_snapshot_passes_folder_id(monkeypatch):
    """Folder chats must auto-snapshot under the folder storage key (B4 lock alignment)."""
    from datetime import UTC, datetime

    from agentcore.config import settings
    from agentcore.storage import SnapshotRef

    captured: dict[str, Any] = {}
    emitted: list[Any] = []

    class MsgRepo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, *_a, **_k):
            return None

        async def upsert_assistant(self, **kw):
            return SimpleNamespace(id=kw["message_id"])

        async def set_followups(self, *_a, **_k):
            return None

    class MetricsRepo:
        def __init__(self, _s):
            pass

        async def record(self, **_kw):
            return None

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    async def _fake_create_snapshot(**kw):
        captured.update(kw)
        return SnapshotRef(
            snapshot_id="snap-folder",
            label=None,
            created_at=datetime.now(UTC),
            size_bytes=1,
        )

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", MsgRepo)
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.drain_cost_ledger_before_reconcile",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.reconcile_turn_cost_ledger",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(cloud_mod, "TurnMetricsRepository", MetricsRepo)
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", AsyncMock())
    monkeypatch.setattr(cloud_mod, "schedule_consolidation", lambda _c: None)
    monkeypatch.setattr(cloud_mod, "schedule_compaction_if_due", AsyncMock(return_value=None))
    monkeypatch.setattr(CloudStore, "clear_stream_segments", AsyncMock(return_value=None))
    monkeypatch.setattr(settings, "workspace_snapshot_enabled", True)
    monkeypatch.setattr(cloud_mod, "create_snapshot", _fake_create_snapshot)
    monkeypatch.setattr(
        cloud_mod,
        "resolve_folder_placement",
        AsyncMock(return_value=FolderPlacement(folder_id="folder-42", rel_path="项目")),
    )

    await CloudStore().finalize(
        mode="cloud",
        result={
            "message_id": "m-snap",
            "content": "done",
            "finish_reason": FinishReason.END_TURN,
            "rounds": 1,
        },
        conversation_id="c-folder",
        user_id="u1",
        folder_id="folder-42",
        backend=SimpleNamespace(location="server", dirty=True),
        sink=SimpleNamespace(emit=lambda event: emitted.append(event)),
        user_message="hi",
        llm_credentials=None,
        trace_id="d" * 32,
        turn_id="turn-snap",
        duration_ms=10,
    )

    assert captured["user_id"] == "u1"
    assert captured["folder_id"] == "folder-42"
    assert captured["conversation_id"] == "c-folder"
    assert [e.type.value for e in emitted] == ["workspace_snapshot_done"]
    assert emitted[0].payload["snapshot_id"] == "snap-folder"


async def test_finalize_cloud_auto_snapshot_failure_emits_sse(monkeypatch):
    """Dirty cloud finalize still completes the turn and emits workspace_snapshot_failed."""
    from agentcore.config import settings

    emitted: list[Any] = []

    class MsgRepo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, *_a, **_k):
            return None

        async def upsert_assistant(self, **kw):
            return SimpleNamespace(id=kw["message_id"])

        async def set_followups(self, *_a, **_k):
            return None

    class MetricsRepo:
        def __init__(self, _s):
            pass

        async def record(self, **_kw):
            return None

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    async def _boom_create_snapshot(**_kw):
        raise RuntimeError("oss down")

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", MsgRepo)
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.drain_cost_ledger_before_reconcile",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.reconcile_turn_cost_ledger",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(cloud_mod, "TurnMetricsRepository", MetricsRepo)
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", AsyncMock())
    monkeypatch.setattr(cloud_mod, "schedule_consolidation", lambda _c: None)
    monkeypatch.setattr(cloud_mod, "schedule_compaction_if_due", AsyncMock(return_value=None))
    monkeypatch.setattr(CloudStore, "clear_stream_segments", AsyncMock(return_value=None))
    monkeypatch.setattr(settings, "workspace_snapshot_enabled", True)
    monkeypatch.setattr(cloud_mod, "create_snapshot", _boom_create_snapshot)

    await CloudStore().finalize(
        mode="cloud",
        result={
            "message_id": "m-snap-fail",
            "content": "done",
            "finish_reason": FinishReason.END_TURN,
            "rounds": 1,
        },
        conversation_id="c-fail",
        user_id="u1",
        folder_id=None,
        backend=SimpleNamespace(location="server", dirty=True),
        sink=SimpleNamespace(emit=lambda event: emitted.append(event)),
        user_message="hi",
        llm_credentials=None,
        trace_id="e" * 32,
        turn_id="turn-snap-fail",
        duration_ms=10,
    )

    assert [e.type.value for e in emitted] == ["workspace_snapshot_failed"]
    assert emitted[0].payload["conversation_id"] == "c-fail"


async def test_finalize_local_settles_empty_error_with_error_code(monkeypatch):
    """Local write-back: empty ERROR settles failed + structured error; content empty."""
    from agentcore.core.error_codes import ErrorCode

    upserted: dict[str, Any] = {}
    journaled: list[Any] = []

    class MsgRepo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, *_a, **_k):
            return None

        async def create(self, **kw):
            return SimpleNamespace(id=kw["message_id"])

        async def upsert_assistant(self, **kw):
            upserted.update(kw)
            return SimpleNamespace(id=kw["message_id"])

        async def user_message_for_assistant(self, **_k):
            return None

        async def set_followups(self, *_a, **_k):
            pass

    class ConvRepo:
        def __init__(self, _s):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title="t")

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    async def _persist_journal(_session, **kw):
        journaled.append(kw)

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", MsgRepo)
    monkeypatch.setattr(cloud_mod, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", _persist_journal)
    monkeypatch.setattr(cloud_mod, "TurnMetricsRepository", _NoopMetricsRepo)
    monkeypatch.setattr(cloud_mod, "schedule_consolidation", lambda _c: None)
    monkeypatch.setattr(cloud_mod, "schedule_compaction_if_due", AsyncMock(return_value=None))
    monkeypatch.setattr(
        cloud_mod, "build_provider", lambda *_a, **_k: SimpleNamespace(close=AsyncMock())
    )
    monkeypatch.setattr(cloud_mod, "resolve_user_model", lambda *_a, **_k: "m")
    monkeypatch.setattr(CloudStore, "clear_stream_segments", AsyncMock(return_value=None))

    result = await CloudStore().finalize(
        mode="local",
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="",
        runs={
            "events": [],
            "finish_reason": "error",
            "error": {"code": ErrorCode.LLM_TIMEOUT, "message": "超时"},
        },
        user_message_id="u1m",
        message_id="m-err",
        trace_id="b" * 32,
        finish_reason=FinishReason.ERROR.value,
    )
    assert result is not None
    assert result["assistant_message_id"] == "m-err"
    assert upserted["message_id"] == "m-err"
    assert upserted["content"] == ""
    meta = upserted["metadata"]
    assert meta["status"] == MESSAGE_STATUS_FAILED
    assert meta["error_code"] == ErrorCode.LLM_TIMEOUT
    assert meta["error"] == {"code": ErrorCode.LLM_TIMEOUT, "message": "超时"}
    assert meta["finish_reason"] == FinishReason.ERROR.value
    assert journaled
    turn_end = next(e for e in journaled[0]["entries"] if e.get("kind") == "turn_end")
    assert turn_end["payload"]["error"] == {
        "code": ErrorCode.LLM_TIMEOUT,
        "message": "超时",
    }


async def test_finalize_local_synthesizes_error_when_missing(monkeypatch):
    """Local ERROR without runs.error still synthesizes structured error into usage/journal."""
    from agentcore.conversation.store.merge import DEFAULT_FAILED_ERROR_MESSAGE
    from agentcore.core.error_codes import ErrorCode

    upserted: dict[str, Any] = {}
    journaled: list[Any] = []

    class MsgRepo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, *_a, **_k):
            return None

        async def create(self, **kw):
            return SimpleNamespace(id=kw["message_id"])

        async def upsert_assistant(self, **kw):
            upserted.update(kw)
            return SimpleNamespace(id=kw["message_id"])

        async def user_message_for_assistant(self, **_k):
            return None

        async def set_followups(self, *_a, **_k):
            pass

    class ConvRepo:
        def __init__(self, _s):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title="t")

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    async def _persist_journal(_session, **kw):
        journaled.append(kw)

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", MsgRepo)
    monkeypatch.setattr(cloud_mod, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", _persist_journal)
    monkeypatch.setattr(cloud_mod, "TurnMetricsRepository", _NoopMetricsRepo)
    monkeypatch.setattr(cloud_mod, "schedule_consolidation", lambda _c: None)
    monkeypatch.setattr(cloud_mod, "schedule_compaction_if_due", AsyncMock(return_value=None))
    monkeypatch.setattr(
        cloud_mod, "build_provider", lambda *_a, **_k: SimpleNamespace(close=AsyncMock())
    )
    monkeypatch.setattr(cloud_mod, "resolve_user_model", lambda *_a, **_k: "m")
    monkeypatch.setattr(CloudStore, "clear_stream_segments", AsyncMock(return_value=None))

    result = await CloudStore().finalize(
        mode="local",
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="",
        runs={"events": [], "finish_reason": "error"},
        user_message_id="u1m",
        message_id="m-synth-local",
        trace_id="f" * 32,
        finish_reason=FinishReason.ERROR.value,
    )
    assert result is not None
    assert upserted["content"] == ""
    meta = upserted["metadata"]
    assert meta["status"] == MESSAGE_STATUS_FAILED
    assert meta["error"] == {
        "code": ErrorCode.PIPELINE_ERROR,
        "message": DEFAULT_FAILED_ERROR_MESSAGE,
    }
    assert journaled
    turn_end = next(e for e in journaled[0]["entries"] if e.get("kind") == "turn_end")
    assert turn_end["payload"]["error"] == {
        "code": ErrorCode.PIPELINE_ERROR,
        "message": DEFAULT_FAILED_ERROR_MESSAGE,
    }


async def test_finalize_local_merges_error_into_incomplete_progressive_journal(
    monkeypatch,
):
    """Local FAILED + progressive journal without turn_end.error → persist concrete error."""
    from agentcore.core.error_codes import ErrorCode

    upserted: dict[str, Any] = {}
    journaled: list[Any] = []

    class MsgRepo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, *_a, **_k):
            return None

        async def create(self, **kw):
            return SimpleNamespace(id=kw["message_id"])

        async def upsert_assistant(self, **kw):
            upserted.update(kw)
            return SimpleNamespace(id=kw["message_id"])

        async def user_message_for_assistant(self, **_k):
            return None

        async def set_followups(self, *_a, **_k):
            pass

    class ConvRepo:
        def __init__(self, _s):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title="t")

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    async def _persist_journal(_session, **kw):
        journaled.append(kw)

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", MsgRepo)
    monkeypatch.setattr(cloud_mod, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", _persist_journal)
    monkeypatch.setattr(cloud_mod, "TurnMetricsRepository", _NoopMetricsRepo)
    monkeypatch.setattr(cloud_mod, "schedule_consolidation", lambda _c: None)
    monkeypatch.setattr(cloud_mod, "schedule_compaction_if_due", AsyncMock(return_value=None))
    monkeypatch.setattr(
        cloud_mod, "build_provider", lambda *_a, **_k: SimpleNamespace(close=AsyncMock())
    )
    monkeypatch.setattr(cloud_mod, "resolve_user_model", lambda *_a, **_k: "m")
    monkeypatch.setattr(CloudStore, "clear_stream_segments", AsyncMock(return_value=None))

    result = await CloudStore().finalize(
        mode="local",
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="",
        runs={
            "events": [],
            "finish_reason": "error",
            "error": {
                "code": ErrorCode.LLM_KEY_REQUIRED,
                "message": "请先配置 API Key",
                "context": {"remedy": "open_byok_settings"},
            },
        },
        journal=[
            {"kind": "llm_call", "payload": {"model": "m"}, "ts": "t0"},
            {"kind": "turn_end", "payload": {"finish_reason": "error"}, "ts": None},
        ],
        user_message_id="u1m",
        message_id="m-local-prog-sparse",
        trace_id="a" * 32,
        finish_reason=FinishReason.ERROR.value,
    )
    assert result is not None
    assert upserted["content"] == ""
    meta = upserted["metadata"]
    assert meta["status"] == MESSAGE_STATUS_FAILED
    assert meta["error"] == {
        "code": ErrorCode.LLM_KEY_REQUIRED,
        "message": "请先配置 API Key",
        "context": {"remedy": "open_byok_settings"},
    }
    assert journaled
    entries = journaled[0]["entries"]
    assert entries[0]["kind"] == "llm_call"
    turn_end = next(e for e in entries if e.get("kind") == "turn_end")
    assert turn_end["payload"]["error"] == {
        "code": ErrorCode.LLM_KEY_REQUIRED,
        "message": "请先配置 API Key",
        "context": {"remedy": "open_byok_settings"},
    }


async def test_finalize_local_keeps_existing_partial_on_empty_error(monkeypatch):
    """Local ERROR salvage must keep a longer existing body (not replace with error text)."""
    from agentcore.core.error_codes import ErrorCode

    upserted: dict[str, Any] = {}

    class MsgRepo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, message_id, **_k):
            if message_id == "m-partial":
                return SimpleNamespace(
                    content="暂停前半成品",
                    usage={"status": MESSAGE_STATUS_RUNNING},
                )
            return None

        async def create(self, **kw):
            return SimpleNamespace(id=kw["message_id"])

        async def upsert_assistant(self, **kw):
            upserted.update(kw)
            return SimpleNamespace(id=kw["message_id"])

        async def user_message_for_assistant(self, **_k):
            return None

        async def set_followups(self, *_a, **_k):
            pass

    class ConvRepo:
        def __init__(self, _s):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title="t")

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", MsgRepo)
    monkeypatch.setattr(cloud_mod, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", AsyncMock())
    monkeypatch.setattr(cloud_mod, "TurnMetricsRepository", _NoopMetricsRepo)
    monkeypatch.setattr(cloud_mod, "schedule_consolidation", lambda _c: None)
    monkeypatch.setattr(cloud_mod, "schedule_compaction_if_due", AsyncMock(return_value=None))
    monkeypatch.setattr(
        cloud_mod, "build_provider", lambda *_a, **_k: SimpleNamespace(close=AsyncMock())
    )
    monkeypatch.setattr(cloud_mod, "resolve_user_model", lambda *_a, **_k: "m")
    monkeypatch.setattr(CloudStore, "clear_stream_segments", AsyncMock(return_value=None))

    result = await CloudStore().finalize(
        mode="local",
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="",
        runs={
            "events": [],
            "finish_reason": "error",
            "error": {
                "code": ErrorCode.LLM_TIMEOUT,
                "message": "模型流式响应停滞（长时间无输出），请稍后重试",
            },
        },
        user_message_id="u1m",
        message_id="m-partial",
        trace_id="d" * 32,
        finish_reason=FinishReason.ERROR.value,
    )
    assert result is not None
    assert upserted["content"] == "暂停前半成品"
    assert upserted["metadata"]["status"] == MESSAGE_STATUS_FAILED


async def test_append_journal_uses_telemetry_pool(monkeypatch):
    used: list[str] = []
    appended: list[dict] = []

    class CM:
        async def __aenter__(self):
            used.append("telemetry")
            return object()

        async def __aexit__(self, *_a):
            return False

    class Repo:
        def __init__(self, _s):
            pass

        async def append(self, **kw) -> int | None:
            appended.append(kw)
            return 0

    def primary_boom():
        used.append("primary")
        raise AssertionError("append_journal must not use primary pool")

    monkeypatch.setattr(cloud_mod, "telemetry_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "async_session_factory", primary_boom)
    monkeypatch.setattr(cloud_mod, "TurnJournalRepository", Repo)
    monkeypatch.setattr("agentcore.runtime.audit.hooks.on_journal_fact_appended", lambda _e: None)

    await CloudStore().append_journal(
        turn_id="m1",
        seq=0,
        conversation_id="c1",
        trace_id="t",
        entry={"kind": "run_plan", "payload": {}},
    )
    assert used == ["telemetry"]
    assert appended[0]["seq"] == 0


async def test_append_journal_skips_hook_on_duplicate(monkeypatch):
    hooks: list[Any] = []

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    class Repo:
        def __init__(self, _s):
            pass

        async def append(self, **_kw) -> int | None:
            return None  # conflict / already present

    monkeypatch.setattr(cloud_mod, "telemetry_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "TurnJournalRepository", Repo)
    monkeypatch.setattr(
        "agentcore.runtime.audit.hooks.on_journal_fact_appended",
        lambda e: hooks.append(e),
    )

    await CloudStore().append_journal(
        turn_id="m1",
        seq=0,
        conversation_id="c1",
        trace_id="t",
        entry={"kind": "x"},
    )
    assert hooks == []


async def test_finalize_local_fills_journal_via_persist(monkeypatch):
    """D7: finalize(mode=local) upserts full journal (no early-return)."""
    journal_calls: list[dict] = []

    class MsgRepo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, *_a, **_k):
            return None

        async def create(self, **kw):
            return SimpleNamespace(id=kw["message_id"])

        async def upsert_assistant(self, **kw):
            return SimpleNamespace(id=kw["message_id"])

        async def user_message_for_assistant(self, **_k):
            return None

        async def set_followups(self, *_a, **_k):
            pass

    class ConvRepo:
        def __init__(self, _s):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title="t")

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    async def fake_persist(_session, **kw):
        journal_calls.append(kw)

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", MsgRepo)
    monkeypatch.setattr(cloud_mod, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", fake_persist)
    monkeypatch.setattr(cloud_mod, "TurnMetricsRepository", _NoopMetricsRepo)
    monkeypatch.setattr(cloud_mod, "schedule_consolidation", lambda _c: None)
    monkeypatch.setattr(cloud_mod, "schedule_compaction_if_due", AsyncMock(return_value=None))

    async def _run_bg(user_id, *, purpose="title", runner):
        from agentcore.billing.gate import BackgroundLlmResult
        from agentcore.llm.credentials import LLMCredentials

        creds = LLMCredentials(
            api_key="sk", base_url="https://x", default_model="m", source="platform"
        )
        value = await runner(creds)
        return BackgroundLlmResult(value=value, credentials=creds)

    monkeypatch.setattr(cloud_mod, "run_background_llm", _run_bg)
    monkeypatch.setattr(
        cloud_mod, "build_provider", lambda *_a, **_k: SimpleNamespace(close=AsyncMock())
    )
    monkeypatch.setattr(cloud_mod, "resolve_user_model", lambda *_a, **_k: "m")
    monkeypatch.setattr(
        "agentcore.runtime.kickoff.stage_card.emit_stage_card_for_motion",
        AsyncMock(return_value=None),
    )

    result = await CloudStore().finalize(
        mode="local",
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="done",
        runs={"events": [{"type": "run_plan", "payload": {}}], "finish_reason": "end_turn"},
        user_message_id="u1m",
        message_id="m1",
        trace_id="t" * 32,
    )
    assert result is not None
    assert result["assistant_message_id"] == "m1"
    assert len(journal_calls) == 1
    assert journal_calls[0]["message_id"] == "m1"


async def test_finalize_local_persists_raw_journal_when_runs_missing(monkeypatch):
    """Crash salvage: runs=None → persist outbox journal facts directly."""
    journal_calls: list[dict] = []
    upserted: dict = {}

    class MsgRepo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, *_a, **_k):
            return None

        async def create(self, **kw):
            return SimpleNamespace(id=kw["message_id"])

        async def upsert_assistant(self, **kw):
            upserted.update(kw)
            return SimpleNamespace(id=kw["message_id"])

        async def user_message_for_assistant(self, **_k):
            return None

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    async def fake_persist(_session, **kw):
        journal_calls.append(kw)

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", MsgRepo)
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", fake_persist)
    monkeypatch.setattr(cloud_mod, "TurnMetricsRepository", _NoopMetricsRepo)

    facts = [
        {"kind": "run_started", "payload": {"id": "r1"}, "ts": "t0"},
        {"kind": "run_completed", "payload": {"id": "r1"}, "ts": None},
    ]
    result = await CloudStore().finalize(
        mode="local",
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="partial",
        runs=None,
        journal=facts,
        user_message_id="u1m",
        message_id="m1",
        trace_id="t" * 32,
        finish_reason=FinishReason.CANCELLED.value,
    )
    assert result is not None
    assert result["assistant_message_id"] == "m1"
    assert upserted["metadata"]["status"] == MESSAGE_STATUS_INCOMPLETE
    assert upserted["metadata"]["incomplete"] is True
    assert upserted["metadata"]["finish_reason"] == "cancelled"
    assert upserted["content"] == "partial"
    assert len(journal_calls) == 1
    persisted = journal_calls[0]["entries"]
    assert persisted[:-1] == facts
    assert persisted[-1] == {
        "kind": "turn_end",
        "payload": {"finish_reason": "cancelled"},
        "ts": None,
    }
    from agentcore.runtime.journal import runs_from_entries

    projected = runs_from_entries(persisted)
    assert projected is not None
    assert projected["finish_reason"] == "cancelled"


async def test_finalize_local_does_not_mint_followups(monkeypatch):
    """Local finalize never mints / persists followups chips (feature offline)."""
    followup_calls: list[dict] = []

    class MsgRepo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, *_a, **_k):
            return None

        async def create(self, **kw):
            return SimpleNamespace(id=kw["message_id"])

        async def upsert_assistant(self, **kw):
            return SimpleNamespace(id=kw["message_id"])

        async def user_message_for_assistant(self, **_k):
            return None

        async def set_followups(self, message_id, *, conversation_id, followups):
            followup_calls.append(
                {
                    "message_id": message_id,
                    "conversation_id": conversation_id,
                    "followups": followups,
                }
            )

    class ConvRepo:
        def __init__(self, _s):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title="already")

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", MsgRepo)
    monkeypatch.setattr(cloud_mod, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", AsyncMock())
    monkeypatch.setattr(cloud_mod, "TurnMetricsRepository", _NoopMetricsRepo)
    monkeypatch.setattr(cloud_mod, "schedule_consolidation", lambda _c: None)
    monkeypatch.setattr(cloud_mod, "schedule_compaction_if_due", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "agentcore.runtime.kickoff.stage_card.emit_stage_card_for_motion",
        AsyncMock(return_value=None),
    )

    result = await CloudStore().finalize(
        mode="local",
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="done reply",
        runs={"events": [], "finish_reason": "end_turn"},
        user_message_id="u1m",
        message_id="m1",
        trace_id="t" * 32,
        finish_reason=FinishReason.END_TURN.value,
    )
    assert result is not None
    assert followup_calls == []
    assert result["followups"] is None


async def test_finalize_local_skips_stage_when_not_end_turn(monkeypatch):
    """Stage card (and former followups) only on end_turn + non-empty body."""
    stage = AsyncMock(return_value="sc_1")

    class MsgRepo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, *_a, **_k):
            return None

        async def create(self, **kw):
            return SimpleNamespace(id=kw["message_id"])

        async def upsert_assistant(self, **kw):
            return SimpleNamespace(id=kw["message_id"])

        async def user_message_for_assistant(self, **_k):
            return None

        async def set_followups(self, *_a, **_k):
            raise AssertionError("set_followups must not run")

    class ConvRepo:
        def __init__(self, _s):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(title="already")

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", MsgRepo)
    monkeypatch.setattr(cloud_mod, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", AsyncMock())
    monkeypatch.setattr(cloud_mod, "TurnMetricsRepository", _NoopMetricsRepo)
    monkeypatch.setattr(cloud_mod, "schedule_consolidation", lambda _c: None)
    monkeypatch.setattr(cloud_mod, "schedule_compaction_if_due", AsyncMock(return_value=None))
    monkeypatch.setattr("agentcore.runtime.kickoff.stage_card.emit_stage_card_for_motion", stage)

    result = await CloudStore().finalize(
        mode="local",
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="partial reply",
        runs={"events": [], "finish_reason": "degraded"},
        user_message_id="u1m",
        message_id="m1",
        trace_id="t" * 32,
        finish_reason=FinishReason.DEGRADED.value,
    )
    assert result is not None
    stage.assert_not_awaited()
    assert result["followups"] is None


async def test_persist_turn_journal_merges_by_seq_by_default(monkeypatch):
    """Default persist is seq insert-if-absent (salvage / cloud live must not wipe)."""
    from agentcore.runtime.journal.persist import persist_turn_journal

    appended: list[int] = []
    recorded: list[object] = []

    class Repo:
        def __init__(self, _s):
            pass

        async def record(self, **_kw) -> None:
            recorded.append(_kw)

        async def append(self, *, turn_id, seq, conversation_id, trace_id, entry) -> int | None:
            appended.append(seq)
            return seq if seq is not None else 0

    class Session:
        async def rollback(self):
            pass

    monkeypatch.setattr("agentcore.db.repositories.TurnJournalRepository", Repo)
    monkeypatch.setattr("agentcore.config.settings.observability_span_export_enabled", False)

    entries = [
        {"kind": "run_plan", "payload": {}},
        {"kind": "run_completed", "payload": {}},
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}},
    ]
    await persist_turn_journal(
        Session(),  # type: ignore[arg-type]
        message_id="m1",
        conversation_id="c1",
        trace_id="t",
        entries=entries,
    )
    assert recorded == []
    assert appended == [0, 1, 2]


async def test_persist_turn_journal_resume_overflow_does_not_duplicate_process_content(
    monkeypatch,
):
    """ask_user pause→resume: inherited [*live, *overflow] must not extend live seq.

    Live writer already flushed one ``process_content`` at max_seq. Old merge used
    fact_log index as seq; the overflow row shifted the same text onto an empty
    slot (cid 36d4e5b9 / seq 21+22).
    """
    from agentcore.runtime.journal import last_turn_end_finish
    from agentcore.runtime.journal.persist import persist_turn_journal

    reply = "一批候选问题（合成夹具，非用户原文）。"
    store: dict[str, dict] = {
        str(i): {"kind": f"pre_{i}", "payload": {}} for i in range(21)
    }
    store["21"] = {
        "kind": "process_content",
        "payload": {"kind": "content", "text": reply},
    }
    appended_beyond: list[tuple[int | None, str]] = []

    class Repo:
        def __init__(self, _s):
            pass

        async def max_seq(self, turn_id: str) -> int | None:
            del turn_id
            return max(int(k) for k in store)

        async def load(self, turn_id: str) -> list:
            del turn_id
            return [store[k] for k in sorted(store, key=int)]

        async def append(self, *, turn_id, seq, conversation_id, trace_id, entry) -> int:
            del conversation_id, trace_id
            if seq is not None and seq <= 21:
                return seq
            next_seq = max(int(k) for k in store) + 1
            store[str(next_seq)] = entry
            appended_beyond.append((seq, str(entry.get("kind") or "")))
            return next_seq

    class Session:
        async def rollback(self):
            pass

    monkeypatch.setattr("agentcore.db.repositories.TurnJournalRepository", Repo)
    monkeypatch.setattr("agentcore.config.settings.observability_span_export_enabled", False)

    # 13 inherited (12 live + overflow) + 9 resume facts → process_content at
    # index 22, past live max 21 — the old enumerate insert slot.
    inherited = [{"kind": f"pre_{i}", "payload": {}} for i in range(12)]
    inherited.append({"kind": "run_completed", "payload": {"overflow": True}})
    resume_facts = [{"kind": f"resume_{i}", "payload": {}} for i in range(9)]
    resume_facts.append(
        {"kind": "process_content", "payload": {"kind": "content", "text": reply}}
    )
    resume_facts.append({"kind": "turn_end", "payload": {"finish_reason": "end_turn"}})
    await persist_turn_journal(
        Session(),  # type: ignore[arg-type]
        message_id="m1",
        conversation_id="c1",
        trace_id="t",
        entries=inherited + resume_facts,
    )
    pcs = [
        e
        for e in store.values()
        if e.get("kind") == "process_content"
        and (e.get("payload") or {}).get("text") == reply
    ]
    assert len(pcs) == 1
    assert last_turn_end_finish(list(store.values())) == "end_turn"
    assert appended_beyond == [(None, "turn_end")]


async def test_persist_turn_journal_end_turn_supersedes_paused_closer(monkeypatch):
    """Resume complete must append end_turn even when pause left turn_end(paused)."""
    from agentcore.runtime.journal import last_turn_end_finish
    from agentcore.runtime.journal.persist import persist_turn_journal

    store: dict[str, dict] = {
        "0": {"kind": "run_started", "payload": {}},
        "1": {"kind": "turn_end", "payload": {"finish_reason": "paused"}},
    }

    class Repo:
        def __init__(self, _s):
            pass

        async def max_seq(self, turn_id: str) -> int | None:
            del turn_id
            return max(int(k) for k in store)

        async def load(self, turn_id: str) -> list:
            del turn_id
            return [store[k] for k in sorted(store, key=int)]

        async def append(self, *, turn_id, seq, conversation_id, trace_id, entry) -> int:
            del conversation_id, trace_id
            if seq is not None and str(seq) in store:
                return seq
            next_seq = max(int(k) for k in store) + 1
            store[str(next_seq)] = entry
            return next_seq

    class Session:
        async def rollback(self):
            pass

    monkeypatch.setattr("agentcore.db.repositories.TurnJournalRepository", Repo)
    monkeypatch.setattr("agentcore.config.settings.observability_span_export_enabled", False)

    await persist_turn_journal(
        Session(),  # type: ignore[arg-type]
        message_id="m1",
        conversation_id="c1",
        trace_id="t",
        entries=[
            {"kind": "run_started", "payload": {}},
            {"kind": "process_content", "payload": {"kind": "content", "text": "续写"}},
            {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}},
        ],
    )
    assert last_turn_end_finish([store[k] for k in sorted(store, key=int)]) == "end_turn"
    assert sum(1 for e in store.values() if e.get("kind") == "process_content") == 0


async def test_persist_turn_journal_replaces_via_record(monkeypatch):
    """``replace=True`` rewrites the live-band prefix via record() (resume / outbox)."""
    from agentcore.runtime.journal.persist import persist_turn_journal

    recorded: list[tuple[str, list]] = []
    appended: list[int] = []

    class Repo:
        def __init__(self, _s):
            pass

        async def record(self, *, turn_id, conversation_id, trace_id, entries) -> None:
            recorded.append((turn_id, list(entries)))

        async def append(self, *, turn_id, seq, conversation_id, trace_id, entry) -> int | None:
            appended.append(seq)
            return seq

    class Session:
        async def rollback(self):
            pass

    monkeypatch.setattr("agentcore.db.repositories.TurnJournalRepository", Repo)
    monkeypatch.setattr("agentcore.config.settings.observability_span_export_enabled", False)

    entries = [
        {"kind": "run_plan", "payload": {}},
        {"kind": "run_completed", "payload": {}},
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}},
    ]
    await persist_turn_journal(
        Session(),  # type: ignore[arg-type]
        message_id="m1",
        conversation_id="c1",
        trace_id="t",
        entries=entries,
        replace=True,
    )
    assert recorded == [("m1", entries)]
    assert appended == []


async def test_persist_turn_journal_replace_keeps_overflow_terminals(monkeypatch):
    """Resume / second-pause replace=True must not drop overflow-band terminals."""
    from agentcore.runtime.journal.persist import persist_turn_journal
    from agentcore.runtime.journal.seq_space import (
        JOURNAL_OVERFLOW_SEQ_START,
        replace_prefix_map,
    )

    store: dict[str, dict[str, dict]] = {
        "m1": {
            "0": {"kind": "run_plan", "payload": {"execution_id": "e1"}},
            "1": {"kind": "team_preview_required", "payload": {"checkpoint_id": "ck-1"}},
            str(JOURNAL_OVERFLOW_SEQ_START): {
                "kind": "run_completed",
                "payload": {"run_id": "w1"},
            },
            str(JOURNAL_OVERFLOW_SEQ_START + 1): {
                "kind": "execution_completed",
                "payload": {"execution_id": "e1"},
            },
        }
    }

    class Repo:
        def __init__(self, _s):
            pass

        async def record(self, *, turn_id, conversation_id, trace_id, entries) -> None:
            del conversation_id, trace_id
            store[turn_id] = replace_prefix_map(list(entries), store.get(turn_id, {}))

        async def load(self, turn_id) -> list:
            mapped = store.get(turn_id, {})
            return [mapped[k] for k in sorted(mapped, key=lambda key: int(key))]

        async def append(self, **_kw) -> int | None:
            raise AssertionError("replace must use record()")

    class Session:
        async def rollback(self):
            pass

    monkeypatch.setattr("agentcore.db.repositories.TurnJournalRepository", Repo)
    monkeypatch.setattr("agentcore.config.settings.observability_span_export_enabled", False)

    snapshot = [
        {"kind": "run_plan", "payload": {"execution_id": "e1"}},
        {"kind": "team_preview_required", "payload": {"checkpoint_id": "ck-2"}},
    ]
    await persist_turn_journal(
        Session(),  # type: ignore[arg-type]
        message_id="m1",
        conversation_id="c1",
        trace_id="t",
        entries=snapshot,
        replace=True,
    )
    loaded = await Repo(None).load("m1")
    kinds = [e.get("kind") for e in loaded]
    assert kinds[:2] == ["run_plan", "team_preview_required"]
    assert loaded[1]["payload"]["checkpoint_id"] == "ck-2"
    assert "run_completed" in kinds
    assert "execution_completed" in kinds
    assert loaded[kinds.index("run_completed")]["payload"]["run_id"] == "w1"


async def test_persist_turn_journal_replace_keeps_unlisted_late_fact(monkeypatch):
    """A late higher-seq fact of a non-overflow kind must survive prefix rewrite."""
    from agentcore.runtime.journal.persist import persist_turn_journal
    from agentcore.runtime.journal.seq_space import replace_prefix_map

    store: dict[str, dict[str, dict]] = {
        "m1": {
            "0": {"kind": "run_plan", "payload": {}},
            "1": {"kind": "team_preview_required", "payload": {"checkpoint_id": "ck-1"}},
            "2": {"kind": "note", "payload": {"content": "late-unrelated"}},
        }
    }

    class Repo:
        def __init__(self, _s):
            pass

        async def record(self, *, turn_id, conversation_id, trace_id, entries) -> None:
            del conversation_id, trace_id
            store[turn_id] = replace_prefix_map(list(entries), store.get(turn_id, {}))

        async def load(self, turn_id) -> list:
            mapped = store.get(turn_id, {})
            return [mapped[k] for k in sorted(mapped, key=lambda key: int(key))]

        async def append(self, **_kw) -> int | None:
            raise AssertionError("replace must use record()")

    class Session:
        async def rollback(self):
            pass

    monkeypatch.setattr("agentcore.db.repositories.TurnJournalRepository", Repo)
    monkeypatch.setattr("agentcore.config.settings.observability_span_export_enabled", False)

    await persist_turn_journal(
        Session(),  # type: ignore[arg-type]
        message_id="m1",
        conversation_id="c1",
        trace_id="t",
        entries=[
            {"kind": "run_plan", "payload": {}},
            {"kind": "team_preview_required", "payload": {"checkpoint_id": "ck-2"}},
        ],
        replace=True,
    )
    loaded = await Repo(None).load("m1")
    kinds = [e.get("kind") for e in loaded]
    assert kinds[:2] == ["run_plan", "team_preview_required"]
    assert "note" in kinds
    assert loaded[kinds.index("note")]["payload"]["content"] == "late-unrelated"


async def test_persist_turn_journal_pause_snapshot_cannot_cover_cancelled(
    monkeypatch,
):
    """A later pause prefix must not leave turn_end(paused)/missing as the close."""
    from agentcore.runtime.journal import last_turn_end_finish, runs_from_entries
    from agentcore.runtime.journal.persist import persist_turn_journal
    from agentcore.runtime.journal.seq_space import replace_prefix_map

    store: dict[str, dict[str, dict]] = {
        "m1": {
            "0": {"kind": "run_started", "payload": {"id": "r1"}},
            "1": {"kind": "turn_end", "payload": {"finish_reason": "cancelled"}},
        }
    }
    appended: list[dict] = []

    class Repo:
        def __init__(self, _s):
            pass

        async def record(self, *, turn_id, conversation_id, trace_id, entries) -> None:
            del conversation_id, trace_id
            store[turn_id] = replace_prefix_map(list(entries), store.get(turn_id, {}))

        async def load(self, turn_id) -> list:
            mapped = store.get(turn_id, {})
            return [mapped[k] for k in sorted(mapped, key=lambda key: int(key))]

        async def append(self, *, turn_id, seq, conversation_id, trace_id, entry) -> int:
            del seq, conversation_id, trace_id
            mapped = store.setdefault(turn_id, {})
            next_seq = max((int(k) for k in mapped), default=-1) + 1
            mapped[str(next_seq)] = entry
            appended.append(entry)
            return next_seq

    class Session:
        async def rollback(self):
            pass

    monkeypatch.setattr("agentcore.db.repositories.TurnJournalRepository", Repo)
    monkeypatch.setattr("agentcore.config.settings.observability_span_export_enabled", False)

    await persist_turn_journal(
        Session(),  # type: ignore[arg-type]
        message_id="m1",
        conversation_id="c1",
        trace_id="t",
        entries=[
            {"kind": "run_started", "payload": {"id": "r1"}},
            {"kind": "turn_paused", "payload": {"checkpoint_id": "cp"}},
        ],
        replace=True,
    )
    loaded = await Repo(None).load("m1")
    assert last_turn_end_finish(loaded) == "cancelled"
    assert appended == [
        {"kind": "turn_end", "payload": {"finish_reason": "cancelled"}, "ts": None}
    ]
    assert runs_from_entries(loaded)["finish_reason"] == "cancelled"


async def test_persist_turn_journal_keeps_cancelled_past_kept_paused_tail(
    monkeypatch,
):
    """Incoming cancelled closer must win over a kept-tail turn_end(paused)."""
    from agentcore.runtime.journal import last_turn_end_finish, runs_from_entries
    from agentcore.runtime.journal.persist import persist_turn_journal
    from agentcore.runtime.journal.seq_space import replace_prefix_map

    store: dict[str, dict[str, dict]] = {
        "m1": {
            "0": {"kind": "run_started", "payload": {"id": "r1"}},
            "1": {"kind": "run_completed", "payload": {"id": "r1"}},
            "2": {"kind": "turn_end", "payload": {"finish_reason": "paused"}},
        }
    }

    class Repo:
        def __init__(self, _s):
            pass

        async def record(self, *, turn_id, conversation_id, trace_id, entries) -> None:
            del conversation_id, trace_id
            store[turn_id] = replace_prefix_map(list(entries), store.get(turn_id, {}))

        async def load(self, turn_id) -> list:
            mapped = store.get(turn_id, {})
            return [mapped[k] for k in sorted(mapped, key=lambda key: int(key))]

        async def append(self, *, turn_id, seq, conversation_id, trace_id, entry) -> int:
            del seq, conversation_id, trace_id
            mapped = store.setdefault(turn_id, {})
            next_seq = max((int(k) for k in mapped), default=-1) + 1
            mapped[str(next_seq)] = entry
            return next_seq

    class Session:
        async def rollback(self):
            pass

    monkeypatch.setattr("agentcore.db.repositories.TurnJournalRepository", Repo)
    monkeypatch.setattr("agentcore.config.settings.observability_span_export_enabled", False)

    # n=2 prefix rewrite keeps seq>=2 turn_end(paused) unless persist re-appends.
    await persist_turn_journal(
        Session(),  # type: ignore[arg-type]
        message_id="m1",
        conversation_id="c1",
        trace_id="t",
        entries=[
            {"kind": "run_started", "payload": {"id": "r1"}},
            {"kind": "turn_end", "payload": {"finish_reason": "cancelled"}},
        ],
        replace=True,
    )
    loaded = await Repo(None).load("m1")
    assert last_turn_end_finish(loaded) == "cancelled"
    assert runs_from_entries(loaded)["finish_reason"] == "cancelled"


async def test_salvage_writes_incomplete_status(monkeypatch):
    upserted: dict = {}

    class MsgRepo:
        def __init__(self, _s):
            pass

        async def upsert_assistant(self, **kw):
            upserted.update(kw)
            return SimpleNamespace(id=kw["message_id"])

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", MsgRepo)
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", AsyncMock())

    await CloudStore().salvage(
        journal=[],
        content="partial reply",
        conversation_id="c1",
        trace_id="t" * 32,
        message_id="m1",
    )
    assert upserted["metadata"]["status"] == MESSAGE_STATUS_INCOMPLETE
    assert "partial reply" in upserted["content"]
