"""Windowed text I/O: stream lines from disk / bytes, never slurp the file first.

``read`` / attachment inline / grep-then-read all ride this primitive. File size
is not an admission ticket. ``limit`` caps rows kept in memory; the rest of the
file is scanned only to count ``total_lines`` and reject binary (NUL).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from agentcore.workspace.file_kind import decode_text_bytes
from agentcore.workspace.limits import FILE_TOO_LARGE_DETAIL, WORKSPACE_READ_MAX_BYTES
from agentcore.workspace.protocol import ReadLinesResult, WorkspaceIOError

_TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")
_BINARY_DETAIL = "二进制文件，无法以文本读取"


def _strip_line_ending(line: str) -> str:
    if line.endswith("\n"):
        line = line[:-1]
    if line.endswith("\r"):
        line = line[:-1]
    return line


def _raise_if_binary_or_huge_line(line: str) -> None:
    if "\x00" in line:
        raise WorkspaceIOError(_BINARY_DETAIL)
    # Pathology: one line bigger than the whole-file ingest cap (not file size).
    encoded = len(line.encode("utf-8", errors="replace"))
    if encoded > WORKSPACE_READ_MAX_BYTES:
        raise WorkspaceIOError(f"{FILE_TOO_LARGE_DETAIL}（{encoded}字节）")


def _window_from_lines(
    lines: Iterator[str], *, offset: int, limit: int | None
) -> ReadLinesResult:
    start = max(int(offset or 1), 1)
    cap = None if limit is None else max(int(limit), 0)
    selected: list[str] = []
    total = 0
    for raw in lines:
        text = _strip_line_ending(raw)
        _raise_if_binary_or_huge_line(text)
        total += 1
        if total < start:
            continue
        if cap is None or len(selected) < cap:
            selected.append(text)
    if start > total:
        return ReadLinesResult(
            lines=[],
            start_line=start,
            end_line=start - 1,
            total_lines=total,
        )
    end = start + len(selected) - 1 if selected else start - 1
    return ReadLinesResult(
        lines=selected,
        start_line=start,
        end_line=end,
        total_lines=total,
    )


def read_text_lines(
    path: Path, *, offset: int = 1, limit: int | None = None
) -> ReadLinesResult:
    """Stream ``path`` as text. File size does not fail the call."""
    last_decode: UnicodeDecodeError | None = None
    for encoding in _TEXT_ENCODINGS:
        try:
            with path.open("r", encoding=encoding, errors="strict") as handle:
                return _window_from_lines(handle, offset=offset, limit=limit)
        except UnicodeDecodeError as exc:
            last_decode = exc
            continue
    if last_decode is not None:
        raise WorkspaceIOError(str(last_decode)) from last_decode
    raise WorkspaceIOError(_BINARY_DETAIL)


def read_text_lines_from_bytes(
    data: bytes, *, offset: int = 1, limit: int | None = None
) -> ReadLinesResult:
    """Window a already-fetched blob (remote HTTP). Still does not keep unused lines."""
    decoded = decode_text_bytes(data)
    if decoded is None:
        if b"\x00" in data:
            raise WorkspaceIOError(_BINARY_DETAIL)
        raise WorkspaceIOError("无法按文本解码")
    # splitlines matches the previous whole-file path for in-memory blobs.
    return _window_from_lines(iter(decoded.splitlines()), offset=offset, limit=limit)
