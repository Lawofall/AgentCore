"""Integration tests for convergence governance wired into the ReAct loop.

Uses a scripted fake provider (no network) and a stub tool to drive the three
behaviors added to ``engine.react_loop``:
  * identical successful tool calls are not a stuck pattern (no nudge)
  * a repeated failing tool call → failure-flavored NUDGE
  * round-budget exhaustion mid-tool-call → forced tool-free answer (never blank)

The same harness also covers per-turn citation aggregation and the A2 citation
numbering (engine-assigned card numbers folded back into the tool output so the
model cites by a number that always lines up with the card).
"""

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from agentcore.config import settings
from agentcore.core.types import ToolCategory, ToolEffect
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, TokenUsage, ToolCallDelta
from agentcore.runtime.engine import ReactLoopOut, react_loop, resolve_tool_timeout
from agentcore.runtime.events import EventSink, EventType, FinishReason, SSEEvent
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_profile_params


def _tool_chunk(name: str, args: str, *, call_id: str = "c") -> LLMChunk:
    return LLMChunk(
        delta_tool_calls=[
            ToolCallDelta(index=0, id=call_id, function_name=name, arguments_delta=args)
        ]
    )


def _content_chunk(text: str) -> LLMChunk:
    return LLMChunk(delta_content=text)


class _ScriptedProvider:
    """Yields a pre-scripted list of chunks on each ``stream`` call (one per round)."""

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk


class _StubTool:
    """A tool that records its call count and reports a fixed success/failure.

    Optionally carries ``citations`` (research-tool data) and/or behaves as a
    ``terminal`` handoff tool — used to verify the loop's citation aggregation,
    including the handoff early-return path.
    """

    def __init__(
        self,
        name: str = "search",
        *,
        success: bool = True,
        citations: list[dict] | None = None,
        citation_script: list[list[dict]] | None = None,
        terminal: bool = False,
        fail_output: str = "",
        category: ToolCategory = ToolCategory.SEARCH,
    ) -> None:
        self._name = name
        self._success = success
        self._category = category
        self._citations = citations
        # Diagnostic detail a failing tool puts in ``output`` (mirrors code_execute,
        # whose stdout/stderr ride output while ``error`` is just the exit code).
        self._fail_output = fail_output
        # Per-call citation lists (i-th call returns the i-th list); lets a test
        # drive multi-round dedup/numbering. Overrides ``citations`` when set.
        self._citation_script = citation_script
        self._terminal = terminal
        self.calls = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=self._category,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        call_index = self.calls
        self.calls += 1
        if not self._success:
            return ToolResult(
                tool_call_id="", success=False, output=self._fail_output, error="boom"
            )
        if self._citation_script is not None:
            citations = (
                self._citation_script[call_index]
                if call_index < len(self._citation_script)
                else None
            )
        else:
            citations = self._citations
        return ToolResult(
            tool_call_id="",
            success=True,
            output="result",
            citations=citations,
            effect=ToolEffect.HANDOFF if self._terminal else ToolEffect.CONTINUE,
            final_text="streamed answer" if self._terminal else None,
        )


def _registry(tool: _StubTool) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(tool)
    return reg


def _context() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


async def _run(
    provider: _ScriptedProvider,
    tool: _StubTool,
    *,
    max_rounds: int,
    citation_sink: list[dict] | None = None,
    annotate_citations: bool = True,
    deliverable_only: bool = False,
    on_reset: Callable[[], None] | None = None,
):
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    profile = make_profile_params(max_rounds=max_rounds)
    result = await react_loop(
        messages=messages,
        llm=provider,
        tools=_registry(tool),
        sink=EventSink(),
        tool_context=_context(),
        profile=profile,
        turn_model="m",
        out=None if citation_sink is None else ReactLoopOut(citations=citation_sink),
        annotate_citations=annotate_citations,
        deliverable_only=deliverable_only,
        on_reset=on_reset,
        approval_gate=None,
    )
    return result, messages


async def test_repeated_success_does_not_nudge(monkeypatch):
    monkeypatch.setattr(settings, "engine_convergence_spin_rounds", 0)
    same = _tool_chunk("search", '{"q": "x"}')
    provider = _ScriptedProvider(
        [[same], [same], [same], [_content_chunk("done from inventory")]]
    )
    tool = _StubTool()
    (content, _r, _usage, rounds), messages = await _run(provider, tool, max_rounds=20)

    assert content == "done from inventory"
    assert rounds == 4
    assert tool.calls == 3
    nudges = [m for m in messages if m.role == "user" and m.content and "停止重复" in m.content]
    assert nudges == []


async def test_repeated_failure_nudge_is_failure_flavored():
    same = _tool_chunk("search", '{"q": "x"}')
    # 3 identical failures → repeated-failure NUDGE; round 3 the model gives a plain
    # answer. (The cumulative circuit breaker also fires its own steers here — they're
    # a separate mechanism; this test pins the fingerprint-flavored nudge specifically.)
    provider = _ScriptedProvider(
        [[same], [same], [same], [_content_chunk("gave up, here is what I know")]]
    )
    tool = _StubTool(success=False)
    (content, *_), messages = await _run(provider, tool, max_rounds=20)

    assert content == "gave up, here is what I know"
    # the distinctive repeated-failure nudge (anchored to the exact-repeat count) is
    # injected exactly once
    nudges = [
        m for m in messages if m.role == "user" and m.content and "已用相同方式失败" in m.content
    ]
    assert len(nudges) == 1


async def test_failed_tool_surfaces_diagnostic_output_not_just_error():
    # 失败的工具结果必须把 output（如 code_execute 的 stdout/stderr）连同 error 一起回给
    # model——否则模型只看到「boom」这种干巴巴的 error、对真实报错盲调（曾导致 worker 反复
    # 乱试 bash 才发现 bash 在本机不可用）。这里模拟 code_execute 失败：error 简短、真正的
    # 诊断在 output 里。
    call = _tool_chunk("search", "{}")
    provider = _ScriptedProvider([[call], [_content_chunk("ok")]])
    tool = _StubTool(success=False, fail_output="stderr:\nexecvpe(/bin/bash) failed")
    _result, messages = await _run(provider, tool, max_rounds=20)

    tool_msg = next(m for m in messages if m.role == "tool")
    assert "boom" in (tool_msg.content or "")  # error 摘要保留
    assert "execvpe(/bin/bash) failed" in (tool_msg.content or "")  # 诊断细节被回显


