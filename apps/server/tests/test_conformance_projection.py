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


def test_multi_agent_user_interjection_with_mentions(projected):
    """Hand-derived: payload agent_mentions fold to camelCase chips; latest status wins."""
    p = projected["multi_agent_user_interjection_with_mentions"]
    marker = next(s for s in p["process"] if s["kind"] == "user_interjection")
    assert marker == {
        "kind": "user_interjection",
        "interjection_id": "inj-mention",
    }
    assert p["userInterjections"] == [
        {
            "interjectionId": "inj-mention",
            "executionId": "exec1",
            "content": "请让研究员再核一遍成本。",
            "status": "addressed",
            "note": "已在合成草稿中承接",
            "agentMentions": [{"agentId": "agent_research", "role": "研究员"}],
        }
    ]


def test_single_agent_tool_timeline(projected):
    p = projected["single_agent_tool"]
    assert [s["kind"] for s in p["process"]] == ["reasoning", "tool", "content"]
    tool = p["process"][1]
    assert tool["id"] == "tc1"
    assert tool["tool_name"] == "web_search"
    assert tool["status"] == "success"
    assert tool["result"] == "找到 3 条结果。"
    # No display in the vector → the key is omitted (not display=None), so both ends
    # agree by absence.
    assert "display" not in tool
    assert p["content"] == "根据搜索，答案如下。"


def test_reload_cursor_incremental_keeps_prefix_and_swaps_the_open_block(projected):
    """游标增量段：段首无 full_replay → 不清空；replace 帧整块换掉半截正文、不叠字。

    手工推导（不抄 golden）：前半场 live 折出 [reasoning, tool, content("根据搜索，")]。
    增量段段首是同 id 且不带 full_replay，按「同回合重开」处理——三步都留着。随后那帧
    ``content_delta`` 带 replace，且末尾正是开放的正文块，故整块换成整步全文；标量与块
    同步，正文只出现一次。
    """
    p = projected["reload_cursor_incremental"]
    assert p["status"] == "completed"
    # 不清空：游标之前的思考 + 工具行仍在（若段首误带 full_replay，这两步会被清掉）。
    assert [s["kind"] for s in p["process"]] == ["reasoning", "tool", "content"]
    assert p["reasoning"] == "我先搜索。"
    assert p["process"][1]["id"] == "tc1"
    assert p["process"][1]["status"] == "success"
    # 不叠字：既不是「根据搜索，根据搜索，答案如下。」也不是只剩半截。
    assert p["content"] == "根据搜索，答案如下。"
    assert p["process"][2] == {"kind": "content", "text": "根据搜索，答案如下。"}


def test_single_agent_error(projected):
    p = projected["single_agent_error"]
    assert p["status"] == "failed"
    assert p["finishReason"] is None
    assert p["content"] == "开始处理"
    assert p["cost"] is None


def test_single_agent_tool_failure(projected):
    p = projected["single_agent_tool_failure"]
    assert p["status"] == "completed"
    assert [s["kind"] for s in p["process"]] == ["reasoning", "tool", "content"]
    tool = p["process"][1]
    assert tool["id"] == "tc1"
    assert tool["tool_name"] == "web_search"
    assert tool["status"] == "error"
    # Model-facing technical detail stays on result (production leak shape as ratchet).
    assert "Connection refused" in (tool["result"] or "")
    assert "searxng.internal:8080" in (tool["result"] or "")
    # User face is failure.message — not the technical result.
    assert tool["failure"] == {
        "message": "本地搜索服务不可用，请稍后重试",
        "code": "searxng_unreachable",
    }
    assert p["content"] == "检索失败了，我先按已有知识回答。"


def test_single_agent_tool_channel_redirect(projected):
    p = projected["single_agent_tool_channel_redirect"]
    assert p["status"] == "completed"
    kinds = [s["kind"] for s in p["process"]]
    assert kinds == ["reasoning", "tool", "tool", "content"]
    steer = p["process"][1]
    assert steer["tool_name"] == "code_execute"
    assert steer["status"] == "redirect"
    assert steer["failure"]["code"] == "source_grep_redirect"
    assert "禁止用" in (steer["result"] or "")
    grep = p["process"][2]
    assert grep["tool_name"] == "grep"
    assert grep["status"] == "success"


def test_single_agent_cancelled(projected):
    p = projected["single_agent_cancelled"]
    assert p["status"] == "cancelled"
    assert p["finishReason"] == "cancelled"
    assert p["reasoning"] == "先梳理要点。"
    assert p["content"] == "根据目前信息，建议分三步："
    assert p["cost"]["total"] == 360_000


def test_single_agent_tool_progress(projected):
    """tool_use_progress is EPHEMERAL — golden matches successful tool timeline."""
    p = projected["single_agent_tool_progress"]
    baseline = projected["single_agent_tool"]
    assert p["status"] == "completed"
    assert [s["kind"] for s in p["process"]] == ["reasoning", "tool", "content"]
    assert p["process"][1]["status"] == "success"
    assert p["process"] == baseline["process"]
    assert p["content"] == baseline["content"]


def test_single_agent_title_and_turn_saved(projected):
    """turn_saved / title_generated are chrome — same judge state as a plain text turn."""
    p = projected["single_agent_title_and_turn_saved"]
    assert p["status"] == "completed"
    assert p["finishReason"] == "end_turn"
    assert p["content"] == "你好，已收到。"
    assert p["process"] == [{"kind": "content", "text": "你好，已收到。"}]
    assert p["runs"] == []


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


def test_plan_review_paused(projected):
    p = projected["plan_review_paused"]
    assert p["status"] == "paused"
    assert p["interactions"] == [
        {
            "kind": "plan_review",
            "id": "cp1",
            "status": "pending",
            "runIds": ["r1"],
        }
    ]
    assert p["runs"][0]["checkpoint"] == {"status": "pending", "decision": None}
    assert p["progress"] == {"completed": 1, "total": 2}


def test_plan_review_resolved_runs_downstream(projected):
    p = projected["plan_review_resolved_continue"]
    assert p["status"] == "completed"
    assert _pending_gates(p) == []
    assert p["runs"][0]["checkpoint"] == {"status": "resolved", "decision": "continue"}
    assert p["progress"] == {"completed": 2, "total": 2}


