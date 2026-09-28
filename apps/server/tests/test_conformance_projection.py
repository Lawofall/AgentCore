"""Independent guard for the ProjectedTurn oracle (前端技术与架构 §十 SSE 与协议一致性).

The `pnpm conformance` gate proves "mobile fold == oracle golden"; this proves the
oracle itself is correct with HAND-VERIFIED expectations, so a correlated bug (oracle
and a fold making the same mistake) can't pass both. Runs the full export pipeline
(vector → serialize → project), the exact bytes the golden is written from.

**Coverage is a curated subset, not every vector.** Failure / abort / gate-lifecycle
faces live in the sibling ``test_conformance_projection_failures``; the rest of the
vector set rides `pnpm conformance` alone. ``test_sentinel_coverage_ratchet`` at the
bottom pins how large that uncovered remainder is allowed to be, so a new vector either
gets a hand-verified assertion or has to widen the baseline in the PR diff.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcore.conformance.export import build_fixtures
from agentcore.conformance.vectors import VECTORS
from agentcore.runtime.interaction import GATE_KINDS


def _pending_gates(p: dict) -> list[dict]:
    """Gate interactions still awaiting the user (legacy pendingInteraction slot)."""
    return [
        i
        for i in p["interactions"]
        if i.get("status") == "pending" and i.get("kind") in GATE_KINDS
    ]


@pytest.fixture(scope="module")
def projected() -> dict[str, dict]:
    return {fx["name"]: fx["projected"] for fx in build_fixtures()}


def test_ask_user_interactions_have_no_context_key(projected):
    """ask 文案只走 question；投影叶不得再带 context。"""
    for name, p in projected.items():
        for leaf in p.get("interactions") or []:
            if leaf.get("kind") == "ask_user":
                assert "context" not in leaf, name


def test_single_agent_text(projected):
    p = projected["single_agent_text"]
    assert p["status"] == "completed"
    assert p["finishReason"] == "end_turn"
    assert p["content"] == "你好，世界！"
    assert p["reasoning"] == "先想一下。好的。"
    assert p["process"] == [
        {"kind": "reasoning", "text": "先想一下。好的。"},
        {"kind": "content", "text": "你好，世界！"},
    ]
    assert p["runs"] == []
    assert p["agents"] == []
    assert p["progress"] == {"completed": 0, "total": 0}
    assert p["interactions"] == []
    assert p["cost"]["total"] == 360_000


def test_single_agent_user_interjection_steer_marker(projected):
    # Mid-flight steer: received pins one zero-width marker; injected does not duplicate.
    # Content splits around the marker (pre / post) — causal order, not trailing coalesce.
    p = projected["single_agent_user_interjection_steer"]
    assert [s["kind"] for s in p["process"]] == [
        "reasoning",
        "content",
        "user_interjection",
        "content",
    ]
    assert p["process"][1]["text"] == "你好"
    assert p["process"][2] == {
        "kind": "user_interjection",
        "interjection_id": "inj-steer-1",
    }
    assert p["process"][3]["text"] == "，世界！"
    assert p["userInterjections"] == [
        {
            "interjectionId": "inj-steer-1",
            "executionId": "exec-classic-1",
            "content": "改成用中文总结",
            "status": "injected",
            "note": None,
        }
    ]


def test_single_agent_cancelled(projected):
    p = projected["single_agent_cancelled"]
    assert p["status"] == "cancelled"
    assert p["finishReason"] == "cancelled"
    assert p["reasoning"] == "先梳理要点。"
    assert p["content"] == "根据目前信息，建议分三步："
    assert p["cost"]["total"] == 360_000


def test_multi_agent_delegate_tree(projected):
    p = projected["multi_agent_delegate"]
    assert p["status"] == "completed"
    # 统一团队时间线: the captain's OWN inline timeline rides `process` (content + a `team`
    # marker fixing where the collaboration graph slots in — the orchestration call itself
    # makes NO tool step). Worker outputs ride `runs`/`agents`, not this lane.
    assert [s["kind"] for s in p["process"]] == ["content", "team", "content"]
    assert [s["execution_id"] for s in p["process"] if s["kind"] == "team"] == ["exec1"]
    assert p["content"] == "我来安排团队。 团队已完成。"
    assert len(p["runs"]) == 2
    assert all(r["status"] == "completed" for r in p["runs"])
    assert p["progress"] == {"completed": 2, "total": 2}
    assert p["agents"][0]["id"] == "w1"
    assert p["agents"][0]["status"] == "completed"
    assert p["agents"][0]["output"] == "调研结论"
    # usage/cost ride verbatim from run_completed.
    assert p["runs"][0]["cost"]["total"] == 360_000
    assert p["runs"][0]["usage"]["input"] == 1200


def test_approval_paused(projected):
    p = projected["approval_paused"]
    assert p["status"] == "paused"
    assert p["finishReason"] is None
    assert p["interactions"] == [
        {
            "kind": "approval",
            "id": "tc1",
            "status": "pending",
            "toolCallId": "tc1",
            "toolName": "code_execute",
            "arguments": {"code": "print(1)"},
        }
    ]


def test_approval_resolved_clears_pending(projected):
    p = projected["approval_resolved_continue"]
    assert p["status"] == "completed"
    assert _pending_gates(p) == []
    assert p["interactions"][0]["status"] == "resolved"
    assert p["content"] == "我需要运行代码。运行结果是 1。"


def test_single_agent_checkpoint(projected):
    parked = projected["single_agent_checkpoint"]
    assert parked["status"] == "paused"
    assert parked["cost"] is None
    assert parked["content"] == "开始前我确认一下方向："
    assert [s["kind"] for s in parked["process"]] == ["reasoning", "content", "checkpoint"]
    assert parked["process"][1]["text"] == "开始前我确认一下方向："


def test_resume_content_continuity_concatenates_across_ask_user(projected):
    """挂起恢复正文续拼：ask_user 前后 content 拼接，不钉 plan_review。"""
    p = projected["resume_content_continuity"]
    assert p["status"] == "completed"
    assert p["content"] == "阶段成果如下。按确认继续交付。"
    assert [s["kind"] for s in p["process"]] == ["content", "team", "checkpoint", "content"]
    assert p["process"][0]["text"] == "阶段成果如下。"
    assert p["process"][3]["text"] == "按确认继续交付。"
    ask = p["interactions"]
    assert len(ask) == 1
    assert ask[0]["kind"] == "ask_user"
    assert ask[0]["status"] == "resolved"
    assert not any(s.get("kind") == "plan_review" for s in p["process"])
    assert all(r["checkpoint"] is None for r in p["runs"])


def test_resume_content_reset_reinject_rewrites_after_ask_user(projected):
    """G6：ask_user 续跑后 content_reset 丢掉违规版，再重灌 pre_pause + 重写。"""
    p = projected["resume_content_reset_reinject"]
    assert p["status"] == "completed"
    assert p["content"] == "阶段成果如下。\n\n重写后的交付正文。"
    kinds = [s["kind"] for s in p["process"]]
    assert kinds == ["content", "team", "checkpoint", "content"]
    assert p["process"][0]["text"] == "阶段成果如下。"
    assert p["process"][3]["text"] == "阶段成果如下。\n\n重写后的交付正文。"
    assert p["interactions"][0]["kind"] == "ask_user"
    assert p["interactions"][0]["status"] == "resolved"


def test_single_agent_citations(projected):
    p = projected["single_agent_citations"]
    assert p["status"] == "completed"
    assert [s["kind"] for s in p["process"]] == ["reasoning", "tool", "content"]
    # citations ride verbatim (full dicts + optional id/tier), in order.
    assert [c["url"] for c in p["citations"]] == [
        "https://a.example/x",
        "https://www.bjnews.com.cn/detail/1.html",
    ]
    assert p["citations"][0]["url"] == "https://a.example/x"
    assert p["citations"][0]["id"] == "#r1"
    assert p["citations"][1]["tier"] == "media"


def test_multi_agent_debate_tags(projected):
    p = projected["multi_agent_debate"]
    assert p["status"] == "completed"
    # 进度含主持人节点 + 各方立论 + 质询续写（revision 合成为独立 run，与桌面 projectExecution
    # 同口径）：1 主持人 + 2 辩手立论 + 2 质询作答 = 5/5
    # （CEO 不进图，是主气泡）。新场不跑结辩。
    assert p["progress"] == {"completed": 5, "total": 5}
    mod = next(r for r in p["runs"] if r["id"] == "debate_mod1")
    assert mod["status"] == "completed"
    assert mod["role"] == "主持人"
    pro = next(r for r in p["runs"] if r["id"] == "debate_mod1_r1_pro")
    con = next(r for r in p["runs"] if r["id"] == "debate_mod1_r1_con")
    # stance/group/round 从 plan 透传；辩手 parent = 主持人节点（CEO→主持人→辩手树）。
    assert (pro["stance"], pro["group"], pro["round"]) == ("pro", "debate:debate", 1)
    assert (con["stance"], con["group"], con["round"]) == ("con", "debate:debate", 1)
    assert pro["parentRunId"] == "debate_mod1"


def test_multi_agent_debate_products(projected):
    """debate_result 折成 ProjectedTurn.debate：决策简报 + 交锋叙事线 verbatim，各方→辩手
    run_id 映射回执行图（取发言全文）。"""
    d = projected["multi_agent_debate"]["debate"]
    assert d is not None
    assert d["moderator_run_id"] == "debate_mod1"
    assert d["form"] == "debate"
    assert d["stop_reason"] == "converged"
    assert d["narrative_first"] is False
    # 决策简报（结论卡）。
    assert d["brief"]["leaning"] == "倾向有条件采用"
    assert d["brief"]["strongest_points"]["pro"] == "收益显著且可量化"
    # 交锋叙事线（逐轮焦点 / 裁判 / 小结）+ 各方→辩手 run_id 映射。
    rd = d["rounds"][0]
    assert rd["round_no"] == 1
    assert rd["verdict"]["converged"] is True
    assert rd["sides"][0]["run_id"] == "debate_mod1_r1_pro"


def test_multi_agent_debate_multibeat_channels(projected):
    """多轮对抗 + 每轮质询：钉死 beat 列数与 run_context.channel（角标语义上游）。"""
    p = projected["multi_agent_debate_multibeat"]
    assert p["status"] == "completed"
    # 1 主持人 + 2 首轮陈词 + 2×质询×2 轮 + 2 第2轮陈词 = 9
    assert p["progress"] == {"completed": 9, "total": 9}
    by_id = {r["id"]: r for r in p["runs"]}
    mod = "debate_mb_mod1"

    def _channels(run_id: str) -> list[str]:
        return [b["channel"] for b in by_id[run_id]["receivedContext"]]

    # 续写 beat：首块 task（真实指令）+ 环节通道块（presence / chip）
    assert _channels(f"{mod}_r1_cx_pro")[0] == "task"
    assert "cross_exam" in _channels(f"{mod}_r1_cx_pro")
    assert _channels(f"{mod}_r2_cx_con")[0] == "task"
    assert "cross_exam" in _channels(f"{mod}_r2_cx_con")
    assert _channels(f"{mod}_r2_pro")[0] == "task"
    assert "round_focus" in _channels(f"{mod}_r2_pro")
    assert by_id[f"{mod}_r2_pro"]["round"] == 2
    assert by_id[f"{mod}_r2_cx_pro"]["round"] == 2
    d = p["debate"]
    assert d is not None
    assert len(d["rounds"]) == 2
    assert len(d["rounds"][0]["cross_exam"]) == 2
    assert len(d["rounds"][1]["cross_exam"]) == 2
    assert d["closings"] == []


def test_multi_agent_plan_revised_trace(projected):
    # 「计划已调整」轻痕迹 (设计 §7.2): plan_revised folds each affected node's kind onto its
    # run's `revised` — "bind" (a late-bound node finalised from upstream) / "steer" (a
    # not-yet-run node re-steered). A node the plan never touched stays `revised=None`. The
    # trace NEVER pauses the turn: it completes end_turn with no gate pending.
    p = projected["multi_agent_plan_revised"]
    assert p["status"] == "completed"
    assert _pending_gates(p) == []
    by_id = {r["id"]: r for r in p["runs"]}
    assert by_id["r1"]["revised"] is None
    assert by_id["r2"]["revised"] == "bind"
    assert by_id["r3"]["revised"] == "steer"
    assert p["progress"] == {"completed": 3, "total": 3}


def test_multi_agent_escalation_nonblocking_banner(projected):
    # 非阻塞 run_escalation: folded onto the raising run as a "raised" record (drives the
    # node ⚠️ badge); the worker kept working → COMPLETED. A sibling that never escalated
    # carries an empty list (no badge).
    p = projected["multi_agent_escalation"]
    assert p["status"] == "completed"
    r1 = next(r for r in p["runs"] if r["id"] == "r1")
    r2 = next(r for r in p["runs"] if r["id"] == "r2")
    assert r1["escalations"] == [
        {
            "question": "数据库选 Postgres 还是 MySQL？这关系到后续所有选型。",
            "assumption": "暂按 Postgres 推进",
            "status": "raised",
            "answer": None,
            "kind": "adjust",
        }
    ]
    assert r2["escalations"] == []


def test_multi_agent_blocking_escalate_resolved(projected):
    # 阻塞式求决策 答复路径: escalation_required → pending → escalation_resolved(resolved)
    # flips the run's escalation to resolved + answer. The turn NEVER pauses (non-halting).
    p = projected["multi_agent_blocking_escalate"]
    assert p["status"] == "completed"
    assert _pending_gates(p) == []
    r1 = next(r for r in p["runs"] if r["id"] == "r1")
    assert r1["escalations"] == [
        {
            "question": "数据库选 Postgres 还是 MySQL？这关系到后续所有选型，且猜错基本要整段返工。",
            "assumption": "暂按 Postgres 推进",
            "status": "resolved",
            "answer": "用 Postgres。",
            "kind": "wait",
        }
    ]


def test_multi_agent_blocking_escalate_pending_does_not_pause(projected):
    # THE 核心不变量 (设计 §4.5/§七): a pending blocking escalate keeps the turn RUNNING (not
    # paused) and sets NO gate pending — unlike approval/ask_user/plan_review halting
    # gates. Escalation still appears in interactions[] (non-gate). The parallel sibling r2
    # keeps running, proving the escalation gates only its own worker, never the wave.
    p = projected["multi_agent_blocking_escalate_pending"]
    assert p["status"] == "running"
    assert _pending_gates(p) == []
    assert any(
        i["kind"] == "escalation" and i["status"] == "pending" for i in p["interactions"]
    )
    r1 = next(r for r in p["runs"] if r["id"] == "r1")
    r2 = next(r for r in p["runs"] if r["id"] == "r2")
    assert r1["escalations"][0]["status"] == "pending"
    assert r1["escalations"][0]["answer"] is None
    assert r2["status"] == "running"


def test_projected_turns_omit_team_notes_and_note_wall(projected):
    """便签墙已删：投影不再产出 teamNotes / noteWall（旧 journal 事件跳过）。"""
    for name, p in projected.items():
        assert "teamNotes" not in p, name
        assert "noteWall" not in p, name


def test_process_tool_result_cap_matches_sink():
    """>8KB tool results: sink process timeline and oracle projection must agree.

    Journal persist of ``tool_use_end.result`` now uses the same ``cap_process_result``
    as the process lane (live SSE stays full). Reload folds through ``project_turn``,
    which applies the cap again (idempotent on an already-capped string). Live runtime
    also caps in ``EventSink._accumulate_process`` — the oracle must apply the same
    helper so golden/reload/live stay aligned."""
    from agentcore.conformance.projection import project_turn
    from agentcore.runtime.events import EventSink, tool_use_end, tool_use_start
    from agentcore.runtime.events.journal_config import _PROCESS_RESULT_CAP, cap_process_result

    big = "x" * (_PROCESS_RESULT_CAP + 500)
    expected = cap_process_result(big)
    assert isinstance(expected, str)
    assert len(expected) == _PROCESS_RESULT_CAP + 1  # cap + ellipsis

    sink = EventSink()
    sink.emit(tool_use_start("tc_big", "web_fetch", {"url": "https://example.com"}))
    sink.emit(tool_use_end("tc_big", "web_fetch", success=True, output=big))

    sink_tool = next(s for s in (sink.process_timeline() or []) if s.get("kind") == "tool")
    assert sink_tool["result"] == expected

    # Uncapped wire events (as journaled / reloaded) — oracle must cap on fold.
    events = [
        {
            "type": "tool_use_start",
            "payload": {
                "tool_call_id": "tc_big",
                "tool_name": "web_fetch",
                "arguments": {"url": "https://example.com"},
            },
            "timestamp": "2026-01-01T00:00:00.000Z",
        },
        {
            "type": "tool_use_end",
            "payload": {
                "tool_call_id": "tc_big",
                "tool_name": "web_fetch",
                "status": "success",
                "result": big,
            },
            "timestamp": "2026-01-01T00:00:00.001Z",
        },
    ]
    oracle_tool = next(s for s in project_turn(events)["process"] if s.get("kind") == "tool")
    assert oracle_tool["result"] == expected
    assert oracle_tool["result"] == sink_tool["result"]


def test_multi_agent_mlr_debate_acts():
    """批 A2：幕1 MLR + 幕2 debate 新图+prev；最终投影以幕2 为准。预览目录调研→开辩代表态。"""
    from agentcore.conformance.projection import project_turn
    from agentcore.conformance.vectors.multi_agent.mlr_debate_acts import (
        _multi_agent_mlr_debate_acts,
    )

    events = _multi_agent_mlr_debate_acts()
    p = project_turn(
        [{"type": e.type.value, "payload": e.payload} for e in events]
    )
    assert len(p["acts"]) == 1
    assert p["acts"][0]["actId"] == "act-2"
    assert p["acts"][0]["kind"] == "debate"
    assert p["acts"][0]["anchorRunId"] == "synthesizer"
    assert p["acts"][0]["authorizedBy"] == "auto"
    by_id = {r["id"]: r for r in p["runs"]}
    assert "synthesizer" not in by_id
    assert by_id["debate_mod_act2"]["actId"] == "act-2"
    assert by_id["debate_mod_act2_r1_pro"]["actId"] == "act-2"
    assert by_id["debate_mod_act2_r1_con"]["actId"] == "act-2"
    assert by_id["debate_mod_act2"]["parentRunId"] == "c2"


def _carrier_consult_events(name: str):
    from agentcore.runtime.events.types import EventType

    _description, builder = VECTORS[name]
    return list(builder()), EventType


def test_carrier_means_consult_smartart_boundary(projected):
    """种子 A：能力边界前置 — 诚实做不到图形 SmartArt + ask 含可交替代与「仍要 Word」。"""
    name = "carrier_means_consult_smartart_boundary"
    p = projected[name]
    assert p["status"] == "paused"
    assert p["finishReason"] == "paused"
    assert p["content"] == (
        "Word 里做不出带框连线的图形 SmartArt 组织架构图；"
        "我这边能交的是文本层级 docx、PPT 连线版，或可折叠交互 HTML。"
    )
    assert [s["kind"] for s in p["process"]] == ["content", "checkpoint"]
    assert p["process"][0]["text"] == p["content"]
    assert p["process"][1] == {"kind": "checkpoint", "checkpoint_id": "cp_carrier_smartart"}
    assert p["interactions"] == [
        {
            "kind": "ask_user",
            "id": "cp_carrier_smartart",
            "status": "pending",
            "question": (
                "组织架构图用哪种可交形态？\n"
                "能力边界前置：图形 SmartArt 做不到；推荐更适合的载体，"
                "仍可坚持 Word 文字版。"
            ),
        }
    ]
    assert "SmartArt" in p["interactions"][0]["question"]

    events, event_type = _carrier_consult_events(name)
    deltas = [
        e.payload.get("delta", "")
        for e in events
        if e.type == event_type.CONTENT_DELTA
    ]
    assert any("SmartArt" in d and ("做不出" in d or "做不到" in d) for d in deltas)
    assert not any(d.strip().startswith("可以") for d in deltas)

    cp = next(e for e in events if e.type == event_type.CHECKPOINT_REQUIRED)
    opts = cp.payload["questions"][0]["options"]
    labels = [o["label"] for o in opts]
    assert any("（推荐）" in o.get("label", "") for o in opts)
    assert any("HTML" in label for label in labels)
    assert any("Word" in label and "仍要" in label for label in labels)
    assert not any("SmartArt" in label and "已" in label for label in labels)


def test_mlr_vectors_stamp_authorized_by_auto():
    """幕2 现行只 stamp auto；调研-only 向量没有辩论幕。"""
    from agentcore.conformance.projection import project_turn

    _description, builder = VECTORS["multi_agent_mlr_debate_acts"]
    events = [{"type": e.type.value, "payload": e.payload} for e in builder()]
    p = project_turn(events)
    debate = next(a for a in p["acts"] if a["kind"] == "debate")
    assert debate["authorizedBy"] == "auto"
    _research_desc, research = VECTORS["multi_agent_multi_lens_research"]
    research_p = project_turn(
        [{"type": e.type.value, "payload": e.payload} for e in research()]
    )
    assert not any(a["kind"] == "debate" for a in research_p["acts"])


def test_preview_skip_writes_false_only():
    from agentcore.conformance.vectors import PREVIEW_SKIP

    assert frozenset({"multi_agent_legal_war_room"}) == PREVIEW_SKIP
    assert PREVIEW_SKIP.issubset(VECTORS)
    by_name = {fx["name"]: fx for fx in build_fixtures()}
    for name in PREVIEW_SKIP:
        assert by_name[name]["preview"] is False
    for name, fx in by_name.items():
        if name not in PREVIEW_SKIP:
            assert "preview" not in fx


# Vectors with no hand-verified assertion in any sentinel module. Ratchet: only down.
# Raising it means a new vector shipped judged solely by "both folds agree with the
# golden the oracle wrote" — legal, but it has to be an explicit line in the diff.
_SENTINEL_UNCOVERED_BASELINE = 11


def _sentinel_sources() -> str:
    """Source of every sentinel module (this one + its topic siblings)."""
    here = Path(__file__).parent
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(here.glob("test_conformance_projection*.py"))
    )


def test_sentinel_coverage_ratchet():
    """Keep the hand-verified subset from quietly shrinking as vectors are added.

    A vector is "covered" when its name appears as a quoted literal in a sentinel module
    — the same crude measure an auditor would apply from outside, deliberately, so the
    number can't be inflated by indirection.
    """
    source = _sentinel_sources()
    uncovered = sorted(name for name in VECTORS if f'"{name}"' not in source)
    assert len(uncovered) <= _SENTINEL_UNCOVERED_BASELINE, (
        f"{len(uncovered)} of {len(VECTORS)} vectors have no hand-verified assertion "
        f"(baseline {_SENTINEL_UNCOVERED_BASELINE}). Add one for the new vector, or "
        f"raise the baseline deliberately.\nUncovered: {uncovered}"
    )
    assert len(uncovered) == _SENTINEL_UNCOVERED_BASELINE, (
        f"Coverage improved to {len(uncovered)} uncovered — tighten "
        f"_SENTINEL_UNCOVERED_BASELINE to match (ratchet only goes down)."
    )
