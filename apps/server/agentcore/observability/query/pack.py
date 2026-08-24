"""Investigation pack — deliverable artifact for one ``trace_id``.

P1 product surface: a directory another session can open without knowing local
jsonl rotation paths. Reuses ``query_trace`` / ``decision_spine``; does not
reimplement spine logic. Journal, if present, is allowlist-redacted
(``journal.redacted.jsonl``) — never the raw ``turn_journal`` payload.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentcore.observability.query.decision_spine import SCHEMA_VERSION as SPINE_SCHEMA
from agentcore.observability.query.journal_redact import (
    redact_journal_row,
    summarize_redacted_journal,
)
from agentcore.observability.query.store import (
    ConversationStore,
    ExportConversationStore,
    PostgresConversationStore,
)
from agentcore.observability.query.timeline import TimelineQueryResult

PACK_SCHEMA_VERSION = "investigation_pack.v0"

_REQUIRED_FILES = ("decision_spine.json", "timeline.jsonl", "meta.json")

# Never ship LLM prompt/completion bodies in the pack timeline.
_LLM_BODY_EVENTS = frozenset({"llm.request", "llm.response"})
_LLM_KEEP_KEYS = frozenset(
    {
        "type",
        "timestamp",
        "event",
        "level",
        "model",
        "scenario",
        "trace_id",
        "conversation_id",
        "traffic",
        "agent_id",
        "run_id",
        "depth",
    }
)


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


def sanitize_timeline_event(ev: dict[str, Any]) -> dict[str, Any]:
    """Drop LLM body payloads; keep decision/span metadata."""
    if ev.get("event") in _LLM_BODY_EVENTS:
        return {k: v for k, v in ev.items() if k in _LLM_KEEP_KEYS}
    return dict(ev)


def _filter_messages_for_trace(
    messages: list[dict[str, Any]],
    trace_id: str,
) -> list[dict[str, Any]]:
    matched = [m for m in messages if m.get("trace_id") == trace_id]
    return matched if matched else list(messages)


def _preview_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable preview rows (no full content / no reasoning body)."""
    out: list[dict[str, Any]] = []
    for m in messages:
        row = {
            "id": m.get("id"),
            "role": m.get("role"),
            "timestamp": m.get("timestamp"),
            "trace_id": m.get("trace_id"),
            "content_preview": m.get("content_preview"),
            "content_len": m.get("content_len"),
            "has_reasoning": m.get("has_reasoning"),
            "finish_reason": m.get("finish_reason"),
            "tool_calls_count": m.get("tool_calls_count"),
            "runs_count": m.get("runs_count"),
        }
        if m.get("usage") is not None:
            row["usage"] = m["usage"]
        out.append(row)
    return out


async def _load_full_messages(
    store: ConversationStore,
    conversation_id: str,
) -> list[dict[str, Any]]:
    """Maintainer ``--full``: message text only — no reasoning, no tool_calls dump."""
    if isinstance(store, ExportConversationStore):
        rows: list[dict[str, Any]] = []
        for row in _read_jsonl(store.export_dir / "messages.jsonl"):
            if str(row.get("conversation_id")) != conversation_id:
                continue
            content = row.get("content") or ""
            rows.append(
                {
                    "id": str(row.get("id", "")),
                    "role": row.get("role"),
                    "timestamp": str(row.get("created_at", "")),
                    "trace_id": row.get("trace_id"),
                    "content": content,
                    "content_preview": content[:200],
                    "content_len": len(content),
                    "has_reasoning": bool(row.get("reasoning_content")),
                    "finish_reason": row.get("finish_reason"),
                }
            )
        rows.sort(key=lambda x: str(x.get("timestamp") or ""))
        return rows

    if isinstance(store, PostgresConversationStore):
        from sqlalchemy import text

        async with store._engine.connect() as conn:
            db_rows = (
                await conn.execute(
                    text(
                        "SELECT id, role, content, reasoning_content, created_at, "
                        "trace_id "
                        "FROM messages WHERE conversation_id = :cid ORDER BY created_at"
                    ),
                    {"cid": conversation_id},
                )
            ).all()
        out: list[dict[str, Any]] = []
        for r in db_rows:
            content = r[2] or ""
            out.append(
                {
                    "id": r[0],
                    "role": r[1],
                    "content": content,
                    "content_preview": content[:200],
                    "content_len": len(content),
                    "has_reasoning": bool(r[3]),
                    "timestamp": str(r[4]),
                    "trace_id": r[5],
                    "finish_reason": None,
                }
            )
        return out

    # Unknown store: fall back to previews already on the protocol.
    previews = await store.get_messages(conversation_id)
    return _preview_messages(previews)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


