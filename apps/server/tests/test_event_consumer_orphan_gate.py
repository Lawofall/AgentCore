"""Consumer-orphan gate — contract-registered types without backend producers."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agentcore.conformance.consumer_orphan_gate import (
    ConsumerOrphanResult,
    ContractRef,
    OrphanReport,
    _line_emits_enum,
    _listed_contract_rels,
    _missing_listed_files,
    _repo_root,
    format_orphan_reports,
    run_consumer_orphan_gate,
)


def test_line_emits_enum_matches_assignment_not_comparison():
    """Producer vs consumer-read paths — string scan must keep the old regex semantics."""
    assert _line_emits_enum(
        "sink.emit(type=EventType.CONTENT_DELTA, payload={})", "CONTENT_DELTA"
    )
    assert _line_emits_enum(
        "sink.emit(type = EventType.CONTENT_DELTA, payload={})", "CONTENT_DELTA"
    )
    assert _line_emits_enum(
        "sink.emit(type=EventType.CONTENT_DELTA.value)", "CONTENT_DELTA"
    )
    assert not _line_emits_enum(
        "if event.type == EventType.CONTENT_DELTA:", "CONTENT_DELTA"
    )
    assert not _line_emits_enum(
        "if event.type is EventType.CONTENT_DELTA:", "CONTENT_DELTA"
    )
    assert _line_emits_enum("build(EventType.CONTENT_DELTA, type=x)", "CONTENT_DELTA")
    assert not _line_emits_enum("x = EventType.CONTENT_DELTA", "CONTENT_DELTA")
    # Substring prefix: GATE assignment also counted as RUN_ESCALATION (legacy).
    assert _line_emits_enum(
        "emit(type=EventType.RUN_ESCALATION_GATE)", "RUN_ESCALATION_GATE"
    )
    assert _line_emits_enum("emit(type=EventType.RUN_ESCALATION_GATE)", "RUN_ESCALATION")


def test_repo_root_is_monorepo_and_listed_files_exist():
    root = _repo_root()
    assert (root / "packages" / "contract-types").is_dir()
    assert _missing_listed_files(root) == []
    listed = _listed_contract_rels()
    assert listed == (
        "packages/contract-types/src/eventTypes.generated.ts",
        "packages/contract-types/src/interactionKinds.generated.ts",
        "packages/contract-types/src/events.generated.ts",
    )
    assert "apps/mobile/src/protocol/fold.ts" not in listed
    assert "apps/desktop/src/renderer/lib/processTimeline.ts" not in listed


def test_consumer_orphan_gate_clean_tree():
    """Both directions green unless a new orphan appears."""
    result = run_consumer_orphan_gate()
    assert result.errors == []
    assert result.orphans == [], format_orphan_reports(result)
    assert result.producer_orphans == [], format_orphan_reports(result)
    assert result.ok


def test_format_orphan_reports_explains_tsc_workflow():
    orphan = OrphanReport(
        surface="sse",
        key="followups_generated",
        contract_refs=(
            ContractRef(
                surface="sse",
                key="followups_generated",
                rel_path="packages/contract-types/src/eventTypes.generated.ts",
                label="contract eventTypes.generated",
            ),
        ),
    )
    lines = format_orphan_reports(ConsumerOrphanResult(ok=False, orphans=[orphan]))
    joined = "\n".join(lines)
    assert "followups_generated" in lines[0]
    assert "no live Python producer" in lines[0]
    assert "packages/contract-types/src/eventTypes.generated.ts" in joined
    assert "delete this type from the contract" in joined
    assert "tsc will fail at every remaining frontend consumer" in joined
    assert "PROCESS_STEP_KIND" in joined


def test_coverage_lists_contract_files_not_frontend():
    result = run_consumer_orphan_gate()
    assert result.coverage is not None
    opened = set(result.coverage.contract_files)
    assert opened == {
        "packages/contract-types/src/eventTypes.generated.ts",
        "packages/contract-types/src/interactionKinds.generated.ts",
        "packages/contract-types/src/events.generated.ts",
    }
    assert result.coverage.repo_root == str(_repo_root())
    assert result.coverage.producer_py_count > 0
    assert result.coverage.registered_sse > 0
    assert result.coverage.registered_process_step > 0
    ui = set(result.coverage.ui_files)
    assert "apps/desktop/src/renderer/services/sse/dispatch.ts" in ui
    assert any(
        rel.startswith("apps/desktop/src/renderer/services/sse/handlers/")
        for rel in ui
    )
    assert "apps/mobile/src/protocol/fold.ts" not in result.coverage.contract_files


def test_gate_flags_contract_key_without_python_producer(monkeypatch):
    """Positive path: a contract-only key with no producer must fail the gate."""
    import agentcore.conformance.consumer_orphan_gate as gate

    probe = "__orphan_probe__"
    real_sse = gate._sse_contract_types
    real_process = gate._process_step_contract_kinds

    monkeypatch.setattr(
        gate,
        "_sse_contract_types",
        lambda root, cache: real_sse(root, cache) | frozenset({probe}),
    )
    monkeypatch.setattr(
        gate,
        "_process_step_contract_kinds",
        lambda root, cache: real_process(root, cache) | frozenset({probe}),
    )

    result = gate.run_consumer_orphan_gate()
    assert not result.ok
    keyed = {(o.surface, o.key) for o in result.orphans}
    assert ("sse", probe) in keyed
    assert ("process_step", probe) in keyed
    joined = "\n".join(gate.format_orphan_reports(result))
    assert "delete this type from the contract" in joined
    assert "tsc will fail at every remaining frontend consumer" in joined


def _desktop_tsc_bin() -> Path:
    desktop = _repo_root() / "apps" / "desktop"
    bin_dir = desktop / "node_modules" / ".bin"
    tsc = bin_dir / ("tsc.cmd" if os.name == "nt" else "tsc")
    if not tsc.is_file():
        pytest.skip(
            "desktop tsc not installed — backend CI has no apps/desktop node_modules"
        )
    return tsc


def _write_process_step_tsc_probe(tmp_path, body: str) -> Path:
    desktop = _repo_root() / "apps" / "desktop"
    probe = tmp_path / "processStepKind.probe.ts"
    probe.write_text(body, encoding="utf-8")
    tsconfig = tmp_path / "tsconfig.json"
    tsconfig.write_text(
        json.dumps(
            {
                "extends": str((desktop / "tsconfig.json").resolve()).replace("\\", "/"),
                "compilerOptions": {
                    "noEmit": True,
                    "incremental": False,
                    "baseUrl": str(desktop.resolve()).replace("\\", "/"),
                    "paths": {
                        "@/*": ["src/renderer/*"],
                        "@shared/*": ["src/shared/*"],
                    },
                },
                "files": [str(probe.resolve()).replace("\\", "/")],
                "include": [],
            }
        ),
        encoding="utf-8",
    )
    return tsconfig


@pytest.mark.timeout(120)
def test_process_step_kind_record_fails_tsc_when_kind_added(tmp_path):
    """Positive path: PROCESS_STEP_KIND is a closed Record — extra kind → tsc red.

    Control probe (assignable to Record<ProcessStep['kind'], true>) must be green,
    otherwise a broken tsc harness would fake-red the extra-kind probe.
    """
    desktop = _repo_root() / "apps" / "desktop"
    tsc = _desktop_tsc_bin()
    header = """\
