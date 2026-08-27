"""Filesystem primitives for ``ServerWorkspace``.

The workspace sandbox boundary (path-traversal guard) and the content-scan
helpers live here, behind the ``WorkspaceBackend`` seam. Tools no longer touch
these directly — they go through ``ServerWorkspace`` — so this is the single
audited place where user-supplied paths are resolved against the root.

Ignore rules are **two-tier** (双模式工作区 · 系统文件隐藏), aligned with
desktop ``apps/desktop/src/main/fs/workspaceIgnore.ts``.

Parity gate (edit both sides or CI fails)::

    uv run python scripts/check_workspace_ignore_parity.py

* **System noise** — hidden from both AI and user file UI (``.git`` /
  ``node_modules`` / caches / ``*.db`` / ``*.pyc`` …, plus path-aware
  ``AgentCore/{index,trash,baselines}`` — never bare ``index``/``trash``/``baselines``).
* **AI noise** — media / archives / fonts / native objects excluded only from
  AI views (``index_files`` / ``list_tree`` / ``grep`` / ``file_list``). User UI
  ``list`` keeps them visible (AI-generated images are deliverables).
"""

import errno
import re
from pathlib import Path

from agentcore.workspace.stage_dirs import (
    AGENTCORE_ROOT,
    DEBATE_PREFIX,
    DRAFTS_PREFIX,
    INTERNAL_ZONE_NAMES,
    RESEARCH_PREFIX,
    REVIEWS_PREFIX,
)

# Write-path unsafe chars (null/controls + Windows reserved). Separators handled
# separately: kept as directory structure outside dossier prefixes; flattened to
# ``_`` under 工作稿/research/reviews/debate so nested model paths become one file.
_UNSAFE_IN_SEGMENT = re.compile(r'[\0-\x1f:*?"<>|]')
_UNSAFE_IN_FILENAME = re.compile(r'[\0-\x1f\\/:*?"<>|]+')
_MULTI_UNDERSCORE = re.compile(r"_+")
# Windows reserved device names (case-insensitive): bare ``nul`` / ``CON`` and
# extension forms ``nul.txt``. Neutralized on write sanitize so LocalWorkspace on
# Windows never opens a hanging device path. ``console`` / ``null.txt`` untouched.
_WIN_RESERVED_DEVICE = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$",
    re.IGNORECASE,
)

# Linux NAME_MAX is 255 bytes; keep headroom for encoding edge cases / suffixes.
_MAX_FILENAME_BYTES = 240


def truncate_filename_utf8(
    name: str, *, max_bytes: int = _MAX_FILENAME_BYTES
) -> str:
    """Truncate a single path segment / file name to ``max_bytes`` UTF-8 bytes.

    Preserves a trailing extension when present (``报告.md`` → stem truncated,
    ``.md`` kept). Empty-after-truncate falls back to ``untitled``.
    """
    if not name:
        return name
    if max_bytes < 1:
        return "untitled"
    raw = name.encode("utf-8")
    if len(raw) <= max_bytes:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem or "/" in ext or "\\" in ext:
        stem, ext = name, ""
    else:
        ext = dot + ext
    ext_bytes = ext.encode("utf-8")
    budget = max_bytes - len(ext_bytes)
    if budget < 8:
        # Extension alone eats the budget — hard-cut the whole name.
        cut = raw[:max_bytes].decode("utf-8", errors="ignore").rstrip(" ._")
        return cut or "untitled"
    cut_stem = (
        stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore").rstrip(" ._")
    )
    return (cut_stem or "untitled") + ext

# Longest-first so prefixes nest correctly if layouts ever share a stem.
_DOSSIER_WRITE_PREFIXES: tuple[str, ...] = (
    DRAFTS_PREFIX,
    RESEARCH_PREFIX,
    REVIEWS_PREFIX,
    DEBATE_PREFIX,
)

# --- System noise (AI + user UI) ---
# Directory set ↔ desktop ``LIST_FILES_SKIP_DIRS`` (parity gate).
# Do NOT put bare ``index``/``trash``/``baselines`` here — see ``is_internal_zone_relpath``.
# Runtime / scratch / lockfile-store names (``logs``, ``tmp``, ``vendor``, …) match
# common VCS ignore + code-search practice so BM25/grep are not flooded by ops text.
IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "bower_components",
        "vendor",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".nox",
        ".eggs",
        ".mypy_cache",
        ".pytest_cache",
        ".pytest_tmp",
        ".ruff_cache",
        ".turbo",
        ".cache",
        ".parcel-cache",
        ".pnpm-store",
        "coverage",
        "htmlcov",
        ".idea",
        ".vscode",
        "dist",
        "build",
        ".next",
        ".nuxt",
        ".vite",
        ".svelte-kit",
        ".wrangler",
        "out",
        "target",
        "logs",
        "tmp",
        "temp",
        ".tmp",
    }
)

