"""Journal-only failure pack — local files after a failed turn persist.

Written off the user path when ``persist_turn_journal`` succeeds and the closer
is ``error`` / ``degraded`` / ``unproductive``. Does not call ``query_trace``
and does not wait for jsonl. Never ships LLM bodies or user dialogue
(``journal_redact`` allowlist). Write / GC failures are logged and swallowed.
"""

from __future__ import annotations

import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentcore.config import PROJECT_ROOT
from agentcore.core.logging import get_logger
from agentcore.observability.query.journal_redact import (
    JOURNAL_REDACT_SCHEMA,
    redact_journal_row,
)
from agentcore.runtime.journal.entries import last_turn_end_finish

logger = get_logger(__name__)

FAILURE_PACK_SCHEMA = "journal_failure_pack.v0"
FAILURE_PACK_FINISH_REASONS = frozenset({"error", "degraded", "unproductive"})
PACK_TTL_DAYS = 30
_PACKS_REL = Path("logs") / "packs"


def _default_packs_root() -> Path:
    return PROJECT_ROOT / _PACKS_REL


def _pack_dir_name(trace_id: str) -> str | None:
    name = trace_id.strip()
    if not name or name in {".", ".."}:
        return None
    if Path(name).name != name:
        return None
    return name


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _redacted_rows(
    entries: list[dict[str, Any]],
    *,
    message_id: str,
    conversation_id: str,
    trace_id: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for seq, entry in enumerate(entries):
        payload = entry.get("payload")
        raw = {
            "turn_id": message_id,
            "seq": seq,
            "kind": entry.get("kind"),
            "ts": entry.get("ts"),
            "conversation_id": conversation_id,
            "trace_id": trace_id,
            "payload": payload if isinstance(payload, dict) else {},
        }
        rows.append(redact_journal_row(raw))
    return rows


def locate_failure_pack(
    trace_id: str | None,
    *,
    packs_root: Path | None = None,
) -> Path | None:
    """Return ``logs/packs/<trace>/`` when ``meta.json`` is present; else ``None``."""
    name = _pack_dir_name(str(trace_id or ""))
    if name is None:
        return None
    root = Path(packs_root) if packs_root is not None else _default_packs_root()
    found = root / name
    if (found / "meta.json").is_file():
        return found
    return None


def format_auto_pack_line(
    trace_id: str | None,
    *,
    packs_root: Path | None = None,
) -> str | None:
    """One human line for ``log_timeline --trace`` when an auto pack exists."""
    found = locate_failure_pack(trace_id, packs_root=packs_root)
    if found is None:
        return None
    rel = str(_PACKS_REL / found.name).replace("\\", "/")
    return f"  Auto pack  {rel}/  (journal-only；无原文。完整包仍用 --pack)"


def failure_pack_pointer(
    trace_id: str | None,
    *,
    packs_root: Path | None = None,
) -> dict[str, str] | None:
    """JSON pointer for ``--json`` ``meta.failure_pack``; omit when missing."""
    found = locate_failure_pack(trace_id, packs_root=packs_root)
    if found is None:
        return None
    rel = str(_PACKS_REL / found.name).replace("\\", "/")
    return {"kind": "journal_only", "path": rel}


def _gc_stale_pack_dirs(packs_root: Path, *, now: float | None = None) -> None:
    if not packs_root.is_dir():
        return
    cutoff = (now if now is not None else time.time()) - PACK_TTL_DAYS * 86400
    try:
        children = list(packs_root.iterdir())
    except OSError as e:
        logger.warning(
            "journal.failure_pack_gc_failed",
            path=str(packs_root),
            error=str(e),
        )
        return
    for child in children:
        try:
            if not child.is_dir():
                continue
            if child.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(child)
            logger.info(
                "journal.failure_pack_gc_expired",
                path=str(child).replace("\\", "/"),
                trace_id=child.name,
                ttl_days=PACK_TTL_DAYS,
            )
        except Exception as e:  # noqa: BLE001 — GC must never break the turn
            logger.warning(
                "journal.failure_pack_gc_failed",
                path=str(child),
                error=str(e),
            )


def write_journal_failure_pack(
    entries: list[dict[str, Any]] | None,
    *,
    message_id: str,
    conversation_id: str,
    trace_id: str | None,
    packs_root: Path | None = None,
) -> None:
    """Write ``meta.json`` + ``journal.redacted.jsonl`` under ``logs/packs/<trace>``.

    No-op for cancelled / paused / end_turn (and any other closer). Missing or
    unsafe ``trace_id`` skips with a log. Never raises.
    """
    try:
        _write_journal_failure_pack(
            entries,
            message_id=message_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            packs_root=packs_root,
        )
    except Exception as e:  # noqa: BLE001 — pack write must never break the turn
        logger.warning(
            "journal.failure_pack_failed",
            message_id=message_id,
            trace_id=trace_id,
            error=str(e),
        )


def _write_journal_failure_pack(
    entries: list[dict[str, Any]] | None,
    *,
    message_id: str,
    conversation_id: str,
    trace_id: str | None,
    packs_root: Path | None,
) -> None:
    if not entries:
        return
    finish = last_turn_end_finish(entries)
    if finish not in FAILURE_PACK_FINISH_REASONS:
        return
    if not trace_id or not str(trace_id).strip():
        logger.warning(
            "journal.failure_pack_skipped",
            message_id=message_id,
            conversation_id=conversation_id,
            finish_reason=finish,
            reason="missing_trace",
        )
        return
    trace_name = _pack_dir_name(str(trace_id))
    if trace_name is None:
        logger.warning(
            "journal.failure_pack_skipped",
            message_id=message_id,
            conversation_id=conversation_id,
            finish_reason=finish,
            reason="invalid_trace",
        )
        return

    root = Path(packs_root) if packs_root is not None else _default_packs_root()
    _gc_stale_pack_dirs(root)
    out_dir = root / trace_name
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _redacted_rows(
        entries,
        message_id=message_id,
        conversation_id=conversation_id,
        trace_id=trace_name,
    )
    _write_jsonl(out_dir / "journal.redacted.jsonl", rows)
    meta: dict[str, Any] = {
        "schema_version": FAILURE_PACK_SCHEMA,
        "kind": "journal_only",
        "exported_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "trace_id": trace_name,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "finish_reason": finish,
        "journal": {
            "mode": "redacted",
            "rows": len(rows),
            "schema": JOURNAL_REDACT_SCHEMA,
            "note": ("never ships raw turn_journal; allowlist-redacted kinds/ids/status only"),
        },
        "files": ["journal.redacted.jsonl", "meta.json"],
    }
    _write_json(out_dir / "meta.json", meta)
    logger.info(
        "journal.failure_pack_written",
        trace_id=trace_name,
        conversation_id=conversation_id,
        message_id=message_id,
        finish_reason=finish,
        rows=len(rows),
        path=str(_PACKS_REL / trace_name).replace("\\", "/"),
    )
