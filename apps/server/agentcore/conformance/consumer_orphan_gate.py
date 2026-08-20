"""Gate: protocol producer ↔ consumer orphans (both directions).

Direction A — contract without producer: type is in the generated contract but
no live Python producer. Fold / EVENT_PARITY / PROCESS_STEP_KIND exhaustiveness
is a compile-time ratchet; this side does not scan those files.

Direction B — producer without desktop live UI: Python producer exists but no
desktop live handler (conversation SSE dispatch + handlers, client-tool fulfill,
browser live, handoff). Fold / parity are **not** counted here: they are
exhaustive no-op switches, so counting them would hide live-dispatch drops.

Coverage (verify by CLI ``coverage:`` lines / listed-file existence errors — do not
trust this comment alone):

* Repo root: walk parents from this file until ``apps/server/agentcore`` and
  ``packages/contract-types`` both exist.
* Producers: ``apps/server/agentcore/**/*.py`` except ``conformance/``, ``evals/``,
  ``demo_tape/``. Process-step also reads ``conformance/projection.py``.
* SSE registry: live ``EventType`` values ∪ ``packages/contract-types/src/eventTypes.generated.ts``.
* Interaction registry: ``packages/contract-types/src/interactionKinds.generated.ts``.
  Live producer = ``InteractionKind`` ∩ (``INTERACTION_KIND_SPECS`` ∪ ``client_tool``).
* ProcessStep registry: ``ProcessStep`` union in
  ``packages/contract-types/src/events.generated.ts``.
* Live UI (direction B): desktop ``sse/dispatch.ts`` + ``sse/handlers/*.ts``,
  client-tool frames/ingress, browserLive, handoff, interaction registry.
  Not scanned: admin, fold, EVENT_PARITY, tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agentcore.conformance.consumer_orphan_allowlist import (
    CONSUMER_ORPHAN_ALLOWLIST,
    PRODUCER_ORPHAN_ALLOWLIST,
)
from agentcore.runtime.events.types import EventType
from agentcore.runtime.interaction import INTERACTION_KIND_SPECS, InteractionKind

SurfaceKind = Literal["sse", "interaction", "process_step"]

_SKIP_AGENTCORE_PREFIXES = (
    "agentcore/conformance/",
    "agentcore/evals/",
    "agentcore/demo_tape/",
)

# Event factories exported from runtime.events (construction path single source).
_EVENT_FACTORY_NAMES: frozenset[str] = frozenset(
    {
        "message_start",
        "content_delta",
        "content_reset",
        "reasoning_delta",
        "tool_progress",
        "tool_use_progress",
        "tool_use_start",
        "tool_use_end",
        "citations_event",
        "evidence_ledger_event",
        "approval_required",
        "approval_resolved",
        "checkpoint_required",
        "checkpoint_resolved",
        "plan_review_required",
        "plan_review_resolved",
        "team_preview_required",
        "team_preview_resolved",
        "stage_card_required",
        "stage_card_resolved",
        "workspace_op_required",
        "board_op_required",
        "board_read_required",
        "browser_live_frame",
        "browser_live_status",
        "desktop_notify_required",
        "external_mount_readonly_required",
        "host_op_required",
        "mcp_op_required",
        "auto_folder_created",
        "handoff_snapshot_done",
        "handoff_job_started",
        "handoff_apply_done",
        "workspace_snapshot_done",
        "workspace_snapshot_failed",
        "message_end",
        "error_event",
        "title_generated",
        "turn_saved",
        "turn_warning",
        "run_plan",
        "graph_append",
        "plan_revised",
        "run_started",
        "run_context",
        "run_output_delta",
        "run_output_reset",
        "run_reasoning_delta",
        "run_phase",
        "run_tool_progress",
        "escalation_raised",
        "escalation_required",
        "escalation_resolved",
        "interaction_orphaned",
        "run_escalation_gate",
        "team_note_posted",
        "team_synthesis_preview",
        "coordination_wait",
        "workspace_lock_wait",
        "delivery_status",
        "user_interjection",
        "turn_queued",
        "turn_queue_started",
        "turn_queue_cancelled",
        "resume_deferred",
        "resume_settled",
        "execution_detached",
        "execution_completed",
        "run_completed",
        "run_failed",
        "run_cancelled",
        "run_skipped",
        "run_progress",
        "batch_metrics",
        "debate_result",
        "debate_round_started",
        "debate_round",
        "debate_pretrial_started",
        "debate_pretrial_orders",
        "debate_pretrial_completed",
    }
)

_UNION_PIPE_RE = re.compile(r'^\s+\|\s+"([^"]+)"')
_PROCESS_STEP_UNION_RE = re.compile(r'\|\s*\{\s*kind:\s*"([^"]+)"')
_WIRE_KEY_RE = re.compile(r'^  "([^"]+)":\s*\{')
_EMIT_EVENTS_RE = re.compile(r"_emit\s*\(\s*\w+\s*,\s*\"([^\"]+)\"")
_PROCESS_STEP_BLOCK_RE = re.compile(
    r"export type ProcessStep\s*=([\s\S]*?);\n\n",
)
_SSE_EVENT_CTOR_RE = re.compile(r'SSEEvent\s*\([^)]*type\s*=\s*"([^"]+)"')
_SIM_WIRE_RE = re.compile(r'"(sim\.[a-z0-9_.]+)"')
_EVENTTYPE_MEMBER_RE = re.compile(r"EventType\.([A-Z0-9_]+)")
_BRIDGE_ONLY_INTERACTION_KINDS = frozenset({"client_tool"})

_SSE_CONTRACT_REL = "packages/contract-types/src/eventTypes.generated.ts"
_INTERACTION_CONTRACT_REL = "packages/contract-types/src/interactionKinds.generated.ts"
_PROCESS_STEP_CONTRACT_REL = "packages/contract-types/src/events.generated.ts"

_REQUIRED_CONTRACT_FILES: tuple[tuple[str, str], ...] = (
    (_SSE_CONTRACT_REL, "contract eventTypes.generated"),
    (_INTERACTION_CONTRACT_REL, "contract interactionKinds.generated"),
    (_PROCESS_STEP_CONTRACT_REL, "contract ProcessStep union"),
)

_SSE_LIVE_HANDLER_DIR = "apps/desktop/src/renderer/services/sse/handlers"
_INTERACTION_REGISTRY_REL = (
    "apps/desktop/src/renderer/stores/interactions/registry.ts"
)

_SSE_LIVE_EXTRA_FILES: tuple[tuple[str, str], ...] = (
    ("apps/desktop/src/renderer/services/sse/dispatch.ts", "desktop sse dispatch"),
    (
        "apps/desktop/src/renderer/services/clientToolFrames.ts",
        "desktop client-tool frames",
    ),
    (
        "apps/desktop/src/renderer/services/clientToolIngress.ts",
        "desktop client-tool ingress",
    ),
    ("apps/desktop/src/renderer/services/browserLive.ts", "desktop browser live"),
    ("apps/desktop/src/renderer/services/handoff.ts", "desktop handoff"),
    (
        "apps/desktop/src/renderer/types/interactionExt.ts",
        "desktop interaction_orphaned wire",
    ),
    (_INTERACTION_REGISTRY_REL, "desktop interaction registry"),
)

_PROCESS_STEP_UI_FILES: tuple[tuple[str, str], ...] = (
    ("apps/desktop/src/renderer/lib/processTimeline.ts", "desktop processTimeline"),
    (
        "apps/desktop/src/renderer/components/chat/message-bubble/ProcessTimeline.tsx",
        "desktop ProcessTimeline UI",
    ),
    ("apps/mobile/src/protocol/fold.ts", "mobile fold process marker"),
    ("apps/mobile/src/components/ProcessTimeline.tsx", "mobile ProcessTimeline UI"),
)

_CASE_RE = re.compile(r"case\s+['\"]([^'\"]+)['\"]")
_EVENTTYPE_EQ_RE = re.compile(
    r"(?:eventType|event\.type|\.type|\btype)\s*===\s*['\"]([^'\"]+)['\"]"
)
_TYPE_PROP_RE = re.compile(r"\btype:\s*['\"]([^'\"]+)['\"]")
_KIND_RE = re.compile(r"kind:\s*['\"]([^'\"]+)['\"]")
_QUOTED_IDENT_RE = re.compile(r"['\"]([a-z][a-z0-9_.]*)['\"]")
_SIM_PREFIX_RE = re.compile(r"""startsWith\(\s*['\"]sim\.['\"]\s*\)""")
_KIND_WIRE_RE = re.compile(
    r'"([^"]+)":\s*\{\s*requiredEvent:\s*"([^"]+)",\s*'
    r'resolvedEvent:\s*(?:"([^"]+)"|null)',
    re.S,
)
_PROCESS_STEP_KIND_BLOCK_RE = re.compile(
    r"export const PROCESS_STEP_KIND[^=]*=\s*\{([\s\S]*?)\}\s*;"
)
_PROCESS_STEP_KIND_KEY_RE = re.compile(
    r"(?:^|\n)\s+(?:\"([^\"]+)\"|([a-z][a-z0-9_]*))\s*:"
)

_WORKFLOW_HINT = (
    "  Next: delete this type from the contract, then tsc will fail at every remaining "
    "frontend consumer (EVENT_PARITY, fold assertNever, PROCESS_STEP_KIND)."
)
_PRODUCER_WORKFLOW_HINT = (
    "  Next: handle this type in desktop live UI (sse/handlers or the dedicated "
    "stream listed above), or stop emitting it."
)


@dataclass(frozen=True, slots=True)
class ContractRef:
    surface: SurfaceKind
    key: str
    rel_path: str
    label: str


@dataclass(frozen=True, slots=True)
class OrphanReport:
    surface: SurfaceKind
    key: str
    contract_refs: tuple[ContractRef, ...]


@dataclass(frozen=True, slots=True)
class ProducerRef:
    rel_path: str
    line: int


@dataclass(frozen=True, slots=True)
class ProducerOrphanReport:
    surface: SurfaceKind
    key: str
    producer_refs: tuple[ProducerRef, ...]


@dataclass
class ConsumerOrphanResult:
    ok: bool
    orphans: list[OrphanReport] = field(default_factory=list)
    producer_orphans: list[ProducerOrphanReport] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    coverage: ScanCoverage | None = None


@dataclass(frozen=True, slots=True)
class ScanCoverage:
    repo_root: str
    producer_py_count: int
    contract_files: tuple[str, ...]
    ui_files: tuple[str, ...]
    registered_sse: int
    registered_interaction: int
    registered_process_step: int


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "apps" / "server" / "agentcore").is_dir() and (
            candidate / "packages" / "contract-types"
        ).is_dir():
            return candidate
    raise FileNotFoundError(
        f"consumer-orphan gate: cannot locate repo root from {here}"
    )


