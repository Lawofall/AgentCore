"""D1: coordination-active blocking escalate → CEO resolve_escalation."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcore.runtime.coordination.inject import format_coordination_events
from agentcore.runtime.coordination.session import (
    CoordinationEvent,
    CoordinationEventKind,
    CoordinationSession,
    clear_active_coordination,
    set_active_coordination,
)
from agentcore.runtime.coordination.tools import ResolveEscalationTool
from agentcore.runtime.interaction import InteractionKind, InteractionRegistry
from agentcore.tools.builtin.escalate import EscalateTool, escalate_tool_result
from agentcore.tools.protocol import EscalationChannel, EscalationOutcome, ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def _ctx(
    *,
    execution_id: str = "e-d1",
    escalation: EscalationChannel | None = None,
    run_id: str = "r1",
) -> ToolContext:
    return ToolContext.create(
        execution_id=execution_id,
        run_id=run_id,
        agent_id="w1",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="c1",
        agent_role="研究员",
        escalation=escalation,
    )


def test_escalate_tool_result_ceo_wording():
    resolved = escalate_tool_result("resolved", "用 Postgres", "暂按 MySQL", arbitrated_by="ceo")
    assert "主管就你的升级问题裁决" in resolved.output
    assert "用 Postgres" in resolved.output
    user = escalate_tool_result("resolved", "用 Postgres", "暂按 MySQL", arbitrated_by="user")
    assert "用户就你的升级问题答复" in user.output


def test_inject_blocking_escalation_prompts_resolve():
    session = CoordinationSession(execution_id="e", total_workers=2)
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ESCALATION,
                payload={
                    "run_id": "r1",
                    "role": "研究员",
                    "kind": "normal",
                    "question": "选 Postgres 还是 MySQL？",
                    "assumption": "暂按 Postgres",
                    "blocking": True,
                    "source": "blocking_arbitrate",
                },
            )
        ],
    )
    assert "阻塞仲裁" in text
    assert "resolve_escalation" in text
    assert "via_user=true" in text
    assert "ask_user" in text
    assert "transfer_ownership" not in text


def test_inject_ownership_conflict_flags_nested_child():
    session = CoordinationSession(execution_id="e", total_workers=2)
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ESCALATION,
                payload={
                    "run_id": "storage",
                    "role": "存储层",
                    "kind": "dep",
                    "question": "写入冲突：`src/storage/db.ts` 已归队友",
                    "assumption": "等主管移交",
                    "blocking": True,
                    "source": "blocking_arbitrate",
                    "ownership_paths": ["src/storage/db.ts"],
                    "lock_owner_run_id": "backend-fix",
                    "escalator_is_lock_owner_nested_child": True,
                    "ownership_kind": "declared",
                    "owner_status": "running",
                },
            )
        ],
    )
    assert "嵌套子队" not in text
    assert "transfer_ownership=true" not in text
    assert "仅派发占位未落盘" not in text


@pytest.mark.asyncio
async def test_resolve_escalation_transfer_ownership_paths():
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-own", total_workers=2)
    set_active_coordination(session)
    ledger = session.ensure_file_ownership()
    ledger.declare("src/storage/db.ts", "backend-fix", frozenset())
    ledger.declare("src/tools/base.ts", "backend-fix", frozenset())
    session.register_arbitration(
        "storage",
        escalation_id="esc-1",
        conversation_id="c1",
        question="写入冲突：`src/storage/db.ts`",
        assumption="等移交",
        ownership_paths=["src/storage/db.ts"],
        lock_owner_run_id="backend-fix",
        escalator_is_lock_owner_nested_child=True,
    )
    tool = ResolveEscalationTool()
    result = await tool.execute(
        {
            "run_id": "storage",
            "answer": "路径已移交给你，继续写",
            "transfer_ownership": True,
        },
        _ctx(execution_id="e-own"),
    )
    assert result.success is True
    assert "路径级移交" not in result.output
    assert ledger.owner_of("src/storage/db.ts") == "backend-fix"
    # Sibling path not in ownership_paths stays with parent.
    assert ledger.owner_of("src/tools/base.ts") == "backend-fix"
    clear_active_coordination()


@pytest.mark.asyncio
async def test_blocking_escalate_routes_to_ceo_when_coordination_active():
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-d1", total_workers=2)
    set_active_coordination(session)
    session._running_workers["r1"] = "研究员"
    session.mark_worker_busy("r1", "tool")
    seen: list[str] = []

    async def _request(q, a, questions, kind, awaiting="user", **_kwargs):
        seen.append(awaiting)
        assert awaiting == "ceo"
        assert session._busy_workers.get("r1") == "arbitrate"
        return EscalationOutcome(status="resolved", answer="用 Postgres")

    channel = EscalationChannel(armed=True, request=_request)
    try:
        result = await EscalateTool().execute(
            {
                "question": "选库？",
                "assumption": "暂按 Postgres",
                "blocking": True,
            },
            _ctx(escalation=channel),
        )
        assert result.success is True
        assert "主管就你的升级问题裁决" in result.output
        assert seen == ["ceo"]
        assert session._busy_workers.get("r1") == "arbitrate"
        assert session.has_inflight_work() is False
    finally:
        clear_active_coordination("e-d1")
        clear_active_coordination()


@pytest.mark.asyncio
async def test_blocking_escalate_stays_user_without_coordination():
    """Invariant B: no coordination session → awaiting=user (never ceo).

    Solo / classic blocking has no live CEO inside ``delegate``; hanging on CEO
    would deadlock. ``resolve_escalation`` is coordination-only.
    """
    clear_active_coordination()
    seen: list[str] = []

    async def _request(q, a, questions, kind, awaiting="user", **_kwargs):
        seen.append(awaiting)
        return EscalationOutcome(status="resolved", answer="用 Postgres")

    channel = EscalationChannel(armed=True, request=_request)
    result = await EscalateTool().execute(
        {
            "question": "选库？",
            "assumption": "暂按 Postgres",
            "blocking": True,
        },
        _ctx(execution_id="e-classic", escalation=channel),
    )
    assert result.success is True
    assert "用户就你的升级问题答复" in result.output
    assert seen == ["user"]


@pytest.mark.asyncio
async def test_ownership_question_escalate_goes_to_ceo_under_coordination():
    """写入冲突话术不再直达用户移交卡；协调活跃时走主管。"""
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-own-u", total_workers=2)
    set_active_coordination(session)
    ledger = session.ensure_file_ownership()
    ledger.declare("site/index.html", "assemble", frozenset())
    session._running_workers["assemble"] = "组装"
    session._running_workers["skeleton"] = "骨架"
    session.mark_worker_busy("skeleton", "tool")
    seen: list[tuple[str, object]] = []

    async def _request(q, a, questions, kind, awaiting="user", **kwargs):
        seen.append((awaiting, kwargs.get("ownership_paths")))
        assert session._busy_workers.get("skeleton") == "arbitrate"
        return EscalationOutcome(status="resolved", answer="继续写")

    channel = EscalationChannel(armed=True, request=_request)
    try:
        result = await EscalateTool().execute(
            {
                "question": "写入冲突：`site/index.html` 已归队友负责",
                "assumption": "等移交后再写",
                "blocking": True,
            },
            _ctx(execution_id="e-own-u", escalation=channel, run_id="skeleton"),
        )
        assert result.success is True
        assert "主管就你的升级问题裁决" in result.output
        assert seen == [("ceo", None)]
    finally:
        clear_active_coordination("e-own-u")
        clear_active_coordination()


@pytest.mark.asyncio
async def test_completed_owner_ownership_escalate_goes_to_ceo():
    """锁主已完成：协调活跃时不弹用户移交卡，改走主管裁决。"""
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-own-done", total_workers=2)
    set_active_coordination(session)
    ledger = session.ensure_file_ownership()
    ledger.declare("trip-plan/transport-stay.md", "transport-stay", frozenset())
    ledger.mark_written("trip-plan/transport-stay.md")
    session.completed_run_ids.add("transport-stay")
    seen: list[tuple[str, object]] = []

    async def _request(q, a, questions, kind, awaiting="user", **kwargs):
        seen.append((awaiting, kwargs.get("ownership_paths")))
        return EscalationOutcome(status="resolved", answer="同座续派接手")

    channel = EscalationChannel(armed=True, request=_request)
    try:
        result = await EscalateTool().execute(
            {
                "question": "写入冲突：`trip-plan/transport-stay.md` 已归队友负责",
                "assumption": "等主管同座续派",
                "blocking": True,
            },
            _ctx(execution_id="e-own-done", escalation=channel, run_id="transport-stay-v2"),
        )
        assert result.success is True
        assert "主管就你的升级问题裁决" in result.output
        assert seen == [("ceo", None)]
    finally:
        clear_active_coordination("e-own-done")
        clear_active_coordination()


@pytest.mark.asyncio
async def test_ended_owner_ownership_escalate_goes_to_ceo():
    """锁主 ended（未进 completed_run_ids）：协调活跃时走主管，不弹用户移交卡。"""
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-own-ended", total_workers=2)
    set_active_coordination(session)
    ledger = session.ensure_file_ownership()
    ledger.declare("docs/plan.md", "author-v1", frozenset())
    ledger.mark_written("docs/plan.md")
    ledger.mark_ended("author-v1")
    # 故意不写入 completed_run_ids（嵌套终态旁路形态）
    assert "author-v1" not in session.completed_run_ids
    seen: list[tuple[str, object]] = []

    async def _request(q, a, questions, kind, awaiting="user", **kwargs):
        seen.append((awaiting, kwargs.get("ownership_paths")))
        return EscalationOutcome(status="resolved", answer="同座续派接手")

    channel = EscalationChannel(armed=True, request=_request)
    try:
        result = await EscalateTool().execute(
            {
                "question": "写入冲突：`docs/plan.md` 已归队友负责",
                "assumption": "等主管同座续派",
                "blocking": True,
            },
            _ctx(execution_id="e-own-ended", escalation=channel, run_id="merger"),
        )
        assert result.success is True
        assert "主管就你的升级问题裁决" in result.output
        assert seen == [("ceo", None)]
    finally:
        clear_active_coordination("e-own-ended")
        clear_active_coordination()


@pytest.mark.asyncio
async def test_nl_transfer_answer_does_not_mutate_ledger():
    """NL「移交写权」答复不改账本；structured transfer 已撤。"""
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-nl", total_workers=2)
    set_active_coordination(session)
    ledger = session.ensure_file_ownership()
    ledger.declare("site/index.html", "assemble", frozenset())
    session.register_arbitration(
        "skeleton",
        escalation_id="esc-nl",
        conversation_id="c1",
        question="写入冲突：`site/index.html`",
        assumption="等移交",
        ownership_paths=["site/index.html"],
        lock_owner_run_id="assemble",
    )
    tool = ResolveEscalationTool()
    result = await tool.execute(
        {
            "run_id": "skeleton",
            "answer": "已移交写权，你继续写 site/index.html",
            # 故意不传 transfer_ownership
        },
        _ctx(execution_id="e-nl"),
    )
    assert result.success is True
    assert ledger.owner_of("site/index.html") == "assemble"
    clear_active_coordination("e-nl")
    clear_active_coordination()


@pytest.mark.asyncio
async def test_nested_ended_escalate_uses_parent_coordination():
    """嵌套 eid 无会话：父回退后 ended 锁主仍走 CEO。"""
    from agentcore.runtime.coordination.session import current_execution_id

    clear_active_coordination()
    session = CoordinationSession(execution_id="parent-coord", total_workers=2)
    set_active_coordination(session)
    ledger = session.ensure_file_ownership()
    ledger.declare("docs/plan.md", "author-v1", frozenset())
    ledger.mark_ended("author-v1")
    seen: list[tuple[str, object]] = []

    async def _request(q, a, questions, kind, awaiting="user", **kwargs):
        seen.append((awaiting, kwargs.get("ownership_paths")))
        return EscalationOutcome(status="resolved", answer="ok")

    channel = EscalationChannel(armed=True, request=_request)
    token = current_execution_id.set("parent-coord")
    try:
        result = await EscalateTool().execute(
            {
                "question": "写入冲突：`docs/plan.md` 已归队友负责",
                "assumption": "等主管",
                "blocking": True,
            },
            _ctx(execution_id="nested-only", escalation=channel, run_id="merger"),
        )
        assert result.success is True
        assert "主管就你的升级问题裁决" in result.output
        assert seen == [("ceo", None)]
    finally:
        current_execution_id.reset(token)
        clear_active_coordination("parent-coord")
        clear_active_coordination()


@pytest.mark.asyncio
async def test_resolve_escalation_settles_live_bridge():
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-d1", total_workers=2)
    set_active_coordination(session)
    registry = InteractionRegistry()
    fut = registry.create(
        "esc1",
        "c1",
        kind=InteractionKind.ESCALATION,
        payload={"awaiting": "ceo", "run_id": "r1"},
    )
    session.register_arbitration(
        "r1",
        escalation_id="esc1",
        conversation_id="c1",
        question="选库？",
        assumption="暂按 Postgres",
    )

    # Patch the tool to use our registry
    import agentcore.runtime.coordination.tools as tools_mod

    original = tools_mod.default_interaction_registry
    tools_mod.default_interaction_registry = lambda: registry
    try:
        result = await ResolveEscalationTool().execute(
            {"run_id": "r1", "answer": "用 Postgres", "via_user": False},
            _ctx(),
        )
        assert result.success is True
        assert fut.done()
        assert fut.result() == {"answer": "用 Postgres", "via_user": False}
        assert session.get_arbitration("r1") is None
    finally:
        tools_mod.default_interaction_registry = original
        clear_active_coordination("e-d1")
        clear_active_coordination()


@pytest.mark.asyncio
async def test_resolve_escalation_stashes_when_no_live_pending():
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-d1", total_workers=2)
    set_active_coordination(session)
    try:
        result = await ResolveEscalationTool().execute(
            {"run_id": "r1", "answer": "用 Postgres", "via_user": True},
            _ctx(),
        )
        assert result.success is True
        stashed = session.take_stashed_resolution("r1")
        assert stashed is not None
        assert stashed["answer"] == "用 Postgres"
        assert stashed["via_user"] is True
    finally:
        clear_active_coordination("e-d1")
        clear_active_coordination()


@pytest.mark.asyncio
async def test_resolve_escalation_soft_success_when_session_inactive():
    """会话已收口（团队全部完成）：resolve_escalation 幂等软化为 success 提示，不硬 error。

    对齐 UpdateSynthesisTool：``session is None``（从未开团）才硬 error；``not active``
    （已收口）给软成功，避免烧掉 CEO 一轮重试。
    """
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-d1", total_workers=2)
    session.close()  # 团队完成、会话收口
    set_active_coordination(session)
    try:
        result = await ResolveEscalationTool().execute(
            {"run_id": "r1", "answer": "用 Postgres"},
            _ctx(),
        )
        assert result.success is True
        assert not result.error
        assert "收口" in result.output
    finally:
        clear_active_coordination("e-d1")
        clear_active_coordination()


@pytest.mark.asyncio
async def test_arbitration_snapshot_roundtrip():
    session = CoordinationSession(execution_id="e", total_workers=2)
    session.register_arbitration(
        "r1",
        escalation_id="esc1",
        conversation_id="c1",
        question="Q",
        assumption="A",
    )
    session.stash_resolution("r2", answer="ans", via_user=True, escalation_id="esc2")
    snap = session.snapshot()
    restored = CoordinationSession.from_snapshot(snap)
    assert restored.get_arbitration("r1")["escalation_id"] == "esc1"
    stashed = restored.take_stashed_resolution("r2")
    assert stashed["answer"] == "ans"
    assert stashed["via_user"] is True
