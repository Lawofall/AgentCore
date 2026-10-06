"""方案切点：切在哪、摘要绑哪一批、撤回还在不在。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from agentcore.conversation.compact_prompt import IDENTITY_LEDGER_FENCE
from agentcore.conversation.context_cut import (
    drop_preview,
    lookup_preview,
    remember_preview,
)
from agentcore.conversation.context_cut_plan import (
    NOTHING_TO_FOLD,
    PROSE_EMPTY,
    SUMMARY_TOO_LONG,
    TOO_MANY_TO_FOLD,
    ContextCutError,
    accept_cut_prose,
    context_cut_undoable,
    fold_digest,
    resolve_verbatim_start,
    take_fold,
    undo_snapshot,
)


def _row(mid: str, role: str, at: datetime) -> SimpleNamespace:
    return SimpleNamespace(id=mid, role=role, created_at=at, content=role)


def test_assistant_cut_keeps_the_user_message_that_opened_the_turn():
    t0 = datetime(2026, 10, 4, tzinfo=UTC)
    chain = [
        _row("u1", "user", t0),
        _row("a1", "assistant", t0 + timedelta(minutes=1)),
        _row("u2", "user", t0 + timedelta(minutes=2)),
        _row("a2", "assistant", t0 + timedelta(minutes=3)),
    ]
    assert resolve_verbatim_start(chain, "a2").id == "u2"
    assert resolve_verbatim_start(chain, "u2").id == "u2"


def test_assistant_without_a_leading_user_is_refused():
    t0 = datetime(2026, 10, 4, tzinfo=UTC)
    chain = [_row("a1", "assistant", t0)]
    try:
        resolve_verbatim_start(chain, "a1")
    except ContextCutError as exc:
        assert "没有你的话" in exc.message
    else:
        raise AssertionError("expected refusal")


def test_take_fold_refuses_empty_and_oversized():
    row = _row("m", "user", datetime(2026, 10, 4, tzinfo=UTC))
    try:
        take_fold([], max_fold=4, more=False)
    except ContextCutError as exc:
        assert exc.message == NOTHING_TO_FOLD
    else:
        raise AssertionError("expected empty refusal")
    try:
        take_fold([row], max_fold=1, more=True)
    except ContextCutError as exc:
        assert exc.message == TOO_MANY_TO_FOLD
    else:
        raise AssertionError("expected overflow refusal")
    assert take_fold([row], max_fold=4, more=False) == [row]


def test_digest_changes_when_the_fold_set_changes():
    t0 = datetime(2026, 10, 4, tzinfo=UTC)
    first = _row("a", "user", t0)
    second = _row("b", "assistant", t0 + timedelta(minutes=1))
    assert fold_digest(None, [first]) != fold_digest(None, [first, second])
    assert fold_digest(t0, [first]) != fold_digest(None, [first])
    assert len(fold_digest(None, [first])) == 64


def test_undo_matches_only_the_watermark_this_cut_wrote():
    t0 = datetime(2026, 10, 4, 1, tzinfo=UTC)
    later = t0 + timedelta(hours=1)
    conv = SimpleNamespace(
        compaction_summary="旧",
        compacted_through=None,
        compaction_input_tokens=12,
    )
    snapshot = undo_snapshot(conv, cut_watermark=later)
    assert snapshot["summary"] == "旧"
    assert snapshot["input_tokens"] == 12
    assert context_cut_undoable(snapshot, later) is True
    assert context_cut_undoable(snapshot, t0) is False
    assert context_cut_undoable(None, later) is False
    assert context_cut_undoable(snapshot, None) is False


def test_accept_cut_prose_keeps_the_edit_and_reattaches_the_ledger():
    stored = accept_cut_prose(
        "改过的方案",
        "路径：\n- a.ts",
        max_chars=20,
    )
    assert stored.startswith("改过的方案")
    assert IDENTITY_LEDGER_FENCE in stored
    assert stored.endswith("路径：\n- a.ts")


def test_accept_cut_prose_drops_a_client_ledger():
    forged = f"留下这句{IDENTITY_LEDGER_FENCE}路径：\n- forged"
    stored = accept_cut_prose(forged, "路径：\n- real.ts", max_chars=40)
    assert "forged" not in stored
    assert "real.ts" in stored
    assert stored.startswith("留下这句")


def test_accept_cut_prose_refuses_blank_and_overlong():
    try:
        accept_cut_prose("  \n", "", max_chars=10)
    except ContextCutError as exc:
        assert exc.message == PROSE_EMPTY
    else:
        raise AssertionError("expected blank refusal")
    try:
        accept_cut_prose("一二三四五", "", max_chars=4)
    except ContextCutError as exc:
        assert exc.message == SUMMARY_TOO_LONG
    else:
        raise AssertionError("expected length refusal")


def test_preview_cache_returns_the_accepted_text_then_forgets_it():
    remember_preview("conv", "a" * 64, "方案已定")
    assert lookup_preview("conv", "a" * 64) == "方案已定"
    assert lookup_preview("conv", "b" * 64) is None
    drop_preview("conv", "a" * 64)
    assert lookup_preview("conv", "a" * 64) is None
