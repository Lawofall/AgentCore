"""Workspace hide-rule parity: Python ↔ desktop TypeScript.

Two families of hand-maintained lists, each mirrored across processes (双模式工作区
· 系统文件隐藏). This module extracts them from every source file and fails when
they diverge — no codegen, minimal ratchet (same spirit as DebateForm member
snapshot).

* **Ignore sets** — ``_paths.py`` ↔ ``main/fs/workspaceIgnore.ts``: the noise
  directory set plus the three suffix tiers. Renderer ``lib/folderUpload.ts``
  hand-copies the two *system* tiers (dirs + system suffixes) to filter folder
  uploads, and is checked against the same Python source.
* **Internal zones** — ``stage_dirs.py`` ↔ ``main/fs/workspaceIgnore.ts`` ↔ the
  inline copy inside renderer ``services/sources/workspaceSource.ts``: the zone
  name set, the ``AgentCore/<zone>`` path form behind every ``*_REL``, and the
  invariant that bare zone names never leak into the global dir-skip set (zones
  are path-aware on purpose — a bare ``index`` would hide user project folders).

Run::

    uv run python scripts/check_workspace_ignore_parity.py
    uv run python scripts/check_workspace_ignore_parity.py --simulate-drift
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_SERVER_ROOT = Path(__file__).resolve().parents[2]
_WORKSPACE_PKG = Path(__file__).resolve().parent
_DESKTOP_SRC = _SERVER_ROOT.parent / "desktop" / "src"
_PY_PATHS = _WORKSPACE_PKG / "_paths.py"
_PY_STAGE_DIRS = _WORKSPACE_PKG / "stage_dirs.py"
_TS_IGNORE = _DESKTOP_SRC / "main" / "fs" / "workspaceIgnore.ts"
# Renderer keeps its own inline zone copy (no import from the main-process
# module), so it drifts silently unless the gate reads it too.
_TS_RENDERER_ZONES = _DESKTOP_SRC / "renderer" / "services" / "sources" / "workspaceSource.ts"
# Folder upload filters in the renderer (before any request), so it needs its own
# copy of the system tiers — same no-main-import reason, same silent-drift risk.
_TS_RENDERER_UPLOAD = _DESKTOP_SRC / "renderer" / "lib" / "folderUpload.ts"

# (label, path) — printed by the CLI and existence-checked before comparing.
_SOURCES: tuple[tuple[str, Path], ...] = (
    ("python", _PY_PATHS),
    ("python (zones)", _PY_STAGE_DIRS),
    ("typescript", _TS_IGNORE),
    ("typescript (renderer zones)", _TS_RENDERER_ZONES),
    ("typescript (renderer upload)", _TS_RENDERER_UPLOAD),
)

# (label, python name, typescript name, ts kind)
_SETS: tuple[tuple[str, str, str, str], ...] = (
    ("dirs", "IGNORED_DIRS", "LIST_FILES_SKIP_DIRS", "set"),
    (
        "system_suffixes",
        "SYSTEM_IGNORED_FILE_SUFFIXES",
        "SYSTEM_IGNORED_FILE_SUFFIXES",
        "array",
    ),
    (
        "ai_noise_suffixes",
        "AI_NOISE_FILE_SUFFIXES",
        "AI_NOISE_FILE_SUFFIXES",
        "array",
    ),
    (
        "ai_archive_suffixes",
        "AI_ARCHIVE_FILE_SUFFIXES",
        "AI_ARCHIVE_FILE_SUFFIXES",
        "array",
    ),
    (
        "ai_image_suffixes",
        "AI_IMAGE_FILE_SUFFIXES",
        "AI_IMAGE_FILE_SUFFIXES",
        "array",
    ),
)

# The renderer upload copy only mirrors the *system* tiers: AI-noise suffixes are
# an AI-view rule, and a user uploading their own png / zip must not lose it.
_RENDERER_UPLOAD_SETS: tuple[tuple[str, str, str, str], ...] = (
    ("dirs", "IGNORED_DIRS", "LIST_FILES_SKIP_DIRS", "set"),
    (
        "system_suffixes",
        "SYSTEM_IGNORED_FILE_SUFFIXES",
        "SYSTEM_IGNORED_FILE_SUFFIXES",
        "array",
    ),
)

_STRING_LIT = re.compile(r'"([^"]+)"')
_PY_COMMENT = re.compile(r"#[^\n]*")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Module-level ``NAME = "value"``. Plain strings only: f-strings are handled by
# the ``*_REL`` patterns below, which resolve their placeholders.
_PY_STR_CONST = re.compile(r'^([A-Z][A-Z0-9_]*)\s*=\s*"([^"]*)"', re.MULTILINE)
_PY_REL_CONST = re.compile(r'^([A-Z][A-Z0-9_]*_REL)\s*=\s*f"([^"]*)"', re.MULTILINE)
_TS_STR_CONST = re.compile(r'^export const ([A-Z][A-Z0-9_]*)\s*=\s*"([^"]*)"\s*;', re.MULTILINE)
_TS_REL_CONST = re.compile(
    r"^export const ([A-Z][A-Z0-9_]*_REL)\s*=\s*([`\"'])(.*?)\2\s*;", re.MULTILINE
)
# Python f-string ``{X}`` and TypeScript template ``${X}`` in one shape.
_PLACEHOLDER = re.compile(r"\$?\{([A-Za-z_][A-Za-z0-9_]*)\}")
# The renderer's inline zone copy: ``for (const zone of [...] as const) {
# const prefix = `AgentCore/${zone}`;``.
_RENDERER_ZONE_LOOP = re.compile(
    r"for\s*\(\s*const\s+(\w+)\s+of\s*\[(.*?)\]\s*as const\s*\)\s*\{\s*"
    r"const\s+\w+\s*=\s*`([^`]*)`",
    re.DOTALL,
)


@dataclass(frozen=True)
class IgnoreParityResult:
    ok: bool
    errors: list[str]
    sources: tuple[tuple[str, Path], ...]


@dataclass(frozen=True)
class ZoneCopy:
    """One source's view of the internal zones."""

    zone_names: frozenset[str]
    rel_paths: frozenset[str]


