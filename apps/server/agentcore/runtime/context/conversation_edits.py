"""This-conversation file mutations for the CEO ``<工作区>`` file index.

Folder chats sparse-list the project tree (attachments + 「另有 N 个」). This
module names the paths *this conversation* wrote / changed / exported / deleted
so the CEO can see recent deliverables without a full-tree dump or mtime sample.

Reads are out. Compaction's read+write ledger stays in ``working_set`` and is
still not injected as ``<工作集>``. Workers do not receive this list.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.facts import FactKind
from agentcore.tools.file_products import file_products_from_text

logger = get_logger(__name__)

MAX_CONVERSATION_EDIT_PATHS = 8
MAX_CONVERSATION_EDIT_HITS = 64

_WRITE = "file_write"
_APPEND = "file_append"
_REPLACE = "str_replace"
_DELETE = "file_delete"
_MOVE = "file_move"
_COPY = "file_copy"
_BATCH = "file_batch"
_DOCX = "md_to_docx"
_PDF = "md_to_pdf"

MUTATION_TOOLS = frozenset(
    {_WRITE, _APPEND, _REPLACE, _DELETE, _MOVE, _COPY, _BATCH, _DOCX, _PDF}
)

_LABEL_WRITE = "写过"
_LABEL_UPDATE = "更新"
_LABEL_DOCX = "已转 Word"
_LABEL_PDF = "已转 PDF"
_LABEL_DELETE = "已删除"
_LABEL_COPY = "已复制"
_LABEL_MOVE = "已移动"


@dataclass(frozen=True, slots=True)
class ConversationEdit:
    """One mutated workspace-relative path (last action wins at merge)."""

    path: str
    label: str


def _normalize_path(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip().replace("\\", "/").lstrip("./")


def _parse_arguments(arguments: str) -> dict[str, Any]:
    if not arguments or not arguments.strip():
        return {}
    try:
        import json

        data = json.loads(arguments)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _success(raw: object) -> bool:
    return raw is not False and raw != "false" and raw != "False"


def _sibling_export(source: str, new_ext: str) -> str:
    rel = _normalize_path(source)
    if not rel:
        return ""
    if "/" in rel:
        base, name = rel.rsplit("/", 1)
    else:
        base, name = "", rel
    lower = name.lower()
    for old in (".markdown", ".md"):
        if lower.endswith(old):
            out = f"{name[: len(name) - len(old)]}{new_ext}"
            return f"{base}/{out}" if base else out
    if "." in name:
        out = f"{name.rsplit('.', 1)[0]}{new_ext}"
        return f"{base}/{out}" if base else out
    out = f"{name}{new_ext}"
    return f"{base}/{out}" if base else out


def _label_for_kind(kind: str, *, tool: str) -> str:
    if kind == "docx" or tool == _DOCX:
        return _LABEL_DOCX
    if kind == "pdf" or tool == _PDF:
        return _LABEL_PDF
    if tool == _DELETE:
        return _LABEL_DELETE
    if tool == _COPY:
        return _LABEL_COPY
    if tool == _MOVE:
        return _LABEL_MOVE
    if tool in {_APPEND, _REPLACE}:
        return _LABEL_UPDATE
    return _LABEL_WRITE


def _from_products(result: str, *, tool: str) -> list[ConversationEdit]:
    out: list[ConversationEdit] = []
    for product in file_products_from_text(result or ""):
        path = _normalize_path(product.path)
        if not path:
            continue
        out.append(ConversationEdit(path=path, label=_label_for_kind(product.kind, tool=tool)))
    return out


def _from_batch_operations(data: dict[str, Any]) -> list[ConversationEdit]:
    raw = data.get("operations")
    if not isinstance(raw, list):
        return []
    out: list[ConversationEdit] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        op = str(item.get("op") or "").strip()
        if op == "mkdir":
            continue
        if op == "delete":
            path = _normalize_path(item.get("path"))
            if path:
                out.append(ConversationEdit(path=path, label=_LABEL_DELETE))
        elif op in {"move", "copy"}:
            dest = _normalize_path(item.get("destination"))
            if dest:
                out.append(
                    ConversationEdit(
                        path=dest,
                        label=_LABEL_MOVE if op == "move" else _LABEL_COPY,
                    )
                )
    return out


def edits_from_tool_call(
    *,
    name: str,
    arguments: str,
    result: str = "",
    success: object = True,
) -> list[ConversationEdit]:
    """Successful mutation tool → zero or more path labels. Reads yield nothing."""
    if not _success(success):
        return []
    tool = (name or "").strip()
    if tool not in MUTATION_TOOLS:
        return []
    products = _from_products(result, tool=tool)
    if products:
        return products
    data = _parse_arguments(arguments)
    if tool == _DOCX:
        path = _sibling_export(str(data.get("path") or ""), ".docx")
        return [ConversationEdit(path=path, label=_LABEL_DOCX)] if path else []
    if tool == _PDF:
        path = _sibling_export(str(data.get("path") or ""), ".pdf")
        return [ConversationEdit(path=path, label=_LABEL_PDF)] if path else []
    if tool == _BATCH:
        return _from_batch_operations(data)
    if tool in {_MOVE, _COPY}:
        dest = _normalize_path(data.get("destination"))
        if not dest:
            return []
        return [
            ConversationEdit(
                path=dest,
                label=_LABEL_MOVE if tool == _MOVE else _LABEL_COPY,
            )
        ]
    path = _normalize_path(data.get("path") or data.get("file_path"))
    if not path:
        return []
    return [ConversationEdit(path=path, label=_label_for_kind("", tool=tool))]


def extract_conversation_edits(entries: Sequence[dict[str, Any]] | None) -> list[ConversationEdit]:
    """Chronological mutation hits from journal / fact-log entries."""
    if not entries:
        return []
    out: list[ConversationEdit] = []
    for entry in entries:
        if (entry.get("kind") or "") != FactKind.TOOL_CALL.value:
            continue
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        out.extend(
            edits_from_tool_call(
                name=str(payload.get("name") or ""),
                arguments=str(payload.get("arguments") or ""),
                result=str(payload.get("result") or ""),
                success=payload.get("success", True),
            )
        )
    return out


def merge_conversation_edits(
    items: Sequence[ConversationEdit],
    *,
    max_paths: int = MAX_CONVERSATION_EDIT_PATHS,
) -> list[ConversationEdit]:
    """Last-action-wins, newest unique paths first, capped."""
    if max_paths <= 0 or not items:
        return []
    seen: set[str] = set()
    out: list[ConversationEdit] = []
    for item in reversed(items):
        if not item.path or item.path in seen:
            continue
        seen.add(item.path)
        out.append(item)
        if len(out) >= max_paths:
            break
    return out


async def _load_hits_from_db(
    *,
    conversation_id: str,
    exclude_turn_id: str | None,
    limit: int,
) -> list[ConversationEdit]:
    from sqlalchemy import text

    from agentcore.db.base import async_session_factory

    cid = (conversation_id or "").strip()
    names = sorted(MUTATION_TOOLS)
    if limit <= 0 or not cid or not names:
        return []

    name_ph = ", ".join(f":n{i}" for i in range(len(names)))
    params: dict[str, Any] = {f"n{i}": n for i, n in enumerate(names)}
    params["lim"] = limit
    params["cid"] = cid
    where_scope = "conversation_id = :cid"
    exclude = (exclude_turn_id or "").strip()
    if exclude:
        params["ex"] = exclude
        where_scope += " AND turn_id != :ex"

    sql = f"""
        SELECT payload->>'name' AS name,
               payload->>'arguments' AS arguments,
               COALESCE(payload->>'success', 'true') AS success,
               payload->>'result' AS result
        FROM turn_journal
        WHERE {where_scope}
          AND kind = 'tool_call'
          AND payload->>'name' IN ({name_ph})
        ORDER BY created_at DESC, band DESC, seq DESC
        LIMIT :lim
    """
    async with async_session_factory() as session:
        rows = (await session.execute(text(sql), params)).all()
    hits: list[ConversationEdit] = []
    for row in rows:
        hits.extend(
            edits_from_tool_call(
                name=str(row[0] or ""),
                arguments=str(row[1] or ""),
                success=row[2],
                result=str(row[3] or ""),
            )
        )
    hits.reverse()
    return hits


async def load_conversation_edits(
    *,
    conversation_id: str = "",
    exclude_turn_id: str | None = None,
    live_entries: Sequence[dict[str, Any]] | None = None,
    max_paths: int = MAX_CONVERSATION_EDIT_PATHS,
    max_hits: int = MAX_CONVERSATION_EDIT_HITS,
) -> list[ConversationEdit]:
    """DB mutation hits + optional live fact-log, merged newest-unique.

    Failures return whatever live entries yielded — never raise into a turn.
    """
    hits: list[ConversationEdit] = []
    try:
        hits.extend(
            await _load_hits_from_db(
                conversation_id=conversation_id,
                exclude_turn_id=exclude_turn_id,
                limit=max_hits,
            )
        )
    except Exception:  # noqa: BLE001 — volatile tail must never break the turn
        logger.warning(
            "conversation_edits.load_failed",
            conversation_id=conversation_id or None,
        )
    if live_entries:
        hits.extend(extract_conversation_edits(live_entries))
    return merge_conversation_edits(hits, max_paths=max_paths)
