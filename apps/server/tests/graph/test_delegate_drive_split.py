"""Smoke tests for drive.py phase split (behavior-preserving refactor)."""

from __future__ import annotations

import importlib
import inspect

import pytest

from agentcore.runtime.graph.drive.drive_redirect import RedirectController
from agentcore.runtime.graph.drive.drive_terminal import post_session_all_completed
from agentcore.runtime.graph.desk.worker_grant import maybe_auto_grant_before_workers
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.redirect_queue import RunRedirectRequest
from agentcore.runtime.runs.types import RunSpec


def _drive_mod():
    # Load the drive module by absolute path. The package init does not re-export it.
    return importlib.import_module("agentcore.runtime.graph.drive.drive")


def test_drive_public_exports_stable():
    drive_mod = _drive_mod()
    assert callable(drive_mod.drive)
    assert callable(drive_mod.drive_coordinated)
    assert drive_mod._post_session_all_completed is post_session_all_completed


def test_drive_signature_unchanged():
    sig = inspect.signature(_drive_mod().drive)
    params = list(sig.parameters)
    assert params[:2] == ["tool", "plan"]
    assert {
        "execution_id",
        "seed_completed",
        "complexity_hint",
        "call_idx",
        "coordinate",
        "session",
    }.issubset(sig.parameters)
    assert "completion_criteria" not in sig.parameters
    assert "seed_notes" not in sig.parameters
    assert "coordination" not in sig.parameters


def test_cold_fallback_mints_unique_redir_ids():
    plan = RunPlan(
        nodes=[
            RunSpec(run_id="w1", agent_id="w1", role="researcher", task="find facts"),
        ]
    )
    ctrl = RedirectController(
        tool=object(),
        plan=plan,
        execution_id="exec-1",
        worker_gate=None,
        session=None,
        total=1,
    )
    redir = RunRedirectRequest(
        execution_id="exec-1",
        run_id="w1",
        feedback="try another angle",
        conversation_id="c1",
    )
    first = ctrl.cold_fallback(plan.by_id("w1"), redir)
    assert first == "w1_redir"
    assert plan.by_id(first) is not None
    assert plan.by_id(first).replaces_run_id == "w1"
    assert plan.by_id(first).steer == "try another angle"
    second = ctrl.cold_fallback(plan.by_id("w1"), redir)
    assert second == "w1_redir2"


@pytest.mark.asyncio
async def test_auto_grant_skips_when_seeded():
    class _Tool:
        _depth = 0

    await maybe_auto_grant_before_workers(
        _Tool(),
        seed_completed={"a": object()},  # type: ignore[dict-item]
    )


@pytest.mark.asyncio
async def test_auto_grant_top_level_without_gate_is_noop():
    class _Tool:
        _depth = 0
        _permission_axes = None
        _base_tool_context = type("C", (), {"backend": None})()

    await maybe_auto_grant_before_workers(
        _Tool(),
        seed_completed=None,
    )


@pytest.mark.asyncio
async def test_auto_grant_light_without_gate_is_noop():
    from agentcore.core.types import WorkspaceBoundary

    class _Tool:
        _depth = 0
        _permission_axes = WorkspaceBoundary.FOLDER
        _pending_pause = False
        _base_tool_context = type("C", (), {"backend": None})()
        _approval_gate = None

    await maybe_auto_grant_before_workers(
        _Tool(),
        seed_completed=None,
    )
