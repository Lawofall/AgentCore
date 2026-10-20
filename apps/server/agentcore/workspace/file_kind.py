"""Sniff workspace bytes so read does not UTF-8-decode binary/OLE/PDF.

Magic is the primary signal. Archive *container* vs Office zip also needs the
filename (PK is shared with docx/xlsx/pptx).
"""

from __future__ import annotations

from typing import Literal

StoredTextEncoding = Literal["utf-8-sig", "utf-8", "gb18030"]

# OLE compound document (legacy .doc / .xls / .ppt). First byte 0xD0.
OLE_MAGIC = b"\xd0\xcf\x11\xe0"
PDF_MAGIC = b"%PDF"
ZIP_MAGIC = b"PK"
RAR_MAGIC = b"Rar!"
SEVEN_Z_MAGIC = b"7z\xbc\xaf\x27\x1c"
GZIP_MAGIC = b"\x1f\x8b"
# POSIX ustar at header offset 257 (need a 512-byte block; read_head is 1 KiB).
TAR_MAGIC_OFFSET = 257
TAR_MAGIC = b"ustar"

FileKind = Literal[
    "pdf", "ole", "zip", "rar", "7z", "tar", "gzip", "binary", "text", "empty"
]

ARCHIVE_KINDS: frozenset[FileKind] = frozenset({"zip", "rar", "7z", "tar", "gzip"})

# Legacy Word that python-docx / markitdown do not extract.
OLE_WORD_EXTENSIONS = frozenset({".doc", ".dot", ".wbk"})

_ARCHIVE_NAME_SUFFIXES: tuple[str, ...] = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tgz",
    ".tbz2",
    ".txz",
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
)


def sniff_bytes(head: bytes) -> FileKind:
    """Classify a file from a short prefix (16+ bytes; tar needs ~262)."""
    if not head:
        return "empty"
    if head.startswith(PDF_MAGIC):
        return "pdf"
    if head.startswith(OLE_MAGIC):
        return "ole"
    if head.startswith(ZIP_MAGIC):
        return "zip"
    if head.startswith(RAR_MAGIC):
        return "rar"
    if head.startswith(SEVEN_Z_MAGIC):
        return "7z"
    if head.startswith(GZIP_MAGIC):
        return "gzip"
    if len(head) >= TAR_MAGIC_OFFSET + len(TAR_MAGIC) and head[
        TAR_MAGIC_OFFSET : TAR_MAGIC_OFFSET + len(TAR_MAGIC)
    ] == TAR_MAGIC:
        return "tar"
    if b"\x00" in head[:1024]:
        return "binary"
    return "text"


def name_looks_like_archive(name: str | None) -> bool:
    """True when the filename uses a container suffix (fallback if magic is opaque)."""
    lower = (name or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return any(lower.endswith(suf) for suf in _ARCHIVE_NAME_SUFFIXES)


def is_container_archive(kind: FileKind, name: str | None) -> bool:
    """True for user-facing packs. Office zip (docx/xlsx/pptx) is not a pack."""
    if kind not in ARCHIVE_KINDS:
        return False
    if kind == "zip":
        return name_looks_like_archive(name)
    return True


def decode_stored_text(data: bytes) -> tuple[str, StoredTextEncoding] | None:
    """Decode workspace text the same way ``read`` does; return encoding for write-back.

    Order: UTF-8 with BOM, UTF-8, GB18030. NUL anywhere → binary (None).
    """
    if b"\x00" in data:
        return None
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data.decode("utf-8-sig"), "utf-8-sig"
        except UnicodeDecodeError:
            return None
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("gb18030"), "gb18030"
    except UnicodeDecodeError:
        return None


def encode_stored_text(text: str, encoding: StoredTextEncoding) -> bytes:
    """Write-back companion of :func:`decode_stored_text` (BOM restored for utf-8-sig)."""
    return text.encode(encoding)


def decode_text_bytes(data: bytes) -> str | None:
    """UTF-8 then GB18030 (Windows Chinese). None if binary / undecodable."""
    decoded = decode_stored_text(data)
    return None if decoded is None else decoded[0]


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