async def test_max_rounds_exhaustion_forces_nonempty_answer():
    # Distinct args each round → governance never trips; the loop exhausts its
    # budget mid-tool-call. Successful stub tool output → salvage inventory →
    # soft+hard LLM salvage (script exhausted → content stays '').
    provider = _ScriptedProvider(
        [
            [_tool_chunk("search", '{"q": "a"}')],
            [_tool_chunk("search", '{"q": "b"}')],
            [_tool_chunk("search", '{"q": "c"}')],
        ]
    )
    tool = _StubTool()
    (content, _r, _usage, rounds), _messages = await _run(provider, tool, max_rounds=3)

    assert content == ""
    assert rounds == 3  # reported as the cap → pipeline surfaces MAX_ROUNDS
    assert tool.calls == 3
    assert provider.calls == 5  # 3 scripted + soft + hard salvage


async def test_clean_answer_has_no_governance_injection():
    # A normal tool-then-answer turn must not inject any governance messages.
    provider = _ScriptedProvider([[_tool_chunk("search", '{"q": "x"}')], [_content_chunk("done")]])
    tool = _StubTool()
    (content, *_), messages = await _run(provider, tool, max_rounds=20)

    assert content == "done"
    assert tool.calls == 1
    assert not any(m.content and "[系统提示]" in m.content for m in messages if m.content)


async def test_research_tool_citations_collected_into_sink():
    # A successful research tool's citations land in the caller's sink.
    cites = [{"url": "https://a.com", "title": "A", "snippet": "s", "site": "a.com"}]
    provider = _ScriptedProvider([[_tool_chunk("search", '{"q": "x"}')], [_content_chunk("done")]])
    sink_list: list[dict] = []
    (content, *_), _ = await _run(
        provider, _StubTool(citations=cites), max_rounds=20, citation_sink=sink_list
    )

    assert content == "done"
    assert sink_list == [{**cites[0], "tier": "unknown"}]


async def test_terminal_tool_citations_collected_before_handoff():
    # A handoff (terminal) tool returns early, but its citations must still be
    # merged into the sink first — the multi-agent → chat-turn source path.
    cites = [{"url": "https://t.com", "title": "T", "snippet": "", "site": "t.com"}]
    provider = _ScriptedProvider([[_tool_chunk("assemble", "{}")]])
    sink_list: list[dict] = []
    (content, *_), _ = await _run(
        provider,
        _StubTool(name="assemble", citations=cites, terminal=True),
        max_rounds=20,
        citation_sink=sink_list,
    )

    assert content == "streamed answer"  # final_text surfaced as the reply
    assert sink_list == [{**cites[0], "tier": "unknown"}]


async def test_citation_numbers_injected_into_tool_output():
    # A2: the engine annotates the tool's model-facing output with the canonical
    # numbers it assigned each source, so the model cites by a card-aligned number
    # instead of guessing one.
    cites = [
        {"url": "https://a.com", "title": "A", "snippet": "", "site": "a.com"},
        {"url": "https://b.com", "title": "B", "snippet": "", "site": "b.com"},
    ]
    provider = _ScriptedProvider([[_tool_chunk("search", '{"q": "x"}')], [_content_chunk("done")]])
    sink_list: list[dict] = []
    (content, *_), messages = await _run(
        provider, _StubTool(citations=cites), max_rounds=20, citation_sink=sink_list
    )

    assert content == "done"
    # cards aggregated in arrival order
    assert [c["url"] for c in sink_list] == ["https://a.com", "https://b.com"]
    # the tool message now carries the source→number annotation (number == card)
    tool_msg = next(m for m in messages if m.role == "tool")
    assert "[来源编号]" in (tool_msg.content or "")
    assert "[1]=https://a.com" in tool_msg.content
    assert "[2]=https://b.com" in tool_msg.content


async def test_citation_numbers_stable_across_rounds_with_dedup():
    # Round 1 surfaces A,B; round 2 re-surfaces B (dedup) and adds C. B's card
    # must keep number 2 and C must get the next free number (3) — and each
    # round's annotation tells the model exactly that, so multi-search + dedup
    # never drifts the body [n] ↔ card mapping.
    round1 = [
        {"url": "https://a.com", "title": "A", "snippet": "", "site": "a.com"},
        {"url": "https://b.com", "title": "B", "snippet": "", "site": "b.com"},
    ]
    round2 = [
        {"url": "https://b.com/#x", "title": "B again", "snippet": "", "site": "b.com"},
        {"url": "https://c.com", "title": "C", "snippet": "", "site": "c.com"},
    ]
    provider = _ScriptedProvider(
        [
            [_tool_chunk("search", '{"q": "1"}', call_id="c1")],
            [_tool_chunk("search", '{"q": "2"}', call_id="c2")],
            [_content_chunk("done")],
        ]
    )
    sink_list: list[dict] = []
    (content, *_), messages = await _run(
        provider,
        _StubTool(citation_script=[round1, round2]),
        max_rounds=20,
        citation_sink=sink_list,
    )

    assert content == "done"
    # dedup: B appears once; cards are A,B,C in arrival order
    assert [c["url"] for c in sink_list] == [
        "https://a.com",
        "https://b.com",
        "https://c.com",
    ]
    tool_msgs = [m for m in messages if m.role == "tool"]
    assert len(tool_msgs) == 2
    # round 1 annotation: A=1, B=2
    assert "[1]=https://a.com" in (tool_msgs[0].content or "")
    assert "[2]=https://b.com" in tool_msgs[0].content
    # round 2 annotation: B reuses 2 (dedup), C gets 3 — numbers stay card-aligned
    assert "[2]=https://b.com/#x" in (tool_msgs[1].content or "")
    assert "[3]=https://c.com" in tool_msgs[1].content


async def test_worker_path_collects_citations_without_annotating():
    # 无回合台账时：worker annotate_citations=False 仍只收集、不注入旧 [n]
    # （兼容路径）。接通 turn_evidence_ledger 后的 #rN 注解见 test_turn_evidence_ledger。
    cites = [
        {"url": "https://a.com", "title": "A", "snippet": "", "site": "a.com"},
        {"url": "https://b.com", "title": "B", "snippet": "", "site": "b.com"},
    ]
    provider = _ScriptedProvider([[_tool_chunk("search", '{"q": "x"}')], [_content_chunk("done")]])
    sink_list: list[dict] = []
    (content, *_), messages = await _run(
        provider,
        _StubTool(citations=cites),
        max_rounds=20,
        citation_sink=sink_list,
        annotate_citations=False,
    )

    assert content == "done"
    # collected for the shared card, in arrival order
    assert [c["url"] for c in sink_list] == ["https://a.com", "https://b.com"]
    # but the worker's tool message is NOT annotated with [n]=url numbers
    tool_msg = next(m for m in messages if m.role == "tool")
    assert tool_msg.content == "result"
    assert "[来源编号]" not in (tool_msg.content or "")


