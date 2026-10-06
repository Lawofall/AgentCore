"""Unit tests for startup migration check / debug auto-upgrade."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcore.config import settings
from agentcore.db import migration_check as mc


def _engine_with_connects(*cms: MagicMock) -> MagicMock:
    """Fake async engine whose ``connect()`` yields the given context managers in order."""
    engine = MagicMock()
    if len(cms) == 1:
        engine.connect = MagicMock(return_value=cms[0])
    else:
        engine.connect = MagicMock(side_effect=list(cms))
    return engine


def _async_cm(conn: MagicMock) -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.fixture
def _isolate_settings(monkeypatch):
    """Ensure each test starts from a known debug flag."""
    monkeypatch.setattr(settings, "debug", False)


def _at_head_conn(head: str) -> MagicMock:
    """Fake connection: revision compare says at-head, schema compare says clean."""
    conn = MagicMock()
    conn.run_sync = AsyncMock(side_effect=[{head}, ([], [])])
    return conn


async def test_prod_skips_auto_upgrade(_isolate_settings, monkeypatch):
    """debug=false must never call alembic upgrade — only the drift notice path."""
    monkeypatch.setattr(settings, "debug", False)
    upgrade = AsyncMock()
    monkeypatch.setattr(mc, "_auto_upgrade_dev", upgrade)
    monkeypatch.setattr(mc, "_script_heads", lambda: {"aaa"})
    monkeypatch.setattr(mc, "engine", _engine_with_connects(_async_cm(_at_head_conn("aaa"))))

    await mc.check_migrations()
    upgrade.assert_not_awaited()


async def test_debug_attempts_auto_upgrade(_isolate_settings, monkeypatch):
    """debug=true runs auto-upgrade before the drift check."""
    monkeypatch.setattr(settings, "debug", True)
    upgrade = AsyncMock()
    monkeypatch.setattr(mc, "_auto_upgrade_dev", upgrade)
    monkeypatch.setattr(mc, "_script_heads", lambda: {"bbb"})
    monkeypatch.setattr(mc, "engine", _engine_with_connects(_async_cm(_at_head_conn("bbb"))))

    await mc.check_migrations()
    upgrade.assert_awaited_once()


async def test_debug_upgrade_failure_refuses_startup(_isolate_settings, monkeypatch):
    """A failed dev upgrade exits startup with the migration error attached."""
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(mc, "_script_heads", lambda: {"head1"})

    preflight = MagicMock()
    preflight.run_sync = AsyncMock(return_value={"behind"})
    monkeypatch.setattr(mc, "engine", _engine_with_connects(_async_cm(preflight)))
    monkeypatch.setattr(
        mc,
        "_upgrade_to_head",
        MagicMock(side_effect=RuntimeError("multiple heads")),
    )

    with pytest.raises(mc.SchemaUpgradeError, match="multiple heads") as raised:
        await mc.check_migrations()
    assert isinstance(raised.value.__cause__, RuntimeError)


async def test_auto_upgrade_noop_when_already_at_head(_isolate_settings, monkeypatch):
    """At-head short-circuit: no alembic command, quiet debug log only."""
    monkeypatch.setattr(mc, "_script_heads", lambda: {"head"})

    conn = MagicMock()
    conn.run_sync = AsyncMock(return_value={"head"})
    monkeypatch.setattr(mc, "engine", _engine_with_connects(_async_cm(conn)))

    upgrade_cmd = MagicMock()
    monkeypatch.setattr(mc, "_upgrade_to_head", upgrade_cmd)

    with patch.object(mc.logger, "debug") as debug_log:
        await mc._auto_upgrade_dev()

    upgrade_cmd.assert_not_called()
    debug_log.assert_any_call("db.migrations_upgrade_noop", heads=["head"])


async def test_auto_upgrade_logs_info_on_actual_upgrade(_isolate_settings, monkeypatch):
    """Successful revision change emits db.migrations_upgraded with before/after."""
    monkeypatch.setattr(mc, "_script_heads", lambda: {"new"})

    before = MagicMock()
    before.run_sync = AsyncMock(return_value={"old"})
    after = MagicMock()
    after.run_sync = AsyncMock(return_value={"new"})
    monkeypatch.setattr(
        mc,
        "engine",
        _engine_with_connects(_async_cm(before), _async_cm(after)),
    )
    monkeypatch.setattr(mc, "_upgrade_to_head", MagicMock())

    with patch.object(mc.logger, "info") as info_log:
        await mc._auto_upgrade_dev()

    info_log.assert_called_once_with(
        "db.migrations_upgraded",
        from_revisions=["old"],
        to_revisions=["new"],
    )


def _live_columns_from_orm(drop: tuple[str, str] | None = None) -> dict:
    """Inspector payload mirroring the ORM exactly, optionally minus one column."""
    from agentcore.db.base import Base

    live = {}
    for table_name, table in Base.metadata.tables.items():
        names = [c.name for c in table.columns]
        if drop is not None and drop[0] == table_name:
            names = [n for n in names if n != drop[1]]
        live[(None, table_name)] = [{"name": n} for n in names]
    return live


def _inspector_returning(live: dict) -> MagicMock:
    inspector = MagicMock()
    inspector.get_multi_columns = MagicMock(return_value=live)
    return inspector


def test_schema_gaps_clean_when_live_matches_orm(monkeypatch):
    """A schema carrying every mapped table/column reports nothing."""
    monkeypatch.setattr(
        mc, "inspect", lambda conn: _inspector_returning(_live_columns_from_orm())
    )

    assert mc._schema_gaps(MagicMock()) == ([], [])


def test_schema_gaps_reports_column_the_orm_maps_but_db_lacks(monkeypatch):
    """The shape a revision compare cannot see: model column with no migration."""
    from agentcore.db.base import Base

    table_name, table = next(iter(Base.metadata.tables.items()))
    column_name = next(iter(table.columns)).name
    monkeypatch.setattr(
        mc,
        "inspect",
        lambda conn: _inspector_returning(
            _live_columns_from_orm(drop=(table_name, column_name))
        ),
    )

    missing_tables, missing_columns = mc._schema_gaps(MagicMock())

    assert missing_tables == []
    assert missing_columns == [f"{table_name}.{column_name}"]


async def test_check_migrations_reports_orm_ahead_at_head(_isolate_settings, monkeypatch):
    """At head with a missing column still errors — the 2026-08-13 local outage."""
    monkeypatch.setattr(mc, "_script_heads", lambda: {"head"})

    conn = MagicMock()
    conn.run_sync = AsyncMock(
        side_effect=[{"head"}, ([], ["conversation_external_grants.device_id"])]
    )
    monkeypatch.setattr(mc, "engine", _engine_with_connects(_async_cm(conn)))

    with patch.object(mc.logger, "error") as error_log:
        await mc.check_migrations()

    assert error_log.call_args.args == ("db.schema_orm_ahead",)
    assert error_log.call_args.kwargs["missing_columns"] == [
        "conversation_external_grants.device_id"
    ]


async def test_check_migrations_never_raises_on_db_error(_isolate_settings, monkeypatch):
    """Drift-check connection failures are swallowed (startup must continue)."""
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(mc, "_script_heads", MagicMock(side_effect=OSError("no db")))

    await mc.check_migrations()  # must not raise


async def test_debug_still_behind_refuses_startup(_isolate_settings, monkeypatch):
    """Dev refuses to serve when upgrade returned and the database is still behind."""
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(mc, "_auto_upgrade_dev", AsyncMock())
    monkeypatch.setattr(mc, "_script_heads", lambda: {"new"})

    conn = MagicMock()
    conn.run_sync = AsyncMock(return_value={"old"})
    monkeypatch.setattr(mc, "engine", _engine_with_connects(_async_cm(conn)))

    with pytest.raises(mc.SchemaUpgradeError, match="behind the latest migration"):
        await mc.check_migrations()


async def test_prod_still_behind_does_not_raise(_isolate_settings, monkeypatch):
    """Production logs a pending migration and keeps serving."""
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(mc, "_script_heads", lambda: {"new"})

    conn = MagicMock()
    conn.run_sync = AsyncMock(return_value={"old"})
    monkeypatch.setattr(mc, "engine", _engine_with_connects(_async_cm(conn)))

    with patch.object(mc.logger, "error") as error_log:
        await mc.check_migrations()

    assert error_log.call_args.args == ("db.migrations_pending",)


async def test_debug_orm_ahead_does_not_refuse_startup(_isolate_settings, monkeypatch):
    """At head with a missing column stays a log — there is no migration to run."""
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(mc, "_auto_upgrade_dev", AsyncMock())
    monkeypatch.setattr(mc, "_script_heads", lambda: {"head"})

    conn = MagicMock()
    conn.run_sync = AsyncMock(
        side_effect=[{"head"}, ([], ["conversation_external_grants.device_id"])]
    )
    monkeypatch.setattr(mc, "engine", _engine_with_connects(_async_cm(conn)))

    with patch.object(mc.logger, "error") as error_log:
        await mc.check_migrations()

    assert error_log.call_args.args == ("db.schema_orm_ahead",)
