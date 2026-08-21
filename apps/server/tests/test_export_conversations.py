"""巡检 ``export_conversations``：窗内仍活跃的旧会话不得按创建日落选。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.pool import StaticPool

from scripts.export_conversations import (
    _conversation_window_clause,
    export_conversations,
)

USER = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
MSG_CONV = "11111111-1111-4111-8111-111111111111"
TM_CONV = "22222222-2222-4222-8222-222222222222"
SILENT = "33333333-3333-4333-8333-333333333333"
DELETED = "44444444-4444-4444-8444-444444444444"
FRESH = "55555555-5555-4555-8555-555555555555"

_LIVE = {
    "conversations": {"id", "user_id", "title", "created_at"},
    "messages": {"id", "conversation_id", "role", "content", "created_at"},
    "cost_events": {"id", "conversation_id", "created_at"},
    "turn_metrics": {"id", "conversation_id", "created_at"},
    "turn_journal": set(),
}


class _AsyncEngine:
    def __init__(self, sync_engine: Any) -> None:
        self._sync = sync_engine

    def connect(self) -> _AsyncConnect:
        return _AsyncConnect(self._sync)

    async def dispose(self) -> None:
        self._sync.dispose()


class _AsyncConnect:
    def __init__(self, sync_engine: Any) -> None:
        self._engine = sync_engine
        self._cm: Any = None

    async def __aenter__(self) -> _AsyncConn:
        self._cm = self._engine.connect()
        return _AsyncConn(self._cm.__enter__())

    async def __aexit__(self, *exc: object) -> None:
        self._cm.__exit__(*exc)


class _AsyncConn:
    def __init__(self, sync_conn: Any) -> None:
        self._sync = sync_conn

    async def execute(self, statement: Any, parameters: Any = None) -> Any:
        if parameters is not None:
            return self._sync.execute(statement, parameters)
        compiled = statement.compile(dialect=self._sync.engine.dialect)
        if "__[POSTCOMPILE_" not in str(compiled):
            return self._sync.execute(statement)
        # sqlite leaves PG UUID expanding IN as one list bind; render it to (?, ?).
        compiled = statement.compile(
            dialect=self._sync.engine.dialect,
            compile_kwargs={"render_postcompile": True},
        )
        args = tuple(compiled.params[name] for name in (compiled.positiontup or ()))
        return self._sync.exec_driver_sql(str(compiled), args)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    text_body = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text_body.splitlines() if line]


def _sql(clause: Any) -> str:
    return str(clause.compile(dialect=postgresql.dialect())).lower()


def test_window_clause_includes_activity_exists() -> None:
    cutoff = datetime(2026, 8, 1, tzinfo=UTC)
    with_journal = _sql(_conversation_window_clause(cutoff, journal_activity=True))
    assert "conversations.created_at" in with_journal
    assert "messages.created_at" in with_journal
    assert "turn_metrics.created_at" in with_journal
    assert "cost_events.created_at" in with_journal
    assert "turn_journal.created_at" in with_journal

    without = _sql(_conversation_window_clause(cutoff, journal_activity=False))
    assert "turn_journal.created_at" not in without


@pytest.mark.filterwarnings("ignore:The default datetime adapter:DeprecationWarning")
async def test_export_keeps_old_conversations_active_in_window(tmp_path: Path) -> None:
    """创建日在 cutoff 前、窗内有消息或 turn_metrics → conversations + 该会话 messages。"""
    now = datetime.now(UTC)
    old = now - timedelta(days=30)
    recent = now - timedelta(hours=1)
    sync_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        with sync_engine.begin() as conn:
            for ddl in (
                "CREATE TABLE conversations ("
                "id TEXT PRIMARY KEY, user_id TEXT, title TEXT,"
                " created_at DATETIME, deleted_at DATETIME)",
                "CREATE TABLE messages ("
                "id TEXT PRIMARY KEY, conversation_id TEXT, role TEXT,"
                " content TEXT, created_at DATETIME)",
                "CREATE TABLE turn_metrics ("
                "id TEXT PRIMARY KEY, conversation_id TEXT, created_at DATETIME)",
                "CREATE TABLE cost_events ("
                "id TEXT PRIMARY KEY, conversation_id TEXT, created_at DATETIME)",
            ):
                conn.execute(text(ddl))
            conn.execute(
                text(
                    "INSERT INTO conversations "
                    "(id, user_id, title, created_at, deleted_at) VALUES "
                    "(:id, :user_id, :title, :created_at, :deleted_at)"
                ),
                [
                    {
                        "id": MSG_CONV,
                        "user_id": USER,
                        "title": "msg-active",
                        "created_at": old,
                        "deleted_at": None,
                    },
                    {
                        "id": TM_CONV,
                        "user_id": USER,
                        "title": "metrics-active",
                        "created_at": old,
                        "deleted_at": None,
                    },
                    {
                        "id": SILENT,
                        "user_id": USER,
                        "title": "silent-old",
                        "created_at": old,
                        "deleted_at": None,
                    },
                    {
                        "id": DELETED,
                        "user_id": USER,
                        "title": "deleted-active",
                        "created_at": old,
                        "deleted_at": now,
                    },
                    {
                        "id": FRESH,
                        "user_id": USER,
                        "title": "fresh",
                        "created_at": recent,
                        "deleted_at": None,
                    },
                ],
            )
            conn.execute(
                text(
                    "INSERT INTO messages "
                    "(id, conversation_id, role, content, created_at) VALUES "
                    "(:id, :cid, :role, :content, :created_at)"
                ),
                [
                    {
                        "id": "10000000-0000-4000-8000-000000000001",
                        "cid": MSG_CONV,
                        "role": "user",
                        "content": "old-body",
                        "created_at": old,
                    },
                    {
                        "id": "10000000-0000-4000-8000-000000000002",
                        "cid": MSG_CONV,
                        "role": "user",
                        "content": "window-body",
                        "created_at": recent,
                    },
                    {
                        "id": "10000000-0000-4000-8000-000000000003",
                        "cid": TM_CONV,
                        "role": "user",
                        "content": "metrics-old-body",
                        "created_at": old,
                    },
                    {
                        "id": "10000000-0000-4000-8000-000000000004",
                        "cid": SILENT,
                        "role": "user",
                        "content": "silent-body",
                        "created_at": old,
                    },
                    {
                        "id": "10000000-0000-4000-8000-000000000005",
                        "cid": DELETED,
                        "role": "user",
                        "content": "deleted-window-body",
                        "created_at": recent,
                    },
                ],
            )
            conn.execute(
                text(
                    "INSERT INTO turn_metrics (id, conversation_id, created_at) "
                    "VALUES (:id, :cid, :created_at)"
                ),
                {
                    "id": "20000000-0000-4000-8000-000000000001",
                    "cid": TM_CONV,
                    "created_at": recent,
                },
            )

        async def _live(_conn: Any, table: str) -> set[str]:
            return set(_LIVE[table])

        out = tmp_path / "export"
        with (
            patch(
                "scripts.export_conversations._create_engine",
                return_value=_AsyncEngine(sync_engine),
            ),
            patch("scripts.export_conversations._live_columns", side_effect=_live),
        ):
            await export_conversations(7, out, skip_journal=True)

        conv_ids = {row["id"] for row in _jsonl(out / "conversations.jsonl")}
        assert MSG_CONV in conv_ids
        assert TM_CONV in conv_ids
        assert FRESH in conv_ids
        assert SILENT not in conv_ids
        assert DELETED not in conv_ids

        bodies = {row["content"] for row in _jsonl(out / "messages.jsonl")}
        assert "old-body" in bodies
        assert "window-body" in bodies
        assert "metrics-old-body" in bodies
        assert "silent-body" not in bodies
        assert "deleted-window-body" not in bodies
    finally:
        sync_engine.dispose()