def _listed_contract_rels() -> tuple[str, ...]:
    seen: list[str] = []
    found: set[str] = set()
    for rel, _label in _REQUIRED_CONTRACT_FILES:
        if rel in found:
            continue
        found.add(rel)
        seen.append(rel)
    return tuple(seen)


def _sse_live_handler_files(root: Path) -> list[tuple[str, str]]:
    handler_dir = root / _SSE_LIVE_HANDLER_DIR
    if not handler_dir.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for path in sorted(handler_dir.glob("*.ts")):
        if path.name.endswith(".test.ts"):
            continue
        rel = path.relative_to(root).as_posix()
        out.append((rel, f"desktop sse handler {path.name}"))
    return out


def _listed_ui_files(root: Path) -> tuple[tuple[str, str], ...]:
    extra = list(_SSE_LIVE_EXTRA_FILES)
    handlers = _sse_live_handler_files(root)
    process_ui = list(_PROCESS_STEP_UI_FILES)
    seen: set[str] = set()
    ordered: list[tuple[str, str]] = []
    for rel, label in (*extra, *handlers, *process_ui):
        if rel in seen:
            continue
        seen.add(rel)
        ordered.append((rel, label))
    return tuple(ordered)


def _listed_ui_rels(root: Path) -> tuple[str, ...]:
    return tuple(rel for rel, _label in _listed_ui_files(root))


