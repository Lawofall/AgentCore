"""Import-graph integrity for SPA / TS landings (``graph_consistent`` criteria).

Scans delivered source for relative / ``@/`` imports; missing targets → gaps.
Used by :func:`~agentcore.runtime.graph.drive.completion.collect_completion_soft_notes`.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

_SOURCE_SUFFIXES = frozenset({".ts", ".tsx", ".vue", ".js", ".jsx"})
_RESOLVE_SUFFIXES = (".ts", ".tsx", ".vue", ".js", ".jsx", "")

# from '…' / from "…" / import('…') / import("…") — skip bare package names.
_IMPORT_RE = re.compile(
    r"""(?:from\s+|import\s*\(\s*)(['"])([^'"]+)\1""",
    re.MULTILINE,
)


def is_graph_source_path(path: str) -> bool:
    suffix = PurePosixPath(path.replace("\\", "/")).suffix.lower()
    return suffix in _SOURCE_SUFFIXES


def _normalize_ws_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _dir_of(path: str) -> str:
    p = PurePosixPath(_normalize_ws_path(path))
    parent = str(p.parent)
    return "" if parent == "." else parent


def _join_rel(base_dir: str, rel: str) -> str:
    # Strip query/hash (rare in source imports).
    rel = rel.split("?", 1)[0].split("#", 1)[0]
    if rel.startswith("@/"):
        # ``@/`` → ``src/`` under the nearest project root heuristic:
        # if the importer lives under ``…/src/…``, map to that src tree;
        # else prefix ``src/``.
        rest = rel[2:]
        parts = PurePosixPath(_normalize_ws_path(base_dir or ".")).parts
        if "src" in parts:
            idx = parts.index("src")
            root = "/".join(parts[: idx + 1])
            return _normalize_ws_path(f"{root}/{rest}")
        return _normalize_ws_path(f"src/{rest}")
    # Relative
    base = PurePosixPath(base_dir) if base_dir else PurePosixPath(".")
    joined = (base / rel).as_posix()
    # Normalize .. segments
    return _normalize_ws_path(str(PurePosixPath(joined)))


def extract_local_imports(source: str, *, importer_path: str) -> list[str]:
    """Return workspace-relative import targets (unresolved extensions)."""
    base_dir = _dir_of(importer_path)
    out: list[str] = []
    seen: set[str] = set()
    for match in _IMPORT_RE.finditer(source or ""):
        spec = match.group(2).strip()
        if not spec:
            continue
        # Only relative and alias imports — skip bare packages (vue, react, …).
        if not (spec.startswith(".") or spec.startswith("@/")):
            continue
        target = _join_rel(base_dir, spec)
        if target and target not in seen:
            seen.add(target)
            out.append(target)
    return out


def _candidate_paths(target: str) -> list[str]:
    """Expand a bare import target to concrete file candidates."""
    t = _normalize_ws_path(target)
    # Already has a known suffix → try as-is only (+ nothing else needed).
    suffix = PurePosixPath(t).suffix.lower()
    candidates: list[str] = []
    if suffix in _SOURCE_SUFFIXES:
        candidates.append(t)
        return candidates
    for ext in _RESOLVE_SUFFIXES:
        if ext:
            candidates.append(f"{t}{ext}")
        else:
            candidates.append(t)
    # index.* under the directory
    for ext in (".ts", ".tsx", ".vue", ".js", ".jsx"):
        candidates.append(f"{t}/index{ext}")
    return candidates


def resolve_missing_imports(
    files: dict[str, str],
) -> list[str]:
    """Return missing import targets (first candidate path form) across ``files``.

    ``files`` maps workspace-relative path → UTF-8 text. Keys are the known
    delivered set; a target resolves if any candidate path is in ``files``
    (case-sensitive posix).
    """
    known = {_normalize_ws_path(p) for p in files}
    missing: list[str] = []
    seen_miss: set[str] = set()
    for path, content in files.items():
        if not is_graph_source_path(path):
            continue
        for target in extract_local_imports(content, importer_path=path):
            candidates = _candidate_paths(target)
            if any(c in known for c in candidates):
                continue
            # Prefer the most likely display path (with .ts / .vue if bare).
            display = candidates[0] if candidates else target
            if display not in seen_miss:
                seen_miss.add(display)
                missing.append(display)
    return missing


def format_graph_gap(missing: list[str]) -> str:
    if not missing:
        return ""
    listed = "、".join(f"`{p}`" for p in missing[:16])
    extra = f" 等 {len(missing)} 处" if len(missing) > 16 else ""
    return (
        f"import 图不闭合：缺文件 {listed}{extra}"
        "（相对路径 / `@/`→src；须同批落盘或修正引用）"
    )


async def load_source_file_map(
    backend: Any,
    paths: list[str],
) -> dict[str, str]:
    """Read delivered source texts from workspace backend (best-effort)."""
    out: dict[str, str] = {}
    if backend is None:
        return out
    for path in paths:
        if not path or not is_graph_source_path(path):
            continue
        key = _normalize_ws_path(path)
        try:
            text = await backend.read(path)
        except Exception:  # noqa: BLE001 — missing / binary / IO → treat as unread
            continue
        if isinstance(text, str):
            out[key] = text
    return out


def load_source_file_map_sync(
    backend: Any,
    paths: list[str],
) -> dict[str, str]:
    """Sync FS read when backend exposes ``_root`` (ServerWorkspace); else empty.

    Local-channel backends need the async path (:func:`load_source_file_map`).
    """
    out: dict[str, str] = {}
    root = getattr(backend, "_root", None) if backend is not None else None
    if root is None:
        return out
    from pathlib import Path

    root_path = Path(root)
    for path in paths:
        if not path or not is_graph_source_path(path):
            continue
        key = _normalize_ws_path(path)
        try:
            target = root_path / key
            if not target.is_file():
                continue
            out[key] = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return out
