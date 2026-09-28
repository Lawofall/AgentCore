"""Ripgrep-backed workspace search (shared by ServerWorkspace / tests).

One ``rg`` process per call: stream until the hit/file cap, then stop. Do not
pre-list the tree or re-open files in argv chunks. Ignore = product name set
(not gitignore). Grep ``glob`` is filename-only after ``normalize_glob``.
Returned hits are sorted for display; truncation is "this batch hit the cap",
not "alphabetically first N of a full-corpus scan".

Missing rg is an explicit ``WorkspaceIOError`` — never PATH / Python walk.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from agentcore.core.spawn import spawn_process
from agentcore.workspace._paths import (
    AI_ARCHIVE_FILE_SUFFIXES,
    AI_IMAGE_FILE_SUFFIXES,
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

GREP_MAX_FILE_BYTES = 2 * 1024 * 1024
GREP_MAX_RESULTS_CAP = 200
GREP_MAX_LINE = 300

_MAX_FILESIZE_ARG = f"{GREP_MAX_FILE_BYTES}"
IgnoreKind = Literal["grep", "list"]


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
        )
    return rg


def _trim(line: str) -> str:
    s = line.strip()
    return s[:GREP_MAX_LINE] + " …" if len(s) > GREP_MAX_LINE else s


def _ignore_globs(
    *,
    kind: IgnoreKind,
    reveal_archives: bool = False,
) -> list[str]:
    """Product ignore set as rg ``--glob`` exclusions (with ``--no-ignore``)."""
    globs: list[str] = []
    for name in sorted(IGNORED_DIRS):
        globs.append(f"!{name}")
        globs.append(f"!**/{name}/**")
    for zone in (INDEX_REL, TRASH_REL, BASELINES_REL, VERSIONS_REL):
        globs.append(f"!{zone}")
        globs.append(f"!{zone}/**")
    suffixes = set(SYSTEM_IGNORED_FILE_SUFFIXES)
    if kind == "grep":
        suffixes |= AI_NOISE_FILE_SUFFIXES
    else:
        suffixes |= AI_NOISE_FILE_SUFFIXES - AI_IMAGE_FILE_SUFFIXES
        if reveal_archives:
            suffixes -= AI_ARCHIVE_FILE_SUFFIXES
    for suf in sorted(suffixes):
        globs.append(f"!**/*{suf}")
    return globs


def _common_rg_flags(
    *,
    case_insensitive: bool = False,
    name_globs: list[str] | None = None,
    kind: IgnoreKind = "grep",
    reveal_archives: bool = False,
    max_depth: int | None = None,
) -> list[str]:
    args = [
        "--no-ignore",
        "--no-config",
        "--hidden",
        "--line-buffered",
        "--color",
        "never",
        "--max-filesize",
        _MAX_FILESIZE_ARG,
    ]
    if case_insensitive:
        args.append("--ignore-case")
    if max_depth is not None:
        args.extend(["--max-depth", str(max(0, int(max_depth)))])
    for g in _ignore_globs(kind=kind, reveal_archives=reveal_archives):
        args.extend(["--glob", g])
    for g in name_globs or ():
        if g:
            args.extend(["--glob", g])
    return args


def _parse_line_hit(line: str) -> tuple[str, int, str] | None:
    """Parse ``path:lineno:text`` (path may contain drive letters on Windows abs)."""
    m = re.match(r"^(.*):(\d+):(.*)$", line)
    if not m:
        return None
    return m.group(1), int(m.group(2)), m.group(3)


# rg stderr often appends CLI flags this product does not expose (--pcre2,
# --multiline / -U). Passing those through teaches the model a switch it cannot
# flip. Keep the parse reason; drop the flag advertisement.
_RG_CLI_FLAG_LINE = re.compile(
    r"(?i)consider enabling|--pcre2|--multiline|"
    r"\(or -U for short\)|when multiline mode is enabled"
)
_LOOKAROUND_STEER = (
    "本工具是 Rust regex，不支持 lookahead/lookbehind。"
    "请改成不含 (?!)/(?=)/(?< 的简单模式，或拆成多次 grep。"
)
_NEWLINE_STEER = (
    "不要把字面换行或 \\n 写进正则。跨行请拆成多次搜索，或只搜其中一行。"
)


def _regex_diagnostic_detail(stderr: str) -> str:
    """Keep rg's useful diagnostic, not a header-only first line."""
    lines = [
        ln.rstrip()
        for ln in (stderr or "").splitlines()
        if ln.strip() and not _RG_CLI_FLAG_LINE.search(ln)
    ]
    return "\n".join(lines) if lines else ""


def _regex_error_message(stderr: str) -> str | None:
    text = (stderr or "").strip()
    if not text:
        return None
    lower = text.lower()
    if "regex" not in lower and "parse error" not in lower and "syntax error" not in lower:
        return None
    detail = _regex_diagnostic_detail(text) or "正则语法无法解析"
    msg = f"正则表达式无效：{detail}"
    if "look-around" in lower or "look-ahead" in lower or "look-behind" in lower:
        msg = f"{msg}\n{_LOOKAROUND_STEER}"
    if "literal" in lower and "\\n" in text:
        msg = f"{msg}\n{_NEWLINE_STEER}"
    return msg


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
    """If stderr is solely per-path IO/permission noise, return soft warnings."""
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    if not lines:
        return None
    if any(not _is_rg_io_line(ln) for ln in lines):
        return None
    warnings: list[str] = []
    for ln in lines:
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