def _missing_listed_files(root: Path) -> list[str]:
    missing = [rel for rel in _listed_contract_rels() if not (root / rel).is_file()]
    handler_dir = root / _SSE_LIVE_HANDLER_DIR
    if not handler_dir.is_dir():
        missing.append(_SSE_LIVE_HANDLER_DIR)
    elif not _sse_live_handler_files(root):
        missing.append(f"{_SSE_LIVE_HANDLER_DIR}/*.ts")
    for rel, _label in _listed_ui_files(root):
        if not (root / rel).is_file():
            missing.append(rel)
    return missing


def _agentcore_root() -> Path:
    return Path(__file__).parent.parent


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _enum_wire_map() -> dict[str, str]:
    return {member.name: member.value for member in EventType}


def _factory_to_wire() -> dict[str, str]:
    """Map event factory function names to wire types via live factory bodies."""
    out: dict[str, str] = {}
    events_dir = _agentcore_root() / "runtime" / "events"
    pat = re.compile(
        r"def\s+(" + "|".join(re.escape(n) for n in sorted(_EVENT_FACTORY_NAMES)) + r")\s*\("
    )
    type_pat = re.compile(r"type\s*=\s*EventType\.([A-Z0-9_]+)")
    for py in events_dir.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        for m in pat.finditer(text):
            name = m.group(1)
            body_start = m.end()
            next_def = text.find("\ndef ", body_start)
            body = text[body_start : next_def if next_def != -1 else len(text)]
            tm = type_pat.search(body)
            if tm is not None:
                wire = EventType[tm.group(1)].value
                out[name] = wire
    return out