def test_execution_completed_gate_still_pending_stays_paused(projected):
    # Conflict: journal has execution_completed(status=completed) AND a still-pending
    # ask_user gate, then message_end(paused). TurnStatus follows finishReason → gate,
    # never the execution frame. Hand-derived from the vector events (not the golden) so
    # a correlated "execution_completed ⇒ completed" bug in oracle + fold cannot pass.
    from agentcore.runtime.events.types import EventType

    _description, builder = VECTORS["execution_completed_gate_still_pending"]
    events = list(builder())
    types = [e.type for e in events]
    assert EventType.EXECUTION_COMPLETED in types
    assert EventType.CHECKPOINT_REQUIRED in types
    assert EventType.MESSAGE_END in types
    done = next(e for e in events if e.type == EventType.EXECUTION_COMPLETED)
    assert done.payload.get("status") == "completed"
    end = next(e for e in events if e.type == EventType.MESSAGE_END)
    assert end.payload.get("finish_reason") == "paused"
    gate = next(e for e in events if e.type == EventType.CHECKPOINT_REQUIRED)
    assert gate.payload.get("checkpoint_id") == "cp-after-exec"
    # Order is the conflict: execution already "completed", then the gate, then paused end.
    assert types.index(EventType.EXECUTION_COMPLETED) < types.index(
        EventType.CHECKPOINT_REQUIRED
    )

    p = projected["execution_completed_gate_still_pending"]
    assert p["status"] == "paused"
    assert p["finishReason"] == "paused"
    assert p["outcome"] is None
    pending = _pending_gates(p)
    assert len(pending) == 1
    assert pending[0]["kind"] == "ask_user"
    assert pending[0]["id"] == "cp-after-exec"
    assert pending[0]["status"] == "pending"
    assert pending[0]["question"] == "按此方案推进吗？\n团队已交付方案。"
    assert p["content"] == "团队已交付，请确认是否按此方案推进。"
    assert [s["kind"] for s in p["process"]] == ["content", "team", "checkpoint"]
    assert p["process"][-1]["checkpoint_id"] == "cp-after-exec"
    assert len(p["runs"]) == 1
    assert p["runs"][0]["id"] == "r1"
    assert p["runs"][0]["status"] == "completed"
    assert p["progress"] == {"completed": 1, "total": 1}


def test_single_agent_checkpoint_finalized_stays_paused(projected):
    # 挂起即收口 (②): a checkpoint that FINALIZES the turn (a trailing message_end with
    # finish_reason=paused) must STAY paused with the SAME resume surface as the parked shape —
    # only finishReason + cost are added. Hand-verified so a correlated oracle+fold
    # "paused→completed" bug (the exact risk this new finish reason introduces, since all three
    # FINISH_TO_STATUS maps default unknown → completed) can't pass the gate by matching itself.
    p = projected["single_agent_checkpoint_finalized"]
    parked = projected["single_agent_checkpoint"]
    assert p["status"] == "paused"
    assert p["finishReason"] == "paused"
    # The terminal message_end bills the pre-pause spend (vs the parked shape's null cost).
    assert p["cost"]["total"] == 360_000
    assert parked["cost"] is None
    # Same single resume surface as the parked checkpoint — timeline + card body byte-identical,
    # so the client renders the one resume card whether the stream parked or finalized.
    assert p["interactions"] == parked["interactions"]
    assert p["process"] == parked["process"]
    assert p["content"] == parked["content"]
    # 模型自写了 question 以外的引导句：气泡必须留下（不再被 checkpoint 无条件吸收）。
    assert parked["content"] == "开始前我确认一下方向："
    assert [s["kind"] for s in parked["process"]] == ["reasoning", "content", "checkpoint"]
    assert parked["process"][1]["text"] == "开始前我确认一下方向："


def test_plan_review_finalized_stays_paused(projected):
    # 挂起即收口 (②) 的 delegate 对偶: a plan_review that FINALIZES the turn stays paused with the
    # gated node's checkpoint badge + progress intact; only finishReason + cost are added vs the
    # parked shape, so the multi-agent graph退回 the same single resume card.
    p = projected["plan_review_finalized"]
    parked = projected["plan_review_paused"]
    assert p["status"] == "paused"
    assert p["finishReason"] == "paused"
    assert p["cost"]["total"] == 360_000
    assert p["interactions"] == [
        {
            "kind": "plan_review",
            "id": "cp1",
            "status": "pending",
            "runIds": ["r1"],
        }
    ]
    assert p["runs"][0]["checkpoint"] == {"status": "pending", "decision": None}
    assert p["progress"] == {"completed": 1, "total": 2}
    # Same resume surface as the parked plan_review — only the terminal frame differs.
    assert p["interactions"] == parked["interactions"]
    assert p["runs"] == parked["runs"]


def test_team_preview_finalized(projected):
    p = projected["team_preview_finalized"]
    assert p["status"] == "paused"
    assert p["finishReason"] == "paused"
    assert not any(i["kind"] == "team_preview" for i in p["interactions"])
    # 有 run_plan：过程时间线 content + team；开工卡事件对已退役，不再投影。
    assert [s["kind"] for s in p["process"]] == ["content", "team"]


def test_team_preview_resolved_continue(projected):
    p = projected["team_preview_resolved_continue"]
    assert p["status"] == "completed"
    assert _pending_gates(p) == []
    assert not any(i["kind"] == "team_preview" for i in p["interactions"])
    assert p["progress"]["completed"] == 2
    assert [s["kind"] for s in p["process"]] == ["content", "team", "content"]