# --- 交付正文只留最终交付、旁白入 journal (Fork-B: deliverable_only) --------------
#
# Every real run passes deliverable_only=True: prose written BEFORE a non-terminal tool
# call is process narration (a lead-in, or an acknowledgement of an injected [系统提示]
# steer) and is rolled back off the RETURNED content so the persisted product / next-turn
# history / CEO synthesis carry only the final deliverable. It is always journaled per
# round (旁白入 journal). Display discipline splits by channel架构:
#   * CEO captain (on_reset=None): narration STAYS in the separate process timeline
#     (透明可见); only messages.content (旁路 conformance) trims — no reset.
#   * worker/debater/revision (on_reset→run_output_reset, card replays from message_final,
#     a single display+data channel): the rollback ALSO emits run_output_reset so
#     直播==重载==deliverable (conformance invariant).


async def test_deliverable_only_drops_pre_tool_narration():
    # round0 写旁白 + 调非终止工具 → round1 给最终答案：交付正文只留最终答案。
    provider = _ScriptedProvider(
        [
            [_content_chunk("我先查一下资料。"), _tool_chunk("search", '{"q": "x"}')],
            [_content_chunk("最终结论：一二三。")],
        ]
    )
    (content, *_), _messages = await _run(
        provider, _StubTool(), max_rounds=20, deliverable_only=True
    )
    assert content == "最终结论：一二三。"
    assert "我先查一下" not in content


async def test_default_keeps_pre_tool_narration():
    # 默认 / worker 路径（deliverable_only=False）逐字不变：旁白仍并入返回正文，
    # 保证 message_final 的重放合成（reload → run_output_delta）与直播不失真。
    provider = _ScriptedProvider(
        [
            [_content_chunk("我先查一下资料。"), _tool_chunk("search", '{"q": "x"}')],
            [_content_chunk("最终结论：一二三。")],
        ]
    )
    (content, *_), _messages = await _run(provider, _StubTool(), max_rounds=20)
    assert "我先查一下资料。" in content
    assert "最终结论：一二三。" in content


async def test_deliverable_only_keeps_terminal_checkpoint_content():
    # 终止工具（ask_user / handoff / suspend）之前的正文是该边界的交付（如提问前的说明），
    # 即便 deliverable_only 也要保留——只回退「非终止工具轮」的前置旁白。
    provider = _ScriptedProvider(
        [[_content_chunk("我来帮你，先确认一点："), _tool_chunk("assemble", "{}")]]
    )
    (content, *_), _messages = await _run(
        provider,
        _StubTool(name="assemble", terminal=True),
        max_rounds=20,
        deliverable_only=True,
    )
    assert "我来帮你，先确认一点：" in content  # 前置说明保留
    assert "streamed answer" in content  # terminal 的 final_text 追加在其后


async def test_deliverable_only_drops_steer_acknowledgement_after_rework():
    # 真实事故复现：finish_guard 以 role=user 注入纠错 → 模型回「谢谢指正，我重新整理」
    # 后接着调工具。那句寒暄是「非终止工具轮」的前置旁白，deliverable_only 下必须回退，
    # 不得进入 message.content；最终只留修正后的答案。（Fork-A 让模型少说这句；Fork-B 是
    # 结构兜底：即便说了，也不落进交付正文。）
    provider = _ScriptedProvider(
        [
            [_content_chunk("我先查一下。"), _tool_chunk("search", '{"q": "a"}', call_id="c0")],
            [_content_chunk("结论见 [1]。")],  # 0 来源 → 越界角标 → finish_guard 回炉
            [
                _content_chunk("谢谢指正，我重新整理。"),
                _tool_chunk("search", '{"q": "b"}', call_id="c1"),
            ],
            [_content_chunk("修正后的最终结论，无来源角标。")],
        ]
    )
    (content, *_), messages = await _run(
        provider, _StubTool(), max_rounds=20, citation_sink=[], deliverable_only=True
    )
    assert content == "修正后的最终结论，无来源角标。"
    assert "谢谢指正" not in content
    assert "我先查一下" not in content
    # 证明确实走了回炉路径（注入过一条 finish_guard 纠错 steer）。
    steers = [m for m in messages if m.role == "user" and m.content and "核验未通过" in m.content]
    assert len(steers) == 1


async def test_worker_deliverable_only_resets_card_on_narration_rollback():
    # worker 路径（on_reset→run_output_reset，卡片=数据同一通道）：写旁白 + 调非终止工具 →
    # 回退交付正文时【必须】发一次 reset 清掉卡片已流式草稿，使 直播==重载(合成自 message_final)。
    resets: list[str] = []
    provider = _ScriptedProvider(
        [
            [_content_chunk("我先查一下资料。"), _tool_chunk("search", '{"q": "x"}')],
            [_content_chunk("最终结论：一二三。")],
        ]
    )
    (content, *_), _messages = await _run(
        provider,
        _StubTool(),
        max_rounds=20,
        deliverable_only=True,
        on_reset=resets.append,
    )
    assert content == "最终结论：一二三。"
    assert "我先查一下" not in content
    # 恰好清一次卡片（那一轮旁白），reason=narration（正常流程，不折 rework chip）。
    assert resets == ["narration"]


async def test_captain_deliverable_only_keeps_timeline_no_reset():
    # CEO 路径（on_reset=None，正文与过程时间线是两条通道）：同样回退交付正文，但【不】发 reset
    # ——旁白仍以 content_delta 流在过程时间线里透明可见，只有持久化正文被裁。与 worker 对偶互补。
    provider = _ScriptedProvider(
        [
            [_content_chunk("我先查一下资料。"), _tool_chunk("search", '{"q": "x"}')],
            [_content_chunk("最终结论：一二三。")],
        ]
    )
    sink = _RecordingSink()
    content, _r, _u, _rounds = await react_loop(
        messages=[LLMMessage(role="user", content="go")],
        llm=provider,
        tools=_registry(_StubTool()),
        sink=sink,
        tool_context=_context(),
        profile=make_profile_params(max_rounds=20),
        turn_model="m",
        deliverable_only=True,
        approval_gate=None,
    )
    assert content == "最终结论：一二三。"
    # 关键：CEO 的旁白回退【不】发 content_reset（旁白留在时间线；仅数据通道 messages.content 被裁）。
    assert [e for e in sink.emitted if e.type == EventType.CONTENT_RESET] == []
    # 旁白确实以 content_delta 直播过（时间线可见）。
    assert any(
        e.type == EventType.CONTENT_DELTA and "我先查一下" in (e.payload.get("delta") or "")
        for e in sink.emitted
    )