# Indexes + pure bytecode caches — never useful in the file panel either.
SYSTEM_IGNORED_FILE_SUFFIXES: frozenset[str] = frozenset(
    {
        ".db",
        ".sqlite",
        ".sqlite3",
        ".pyc",
        ".pyo",
    }
)

# --- AI noise (AI views only; user UI must still show these) ---
# ↔ desktop ``AI_NOISE_FILE_SUFFIXES`` (parity gate).
AI_NOISE_FILE_SUFFIXES: frozenset[str] = frozenset(
    {
        ".class",
        ".o",
        ".a",
        ".lib",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".wasm",
        ".bin",
        ".dat",
        ".pack",
        ".idx",
        # Runtime log files (dirs named ``logs`` are system-noise; loose ``*.log`` is AI-only).
        ".log",
        # Columnar / numeric / serialized data blobs (not source text).
        ".parquet",
        ".feather",
        ".arrow",
        ".npy",
        ".h5",
        ".hdf5",
        ".pkl",
        ".pickle",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".bmp",
        ".mp3",
        ".mp4",
        ".wav",
        ".webm",
        ".zip",
        ".tar",
        ".gz",
        ".tgz",
        ".bz2",
        ".7z",
        ".rar",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
    }
)

# Archive subset of AI noise — visible under ``external/<alias>/`` list / when
# ``glob`` pattern targets these suffixes. ↔ desktop ``AI_ARCHIVE_FILE_SUFFIXES``.
AI_ARCHIVE_FILE_SUFFIXES: frozenset[str] = frozenset(
    {
        ".zip",
        ".tar",
        ".gz",
        ".tgz",
        ".bz2",
        ".7z",
        ".rar",
    }
)

# Combined AI-perspective suffixes (system ∪ AI noise). Prefer the tiered helpers
# below; this alias exists for callers / tests that mean "hide from AI".
IGNORED_FILE_SUFFIXES: frozenset[str] = SYSTEM_IGNORED_FILE_SUFFIXES | AI_NOISE_FILE_SUFFIXES

MAX_FILE_BYTES = 2_000_000  # skip files larger than ~2 MB during content scans


def is_ignored_dir_name(name: str) -> bool:
    """Whether a single path segment is a system-noise directory (name-only)."""
    return name in IGNORED_DIRS


def is_access_denied_oserror(exc: BaseException) -> bool:
    """True for per-entry permission / lock refusals (skip; do not fail whole walk).

    Covers POSIX ``EACCES``/``EPERM``/``EBUSY``, Windows WinError 5/32, and common
    localized / English denial strings (e.g. locked ``.pytest_tmp`` on Windows).
    """
    if isinstance(exc, PermissionError):
        return True
    if not isinstance(exc, OSError):
        return False
    winerror = getattr(exc, "winerror", None)
    if winerror in (5, 32):  # ACCESS_DENIED / SHARING_VIOLATION
        return True
    err = getattr(exc, "errno", None)
    if err in {errno.EACCES, errno.EPERM, errno.EBUSY}:
        return True
    text = str(exc).lower()
    return any(
        needle in text
        for needle in (
            "permission denied",
            "access is denied",
            "access denied",
            "拒绝访问",
        )
    )


def is_internal_zone_relpath(relpath: str) -> bool:
    """True when ``relpath`` is ``AgentCore/{index|trash|baselines|versions}`` or under it.

    Path-aware only — bare ``index`` / ``trash`` / ``baselines`` / ``versions``
    elsewhere (user projects) are never treated as internal.
    """
    p = relpath.replace("\\", "/").strip("/")
    if not p or p == ".":
        return False
    for zone in INTERNAL_ZONE_NAMES:
        prefix = f"{AGENTCORE_ROOT}/{zone}"
        if p == prefix or p.startswith(f"{prefix}/"):
            return True
    return False


