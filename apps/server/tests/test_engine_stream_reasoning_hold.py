"""Live content hold: don't let mid-CoT ``delta.content`` split a thought.

Pins ``stream_llm_round``: reasoning emits immediately; after CoT has started,
content is held until thinking has paused (second content-only chunk / tools /
finish / abort). Content that arrives while still idle (no reasoning yet) is
live — holding from idle is out of scope (no recorded vector; it would miss
the hot-redirect in-flight enqueue window). Same-chunk mixed deltas emit
reasoning first. ``thinking=False`` bypasses the hold.
"""

from __future__ import annotations

from agentcore.llm.provider.protocol import (
    LLMChunk,
    LLMMessage,
    LLMRequest,
    ToolCallDelta,
)
from agentcore.runtime.engine.stream import stream_llm_round


def _request(*, thinking: bool | None = None) -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model="m",
        thinking=thinking,
    )


class _ScriptedProvider:
    def __init__(self, chunks: list[LLMChunk]) -> None:
        self._chunks = chunks

    async def stream(self, request: LLMRequest):  # noqa: ARG002
        for chunk in self._chunks:
            yield chunk


async def _run(
    chunks: list[LLMChunk],
    *,
    thinking: bool | None = None,
) -> tuple[list[tuple[str, str]], object]:
    events: list[tuple[str, str]] = []
    result = await stream_llm_round(
        _ScriptedProvider(chunks),
        _request(thinking=thinking),
        lambda d: events.append(("c", d)),
        lambda d: events.append(("r", d)),
    )
    return events, result


async def test_interleaved_content_does_not_split_thought():
    """Greeting leak: content token between two reasoning fragments → one thought then reply."""
    events, result = await _run(
        [
            LLMChunk(delta_reasoning="用户只"),
            LLMChunk(delta_content="你好！"),
            LLMChunk(delta_reasoning="是打招呼。简单回应即可，不需要工具。"),
            LLMChunk(delta_content="我是你的 AI 工作台。"),
            LLMChunk(finish_reason="stop"),
        ]
    )
    assert events == [
        ("r", "用户只"),
        ("r", "是打招呼。简单回应即可，不需要工具。"),
        ("c", "你好！我是你的 AI 工作台。"),
    ]
    assert result.content == "你好！我是你的 AI 工作台。"
    assert result.reasoning == "用户只是打招呼。简单回应即可，不需要工具。"


async def test_mixed_chunk_emits_reasoning_before_held_content():
    events, result = await _run(
        [
            LLMChunk(delta_content="你好！", delta_reasoning="用户只是打招呼。"),
            LLMChunk(delta_content="我是工作台。"),
            LLMChunk(finish_reason="stop"),
        ]
    )
    assert events == [
        ("r", "用户只是打招呼。"),
        ("c", "你好！"),
        ("c", "我是工作台。"),
    ]
    assert result.content == "你好！我是工作台。"


async def test_content_first_then_reasoning_then_rest_merges_reply():
    """Content before any CoT is live; mid-CoT content after that is still held.

    ``_IDLE`` does not hold: the recorded leak is content between reasoning
    fragments, and a first-token hold would miss the hot-redirect in-flight
    enqueue window. Accumulated ``result.content`` still concatenates.
    """
    events, result = await _run(
        [
            LLMChunk(delta_content="你"),
            LLMChunk(delta_reasoning="用户只是打招呼。简单回应即可。"),
            LLMChunk(delta_content="好！我是工作台。"),
            LLMChunk(finish_reason="stop"),
        ]
    )
    assert events == [
        ("c", "你"),
        ("r", "用户只是打招呼。简单回应即可。"),
        ("c", "好！我是工作台。"),
    ]
    assert result.content == "你好！我是工作台。"
    assert result.reasoning == "用户只是打招呼。简单回应即可。"


async def test_content_only_stream_emits_each_chunk_immediately():
    """No reasoning yet (``_IDLE``): every content chunk is live, none held."""
    events, result = await _run(
        [
            LLMChunk(delta_content="0"),
            LLMChunk(delta_content="1"),
            LLMChunk(delta_content="2"),
            LLMChunk(delta_content="3"),
            LLMChunk(finish_reason="stop"),
        ]
    )
    assert events == [("c", "0"), ("c", "1"), ("c", "2"), ("c", "3")]
    assert result.content == "0123"


async def test_thinking_false_does_not_hold_interleaved_content():
    events, result = await _run(
        [
            LLMChunk(delta_reasoning="think"),
            LLMChunk(delta_content="a"),
            LLMChunk(delta_reasoning="more"),
            LLMChunk(delta_content="b"),
            LLMChunk(finish_reason="stop"),
        ],
        thinking=False,
    )
    assert events == [
        ("r", "think"),
        ("c", "a"),
        ("r", "more"),
        ("c", "b"),
    ]
    assert result.content == "ab"


async def test_flush_held_content_before_tool_calls():
    events, result = await _run(
        [
            LLMChunk(delta_reasoning="先搜"),
            LLMChunk(delta_content="我去查一下。"),
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(index=0, id="c1", function_name="web_search")
                ]
            ),
            LLMChunk(finish_reason="tool_calls"),
        ]
    )
    assert events == [("r", "先搜"), ("c", "我去查一下。")]
    assert result.tool_calls is not None
    assert result.tool_calls[0].function.name == "web_search"


async def test_stream_reset_drops_held_content():
    events, result = await _run(
        [
            LLMChunk(delta_reasoning="stale"),
            LLMChunk(delta_content="drop-me"),
            LLMChunk(stream_reset=True),
            LLMChunk(delta_reasoning="fresh think"),
            LLMChunk(delta_content="kept"),
            LLMChunk(finish_reason="stop"),
        ]
    )
    assert ("c", "drop-me") not in events
    assert events == [
        ("r", "stale"),
        ("r", "fresh think"),
        ("c", "kept"),
    ]
    assert result.content == "kept"
    assert result.reasoning == "fresh think"