async def test_captain_coordination_wait_aside_not_in_deliverable():
    """协调态进度旁白 + wait：deliverable_only 裁掉终稿 content，旁白仍进 process 流。

    证明路径：content_delta 直播过程旁白；非终止 wait 后回退，最终 messages.content
    只留交付段（阶段结论）。
    """
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )
    from agentcore.runtime.coordination.tools import WaitTool

    session = CoordinationSession(execution_id="e", total_workers=2)
    set_active_coordination(session)
    try:
        provider = _ScriptedProvider(
            [
                [
                    _content_chunk("研究员还在检索，写手待启动。"),
                    _tool_chunk("wait", '{"reason": "no disposition"}'),
                ],
                [_content_chunk("阶段结论：调研已齐，开始合成。")],
            ]
        )
        sink = _RecordingSink()
        reg = ToolRegistry()
        reg.register(WaitTool())
        content, _r, _u, _rounds = await react_loop(
            messages=[LLMMessage(role="user", content="go")],
            llm=provider,
            tools=reg,
            sink=sink,
            tool_context=_context(),
            profile=make_profile_params(max_rounds=20),
            turn_model="m",
            deliverable_only=True,
            approval_gate=None,
        )
        assert content == "阶段结论：调研已齐，开始合成。"
        assert "还在检索" not in content
        assert [e for e in sink.emitted if e.type == EventType.CONTENT_RESET] == []
        assert any(
            e.type == EventType.CONTENT_DELTA and "还在检索" in (e.payload.get("delta") or "")
            for e in sink.emitted
        )
    finally:
        clear_active_coordination()


async def test_attached_inject_closing_round_keeps_body_despite_successful_tool():
    """收口轮正文是交付物：同轮成功的非终端工具不得 narration 回滚。

    Wait 已吃 ALL_COMPLETED（session 关闭、settled_via=attached_inject）后，
    CEO 写出终稿并调仍返回 success 的 ``update_synthesis``。闸若仍把正文当旁白，
    persist 被裁空、harvest skip 会变成零终稿。修闸后：正文保留、harvest 跳过、
    不开第二条收口消息。
    """
    from unittest.mock import AsyncMock, patch

    from structlog.testing import capture_logs

    import agentcore.runtime.coordination.session as session_mod
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        bind_host_journal,
        clear_active_coordination,
        finish_detached_coordination,
        set_active_coordination,
    )
    from agentcore.runtime.coordination.tools import UpdateSynthesisTool

    class _HarvestJournalWriter:
        def __init__(self) -> None:
            self.entries: list[dict] = []

        def schedule_append(self, entry: dict):
            self.entries.append(entry)
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[int | None] = loop.create_future()
            fut.set_result(len(self.entries))
            return fut

        async def flush(self) -> None:
            return None

    eid = "exec-close-round-keep"
    writer = _HarvestJournalWriter()
    session = CoordinationSession(
        execution_id=eid,
        total_workers=1,
        conversation_id="conv-close-round-keep",
    )
    session.turn_attached = True
    session.all_completed_injected = True
    session.mark_settled("attached_inject")
    session.close()
    bind_host_journal(session, writer=writer)
    set_active_coordination(session)
    sink = _RecordingSink()
    tools = ToolRegistry()
    tools.register(UpdateSynthesisTool(sink=sink))
    try:
        provider = _ScriptedProvider(
            [
                [
                    _content_chunk("交付终稿：团队结论如下。"),
                    _tool_chunk("update_synthesis", '{"draft": "不应覆盖终稿"}'),
                ]
            ]
        )
        content, _r, _u, _rounds = await react_loop(
            messages=[LLMMessage(role="user", content="go")],
            llm=provider,
            tools=tools,
            sink=sink,
            tool_context=ToolContext.create(
                execution_id=eid,
                run_id="s",
                agent_id="a",
                backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
                user_id="u",
            ),
            profile=make_profile_params(max_rounds=20),
            turn_model="m",
            role="captain",
            deliverable_only=True,
            approval_gate=None,
        )
        assert content == "交付终稿：团队结论如下。"
        assert session.attached_inject_visible_close is True
        assert [e for e in sink.emitted if e.type == EventType.CONTENT_RESET] == []

        with (
            patch.object(session_mod, "_HARVEST_ATTACH_GRACE_S", 2.0),
            patch.object(session_mod, "_HARVEST_ATTACH_POLL_S", 0.02),
            patch(
                "agentcore.runtime.coordination.harvest.harvest_detached_execution",
                new_callable=AsyncMock,
            ) as harvest,
            capture_logs() as logs,
        ):
            finish_detached_coordination(session)
            assert session.harvest_scheduled is True
            harvest.assert_not_awaited()
            session.turn_attached = False
            await asyncio.sleep(0.1)
            harvest.assert_not_awaited()
            assert session.harvest_scheduled is False
            assert session.settled_via == "attached_inject"
            assert any(
                e.get("event") == "coordination.harvest_skipped_attached_visible_close"
                for e in logs
            )
            assert any(e.get("kind") == "execution_completed" for e in writer.entries)
            assert session_mod.active_coordination(eid) is None
    finally:
        clear_active_coordination()


async def test_deliverable_only_keeps_prose_when_all_tools_fail():
    """Failed non-terminal tool must not silently drop already-streamed body.

    Repro shape (debate 复测): CEO streams 案件简介 + debate(stance 超限) → fail →
    reflection → retry. deliverable_only used to roll back the brief on the failed
    round, so pause/finalize only kept the short retry line.
    """
    provider = _ScriptedProvider(
        [
            [
                _content_chunk("【案件简介】LV 诉茉莉奶白商标案要点……"),
                _tool_chunk("search", '{"q": "x"}', call_id="c0"),
            ],
            [_content_chunk("立场字数超限，修正后重新启动庭审：")],
        ]
    )
    (content, *_), _messages = await _run(
        provider,
        _StubTool(success=False, fail_output="stance 过长"),
        max_rounds=20,
        deliverable_only=True,
    )
    assert "【案件简介】LV 诉茉莉奶白商标案要点……" in content
    assert "立场字数超限，修正后重新启动庭审：" in content


async def test_worker_deliverable_only_no_reset_when_no_pre_tool_content():
    # worker 只在末轮给产出、工具轮无正文（既有向量的常态）→ 无旁白可回退 → 不发 reset，
    # 逐字等价今日行为（保证既有 multi_agent 向量不被这次改动改动）。
    resets: list[str] = []
    provider = _ScriptedProvider(
        [
            [_tool_chunk("search", '{"q": "x"}')],  # 工具轮无正文
            [_content_chunk("成稿")],
        ]
    )
    (content, *_), _messages = await _run(
        provider,
        _StubTool(),
        max_rounds=20,
        deliverable_only=True,
        on_reset=resets.append,
    )
    assert content == "成稿"
    assert resets == []  # 没有旁白 → 不清卡片


# --- ReactLoopOut.usage: partial usage survives a mid-loop raise (B-deep 失败计费) ----


