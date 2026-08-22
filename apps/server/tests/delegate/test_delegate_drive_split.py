"""Smoke tests for drive.py phase split (behavior-preserving refactor)."""

from __future__ import annotations

import importlib
import inspect

import pytest

from agentcore.runtime.delegate.drive_preview import team_preview_before_workers
from agentcore.runtime.delegate.drive_redirect import RedirectController
from agentcore.runtime.delegate.drive_terminal import post_session_all_completed
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.redirect_queue import RunRedirectRequest
from agentcore.runtime.runs.types import RunSpec


def _drive_mod():
    # Package ``__init__`` re-exports ``drive`` the function, shadowing the submodule
    # name on ``agentcore.runtime.delegate`` — load by absolute module path.
    return importlib.import_module("agentcore.runtime.delegate.drive")


def test_drive_public_exports_stable():
    drive_mod = _drive_mod()
    assert callable(drive_mod.drive)
    assert callable(drive_mod.drive_coordinated)
    # Private helpers remain importable from drive (existing tests rely on this).
    assert drive_mod._team_preview_before_workers is team_preview_before_workers
    assert drive_mod._post_session_all_completed is post_session_all_completed


def test_drive_signature_unchanged():
    sig = inspect.signature(_drive_mod().drive)
    params = list(sig.parameters)
    assert params[:2] == ["tool", "plan"]
    assert {
        "execution_id",
        "seed_completed",
        "seed_notes",
        "complexity_hint",
        "coordination",
        "call_idx",
        "coordinate",
        "session",
    }.issubset(sig.parameters)
    assert "completion_criteria" not in sig.parameters


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
async def test_team_preview_skips_when_seeded():
    class _Tool:
        _depth = 0

    plan = RunPlan(nodes=[RunSpec(run_id="a", agent_id="a", role="r", task="t")])
    result = await team_preview_before_workers(
        _Tool(),
        plan,
        complexity_hint="standard",
        seed_completed={"a": object()},  # type: ignore[dict-item]
        call_idx=0,
    )
    assert result is None


@pytest.mark.asyncio
async def test_team_preview_skips_light_handwritten():
    """普通 light 手写任务仍跳过开工卡（早返回，不进 kickoff）。"""

    class _Tool:
        _depth = 0
        _active_playbook = None
        _permission_axes = None
        _base_tool_context = type("C", (), {"backend": None})()

    plan = RunPlan(nodes=[RunSpec(run_id="a", agent_id="a", role="r", task="t")])
    result = await team_preview_before_workers(
        _Tool(),
        plan,
        complexity_hint="light",
        seed_completed=None,
        call_idx=0,
    )
    assert result is None


@pytest.mark.asyncio
async def test_team_preview_light_with_capability_auth_does_not_skip():
    """light 任务也不再挂新开工卡。"""
    from agentcore.core.types import AutonomyPolicy

    class _Tool:
        _depth = 0
        _permission_axes = AutonomyPolicy.LESS_INTERRUPT
        _active_playbook = None
        _pending_pause = False
        _base_tool_context = type("C", (), {"backend": None})()
        _approval_gate = None

    plan = RunPlan(nodes=[RunSpec(run_id="a", agent_id="a", role="r", task="t")])
    result = await team_preview_before_workers(
        _Tool(),
        plan,
        complexity_hint="light",
        seed_completed=None,
        call_idx=0,
    )
    assert result is None
