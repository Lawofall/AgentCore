"""Architecture import-boundary guards (executable layering contract).

Encodes the dependency contracts from ``docs/02-架构/项目结构.md`` §二 as tests so
the refined boundaries can't silently erode again. Each test parses real source
files (via ``ast``) and asserts the absence of forbidden ``agentcore.*`` imports.

The contracts are deliberately *pragmatic*, not maximal (post-2026-06 META
review): they forbid the couplings that genuinely break layering — routes
*executing*, the LLM gateway reaching into the DB, ``core`` depending upward —
while allowing the documented benign ones (routes reusing pricing constants /
runtime DTOs / credential resolution; ``core`` depending on ``config``). When a
boundary legitimately needs to change, update *both* this test and the doc — that
paired edit is the point.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

_SERVER_ROOT = Path(__file__).resolve().parents[1]
_PKG_ROOT = _SERVER_ROOT / "agentcore"


def _module_imports(path: Path) -> set[str]:
    """All ``agentcore.*`` dotted module targets imported by a source file.

    ``from agentcore.llm.factory import build_provider`` -> ``agentcore.llm.factory``;
    ``import agentcore.db.x`` -> ``agentcore.db.x``. Relative imports are ignored
    (they can't cross top-level package boundaries).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and node.module.startswith("agentcore"):
                out.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("agentcore"):
                    out.add(alias.name)
    return out


def _py_files(*rel: str) -> list[Path]:
    """Resolve package-relative paths to ``.py`` files (file or recursive dir)."""
    files: list[Path] = []
    for r in rel:
        base = _PKG_ROOT / r
        if base.is_file():
            files.append(base)
        else:
            files.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return files


def _violations(files: Iterable[Path], forbidden: Iterable[str]) -> dict[str, set[str]]:
    """Map ``relpath -> forbidden imports found``; empty dict == contract holds."""
    forbidden = tuple(forbidden)
    bad: dict[str, set[str]] = {}
    for f in files:
        hits = {
            imp
            for imp in _module_imports(f)
            for pref in forbidden
            if imp == pref or imp.startswith(pref + ".")
        }
        if hits:
            bad[str(f.relative_to(_PKG_ROOT))] = hits
    return bad


def test_api_routes_do_not_execute() -> None:
    """HTTP routes delegate; they never build LLM providers nor drive the pipeline.

    Routes legitimately import runtime DTOs/helpers, pricing constants and
    credential resolution (documented benign). But constructing a provider
    (``llm.factory``) or running the engine (``runtime.pipeline`` /
    ``runtime.engine``) belongs in the service/runtime layer — e.g. ``files.py``
    delegates rewriting to ``assist.rewrite`` instead of building a provider
    inline.
    """
    forbidden = (
        "agentcore.llm.factory",
        "agentcore.runtime.pipeline",
        "agentcore.runtime.engine",
    )
    files = [f for f in _py_files("api/routes") if "inference" not in f.parts]
    assert _violations(files, forbidden) == {}


def test_llm_gateway_does_not_import_db() -> None:
    """The LLM gateway is a pure outbound adapter — no DB/business coupling.

    Exemptions are intentional llm↔db bridges:
    - ``provider_service`` / ``resolve`` — BYOK credential resolution
    - ``platform_credential_service`` — platform-pool credential CRUD + snapshot reload
      (boot/refresh opens a session; hot-path pick lives in db-free ``platform_pool``)
    - ``model_profiles`` — combo CRUD + expand (derived from catalog 上架; not metadata owner)
    - ``factory`` — ``build_turn_router`` may open a session to inject a cross-provider
      worker (agent provider_id ≠ chat provider)
    """
    bridge = {
        "provider_service.py",
        "resolve.py",
        "platform_credential_service.py",
        "model_profiles.py",
        "factory.py",
    }
    files = [f for f in _py_files("llm") if f.name not in bridge]
    assert _violations(files, ("agentcore.db",)) == {}


def test_db_does_not_import_runtime_or_conversation() -> None:
    """``db`` is a persistence leaf — no upward reach into runtime / conversation.

    Shared pure helpers that both db and conversation/runtime need live in leaf
    packages (``core.message_merge``, ``core.assistant_content``, ``costing``).
    Lease CRUD stays under ``runtime.leases`` and is imported from there by
    callers, not re-exported through ``db.repositories``.
    """
    files = _py_files("db")
    assert _violations(files, ("agentcore.runtime", "agentcore.conversation")) == {}


def test_core_has_no_upward_business_deps() -> None:
    """``core`` is the bottom layer: shared infra/types, zero business imports.

    It may depend on ``config`` (settings/logging/net wiring) but nothing above
    it — so ``core.net`` can host the shared SSRF/timeout primitives consumed by
    both the web tools and the favicon route without an ``api -> tools`` edge.
    """
    forbidden = (
        "agentcore.api",
        "agentcore.runtime",
        "agentcore.tools",
        "agentcore.llm",
        "agentcore.db",
        "agentcore.conversation",
        "agentcore.memory",
        "agentcore.board",
        "agentcore.evals",
        "agentcore.assist",
        "agentcore.vision",
        "agentcore.sidecar",
        "agentcore.conformance",
        "agentcore.workspace",
    )
    exempt = {"errors.py"}  # lazy-imports llm.errors for SSE error context projection
    files = [f for f in _py_files("core") if f.name not in exempt]
    assert _violations(files, forbidden) == {}


def test_leaf_web_tools_do_not_import_runtime_or_llm() -> None:
    """Leaf tools are self-contained — no reach into runtime/llm.

    (Orchestration primitives such as delegate/debate legitimately drive the
    runtime, so only the leaf web tools are asserted here.)
    """
    files = _py_files("tools/builtin/web")
    assert _violations(files, ("agentcore.runtime", "agentcore.llm")) == {}


def test_runtime_drive_and_coordination_do_not_import_tools_delegate() -> None:
    """Delegate drive / coordination sit in runtime — no tools.builtin.delegate edge.

    Composition roots (pipeline / resolve / recover) may still construct
    ``DelegateTool``; the forbidden cycle was ``coordination.host`` ↔
    ``tools.builtin.delegate.drive``. After the lift, ``runtime.delegate`` and
    ``runtime.coordination`` must not import the tools-side package at all.
    """
    files = _py_files("runtime/delegate", "runtime/coordination")
    assert _violations(files, ("agentcore.tools.builtin.delegate",)) == {}


def test_delegate_tools_package_is_thin_adapter() -> None:
    """``tools.builtin.delegate`` hosts schema + thin execute + nesting mint only."""
    allowed = {"__init__.py", "schema.py", "tool.py", "nesting.py"}
    present = {p.name for p in _py_files("tools/builtin/delegate")}
    assert present <= allowed, f"unexpected delegate tool modules: {present - allowed}"


def test_debate_tools_package_is_thin_adapter() -> None:
    """``tools.builtin.debate`` hosts schema + thin execute only；域逻辑在 runtime.debate。"""
    allowed = {"__init__.py", "schema.py", "tool.py"}
    present = {p.name for p in _py_files("tools/builtin/debate")}
    assert present <= allowed, f"unexpected debate tool modules: {present - allowed}"
    # 域驱动不得回留在 tools 包（rounds/prompt/events 已上收 runtime.debate）。
    assert not (present & {"rounds.py", "prompt.py", "events.py"})


def test_engine_stream_uses_public_retry_constants() -> None:
    """``engine.stream`` takes retry/backoff from ``llm.provider.protocol``, not privates."""
    stream = _PKG_ROOT / "runtime" / "engine" / "stream.py"
    imports = _module_imports(stream)
    assert "agentcore.llm.provider.protocol" in imports
    tree = ast.parse(stream.read_text(encoding="utf-8"), filename=str(stream))
    private_hits: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "agentcore.llm.provider.openai_compatible"
        ):
            for alias in node.names:
                if alias.name.startswith("_"):
                    private_hits.add(alias.name)
    assert private_hits == set(), f"stream imports provider privates: {private_hits}"
    src = stream.read_text(encoding="utf-8")
    assert "MAX_RETRIES" in src
    assert "INITIAL_BACKOFF" in src
    assert "BACKOFF_MULTIPLIER" in src


