"""ask_user durable resume — answer→result mapping + frame settlement (结构化挂起 2b).

Pins the pure pieces the ask_user ``POST .../resume`` path adds on top of the
plan_review machinery:

- :func:`ask_user_tool_result` is the SINGLE source of truth shared by the live tool
  and resume — continue / stop / timeout all feed the CEO loop a ``CONTINUE``
  result (stop is 拒答 with soft guidance, not empty-continue「按默认」; wire stays
  ``decision=stop``). ``ADJUST`` is rejected (plan_review only).
- :func:`_settle_resumed_suspension` applies the user's decision to a paused frame by
  kind: for ask_user it emits the journaled ``checkpoint_resolved``, drops off-menu
  picks (same guard as the live tool), and on a **first** STOP leaves ``terminal_text``
  unset (CEO resumes). A second consecutive same-turn STOP upgrades to ``INTERACT``.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.core.types import ToolEffect
from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.checkpoints import CheckpointDecision, CheckpointResponse
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.pipeline.resume import (
    finish_paused_resume,
    finish_terminal_resume,
    pre_pause_content,
    settle_resumed_suspension,
)
from agentcore.runtime.suspension import AskUserSuspension
from agentcore.tools.builtin.ask_user import ask_user_tool_result
from agentcore.tools.builtin.ask_user.result import (
    confirmed_defaults_summary,
    structured_options_summary,
)


def _ask_frame(*, options: list[str] | None = None) -> AskUserSuspension:
    opts = options if options is not None else ["A", "B"]
    return AskUserSuspension(
        message_id="m1",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap1",
        checkpoint_id="ck1",
        tool_call_id="call_ask",
        base_system_prompt="base sys",
        user_message="A 还是 B?",
        transcript=[],
        question="A 还是 B?",
        questions=[
            {
                "id": "q0",
                "prompt": "A 还是 B?",
                "kind": "choice",
                "options": opts,
                "multiple": False,
                "default": "",
            }
        ],
    )


def test_result_continue_empty_uses_legacy_when_no_defaults():
    res = ask_user_tool_result(
        CheckpointResponse(decision=CheckpointDecision.CONTINUE, note="", selected=[])
    )
    assert res.effect is ToolEffect.CONTINUE
    assert "按你提出的方向继续" in res.output


def test_result_continue_empty_injects_confirmed_defaults():
    """案 0cb83288 · B：空 continue + 卡上 default → 用户确认默认：… + 按确认默认。"""
    questions = [
        {
            "id": "q0",
            "prompt": "日程粒度",
            "kind": "choice",
            "options": ["半天块", "小时级"],
            "multiple": False,
            "default": "上班族 + 半天块通用模板",
        }
    ]
    assumptions = [{"id": "a0", "label": "本周=周一至周日", "value": ""}]
    assert "上班族" in confirmed_defaults_summary(questions, assumptions)
    res = ask_user_tool_result(
        CheckpointResponse(decision=CheckpointDecision.CONTINUE, note="", selected=[]),
        questions=questions,
        assumptions=assumptions,
    )
    assert res.effect is ToolEffect.CONTINUE
    assert res.output.startswith("用户确认默认：")
    assert "按确认默认" in res.output
    assert "先问你" in res.output  # 禁表出现在注入文案里
    assert "上班族 + 半天块通用模板" in res.output


def test_result_continue_empty_injects_path_default():
    """53f08：新建仓库/本地路径 default + option.path → 空 continue 注入路径。"""
    questions = [
        {
            "id": "q0",
            "prompt": "仓库路径",
            "kind": "choice",
            "options": [
                {"label": "当前目录建仓", "path": "C:/Work/demo-repo"},
                {"label": "另选文件夹"},
            ],
            "multiple": False,
            "default": "当前目录建仓",
        }
    ]
    summary = confirmed_defaults_summary(questions)
    assert "当前目录建仓" in summary
    assert "C:/Work/demo-repo" in summary
    res = ask_user_tool_result(
        CheckpointResponse(decision=CheckpointDecision.CONTINUE, note="", selected=[]),
        questions=questions,
    )
    assert res.output.startswith("用户确认默认：")
    assert "C:/Work/demo-repo" in res.output
    assert "按确认默认" in res.output


def test_result_continue_empty_restates_options_without_default():
    """d4d5：空 continue + 有选项无 default → 复述选项，禁冲成空模板。"""
    questions = [
        {
            "id": "q0",
            "prompt": "目录恢复后怎么走",
            "kind": "choice",
            "options": ["重新打开/授权", "告知新路径", "改审名册其他项目"],
            "multiple": False,
            "default": "",
        }
    ]
    assert "重新打开/授权" in structured_options_summary(questions)
    res = ask_user_tool_result(
        CheckpointResponse(decision=CheckpointDecision.CONTINUE, note="", selected=[]),
        questions=questions,
    )
    assert "复述" in res.output
    assert "重新打开/授权" in res.output
    assert "空转确认" in res.output or "不承接选项" in res.output
    assert "按你提出的方向继续" not in res.output


# --- ask_user_tool_result: the shared answer → ToolResult mapping ------------------


def test_result_continue_folds_picks_and_note():
    res = ask_user_tool_result(
        CheckpointResponse(decision=CheckpointDecision.CONTINUE, note="走稳一点", selected=["A"])
    )
    assert res.effect is ToolEffect.CONTINUE
    assert res.final_text is None  # non-terminal: no in-band closing reply
    assert "A" in res.output and "走稳一点" in res.output


def test_result_stop_feeds_ceo_with_cancel_guidance():
    res = ask_user_tool_result(
        CheckpointResponse(decision=CheckpointDecision.STOP, note="先到这", selected=[])
    )
    # stop = 拒答可见：CONTINUE 回灌 CEO（非 INTERACT 静默终结）；留言进 output。
    assert res.effect is ToolEffect.CONTINUE
    assert res.final_text is None
    assert "取消了澄清" in res.output
    assert "先到这" in res.output
    assert "默认据此收口" in res.output
    assert "禁止】再弹 ask_user" in res.output or "再弹 ask_user" in res.output

def test_result_stop_empty_note_still_feeds_ceo():
    res = ask_user_tool_result(
        CheckpointResponse(decision=CheckpointDecision.STOP, note="", selected=[])
    )
    assert res.effect is ToolEffect.CONTINUE
    assert res.final_text is None
    assert "取消了澄清" in res.output
    assert "用户留言" not in res.output


def test_result_adjust_rejected():
    with pytest.raises(ValueError, match="ADJUST"):
        ask_user_tool_result(
            CheckpointResponse(decision=CheckpointDecision.ADJUST, note="走稳一点", selected=["A"])
        )


def test_result_timeout_hands_back_to_ceo():
    res = ask_user_tool_result(CheckpointResponse(decision=CheckpointDecision.TIMEOUT))
    # not terminal — the CEO decides how to wrap up on the next round.
    assert res.effect is ToolEffect.CONTINUE
    assert res.final_text is None


# --- _settle_resumed_suspension: ask_user branch ----------------------------------


def _sink_with_seeded_checkpoint() -> EventSink:
    """An EventSink pre-seeded with the pause's ``checkpoint_required`` — as
    ``resume_chat_pipeline`` does via ``seed_journal`` before settling. Without this
    surface event ``execution_journal`` returns None (nothing to replay)."""
    sink = EventSink()
    sink.seed_journal(
        [{"type": EventType.CHECKPOINT_REQUIRED.value, "payload": {}, "timestamp": "t"}]
    )
    return sink


async def test_settle_ask_user_stop_feeds_loop_without_terminal():
    sink = _sink_with_seeded_checkpoint()
    settled = await settle_resumed_suspension(
        _ask_frame(),
        decision=CheckpointDecision.STOP,
        note="收工",
        selected=[],
        sink=sink,
        delegate_tool=None,  # unused on the ask_user branch
        execution_id="",
    )
    # stop → CEO round (拒答可见)；留言在 output，无 terminal_text。
    assert settled.terminal_text is None
    assert settled.effect is ToolEffect.CONTINUE
    assert "取消了澄清" in settled.output
    assert "收工" in settled.output
    # the resolution is journaled so a reload replays the settled card.
    journal = sink.execution_journal() or []
    assert any(e["type"] == EventType.CHECKPOINT_RESOLVED.value for e in journal)


async def test_settle_ask_user_continue_feeds_loop_without_terminal():
    sink = EventSink()
    settled = await settle_resumed_suspension(
        _ask_frame(),
        decision=CheckpointDecision.CONTINUE,
        note="",
        selected=["A"],
        sink=sink,
        delegate_tool=None,
        execution_id="",
    )
    # continue → no terminal text (run the CEO loop), and the pick rides the result.
    assert settled.terminal_text is None
    assert settled.effect is ToolEffect.CONTINUE
    assert "A" in settled.output


async def test_settle_ask_user_drops_off_menu_picks():
    # A resolve can't inject arbitrary strings into the CEO context — only offered
    # options survive (same guard as the live AskUserTool).
    sink = _sink_with_seeded_checkpoint()
    settled = await settle_resumed_suspension(
        _ask_frame(options=["A", "B"]),
        decision=CheckpointDecision.CONTINUE,
        note="",
        selected=["A", "HACK"],
        sink=sink,
        delegate_tool=None,
        execution_id="",
    )
    assert "A" in settled.output
    assert "HACK" not in settled.output
    resolved = [
        e
        for e in (sink.execution_journal() or [])
        if e["type"] == EventType.CHECKPOINT_RESOLVED.value
    ]
    assert resolved and resolved[0]["payload"]["selected"] == ["A"]


async def test_settle_organize_plan_continue_keeps_all_selected():
    """B1: organize_plan confirm with full selected (= mobile keep-all) registers all ops."""
    from agentcore.workspace import organize_plan_store

    organize_plan_store.clear_all_for_tests()
    try:
        frame = AskUserSuspension(
            message_id="m1",
            conversation_id="c1",
            user_id="u1",
            captain_run_id="cap1",
            checkpoint_id="ck-org",
            tool_call_id="call_ask",
            base_system_prompt="base",
            user_message="整理桌面",
            transcript=[],
            question="保留哪些操作？",
            intent="organize_plan",
            questions=[
                {
                    "id": "q0",
                    "prompt": "保留哪些操作？",
                    "kind": "choice",
                    "multiple": True,
                    "default": "",
                    "options": [
                        {
                            "label": "a → b",
                            "op": "move",
                            "source": "a",
                            "destination": "b",
                        },
                        {"label": "删 x", "op": "delete", "path": "x"},
                    ],
                }
            ],
        )
        sink = _sink_with_seeded_checkpoint()
        settled = await settle_resumed_suspension(
            frame,
            decision=CheckpointDecision.CONTINUE,
            note="",
            selected=["a → b", "删 x"],
            sink=sink,
            delegate_tool=None,
            execution_id="",
        )
        assert settled.terminal_text is None
        assert "plan_id=ck-org" in settled.output
        assert "保留 2 项" in settled.output
        plan = organize_plan_store.get_plan("ck-org")
        assert plan is not None
        assert len(plan.operations) == 2
        resolved = [
            e
            for e in (sink.execution_journal() or [])
            if e["type"] == EventType.CHECKPOINT_RESOLVED.value
        ]
        assert resolved and resolved[0]["payload"]["selected"] == ["a → b", "删 x"]
    finally:
        organize_plan_store.clear_all_for_tests()


# --- pre-pause carry-forward: a 2b resume keeps the CEO's pre-pause reply -----------


def test_pre_pause_content_joins_this_turn_assistant_rounds():
    # The frame transcript ends with this turn's assistant rounds; their joined content
    # (paragraph-separated) is the pre-pause reply — what the live loop already accrued.
    transcript = [
        LLMMessage(role="system", content="sys"),
        LLMMessage(role="user", content="新任务"),
        LLMMessage(role="assistant", content="先看一下需求"),
        LLMMessage(role="assistant", content="我来发问"),
    ]
    assert pre_pause_content(transcript) == "先看一下需求\n\n我来发问"


def test_pre_pause_content_excludes_prior_turns():
    # Only THIS turn counts: assistant text before the last user message belongs to an
    # earlier message and must not leak into the resumed reply.
    transcript = [
        LLMMessage(role="user", content="上一轮"),
        LLMMessage(role="assistant", content="上一轮的回答"),
        LLMMessage(role="user", content="这一轮"),
        LLMMessage(role="assistant", content="这一轮开场"),
    ]
    assert pre_pause_content(transcript) == "这一轮开场"


def test_pre_pause_content_empty_when_no_preamble():
    # The ideal ask_user shape (after the prompt fix): the CEO calls ask_user with an
    # empty body, so there is nothing to carry forward.
    transcript = [
        LLMMessage(role="user", content="问"),
        LLMMessage(role="assistant", content=""),
    ]
    assert pre_pause_content(transcript) == ""


def test_finish_terminal_resume_prepends_pre_pause_to_closing():
    # ask_user STOP after the CEO already wrote an overview: the persisted reply is the
    # overview + closing note as separate paragraphs (parity with live), not the closing
    # note alone — the pre-pause text must not be dropped on a fresh-process resume.
    result = finish_terminal_resume(
        message_id="m1",
        pre_pause_content="阶段成果如上。",
        closing="先到这。",
        sink=EventSink(),
    )
    assert result["content"] == "阶段成果如上。\n\n先到这。"


def test_finish_terminal_resume_keeps_closing_only_without_pre_pause():
    result = finish_terminal_resume(
        message_id="m1", pre_pause_content="", closing="先到这。", sink=EventSink()
    )
    assert result["content"] == "先到这。"


def test_finish_paused_resume_emits_paused_without_closing():
    """Re-entrant settle SUSPEND → PAUSED; keep pre_pause, no CEO closing text."""
    from agentcore.runtime.events import FinishReason

    result = finish_paused_resume(
        message_id="m1",
        pre_pause_content="挂起前正文",
        sink=EventSink(),
        pre_pause_reasoning="想",
    )
    assert result["finish_reason"] is FinishReason.PAUSED
    assert result["content"] == "挂起前正文"
    assert result["reasoning_content"] == "想"
    assert result["rounds"] == 0
    assert result["input_tokens"] == 0


async def test_recover_window_skips_tool_result_on_suspend(monkeypatch):
    """SUSPEND during settle must leave the original tool_call PENDING (no result)."""
    from agentcore.core.types import ToolEffect
    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
    from agentcore.runtime.pipeline.resume import recover_path as rp
    from agentcore.runtime.recover import SettledSuspension
    from agentcore.runtime.runs import RunPlan, RunSpec
    from agentcore.runtime.suspension import TeamPreviewSuspension

    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="t", role="研究员")])
    suspension = TeamPreviewSuspension(
        message_id="m1",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap1",
        checkpoint_id="cp1",
        tool_call_id="call_del",
        user_message="task",
        base_system_prompt="sys",
        journal_entries=[],
        plan=plan,
        workers=[{"run_id": "w1", "role": "研究员", "task": "t"}],
        transcript=[
            LLMMessage(role="user", content="task"),
            LLMMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_del",
                        function=ToolCallFunction(name="delegate", arguments="{}"),
                    )
                ],
            ),
        ],
    )
    monkeypatch.setattr(
        rp,
        "resumed_captain_window",
        lambda _s, _h: list(suspension.transcript),
    )
    monkeypatch.setattr(
        rp,
        "recover_turn",
        AsyncMock(
            return_value=SettledSuspension("", None, ToolEffect.SUSPEND),
        ),
    )
    persist = MagicMock()
    append = MagicMock()
    monkeypatch.setattr(rp, "persist_resumed_tool_results", persist)
    monkeypatch.setattr(rp, "append_resumed_tool_results", append)

    recovered = await rp.recover_and_rebuild_window(
        suspension=suspension,
        decision=CheckpointDecision.CONTINUE,
        note="",
        selected=[],
        history=None,
        sink=EventSink(),
        delegate_tool=MagicMock(),
        debate_tool=MagicMock(),
        execution_id="e1",
        captain_run_id="cap1",
    )
    assert recovered.settled.effect is ToolEffect.SUSPEND
    append.assert_not_called()
    persist.assert_not_called()
    # Window still ends on the pending assistant tool_call (no tool result appended).
    assert recovered.messages[-1].role == "assistant"
    assert recovered.messages[-1].tool_calls


async def test_recover_window_stop_skips_continuity_steer(monkeypatch):
    """ask_user STOP feeds CEO but must not inject deliverable continuity steer."""
    from agentcore.core.types import ToolEffect
    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
    from agentcore.runtime.pipeline.resume import recover_path as rp
    from agentcore.runtime.recover import SettledSuspension

    suspension = _ask_frame()
    suspension.transcript = [
        LLMMessage(role="user", content="A 还是 B?"),
        LLMMessage(
            role="assistant",
            content="已交付前半段分析。",
            tool_calls=[
                ToolCall(
                    id="call_ask",
                    function=ToolCallFunction(name="ask_user", arguments="{}"),
                )
            ],
        ),
    ]
    monkeypatch.setattr(
        rp,
        "resumed_captain_window",
        lambda _s, _h: list(suspension.transcript),
    )
    monkeypatch.setattr(
        rp,
        "recover_turn",
        AsyncMock(
            return_value=SettledSuspension(
                "用户取消了澄清，未作答。\n宜据此自行收口。",
                None,
                ToolEffect.CONTINUE,
            ),
        ),
    )
    monkeypatch.setattr(rp, "persist_resumed_tool_results", MagicMock())
    monkeypatch.setattr(
        rp,
        "append_resumed_tool_results",
        lambda msgs, _id, output: msgs.append(
            LLMMessage(role="tool", content=output, tool_call_id="call_ask")
        ),
    )

    recovered = await rp.recover_and_rebuild_window(
        suspension=suspension,
        decision=CheckpointDecision.STOP,
        note="",
        selected=[],
        history=None,
        sink=EventSink(),
        delegate_tool=MagicMock(),
        debate_tool=MagicMock(),
        execution_id="e1",
        captain_run_id="cap1",
        pre_pause_override="已交付前半段分析。",
    )
    assert recovered.settled.terminal_text is None
    assert recovered.messages[-1].role == "tool"
    assert "取消了澄清" in (recovered.messages[-1].content or "")
    # No continuity steer user message after the cancel tool result.
    assert not any(
        m.role == "user" and "[系统提示]" in (m.content or "") for m in recovered.messages
    )


async def test_resume_pipeline_suspend_skips_ceo(monkeypatch):
    """team_preview settle → SUSPEND must PAUSED-finish without arming the CEO loop."""
    from types import SimpleNamespace

    from agentcore.runtime.events import FinishReason
    from agentcore.runtime.pipeline.resume import pipeline as resume_mod
    from agentcore.runtime.pipeline.resume.recover_path import RecoveredResume
    from agentcore.runtime.pipeline.resume.rehydrate import RehydratedTurnState
    from agentcore.runtime.recover import SettledSuspension
    from agentcore.runtime.runs import RunPlan, RunSpec
    from agentcore.runtime.suspension import TeamPreviewSuspension
    from agentcore.workspace.protocol import WorkspaceBackend

    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="t", role="研究员")])
    suspension = TeamPreviewSuspension(
        message_id="m1",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap1",
        checkpoint_id="cp-preview",
        tool_call_id="call_del",
        user_message="task",
        base_system_prompt="sys",
        journal_entries=[],
        plan=plan,
        workers=[{"run_id": "w1", "role": "研究员", "task": "t"}],
    )
    sink = EventSink()
    llm = MagicMock()
    llm.supports_tools = True
    llm.close = AsyncMock()

    wired = SimpleNamespace(
        delegate_tool=MagicMock(),
        debate_tool=MagicMock(),
        chat_tools=[],
        base_tool_context=SimpleNamespace(execution_id="e1"),
        approval_gate=MagicMock(),
        bound_execution_id=None,
        execution_id_token=None,
        vision_cost_sink=[],
    )
    monkeypatch.setattr(resume_mod, "wire_resume_turn", AsyncMock(return_value=wired))
    monkeypatch.setattr(
        resume_mod,
        "bootstrap_resume_display",
        lambda **_k: RehydratedTurnState(
            pre_pause_content="挂起前",
            pre_pause_reasoning="",
            citations=[],
            from_turn_paused=False,
            controller_seed=None,
        ),
    )
    monkeypatch.setattr(
        resume_mod,
        "recover_and_rebuild_window",
        AsyncMock(
            return_value=RecoveredResume(
                messages=[LLMMessage(role="user", content="task")],
                pre_pause="挂起前",
                settled=SettledSuspension("", None, ToolEffect.SUSPEND),
            )
        ),
    )
    monkeypatch.setattr(
        resume_mod.pipeline_pkg, "build_turn_router", AsyncMock(return_value=llm)
    )
    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        MagicMock(side_effect=RuntimeError("no db")),
    )
    captain = MagicMock(side_effect=AssertionError("CEO must not run on re-suspend"))
    monkeypatch.setattr(resume_mod, "build_captain_resumer", captain)

    result = await resume_mod.resume_chat_pipeline(
        suspension=suspension,
        decision=CheckpointDecision.CONTINUE,
        note="",
        sink=sink,
        backend=MagicMock(spec=WorkspaceBackend),
    )
    assert result["finish_reason"] is FinishReason.PAUSED
    assert result["content"] == "挂起前"
    captain.assert_not_called()
    llm.close.assert_awaited()
