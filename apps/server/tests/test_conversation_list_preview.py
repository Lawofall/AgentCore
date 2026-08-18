"""列表「最近活动」= 回合；预览 = 最后可见助手句。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Update
from sqlalchemy.dialects import postgresql

from agentcore.api.routes.conversations.crud import (
    _first_user_contents_for_untitled,
    _summary_with_count,
)
from agentcore.api.schemas.conversations import conversation_summary_from_orm
from agentcore.conversation.list_preview import (
    PREVIEW_CHROME_ONLY,
    PREVIEW_MAX_CHARS,
    PREVIEW_SQL_LOOKBACK,
    assistant_preview_text,
    pick_last_visible_assistant_preview,
)
from agentcore.core.message_merge import (
    MESSAGE_STATUS_COMPLETE,
    MESSAGE_STATUS_INCOMPLETE,
    MESSAGE_STATUS_RUNNING,
)
from agentcore.db.models import Conversation
from agentcore.db.repositories.conversations import ConversationRepository
from agentcore.db.repositories.messages import MessageRepository
from agentcore.runtime.events.types import FinishReason
from agentcore.runtime.turn.interrupt import (
    INTERRUPTED_EMPTY_USER_VISIBLE,
    REDRIVE_FAILED_USER_VISIBLE,
)

CONV_ID = "11111111-2222-4333-8444-555555555555"


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class _Result:
    def __init__(self, *, rows: list[Any] | None = None, rowcount: int = 0) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)


class _RecordingSession:
    def __init__(self, results: list[_Result] | None = None) -> None:
        self.statements: list[Any] = []
        self.commits = 0
        self.flushes = 0
        self._results = list(results or [])

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _Result:
        self.statements.append(statement)
        return self._results.pop(0) if self._results else _Result()

    async def commit(self) -> None:
        self.commits += 1

    async def flush(self) -> None:
        self.flushes += 1


def _sql(statement: Any) -> str:
    compiled = statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"render_postcompile": True}
    )
    sql = str(compiled)
    for name, value in compiled.params.items():
        if isinstance(value, datetime):
            rendered = f"<ts:{value.isoformat()}>"
        elif value is None:
            rendered = "NULL"
        else:
            rendered = f"'{value}'"
        sql = sql.replace(f"%({name})s", rendered)
    return sql


def _conv(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": CONV_ID,
        "title": "定价讨论",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 2, tzinfo=UTC),
        "folder_id": None,
        "local_container_root_id": None,
        "pinned": False,
        "archived": False,
        "permission_axes": {},
        "deep_research_auto": False,
        "model_profile_id": None,
        "compaction_summary": None,
        "compacted_through": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# --- picker -------------------------------------------------------------------


def test_complete_assistant_is_the_preview():
    assert (
        assistant_preview_text("助手句", {"status": MESSAGE_STATUS_COMPLETE}) == "助手句"
    )


def test_running_placeholder_is_skipped_even_with_leaked_text():
    assert assistant_preview_text("半截", {"status": MESSAGE_STATUS_RUNNING}) is None


def test_empty_cancelled_and_incomplete_are_skipped():
    assert (
        assistant_preview_text(
            "",
            {"status": MESSAGE_STATUS_INCOMPLETE, "finish_reason": FinishReason.CANCELLED.value},
        )
        is None
    )
    assert assistant_preview_text("   ", {"status": "cancelled"}) is None


def test_interrupt_chrome_alone_is_skipped():
    assert assistant_preview_text(INTERRUPTED_EMPTY_USER_VISIBLE, {}) is None
    assert assistant_preview_text(REDRIVE_FAILED_USER_VISIBLE, {}) is None
    assert assistant_preview_text("已停止", {}) is None
    assert (
        assistant_preview_text(
            "（已停止，本回合未完成——以上为已生成部分；如需继续，可重新发送消息。）",
            {"status": MESSAGE_STATUS_INCOMPLETE},
        )
        is None
    )


def test_real_body_plus_chrome_keeps_the_body():
    body = f"已经写好的段落\n\n{REDRIVE_FAILED_USER_VISIBLE}"
    assert assistant_preview_text(body, {"status": MESSAGE_STATUS_INCOMPLETE}) == "已经写好的段落"


def test_incomplete_with_real_body_is_kept():
    assert (
        assistant_preview_text("半截可见回复", {"status": MESSAGE_STATUS_INCOMPLETE})
        == "半截可见回复"
    )


def test_preview_collapses_whitespace_and_clips():
    long = "可见正文" * 40
    assert assistant_preview_text("  你好\n世界  ", {}) == "你好 世界"
    assert assistant_preview_text(long, {}) == ("可见正文" * 40)[:PREVIEW_MAX_CHARS]


def test_walk_back_skips_stop_then_uses_prior_assistant():
    rows = [
        ("", {"status": MESSAGE_STATUS_INCOMPLETE, "finish_reason": "cancelled"}),
        (INTERRUPTED_EMPTY_USER_VISIBLE, {"status": MESSAGE_STATUS_INCOMPLETE}),
        ("上次成功回复", {"status": MESSAGE_STATUS_COMPLETE}),
    ]
    assert pick_last_visible_assistant_preview(rows) == "上次成功回复"


def test_walk_back_skips_running_then_uses_prior_assistant():
    rows = [
        ("", {"status": MESSAGE_STATUS_RUNNING}),
        ("上一轮助手", {"status": MESSAGE_STATUS_COMPLETE}),
    ]
    assert pick_last_visible_assistant_preview(rows) == "上一轮助手"


def test_no_visible_assistant_is_null_not_user_text():
    assert pick_last_visible_assistant_preview([]) is None
    assert pick_last_visible_assistant_preview([("", {"status": "cancelled"})]) is None
    # Picker never sees user rows; even if a caller stuffed user text in, chrome/empty
    # still cannot invent a fallback — a non-empty user string would look like assistant
    # prose, so the repo query (role=assistant) is the hard gate.
    assert pick_last_visible_assistant_preview([("", None)]) is None


def test_walk_back_past_many_chrome_still_finds_visible():
    """Empty/stop chrome must not hide the prior sentence under the SQL lookback cap."""
    newest_first = (
        [("", {"status": "cancelled"})] * PREVIEW_SQL_LOOKBACK
        + [("已停止", {})] * PREVIEW_SQL_LOOKBACK
        + [(INTERRUPTED_EMPTY_USER_VISIBLE, {})] * PREVIEW_SQL_LOOKBACK
        + [("可见助手句", {"status": MESSAGE_STATUS_COMPLETE})]
    )
    kept = [
        (content, usage)
        for content, usage in newest_first
        if (content or "").strip() not in {"", *PREVIEW_CHROME_ONLY}
    ][:PREVIEW_SQL_LOOKBACK]
    assert kept == [("可见助手句", {"status": MESSAGE_STATUS_COMPLETE})]
    assert pick_last_visible_assistant_preview(kept) == "可见助手句"
    assert pick_last_visible_assistant_preview(newest_first) == "可见助手句"


# --- schema / list overlay ----------------------------------------------------


def test_conversation_updated_at_has_no_onupdate():
    assert Conversation.__table__.c.updated_at.onupdate is None


def test_summary_overlay_fills_preview_and_count():
    summary = conversation_summary_from_orm(
        _conv(), message_count=4, last_message_preview="助手句"
    )
    assert summary.message_count == 4
    assert summary.last_message_preview == "助手句"
    dumped = summary.model_dump()
    assert dumped["last_message_preview"] == "助手句"


def test_summary_with_count_uses_preview_map():
    conv = _conv()
    summary = _summary_with_count(
        conv, {CONV_ID: 3}, previews={CONV_ID: "最后可见助手"}
    )
    assert summary.message_count == 3
    assert summary.last_message_preview == "最后可见助手"
    empty = _summary_with_count(conv, {}, previews={})
    assert empty.message_count == 0
    assert empty.last_message_preview is None


def test_summary_fills_fallback_title_when_db_empty():
    """DB ``title`` stays empty; response overlay uses ``fallback_title``."""
    from agentcore.conversation.common import fallback_title

    conv = _conv(title=None)
    user = "帮我做一个季度销售复盘\n数据在 sales.csv，需要按大区拆分"
    summary = conversation_summary_from_orm(conv, first_user_message=user)
    assert conv.title is None
    assert summary.title == fallback_title(user)
    assert summary.title == "帮我做一个季度销售复盘…"


def test_summary_keeps_empty_title_without_user_message():
    conv = _conv(title=None)
    summary = conversation_summary_from_orm(conv)
    assert conv.title is None
    assert summary.title is None


def test_summary_does_not_overlay_minted_title():
    conv = _conv(title="季度复盘")
    summary = conversation_summary_from_orm(
        conv, first_user_message="帮我做一个季度销售复盘\n数据在 sales.csv"
    )
    assert summary.title == "季度复盘"
    assert conv.title == "季度复盘"


def test_summary_with_count_uses_first_user_map_when_untitled():
    from agentcore.conversation.common import fallback_title

    conv = _conv(title=None)
    user = "帮我写一份周报"
    summary = _summary_with_count(
        conv, {CONV_ID: 2}, first_user_messages={CONV_ID: user}
    )
    assert conv.title is None
    assert summary.title == fallback_title(user)


@pytest.mark.asyncio
async def test_untitled_batch_only_queries_empty_titles():
    called: list[list[str]] = []

    class _Repo:
        async def first_user_contents_for_conversations(self, ids: list[str]) -> dict[str, str]:
            called.append(list(ids))
            return {ids[0]: "帮我写周报"} if ids else {}

    titled = _conv(title="已铸")
    empty_id = "22222222-2222-4222-8222-222222222222"
    empty = _conv(id=empty_id, title=None)
    out = await _first_user_contents_for_untitled(_Repo(), [titled, empty])  # type: ignore[arg-type]
    assert called == [[empty_id]]
    assert out == {empty_id: "帮我写周报"}

    called.clear()
    skipped = await _first_user_contents_for_untitled(_Repo(), [titled])  # type: ignore[arg-type]
    assert called == []
    assert skipped == {}


# --- touch_activity SQL -------------------------------------------------------


@pytest.mark.asyncio
async def test_touch_activity_stamps_utc_now():
    session = _RecordingSession()
    before = datetime.now(UTC)
    await ConversationRepository(session).touch_activity(CONV_ID, commit=False)
    after = datetime.now(UTC)

    stmt = next(s for s in session.statements if isinstance(s, Update))
    sql = _sql(stmt)
    assert "updated_at=<ts:" in sql.split("WHERE")[0]
    assert f"conversations.id = '{CONV_ID}'" in sql
    ts = datetime.fromisoformat(sql.split("<ts:")[1].split(">")[0])
    assert ts.tzinfo is not None
    assert before <= ts <= after
    assert session.commits == 0
    assert session.flushes == 1


@pytest.mark.asyncio
async def test_previews_query_is_assistant_only():
    session = _RecordingSession([_Result(rows=[])])
    await MessageRepository(session).previews_for_conversations([CONV_ID])
    sql = _sql(session.statements[0])
    assert "messages.role = 'assistant'" in sql
    assert "messages.role = 'user'" not in sql
    assert "row_number" in sql.lower()


@pytest.mark.asyncio
async def test_first_user_contents_skips_empty_ids():
    session = _RecordingSession()
    out = await MessageRepository(session).first_user_contents_for_conversations([])
    assert out == {}
    assert session.statements == []


@pytest.mark.asyncio
async def test_first_user_contents_is_one_user_query():
    other = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    session = _RecordingSession([_Result(rows=[(CONV_ID, "帮我写周报")])])
    out = await MessageRepository(session).first_user_contents_for_conversations(
        [CONV_ID, other]
    )
    assert len(session.statements) == 1
    sql = _sql(session.statements[0])
    assert "messages.role = 'user'" in sql
    assert "row_number" in sql.lower()
    assert "messages.role = 'assistant'" not in sql
    assert out == {CONV_ID: "帮我写周报"}


@pytest.mark.asyncio
async def test_previews_query_drops_empty_and_chrome_under_lookback():
    session = _RecordingSession([_Result(rows=[])])
    await MessageRepository(session).previews_for_conversations([CONV_ID])
    sql = _sql(session.statements[0])
    assert str(PREVIEW_SQL_LOOKBACK) in sql
    assert "<=" in sql
    assert "btrim" in sql.lower()
    assert "NOT IN" in sql
    assert "已停止" in sql
    for body in PREVIEW_CHROME_ONLY:
        assert body in sql


@pytest.mark.asyncio
async def test_delete_by_id_defaults_to_commit():
    session = _RecordingSession()
    await MessageRepository(session).delete_by_id("m1", conversation_id=CONV_ID)
    assert session.commits == 1
    assert session.flushes == 0


@pytest.mark.asyncio
async def test_delete_by_id_commit_false_flushes_only():
    session = _RecordingSession()
    await MessageRepository(session).delete_by_id(
        "m1", conversation_id=CONV_ID, commit=False
    )
    assert session.commits == 0
    assert session.flushes == 1
