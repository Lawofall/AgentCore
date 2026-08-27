"""Gate: event payload leaf fields with no production consumer (ratchet).

Independent of the event-name orphan gate in ``consumer_orphan_gate.py`` (that
gate's match regex is intentionally untouched).

Criterion: a **leaf name** (last path segment of a field slot walked from
``SSEPayloadMap`` in ``events.generated.ts``) has zero hits in production
consumer code. Not an AST-level ``(event, field)`` pair — that inventory is
known incomplete and is not this gate.

Hits are token-level after string-aware comment stripping, so these reads count:

* property access ``.x`` and quoted ``"x"`` / ``'x'``
* destructuring ``const { x } = p``
* rename copies ``{ x: renamed }``
* whole-payload store later read as ``p.x`` elsewhere in the scan surface
* ``INTERACTION_KIND_WIRE.idField`` values (dynamic ``payload[wire.idField]``)

Scan surface: desktop / mobile / admin ``src``,
``protocol-fold-kit/src``. Tests and JSON fixtures are excluded.

Stock unread names are grouped in ``field_consumer_baseline.py``. The gate only
fails on **new** unread leaf names. Do not delete contract fields to go green.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from agentcore.conformance.field_consumer_baseline import (
    FIELD_CONSUMER_BASELINE,
    baseline_leaf_names,
    duplicate_baseline_leaves,
)

_EVENTS_REL: Final = "packages/contract-types/src/events.generated.ts"
_IDFIELD_REL: Final = "packages/contract-types/src/interactionKinds.generated.ts"

_SCAN_ROOTS: Final[tuple[str, ...]] = (
    "apps/desktop/src",
    "apps/mobile/src",
    "apps/admin/src",
    "packages/protocol-fold-kit/src",
)
_SCAN_EXTS: Final[frozenset[str]] = frozenset(
    {".ts", ".tsx", ".js", ".jsx", ".mts", ".cts"}
)
_TEST_DIR_NAMES: Final[frozenset[str]] = frozenset({"__tests__", "__test__"})
_SKIP_NAME_MARKERS: Final[tuple[str, ...]] = (".test.", ".spec.", ".stories.")

_SKIP_TYPE_NAMES: Final[frozenset[str]] = frozenset({"SSEEvent", "SSEPayloadMap"})
_PRIMITIVE_TYPES: Final[frozenset[str]] = frozenset(
    {"string", "number", "boolean", "unknown", "null", "true", "false", "Record", "Array"}
)

_INTERFACE_RE = re.compile(r"export interface (\w+)(?:\s+extends\s+(\w+))?\s*\{")
_TYPE_ALIAS_RE = re.compile(r"export type (\w+)\s*=")
_INLINE_UNION_MEMBER_RE = re.compile(r"\{\s*([^}]*)\s*\}")
_FIELD_RE = re.compile(r"(?:^|\n)\s{2}([A-Za-z_][A-Za-z0-9_]*)\??:\s*([^;]+);")
_INLINE_FIELD_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\??:\s*([^;]+)")
_PAYLOAD_MAP_RE = re.compile(r"export type SSEPayloadMap = \{([\s\S]*?)\n\};")
_MAP_ENTRY_RE = re.compile(
    r'(?:^|\n)\s+(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_.]*))\s*:\s*(\w+);'
)
_TYPE_NAME_RE = re.compile(r"\b([A-Z][A-Za-z0-9]*)\b")
_IDFIELD_RE = re.compile(r'idField:\s*"([^"]+)"')
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_QUOTED_RE = re.compile(r"""(?<!\\)(["'])(?:\\.|(?!\1).)*\1""")

_WORKFLOW_HINT = (
    "  Next: read this leaf in desktop/mobile/admin/fold-kit production code, "
    "or add a baseline group with a factual reason in "
    "agentcore/conformance/field_consumer_baseline.py. "
    "Do not delete the contract field without a human decision."
)


@dataclass(frozen=True, slots=True)
class FieldSlot:
    event: str
    path: str  # dotted path under the payload, leaf included
    depth: int

    @property
    def leaf(self) -> str:
        return self.path.rsplit(".", 1)[-1]

    @property
    def qualified(self) -> str:
        return f"{self.event}.{self.path}"


@dataclass(frozen=True, slots=True)
class FieldScanCoverage:
    repo_root: str
    events: int
    top_level_slots: int
    nested_slots: int
    unique_leaves: int
    scan_files: int
    baseline_leaves: int


@dataclass
class FieldConsumerResult:
    ok: bool
    new_orphans: list[str] = field(default_factory=list)
    paths_by_leaf: dict[str, tuple[str, ...]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    coverage: FieldScanCoverage | None = None
    unknown_baseline_leaves: tuple[str, ...] = ()


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "apps" / "server" / "agentcore").is_dir() and (
            candidate / "packages" / "contract-types"
        ).is_dir():
            return candidate
    raise FileNotFoundError(f"field-consumer gate: cannot locate repo root from {here}")


def strip_comments(src: str) -> str:
    """Remove // and /* */ comments without touching string / template contents."""
    out: list[str] = []
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if ch == "@" and nxt == '"':
            out.append(ch)
            i += 1
            out.append(src[i])
            i += 1
            while i < n:
                c = src[i]
                out.append(c)
                if c == '"':
                    if i + 1 < n and src[i + 1] == '"':
                        out.append(src[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == "/" and nxt == "/":
            i += 2
            while i < n and src[i] not in "\n\r":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i = min(i + 2, n)
            out.append(" ")
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                c = src[i]
                out.append(c)
                if c == "\\" and i + 1 < n:
                    out.append(src[i + 1])
                    i += 2
                    continue
                if c == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "`":
            out.append(ch)
            i += 1
            while i < n:
                c = src[i]
                out.append(c)
                if c == "\\" and i + 1 < n:
                    out.append(src[i + 1])
                    i += 2
                    continue
                if c == "`":
                    i += 1
                    break
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def tokens_from_source(src: str) -> set[str]:
    """Identifiers + quoted-string contents after comment stripping."""
    text = strip_comments(src)
    tokens = set(_IDENT_RE.findall(text))
    for m in _QUOTED_RE.finditer(text):
        inner = m.group(0)[1:-1]
        tokens.add(inner)
        tokens.update(_IDENT_RE.findall(inner))
    return tokens


def _body_after(src: str, start_brace: int) -> str:
    depth = 0
    i = start_brace
    n = len(src)
    while i < n:
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start_brace + 1 : i]
        i += 1
    raise ValueError("unbalanced brace in events.generated.ts")


def parse_event_payload_types(
    src: str,
) -> tuple[dict[str, list[tuple[str, str]]], dict[str, str], dict[str, str]]:
    """Parse interfaces / inline unions / SSEPayloadMap from generated TS.

    Returns (type → [(field, type_str)], type → extends, event → payload type).
    """
    stripped = strip_comments(src)
    fields: dict[str, list[tuple[str, str]]] = {}
    extends: dict[str, str] = {}
    for m in _INTERFACE_RE.finditer(stripped):
        name = m.group(1)
        if name in _SKIP_TYPE_NAMES:
            continue
        ext = m.group(2)
        if ext:
            extends[name] = ext
        brace_at = stripped.find("{", m.end() - 1)
        body = _body_after(stripped, brace_at)
        items = [(fm.group(1), fm.group(2).strip()) for fm in _FIELD_RE.finditer(body)]
        fields[name] = items
    for m in _TYPE_ALIAS_RE.finditer(stripped):
        name = m.group(1)
        if name in _SKIP_TYPE_NAMES or name in fields:
            continue
        after = stripped[m.end() :]
        nxt = re.search(r"\nexport ", after)
        block = after[: nxt.start()] if nxt else after
        if "{" not in block:
            continue
        merged: dict[str, str] = {}
        for um in _INLINE_UNION_MEMBER_RE.finditer(block):
            for fm in _INLINE_FIELD_RE.finditer(um.group(1)):
                merged[fm.group(1)] = fm.group(2).strip()
        if merged:
            fields[name] = list(merged.items())
    payload_map: dict[str, str] = {}
    mm = _PAYLOAD_MAP_RE.search(stripped)
    if mm is None:
        raise ValueError("SSEPayloadMap not found in events.generated.ts")
    for em in _MAP_ENTRY_RE.finditer(mm.group(1)):
        event = em.group(1) or em.group(2)
        payload_map[event] = em.group(3)
    return fields, extends, payload_map


def _own_and_inherited(
    name: str,
    fields: dict[str, list[tuple[str, str]]],
    extends: dict[str, str],
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    chain = [name]
    guard: set[str] = set()
    while chain:
        cur = chain.pop(0)
        if cur in guard:
            continue
        guard.add(cur)
        for fname, ftype in fields.get(cur, []):
            if fname in seen:
                continue
            seen.add(fname)
            out.append((fname, ftype))
        parent = extends.get(cur)
        if parent:
            chain.append(parent)
    return out


def _referred_types(type_str: str, known: set[str]) -> list[str]:
    found: list[str] = []
    for ident in _TYPE_NAME_RE.findall(type_str):
        if ident in _PRIMITIVE_TYPES:
            continue
        if ident in known:
            found.append(ident)
    return found


def walk_field_slots(
    payload_map: dict[str, str],
    fields: dict[str, list[tuple[str, str]]],
    extends: dict[str, str],
) -> list[FieldSlot]:
    known = set(fields)
    slots: list[FieldSlot] = []

    def rec(event: str, type_name: str, prefix: str, depth: int, stack: frozenset[str]) -> None:
        if type_name in stack:
            return
        nxt_stack = stack | {type_name}
        for fname, ftype in _own_and_inherited(type_name, fields, extends):
            path = f"{prefix}.{fname}" if prefix else fname
            slots.append(FieldSlot(event=event, path=path, depth=depth))
            for child in _referred_types(ftype, known):
                rec(event, child, path, depth + 1, nxt_stack)

    for event, payload in payload_map.items():
        rec(event, payload, "", 0, frozenset())
    return slots


def idfield_seed_names(src: str) -> frozenset[str]:
    return frozenset(_IDFIELD_RE.findall(src))


def is_production_scan_file(path: Path) -> bool:
    if path.suffix.lower() not in _SCAN_EXTS:
        return False
    if any(part in _TEST_DIR_NAMES for part in path.parts):
        return False
    lowered = path.name.lower()
    if any(marker in lowered for marker in _SKIP_NAME_MARKERS):
        return False
    return not lowered.endswith("tests.cs")


def iter_scan_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for rel in _SCAN_ROOTS:
        base = root / rel
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and is_production_scan_file(path):
                out.append(path)
    return out


def collect_consumer_tokens(root: Path, files: list[Path]) -> set[str]:
    tokens: set[str] = set()
    for path in files:
        tokens |= tokens_from_source(path.read_text(encoding="utf-8-sig"))
    idfield_path = root / _IDFIELD_REL
    if idfield_path.is_file():
        tokens |= set(idfield_seed_names(idfield_path.read_text(encoding="utf-8")))
    return tokens


def run_field_consumer_gate() -> FieldConsumerResult:
    try:
        root = _repo_root()
    except FileNotFoundError as exc:
        return FieldConsumerResult(ok=False, errors=[str(exc)])

    events_path = root / _EVENTS_REL
    if not events_path.is_file():
        return FieldConsumerResult(
            ok=False, errors=[f"listed file missing: {_EVENTS_REL}"]
        )
    idfield_path = root / _IDFIELD_REL
    if not idfield_path.is_file():
        return FieldConsumerResult(
            ok=False, errors=[f"listed file missing: {_IDFIELD_REL}"]
        )
    dupes = duplicate_baseline_leaves()
    if dupes:
        return FieldConsumerResult(
            ok=False,
            errors=[
                "baseline leaf claimed by multiple groups: "
                + ", ".join(f"{leaf}→{ids}" for leaf, ids in sorted(dupes.items()))
            ],
        )

    src = events_path.read_text(encoding="utf-8")
    try:
        fields, extends, payload_map = parse_event_payload_types(src)
    except ValueError as exc:
        return FieldConsumerResult(ok=False, errors=[str(exc)])

    slots = walk_field_slots(payload_map, fields, extends)
    paths_by_leaf: dict[str, list[str]] = {}
    for slot in slots:
        paths_by_leaf.setdefault(slot.leaf, []).append(slot.qualified)

    files = iter_scan_files(root)
    hits = collect_consumer_tokens(root, files)
    baseline = baseline_leaf_names()
    contract_leaves = frozenset(paths_by_leaf)
    unknown_baseline = tuple(sorted(baseline - contract_leaves))

    zero_leaves = sorted(leaf for leaf in paths_by_leaf if leaf not in hits)
    new_orphans = [leaf for leaf in zero_leaves if leaf not in baseline]

    coverage = FieldScanCoverage(
        repo_root=str(root),
        events=len(payload_map),
        top_level_slots=sum(1 for s in slots if s.depth == 0),
        nested_slots=sum(1 for s in slots if s.depth > 0),
        unique_leaves=len(paths_by_leaf),
        scan_files=len(files),
        baseline_leaves=len(baseline),
    )
    frozen_paths = {leaf: tuple(paths) for leaf, paths in paths_by_leaf.items()}
    errors: list[str] = []
    if unknown_baseline:
        errors.append(
            "baseline names not in events.generated.ts: " + ", ".join(unknown_baseline)
        )
    ok = not new_orphans and not errors
    return FieldConsumerResult(
        ok=ok,
        new_orphans=new_orphans,
        paths_by_leaf=frozen_paths,
        errors=errors,
        coverage=coverage,
        unknown_baseline_leaves=unknown_baseline,
    )


def format_coverage(result: FieldConsumerResult) -> list[str]:
    cov = result.coverage
    if cov is None:
        return []
    return [
        f"  repo_root: {cov.repo_root}",
        (
            f"  contract: events={cov.events} top_level={cov.top_level_slots} "
            f"nested={cov.nested_slots} unique_leaves={cov.unique_leaves}"
        ),
        (
            f"  scan: {cov.scan_files} files under "
            + ", ".join(_SCAN_ROOTS)
            + " (skip tests/json)"
        ),
        f"  baseline exempt leaf names: {cov.baseline_leaves} "
        f"({len(FIELD_CONSUMER_BASELINE)} groups in field_consumer_baseline.py)",
    ]


def format_field_orphan_reports(result: FieldConsumerResult) -> list[str]:
    lines: list[str] = []
    for leaf in result.new_orphans:
        lines.append(f"leaf {leaf!r} — zero hits in production consumer code:")
        for path in result.paths_by_leaf.get(leaf, ()):
            lines.append(f"  - {path}")
        lines.append(_WORKFLOW_HINT)
    return lines
