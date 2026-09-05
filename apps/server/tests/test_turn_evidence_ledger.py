"""回合共享调研台账接线（引用即出处 P1 §十第 2 步）。

覆盖：多 worker 并发不撞号且 id→URL 稳定；CEO 直答登记；weak citable=true（P2）；
web_fetch 升级 deep_read；tool_exec / react_loop 对 worker 注入 ``#rN=url``（非旧 ``[n]``）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentcore.core.types import ToolCategory, ToolEffect
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.runtime.citations import annotate_ledger_ids, normalize_citation_url
from agentcore.runtime.engine import ReactLoopOut, react_loop
from agentcore.runtime.events import EventSink
from agentcore.runtime.evidence_ledger import EvidenceLedgerCore
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_profile_params


def test_annotate_ledger_ids_format():
    cites = [
        {"url": "https://a.example/x", "title": "A"},
        {"url": "https://b.example/y", "title": "B"},
    ]
    ids = {
        normalize_citation_url("https://a.example/x"): "#r1",
        normalize_citation_url("https://b.example/y"): "#r3",
    }
    out = annotate_ledger_ids("RESULT", cites, ids)
    assert "[已登记来源]" in out
    assert "深读" in out or "selected" in out  # 尾注教法：成稿挂号须深读/selected
    assert "#r1=https://a.example/x" in out
    assert "#r3=https://b.example/y" in out
    assert "[来源编号]" not in out
    assert "[1]=" not in out


@pytest.mark.asyncio
async def test_parallel_workers_shared_ledger_no_collision():
    """两路并行登记 → 全局不撞号；正文侧 id 仍指原 URL（禁止汇入重写）。"""
    led = EvidenceLedgerCore(id_prefix="#r")

    async def worker(prefix: str, registrant: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for i in range(10):
            url = f"https://{prefix}.example/p/{i}"
            eid = await led.register(
                url=url,
                title=f"{prefix}-{i}",
                registrant=registrant,
                query=f"q-{prefix}",
            )
            assert eid is not None
            out.append((eid, url))
        return out

    a, b = await asyncio.gather(
        worker("alpha", "worker:alpha"),
        worker("beta", "worker:beta"),
    )
    all_pairs = a + b
    ids = [p[0] for p in all_pairs]
    assert len(ids) == 20
    assert len(set(ids)) == 20
    assert set(ids) == {f"#r{i}" for i in range(1, 21)}
    for eid, url in all_pairs:
        entry = led.get(eid)
        assert entry is not None
        assert normalize_citation_url(entry["url"]) == normalize_citation_url(url)


@pytest.mark.asyncio
async def test_ceo_direct_register_and_weak_citable():
    led = EvidenceLedgerCore(id_prefix="#r")
    ok = await led.register(
        url="https://www.gov.cn/zhengce/demo.htm",
        title="政策",
        registrant="ceo",
        query="政策查询",
    )
    weak = await led.register(
        url="https://wenku.baidu.com/view/demo",
        title="文库",
        registrant="ceo",
        query="政策查询",
    )
    assert ok == "#r1"
    assert weak == "#r2"
    assert led.get("#r1")["citable"] is True
    assert led.get("#r1")["registrant"] == "ceo"
    assert led.get("#r1")["query"] == "政策查询"
    assert led.get("#r2")["tier"] == "weak"
    assert led.get("#r2")["citable"] is True


@pytest.mark.asyncio
async def test_web_fetch_upgrades_deep_read_on_shared_ledger():
    led = EvidenceLedgerCore(id_prefix="#r")
    eid = await led.register(
        url="https://news.example.com/story",
        title="Story",
        registrant="worker:w1",
        query="news",
        deep_read=False,
    )
    assert eid == "#r1"
    upgraded = await led.register(
        url="https://news.example.com/story",
        title="Story full",
        registrant="worker:w1",
        deep_read=True,
    )
    assert upgraded == "#r1"
    assert led.get("#r1")["deep_read"] is True
    assert led.get("#r1")["query"] == "news"


def _tool_chunk(name: str, args: str, *, call_id: str = "c") -> LLMChunk:
    return LLMChunk(
        delta_tool_calls=[
            ToolCallDelta(index=0, id=call_id, function_name=name, arguments_delta=args)
        ]
    )


def _content_chunk(text: str) -> LLMChunk:
    return LLMChunk(delta_content=text)


class _ScriptedProvider:
    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk


class _StubTool:
    def __init__(self, citations: list[dict]) -> None:
        self._citations = citations
        self.calls = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="search",
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.SEARCH,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        self.calls += 1
        return ToolResult(
            tool_call_id="",
            success=True,
            output="result",
            citations=self._citations,
            effect=ToolEffect.CONTINUE,
        )


@pytest.mark.asyncio
async def test_worker_loop_annotates_stable_ids_not_pool_numbers():
    """annotate_citations=False + 共享台账 → 注入 #rN；P2 weak 可引用且进 sink。"""
    cites = [
        {
            "url": "https://media.example.com/a",
            "title": "A",
            "snippet": "s",
            "site": "media.example.com",
            "query": "q",
        },
        {
            "url": "https://wenku.baidu.com/view/w",
            "title": "Weak",
            "snippet": "w",
            "site": "wenku.baidu.com",
            "query": "q",
        },
    ]
    led = EvidenceLedgerCore(id_prefix="#r")
    sink: list[dict] = []
    reg = ToolRegistry()
    reg.register(_StubTool(cites))
    messages = [LLMMessage(role="user", content="go")]
    provider = _ScriptedProvider(
        [[_tool_chunk("search", '{"q": "x"}')], [_content_chunk("done")]]
    )
    await react_loop(
        messages=messages,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        tool_context=ToolContext.create(
            execution_id="e",
            run_id="s",
            agent_id="w1",
            backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
            user_id="u",
        ),
        profile=make_profile_params(max_rounds=20),
        turn_model="m",
        out=ReactLoopOut(citations=sink),
        annotate_citations=False,
        turn_evidence_ledger=led,
        ledger_registrant="worker:w1",
        approval_gate=None,
    )
    tool_msg = next(m for m in messages if m.role == "tool")
    content = tool_msg.content or ""
    assert "[已登记来源]" in content
    assert "#r1=https://media.example.com/a" in content
    assert "#r2=https://wenku.baidu.com/view/w" in content
    assert "[来源编号]" not in content
    assert "[1]=" not in content
    assert led.get("#r2")["citable"] is True
    assert led.get("#r2")["tier"] == "weak"
    assert led.get("#r2")["registrant"] == "worker:w1"
    # mid-turn sink：含 weak（主卡是否挂出由 settle cited 投影决定）
    assert len(sink) == 2
    assert sink[1]["tier"] == "weak"


