"""Class B empty-fail delete predicate (stream_chat this-send only)."""

from contextlib import asynccontextmanager

import pytest

from agentcore.conversation import zero_output_rollback as zor
from agentcore.conversation.zero_output_rollback import (
    ZERO_OUTPUT_SEND_REFUSAL_CODES,
    maybe_delete_zero_output_send,
    should_delete_zero_output_send,
    should_delete_zero_output_send_result,
)
from agentcore.core.error_codes import ErrorCode

# Class A preflight — must stay disjoint from the Class B small set.
_CLASS_A_PRECHECK_CODES = frozenset(
    {
        ErrorCode.LLM_KEY_REQUIRED,
        ErrorCode.QUOTA_EXCEEDED,
        ErrorCode.RATE_LIMITED,
        ErrorCode.PLATFORM_BILLING_UNAVAILABLE,
    }
)


def _yes(**overrides):
    base = dict(
        error_code=ErrorCode.LLM_RATE_LIMIT,
        content="",
        tokens=0,
        has_tool_call=False,
        has_delegated_workers=False,
        user_created_this_send=True,
    )
    base.update(overrides)
    return should_delete_zero_output_send(**base)


def test_empty_failure_deletes_for_each_class_b_code():
    for code in (
        ErrorCode.LLM_RATE_LIMIT,
        ErrorCode.LLM_KEY_INVALID,
        ErrorCode.LLM_INSUFFICIENT_BALANCE,
    ):
        assert _yes(error_code=code) is True


def test_has_body_does_not_delete():
    assert _yes(content="半句") is False
    assert _yes(content="  还有字  ") is False


def test_has_tool_call_does_not_delete():
    assert _yes(has_tool_call=True) is False


def test_has_delegated_workers_does_not_delete():
    assert _yes(has_delegated_workers=True) is False


def test_has_tokens_does_not_delete():
    assert _yes(tokens=1) is False
    assert _yes(tokens=12) is False


def test_wrong_code_does_not_delete():
    for code in (
        ErrorCode.LLM_KEY_REQUIRED,
        ErrorCode.QUOTA_EXCEEDED,
        ErrorCode.RATE_LIMITED,
        ErrorCode.PLATFORM_BILLING_UNAVAILABLE,
        ErrorCode.LLM_TIMEOUT,
        ErrorCode.PIPELINE_ERROR,
        ErrorCode.LLM_ERROR,
        None,
        "",
    ):
        assert _yes(error_code=code) is False


def test_not_this_send_does_not_delete():
    assert _yes(user_created_this_send=False) is False


def test_paused_does_not_delete():
    from agentcore.runtime.events import FinishReason

    assert _yes(outcome="paused") is False
    assert _yes(finish_reason="paused") is False
    assert _yes(outcome="paused", finish_reason="paused") is False
    assert _yes(finish_reason=FinishReason.PAUSED) is False
    assert _yes(outcome="error") is True
    assert _yes(finish_reason="error") is True


def test_class_b_codes_are_the_small_set_and_disjoint_from_class_a():
    assert frozenset(
        {
            ErrorCode.LLM_RATE_LIMIT,
            ErrorCode.LLM_KEY_INVALID,
            ErrorCode.LLM_INSUFFICIENT_BALANCE,
        }
    ) == ZERO_OUTPUT_SEND_REFUSAL_CODES
    assert ZERO_OUTPUT_SEND_REFUSAL_CODES.isdisjoint(_CLASS_A_PRECHECK_CODES)


def _empty_fail_result(**overrides) -> dict:
    base = {
        "message_id": "a1",
        "content": "",
        "error_code": ErrorCode.LLM_RATE_LIMIT,
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "journal_entries": [],
        "cost_runs": [],
    }
    base.update(overrides)
    return base


def test_result_empty_failure_deletes():
    assert (
        should_delete_zero_output_send_result(
            _empty_fail_result(), user_created_this_send=True
        )
        is True
    )