def test_team_preview_resolved_adjust(projected):
    """adjust 路径：无开工卡、无 worker 开跑、意见在 tool_use_end 回灌。"""
    from agentcore.conformance.vectors import VECTORS
    from agentcore.runtime.events.types import EventType

    p = projected["team_preview_resolved_adjust"]
    assert p["status"] == "completed"
    assert _pending_gates(p) == []
    assert not any(i["kind"] == "team_preview" for i in p["interactions"])
    assert p["progress"]["completed"] == 0
    assert all(r["status"] != "running" for r in p["runs"])
    assert all(r["status"] != "completed" for r in p["runs"])

    _description, builder = VECTORS["team_preview_resolved_adjust"]
    events = builder()
    assert not any(e.type == EventType.RUN_STARTED for e in events)
    ended = next(e for e in events if e.type == EventType.TOOL_USE_END)
    assert "宜先问" not in (ended.payload.get("result") or "")
    assert "重新调用 delegate" in (ended.payload.get("result") or "")
    assert "人太多" in (ended.payload.get("result") or "")


def test_team_preview_resolved_adjust_pre_ttft(projected):
    """adjust 后 CEO 已续跑、尚未吐首 token：挂起后续跑、气泡无新正文。

    手工推导（不抄 golden）：生产冷恢复在 captain ``run_started`` 之后、上游 TTFT
    之前有一段无 delta 窗口。折完应是 running、无开工卡、未跑 worker 为
    skipped、captain 为 running、正文仍是挂起前那句、过程时间线不再长出新
    content/reasoning。
    """
    from agentcore.conformance.vectors import VECTORS
    from agentcore.runtime.events.types import EventType

    p = projected["team_preview_resolved_adjust_pre_ttft"]
    assert p["status"] == "running"
    assert p["finishReason"] is None
    assert p["outcome"] is None
    assert _pending_gates(p) == []
    assert p["content"] == "我来安排团队。"
    assert p["reasoning"] == ""
    assert [s["kind"] for s in p["process"]] == ["content", "team"]
    assert p["process"][0]["text"] == "我来安排团队。"
    assert not any(i["kind"] == "team_preview" for i in p["interactions"])

    by_id = {r["id"]: r for r in p["runs"]}
    assert by_id["c1"]["status"] == "running"
    assert by_id["c1"]["kind"] == "captain"
    assert by_id["r1"]["status"] == "skipped"
    assert by_id["r2"]["status"] == "skipped"
    assert p["progress"] == {"completed": 0, "total": 3}

    _description, builder = VECTORS["team_preview_resolved_adjust_pre_ttft"]
    events = builder()
    assert events[-1].type == EventType.RUN_STARTED
    assert events[-1].payload["kind"] == "captain"
    assert events[-1].payload["run_id"] == "c1"
    assert events[-1].payload["agent_id"] == "c1"

    starts = [e for e in events if e.type == EventType.MESSAGE_START]
    assert len(starts) == 2
    assert {e.payload["message_id"] for e in starts} == {"m1"}
    assert all("full_replay" not in e.payload for e in starts)

    last_end = max(i for i, e in enumerate(events) if e.type == EventType.TOOL_USE_END)
    after_end = events[last_end + 1 :]
    assert [e.type for e in after_end] == [EventType.RUN_STARTED]
    assert not any(
        e.type
        in (EventType.CONTENT_DELTA, EventType.REASONING_DELTA, EventType.TOOL_USE_START)
        for e in after_end
    )

    skipped = [e for e in events if e.type == EventType.RUN_SKIPPED]
    assert len(skipped) == 2
    assert all(e.payload["reason"] == "abort" for e in skipped)
    ended = next(e for e in events if e.type == EventType.TOOL_USE_END)
    assert "宜先问" not in (ended.payload.get("result") or "")
    assert "重新调用 delegate" in (ended.payload.get("result") or "")
    assert "人太多" in (ended.payload.get("result") or "")


def test_team_preview_revised_card(projected):
    """修订后再挂起：无开工卡；不再从 required 事件读谱系。"""
    from agentcore.conformance.vectors import VECTORS
    from agentcore.runtime.events.types import EventType

    p = projected["team_preview_revised_card"]
    assert p["status"] == "paused"
    assert p["finishReason"] == "paused"
    assert _pending_gates(p) == []
    assert not any(i["kind"] == "team_preview" for i in p["interactions"])

    _description, builder = VECTORS["team_preview_revised_card"]
    events = builder()
    assert not any(e.type == EventType.RUN_STARTED for e in events)


def test_team_preview_exclude_one_continue(projected):
    p = projected["team_preview_exclude_one_continue"]
    assert p["status"] == "completed"
    assert _pending_gates(p) == []
    assert not any(i["kind"] == "team_preview" for i in p["interactions"])
    assert not any("excludedRunIds" in i for i in p["interactions"])
    assert p["progress"]["completed"] == 1


def test_team_preview_tighten_write_continue(projected):
    p = projected["team_preview_tighten_write_continue"]
    assert p["status"] == "completed"
    assert _pending_gates(p) == []
    assert not any(i["kind"] == "team_preview" for i in p["interactions"])
    assert not any("writeCapabilityOverrides" in i for i in p["interactions"])
    assert p["progress"]["completed"] == 2


def test_team_preview_model_override_continue(projected):
    p = projected["team_preview_model_override_continue"]
    assert p["status"] == "completed"
    assert _pending_gates(p) == []
    assert not any(i["kind"] == "team_preview" for i in p["interactions"])
    assert not any("modelOverrides" in i for i in p["interactions"])
    r2 = next(r for r in p["runs"] if r["id"] == "r2")
    assert r2["model"] == "deepseek-v4-pro"


def test_debate_team_preview_resolved_continue(projected):
    p = projected["debate_team_preview_resolved_continue"]
    assert p["status"] == "running"
    assert _pending_gates(p) == []
    assert not any(i["kind"] == "team_preview" for i in p["interactions"])
    assert len(p["runs"]) >= 1
    assert "team" in [s["kind"] for s in p["process"]]


def test_debate_team_preview_research_first(projected):
    """棘轮：research_first 回灌仍不开赛 — 无辩手 runs；无开工卡。"""
    p = projected["debate_team_preview_research_first"]
    assert p["status"] == "completed"
    assert _pending_gates(p) == []
    assert not any(i["kind"] == "team_preview" for i in p["interactions"])
    assert p["runs"] == []
    assert p.get("debate") is None
    assert p.get("debateRounds") == []
    assert "team" not in [s["kind"] for s in p["process"]]


