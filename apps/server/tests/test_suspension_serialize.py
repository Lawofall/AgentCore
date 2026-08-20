"""TurnSuspension JSON round-trip + capture helpers (结构化挂起 2b turn 级落盘).

Pins the inert data layer that makes a plan_review / ask_user pause durable: the
serializers (RunState seed, RunPlan with minted run_ids, scheduler completed-map) still
round-trip losslessly (they back the journal facts now), and each TurnSuspension subclass
frame round-trips its CONTROL metadata — dispatched back to the right kind by
``suspension_from_json``. The frame holds resume CONTROL metadata ONLY: the rebuild inputs
(``transcript`` / ``history`` / ``journal`` / ``journal_entries`` — Phase 2 ⑤; ``plan`` /
``completed`` — Phase 2) are NOT serialized — the CEO window, the DAG and the finished-worker
seed are all projections of ``turn_journal`` (+ reloaded history), rebuilt on claim, so these
tests assert they DON'T survive to_json.
"""

import pytest

from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.runs import RunPhase, RunPlan, RunSpec, RunState
from agentcore.runtime.runs.serialize import (
    plan_from_json,
    plan_to_json,
    state_from_json,
    state_map_from_json,
    state_map_to_json,
    state_to_json,
)
from agentcore.runtime.suspension import (
    AskUserSuspension,
    PlanReviewSuspension,
    SuspensionKind,
    TeamPreviewSuspension,
    find_tool_call_id,
    suspension_from_json,
)


def _completed_state() -> RunState:
    return RunState(
        phase=RunPhase.COMPLETED,
        content="worker 产出",
        reasoning="想了想",
        model="deepseek-v4-flash",
        duration_ms=1234,
        rounds=2,
        usage={"input": 10, "output": 20, "reasoning": 5, "cache_hit": 0, "cache_miss": 10},
        cost={"input": 1, "cached": 0, "output": 2, "total": 3, "currency": "USD"},
        citations=[{"url": "https://x", "title": "X"}],
    )


def test_state_seed_round_trips_dropping_transcript():
    state = _completed_state()
    state.transcript = [LLMMessage(role="assistant", content="heavy")]
    restored = state_from_json(state_to_json(state))
    assert restored.phase is RunPhase.COMPLETED
    assert restored.content == "worker 产出"
    assert restored.usage["cache_miss"] == 10
    assert restored.cost["total"] == 3
    assert restored.citations == [{"url": "https://x", "title": "X"}]
    # The heavy transcript is intentionally dropped — a seed node is never re-run.
    assert restored.transcript == []


def test_plan_round_trips_preserving_minted_run_ids_and_origin():
    plan = RunPlan(
        nodes=[
            RunSpec(run_id="del_abc_1", task="研究", role="研究员", depends_on=[]),
            RunSpec(run_id="del_abc_2", task="汇总", role="编辑", depends_on=["del_abc_1"]),
        ]
    )
    restored = plan_from_json(plan_to_json(plan))
    assert [n.run_id for n in restored.nodes] == ["del_abc_1", "del_abc_2"]
    assert restored.nodes[1].depends_on == ["del_abc_1"]
    # origin survives so the resumed plan keeps its provenance.
    assert restored.origin is plan.origin


def test_state_map_round_trips():
    completed = {"del_abc_1": _completed_state()}
    restored = state_map_from_json(state_map_to_json(completed))
    assert set(restored) == {"del_abc_1"}
    assert restored["del_abc_1"].content == "worker 产出"