import type { ProcessStep } from "@/types/events";
import { PROCESS_STEP_KIND } from "@/lib/processTimeline";
"""
    control_dir = tmp_path / "control"
    control_dir.mkdir()
    control_tsconfig = _write_process_step_tsc_probe(
        control_dir,
        header
        + "export const shouldPass: Record<ProcessStep['kind'], true> = "
        + "PROCESS_STEP_KIND;\n",
    )
    control = subprocess.run(
        [str(tsc), "--pretty", "false", "-p", str(control_tsconfig)],
        cwd=str(desktop),
        capture_output=True,
        text=True,
        check=False,
    )
    assert control.returncode == 0, control.stdout + control.stderr

    fail_dir = tmp_path / "fail"
    fail_dir.mkdir()
    fail_tsconfig = _write_process_step_tsc_probe(
        fail_dir,
        header
        + """\
type Grown = ProcessStep | { kind: "__orphan_probe__" };
export const shouldFail: Record<Grown["kind"], true> = PROCESS_STEP_KIND;
""",
    )
    completed = subprocess.run(
        [str(tsc), "--pretty", "false", "-p", str(fail_tsconfig)],
        cwd=str(desktop),
        capture_output=True,
        text=True,
        check=False,
    )
    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0, combined
    assert "__orphan_probe__" in combined, combined


def test_allowlists_default_empty():
    from agentcore.conformance.consumer_orphan_allowlist import (
        CONSUMER_ORPHAN_ALLOWLIST,
        PRODUCER_ORPHAN_ALLOWLIST,
    )

    assert CONSUMER_ORPHAN_ALLOWLIST == {}
    assert PRODUCER_ORPHAN_ALLOWLIST == {}


def test_gate_flags_producer_without_desktop_live_ui(monkeypatch):
    """Positive path: a registered producer with no live UI hit must fail direction B."""
    import agentcore.conformance.consumer_orphan_gate as gate

    probe = "__producer_ui_probe__"
    real_producers = gate._scan_sse_producers
    real_sse = gate._registered_sse

    def fake_producers(root, cache):
        out = real_producers(root, cache)
        out.setdefault(probe, [("runtime/events/chat.py", 1)])
        return out

    monkeypatch.setattr(gate, "_scan_sse_producers", fake_producers)
    monkeypatch.setattr(
        gate,
        "_registered_sse",
        lambda root, cache: real_sse(root, cache) | frozenset({probe}),
    )

    result = gate.run_consumer_orphan_gate()
    assert not result.ok
    keyed = {(o.surface, o.key) for o in result.producer_orphans}
    assert ("sse", probe) in keyed
    joined = "\n".join(gate.format_orphan_reports(result))
    assert "no desktop live UI consumer" in joined
    assert "runtime/events/chat.py:1" in joined


def test_release_gate_wires_log_catalog_check_and_orphan_cli():
    gate = _repo_root() / "scripts" / "release-gate.mjs"
    src = gate.read_text(encoding="utf-8")
    assert "sync_log_event_registry.py" in src
    assert "--check" in src
    assert "check_event_consumer_orphans.py" in src


def test_graph_append_is_not_flagged_as_orphan():
    """BY-DESIGN: factory kept for old journal replay; live handler still consumes it."""
    result = run_consumer_orphan_gate()
    keys = {o.key for o in result.orphans} | {o.key for o in result.producer_orphans}
    assert "graph_append" not in keys