def path_has_non_internal_entries(root: Path) -> bool:
    """True when ``root`` has content outside ``AgentCore/{index,trash,baselines,versions}``.

    Hub ``has_files`` / lazy index kicks use this so a tree that only holds
    internal zones (or an empty ``AgentCore/``) counts as empty. Does not create
    ``root``. Bare top-level ``index`` / ``trash`` / ``baselines`` / ``versions``
    still count — only the path-aware internal zones are skipped (via
    :func:`is_internal_zone_relpath`).
    """
    if not root.is_dir():
        return False
    try:
        children = list(root.iterdir())
    except OSError:
        return False
    for child in children:
        name = child.name
        if name == AGENTCORE_ROOT and child.is_dir():
            try:
                ac_children = list(child.iterdir())
            except OSError:
                continue
            for ac in ac_children:
                if is_internal_zone_relpath(f"{AGENTCORE_ROOT}/{ac.name}"):
                    continue
                return True
            continue
        return True
    return False


def is_ignored_dir_entry(*, parent_rel: str, name: str) -> bool:
    """Whether a directory child should be pruned during a workspace walk.

    Combines name-only :data:`IGNORED_DIRS` with path-aware internal zones.
    ``parent_rel`` is the workspace-relative POSIX path of the parent
    (``""`` / ``.`` = workspace root).

    Also true when any *ancestor* segment in ``parent_rel`` is in
    :data:`IGNORED_DIRS` — needed for recursive ``**/*`` globs that already
    descended into a noise dir (walk-based callers prune earlier and never
    hit this).
    """
    if name in IGNORED_DIRS:
        return True
    parent = parent_rel.replace("\\", "/").strip("/")
    if parent and parent != ".":
        for seg in parent.split("/"):
            if seg and is_ignored_dir_name(seg):
                return True
    child = name if parent in ("", ".") else f"{parent}/{name}"
    return is_internal_zone_relpath(child)


def _suffix_match(name: str, suffixes: frozenset[str]) -> bool:
    lower = name.lower()
    return any(lower.endswith(suf) for suf in suffixes)


def is_system_ignored_file_name(name: str) -> bool:
    """Whether a file basename is system noise (hidden from UI and AI)."""
    return _suffix_match(name, SYSTEM_IGNORED_FILE_SUFFIXES)


def is_ai_noise_file_name(name: str) -> bool:
    """Whether a file basename is AI-only noise (still visible in user UI)."""
    return _suffix_match(name, AI_NOISE_FILE_SUFFIXES)


def is_ai_archive_file_name(name: str) -> bool:
    """Whether a file basename is an archive suffix (AI-noise subset)."""
    return _suffix_match(name, AI_ARCHIVE_FILE_SUFFIXES)


def is_ignored_file_name(name: str) -> bool:
    """Whether a file basename should be omitted from AI listings / indexes."""
    return _suffix_match(name, IGNORED_FILE_SUFFIXES)


def is_ignored_relpath(relpath: str) -> bool:
    """Whether a workspace-relative POSIX path should be omitted from AI listings.

    True when the path is under an AgentCore internal zone, any directory
    segment is in :data:`IGNORED_DIRS`, or the final file name matches
    :data:`IGNORED_FILE_SUFFIXES` (system ∪ AI noise).
    """
    if is_internal_zone_relpath(relpath):
        return True
    parts = [p for p in relpath.replace("\\", "/").split("/") if p and p != "."]
    if not parts:
        return False
    *dirs, name = parts
    if any(is_ignored_dir_name(d) for d in dirs):
        return True
    return is_ignored_file_name(name)


def strip_root_label_prefix(relative_path: str, root_label: str) -> str:
    """Rewrite a ``/<root_label>/…`` absolute input to its workspace-relative form.

    Models routinely emit absolute sandbox-style paths (``/workspace/research/x.md``)
    because ``/workspace`` is the de-facto sandbox root and the system prompt names the
    root ``workspace``. Every such absolute path is **already rejected** by
    :func:`resolve_safe_path` (joining an absolute path against the root escapes it),
    which costs a worker 2–5 retry rounds and can trip the file-tool circuit breaker.
    This maps only that otherwise-doomed shape back to the equivalent relative path so
    it flows through the *unchanged* containment guard:

    * ``/<root_label>/foo/bar.md`` → ``foo/bar.md``
    * ``/<root_label>`` (the root itself) → ``.``
    * everything else is returned verbatim — a genuine relative path (even one whose
      first segment coincidentally equals ``root_label``, e.g. ``workspace/foo``), or
      an absolute path under a *different* first segment (``/etc/passwd``).

    Security contract: this NEVER widens access. It only rewrites strings the guard
    would have refused, and it does not defuse traversal — ``/<root_label>/../x``
    becomes ``../x`` and is still rejected downstream by containment.
    """
    if not root_label:
        return relative_path
    normalized = relative_path.replace("\\", "/")
    if not normalized.startswith("/"):
        return relative_path  # relative input — its behavior must not change
    first, _sep, rest = normalized.lstrip("/").partition("/")
    if first != root_label:
        return relative_path  # a different absolute root — leave it to be rejected
    return rest if rest else "."


