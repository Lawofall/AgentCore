"""Rewind keeps the request, sealed conclusions, and one scar line."""

from types import SimpleNamespace

import pytest

from agentcore.conversation.transcript import transcript_rows
from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.graph.shape.rewind import (
    MAX_MODEL_REWINDS,
    RewindNode,
    RewindRejected,
    history_row,
    model_rewind_count,
    nodes_from_journal,
    plan_rewind,
    project_captain_window,
    project_journal_window,
    seal_snapshot_id,
)
from agentcore.runtime.facts import GraphRewindFact, TurnFactLog, current_fact_log, record_turn_fact


def _node(node_id: str, *deps: str, sealed: bool = True, conclusion: str = "") -> RewindNode:
    return RewindNode(
        id=node_id,
        depends_on=tuple(deps),
        goal=node_id,
        conclusion=conclusion or f"{node_id} 的结论",
        sealed=sealed,
    )


def test_rewind_keeps_ancestors_and_writes_a_scar():
    nodes = [
        _node("open"),
        _node("good", "open"),
        _node("bad", "good"),
        _node("side", "open"),
    ]
    plan = plan_rewind(nodes, "good", "写偏了", original_request="做一个东西")
    assert plan.kept_ids == ("open", "good")
    assert plan.cut_ids == ("bad", "side")
    assert plan.scar == "已切出：bad、side。原因：写偏了"
    assert plan.conclusions == (("open", "open 的结论"), ("good", "good 的结论"))
    assert "bad 的结论" not in history_row(
        {
            "conclusions": [{"id": "open", "text": "open 的结论"}],
            "scar": plan.scar,
        }
    )["content"]


def test_unsealed_and_unknown_targets_are_refused():
    nodes = [_node("open", sealed=False, conclusion="")]
    try:
        plan_rewind(nodes, "open", "原因")
    except RewindRejected as exc:
        assert exc.code == "not_sealed"
    else:
        raise AssertionError("expected not_sealed")
    try:
        plan_rewind(nodes, "missing", "原因")
    except RewindRejected as exc:
        assert exc.code == "unknown"
    else:
        raise AssertionError("expected unknown")


def test_journal_nodes_and_history_drop_tool_traces():
    entries = [
        {"kind": "turn_started", "payload": {"user_message": "做一个东西"}},
        {
            "kind": "run_plan",
            "payload": {
                "runs": [
                    {"id": "open", "task": "开头", "depends_on": []},
                    {"id": "bad", "task": "走偏", "depends_on": ["open"]},
                ]
            },
        },
        {
            "kind": "run_completed",
            "payload": {"run_id": "open", "output_summary": "开头结论"},
        },
        {
            "kind": "run_completed",
            "payload": {"run_id": "bad", "output_summary": "走偏结论"},
        },
        {
            "kind": "llm_call",
            "payload": {
                "run_id": "cap",
                "tool_calls": [{"id": "c1", "function": {"name": "graph", "arguments": "{}"}}],
            },
        },
        {"kind": "tool_call", "payload": {"run_id": "cap", "tool_call_id": "c1", "result": "痕迹"}},
    ]
    nodes, original = nodes_from_journal(entries)
    assert original == "做一个东西"
    plan = plan_rewind(nodes, "open", "你选择从这一段继续", original_request=original)
    assert plan.cut_ids == ("bad",)
    entries.append(
        {
            "kind": "graph_rewind",
            "payload": {
                "scar": plan.scar,
                "conclusions": [{"id": node_id, "text": text} for node_id, text in plan.conclusions],
            },
        }
    )
    rows = transcript_rows(entries, "后来的长回复")
    assert len(rows) == 1
    assert rows[0]["role"] == "assistant"
    assert "开头结论" in rows[0]["content"]
    assert "痕迹" not in rows[0]["content"]
    assert "走偏结论" not in rows[0]["content"]
    assert "已切出：走偏" in rows[0]["content"]


def test_captain_window_drops_traces_and_keeps_the_rewind_call():
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        record_turn_fact(
            GraphRewindFact(
                target_id="open",
                scar="已切出：bad。原因：写偏了",
                conclusions=(("open", "开头结论"),),
                original_request="做一个东西",
                source="model",
                window_run_id="cap",
            ).to_fact()
        )
        call = ToolCall(id="c1", function=ToolCallFunction(name="rewind", arguments="{}"))
        window = project_captain_window(
            [
                LLMMessage(role="system", content="宪法"),
                LLMMessage(role="user", content="做一个东西"),
                LLMMessage(role="assistant", content="", tool_calls=[call]),
                LLMMessage(role="tool", content="旧痕迹", tool_call_id="c1"),
                LLMMessage(
                    role="assistant",
                    content="",
                    tool_calls=[ToolCall(id="c2", function=ToolCallFunction("rewind", "{}"))],
                ),
                LLMMessage(role="tool", content="回转结果", tool_call_id="c2"),
            ],
            "cap",
        )
    finally:
        current_fact_log.reset(token)
    roles = [message.role for message in window]
    assert roles == ["system", "user", "assistant", "user", "assistant", "tool"]
    assert window[0].content == "宪法"
    assert window[1].content == "做一个东西"
    assert "开头结论" in window[2].content
    assert window[3].content.startswith("已切出：")
    assert window[5].content == "回转结果"
    assert all("旧痕迹" not in (message.content or "") for message in window)