def _iter_production_py(cache: dict[str, Any]) -> list[Path]:
    root = _agentcore_root()
    cached_key = "__agentcore_py_files__"
    if cached_key in cache:
        return cache[cached_key]
    out: list[Path] = []
    for py in root.rglob("*.py"):
        rel = py.relative_to(root).as_posix()
        if any(rel.startswith(prefix) for prefix in _SKIP_AGENTCORE_PREFIXES):
            continue
        out.append(py)
    out.sort()
    cache[cached_key] = out
    return out


def _is_word_char(ch: str) -> bool:
    return ch == "_" or ch.isalnum()


def _line_compares_eventtype(line: str, enum_name: str) -> bool:
    """Same as ``re.search(r'(==|is)\\s*EventType.{name}\\b', line)`` without compiling."""
    needle = f"EventType.{enum_name}"
    start = 0
    nlen = len(needle)
    while True:
        idx = line.find(needle, start)
        if idx < 0:
            return False
        end = idx + nlen
        if end < len(line) and _is_word_char(line[end]):
            start = idx + 1
            continue
        j = idx
        while j > 0 and line[j - 1].isspace():
            j -= 1
        if j >= 2 and line[j - 2 : j] in ("==", "is"):
            return True
        start = idx + 1


def _line_emits_enum(line: str, enum_name: str, *, compact: str | None = None) -> bool:
    if compact is None:
        compact = line.replace(" ", "")
    needle = f"EventType.{enum_name}"
    if f"type={needle}" in compact:
        return True
    if f"type={needle}.value" in compact:
        return True
    # Comparison / read paths are consumers, not producers.
    # ``.type == EventType.X`` / ``.type is EventType.X`` are covered by this check.
    if _line_compares_eventtype(line, enum_name):
        return False
    return needle in line and "type=" in line


def _enum_names_mentioned(
    line: str, compact: str, enum_names: tuple[str, ...]
) -> list[str]:
    """Enum names the old substring checks could hit on this line (incl. prefixes)."""
    if "EventType." not in compact:
        return []
    mentioned: list[str] = []
    seen: set[str] = set()
    for src in (line, compact):
        if "EventType." not in src:
            continue
        for m in _EVENTTYPE_MEMBER_RE.finditer(src):
            token = m.group(1)
            for name in enum_names:
                if name not in seen and token.startswith(name):
                    seen.add(name)
                    mentioned.append(name)
    return mentioned


def _compile_factory_call_re(factory_names: list[str]) -> re.Pattern[str] | None:
    if not factory_names:
        return None
    alts = "|".join(
        re.escape(n) for n in sorted(set(factory_names), key=len, reverse=True)
    )
    return re.compile(rf"\b({alts})\s*\(")