def test_turn_suspension_full_frame_round_trips():
    transcript = [
        LLMMessage(role="system", content="sys"),
        LLMMessage(role="user", content="原始请求"),
        LLMMessage(
            role="assistant",
            content=None,
            reasoning_content="先派活",
            tool_calls=[
                ToolCall(
                    id="call_del_1",
                    function=ToolCallFunction(name="delegate", arguments='{"tasks":[]}'),
                )
            ],
        ),
    ]
    plan = RunPlan(
        nodes=[
            RunSpec(run_id="del_abc_1", task="研究", role="研究员"),
            RunSpec(run_id="del_abc_2", task="实现", role="工程师", depends_on=["del_abc_1"]),
        ]
    )
    frame = PlanReviewSuspension(
        message_id="m1",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap1",
        checkpoint_id="ck1",
        tool_call_id="call_del_1",
        base_system_prompt="base sys",
        user_message="原始请求",
        folder_id="F1",
        memory_enabled=False,
        transcript=transcript,
        plan=plan,
        completed={"del_abc_1": _completed_state()},
        journal_entries=[{"kind": "run_plan", "payload": {}, "ts": "t"}],
        steps=[{"run_id": "del_abc_1", "role": "研究员", "summary": "…"}],
        pending=[{"run_id": "del_abc_2", "role": "工程师"}],
        ceo_review={
            "conclusion": "可过",
            "risks": ["r"],
            "suggestions": ["s"],
            "source": "llm",
        },
        trace_id="trace123",
    )

    restored = suspension_from_json(frame.to_json())

    # The discriminator dispatched back to the plan_review subclass.
    assert isinstance(restored, PlanReviewSuspension)
    assert restored.kind is SuspensionKind.PLAN_REVIEW
    assert restored.message_id == "m1"
    assert restored.conversation_id == "c1"
    assert restored.user_id == "u1"
    assert restored.captain_run_id == "cap1"
    assert restored.checkpoint_id == "ck1"
    assert restored.tool_call_id == "call_del_1"
    assert restored.base_system_prompt == "base sys"
    assert restored.user_message == "原始请求"
    assert restored.ceo_review == {
        "conclusion": "可过",
        "risks": ["r"],
        "suggestions": ["s"],
        "source": "llm",
    }
    assert frame.to_json()["ceo_review"]["source"] == "llm"
    # The project scope survives the frame so resume re-wires consult to it.
    assert restored.folder_id == "F1"
    assert frame.to_json()["folder_id"] == "F1"
    # The memory master switch survives too: a memory-off turn resumes memory-off.
    assert restored.memory_enabled is False
    assert frame.to_json()["memory_enabled"] is False
    assert restored.trace_id == "trace123"
    # transcript / history are NOT serialized into the frame (Phase 2 ⑤) — the CEO window
    # is rebuilt from turn_journal on claim; resume echoes the call via the serialized
    # tool_call_id (asserted above), not a stored transcript blob.
    assert "transcript" not in frame.to_json()
    assert "history" not in frame.to_json()
    assert restored.transcript == []
    # NEITHER the ``plan`` (with minted ids) NOR the finished-worker ``completed`` seed is
    # serialized (执行级事件溯源 Phase 2) — resume re-projects BOTH from the journal
    # (``plan_from_journal`` / ``completed_from_journal``), so a claimed frame carries an
    # empty plan placeholder + no completed.
    assert "plan" not in frame.to_json()
    assert restored.plan.nodes == []
    assert "completed" not in frame.to_json()
    assert restored.completed == {}
    # steps / pending carried for card re-render; the journal is NOT serialized into
    # the frame — it lives in turn_journal (§18.3), hydrated separately on claim.
    assert "journal" not in frame.to_json()
    assert restored.journal == []
    assert restored.steps[0]["run_id"] == "del_abc_1"
    assert restored.pending[0]["run_id"] == "del_abc_2"
    # the reviewed checkpoint roots an adjust steer scopes to.
    assert restored.checkpoint_run_ids == {"del_abc_1"}


