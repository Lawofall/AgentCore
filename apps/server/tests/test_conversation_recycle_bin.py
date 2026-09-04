"""最近删除（对话）：软删打标、列出、恢复回原位、过期不给恢复。

删除本来就是软删——行一直都在，只是从来没有任何东西列出过它们，所以「删了就没了」
在事实层面成立。这一层给它一条回头路，四条约束钉在这里：

  * **软删不许改写 ``updated_at``**——「最近活动」只由回合 ``touch_activity`` 推进；
    一次误写就会把位次刷成删除时刻，恢复后落进「今天」且原值追不回来。自赋值
    钉住「家政不顶位次」，是编译期性质，所以直接断言编译出的 SQL。
  * **``deleted_at`` 必须带 UTC 时区**——列是 ``TIMESTAMPTZ``，asyncpg 把 naive 值按 UTC
    绑定；naive 的本地 ``now()`` 会按机器时区偏移，而「删除于」「还剩几天」直接渲染它。
  * **回收站不列基础设施行**——``handoff`` / ``standing`` 由机器路径软删，用户不知道
    它们是什么。
  * **过了保留期返 409**——消息可能已被清扫，不许静默成功。

路由的读侧配额度用真 SQL 断言（仓储层），写侧用替身钉 409 / 404 的分叉。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Delete, Select, Update
from sqlalchemy.dialects import postgresql

from agentcore.api.routes.conversations.crud import (
    list_deleted_conversations,
    restore_deleted_conversation,
    router,
)
from agentcore.config import settings
from agentcore.core.errors import ConflictError, NotFoundError
from agentcore.db.repositories.conversations import ConversationRepository

CONV_ID = "11111111-2222-4333-8444-555555555555"
USER_ID = "66666666-7777-4888-8999-aaaaaaaaaaaa"
FOLDER_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


# --- fakes ---------------------------------------------------------------------


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class _Result:
    def __init__(
        self, *, scalar: Any = None, rows: list[Any] | None = None, rowcount: int = 0
    ) -> None:
        self._scalar = scalar
        self._rows = rows or []
        self.rowcount = rowcount

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)


class _RecordingSession:
    """Records every statement and replays canned results in order.

    Lets the repository's real SQL be inspected without a database — the
    ``updated_at`` self-assign is a compile-time property of the SET clause.
    """

    def __init__(self, results: list[_Result] | None = None) -> None:
        self.statements: list[Any] = []
        self.commits = 0
        self._results = list(results or [])

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _Result:
        self.statements.append(statement)
        return self._results.pop(0) if self._results else _Result()

    async def commit(self) -> None:
        self.commits += 1


def _fake_conversation(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": CONV_ID,
        "user_id": USER_ID,
        "title": "定价讨论",
        "folder_id": FOLDER_ID,
        "local_container_root_id": None,
        "mode": "chat",
        "pinned": False,
        "archived": False,
        "permission_axes": {},
        "deep_research_auto": False,
        "model_profile_id": None,
        "compaction_summary": None,
        "compacted_through": None,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 2, tzinfo=UTC),
        "deleted_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _sql(statement: Any) -> str:
    """Compiled Postgres SQL with bind values inlined, for readable assertions.

    Hand-substituted rather than compiled with ``literal_binds``: SQLAlchemy has no
    literal renderer for ``DateTime``, and the retention predicates bind timestamps.
    """
    compiled = statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"render_postcompile": True}
    )
    sql = str(compiled)
    for name, value in compiled.params.items():
        if isinstance(value, datetime):
            rendered = f"<ts:{value.isoformat()}>"
        elif value is None:
            rendered = "NULL"
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, int):
            rendered = str(value)
        else:
            rendered = f"'{value}'"
        sql = sql.replace(f"%({name})s", rendered)
    return sql


def _updates(session: _RecordingSession) -> list[str]:
    return [
        _sql(s)
        for s in session.statements
        if isinstance(s, Update) and s.table.name == "conversations"
    ]


def _selects(session: _RecordingSession) -> list[str]:
    return [_sql(s) for s in session.statements if isinstance(s, Select)]


def _soft_delete_session(conv: Any) -> _RecordingSession:
    """``soft_delete`` 先读对话，再清 run_sessions，最后打删除标。"""
    return _RecordingSession([_Result(scalar=conv)])


# --- 软删：不许碰 updated_at，时间戳带 UTC ------------------------------------------


async def test_soft_delete_does_not_restamp_updated_at():
    """自赋值保持位次：SET 里必须是 updated_at=conversations.updated_at。"""
    session = _soft_delete_session(_fake_conversation())

    assert await ConversationRepository(session).soft_delete(CONV_ID, user_id=USER_ID)

    set_clause = _updates(session)[0].split("WHERE")[0]
    assert "updated_at=conversations.updated_at" in set_clause
    # 若误写成绑定值（编译期渲染成 <ts:…>），就是把「最近活动」改成了删除时刻。
    assert "updated_at=<ts:" not in set_clause


async def test_soft_delete_stamps_utc_aware_deleted_at():
    """``deleted_at`` 必须带 UTC 时区（列是 TIMESTAMPTZ，naive 会按 UTC 绑定）。"""
    session = _soft_delete_session(_fake_conversation())

    await ConversationRepository(session).soft_delete(CONV_ID, user_id=USER_ID)

    stmt = next(
        s
        for s in session.statements
        if isinstance(s, Update) and s.table.name == "conversations"
    )
    stamped = stmt.compile(dialect=postgresql.dialect()).params["deleted_at"]
    assert stamped.tzinfo is not None
    assert stamped.utcoffset() == timedelta(0)
    assert session.commits == 1


async def test_soft_delete_missing_conversation_writes_nothing():
    session = _RecordingSession([_Result(scalar=None)])

    assert not await ConversationRepository(session).soft_delete(CONV_ID, user_id=USER_ID)
    assert not [s for s in session.statements if isinstance(s, Update)]
    assert session.commits == 0


# --- 列表：保留期内 + 只列用户看得见的 ----------------------------------------------


async def test_deleted_list_is_scoped_to_owner_window_and_visible_modes():
    session = _RecordingSession([_Result(rows=[])])

    await ConversationRepository(session).list_deleted_by_user(
        USER_ID, not_before=datetime(2026, 1, 1, tzinfo=UTC), limit=50
    )

    sql = _selects(session)[0]
    assert "conversations.deleted_at IS NOT NULL" in sql
    # 过了保留期的不再可恢复，也就不该列——列了就是承诺一个清扫工有权拒绝的恢复。
    assert "conversations.deleted_at > <ts:2026-01-01" in sql
    assert f"conversations.user_id = '{USER_ID}'" in sql
    assert "conversations.mode NOT IN ('handoff', 'standing')" in sql
    assert "ORDER BY conversations.deleted_at DESC" in sql


async def test_deleted_list_short_circuits_on_nonpositive_limit():
    session = _RecordingSession()

    rows = await ConversationRepository(session).list_deleted_by_user(
        USER_ID, not_before=datetime(2026, 1, 1, tzinfo=UTC), limit=0
    )

    assert rows == []
    assert session.statements == []


async def test_get_deleted_by_id_ignores_retention_window():
    """按 id 取不设保留期判据：过期的也要查得到，才能回 409 而不是分不清的 404。"""
    session = _RecordingSession([_Result(scalar=None)])

    await ConversationRepository(session).get_deleted_by_id(CONV_ID, user_id=USER_ID)

    sql = _selects(session)[0]
    assert "conversations.deleted_at IS NOT NULL" in sql
    assert "conversations.deleted_at > " not in sql
    assert f"conversations.user_id = '{USER_ID}'" in sql


# --- 恢复：条件 UPDATE + rowcount ---------------------------------------------------


def _restore_session(*, rowcount: int, restored: Any = None) -> _RecordingSession:
    # restore(): get_deleted_by_id SELECT → conditional UPDATE → optional reread.
    results = [_Result(scalar=_fake_conversation())]
    results.append(_Result(rowcount=rowcount))
    if rowcount == 1:
        results.append(_Result(scalar=restored or _fake_conversation()))
    return _RecordingSession(results)


async def test_restore_only_clears_deleted_at():
    """回原位靠的是「什么都不动」：folder_id / pinned / archived 一个都不在 SET 里。"""
    session = _restore_session(rowcount=1)

    await ConversationRepository(session).restore(
        CONV_ID, user_id=USER_ID, not_before=datetime(2026, 1, 1, tzinfo=UTC)
    )

    set_clause, where_clause = _updates(session)[0].split("WHERE", 1)
    assert "deleted_at=NULL" in set_clause
    assert "updated_at=conversations.updated_at" in set_clause
    for untouched in ("folder_id", "pinned", "archived"):
        assert untouched not in set_clause
    # 保留期判据必须在同一条 UPDATE 上，否则「查得到」和「改得动」各算各的。
    # 成员桌上的行 Conversation.user_id 可能不是调用者；可见性已由 get_deleted_by_id 闸过。
    assert "conversations.deleted_at > <ts:2026-01-01" in where_clause
    assert f"conversations.id = '{CONV_ID}'" in where_clause
    assert session.commits == 1


async def test_restore_losing_the_purge_race_commits_nothing():
    """rowcount=0（清扫抢先）⇒ 不回读、不提交，如实失败。"""
    session = _restore_session(rowcount=0)

    restored = await ConversationRepository(session).restore(
        CONV_ID, user_id=USER_ID, not_before=datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert restored is None
    assert len(_selects(session)) == 1
    assert session.commits == 0


async def test_restore_rereads_with_populate_existing():
    """回读要盖掉身份映射里那份仍带 deleted_at 的旧对象（expire_on_commit=False）。"""
    session = _restore_session(rowcount=1)

    await ConversationRepository(session).restore(
        CONV_ID, user_id=USER_ID, not_before=datetime(2026, 1, 1, tzinfo=UTC)
    )

    rereads = [s for s in session.statements if isinstance(s, Select)]
    assert rereads[-1].get_execution_options().get("populate_existing") is True


# --- 路由：过期 409、竞态 409、未知 404 ---------------------------------------------


class _StubRepo:
    def __init__(self, *, deleted: Any = None, restored: Any = None) -> None:
        self._deleted = deleted
        self._restored = restored
        self.listed: list[dict[str, Any]] = []
        self.restores: list[dict[str, Any]] = []

    async def get_deleted_by_id(self, conversation_id: str, *, user_id: str) -> Any:
        del conversation_id, user_id
        return self._deleted

    async def list_deleted_by_user(self, user_id: str, **kwargs: Any) -> list[Any]:
        self.listed.append({"user_id": user_id, **kwargs})
        return [self._deleted] if self._deleted is not None else []

    async def restore(self, conversation_id: str, **kwargs: Any) -> Any:
        self.restores.append({"conversation_id": conversation_id, **kwargs})
        return self._restored


class _StubMessageRepo:
    def __init__(self, counts: dict[str, int] | None = None) -> None:
        self._counts = counts or {}

    async def counts_for_conversations(self, ids: list[str]) -> dict[str, int]:
        return {cid: self._counts.get(cid, 0) for cid in ids}


def _user() -> SimpleNamespace:
    return SimpleNamespace(user_id=USER_ID)


async def test_restore_past_retention_is_409_not_silent_success(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "workspace_retention_days", 30)
    expired = _fake_conversation(deleted_at=datetime.now(UTC) - timedelta(days=31))
    repo = _StubRepo(deleted=expired)

    with pytest.raises(ConflictError) as exc:
        await restore_deleted_conversation(
            CONV_ID, _user(), repo=repo, msg_repo=_StubMessageRepo()
        )

    assert exc.value.status_code == 409
    assert "30 天" in str(exc.value)
    assert repo.restores == []


async def test_restore_race_with_sweeper_surfaces_as_409(
    monkeypatch: pytest.MonkeyPatch,
):
    """查得到但条件 UPDATE 落空 ⇒ 409「已被清理」，不对账不重试。"""
    monkeypatch.setattr(settings, "workspace_retention_days", 30)
    repo = _StubRepo(
        deleted=_fake_conversation(deleted_at=datetime.now(UTC) - timedelta(days=1)),
        restored=None,
    )

    with pytest.raises(ConflictError) as exc:
        await restore_deleted_conversation(
            CONV_ID, _user(), repo=repo, msg_repo=_StubMessageRepo()
        )

    assert exc.value.status_code == 409
    assert "已被清理" in str(exc.value)
    # 保留期判据一路带到条件 UPDATE，与列表用的是同一个截止点。
    assert repo.restores[0]["not_before"] < datetime.now(UTC)


async def test_restore_unknown_conversation_is_404():
    repo = _StubRepo(deleted=None)

    with pytest.raises(NotFoundError):
        await restore_deleted_conversation(
            CONV_ID, _user(), repo=repo, msg_repo=_StubMessageRepo()
        )

    assert repo.restores == []


async def test_restore_returns_the_chat_in_its_original_group(
    monkeypatch: pytest.MonkeyPatch,
):
    """验收本体：恢复后仍属原项目、仍带原「最近活动」时间 ⇒ 回到原来的分组位置。"""
    monkeypatch.setattr(settings, "workspace_retention_days", 30)
    updated_at = datetime(2026, 3, 3, tzinfo=UTC)
    repo = _StubRepo(
        deleted=_fake_conversation(deleted_at=datetime.now(UTC) - timedelta(days=2)),
        restored=_fake_conversation(
            folder_id=FOLDER_ID, pinned=True, updated_at=updated_at
        ),
    )

    summary = await restore_deleted_conversation(
        CONV_ID, _user(), repo=repo, msg_repo=_StubMessageRepo({CONV_ID: 12})
    )

    assert summary.id == CONV_ID
    assert summary.folder_id == FOLDER_ID
    assert summary.pinned is True
    assert summary.updated_at == updated_at
    assert summary.message_count == 12


async def test_trash_list_computes_purge_moment_server_side(
    monkeypatch: pytest.MonkeyPatch,
):
    """前端不该拿 deleted_at 自己减天数——清算时刻由服务端算好。"""
    monkeypatch.setattr(settings, "workspace_retention_days", 30)
    deleted_at = datetime(2026, 8, 1, 3, 0, tzinfo=UTC)
    repo = _StubRepo(deleted=_fake_conversation(deleted_at=deleted_at))

    body = await list_deleted_conversations(
        _user(), repo=repo, msg_repo=_StubMessageRepo({CONV_ID: 5})
    )

    assert body.total == 1
    assert body.retention_days == 30
    entry = body.data[0]
    assert entry.deleted_at == deleted_at
    assert entry.purge_at == deleted_at + timedelta(days=30)
    assert entry.message_count == 5
    assert entry.folder_id == FOLDER_ID
    # 列表也把过期行挡在外面：仓储收到的是清扫用的同一个截止点。
    assert repo.listed[0]["not_before"] < datetime.now(UTC)


# --- 路由注册顺序 ------------------------------------------------------------------


def test_trash_routes_are_registered_before_the_conversation_id_matcher():
    """FastAPI 按注册顺序匹配：``/trash`` 晚于 ``/{conversation_id}`` 就会被当成对话 id。"""
    paths = [getattr(r, "path", "") for r in router.routes]
    assert paths.index("/conversations/trash") < paths.index(
        "/conversations/{conversation_id}"
    )


# --- 硬删：先清 per-user prefs ------------------------------------------------------


async def test_hard_delete_clears_conversation_preferences_first():
    session = _RecordingSession()

    await ConversationRepository(session).hard_delete(CONV_ID)

    deletes = [s for s in session.statements if isinstance(s, Delete)]
    tables = [s.table.name for s in deletes]
    assert tables[0] == "conversation_preferences"
    assert tables.index("conversation_preferences") < tables.index("conversations")
    sql = _sql(deletes[0])
    assert f"conversation_preferences.conversation_id = '{CONV_ID}'" in sql
    assert session.commits == 1