async def _run_rg_capped(
    rg: Path,
    args: list[str],
    *,
    cwd: Path,
    max_lines: int,
) -> tuple[int, list[str], str, bool]:
    """Run one rg; keep at most ``max_lines`` stdout lines, then kill.

    Spawn goes through :func:`spawn_process` so a Windows SelectorEventLoop
    (uvicorn ``--reload``) can still start ``rg``, and cancellation still
    kills the child.
    """
    result = await spawn_process(
        [str(rg), *args],
        cwd=cwd,
        max_stdout_lines=max_lines,
    )
    stderr = result.stderr.decode("utf-8", errors="replace")
    if result.truncated:
        return 0, list(result.lines), stderr, True
    code = result.returncode if result.returncode >= 0 else 2
    return code, list(result.lines), stderr, False


def _to_rel(
    raw_path: str,
    *,
    search_cwd: Path,
    search_root: Path,
    single_file: bool,
    model_path: Callable[[Path], str],
) -> str:
    if single_file:
        return model_path(search_root)
    abs_path = Path(raw_path)
    if not abs_path.is_absolute():
        abs_path = (search_cwd / raw_path)
    return model_path(abs_path.resolve())


async def run_grep_rg(
    *,
    query: GrepQuery,
    search_root: Path,
    workspace_root: Path,
    model_path: Callable[[Path], str],
    rg: Path | None = None,
) -> GrepResult:
    """Run product-semantic grep via one embedded ripgrep process."""
    del workspace_root
    rg = rg or require_rg_binary()
    max_results = max(1, min(query.max_results, GREP_MAX_RESULTS_CAP))
    single_file = search_root.is_file()
    name_glob = None if single_file else normalize_glob(query.glob or "")
    search_cwd = search_root.parent if single_file else search_root
    target = str(search_root) if single_file else "."

    mode_flags = (
        ["--files-with-matches"]
        if query.files_only
        else ["--line-number", "--with-filename", "--no-heading"]
    )

    args = [
        *mode_flags,
        *_common_rg_flags(
            case_insensitive=query.case_insensitive,
            name_globs=[name_glob] if name_glob else None,
            kind="grep",
        ),
        "--regexp",
        query.pattern,
        "--",
        target,
    ]
    code, lines, stderr, capped = await _run_rg_capped(
        rg, args, cwd=search_cwd, max_lines=max_results
    )
    warnings = [] if capped else _handle_rg_status(code, stderr)
    if capped:
        io_warn = _rg_io_warnings(stderr)
        if io_warn:
            warnings.extend(io_warn)

    result = GrepResult(truncated=capped, warnings=list(warnings))
    if query.files_only:
        rels: list[str] = []
        seen: set[str] = set()
        for ln in lines:
            rel = _to_rel(
                ln.replace("\\", "/"),
                search_cwd=search_cwd,
                search_root=search_root,
                single_file=single_file,
                model_path=model_path,
            )
            if rel in seen:
                continue
            seen.add(rel)
            rels.append(rel)
        rels.sort()
        if len(rels) > max_results:
            result.truncated = True
            rels = rels[:max_results]
        result.file_counts = [(rel, 1) for rel in rels]
        result.total_matches = len(rels)
        return result

    parsed_hits: list[tuple[str, int, str]] = []
    for ln in lines:
        hit = _parse_line_hit(ln)
        if not hit:
            continue
        raw_path, lineno, text = hit
        rel = _to_rel(
            raw_path,
            search_cwd=search_cwd,
            search_root=search_root,
            single_file=single_file,
            model_path=model_path,
        )
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


async def run_files_rg(
    *,
    search_root: Path,
    model_path: Callable[[Path], str],
    name_globs: list[str] | None = None,
    max_depth: int | None = None,
    max_entries: int = 50,
    reveal_archives: bool = False,
    rg: Path | None = None,
) -> tuple[list[str], bool, list[str]]:
    """``rg --files`` with product list-ignore; stop at ``max_entries``."""
    rg = rg or require_rg_binary()
    cap = max(1, int(max_entries))
    args = [
        "--files",
        *_common_rg_flags(
            name_globs=name_globs,
            kind="list",
            reveal_archives=reveal_archives,
            max_depth=max_depth,
        ),
        "--",
        ".",
    ]
    code, lines, stderr, capped = await _run_rg_capped(
        rg, args, cwd=search_root, max_lines=cap
    )
    warnings = [] if capped else _handle_rg_status(code, stderr)
    if capped:
        io_warn = _rg_io_warnings(stderr)
        if io_warn:
            warnings.extend(io_warn)

    rels: list[str] = []
    seen: set[str] = set()
    for ln in lines:
        raw = ln.replace("\\", "/").strip()
        if not raw:
            continue
        rel = _to_rel(
            raw,
            search_cwd=search_root,
            search_root=search_root,
            single_file=False,
            model_path=model_path,
        )
        if rel in seen:
            continue
        seen.add(rel)
        rels.append(rel)
    rels.sort()
    truncated = capped
    if len(rels) > cap:
        truncated = True
        rels = rels[:cap]
    return rels, truncated, warnings
