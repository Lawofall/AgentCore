"""Sticky channel-dead + write-desk delegate hard gate."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentcore.runtime.delegate.channel_dead_gate import (
    CHANNEL_DEAD_WRITE_DESK_REJECT,
    channel_dead_write_desk_error,
    channel_dead_write_tasks_error,
)
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import Deliverable, RunSpec


def _tool(*, execution_id: str = "exec-cd", conversation_id: str = "conv-cd") -> MagicMock:
    t = MagicMock()
    t._conversation_id = conversation_id
    t._base_tool_context = SimpleNamespace(execution_id=execution_id, backend=None)
    t._folder_id = "test_birth"
    t._depth = 0
    return t


def _files_plan() -> RunPlan:
    return RunPlan(
        nodes=[
            RunSpec(
                run_id="w1",
                role="写手",
                task="落盘报告",
                deliverable=Deliverable(form="files"),
            )
        ]
    )


def _prose_plan() -> RunPlan:
    return RunPlan(
        nodes=[
            RunSpec(
                run_id="p1",
                role="顾问",
                task="口头总结",
                deliverable=Deliverable(form="prose"),
            )
        ]
    )


def test_rejects_files_form_when_session_workspace_channel_dead():
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )

    clear_active_coordination()
    session = CoordinationSession(execution_id="exec-cd", total_workers=1)
    session.conversation_id = "conv-cd"
    session.workspace_channel_dead = True
    set_active_coordination(session)
    try:
        err = channel_dead_write_desk_error(_tool(), _files_plan())
        assert err == CHANNEL_DEAD_WRITE_DESK_REJECT
    finally:
        clear_active_coordination("exec-cd")


def test_allows_prose_when_session_workspace_channel_dead():
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )

    clear_active_coordination()
    session = CoordinationSession(execution_id="exec-cd", total_workers=1)
    session.conversation_id = "conv-cd"
    session.workspace_channel_dead = True
    set_active_coordination(session)
    try:
        assert channel_dead_write_desk_error(_tool(), _prose_plan()) is None
    finally:
        clear_active_coordination("exec-cd")


def test_allows_files_when_channel_alive():
    assert channel_dead_write_desk_error(_tool(), _files_plan()) is None


def test_rejects_when_backend_channel_is_dead():
    from agentcore.runtime.interaction import InteractionRegistry
    from agentcore.workspace.channel import WorkspaceChannel

    channel = WorkspaceChannel(
        user_id="u-test",
        conversation_id="conv-cd",
        registry=InteractionRegistry(),
        timeout_seconds=1.0,
    )
    channel._dead = True  # noqa: SLF001 — sticky dead stamp
    tool = _tool()
    tool._base_tool_context = SimpleNamespace(
        execution_id="exec-other",
        backend=SimpleNamespace(_channel=channel),
    )
    err = channel_dead_write_desk_error(tool, _files_plan())
    assert err == CHANNEL_DEAD_WRITE_DESK_REJECT
    assert channel_dead_write_desk_error(tool, _prose_plan()) is None


def test_skip_completed_write_nodes_allows_prose_tail():
    """Resume: completed files node skipped; pending prose → allow."""
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )

    clear_active_coordination()
    session = CoordinationSession(execution_id="exec-cd", total_workers=2)
    session.conversation_id = "conv-cd"
    session.workspace_channel_dead = True
    set_active_coordination(session)
    try:
        plan = RunPlan(
            nodes=[
                RunSpec(
                    run_id="done",
                    role="写手",
                    task="已落盘",
                    deliverable=Deliverable(form="files"),
                ),
                RunSpec(
                    run_id="tail",
                    role="顾问",
                    task="总结",
                    deliverable=Deliverable(form="prose"),
                ),
            ]
        )
        assert (
            channel_dead_write_desk_error(_tool(), plan, skip_run_ids={"done"}) is None
        )
        assert channel_dead_write_desk_error(_tool(), plan) == CHANNEL_DEAD_WRITE_DESK_REJECT
    finally:
        clear_active_coordination("exec-cd")


def test_write_tasks_error_for_replan_adds():
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )

    clear_active_coordination()
    session = CoordinationSession(execution_id="exec-cd", total_workers=1)
    session.conversation_id = "conv-cd"
    session.workspace_channel_dead = True
    set_active_coordination(session)
    try:
        err = channel_dead_write_tasks_error(
            _tool(),
            [{"role": "写手", "task": "补文件", "deliverable": {"form": "files"}}],
        )
        assert err == CHANNEL_DEAD_WRITE_DESK_REJECT
        assert (
            channel_dead_write_tasks_error(
                _tool(),
                [{"role": "顾问", "task": "补文", "deliverable": {"form": "prose"}}],
            )
            is None
        )
    finally:
        clear_active_coordination("exec-cd")


@pytest.mark.asyncio
async def test_delegate_rejects_files_form_when_session_workspace_channel_dead():
    """drive 冷开：session sticky dead + form=files → contract_failure。"""
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )
    from agentcore.runtime.events import EventSink
    from tests.delegate.conftest import ctx, tool
    from tests.delegate.test_coordination_secondary_delegate import _SlowWorkers

    clear_active_coordination()
    session = CoordinationSession(execution_id="e", total_workers=1)
    session.conversation_id = "c"
    session.workspace_channel_dead = True
    session.active = False  # sticky flag only — not a live merge host
    set_active_coordination(session)
    try:
        sink = EventSink()
        t = tool(_SlowWorkers(["ok"], delay=0.01), sink=sink)
        # Keep execution_id aligned with sticky session (ctx default "e").
        t._base_tool_context = ctx()
        result = await t.execute(
            {
                "tasks": [
                    {
                        "role": "写手",
                        "task": "写文件",
                        "deliverable": {"form": "files"},
                    }
                ],
                "coordinate": False,
            },
            ctx(),
        )
        assert result.success is False
        assert result.contract_failure is True
        assert "channel dead" in (result.error or "").lower()
        assert "写盘" in (result.error or "")
    finally:
        clear_active_coordination()


@pytest.mark.asyncio
async def test_delegate_allows_prose_when_session_workspace_channel_dead():
    """drive：channel dead 时 prose 仍可派。"""
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )
    from agentcore.runtime.events import EventSink
    from tests.delegate.conftest import ctx, tool
    from tests.delegate.test_coordination_secondary_delegate import _SlowWorkers

    clear_active_coordination()
    session = CoordinationSession(execution_id="e", total_workers=1)
    session.conversation_id = "c"
    session.workspace_channel_dead = True
    session.active = False
    set_active_coordination(session)
    try:
        sink = EventSink()
        t = tool(_SlowWorkers(["ok"], delay=0.01), sink=sink)
        result = await t.execute(
            {
                "tasks": [
                    {
                        "role": "顾问",
                        "task": "口头总结",
                        "deliverable": {"form": "prose"},
                    }
                ],
                "coordinate": False,
            },
            ctx(),
        )
        assert result.success is True
    finally:
        clear_active_coordination()


@pytest.mark.asyncio
async def test_apply_replan_rejects_files_add_when_channel_dead():
    """supervised replan adds 绕过 drive 冷开时仍拒写盘节点。"""
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )
    from agentcore.runtime.delegate.supervised import apply_replan
    from agentcore.runtime.runs.types import RunPhase, RunState

    clear_active_coordination()
    session = CoordinationSession(execution_id="exec-cd", total_workers=2)
    session.conversation_id = "conv-cd"
    session.workspace_channel_dead = True
    set_active_coordination(session)
    try:

        class _FakeTools:
            def list_all(self):
                return []

        class _FakeDelegate:
            _tools = _FakeTools()
            _captain_run_id = "cap"
            _depth = 0
            _topology_lock = False
            _folder_id = "test_birth"
            _conversation_id = "conv-cd"
            _user_message = None
            _base_tool_context = SimpleNamespace(
                execution_id="exec-cd",
                turn_target_desk=None,
                user_id="u",
                conversation_id="conv-cd",
            )

            def effective_default_target_folder_id(self) -> str | None:
                return None

        plan = RunPlan(nodes=[RunSpec(run_id="ok", role="A", task="done")])
        completed = {"ok": RunState(phase=RunPhase.COMPLETED, content="ok")}
        err = await apply_replan(
            _FakeDelegate(),
            plan,
            completed,
            binds=[],
            steers=[],
            adds=[
                {
                    "role": "写手",
                    "task": "补落盘",
                    "deliverable": {"form": "files"},
                    "target_folder_id": "desk1",
                }
            ],
        )
        assert err
        assert any("channel dead" in e.lower() for e in err)
    finally:
        clear_active_coordination("exec-cd")


def test_ceo_inject_names_workspace_channel_dead():
    from agentcore.runtime.coordination.inject import format_coordination_events
    from agentcore.runtime.coordination.session import (
        CoordinationEvent,
        CoordinationEventKind,
        CoordinationSession,
    )
    from agentcore.workspace.limits import CHANNEL_DEAD_CEO_INJECT, CHANNEL_DEAD_USER_VISIBLE

    session = CoordinationSession(execution_id="exec-cd-inj", total_workers=1)
    session.workspace_channel_dead = True
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.WORKER_COMPLETED,
                payload={
                    "run_id": "w1",
                    "role": "写手",
                    "status": "completed",
                    "summary": "ok",
                },
            )
        ],
    )
    assert CHANNEL_DEAD_CEO_INJECT in text
    assert CHANNEL_DEAD_USER_VISIBLE in text
    for token in ("工作区/本地文件连不上", "稍后重试", "重开桌面", "已有材料收口"):
        assert token in text
    assert "禁止再派需要写盘的队员" in text


def test_ceo_inject_user_stop_unchanged_when_channel_dead():
    from agentcore.runtime.coordination.cancel_close import USER_STOPPED_MARK
    from agentcore.runtime.coordination.inject import format_coordination_events
    from agentcore.runtime.coordination.session import (
        CoordinationEvent,
        CoordinationEventKind,
        CoordinationSession,
    )

    session = CoordinationSession(execution_id="exec-cd-stop", total_workers=2)
    session.user_stopped = True
    session.workspace_channel_dead = True
    session._worker_started_at["w1"] = 1.0  # noqa: SLF001
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={"completed": 1, "total": 2, "cancelled": True},
            )
        ],
    )
    assert USER_STOPPED_MARK in text
    assert "调度中断" not in text