def test_debate_team_preview_resolved_adjust(projected):
    """辩论 adjust：无开工卡、不开赛、无辩手 runs、意见回灌。"""
    from agentcore.conformance.vectors import VECTORS
    from agentcore.runtime.events.types import EventType

    p = projected["debate_team_preview_resolved_adjust"]
    assert p["status"] == "completed"
    assert _pending_gates(p) == []
    assert not any(i["kind"] == "team_preview" for i in p["interactions"])
    assert p["runs"] == []
    assert p.get("debate") is None
    assert p.get("debateRounds") == []
    assert "team" not in [s["kind"] for s in p["process"]]

    _description, builder = VECTORS["debate_team_preview_resolved_adjust"]
    events = builder()
    assert not any(e.type == EventType.RUN_STARTED for e in events)
    ended = next(e for e in events if e.type == EventType.TOOL_USE_END)
    assert "宜先问" not in (ended.payload.get("result") or "")
    assert "重新调用 debate" in (ended.payload.get("result") or "")
    assert "先改命题" in (ended.payload.get("result") or "")


def test_debate_pretrial_fast_projection(projected):
    """庭前 fast：skipped + skipReason=fast，无取证员舰队。"""
    p = projected["multi_agent_debate_pretrial_fast"]
    pt = p.get("debatePretrial")
    assert pt is not None
    assert pt["status"] == "skipped"
    assert pt["skipReason"] == "fast"
    assert pt["completeness"] == "empty"
    assert pt["incomplete"] is False
    assert not any("_inv_" in r["id"] for r in p["runs"])


def test_debate_pretrial_no_pack_projection(projected):
    """thorough 无 pack：skipped + no_pack，无舰队，进入立论。"""
    p = projected["multi_agent_debate_pretrial_no_pack"]
    pt = p.get("debatePretrial")
    assert pt is not None
    assert pt["status"] == "skipped"
    assert pt["skipReason"] == "no_pack"
    assert pt["completeness"] == "empty"
    assert pt["incomplete"] is False
    assert pt["externalEvidenceMode"] == "skip"
    assert pt["externalEvidenceReason"] == "no_pack"
    assert not any("_inv_" in r["id"] for r in p["runs"])
    assert any(r["id"].endswith("_r1_pro") for r in p["runs"])


def test_debate_pretrial_evidence_pack_full_projection(projected):
    """Evidence Pack 齐全：skip 外证、budget=0、completeness=full。"""
    p = projected["multi_agent_debate_pretrial_evidence_pack_full"]
    pt = p.get("debatePretrial")
    assert pt is not None
    assert pt["status"] == "skipped"
    assert pt["skipReason"] == "evidence_pack"
    assert pt["completeness"] == "full"
    assert pt["incomplete"] is False
    assert pt["externalEvidenceMode"] == "skip"
    assert pt["externalEvidenceReason"] == "evidence_pack_full"
    assert pt["evidenceReady"] is True
    assert not any("_inv_" in r["id"] for r in p["runs"])


def test_debate_pretrial_evidence_pack_partial_projection(projected):
    """Evidence Pack 截断：skip 外证舰队；completeness=partial。"""
    p = projected["multi_agent_debate_pretrial_evidence_pack_partial"]
    pt = p.get("debatePretrial")
    assert pt is not None
    assert pt["status"] == "skipped"
    assert pt["skipReason"] == "evidence_pack"
    assert pt["completeness"] == "partial"
    assert pt["incomplete"] is False
    assert pt["externalEvidenceMode"] == "skip"
    assert pt["externalEvidenceReason"] == "evidence_pack_partial"
    assert pt["evidenceReady"] is True
    assert not any("_inv_" in r["id"] for r in p["runs"])
    assert any(r["id"].endswith("_r1_pro") for r in p["runs"])


def test_debate_team_preview_research_first_recommended(projected):
    """棘轮：不再点亮 recommended 主键；paused 收口、无开工卡。"""
    p = projected["debate_team_preview_research_first_recommended"]
    assert p["status"] == "paused"
    assert _pending_gates(p) == []
    assert not any(i["kind"] == "team_preview" for i in p["interactions"])
    assert p["runs"] == []
    assert p.get("debate") is None
    assert "team" not in [s["kind"] for s in p["process"]]


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


def test_multi_agent_worker_tool(projected):
    p = projected["multi_agent_worker_tool"]
    # No message_end → still running; w2 frozen mid-compose so its toolProgress shows.
    assert p["status"] == "running"
    assert p["finishReason"] is None
    # 统一团队时间线 (worker-tool 归属修): a delegated worker's tool_use carries run_id, so the
    # process folds keep it OUT of the captain bubble — `process` is the CEO's own intro
    # content plus the `team` marker (dropped at run_plan) fixing the graph's slot. The
    # worker's tool rides the team graph (toolProgress, asserted below), never the CEO timeline.
    assert p["process"] == [
        {"kind": "content", "text": "我来分工。"},
        {"kind": "team", "execution_id": "exec1"},
    ]
    w1 = next(a for a in p["agents"] if a["id"] == "w1")
    w2 = next(a for a in p["agents"] if a["id"] == "w2")
    assert w1["status"] == "completed"
    assert w1["toolProgress"] is None  # cleared by run_completed
    assert w2["status"] == "working"
    assert w2["toolProgress"] == {"toolName": "code_execute", "chars": 64}
    assert p["progress"] == {"completed": 1, "total": 2}