def _scan_sse_producers(
    root: Path, cache: dict[str, Any]
) -> dict[str, list[tuple[str, int]]]:
    enum_map = _enum_wire_map()
    enum_names = tuple(enum_map)
    factory_to_wire = _factory_to_wire()
    factory_call_re = _compile_factory_call_re(list(factory_to_wire))
    producers: dict[str, list[tuple[str, int]]] = {}

    def add(wire: str, rel_path: str, line: int) -> None:
        producers.setdefault(wire, []).append((rel_path, line))

    for py in _iter_production_py(cache):
        rel_path = _rel(py, _agentcore_root())
        for line_no, line in enumerate(_read_text_cached(cache, py).splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("def "):
                continue
            if factory_call_re is not None:
                seen_factories: set[str] = set()
                for m in factory_call_re.finditer(line):
                    factory = m.group(1)
                    if factory in seen_factories:
                        continue
                    seen_factories.add(factory)
                    wire = factory_to_wire.get(factory)
                    if wire:
                        add(wire, rel_path, line_no)
            compact = line.replace(" ", "")
            for enum_name in _enum_names_mentioned(line, compact, enum_names):
                if _line_emits_enum(line, enum_name, compact=compact):
                    add(enum_map[enum_name], rel_path, line_no)
            for m in _SSE_EVENT_CTOR_RE.finditer(line):
                add(m.group(1), rel_path, line_no)
            for m in _SIM_WIRE_RE.finditer(line):
                add(m.group(1), rel_path, line_no)
            for m in _EMIT_EVENTS_RE.finditer(line):
                add(m.group(1), rel_path, line_no)
            if "persist_and_emit" in line or ".emit(" in line or "emit_sse" in line:
                if "==" in line or " is EventType." in line:
                    continue
                for em in _EVENTTYPE_MEMBER_RE.finditer(line):
                    wire = enum_map.get(em.group(1))
                    if wire:
                        add(wire, rel_path, line_no)

    return producers


def _scan_process_step_producers(
    cache: dict[str, Any],
) -> dict[str, list[tuple[str, int]]]:
    producers: dict[str, list[tuple[str, int]]] = {}
    py_files = list(_iter_production_py(cache))
    projection = _agentcore_root() / "conformance" / "projection.py"
    if projection.is_file():
        _read_text_cached(cache, projection)
        py_files.append(projection)
    kind_on_line = re.compile(r'"kind":\s*"([^"]+)"')
    for py in py_files:
        rel_path = _rel(py, _agentcore_root())
        text = _read_text_cached(cache, py)
        for line_no, line in enumerate(text.splitlines(), 1):
            if "process.append" in line or "process.insert" in line or "marker" in line:
                for m in kind_on_line.finditer(line):
                    producers.setdefault(m.group(1), []).append((rel_path, line_no))
        for m in kind_on_line.finditer(text):
            start = m.start()
            snippet = text[max(0, start - 160) : start]
            if "process" not in snippet and "marker" not in snippet:
                continue
            line_no = text.count("\n", 0, start) + 1
            producers.setdefault(m.group(1), []).append((rel_path, line_no))
    return producers


def _read_text_cached(cache: dict[str, Any], path: Path) -> str:
    key = str(path)
    if key not in cache:
        cache[key] = path.read_text(encoding="utf-8")
    return cache[key]


def _parse_literals_in_file(
    root: Path,
    rel: str,
    pattern: re.Pattern[str],
    cache: dict[str, Any],
) -> dict[str, list[tuple[int, str]]]:
    path = root / rel
    if not path.is_file():
        return {}
    out: dict[str, list[tuple[int, str]]] = {}
    for line_no, line in enumerate(_read_text_cached(cache, path).splitlines(), 1):
        for m in pattern.finditer(line):
            out.setdefault(m.group(1), []).append((line_no, rel))
    return out


def _sse_contract_types(root: Path, cache: dict[str, Any]) -> frozenset[str]:
    return frozenset(
        _parse_literals_in_file(root, _SSE_CONTRACT_REL, _UNION_PIPE_RE, cache)
    )


def _interaction_contract_kinds(root: Path, cache: dict[str, Any]) -> frozenset[str]:
    kinds = set(
        _parse_literals_in_file(root, _INTERACTION_CONTRACT_REL, _UNION_PIPE_RE, cache)
    )
    kinds.update(
        _parse_literals_in_file(root, _INTERACTION_CONTRACT_REL, _WIRE_KEY_RE, cache)
    )
    return frozenset(kinds)


def _process_step_contract_kinds(root: Path, cache: dict[str, Any]) -> frozenset[str]:
    path = root / _PROCESS_STEP_CONTRACT_REL
    if not path.is_file():
        return frozenset()
    text = _read_text_cached(cache, path)
    block_m = _PROCESS_STEP_BLOCK_RE.search(text)
    if not block_m:
        return frozenset()
    return frozenset(_PROCESS_STEP_UNION_RE.findall(block_m.group(1)))


def _registered_sse(root: Path, cache: dict[str, Any]) -> frozenset[str]:
    live = frozenset(e.value for e in EventType)
    return live | _sse_contract_types(root, cache)


def _registered_interaction(root: Path, cache: dict[str, Any]) -> frozenset[str]:
    return _interaction_contract_kinds(root, cache)


def _registered_process_step(root: Path, cache: dict[str, Any]) -> frozenset[str]:
    return _process_step_contract_kinds(root, cache)


def _allowlisted_consumer(surface: SurfaceKind, key: str) -> bool:
    return f"{surface}:{key}" in CONSUMER_ORPHAN_ALLOWLIST


def _allowlisted_producer(surface: SurfaceKind, key: str) -> bool:
    return f"{surface}:{key}" in PRODUCER_ORPHAN_ALLOWLIST


def _add_ui_hit(
    consumers: dict[str, list[tuple[str, int, str]]],
    known: frozenset[str],
    key: str,
    rel: str,
    line: int,
    label: str,
) -> None:
    if key not in known:
        return
    consumers.setdefault(key, []).append((rel, line, label))


def _interaction_registry_kinds(root: Path, cache: dict[str, Any]) -> frozenset[str]:
    hits = _parse_literals_in_file(root, _INTERACTION_REGISTRY_REL, _KIND_RE, cache)
    return frozenset(hits)


def _interaction_wire_events(
    root: Path, cache: dict[str, Any], registry_kinds: frozenset[str]
) -> dict[str, list[tuple[str, int, str]]]:
    """SSE wire types consumed via INTERACTION_REGISTRY × INTERACTION_KIND_WIRE."""
    text = _read_text_cached(cache, root / _INTERACTION_CONTRACT_REL)
    consumers: dict[str, list[tuple[str, int, str]]] = {}
    for m in _KIND_WIRE_RE.finditer(text):
        kind, required, resolved = m.group(1), m.group(2), m.group(3)
        if kind not in registry_kinds:
            continue
        line = text.count("\n", 0, m.start()) + 1
        consumers.setdefault(required, []).append(
            (_INTERACTION_REGISTRY_REL, line, "desktop interaction registry wire")
        )
        if resolved:
            consumers.setdefault(resolved, []).append(
                (_INTERACTION_REGISTRY_REL, line, "desktop interaction registry wire")
            )
    return consumers


def _scan_sse_ui_consumers(
    root: Path, cache: dict[str, Any], known: frozenset[str]
) -> dict[str, list[tuple[str, int, str]]]:
    consumers: dict[str, list[tuple[str, int, str]]] = {}
    process_rels = {item[0] for item in _PROCESS_STEP_UI_FILES}
    files = [
        (rel, label) for rel, label in _listed_ui_files(root) if rel not in process_rels
    ]
    sim_prefix_hit = False
    for rel, label in files:
        path = root / rel
        if not path.is_file():
            continue
        text = _read_text_cached(cache, path)
        if _SIM_PREFIX_RE.search(text):
            sim_prefix_hit = True
        for line_no, line in enumerate(text.splitlines(), 1):
            for m in _CASE_RE.finditer(line):
                _add_ui_hit(consumers, known, m.group(1), rel, line_no, label)
            for m in _EVENTTYPE_EQ_RE.finditer(line):
                _add_ui_hit(consumers, known, m.group(1), rel, line_no, label)
            for m in _TYPE_PROP_RE.finditer(line):
                _add_ui_hit(consumers, known, m.group(1), rel, line_no, label)
            for m in _QUOTED_IDENT_RE.finditer(line):
                _add_ui_hit(consumers, known, m.group(1), rel, line_no, label)
    if sim_prefix_hit:
        dispatch_rel = "apps/desktop/src/renderer/services/sse/dispatch.ts"
        for key in known:
            if key.startswith("sim."):
                _add_ui_hit(
                    consumers, known, key, dispatch_rel, 0, "desktop sse dispatch sim.*"
                )
    registry_kinds = _interaction_registry_kinds(root, cache)
    for key, refs in _interaction_wire_events(root, cache, registry_kinds).items():
        for ui_rel, ui_line, ui_label in refs:
            _add_ui_hit(consumers, known, key, ui_rel, ui_line, ui_label)
    return consumers


def _scan_process_step_ui_consumers(
    root: Path, cache: dict[str, Any], known: frozenset[str]
) -> dict[str, list[tuple[str, int, str]]]:
    consumers: dict[str, list[tuple[str, int, str]]] = {}
    for rel, label in _PROCESS_STEP_UI_FILES:
        path = root / rel
        if not path.is_file():
            continue
        text = _read_text_cached(cache, path)
        block = _PROCESS_STEP_KIND_BLOCK_RE.search(text)
        if block:
            for m in _PROCESS_STEP_KIND_KEY_RE.finditer(block.group(1)):
                key = m.group(1) or m.group(2)
                _add_ui_hit(consumers, known, key, rel, 0, f"{label} PROCESS_STEP_KIND")
        for line_no, line in enumerate(text.splitlines(), 1):
            for m in _KIND_RE.finditer(line):
                _add_ui_hit(consumers, known, m.group(1), rel, line_no, label)
            for m in _CASE_RE.finditer(line):
                _add_ui_hit(consumers, known, m.group(1), rel, line_no, label)
    return consumers


def _producer_refs(
    locs: list[tuple[str, int]], *, limit: int = 8
) -> tuple[ProducerRef, ...]:
    seen: list[ProducerRef] = []
    found: set[tuple[str, int]] = set()
    for rel, line in locs:
        key = (rel, line)
        if key in found:
            continue
        found.add(key)
        seen.append(ProducerRef(rel_path=rel, line=line))
        if len(seen) >= limit:
            break
    return tuple(seen)


def _scan_coverage(
    root: Path,
    cache: dict[str, Any],
    *,
    registered_sse: int,
    registered_interaction: int,
    registered_process_step: int,
) -> ScanCoverage:
    return ScanCoverage(
        repo_root=str(root),
        producer_py_count=len(_iter_production_py(cache)),
        contract_files=_listed_contract_rels(),
        ui_files=_listed_ui_rels(root),
        registered_sse=registered_sse,
        registered_interaction=registered_interaction,
        registered_process_step=registered_process_step,
    )


def run_consumer_orphan_gate() -> ConsumerOrphanResult:
    try:
        root = _repo_root()
    except FileNotFoundError as exc:
        return ConsumerOrphanResult(ok=False, errors=[str(exc)])

    cache: dict[str, Any] = {}
    errors: list[str] = [
        f"listed file missing (repo_root={root.as_posix()}): {rel}"
        for rel in _missing_listed_files(root)
    ]
    if errors:
        return ConsumerOrphanResult(
            ok=False,
            errors=errors,
            coverage=_scan_coverage(
                root,
                cache,
                registered_sse=0,
                registered_interaction=0,
                registered_process_step=0,
            ),
        )

    sse_producers = _scan_sse_producers(root, cache)
    process_producers = _scan_process_step_producers(cache)

    sse_registered = _registered_sse(root, cache)
    interaction_registered = _registered_interaction(root, cache)
    process_registered = _registered_process_step(root, cache)
    coverage = _scan_coverage(
        root,
        cache,
        registered_sse=len(sse_registered),
        registered_interaction=len(interaction_registered),
        registered_process_step=len(process_registered),
    )

    orphans: list[OrphanReport] = []
    producer_orphans: list[ProducerOrphanReport] = []

    for wire in sorted(sse_registered):
        if _allowlisted_consumer("sse", wire):
            continue
        if wire in sse_producers:
            continue
        orphans.append(
            OrphanReport(
                surface="sse",
                key=wire,
                contract_refs=(
                    ContractRef(
                        surface="sse",
                        key=wire,
                        rel_path=_SSE_CONTRACT_REL,
                        label="contract eventTypes.generated",
                    ),
                ),
            )
        )

    live_interaction_kinds = frozenset(e.value for e in InteractionKind)
    spec_interaction_kinds = frozenset(k.value for k in INTERACTION_KIND_SPECS)

    for kind in sorted(interaction_registered):
        if _allowlisted_consumer("interaction", kind):
            continue
        if kind in live_interaction_kinds and (
            kind in spec_interaction_kinds or kind in _BRIDGE_ONLY_INTERACTION_KINDS
        ):
            continue
        orphans.append(
            OrphanReport(
                surface="interaction",
                key=kind,
                contract_refs=(
                    ContractRef(
                        surface="interaction",
                        key=kind,
                        rel_path=_INTERACTION_CONTRACT_REL,
                        label="contract interactionKinds.generated",
                    ),
                ),
            )
        )

    for kind in sorted(process_registered):
        if _allowlisted_consumer("process_step", kind):
            continue
        if kind in process_producers:
            continue
        orphans.append(
            OrphanReport(
                surface="process_step",
                key=kind,
                contract_refs=(
                    ContractRef(
                        surface="process_step",
                        key=kind,
                        rel_path=_PROCESS_STEP_CONTRACT_REL,
                        label="contract ProcessStep union",
                    ),
                ),
            )
        )

    sse_ui = _scan_sse_ui_consumers(root, cache, sse_registered)
    process_ui = _scan_process_step_ui_consumers(root, cache, process_registered)
    interaction_ui = _interaction_registry_kinds(root, cache)

    for wire, locs in sorted(sse_producers.items()):
        if wire not in sse_registered:
            continue
        if _allowlisted_producer("sse", wire):
            continue
        if wire in sse_ui:
            continue
        producer_orphans.append(
            ProducerOrphanReport(
                surface="sse",
                key=wire,
                producer_refs=_producer_refs(locs),
            )
        )

    for kind in sorted(spec_interaction_kinds):
        if _allowlisted_producer("interaction", kind):
            continue
        if kind in interaction_ui:
            continue
        producer_orphans.append(
            ProducerOrphanReport(
                surface="interaction",
                key=kind,
                producer_refs=(
                    ProducerRef(
                        rel_path="agentcore/runtime/interaction.py",
                        line=0,
                    ),
                ),
            )
        )

    for kind, locs in sorted(process_producers.items()):
        if kind not in process_registered:
            continue
        if _allowlisted_producer("process_step", kind):
            continue
        if kind in process_ui:
            continue
        producer_orphans.append(
            ProducerOrphanReport(
                surface="process_step",
                key=kind,
                producer_refs=_producer_refs(locs),
            )
        )

    return ConsumerOrphanResult(
        ok=not orphans and not producer_orphans,
        orphans=orphans,
        producer_orphans=producer_orphans,
        errors=errors,
        coverage=coverage,
    )


def format_coverage(result: ConsumerOrphanResult) -> list[str]:
    cov = result.coverage
    if cov is None:
        return []
    lines = [
        f"  repo_root: {cov.repo_root}",
        (
            f"  producers: {cov.producer_py_count} py under apps/server/agentcore"
            " (skip conformance/evals/demo_tape)"
        ),
        (
            "  registered keys: "
            f"sse={cov.registered_sse} "
            f"interaction={cov.registered_interaction} "
            f"process_step={cov.registered_process_step}"
        ),
        "  contract files opened:",
    ]
    for rel in cov.contract_files:
        lines.append(f"    - {rel}")
    lines.append("  desktop live UI files opened:")
    for rel in cov.ui_files:
        lines.append(f"    - {rel}")
    lines.append(
        "  fold/parity/admin: not scanned for producer→UI "
        "(tsc exhaustiveness / out of scope)"
    )
    return lines


def format_producer_orphan_reports(result: ConsumerOrphanResult) -> list[str]:
    lines: list[str] = []
    for orphan in result.producer_orphans:
        lines.append(
            f"{orphan.surface} {orphan.key!r} — live Python producer, "
            "no desktop live UI consumer:"
        )
        seen: set[tuple[str, int]] = set()
        for ref in orphan.producer_refs:
            if ref.line == 0:
                lines.append(f"  - {ref.rel_path}")
                continue
            key = (ref.rel_path, ref.line)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"  - {ref.rel_path}:{ref.line}")
        lines.append(_PRODUCER_WORKFLOW_HINT)
    return lines


def format_orphan_reports(result: ConsumerOrphanResult) -> list[str]:
    lines: list[str] = []
    for orphan in result.orphans:
        lines.append(
            f"{orphan.surface} {orphan.key!r} — registered in the generated contract, "
            "no live Python producer:"
        )
        seen: set[str] = set()
        for ref in orphan.contract_refs:
            if ref.rel_path in seen:
                continue
            seen.add(ref.rel_path)
            lines.append(f"  - {ref.rel_path} ({ref.label})")
        lines.append(_WORKFLOW_HINT)
    lines.extend(format_producer_orphan_reports(result))
    return lines