def _neutralize_win_reserved_segment(segment: str) -> str:
    """Prefix Windows reserved device names so they become ordinary file names.

    ``nul`` → ``_nul``, ``NUL.txt`` → ``_NUL.txt``. Idempotent when already
    prefixed (``_nul`` does not match the device regex).
    """
    if _WIN_RESERVED_DEVICE.match(segment):
        return f"_{segment}"
    return segment


def _finalize_cleaned_name(cleaned: str, *, empty_fallback: str) -> str:
    """Keep meaningful leading ``_`` / ``.``; strip only junk / Windows-dangerous tails.

    Leading underscores (``_inventory``) and hidden-file dots (``.gitignore``) are
    intentional names — never strip them. Trailing spaces / dots are still removed
    (Windows forbids them) from the whole name **and** from the stem so a truncated
    ``GoWindowsCard.tsx`` cannot land as ``GoWindowsCard..md``. Consecutive
    underscores stay collapsed by callers.
    """
    cleaned = cleaned.lstrip(" ").rstrip(" .")
    if not cleaned:
        cleaned = empty_fallback
    stem, dot, ext = cleaned.rpartition(".")
    if dot and stem and "/" not in ext and "\\" not in ext:
        # Dots/spaces only — a trailing ``_`` is often an unsafe-char substitute
        # (``foo?.md`` → ``foo_.md``) and must stay.
        stem = stem.rstrip(" .")
        cleaned = (stem or empty_fallback) + dot + ext
    return truncate_filename_utf8(cleaned or empty_fallback)


def clean_path_segment(segment: str, *, empty_fallback: str = "_") -> str:
    """Strip reserved chars from one path segment (directory or file name).

    Public because cloud folder names (``cloud_tree``) must land on disk under the
    same rules model-supplied write paths do — one sanitizer, not two.
    """
    cleaned = _UNSAFE_IN_SEGMENT.sub("_", segment)
    cleaned = _MULTI_UNDERSCORE.sub("_", cleaned)
    cleaned = _finalize_cleaned_name(cleaned, empty_fallback=empty_fallback)
    return _neutralize_win_reserved_segment(cleaned)


def _clean_path_segment(segment: str) -> str:
    return clean_path_segment(segment)


def _clean_dossier_filename(rest: str) -> str:
    """Flatten everything after a dossier prefix into one safe file name."""
    cleaned = _UNSAFE_IN_FILENAME.sub("_", rest.replace("\\", "/"))
    cleaned = _MULTI_UNDERSCORE.sub("_", cleaned)
    cleaned = _finalize_cleaned_name(cleaned, empty_fallback="untitled")
    return _neutralize_win_reserved_segment(cleaned)


