"""apply_delegation_grant ownership for kickoff / merge-rearm (工具审批 A+B · B)."""

from __future__ import annotations

from types import SimpleNamespace

from agentcore.core.types import WorkspaceBoundary
from agentcore.runtime.approvals import ApprovalGate
from agentcore.runtime.graph.drive.drive_setup import apply_delegation_grant
from agentcore.runtime.events import EventSink
from agentcore.runtime.interaction import InteractionRegistry


def _gate() -> ApprovalGate:
    return ApprovalGate(
        sink=EventSink(),
        conversation_id="c",
        registry=InteractionRegistry(),
        timeout_seconds=5.0,
        delegation_grantable_tools=frozenset({"terminal", "code_execute"}),
    )


def test_apply_grant_fresh_full_auto_owns_segment():
    gate = _gate()
    tool = SimpleNamespace(
        _auto_grant_pending=False,
        _permission_axes=WorkspaceBoundary.FOLDER,
    )
    assert apply_delegation_grant(
        tool, execution_id="e1", worker_gate=gate, seed_completed=None
    )
    assert gate.has_delegation_grant("e1")


def test_apply_grant_already_present_does_not_own_revoke():
    """Kickoff already granted → merge-rearm must not take revoke ownership."""
    gate = _gate()
    gate.grant_delegation("e1")
    tool = SimpleNamespace(
        _auto_grant_pending=False,
        _permission_axes=WorkspaceBoundary.FOLDER,
    )
    assert not apply_delegation_grant(
        tool, execution_id="e1", worker_gate=gate, seed_completed=None
    )
    assert gate.has_delegation_grant("e1")


def test_apply_grant_seed_completed_is_noop():
    gate = _gate()
    tool = SimpleNamespace(
        _auto_grant_pending=True,
        _permission_axes=WorkspaceBoundary.FOLDER,
    )
    assert not apply_delegation_grant(
        tool,
        execution_id="e1",
        worker_gate=gate,
        seed_completed={"r1": object()},  # type: ignore[dict-item]
    )
    assert not gate.has_delegation_grant("e1")
