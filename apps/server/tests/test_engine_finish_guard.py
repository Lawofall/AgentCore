"""Integration tests for 交付前核验·finish_guard wired into ``react_loop``.

Drives the done-round verification guard with a scripted provider (no network):
structural defects (unclosed code fence) are the 统一底线 — they fire on BOTH
paths. Empty language fences no longer rework. Citation markers (``[n]`` / ``#rN``)
do **not** rework the chat bubble. On the CEO path the reset is ``content_reset``;
a worker passes ``on_reset`` so its rework clears the run card via
``run_output_reset`` instead.
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


async def test_rework_cap_ships_product_as_is():
    # 始终结构缺陷：回炉到上限（默认 2）后放行，不无限循环。
    bad = [_content_chunk("见下：\n```python\nprint(1)")]
    provider = _ScriptedProvider([bad, bad, bad])
    (content, _r, _u, rounds), _messages, sink = await _run(
        provider, citation_sink=[], max_rounds=10
    )
    assert "```python" in content  # 额度耗尽后照发
    assert rounds == 3  # round0 回炉 → round1 回炉 → round2 放行
    assert len(_resets(sink)) == 2  # 恰好回炉上限次数


async def test_worker_path_skips_citation_guard():
    # worker 路径（annotate_citations=False）不跑角标校验：越界 [1] 也直接结束。
    provider = _ScriptedProvider([[_content_chunk("worker 产出 [1]。")]])
    (content, _r, _u, rounds), messages, sink = await _run(
        provider, citation_sink=[], annotate_citations=False
    )
    assert content == "worker 产出 [1]。"
    assert rounds == 1
    assert _resets(sink) == []


async def test_ceo_structural_defect_reworks():
    # 统一底线·CEO 路径：未闭合代码块 → 回炉 + content_reset；次轮干净 → 结束。
    provider = _ScriptedProvider(
        [
            [_content_chunk("见下：\n```python\nprint(1)")],
            [_content_chunk("修正：\n```python\nprint(1)\n```\n完成。")],
        ]
    )
    (content, _r, _u, rounds), messages, sink = await _run(provider, citation_sink=[])
    assert rounds == 2
    assert content.endswith("完成。")
    steers = [m for m in messages if m.role == "user" and m.content and "核验未通过" in m.content]
    assert len(steers) == 1
    assert "没有闭合" in steers[0].content
    assert len(_resets(sink)) == 1


async def test_worker_structural_defect_reworks_via_on_reset():
    # 统一底线·worker 路径：结构缺陷照样回炉，但重置走 on_reset（run_output_reset），
    # 而非 content_reset；引用查仍跳过。
    resets: list[str] = []
    provider = _ScriptedProvider(
        [
            [_content_chunk("草稿：\n```json\n{\"a\": 1}")],  # 未闭合围栏
            [_content_chunk("修正后的产出，无代码块。")],
        ]
    )
    (content, _r, _u, rounds), messages, sink = await _run(
        provider,
        citation_sink=[],
        annotate_citations=False,
        on_reset=resets.append,
    )
    assert rounds == 2
    assert content == "修正后的产出，无代码块。"
    # worker 的重置回调被触发，且 reason=finish_guard（结构回炉；fold 只清草稿）。
    assert resets == ["finish_guard"]
    assert _resets(sink) == []  # 没有走 CEO 的 content_reset