def test_multi_agent_debate_tags(projected):
    p = projected["multi_agent_debate"]
    assert p["status"] == "completed"
    # 进度含主持人节点 + 各方立论 + 各 beat 的续写节点（revision 合成为独立 run，与桌面 projectExecution
    # 同口径）：1 主持人 + 2 辩手立论 + 2 质询作答（P1 revision）+ 2 结辩（P4·结辩收束 revision）= 7/7
    # （CEO 不进图，是主气泡）。
    assert p["progress"] == {"completed": 7, "total": 7}
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
    """多轮对抗 + 每轮质询 + 结辩：钉死 beat 列数与 run_context.channel（角标语义上游）。"""
    p = projected["multi_agent_debate_multibeat"]
    assert p["status"] == "completed"
    # 1 主持人 + 2 首轮陈词 + 2×质询×2 轮 + 2 第2轮陈词 + 2 结辩 = 11
    assert p["progress"] == {"completed": 11, "total": 11}
    by_id = {r["id"]: r for r in p["runs"]}
    mod = "debate_mb_mod1"

    def _channels(run_id: str) -> list[str]:
        return [b["channel"] for b in by_id[run_id]["receivedContext"]]

    # 续写 beat：首块 task（真实指令）+ 环节通道块（presence / chip）
    assert _channels(f"{mod}_r1_cx_pro")[0] == "task"
    assert "cross_exam" in _channels(f"{mod}_r1_cx_pro")
    assert _channels(f"{mod}_r2_cx_con")[0] == "task"
    assert "cross_exam" in _channels(f"{mod}_r2_cx_con")
    assert _channels(f"{mod}_closing_pro")[0] == "task"
    assert "closing" in _channels(f"{mod}_closing_pro")
    assert _channels(f"{mod}_r2_pro")[0] == "task"
    assert "round_focus" in _channels(f"{mod}_r2_pro")
    assert by_id[f"{mod}_r2_pro"]["round"] == 2
    assert by_id[f"{mod}_r2_cx_pro"]["round"] == 2
    assert by_id[f"{mod}_closing_con"]["round"] == 2
    d = p["debate"]
    assert d is not None
    assert len(d["rounds"]) == 2
    assert len(d["rounds"][0]["cross_exam"]) == 2
    assert len(d["rounds"][1]["cross_exam"]) == 2
    assert len(d["closings"]) == 2


def test_multi_agent_revision_synthesizes_node(projected):
    p = projected["multi_agent_revision"]
    assert p["status"] == "completed"
    # A continuation is born from its run_started frame (not the plan): a new agent cloned
    # from the original's identity + a 续派 node with continuesRunId = session root.
    assert [a["id"] for a in p["agents"]] == ["w1", "w1b"]
    w1b = next(a for a in p["agents"] if a["id"] == "w1b")
    assert w1b["role"] == "撰写员"  # inherited from the original agent
    assert w1b["output"] == "修订稿"
    rev = next(r for r in p["runs"] if r["id"] == "r1v2")
    assert rev["continuesRunId"] == "r1"
    assert rev["parentRunId"] is None
    assert rev["task"] == "起草"  # inherited from the original run
    assert p["progress"] == {"completed": 2, "total": 2}


def test_multi_agent_redelegate_continuation_in_plan(projected):
    p = projected["multi_agent_redelegate_continuation"]
    assert p["status"] == "completed"
    by_id = {r["id"]: r for r in p["runs"]}
    assert by_id["r2"]["continuesRunId"] == "r1"
    assert by_id["r2"]["parentRunId"] == "cap"
    assert by_id["r1"]["continuesRunId"] is None
    assert "continuation" in {b["channel"] for b in by_id["r2"]["receivedContext"]}


def test_multi_agent_multi_batch_merges(projected):
    p = projected["multi_agent_multi_batch"]
    assert p["status"] == "completed"
    # Second delegate batch (same execution_id) merges into the live graph; progress is
    # cumulative across both batches (derived from run states, not run_progress).
    assert [a["id"] for a in p["agents"]] == ["w1", "w2"]
    assert [r["id"] for r in p["runs"]] == ["r1", "r2"]
    assert all(r["status"] == "completed" for r in p["runs"])
    assert p["progress"] == {"completed": 2, "total": 2}
    assert p["content"] == "先调研。 再撰写。"


def test_multi_agent_multi_batch_disjoint_merges_without_cross_deps(projected):
    """同回合两批 delegate、跨批无 depends_on：fold 仍合并进同一 execution，不伪造依赖。"""
    p = projected["multi_agent_multi_batch_disjoint"]
    assert p["status"] == "completed"
    assert [a["id"] for a in p["agents"]] == ["w1", "w2", "w3", "w4"]
    assert [r["id"] for r in p["runs"]] == ["r1", "r2", "r3", "r4"]
    by_id = {r["id"]: r for r in p["runs"]}
    assert by_id["r1"]["dependsOn"] == []
    assert by_id["r2"]["dependsOn"] == ["r1"]
    assert by_id["r3"]["dependsOn"] == []
    assert by_id["r4"]["dependsOn"] == ["r3"]
    assert all(r["status"] == "completed" for r in p["runs"])
    assert p["progress"] == {"completed": 4, "total": 4}


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


def test_multi_agent_lead_subplan_bind_replan_nests_and_traces(projected):
    # 受监督子计划 B (docs/03-AI核心/编排器与CEO主Agent.md §2.4): a LEAD's sub-plan shares the parent
    # execution_id, so the two run_plans MERGE into one team graph linked by parentRunId (NOT a
    # reset) — sa/sb hang under the lead L1. The lead's OWN replan finalises the late-bound sb
    # (revised="bind") without pausing the turn; one `team` marker despite two run_plans.
    p = projected["multi_agent_lead_subplan_bind_replan"]
    assert p["status"] == "completed"
    assert _pending_gates(p) == []
    assert [s["execution_id"] for s in p["process"] if s["kind"] == "team"] == ["exec1"]
    by_id = {r["id"]: r for r in p["runs"]}
    assert by_id["L1"]["parentRunId"] is None  # the lead is a top-level worker (CEO is the bubble)
    assert by_id["sa"]["parentRunId"] == "L1"  # sub-team nests under the lead — graph NOT reset
    assert by_id["sb"]["parentRunId"] == "L1"
    assert by_id["sb"]["revised"] == "bind"  # the lead's own late-bind finalise is visible
    assert by_id["sa"]["revised"] is None
    assert by_id["L1"]["revised"] is None
    assert p["progress"] == {"completed": 3, "total": 3}