@pytest.mark.asyncio
async def test_ceo_loop_annotates_stable_ids_when_ledger_present():
    cites = [
        {"url": "https://a.example/x", "title": "A", "snippet": "", "site": "a.example"},
        {"url": "https://b.example/y", "title": "B", "snippet": "", "site": "b.example"},
    ]
    led = EvidenceLedgerCore(id_prefix="#r")
    sink: list[dict] = []
    reg = ToolRegistry()
    reg.register(_StubTool(cites))
    messages = [LLMMessage(role="user", content="go")]
    provider = _ScriptedProvider(
        [[_tool_chunk("search", '{"q": "x"}')], [_content_chunk("done")]]
    )
    await react_loop(
        messages=messages,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        tool_context=ToolContext.create(
            execution_id="e",
            run_id="s",
            agent_id="ceo",
            backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
            user_id="u",
        ),
        profile=make_profile_params(max_rounds=20),
        turn_model="m",
        out=ReactLoopOut(citations=sink),
        annotate_citations=True,
        turn_evidence_ledger=led,
        ledger_registrant="ceo",
        approval_gate=None,
    )
    tool_msg = next(m for m in messages if m.role == "tool")
    content = tool_msg.content or ""
    assert "#r1=https://a.example/x" in content
    assert "#r2=https://b.example/y" in content
    assert "[来源编号]" not in content
    assert led.get("#r1")["registrant"] == "ceo"
    assert [c["url"] for c in sink] == ["https://a.example/x", "https://b.example/y"]