class _MeterThenBoom:
    """Round 0 meters usage (a tool call so the loop continues + a usage chunk);
    round 1 raises. With raise_on_error=True the loop re-raises — but the round
    that completed must still be readable via ``out.usage`` so the caller can bill it."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        c = self.calls
        self.calls += 1
        if c == 0:
            yield _tool_chunk("search", "{}")
            yield LLMChunk(
                usage=TokenUsage(input_tokens=1000, cache_miss_tokens=1000, output_tokens=400)
            )
            return
        raise RuntimeError("provider down")
        yield  # pragma: no cover - makes this an async generator


async def test_usage_sink_holds_completed_round_usage_on_raise():
    sink_usage: list[TokenUsage] = []
    profile = make_profile_params(max_rounds=20)
    with pytest.raises(RuntimeError, match="provider down"):
        await react_loop(
            messages=[LLMMessage(role="user", content="go")],
            llm=_MeterThenBoom(),
            tools=_registry(_StubTool()),
            sink=EventSink(),
            tool_context=_context(),
            profile=profile,
            turn_model="m",
            raise_on_error=True,
            out=ReactLoopOut(usage=sink_usage),
            approval_gate=None,
        )
    # The round that completed before the crash is mirrored for the caller to bill.
    assert len(sink_usage) == 1
    assert sink_usage[0].cache_miss_tokens == 1000
    assert sink_usage[0].output_tokens == 400


async def test_usage_sink_empty_when_first_round_raises():
    # Nothing metered before the crash → the mirror stays empty, so the caller bills
    # nothing (no spurious zero-usage ledger row).
    class _BoomFirst:
        async def stream(self, request):  # noqa: ANN001
            raise RuntimeError("down")
            yield  # pragma: no cover

    sink_usage: list[TokenUsage] = []
    profile = make_profile_params(max_rounds=20)
    with pytest.raises(RuntimeError, match="down"):
        await react_loop(
            messages=[LLMMessage(role="user", content="go")],
            llm=_BoomFirst(),
            tools=_registry(_StubTool()),
            sink=EventSink(),
            tool_context=_context(),
            profile=profile,
            turn_model="m",
            raise_on_error=True,
            out=ReactLoopOut(usage=sink_usage),
            approval_gate=None,
        )
    assert sink_usage == []


# --- B1: engine-level tool timeout backstop ----------------------------------


def _schema(category: ToolCategory, timeout: float | None = None) -> ToolSchema:
    return ToolSchema(
        name="t",
        description="d",
        parameters={"type": "object", "properties": {}},
        category=category,
        timeout_seconds=timeout,
    )


def test_resolve_tool_timeout_by_category():
    # The exemption policy is the part most likely to silently regress and break a
    # legitimate long wait (delegate's sub-DAG / ask_user's user round-trip), so pin it.
    assert resolve_tool_timeout(_schema(ToolCategory.ORCHESTRATION)) is None
    assert resolve_tool_timeout(_schema(ToolCategory.INTERACTION)) is None
    # execution runs code → higher ceiling; everything else → the flat default
    assert (
        resolve_tool_timeout(_schema(ToolCategory.EXECUTION))
        == settings.tool_execution_timeout_seconds
    )
    assert (
        resolve_tool_timeout(_schema(ToolCategory.SEARCH)) == settings.tool_default_timeout_seconds
    )
    assert (
        resolve_tool_timeout(_schema(ToolCategory.FILESYSTEM))
        == settings.tool_default_timeout_seconds
    )
    # an explicit per-tool override wins over the category rule — even the exemption
    assert resolve_tool_timeout(_schema(ToolCategory.ORCHESTRATION, 12.5)) == 12.5
    assert resolve_tool_timeout(_schema(ToolCategory.EXECUTION, 5.0)) == 5.0


class _SlowTool:
    """A tool that sleeps well past its declared ceiling, to trip the engine timeout."""

    def __init__(self, *, delay: float, timeout_seconds: float | None) -> None:
        self._delay = delay
        self._timeout_seconds = timeout_seconds
        self.completed = False

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="slow",
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.SEARCH,
            timeout_seconds=self._timeout_seconds,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        await asyncio.sleep(self._delay)
        self.completed = True  # only reached if the timeout did NOT fire
        return ToolResult(tool_call_id="", success=True, output="late result")


async def test_tool_timeout_aborts_and_loop_recovers():
    # A tool that blows its (tiny) ceiling is aborted by the engine: the model gets a
    # timeout tool result it can adapt to, and the turn reaches an answer instead of
    # hanging on the wedged call. The tool's own body is cancelled (never completes).
    provider = _ScriptedProvider([[_tool_chunk("slow", "{}")], [_content_chunk("recovered")]])
    tool = _SlowTool(delay=5.0, timeout_seconds=0.05)
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    profile = make_profile_params(max_rounds=20)
    content, _r, _u, _rounds = await react_loop(
        messages=messages,
        llm=provider,
        tools=_registry(tool),
        sink=EventSink(),
        tool_context=_context(),
        profile=profile,
        turn_model="m",
        approval_gate=None,
    )

    assert content == "recovered"
    assert tool.completed is False  # the sleep was cancelled, not awaited to the end
    tool_msg = next(m for m in messages if m.role == "tool")
    assert "活性挂起" in (tool_msg.content or "")
    assert "中止" in (tool_msg.content or "")  # the model saw an honest timeout error
    assert "禁止原样重试" in (tool_msg.content or "")


# --- B2: empty-response degraded (same-model retry) -----------------------------


class _ModelRecordingProvider:
    """Scripted provider that also records each request's model."""

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0
        self.models: list[str] = []

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        self.models.append(request.model)
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk


async def _run_loop(  # noqa: ANN001
    provider,
    profile,
    *,
    finish_override_sink=None,
    tool=None,
    turn_model: str = "primary",
):
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    return await react_loop(
        messages=messages,
        llm=provider,
        tools=_registry(tool or _StubTool()),
        sink=EventSink(),
        tool_context=_context(),
        profile=profile,
        turn_model=turn_model,
        out=(
            None
            if finish_override_sink is None
            else ReactLoopOut(finish_override=finish_override_sink)
        ),
        approval_gate=None,
    )


async def test_empty_response_retries_same_model_and_recovers():
    # Round 0 is empty (no content, no tool) → the engine retries round 1 on the same
    # model, which answers. The turn finishes clean (not degraded).
    provider = _ModelRecordingProvider([[], [_content_chunk("recovered")]])
    profile = make_profile_params(max_rounds=20)
    finish_override: list[FinishReason] = []
    content, _r, _u, _rounds = await _run_loop(
        provider, profile, finish_override_sink=finish_override
    )

    assert content == "recovered"
    assert provider.models == ["primary", "primary"]
    assert finish_override == []  # recovered → not degraded


