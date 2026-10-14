"""阶段推进卡：fold 投影 skip；kind 不在 live resolve union。遗留 pending 不进 recovery。"""

from __future__ import annotations

import pytest

from agentcore.runtime.journal.pending_interactions import (
    fold_interactions,
    fold_pending_interactions,
)


def _valid_card(**overrides):
    base = {
        "motion": "一审判决是否过重",
        "sides": [
            {"key": "pro", "name": "正方", "stance": "支持一审判决正确"},
            {"key": "con", "name": "反方", "stance": "认为判赔过重"},
        ],
        "fact_pointers": ["#r1"],
        "rationale": "各方已握同一事实却价值对立，继续调研消解不了，需要对抗检验",
        "form": "debate",
    }
    base.update(overrides)
    return base


def test_fold_skips_leftover_stage_card_and_recovery_pending_excludes_it():
    entries = [
        {
            "type": "stage_card_required",
            "payload": {
                "stage_card_id": "sc_1",
                "conversation_id": "c",
                "motion": "命题",
                "sides": [
                    {"key": "a", "name": "甲", "stance": "倾向甲"},
                    {"key": "b", "name": "乙", "stance": "倾向乙"},
                ],
                "form": "debate",
                "rationale": "真对立轴需对抗检验",
                "fact_pointers": [],
                "max_rounds": 5,
            },
        }
    ]
    pending = fold_pending_interactions(entries, message_id="m1")
    assert pending == []
    recs = fold_interactions(entries)
    assert recs == []


@pytest.mark.asyncio
async def test_resolve_interaction_rejects_stage_card_kind():
    from pydantic import TypeAdapter, ValidationError

    from agentcore.api.schemas.messages import ResolveInteractionRequest

    with pytest.raises(ValidationError):
        TypeAdapter(ResolveInteractionRequest).validate_python(
            {"kind": "stage_card", "decision": "start_debate"}
        )


@pytest.mark.asyncio
async def test_drive_top_level_no_longer_hangs_team_preview(monkeypatch):
    """顶层也不再挂 team_preview。"""
    from agentcore.core.types import WorkspaceBoundary
    from agentcore.runtime.graph.desk.worker_grant import maybe_auto_grant_before_workers

    monkeypatch.setattr(
        "agentcore.runtime.sandbox_approval.worker_gate_applies", lambda *_a, **_k: False
    )

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
    await maybe_auto_grant_before_workers(
        _Tool(),
        seed_completed=None,
    )


@pytest.mark.asyncio
async def test_list_recent_turn_ids_orders_by_session_not_in_turn_seq():
    """长回合高 seq 不得挤掉更早回合的扫描窗口。"""
    from agentcore.db.repositories.runs import TurnJournalRepository

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

    class _Session:
        def __init__(self):
            self.last_stmt = None

        async def execute(self, stmt):
            self.last_stmt = stmt
            return _Result(["turn_new", "turn_old_with_card"])

    session = _Session()
    repo = TurnJournalRepository(session)
    ids = await repo.list_recent_turn_ids("conv", limit=40)
    assert ids == ["turn_new", "turn_old_with_card"]
    sql = str(session.last_stmt)
    assert "max" in sql.lower() or "GROUP BY" in sql.upper()


@pytest.mark.asyncio
async def test_oral_debate_does_not_consume_stage_card(monkeypatch):
    """口头开辩是独立重活：不消费推进卡、不走 stage_card 授权。"""
    import tempfile
    from pathlib import Path

    from agentcore.runtime.events import EventSink
    from agentcore.tools.builtin.debate.tool import DebateTool
    from agentcore.tools.protocol import ToolContext, ToolEffect, ToolResult
    from agentcore.tools.registry import ToolRegistry
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.server import ServerWorkspace
    from tests.delegate.conftest import Provider

    async def _fake_run(self, config, usage_metadata):
        return ToolResult(
            tool_call_id="",
            success=True,
            output="ok",
            effect=ToolEffect.CONTINUE,
        )

    monkeypatch.setattr(DebateTool, "_run_moderator", _fake_run)

    backend = ServerWorkspace(
        root=Path(tempfile.mkdtemp(prefix="stage_card_ws_")),
        sandbox=SubprocessSandbox(),
    )
    ctx = ToolContext.create(
        execution_id="e",
        run_id="captain",
        agent_id="CEO",
        backend=backend,
        user_id="u",
        conversation_id="conv_sc",
    )
    tool = DebateTool(
        llm=Provider([]),
        sink=EventSink(),
        system_prompt="sys",
        user_message="开辩",
        tools=ToolRegistry(),
        base_tool_context=ctx,
        conversation_id="conv_sc",
        message_id="m_debate",
        captain_run_id="captain",
        approval_gate=None,
    )
    result = await tool.execute(
        {
            "motion": _valid_card()["motion"],
            "form": "debate",
            "sides": _valid_card()["sides"],
        },
        tool._base_tool_context,
    )
    assert result.success is True
    assert tool._debate_authorized_by in (None, "auto")
