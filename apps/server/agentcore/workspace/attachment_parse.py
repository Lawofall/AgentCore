"""Pre-parse text-like binary attachments at residency time (附件分流预解析).

分流：``.docx`` 用 python-docx（段落+表格）抽文本；pdf/pptx/odt/rtf 用 markitdown。
写出与原件并存的 ``原名.ext.md``。``.txt`` / ``.md`` / HTML 等可直接 UTF-8 读的
格式只内联正文（原件已是可读副本）；xlsx/csv/tsv 不把全表抽进 prompt，只产
**结构面**（列名 / 行数 / 推断类型 / 样例行），原始数据留在工作区文件。扫描版
PDF 首版不做 OCR，写入明确降级提示。解析失败不阻塞驻留，回落路径提示。

工作区 ``file_read`` 与附件预解析共用公开核 ``extract_office_bytes``（可杀子进程，
墙钟超时 = FAILED / extract_timeout，不是通道活性挂起；读时默认不写 ``*.md``）。
``convert_with_markitdown`` 仅服务非 docx 桶；``preparse_resident`` 仍只服务附件驻留。

→ 见决策：docs/02-架构/双模式工作区.md §七（Office 云=本地）与附件驻留实现。
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

from agentcore.core.logging import get_logger
from agentcore.workspace.protocol import WorkspaceBackend, WorkspaceError

logger = get_logger(__name__)

# Office / PDF 透明抽取路由桶（含 docx）。docx 主路径是 python-docx，不是 markitdown。
MARKITDOWN_EXTENSIONS = frozenset({".docx", ".pdf", ".pptx", ".odt", ".rtf"})
_PYTHON_DOCX_EXTENSIONS = frozenset({".docx"})
_IS_WINDOWS = sys.platform == "win32"
_EXTRACT_OUTPUT_ENV = "AGENTCORE_OFFICE_EXTRACT_OUTPUT"
# 已是文本层：直接 UTF-8 解码；原件本身即工作区可读副本。
PLAIN_TEXT_EXTENSIONS = frozenset({".txt", ".md", ".markdown", ".html", ".htm"})
# 大表 / 计算场景：不预解析全表进 prompt；只产结构面。``file_read`` 亦不透明抽。
SKIP_EXTENSIONS = frozenset({".xlsx", ".xlsm", ".xls", ".csv", ".tsv"})
TABLE_EXTENSIONS = SKIP_EXTENSIONS

# Back-compat aliases (private names used by older call sites / tests).
_MARKITDOWN_EXTENSIONS = MARKITDOWN_EXTENSIONS
_PLAIN_TEXT_EXTENSIONS = PLAIN_TEXT_EXTENSIONS
_SKIP_EXTENSIONS = SKIP_EXTENSIONS

# 扫描件启发式：可打印字母数字过少 → 视为无文本层（不做 OCR）。
_SCAN_MIN_ALNUM = 40
# 大文件仍几乎无字：≥50KB 且 alnum < 200 → 扫描件。
_SCAN_LARGE_BYTES = 50_000
_SCAN_LARGE_MIN_ALNUM = 200

# 首轮 prompt 内联上限（字符）。约 6k tokens，给历史/工具/记忆留预算；
# 全文落在 ``*.md`` 副本，Agent 可用 file_read 续读。多附件时各自独立截断。
ATTACHMENT_INLINE_MAX_CHARS = 24_000

# 表格结构面：只读到此字节数；样例/列/单元格各自封顶。全量行不进 prompt。
TABLE_PREVIEW_MAX_BYTES = 2 * 1024 * 1024
TABLE_PREVIEW_MAX_SAMPLE_ROWS = 5
TABLE_PREVIEW_MAX_COLUMNS = 24
TABLE_PREVIEW_MAX_CELL_CHARS = 48
TABLE_PREVIEW_MAX_SHEETS = 3
TABLE_PREVIEW_MAX_PROMPT_CHARS = 4_000

SCAN_NOTICE = (
    "This file appears to be a scanned / image-only document with little or no "
    "extractable text layer. OCR is not available in this build. Tell the user "
    "the file looks like a scan and ask them to provide a text-layer PDF or paste "
    "the relevant passages."
)
_SCAN_NOTICE = SCAN_NOTICE


class ParseStatus(StrEnum):
    OK = "ok"
    SCANNED = "scanned"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExtractResult:
    """In-memory office/PDF extract (no workspace write)."""

    status: ParseStatus
    text: str = ""
    """Full extracted text, or scan/failure note. Empty when skipped."""
    detail: str = ""
    """Short machine-oriented reason for logs / tests."""


@dataclass(frozen=True, slots=True)
class PreparseResult:
    status: ParseStatus
    text: str = ""
    """Full extracted text (or scan/failure note). Empty when skipped."""
    parsed_workspace_path: str | None = None
    """Workspace-relative readable copy (``*.md``) or the original when already text."""
    detail: str = ""
    """Short machine-oriented reason for logs / tests."""


@dataclass(frozen=True, slots=True)
class TableColumn:
    name: str
    inferred_type: str


@dataclass(frozen=True, slots=True)
class TableSheetPreview:
    name: str
    row_count: int
    """Data rows excluding the header."""
    columns: tuple[TableColumn, ...]
    sample_rows: tuple[tuple[str, ...], ...]
    truncated: bool = False
    """True when column / sample / scan caps hid part of this sheet."""


@dataclass(frozen=True, slots=True)
class TablePreview:
    sheets: tuple[TableSheetPreview, ...]
    detail: str = "ok"
    bytes_truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "sheets": [
                {
                    "name": sheet.name,
                    "row_count": sheet.row_count,
                    "columns": [
                        {"name": col.name, "inferred_type": col.inferred_type}
                        for col in sheet.columns
                    ],
                    "sample_rows": [list(row) for row in sheet.sample_rows],
                    "truncated": sheet.truncated,
                }
                for sheet in self.sheets
            ],
            "detail": self.detail,
            "bytes_truncated": self.bytes_truncated,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> TablePreview | None:
        sheets_raw = raw.get("sheets")
        if not isinstance(sheets_raw, list) or not sheets_raw:
            return None
        sheets: list[TableSheetPreview] = []
        for item in sheets_raw:
            if not isinstance(item, dict):
                continue
            cols_raw = item.get("columns") or []
            columns = tuple(
                TableColumn(
                    name=str(col.get("name") or ""),
                    inferred_type=str(col.get("inferred_type") or "string"),
                )
                for col in cols_raw
                if isinstance(col, dict)
            )
            samples_raw = item.get("sample_rows") or []
            samples = tuple(
                tuple(str(cell) for cell in row)
                for row in samples_raw
                if isinstance(row, list)
            )
            sheets.append(
                TableSheetPreview(
                    name=str(item.get("name") or "Sheet1"),
                    row_count=int(item.get("row_count") or 0),
                    columns=columns,
                    sample_rows=samples,
                    truncated=bool(item.get("truncated")),
                )
            )
        if not sheets:
            return None
        return cls(
            sheets=tuple(sheets),
            detail=str(raw.get("detail") or "ok"),
            bytes_truncated=bool(raw.get("bytes_truncated")),
        )


@dataclass(frozen=True, slots=True)
class TablePreviewResult:
    status: ParseStatus
    preview: TablePreview | None = None
    detail: str = ""


def extension_of(name: str | None, workspace_path: str | None = None) -> str:
    """Lowercase extension from display name, falling back to workspace path."""
    for candidate in (name, workspace_path):
        if not candidate:
            continue
        base = os.path.basename(candidate.replace("\\", "/"))
        _, ext = os.path.splitext(base)
        if ext:
            return ext.lower()
    return ""


def should_preparse(name: str | None, workspace_path: str | None = None) -> bool:
    """True when this binary resident is in the text-document bucket (not xlsx/csv)."""
    ext = extension_of(name, workspace_path)
    if not ext or ext in SKIP_EXTENSIONS:
        return False
    return ext in MARKITDOWN_EXTENSIONS or ext in PLAIN_TEXT_EXTENSIONS


def should_preview_table(name: str | None, workspace_path: str | None = None) -> bool:
    """True when this resident is a spreadsheet / delimited table (structure only)."""
    ext = extension_of(name, workspace_path)
    return ext in TABLE_EXTENSIONS


def looks_like_scanned(text: str, raw_size: int) -> bool:
    """Heuristic: almost no alphanumeric content ⇒ image-only / scan PDF."""
    alnum = sum(1 for c in text if c.isalnum())
    if alnum < _SCAN_MIN_ALNUM:
        return True
    return raw_size >= _SCAN_LARGE_BYTES and alnum < _SCAN_LARGE_MIN_ALNUM


def parsed_copy_path(workspace_path: str) -> str:
    """``attachments/report.docx`` → ``attachments/report.docx.md``."""
    return f"{workspace_path}.md"


def truncate_for_prompt(text: str, limit: int = ATTACHMENT_INLINE_MAX_CHARS) -> tuple[str, bool]:
    """Return ``(maybe_truncated_text, was_truncated)`` for first-turn inline context."""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def convert_with_markitdown(data: bytes, ext: str) -> str:
    """Sync markitdown convert for non-docx office/PDF. Not used for ``.docx``."""
    return _convert_with_markitdown(data, ext)


def _convert_with_markitdown(data: bytes, ext: str) -> str:
    from markitdown import MarkItDown

    md = MarkItDown(enable_plugins=False)
    result = md.convert_stream(BytesIO(data), file_extension=ext)
    return (result.text_content or "").strip()


def _decode_plain_text(data: bytes) -> str | None:
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            return data.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return None


def _normalize_extract_ext(ext: str) -> str:
    if not ext:
        return ""
    return ext.lower() if ext.startswith(".") else f".{ext.lower()}"


def _extract_docx_with_python_docx(data: bytes) -> str:
    """Paragraphs + tables in document order. No markitdown / HTML preprocess."""
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(BytesIO(data))
    parts: list[str] = []
    body = document.element.body
    for child in body:
        if child.tag == qn("w:p"):
            text = (Paragraph(child, document).text or "").strip()
            if text:
                parts.append(text)
        elif child.tag == qn("w:tbl"):
            table = Table(child, document)
            for row in table.rows:
                cells = [(cell.text or "").replace("\n", " ").strip() for cell in row.cells]
                if any(cells):
                    parts.append("\t".join(cells))
    return "\n".join(parts).strip()


def extract_office_payload(data: bytes, ext: str) -> ExtractResult:
    """Sync extract used by the worker. Never spawns (avoids recursion)."""
    normalized = _normalize_extract_ext(ext)
    if normalized in SKIP_EXTENSIONS:
        return ExtractResult(status=ParseStatus.SKIPPED, detail=f"skip_ext:{normalized}")
    if normalized not in MARKITDOWN_EXTENSIONS:
        return ExtractResult(status=ParseStatus.SKIPPED, detail=f"unknown_ext:{normalized or '?'}")

    from agentcore.workspace.limits import OFFICE_EXTRACT_MAX_BYTES

    if len(data) > OFFICE_EXTRACT_MAX_BYTES:
        return ExtractResult(
            status=ParseStatus.FAILED,
            detail=f"extract_budget:{len(data)}>{OFFICE_EXTRACT_MAX_BYTES}",
        )

    try:
        if normalized in _PYTHON_DOCX_EXTENSIONS:
            text = _extract_docx_with_python_docx(data)
            engine = "python-docx"
        else:
            text = _convert_with_markitdown(data, normalized)
            engine = "markitdown"
    except Exception as e:
        logger.warning(
            "attachment.extract_failed",
            ext=normalized,
            error=str(e),
            error_type=type(e).__name__,
        )
        return ExtractResult(status=ParseStatus.FAILED, detail=f"convert:{type(e).__name__}")

    if looks_like_scanned(text, len(data)):
        return ExtractResult(
            status=ParseStatus.SCANNED,
            text=SCAN_NOTICE,
            detail="scanned_or_empty_text_layer",
        )

    return ExtractResult(status=ParseStatus.OK, text=text, detail=f"ok:{engine}")


def _spawn_group_kwargs() -> dict:
    """Match SubprocessSandbox: POSIX new session so the tree is killable."""
    return {} if _IS_WINDOWS else {"start_new_session": True}


def _reap_tree_sync(process: subprocess.Popen[bytes], pid: int) -> None:
    """Kill the child and every descendant, then reap. Best-effort, never raises."""
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)
    with contextlib.suppress(Exception):
        process.wait(timeout=5)


def _extract_worker_argv(ext: str) -> list[str]:
    """``sys.executable`` so the sidecar bundled interpreter is used."""
    return [
        sys.executable,
        "-m",
        "agentcore.workspace.attachment_parse",
        "--extract-worker",
        ext,
    ]


def _run_extract_subprocess(
    data: bytes,
    ext: str,
    timeout: float,
    *,
    holder: dict | None = None,
) -> ExtractResult:
    """Blocking Popen + communicate timeout. Safe on Windows SelectorEventLoop."""
    fd, out_path = tempfile.mkstemp(suffix=".extract.json")
    os.close(fd)
    proc: subprocess.Popen[bytes] | None = None
    try:
        env = os.environ.copy()
        env[_EXTRACT_OUTPUT_ENV] = out_path
        proc = subprocess.Popen(
            _extract_worker_argv(ext),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
            **_spawn_group_kwargs(),
        )
        if holder is not None:
            holder["proc"] = proc
        try:
            _, stderr = proc.communicate(input=data, timeout=timeout)
        except subprocess.TimeoutExpired:
            _reap_tree_sync(proc, proc.pid)
            return ExtractResult(status=ParseStatus.FAILED, detail="extract_timeout")
        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="replace").strip()[:120]
            return ExtractResult(
                status=ParseStatus.FAILED,
                detail=f"extract_worker:{proc.returncode}:{err}",
            )
        raw = Path(out_path).read_text(encoding="utf-8")
        payload = json.loads(raw)
        status = ParseStatus(str(payload.get("status") or "failed"))
        return ExtractResult(
            status=status,
            text=str(payload.get("text") or ""),
            detail=str(payload.get("detail") or ""),
        )
    except Exception as e:
        if proc is not None and proc.poll() is None:
            _reap_tree_sync(proc, proc.pid)
        logger.warning(
            "attachment.extract_spawn_failed",
            ext=ext,
            error=str(e),
            error_type=type(e).__name__,
        )
        return ExtractResult(status=ParseStatus.FAILED, detail=f"extract_spawn:{type(e).__name__}")
    finally:
        with contextlib.suppress(OSError):
            os.unlink(out_path)


def _extract_worker_main(argv: list[str]) -> int:
    """Worker entry: stdin bytes → JSON file in ``_EXTRACT_OUTPUT_ENV``. Sync only."""
    ext = ".pdf"
    args = list(argv)
    while args:
        token = args.pop(0)
        if token == "--extract-worker" and args:
            ext = args.pop(0)
    out_path = os.environ.get(_EXTRACT_OUTPUT_ENV, "")
    if not out_path:
        return 2
    data = sys.stdin.buffer.read()
    # Keep JSON file the only stdout protocol; libraries may print.
    sys.stdout = sys.stderr
    result = extract_office_payload(data, ext)
    Path(out_path).write_text(
        json.dumps(
            {"status": result.status.value, "text": result.text, "detail": result.detail},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return 0


async def extract_office_bytes(
    data: bytes,
    *,
    ext: str,
    timeout: float | None = None,
) -> ExtractResult:
    """Extract text from office/PDF bytes without writing a workspace ``*.md`` copy.

    Skip / byte-budget checks run in-process. Conversion runs in a killable
    child (``sys.executable -m``). Wall-clock timeout is ``extract_timeout``
    (contract), not channel liveness. Worker must call ``extract_office_payload``,
    never this coroutine.
    """
    from agentcore.workspace.limits import (
        OFFICE_EXTRACT_MAX_BYTES,
        OFFICE_EXTRACT_TIMEOUT_SECONDS,
    )

    normalized = _normalize_extract_ext(ext)
    if normalized in SKIP_EXTENSIONS:
        return ExtractResult(status=ParseStatus.SKIPPED, detail=f"skip_ext:{normalized}")
    if normalized not in MARKITDOWN_EXTENSIONS:
        return ExtractResult(status=ParseStatus.SKIPPED, detail=f"unknown_ext:{normalized or '?'}")
    if len(data) > OFFICE_EXTRACT_MAX_BYTES:
        return ExtractResult(
            status=ParseStatus.FAILED,
            detail=f"extract_budget:{len(data)}>{OFFICE_EXTRACT_MAX_BYTES}",
        )

    budget = OFFICE_EXTRACT_TIMEOUT_SECONDS if timeout is None else timeout
    holder: dict = {}
    loop = asyncio.get_running_loop()

    def blocking() -> ExtractResult:
        return _run_extract_subprocess(data, normalized, budget, holder=holder)

    fut = loop.run_in_executor(None, blocking)
    try:
        return await fut
    except asyncio.CancelledError:
        proc = holder.get("proc")
        if proc is not None:
            await asyncio.to_thread(_reap_tree_sync, proc, proc.pid)
        with contextlib.suppress(Exception):
            await fut
        raise


def _cli_main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--extract-worker":
        return _extract_worker_main(args)
    sys.stderr.write("internal office extract worker\n")
    return 2


async def preparse_resident(
    backend: WorkspaceBackend,
    *,
    workspace_path: str,
    name: str | None,
) -> PreparseResult:
    """Read a resident binary attachment and attempt text extraction.

    Never raises for parse failures — returns ``FAILED`` / ``SKIPPED`` so the
    caller can keep the existing path-hint behaviour.
    """
    ext = extension_of(name, workspace_path)
    if ext in SKIP_EXTENSIONS:
        return PreparseResult(status=ParseStatus.SKIPPED, detail=f"skip_ext:{ext}")
    if ext not in MARKITDOWN_EXTENSIONS and ext not in PLAIN_TEXT_EXTENSIONS:
        return PreparseResult(status=ParseStatus.SKIPPED, detail=f"unknown_ext:{ext or '?'}")

    try:
        data = await backend.read_bytes(workspace_path)
    except WorkspaceError as e:
        logger.warning(
            "attachment.preparse_read_failed",
            path=workspace_path,
            error=str(e),
        )
        return PreparseResult(status=ParseStatus.FAILED, detail=f"read:{e}")

    if ext in PLAIN_TEXT_EXTENSIONS:
        try:
            text = _decode_plain_text(data)
            if text is None:
                # Odd encoding — last resort via markitdown.
                text = await asyncio.to_thread(_convert_with_markitdown, data, ext)
        except Exception as e:
            logger.warning(
                "attachment.preparse_failed",
                path=workspace_path,
                name=name,
                error=str(e),
                error_type=type(e).__name__,
            )
            return PreparseResult(status=ParseStatus.FAILED, detail=f"convert:{type(e).__name__}")
        if not text:
            return PreparseResult(status=ParseStatus.FAILED, detail="empty_plain_text")
        return PreparseResult(
            status=ParseStatus.OK,
            text=text,
            parsed_workspace_path=workspace_path,
            detail="plain_text",
        )

    extracted = await extract_office_bytes(data, ext=ext)

    if extracted.status == ParseStatus.FAILED:
        return PreparseResult(status=ParseStatus.FAILED, detail=extracted.detail)

    if extracted.status == ParseStatus.SCANNED:
        notice = extracted.text or SCAN_NOTICE
        copy_path = parsed_copy_path(workspace_path)
        try:
            await backend.write(copy_path, notice + "\n")
        except WorkspaceError as e:
            logger.warning(
                "attachment.preparse_scan_note_write_failed",
                path=copy_path,
                error=str(e),
            )
            copy_path_out: str | None = None
        else:
            copy_path_out = copy_path
        logger.info(
            "attachment.preparse_scanned",
            path=workspace_path,
            name=name,
            raw_bytes=len(data),
            extracted_chars=len(extracted.text),
        )
        return PreparseResult(
            status=ParseStatus.SCANNED,
            text=notice,
            parsed_workspace_path=copy_path_out,
            detail=extracted.detail or "scanned_or_empty_text_layer",
        )

    text = extracted.text
    copy_path = parsed_copy_path(workspace_path)
    try:
        await backend.write(copy_path, text if text.endswith("\n") else text + "\n")
    except WorkspaceError as e:
        logger.warning(
            "attachment.preparse_copy_write_failed",
            path=copy_path,
            error=str(e),
        )
        # Still expose text inline even if the durable copy failed.
        return PreparseResult(
            status=ParseStatus.OK,
            text=text,
            parsed_workspace_path=None,
            detail="ok_inline_only",
        )

    logger.info(
        "attachment.preparse_ok",
        path=workspace_path,
        parsed_path=copy_path,
        name=name,
        chars=len(text),
    )
    return PreparseResult(
        status=ParseStatus.OK,
        text=text,
        parsed_workspace_path=copy_path,
        detail="ok",
    )


_DATE_RE = re.compile(
    r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?$"
)
_BOOL_VALUES = frozenset({"true", "false", "yes", "no", "是", "否"})
_XLS_MAGIC = b"\xd0\xcf\x11\xe0"


def _xml_local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _clip_cell(value: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = " ".join(text.split())
    if len(text) <= TABLE_PREVIEW_MAX_CELL_CHARS:
        return text
    return text[: TABLE_PREVIEW_MAX_CELL_CHARS - 1] + "…"


def _infer_cell_type(value: str) -> str:
    raw = value.strip()
    if not raw:
        return "empty"
    lowered = raw.lower()
    if lowered in _BOOL_VALUES:
        return "bool"
    if _DATE_RE.match(raw):
        return "date"
    if re.fullmatch(r"-?\d+", raw):
        return "int"
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return "float"
    return "string"


def _infer_column_types(
    header: list[str], samples: list[list[str]]
) -> tuple[TableColumn, ...]:
    width = len(header)
    types: list[str] = []
    for idx in range(width):
        votes: dict[str, int] = {}
        for row in samples:
            if idx >= len(row):
                continue
            kind = _infer_cell_type(row[idx])
            if kind == "empty":
                continue
            votes[kind] = votes.get(kind, 0) + 1
        if not votes:
            types.append("empty")
            continue
        top = max(votes.values())
        winners = [name for name, count in votes.items() if count == top]
        types.append(winners[0] if len(winners) == 1 else "mixed")
    return tuple(
        TableColumn(name=header[i] or f"col_{i + 1}", inferred_type=types[i])
        for i in range(width)
    )


def _trim_sheet(
    name: str,
    header: list[str],
    data_rows: list[list[str]],
    *,
    row_count: int,
    extra_truncated: bool = False,
) -> TableSheetPreview:
    col_trunc = len(header) > TABLE_PREVIEW_MAX_COLUMNS
    header = header[:TABLE_PREVIEW_MAX_COLUMNS]
    clipped_rows = [
        [_clip_cell(cell) for cell in row[:TABLE_PREVIEW_MAX_COLUMNS]]
        for row in data_rows[:TABLE_PREVIEW_MAX_SAMPLE_ROWS]
    ]
    sample_trunc = row_count > len(clipped_rows)
    columns = _infer_column_types(header, clipped_rows)
    return TableSheetPreview(
        name=name or "Sheet1",
        row_count=row_count,
        columns=columns,
        sample_rows=tuple(tuple(row) for row in clipped_rows),
        truncated=col_trunc or sample_trunc or extra_truncated,
    )


def _decode_delimited_bytes(data: bytes) -> str | None:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _preview_delimited(
    data: bytes, *, ext: str, bytes_truncated: bool
) -> TablePreviewResult:
    text = _decode_delimited_bytes(data)
    if text is None:
        return TablePreviewResult(status=ParseStatus.FAILED, detail="decode")
    if not text.strip():
        return TablePreviewResult(status=ParseStatus.FAILED, detail="empty_table")

    delimiter = "\t" if ext == ".tsv" else ","
    if ext == ".csv":
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ","

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [list(row) for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return TablePreviewResult(status=ParseStatus.FAILED, detail="empty_table")

    header = [cell.strip() or f"col_{i + 1}" for i, cell in enumerate(rows[0])]
    data_rows = rows[1:]
    sheet = _trim_sheet(
        "Sheet1",
        header,
        data_rows,
        row_count=len(data_rows),
        extra_truncated=bytes_truncated,
    )
    return TablePreviewResult(
        status=ParseStatus.OK,
        preview=TablePreview(
            sheets=(sheet,),
            detail="delimited",
            bytes_truncated=bytes_truncated,
        ),
        detail="delimited",
    )


def _col_index(ref: str) -> int:
    letters = []
    for char in ref:
        if char.isalpha():
            letters.append(char.upper())
        else:
            break
    if not letters:
        return 0
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _xlsx_cell_text(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        parts = [
            (node.text or "")
            for node in cell.iter()
            if _xml_local(node.tag) == "t"
        ]
        return "".join(parts)
    value_el = next((c for c in cell if _xml_local(c.tag) == "v"), None)
    raw = (value_el.text or "") if value_el is not None else ""
    if kind == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return raw
    if kind == "b":
        return "true" if raw in {"1", "true"} else "false"
    return raw


def _load_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    names = {name.replace("\\", "/") for name in zf.namelist()}
    path = next((n for n in ("xl/sharedStrings.xml", "xl/SharedStrings.xml") if n in names), None)
    if path is None:
        return []
    root = ET.fromstring(zf.read(path))
    out: list[str] = []
    for si in root:
        if _xml_local(si.tag) != "si":
            continue
        parts = [
            (node.text or "")
            for node in si.iter()
            if _xml_local(node.tag) == "t"
        ]
        out.append("".join(parts))
    return out


def _xlsx_sheet_targets(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    names = {name.replace("\\", "/") for name in zf.namelist()}
    if "xl/workbook.xml" not in names:
        fallback = [
            n for n in sorted(names) if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
        ]
        return [(f"Sheet{i + 1}", path) for i, path in enumerate(fallback)]

    root = ET.fromstring(zf.read("xl/workbook.xml"))
    rels: dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in names:
        rel_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        for rel in rel_root:
            rid = rel.get("Id")
            target = (rel.get("Target") or "").replace("\\", "/")
            if not rid or not target:
                continue
            if target.startswith("/"):
                target = target.lstrip("/")
            elif not target.startswith("xl/"):
                target = f"xl/{target}"
            rels[rid] = target

    sheets: list[tuple[str, str]] = []
    for node in root.iter():
        if _xml_local(node.tag) != "sheet":
            continue
        name = node.get("name") or f"Sheet{len(sheets) + 1}"
        rid = node.get("id") or node.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        if rid is None:
            for key, value in node.attrib.items():
                if key.endswith("}id"):
                    rid = value
                    break
        sheet_target = rels.get(rid or "")
        if sheet_target:
            sheets.append((name, sheet_target))
    if sheets:
        return sheets
    fallback = [
        n for n in sorted(names) if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
    ]
    return [(f"Sheet{i + 1}", path) for i, path in enumerate(fallback)]


def _preview_xlsx_sheet(
    xml: bytes, *, name: str, shared: list[str]
) -> TableSheetPreview | None:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    rows: list[list[str]] = []
    for row_el in root.iter():
        if _xml_local(row_el.tag) != "row":
            continue
        cells: dict[int, str] = {}
        max_idx = -1
        for cell in row_el:
            if _xml_local(cell.tag) != "c":
                continue
            idx = _col_index(cell.get("r") or "")
            cells[idx] = _xlsx_cell_text(cell, shared)
            if idx > max_idx:
                max_idx = idx
        if max_idx < 0:
            continue
        rows.append([cells.get(i, "") for i in range(max_idx + 1)])
    nonempty = [row for row in rows if any(cell.strip() for cell in row)]
    if not nonempty:
        return None
    header = [cell.strip() or f"col_{i + 1}" for i, cell in enumerate(nonempty[0])]
    data_rows = nonempty[1:]
    return _trim_sheet(name, header, data_rows, row_count=len(data_rows))


def _preview_xlsx(data: bytes, *, bytes_truncated: bool) -> TablePreviewResult:
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile:
        return TablePreviewResult(status=ParseStatus.FAILED, detail="bad_xlsx_zip")
    with zf:
        try:
            shared = _load_shared_strings(zf)
            targets = _xlsx_sheet_targets(zf)
        except (ET.ParseError, KeyError, OSError) as exc:
            return TablePreviewResult(
                status=ParseStatus.FAILED, detail=f"xlsx:{type(exc).__name__}"
            )
        if not targets:
            return TablePreviewResult(status=ParseStatus.FAILED, detail="xlsx_no_sheets")
        sheets: list[TableSheetPreview] = []
        sheet_trunc = len(targets) > TABLE_PREVIEW_MAX_SHEETS
        for sheet_name, path in targets[:TABLE_PREVIEW_MAX_SHEETS]:
            try:
                xml = zf.read(path)
            except KeyError:
                continue
            sheet = _preview_xlsx_sheet(xml, name=sheet_name, shared=shared)
            if sheet is not None:
                sheets.append(sheet)
        if not sheets:
            return TablePreviewResult(status=ParseStatus.FAILED, detail="xlsx_empty")
        if sheet_trunc or bytes_truncated:
            first = sheets[0]
            sheets[0] = TableSheetPreview(
                name=first.name,
                row_count=first.row_count,
                columns=first.columns,
                sample_rows=first.sample_rows,
                truncated=True,
            )
        return TablePreviewResult(
            status=ParseStatus.OK,
            preview=TablePreview(
                sheets=tuple(sheets),
                detail="xlsx",
                bytes_truncated=bytes_truncated,
            ),
            detail="xlsx",
        )


def extract_table_preview(data: bytes, ext: str) -> TablePreviewResult:
    """Build a capped structure preview. Never returns the full table body."""
    normalized = ext.lower() if ext.startswith(".") else (f".{ext.lower()}" if ext else "")
    if normalized not in TABLE_EXTENSIONS:
        return TablePreviewResult(status=ParseStatus.SKIPPED, detail=f"not_table:{normalized}")
    if not data:
        return TablePreviewResult(status=ParseStatus.FAILED, detail="empty_bytes")
    if normalized == ".xls" or data.startswith(_XLS_MAGIC):
        return TablePreviewResult(status=ParseStatus.FAILED, detail="xls_unsupported")

    over_budget = len(data) > TABLE_PREVIEW_MAX_BYTES
    try:
        if normalized in {".csv", ".tsv"}:
            payload = data[:TABLE_PREVIEW_MAX_BYTES]
            return _preview_delimited(
                payload, ext=normalized, bytes_truncated=over_budget
            )
        if over_budget:
            return TablePreviewResult(
                status=ParseStatus.FAILED,
                detail=f"extract_budget:{len(data)}>{TABLE_PREVIEW_MAX_BYTES}",
            )
        return _preview_xlsx(data, bytes_truncated=False)
    except Exception as exc:  # noqa: BLE001 — preview must never break residency
        logger.warning(
            "attachment.preparse_failed",
            ext=normalized,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return TablePreviewResult(
            status=ParseStatus.FAILED, detail=f"preview:{type(exc).__name__}"
        )


def format_table_preview(preview: TablePreview) -> str:
    """Render a structure-only block. Caps total characters; never dumps the table."""
    lines: list[str] = []
    if preview.bytes_truncated:
        lines.append(
            f"scan capped at {TABLE_PREVIEW_MAX_BYTES} bytes; row counts are from the scan."
        )
    if len(preview.sheets) > 1:
        lines.append(f"sheets: {len(preview.sheets)}")
    for sheet in preview.sheets:
        lines.append(f"sheet: {sheet.name}")
        lines.append(f"rows: {sheet.row_count} (data; header excluded)")
        col_bits = [f"{col.name}:{col.inferred_type}" for col in sheet.columns]
        lines.append(f"columns ({len(sheet.columns)}): {' | '.join(col_bits)}")
        sample_n = len(sheet.sample_rows)
        lines.append(f"sample rows ({sample_n} of {sheet.row_count}):")
        if not sheet.sample_rows:
            lines.append("  (none)")
        for i, row in enumerate(sheet.sample_rows, start=1):
            lines.append(f"  {i}. {' | '.join(row)}")
        if sheet.truncated:
            lines.append("  … additional columns or rows omitted from this preview.")
    body = "\n".join(lines)
    if len(body) <= TABLE_PREVIEW_MAX_PROMPT_CHARS:
        return body
    return body[: TABLE_PREVIEW_MAX_PROMPT_CHARS - 1] + "…"


def table_preview_from_mapping(raw: object) -> TablePreview | None:
    """Accept a persist-enriched dict or an already-built preview."""
    if isinstance(raw, TablePreview):
        return raw
    if isinstance(raw, dict):
        return TablePreview.from_dict(raw)
    return None


async def preview_table_resident(
    backend: WorkspaceBackend,
    *,
    workspace_path: str,
    name: str | None,
) -> TablePreviewResult:
    """Read a resident spreadsheet / delimited file and build a structure preview.

    Never raises for parse failures. Does not write a ``*.md`` copy and never
    returns the full table body.
    """
    ext = extension_of(name, workspace_path)
    if ext not in TABLE_EXTENSIONS:
        return TablePreviewResult(status=ParseStatus.SKIPPED, detail=f"not_table:{ext or '?'}")
    try:
        data = await backend.read_bytes(workspace_path)
    except WorkspaceError as e:
        logger.warning(
            "attachment.preparse_read_failed",
            path=workspace_path,
            error=str(e),
        )
        return TablePreviewResult(status=ParseStatus.FAILED, detail=f"read:{e}")

    result = await asyncio.to_thread(extract_table_preview, data, ext)
    if result.status == ParseStatus.OK and result.preview is not None:
        logger.info(
            "attachment.preparse_ok",
            path=workspace_path,
            name=name,
            kind="table",
            sheets=len(result.preview.sheets),
            rows=result.preview.sheets[0].row_count if result.preview.sheets else 0,
        )
    else:
        logger.warning(
            "attachment.preparse_failed",
            path=workspace_path,
            name=name,
            kind="table",
            detail=result.detail,
        )
    return result


if __name__ == "__main__":
    raise SystemExit(_cli_main())