def test_result_body_or_tool_or_token_does_not_delete():
    assert (
        should_delete_zero_output_send_result(
            _empty_fail_result(content="半句"),
            user_created_this_send=True,
        )
        is False
    )
    assert (
        should_delete_zero_output_send_result(
            _empty_fail_result(
                journal_entries=[{"kind": "tool_use_start", "payload": {}}]
            ),
            user_created_this_send=True,
        )
        is False
    )
    assert (
        should_delete_zero_output_send_result(
            _empty_fail_result(input_tokens=8),
            user_created_this_send=True,
        )
        is False
    )


def test_result_wrong_code_does_not_delete():
    assert (
        should_delete_zero_output_send_result(
            _empty_fail_result(error_code=ErrorCode.LLM_KEY_REQUIRED),
            user_created_this_send=True,
        )
        is False
    )


def test_result_paused_llm_rate_limit_does_not_delete():
    """Empty-body LLM_RATE_LIMIT that already paused must keep the send."""
    from agentcore.conversation.zero_output_rollback import (
        result_from_local_turn_writeback,
    )

    assert (
        should_delete_zero_output_send_result(
            _empty_fail_result(outcome="paused"),
            user_created_this_send=True,
        )
        is False
    )
    assert (
        should_delete_zero_output_send_result(
            _empty_fail_result(finish_reason="paused"),
            user_created_this_send=True,
        )
        is False
    )
    assert (
        should_delete_zero_output_send_result(
            _empty_fail_result(outcome="paused", finish_reason="paused"),
            user_created_this_send=True,
        )
        is False
    )
    mapped = result_from_local_turn_writeback(
        message_id="a1",
        content="",
        runs={
            "error": {"code": ErrorCode.LLM_RATE_LIMIT, "message": "限流"},
            "outcome": "paused",
            "finish_reason": "paused",
        },
    )
    assert (
        should_delete_zero_output_send_result(mapped, user_created_this_send=True)
        is False
    )


def test_missing_result_does_not_delete():
    assert (
        should_delete_zero_output_send_result(None, user_created_this_send=True)
        is False
    )


class _TxnSession:
    def __init__(self) -> None:
        self.alive = {"a1", "u1"}
        self._pending_dead: set[str] = set()
        self.commits = 0

    async def commit(self) -> None:
        self.alive -= self._pending_dead
        self._pending_dead.clear()
        self.commits += 1

    async def rollback(self) -> None:
        self._pending_dead.clear()

    async def flush(self) -> None:
        return None


class _TrackingRepo:
    def __init__(self, session: _TxnSession, *, fail_on: str | None = None) -> None:
        self._session = session
        self._fail_on = fail_on
        self.calls: list[dict[str, object]] = []

    async def delete_by_id(
        self, message_id: str, *, conversation_id: str, commit: bool = True
    ) -> bool:
        self.calls.append(
            {"id": message_id, "conversation_id": conversation_id, "commit": commit}
        )
        if self._fail_on and message_id == self._fail_on:
            raise RuntimeError("second delete failed")
        self._session._pending_dead.add(message_id)
        if commit:
            await self._session.commit()
        else:
            await self._session.flush()
        return True


def _patch_rollback_txn(monkeypatch, session: _TxnSession, repo: _TrackingRepo):
    @asynccontextmanager
    async def _factory():
        try:
            yield session
        except Exception:
            await session.rollback()
            raise

    monkeypatch.setattr(zor, "async_session_factory", _factory)
    monkeypatch.setattr(zor, "MessageRepository", lambda _session: repo)