def test_ask_user_suspension_round_trips():
    # The ask_user frame carries the card payload (no plan tail) so resume can
    # re-emit the prompt + validate picks — and the discriminator routes it back.
    transcript = [
        LLMMessage(role="system", content="sys"),
        LLMMessage(role="user", content="帮我选"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_ask_1",
                    function=ToolCallFunction(
                        name="ask_user", arguments='{"question":"A 还是 B?"}'
                    ),
                )
            ],
        ),
    ]
    frame = AskUserSuspension(
        message_id="m2",
        conversation_id="c2",
        user_id="u2",
        captain_run_id="cap2",
        checkpoint_id="ck2",
        tool_call_id="call_ask_1",
        base_system_prompt="base sys",
        user_message="帮我选",
        folder_id="F2",
        transcript=transcript,
        question="按这个计划开做？\n两者代价不同",
        assumptions=[{"id": "a0", "label": "部署", "value": "纯静态"}],
        questions=[
            {
                "id": "q0",
                "prompt": "A 还是 B?",
                "kind": "choice",
                "options": [{"label": "A"}, {"label": "B"}],
                "multiple": True,
                "default": "",
            }
        ],
        intent="kickoff",
        journal_entries=[{"kind": "checkpoint_required", "payload": {}, "ts": "t"}],
        trace_id="trace456",
    )

    restored = suspension_from_json(frame.to_json())

    assert isinstance(restored, AskUserSuspension)
    assert restored.kind is SuspensionKind.ASK_USER
    assert restored.message_id == "m2"
    assert restored.tool_call_id == "call_ask_1"
    assert restored.folder_id == "F2"
    assert restored.question == "按这个计划开做？\n两者代价不同"
    assert "context" not in frame.to_json()
    assert restored.assumptions == [{"id": "a0", "label": "部署", "value": "纯静态"}]
    assert restored.questions[0]["prompt"] == "A 还是 B?"
    assert restored.questions[0]["options"] == [{"label": "A"}, {"label": "B"}]
    assert restored.questions[0]["multiple"] is True
    assert restored.intent == "kickoff"
    # transcript / history are NOT serialized (Phase 2 ⑤): resume echoes the call via the
    # serialized tool_call_id (asserted above) and rebuilds the window from turn_journal.
    assert "transcript" not in frame.to_json()
    assert "history" not in frame.to_json()
    assert restored.transcript == []
    # the journal is not in the frame — turn_journal owns it (§18.3).
    assert "journal" not in frame.to_json()
    assert restored.journal == []


def _minimal_team_preview(**overrides) -> TeamPreviewSuspension:
    base: dict = {
        "message_id": "m1",
        "conversation_id": "c1",
        "user_id": "u1",
        "captain_run_id": "cap1",
        "checkpoint_id": "ck1",
        "tool_call_id": "call_del_1",
        "base_system_prompt": "sys",
        "user_message": "组个团队",
        "plan": RunPlan(),
        "workers": [{"run_id": "w1", "role": "研究员", "task": "调研"}],
    }
    base.update(overrides)
    return TeamPreviewSuspension(**base)


def test_team_preview_frame_round_trips_batch_coordination():
    """开工卡帧携带批次协作参数（coordination / team_brief / seed_notes）。

    挂起点在 setup_note_wall 之前，这三样只活在 DelegateTool 实例上；不落帧则耐久恢复
    （全新工具实例，_coordination 缺省 none）后 wall 批降级 → worker 被剥便签三件套、
    CEO 预贴便签永久丢失（2026-07-20 P2 手驱真跑抓获的真 bug）。
    """
    frame = _minimal_team_preview(
        coordination="wall",
        team_brief="统一用中文交付",
        seed_notes=[{"kind": "heads_up", "text": "接口用 REST"}],
    )
    restored = suspension_from_json(frame.to_json())
    assert isinstance(restored, TeamPreviewSuspension)
    assert restored.coordination == "wall"
    assert restored.team_brief == "统一用中文交付"
    assert restored.seed_notes == [{"kind": "heads_up", "text": "接口用 REST"}]


def test_team_preview_frame_defaults_stay_compact_and_legacy_safe():
    # 缺省批（none / 无简报 / 无种子）不写键 —— 帧紧凑，且与旧帧字节口径一致。
    frame = _minimal_team_preview()
    data = frame.to_json()
    assert "coordination" not in data
    assert "team_brief" not in data
    assert "seed_notes" not in data
    # 旧帧（无这些键）读回走缺省 —— 行为与修复前逐字节等价。
    restored = suspension_from_json(data)
    assert isinstance(restored, TeamPreviewSuspension)
    assert restored.coordination == "none"
    assert restored.team_brief is None
    assert restored.seed_notes == []