def test_journal_window_projects_only_the_matching_run():
    entries = [
        {
            "kind": "graph_rewind",
            "payload": {
                "window_run_id": "cap",
                "original_request": "做一个东西",
                "scar": "已切出：bad。原因：写偏了",
                "conclusions": [{"id": "open", "text": "开头结论"}],
            },
        }
    ]
    call = ToolCall(id="c2", function=ToolCallFunction("rewind", "{}"))
    messages = [
        LLMMessage(role="system", content="宪法"),
        LLMMessage(role="user", content="做一个东西"),
        LLMMessage(role="assistant", content="痕迹"),
        LLMMessage(role="assistant", content="", tool_calls=[call]),
        LLMMessage(role="tool", content="回转结果", tool_call_id="c2"),
    ]
    projected = project_journal_window(messages, entries, "cap")
    assert "痕迹" not in (projected[2].content or "")
    assert projected[1].content == "做一个东西"
    assert project_journal_window(messages, entries, "worker") is messages


def test_model_rewind_cap_and_seal_snapshot_lookup():
    entries = [
        {"kind": "graph_rewind", "payload": {"source": "model"}},
        {"kind": "graph_rewind", "payload": {"source": "human"}},
        {"kind": "graph_rewind", "payload": {"source": "model"}},
        {"kind": "seal_snapshot", "payload": {"run_id": "open", "snapshot_id": "snap-1"}},
    ]
    assert model_rewind_count(entries) == MAX_MODEL_REWINDS
    assert seal_snapshot_id(entries, "open") == "snap-1"
    assert seal_snapshot_id(entries, "other") == ""


def _sealed_journal() -> list[dict]:
    return [
        {"kind": "turn_started", "payload": {"user_message": "做一个东西"}},
        {
            "kind": "run_plan",
            "payload": {
                "runs": [
                    {"id": "open", "task": "开头", "depends_on": []},
                    {"id": "bad", "task": "走偏", "depends_on": ["open"]},
                ]
            },
            "ts": 1,
        },
        {
            "kind": "run_completed",
            "payload": {"run_id": "open", "output_summary": "开头结论"},
        },
        {
            "kind": "run_completed",
            "payload": {"run_id": "bad", "output_summary": "走偏了"},
        },
    ]


def test_reload_keeps_the_cut():
    from agentcore.runtime.journal.fold import runs_from_entries

    entries = _sealed_journal()
    entries.append(
        {
            "kind": "graph_rewind",
            "payload": {
                "target_id": "open",
                "cut_ids": ["bad"],
                "scar": "已切出：bad。原因：写偏了",
                "source": "human",
            },
            "ts": 2,
        }
    )
    runs = runs_from_entries(entries)
    assert runs is not None
    rewind = [event for event in runs["events"] if event["type"] == "graph_rewind"]
    assert len(rewind) == 1
    assert rewind[0]["payload"]["cut_run_ids"] == ["bad"]
    assert rewind[0]["payload"]["target_run_id"] == "open"


@pytest.mark.asyncio
async def test_human_rewind_waits_and_leaves_files(monkeypatch: pytest.MonkeyPatch):
    import agentcore.api.routes.conversations.graph_rewind as route

    appended: list[dict] = []

    async def _write(*_args, **_kwargs):
        return None

    class _MessageRepo:
        def __init__(self, _session):
            pass

        async def get_by_id(self, _message_id, conversation_id=None):
            del conversation_id
            return SimpleNamespace(role="assistant")

    class _JournalRepo:
        def __init__(self, _session):
            pass

        async def load_owned(self, _message_id, _conversation_id):
            return _sealed_journal()

        async def append(self, **kwargs):
            appended.append(kwargs)

    async def _restore(*_args, **_kwargs):
        raise AssertionError("未勾选时不应恢复文件")

    monkeypatch.setattr(route, "_require_conversation_write", _write)
    monkeypatch.setattr(route, "active_coordination_for_conversation", lambda _cid: None)
    monkeypatch.setattr(route, "MessageRepository", _MessageRepo)
    monkeypatch.setattr(route, "TurnJournalRepository", _JournalRepo)
    monkeypatch.setattr(route, "restore_snapshot", _restore)

    result = await route.rewind_graph(
        "c1",
        route.GraphRewindRequest(
            message_id="m1",
            target_run_id="open",
            reason="写偏了",
            restore_files=False,
        ),
        SimpleNamespace(user_id="u1"),
        conv_repo=SimpleNamespace(_session=None),
        session=None,
    )
    assert result.files_restored is False
    assert result.cut_run_ids == ["bad"]
    assert appended[0]["entry"]["kind"] == "graph_rewind"
    assert appended[0]["entry"]["payload"]["source"] == "human"