def sanitize_write_relpath(
    relative_path: str, *, root_label: str | None = "workspace"
) -> str:
    """Sanitize a model-supplied write path before landing on disk.

    * Dangerous characters (controls, ``:*?"<>|``) → ``_``.
    * Windows reserved device names (``nul`` / ``CON`` / ``nul.txt`` / …) get a
      leading ``_`` so they never land as hanging Win32 device paths.
    * Under dossier prefixes (``工作稿`` / ``research`` / ``reviews`` / ``debate``),
      everything after the prefix is treated as a **single file name**: nested
      ``/`` ``\\`` become ``_`` so ``…/research/a/b.md`` → ``…/research/a_b.md``.
    * Each file / segment name is capped to ``_MAX_FILENAME_BYTES`` UTF-8 bytes
      (below Linux ``NAME_MAX``) so model-supplied angle titles cannot raise
      ``ENAMETOOLONG``.
    * Elsewhere, directory structure is preserved; each segment is cleaned.
    * ``..`` segments are left intact so the containment guard still rejects them.
    * Empty / ``.`` inputs are returned unchanged (callers validate required paths).
    * ``/<root_label>/…`` is rewritten to workspace-relative first so sandbox
      absolutes still land correctly (and dossier flatten sees the relative form).
    * Other absolute paths keep a leading ``/`` so containment can still refuse them.
    """
    if not relative_path or relative_path == ".":
        return relative_path
    unified = relative_path.replace("\\", "/").strip()
    if not unified or unified == ".":
        return relative_path if not (relative_path or "").strip() else "."
    if unified == "/":
        return "."
    # Same rewrite resolve_safe_path applies: ``/workspace/foo`` → ``foo``.
    if root_label:
        unified = strip_root_label_prefix(unified, root_label)
        if unified in (".", ""):
            return "."
    raw = unified.replace("\\", "/").strip()
    if not raw or raw == ".":
        return "."

    for prefix in _DOSSIER_WRITE_PREFIXES:
        bare = prefix.rstrip("/")
        if raw in (bare, prefix):
            return bare
        if raw.startswith(prefix):
            rest = raw[len(prefix) :]
            if not rest or rest in (".", "/"):
                return bare
            return f"{prefix}{_clean_dossier_filename(rest)}"

    absolute = raw.startswith("/")
    parts = [p for p in raw.split("/") if p and p != "."]
    if not parts:
        return "/" if absolute else "."
    cleaned: list[str] = []
    for part in parts:
        if part == "..":
            cleaned.append("..")
            continue
        cleaned.append(_clean_path_segment(part))
    joined = "/".join(cleaned)
    return f"/{joined}" if absolute else joined


def normalize_workspace_path(
    relative_path: str, *, root_label: str | None = None
) -> str:
    """Normalize a model-facing tool path to workspace-relative POSIX form.

    Single source of truth for the path contract (desktop ``pathGuard`` mirrors):

    * empty / ``.`` → ``.``
    * bare ``/`` or ``\\`` → ``.`` (whole-workspace root aliases)
    * ``/<root_label>/…`` → strip via :func:`strip_root_label_prefix` when label given
    * other absolute paths (``/etc``, drive letters, …) left for containment to reject
    * relative paths: separators unified to ``/``

    Called **only** from :func:`resolve_safe_path` on the Python side (and from
    ``LocalWorkspace`` before the desktop channel). Tools must not add private
    ``if path == "/"`` rescues.
    """
    if not relative_path or relative_path == ".":
        return "."
    unified = relative_path.replace("\\", "/")
    if unified == "/":
        return "."
    if root_label:
        return strip_root_label_prefix(unified, root_label)
    return unified


def resolve_safe_path(
    workspace: Path, relative_path: str, *, root_label: str | None = None
) -> Path | None:
    """Resolve ``relative_path`` against ``workspace``, refusing escapes.

    Returns the resolved absolute path when it stays inside ``workspace`` (or is
    the workspace root itself), or ``None`` when the path traverses outside it
    (``..``, an absolute path, a prefix sibling like ``workspace-evil``) or
    cannot be resolved. This is the single source of truth for the workspace
    sandbox boundary — every filesystem operation must route through it.

    Paths are first passed through :func:`normalize_workspace_path` (bare ``/`` /
    ``\\`` → ``.``; optional ``/<root_label>/…`` strip) then the same containment
    check — so aliases never widen access (``..`` / other-root absolutes still fail).
    """
    relative_path = normalize_workspace_path(relative_path, root_label=root_label)
    try:
        resolved = (workspace / relative_path).resolve()
        root = workspace.resolve()
        # Containment via the ancestor chain — NOT a string prefix, which would
        # wrongly accept a sibling dir sharing the workspace name as a prefix.
        if resolved != root and root not in resolved.parents:
            return None
        return resolved
    except (ValueError, OSError):
        return None


def normalize_glob(glob_pat: str) -> str | None:
    """Reduce a (possibly path-qualified) glob to a file-NAME pattern.

    We filter by file name only, so ``**/*.py`` and ``src/*.ts`` both collapse to
    their trailing name component (``*.py`` / ``*.ts``). Returns ``None`` for an
    empty filter.
    """
    p = glob_pat.strip().replace("\\", "/")
    if not p:
        return None
    if p.startswith("**/"):
        p = p[3:]
    if "/" in p:
        p = p.rsplit("/", 1)[-1]
    return p or None


def read_text_file(path: Path) -> str | None:
    """Read a regular text file, or ``None`` to skip it.

    Skips symlinks (avoids following links out of the tree or into loops),
    non-regular files, oversized files, and anything that isn't valid UTF-8 text
    (a cheap, reliable binary filter).
    """
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
    except OSError:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None
