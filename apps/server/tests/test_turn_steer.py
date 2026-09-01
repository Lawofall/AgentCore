"""Classic turn steer (同对话再发 P1) — queue + react_loop step-boundary inject."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.runtime.engine import react_loop
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.turn.queue import new_queued_turn, turn_queue
from agentcore.runtime.turn.runs import turn_runs
from agentcore.runtime.turn.steer import (
    _USER_STOP_DISCARD_NOTE,
    _reset_for_tests,
    begin_accepting,
    discard_leftovers_on_user_stop,
    drain_as_messages,
    end_accepting,
    format_steer_user_message,
    peek_count,
    promote_leftovers_to_queue,
    try_enqueue,
)
from tests.llm_helpers import make_profile_params
from tests.test_captain_loop_seed import _context, _registry, _ScriptedProvider, _StubTool


def test_format_steer_marks_mid_turn_supplement():
    text = format_steer_user_message("改用中文")
    assert "中途补充" in text
    assert "继续完成当前任务" in text
    assert "改用中文" in text


def test_format_steer_includes_attachment_inventory():
    text = format_steer_user_message(
        "对照这份表",
        [
            {
                "name": "成本表.xlsx",
                "workspace_path": "attachments/成本表.xlsx",
                "binary": True,
            },
            {"name": "notes.md", "workspace_path": "attachments/notes.md"},
        ],
    )
    assert "对照这份表" in text
    assert "附件：成本表.xlsx → attachments/成本表.xlsx（二进制）" in text
    assert "附件：notes.md → attachments/notes.md" in text
    assert "（二进制）" not in text.split("notes.md")[-1]


def test_drain_as_messages_surfaces_attachments():
    _reset_for_tests()
    begin_accepting("c-att")
    assert (
        try_enqueue(
            conversation_id="c-att",
            content="看附件",
            attachments=[{"name": "a.pdf", "workspace_path": "attachments/a.pdf", "binary": True}],
        )
        is not None
    )
    msgs = drain_as_messages("c-att")
    assert len(msgs) == 1
    body = msgs[0].content or ""
    assert "看附件" in body
    assert "附件：a.pdf → attachments/a.pdf（二进制）" in body
    end_accepting("c-att")
    _reset_for_tests()


def test_drain_as_messages_surfaces_agent_mentions():
    _reset_for_tests()
    begin_accepting("c-mention")
    assert (
        try_enqueue(
            conversation_id="c-mention",
            content="让写手收紧口径",
            agent_mentions=[{"agent_id": "agent_writer", "role": "写手"}],
        )
        is not None
    )
    msgs = drain_as_messages("c-mention")
    assert len(msgs) == 1
    body = msgs[0].content or ""
    assert "让写手收紧口径" in body
    assert "用户点名关注以下 Agent（软提示，非强制派单/非硬路由）" in body
    assert "- 写手 (id=agent_writer)" in body
    assert "<队员点名>" in body
    end_accepting("c-mention")
    _reset_for_tests()


def test_try_enqueue_requires_accepting_window():
    _reset_for_tests()
    assert try_enqueue(conversation_id="c1", content="x") is None
    begin_accepting("c1")
    item = try_enqueue(conversation_id="c1", content="x")
    assert item is not None
    assert peek_count("c1") == 1
    msgs = drain_as_messages("c1")
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert "x" in (msgs[0].content or "")
    assert peek_count("c1") == 0
    end_accepting("c1")
    _reset_for_tests()


async def _never() -> None:
    await asyncio.Future()


@pytest.mark.asyncio
async def test_leftover_promote_emits_degraded_turn_queued_on_live_sink():
    """accepted 后 leftover promote → user_interjection(queued) + turn_queued.degraded_from=steer。"""
    _reset_for_tests()
    cid = "c-leftover-promote"
    turn_queue.clear(cid)
    sink = EventSink()
    blocker = asyncio.create_task(_never())
    turn_runs.register(conversation_id=cid, task=blocker, sink=sink)
    try:
        begin_accepting(cid, execution_id="exec-leftover")
        item = try_enqueue(conversation_id=cid, content="晚到的纠偏")
        assert item is not None
        leftovers = end_accepting(cid)
        assert len(leftovers) == 1
        assert leftovers[0].interjection_id == item.interjection_id
        n = promote_leftovers_to_queue(leftovers)
        assert n == 1
        assert turn_queue.depth(cid) == 1

        interjections = [
            e for e in sink._history if e.type is EventType.USER_INTERJECTION  # noqa: SLF001
        ]
        assert any(e.payload.get("status") == "queued" for e in interjections)
        queued = [e for e in sink._history if e.type is EventType.TURN_QUEUED]  # noqa: SLF001
        assert len(queued) == 1
        payload = queued[0].payload
        assert payload["degraded_from"] == "steer"
        assert payload["conversation_id"] == cid
        assert payload["queue_id"]
        assert payload["position"] == 1
        assert payload["queue_depth"] == 1
    finally:
        # Clear before cancelling so done-callback drain cannot start the leftover.
        turn_queue.clear(cid)
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        _reset_for_tests()


@pytest.mark.asyncio
async def test_leftover_promote_without_live_sink_still_enqueues():
    """无 live sink：内容仍入队；不抛；不清空消息。"""
    _reset_for_tests()
    cid = "c-leftover-nosink"
    turn_queue.clear(cid)
    begin_accepting(cid)
    assert try_enqueue(conversation_id=cid, content="keep me") is not None
    leftovers = end_accepting(cid)
    assert promote_leftovers_to_queue(leftovers) == 1
    assert turn_queue.depth(cid) == 1
    popped = turn_queue.pop_next(cid)
    assert popped is not None
    assert popped.content == "keep me"
    assert popped.interjection_id == leftovers[0].interjection_id
    turn_queue.clear(cid)
    _reset_for_tests()


@pytest.mark.asyncio
async def test_user_stop_discards_leftovers_without_enqueue():
    """user_stop：未读插话 failed 丢弃，不入 FIFO、不自动开跑。"""
    _reset_for_tests()
    cid = "c-leftover-stop-discard"
    turn_queue.clear(cid)
    sink = EventSink()
    blocker = asyncio.create_task(_never())
    turn_runs.register(conversation_id=cid, task=blocker, sink=sink)
    try:
        begin_accepting(cid, execution_id="exec-stop-discard")
        item = try_enqueue(conversation_id=cid, content="把它停止")
        assert item is not None
        leftovers = end_accepting(cid)
        assert len(leftovers) == 1
        n = discard_leftovers_on_user_stop(leftovers)
        assert n == 1
        assert turn_queue.depth(cid) == 0

        interjections = [
            e for e in sink._history if e.type is EventType.USER_INTERJECTION  # noqa: SLF001
        ]
        assert len(interjections) == 1
        payload = interjections[0].payload
        assert payload["status"] == "failed"
        assert payload["note"] == _USER_STOP_DISCARD_NOTE
        assert payload["interjection_id"] == item.interjection_id
        assert not any(e.type is EventType.TURN_QUEUED for e in sink._history)  # noqa: SLF001
    finally:
        turn_queue.clear(cid)
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        _reset_for_tests()


@pytest.mark.asyncio
async def test_user_stop_discard_leaves_user_fifo_untouched():
    """Stop ≠ 取消排队：丢弃 leftover 不得动用户主动排队的 FIFO 项。"""
    _reset_for_tests()
    cid = "c-leftover-stop-fifo"
    turn_queue.clear(cid)
    user_item = new_queued_turn(content="用户先排的下一句", user_id="u1")
    turn_queue.enqueue(cid, user_item)
    assert turn_queue.depth(cid) == 1

    sink = EventSink()
    blocker = asyncio.create_task(_never())
    turn_runs.register(conversation_id=cid, task=blocker, sink=sink)
    try:
        begin_accepting(cid, execution_id="exec-stop-fifo")
        assert try_enqueue(conversation_id=cid, content="停止时的插话") is not None
        leftovers = end_accepting(cid)
        assert discard_leftovers_on_user_stop(leftovers) == 1
        assert turn_queue.depth(cid) == 1
        popped = turn_queue.pop_next(cid)
        assert popped is user_item
        assert popped.content == "用户先排的下一句"
    finally:
        turn_queue.clear(cid)
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        _reset_for_tests()


@pytest.mark.asyncio
async def test_react_loop_user_stop_discards_undrained_steer():
    """react_loop finally：user_stop 时 leftover 走 discard，不 promote。"""
    _reset_for_tests()
    cid = "c-loop-stop-discard"
    turn_queue.clear(cid)
    ctx = replace(_context(), conversation_id=cid, execution_id="exec-loop-stop")

    provider = _ScriptedProvider(
        [
            [
                LLMChunk(delta_content="working"),
                LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0, id="c1", function_name="search", arguments_delta="{}"
                        )
                    ]
                ),
            ],
            [LLMChunk(delta_content="should not run")],
        ]
    )
    tool = _StubTool()
    stop_event = asyncio.Event()

    async def _execute(arguments, context):  # noqa: ANN001
        assert try_enqueue(conversation_id=cid, content="把它停止") is not None
        stop_event.set()
        await asyncio.Future()  # hang until cancelled

    tool.execute = _execute  # type: ignore[method-assign]

    sink = EventSink()
    loop_task = asyncio.create_task(
        react_loop(
            messages=[LLMMessage(role="user", content="go")],
            llm=provider,
            tools=_registry(tool),
            sink=sink,
            tool_context=ctx,
            profile=make_profile_params(max_rounds=4),
            turn_model="m",
            role="captain",
            approval_gate=None,
        )
    )
    turn_runs.register(conversation_id=cid, task=loop_task, sink=sink)
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=2.0)
        assert turn_runs.stop(cid) is True
        with pytest.raises(asyncio.CancelledError):
            await loop_task
        assert turn_queue.depth(cid) == 0
        interjections = [
            e for e in sink._history if e.type is EventType.USER_INTERJECTION  # noqa: SLF001
        ]
        failed = [e for e in interjections if e.payload.get("status") == "failed"]
        assert len(failed) == 1
        assert failed[0].payload.get("note") == _USER_STOP_DISCARD_NOTE
        assert not any(e.payload.get("status") == "queued" for e in interjections)
    finally:
        turn_queue.clear(cid)
        if not loop_task.done():
            loop_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop_task
        _reset_for_tests()


@pytest.mark.asyncio
async def test_react_loop_drains_steer_at_step_top():
    """Captain loop：工具步后下一轮顶端把 pending steer 注入 messages 并发 injected。"""
    _reset_for_tests()
    cid = "c-loop-steer"
    ctx = replace(_context(), conversation_id=cid, execution_id="exec-loop-steer")

    seen_user_contents: list[str] = []

    class _SpyProvider(_ScriptedProvider):
        async def stream(self, request):  # noqa: ANN001
            for m in request.messages:
                if m.role == "user" and m.content:
                    seen_user_contents.append(m.content)
            async for chunk in super().stream(request):
                yield chunk

    provider = _SpyProvider(
        [
            [
                LLMChunk(delta_content="working"),
                LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0, id="c1", function_name="search", arguments_delta="{}"
                        )
                    ]
                ),
            ],
            [LLMChunk(delta_content="done after steer")],
        ]
    )

    tool = _StubTool()
    original_execute = tool.execute

    async def _execute(arguments, context):  # noqa: ANN001
        # Park while tool runs (between round 0 LLM and round 1 LLM).
        assert try_enqueue(conversation_id=cid, content="请改成要点列表") is not None
        return await original_execute(arguments, context)

    tool.execute = _execute  # type: ignore[method-assign]

    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    sink = EventSink()
    content, *_ = await react_loop(
        messages=messages,
        llm=provider,
        tools=_registry(tool),
        sink=sink,
        tool_context=ctx,
        profile=make_profile_params(max_rounds=4),
        turn_model="m",
        role="captain",
        approval_gate=None,
    )
    assert "done after steer" in content
    assert any("请改成要点列表" in c and "中途补充" in c for c in seen_user_contents)
    assert any(
        m.role == "user" and m.content and "请改成要点列表" in m.content for m in messages
    )
    assert peek_count(cid) == 0
    injected = [
        e for e in sink._history if e.type is EventType.USER_INTERJECTION  # noqa: SLF001
    ]
    assert any(e.payload.get("status") == "injected" for e in injected)
    _reset_for_tests()


@pytest.mark.asyncio
async def test_react_loop_holds_return_to_hear_steer_same_turn():
    """散文无工具步：流中插队不 leftover 升队，同回合再跑一轮并 injected。"""
    _reset_for_tests()
    cid = "c-loop-hold-return"
    turn_queue.clear(cid)
    ctx = replace(_context(), conversation_id=cid, execution_id="exec-hold-return")

    seen_user_contents: list[str] = []
    enqueued = False

    class _SpyProvider(_ScriptedProvider):
        async def stream(self, request):  # noqa: ANN001
            nonlocal enqueued
            for m in request.messages:
                if m.role == "user" and m.content:
                    seen_user_contents.append(m.content)
            async for chunk in super().stream(request):
                if not enqueued and chunk.delta_content:
                    enqueued = True
                    assert try_enqueue(conversation_id=cid, content="改成要点列表") is not None
                yield chunk

    provider = _SpyProvider(
        [
            [LLMChunk(delta_content="先写一长段")],
            [LLMChunk(delta_content="已按要点改")],
        ]
    )
    messages: list[LLMMessage] = [LLMMessage(role="user", content="写一段说明")]
    sink = EventSink()
    content, *_ = await react_loop(
        messages=messages,
        llm=provider,
        tools=_registry(_StubTool()),
        sink=sink,
        tool_context=ctx,
        profile=make_profile_params(max_rounds=4),
        turn_model="m",
        role="captain",
        approval_gate=None,
    )
    assert "已按要点改" in content
    assert any("改成要点列表" in c and "中途补充" in c for c in seen_user_contents)
    assert any(
        m.role == "user" and m.content and "改成要点列表" in m.content for m in messages
    )
    assert peek_count(cid) == 0
    assert turn_queue.depth(cid) == 0
    injected = [
        e for e in sink._history if e.type is EventType.USER_INTERJECTION  # noqa: SLF001
    ]
    assert any(e.payload.get("status") == "injected" for e in injected)
    assert not any(e.payload.get("status") == "queued" for e in injected)
    turn_queue.clear(cid)
    _reset_for_tests()


@pytest.mark.asyncio
async def test_react_loop_prose_steer_leftover_when_no_remaining_round():
    """max_rounds 用尽：散文流中插队仍 leftover 升队，不虚构同回合注入。"""
    _reset_for_tests()
    cid = "c-loop-hold-ceiling"
    turn_queue.clear(cid)
    ctx = replace(_context(), conversation_id=cid, execution_id="exec-hold-ceiling")
    sink = EventSink()
    blocker = asyncio.create_task(_never())
    turn_runs.register(conversation_id=cid, task=blocker, sink=sink)
    try:
        enqueued = False

        class _SpyProvider(_ScriptedProvider):
            async def stream(self, request):  # noqa: ANN001
                nonlocal enqueued
                async for chunk in super().stream(request):
                    if not enqueued and chunk.delta_content:
                        enqueued = True
                        assert try_enqueue(conversation_id=cid, content="来不及听") is not None
                    yield chunk

        provider = _SpyProvider([[LLMChunk(delta_content="只能这一轮")]])
        await react_loop(
            messages=[LLMMessage(role="user", content="写一段")],
            llm=provider,
            tools=_registry(_StubTool()),
            sink=sink,
            tool_context=ctx,
            profile=make_profile_params(max_rounds=1),
            turn_model="m",
            role="captain",
            approval_gate=None,
        )
        assert turn_queue.depth(cid) == 1
        interjections = [
            e for e in sink._history if e.type is EventType.USER_INTERJECTION  # noqa: SLF001
        ]
        assert any(e.payload.get("status") == "queued" for e in interjections)
        assert not any(e.payload.get("status") == "injected" for e in interjections)
    finally:
        turn_queue.clear(cid)
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        _reset_for_tests()


@pytest.mark.asyncio
async def test_worker_loop_does_not_accept_classic_steer():
    """Workers must not open the classic-steer accepting window."""
    _reset_for_tests()
    ctx = replace(_context(), conversation_id="c-worker")
    provider = _ScriptedProvider([[LLMChunk(delta_content="worker out")]])
    await react_loop(
        messages=[LLMMessage(role="user", content="go")],
        llm=provider,
        tools=_registry(_StubTool()),
        sink=EventSink(),
        tool_context=ctx,
        profile=make_profile_params(max_rounds=2),
        turn_model="m",
        role="worker",
        on_reset=lambda _r: None,
        approval_gate=None,
    )
    assert try_enqueue(conversation_id="c-worker", content="nope") is None
    _reset_for_tests()
