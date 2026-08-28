"""Tests for long-conversation compaction (conversation/compaction.py).

The summary-prefixed loader (load_chat_context) is exercised against a real schema
at the integration layer; here everything else is tested in isolation — the pure
decision logic, the LLM summarize step (fake provider), the live token trigger /
dedupe, and the runner's branch logic (compact_conversation, with its session
factory + repositories + provider all faked, so no DB is required).
"""

import asyncio
import contextlib
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import agentcore.conversation.compaction as compaction
from agentcore.conversation.compaction import (
    _COMPACT_SYSTEM_PROMPT,
    _render_fold,
    _select_fold,
    _summarize,
    _truncate_head_tail,
)
from agentcore.conversation.history import _summary_block
from agentcore.llm import LLMRequest, LLMResponse
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH


def _msg(role: str, content: str, created_at: int = 0) -> SimpleNamespace:
    """A Message stand-in: the compaction helpers only read role/content/created_at."""
    return SimpleNamespace(role=role, content=content, created_at=created_at)


# --- _select_fold (the fold-vs-keep decision, pure) ---


def test_select_fold_keeps_recency_window():
    batch = [_msg("user", f"m{i}", i) for i in range(30)]
    fold = _select_fold(batch, recency=20, min_fold=4)
    # 30 − 20 = 10 oldest fold; the newest 20 stay verbatim.
    assert [m.content for m in fold] == [f"m{i}" for i in range(10)]


