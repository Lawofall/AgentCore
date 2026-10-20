"""Same-round parallel ``read`` coalesce helpers (path+window key + fan-out clone)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from agentcore.tools.builtin.file_ops.read import (
    _effective_line_limit,
    _effective_offset,
)
from agentcore.tools.protocol import ToolResult
from agentcore.workspace.pdf_pages import PdfPagesError, parse_pdf_pages


def _file_read_effective_window(args: dict[str, Any]) -> tuple[int, int] | None:
    """Normalize offset/limit to the window ``read`` actually reads.

    Mirrors ``FileReadTool`` (omit → line 1 / safety line cap, clamp to cap).
    Unparseable values → no coalesce (do not share a sibling's window).
    """
    try:
        return _effective_offset(args.get("offset")), _effective_line_limit(
            args.get("limit")
        )
    except (TypeError, ValueError):
        return None


def _file_read_round_coalesce_key(args: dict[str, Any]) -> str | None:
    """Same-round parallel ``read`` coalesce key: normalized path + effective window.

    Same path and same window share one underlying read (fan-out). Different
    offset/limit or ``pages`` windows do not. Empty path or unparseable
    window → no coalesce.
    """
    if not isinstance(args, dict):
        return None
    path = str(args.get("file_path") or "").strip().replace("\\", "/")
    if not path:
        return None
    window = _file_read_effective_window(args)
    if window is None:
        return None
    try:
        pages = parse_pdf_pages(args.get("pages"))
    except PdfPagesError:
        return None
    offset, limit = window
    pages_token = "" if pages is None else f"{pages[0]}-{pages[1]}"
    return f"{path}\0{offset}:{limit}\0{pages_token}"


def _clone_tool_result(result: ToolResult, tool_call_id: str) -> ToolResult:
    """Fan-out copy of a shared ``read`` result for a sibling tool_call."""
    return replace(
        result,
        tool_call_id=tool_call_id,
        citations=list(result.citations) if result.citations else None,
        metadata=dict(result.metadata) if result.metadata else {},
        display=dict(result.display) if result.display else None,
    )