def py_paths_file() -> Path:
    return _PY_PATHS


def ts_ignore_file() -> Path:
    return _TS_IGNORE


def stage_dirs_file() -> Path:
    return _PY_STAGE_DIRS


def renderer_zones_file() -> Path:
    return _TS_RENDERER_ZONES


def renderer_upload_file() -> Path:
    return _TS_RENDERER_UPLOAD


def _quoted_strings(block: str) -> frozenset[str]:
    return frozenset(_STRING_LIT.findall(block))


def _resolve_placeholders(template: str, consts: Mapping[str, str], *, where: str) -> str:
    missing: list[str] = []

    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in consts:
            missing.append(name)
            return m.group(0)
        return consts[name]

    resolved = _PLACEHOLDER.sub(repl, template)
    if missing:
        raise ValueError(f"{where}: unresolved constant(s) {sorted(set(missing))}")
    return resolved


def _python_frozenset_body(src: str, name: str, *, where: str) -> str:
    m = re.search(
        rf"{re.escape(name)}\s*:\s*frozenset\[str\]\s*=\s*frozenset\(\s*\{{(.*?)\}}\s*\)",
        src,
        flags=re.DOTALL,
    )
    if not m:
        raise ValueError(f"Python set {name!r} not found in {where}")
    return m.group(1)


def extract_python_set(src: str, name: str, *, where: str | None = None) -> frozenset[str]:
    """Pull members from ``NAME: frozenset[str] = frozenset({...})``."""
    return _quoted_strings(_python_frozenset_body(src, name, where=where or _PY_PATHS.name))


def extract_typescript_set(
    src: str, name: str, *, kind: str, where: str | None = None
) -> frozenset[str]:
    """Pull members from ``export const NAME = new Set([...])`` or ``[…] as const``."""
    if kind == "set":
        pat = rf"export const {re.escape(name)}\s*=\s*new Set\(\[(.*?)\]\)"
    elif kind == "array":
        pat = rf"export const {re.escape(name)}\s*=\s*\[(.*?)\]\s*as const"
    else:
        raise ValueError(f"unknown ts kind {kind!r}")
    m = re.search(pat, src, flags=re.DOTALL)
    if not m:
        raise ValueError(
            f"TypeScript {kind} {name!r} not found in {where or _TS_IGNORE.name}"
        )
    return _quoted_strings(m.group(1))


def _rel_paths(root: str, zones: frozenset[str]) -> frozenset[str]:
    """The canonical ``AgentCore/<zone>`` form every ``*_REL`` must expand to."""
    return frozenset(f"{root}/{zone}" for zone in zones)


