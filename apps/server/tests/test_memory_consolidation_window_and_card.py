"""Episodic consolidation: watermark-cut window, anchored card, no card without a summary.

Three production bugs pinned here, all on the same pass:

1. Every pass re-read a fixed 40-message tail, so adjacent episodes overlapped — the
   second card could restate the whole first one. The window is now cut at
   ``memory_synced_at``.
2. The card had no thread position. It is written on an idle debounce, minutes after the
   window it covers, so ``created_at`` cannot place it; ``anchor_at`` carries the last
   consolidated message's timestamp instead.
3. When the summarizer timed out, the fallback pasted the user's first turns back into
   the thread as a「已记下本场摘要」card — verbatim PII in the real incident. No summary
   now means no card, while the episode still lands for the semantic layer.

The runner is wired to in-memory fakes; the loader under test is the real
``load_recent_history`` so the watermark→``list_recent_after`` seam is covered too.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from agentcore.conversation import history as history_mod
from agentcore.memory import consolidation

_T0 = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeMessageStore:
    """Chronological message log backing the real ``load_recent_history``."""

    def __init__(self) -> None:
        self.rows: list[SimpleNamespace] = []

    def add_turn(self, user_text: str, assistant_text: str, *, at: datetime) -> None:
        self.rows.append(SimpleNamespace(role="user", content=user_text, created_at=at, usage=None))
        self.rows.append(
            SimpleNamespace(
                role="assistant",
                content=assistant_text,
                created_at=at + timedelta(seconds=1),
                usage={"status": "complete", "finish_reason": "end_turn"},
            )
        )

    @property
    def latest_created_at(self) -> datetime | None:
        return self.rows[-1].created_at if self.rows else None

    def repo_factory(self):
        store = self

        class _FakeRepo:
            def __init__(self, session):
                pass

            async def list_recent(self, conversation_id, *, limit):
                return store.rows[-limit:]

            async def list_recent_after(self, conversation_id, *, after, limit):
                newer = [r for r in store.rows if r.created_at > after]
                return newer[-limit:]

            async def latest_created_at(self, conversation_id):
                return store.latest_created_at

        return _FakeRepo


class _RecordingSummarizer:
    """Captures each window the episodic summarizer is handed; returns a canned reply."""

    windows: list[list[str]] = []
    reply: str = "本场摘要"

    def __init__(self, provider, *, model=None, role="memory"):
        pass

    async def summarize(self, messages, *, max_chars, actions=None):
        type(self).windows.append([str(m["content"]) for m in messages])
        return type(self).reply


class _FakeProvider:
    async def close(self) -> None:
        return None


def _wire(monkeypatch, store: _FakeMessageStore) -> dict:
    """Point ``consolidate_conversation`` at in-memory fakes; record published cards."""
    state: dict = {
        "synced_at": None,
        "conv_id": "c-window",
        "cards": [],
        "episodes": [],
    }

    @asynccontextmanager
    async def _lock(_conversation_id: str):
        yield "u-window"

    class _FakeConvRepo:
        def __init__(self, session):
            pass

        async def get_by_id_unscoped(self, conversation_id):
            return SimpleNamespace(
                id=conversation_id,
                folder_id=None,
                memory_synced_at=state["synced_at"],
            )

        async def set_memory_synced_at(self, conversation_id, synced_at):
            state["synced_at"] = synced_at

    async def _turn_open(_session, _cid):
        return False

    async def _assistant_row(_session, _cid):
        return ({"status": "complete", "finish_reason": "end_turn"}, "正文", True)

    async def _actions(_session, _cid, *, max_turns, after=None):
        state.setdefault("action_after", []).append(after)
        return None

    async def _run_bg(user_id, *, purpose="memory", runner):
        from agentcore.billing.gate import BackgroundLlmResult

        value = await runner(SimpleNamespace(source="platform", default_model="m"))
        return BackgroundLlmResult(
            value=value, credentials=SimpleNamespace(source="platform")
        )

    async def _append_episode(_store, **kwargs):
        state["episodes"].append(kwargs["summary"])
        return SimpleNamespace(id=f"ep-{len(state['episodes'])}", summary=kwargs["summary"])

    async def _record_and_publish(**kwargs):
        state["cards"].append(kwargs)

    async def _semantic(**kwargs):
        return False

    monkeypatch.setattr(consolidation, "user_memory_lock_for", _lock)
    monkeypatch.setattr(consolidation, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(consolidation, "MessageRepository", store.repo_factory())
    monkeypatch.setattr(history_mod, "MessageRepository", store.repo_factory())
    monkeypatch.setattr(consolidation, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(consolidation, "conversation_turn_open", _turn_open)
    monkeypatch.setattr(consolidation, "_latest_assistant_row", _assistant_row)
    monkeypatch.setattr(consolidation, "_load_conversation_action_inventory", _actions)
    monkeypatch.setattr(consolidation, "run_background_llm", _run_bg)
    monkeypatch.setattr(consolidation, "append_episode", _append_episode)
    monkeypatch.setattr(consolidation, "_record_and_publish", _record_and_publish)
    monkeypatch.setattr(consolidation, "run_semantic_for_scope", _semantic)
    monkeypatch.setattr(consolidation, "default_episode_store", lambda: object())
    monkeypatch.setattr(consolidation, "default_memory_store", lambda: object())
    monkeypatch.setattr(consolidation, "build_provider", lambda *a, **k: _FakeProvider())
    monkeypatch.setattr(consolidation, "resolve_user_model", lambda creds: "m")
    monkeypatch.setattr(consolidation, "LLMEpisodicSummarizer", _RecordingSummarizer)
    return state


@pytest.fixture(autouse=True)
def _reset():
    _RecordingSummarizer.windows = []
    _RecordingSummarizer.reply = "本场摘要"
    consolidation._reset_failure_cooldowns_for_tests()
    yield
    _RecordingSummarizer.windows = []
    _RecordingSummarizer.reply = "本场摘要"
    consolidation._reset_failure_cooldowns_for_tests()


@pytest.mark.asyncio
async def test_adjacent_windows_do_not_overlap(monkeypatch):
    """Second pass sees only the turns that arrived after the first watermark."""
    store = _FakeMessageStore()
    store.add_turn("第一轮问题", "第一轮回答", at=_T0)
    state = _wire(monkeypatch, store)

    assert await consolidation.consolidate_conversation("c-window") is True
    first_window = _RecordingSummarizer.windows[-1]
    assert first_window == ["第一轮问题", "第一轮回答"]
    assert state["synced_at"] == store.latest_created_at

    store.add_turn("第二轮问题", "第二轮回答", at=_T0 + timedelta(minutes=5))

    assert await consolidation.consolidate_conversation("c-window") is True
    second_window = _RecordingSummarizer.windows[-1]
    assert second_window == ["第二轮问题", "第二轮回答"]
    # The overlap bug: the second episode must not re-summarize the first one's turns.
    assert not set(first_window) & set(second_window)


@pytest.mark.asyncio
async def test_window_still_capped_when_many_messages_are_new(monkeypatch):
    """A long unconsolidated gap is bounded by the 40-message cap, newest-biased."""
    store = _FakeMessageStore()
    for i in range(30):
        store.add_turn(f"问题{i}", f"回答{i}", at=_T0 + timedelta(minutes=i))
    _wire(monkeypatch, store)
    monkeypatch.setattr(
        consolidation.settings, "memory_consolidation_window_messages", 40, raising=True
    )

    await consolidation.consolidate_conversation("c-window")

    window = _RecordingSummarizer.windows[-1]
    assert len(window) == 40
    assert window[-1] == "回答29"  # newest kept, oldest dropped


@pytest.mark.asyncio
async def test_action_inventory_uses_the_same_watermark(monkeypatch):
    store = _FakeMessageStore()
    store.add_turn("第一轮问题", "第一轮回答", at=_T0)
    state = _wire(monkeypatch, store)

    await consolidation.consolidate_conversation("c-window")
    first_watermark = state["synced_at"]
    store.add_turn("第二轮问题", "第二轮回答", at=_T0 + timedelta(minutes=5))
    await consolidation.consolidate_conversation("c-window")

    assert state["action_after"] == [None, first_watermark]


@pytest.mark.asyncio
async def test_card_carries_anchor_at_of_last_consolidated_message(monkeypatch):
    store = _FakeMessageStore()
    store.add_turn("问题", "回答", at=_T0)
    state = _wire(monkeypatch, store)

    await consolidation.consolidate_conversation("c-window")

    assert len(state["cards"]) == 1
    card = state["cards"][0]
    assert card["kind"] == "episodic"
    assert card["anchor_at"] == store.latest_created_at
    # Anchored to the window, not to the (debounced, later) moment the card was written.
    assert card["anchor_at"] == state["synced_at"]


@pytest.mark.asyncio
async def test_summarizer_timeout_writes_episode_but_no_card(monkeypatch):
    """超时不出卡: the raw-user-text fallback never reaches the conversation."""
    store = _FakeMessageStore()
    store.add_turn("我的身份证号是 110101199001011234", "好的", at=_T0)
    state = _wire(monkeypatch, store)
    _RecordingSummarizer.reply = ""  # LLMEpisodicSummarizer returns "" on TimeoutError

    changed = await consolidation.consolidate_conversation("c-window")

    assert changed is True  # the episode still feeds the semantic layer
    assert len(state["episodes"]) == 1
    assert state["cards"] == []  # …but nothing is posted back into the thread
    assert state["synced_at"] == store.latest_created_at  # watermark advances: no retry storm


@pytest.mark.asyncio
async def test_suppressed_card_does_not_leak_user_text_into_the_episode_card(monkeypatch):
    """The fallback text exists (episode material) but is never handed to a card."""
    store = _FakeMessageStore()
    secret = "我的手机号 13800138000，住在示例路 1 号"
    store.add_turn(secret, "收到", at=_T0)
    state = _wire(monkeypatch, store)
    _RecordingSummarizer.reply = "   "  # whitespace-only counts as no summary

    await consolidation.consolidate_conversation("c-window")

    assert secret in state["episodes"][0]
    assert state["cards"] == []


@pytest.mark.asyncio
async def test_real_summary_still_publishes_a_card(monkeypatch):
    store = _FakeMessageStore()
    store.add_turn("帮我查天气", "今天晴", at=_T0)
    state = _wire(monkeypatch, store)
    _RecordingSummarizer.reply = "用户询问天气。"

    await consolidation.consolidate_conversation("c-window")

    assert [c["summary"] for c in state["cards"]] == ["用户询问天气。"]


@pytest.mark.asyncio
async def test_record_and_publish_threads_anchor_at_to_row_and_firehose(monkeypatch):
    """anchor_at reaches both the persisted row and the live memory_updated payload."""
    anchor = _T0 + timedelta(minutes=3)
    recorded: dict = {}
    published: list[dict] = []

    class _FakeUpdateRepo:
        def __init__(self, session):
            pass

        async def record(self, **kwargs):
            recorded.update(kwargs)
            return SimpleNamespace(id="row-1", created_at=_T0 + timedelta(minutes=9))

    class _FakeHub:
        async def publish(self, user_ids, event):
            published.append(event)

    monkeypatch.setattr(consolidation, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(consolidation, "MemoryUpdateRepository", _FakeUpdateRepo)
    monkeypatch.setattr(consolidation, "default_chat_hub", lambda: _FakeHub())

    await consolidation._record_and_publish(
        conversation_id="c-window",
        user_id="u-window",
        kind="episodic",
        items=[],
        summary="本场摘要",
        anchor_at=anchor,
    )

    assert recorded["anchor_at"] == anchor
    assert published[0]["update"]["anchor_at"] == anchor.isoformat()
    # created_at stays the write time — the two answer different questions.
    assert published[0]["update"]["created_at"] != published[0]["update"]["anchor_at"]


@pytest.mark.asyncio
async def test_record_and_publish_defaults_anchor_at_to_null(monkeypatch):
    """Semantic sweeps have no message window; the field is present and null."""
    published: list[dict] = []

    class _FakeUpdateRepo:
        def __init__(self, session):
            pass

        async def record(self, **kwargs):
            assert kwargs["anchor_at"] is None
            return SimpleNamespace(id="row-2", created_at=_T0)

    class _FakeHub:
        async def publish(self, user_ids, event):
            published.append(event)

    monkeypatch.setattr(consolidation, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(consolidation, "MemoryUpdateRepository", _FakeUpdateRepo)
    monkeypatch.setattr(consolidation, "default_chat_hub", lambda: _FakeHub())

    await consolidation._record_and_publish(
        conversation_id="c-window",
        user_id="u-window",
        kind="semantic",
        items=[],
        summary="记忆已整理",
    )

    assert published[0]["update"]["anchor_at"] is None


async def test_load_recent_history_after_uses_the_watermark_query(monkeypatch):
    """The loader delegates to list_recent_after — not a client-side slice of the tail."""
    store = _FakeMessageStore()
    store.add_turn("旧问题", "旧回答", at=_T0)
    store.add_turn("新问题", "新回答", at=_T0 + timedelta(minutes=5))
    monkeypatch.setattr(history_mod, "MessageRepository", store.repo_factory())

    cut = store.rows[1].created_at
    window = await history_mod.load_recent_history(object(), "c-window", max_messages=40, after=cut)

    assert [m["content"] for m in window] == ["新问题", "新回答"]


def test_memory_update_view_exposes_anchor_at():
    from agentcore.api.schemas import MemoryUpdateView

    view = MemoryUpdateView.model_validate(
        SimpleNamespace(
            id="row-1",
            kind="episodic",
            summary="本场摘要",
            items=[],
            anchor_at=_T0,
            created_at=_T0 + timedelta(minutes=9),
        )
    )
    assert view.anchor_at == _T0
    assert MemoryUpdateView(id="row-2", created_at=_T0).anchor_at is None


def test_memory_update_view_closed_kind_and_action():
    """kind / items[].action are persisted JSONB closed sets — unknown values fail read."""
    from pydantic import ValidationError

    from agentcore.api.schemas import MemoryUpdateItemView, MemoryUpdateView

    MemoryUpdateView(id="row-q", created_at=_T0, kind="quota")
    MemoryUpdateItemView(action="quota_denied", file="画像")
    MemoryUpdateItemView(action="quota", file="")
    MemoryUpdateItemView(action="add", file="偏好")
    with pytest.raises(ValidationError):
        MemoryUpdateView(id="row-x", created_at=_T0, kind="future_kind")
    with pytest.raises(ValidationError):
        MemoryUpdateItemView(action="upsert", file="画像")


def test_episodic_timeout_ceiling_clears_observed_latency():
    """20s clipped every real memory pass (35–37s measured); keep real headroom."""
    from agentcore.memory.episodic import _EPISODIC_TIMEOUT_SECONDS

    assert _EPISODIC_TIMEOUT_SECONDS >= 60.0
