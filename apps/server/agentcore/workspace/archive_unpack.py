"""Unpack user-attached archives at residency (压缩包 = 文件夹).

Zip / tar / gzip via stdlib. RAR / 7z via ``7z`` (``AGENTCORE_7Z_PATH``,
``apps/server/bin``, then PATH). API runtime / guest image / sidecar all
bundle official 7-Zip (``fetch_7zip.py``), not Debian p7zip. One level
only; nested packs stay files. Citations outside ``attachments/`` are
not mutated.
"""

from __future__ import annotations

import contextlib
import gzip
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from agentcore.core.logging import get_logger
from agentcore.storage._archive import (
    ZipExtractLimitError,
    ZipSlipError,
    zip_member_relpath,
)
from agentcore.workspace.file_kind import (
    FileKind,
    is_container_archive,
    name_looks_like_archive,
    sniff_bytes,
)
from agentcore.workspace.limits import (
    ARCHIVE_PROMPT_MEMBER_CAP,
    ARCHIVE_UNPACK_MAX_BYTES,
    ARCHIVE_UNPACK_MAX_FILES,
    ARCHIVE_UNPACK_TIMEOUT_SECONDS,
)
from agentcore.workspace.protocol import (
    AlreadyExists,
    WorkspaceBackend,
    WorkspaceError,
)

logger = get_logger(__name__)

_SEVEN_Z_BINS = ("7z", "7zz", "7za")
_TarReadMode = Literal["r:*", "r:gz", "r:bz2", "r:xz"]
_TAR_MODES: tuple[_TarReadMode, ...] = ("r:*", "r:gz", "r:bz2", "r:xz")
_ZIP_READ_CHUNK = 1024 * 1024


class ArchiveStatus(StrEnum):
    OK = "ok"
    PASSWORD = "password"
    BOMB = "bomb"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class UnpackResult:
    status: ArchiveStatus
    dest_dir: str | None = None
    members: tuple[str, ...] = ()
    member_count: int = 0
    truncated_listing: bool = False
    detail: str = ""


class UnpackFailedError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class UnpackPasswordError(Exception):
    pass


def find_7z() -> str | None:
    """Prefer the bundled extra, then PATH (guest / user install)."""
    env = (os.environ.get("AGENTCORE_7Z_PATH") or "").strip()
    if env and Path(env).is_file():
        return env
    exe_names = (
        "7z.exe",
        "7zz.exe",
        "7za.exe",
        "7z",
        "7zz",
        "7za",
    )
    bundled = Path(__file__).resolve().parents[2] / "bin"
    for name in exe_names:
        candidate = bundled / name
        if candidate.is_file():
            return str(candidate)
    for name in _SEVEN_Z_BINS:
        found = shutil.which(name)
        if found:
            return found
    return None


def unpack_dir_for(archive_rel: str) -> str:
    """``attachments/pack.zip`` → ``attachments/pack`` (``-files`` if it collides)."""
    cleaned = (archive_rel or "").replace("\\", "/").strip("/")
    if "/" in cleaned:
        parent, name = cleaned.rsplit("/", 1)
    else:
        parent, name = "", cleaned
    lower = name.lower()
    stem = name
    for ext in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if lower.endswith(ext):
            stem = name[: -len(ext)]
            break
    else:
        stem = Path(name).stem or "archive"
    dest = f"{parent}/{stem}" if parent else stem
    if dest == cleaned:
        dest = f"{dest}-files"
    return dest


def should_try_unpack(*, sniff: FileKind, name: str | None) -> bool:
    """Zip magic is shared with Office; only named containers are unpacked."""
    if is_container_archive(sniff, name):
        return True
    return sniff == "binary" and name_looks_like_archive(name)


def _listing(members: list[str]) -> tuple[tuple[str, ...], bool]:
    if len(members) <= ARCHIVE_PROMPT_MEMBER_CAP:
        return tuple(members), False
    return tuple(members[:ARCHIVE_PROMPT_MEMBER_CAP]), True