async def test_length_empty_degrades_immediately_without_continue():
    """finish_reason=length + empty + no tools → DEGRADED on round 0 (no Continue)."""
    provider = _ModelRecordingProvider([[LLMChunk(finish_reason="length")]])
    profile = make_profile_params(max_rounds=20)
    finish_override: list[FinishReason] = []
    sink = _RecordingSink()
    content, _r, _u, rounds = await react_loop(
        messages=[LLMMessage(role="user", content="go")],
        llm=provider,
        tools=_registry(_StubTool()),
        sink=sink,
        tool_context=_context(),
        profile=profile,
        turn_model="primary",
        out=ReactLoopOut(finish_override=finish_override),
        approval_gate=None,
    )

    assert content == ""
    assert rounds == 1
    assert provider.models == ["primary"]  # no second Continue round
    assert finish_override == [FinishReason.DEGRADED]
    errs = _errors(sink)
    assert errs
    assert "多次空响应" not in (errs[0].payload.get("message") or "")
    assert "截断" in (errs[0].payload.get("message") or "")


async def test_length_empty_not_exempted_for_captain_coordination(monkeypatch):
    """Active coordination listen exemption must NOT cover length+empty."""
    from types import SimpleNamespace

    async def _no_inject(messages):  # noqa: ANN001
        return []

    monkeypatch.setattr(
        "agentcore.runtime.coordination.wait.await_coordination_injection",
        _no_inject,
    )
    monkeypatch.setattr(
        "agentcore.runtime.coordination.session.active_coordination",
        lambda: SimpleNamespace(active=True, execution_id="e-len"),
    )
    provider = _ModelRecordingProvider([[LLMChunk(finish_reason="length")]])
    profile = make_profile_params(max_rounds=20)
    finish_override: list[FinishReason] = []
    content, _r, _u, rounds = await react_loop(
        messages=[LLMMessage(role="user", content="go")],
        llm=provider,
        tools=_registry(_StubTool()),
        sink=EventSink(),
        tool_context=_context(),
        profile=profile,
        turn_model="primary",
        out=ReactLoopOut(finish_override=finish_override),
        role="captain",
        run_id="cap",
        approval_gate=None,
    )
    assert content == ""
    assert rounds == 1
    assert finish_override == [FinishReason.DEGRADED]


async def test_length_with_partial_content_does_not_hard_cut_empty_ladder():
    """Non-empty truncated content is a normal answer path — not the empty gate."""
    provider = _ModelRecordingProvider([[LLMChunk(delta_content="半截", finish_reason="length")]])
    profile = make_profile_params(max_rounds=20)
    finish_override: list[FinishReason] = []
    content, _r, _u, rounds = await _run_loop(
        provider, profile, finish_override_sink=finish_override
    )

    assert content == "半截"
    assert rounds == 1
    assert finish_override == []


async def test_consecutive_empty_finishes_degraded():
    # Two consecutive empty rounds hit the threshold → degraded finish (no blank end_turn).
    provider = _ModelRecordingProvider([[], []])
    profile = make_profile_params(max_rounds=20)
    finish_override: list[FinishReason] = []
    content, _r, _u, _rounds = await _run_loop(
        provider, profile, finish_override_sink=finish_override
    )

    assert content == ""
    assert provider.models == ["primary", "primary"]
    assert finish_override == [FinishReason.DEGRADED]


async def test_empty_response_degrades_without_model_switch():
    # Same as consecutive empty: model is never switched; turn ends degraded.
    provider = _ModelRecordingProvider([[], []])
    profile = make_profile_params(max_rounds=20)
    finish_override: list[FinishReason] = []
    await _run_loop(provider, profile, finish_override_sink=finish_override)

    assert provider.models == ["primary", "primary"]
    assert finish_override == [FinishReason.DEGRADED]


# --- B2: LLM hard failure (no model escalation) -------------------------------


class _RecordingSink(EventSink):
    """EventSink that also keeps every emitted event for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.emitted: list[SSEEvent] = []

    def emit(self, event: SSEEvent) -> None:
        self.emitted.append(event)
        super().emit(event)


class _FailingProvider:
    """Records each request's model and raises on the rounds in ``fail_on`` (simulating
    an LLM call whose provider-level retries are already exhausted); otherwise streams
    the scripted chunks for that round."""

    def __init__(self, rounds: list[list[LLMChunk]], *, fail_on: set[int]) -> None:
        self._rounds = rounds
        self._fail_on = fail_on
        self.calls = 0
        self.models: list[str] = []

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        idx = self.calls
        self.models.append(request.model)
        self.calls += 1
        if idx in self._fail_on:
            raise RuntimeError("provider boom")
        chunks = self._rounds[idx] if idx < len(self._rounds) else []
        for chunk in chunks:
            yield chunk


def _errors(sink: _RecordingSink) -> list[SSEEvent]:
    return [e for e in sink.emitted if e.type == EventType.ERROR]


async def _run_with_sink(  # noqa: ANN001
    provider, profile, sink, *, turn_model: str = "primary"
):
    finish_override: list[FinishReason] = []
    content, _r, _u, rounds = await react_loop(
        messages=[LLMMessage(role="user", content="go")],
        llm=provider,
        tools=_registry(_StubTool()),
        sink=sink,
        tool_context=_context(),
        profile=profile,
        turn_model=turn_model,
        out=ReactLoopOut(finish_override=finish_override),
        approval_gate=None,
    )
    return content, rounds, finish_override


async def test_llm_failure_errors_without_model_escalation():
    # Hard LLM failure ends the turn immediately — no profile-level model escalation.
    provider = _FailingProvider([[], [_content_chunk("recovered")]], fail_on={0})
    profile = make_profile_params(max_rounds=20)
    sink = _RecordingSink()
    content, rounds, finish_override = await _run_with_sink(provider, profile, sink)

    assert content == ""
    assert rounds == 1
    assert provider.models == ["primary"]
    assert finish_override == [FinishReason.ERROR]
    assert len(_errors(sink)) == 1


async def test_llm_failure_after_first_round_errors():
    # Same as a single hard failure: the ladder does not retry on another model.
    provider = _FailingProvider([], fail_on={0, 1})
    profile = make_profile_params(max_rounds=20)
    sink = _RecordingSink()
    content, rounds, finish_override = await _run_with_sink(provider, profile, sink)

    assert content == ""
    assert rounds == 1
    assert provider.models == ["primary"]
    assert finish_override == [FinishReason.ERROR]
    assert len(_errors(sink)) == 1


async def test_llm_failure_with_partial_content_degrades():
    # Round 0 streams partial content + a tool call (loop continues); round 1 hard-fails
    # → the turn keeps the partial answer and finishes DEGRADED.
    provider = _FailingProvider(
        [[_content_chunk("partial"), _tool_chunk("search", "{}")], []],
        fail_on={1},
    )
    profile = make_profile_params(max_rounds=20)
    sink = _RecordingSink()
    content, rounds, finish_override = await _run_with_sink(provider, profile, sink)

    assert content == "partial"
    assert rounds == 2
    assert finish_override == [FinishReason.DEGRADED]
    assert len(_errors(sink)) == 1


class _AbortAfterContentProvider:
    """Streams content then signals aborted (post-commit disconnect salvage)."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        self.calls += 1
        yield _content_chunk(self._text)
        yield LLMChunk(aborted=True)


