"""批 A2：辩论进宿主图 — 判据 / 幕序号 / 回落独立图 / 进宿主图接线。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agentcore.runtime.debate.events import debate_act_payload, moderator_plan_event
from agentcore.runtime.kickoff.debate_host import (
    DebateHostAttach,
    host_graph_binding,
    is_mlr_synthesizer_id,
    next_act_id,
    research_chain_evidence,
    resolve_debate_host_attach,
    synthesizer_completed,
    synthesizer_run_id,
)


def test_research_chain_evidence_mirrors_research_first_inverse():
    assert research_chain_evidence([]) is False
    assert research_chain_evidence([], has_research_artifacts=True) is True
    entries = [
        {
            "kind": "tool_call",
            "payload": {
                "name": "delegate",
                "arguments": '{"playbook": "multi_lens_research"}',
                "success": True,
            },
        }
    ]
    assert research_chain_evidence(entries) is True


def test_next_act_id_defaults_and_increments():
    assert next_act_id([]) == "act-2"
    assert next_act_id(None) == "act-2"
    entries = [
        {
            "kind": "run_plan",
            "payload": {"act": {"act_id": "act-1", "kind": "multi_agent"}},
        },
        {
            "kind": "run_plan",
            "payload": {"act": {"act_id": "act-2", "kind": "debate"}},
        },
    ]
    assert next_act_id(entries) == "act-3"


def test_is_mlr_synthesizer_id_raw_and_namespaced():
    assert is_mlr_synthesizer_id("synthesizer")
    assert is_mlr_synthesizer_id("del_2468005e-cf60-4032-84e4-9eca57633098_synthesizer")
    assert is_mlr_synthesizer_id(None, "add_abc_synthesizer")
    assert not is_mlr_synthesizer_id("del_x_lens_0")
    assert not is_mlr_synthesizer_id("synthesizer_helper")  # 非后缀


def test_synthesizer_run_id_and_completed():
    entries = [
        {
            "kind": "run_plan",
            "payload": {
                "plan_type": "multi_agent",
                "runs": [
                    {"id": "lens_0", "agent_id": "lens_0"},
                    {"id": "synthesizer", "agent_id": "synthesizer"},
                ],
            },
        },
        {"kind": "run_completed", "payload": {"run_id": "synthesizer"}},
    ]
    assert synthesizer_run_id(entries) == "synthesizer"
    assert synthesizer_completed(entries, "synthesizer") is True
    failed = [
        *entries[:-1],
        {"kind": "run_failed", "payload": {"run_id": "synthesizer"}},
    ]
    assert synthesizer_completed(failed, "synthesizer") is False


def test_synthesizer_run_id_matches_dag_namespaced():
    """真跑实证：DAG 铸造 del_<uuid>_synthesizer，精确匹配会漏挂宿主。"""
    rid = "del_2468005e-cf60-4032-84e4-9eca57633098_synthesizer"
    entries = [
        {
            "kind": "run_plan",
            "payload": {
                "plan_type": "multi_agent",
                "execution_id": "exec_mlr",
                "runs": [
                    {
                        "id": "del_2468005e-cf60-4032-84e4-9eca57633098_lens_0",
                        "agent_id": "del_2468005e-cf60-4032-84e4-9eca57633098_lens_0",
                    },
                    {"id": rid, "agent_id": rid},
                ],
            },
        },
        {"kind": "run_completed", "payload": {"run_id": rid}},
    ]
    assert synthesizer_run_id(entries) == rid
    assert synthesizer_completed(entries, rid) is True


@pytest.mark.asyncio
async def test_resolve_debate_host_attach_fallback_no_mlr(monkeypatch):
    async def _none(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.resolve_latest_mlr_execution",
        _none,
    )
    got = await resolve_debate_host_attach(
        conversation_id="c1",
        append_message_id="m2",
        has_research_artifacts=True,
    )
    assert got is None


@pytest.mark.asyncio
async def test_resolve_debate_host_attach_success(monkeypatch):
    host_entries = [
        {
            "kind": "run_plan",
            "payload": {
                "plan_type": "multi_agent",
                "execution_id": "exec1",
                "act": {"act_id": "act-1", "kind": "multi_agent"},
                "runs": [{"id": "synthesizer", "agent_id": "synthesizer"}],
            },
        },
        {"kind": "run_completed", "payload": {"run_id": "synthesizer"}},
    ]

    async def _eid(**_kwargs: Any) -> str:
        return "exec1"

    async def _mid(**_kwargs: Any) -> str:
        return "m1"

    async def _load(_mid: str) -> list:
        return host_entries

    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.resolve_latest_mlr_execution",
        _eid,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.resolve_host_message_id",
        _mid,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.load_host_journal_entries",
        _load,
    )
    got = await resolve_debate_host_attach(
        conversation_id="c1",
        append_message_id="m2",
        has_research_artifacts=True,
    )
    assert got == DebateHostAttach(
        execution_id="exec1",
        host_message_id="m1",
        anchor_run_id="synthesizer",
        act_id="act-2",
        same_turn=False,
    )


@pytest.mark.asyncio
async def test_resolve_debate_host_attach_same_turn(monkeypatch):
    host_entries = [
        {
            "kind": "run_plan",
            "payload": {
                "plan_type": "multi_agent",
                "execution_id": "exec1",
                "act": {"act_id": "act-1", "kind": "multi_agent"},
                "runs": [{"id": "synthesizer", "agent_id": "synthesizer"}],
            },
        },
        {"kind": "run_completed", "payload": {"run_id": "synthesizer"}},
    ]

    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.resolve_latest_mlr_execution",
        AsyncMock(return_value="exec1"),
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.resolve_host_message_id",
        AsyncMock(return_value="m1"),
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.load_host_journal_entries",
        AsyncMock(return_value=host_entries),
    )
    got = await resolve_debate_host_attach(
        conversation_id="c1",
        append_message_id="m1",
        has_research_artifacts=True,
    )
    assert got == DebateHostAttach(
        execution_id="exec1",
        host_message_id="m1",
        anchor_run_id="synthesizer",
        act_id="act-2",
        same_turn=True,
    )


def test_host_graph_binding_same_turn_reuses_without_mint():
    attach = DebateHostAttach(
        execution_id="exec_host",
        host_message_id="m1",
        anchor_run_id="synthesizer",
        act_id="act-2",
        same_turn=True,
    )
    minted: list[str] = []

    def _mint() -> str:
        minted.append("x")
        return "NEW"

    eid, prev = host_graph_binding(attach, mint_id=_mint)
    assert eid == "exec_host"
    assert prev is None
    assert minted == []


def test_host_graph_binding_cross_turn_mints_prev():
    attach = DebateHostAttach(
        execution_id="exec_host",
        host_message_id="m1",
        anchor_run_id="synthesizer",
        act_id="act-2",
        same_turn=False,
    )
    eid, prev = host_graph_binding(attach, mint_id=lambda: "exec_new")
    assert eid == "exec_new"
    assert prev == "exec_host"


@pytest.mark.asyncio
async def test_resolve_debate_host_attach_fallback_incomplete_synthesizer(monkeypatch):
    host_entries = [
        {
            "kind": "run_plan",
            "payload": {
                "plan_type": "multi_agent",
                "runs": [{"id": "synthesizer", "agent_id": "synthesizer"}],
            },
        },
        # no run_completed
    ]

    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.resolve_latest_mlr_execution",
        AsyncMock(return_value="exec1"),
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.resolve_host_message_id",
        AsyncMock(return_value="m1"),
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.load_host_journal_entries",
        AsyncMock(return_value=host_entries),
    )
    got = await resolve_debate_host_attach(
        conversation_id="c1",
        append_message_id="m2",
        has_research_artifacts=True,
    )
    assert got is None


@pytest.mark.asyncio
async def test_resolve_debate_host_attach_namespaced_synthesizer(monkeypatch):
    """口头开辩 fallback：namespaced synthesizer 仍须附着幕1 宿主。"""
    rid = "del_2468005e-cf60-4032-84e4-9eca57633098_synthesizer"
    host_entries = [
        {
            "kind": "run_plan",
            "payload": {
                "plan_type": "multi_agent",
                "execution_id": "exec1",
                "act": {"act_id": "act-1", "kind": "multi_agent"},
                "runs": [{"id": rid, "agent_id": rid}],
            },
        },
        {"kind": "run_completed", "payload": {"run_id": rid}},
    ]

    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.resolve_latest_mlr_execution",
        AsyncMock(return_value="exec1"),
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.resolve_host_message_id",
        AsyncMock(return_value="m1"),
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.load_host_journal_entries",
        AsyncMock(return_value=host_entries),
    )
    got = await resolve_debate_host_attach(
        conversation_id="c1",
        append_message_id="m2",
        has_research_artifacts=True,
    )
    assert got == DebateHostAttach(
        execution_id="exec1",
        host_message_id="m1",
        anchor_run_id=rid,
        act_id="act-2",
        same_turn=False,
    )


@pytest.mark.asyncio
async def test_resolve_latest_mlr_falls_back_to_appendable_journal(monkeypatch):
    """两套查找对齐：MLR SQL 漏检时，appendable + journal synthesizer 复核仍命中。"""
    from agentcore.runtime.delegate import graph_append as ga

    rid = "del_abc_synthesizer"
    host_entries = [
        {
            "kind": "run_plan",
            "payload": {
                "plan_type": "multi_agent",
                "execution_id": "exec_ma",
                "runs": [{"id": rid, "agent_id": rid}],
            },
        },
        {"kind": "run_completed", "payload": {"run_id": rid}},
    ]

    class _Repo:
        async def find_latest_mlr_execution(self, *, conversation_id: str):
            return None

        async def find_latest_multi_agent_execution(self, *, conversation_id: str):
            return "exec_ma"

        async def load(self, mid: str):
            assert mid == "m_host"
            return host_entries

    class _CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    import agentcore.db.base as base_mod
    import agentcore.db.repositories as repos_mod

    monkeypatch.setattr(base_mod, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(repos_mod, "TurnJournalRepository", lambda _s: _Repo())
    monkeypatch.setattr(
        ga, "resolve_host_message_id", AsyncMock(return_value="m_host")
    )

    got = await ga.resolve_latest_mlr_execution(conversation_id="c1")
    assert got == "exec_ma"



def test_moderator_plan_event_independent_act_1():
    from agentcore.runtime.debate.types import DebateForm

    tool = SimpleNamespace(
        _captain_run_id="cap",
        _debate_act_id="act-1",
        _debate_act_title=None,
        _debate_anchor_run_id=None,
        _debate_host_message_id=None,
        _debate_graph_parent_run_id=None,
    )
    cfg = SimpleNamespace(form=DebateForm.DEBATE, motion="是否采用方案 A")
    ev = moderator_plan_event(tool, "e-new", "mod-1", cfg)  # type: ignore[arg-type]
    assert ev.payload["act"] == {"act_id": "act-1", "kind": "debate"}
    assert "host_message_id" not in ev.payload
    assert ev.payload["runs"][0]["parent_run_id"] == "cap"


def test_moderator_plan_event_host_act_2():
    from agentcore.runtime.debate.types import DebateForm

    tool = SimpleNamespace(
        _captain_run_id="cap",
        _debate_act_id="act-2",
        _debate_act_title="正反辩论对抗",
        _debate_anchor_run_id="synthesizer",
        _debate_host_message_id="m1",
        _debate_prev_execution_id="exec_mlr",
        # 新图+prev：parent 用本回合 captain
        _debate_graph_parent_run_id=None,
    )
    cfg = SimpleNamespace(form=DebateForm.DEBATE, motion="命题")
    ev = moderator_plan_event(tool, "exec_debate", "mod-1", cfg)  # type: ignore[arg-type]
    assert ev.payload["execution_id"] == "exec_debate"
    assert ev.payload["prev_execution_id"] == "exec_mlr"
    assert "host_message_id" not in ev.payload
    assert ev.payload["act"] == {
        "act_id": "act-2",
        "kind": "debate",
        "title": "正反辩论对抗",
        "anchor_run_id": "synthesizer",
    }
    assert ev.payload["runs"][0]["parent_run_id"] == "cap"


def test_moderator_plan_event_same_turn_omits_prev():
    from agentcore.runtime.debate.types import DebateForm

    tool = SimpleNamespace(
        _captain_run_id="cap",
        _debate_act_id="act-2",
        _debate_act_title="正反辩论对抗",
        _debate_anchor_run_id="synthesizer",
        _debate_host_message_id="m1",
        _debate_prev_execution_id=None,
        _debate_graph_parent_run_id=None,
        _debate_authorized_by="preview",
    )
    cfg = SimpleNamespace(form=DebateForm.DEBATE, motion="命题")
    ev = moderator_plan_event(tool, "exec_host", "mod-1", cfg)  # type: ignore[arg-type]
    assert ev.payload["execution_id"] == "exec_host"
    assert "prev_execution_id" not in ev.payload
    assert ev.payload["act"]["act_id"] == "act-2"
    assert ev.payload["act"]["anchor_run_id"] == "synthesizer"
    assert ev.payload["runs"][0]["parent_run_id"] == "cap"


def _debate_tool_for_host_bind():
    import tempfile
    from pathlib import Path

    from agentcore.runtime.events import EventSink
    from agentcore.tools.builtin.debate.tool import DebateTool
    from agentcore.tools.protocol import ToolContext
    from agentcore.tools.registry import ToolRegistry
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.server import ServerWorkspace
    from tests.delegate.conftest import Provider

    backend = ServerWorkspace(
        root=Path(tempfile.mkdtemp(prefix="debate_host_ws_")),
        sandbox=SubprocessSandbox(),
    )
    ctx = ToolContext.create(
        execution_id="e-context",
        run_id="captain",
        agent_id="CEO",
        backend=backend,
        user_id="u",
        conversation_id="c1",
    )
    return DebateTool(
        llm=Provider([]),
        sink=EventSink(),
        system_prompt="sys",
        user_message="开辩",
        tools=ToolRegistry(),
        base_tool_context=ctx,
        conversation_id="c1",
        message_id="m1",
        captain_run_id="captain",
        approval_gate=None,
    )


def _host_attach(*, same_turn: bool) -> DebateHostAttach:
    return DebateHostAttach(
        execution_id="exec_host",
        host_message_id="m1",
        anchor_run_id="synthesizer",
        act_id="act-2",
        same_turn=same_turn,
    )


async def _first_plan_after_attach(monkeypatch, *, same_turn: bool):
    from agentcore.llm.provider.protocol import TokenUsage
    from agentcore.runtime.costing import usage_metadata
    from agentcore.runtime.debate import DebateConfig, DebateForm, DebateSide, RoundPolicy
    from agentcore.runtime.events.types import EventType
    from agentcore.tools.builtin.debate import tool as debate_tool_mod

    attach = _host_attach(same_turn=same_turn)

    async def _from_card(*_a, **_k):
        return attach

    class _FakeModerator:
        usage = TokenUsage()
        llm_rounds = 0

        def __init__(self, **_kw):
            pass

        async def _complete_json(self, *_a, **_k):
            return {}

        async def run(self, *_a, **_k):
            raise RuntimeError("stop after plan")

    async def _skip_pretrial(*_a, **_k):
        return None

    monkeypatch.setattr(
        "agentcore.runtime.kickoff.stage_card.resolve_host_attach_from_card",
        _from_card,
    )
    monkeypatch.setattr(debate_tool_mod, "Moderator", _FakeModerator)
    monkeypatch.setattr(
        "agentcore.runtime.debate.pretrial.run_pretrial_phase",
        _skip_pretrial,
    )

    tool = _debate_tool_for_host_bind()
    config = DebateConfig(
        motion="命题",
        form=DebateForm.DEBATE,
        sides=[
            DebateSide(key="pro", name="正方", stance="a"),
            DebateSide(key="con", name="反方", stance="b"),
        ],
        policy=RoundPolicy.for_form(DebateForm.DEBATE, thorough=False),
        moderator_run_id="debate_mod_test",
    )
    await tool._run_moderator(config, usage_metadata(tool._acc.usage))
    plans = [e for e in tool._sink._history if e.type == EventType.RUN_PLAN]  # noqa: SLF001
    assert plans, "moderator_plan_event must emit before moderator.run"
    return tool, plans[0]


@pytest.mark.asyncio
async def test_run_moderator_same_turn_reuses_host_eid(monkeypatch):
    tool, plan = await _first_plan_after_attach(monkeypatch, same_turn=True)
    assert plan.payload["execution_id"] == "exec_host"
    assert "prev_execution_id" not in plan.payload
    assert tool._base_tool_context.execution_id == "exec_host"
    assert tool._debate_prev_execution_id is None
    assert plan.payload["act"]["act_id"] == "act-2"


@pytest.mark.asyncio
async def test_run_moderator_cross_turn_mints_prev(monkeypatch):
    tool, plan = await _first_plan_after_attach(monkeypatch, same_turn=False)
    assert plan.payload["execution_id"] != "exec_host"
    assert plan.payload["execution_id"] != "e-context"
    assert plan.payload.get("prev_execution_id") == "exec_host"
    assert tool._debate_prev_execution_id == "exec_host"
    assert plan.payload["act"]["act_id"] == "act-2"
    tool = SimpleNamespace()
    assert debate_act_payload(tool) == {"act_id": "act-1", "kind": "debate"}


def test_project_turn_mlr_debate_acts_vector():
    from agentcore.conformance.projection import project_turn
    from agentcore.conformance.vectors.multi_agent.mlr_debate_acts import (
        _multi_agent_mlr_debate_acts,
    )

    wire = [
        {"type": e.type.value, "payload": e.payload}
        for e in _multi_agent_mlr_debate_acts()
    ]
    projected = project_turn(wire)
    acts = projected.get("acts") or []
    # 新 eid 重置 slot：最终投影只剩幕 2 辩论图（prev 链留给前端跨图呈现）。
    assert len(acts) == 1
    assert acts[0]["actId"] == "act-2"
    assert acts[0]["kind"] == "debate"
    assert acts[0]["anchorRunId"] == "synthesizer"
    runs = {r["id"]: r for r in projected.get("runs") or []}
    assert runs["debate_mod_act2_r1_pro"]["actId"] == "act-2"
    assert runs["debate_mod_act2_r1_con"]["actId"] == "act-2"
    assert "synthesizer" not in runs
    # MLR 图不在最终 slot；prev 在最后一张 run_plan 上
    last_plans = [e for e in wire if e["type"] == "run_plan"]
    assert last_plans[-1]["payload"].get("prev_execution_id") == "exec_mlr_debate"