def test_multi_agent_lead_subplan_scope_steer_nests_and_traces(projected):
    # 受监督子计划 B 自底向上 (SCOPE 臂): a sub-worker (sa) reports a scope deviation
    # (run_escalation, non-blocking → node ⚠️ badge, turn not paused); the lead catches the
    # SCOPE boundary and its OWN replan re-steers the un-run downstream sb (revised="steer").
    # Same shared-execution_id nesting (sa/sb under L1) as the bind arm.
    p = projected["multi_agent_lead_subplan_scope_steer"]
    assert p["status"] == "completed"
    assert _pending_gates(p) == []
    by_id = {r["id"]: r for r in p["runs"]}
    assert by_id["sa"]["parentRunId"] == "L1"
    assert by_id["sb"]["parentRunId"] == "L1"
    assert by_id["sb"]["revised"] == "steer"
    assert by_id["sb"]["escalations"] == []
    assert by_id["sa"]["escalations"] == [
        {
            "question": "真正要做的是 X 而非初始子计划的 Y，下游写法应随之调整。",
            "assumption": "暂按 X 推进",
            "blocking": False,
            "status": "raised",
            "answer": None,
            "kind": "scope",
        }
    ]
    assert p["progress"] == {"completed": 3, "total": 3}


def test_multi_agent_lead_peer_mixed_overlap_folds_without_reject(projected):
    # 嵌套 lead + 平级同名角色混合（反模式）：引擎不拒单；同 execution_id 合并图，
    # lead 子节点挂 L1，平级挂根；进度 4/4。
    p = projected["multi_agent_lead_peer_mixed_overlap"]
    assert p["status"] == "completed"
    assert _pending_gates(p) == []
    by_id = {r["id"]: r for r in p["runs"]}
    assert by_id["L1"]["parentRunId"] is None
    assert by_id["w_fe"]["parentRunId"] is None
    assert by_id["w_be"]["parentRunId"] is None
    assert by_id["sa"]["parentRunId"] == "L1"
    assert p["progress"] == {"completed": 4, "total": 4}


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
            "blocking": True,
            "status": "raised",
            "answer": None,
            "kind": "normal",
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
            "blocking": True,
            "status": "resolved",
            "answer": "用 Postgres。",
            "kind": "normal",
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


def test_multi_agent_blocking_escalate_timeout_falls_back(projected):
    # Wall-clock miss: escalation_resolved(timed_out) flips to timed_out (answer None).
    p = projected["multi_agent_blocking_escalate_timeout"]
    assert p["status"] == "completed"
    assert _pending_gates(p) == []
    r1 = next(r for r in p["runs"] if r["id"] == "r1")
    assert r1["escalations"][0]["status"] == "timed_out"
    assert r1["escalations"][0]["answer"] is None


def test_multi_agent_blocking_escalate_multi_settles_each(projected):
    # 多升级: one run raises two sequential blocking escalates — the first answered, the
    # second timed out. Each settles independently in fire order (the "find first pending"
    # fold is order-correct: when esc2 resolves, esc1 is already resolved so it targets esc2).
    p = projected["multi_agent_blocking_escalate_multi"]
    assert p["status"] == "completed"
    assert _pending_gates(p) == []
    r1 = next(r for r in p["runs"] if r["id"] == "r1")
    assert [(e["status"], e["answer"]) for e in r1["escalations"]] == [
        ("resolved", "用 Postgres。"),
        ("timed_out", None),
    ]


def test_multi_agent_ceo_arbitrate_escalate_direct(projected):
    p = projected["multi_agent_ceo_arbitrate_escalate"]
    assert p["status"] == "completed"
    assert _pending_gates(p) == []
    r1 = next(r for r in p["runs"] if r["id"] == "r1")
    assert len(r1["escalations"]) == 1
    esc = r1["escalations"][0]
    assert esc["status"] == "resolved"
    assert esc["awaiting"] == "ceo"
    assert esc["arbitrated_by"] == "ceo"
    assert esc["via_user"] is False
    assert esc["answer"] == "用 Postgres。"


def test_multi_agent_ceo_arbitrate_escalate_via_user(projected):
    p = projected["multi_agent_ceo_arbitrate_escalate_via_user"]
    r1 = next(r for r in p["runs"] if r["id"] == "r1")
    esc = r1["escalations"][0]
    assert esc["status"] == "resolved"
    assert esc["arbitrated_by"] == "ceo"
    assert esc["via_user"] is True
    assert "用户确认" in esc["answer"]


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
    sink.emit(tool_use_start("tc_big", "read_url", {"url": "https://example.com"}))
    sink.emit(tool_use_end("tc_big", "read_url", success=True, output=big))

    sink_tool = next(s for s in (sink.process_timeline() or []) if s.get("kind") == "tool")
    assert sink_tool["result"] == expected

    # Uncapped wire events (as journaled / reloaded) — oracle must cap on fold.
    events = [
        {
            "type": "tool_use_start",
            "payload": {
                "tool_call_id": "tc_big",
                "tool_name": "read_url",
                "arguments": {"url": "https://example.com"},
            },
            "timestamp": "2026-01-01T00:00:00.000Z",
        },
        {
            "type": "tool_use_end",
            "payload": {
                "tool_call_id": "tc_big",
                "tool_name": "read_url",
                "status": "success",
                "result": big,
            },
            "timestamp": "2026-01-01T00:00:00.001Z",
        },
    ]
    oracle_tool = next(s for s in project_turn(events)["process"] if s.get("kind") == "tool")
    assert oracle_tool["result"] == expected
    assert oracle_tool["result"] == sink_tool["result"]


def test_resume_content_continuity(projected):
    """挂起前 content 经 plan_review resume 后与续跑 content 续拼（reload == live）。"""
    p = projected["resume_content_continuity"]
    assert p["status"] == "completed"
    assert p["finishReason"] == "end_turn"
    assert p["content"] == "阶段成果如下。按复核结论继续交付。"
    assert _pending_gates(p) == []
    assert p["interactions"] == [
        {
            "kind": "plan_review",
            "id": "cp1",
            "status": "resolved",
            "runIds": ["r1"],
        }
    ]
    assert [s["kind"] for s in p["process"]] == [
        "content",
        "team",
        "plan_review",
        "content",
    ]
    assert p["process"][0]["text"] == "阶段成果如下。"
    assert p["process"][-1]["text"] == "按复核结论继续交付。"
    assert p["runs"][0]["checkpoint"] == {"status": "resolved", "decision": "continue"}