async def test_stream_aborted_keeps_partial_and_degrades():
    # Same-round content commit + aborted → DEGRADED with the partial kept (not empty ERROR).
    provider = _AbortAfterContentProvider("半成品正文")
    profile = make_profile_params(max_rounds=20)
    sink = _RecordingSink()
    content, rounds, finish_override = await _run_with_sink(provider, profile, sink)

    assert content == "半成品正文"
    assert rounds == 1
    assert provider.calls == 1
    assert finish_override == [FinishReason.DEGRADED]
    assert len(_errors(sink)) == 1


async def test_llm_failure_errors_immediately():
    # A first-round hard failure ends the turn on ERROR after a single attempt.
    provider = _FailingProvider([], fail_on={0})
    profile = make_profile_params(max_rounds=20)
    sink = _RecordingSink()
    content, rounds, finish_override = await _run_with_sink(provider, profile, sink)

    assert content == ""
    assert rounds == 1
    assert provider.models == ["primary"]  # no escalation attempted
    assert finish_override == [FinishReason.ERROR]
    assert len(_errors(sink)) == 1


# --- B2: tool failure circuit breaker + no-output early stop --------------------


class _ToolsRecordingProvider:
    """Scripted provider that records the tool names offered to it each round.

    Lets a test assert the circuit breaker actually removed a disabled tool from the
    toolset (request.tools) on the round after it tripped, not just that a steer was
    injected.
    """

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0
        self.offered: list[list[str]] = []

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        self.offered.append([t["function"]["name"] for t in (request.tools or [])])
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk


async def test_circuit_breaker_warns_then_disables_failing_tool():
    # `flaky` fails with DIFFERENT args every round (so fingerprint-keyed
    # REPEATED_FAILURE never trips) and the model writes content each round (so the
    # unproductive early-stop never trips) — isolating the cumulative circuit breaker:
    # warn at the 2nd failure, disable (remove from the toolset) at the 3rd.
    reg = ToolRegistry()
    reg.register(_StubTool(success=False, name="flaky"))
    reg.register(_StubTool(success=True, name="other"))
    provider = _ToolsRecordingProvider(
        [
            [_content_chunk("t0"), _tool_chunk("flaky", '{"q": "a"}')],
            [_content_chunk("t1"), _tool_chunk("flaky", '{"q": "b"}')],
            [_content_chunk("t2"), _tool_chunk("flaky", '{"q": "c"}')],
            [_content_chunk("done")],
        ]
    )
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    profile = make_profile_params(max_rounds=20)
    await react_loop(
        messages=messages,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        tool_context=_context(),
        profile=profile,
        turn_model="m",
        approval_gate=None,
    )

    steers = [m.content or "" for m in messages if m.role == "user"]
    assert any("请不要再以相同方式调用" in s for s in steers)  # warn at 2 failures
    assert any("停用" in s for s in steers)  # disable at 3 failures
    # the disabled tool is gone from the toolset offered on the round AFTER disable
    assert provider.offered[0] == ["flaky", "other"]
    assert provider.offered[-1] == ["other"]


async def test_read_url_disable_survives_react_loop_restart():
    """After CB disables read_url, a fresh react_loop with the same run_id must not
    re-offer it (stream-stall → Wave retry / contract write_pass).

    C2: web_search is stripped with read_url so deep-read death cannot reopen
    search thrash on Wave/contract retry.
    """
    from agentcore.tools.builtin.web._net import (
        clear_read_url_retired,
        is_read_url_retired,
    )

    run_id = "read-url-survive-restart"
    clear_read_url_retired(run_id)
    ctx = ToolContext.create(
        execution_id="e",
        run_id=run_id,
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )
    reg = ToolRegistry()
    reg.register(_StubTool(success=False, name="read_url"))
    reg.register(_StubTool(success=True, name="web_search"))
    reg.register(_StubTool(success=True, name="other"))
    provider = _ToolsRecordingProvider(
        [
            [_content_chunk("t0"), _tool_chunk("read_url", '{"url": "a"}')],
            [_content_chunk("t1"), _tool_chunk("read_url", '{"url": "b"}')],
            [_content_chunk("t2"), _tool_chunk("read_url", '{"url": "c"}')],
            [_content_chunk("done")],
        ]
    )
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    await react_loop(
        messages=messages,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        tool_context=ctx,
        profile=make_profile_params(max_rounds=20),
        turn_model="m",
        run_id=run_id,
        approval_gate=None,
    )
    assert is_read_url_retired(run_id)
    steers = [m.content or "" for m in messages if m.role == "user"]
    assert any("停用" in s and "read_url" in s for s in steers)
    assert any("收束继续 web_search" in s or "不要把继续检索当默认出路" in s for s in steers)
    assert "read_url" not in provider.offered[-1]
    assert "web_search" not in provider.offered[-1]

    # Fresh loop = Wave / contract retry: retirement latch keeps read_url + search off.
    provider2 = _ToolsRecordingProvider([[_content_chunk("done2")]])
    await react_loop(
        messages=[LLMMessage(role="user", content="retry")],
        llm=provider2,
        tools=reg,
        sink=EventSink(),
        tool_context=ctx,
        profile=make_profile_params(max_rounds=5),
        turn_model="m",
        run_id=run_id,
        approval_gate=None,
    )
    assert provider2.offered[0] == ["other"]
    clear_read_url_retired(run_id)


async def test_unproductive_rounds_early_stop_and_salvage_answer():
    # Every round: one tool call that FAILS (varied args → not a repeated pattern) and
    # no content. After the unproductive threshold (3) consecutive such rounds the loop
    # early-stops. Empty inventory → skip LLM salvage; still surfaces UNPRODUCTIVE.
    flaky = _StubTool(success=False, name="flaky")
    provider = _ScriptedProvider(
        [
            [_tool_chunk("flaky", '{"q": "a"}')],
            [_tool_chunk("flaky", '{"q": "b"}')],
            [_tool_chunk("flaky", '{"q": "c"}')],
        ]
    )
    profile = make_profile_params(max_rounds=20)
    finish_override: list[FinishReason] = []
    content, _r, _u, rounds = await _run_loop(
        provider, profile, finish_override_sink=finish_override, tool=flaky
    )

    assert content == ""
    assert rounds == 3  # stopped at the 3rd unproductive round, before the cap
    assert provider.calls == 3  # no salvage LLM round on empty inventory
    assert finish_override == [FinishReason.UNPRODUCTIVE]