async def write_investigation_pack(
    result: TimelineQueryResult,
    *,
    out_dir: Path,
    store: ConversationStore | None = None,
    full: bool = False,
    log_file: Path | None = None,
    export_dir: Path | None = None,
) -> dict[str, Any]:
    """Write pack files under ``out_dir``; return ``meta.json`` payload.

    Required: ``decision_spine.json``, ``timeline.jsonl``, ``meta.json``.
    Optional: ``messages.preview.json``, ``turn_metrics.json``,
    ``journal.redacted.jsonl`` + ``journal.summary.json``,
    and with ``full=True`` ``messages.json`` (no LLM body dump).
    Never writes raw ``turn_journal`` (user/LLM bodies).
    ``meta.layers`` is a pack-presence index (not a second journal contract):
    ``decision`` / ``execution`` are always present; ``turn_metrics`` follows
    whether the file was written; ``sidecar_host`` stays ``not_in_pack`` (desktop
    jsonl is not synced). Journal presence remains ``meta.journal.mode``.
    """
    if result.mode != "trace":
        raise ValueError("investigation pack requires a trace query result")
    if result.decision_spine is None:
        raise ValueError("decision_spine missing on query result")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    spine = result.decision_spine
    trace_id = result.trace_id or spine.get("trace_id") or ""
    conversation_id = (result.meta or {}).get("conversation_id") or spine.get(
        "conversation_id"
    )
    traffic = (result.meta or {}).get("traffic") or (spine.get("health") or {}).get(
        "traffic"
    )
    jsonl_gap = (spine.get("health") or {}).get("jsonl_gap")

    written: list[str] = []

    _write_json(out_dir / "decision_spine.json", spine)
    written.append("decision_spine.json")

    timeline_rows = [sanitize_timeline_event(ev) for ev in result.log_events]
    _write_jsonl(out_dir / "timeline.jsonl", timeline_rows)
    written.append("timeline.jsonl")

    turn_metrics: dict[str, Any] | None = None
    if store is not None and trace_id:
        turn_metrics = await store.get_turn_metrics_by_trace(trace_id)
    if turn_metrics is not None:
        _write_json(out_dir / "turn_metrics.json", turn_metrics)
        written.append("turn_metrics.json")

    if store is not None and conversation_id:
        previews = _filter_messages_for_trace(
            await store.get_messages(str(conversation_id)),
            str(trace_id),
        )
        if previews:
            _write_json(
                out_dir / "messages.preview.json",
                {
                    "conversation_id": conversation_id,
                    "trace_id": trace_id,
                    "messages": _preview_messages(previews),
                },
            )
            written.append("messages.preview.json")

        if full:
            full_msgs = _filter_messages_for_trace(
                await _load_full_messages(store, str(conversation_id)),
                str(trace_id),
            )
            _write_json(
                out_dir / "messages.json",
                {
                    "conversation_id": conversation_id,
                    "trace_id": trace_id,
                    "note": "maintainer --full; message text only — no LLM bodies",
                    "messages": full_msgs,
                },
            )
            written.append("messages.json")

    head = spine.get("head") or {}
    via = head.get("via")
    layers: dict[str, str] = {
        "decision": "present",
        "execution": "present",
        "turn_metrics": "present" if turn_metrics is not None else "absent",
        "sidecar_host": "not_in_pack",
    }

    journal_rows: list[dict[str, Any]] = []
    if store is not None and trace_id:
        getter = getattr(store, "get_journal_by_trace", None)
        if getter is not None:
            raw_journal = await getter(str(trace_id))
            journal_rows = [redact_journal_row(row) for row in raw_journal]
    if journal_rows:
        _write_jsonl(out_dir / "journal.redacted.jsonl", journal_rows)
        written.append("journal.redacted.jsonl")
        _write_json(
            out_dir / "journal.summary.json",
            summarize_redacted_journal(journal_rows),
        )
        written.append("journal.summary.json")

    meta: dict[str, Any] = {
        "schema_version": PACK_SCHEMA_VERSION,
        "decision_spine_schema": SPINE_SCHEMA,
        "exported_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "trace_id": trace_id,
        "conversation_id": conversation_id,
        "traffic": traffic,
        "jsonl_gap": jsonl_gap,
        "full": full,
        "journal": {
            "mode": "redacted" if journal_rows else "absent",
            "rows": len(journal_rows),
            "note": (
                "never ships raw turn_journal; allowlist-redacted kinds/ids/status only"
            ),
        },
        "layers": layers,
        "environment": {
            "log_file": str(log_file) if log_file else None,
            "export_dir": str(export_dir) if export_dir else None,
            "files_read": (result.meta or {}).get("files"),
            "bad_lines": (result.meta or {}).get("bad_lines"),
        },
        "files": written + ["meta.json"],
    }
    if via is not None and str(via).strip():
        meta["hints"] = {"via": str(via)}
    _write_json(out_dir / "meta.json", meta)
    return meta


def required_pack_files() -> tuple[str, ...]:
    """Canonical required file names (for tests / docs)."""
    return _REQUIRED_FILES
