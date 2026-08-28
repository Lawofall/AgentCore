"""External-mount op policy parity: Python ↔ desktop dispatch gate.

The session-grant gate is hand-maintained on both ends (server routing layer +
desktop ``workspace/sessionRoot.ts``). Both are whitelists, so a newly added
``WorkspaceOp`` must be classified on both ends before it can run against a W3
session grant — this ratchet is what makes that "ring" instead of defaulting to
allow on the desktop and deny on the server. Deny-sentence copy is the same
mirror (file_ops must recognize the desktop wording without a hand-copied scrape).

Same spirit (and same regex-extraction approach) as
``test_workspace_ignore_parity.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentcore.workspace import external_mounts as em
from agentcore.workspace.channel import WorkspaceOp

_DESKTOP_SRC = Path(__file__).resolve().parents[2] / "desktop" / "src"
_TS_GATE = _DESKTOP_SRC / "main" / "fs" / "workspace" / "sessionRoot.ts"
_TS_CONTRACT = _DESKTOP_SRC / "shared" / "ipc-contract.ts"

_STRING_LIT = re.compile(r'"([^"]+)"')


def _ts_op_set(src: str, name: str) -> frozenset[str]:
    """Members of ``export const NAME = new Set<WorkspaceOpName>([...])``."""
    m = re.search(
        rf"export const {re.escape(name)}\s*=\s*new Set<WorkspaceOpName>\(\[(.*?)\]\)",
        src,
        flags=re.DOTALL,
    )
    if not m:
        raise AssertionError(f"TypeScript set {name!r} not found in {_TS_GATE.name}")
    members = _STRING_LIT.findall(m.group(1))
    assert members, f"TypeScript set {name!r} parsed empty (parse failure?)"
    return frozenset(members)


def _ts_op_names(src: str) -> frozenset[str]:
    """Members of the ``WorkspaceOpName`` string union."""
    m = re.search(r"export type WorkspaceOpName\s*=(.*?);", src, flags=re.DOTALL)
    if not m:
        raise AssertionError(f"WorkspaceOpName union not found in {_TS_CONTRACT.name}")
    names = _STRING_LIT.findall(m.group(1))
    assert names, "WorkspaceOpName union parsed empty (parse failure?)"
    return frozenset(names)


def _ts_string_const(src: str, name: str) -> str:
    """Value of ``const NAME = "…"`` (assignment may break across lines)."""
    m = re.search(rf"const {re.escape(name)}\s*=\s*\"([^\"]+)\"", src)
    if not m:
        raise AssertionError(f"TypeScript const {name!r} not found in {_TS_GATE.name}")
    return m.group(1)


@pytest.fixture(scope="module")
def ts_gate() -> str:
    assert _TS_GATE.is_file(), f"missing desktop gate: {_TS_GATE}"
    return _TS_GATE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ts_contract() -> str:
    assert _TS_CONTRACT.is_file(), f"missing desktop contract: {_TS_CONTRACT}"
    return _TS_CONTRACT.read_text(encoding="utf-8")


def test_op_universe_matches(ts_contract: str):
    """``WorkspaceOp`` (Python) and ``WorkspaceOpName`` (TypeScript) are one closed set."""
    assert _ts_op_names(ts_contract) == frozenset(op.value for op in WorkspaceOp)


def test_desktop_gate_mirrors_server_policy(ts_gate: str):
    """Each bucket matches; the desktop readonly face is ALLOWED − MUTATION."""
    assert _ts_op_set(ts_gate, "READONLY_ALLOWED_OPS") == em.READONLY_ALLOWED_OPS
    assert _ts_op_set(ts_gate, "ORGANIZE_MUTATION_OPS") == em.ORGANIZE_MUTATION_OPS
    assert _ts_op_set(ts_gate, "ORGANIZE_DENIED_OPS") == em.ORGANIZE_DENIED_OPS
    assert em.READONLY_ALLOWED_OPS | em.ORGANIZE_MUTATION_OPS == em.ORGANIZE_ALLOWED_OPS


def test_desktop_gate_mirrors_server_copy(ts_gate: str):
    """Deny sentences must match; a wording change on either end turns this red."""
    assert _ts_string_const(ts_gate, "READONLY_MSG") == em._READONLY_MSG
    assert _ts_string_const(ts_gate, "ORGANIZE_DENY_MSG") == em._ORGANIZE_DENY_MSG
    assert _ts_string_const(ts_gate, "PERMANENT_EXTERNAL_MSG") == em._PERMANENT_EXTERNAL_MSG


def test_copy_wording_drift_turns_red(ts_gate: str):
    """Sanity: the copy ratchet is not a tautology that stays green after a rewrite."""
    drifted = ts_gate.replace(em._READONLY_MSG, em._READONLY_MSG + "（改词）", 1)
    assert drifted != ts_gate
    assert _ts_string_const(drifted, "READONLY_MSG") != em._READONLY_MSG


def test_policy_is_exhaustive_over_workspace_ops(ts_gate: str):
    """A new op must be classified on both ends (whitelists → otherwise denied)."""
    ops = frozenset(op.value for op in WorkspaceOp)
    for label, readonly, mutation, denied in (
        (
            "python",
            em.READONLY_ALLOWED_OPS,
            em.ORGANIZE_MUTATION_OPS,
            em.ORGANIZE_DENIED_OPS,
        ),
        (
            "typescript",
            _ts_op_set(ts_gate, "READONLY_ALLOWED_OPS"),
            _ts_op_set(ts_gate, "ORGANIZE_MUTATION_OPS"),
            _ts_op_set(ts_gate, "ORGANIZE_DENIED_OPS"),
        ),
    ):
        classified = readonly | mutation | denied
        assert classified == ops, (
            f"{label}: unclassified ops {sorted(ops - classified)} / "
            f"unknown ops {sorted(classified - ops)} — classify them in both "
            f"external_mounts.py and sessionRoot.ts"
        )
        assert len(readonly) + len(mutation) + len(denied) == len(ops), (
            f"{label}: op classified into more than one bucket"
        )


def test_organize_mutation_gate_still_denies_non_mutations():
    """Routing-layer gate: organize only opens move/copy/mkdir/delete."""
    mount = em.ExternalMount(alias="desk", root_id="r", label="桌面", mode="organize")
    for op in em.ORGANIZE_MUTATION_OPS:
        assert em.external_mutation_allowed(mount, op) is None
    for op in em.ORGANIZE_DENIED_OPS | em.READONLY_ALLOWED_OPS:
        assert em.external_mutation_allowed(mount, op) is not None
    assert em.external_mutation_allowed(mount, "delete", permanent=True) is not None


def test_attach_rw_allows_writes_but_not_permanent_delete():
    mount = em.ExternalMount(alias="desk", root_id="r", label="桌面", mode="attach_rw")
    for op in ("write", "replace", "append", "execute", "copy", "mkdir", "delete"):
        assert em.external_mutation_allowed(mount, op) is None
    assert em.external_mutation_allowed(mount, "delete", permanent=True) is not None
