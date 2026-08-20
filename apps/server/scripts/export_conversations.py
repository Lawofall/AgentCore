"""Export conversation data from Postgres to JSONL files for offline analysis.

Run from apps/server:

    uv run python scripts/export_conversations.py [--days N] [--output DIR]
    uv run python scripts/export_conversations.py --skip-journal
    uv run python scripts/export_conversations.py --journal-redacted

Default output:
  - If ``DATA_DIR`` is set (prod container): ``$DATA_DIR/export``
  - Else (dev monorepo): ``<repo>/data/export``

Column sets come from the live ORM tables intersected with an allowlist — never
hardcode SELECT lists that drift from migrations (``tool_calls`` / ``finish_reason``
left ``messages`` long ago). Works in monorepo and Docker (``/app/scripts/…``).
``--journal-redacted`` writes structural journal rows only (no user/LLM bodies);
``pnpm sync:logs`` uses that by default. ``--skip-journal`` writes an empty file.
Raw journal is the no-flag default (local maintainer dump / ``sync:logs --full``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

_SCRIPT = Path(__file__).resolve()
_SERVER_ROOT = _SCRIPT.parent.parent  # apps/server or /app
if (_SERVER_ROOT / "agentcore").is_dir():
    sys.path.insert(0, str(_SERVER_ROOT))


def _default_output() -> Path:
    data_dir = (os.environ.get("DATA_DIR") or "").strip()
    if data_dir:
        return Path(data_dir) / "export"
    parents = _SCRIPT.parents
    if len(parents) >= 4:
        return parents[3] / "data" / "export"
    return Path("data") / "export"


_DEFAULT_OUTPUT = _default_output()

# Curated offline-analysis fields. Intersected with ORM columns at runtime so an
# older image / newer allowlist (or the reverse) never SELECT a missing column.
_CONV_KEEP = (
    "id",
    "user_id",
    "title",
    "agent_id",
    "mode",
    "folder_id",
    "pinned",
    "archived",
    "created_at",
)
_MSG_KEEP = (
    "id",
    "conversation_id",
    "role",
    "content",
    "reasoning_content",
    "usage",
    "attachments",
    "citations",
    "evidence_ledger",
    "followups",
    "cost",
    "feedback",
    "trace_id",
    "baseline_snapshot_id",
    "created_at",
)
_COST_KEEP = (
    "id",
    "user_id",
    "conversation_id",
    "message_id",
    "run_id",
    "parent_run_id",
    "agent_id",
    "role",
    "persona",
    "model",
    "tokens",
    "cost",
    "cost_total_nano",
    "cost_estimated_nano",
    "currency",
    "rounds",
    "duration_ms",
    "trace_id",
    "created_at",
)
_TURN_METRICS_KEEP = (
    "id",
    "turn_id",
    "conversation_id",
    "user_id",
    "agent_id",
    "trace_id",
    "kind",
    "status",
    "finish_reason",
    "error",
    "rounds",
    "duration_ms",
    "delegated",
    "workers",
    "input_tokens",
    "output_tokens",
    "boundary_yields",
    "scope_signals",
    "revises",
    "escalations",
    "audit_drops",
    "created_at",
)
_TURN_JOURNAL_KEEP = (
    "turn_id",
    "seq",
    "band",
    "kind",
    "payload",
    "ts",
    "conversation_id",
    "trace_id",
    "created_at",
)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")
    return len(rows)


def _create_engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    from agentcore.config import settings

    return create_async_engine(settings.database_url, pool_size=2, max_overflow=0)


def _cols(table: Any, keep: tuple[str, ...], live: set[str]) -> list[Any]:
    """Allowlist ∩ ORM ∩ live DB — survives schema drift in either direction."""
    available = {c.name: c for c in table.columns}
    selected = [
        available[name] for name in keep if name in available and name in live
    ]
    if not selected:
        raise RuntimeError(
            f"no exportable columns on {table.name} "
            f"(allowlist={list(keep)}; live={sorted(live)})"
        )
    skipped = [name for name in keep if name not in live or name not in available]
    if skipped:
        print(f"  note: skip missing columns on {table.name}: {', '.join(skipped)}")
    return selected


def _mapping_rows(result: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


async def _live_columns(conn: Any, table_name: str) -> set[str]:
    from sqlalchemy import text

    rows = (
        await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table_name},
        )
    ).all()
    return {r[0] for r in rows}


async def export_conversations(
    days: int,
    output_dir: Path,
    *,
    skip_journal: bool = False,
    journal_redacted: bool = False,
) -> None:
    from sqlalchemy import select

    from agentcore.db.models.billing import CostEvent
    from agentcore.db.models.conversations import Conversation, Message
    from agentcore.db.models.runs import TurnJournalRow, TurnMetricsRow

    output_dir.mkdir(parents=True, exist_ok=True)
    engine = _create_engine()
    cutoff = datetime.now(UTC) - timedelta(days=days)

    async with engine.connect() as conn:
        conv_cols = _cols(
            Conversation.__table__,
            _CONV_KEEP,
            await _live_columns(conn, "conversations"),
        )
        msg_cols = _cols(
            Message.__table__,
            _MSG_KEEP,
            await _live_columns(conn, "messages"),
        )
        cost_cols = _cols(
            CostEvent.__table__,
            _COST_KEEP,
            await _live_columns(conn, "cost_events"),
        )
        tm_cols = _cols(
            TurnMetricsRow.__table__,
            _TURN_METRICS_KEEP,
            await _live_columns(conn, "turn_metrics"),
        )
        tj_cols = None
        if not skip_journal:
            tj_cols = _cols(
                TurnJournalRow.__table__,
                _TURN_JOURNAL_KEEP,
                await _live_columns(conn, "turn_journal"),
            )
        conv_stmt = select(*conv_cols).where(Conversation.created_at >= cutoff)
        if "deleted_at" in Conversation.__table__.columns:
            conv_stmt = conv_stmt.where(Conversation.deleted_at.is_(None))
        conversations = _mapping_rows(await conn.execute(conv_stmt))
        conv_ids = [c["id"] for c in conversations]
        conv_count = _write_jsonl(output_dir / "conversations.jsonl", conversations)

        empty_targets: list[tuple[str, object]] = [
            ("messages.jsonl", msg_cols),
            ("cost_events.jsonl", cost_cols),
            ("turn_metrics.jsonl", tm_cols),
        ]
        if tj_cols is not None:
            empty_targets.append(("turn_journal.jsonl", tj_cols))

        if not conv_ids:
            for name, _ in empty_targets:
                _write_jsonl(output_dir / name, [])
            msg_count = cost_count = tm_count = tj_count = 0
            if skip_journal:
                tj_count = _write_jsonl(output_dir / "turn_journal.jsonl", [])
        else:
            msg_stmt = (
                select(*msg_cols)
                .where(Message.conversation_id.in_(conv_ids))
                .order_by(Message.conversation_id, Message.created_at)
            )
            msg_count = _write_jsonl(
                output_dir / "messages.jsonl",
                _mapping_rows(await conn.execute(msg_stmt)),
            )

            cost_stmt = select(*cost_cols).where(CostEvent.conversation_id.in_(conv_ids))
            cost_count = _write_jsonl(
                output_dir / "cost_events.jsonl",
                _mapping_rows(await conn.execute(cost_stmt)),
            )

            tm_stmt = select(*tm_cols).where(TurnMetricsRow.conversation_id.in_(conv_ids))
            tm_count = _write_jsonl(
                output_dir / "turn_metrics.jsonl",
                _mapping_rows(await conn.execute(tm_stmt)),
            )

            if skip_journal:
                tj_count = _write_jsonl(output_dir / "turn_journal.jsonl", [])
            else:
                assert tj_cols is not None
                tj_stmt = (
                    select(*tj_cols)
                    .where(TurnJournalRow.conversation_id.in_(conv_ids))
                    .order_by(TurnJournalRow.turn_id, TurnJournalRow.seq)
                )
                journal_rows = _mapping_rows(await conn.execute(tj_stmt))
                if journal_redacted:
                    from agentcore.observability.query.journal_redact import (
                        redact_journal_row,
                    )

                    journal_rows = [redact_journal_row(row) for row in journal_rows]
                tj_count = _write_jsonl(
                    output_dir / "turn_journal.jsonl",
                    journal_rows,
                )

    await engine.dispose()

    stats = [
        ("conversations.jsonl", conv_count),
        ("messages.jsonl", msg_count),
        ("cost_events.jsonl", cost_count),
        ("turn_metrics.jsonl", tm_count),
        ("turn_journal.jsonl", tj_count),
    ]

    print(f"\nExport complete → {output_dir}\n")
    total_bytes = 0
    for filename, count in stats:
        path = output_dir / filename
        size = path.stat().st_size if path.exists() else 0
        total_bytes += size
        print(f"  {filename:<24} {count:>6} rows  {size:>10,} bytes")
    print(f"\n  {'total':<24} {'':>6}      {total_bytes:>10,} bytes\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, help="Export conversations from last N days")
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=f"Output directory (default: {_DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--skip-journal",
        action="store_true",
        help="Write empty turn_journal.jsonl (skip DB read)",
    )
    parser.add_argument(
        "--journal-redacted",
        action="store_true",
        help="Export journal with user/LLM bodies stripped (default for pnpm sync:logs)",
    )
    args = parser.parse_args()
    if args.skip_journal and args.journal_redacted:
        parser.error("use only one of --skip-journal / --journal-redacted")
    asyncio.run(
        export_conversations(
            args.days,
            args.output.resolve(),
            skip_journal=args.skip_journal,
            journal_redacted=args.journal_redacted,
        )
    )


if __name__ == "__main__":
    main()
