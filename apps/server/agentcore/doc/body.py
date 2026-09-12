"""Block-list body for the creation-tool 文档 (not memory ``documents``).

The live doc and a future share snapshot both store this shape. Unknown block
types are dropped (照白板 scene). The public HTML renderer reads this same shape (``doc.share``).
"""

from __future__ import annotations

import math
from typing import Any

from agentcore.core.types import new_id

BODY_SCHEMA_VERSION = 1
MAX_BLOCKS = 200
MAX_TEXT_CHARS = 20_000
MAX_LIST_ITEMS = 100
HEADING_LEVELS = frozenset({1, 2, 3})
BLOCK_TYPES = frozenset({"heading", "paragraph", "list", "table", "chart"})
MAX_TABLE_COLUMNS = 12
MAX_CHART_ITEMS = 12
MAX_CHART_TITLE_CHARS = 200
MAX_CHART_LABEL_CHARS = 80
MAX_CHART_VALUE = 1e12
MAX_TABLE_ROWS = 40
MAX_TABLE_CELL_CHARS = 500


def empty_body() -> dict[str, Any]:
    return {"schemaVersion": BODY_SCHEMA_VERSION, "blocks": []}


def sanitize_body(raw: Any) -> dict[str, Any]:
    """Coerce a write payload into a stored body. Never raises — bad input is dropped."""
    src: dict[str, Any] = raw if isinstance(raw, dict) else {}
    blocks_in = src.get("blocks")
    if not isinstance(blocks_in, list):
        blocks_in = []
    blocks: list[dict[str, Any]] = []
    for item in blocks_in:
        if len(blocks) >= MAX_BLOCKS:
            break
        cleaned = _sanitize_block(item)
        if cleaned is not None:
            blocks.append(cleaned)
    return {"schemaVersion": BODY_SCHEMA_VERSION, "blocks": blocks}


def _sanitize_block(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    kind = item.get("type")
    if kind not in BLOCK_TYPES:
        return None
    block_id = item.get("id")
    if not isinstance(block_id, str) or not block_id.strip():
        block_id = new_id()
    else:
        block_id = block_id.strip()[:64]
    if kind == "heading":
        level = item.get("level", 1)
        try:
            level_i = int(level)
        except (TypeError, ValueError):
            level_i = 1
        if level_i not in HEADING_LEVELS:
            level_i = 1
        return {
            "id": block_id,
            "type": "heading",
            "level": level_i,
            "text": _clip_text(item.get("text")),
        }
    if kind == "paragraph":
        return {
            "id": block_id,
            "type": "paragraph",
            "text": _clip_text(item.get("text")),
        }
    if kind == "table":
        columns = _sanitize_table_columns(item.get("columns"))
        return {
            "id": block_id,
            "type": "table",
            "columns": columns,
            "rows": _sanitize_table_rows(item.get("rows"), len(columns)),
        }
    if kind == "chart":
        chart_kind = item.get("kind") or "bar"
        if chart_kind != "bar":
            return None
        return {
            "id": block_id,
            "type": "chart",
            "kind": "bar",
            "title": _clip_plain_text(item.get("title"), MAX_CHART_TITLE_CHARS),
            "items": _sanitize_chart_items(item.get("items")),
        }
    if kind != "list":
        return None
    ordered = bool(item.get("ordered"))
    items_raw = item.get("items")
    if not isinstance(items_raw, list):
        items_raw = []
    items: list[str] = []
    for row in items_raw:
        if len(items) >= MAX_LIST_ITEMS:
            break
        items.append(_clip_text(row))
    return {
        "id": block_id,
        "type": "list",
        "ordered": ordered,
        "items": items,
    }


def _clip_text(value: Any) -> str:
    return _clip_plain_text(value, MAX_TEXT_CHARS)


def _clip_cell(value: Any) -> str:
    return _clip_plain_text(value, MAX_TABLE_CELL_CHARS)


def _clip_plain_text(value: Any, max_chars: int) -> str:
    if value is None:
        return ""
    text = str(value)
    if len(text) > max_chars:
        return text[:max_chars]
    return text


def _sanitize_chart_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    if num < 0:
        num = 0.0
    if num > MAX_CHART_VALUE:
        num = MAX_CHART_VALUE
    return num


def _sanitize_chart_items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raw = []
    items: list[dict[str, Any]] = []
    for row in raw:
        if len(items) >= MAX_CHART_ITEMS:
            break
        if not isinstance(row, dict):
            continue
        val = _sanitize_chart_value(row.get("value"))
        if val is None:
            continue
        items.append(
            {
                "label": _clip_plain_text(row.get("label"), MAX_CHART_LABEL_CHARS),
                "value": val,
            }
        )
    return items


def _sanitize_table_columns(raw: Any) -> list[str]:
    if not isinstance(raw, list) or not raw:
        return [""]
    columns: list[str] = []
    for item in raw:
        if len(columns) >= MAX_TABLE_COLUMNS:
            break
        columns.append(_clip_cell(item))
    return columns or [""]


def _sanitize_table_rows(raw: Any, col_count: int) -> list[list[str]]:
    if col_count < 1:
        col_count = 1
    if not isinstance(raw, list):
        raw = []
    rows: list[list[str]] = []
    for item in raw:
        if len(rows) >= MAX_TABLE_ROWS:
            break
        row_in = item if isinstance(item, list) else []
        cells: list[str] = []
        for i in range(col_count):
            if i < len(row_in):
                cells.append(_clip_cell(row_in[i]))
            else:
                cells.append("")
        rows.append(cells)
    return rows
