"""Shared helpers for the repository layer (sentinel + SQL fragment builders).

Kept in one private module so the domain repos can share them without a circular
import. The package ``__init__`` re-exports ``_ilike_pattern`` for callers that
import it directly (e.g. global-search tests).

Transaction boundary (P1-8)
---------------------------
Canonical rule: **the caller owns the unit-of-work**. Repository write methods
default to ``commit=True`` for single-op CRUD (legacy / thin routers), but any
multi-step composite MUST pass ``commit=False`` on each step and call
``session.commit()`` once. Prefer flush (via :func:`commit_or_flush`) over
mid-composite commits so a later failure rolls the whole batch back.

Exception: intentional immediate persistence (e.g. auth lockout counters) may
keep ``commit=True`` and must not be mixed with other writes on the same
session without an explicit comment.
"""

from typing import Any

from sqlalchemy import BigInteger, cast, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

# Sentinel for "field not provided" in partial updates, distinct from an explicit
# None (which clears a nullable column).
_UNSET: object = object()

# Infrastructure conversations that never belong in a user-facing list: ``handoff``
# hosts a local→云 job run (双模式 P2e/e2). ``standing`` is a retired product mode
# kept here so leftover rows stay hidden. Every user-scoped read filters them
# out; so must every user-triggered bulk write, or a project delete would archive
# rows the user can neither see nor un-archive.
HIDDEN_CONVERSATION_MODES: tuple[str, ...] = ("handoff", "standing")


async def commit_or_flush(session: AsyncSession, *, commit: bool) -> None:
    """Commit (default single-op) or flush (composite unit-of-work step).

    ``commit=True`` preserves today's per-method atomicity for standalone CRUD.
    ``commit=False`` flushes so subsequent steps share one transaction; the
    caller must ``await session.commit()`` (or rollback) when the unit completes.
    """
    if commit:
        await session.commit()
    else:
        await session.flush()


def strip_nul(value: Any) -> Any:
    """Remove ``\\x00`` from strings before Postgres writes (text / JSONB).

    Postgres rejects NUL in text **and** in JSON object keys / string values
    (``UntranslatableCharacterError``). Tool stdout / LLM content can carry it;
    ownership composite keys historically used ``\\x00`` as a separator and landed
    in ``coordination_snapshot`` dict keys. One recursive cleaner at the
    repository write boundary covers journal, run_sessions, and messages.
    """
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, dict):
        # Keys must be cleaned too — JSONB object keys are text.
        return {strip_nul(k): strip_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strip_nul(v) for v in value]
    if isinstance(value, tuple):
        return tuple(strip_nul(v) for v in value)
    return value


def _ilike_pattern(query: str) -> str:
    """Wrap a user query as a substring ILIKE pattern, escaping LIKE wildcards.

    The user's raw text is matched literally: ``%`` ``_`` and the escape char
    ``\\`` are neutralized so a query like ``50%`` can't turn into a match-all
    wildcard. Used by the global-search repos (ILIKE over title/content/name —
    前端技术与架构.md §9.8).
    """
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _sum_int(expr: ColumnElement) -> ColumnElement:
    """SUM(expr) coalesced to 0 (so an empty window aggregates to 0, not NULL)."""
    return func.coalesce(func.sum(expr), 0)


def _json_int(column: ColumnElement, key: str) -> ColumnElement:
    """Read a JSONB integer field as a castable BigInteger (nano-CNY / tokens).

    ``->>`` yields text; a missing key is NULL, which SUM ignores — so absent
    token/cost keys simply don't contribute rather than erroring.
    """
    return cast(column[key].astext, BigInteger)
