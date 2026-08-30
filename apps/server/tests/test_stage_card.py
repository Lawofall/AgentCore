"""批 B · 阶段推进卡：建卡 / motion_override / 口头消费 / 收尾 orphan / MLR pre-auth。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agentcore.core.log_context import clear_log_context, log_context
from agentcore.llm.provider.protocol import TokenUsage
from agentcore.runtime.journal.pending_interactions import (
    fold_interactions,
    fold_pending_interactions,
    project_interaction_leaf,
)
from agentcore.runtime.kickoff.stage_card import (
    STAGE_CARD_DECISIONS,
    apply_motion_override,
    build_stage_card_payload,
    clear_turn_keeps_stage_card,
    debate_arguments_from_card,
    host_triple_from_journal,
    mark_turn_keeps_stage_card,
    research_first_user_message,
    reset_stage_card_turn_flags,
    turn_advanced_stage_from_entries,
    turn_keeps_stage_card,
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


def test_build_stage_card_payload_from_motion_card():
    payload = build_stage_card_payload(
        _valid_card(), conversation_id="conv_1", stage_card_id="sc_1"
    )
    assert payload is not None
    assert payload["stage_card_id"] == "sc_1"
    assert payload["motion"] == "一审判决是否过重"
    assert len(payload["sides"]) == 2
    assert payload["form"] == "debate"
    assert payload["thorough"] is True
    assert isinstance(payload["max_rounds"], int) and payload["max_rounds"] >= 1


def test_build_stage_card_payload_rejects_bad_card():
    assert build_stage_card_payload({"motion": "x"}, conversation_id="c") is None


def test_apply_motion_override_gate_failure_keeps_pending_semantics():
    card = build_stage_card_payload(
        _valid_card(), conversation_id="c", stage_card_id="sc"
    )
    assert card is not None
    merged, err = apply_motion_override(card, "")
    assert merged is None
    assert err


def test_apply_motion_override_accepts_rewrite():
    card = build_stage_card_payload(
        _valid_card(), conversation_id="c", stage_card_id="sc"
    )
    assert card is not None
    merged, err = apply_motion_override(card, "本案原被告对抗争议是否成立")
    assert err == ""
    assert merged is not None
    assert merged["motion"] == "本案原被告对抗争议是否成立"


def test_debate_arguments_note_goes_to_kickoff_ask():
    card = build_stage_card_payload(
        _valid_card(), conversation_id="c", stage_card_id="sc"
    )
    assert card is not None
    args = debate_arguments_from_card(card, note="开赛时先澄清事实边界")
    assert args["motion"] == card["motion"]
    assert args["_kickoff_ask"] == "开赛时先澄清事实边界"
    assert args["thorough"] is True
    assert args["max_rounds"] == card["max_rounds"]


def test_debate_arguments_maps_thorough_and_rounds_from_card():
    card = build_stage_card_payload(
        _valid_card(), conversation_id="c", stage_card_id="sc"
    )
    assert card is not None
    card["thorough"] = False
    card["max_rounds"] = 1
    args = debate_arguments_from_card(card)
    assert args["thorough"] is False
    assert args["max_rounds"] == 1


def test_debate_arguments_maps_moderator_from_card():
    card = build_stage_card_payload(
        _valid_card(), conversation_id="c", stage_card_id="sc"
    )
    assert card is not None
    card["moderator_model"] = "deepseek-chat"
    card["moderator_origin"] = "byok"
    card["moderator_provider_id"] = "ds"
    args = debate_arguments_from_card(card)
    assert args["moderator_model"] == "deepseek-chat"
    assert args["moderator_origin"] == "byok"
    assert args["moderator_provider_id"] == "ds"


def test_research_first_user_message_mentions_motion():
    text = research_first_user_message(motion="一审判决是否过重")
    assert "一审判决是否过重" in text
    assert "调研" in text


def test_decisions_are_binary():
    assert frozenset({"start_debate", "research_first"}) == STAGE_CARD_DECISIONS


def test_fold_pending_includes_stage_card():
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
                "thorough": True,
                "max_rounds": 5,
            },
        }
    ]
    pending = fold_pending_interactions(entries, message_id="m1")
    assert len(pending) == 1
    assert pending[0].kind == "stage_card"
    assert pending[0].id == "sc_1"
    leaf = project_interaction_leaf(fold_interactions(entries)[0])
    assert leaf["kind"] == "stage_card"
    assert leaf["motion"] == "命题"


@pytest.mark.asyncio
async def test_prewrite_stage_card_resolved_passes_trace_id(monkeypatch):
    """钉住 resolve 落 settlement 必传 trace_id（缺参曾导致 HTTP 500）。"""
    from agentcore.conversation import stage_card_resolve as mod

    captured: dict = {}

    async def _fake_prewrite(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(mod, "prewrite_settlement_direct", _fake_prewrite)
    monkeypatch.setattr(mod, "already_settled_in_writer", lambda _event: False)

    clear_log_context()
    with log_context(trace_id="trace_from_ctx"):
        await mod.prewrite_stage_card_resolved(
            turn_id="turn_host",
            conversation_id="conv_1",
            stage_card_id="sc_1",
            decision="start_debate",
            note="",
        )
    assert "trace_id" in captured
    assert captured["trace_id"] == "trace_from_ctx"
    assert captured["turn_id"] == "turn_host"
    assert captured["conversation_id"] == "conv_1"
    assert captured["event"].type.value == "stage_card_resolved"

    clear_log_context()
    captured.clear()
    await mod.prewrite_stage_card_resolved(
        turn_id="turn_host",
        conversation_id="conv_1",
        stage_card_id="sc_1",
        decision="research_first",
    )
    assert "trace_id" in captured
    assert captured["trace_id"] is None


def test_host_triple_stamped_on_payload_and_preserved_through_override():
    card = build_stage_card_payload(
        _valid_card(),
        conversation_id="c",
        stage_card_id="sc",
        host_execution_id="exec_1",
        synthesizer_run_id="synthesizer",
        host_message_id="m_host",
    )
    assert card is not None
    assert card["host_execution_id"] == "exec_1"
    assert card["synthesizer_run_id"] == "synthesizer"
    assert card["host_message_id"] == "m_host"
    merged, err = apply_motion_override(card, "本案原被告对抗争议是否成立")
    assert err == ""
    assert merged is not None
    assert merged["host_execution_id"] == "exec_1"
    assert merged["synthesizer_run_id"] == "synthesizer"
    assert merged["host_message_id"] == "m_host"


def test_host_triple_from_journal_mlr_shape():
    entries = [
        {
            "kind": "run_plan",
            "payload": {
                "execution_id": "exec_mlr",
                "plan_type": "multi_agent",
                "runs": [
                    {"id": "lens_0", "agent_id": "lens_0"},
                    {"id": "synthesizer", "agent_id": "synthesizer"},
                ],
            },
        },
        {"kind": "run_completed", "payload": {"run_id": "synthesizer"}},
    ]
    triple = host_triple_from_journal(entries, host_message_id="m1")
    assert triple == {
        "host_execution_id": "exec_mlr",
        "synthesizer_run_id": "synthesizer",
        "host_message_id": "m1",
    }


def test_host_triple_from_journal_dag_namespaced_synthesizer():
    """P0 实证：run_plan 上是 del_<uuid>_synthesizer，发卡必须仍能打三元组。"""
    rid = "del_2468005e-cf60-4032-84e4-9eca57633098_synthesizer"
    entries = [
        {
            "kind": "run_plan",
            "payload": {
                "execution_id": "1dd8bc00-bef7-40fc-9b73-fa343380c25b",
                "plan_type": "multi_agent",
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
    triple = host_triple_from_journal(entries, host_message_id="m_host")
    assert triple == {
        "host_execution_id": "1dd8bc00-bef7-40fc-9b73-fa343380c25b",
        "synthesizer_run_id": rid,
        "host_message_id": "m_host",
    }


def test_turn_keeps_stage_card_flag():
    reset_stage_card_turn_flags()
    assert turn_keeps_stage_card() is False
    mark_turn_keeps_stage_card()
    assert turn_keeps_stage_card() is True
    clear_turn_keeps_stage_card()
    assert turn_keeps_stage_card() is False
    mark_turn_keeps_stage_card()
    reset_stage_card_turn_flags()
    assert turn_keeps_stage_card() is False


def test_turn_advanced_stage_from_entries_detects_debate_and_delegate():
    assert turn_advanced_stage_from_entries(
        [{"type": "tool_use_start", "payload": {"tool_name": "debate", "arguments": {}}}]
    )
    assert turn_advanced_stage_from_entries(
        [
            {
                "type": "tool_use_start",
                "payload": {
                    "tool_name": "delegate",
                    "arguments": {"tasks": [{"role": "法律视角", "task": "查"}]},
                },
            }
        ]
    )
    assert not turn_advanced_stage_from_entries(
        [
            {
                "type": "tool_use_start",
                "payload": {"tool_name": "web_search", "arguments": {}},
            }
        ]
    )


@pytest.mark.asyncio
async def test_refuse_stage_card_resolve_is_gone():
    from agentcore.core.errors import GoneError
    from agentcore.runtime.kickoff.retired import (
        STAGE_CARD_UNRECOVERABLE,
        refuse_stage_card_resolve,
    )

    with pytest.raises(GoneError, match="开辩请直接") as ei:
        refuse_stage_card_resolve()
    assert ei.value.message == STAGE_CARD_UNRECOVERABLE


@pytest.mark.asyncio
async def test_consume_pending_stage_card_for_debate_prepares_without_resolve(monkeypatch):
    """口头开赛 helper：合并参数 + keep，``debate.started`` 前不 resolve。"""
    from agentcore.conversation import stage_card_resolve as mod

    payload = build_stage_card_payload(
        _valid_card(),
        conversation_id="conv_x",
        stage_card_id="sc_oral",
        host_execution_id="exec_h",
        synthesizer_run_id="synthesizer",
        host_message_id="m_host",
    )
    assert payload is not None

    async def _fake_list(_cid: str):
        return [("turn_host", "sc_oral", payload)]

    prewrites: list[dict] = []

    async def _fake_prewrite(**kwargs):
        prewrites.append(kwargs)

    monkeypatch.setattr(mod, "list_pending_stage_cards", _fake_list)
    monkeypatch.setattr(mod, "prewrite_stage_card_resolved", _fake_prewrite)

    reset_stage_card_turn_flags()
    merged, override, err = await mod.consume_pending_stage_card_for_debate(
        conversation_id="conv_x",
        ceo_motion="本案原被告对抗争议是否成立",
        sink=None,
    )
    assert err == ""
    assert override == "本案原被告对抗争议是否成立"
    assert merged is not None
    assert merged["motion"] == "本案原被告对抗争议是否成立"
    assert merged["host_execution_id"] == "exec_h"
    assert merged["_host_turn_id"] == "turn_host"
    assert prewrites == []  # debate.started 前不 resolve
    assert turn_keeps_stage_card() is True


@pytest.mark.asyncio
async def test_finalize_stage_card_resolves_and_orphans_siblings(monkeypatch):
    from agentcore.conversation import stage_card_resolve as mod

    prewrites: list[dict] = []
    orphans: list[dict] = []

    async def _fake_prewrite(**kwargs):
        prewrites.append(kwargs)

    async def _fake_orphan(cid, *, keep_id, sink=None, reason="superseded"):
        orphans.append(
            {"cid": cid, "keep_id": keep_id, "reason": reason}
        )
        return ["sc_old"]

    monkeypatch.setattr(mod, "prewrite_stage_card_resolved", _fake_prewrite)
    monkeypatch.setattr(mod, "orphan_sibling_stage_cards", _fake_orphan)

    await mod.finalize_stage_card_start_debate(
        conversation_id="conv",
        host_turn_id="turn_h",
        stage_card_id="sc_new",
        note="",
        motion_override=None,
        sink=None,
    )
    assert prewrites and prewrites[0]["decision"] == "start_debate"
    assert orphans == [{"cid": "conv", "keep_id": "sc_new", "reason": "superseded"}]


@pytest.mark.asyncio
async def test_consume_pending_gate_fail_keeps_card(monkeypatch):
    from agentcore.conversation import stage_card_resolve as mod

    payload = build_stage_card_payload(
        _valid_card(), conversation_id="c", stage_card_id="sc"
    )
    assert payload is not None

    async def _fake_list(_cid: str):
        return [("turn_host", "sc", payload)]

    called = {"n": 0}

    async def _boom(**_kwargs):
        called["n"] += 1

    monkeypatch.setattr(mod, "list_pending_stage_cards", _fake_list)
    monkeypatch.setattr(mod, "prewrite_stage_card_resolved", _boom)
    monkeypatch.setattr(
        mod, "apply_motion_override", lambda *_a, **_k: (None, "motion 检定未通过")
    )

    merged, _override, err = await mod.consume_pending_stage_card_for_debate(
        conversation_id="c",
        ceo_motion="任意改写",
        sink=None,
    )
    assert merged is None
    assert err
    assert called["n"] == 0  # 闸失败不得 resolve


@pytest.mark.asyncio
async def test_maybe_orphan_skipped_when_turn_keeps(monkeypatch):
    from agentcore.conversation import stage_card_resolve as mod

    called = {"n": 0}

    async def _fake_orphan(*_a, **_k):
        called["n"] += 1
        return ["sc"]

    monkeypatch.setattr(mod, "orphan_conversation_stage_cards", _fake_orphan)
    reset_stage_card_turn_flags()
    mark_turn_keeps_stage_card()
    out = await mod.maybe_orphan_stage_cards_at_turn_end("conv")
    assert out == []
    assert called["n"] == 0

    reset_stage_card_turn_flags()
    out = await mod.maybe_orphan_stage_cards_at_turn_end("conv")
    assert called["n"] == 1
    assert out == ["sc"]


@pytest.mark.asyncio
async def test_orphan_writes_journal_fact(monkeypatch):
    """收尾 orphan 必须落 interaction_orphaned journal 事实（非仅日志）。"""
    from agentcore.conversation import stage_card_resolve as mod

    async def _fake_list(_cid: str):
        return [("turn_host", "sc_1", {"motion": "x"})]

    facts: list[dict] = []

    async def _fake_emit(**kwargs):
        facts.append(kwargs)

    monkeypatch.setattr(mod, "list_pending_stage_cards", _fake_list)
    monkeypatch.setattr(
        "agentcore.runtime.interaction_orphan.emit_orphan_fact", _fake_emit
    )

    out = await mod.orphan_conversation_stage_cards("conv_z")
    assert out == ["sc_1"]
    assert len(facts) == 1
    assert facts[0]["interaction_id"] == "sc_1"
    assert facts[0]["kind"] == "stage_card"
    assert facts[0]["turn_id"] == "turn_host"
    assert facts[0]["prefer_direct"] is True


@pytest.mark.asyncio
async def test_drive_top_level_no_longer_hangs_team_preview(monkeypatch):
    """顶层也不再挂 team_preview。"""
    from agentcore.core.types import AutonomyPolicy
    from agentcore.runtime.delegate.drive import _team_preview_before_workers
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec

    monkeypatch.setattr(
        "agentcore.runtime.sandbox_approval.worker_gate_applies", lambda *_a, **_k: False
    )

    class _Tool:
        _depth = 0
        _permission_axes = AutonomyPolicy.LESS_INTERRUPT
        _active_playbook = None
        _pending_pause = False
        _base_tool_context = type("C", (), {"backend": None})()
        _approval_gate = None

    plan = RunPlan(nodes=[RunSpec(run_id="a", agent_id="a", role="r", task="t")])
    result = await _team_preview_before_workers(
        _Tool(),
        plan,
        complexity_hint="standard",
        seed_completed=None,
        call_idx=0,
    )
    assert result is None
    result2 = await _team_preview_before_workers(
        _Tool(),
        plan,
        complexity_hint="standard",
        seed_completed=None,
        call_idx=1,
    )
    assert result2 is None


@pytest.mark.asyncio
async def test_list_recent_turn_ids_orders_by_session_time_not_seq():
    """长回合高 seq 不得挤掉更早回合的卡扫描窗口。"""
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
            # Simulates SQL order: newest session turn first.
            return _Result(["turn_new", "turn_old_with_card"])

    session = _Session()
    repo = TurnJournalRepository(session)
    ids = await repo.list_recent_turn_ids("conv", limit=40)
    assert ids == ["turn_new", "turn_old_with_card"]
    sql = str(session.last_stmt)
    assert "max" in sql.lower() or "GROUP BY" in sql.upper()


@pytest.mark.asyncio
async def test_emit_stage_card_journal_failure_returns_none(monkeypatch):
    """journal prewrite 失败 → 不返回 stage_card_id（保留 followup 芯片兜底）。"""
    from agentcore.runtime.kickoff import stage_card as sc_mod

    async def _boom(**_k):
        raise RuntimeError("db down")

    async def _no_prior(*_a, **_k):
        return []

    monkeypatch.setattr(
        "agentcore.runtime.settlement.prewrite_settlement_direct", _boom
    )
    monkeypatch.setattr(
        "agentcore.conversation.stage_card_resolve.orphan_conversation_stage_cards",
        _no_prior,
    )
    out = await sc_mod.emit_stage_card_for_motion(
        None,
        conversation_id="c",
        motion_card=_valid_card(),
        turn_id="turn_1",
        trace_id="t",
    )
    assert out is None


@pytest.mark.asyncio
async def test_emit_supersedes_prior_pending(monkeypatch):
    from agentcore.runtime.kickoff import stage_card as sc_mod

    superseded: list[dict] = []

    async def _orphan(cid, *, sink=None, reason=None, exclude_ids=None):
        superseded.append({"cid": cid, "reason": reason})
        return ["sc_old"]

    async def _ok(**_k):
        return None

    monkeypatch.setattr(
        "agentcore.conversation.stage_card_resolve.orphan_conversation_stage_cards",
        _orphan,
    )
    monkeypatch.setattr(
        "agentcore.runtime.settlement.prewrite_settlement_direct", _ok
    )
    out = await sc_mod.emit_stage_card_for_motion(
        None,
        conversation_id="c",
        motion_card=_valid_card(),
        turn_id="turn_1",
    )
    assert out
    assert superseded == [{"cid": "c", "reason": "superseded"}]


@pytest.mark.asyncio
async def test_resolve_host_attach_invalid_mid_returns_none(monkeypatch):
    """卡上三元组存在但宿主图失效 → None（调用方回落角色匹配）。"""
    from agentcore.runtime.kickoff import stage_card as sc_mod

    async def _empty(_mid):
        return []

    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.load_host_journal_entries",
        _empty,
    )
    attach = await sc_mod.resolve_host_attach_from_card(
        {
            "host_execution_id": "exec_gone",
            "host_message_id": "m_gone",
            "synthesizer_run_id": "syn",
        }
    )
    assert attach is None


@pytest.mark.asyncio
async def test_mlr_stop_clears_keep_flag(monkeypatch):
    """MLR 开跑仍 keep stage_card；新 team_preview STOP 路径不再触发。"""
    from agentcore.core.types import AutonomyPolicy
    from agentcore.runtime.delegate.drive import _team_preview_before_workers
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec

    async def _finalize_stopped(*_a, **_k):
        from agentcore.tools.protocol import ToolEffect, ToolResult

        return ToolResult(
            tool_call_id="", success=True, output="stopped", effect=ToolEffect.CONTINUE
        )

    monkeypatch.setattr(
        "agentcore.runtime.sandbox_approval.worker_gate_applies", lambda *_a, **_k: False
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.supervised.finalize_stopped",
        _finalize_stopped,
    )

    class _Tool:
        _depth = 0
        _permission_axes = AutonomyPolicy.LESS_INTERRUPT
        _active_playbook = None
        _pending_pause = False
        _base_tool_context = type("C", (), {"backend": None})()
        _approval_gate = None

    reset_stage_card_turn_flags()
    mark_turn_keeps_stage_card()  # simulate stale early mark
    plan = RunPlan(nodes=[RunSpec(run_id="a", agent_id="a", role="r", task="t")])
    await _team_preview_before_workers(
        _Tool(),
        plan,
        complexity_hint="standard",
        seed_completed=None,
        call_idx=0,
    )
    # 新卡不挂：MLR 开跑 keep，不再走开工 STOP 清 keep。
    assert turn_keeps_stage_card() is True


@pytest.mark.asyncio
async def test_emit_stage_card_stamps_host_triple_from_mlr_journal(monkeypatch):
    """幕1 MLR 正常收尾发卡 → payload 必带宿主三元组。"""
    from agentcore.runtime.kickoff import stage_card as sc_mod

    rid = "del_abc_synthesizer"
    journal = [
        {
            "kind": "run_plan",
            "payload": {
                "execution_id": "exec_host",
                "plan_type": "multi_agent",
                "runs": [{"id": rid, "agent_id": rid}],
            },
        },
        {"kind": "run_completed", "payload": {"run_id": rid}},
    ]
    captured: dict = {}

    async def _ok(**kwargs):
        captured["event"] = kwargs.get("event")

    async def _no_prior(*_a, **_k):
        return []

    monkeypatch.setattr(
        "agentcore.runtime.settlement.prewrite_settlement_direct", _ok
    )
    monkeypatch.setattr(
        "agentcore.conversation.stage_card_resolve.orphan_conversation_stage_cards",
        _no_prior,
    )
    out = await sc_mod.emit_stage_card_for_motion(
        None,
        conversation_id="conv",
        motion_card=_valid_card(),
        turn_id="m_host",
        journal_entries=journal,
    )
    assert out
    ev = captured["event"]
    assert ev.payload["host_execution_id"] == "exec_host"
    assert ev.payload["synthesizer_run_id"] == rid
    assert ev.payload["host_message_id"] == "m_host"


@pytest.mark.asyncio
async def test_mlr_stage_card_oral_consume_attaches_host(monkeypatch):
    """复现链：幕1发卡(带三元组) → 口头消费 → resolve_host_attach_from_card 成功。"""
    from agentcore.conversation import stage_card_resolve as resolve_mod
    from agentcore.runtime.kickoff import stage_card as sc_mod

    rid = "del_abc_synthesizer"
    host_entries = [
        {
            "kind": "run_plan",
            "payload": {
                "execution_id": "exec_host",
                "plan_type": "multi_agent",
                "act": {"act_id": "act-1", "kind": "multi_agent"},
                "runs": [{"id": rid, "agent_id": rid}],
            },
        },
        {"kind": "run_completed", "payload": {"run_id": rid}},
    ]
    prewrites: list = []

    async def _ok(**kwargs):
        prewrites.append(kwargs)

    async def _no_prior(*_a, **_k):
        return []

    monkeypatch.setattr(
        "agentcore.runtime.settlement.prewrite_settlement_direct", _ok
    )
    monkeypatch.setattr(
        "agentcore.conversation.stage_card_resolve.orphan_conversation_stage_cards",
        _no_prior,
    )
    card_id = await sc_mod.emit_stage_card_for_motion(
        None,
        conversation_id="conv_gold",
        motion_card=_valid_card(),
        turn_id="m_host",
        journal_entries=host_entries,
    )
    assert card_id
    stamped = prewrites[0]["event"].payload
    assert stamped["host_execution_id"] == "exec_host"

    async def _fake_list(_cid: str):
        return [("m_host", card_id, stamped)]

    monkeypatch.setattr(resolve_mod, "list_pending_stage_cards", _fake_list)
    monkeypatch.setattr(resolve_mod, "prewrite_stage_card_resolved", _ok)

    reset_stage_card_turn_flags()
    merged, _override, err = await resolve_mod.consume_pending_stage_card_for_debate(
        conversation_id="conv_gold",
        ceo_motion=None,
        sink=None,
    )
    assert err == ""
    assert merged is not None
    assert merged["host_execution_id"] == "exec_host"
    assert merged["synthesizer_run_id"] == rid

    async def _load(_mid: str):
        return host_entries

    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.load_host_journal_entries",
        _load,
    )
    attach = await sc_mod.resolve_host_attach_from_card(
        merged, append_message_id="m_debate"
    )
    assert attach is not None
    assert attach.execution_id == "exec_host"
    assert attach.anchor_run_id == rid
    assert attach.act_id == "act-2"
    assert attach.same_turn is False


def _debate_tool_for_stage_card_finalize(sink=None):
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
    return DebateTool(
        llm=Provider([]),
        sink=sink or EventSink(),
        system_prompt="sys",
        user_message="开辩",
        tools=ToolRegistry(),
        base_tool_context=ctx,
        conversation_id="conv_sc",
        message_id="m_debate",
        captain_run_id="captain",
        approval_gate=None,
    )


@pytest.mark.asyncio
async def test_oral_debate_does_not_consume_stage_card(monkeypatch):
    """口头开辩是独立重活：不消费推进卡、不走 stage_card 授权。"""
    from agentcore.tools.builtin.debate.tool import DebateTool
    from agentcore.tools.protocol import ToolEffect, ToolResult

    consume_calls: list = []

    async def _fake_consume(**kwargs):
        consume_calls.append(kwargs)
        raise AssertionError("must not consume leftover stage_card")

    async def _fake_run(self, config, usage_metadata):
        return ToolResult(
            tool_call_id="",
            success=True,
            output="ok",
            effect=ToolEffect.CONTINUE,
        )

    monkeypatch.setattr(
        "agentcore.conversation.stage_card_resolve.consume_pending_stage_card_for_debate",
        _fake_consume,
    )
    monkeypatch.setattr(DebateTool, "_run_moderator", _fake_run)

    tool = _debate_tool_for_stage_card_finalize()
    reset_stage_card_turn_flags()
    result = await tool.execute(
        {
            "motion": _valid_card()["motion"],
            "form": "debate",
            "sides": _valid_card()["sides"],
            "thorough": False,
        },
        tool._base_tool_context,
    )
    assert result.success is True
    assert consume_calls == []
    assert tool._debate_authorized_by in (None, "auto")
    assert tool._stage_card_finalize is None


@pytest.mark.asyncio
async def test_finalize_at_debate_started_before_moderator_run(monkeypatch):
    """成功边界 = debate.started：finalize 先于 moderator.run；开跑后失败不回 pending。"""
    from agentcore.tools.builtin.debate import tool as debate_tool_mod

    order: list[str] = []

    async def _fake_finalize(**kwargs):
        order.append("finalize")
        assert kwargs["stage_card_id"] == "sc_btn"
        assert kwargs["host_turn_id"] == "turn_host"

    class _FakeModerator:
        # 崩溃后 _run_moderator 的 finally 要发终帧 + 入账，故 double 须带用量面。
        usage = TokenUsage()
        llm_rounds = 0

        def __init__(self, **_kw):
            pass

        async def _complete_json(self, *_a, **_k):
            return {}

        async def run(self, *_a, **_k):
            assert "finalize" in order
            order.append("moderator_run")
            raise RuntimeError("mid-debate boom")

    async def _no_attach(self, _config):
        return None

    monkeypatch.setattr(debate_tool_mod, "Moderator", _FakeModerator)
    monkeypatch.setattr(
        "agentcore.conversation.stage_card_resolve.finalize_stage_card_start_debate",
        _fake_finalize,
    )
    monkeypatch.setattr(debate_tool_mod.DebateTool, "_resolve_host_attach", _no_attach)

    tool = _debate_tool_for_stage_card_finalize()
    tool._debate_authorized_by = "stage_card"
    tool._debate_stage_card = {
        "stage_card_id": "sc_btn",
        "_host_turn_id": "turn_host",
        "motion": _valid_card()["motion"],
        "sides": _valid_card()["sides"],
        "form": "debate",
    }
    tool._stage_card_finalize = {
        "host_turn_id": "turn_host",
        "stage_card_id": "sc_btn",
        "note": "",
        "motion_override": None,
    }
    reset_stage_card_turn_flags()
    mark_turn_keeps_stage_card()

    from agentcore.runtime.costing import usage_metadata
    from agentcore.runtime.debate import (
        DebateConfig,
        DebateForm,
        DebateSide,
        RoundPolicy,
    )

    config = DebateConfig(
        motion=_valid_card()["motion"],
        form=DebateForm.DEBATE,
        sides=[
            DebateSide(key="pro", name="正方", stance="a"),
            DebateSide(key="con", name="反方", stance="b"),
        ],
        policy=RoundPolicy.for_form(DebateForm.DEBATE, thorough=False),
    )
    result = await tool._run_moderator(config, usage_metadata(tool._acc.usage))
    assert result.success is False
    assert order == ["finalize", "moderator_run"]
    assert tool._stage_card_finalized_at_start is True


@pytest.mark.asyncio
async def test_kickoff_failure_does_not_finalize(monkeypatch):
    """口头开辩失败：不消费推进卡、不 finalize。"""
    from agentcore.tools.builtin.debate.tool import DebateTool
    from agentcore.tools.protocol import ToolEffect, ToolResult

    finalize_calls: list = []

    async def _fake_finalize(**kwargs):
        finalize_calls.append(kwargs)

    async def _fail_before_started(self, config, usage_metadata):
        assert self._stage_card_finalize is None
        return ToolResult(
            tool_call_id="",
            success=False,
            output="辩论执行失败：启动失败。",
            effect=ToolEffect.CONTINUE,
        )

    monkeypatch.setattr(
        "agentcore.conversation.stage_card_resolve.finalize_stage_card_start_debate",
        _fake_finalize,
    )
    monkeypatch.setattr(DebateTool, "_run_moderator", _fail_before_started)

    tool = _debate_tool_for_stage_card_finalize()
    reset_stage_card_turn_flags()
    result = await tool.execute(
        {
            "motion": _valid_card()["motion"],
            "form": "debate",
            "sides": _valid_card()["sides"],
            "thorough": False,
        },
        tool._base_tool_context,
    )
    assert result.success is False
    assert tool._stage_card_finalized_at_start is False
    assert finalize_calls == []


def _patch_start_debate_harness(monkeypatch, mod, *, pipeline):
    """Shared stubs for ``run_stage_card_start_debate`` without real DB / workspace."""
    from agentcore.core.types import AutonomyPolicy, recipe_to_axes

    async def _noop_placeholder(**_k):
        return None

    async def _noop_persist(**_k):
        return None

    async def _none(*_a, **_k):
        return None

    async def _preset(*_a, **_k):
        return recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT)

    async def _empty_history(*_a, **_k):
        return []

    class _FakeConv:
        folder_id = None

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return None

    class _FakeConvRepo:
        def __init__(self, _db):
            pass

        async def get_by_id_unscoped(self, _cid):
            return _FakeConv()

    class _FakeBoardRepo:
        def __init__(self, _db):
            pass

        async def get_by_conversation_id(self, *_a, **_k):
            return None

    class _FakeMsgRepo:
        def __init__(self, _db):
            pass

        async def create(self, **_k):
            return None

    monkeypatch.setattr(mod, "run_stage_card_debate_pipeline", pipeline)
    monkeypatch.setattr(mod, "create_assistant_placeholder", _noop_placeholder)
    monkeypatch.setattr(mod, "persist_turn_result", _noop_persist)
    monkeypatch.setattr(mod, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(mod, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(mod, "BoardRepository", _FakeBoardRepo)
    monkeypatch.setattr("agentcore.db.repositories.MessageRepository", _FakeMsgRepo)
    monkeypatch.setattr(mod, "resolve_local_binding", _none)
    monkeypatch.setattr(mod, "resolve_profile_set", _none)
    monkeypatch.setattr(mod, "resolve_permission_axes", _preset)
    monkeypatch.setattr(mod, "load_chat_context", _empty_history)
    monkeypatch.setattr(mod, "build_turn_backend", AsyncMock(return_value=object()))
    monkeypatch.setattr(mod, "session_callbacks", lambda *_a: (None, None))
    monkeypatch.setattr(mod, "suspension_callbacks", lambda: (None, None))


@pytest.mark.asyncio
async def test_button_path_finalizes_at_started_not_after_pipeline(monkeypatch):
    """按钮路径：card 带 _host_turn_id；pipeline 开跑即 finalize；外层不再二次 finalize。"""
    from agentcore.conversation import stage_card_resolve as mod
    from agentcore.runtime.events import EventSink

    pipeline_cards: list[dict] = []
    finalize_calls: list[dict] = []

    async def _fake_pipeline(**kwargs):
        pipeline_cards.append(dict(kwargs.get("card") or {}))
        return {
            "finish_reason": "end_turn",
            "error": None,
            "stage_card_finalized": True,
            "content": "ok",
        }

    async def _fake_finalize(**kwargs):
        finalize_calls.append(kwargs)

    _patch_start_debate_harness(monkeypatch, mod, pipeline=_fake_pipeline)
    monkeypatch.setattr(mod, "finalize_stage_card_start_debate", _fake_finalize)

    sink = EventSink()
    await mod.run_stage_card_start_debate(
        conversation_id="conv",
        user_id="u",
        sink=sink,
        card=_valid_card(),
        note="go",
        host_turn_id="turn_host",
        stage_card_id="sc_btn",
        motion_override=None,
    )
    assert pipeline_cards
    assert pipeline_cards[0]["_host_turn_id"] == "turn_host"
    assert pipeline_cards[0]["stage_card_id"] == "sc_btn"
    assert pipeline_cards[0]["_resolve_note"] == "go"
    # 外层不再等整场结束 finalize（已在 debate.started 边界完成）
    assert finalize_calls == []


@pytest.mark.asyncio
async def test_button_path_startup_failure_keeps_pending_semantics(monkeypatch):
    """按钮路径启动失败（未 finalized）：kept_pending 日志，外层不误 finalize。"""
    from agentcore.conversation import stage_card_resolve as mod
    from agentcore.runtime.events import EventSink

    async def _fake_pipeline(**_k):
        return {
            "finish_reason": "error",
            "error": "启动失败",
            "stage_card_finalized": False,
            "content": "",
        }

    finalize_calls: list = []
    log_events: list[str] = []

    async def _fake_finalize(**kwargs):
        finalize_calls.append(kwargs)

    def _capture_info(event, **_kw):
        log_events.append(str(event))

    _patch_start_debate_harness(monkeypatch, mod, pipeline=_fake_pipeline)
    monkeypatch.setattr(mod, "finalize_stage_card_start_debate", _fake_finalize)
    monkeypatch.setattr(mod.logger, "info", _capture_info)

    sink = EventSink()
    await mod.run_stage_card_start_debate(
        conversation_id="conv",
        user_id="u",
        sink=sink,
        card=_valid_card(),
        host_turn_id="turn_host",
        stage_card_id="sc_fail",
    )
    assert finalize_calls == []
    assert "stage_card.start_debate_failed_kept_pending" in log_events


@pytest.mark.asyncio
async def test_button_path_after_started_failure_keeps_resolved(monkeypatch):
    """开跑后失败：已 finalized → 不回 pending（日志 after_started，非 kept_pending）。"""
    from agentcore.conversation import stage_card_resolve as mod
    from agentcore.runtime.events import EventSink

    async def _fake_pipeline(**_k):
        return {
            "finish_reason": "error",
            "error": "中途崩溃",
            "stage_card_finalized": True,
            "content": "",
        }

    finalize_calls: list = []
    log_events: list[str] = []

    async def _fake_finalize(**kwargs):
        finalize_calls.append(kwargs)

    def _capture_info(event, **_kw):
        log_events.append(str(event))

    _patch_start_debate_harness(monkeypatch, mod, pipeline=_fake_pipeline)
    monkeypatch.setattr(mod, "finalize_stage_card_start_debate", _fake_finalize)
    monkeypatch.setattr(mod.logger, "info", _capture_info)

    sink = EventSink()
    await mod.run_stage_card_start_debate(
        conversation_id="conv",
        user_id="u",
        sink=sink,
        card=_valid_card(),
        host_turn_id="turn_host",
        stage_card_id="sc_mid",
    )
    assert finalize_calls == []  # 已在 pipeline 内 started 边界完成
    assert "stage_card.start_debate_failed_after_started" in log_events
    assert "stage_card.start_debate_failed_kept_pending" not in log_events
