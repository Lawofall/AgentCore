"""最近删除（项目回收站）：软删打标、恢复解档、过期不给恢复。

软删曾是单程票，这一层给它一条回头路。四条人定死的约束钉在这里：

  * **软删不许改写成员对话的 ``updated_at``**——对话「最近活动」只由回合
    ``touch_activity`` 推进；批量误写会把每条成员刷成删除时刻，侧栏最近活动排序
    当场作废且事后追不回。自赋值
    ``updated_at=Conversation.updated_at`` 钉住位次——这是编译期性质，
    所以直接断言编译出来的 SQL。
  * **只给「删除时尚未归档」的行打标**——legacy ``Conversation.archived`` 与文件夹
    所有者 ``conversation_preferences.archived`` 都算已归档；无脑全解档会把用户
    自己归档的帖拽回侧栏。
  * **回收站只列用户删的**——``reclaim_orphan_auto_desk_folder`` 调同一个
    ``soft_delete``，而它铸的文件夹名字来自对话标题、看起来像正常项目。
  * **过了保留期返 409**——工作区文件可能已经没了，不许静默成功。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Delete, Select, Update
from sqlalchemy.dialects import postgresql

from agentcore.api.routes.folders import (
    list_deleted_folders,
    restore_deleted_folder,
    router,
)
from agentcore.config import settings
from agentcore.core.errors import ConflictError, NotFoundError
from agentcore.db.repositories.conversations import ConversationRepository
from agentcore.db.repositories.folders import (
    FOLDER_DELETE_ORIGIN_AUTO_DESK_RECLAIM,
    FOLDER_DELETE_ORIGIN_USER,
    FolderRepository,
)

FOLDER_ID = "11111111-2222-4333-8444-555555555555"
USER_ID = "66666666-7777-4888-8999-aaaaaaaaaaaa"


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


def _fake_folder(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": FOLDER_ID,
        "user_id": USER_ID,
        "name": "报告",
        "local_root_id": None,
        "local_subpath": None,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 2, tzinfo=UTC),
        "deleted_at": None,
        "delete_origin": None,
        # 云端目录树是另一条线。这些用例只钉回收站的不变量，所以让文件夹不占树位，
        # 子树重排/墓碑搬迁短路掉——那部分归 tree_ops 自己的用例管。
        "rel_path": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _soft_delete_session(folder: Any) -> _RecordingSession:
    """``soft_delete`` 读两次文件夹：自己一次，``list_live_subtree_ids`` 一次。"""
    return _RecordingSession([_Result(scalar=folder), _Result(scalar=folder)])


def _restore_session(*, rowcount: int, restored: Any = None) -> _RecordingSession:
    """``restore``：先读待恢复行，再条件 UPDATE，成功后解档并回读。"""
    results = [_Result(scalar=_fake_folder(deleted_at=datetime(2026, 2, 2, tzinfo=UTC)))]
    results.append(_Result(rowcount=rowcount))
    if rowcount == 1:
        results.append(_Result())
        results.append(_Result(scalar=restored or _fake_folder()))
    return _RecordingSession(results)


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


def _updates(session: _RecordingSession, table: str) -> list[str]:
    return [
        _sql(s)
        for s in session.statements
        if isinstance(s, Update) and s.table.name == table
    ]


def _selects(session: _RecordingSession) -> list[str]:
    return [_sql(s) for s in session.statements if isinstance(s, Select)]


def _member_archive_sql(session: _RecordingSession) -> str:
    """The one UPDATE that flips ``conversations.archived`` (not the pointer clears)."""
    matches = [sql for sql in _updates(session, "conversations") if "SET archived" in sql]
    assert len(matches) == 1, [_sql(s) for s in session.statements]
    return matches[0]


# --- 软删：不许碰 updated_at ------------------------------------------------------


async def test_soft_delete_does_not_restamp_member_updated_at():
    """自赋值保持位次：SET 里必须是 updated_at=conversations.updated_at。"""
    session = _soft_delete_session(_fake_folder())

    assert await FolderRepository(session).soft_delete(FOLDER_ID, user_id=USER_ID)

    sql = _member_archive_sql(session)
    set_clause = sql.split("WHERE")[0]
    assert "updated_at=conversations.updated_at" in set_clause
    # 若误写成绑定值（编译期渲染成 <ts:…>/NULL）而不是列自赋值，就会把成员顶到侧栏最前。
    assert "<ts:" not in set_clause
    assert "updated_at=NULL" not in set_clause


async def test_restore_does_not_restamp_member_updated_at():
    """解档同理——恢复也不该把整批对话顶到列表最前面。"""
    session = _restore_session(rowcount=1)

    await FolderRepository(session).restore(
        FOLDER_ID, user_id=USER_ID, not_before=datetime(2026, 1, 1, tzinfo=UTC)
    )

    sql = _member_archive_sql(session)
    assert "updated_at=conversations.updated_at" in sql.split("WHERE")[0]


# --- 软删：打标范围 ---------------------------------------------------------------


async def test_soft_delete_marks_only_unarchived_visible_live_rows():
    """只打未归档行；已软删对话与隐藏基础设施行一并排除（对齐其它读路径）。"""
    session = _soft_delete_session(_fake_folder())

    await FolderRepository(session).soft_delete(FOLDER_ID, user_id=USER_ID)

    sql = _member_archive_sql(session)
    set_clause, where_clause = sql.split("WHERE", 1)
    assert "archived_by_folder_delete=true" in set_clause
    # 用户自己归档的对话不打标 ⇒ 恢复时不会被拽回侧栏。
    assert "conversations.archived IS false" in where_clause
    assert "conversation_preferences.archived IS true" in where_clause
    assert f"conversation_preferences.user_id = '{USER_ID}'" in where_clause
    assert "conversations.id NOT IN" in where_clause
    assert "conversations.deleted_at IS NULL" in where_clause
    assert "conversations.mode NOT IN ('handoff', 'standing')" in where_clause
    # Desk archive is every thread on the folder (incl. members' Conversation.user_id),
    # not owner-scoped. Prefs exclusion is keyed on conversation_preferences.user_id.
    assert "conversations.user_id" not in where_clause
    # ``IN`` 而非 ``=``：软删连带整棵子树（UUID 列渲染带 ``::UUID`` 后缀，故不比整段）。
    assert f"conversations.folder_id IN ('{FOLDER_ID}'" in where_clause


def _folder_delete_stamp(session: _RecordingSession) -> dict[str, Any]:
    """写进 ``folders`` 的删除标记（``deleted_at`` / ``delete_origin`` 的绑定值）。"""
    stmts = [
        s for s in session.statements if isinstance(s, Update) and s.table.name == "folders"
    ]
    assert len(stmts) == 1, [_sql(s) for s in session.statements]
    return stmts[0].compile(dialect=postgresql.dialect()).params


async def test_soft_delete_stamps_utc_and_user_origin():
    """``deleted_at`` 必须带 UTC 时区（列是 TIMESTAMPTZ，naive 会按 UTC 绑定）。"""
    session = _soft_delete_session(_fake_folder())

    await FolderRepository(session).soft_delete(FOLDER_ID, user_id=USER_ID)

    stamped = _folder_delete_stamp(session)
    assert stamped["deleted_at"].tzinfo is not None
    assert stamped["deleted_at"].utcoffset() == timedelta(0)
    assert stamped["delete_origin"] == FOLDER_DELETE_ORIGIN_USER
    assert session.commits == 1


async def test_soft_delete_records_machine_origin_when_asked():
    session = _soft_delete_session(_fake_folder())

    await FolderRepository(session).soft_delete(
        FOLDER_ID, user_id=USER_ID, origin=FOLDER_DELETE_ORIGIN_AUTO_DESK_RECLAIM
    )

    assert _folder_delete_stamp(session)["delete_origin"] == FOLDER_DELETE_ORIGIN_AUTO_DESK_RECLAIM


async def test_soft_delete_missing_folder_writes_nothing():
    session = _RecordingSession([_Result(scalar=None)])

    assert not await FolderRepository(session).soft_delete(FOLDER_ID, user_id=USER_ID)
    assert not [s for s in session.statements if isinstance(s, Update)]
    assert session.commits == 0


# --- 自动桌回收不进回收站 ---------------------------------------------------------


async def test_deleted_list_only_selects_user_deleted_rows():
    """列表只认 delete_origin='user'：自动桌回收与历史 NULL 行都不出现。"""
    session = _RecordingSession([_Result(rows=[])])

    await FolderRepository(session).list_deleted_by_user(
        USER_ID, not_before=datetime(2026, 1, 1, tzinfo=UTC), limit=50
    )

    sql = _selects(session)[0]
    assert f"folders.delete_origin = '{FOLDER_DELETE_ORIGIN_USER}'" in sql
    assert "folders.deleted_at IS NOT NULL" in sql
    # 过了保留期的不再可恢复，也就不该列。
    assert "folders.deleted_at > <ts:2026-01-01" in sql
    assert f"folders.user_id = '{USER_ID}'" in sql


async def test_auto_desk_reclaim_tags_machine_origin(monkeypatch: pytest.MonkeyPatch):
    """裸聊自动云桌回收走同一个 soft_delete，但必须自报家门。"""
    from agentcore.runtime.delegate.target_desktop_auto_cloud import (
        reclaim_orphan_auto_desk_folder,
    )

    calls: list[dict[str, Any]] = []

    class _Repo:
        def __init__(self, session: Any) -> None:
            del session

        async def soft_delete(self, folder_id: str, **kwargs: Any) -> bool:
            calls.append({"folder_id": folder_id, **kwargs})
            return True

    class _CM:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr("agentcore.db.base.async_session_factory", lambda: _CM())
    monkeypatch.setattr("agentcore.db.repositories.FolderRepository", _Repo)

    await reclaim_orphan_auto_desk_folder(user_id=USER_ID, folder_id=FOLDER_ID)

    assert calls == [
        {
            "folder_id": FOLDER_ID,
            "user_id": USER_ID,
            "origin": FOLDER_DELETE_ORIGIN_AUTO_DESK_RECLAIM,
        }
    ]


# --- 恢复：条件 UPDATE + rowcount --------------------------------------------------


async def test_restore_unarchives_only_marked_conversations():
    session = _restore_session(rowcount=1)

    await FolderRepository(session).restore(
        FOLDER_ID, user_id=USER_ID, not_before=datetime(2026, 1, 1, tzinfo=UTC)
    )

    set_clause, where_clause = _member_archive_sql(session).split("WHERE", 1)
    assert "archived=false" in set_clause
    assert "archived_by_folder_delete=false" in set_clause
    # 打过标的那批，仅此而已——用户自己归档的对话没有标，留在归档里。
    assert "conversations.archived_by_folder_delete IS true" in where_clause
    assert f"conversations.folder_id IN ('{FOLDER_ID}'" in where_clause


async def test_restore_folder_update_carries_retention_and_origin_predicates():
    """条件 UPDATE 自带保留期与归属判据 ⇒ 这一行的恢复自身是原子的。"""
    session = _restore_session(rowcount=1)

    await FolderRepository(session).restore(
        FOLDER_ID, user_id=USER_ID, not_before=datetime(2026, 2, 1, tzinfo=UTC)
    )

    sql = _updates(session, "folders")[0]
    set_clause, where_clause = sql.split("WHERE", 1)
    assert "deleted_at=NULL" in set_clause
    assert "delete_origin=NULL" in set_clause
    assert "folders.deleted_at > <ts:2026-02-01" in where_clause
    assert f"folders.delete_origin = '{FOLDER_DELETE_ORIGIN_USER}'" in where_clause
    assert f"folders.user_id = '{USER_ID}'" in where_clause


async def test_restore_losing_the_purge_race_touches_no_conversations():
    """rowcount=0（清扫抢先）⇒ 不解档、不提交，如实失败。"""
    session = _restore_session(rowcount=0)

    restored = await FolderRepository(session).restore(
        FOLDER_ID, user_id=USER_ID, not_before=datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert restored is None
    # 读一次 + 条件 UPDATE 落空，到此为止：没有解档、没有提交。
    assert not [s for s in session.statements if isinstance(s, Update) and s.table.name != "folders"]
    assert session.commits == 0


# --- 路由：过期 409、竞态 409、未知 404 --------------------------------------------


class _StubRepo:
    def __init__(self, *, deleted: Any = None) -> None:
        self._deleted = deleted
        self.listed: list[dict[str, Any]] = []

    async def get_deleted_by_id(self, folder_id: str, *, user_id: str) -> Any:
        del folder_id, user_id
        return self._deleted

    async def list_deleted_by_user(self, user_id: str, **kwargs: Any) -> list[Any]:
        self.listed.append({"user_id": user_id, **kwargs})
        return [self._deleted] if self._deleted is not None else []


class _TreeRestoreSpy:
    """替身：恢复的落地在 ``folders.tree_ops``（连带墓碑目录搬回），路由只该调它一次。"""

    def __init__(self, restored: Any = None) -> None:
        self.restored = restored
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, session: Any, **kwargs: Any) -> Any:
        del session
        self.calls.append(kwargs)
        return self.restored


def _spy_on_tree_restore(
    monkeypatch: pytest.MonkeyPatch, restored: Any = None
) -> _TreeRestoreSpy:
    spy = _TreeRestoreSpy(restored)
    monkeypatch.setattr("agentcore.api.routes.folders.restore_folder_tree", spy)
    return spy


def _user() -> SimpleNamespace:
    return SimpleNamespace(user_id=USER_ID)


async def test_restore_past_retention_is_409_not_silent_success(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "workspace_retention_days", 30)
    expired = _fake_folder(
        deleted_at=datetime.now(UTC) - timedelta(days=31),
        delete_origin=FOLDER_DELETE_ORIGIN_USER,
    )
    repo = _StubRepo(deleted=expired)
    spy = _spy_on_tree_restore(monkeypatch)

    with pytest.raises(ConflictError) as exc:
        await restore_deleted_folder(FOLDER_ID, _user(), repo=repo, session=object())

    assert exc.value.status_code == 409
    assert "30 天" in str(exc.value)
    assert spy.calls == []


async def test_restore_race_with_sweeper_surfaces_as_409(
    monkeypatch: pytest.MonkeyPatch,
):
    """查得到但条件 UPDATE 落空 ⇒ 409「已被清理」，不对账不重试。"""
    monkeypatch.setattr(settings, "workspace_retention_days", 30)
    fresh = _fake_folder(
        deleted_at=datetime.now(UTC) - timedelta(days=1),
        delete_origin=FOLDER_DELETE_ORIGIN_USER,
    )
    repo = _StubRepo(deleted=fresh)
    spy = _spy_on_tree_restore(monkeypatch, restored=None)

    with pytest.raises(ConflictError) as exc:
        await restore_deleted_folder(FOLDER_ID, _user(), repo=repo, session=object())

    assert exc.value.status_code == 409
    assert "已被清理" in str(exc.value)
    assert len(spy.calls) == 1
    # 保留期判据必须一路带到条件 UPDATE，否则「查得到」和「改得动」会各算各的。
    assert spy.calls[0]["not_before"] < datetime.now(UTC)


async def test_restore_unknown_project_is_404(monkeypatch: pytest.MonkeyPatch):
    repo = _StubRepo(deleted=None)
    spy = _spy_on_tree_restore(monkeypatch)

    with pytest.raises(NotFoundError) as exc:
        await restore_deleted_folder(FOLDER_ID, _user(), repo=repo, session=object())

    assert exc.value.status_code == 404
    assert spy.calls == []


async def test_restore_returns_the_live_project(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "workspace_retention_days", 30)
    deleted = _fake_folder(
        deleted_at=datetime.now(UTC) - timedelta(days=2),
        delete_origin=FOLDER_DELETE_ORIGIN_USER,
    )
    repo = _StubRepo(deleted=deleted)
    _spy_on_tree_restore(monkeypatch, restored=_fake_folder(name="报告"))

    summary = await restore_deleted_folder(FOLDER_ID, _user(), repo=repo, session=object())

    assert summary.id == FOLDER_ID
    assert summary.name == "报告"
    assert summary.mode == "cloud"


async def test_trash_list_computes_purge_moment_server_side(
    monkeypatch: pytest.MonkeyPatch,
):
    """前端不该拿 deleted_at 自己减天数——清算时刻由服务端算好。"""
    monkeypatch.setattr(settings, "workspace_retention_days", 30)
    deleted_at = datetime(2026, 8, 1, 3, 0, tzinfo=UTC)
    repo = _StubRepo(
        deleted=_fake_folder(
            deleted_at=deleted_at, delete_origin=FOLDER_DELETE_ORIGIN_USER
        )
    )

    body = await list_deleted_folders(_user(), repo=repo)

    assert body.total == 1
    assert body.retention_days == 30
    entry = body.data[0]
    assert entry.deleted_at == deleted_at
    assert entry.purge_at == deleted_at + timedelta(days=30)
    # 列表也把过期行挡在外面：仓储收到的是清扫用的同一个截止点。
    assert body.data[0].mode == "cloud"
    assert repo.listed[0]["not_before"] < datetime.now(UTC)


# --- 路由注册顺序 ------------------------------------------------------------------


def test_trash_routes_are_registered_before_the_folder_id_matcher():
    """FastAPI 按注册顺序匹配：``/trash`` 晚于 ``/{folder_id}`` 就会被当成项目 id。"""
    paths = [getattr(r, "path", "") for r in router.routes]
    assert paths.index("/folders/trash") < paths.index("/folders/{folder_id}")


# --- 彻底删除：名册与成员开的帖 ----------------------------------------------------


async def test_list_ids_by_folder_does_not_filter_conversation_user_id():
    """彻底删除须含成员开的帖：SQL 只按 folder_id，不滤 Conversation.user_id。"""
    session = _RecordingSession()

    await ConversationRepository(session).list_ids_by_folder(FOLDER_ID, user_id=USER_ID)

    sql = _selects(session)[0]
    assert f"conversations.folder_id = '{FOLDER_ID}'" in sql
    assert "conversations.mode NOT IN ('handoff', 'standing')" in sql
    assert "conversations.user_id" not in sql


async def test_hard_delete_clears_membership_roster_in_same_transaction():
    session = _RecordingSession()

    await FolderRepository(session).hard_delete(FOLDER_ID)

    deletes = [s for s in session.statements if isinstance(s, Delete)]
    assert [s.table.name for s in deletes] == ["folder_members", "folders"]
    assert session.commits == 1
    members_sql = _sql(deletes[0])
    folders_sql = _sql(deletes[1])
    assert f"folder_members.folder_id IN ('{FOLDER_ID}'" in members_sql
    assert f"folders.id IN ('{FOLDER_ID}'" in folders_sql


async def test_hard_delete_many_clears_membership_roster_before_folders():
    session = _RecordingSession()
    other = "99999999-8888-4777-8666-555555555555"

    await FolderRepository(session).hard_delete_many([FOLDER_ID, other])

    deletes = [s for s in session.statements if isinstance(s, Delete)]
    assert [s.table.name for s in deletes] == ["folder_members", "folders"]
    assert session.commits == 1


async def test_hard_delete_many_empty_writes_nothing():
    session = _RecordingSession()

    await FolderRepository(session).hard_delete_many([])

    assert session.statements == []
    assert session.commits == 0
