"""Sniff workspace bytes so file_read does not UTF-8-decode binary/OLE/PDF.

Magic-only: no filename heuristics beyond what the caller already knows.
"""

from __future__ import annotations

from typing import Literal

# OLE compound document (legacy .doc / .xls / .ppt). First byte 0xD0.
OLE_MAGIC = b"\xd0\xcf\x11\xe0"
PDF_MAGIC = b"%PDF"
ZIP_MAGIC = b"PK"

FileKind = Literal["pdf", "ole", "zip", "binary", "text", "empty"]

# Legacy Word that python-docx / markitdown do not extract.
OLE_WORD_EXTENSIONS = frozenset({".doc", ".dot", ".wbk"})


def sniff_bytes(head: bytes) -> FileKind:
    """Classify a file from a short prefix (16+ bytes is enough for these magics)."""
    if not head:
        return "empty"
    if head.startswith(PDF_MAGIC):
        return "pdf"
    if head.startswith(OLE_MAGIC):
        return "ole"
    if head.startswith(ZIP_MAGIC):
        return "zip"
    if b"\x00" in head[:1024]:
        return "binary"
    return "text"


def decode_text_bytes(data: bytes) -> str | None:
    """UTF-8 then GB18030 (Windows Chinese). None if binary / undecodable."""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def parse_too_large_size(detail: str) -> int | None:
    """Parse ``文件过大，无法读取（N字节）``; unknown if the suffix is absent."""
    text = (detail or "").strip()
    marker = "（"
    tail = "字节）"
    if marker not in text or not text.endswith(tail):
        return None
    inner = text[text.rfind(marker) + 1 : -len(tail)]
    try:
        return int(inner)
    except ValueError:
        return None