def test_multi_agent_mlr_debate_acts(projected):
    """批 A2：幕1 MLR + 幕2 debate 新图+prev；最终投影以幕2 为准。"""
    p = projected["multi_agent_mlr_debate_acts"]
    assert len(p["acts"]) == 1
    assert p["acts"][0]["actId"] == "act-2"
    assert p["acts"][0]["kind"] == "debate"
    assert p["acts"][0]["anchorRunId"] == "synthesizer"
    by_id = {r["id"]: r for r in p["runs"]}
    assert "synthesizer" not in by_id
    assert by_id["debate_mod_act2"]["actId"] == "act-2"
    assert by_id["debate_mod_act2_r1_pro"]["actId"] == "act-2"
    assert by_id["debate_mod_act2_r1_con"]["actId"] == "act-2"
    assert by_id["debate_mod_act2"]["parentRunId"] == "c2"


def test_single_agent_content_reset_finish_guard_clears_body_only(projected):
    """finish_guard 结构回炉：弃稿弹掉尾部 content 步，不折过程痕迹（与 retry 同形）。"""
    p = projected["single_agent_content_reset"]
    assert p["content"] == "依据 [1] 可知……"
    assert [s["kind"] for s in p["process"]] == ["reasoning", "tool", "content"]
    assert p["process"][-1]["text"] == "依据 [1] 可知……"


def test_single_agent_retry_reset_leaves_no_trace(projected):
    """reason=retry（LLM 流式透明重试）：清正文照旧，不折过程痕迹。"""
    p = projected["single_agent_retry_reset"]
    assert p["content"] == "答案：42。"
    assert p["process"] == [
        {"kind": "reasoning", "text": "直接作答。"},
        {"kind": "content", "text": "答案：42。"},
    ]


def test_worker_deliverable_reset_narration_leaves_no_trace(projected):
    """worker 旁白回滚（reason=narration）：清卡片草稿照旧，节点时间线无核验痕迹。"""
    p = projected["multi_agent_worker_deliverable_reset"]
    run = p["runs"][0]
    assert [s["kind"] for s in run["process"]] == [
        "reasoning",
        "content",
        "tool",
        "content",
    ]


def test_worker_output_reset_finish_guard_clears_body_only(projected):
    """worker finish_guard 结构回炉：节点时间线只清草稿、不折过程痕迹。"""
    p = projected["multi_agent_worker_output_reset"]
    run = p["runs"][0]
    assert [s["kind"] for s in run["process"]] == [
        "reasoning",
        "content",
    ]
    assert run["process"][-1]["text"] == '修正后的产出：{"status":"ok"}'


def test_resume_content_reset_reinject(projected):
    """G6：content_reset 清标量后重灌 pre_pause delta，再叠重写正文。"""
    p = projected["resume_content_reset_reinject"]
    assert p["status"] == "completed"
    assert p["finishReason"] == "end_turn"
    assert p["content"] == "阶段成果如下。\n\n重写后的交付正文。"
    assert _pending_gates(p) == []
    assert p["interactions"][0]["kind"] == "plan_review"
    assert p["interactions"][0]["status"] == "resolved"
    kinds = [s["kind"] for s in p["process"]]
    assert kinds == ["content", "team", "plan_review", "content"]
    # Trailing content after reset is reinject ⊕ rewrite (ordinary deltas).
    assert p["process"][-1] == {
        "kind": "content",
        "text": "阶段成果如下。\n\n重写后的交付正文。",
    }


def test_resume_ask_user_absorb(projected):
    """ask_user 吸收：气泡基底为空，问句在卡片；续跑只叠 post-resume 正文。"""
    p = projected["resume_ask_user_absorb"]
    assert p["status"] == "completed"
    assert p["finishReason"] == "end_turn"
    assert p["content"] == "收到，继续推进交付。"
    assert _pending_gates(p) == []
    assert p["interactions"] == [
        {
            "kind": "ask_user",
            "id": "cp_absorb",
            "status": "resolved",
            "question": "帮你分析一下选项：\n请确认后继续。",
        }
    ]
    assert [s["kind"] for s in p["process"]] == ["checkpoint", "content"]
    assert p["process"][-1]["text"] == "收到，继续推进交付。"


def _carrier_consult_events(name: str):
    from agentcore.conformance.vectors import VECTORS
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


def test_carrier_means_consult_html_org_tree(projected):
    """种子 B：次优载体短对齐 — 静态 1:1 难看全 + ask 推荐折叠/分区并保留原样 HTML。"""
    name = "carrier_means_consult_html_org_tree"
    p = projected[name]
    assert p["status"] == "paused"
    assert p["finishReason"] == "paused"
    assert p["content"] == (
        "这棵组织树很宽，静态 HTML 1:1 照搬几乎看不全；"
        "更适合可折叠树或按部门分区，也可仍按原样 HTML。"
    )
    assert [s["kind"] for s in p["process"]] == ["content", "checkpoint"]
    assert p["process"][0]["text"] == p["content"]
    assert p["process"][1] == {"kind": "checkpoint", "checkpoint_id": "cp_carrier_html_tree"}
    assert p["interactions"] == [
        {
            "kind": "ask_user",
            "id": "cp_carrier_html_tree",
            "status": "pending",
            "question": (
                "组织树 HTML 用哪种呈现？\n"
                "次优载体短对齐：框架可保，呈现建议改；坚持原样静态 HTML 亦可。"
            ),
        }
    ]

    events, event_type = _carrier_consult_events(name)
    deltas = [
        e.payload.get("delta", "")
        for e in events
        if e.type == event_type.CONTENT_DELTA
    ]
    assert any("1:1" in d and ("看不全" in d or "难看" in d) for d in deltas)
    # 非盲跟：首轮即挂起 ask，无 delegate / 假交付落盘
    assert not any(e.type == event_type.RUN_PLAN for e in events)
    assert not any(
        e.type == event_type.TOOL_USE_START and e.payload.get("tool_name") == "delegate"
        for e in events
    )

    cp = next(e for e in events if e.type == event_type.CHECKPOINT_REQUIRED)
    opts = cp.payload["questions"][0]["options"]
    labels = [o["label"] for o in opts]
    assert any("（推荐）" in o.get("label", "") for o in opts)
    assert any("折叠" in label for label in labels)
    assert any("原样" in label and "HTML" in label for label in labels)


