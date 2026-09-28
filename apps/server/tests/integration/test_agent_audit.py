"""Agent audit integration — delegate turn writes expected audit rows."""

from uuid import uuid4

import pytest

from agentcore.db.models import Conversation
from agentcore.db.repositories import AgentAuditEventRepository
from agentcore.runtime.audit.hooks import bind_recorder, on_delegate_plan, on_journal_fact_appended
from agentcore.runtime.audit.recorder import current_audit_recorder
from agentcore.runtime.runs import build_run_plan


@pytest.mark.asyncio
async def test_delegate_turn_audit_rows(session_factory, monkeypatch):
    monkeypatch.setattr(
        "agentcore.runtime.audit.recorder.telemetry_session_factory",
        session_factory,
    )
    user_id = str(uuid4())
    conversation_id = str(uuid4())
    turn_id = str(uuid4())
    async with session_factory() as s:
        s.add(Conversation(id=conversation_id, user_id=user_id))
        await s.commit()
    plan, errors = build_run_plan(
        [
            {"id": "w1", "role": "研究员", "task": "调研市场趋势并写摘要"},
            {
                "id": "w2",
                "role": "写手",
                "task": "根据调研撰写报告",
                "depends_on": ["w1"],
            },
        ],
        valid_tools={"write", "read", "grep"},
        id_prefix="del_x",
    )
    assert not errors
    assert len(plan.nodes) == 2

    recorder, token = bind_recorder(
        user_id=user_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        trace_id="trace-delegate-audit",
        captain_run_id="captain-1",
    )
    try:
        on_delegate_plan(execution_id="exec-1", plan=plan, captain_run_id="captain-1")
        on_journal_fact_appended(
            {
                "kind": "run_started",
                "payload": {
                    "run_id": plan.nodes[0].run_id,
                    "agent_id": plan.nodes[0].run_id,
                    "parent_run_id": "captain-1",
                    "kind": "agent",
                },
            }
        )
        on_journal_fact_appended(
            {
                "kind": "tool_use_start",
                "payload": {
                    "tool_call_id": "tc-1",
                    "tool_name": "write",
                    "arguments": {"file_path": "report.md"},
                    "run_id": plan.nodes[0].run_id,
                },
            }
        )
        on_journal_fact_appended(
            {
                "kind": "tool_use_end",
                "payload": {
                    "tool_call_id": "tc-1",
                    "tool_name": "write",
                    "status": "success",
                    "run_id": plan.nodes[0].run_id,
                },
            }
        )
        await recorder.flush()
    finally:
        current_audit_recorder.reset(token)

    async with session_factory() as session:
        rows = await AgentAuditEventRepository(session).list_for_turn(
            conversation_id=conversation_id,
            turn_id=turn_id,
        )

    actions = [row.action for row in rows]
    assert "delegate.plan" in actions
    assert "run.started" in actions
    assert "tool.write" in actions
    assert len(rows) >= 3

    plan_row = next(row for row in rows if row.action == "delegate.plan")
    assert plan_row.category == "orchestration"
    assert plan_row.execution_id == "exec-1"
    task_detail = plan_row.detail["tasks"][0]
    assert "task_hash" in task_detail
    assert len(task_detail["task"]) <= 200

    tool_row = next(row for row in rows if row.action == "tool.write")
    assert tool_row.target_type == "file"
    assert tool_row.target_ref == "report.md"