@pytest.mark.asyncio
async def test_second_delete_failure_keeps_both_messages(monkeypatch):
    session = _TxnSession()
    repo = _TrackingRepo(session, fail_on="u1")
    _patch_rollback_txn(monkeypatch, session, repo)

    ok = await maybe_delete_zero_output_send(
        conversation_id="c1",
        user_message_id="u1",
        result=_empty_fail_result(),
        user_created_this_send=True,
    )

    assert ok is False
    assert session.commits == 0
    assert session.alive == {"a1", "u1"}
    assert [c["commit"] for c in repo.calls] == [False, False]
    assert [c["id"] for c in repo.calls] == ["a1", "u1"]


@pytest.mark.asyncio
async def test_zero_output_deletes_share_one_commit(monkeypatch):
    session = _TxnSession()
    repo = _TrackingRepo(session)
    _patch_rollback_txn(monkeypatch, session, repo)

    ok = await maybe_delete_zero_output_send(
        conversation_id="c1",
        user_message_id="u1",
        result=_empty_fail_result(),
        user_created_this_send=True,
    )

    assert ok is True
    assert session.commits == 1
    assert session.alive == set()
    assert [c["commit"] for c in repo.calls] == [False, False]


@pytest.mark.asyncio
async def test_local_finalize_and_writeback_leave_no_zero_output_turn(
    tmp_path, monkeypatch
):
    """Sidecar this-send Class B empty-fail: outbox gone, write-back does not persist."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agentcore.conversation.local_turn import record_local_turn
    from agentcore.conversation.store.outbox import (
        PHASE_READY,
        OutboxStore,
        list_outbox_records,
    )
    from agentcore.sidecar.server_pkg.turns import TurnExecutionMixin

    outbox = OutboxStore(tmp_path / "outbox")
    umid = "u-local-zero"
    mid = "a-local-zero"
    outbox.bind_turn(
        conversation_id="c1",
        user_message_id=umid,
        user_message="hello",
        message_id=mid,
        trace_id="a" * 32,
    )
    await outbox.begin_turn(conversation_id="c1", message_id=mid, trace_id="a" * 32)
    assert list_outbox_records(tmp_path / "outbox")

    host = TurnExecutionMixin.__new__(TurnExecutionMixin)
    await host._outbox_finalize(
        outbox,
        conversation_id="c1",
        user_message="hello",
        user_message_id=umid,
        trace_id="a" * 32,
        result=_empty_fail_result(message_id=mid),
        user_created_this_send=True,
    )
    assert list_outbox_records(tmp_path / "outbox") == []

    umid_keep = "u-resume-keep"
    mid_keep = "a-resume-keep"
    outbox.bind_turn(
        conversation_id="c1",
        user_message_id=umid_keep,
        user_message="hello",
        message_id=mid_keep,
        trace_id="a" * 32,
    )
    await outbox.begin_turn(
        conversation_id="c1", message_id=mid_keep, trace_id="a" * 32
    )
    await host._outbox_finalize(
        outbox,
        conversation_id="c1",
        user_message="hello",
        user_message_id=umid_keep,
        trace_id="a" * 32,
        result=_empty_fail_result(message_id=mid_keep),
        user_created_this_send=False,
    )
    kept = list_outbox_records(tmp_path / "outbox")
    assert len(kept) == 1
    assert kept[0]["user_message_id"] == umid_keep
    assert kept[0]["phase"] == PHASE_READY

    finalize = AsyncMock(side_effect=AssertionError("write-back must not persist"))
    monkeypatch.setattr(
        "agentcore.conversation.local_turn.get_cloud_store",
        lambda: SimpleNamespace(finalize=finalize),
    )
    recorded = await record_local_turn(
        conversation_id="c1",
        user_id="user-1",
        user_message="hello",
        assistant_content="",
        runs={
            "error": {"code": ErrorCode.LLM_RATE_LIMIT, "message": "限流"},
            "finish_reason": "error",
        },
        user_message_id=umid,
        message_id=mid,
        trace_id="a" * 32,
        finish_reason="error",
    )
    assert recorded["noop"] is True
    assert recorded["assistant_message_id"] is None
    finalize.assert_not_called()