def test_multi_agent_export_docx_artifacts(projected):
    """导出件主清单：docx 只从自报产物来，工具入参里根本没有它。

    手工推导（不抄 golden）：这一路的真相源是 ``delivery_status.artifacts``——两个工具调用
    的 ``arguments`` 都只提到源 md（``file_write`` 写它、``md_to_docx`` 读它），所以任何按
    工具参数合成的清单都只能给出 1 项，正是线上事故。断言分三段：① worker 时间线确实只有
    这两步、参数里不出现 .docx；② artifacts 仍有两行且导出件在列（计数 2）；③ 导出件带
    ``derived_from`` 指回源 md（客户端折中间稿的唯一依据），源 md 自己不带。
    """
    p = projected["multi_agent_export_docx_artifacts"]
    assert p["status"] == "completed"
    md = "抚养费起诉状-昝雯.md"
    docx = "抚养费起诉状-昝雯.docx"

    run = next(r for r in p["runs"] if r["id"] == "r1")
    tools = [s for s in run["process"] if s["kind"] == "tool"]
    assert [t["tool_name"] for t in tools] == ["file_write", "md_to_docx"]
    assert all(docx not in str(t["arguments"]) for t in tools)

    ds = p["deliveryStatus"]
    assert ds is not None
    assert ds["state"] == "delivered"
    assert [(a["path"], a["status"]) for a in ds["artifacts"]] == [
        (md, "accepted"),
        (docx, "accepted"),
    ]
    assert ds["delivered_files"] == [md, docx]

    by_path = {a["path"]: a for a in ds["artifacts"]}
    assert by_path[docx]["kind"] == "docx"
    assert by_path[docx]["derived_from"] == md
    assert by_path[md]["kind"] == "md"
    assert "derived_from" not in by_path[md]


def test_multi_agent_cross_turn_live_prev_new_graph_excludes_old_runs(projected):
    """上一轮后台仍在跑时新回合再派人：新人进新图、不进旧图（从向量事件手推，不抄 golden）。"""
    from agentcore.runtime.events.types import EventType

    _description, builder = VECTORS["multi_agent_cross_turn_live_prev"]
    events = list(builder())
    plans = [e for e in events if e.type == EventType.RUN_PLAN]
    assert len(plans) == 2
    exec1 = plans[0].payload["execution_id"]
    exec2 = plans[1].payload["execution_id"]
    assert exec1 != exec2
    assert plans[1].payload.get("prev_execution_id") == exec1
    old_ids = {str(r.get("id")) for r in plans[0].payload.get("runs") or []}
    new_ids = {str(r.get("id")) for r in plans[1].payload.get("runs") or []}
    assert "r1" in old_ids
    assert "r1" not in new_ids
    assert not any(e.type == EventType.GRAPH_APPEND for e in events)
    assert any(
        e.type == EventType.RUN_STARTED and e.payload.get("run_id") == "r1"
        for e in events
    )
    assert not any(
        e.type == EventType.RUN_COMPLETED and e.payload.get("run_id") == "r1"
        for e in events
    )
    assert any(e.type == EventType.EXECUTION_DETACHED for e in events)

    p = projected["multi_agent_cross_turn_live_prev"]
    proj_ids = {r["id"] for r in p["runs"]}
    assert "r1" not in proj_ids
    assert proj_ids == new_ids
    assert p["progress"]["total"] == len(new_ids)
    completed_ids = {
        e.payload.get("run_id")
        for e in events
        if e.type == EventType.RUN_COMPLETED
    }
    assert p["progress"]["completed"] == len(completed_ids & proj_ids)
    team_eids = [s["execution_id"] for s in p["process"] if s.get("kind") == "team"]
    assert team_eids == [exec2]


def test_multi_agent_same_turn_mlr_debate_single_execution(projected):
    """同一条消息两幕共用一个 execution_id（从向量事件手推，不抄 golden）。"""
    from agentcore.runtime.events.types import EventType

    _description, builder = VECTORS["multi_agent_same_turn_mlr_debate"]
    events = list(builder())
    message_ids = [
        e.payload.get("message_id")
        for e in events
        if e.type == EventType.MESSAGE_START
    ]
    assert message_ids == ["m1"]
    plans = [e for e in events if e.type == EventType.RUN_PLAN]
    assert len(plans) >= 2
    eids = {str(e.payload.get("execution_id") or "") for e in plans}
    assert len(eids) == 1
    eid = next(iter(eids))
    assert eid
    assert all(not e.payload.get("prev_execution_id") for e in plans)
    act_ids = []
    for plan in plans:
        act = plan.payload.get("act") or {}
        aid = str(act.get("act_id") or "")
        if aid and aid not in act_ids:
            act_ids.append(aid)
    assert act_ids == ["act-1", "act-2"]
    kinds = {
        str((e.payload.get("act") or {}).get("kind") or "")
        for e in plans
        if (e.payload.get("act") or {}).get("act_id")
    }
    assert kinds == {"multi_agent", "debate"}

    p = projected["multi_agent_same_turn_mlr_debate"]
    acts = p["acts"]
    assert [a["actId"] for a in acts] == ["act-1", "act-2"]
    assert acts[0]["kind"] == "multi_agent"
    assert acts[1]["kind"] == "debate"
    assert acts[1]["anchorRunId"] == "synthesizer"
    run_by_id = {r["id"]: r for r in p["runs"]}
    assert run_by_id["synthesizer"]["actId"] == "act-1"
    assert run_by_id["debate_mod_same"]["actId"] == "act-2"
    assert run_by_id["debate_mod_same_r1_pro"]["actId"] == "act-2"
    assert run_by_id["debate_mod_same_r1_con"]["actId"] == "act-2"
    team_eids = [s["execution_id"] for s in p["process"] if s.get("kind") == "team"]
    assert team_eids == [eid]


# Vectors with no hand-verified assertion in any sentinel module. Ratchet: only down.
# Raising it means a new vector shipped judged solely by "both folds agree with the
# golden the oracle wrote" — legal, but it has to be an explicit line in the diff.
_SENTINEL_UNCOVERED_BASELINE = 66


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
