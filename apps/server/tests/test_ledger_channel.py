"""Turn 级台账 SSE 通道（引用即出处 P1 地基 · P2 投影）。"""

from __future__ import annotations

from agentcore.runtime.citations import (
    extract_ledger_ref_ids,
    project_cited_citations,
    stamp_citations_from_ledger,
)
from agentcore.runtime.events import EventType
from agentcore.runtime.evidence_ledger import EvidenceLedgerCore
from agentcore.runtime.ledger_channel import emit_ledger_delta, emit_turn_evidence_ledger


class _ListSink:
    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event) -> None:  # noqa: ANN001
        self.events.append(event)


async def test_emit_ledger_delta_and_settle_cited_subset() -> None:
    led = EvidenceLedgerCore(id_prefix="#r")
    await led.register(
        url="https://docs.example.com/a",
        title="A",
        registrant="ceo",
        query="q1",
        deep_read=True,
    )
    sink = _ListSink()
    emit_ledger_delta(sink, led)
    assert len(sink.events) == 1
    assert sink.events[0].type is EventType.EVIDENCE_LEDGER
    assert sink.events[0].payload["delta"][0]["id"] == "#r1"
    assert "entries" not in sink.events[0].payload

    await led.register(
        url="https://news.example.com/b",
        title="B",
        registrant="worker:w1",
        query="q1",
    )
    await led.register(
        url="https://wenku.baidu.com/view/x",
        title="弱源",
        registrant="worker:w1",
        query="q1",
    )
    # 仅引 #r1 与 weak #r3；#r2 未引用 → 不进主卡
    content = "见 #r1 与弱源 #r3。"
    pool = [
        {"url": "https://docs.example.com/a", "title": "A", "site": "docs.example.com"},
        {"url": "https://news.example.com/b", "title": "B", "site": "news.example.com"},
        {"url": "https://wenku.baidu.com/view/x", "title": "弱源", "site": "wenku.baidu.com"},
    ]
    cited_cards, entries, cited = emit_turn_evidence_ledger(
        sink, ledger=led, content=content, citations=pool
    )
    assert [e["id"] for e in entries] == ["#r1", "#r2", "#r3"]
    assert cited == ["#r1", "#r3"]
    assert [c["id"] for c in cited_cards] == ["#r1", "#r3"]
    assert cited_cards[0]["query"] == "q1"
    assert cited_cards[0]["deep_read"] is True
    assert cited_cards[1]["tier"] == "weak"
    assert cited_cards[1]["citable"] is True
    settle = [e for e in sink.events if e.payload.get("entries")][-1]
    assert settle.payload["cited_ids"] == ["#r1", "#r3"]


async def test_emit_turn_projects_search_only_skips_unregistered() -> None:
    led = EvidenceLedgerCore(id_prefix="#r")
    await led.register(
        url="https://docs.example.com/a",
        title="A",
        registrant="ceo",
        query="q1",
        deep_read=False,
    )
    sink = _ListSink()
    cited_cards, _entries, cited = emit_turn_evidence_ledger(
        sink,
        ledger=led,
        content="见 #r1 与幽灵 #r9。",
        citations=[],
    )
    assert cited == ["#r1"]
    assert [c["id"] for c in cited_cards] == ["#r1"]
    assert cited_cards[0]["deep_read"] is False


def test_project_cited_citations_no_hard_cap() -> None:
    entries = [
        {
            "id": f"#r{i}",
            "url": f"https://ex.example/{i}",
            "title": str(i),
            "tier": "unknown",
            "citable": True,
        }
        for i in range(1, 31)
    ]
    cited = [f"#r{i}" for i in range(1, 31)]
    cards = project_cited_citations(entries, cited)
    assert len(cards) == 30
    assert cards[0]["id"] == "#r1"
    assert cards[-1]["id"] == "#r30"


def test_stamp_and_extract_helpers() -> None:
    entries = [
        {
            "id": "#r1",
            "url": "https://a.example/x",
            "query": "q",
            "deep_read": False,
            "registrant": "ceo",
            "citable": True,
            "tier": "unknown",
        }
    ]
    stamped = stamp_citations_from_ledger(
        [{"url": "https://a.example/x", "title": "A"}], entries
    )
    assert stamped[0]["id"] == "#r1"
    assert extract_ledger_ref_ids("正文 #r1 与幽灵 #r9") == ["#r1", "#r9"]
    # 首次出现序（非升序）：#r2 先于 #r1
    assert extract_ledger_ref_ids("先 #r2 后 #r1 再 #r2") == ["#r2", "#r1"]
