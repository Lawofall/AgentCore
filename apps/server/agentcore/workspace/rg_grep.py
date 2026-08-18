"""Ripgrep-backed workspace content search (shared by ServerWorkspace / tests).

Contract: assemble ``GrepResult`` with the same fields the Python walk used to
produce. Product semantics (path guard happens *before* this module; ignore =
name set not gitignore; glob = filename only after ``normalize_glob``; stable
sort before truncation) are enforced here so cloud and sidecar stay aligned.

Binary resolution never falls back to PATH or a Python walk — missing rg is an
explicit ``WorkspaceIOError``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from agentcore.workspace._paths import (
    AI_NOISE_FILE_SUFFIXES,
    IGNORED_DIRS,
    SYSTEM_IGNORED_FILE_SUFFIXES,
    normalize_glob,
)
from agentcore.workspace.protocol import (
    GrepHit,
    GrepQuery,
    GrepResult,
    WorkspaceIOError,
)
from agentcore.workspace.stage_dirs import (
    BASELINES_REL,
    INDEX_REL,
    TRASH_REL,
    VERSIONS_REL,
)

# Unified grep file-size cap (was ~2MB server / 5MiB desktop walk) — both ends.
GREP_MAX_FILE_BYTES = 2 * 1024 * 1024
GREP_MAX_FILES_SCANNED = 5000
GREP_MAX_RESULTS_CAP = 200
GREP_MAX_LINE = 300

_MAX_FILESIZE_ARG = f"{GREP_MAX_FILE_BYTES}"


def resolve_rg_binary() -> Path | None:
    """Locate the embedded rg binary; never consult PATH."""
    env = (os.environ.get("AGENTCORE_RG_PATH") or "").strip()
    if env:
        p = Path(env)
        if p.is_file():
            return p
        return None

    exe = "rg.exe" if sys.platform == "win32" else "rg"
    candidates = [
        Path("/usr/local/bin/rg"),  # Docker runtime image
        # apps/server/bin/rg — local / CI after ``fetch_ripgrep.py --install-server``
        Path(__file__).resolve().parents[2] / "bin" / exe,
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def require_rg_binary() -> Path:
    rg = resolve_rg_binary()
    if rg is None:
        raise WorkspaceIOError(
            "ripgrep 二进制未找到（未设置 AGENTCORE_RG_PATH / 未内嵌 rg）。"
            "请运行: python apps/server/scripts/fetch_ripgrep.py --install-server"
        )
    return rg


def _trim(line: str) -> str:
    s = line.strip()
    return s[:GREP_MAX_LINE] + " …" if len(s) > GREP_MAX_LINE else s


def _ignore_globs() -> list[str]:
    """Product ignore set as rg ``--glob`` exclusions (with ``--no-ignore``)."""
    globs: list[str] = []
    for name in sorted(IGNORED_DIRS):
        globs.append(f"!{name}")
        globs.append(f"!**/{name}/**")
    # Path-aware internal zones — never bare index/trash/baselines/versions.
    for zone in (INDEX_REL, TRASH_REL, BASELINES_REL, VERSIONS_REL):
        globs.append(f"!{zone}")
        globs.append(f"!{zone}/**")
    for suf in sorted(SYSTEM_IGNORED_FILE_SUFFIXES | AI_NOISE_FILE_SUFFIXES):
        globs.append(f"!**/*{suf}")
    return globs


def _common_rg_flags(
    *,
    case_insensitive: bool,
    name_glob: str | None,
    apply_product_ignore: bool = True,
) -> list[str]:
    args = [
        "--no-ignore",
        "--no-config",
        "--hidden",
        "--color",
        "never",
        "--max-filesize",
        _MAX_FILESIZE_ARG,
        "--sort",
        "path",
    ]
    if case_insensitive:
        args.append("--ignore-case")
    if apply_product_ignore:
        for g in _ignore_globs():
            args.extend(["--glob", g])
    if name_glob:
        args.extend(["--glob", name_glob])
    return args


def _parse_line_hit(line: str) -> tuple[str, int, str] | None:
    """Parse ``path:lineno:text`` (path may contain drive letters on Windows abs)."""
    # Prefer the last ``:digits:`` split from the right after finding lineno.
    m = re.match(r"^(.*):(\d+):(.*)$", line)
    if not m:
        return None
    return m.group(1), int(m.group(2)), m.group(3)


def _parse_count_line(line: str) -> tuple[str, int] | None:
    m = re.match(r"^(.*):(\d+)$", line)
    if not m:
        return None
    return m.group(1), int(m.group(2))


def _regex_diagnostic_detail(stderr: str) -> str:
    """Keep rg's useful diagnostic, not a header-only first line.

    ``rg: regex parse error:`` is a label; the reason and caret live on the
    following lines. Clipping to first (or last) line drops that reason — or,
    for ``the literal "\\n" is not allowed``, drops the multiline hint.
    """
    lines = [ln.rstrip() for ln in (stderr or "").splitlines() if ln.strip()]
    return "\n".join(lines) if lines else (stderr or "").strip()


def _regex_error_message(stderr: str) -> str | None:
    text = (stderr or "").strip()
    if not text:
        return None
    lower = text.lower()
    if "regex" in lower or "parse error" in lower or "syntax error" in lower:
        return f"正则表达式无效：{_regex_diagnostic_detail(text)}"
    return None


_RG_IO_HINTS = (
    "permission denied",
    "access is denied",
    "access denied",
    "os error 5",
    "os error 13",
    "os error 32",
    "拒绝访问",
)


def _is_rg_io_line(line: str) -> bool:
    lower = line.lower()
    return any(h in lower for h in _RG_IO_HINTS)


def _rg_io_warnings(stderr: str) -> list[str] | None:
    """If stderr is solely per-path IO/permission noise, return soft warnings.

    ``None`` means the failure is not a soft-skippable IO case (caller should
    raise). Empty stderr → ``None`` (unknown hard failure).
    """
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    if not lines:
        return None
    if any(not _is_rg_io_line(ln) for ln in lines):
        return None
    warnings: list[str] = []
    for ln in lines:
        # ``rg: path: Access is denied. (os error 5)`` → keep path if present.
        body = ln[3:].strip() if ln.lower().startswith("rg:") else ln
        warnings.append(f"跳过无权限路径：{body}")
    return warnings


def _handle_rg_status(code: int, stderr: str) -> list[str]:
    """Return soft IO warnings, or raise on hard failure. Codes 0/1 → no warnings."""
    if code in (0, 1):
        return []
    regex_msg = _regex_error_message(stderr)
    if regex_msg:
        raise WorkspaceIOError(regex_msg)
    io_warnings = _rg_io_warnings(stderr)
    if io_warnings is not None:
        return io_warnings
    detail = (stderr or "").strip() or f"rg exited with code {code}"
    raise WorkspaceIOError(f"ripgrep 失败：{detail}")


async def _run_rg(
    rg: Path,
    args: list[str],
    *,
    cwd: Path,
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        str(rg),
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await proc.communicate()
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        raise
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    code = proc.returncode if proc.returncode is not None else 2
    return code, stdout, stderr


async def _list_candidate_files(
    rg: Path,
    *,
    search_root: Path,
    case_insensitive: bool,
    name_glob: str | None,
    single_file: bool,
) -> tuple[list[str], bool, list[str]]:
    """Return sorted relative paths under ``search_root``, capped at scan limit."""
    if single_file:
        return [search_root.name], False, []

    args = [
        "--files",
        *_common_rg_flags(
            case_insensitive=case_insensitive,
            name_glob=name_glob,
            apply_product_ignore=True,
        ),
        ".",
    ]
    code, stdout, stderr = await _run_rg(rg, args, cwd=search_root)
    warnings = _handle_rg_status(code, stderr)
    files = [ln.replace("\\", "/") for ln in stdout.splitlines() if ln.strip()]
    # ``--sort path`` already sorted; re-sort for defense in depth.
    files.sort()
    truncated = len(files) > GREP_MAX_FILES_SCANNED
    if truncated:
        files = files[:GREP_MAX_FILES_SCANNED]
    return files, truncated, warnings


_FILE_ARG_CHUNK = 200  # stay under OS argv limits when passing paths to rg


async def _validate_regexp(rg: Path, pattern: str) -> None:
    """Force rg to parse ``pattern`` even when the candidate file set is empty."""
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, suffix=".txt"
    ) as tf:
        probe = Path(tf.name)
    try:
        code, _stdout, stderr = await _run_rg(
            rg,
            ["--regexp", pattern, "--", str(probe)],
            cwd=probe.parent,
        )
        _handle_rg_status(code, stderr)
    finally:
        probe.unlink(missing_ok=True)


async def _search_paths(
    rg: Path,
    *,
    pattern: str,
    paths: list[str],
    cwd: Path,
    case_insensitive: bool,
    files_only: bool,
) -> tuple[str, list[str]]:
    """Search an explicit path list via positional args (chunked; no --files-from)."""
    if not paths:
        return "", []
    mode_flags = (
        ["--count", "--with-filename"]
        if files_only
        else ["--line-number", "--with-filename", "--no-heading"]
    )
    base = [
        *mode_flags,
        *_common_rg_flags(
            case_insensitive=case_insensitive,
            name_glob=None,
            apply_product_ignore=False,
        ),
        "--regexp",
        pattern,
    ]
    chunks: list[str] = []
    warnings: list[str] = []
    for i in range(0, len(paths), _FILE_ARG_CHUNK):
        chunk = paths[i : i + _FILE_ARG_CHUNK]
        code, stdout, stderr = await _run_rg(
            rg, [*base, "--", *chunk], cwd=cwd
        )
        warnings.extend(_handle_rg_status(code, stderr))
        if stdout:
            chunks.append(stdout)
    return "".join(chunks), warnings


async def run_grep_rg(
    *,
    query: GrepQuery,
    search_root: Path,
    workspace_root: Path,
    model_path: Callable[[Path], str],
    rg: Path | None = None,
) -> GrepResult:
    """Run product-semantic grep via embedded ripgrep."""
    del workspace_root  # reserved for callers / future abs-path normalization
    rg = rg or require_rg_binary()
    max_results = max(1, min(query.max_results, GREP_MAX_RESULTS_CAP))
    single_file = search_root.is_file()
    # Single-file path: glob is moot (rg PATTERN FILE).
    name_glob = None if single_file else normalize_glob(query.glob or "")

    await _validate_regexp(rg, query.pattern)

    files, scan_truncated, list_warnings = await _list_candidate_files(
        rg,
        search_root=search_root if not single_file else search_root.parent,
        case_insensitive=query.case_insensitive,
        name_glob=name_glob,
        single_file=single_file,
    )
    if single_file:
        search_cwd = search_root.parent
        path_args = [search_root.name]
    else:
        search_cwd = search_root
        if not files:
            return GrepResult(truncated=scan_truncated, warnings=list_warnings)
        path_args = files

    result = GrepResult(truncated=scan_truncated, warnings=list(list_warnings))
    stdout, search_warnings = await _search_paths(
        rg,
        pattern=query.pattern,
        paths=path_args,
        cwd=search_cwd,
        case_insensitive=query.case_insensitive,
        files_only=query.files_only,
    )
    result.warnings.extend(search_warnings)

    lines = [ln for ln in stdout.splitlines() if ln]
    if query.files_only:
        parsed_counts: list[tuple[str, int]] = []
        for ln in lines:
            parsed = _parse_count_line(ln)
            if not parsed:
                continue
            raw_path, count = parsed
            if single_file:
                rel = model_path(search_root)
            else:
                abs_path = (search_cwd / raw_path).resolve()
                rel = model_path(abs_path)
            parsed_counts.append((rel, count))
        parsed_counts.sort(key=lambda x: x[0])
        if len(parsed_counts) > max_results:
            result.truncated = True
            parsed_counts = parsed_counts[:max_results]
        result.file_counts = parsed_counts
        result.total_matches = sum(c for _, c in parsed_counts)
        return result

    parsed_hits: list[tuple[str, int, str]] = []
    for ln in lines:
        hit = _parse_line_hit(ln)
        if not hit:
            continue
        raw_path, lineno, text = hit
        if single_file:
            rel = model_path(search_root)
        else:
            abs_path = (search_cwd / raw_path).resolve()
            rel = model_path(abs_path)
        parsed_hits.append((rel, lineno, _trim(text)))
    parsed_hits.sort(key=lambda h: (h[0], h[1]))
    if len(parsed_hits) > max_results:
        result.truncated = True
        parsed_hits = parsed_hits[:max_results]

    file_counts_map: dict[str, int] = {}
    for rel, lineno, text in parsed_hits:
        result.hits.append(GrepHit(rel, lineno, text))
        file_counts_map[rel] = file_counts_map.get(rel, 0) + 1
    result.file_counts = sorted(file_counts_map.items(), key=lambda x: x[0])
    result.total_matches = len(result.hits)
    return result
