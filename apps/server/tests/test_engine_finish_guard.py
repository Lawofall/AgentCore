"""Integration tests for 交付前核验·finish_guard wired into ``react_loop``.

Drives the done-round verification guard with a scripted provider (no network):
unclosed code fences no longer rework. Empty language fences no longer rework.
Citation markers (``[n]`` / ``#rN``) do **not** rework the chat bubble.
"""

from pathlib import Path

from agentcore.llm.provider.protocol import LLMChunk, LLMMessage
from agentcore.runtime.engine import ReactLoopOut, react_loop
from agentcore.runtime.events import EventSink, EventType
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_profile_params


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
    *,
    citation_sink: list[dict] | None = None,
    annotate_citations: bool = True,
    on_reset=None,
    max_rounds: int = 10,
):
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    sink = EventSink()
    profile = make_profile_params(max_rounds=max_rounds)
    result = await react_loop(
        messages=messages,
        llm=provider,
        tools=ToolRegistry(),
        sink=sink,
        tool_context=_context(),
        profile=profile,
        turn_model="m",
        out=None if citation_sink is None else ReactLoopOut(citations=citation_sink),
        annotate_citations=annotate_citations,
        on_reset=on_reset,
        approval_gate=None,
    )
    return result, messages, sink


def _resets(sink: EventSink) -> list:
    return [e for e in sink._history if e.type == EventType.CONTENT_RESET]


async def test_out_of_range_citation_does_not_rework():
    # 0 来源，正文写了 [1] → 对话不回炉、不 content_reset。
    provider = _ScriptedProvider([[_content_chunk("结论见 [1]。")]])
    (content, _r, _u, rounds), messages, sink = await _run(provider, citation_sink=[])

    assert content == "结论见 [1]。"
    assert rounds == 1
    assert provider.calls == 1
    steers = [m for m in messages if m.role == "user" and m.content and "核验未通过" in m.content]
    assert steers == []
    assert _resets(sink) == []


async def test_clean_citation_finishes_without_rework():
    # 1 来源，正文用 [1]（合法）→ 不回炉，直接结束、不发 content_reset。
    provider = _ScriptedProvider([[_content_chunk("结论见 [1]。")]])
    (content, _r, _u, rounds), messages, sink = await _run(
        provider, citation_sink=[{"url": "http://example.com"}]
    )
    assert content == "结论见 [1]。"
    assert rounds == 1
    assert _resets(sink) == []


async def test_unclosed_fence_ships_without_rework():
    # 未闭合围栏回炉已撤：CEO 照发，不 content_reset、不注入 [系统提示]。
    provider = _ScriptedProvider(
        [[_content_chunk("见下：\n```python\nprint(1)")], [_content_chunk("不会跑到。")]]
    )
    (content, _r, _u, rounds), messages, sink = await _run(
        provider, citation_sink=[], max_rounds=10
    )
    assert content == "见下：\n```python\nprint(1)"
    assert rounds == 1
    assert provider.calls == 1
    assert _resets(sink) == []
    assert not any(
        m.role == "user" and m.content and "没有闭合" in m.content for m in messages
    )


async def test_worker_unclosed_fence_does_not_reset():
    # worker 路径同样照发，不走 on_reset。
    resets: list[str] = []
    provider = _ScriptedProvider(
        [[_content_chunk("草稿：\n```json\n{\"a\": 1}")]]
    )
    (content, _r, _u, rounds), _messages, sink = await _run(
        provider,
        citation_sink=[],
        annotate_citations=False,
        on_reset=resets.append,
    )
    assert rounds == 1
    assert "```json" in content
    assert resets == []
    assert _resets(sink) == []


async def test_worker_path_skips_citation_guard():
    # worker 路径（annotate_citations=False）不跑角标校验：越界 [1] 也直接结束。
    provider = _ScriptedProvider([[_content_chunk("worker 产出 [1]。")]])
    (content, _r, _u, rounds), _messages, sink = await _run(
        provider, citation_sink=[], annotate_citations=False
    )
    assert content == "worker 产出 [1]。"
    assert rounds == 1
    assert _resets(sink) == []