def test_conversation_summary_context_compacted_flag_only():
    """REST summary exposes a boolean flag, never the rolling-summary body."""
    from agentcore.api.schemas.conversations import (
        ConversationSummary,
        conversation_summary_from_orm,
    )

    base = dict(
        id="c1",
        title="t",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    both = conversation_summary_from_orm(
        SimpleNamespace(
            **base,
            compaction_summary="## 事实\n- X",
            compacted_through=datetime(2026, 1, 1, 12, tzinfo=UTC),
            folder_id=None,
            local_container_root_id=None,
            pinned=False,
            archived=False,
            permission_axes={},
            deep_research_auto=False,
            model_profile_id=None,
        )
    )
    assert both.context_compacted is True
    dumped = both.model_dump()
    assert "compaction_summary" not in dumped
    assert "compacted_through" not in dumped

    missing = conversation_summary_from_orm(
        SimpleNamespace(
            **base,
            compaction_summary="orphan",
            compacted_through=None,
            folder_id=None,
            local_container_root_id=None,
            pinned=False,
            archived=False,
            permission_axes={},
            deep_research_auto=False,
            model_profile_id=None,
        )
    )
    assert missing.context_compacted is False
    assert ConversationSummary.model_fields["context_compacted"].default is False


def test_select_fold_noop_when_tail_within_recency():
    batch = [_msg("user", f"m{i}", i) for i in range(12)]
    assert _select_fold(batch, recency=20, min_fold=4) == []


def test_select_fold_noop_below_min_fold():
    # 23 − 20 = 3 foldable, below the min_fold floor of 4 → not worth an LLM call.
    batch = [_msg("user", f"m{i}", i) for i in range(23)]
    assert _select_fold(batch, recency=20, min_fold=4) == []


def test_select_fold_fires_at_min_fold_boundary():
    batch = [_msg("user", f"m{i}", i) for i in range(24)]
    fold = _select_fold(batch, recency=20, min_fold=4)
    assert len(fold) == 4
    # The LAST folded message is the new watermark — sequential, oldest-first.
    assert fold[-1].created_at == 3


def test_select_fold_floors_to_user_turn_boundary():
    """C&M-04: odd fold count would leave the tail starting on assistant.

    Alternating u/a, 25 msgs, recency=20 → naive fold=5 (ends mid-turn, tail[0]=assistant).
    Floor to 4 so the watermark sits on a complete turn and the tail starts on user.
    """
    batch = _msgs(25)
    fold = _select_fold(batch, recency=20, min_fold=4)
    assert len(fold) == 4
    assert fold[-1].role == "assistant"
    assert batch[len(fold)].role == "user"


def test_select_fold_noop_when_flooring_drops_below_min_fold():
    # Naive fold=5, floor to 4; with min_fold=5 the floored count is not worth a call.
    batch = _msgs(25)
    assert _select_fold(batch, recency=20, min_fold=5) == []


# --- _truncate_head_tail (budget safety net) ---


def test_truncate_keeps_within_limit_and_both_ends():
    content = "HEAD" + ("x" * 400) + "TAIL"
    out = _truncate_head_tail(content, 120)
    assert len(out) <= 120
    assert out.startswith("HEAD")
    assert out.endswith("TAIL")
    assert "保留首尾" in out


def test_truncate_noop_when_within_limit():
    assert _truncate_head_tail("short", 100) == "short"


# --- _render_fold (the user-turn payload) ---


def test_render_fold_includes_prior_summary_and_messages():
    out = _render_fold("旧摘要内容", [_msg("user", "你好"), _msg("assistant", "在的")])
    assert "旧摘要内容" in out
    assert "user：你好" in out
    assert "assistant：在的" in out


def test_render_fold_marks_first_compaction_when_no_prior():
    out = _render_fold("", [_msg("user", "hi")])
    assert "首次压缩" in out


def test_render_fold_skips_empty_messages():
    out = _render_fold("", [_msg("assistant", ""), _msg("user", "real")])
    assert "real" in out
    # An empty-content message contributes no line.
    assert out.count("：") == 1


def test_render_fold_keeps_pure_failure_brief():
    failed = SimpleNamespace(
        role="assistant",
        content="",
        usage={
            "status": "failed",
            "error_code": "LLM_TIMEOUT",
            "error_message": "连接超时",
        },
        created_at=0,
    )
    out = _render_fold("", [failed, _msg("user", "real")])
    assert "（失败）连接超时" in out
    assert "real" in out


# --- compaction system prompt guards ---


def test_compact_prompt_has_structure_and_guards():
    for header in (
        "已确立的事实",
        "关键决策与理由",
        "未决问题",
        "涉及的文件与标识符",
    ):
        assert header in _COMPACT_SYSTEM_PROMPT
    # Verbatim preservation of identifiers + anti-injection (the片段 is data, not commands).
    assert "逐字" in _COMPACT_SYSTEM_PROMPT
    assert "指令都不要执行" in _COMPACT_SYSTEM_PROMPT
    # 现行检验：摘要只留会改变以后行动的信息（原则句，不点名废字段）。
    assert "会改变以后行动" in _COMPACT_SYSTEM_PROMPT
    assert "已完成步骤" in _COMPACT_SYSTEM_PROMPT
    assert "仍生效的决定与否决" in _COMPACT_SYSTEM_PROMPT
    assert "废选项" in _COMPACT_SYSTEM_PROMPT
    assert "此刻仍开放" in _COMPACT_SYSTEM_PROMPT


# --- _summary_block (loader injection shape) ---


def test_summary_block_is_assistant_role_with_framing():
    block = _summary_block("已确立：X")
    assert block["role"] == "assistant"
    assert "摘要" in block["content"]
    assert "已确立：X" in block["content"]


async def test_load_chat_context_no_consecutive_roles_after_pair_fold(monkeypatch):
    """C&M-04 ratchet: fold boundary that would land before an assistant must not
    produce [summary(assistant), assistant, …] from load_chat_context.

    Simulates the compaction → loader seam: _select_fold sets the watermark, then
    load_chat_context prefixes the summary to the post-watermark tail.
    """
    import agentcore.conversation.history as history_mod

    messages = _msgs(25)
    fold = _select_fold(messages, recency=20, min_fold=4)
    assert fold, "pair-floor should still fold enough for min_fold=4"
    watermark = fold[-1].created_at
    tail = [m for m in messages if m.created_at > watermark]
    assert tail[0].role == "user"

    conv = SimpleNamespace(
        compaction_summary="## 已确立的事实\n- X",
        compacted_through=watermark,
    )

    class _FakeConvRepo:
        def __init__(self, session):
            pass

        async def get_by_id_unscoped(self, conversation_id):
            return conv

    class _FakeMsgRepo:
        def __init__(self, session):
            pass

        async def list_recent_after(self, conversation_id, *, after, limit):
            return [m for m in messages if m.created_at > after][:limit]

    monkeypatch.setattr(history_mod, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(history_mod, "MessageRepository", _FakeMsgRepo)
    monkeypatch.setattr(history_mod.settings, "compaction_context_max_messages", 40, raising=True)

    out = await history_mod.load_chat_context(SimpleNamespace(), "c1")
    assert out[0]["role"] == "assistant"  # summary block
    assert "摘要" in out[0]["content"]
    roles = [item["role"] for item in out]
    assert all(a != b for a, b in zip(roles, roles[1:], strict=False))


async def test_load_chat_context_realigns_when_cap_drops_the_boundary_user(monkeypatch):
    """CTX-A4 ratchet: the loader's OWN cap-driven cut must keep the near end user-led.

    _select_fold floors the fold to a user boundary, but a stalled compaction lets the
    un-folded tail outgrow compaction_context_max_messages — and list_recent_after then
    drops the oldest of that tail, which is exactly the boundary user the fold preserved.
    The window handed to a strict backend must still read [summary(assistant), user, …].
    """
    import agentcore.conversation.history as history_mod

    messages = _msgs(60)
    fold = _select_fold(messages, recency=20, min_fold=4)
    watermark = fold[-1].created_at
    tail = [m for m in messages if m.created_at > watermark]
    assert tail[0].role == "user"  # the fold's own cut is aligned

    conv = SimpleNamespace(
        compaction_summary="## 已确立的事实\n- X",
        compacted_through=watermark,
    )

    class _FakeConvRepo:
        def __init__(self, session):
            pass

        async def get_by_id_unscoped(self, conversation_id):
            return conv

    class _FakeMsgRepo:
        def __init__(self, session):
            pass

        async def list_recent_after(self, conversation_id, *, after, limit):
            # Production semantics: recent-biased — the OLDEST of the tail is what drops.
            return [m for m in messages if m.created_at > after][-limit:]

    monkeypatch.setattr(history_mod, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(history_mod, "MessageRepository", _FakeMsgRepo)
    # One under the tail length: the cap drops exactly tail[0], the boundary user.
    monkeypatch.setattr(
        history_mod.settings,
        "compaction_context_max_messages",
        len(tail) - 1,
        raising=True,
    )

    out = await history_mod.load_chat_context(SimpleNamespace(), "c1")
    assert out[0]["role"] == "assistant"  # summary block
    assert out[1]["role"] == "user"  # the orphaned assistant went with its dropped prompt
    roles = [item["role"] for item in out]
    assert all(a != b for a, b in zip(roles, roles[1:], strict=False))
    assert out[-1]["content"] == messages[-1].content  # newest turns are never the ones cut


def test_from_first_user_drops_an_all_assistant_remainder():
    """Terminal case of the same cut: nothing to align to → the summary rides alone."""
    from agentcore.conversation.history import _from_first_user

    assert _from_first_user([{"role": "assistant", "content": "orphan"}]) == []


# --- _summarize (async, fake provider) ---


class _FakeProvider:
    """Minimal LLMProvider stub: returns canned content and records requests."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(content=self._content)


async def test_summarize_uses_flash_non_thinking_and_injects_budget(monkeypatch):
    monkeypatch.setattr(compaction.settings, "compaction_summary_char_budget", 4000, raising=True)
    provider = _FakeProvider("## 已确立的事实\n- X")
    out = await _summarize(
        provider, "", [_msg("user", "hi")], model=DEEPSEEK_V4_FLASH, conversation_id="c1"
    )
    assert out == "## 已确立的事实\n- X"
    req = provider.requests[0]
    assert req.model == "deepseek-v4-flash"
    assert req.thinking is False
    # The budget placeholder is resolved into the real system prompt, never leaked.
    assert "__BUDGET__" not in req.messages[0].content
    assert "4000" in req.messages[0].content


async def test_summarize_truncates_overlong_output(monkeypatch):
    monkeypatch.setattr(compaction.settings, "compaction_summary_char_budget", 100, raising=True)
    provider = _FakeProvider("H" + "x" * 500 + "T")
    out = await _summarize(
        provider, "", [_msg("user", "hi")], model=DEEPSEEK_V4_FLASH, conversation_id="c1"
    )
    assert len(out) <= 100


async def test_summarize_hands_the_provider_who_is_waiting():
    """折叠这一趟能不能睡在 429 上，全看回合有没有被它挡着——这个信号必须传到底。"""
    provider = _FakeProvider("## 已确立的事实\n- X")
    await _summarize(
        provider, "", [_msg("user", "hi")], model=DEEPSEEK_V4_FLASH, conversation_id="c1"
    )
    await _summarize(
        provider,
        "",
        [_msg("user", "hi")],
        model=DEEPSEEK_V4_FLASH,
        conversation_id="c1",
        user_waiting=True,
    )
    patience = [r.retry_patience_seconds for r in provider.requests]
    assert patience == [compaction._COMPACT_TIMEOUT_SECONDS, 0.0]


async def test_summarize_returns_empty_on_timeout(monkeypatch):
    monkeypatch.setattr(compaction, "_COMPACT_TIMEOUT_SECONDS", 0.01, raising=True)

    class _SlowProvider:
        async def complete(self, request: LLMRequest) -> LLMResponse:
            await asyncio.sleep(1)
            return LLMResponse(content="never")

    out = await _summarize(
        _SlowProvider(), "", [_msg("user", "hi")], model=DEEPSEEK_V4_FLASH, conversation_id="c1"
    )
    assert out == ""


# --- schedule_compaction_if_due (dual trigger + dedupe) ---


async def test_if_due_fires_on_token_threshold(monkeypatch):
    monkeypatch.setattr(compaction.settings, "compaction_enabled", True, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_trigger_input_tokens", 100, raising=True)
    calls: list[tuple[str, int | None]] = []
    fired = asyncio.Event()

    async def _rec(conversation_id, *, trigger_input_tokens=None, user_waiting=False):
        calls.append((conversation_id, trigger_input_tokens))
        fired.set()

    async def _never_message(_cid):
        raise AssertionError("token due must short-circuit before DB message check")

    monkeypatch.setattr(compaction, "compact_conversation", _rec, raising=True)
    monkeypatch.setattr(compaction, "_is_message_due", _never_message, raising=True)
    await compaction.schedule_compaction_if_due("c1", 150)
    await asyncio.wait_for(fired.wait(), 1)
    assert calls == [("c1", 150)]
    await asyncio.sleep(0)
    assert "c1" not in compaction._inflight_tasks


async def test_if_due_fires_on_message_trigger(monkeypatch):
    """Message due: DB ``_select_fold`` non-empty with message_trigger_min_fold, even under token."""
    monkeypatch.setattr(compaction.settings, "compaction_enabled", True, raising=True)
    monkeypatch.setattr(
        compaction.settings, "compaction_trigger_input_tokens", 64_000, raising=True
    )
    calls: list[tuple[str, int | None]] = []
    fired = asyncio.Event()

    async def _rec(conversation_id, *, trigger_input_tokens=None, user_waiting=False):
        calls.append((conversation_id, trigger_input_tokens))
        fired.set()

    async def _msg_due(_cid):
        return True

    monkeypatch.setattr(compaction, "compact_conversation", _rec, raising=True)
    monkeypatch.setattr(compaction, "_is_message_due", _msg_due, raising=True)
    await compaction.schedule_compaction_if_due("c1", 100)  # under token threshold
    await asyncio.wait_for(fired.wait(), 1)
    assert calls == [("c1", 100)]


async def test_if_due_noop_when_neither_trigger(monkeypatch):
    monkeypatch.setattr(compaction.settings, "compaction_enabled", True, raising=True)
    monkeypatch.setattr(
        compaction.settings, "compaction_trigger_input_tokens", 64_000, raising=True
    )
    calls: list[str] = []

    async def _rec(conversation_id, *, trigger_input_tokens=None, user_waiting=False):
        calls.append(conversation_id)

    async def _msg_due(_cid):
        return False

    monkeypatch.setattr(compaction, "compact_conversation", _rec, raising=True)
    monkeypatch.setattr(compaction, "_is_message_due", _msg_due, raising=True)
    await compaction.schedule_compaction_if_due("c1", 100)
    await asyncio.sleep(0.02)
    assert calls == []


async def test_if_due_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(compaction.settings, "compaction_enabled", False, raising=True)
    calls: list[str] = []

    async def _rec(conversation_id, *, trigger_input_tokens=None, user_waiting=False):
        calls.append(conversation_id)

    monkeypatch.setattr(compaction, "compact_conversation", _rec, raising=True)
    await compaction.schedule_compaction_if_due("c1", 10_000_000)
    await asyncio.sleep(0.02)
    assert calls == []


async def test_if_due_dedupes_while_inflight(monkeypatch):
    monkeypatch.setattr(compaction.settings, "compaction_enabled", True, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_trigger_input_tokens", 100, raising=True)
    calls: list[str] = []

    async def _rec(conversation_id, *, trigger_input_tokens=None, user_waiting=False):
        calls.append(conversation_id)

    monkeypatch.setattr(compaction, "compact_conversation", _rec, raising=True)
    compaction._inflight_tasks["c1"] = object()  # type: ignore[assignment]
    try:
        await compaction.schedule_compaction_if_due("c1", 150)
        await asyncio.sleep(0.02)
        assert calls == []
    finally:
        compaction._inflight_tasks.pop("c1", None)


async def test_if_due_skips_during_failure_cooldown(monkeypatch):
    """Cooldown blocks both token and message triggers — no arm while active."""
    import time

    monkeypatch.setattr(compaction.settings, "compaction_enabled", True, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_trigger_input_tokens", 100, raising=True)
    calls: list[str] = []

    async def _rec(conversation_id, *, trigger_input_tokens=None, user_waiting=False):
        calls.append(conversation_id)

    async def _never_message(_cid):
        raise AssertionError("cooldown must short-circuit before DB message check")

    monkeypatch.setattr(compaction, "compact_conversation", _rec, raising=True)
    monkeypatch.setattr(compaction, "_is_message_due", _never_message, raising=True)
    compaction._failure_cooldown_until["c1"] = time.monotonic() + 60
    try:
        await compaction.schedule_compaction_if_due("c1", 150)
        await asyncio.sleep(0.02)
        assert calls == []
    finally:
        compaction._failure_cooldown_until.pop("c1", None)


async def test_if_due_arms_after_cooldown_expires(monkeypatch):
    import time

    monkeypatch.setattr(compaction.settings, "compaction_enabled", True, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_trigger_input_tokens", 100, raising=True)
    calls: list[tuple[str, int | None]] = []
    fired = asyncio.Event()

    async def _rec(conversation_id, *, trigger_input_tokens=None, user_waiting=False):
        calls.append((conversation_id, trigger_input_tokens))
        fired.set()

    monkeypatch.setattr(compaction, "compact_conversation", _rec, raising=True)
    compaction._failure_cooldown_until["c1"] = time.monotonic() - 1  # already expired
    try:
        await compaction.schedule_compaction_if_due("c1", 150)
        await asyncio.wait_for(fired.wait(), 1)
        assert calls == [("c1", 150)]
        assert "c1" not in compaction._failure_cooldown_until
    finally:
        compaction._failure_cooldown_until.pop("c1", None)
        await asyncio.sleep(0)


def test_compaction_message_due_uses_select_fold_not_history_len():
    """Message due is ``_select_fold`` on the DB batch — never turn ``history_len``.

    A batch with only 20 msgs (recency=12 → fold=8) stays not-due under min_fold=16,
    even though a loader history_len that counted a summary block could look "long".
    """
    assert compaction.compaction_message_due(_msgs(20), recency=12, min_fold=16) is False
    # 12 + 16 = 28 → foldable exactly at message-trigger boundary (all-user so no floor).
    batch = [_msg("user", f"m{i}", i) for i in range(28)]
    assert compaction.compaction_message_due(batch, recency=12, min_fold=16) is True
    # Explicit: due helper does not take / consult history_len.
    assert "history_len" not in compaction.compaction_message_due.__code__.co_varnames
    assert "history_len" not in compaction.schedule_compaction_if_due.__code__.co_varnames


def test_select_fold_recency_12_keeps_near_window():
    batch = [_msg("user", f"m{i}", i) for i in range(30)]
    fold = _select_fold(batch, recency=12, min_fold=4)
    assert len(fold) == 18
    assert [m.content for m in fold] == [f"m{i}" for i in range(18)]


def test_default_compaction_settings_match_design():
    from agentcore.config.persistence import PersistenceSettings

    defaults = PersistenceSettings()
    assert defaults.compaction_recency_messages == 12
    assert defaults.compaction_trigger_input_tokens == 32_000
    assert defaults.compaction_message_trigger_min_fold == 16
    assert defaults.compaction_min_fold_messages == 4
    assert defaults.compaction_failure_cooldown_seconds == 90
    assert defaults.compaction_near_context_ratio == 0.8
    assert defaults.compaction_near_context_tokens == 200_000
    assert defaults.compaction_near_max_passes == 3


def test_near_context_ceiling_ratio_and_absolute():
    """Near-ceiling: ratio of known window, else absolute floor."""
    assert compaction.near_context_ceiling(0, 100_000) is False
    assert compaction.near_context_ceiling(79_999, 100_000) is False
    assert compaction.near_context_ceiling(80_000, 100_000) is True
    assert compaction.near_context_ceiling(199_999, None) is False
    assert compaction.near_context_ceiling(200_000, None) is True
    assert compaction.near_context_ceiling(200_000, 0) is True  # non-positive → absolute


def test_near_context_ceiling_flash_free_uses_zen_gateway_cap():
    """Existing ``deepseek-v4-flash-free`` must near-top at 80% of 200K, not 1M/128K."""
    from agentcore.llm.model_metadata import model_metadata_for

    free = model_metadata_for("deepseek-v4-flash-free").context_length
    native = model_metadata_for("deepseek-v4-flash").context_length
    assert free == 200_000
    assert native == 1_000_000
    assert compaction.near_context_ceiling(160_000, free) is True
    assert compaction.near_context_ceiling(159_999, free) is False
    # Same 160K is far from native 1M; the retired 128K hint would have fired at 102.4K.
    assert compaction.near_context_ceiling(160_000, native) is False


async def test_ensure_before_turn_noop_when_not_near(monkeypatch):
    monkeypatch.setattr(compaction.settings, "compaction_enabled", True, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_near_context_ratio", 0.8, raising=True)
    calls: list[str] = []

    async def _rec(conversation_id, *, trigger_input_tokens=None, user_waiting=False):
        calls.append(conversation_id)
        return True

    monkeypatch.setattr(compaction, "compact_conversation", _rec, raising=True)
    wrote = await compaction.ensure_compaction_before_turn(
        "c1", input_tokens=10_000, context_length=100_000
    )
    assert wrote is False
    assert calls == []


async def test_ensure_before_turn_awaits_fold_when_near(monkeypatch):
    monkeypatch.setattr(compaction.settings, "compaction_enabled", True, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_near_context_ratio", 0.8, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_near_max_passes", 3, raising=True)
    calls: list[int] = []
    waiting: list[bool] = []

    async def _rec(conversation_id, *, trigger_input_tokens=None, user_waiting=False):
        calls.append(trigger_input_tokens or 0)
        waiting.append(user_waiting)
        # First pass writes; second finds nothing — stops the loop.
        return len(calls) == 1

    monkeypatch.setattr(compaction, "compact_conversation", _rec, raising=True)
    compaction._inflight_tasks.pop("c-near", None)
    wrote = await compaction.ensure_compaction_before_turn(
        "c-near", input_tokens=90_000, context_length=100_000
    )
    assert wrote is True
    assert calls == [90_000, 90_000]
    # 回合在等这两趟：它们不许睡在上游冷却上（llm.provider.call_budget）。
    assert waiting == [True, True]
    assert "c-near" not in compaction._inflight_tasks


async def test_post_turn_pass_is_not_marked_as_blocking_a_turn(monkeypatch):
    """后台那趟没人等——它照旧睡得起 32 秒，这正是预算机制要救的那批调用。"""
    monkeypatch.setattr(compaction.settings, "compaction_enabled", True, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_trigger_input_tokens", 100, raising=True)
    waiting: list[bool] = []
    fired = asyncio.Event()

    async def _rec(conversation_id, *, trigger_input_tokens=None, user_waiting=False):
        waiting.append(user_waiting)
        fired.set()

    monkeypatch.setattr(compaction, "compact_conversation", _rec, raising=True)
    _forget("c-bg")
    try:
        await compaction.schedule_compaction_if_due("c-bg", 150)
        await asyncio.wait_for(fired.wait(), 1)
        assert waiting == [False]
    finally:
        _forget("c-bg")


async def test_ensure_before_turn_bypasses_failure_cooldown(monkeypatch):
    import time

    monkeypatch.setattr(compaction.settings, "compaction_enabled", True, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_near_context_tokens", 50_000, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_near_max_passes", 1, raising=True)
    calls: list[str] = []

    async def _rec(conversation_id, *, trigger_input_tokens=None, user_waiting=False):
        calls.append(conversation_id)
        return True

    monkeypatch.setattr(compaction, "compact_conversation", _rec, raising=True)
    compaction._failure_cooldown_until["c-cd"] = time.monotonic() + 60
    try:
        wrote = await compaction.ensure_compaction_before_turn(
            "c-cd", input_tokens=60_000, context_length=None
        )
        assert wrote is True
        assert calls == ["c-cd"]
    finally:
        compaction._failure_cooldown_until.pop("c-cd", None)
        compaction._inflight_tasks.pop("c-cd", None)


async def test_maybe_compact_near_ceiling_uses_metrics_and_model(monkeypatch):
    monkeypatch.setattr(compaction.settings, "compaction_enabled", True, raising=True)
    seen: list[tuple[int, int | None]] = []

    async def _ensure(cid, *, input_tokens, context_length=None):
        seen.append((input_tokens, context_length))
        return True

    class _CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    class _Metrics:
        def __init__(self, _s):
            pass

        async def latest_prompt_tokens(self, _cid):
            return 900_000

    monkeypatch.setattr(compaction, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(compaction, "TurnMetricsRepository", _Metrics)
    monkeypatch.setattr(compaction, "ensure_compaction_before_turn", _ensure, raising=True)
    # gpt-4.1 curated meta is 1_000_000 → 80% = 800_000; 900k is near.
    ok = await compaction.maybe_compact_near_ceiling("c1", model_id="gpt-4.1")
    assert ok is True
    assert seen == [(900_000, 1_000_000)]


async def test_finalize_cloud_and_local_call_if_due(monkeypatch):
    """Cloud + local finalize both await schedule_compaction_if_due (not bare schedule)."""
    from agentcore.conversation.store import cloud as cloud_mod
    from agentcore.conversation.store.cloud import CloudStore
    from agentcore.core.error_codes import ErrorCode
    from agentcore.runtime.events import FinishReason

    calls: list[tuple[str, int]] = []

    async def _if_due(conversation_id, input_tokens):
        calls.append((conversation_id, input_tokens))

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
    monkeypatch.setattr(cloud_mod, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(cloud_mod, "TurnMetricsRepository", MetricsRepo)
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", AsyncMock())
    monkeypatch.setattr(cloud_mod, "schedule_consolidation", lambda _c: None)
    monkeypatch.setattr(cloud_mod, "schedule_compaction_if_due", _if_due)
    monkeypatch.setattr(CloudStore, "clear_stream_segments", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.drain_cost_ledger_before_reconcile",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.reconcile_turn_cost_ledger",
        AsyncMock(return_value=[]),
    )

    sink = SimpleNamespace(emit=lambda *_a, **_k: None)
    # ERROR path skips derived mint but still schedules compaction.
    await CloudStore().finalize(
        mode="cloud",
        result={
            "message_id": "m-cloud",
            "content": "",
            "error": "超时",
            "error_code": ErrorCode.LLM_TIMEOUT,
            "finish_reason": FinishReason.ERROR,
            "rounds": 0,
            "input_tokens": 42,
            "journal_entries": [],
        },
        conversation_id="c-cloud",
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
    assert ("c-cloud", 42) in calls

    calls.clear()
    monkeypatch.setattr(
        cloud_mod, "build_provider", lambda *_a, **_k: SimpleNamespace(close=AsyncMock())
    )
    monkeypatch.setattr(cloud_mod, "resolve_user_model", lambda *_a, **_k: "m")
    await CloudStore().finalize(
        mode="local",
        conversation_id="c-local",
        user_id="u1",
        user_message="hi",
        assistant_content="",
        runs={
            "events": [],
            "finish_reason": "error",
            "error": {"code": ErrorCode.LLM_TIMEOUT, "message": "超时"},
        },
        user_message_id="u1m",
        message_id="m-local",
        input_tokens=7,
        trace_id="b" * 32,
        finish_reason=FinishReason.ERROR.value,
    )
    assert ("c-local", 7) in calls


# --- compact_conversation (DB-bound runner; session/repos/provider all faked) ---


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _CloseProvider(_FakeProvider):
    """_FakeProvider + the ``close()`` the runner awaits in its finally block."""

    def __init__(self, content: str) -> None:
        super().__init__(content)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _conv(*, summary: str | None, watermark: datetime | None) -> SimpleNamespace:
    return SimpleNamespace(user_id="u1", compaction_summary=summary, compacted_through=watermark)


def _msgs(n: int) -> list[SimpleNamespace]:
    """``n`` alternating user/assistant messages with increasing datetime created_at."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        _msg("user" if i % 2 == 0 else "assistant", f"m{i}", base + timedelta(minutes=i))
        for i in range(n)
    ]


def _wire_runner(monkeypatch, *, conv, messages, provider, credentials=...) -> dict:
    """Point compact_conversation's deps at in-memory fakes; return a recorder dict.

    ``credentials`` defaults to a non-None stub so the runner proceeds to the LLM.
    Pass ``credentials=None`` to exercise the gate-skip path.
    """
    rec: dict = {"set": None, "built": False}
    if credentials is ...:
        credentials = SimpleNamespace(default_model="flash", source="platform")

    monkeypatch.setattr(compaction, "async_session_factory", lambda: _FakeSession())

    class _FakeConvRepo:
        def __init__(self, session):
            pass

        async def get_by_id_unscoped(self, conversation_id):
            return conv

        async def set_compaction(
            self, conversation_id, *, summary, compacted_through, input_tokens
        ):
            rec["set"] = {
                "conversation_id": conversation_id,
                "summary": summary,
                "compacted_through": compacted_through,
                "input_tokens": input_tokens,
            }

    class _FakeMsgRepo:
        def __init__(self, session):
            pass

        async def list_by_conversation(self, conversation_id, *, limit):
            return (messages, len(messages))

        async def list_after(self, conversation_id, *, after, limit):
            return ([m for m in messages if m.created_at > after], False)

    async def _run_bg(user_id, conversation_id, *, runner):
        from agentcore.billing.gate import (
            BackgroundLlmResult,
            BackgroundLlmSkip,
            BackgroundSkipReason,
        )

        if credentials is None:
            return BackgroundLlmSkip(reason=BackgroundSkipReason.NO_CREDENTIALS)
        value = await runner(credentials)
        return BackgroundLlmResult(value=value, credentials=credentials)

    def _build(creds, purpose="platform_internal"):
        rec["built"] = True
        return provider

    monkeypatch.setattr(compaction, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(compaction, "MessageRepository", _FakeMsgRepo)
    monkeypatch.setattr(compaction, "run_compaction_llm", _run_bg)
    monkeypatch.setattr(compaction, "build_provider", _build)
    monkeypatch.setattr(compaction.settings, "compaction_enabled", True, raising=True)
    monkeypatch.setattr(compaction.settings, "billing_mode", "platform", raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_recency_messages", 20, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_min_fold_messages", 4, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_max_fold_messages", 200, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_summary_char_budget", 4000, raising=True)
    return rec


async def test_compact_conversation_first_fold_persists_summary_and_watermark(
    monkeypatch,
):
    import time

    messages = _msgs(30)  # 30 − 20 recency = 10 oldest fold
    conv = _conv(summary=None, watermark=None)
    provider = _CloseProvider("## 已确立的事实\n- X")
    rec = _wire_runner(monkeypatch, conv=conv, messages=messages, provider=provider)
    compaction._failure_cooldown_until["c1"] = time.monotonic() + 60

    ok = await compaction.compact_conversation("c1", trigger_input_tokens=12345)

    assert ok is True
    assert provider.closed is True
    assert rec["set"] is not None
    assert rec["set"]["summary"] == "## 已确立的事实\n- X"
    # Watermark = created_at of the LAST folded (10th-oldest, index 9) message.
    assert rec["set"]["compacted_through"] == messages[9].created_at
    assert rec["set"]["input_tokens"] == 12345
    # Successful write clears any prior failure cooldown.
    assert "c1" not in compaction._failure_cooldown_until


async def test_compact_conversation_noop_when_nothing_to_fold(monkeypatch):
    # 12 messages, all within the 20 recency window → nothing old enough to fold.
    messages = _msgs(12)
    conv = _conv(summary=None, watermark=None)
    provider = _CloseProvider("unused")
    rec = _wire_runner(monkeypatch, conv=conv, messages=messages, provider=provider)

    ok = await compaction.compact_conversation("c1", trigger_input_tokens=999)

    assert ok is False
    assert rec["built"] is False  # gated BEFORE any LLM spend
    assert rec["set"] is None
    # No-op (nothing to fold) is not a failure — must not arm cooldown.
    assert "c1" not in compaction._failure_cooldown_until


async def test_compact_conversation_skips_empty_summary(monkeypatch):
    # Enough to fold, but the model yields nothing (timeout/refusal) → never persist
    # a blank summary; leave state untouched and arm failure cooldown.
    messages = _msgs(30)
    conv = _conv(summary="旧摘要", watermark=None)
    provider = _CloseProvider("   ")
    rec = _wire_runner(monkeypatch, conv=conv, messages=messages, provider=provider)
    monkeypatch.setattr(
        compaction.settings, "compaction_failure_cooldown_seconds", 90, raising=True
    )
    compaction._failure_cooldown_until.pop("c1", None)

    ok = await compaction.compact_conversation("c1", trigger_input_tokens=777)

    assert ok is False
    assert provider.closed is True  # built + closed, but no write
    assert rec["set"] is None
    assert "c1" in compaction._failure_cooldown_until
    compaction._failure_cooldown_until.pop("c1", None)


async def test_compact_conversation_byok_without_key_skips_without_watermark(
    monkeypatch,
):
    # Gate returns None (no platform/BYOK) → skip WITHOUT folding; arm cooldown.
    messages = _msgs(30)
    conv = _conv(summary=None, watermark=None)
    provider = _CloseProvider("unused")
    rec = _wire_runner(
        monkeypatch, conv=conv, messages=messages, provider=provider, credentials=None
    )
    monkeypatch.setattr(
        compaction.settings, "compaction_failure_cooldown_seconds", 90, raising=True
    )
    compaction._failure_cooldown_until.pop("c1", None)

    ok = await compaction.compact_conversation("c1", trigger_input_tokens=500)

    assert ok is False
    assert rec["built"] is False  # no provider, no LLM call
    assert rec["set"] is None
    assert "c1" in compaction._failure_cooldown_until
    compaction._failure_cooldown_until.pop("c1", None)


# --- Upstream-dated cooldown (the one thing near-ceiling yields to) ---


class _RaisingProvider(_CloseProvider):
    """Fails the call the way the upstream under test fails it."""

    def __init__(self, exc: BaseException) -> None:
        super().__init__("")
        self._exc = exc

    async def complete(self, request: LLMRequest) -> LLMResponse:
        raise self._exc


def _forget(*conversation_ids: str) -> None:
    for cid in conversation_ids:
        compaction._failure_cooldown_until.pop(cid, None)
        compaction._declared_ready_at.pop(cid, None)
        compaction._declared_recovery_at.pop(cid, None)
        compaction._cooldown_allowance.pop(cid, None)
        compaction._inflight_tasks.pop(cid, None)


def _declared_429(retry_after: float | None, **kwargs):
    """A 429 whose cooldown upstream actually stated in a ``Retry-After`` header.

    The only kind that may date a wall: a header-less 429 carries our own backoff
    instead, and is pinned separately in
    :func:`test_our_own_backoff_never_dates_a_wall`.
    """
    from agentcore.core.errors import RETRY_AFTER_FROM_HEADER, upstream_rate_limit_error

    return upstream_rate_limit_error(
        retry_after, retry_after_source=RETRY_AFTER_FROM_HEADER, **kwargs
    )


def test_declared_recovery_only_from_dated_hopeless_failures():
    """A cooldown counts as declared only when upstream both refused AND dated it."""
    from agentcore.core.errors import LLMError

    declared = compaction._declared_recovery_seconds
    # The day-reset 429 this whole path exists for: past the call's budget → hopeless.
    assert declared(_declared_429(46_440, retry_ceiling=40)) == 46_440
    # Same 429 on a platform-funded call: the quota face keeps its date in
    # ``details`` only, and that face is the one compaction meets most often.
    assert declared(_declared_429(46_440, credential_source="platform", retry_ceiling=40)) == 46_440
    # Fits the budget → the provider already sat it out; still worth another turn.
    assert declared(_declared_429(20.0, retry_ceiling=40)) is None
    assert declared(_declared_429(None)) is None
    # Non-retryable but undated, and the plain unknown-duration failures.
    assert declared(LLMError("上游炸了")) is None
    assert declared(TimeoutError()) is None
    assert declared(RuntimeError("boom")) is None


def test_our_own_backoff_never_dates_a_wall():
    """无头 429 带的是我们自己退避链的末项，不是上游的表态——它不许摆出一个日期。

    138 次生产放弃打的都是 ``retry_after_sec=32.0``：2→4→8→16→32 的最后一跳超出了
    后台调用的预算，于是这个异常「不可重试 + 带 retry_after」两条全中，正好长成一次
    上游声明的样子。照单收下的代价是这段对话整整一趟折叠被跳过，用户还会被告知一个
    上游从没说过的恢复时刻。
    """
    from agentcore.core.errors import (
        RETRY_AFTER_FROM_BACKOFF,
        LLMRateLimitError,
        upstream_rate_limit_error,
    )

    declared = compaction._declared_recovery_seconds
    ours = upstream_rate_limit_error(
        32.0,
        credential_source="platform",
        retry_ceiling=30,
        retry_after_source=RETRY_AFTER_FROM_BACKOFF,
    )
    # 出处不够，就不许换上那张「额度已耗尽」的脸，更不许把 32 秒当日期。
    assert isinstance(ours, LLMRateLimitError)
    assert ours.retryable is False and ours.retry_after == 32.0
    assert declared(ours) is None

    # 跨 /inference/ hop 转述来的裸数字同样无从作证。
    assert declared(upstream_rate_limit_error(32.0, retry_ceiling=30)) is None


def test_declared_recovery_rejects_unusable_durations():
    """Only a real positive span is a date; a garbage header must not wall forever."""
    from agentcore.core.errors import RETRY_AFTER_FROM_HEADER

    class _Dated(Exception):
        retryable = False
        retry_after_source = RETRY_AFTER_FROM_HEADER

        def __init__(self, retry_after) -> None:
            super().__init__("dated")
            self.retry_after = retry_after

    declared = compaction._declared_recovery_seconds
    assert declared(_Dated(3600)) == 3600.0
    assert declared(_Dated(0)) is None
    assert declared(_Dated(-5)) is None
    assert declared(_Dated(float("inf"))) is None
    assert declared(_Dated(float("nan"))) is None
    assert declared(_Dated("3600")) is None  # un-parsed header text is not a duration


async def test_failed_pass_takes_its_cooldown_from_the_upstream_retry_after(monkeypatch):
    """The 429's own ``Retry-After`` outranks the 90s guess and is remembered as dated."""
    import time

    messages = _msgs(30)
    conv = _conv(summary="旧摘要", watermark=None)
    provider = _RaisingProvider(_declared_429(1_200, retry_ceiling=40))
    rec = _wire_runner(monkeypatch, conv=conv, messages=messages, provider=provider)
    monkeypatch.setattr(
        compaction.settings, "compaction_failure_cooldown_seconds", 90, raising=True
    )
    _forget("c1")
    try:
        ok = await compaction.compact_conversation("c1", trigger_input_tokens=777)

        assert ok is False
        assert rec["set"] is None  # failure never advances the watermark
        assert provider.closed is True
        remaining = compaction._in_declared_cooldown("c1")
        assert remaining is not None and 1_190 < remaining <= 1_200
        # The schedule-side cooldown stretches with it — 90s would re-arm 13 times.
        assert compaction._failure_cooldown_until["c1"] - time.monotonic() > 1_100
    finally:
        _forget("c1")


async def test_a_day_scale_wall_is_obeyed_for_an_hour_not_for_a_day(monkeypatch):
    """上游说 12.9 小时后再来：照单全收就是这段对话半天不整理，越攒越装不下。

    申报冷却值得听，不值得逐字听。日级重置是它最常见的形态，而这半天里对话还在长；
    每小时白花一次调用，对比「上下文撑爆」的代价可以忽略。
    """
    import time

    messages = _msgs(30)
    conv = _conv(summary="旧摘要", watermark=None)
    provider = _RaisingProvider(_declared_429(46_440, retry_ceiling=40))
    _wire_runner(monkeypatch, conv=conv, messages=messages, provider=provider)
    monkeypatch.setattr(
        compaction.settings, "compaction_failure_cooldown_seconds", 90, raising=True
    )
    _forget("c1")
    try:
        assert await compaction.compact_conversation("c1", trigger_input_tokens=777) is False

        cap = compaction.DECLARED_COOLDOWN_CAP_SECONDS
        assert cap == 3600.0
        remaining = compaction._in_declared_cooldown("c1")
        assert remaining is not None and cap - 10 < remaining <= cap
        assert compaction._failure_cooldown_until["c1"] - time.monotonic() <= cap
    finally:
        _forget("c1")


async def test_a_new_key_retires_the_wall_that_belonged_to_the_old_one(monkeypatch):
    """额度是账号级的，冷却却记在每个对话上——换 key 之后它必须当场作废。

    错误文案给的出口就是「接入自己的 API Key 立即继续」；用户照做了，整理却还卡在
    上一把钥匙的墙后面，聊天记录一路堆到装不下。
    """
    from agentcore.billing.allowance import invalidate_allowance, reset_allowance_epochs

    messages = _msgs(30)
    conv = _conv(summary="旧摘要", watermark=None)
    provider = _RaisingProvider(_declared_429(46_440, retry_ceiling=40))
    _wire_runner(monkeypatch, conv=conv, messages=messages, provider=provider)
    reset_allowance_epochs()
    _forget("c1")
    try:
        assert await compaction.compact_conversation("c1", trigger_input_tokens=777) is False
        assert compaction._in_declared_cooldown("c1") is not None
        assert compaction._in_failure_cooldown("c1") is True

        # ``_wire_runner`` 的会话属于 u1；换的是别人的 key，这堵墙照旧。
        invalidate_allowance("someone-else", reason="byok_provider_changed")
        assert compaction._in_declared_cooldown("c1") is not None

        invalidate_allowance("u1", reason="byok_provider_changed")
        assert compaction._in_declared_cooldown("c1") is None
        # 猜出来的那条也一起退休：它挡的是同一件事——「上游现在不接这个账号」。
        assert compaction._in_failure_cooldown("c1") is False
        assert "c1" not in compaction._cooldown_allowance
    finally:
        reset_allowance_epochs()
        _forget("c1")


async def test_quota_change_and_key_change_are_the_two_things_that_retire_it():
    """两个入口都要真的 bump——否则「立即失效」只是文档里的一句话。"""
    from unittest.mock import AsyncMock, MagicMock

    from agentcore.admin.service import AdminService
    from agentcore.billing.allowance import allowance_epoch, reset_allowance_epochs
    from agentcore.llm.provider_service import LlmProviderService

    reset_allowance_epochs()
    try:
        users = MagicMock()
        actor = MagicMock(user_id="admin-1")
        target = MagicMock(user_id="u1")
        users.get_by_id = AsyncMock(return_value=target)
        users.set_quota = AsyncMock()
        await AdminService(users).update_user(
            actor=actor, user_id="u1", quota={"daily_tokens": 10_000}
        )
        assert allowance_epoch("u1") == 1

        svc = LlmProviderService(MagicMock())
        svc._repo = MagicMock()
        svc._repo.update = AsyncMock(return_value=MagicMock(status="active"))
        svc._repo.get = AsyncMock(return_value=MagicMock())
        # 改标签不是换钥匙：上游会怎么答没变，不该退休一堵仍然成立的墙。
        await svc.update_provider("u1", "p1", label="新名字", fields_set={"label"})
        assert allowance_epoch("u1") == 1

        await svc.update_provider(
            "u1", "p1", base_url="https://new.example/v1", fields_set={"base_url"}
        )
        assert allowance_epoch("u1") == 2
    finally:
        reset_allowance_epochs()


async def test_failed_pass_without_a_date_keeps_the_guessed_cooldown(monkeypatch):
    """An undated failure arms the 90s guess only — nothing for near-ceiling to obey."""
    import time

    from agentcore.core.errors import LLMError

    messages = _msgs(30)
    conv = _conv(summary="旧摘要", watermark=None)
    provider = _RaisingProvider(LLMError("上游炸了"))
    rec = _wire_runner(monkeypatch, conv=conv, messages=messages, provider=provider)
    monkeypatch.setattr(
        compaction.settings, "compaction_failure_cooldown_seconds", 90, raising=True
    )
    _forget("c1")
    try:
        ok = await compaction.compact_conversation("c1", trigger_input_tokens=777)

        assert ok is False
        assert rec["set"] is None
        assert compaction._in_declared_cooldown("c1") is None
        assert compaction._failure_cooldown_until["c1"] - time.monotonic() <= 90
    finally:
        _forget("c1")


async def test_a_header_less_429_never_costs_the_next_fold(monkeypatch):
    """无头 429 撞预算 → 只武装那条猜出来的冷却，近顶折叠照跑。

    这是那个根因的最后一个消费方。上游一个字没说，我们自己退避到 32 秒、超出后台
    调用的预算而放弃——异常于是「不可重试 + 带 retry_after」两条全中，长得和一次真
    声明一模一样。当成声明收下的后果不是多等 32 秒：近顶预压是唯一有人在等的路径，
    它对申报墙让路，于是这一整趟折叠被跳过，而界面还会挂出一个上游从没许诺的恢复
    时刻。
    """
    from agentcore.core.errors import RETRY_AFTER_FROM_BACKOFF, upstream_rate_limit_error

    messages = _msgs(30)
    conv = _conv(summary="旧摘要", watermark=None)
    ours = upstream_rate_limit_error(
        32.0,
        credential_source="platform",
        retry_ceiling=30,
        retry_after_source=RETRY_AFTER_FROM_BACKOFF,
    )
    rec = _wire_runner(monkeypatch, conv=conv, messages=messages, provider=_RaisingProvider(ours))
    monkeypatch.setattr(
        compaction.settings, "compaction_failure_cooldown_seconds", 90, raising=True
    )
    monkeypatch.setattr(compaction.settings, "compaction_near_context_tokens", 50_000, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_near_max_passes", 1, raising=True)
    _forget("c1")
    try:
        assert await compaction.compact_conversation("c1", trigger_input_tokens=777) is False

        # 猜出来的 90 秒照旧武装——放弃过一次，下一趟确实该缓一缓。
        assert compaction._in_failure_cooldown("c1") is True
        # 申报侧一个字都不许留下：既没有墙，也没有能说给用户听的恢复时刻。
        assert compaction._in_declared_cooldown("c1") is None
        assert compaction.declared_recovery_at("c1") is None

        # 近顶是紧急的：它让路的只有上游真说过的墙，这次必须再调一次模型。
        rec["built"] = False
        assert (
            await compaction.ensure_compaction_before_turn(
                "c1", input_tokens=60_000, context_length=None
            )
            is False
        )
        assert rec["built"] is True
    finally:
        _forget("c1")


async def test_near_ceiling_stops_calling_once_upstream_dates_its_recovery(monkeypatch):
    """验收①: after an hour-scale ``Retry-After``, later turns spend no LLM call."""
    monkeypatch.setattr(compaction.settings, "compaction_enabled", True, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_near_context_tokens", 50_000, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_near_max_passes", 1, raising=True)
    monkeypatch.setattr(
        compaction.settings, "compaction_failure_cooldown_seconds", 90, raising=True
    )
    passes: list[str] = []

    async def _rec(conversation_id, *, trigger_input_tokens=None, user_waiting=False):
        passes.append(conversation_id)
        compaction._mark_failure_cooldown(conversation_id, declared_recovery_in=12.9 * 3600)
        return False

    monkeypatch.setattr(compaction, "compact_conversation", _rec, raising=True)
    _forget("c-wall")
    try:
        for _ in range(4):
            wrote = await compaction.ensure_compaction_before_turn(
                "c-wall", input_tokens=60_000, context_length=None
            )
            assert wrote is False
        assert passes == ["c-wall"]  # one attempt, then the wall is taken at its word
    finally:
        _forget("c-wall")


async def test_near_ceiling_still_retries_every_turn_on_undated_failure(monkeypatch):
    """验收②: unknown-duration failures keep the urgent bypass exactly as before."""
    monkeypatch.setattr(compaction.settings, "compaction_enabled", True, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_near_context_tokens", 50_000, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_near_max_passes", 1, raising=True)
    monkeypatch.setattr(
        compaction.settings, "compaction_failure_cooldown_seconds", 90, raising=True
    )
    passes: list[str] = []

    async def _rec(conversation_id, *, trigger_input_tokens=None, user_waiting=False):
        passes.append(conversation_id)
        compaction._mark_failure_cooldown(conversation_id)
        return False

    monkeypatch.setattr(compaction, "compact_conversation", _rec, raising=True)
    _forget("c-guess")
    try:
        for _ in range(4):
            await compaction.ensure_compaction_before_turn(
                "c-guess", input_tokens=60_000, context_length=None
            )
        assert passes == ["c-guess"] * 4
    finally:
        _forget("c-guess")


async def test_near_ceiling_resumes_once_the_declared_moment_passes(monkeypatch):
    """The wall is a moment, not a latch: past it the urgent path runs again."""
    import time

    monkeypatch.setattr(compaction.settings, "compaction_enabled", True, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_near_context_tokens", 50_000, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_near_max_passes", 1, raising=True)
    passes: list[str] = []

    async def _rec(conversation_id, *, trigger_input_tokens=None, user_waiting=False):
        passes.append(conversation_id)
        return True

    monkeypatch.setattr(compaction, "compact_conversation", _rec, raising=True)
    _forget("c-past")
    # Upstream's moment has come and gone; the stale guessed cooldown still stands.
    compaction._declared_ready_at["c-past"] = time.monotonic() - 1
    compaction._failure_cooldown_until["c-past"] = time.monotonic() + 60
    try:
        wrote = await compaction.ensure_compaction_before_turn(
            "c-past", input_tokens=60_000, context_length=None
        )
        assert wrote is True
        assert passes == ["c-past"]
        assert "c-past" not in compaction._declared_ready_at  # expired lazily
    finally:
        _forget("c-past")


async def test_successful_pass_clears_both_cooldowns(monkeypatch):
    """A written summary retires the dated wall too — nothing outlives its cause."""
    import time

    messages = _msgs(30)
    conv = _conv(summary=None, watermark=None)
    provider = _CloseProvider("## 已确立的事实\n- X")
    _wire_runner(monkeypatch, conv=conv, messages=messages, provider=provider)
    compaction._declared_ready_at["c1"] = time.monotonic() + 46_440
    compaction._failure_cooldown_until["c1"] = time.monotonic() + 46_440
    try:
        assert await compaction.compact_conversation("c1") is True
        assert "c1" not in compaction._declared_ready_at
        assert "c1" not in compaction._failure_cooldown_until
    finally:
        _forget("c1")


async def test_platform_day_reset_dates_the_wall_through_the_real_gate(monkeypatch):
    """端到端：平台代付撞日级 429 → 闸交出申报恢复时刻 → 近顶预压在该时刻前不再调模型。

    The whole reason the gate answers with a skip instead of an empty value. On a
    platform-funded call an hour-scale ``Retry-After`` is re-faced as
    ``LLMQuotaExceededError`` (``upstream_rate_limit_error``), which the gate turns
    down silently — and used to turn down *anonymously*, leaving compaction to guess
    90 seconds at a wall upstream had dated in hours. This runs the real gate so the
    date survives the whole hop, not just the helper that reads it.
    """
    from agentcore.billing import gate as gate_mod
    from agentcore.billing.gate import BackgroundGateResolve
    from agentcore.core.errors import (
        RETRY_AFTER_FROM_HEADER,
        LLMQuotaExceededError,
        upstream_rate_limit_error,
    )
    from agentcore.llm.credentials import LLMCredentials

    day_reset = upstream_rate_limit_error(
        46_440,
        credential_source="platform",
        retry_ceiling=40,
        # Upstream dated this one itself — the only kind that may date a wall.
        retry_after_source=RETRY_AFTER_FROM_HEADER,
    )
    # Platform-funded 429s past the budget wear the quota face, not the 429 one.
    assert isinstance(day_reset, LLMQuotaExceededError)

    messages = _msgs(30)
    conv = _conv(summary="旧摘要", watermark=None)
    provider = _RaisingProvider(day_reset)
    rec = _wire_runner(monkeypatch, conv=conv, messages=messages, provider=provider)
    # Real gate: the resolve step admits the call, the upstream call then refuses it.
    monkeypatch.setattr(compaction, "run_compaction_llm", gate_mod.run_compaction_llm)
    monkeypatch.setattr(gate_mod, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(
        gate_mod,
        "resolve_and_gate_compaction",
        AsyncMock(
            return_value=BackgroundGateResolve(
                credentials=LLMCredentials(
                    api_key="sk-platform",
                    base_url="https://p.example/v1",
                    default_model="flash",
                    source="platform",
                )
            )
        ),
    )
    monkeypatch.setattr(
        compaction.settings, "compaction_failure_cooldown_seconds", 90, raising=True
    )
    monkeypatch.setattr(compaction.settings, "compaction_near_context_tokens", 50_000, raising=True)
    monkeypatch.setattr(compaction.settings, "compaction_near_max_passes", 1, raising=True)
    _forget("c1")
    try:
        ok = await compaction.compact_conversation("c1", trigger_input_tokens=777)

        assert ok is False
        assert rec["set"] is None  # the refused pass never advances the watermark
        remaining = compaction._in_declared_cooldown("c1")
        cap = compaction.DECLARED_COOLDOWN_CAP_SECONDS
        assert remaining is not None and cap - 10 < remaining <= cap

        # Near-ceiling is urgent enough to bypass a guess, but not a dated refusal:
        # no further turn spends a call before upstream says its allowance is back.
        rec["built"] = False
        for _ in range(3):
            wrote = await compaction.ensure_compaction_before_turn(
                "c1", input_tokens=60_000, context_length=None
            )
            assert wrote is False
        assert rec["built"] is False
    finally:
        _forget("c1")


# --- 丢了历史就说出来 (conversation/context_gap.py) ---
#
# 压缩失败本身不值得打扰用户——真正的伤害是「早期对话既不在摘要里、也不在原文窗口里」。
# 这一组盯的就是那条线：跨过去才出声，没跨过去必须闭嘴。


def _gap_conv(*, cid: str = "c1", summary: str | None, watermark: datetime | None):
    return SimpleNamespace(
        id=cid, user_id="u1", compaction_summary=summary, compacted_through=watermark
    )


def test_short_chat_that_failed_to_compact_says_nothing():
    """压缩失败但整段对话都还在窗口里 → 没有实际后果，不得打扰用户。"""
    from agentcore.conversation.context_gap import context_gap_for
    from agentcore.conversation.history import FALLBACK_CONTEXT_MAX_MESSAGES

    conv = _gap_conv(summary=None, watermark=None)
    assert context_gap_for(conv, unfolded_messages=FALLBACK_CONTEXT_MAX_MESSAGES) is None


def test_no_summary_past_the_fallback_window_is_history_actually_gone():
    """无摘要时兜底窗口就是模型的全部视野——超出的那截谁也不认得了。"""
    from agentcore.conversation.context_gap import context_gap_for
    from agentcore.conversation.history import FALLBACK_CONTEXT_MAX_MESSAGES

    conv = _gap_conv(summary=None, watermark=None)
    gap = context_gap_for(conv, unfolded_messages=FALLBACK_CONTEXT_MAX_MESSAGES + 32)

    assert gap is not None
    # 条数是窗口切掉的真实行数，不是估算。
    assert gap.dropped_messages == 32
    # 没人给过日期就不许编一个。
    assert gap.recovery_at is None


def test_a_healthy_summary_is_not_a_gap(monkeypatch):
    """有摘要 = watermark 之前的都以摘要形式活着，尾巴没溢出就什么都没丢。"""
    from agentcore.conversation.context_gap import context_gap_for

    monkeypatch.setattr(compaction.settings, "compaction_context_max_messages", 300, raising=True)
    conv = _gap_conv(summary="## 已确立的事实", watermark=datetime(2026, 1, 1, tzinfo=UTC))
    # 全对话 5000 条，但只有 300 条没折进摘要 → 模型看得见全部（摘要 + 原文尾）。
    assert context_gap_for(conv, unfolded_messages=300) is None
    overflow = context_gap_for(conv, unfolded_messages=307)
    assert overflow is not None and overflow.dropped_messages == 7


def test_compaction_switched_off_makes_no_promise_to_break(monkeypatch):
    """关掉压缩不是「压缩失败」——没许过的承诺不必道歉。"""
    from agentcore.conversation.context_gap import context_gap_for

    monkeypatch.setattr(compaction.settings, "compaction_enabled", False, raising=True)
    conv = _gap_conv(summary=None, watermark=None)
    assert context_gap_for(conv, unfolded_messages=5_000) is None


def test_gap_reports_the_moment_upstream_dated_not_the_hour_we_capped_it_to():
    """定案：调度按小时封顶重问，告诉用户的必须是上游自己说的那个时刻。

    两个数回答的是两个问题。把封顶后的「1 小时后」说给用户，他会在 1 小时后回来撞上
    同一堵墙——恰恰是这次改动要终结的那种沉默。
    """
    from datetime import UTC, datetime

    from agentcore.conversation.context_gap import context_gap_for

    day_reset = 12.9 * 3600
    _forget("c-wall")
    try:
        compaction._mark_failure_cooldown("c-wall", declared_recovery_in=day_reset)

        # 调度侧照旧封顶在一小时，省得一段还在长的对话半天不整理。
        remaining = compaction._in_declared_cooldown("c-wall")
        cap = compaction.DECLARED_COOLDOWN_CAP_SECONDS
        assert remaining is not None and remaining <= cap

        gap = context_gap_for(
            _gap_conv(cid="c-wall", summary=None, watermark=None), unfolded_messages=100
        )
        assert gap is not None
        # 下发的是绝对瞬间（ISO8601 UTC），不是措辞好的钟点——时区由客户端决定。
        dated = datetime.strptime(gap.recovery_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        ahead = (dated - datetime.now(UTC)).total_seconds()
        assert abs(ahead - day_reset) <= 2  # 上游申报值
        assert ahead - cap > 3600  # 而不是调度封顶值
    finally:
        _forget("c-wall")


def test_recovery_at_goes_quiet_rather_than_guess():
    """没申报日期 / 已经过点 / 换了 key —— 三种都只能答「不知道」。"""
    import time

    from agentcore.billing.allowance import invalidate_allowance, reset_allowance_epochs

    _forget("c-guess", "c-past", "c-key")
    try:
        # 猜出来的冷却不是日期。
        compaction._mark_failure_cooldown("c-guess")
        assert compaction.declared_recovery_at("c-guess") is None

        # 时刻已过 → 就地退休，不留个陈旧承诺挂在界面上。
        compaction._declared_recovery_at["c-past"] = time.monotonic() - 1
        assert compaction.declared_recovery_at("c-past") is None
        assert "c-past" not in compaction._declared_recovery_at

        # 额度是账号的：换 key 之后那堵墙跟这段对话再无关系。
        compaction._mark_failure_cooldown("c-key", declared_recovery_in=3_000, user_id="u-key")
        assert compaction.declared_recovery_at("c-key") is not None
        invalidate_allowance("u-key", reason="byok_provider_changed")
        assert compaction.declared_recovery_at("c-key") is None
    finally:
        reset_allowance_epochs()
        _forget("c-guess", "c-past", "c-key")


async def test_successful_fold_retires_the_recovery_at_with_the_cooldowns(monkeypatch):
    """摘要写成了，界面上就不该再挂着「上游几点恢复」。"""
    import time

    messages = _msgs(30)
    conv = _conv(summary=None, watermark=None)
    _wire_runner(monkeypatch, conv=conv, messages=messages, provider=_CloseProvider("## 事实\n- X"))
    compaction._declared_recovery_at["c1"] = time.monotonic() + 46_440
    try:
        assert await compaction.compact_conversation("c1") is True
        assert "c1" not in compaction._declared_recovery_at
    finally:
        _forget("c1")


def test_rest_summary_stays_quiet_unless_the_count_was_actually_taken():
    """没算过 un-folded 就不发 context_gap——「未计算」不能冒充「完好」。"""
    from agentcore.api.schemas.conversations import conversation_summary_from_orm
    from agentcore.conversation.history import FALLBACK_CONTEXT_MAX_MESSAGES

    conv = SimpleNamespace(
        id="c1",
        title="t",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 2, tzinfo=UTC),
        compaction_summary=None,
        compacted_through=None,
        folder_id=None,
        local_container_root_id=None,
        pinned=False,
        archived=False,
        permission_axes={},
        deep_research_auto=False,
        model_profile_id=None,
    )

    quiet = conversation_summary_from_orm(conv, message_count=500)
    assert quiet.context_gap is None

    told = conversation_summary_from_orm(
        conv, message_count=500, unfolded_messages=FALLBACK_CONTEXT_MAX_MESSAGES + 9
    )
    assert told.context_gap is not None
    assert told.context_gap.dropped_messages == 9
    # 旗标语义不变：从没压缩成功过 → 仍是 False，降级提示走 context_gap 这条腿。
    assert told.context_compacted is False
    assert "compaction_summary" not in told.model_dump()


async def test_only_long_chats_pay_for_the_backlog_query():
    """侧边栏一屏短会话不该为这条提示多跑一次统计。"""
    from agentcore.api.routes.conversations.crud import _unfolded_counts
    from agentcore.conversation.history import FALLBACK_CONTEXT_MAX_MESSAGES

    asked: list[list[str]] = []

    class _Repo:
        async def unfolded_counts_for_conversations(self, ids):
            asked.append(list(ids))
            return {"long": 120}

    short_only = {"a": 3, "b": FALLBACK_CONTEXT_MAX_MESSAGES}
    assert await _unfolded_counts(_Repo(), short_only) == {}
    assert asked == []  # 没有候选就一次都不问

    mixed = {"a": 3, "long": 900, "folded": FALLBACK_CONTEXT_MAX_MESSAGES + 1}
    out = await _unfolded_counts(_Repo(), mixed)
    assert asked == [["long", "folded"]]
    # 查过但没有 un-folded 行 → 0（已全部折进摘要），而不是「没算过」。
    assert out == {"long": 120, "folded": 0}


async def test_shutdown_compaction_abandons_after_timeout():
    """In-flight folds are best-effort; shutdown must not wait a wedged gather."""

    async def _hang() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(_hang())
    compaction._tasks.add(task)
    try:
        started = time.monotonic()
        await compaction.shutdown_compaction(timeout=0.05)
        assert time.monotonic() - started < 0.5
        await asyncio.sleep(0)
        assert task.done()
    finally:
        task.cancel()
        compaction._tasks.discard(task)
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_shutdown_compaction_noop_without_tasks():
    assert not compaction._tasks
    await compaction.shutdown_compaction(timeout=0.01)


async def test_compact_before_turn_refuses_when_near_and_unfolded(monkeypatch):
    from agentcore.core.error_codes import ErrorCode
    from agentcore.core.errors import ContextOverflowError
    from agentcore.llm.errors import CONTEXT_OVERFLOW_PRODUCT

    monkeypatch.setattr(compaction.settings, "compaction_enabled", True, raising=True)

    async def _load(_cid, _model_id):
        return 90_000, 100_000

    async def _ensure(_cid, *, input_tokens, context_length=None):
        return False

    monkeypatch.setattr(compaction, "_load_fit_watermark", _load)
    monkeypatch.setattr(compaction, "ensure_compaction_before_turn", _ensure)
    with pytest.raises(ContextOverflowError) as ei:
        await compaction.compact_before_turn("c1", model_id="m")
    assert ei.value.message == CONTEXT_OVERFLOW_PRODUCT
    assert ei.value.status_code == 413
    assert ei.value.code == ErrorCode.CONTEXT_OVERFLOW
    assert "压缩" not in ei.value.message


async def test_compact_before_turn_proceeds_when_fold_wrote(monkeypatch):
    monkeypatch.setattr(compaction.settings, "compaction_enabled", True, raising=True)

    async def _load(_cid, _model_id):
        return 90_000, 100_000

    async def _ensure(_cid, *, input_tokens, context_length=None):
        return True

    monkeypatch.setattr(compaction, "_load_fit_watermark", _load)
    monkeypatch.setattr(compaction, "ensure_compaction_before_turn", _ensure)
    await compaction.compact_before_turn("c1", model_id="m")


async def test_compact_before_turn_skips_fold_when_not_near(monkeypatch):
    monkeypatch.setattr(compaction.settings, "compaction_enabled", True, raising=True)
    called: list[int] = []

    async def _load(_cid, _model_id):
        return 10_000, 100_000

    async def _ensure(_cid, *, input_tokens, context_length=None):
        called.append(1)
        return False

    monkeypatch.setattr(compaction, "_load_fit_watermark", _load)
    monkeypatch.setattr(compaction, "ensure_compaction_before_turn", _ensure)
    await compaction.compact_before_turn("c1", model_id="m")
    assert called == []


def test_max_prompt_tokens_from_journal_skips_empty_and_keeps_max():
    from agentcore.conversation.prompt_tokens import max_prompt_tokens_from_journal

    assert max_prompt_tokens_from_journal([]) == 0
    assert (
        max_prompt_tokens_from_journal(
            [
                {"kind": "llm_call", "payload": {"usage": {"input": 0}}},
                {"kind": "tool_call", "payload": {"usage": {"input": 99999}}},
                {"kind": "llm_call", "payload": {"usage": {"input": 1200}}},
                {"kind": "llm_call", "payload": {"usage": {"last_prompt": 800, "input": 800}}},
            ]
        )
        == 1200
    )


def test_token_usage_add_keeps_max_last_prompt():
    from agentcore.llm.provider.protocol import TokenUsage

    a = TokenUsage.from_openai_wire({"prompt_tokens": 100, "completion_tokens": 1})
    b = TokenUsage.from_openai_wire({"prompt_tokens": 40, "completion_tokens": 2})
    summed = a + b
    assert summed.input_tokens == 140
    assert summed.last_prompt_tokens == 100
    zero = TokenUsage() + a
    assert zero.last_prompt_tokens == 100
    assert TokenUsage.from_usage_dict(a.as_dict()).last_prompt_tokens == 100
