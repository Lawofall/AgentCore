"""File-path ledger for long-conversation compaction (not a per-turn prompt).

History reconstruction replays no tool I/O; the compact summarizer otherwise only
sees user/assistant prose. This module extracts path + last action + optional
digest from journal so ``_render_fold`` can keep identifiers. Product models do
not get a ``<工作集>`` block each turn — CEO uses the workspace file index;
workers use glob / file_read.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from agentcore.core.logging import get_logger
from agentcore.runtime.engine.tool_clear import (
    FILE_READ_DIGEST_PREVIEW,
    FILE_READ_DIGEST_STRUCTURE,
    structural_file_read_summary,
)
from agentcore.runtime.facts import FactKind

logger = get_logger(__name__)

Action = Literal["read", "write"]

READ_TOOLS = frozenset({"file_read"})
WRITE_TOOLS = frozenset({"file_write", "file_append", "str_replace"})
FILE_TOOLS = READ_TOOLS | WRITE_TOOLS

# Newest unique paths kept in the compaction ledger.
MAX_WORKING_SET_PATHS = 16
# Raw journal hits scanned before unique-merge (lean rows, no result body).
MAX_WORKING_SET_HITS = 64
# One-line structural hint persisted on tool_call (not the 1200-char tool_clear digest).
DIGEST_MAX_CHARS = 120
DIGEST_KEY = "working_set_digest"
_WRITE_BODY_KEYS = ("content", "new_str", "new_string", "replacement")
_DIGEST_PREFIXES = (FILE_READ_DIGEST_STRUCTURE, FILE_READ_DIGEST_PREVIEW)


@dataclass(frozen=True, slots=True)
class WorkingSetItem:
    """One path still in play (last action wins)."""

    path: str
    action: Action
    start_line: int | None = None
    end_line: int | None = None
    digest: str = ""


def _normalize_path(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip().replace("\\", "/")


def _int_or_none(raw: object) -> int | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, str) and raw.strip().isdigit():
        value = int(raw.strip())
        return value if value > 0 else None
    return None


def _parse_arguments(arguments: str) -> dict[str, Any]:
    if not arguments or not arguments.strip():
        return {}
    try:
        import json

        data = json.loads(arguments)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _path_and_range(name: str, arguments: str) -> tuple[str, int | None, int | None]:
    data = _parse_arguments(arguments)
    path = _normalize_path(data.get("path") or data.get("file_path"))
    if not path:
        return "", None, None
    if name not in READ_TOOLS:
        return path, None, None
    start = _int_or_none(data.get("offset")) or 1
    limit = _int_or_none(data.get("limit"))
    if start <= 1 and limit is None:
        return path, None, None
    end = start + limit - 1 if limit is not None else None
    return path, start, end


def _action_for(name: str) -> Action | None:
    if name in READ_TOOLS:
        return "read"
    if name in WRITE_TOOLS:
        return "write"
    return None


def _success(raw: object) -> bool:
    return raw is not False and raw != "false" and raw != "False"


def _one_line(text: str) -> str:
    return " ".join((text or "").split())


def _write_body(arguments: str) -> str:
    data = _parse_arguments(arguments)
    for key in _WRITE_BODY_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _strip_digest_prefix(text: str) -> str:
    for prefix in _DIGEST_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text.strip()


def file_working_set_digest(
    *,
    name: str,
    arguments: str,
    result: str = "",
    success: object = True,
) -> str:
    """Deterministic one-line hint for a successful file tool (no LLM).

    Reads use the tool result; writes use the argument body (fallback: result).
    Empty when the call is not a file hit or no structure/preview can be made.
    """
    if not _success(success):
        return ""
    tool = (name or "").strip()
    if tool not in FILE_TOOLS:
        return ""
    path, _, _ = _path_and_range(tool, arguments)
    if not path:
        return ""
    body = (result or "") if tool in READ_TOOLS else (_write_body(arguments) or (result or ""))
    if not body.strip():
        return ""
    raw = structural_file_read_summary(path, body, max_chars=DIGEST_MAX_CHARS)
    if not raw:
        return ""
    return _one_line(_strip_digest_prefix(raw))[:DIGEST_MAX_CHARS]


def item_from_tool_call(
    *,
    name: str,
    arguments: str,
    success: object = True,
    digest: str = "",
) -> WorkingSetItem | None:
    """One successful file tool call → item, or ``None``."""
    if not _success(success):
        return None
    action = _action_for((name or "").strip())
    if action is None:
        return None
    path, start, end = _path_and_range(name, arguments)
    if not path:
        return None
    hint = _one_line(digest)[:DIGEST_MAX_CHARS] if digest else ""
    return WorkingSetItem(
        path=path, action=action, start_line=start, end_line=end, digest=hint
    )


def extract_working_set_items(entries: Sequence[dict[str, Any]] | None) -> list[WorkingSetItem]:
    """Chronological hits from journal / fact-log entries (successful file tools)."""
    if not entries:
        return []
    out: list[WorkingSetItem] = []
    for entry in entries:
        if (entry.get("kind") or "") != FactKind.TOOL_CALL.value:
            continue
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        item = item_from_tool_call(
            name=str(payload.get("name") or ""),
            arguments=str(payload.get("arguments") or ""),
            success=payload.get("success", True),
            digest=str(payload.get(DIGEST_KEY) or ""),
        )
        if item is not None:
            out.append(item)
    return out


def merge_working_set(
    items: Sequence[WorkingSetItem],
    *,
    max_paths: int = MAX_WORKING_SET_PATHS,
) -> list[WorkingSetItem]:
    """Last-action-wins, newest unique paths first, capped."""
    if max_paths <= 0 or not items:
        return []
    seen: set[str] = set()
    out: list[WorkingSetItem] = []
    for item in reversed(items):
        if item.path in seen:
            continue
        seen.add(item.path)
        out.append(item)
        if len(out) >= max_paths:
            break
    return out


def _format_item(item: WorkingSetItem) -> str:
    if item.start_line is None:
        loc = f"- {item.action} {item.path}"
    elif item.end_line is None:
        loc = f"- {item.action} {item.path}:{item.start_line}-"
    else:
        loc = f"- {item.action} {item.path}:{item.start_line}-{item.end_line}"
    if item.digest:
        return f"{loc}  ·  {item.digest}"
    return loc


def render_file_ledger(items: Sequence[WorkingSetItem]) -> str:
    """Compaction-fold ledger (no XML). Empty when nothing to keep."""
    if not items:
        return ""
    return "\n".join(_format_item(i) for i in items)


async def _load_hits_from_db(
    *,
    conversation_id: str,
    exclude_turn_id: str | None,
    turn_ids: Sequence[str] | None,
    limit: int,
) -> list[WorkingSetItem]:
    """Lean tool_call rows (name / arguments / success / digest — no result body)."""
    from sqlalchemy import text

    from agentcore.db.base import async_session_factory

    cid = (conversation_id or "").strip()
    names = sorted(FILE_TOOLS)
    if limit <= 0 or not names:
        return []
    exclude = (exclude_turn_id or "").strip()
    ids = [str(t).strip() for t in (turn_ids or []) if str(t).strip()]
    if turn_ids is not None and not ids:
        return []

    name_ph = ", ".join(f":n{i}" for i in range(len(names)))
    params: dict[str, Any] = {f"n{i}": n for i, n in enumerate(names)}
    params["lim"] = limit

    if ids:
        id_ph = ", ".join(f":t{i}" for i in range(len(ids)))
        params.update({f"t{i}": t for i, t in enumerate(ids)})
        where_scope = f"turn_id IN ({id_ph})"
    elif cid:
        params["cid"] = cid
        where_scope = "conversation_id = :cid"
        if exclude:
            params["ex"] = exclude
            where_scope += " AND turn_id != :ex"
    else:
        return []

    sql = f"""
        SELECT payload->>'name' AS name,
               payload->>'arguments' AS arguments,
               COALESCE(payload->>'success', 'true') AS success,
               payload->>'working_set_digest' AS digest
        FROM turn_journal
        WHERE {where_scope}
          AND kind = 'tool_call'
          AND payload->>'name' IN ({name_ph})
        ORDER BY created_at DESC, band DESC, seq DESC
        LIMIT :lim
    """
    async with async_session_factory() as session:
        result = await session.execute(text(sql), params)
        rows = result.all()
    hits: list[WorkingSetItem] = []
    for row in rows:
        item = item_from_tool_call(
            name=str(row[0] or ""),
            arguments=str(row[1] or ""),
            success=row[2],
            digest=str(row[3] or ""),
        )
        if item is not None:
            hits.append(item)
    hits.reverse()
    return hits


async def load_working_set_items(
    *,
    conversation_id: str = "",
    exclude_turn_id: str | None = None,
    turn_ids: Sequence[str] | None = None,
    live_entries: Sequence[dict[str, Any]] | None = None,
    max_paths: int = MAX_WORKING_SET_PATHS,
    max_hits: int = MAX_WORKING_SET_HITS,
) -> list[WorkingSetItem]:
    """DB lean hits + optional live fact-log, merged newest-unique.

    ``turn_ids`` (compaction fold) scopes to those journals; otherwise the
    conversation's recent file tools. Failures return whatever live entries
    yielded — never raise into a turn.
    """
    hits: list[WorkingSetItem] = []
    try:
        hits.extend(
            await _load_hits_from_db(
                conversation_id=conversation_id,
                exclude_turn_id=exclude_turn_id,
                turn_ids=turn_ids,
                limit=max_hits,
            )
        )
    except Exception:  # noqa: BLE001 — volatile tail must never break the turn
        logger.warning(
            "working_set.load_failed",
            conversation_id=conversation_id or None,
        )
    if live_entries:
        hits.extend(extract_working_set_items(live_entries))
    return merge_working_set(hits, max_paths=max_paths)


async def build_fold_file_ledger(turn_ids: Sequence[str]) -> str:
    """Deterministic file list for a compaction fold window, or ``\"\"``."""
    items = await load_working_set_items(turn_ids=turn_ids)
    return render_file_ledger(items)
