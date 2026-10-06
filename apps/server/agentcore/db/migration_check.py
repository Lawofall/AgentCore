"""Startup migration-drift check + optional auto-upgrade in debug.

Compares the database's applied Alembic revision against the migration scripts'
head(s). In **debug** (local/dev), attempts ``alembic upgrade head`` first so a
forgotten migration after parallel-worktree merges cannot leave the schema
behind — the classic footgun where another worktree adds a model column plus its
migration, but the local DB is never upgraded, so a query selecting the new
column 500s with ``UndefinedColumnError``.

The revision compare alone cannot see the *other half* of that footgun: a model
column whose migration was never written at all. Head matches, ``upgrade`` is a
no-op, and the mismatch only surfaces as a 500 on the first query touching the
model — so the schema itself is compared to the ORM as well.

In **production** (``settings.debug`` false) this is a *notice* only: it never
mutates schema, never raises, and never blocks startup.

In **debug**, an upgrade that fails — or that returns with the database still
behind head — raises :class:`SchemaUpgradeError`. The lifespan failure makes
uvicorn exit non-zero and print the traceback, instead of serving requests
that 500 on the missing column. A database already at head whose live schema
still lacks a mapped column (``db.schema_orm_ahead``) stays a log: no migration
exists to apply.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import NoReturn

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, inspect

import agentcore.db as _db_pkg
from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.db.base import engine

logger = get_logger(__name__)

_UPGRADE_HINT = "run `uv run alembic upgrade head` from apps/server"


class SchemaUpgradeError(RuntimeError):
    """Debug startup cannot serve until ``alembic upgrade head`` reaches head."""


def _alembic_config() -> Config:
    """Alembic Config without loading ``alembic.ini``.

    Avoids ``env.py``'s ``fileConfig(config_file_name)`` path, which would
    reconfigure the root logger and pollute the app's structured logging setup.
    ``env.py`` still overrides ``sqlalchemy.url`` from ``settings.database_url``.
    """
    migrations_dir = Path(_db_pkg.__file__).resolve().parent / "migrations"
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations_dir))
    return cfg


def _script_heads() -> set[str]:
    """Head revision(s) declared by the migration scripts (offline, reads files).

    Resolved from the package location rather than the cwd so the result is the
    same whether the server is launched from ``apps/server`` or elsewhere.
    """
    return set(ScriptDirectory.from_config(_alembic_config()).get_heads())


def _db_heads(sync_conn: Connection) -> set[str]:
    """Revision(s) recorded in the database's ``alembic_version`` table.

    Returns an empty set when the table is absent (a schema never put under
    Alembic management).
    """
    return set(MigrationContext.configure(sync_conn).get_current_heads())


def _schema_gaps(sync_conn: Connection) -> tuple[list[str], list[str]]:
    """Tables / ``table.column`` names the ORM maps that the live schema lacks.

    Only this direction is reported: schema the ORM expects but cannot find is
    what turns into ``UndefinedColumnError`` / ``UndefinedTableError`` at query
    time. Extra columns in the database are harmless to a running server (a
    retired model field, a migration landing ahead of its code) and belong to
    the deploy-time ``alembic check``, not to a startup notice.
    """
    # Local import: models pull half the app, and this module is imported from
    # the startup path. Deferring keeps that graph acyclic.
    import agentcore.db.models  # noqa: F401  — registers tables on Base.metadata
    from agentcore.db.base import Base

    inspector = inspect(sync_conn)
    live: dict[str, set[str]] = {
        key[-1]: {col["name"] for col in columns}
        for key, columns in inspector.get_multi_columns().items()
    }

    missing_tables: list[str] = []
    missing_columns: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        live_columns = live.get(table_name)
        if live_columns is None:
            missing_tables.append(table_name)
            continue
        missing_columns.extend(
            f"{table_name}.{column.name}"
            for column in table.columns
            if column.name not in live_columns
        )
    return sorted(missing_tables), sorted(missing_columns)


def _upgrade_to_head() -> None:
    """Synchronous ``alembic upgrade head`` (must run off the asyncio loop).

    ``env.py`` calls ``asyncio.run(...)``, which is illegal inside a running
    event loop — callers must use ``asyncio.to_thread``.
    """
    command.upgrade(_alembic_config(), "head")


def _upgrade_failed(exc: Exception, **fields: object) -> NoReturn:
    """Log the failed dev upgrade, then refuse to serve."""
    logger.warning(
        "db.migration_upgrade_failed",
        error=str(exc),
        exc_info=True,
        **fields,
    )
    raise SchemaUpgradeError(
        f"alembic upgrade head failed: {exc}; {_UPGRADE_HINT}"
    ) from exc


async def _auto_upgrade_dev() -> None:
    """``upgrade head`` when ``settings.debug``.

    Already-at-head is a cheap no-op (revision compare only, no Alembic command).
    A failed upgrade raises :class:`SchemaUpgradeError`. Still-behind after a
    command that returned is left for :func:`check_migrations` to refuse.
    """
    try:
        heads = _script_heads()
        async with engine.connect() as conn:
            before = await conn.run_sync(_db_heads)
    except Exception as exc:
        _upgrade_failed(exc, phase="preflight")

    if before == heads:
        logger.debug("db.migrations_upgrade_noop", heads=sorted(heads))
        return

    try:
        await asyncio.to_thread(_upgrade_to_head)
    except Exception as exc:
        _upgrade_failed(
            exc,
            from_revisions=sorted(before),
            heads=sorted(heads),
        )

    try:
        async with engine.connect() as conn:
            after = await conn.run_sync(_db_heads)
    except Exception as exc:
        _upgrade_failed(exc, phase="verify", from_revisions=sorted(before))

    if before != after:
        logger.info(
            "db.migrations_upgraded",
            from_revisions=sorted(before),
            to_revisions=sorted(after),
        )
    else:
        # Upgrade reported success but revisions unchanged (e.g. race / no-op path).
        logger.debug("db.migrations_upgrade_noop", heads=sorted(after))


async def check_migrations() -> None:
    """Report schema drift. Debug refuses to serve when the database is not at head.

    In debug, attempts an auto-upgrade first; production only reports drift.

    Surfaces four drift shapes:

    - DB behind / diverged from head — unapplied migration(s); the usual cause of
      ``UndefinedColumnError`` at runtime.
    - DB carries no Alembic version row — schema is not migration-managed.
    - Multiple script heads — branched migrations; ``upgrade head`` refuses until
      they are merged.
    - DB *at* head yet missing schema the ORM maps — a model changed without its
      migration, which no revision compare can see.
    """
    if settings.debug:
        await _auto_upgrade_dev()

    try:
        heads = _script_heads()
        async with engine.connect() as conn:
            current = await conn.run_sync(_db_heads)
    except Exception as exc:  # a flaky check must never break startup
        logger.warning("db.migration_check_failed", error=str(exc), exc_info=True)
        return

    if len(heads) > 1:
        logger.warning(
            "db.migrations_branched",
            heads=sorted(heads),
            detail="multiple migration heads — merge them before upgrading",
        )

    if not current:
        # Root-cause, persistent until someone migrates — error (not warning) so it
        # can't hide among routine startup noise. A schema-dependent background sweep
        # WILL fail every interval until this is resolved.
        detail = f"database has no Alembic version row; {_UPGRADE_HINT}"
        logger.error(
            "db.migrations_unmanaged",
            heads=sorted(heads),
            detail=detail,
        )
        if settings.debug:
            raise SchemaUpgradeError(detail)
        return

    if current != heads:
        # Schema is behind code: any table added by a pending migration is missing,
        # so dependent sweeps fail every interval. Error, not warning.
        detail = f"database schema is behind the latest migration; {_UPGRADE_HINT}"
        logger.error(
            "db.migrations_pending",
            db=sorted(current),
            heads=sorted(heads),
            pending=sorted(heads - current),
            detail=detail,
        )
        if settings.debug:
            raise SchemaUpgradeError(detail)
        return

    try:
        async with engine.connect() as conn:
            missing_tables, missing_columns = await conn.run_sync(_schema_gaps)
    except Exception as exc:  # a flaky check must never break startup
        logger.warning(
            "db.migration_check_failed",
            error=str(exc),
            phase="schema",
            exc_info=True,
        )
        return

    if missing_tables or missing_columns:
        # At head yet incomplete: every query touching these 500s until a
        # migration lands, so this is a root cause, not routine startup noise.
        logger.error(
            "db.schema_orm_ahead",
            heads=sorted(heads),
            missing_tables=missing_tables,
            missing_columns=missing_columns,
            detail=(
                "database is at migration head but lacks schema the ORM maps — "
                "a model changed without its migration; write one before querying it"
            ),
        )
        return

    logger.debug("db.migrations_ok", heads=sorted(heads))