@pytest.mark.asyncio
async def test_local_file_restore_is_refused_and_not_recorded(monkeypatch: pytest.MonkeyPatch):
    import agentcore.api.routes.conversations.graph_rewind as route
    from agentcore.core.errors import ConflictError

    appended: list[dict] = []

    async def _write(*_args, **_kwargs):
        return None

    class _MessageRepo:
        def __init__(self, _session):
            pass

        async def get_by_id(self, _message_id, conversation_id=None):
            del conversation_id
            return SimpleNamespace(role="assistant")

    class _JournalRepo:
        def __init__(self, _session):
            pass

        async def load_owned(self, _message_id, _conversation_id):
            return _sealed_journal()

        async def append(self, **kwargs):
            appended.append(kwargs)

    async def _owned(*_args, **_kwargs):
        return SimpleNamespace(folder_id="f1")

    async def _local(*_args, **_kwargs):
        return object()

    monkeypatch.setattr(route, "_require_conversation_write", _write)
    monkeypatch.setattr(route, "active_coordination_for_conversation", lambda _cid: None)
    monkeypatch.setattr(route, "MessageRepository", _MessageRepo)
    monkeypatch.setattr(route, "TurnJournalRepository", _JournalRepo)
    monkeypatch.setattr(route, "_get_owned_conversation", _owned)
    monkeypatch.setattr(route, "resolve_local_binding", _local)

    with pytest.raises(ConflictError):
        await route.rewind_graph(
            "c1",
            route.GraphRewindRequest(
                message_id="m1",
                target_run_id="open",
                reason="写偏了",
                restore_files=True,
            ),
            SimpleNamespace(user_id="u1"),
            conv_repo=SimpleNamespace(_session=None),
            session=None,
        )
    assert appended == []


@pytest.mark.asyncio
async def test_model_rewind_starts_a_follow_segment_and_stops_at_two(
    monkeypatch: pytest.MonkeyPatch,
):
    from agentcore.runtime.graph.drive.rewind_drive import apply_model_rewind
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState
    from agentcore.tools.protocol import ToolEffect, ToolResult

    driven: list[RunPlan] = []

    async def _drive(tool, plan, **_kwargs):
        tool._last_drive_results = {
            "open": RunState(phase=RunPhase.COMPLETED, content="开头结论"),
        }
        driven.append(plan)
        return ToolResult(
            tool_call_id="",
            success=True,
            output="跑完了",
            effect=ToolEffect.CONTINUE,
        )

    import sys

    drive_mod = sys.modules["agentcore.runtime.graph.drive.drive"]
    monkeypatch.setattr(drive_mod, "drive", _drive)
    monkeypatch.setattr(
        "agentcore.runtime.graph.drive.plan_events.plan_event",
        lambda *_args, **_kwargs: {"type": "run_plan"},
    )
    monkeypatch.setattr(
        "agentcore.runtime.graph.session.session.active_coordination",
        lambda _execution_id: None,
    )

    plan = RunPlan()
    plan.add(RunSpec(run_id="open", task="开头", role="open"))
    plan.add(RunSpec(run_id="bad", task="走偏", role="bad", depends_on=["open"]))

    class _Sink:
        def emit(self, _event):
            return None

    tool = SimpleNamespace(
        _depth=0,
        _last_graph_plan=plan,
        _last_graph_execution_id="ex1",
        _last_graph_seed={
            "open": RunState(phase=RunPhase.COMPLETED, content="开头结论"),
            "bad": RunState(phase=RunPhase.COMPLETED, content="走偏了"),
        },
        _last_drive_results={},
        _user_message="做一个东西",
        _captain_run_id="cap",
        _sink=_Sink(),
        _base_tool_context=None,
    )
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        first = await apply_model_rewind(
            tool,
            {"target_id": "open", "reason": "写偏了", "next_goal": "改对"},
        )
        second = await apply_model_rewind(
            tool,
            {"target_id": "open", "reason": "还是偏", "next_goal": "再改"},
        )
        third = await apply_model_rewind(
            tool,
            {"target_id": "open", "reason": "又偏", "next_goal": "第三次"},
        )
    finally:
        current_fact_log.reset(token)

    assert first.success is True
    assert second.success is True
    assert third.success is False
    assert "上限" in (third.output or "")
    assert len(driven) == 2
    assert any(node.run_id == "rw_1" for node in driven[0].nodes)
    assert driven[0].nodes[-1].depends_on == ["open"]