def test_plan_review_frame_round_trips_batch_coordination():
    frame = PlanReviewSuspension(
        message_id="m1",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap1",
        checkpoint_id="ck1",
        tool_call_id="call_del_1",
        base_system_prompt="sys",
        user_message="带检查点的团队",
        plan=RunPlan(),
        steps=[{"run_id": "w1", "role": "研究员", "summary": "…"}],
        coordination="wall",
        team_brief="口径按 v2 契约",
    )
    restored = suspension_from_json(frame.to_json())
    assert isinstance(restored, PlanReviewSuspension)
    assert restored.coordination == "wall"
    assert restored.team_brief == "口径按 v2 契约"
    # 缺省批不写键 + 旧帧读回缺省。
    plain = PlanReviewSuspension(
        message_id="m2",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap1",
        checkpoint_id="ck2",
        tool_call_id="call_del_2",
        base_system_prompt="sys",
        user_message="普通团队",
        plan=RunPlan(),
    )
    data = plain.to_json()
    assert "coordination" not in data
    assert "team_brief" not in data
    restored_plain = suspension_from_json(data)
    assert isinstance(restored_plain, PlanReviewSuspension)
    assert restored_plain.coordination == "none"
    assert restored_plain.team_brief is None


def test_suspension_from_json_requires_kind():
    with pytest.raises(ValueError, match="missing or unknown suspension kind"):
        suspension_from_json({"message_id": "m1"})


def test_find_tool_call_id_picks_trailing_matching_call():
    transcript = [
        LLMMessage(role="user", content="原始"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_search", function=ToolCallFunction(name="web_search", arguments="{}")
                ),
            ],
        ),
        LLMMessage(role="tool", content="…", tool_call_id="call_search"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(id="call_del", function=ToolCallFunction(name="delegate", arguments="{}")),
                ToolCall(id="call_ask", function=ToolCallFunction(name="ask_user", arguments="{}")),
            ],
        ),
    ]
    # The helper is tool-agnostic — it finds the trailing call by NAME.
    assert find_tool_call_id(transcript, "delegate") == "call_del"
    assert find_tool_call_id(transcript, "ask_user") == "call_ask"


def test_find_tool_call_id_skips_excluded_sibling():
    transcript = [
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(id="ask_a", function=ToolCallFunction(name="ask_user", arguments="{}")),
                ToolCall(id="ask_b", function=ToolCallFunction(name="ask_user", arguments="{}")),
            ],
        ),
    ]
    assert find_tool_call_id(transcript, "ask_user") == "ask_a"
    assert find_tool_call_id(transcript, "ask_user", exclude_ids={"ask_a"}) == "ask_b"


def test_claim_next_tool_call_id_distinct_for_parallel_ask_user():
    from agentcore.runtime.suspension import claim_next_tool_call_id

    transcript = [
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(id="ask_a", function=ToolCallFunction(name="ask_user", arguments="{}")),
                ToolCall(id="ask_b", function=ToolCallFunction(name="ask_user", arguments="{}")),
            ],
        ),
    ]
    mid = "msg-claim-parallel-ask"
    assert claim_next_tool_call_id(mid, transcript, "ask_user") == "ask_a"
    assert claim_next_tool_call_id(mid, transcript, "ask_user") == "ask_b"
    assert claim_next_tool_call_id(mid, transcript, "ask_user") == ""


def test_claim_batch_release_allows_reuse_on_same_message_id():
    from agentcore.runtime.suspension import (
        claim_next_tool_call_id,
        release_claimed_pause_tool_calls_if_complete,
    )

    transcript = [
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(id="ask_a", function=ToolCallFunction(name="ask_user", arguments="{}")),
                ToolCall(id="ask_b", function=ToolCallFunction(name="ask_user", arguments="{}")),
            ],
        ),
    ]
    mid = "msg-claim-release-reuse"
    assert claim_next_tool_call_id(mid, transcript, "ask_user") == "ask_a"
    release_claimed_pause_tool_calls_if_complete(mid, transcript)
    assert claim_next_tool_call_id(mid, transcript, "ask_user") == "ask_b"
    assert claim_next_tool_call_id(mid, transcript, "ask_user") == ""
    release_claimed_pause_tool_calls_if_complete(mid, transcript)
    assert claim_next_tool_call_id(mid, transcript, "ask_user") == "ask_a"


def test_find_tool_call_id_empty_when_absent():
    transcript = [LLMMessage(role="user", content="hi")]
    assert find_tool_call_id(transcript, "delegate") == ""