# ---------------------------------------------------------------------------
# runtime package scale (P3-A)
# ---------------------------------------------------------------------------

# Soft ceiling for *new* runtime modules. Existing oversized files are
# grandfathered below; shrink or split them when touching that area — do not
# grow the exemption set without an explicit decision.
_RUNTIME_LINE_SOFT_MAX = 800

_RUNTIME_OVERSIZE_EXEMPT: frozenset[str] = frozenset(
    {
        # Grandfathered at P3-A land (do not grow this set casually).
        "browser/registry.py",
        "coordination/host.py",
        "coordination/session.py",
        "debate/models.py",
        "debate/prompt.py",
        "debate/rounds.py",
        "debate/types.py",
        "delegate/completion.py",
        "delegate/delivery_status.py",
        "engine/governance.py",
        "engine/loop.py",
        "runs/builder.py",
        "runs/contract.py",
        "runs/executor/context.py",
        "runs/executor/loop.py",
        "runs/research_quality.py",
        "runs/wave.py",
    }
)

def test_runtime_no_new_oversized_modules_without_exemption() -> None:
    """Forbid new ``runtime/`` files above the soft line ceiling.

    P3-A keeps a single ``runtime`` package; navigability comes from subpackages
    + this scale latch. Grandfathered paths are listed in
    ``_RUNTIME_OVERSIZE_EXEMPT`` — adding to that set requires an explicit
    decision (prefer split / shrink instead).
    """
    root = _PKG_ROOT / "runtime"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        lines = sum(1 for _ in path.open(encoding="utf-8"))
        if lines <= _RUNTIME_LINE_SOFT_MAX:
            continue
        if rel in _RUNTIME_OVERSIZE_EXEMPT:
            continue
        offenders.append(f"{rel} ({lines} lines)")
    assert offenders == [], (
        "new runtime modules over "
        f"{_RUNTIME_LINE_SOFT_MAX} lines need a split or an explicit exemption:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_runs_executor_has_no_flat_shims() -> None:
    """``runs/executor/`` is the only path — forbid resurrecting ``runs/executor_*.py``."""
    runs = _PKG_ROOT / "runtime" / "runs"
    flat = sorted(p.name for p in runs.glob("executor_*.py"))
    assert flat == [], (
        "flat runs/executor_*.py shims are retired; use "
        "agentcore.runtime.runs.executor.<leaf>:\n  " + "\n  ".join(flat)
    )
    # Real imports only (AST) — ``…runs.executor`` package ok; ``…executor_*`` not.
    prefix = "agentcore.runtime.runs.executor_"
    bad_imports: list[str] = []
    for root in (
        _PKG_ROOT / "runtime",
        _PKG_ROOT / "tools",
        _PKG_ROOT / "evals",
        _SERVER_ROOT / "tests",
    ):
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                mods = _module_imports(path)
            except SyntaxError:
                continue
            for mod in mods:
                if mod.startswith(prefix):
                    bad_imports.append(
                        f"{path.relative_to(_SERVER_ROOT).as_posix()}: {mod}"
                    )
    assert bad_imports == [], (
        "old flat executor_* import paths still referenced:\n  " + "\n  ".join(bad_imports)
    )


def test_prompt_profile_has_no_flat_root_shim() -> None:
    """``resolve.profile`` is the only path — forbid resurrecting ``runtime/prompt_profile.py``."""
    flat = _PKG_ROOT / "runtime" / "prompt_profile.py"
    assert not flat.exists(), (
        "flat runtime/prompt_profile.py shim is retired; use "
        "agentcore.runtime.resolve.profile"
    )
    bad_mod = "agentcore.runtime.prompt_profile"
    bad_imports: list[str] = []
    for scan_root in (
        _PKG_ROOT / "runtime",
        _PKG_ROOT / "tools",
        _PKG_ROOT / "evals",
        _SERVER_ROOT / "tests",
    ):
        if not scan_root.is_dir():
            continue
        for path in scan_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                mods = _module_imports(path)
            except SyntaxError:
                continue
            for mod in mods:
                if mod == bad_mod or mod.startswith(bad_mod + "."):
                    bad_imports.append(
                        f"{path.relative_to(_SERVER_ROOT).as_posix()}: {mod}"
                    )
    assert bad_imports == [], (
        "old flat prompt_profile import paths still referenced:\n  "
        + "\n  ".join(bad_imports)
    )


def test_p3a_clusters_have_no_flat_root_shims() -> None:
    """Four P3-A clusters: package paths only — forbid resurrecting flat root shims.

    Covers ``closing_posture_*`` / ``loop_controller_*`` / ``turn_*`` / ``suspension_*``.
    """
    root = _PKG_ROOT / "runtime"
    flat_prefixes = (
        "closing_posture_",
        "loop_controller_",
        "turn_",
        "suspension_",
    )
    flat = sorted(p.name for p in root.glob("*.py") if p.name.startswith(flat_prefixes))
    assert flat == [], (
        "flat P3-A root shims are retired; use "
        "agentcore.runtime.<cluster>.<leaf> "
        "(closing_posture / loop_controller / turn / suspension):\n  "
        + "\n  ".join(flat)
    )
    bad_prefixes = (
        "agentcore.runtime.closing_posture_",
        "agentcore.runtime.loop_controller_",
        "agentcore.runtime.turn_",
        "agentcore.runtime.suspension_",
    )
    bad_imports: list[str] = []
    for scan_root in (
        _PKG_ROOT / "runtime",
        _PKG_ROOT / "tools",
        _PKG_ROOT / "evals",
        _SERVER_ROOT / "tests",
    ):
        if not scan_root.is_dir():
            continue
        for path in scan_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                mods = _module_imports(path)
            except SyntaxError:
                continue
            for mod in mods:
                if any(mod.startswith(p) for p in bad_prefixes):
                    bad_imports.append(
                        f"{path.relative_to(_SERVER_ROOT).as_posix()}: {mod}"
                    )
    assert bad_imports == [], (
        "old flat P3-A root import paths still referenced:\n  " + "\n  ".join(bad_imports)
    )
