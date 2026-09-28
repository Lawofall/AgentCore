"""Real-Postgres proof: harvest unique index builds beside historical dups."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from agentcore.db.migrations.versions.c8a1f3e6b2d9_messages_harvest_execution_unique import (
    _INDEX,
    _WHERE,
    downgrade,
    upgrade,
)
from agentcore.db.models import Conversation, Message

_HISTORICAL = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
_IN_WINDOW = datetime(2026, 8, 18, 7, 0, tzinfo=UTC)


def _run_upgrade(sync_conn) -> None:
    ctx = MigrationContext.configure(sync_conn)
    with Operations.context(ctx):
        upgrade()


def _run_downgrade(sync_conn) -> None:
    ctx = MigrationContext.configure(sync_conn)
    with Operations.context(ctx):
        downgrade()


async def _drop_index(session) -> None:
    await session.execute(text(f"DROP INDEX IF EXISTS {_INDEX}"))
    await session.commit()


async def _conversation_id(session) -> str:
    cid = str(uuid4())
    session.add(Conversation(id=cid, user_id=str(uuid4())))
    await session.flush()
    return cid


def _harvest_row(*, conversation_id: str, created_at: datetime, execution_id: str) -> Message:
    return Message(
        conversation_id=conversation_id,
        role="user",
        content="【系统收口】",
        usage={"origin": "execution_harvest", "execution_id": execution_id},
        created_at=created_at,
    )


async def test_upgrade_builds_index_when_historical_duplicates_exist(session_factory):
    """Production-shaped: pre-bound dups stay; CREATE INDEX still succeeds."""
    async with session_factory() as session:
        await _drop_index(session)
        cid = await _conversation_id(session)
        session.add(
            _harvest_row(
                conversation_id=cid, created_at=_HISTORICAL, execution_id="exec-hist-dup"
            )
        )
        session.add(
            _harvest_row(
                conversation_id=cid, created_at=_HISTORICAL, execution_id="exec-hist-dup"
            )
        )
        await session.commit()

        conn = await session.connection()
        await conn.run_sync(_run_upgrade)
        await session.commit()

        defn = (
            await session.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE indexname = :n AND schemaname = current_schema()"
                ),
                {"n": _INDEX},
            )
        ).scalar_one()
        assert "TIMESTAMPTZ" in defn.upper() or "timestamp with time zone" in defn.lower()
        assert "2026-08-18 06:00:00" in defn

        session.add(
            _harvest_row(
                conversation_id=cid, created_at=_HISTORICAL, execution_id="exec-hist-dup"
            )
        )
        await session.commit()

        session.add(
            _harvest_row(conversation_id=cid, created_at=_IN_WINDOW, execution_id="exec-new")
        )
        await session.commit()
        session.add(
            _harvest_row(conversation_id=cid, created_at=_IN_WINDOW, execution_id="exec-new")
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        drop_conn = await session.connection()
        await drop_conn.run_sync(_run_downgrade)
        await session.commit()
        gone = (
            await session.execute(
                text(
                    "SELECT 1 FROM pg_indexes "
                    "WHERE indexname = :n AND schemaname = current_schema()"
                ),
                {"n": _INDEX},
            )
        ).scalar_one_or_none()
        assert gone is None


async def test_upgrade_raises_on_in_window_duplicates(session_factory):
    async with session_factory() as session:
        await _drop_index(session)
        cid = await _conversation_id(session)
        session.add(
            _harvest_row(
                conversation_id=cid, created_at=_IN_WINDOW, execution_id="exec-live-dup"
            )
        )
        session.add(
            _harvest_row(
                conversation_id=cid, created_at=_IN_WINDOW, execution_id="exec-live-dup"
            )
        )
        await session.commit()

        conn = await session.connection()
        with pytest.raises(RuntimeError, match="index window"):
            await conn.run_sync(_run_upgrade)


async def test_timestamp_without_tz_predicate_is_rejected(session_factory):
    """Bare timestamp is STABLE vs timestamptz created_at — Postgres refuses it."""
    bad = (
        "CREATE UNIQUE INDEX uq_messages_execution_harvest_bad "
        "ON messages ((usage ->> 'execution_id')) "
        "WHERE role = 'user' "
        "AND usage ->> 'origin' = 'execution_harvest' "
        "AND COALESCE(usage ->> 'execution_id', '') <> '' "
        "AND created_at >= TIMESTAMP '2026-08-18 06:00:00'"
    )
    async with session_factory() as session:
        with pytest.raises(ProgrammingError, match="IMMUTABLE"):
            await session.execute(text(bad))
        await session.rollback()

        ok = (
            f"CREATE UNIQUE INDEX uq_messages_execution_harvest_ok "
            f"ON messages ((usage ->> 'execution_id')) "
            f"WHERE {_WHERE}"
        )
        await session.execute(text(ok))
        await session.commit()