def extract_python_zone_copy(stage_src: str) -> ZoneCopy:
    """Zones from ``stage_dirs.py``: the name set + every ``*_REL`` expansion.

    Members and ``*_REL`` bodies are written as constant references
    (``frozenset({INDEX_ZONE_NAME, …})`` / ``f"{AGENTCORE_ROOT}/{INDEX_ZONE_NAME}"``),
    so both are resolved against the module's plain string constants.
    """
    where = _PY_STAGE_DIRS.name
    consts = dict(_PY_STR_CONST.findall(stage_src))
    block = _PY_COMMENT.sub(
        "", _python_frozenset_body(stage_src, "INTERNAL_ZONE_NAMES", where=where)
    )
    literals = _quoted_strings(block)
    refs = set(_IDENT.findall(_STRING_LIT.sub("", block)))
    unknown = sorted(r for r in refs if r not in consts)
    if unknown:
        raise ValueError(f"{where} INTERNAL_ZONE_NAMES: unresolved {unknown}")
    rels = {
        name: _resolve_placeholders(body, consts, where=f"{where} {name}")
        for name, body in _PY_REL_CONST.findall(stage_src)
    }
    return ZoneCopy(
        zone_names=frozenset(literals | {consts[r] for r in refs}),
        rel_paths=frozenset(rels.values()),
    )


def extract_typescript_zone_copy(ts_src: str) -> ZoneCopy:
    """Zones from ``main/fs/workspaceIgnore.ts`` (``INTERNAL_ZONE_NAMES`` + ``*_REL``)."""
    consts = dict(_TS_STR_CONST.findall(ts_src))
    rels = {
        name: _resolve_placeholders(body, consts, where=f"{_TS_IGNORE.name} {name}")
        for name, _quote, body in _TS_REL_CONST.findall(ts_src)
    }
    return ZoneCopy(
        zone_names=extract_typescript_set(ts_src, "INTERNAL_ZONE_NAMES", kind="set"),
        rel_paths=frozenset(rels.values()),
    )


def extract_renderer_zone_copy(renderer_src: str) -> ZoneCopy:
    """Zones from the renderer's inline ``isInternalZonePath`` loop.

    The renderer deliberately does not import the main-process constants (no
    main↔renderer module edge), so this copy is the easiest one to forget.
    """
    m = _RENDERER_ZONE_LOOP.search(renderer_src)
    if not m:
        raise ValueError(
            f"inline zone loop not found in {_TS_RENDERER_ZONES.name} — if that "
            "copy moved or now imports shared constants, update _SOURCES here"
        )
    loop_var, array_body, prefix_template = m.groups()
    zones = _quoted_strings(array_body)
    placeholder = f"${{{loop_var}}}"
    return ZoneCopy(
        zone_names=zones,
        rel_paths=frozenset(prefix_template.replace(placeholder, z) for z in zones),
    )


def _diff_line(
    label: str,
    only_left: frozenset[str],
    only_right: frozenset[str],
    *,
    left: str = "Python",
    right: str = "TypeScript",
) -> str | None:
    if not only_left and not only_right:
        return None
    parts: list[str] = [f"{label}:"]
    if only_left:
        parts.append(f" only-in-{left}={sorted(only_left)}")
    if only_right:
        parts.append(f" only-in-{right}={sorted(only_right)}")
    return "".join(parts)


