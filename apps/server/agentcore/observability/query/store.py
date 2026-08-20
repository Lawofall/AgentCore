"""Postgres / offline-export conversation stores for log timeline joins."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


def resolve_database_url() -> str:
    """Resolve DB URL: ``AGENTCORE_DATABASE_URL`` → ``DATABASE_URL`` → settings."""
    for key in ("AGENTCORE_DATABASE_URL", "DATABASE_URL"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    from agentcore.config import settings

    return settings.database_url


@runtime_checkable
class ConversationStore(Protocol):
    """Join target for message bodies + turn_metrics / cost_events (Postgres or export)."""

    async def get_conversation(self, conversation_id: str) -> dict[str, Any] | None: ...

    async def get_messages(self, conversation_id: str) -> list[dict[str, Any]]: ...

    async def list_recent(self, n: int = 5) -> list[dict[str, Any]]: ...

    async def get_turn_metrics_by_trace(self, trace_id: str) -> dict[str, Any] | None: ...

    async def get_turn_metrics_for_conversation(
        self, conversation_id: str
    ) -> list[dict[str, Any]]: ...

    async def get_cost_by_trace(self, trace_id: str) -> dict[str, Any] | None: ...

    async def get_journal_by_trace(self, trace_id: str) -> list[dict[str, Any]]: ...

    async def aclose(self) -> None: ...


_TURN_METRICS_KEYS = (
    "id",
    "turn_id",
    "conversation_id",
    "user_id",
    "agent_id",
    "trace_id",
    "kind",
    "mode",
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
    "created_at",
)


def _project_turn_metrics_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in _TURN_METRICS_KEYS:
        if k not in row:
            continue
        val = row[k]
        if k == "created_at" and val is not None:
            out[k] = str(val)
        else:
            out[k] = val
    return out


def _row_credential_source(row: dict[str, Any]) -> str | None:
    raw = row.get("credential_source")
    if not raw:
        cost = row.get("cost")
        if isinstance(cost, str):
            try:
                cost = json.loads(cost)
            except json.JSONDecodeError:
                cost = None
        if isinstance(cost, dict):
            raw = cost.get("credential_source")
    return str(raw) if raw else None


def _billing_label(sources: set[str], billed: int, estimated: int) -> str | None:
    """Spine-only display: BYOK / platform / mixed. Does not change quota semantics."""
    has_user = "user" in sources
    has_billed_src = bool(sources - {"user"})
    if has_user and has_billed_src:
        return "mixed"
    if has_user:
        return "BYOK"
    if has_billed_src:
        return "platform"
    if estimated and billed:
        return "mixed"
    if estimated:
        return "BYOK"
    if billed:
        return "platform"
    return None


def _aggregate_cost_rows(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    total_nano = 0
    estimated_nano = 0
    models: dict[str, int] = {}
    currency: str | None = None
    billed_currency: str | None = None
    estimated_currency: str | None = None
    sources: set[str] = set()
    for r in rows:
        nano = r.get("cost_total_nano")
        billed = 0
        if nano is None and r.get("cost") is not None:
            # Some legacy rows only carry float ``cost``; leave nano unset then.
            pass
        else:
            billed = int(nano or 0)
            total_nano += billed
        estimated = int(r.get("cost_estimated_nano") or 0)
        estimated_nano += estimated
        model = r.get("model")
        if model:
            models[str(model)] = models.get(str(model), 0) + 1
        row_currency = str(r["currency"]) if r.get("currency") else None
        if currency is None and row_currency:
            currency = row_currency
        if billed and billed_currency is None and row_currency:
            billed_currency = row_currency
        if estimated and estimated_currency is None and row_currency:
            estimated_currency = row_currency
        src = _row_credential_source(r)
        if src:
            sources.add(src)
    return {
        "total_nano": total_nano,
        "estimated_nano": estimated_nano,
        "currency": billed_currency or currency,
        "estimated_currency": estimated_currency,
        "billing": _billing_label(sources, total_nano, estimated_nano),
        "runs": len(rows),
        "models": [{"model": m, "runs": n} for m, n in sorted(models.items())],
    }


_JOURNAL_KEYS = (
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


def _project_journal_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in _JOURNAL_KEYS:
        if key not in row:
            continue
        val = row[key]
        if key == "created_at" and val is not None:
            out[key] = str(val)
        else:
            out[key] = val
    return out


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


class ExportConversationStore:
    """Offline store over ``pnpm sync:logs`` export directory."""

    def __init__(self, export_dir: Path) -> None:
        self.export_dir = export_dir

    async def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        for conv in _read_jsonl(self.export_dir / "conversations.jsonl"):
            if str(conv.get("id")) == conversation_id:
                return {
                    "id": str(conv["id"]),
                    "title": conv.get("title"),
                    "agent_id": conv.get("agent_id"),
                    "created_at": str(conv.get("created_at", "")),
                }
        return None

    async def get_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        journal_counts: dict[str, int] = {}
        for entry in _read_jsonl(self.export_dir / "turn_journal.jsonl"):
            if str(entry.get("conversation_id")) != conversation_id:
                continue
            turn_id = str(entry.get("turn_id", ""))
            journal_counts[turn_id] = journal_counts.get(turn_id, 0) + 1

        messages: list[dict[str, Any]] = []
        for row in _read_jsonl(self.export_dir / "messages.jsonl"):
            if str(row.get("conversation_id")) != conversation_id:
                continue
            content = row.get("content") or ""
            tool_calls = row.get("tool_calls")
            msg_id = str(row.get("id", ""))
            msg: dict[str, Any] = {
                "type": "message",
                "timestamp": str(row.get("created_at", "")),
                "id": msg_id,
                "role": row.get("role"),
                "content_preview": content[:200],
                "content_len": len(content),
                "has_reasoning": bool(row.get("reasoning_content")),
                "tool_calls_count": len(tool_calls) if tool_calls else 0,
                "runs_count": journal_counts.get(msg_id, 0),
                "finish_reason": row.get("finish_reason"),
                "trace_id": row.get("trace_id"),
            }
            if row.get("usage"):
                msg["usage"] = row["usage"]
            messages.append(msg)
        messages.sort(key=lambda x: x.get("timestamp", ""))
        return messages

    async def list_recent(self, n: int = 5) -> list[dict[str, Any]]:
        rows = sorted(
            _read_jsonl(self.export_dir / "conversations.jsonl"),
            key=lambda c: str(c.get("created_at", "")),
            reverse=True,
        )[:n]
        return [
            {
                "id": str(r.get("id", "")),
                "title": r.get("title"),
                "created_at": str(r.get("created_at", "")),
            }
            for r in rows
        ]

    async def get_turn_metrics_by_trace(self, trace_id: str) -> dict[str, Any] | None:
        for row in _read_jsonl(self.export_dir / "turn_metrics.jsonl"):
            if str(row.get("trace_id") or "") == trace_id:
                return _project_turn_metrics_row(row)
        return None

    async def get_turn_metrics_for_conversation(
        self, conversation_id: str
    ) -> list[dict[str, Any]]:
        rows = [
            _project_turn_metrics_row(row)
            for row in _read_jsonl(self.export_dir / "turn_metrics.jsonl")
            if str(row.get("conversation_id") or "") == conversation_id
        ]
        rows.sort(key=lambda r: str(r.get("created_at") or ""))
        return rows

    async def get_cost_by_trace(self, trace_id: str) -> dict[str, Any] | None:
        rows = [
            row
            for row in _read_jsonl(self.export_dir / "cost_events.jsonl")
            if str(row.get("trace_id") or "") == trace_id
        ]
        return _aggregate_cost_rows(rows)

    async def get_journal_by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        rows = [
            _project_journal_row(row)
            for row in _read_jsonl(self.export_dir / "turn_journal.jsonl")
            if str(row.get("trace_id") or "") == trace_id
        ]
        rows.sort(
            key=lambda r: (
                str(r.get("created_at") or ""),
                str(r.get("ts") or ""),
                int(r.get("seq") or 0),
            )
        )
        return rows

    async def aclose(self) -> None:
        return None


class PostgresConversationStore:
    """Live store over ``messages`` / ``conversations`` / ``turn_journal``."""

    def __init__(self, database_url: str | None = None) -> None:
        from sqlalchemy.ext.asyncio import create_async_engine

        url = database_url or resolve_database_url()
        self._engine = create_async_engine(url, pool_size=2, max_overflow=0)

    async def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        from sqlalchemy import text

        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT id, title, agent_id, created_at "
                        "FROM conversations WHERE id = :cid"
                    ),
                    {"cid": conversation_id},
                )
            ).first()
        if not row:
            return None
        return {
            "id": row[0],
            "title": row[1],
            "agent_id": row[2],
            "created_at": str(row[3]),
        }

    async def get_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        from sqlalchemy import text

        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT id, role, content, reasoning_content, usage, created_at, "
                        "trace_id "
                        "FROM messages WHERE conversation_id = :cid ORDER BY created_at"
                    ),
                    {"cid": conversation_id},
                )
            ).all()
            journal_counts: dict[str, int] = {}
            for jr in (
                await conn.execute(
                    text(
                        "SELECT turn_id, count(*) FROM turn_journal "
                        "WHERE conversation_id = :cid GROUP BY turn_id"
                    ),
                    {"cid": conversation_id},
                )
            ).all():
                journal_counts[jr[0]] = jr[1]

        messages: list[dict[str, Any]] = []
        for r in rows:
            msg: dict[str, Any] = {
                "type": "message",
                "timestamp": str(r[5]),
                "id": r[0],
                "role": r[1],
                "content_preview": (r[2] or "")[:200],
                "content_len": len(r[2] or ""),
                "has_reasoning": bool(r[3]),
                "tool_calls_count": 0,
                "runs_count": journal_counts.get(r[0], 0),
                "finish_reason": None,
                # DB↔log join key (assistant rows carry the turn's trace;
                # user / handoff rows are NULL by design).
                "trace_id": r[6],
            }
            if r[4]:
                msg["usage"] = r[4]
            messages.append(msg)
        return messages

    async def list_recent(self, n: int = 5) -> list[dict[str, Any]]:
        from sqlalchemy import text

        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT id, title, created_at FROM conversations "
                        "WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT :n"
                    ),
                    {"n": n},
                )
            ).all()
        return [
            {"id": r[0], "title": r[1], "created_at": str(r[2])} for r in rows
        ]

    async def get_turn_metrics_by_trace(self, trace_id: str) -> dict[str, Any] | None:
        from sqlalchemy import text

        cols = ", ".join(_TURN_METRICS_KEYS)
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        f"SELECT {cols} FROM turn_metrics "
                        "WHERE trace_id = :tid ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"tid": trace_id},
                )
            ).mappings().first()
        if not row:
            return None
        return _project_turn_metrics_row(dict(row))

    async def get_turn_metrics_for_conversation(
        self, conversation_id: str
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text

        cols = ", ".join(_TURN_METRICS_KEYS)
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        f"SELECT {cols} FROM turn_metrics "
                        "WHERE conversation_id = :cid ORDER BY created_at ASC"
                    ),
                    {"cid": conversation_id},
                )
            ).mappings().all()
        return [_project_turn_metrics_row(dict(r)) for r in rows]

    async def get_cost_by_trace(self, trace_id: str) -> dict[str, Any] | None:
        from sqlalchemy import text

        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT model, cost_total_nano, cost_estimated_nano, "
                        "currency, cost "
                        "FROM cost_events WHERE trace_id = :tid"
                    ),
                    {"tid": trace_id},
                )
            ).mappings().all()
        return _aggregate_cost_rows([dict(r) for r in rows])

    async def get_journal_by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        from sqlalchemy import text

        cols = ", ".join(_JOURNAL_KEYS)
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        f"SELECT {cols} FROM turn_journal "
                        "WHERE trace_id = :tid "
                        "ORDER BY created_at ASC, seq ASC"
                    ),
                    {"tid": trace_id},
                )
            ).mappings().all()
        return [_project_journal_row(dict(r)) for r in rows]

    async def aclose(self) -> None:
        await self._engine.dispose()


def open_conversation_store(
    *,
    export_dir: Path | None = None,
    database_url: str | None = None,
) -> ConversationStore:
    """Factory: export dir wins when provided, else Postgres."""
    if export_dir is not None:
        return ExportConversationStore(export_dir)
    return PostgresConversationStore(database_url=database_url)