def _raise_if_unpack_budget(*, file_count: int, total_bytes: int) -> None:
    if file_count > ARCHIVE_UNPACK_MAX_FILES:
        raise ZipExtractLimitError(
            reason="max_files",
            file_count=file_count,
            total_bytes=total_bytes,
        )
    if total_bytes > ARCHIVE_UNPACK_MAX_BYTES:
        raise ZipExtractLimitError(
            reason="max_uncompressed_bytes",
            file_count=file_count,
            total_bytes=total_bytes,
        )


def _tree_file_bytes(root: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            try:
                total += path.stat().st_size
            except OSError:
                continue
            if total > ARCHIVE_UNPACK_MAX_BYTES:
                return total
    return total


def _run_7z(
    args: list[str], *, watch_dir: Path | None = None
) -> subprocess.CompletedProcess[bytes]:
    if watch_dir is None:
        try:
            return subprocess.run(
                args,
                capture_output=True,
                timeout=ARCHIVE_UNPACK_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise UnpackFailedError("timeout") from exc
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + ARCHIVE_UNPACK_TIMEOUT_SECONDS
    while proc.poll() is None:
        if time.monotonic() > deadline:
            proc.kill()
            proc.wait(timeout=5)
            raise UnpackFailedError("timeout")
        used = _tree_file_bytes(watch_dir)
        if used > ARCHIVE_UNPACK_MAX_BYTES:
            proc.kill()
            proc.wait(timeout=5)
            raise ZipExtractLimitError(
                reason="max_uncompressed_bytes",
                file_count=0,
                total_bytes=used,
            )
        time.sleep(0.05)
    stderr = proc.stderr.read() if proc.stderr is not None else b""
    return subprocess.CompletedProcess(args, proc.returncode or 0, b"", stderr)


def _members_from_zip(data: bytes) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                try:
                    rel = zip_member_relpath(info.filename)
                except ZipSlipError:
                    continue
                if rel is None:
                    continue
                if len(out) >= ARCHIVE_UNPACK_MAX_FILES:
                    raise ZipExtractLimitError(
                        reason="max_files",
                        file_count=len(out),
                        total_bytes=total,
                    )
                declared = int(info.file_size)
                if total + declared > ARCHIVE_UNPACK_MAX_BYTES:
                    raise ZipExtractLimitError(
                        reason="max_uncompressed_bytes",
                        file_count=len(out),
                        total_bytes=total,
                    )
                chunks: list[bytes] = []
                actual = 0
                with zf.open(info) as src:
                    while True:
                        chunk = src.read(_ZIP_READ_CHUNK)
                        if not chunk:
                            break
                        actual += len(chunk)
                        if total + actual > ARCHIVE_UNPACK_MAX_BYTES:
                            raise ZipExtractLimitError(
                                reason="max_uncompressed_bytes",
                                file_count=len(out),
                                total_bytes=total,
                            )
                        chunks.append(chunk)
                content = b"".join(chunks)
                out.append((rel, content))
                total += actual
    except zipfile.BadZipFile as exc:
        raise UnpackFailedError("bad_zip") from exc
    except RuntimeError as exc:
        if "password" in str(exc).lower() or "encrypted" in str(exc).lower():
            raise UnpackPasswordError from exc
        raise UnpackFailedError("zip_runtime") from exc
    return out


def _safe_rel(member: str) -> str | None:
    try:
        return zip_member_relpath(member)
    except ZipSlipError:
        return None


def _members_from_tar(data: bytes) -> list[tuple[str, bytes]]:
    last_error: Exception | None = None
    for mode in _TAR_MODES:
        try:
            return _read_tar(data, mode)
        except tarfile.TarError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise UnpackFailedError("bad_tar") from last_error
    raise UnpackFailedError("bad_tar")


def _read_tar(data: bytes, mode: _TarReadMode) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode=mode) as tf:
        for info in tf:
            if not info.isfile():
                continue
            rel = _safe_rel(info.name)
            if rel is None:
                continue
            if len(out) >= ARCHIVE_UNPACK_MAX_FILES:
                raise ZipExtractLimitError(
                    reason="max_files",
                    file_count=len(out),
                    total_bytes=total,
                )
            declared = int(info.size or 0)
            if total + declared > ARCHIVE_UNPACK_MAX_BYTES:
                raise ZipExtractLimitError(
                    reason="max_uncompressed_bytes",
                    file_count=len(out),
                    total_bytes=total,
                )
            src = tf.extractfile(info)
            if src is None:
                continue
            content = src.read(ARCHIVE_UNPACK_MAX_BYTES - total + 1)
            if total + len(content) > ARCHIVE_UNPACK_MAX_BYTES:
                raise ZipExtractLimitError(
                    reason="max_uncompressed_bytes",
                    file_count=len(out),
                    total_bytes=total,
                )
            out.append((rel, content))
            total += len(content)
    return out


def _members_from_gzip_file(data: bytes, archive_name: str) -> list[tuple[str, bytes]]:
    raw = gzip.decompress(data)
    if len(raw) > ARCHIVE_UNPACK_MAX_BYTES:
        raise ZipExtractLimitError(
            reason="max_uncompressed_bytes",
            file_count=0,
            total_bytes=0,
        )
    name = Path(archive_name.replace("\\", "/").rsplit("/", 1)[-1]).stem or "file"
    rel = _safe_rel(name)
    if rel is None:
        raise UnpackFailedError("gzip_name")
    return [(rel, raw)]


def _members_from_7z(data: bytes) -> list[tuple[str, bytes]]:
    binary = find_7z()
    if binary is None:
        raise UnpackFailedError("need_7z")

    with tempfile.TemporaryDirectory(prefix="ac-unpack-") as td:
        root = Path(td)
        src = root / "archive.bin"
        dest = root / "out"
        dest.mkdir()
        src.write_bytes(data)
        listed = _names_from_7z_binary(binary, src)
        _raise_if_unpack_budget(
            file_count=len(listed),
            total_bytes=sum(size for _rel, size in listed),
        )
        proc = _run_7z(
            [binary, "x", "-y", "-bd", f"-o{dest}", str(src)],
            watch_dir=dest,
        )
        err = (proc.stderr or b"").decode("utf-8", errors="replace").lower()
        if proc.returncode != 0:
            if "password" in err or "encrypted" in err:
                raise UnpackPasswordError
            raise UnpackFailedError(f"7z_exit_{proc.returncode}")
        out: list[tuple[str, bytes]] = []
        total = 0
        for path in sorted(dest.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            rel = _safe_rel(path.relative_to(dest).as_posix())
            if rel is None:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            _raise_if_unpack_budget(
                file_count=len(out) + 1,
                total_bytes=total + size,
            )
            payload = path.read_bytes()
            out.append((rel, payload))
            total += len(payload)
        return out


def extract_members(data: bytes, *, sniff: FileKind, name: str) -> list[tuple[str, bytes]]:
    """Return ``(relpath, bytes)`` for one archive. Raises typed unpack errors."""
    if sniff == "zip":
        return _members_from_zip(data)
    if sniff in {"rar", "7z"}:
        return _members_from_7z(data)
    if sniff == "tar":
        return _members_from_tar(data)
    if sniff == "gzip":
        try:
            return _members_from_tar(data)
        except UnpackFailedError:
            return _members_from_gzip_file(data, name)
    if sniff == "binary" and name_looks_like_archive(name):
        lower = name.lower()
        if lower.endswith(".zip"):
            return _members_from_zip(data)
        if lower.endswith((".rar", ".7z")):
            return _members_from_7z(data)
        return _members_from_tar(data)
    raise UnpackFailedError("not_archive")


def _names_from_zip(data: bytes) -> list[str]:
    out: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                try:
                    rel = zip_member_relpath(info.filename)
                except ZipSlipError:
                    continue
                if rel is None:
                    continue
                if len(out) >= ARCHIVE_UNPACK_MAX_FILES:
                    raise ZipExtractLimitError(
                        reason="max_files",
                        file_count=len(out),
                        total_bytes=0,
                    )
                out.append(rel)
    except zipfile.BadZipFile as exc:
        raise UnpackFailedError("bad_zip") from exc
    except RuntimeError as exc:
        if "password" in str(exc).lower() or "encrypted" in str(exc).lower():
            raise UnpackPasswordError from exc
        raise UnpackFailedError("zip_runtime") from exc
    return out


def _names_from_tar(data: bytes) -> list[str]:
    last_error: Exception | None = None
    for mode in _TAR_MODES:
        try:
            out: list[str] = []
            with tarfile.open(fileobj=io.BytesIO(data), mode=mode) as tf:
                for info in tf:
                    if not info.isfile():
                        continue
                    rel = _safe_rel(info.name)
                    if rel is None:
                        continue
                    if len(out) >= ARCHIVE_UNPACK_MAX_FILES:
                        raise ZipExtractLimitError(
                            reason="max_files",
                            file_count=len(out),
                            total_bytes=0,
                        )
                    out.append(rel)
            return out
        except tarfile.TarError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise UnpackFailedError("bad_tar") from last_error
    raise UnpackFailedError("bad_tar")


def _names_from_7z(data: bytes) -> list[str]:
    binary = find_7z()
    if binary is None:
        raise UnpackFailedError("need_7z")
    with tempfile.TemporaryDirectory(prefix="ac-list-") as td:
        src = Path(td) / "archive.bin"
        src.write_bytes(data)
        return [rel for rel, _size in _names_from_7z_binary(binary, src)]


def _names_from_7z_binary(binary: str, src: Path) -> list[tuple[str, int]]:
    proc = _run_7z([binary, "l", "-slt", "-y", str(src)])
    err = (proc.stderr or b"").decode("utf-8", errors="replace").lower()
    if proc.returncode != 0:
        if "password" in err or "encrypted" in err:
            raise UnpackPasswordError
        raise UnpackFailedError(f"7z_exit_{proc.returncode}")
    entries = _parse_7z_slt_entries(
        (proc.stdout or b"").decode("utf-8", errors="replace")
    )
    _raise_if_unpack_budget(
        file_count=len(entries),
        total_bytes=sum(size for _rel, size in entries),
    )
    return entries


def _parse_7z_slt_paths(stdout: str) -> list[str]:
    """Parse ``7z l -slt`` file paths; skip the temp archive name and folders."""
    return [rel for rel, _size in _parse_7z_slt_entries(stdout)]


def _parse_7z_slt_entries(stdout: str) -> list[tuple[str, int]]:
    """Parse ``7z l -slt`` into ``(relpath, uncompressed size)`` for files."""
    entries: list[tuple[str, int]] = []

    def _take(member: str | None, folder: bool, size: int) -> None:
        if member is None or folder:
            return
        rel = _safe_rel(member)
        if rel is None or rel == "archive.bin":
            return
        if len(entries) >= ARCHIVE_UNPACK_MAX_FILES:
            raise ZipExtractLimitError(
                reason="max_files",
                file_count=len(entries),
                total_bytes=sum(s for _r, s in entries),
            )
        entries.append((rel, size))

    current: str | None = None
    is_folder = False
    size = 0
    for raw in stdout.splitlines():
        line = raw.strip()
        if line.startswith("Path = "):
            _take(current, is_folder, size)
            current = line[7:]
            is_folder = False
            size = 0
        elif line.startswith("Folder = "):
            is_folder = line[9:].strip() not in {"-", "0", ""}
        elif line.startswith("Size = "):
            try:
                size = int(line[7:].strip() or "0")
            except ValueError:
                size = 0
    _take(current, is_folder, size)
    return entries


def list_member_names(data: bytes, *, sniff: FileKind, name: str) -> list[str]:
    """Member paths only — no payload extract. Same typed errors as unpack."""
    if sniff == "zip":
        return _names_from_zip(data)
    if sniff in {"rar", "7z"}:
        return _names_from_7z(data)
    if sniff == "tar":
        return _names_from_tar(data)
    if sniff == "gzip":
        try:
            return _names_from_tar(data)
        except UnpackFailedError:
            rels = _members_from_gzip_file(data, name)
            return [rel for rel, _ in rels]
    if sniff == "binary" and name_looks_like_archive(name):
        lower = name.lower()
        if lower.endswith(".zip"):
            return _names_from_zip(data)
        if lower.endswith((".rar", ".7z")):
            return _names_from_7z(data)
        return _names_from_tar(data)
    raise UnpackFailedError("not_archive")


async def unpack_resident(
    backend: WorkspaceBackend,
    workspace_path: str,
    *,
    name: str | None = None,
) -> UnpackResult | None:
    """Unpack an ``attachments/<file>`` archive beside the original. None if not a pack."""
    display = name or workspace_path.rsplit("/", 1)[-1]
    try:
        head = await backend.read_head(workspace_path)
    except WorkspaceError:
        logger.warning("attachment.archive_head_failed", path=workspace_path, exc_info=True)
        return None
    sniff = sniff_bytes(head.data)
    if not should_try_unpack(sniff=sniff, name=display):
        return None
    try:
        data = await backend.read_bytes(workspace_path)
    except WorkspaceError:
        logger.warning("attachment.archive_read_failed", path=workspace_path, exc_info=True)
        return UnpackResult(status=ArchiveStatus.FAILED, detail="read_failed")
    try:
        members = extract_members(data, sniff=sniff, name=display)
    except UnpackPasswordError:
        logger.info("attachment.archive_failed", path=workspace_path, reason="password")
        return UnpackResult(status=ArchiveStatus.PASSWORD, detail="password")
    except ZipExtractLimitError as exc:
        logger.info(
            "attachment.archive_failed",
            path=workspace_path,
            reason="bomb",
            limit=exc.reason,
        )
        return UnpackResult(status=ArchiveStatus.BOMB, detail=exc.reason)
    except UnpackFailedError as exc:
        status = (
            ArchiveStatus.UNSUPPORTED
            if exc.detail == "need_7z"
            else ArchiveStatus.FAILED
        )
        logger.info(
            "attachment.archive_failed",
            path=workspace_path,
            reason=exc.detail,
        )
        return UnpackResult(status=status, detail=exc.detail)
    except Exception:  # noqa: BLE001 — persist must not break the turn
        logger.warning("attachment.archive_failed", path=workspace_path, exc_info=True)
        return UnpackResult(status=ArchiveStatus.FAILED, detail="unpack_error")

    dest = unpack_dir_for(workspace_path)
    written: list[str] = []
    try:
        with contextlib.suppress(AlreadyExists):
            await backend.mkdir(dest)
        for rel, content in members:
            target = f"{dest}/{rel}"
            parent = target.rsplit("/", 1)[0]
            if parent and parent != dest:
                with contextlib.suppress(AlreadyExists):
                    await backend.mkdir(parent)
            await backend.write_bytes(target, content)
            written.append(rel)
    except WorkspaceError:
        logger.warning("attachment.archive_write_failed", path=dest, exc_info=True)
        return UnpackResult(status=ArchiveStatus.FAILED, detail="write_failed")

    listing, truncated = _listing(written)
    logger.info(
        "attachment.archive_unpacked",
        path=workspace_path,
        dest=dest,
        members=len(written),
    )
    return UnpackResult(
        status=ArchiveStatus.OK,
        dest_dir=dest,
        members=listing,
        member_count=len(written),
        truncated_listing=truncated,
        detail=sniff,
    )