def compare_zone_sources(
    *,
    py_src: str,
    stage_src: str,
    ts_src: str,
    renderer_src: str,
) -> list[str]:
    """Internal-zone parity across the three hand-maintained copies.

    Checks the zone name set, the ``AgentCore/<zone>`` path form behind every
    ``*_REL`` constant, and that no bare zone name leaked into the global
    dir-skip sets (which would hide same-named folders in user projects).
    """
    errors: list[str] = []
    py = extract_python_zone_copy(stage_src)
    ts = extract_typescript_zone_copy(ts_src)
    renderer = extract_renderer_zone_copy(renderer_src)
    if not py.zone_names:
        errors.append("zone_names: Python set is empty (parse failure?)")
        return errors

    for side, other in (("desktop main", ts), ("renderer inline copy", renderer)):
        line = _diff_line(
            f"zone_names (python ↔ {side})",
            py.zone_names - other.zone_names,
            other.zone_names - py.zone_names,
            right=side,
        )
        if line:
            errors.append(line)

    py_root = dict(_PY_STR_CONST.findall(stage_src)).get("AGENTCORE_ROOT")
    ts_root = dict(_TS_STR_CONST.findall(ts_src)).get("AGENTCORE_ROOT")
    if not py_root or not ts_root:
        errors.append("zone_root: AGENTCORE_ROOT not found on both sides")
        return errors
    if py_root != ts_root:
        errors.append(f"zone_root: Python={py_root!r} TypeScript={ts_root!r}")
        return errors

    # Path form: every ``*_REL`` (and the renderer's inline prefix) must expand
    # to exactly one ``AgentCore/<zone>`` per zone — no missing or stray zone.
    expected = _rel_paths(py_root, py.zone_names)
    for side, copy in (
        ("python", py),
        ("desktop main", ts),
        ("renderer inline copy", renderer),
    ):
        line = _diff_line(
            f"zone_rel_paths ({side})",
            expected - copy.rel_paths,
            copy.rel_paths - expected,
            left="expected",
            right="found",
        )
        if line:
            errors.append(line)

    py_dirs = extract_python_set(py_src, "IGNORED_DIRS")
    ts_dirs = extract_typescript_set(ts_src, "LIST_FILES_SKIP_DIRS", kind="set")
    for side, leaked in (
        ("Python IGNORED_DIRS", py.zone_names & py_dirs),
        ("TypeScript LIST_FILES_SKIP_DIRS", ts.zone_names & ts_dirs),
    ):
        if leaked:
            errors.append(
                f"zone_names: bare {sorted(leaked)} leaked into {side} — zones are "
                "path-aware on purpose (a bare name hides user project folders)"
            )
    return errors


def compare_upload_sources(*, py_src: str, upload_src: str) -> list[str]:
    """System-tier parity for the renderer's folder-upload filter copy."""
    errors: list[str] = []
    where = _TS_RENDERER_UPLOAD.name
    for label, py_name, ts_name, ts_kind in _RENDERER_UPLOAD_SETS:
        py_set = extract_python_set(py_src, py_name)
        ts_set = extract_typescript_set(upload_src, ts_name, kind=ts_kind, where=where)
        line = _diff_line(
            f"{label} (python ↔ renderer upload)",
            py_set - ts_set,
            ts_set - py_set,
            right="renderer upload",
        )
        if line:
            errors.append(line)
    return errors


def compare_sources(
    py_src: str,
    ts_src: str,
    *,
    stage_src: str,
    renderer_src: str,
    upload_src: str,
    simulate_drift: bool = False,
) -> list[str]:
    """Return human-readable mismatch lines (empty ⇒ aligned)."""
    errors: list[str] = []
    for label, py_name, ts_name, ts_kind in _SETS:
        py_set = extract_python_set(py_src, py_name)
        ts_set = extract_typescript_set(ts_src, ts_name, kind=ts_kind)
        if simulate_drift and label == "dirs":
            # Inject a phantom member on the TS side so the gate must fail.
            ts_set = ts_set | {"__parity_drift_probe__"}
        line = _diff_line(label, py_set - ts_set, ts_set - py_set)
        if line:
            errors.append(line)
        if not py_set:
            errors.append(f"{label}: Python set is empty (parse failure?)")
        if not ts_set and not simulate_drift:
            errors.append(f"{label}: TypeScript set is empty (parse failure?)")
    errors.extend(compare_upload_sources(py_src=py_src, upload_src=upload_src))
    errors.extend(
        compare_zone_sources(
            py_src=py_src,
            stage_src=stage_src,
            ts_src=ts_src,
            renderer_src=renderer_src,
        )
    )
    return errors


def run_ignore_parity(*, simulate_drift: bool = False) -> IgnoreParityResult:
    """Compare on-disk hide rules; ``simulate_drift`` expects failure."""
    missing = [f"missing source: {path}" for _, path in _SOURCES if not path.is_file()]
    if missing:
        return IgnoreParityResult(ok=False, errors=missing, sources=_SOURCES)
    try:
        errors = compare_sources(
            _PY_PATHS.read_text(encoding="utf-8"),
            _TS_IGNORE.read_text(encoding="utf-8"),
            stage_src=_PY_STAGE_DIRS.read_text(encoding="utf-8"),
            renderer_src=_TS_RENDERER_ZONES.read_text(encoding="utf-8"),
            upload_src=_TS_RENDERER_UPLOAD.read_text(encoding="utf-8"),
            simulate_drift=simulate_drift,
        )
    except ValueError as e:
        errors = [str(e)]
    return IgnoreParityResult(ok=not errors, errors=errors, sources=_SOURCES)