async def test_productive_round_resets_unproductive_streak():
    # A round that produces content (even alongside a failing tool) breaks the streak,
    # so an intermittent failure run is NOT early-stopped as unproductive.
    flaky = _StubTool(success=False, name="flaky")
    provider = _ScriptedProvider(
        [
            [_tool_chunk("flaky", '{"q": "a"}')],  # unproductive (fail, no content)
            [_content_chunk("progress"), _tool_chunk("flaky", '{"q": "b"}')],  # resets
            [_tool_chunk("flaky", '{"q": "c"}')],  # streak restarts at 1
            [_content_chunk("final")],
        ]
    )
    profile = make_profile_params(max_rounds=20)
    finish_override: list[FinishReason] = []
    content, _r, _u, _rounds = await _run_loop(
        provider, profile, finish_override_sink=finish_override, tool=flaky
    )

    # reached the model's own answer — never early-stopped
    assert "final" in content
    assert finish_override == []


# --- investigation-round finalize retired (engine wiring) ----------------
#
# Factory always passes convergence_finalize_rounds=0 (settings cannot revive it).
# Different-target reads do not force-finalize; same-target spin still does
# (covered by the repeated-call tests above). Soft nudge stays gone.


def _read_then_answer(reads: int) -> _ScriptedProvider:
    # Distinct args so spin / repeated-call never trip. Trailing tool-free answer
    # is the model's own wrap-up (not salvage).
    rounds: list[list[LLMChunk]] = [
        [_tool_chunk("file_read", '{"p": "%d"}' % i)] for i in range(reads)
    ]
    rounds.append([_content_chunk("done")])
    return _ScriptedProvider(rounds)


async def _run_with_registry(provider: _ScriptedProvider, reg: ToolRegistry):
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    profile = make_profile_params(max_rounds=20)
    content, *_ = await react_loop(
        messages=messages,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        tool_context=_context(),
        profile=profile,
        turn_model="m",
        approval_gate=None,
    )
    return content, messages


def _finalizes(messages: list[LLMMessage]) -> list[LLMMessage]:
    return [
        m
        for m in messages
        if m.role == "user" and m.content and "停止使用调查与执行类工具" in m.content
    ]


def _convergence_steers(messages: list[LLMMessage]) -> list[LLMMessage]:
    # Any over-investigation steer at all (the removed soft nudge used to live here).
    return [m for m in messages if m.role == "user" and m.content and "[系统提示]" in m.content]


async def test_few_different_target_reads_have_no_convergence_steer():
    # A short different-target read run that answers itself: no soft nudge, no finalize.
    reg = ToolRegistry()
    reg.register(_StubTool(name="file_read"))  # investigation (SEARCH + NEVER approval)
    content, messages = await _run_with_registry(_read_then_answer(3), reg)

    assert content == "done"
    assert _finalizes(messages) == []
    assert _convergence_steers(messages) == []


async def test_many_different_target_reads_do_not_force_finalize(monkeypatch):
    # Settings >0 must not revive investigation-round finalize. Many distinct
    # file_read targets still reach the model's own answer; no 收工 prompt.
    monkeypatch.setattr(settings, "engine_convergence_finalize_rounds", 6)
    reg = ToolRegistry()
    reg.register(_StubTool(name="file_read"))
    provider = _read_then_answer(12)
    content, messages = await _run_with_registry(provider, reg)

    assert content == "done"
    assert provider.calls == 13  # 12 reads + model's own answer; no salvage extra call
    assert _finalizes(messages) == []


async def test_many_different_target_reads_do_not_force_finalize_with_delegate(monkeypatch):
    # Same contract with an ORCHESTRATION tool present — no flavor-specific
    # round-count finalize either.
    monkeypatch.setattr(settings, "engine_convergence_finalize_rounds", 6)
    reg = ToolRegistry()
    reg.register(_StubTool(name="file_read"))
    reg.register(_StubTool(name="delegate", category=ToolCategory.ORCHESTRATION))
    provider = _read_then_answer(12)
    content, messages = await _run_with_registry(provider, reg)

    assert content == "done"
    assert provider.calls == 13
    assert _finalizes(messages) == []


async def test_worker_react_loop_emits_tools_offered_once(monkeypatch):
    """COST-004: worker 开口发 cost.tools_offered scope=worker_run；captain 不重复。"""
    from agentcore.runtime.resolve import ceo_surface

    captured: list[str] = []
    real = ceo_surface.observe_tools_offered

    def _spy(tools, *, scope, tool_defs=None):
        captured.append(scope)
        return real(tools, scope=scope, tool_defs=tool_defs)

    monkeypatch.setattr(ceo_surface, "observe_tools_offered", _spy)
    messages = [LLMMessage(role="user", content="go")]
    profile = make_profile_params(max_rounds=2)
    await react_loop(
        messages=messages,
        llm=_ScriptedProvider([[_content_chunk("ok")]]),
        tools=_registry(_StubTool()),
        sink=EventSink(),
        tool_context=_context(),
        profile=profile,
        turn_model="m",
        role="worker",
        approval_gate=None,
    )
    assert captured.count("worker_run") == 1

    captured.clear()
    await react_loop(
        messages=[LLMMessage(role="user", content="go")],
        llm=_ScriptedProvider([[_content_chunk("ok")]]),
        tools=_registry(_StubTool()),
        sink=EventSink(),
        tool_context=_context(),
        profile=profile,
        turn_model="m",
        role="captain",
        approval_gate=None,
    )
    assert "worker_run" not in captured


def _openai_tool_names(request) -> list[str]:  # noqa: ANN001
    names: list[str] = []
    for item in request.tools or []:
        if isinstance(item, dict):
            fn = item.get("function") or {}
            if isinstance(fn, dict) and fn.get("name"):
                names.append(str(fn["name"]))
    return names


@pytest.mark.parametrize("supervised,expect_replan", [(False, False), (True, True)])
async def test_worker_nested_lead_replan_follows_supervised(supervised: bool, expect_replan: bool):
    """嵌套 lead：无子计划时开口无 replan；续跑已有 _supervised 时首轮 LLM 已挂上。"""

    class _Rec(_ScriptedProvider):
        def __init__(self) -> None:
            super().__init__([[_content_chunk("ok")]])
            self.names: list[list[str]] = []

        async def stream(self, request):  # noqa: ANN001
            self.names.append(_openai_tool_names(request))
            async for chunk in super().stream(request):
                yield chunk

    delegate = _StubTool(name="delegate", category=ToolCategory.ORCHESTRATION)
    delegate._supervised = object() if supervised else None
    delegate._depth = 1
    delegate._sink = None
    reg = ToolRegistry()
    reg.register(delegate)
    provider = _Rec()
    profile = make_profile_params(max_rounds=2)
    await react_loop(
        messages=[LLMMessage(role="user", content="go")],
        llm=provider,
        tools=reg,
        sink=EventSink(),
        tool_context=_context(),
        profile=profile,
        turn_model="m",
        role="worker",
        approval_gate=None,
    )
    assert provider.names, "expected at least one LLM request"
    opening = provider.names[0]
    assert "delegate" in opening
    if expect_replan:
        assert "replan" in opening
        assert "wait" not in opening
    else:
        assert "replan" not in opening
