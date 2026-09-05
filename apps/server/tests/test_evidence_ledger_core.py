"""平台层证据台账共享核单测（引用即出处 P1 §十第 1 步）。

覆盖：原子 id（含并发不撞号）、URL 去重、tier/citable 戳记、blocked 拒登记。
"""

from __future__ import annotations

import asyncio

from agentcore.runtime.evidence_ledger import EvidenceLedgerCore, citable_for_tier


def test_citable_for_tier_p2():
    assert citable_for_tier("official") is True
    assert citable_for_tier("media") is True
    assert citable_for_tier("unknown") is True
    assert citable_for_tier("weak") is True
    assert citable_for_tier("blocked") is False


def test_promote_refs_cited_in_landed_note_selects_search_only():
    """方向笔记落盘：正文 #rN 升 selected，供 CEO 成稿闸继承。"""
    led = EvidenceLedgerCore(id_prefix="#r")
    led.load_entries(
        [
            {
                "id": "#r1",
                "url": "https://example.com/a",
                "title": "A",
                "tier": "unknown",
                "citable": True,
                "deep_read": False,
                "selected": False,
                "registrant": "worker:w1",
            }
        ]
    )
    assert led.draft_citable_ids() == frozenset()
    newly = led.promote_refs_cited_in_landed_note("结论见 #r1 与伪造 #r9")
    assert newly == frozenset({"#r1"})
    assert led.draft_citable_ids() == frozenset({"#r1"})


def test_load_entries_preserves_ids_and_continues():
    led = EvidenceLedgerCore(id_prefix="#r")
    led.load_entries(
        [
            {
                "id": "#r1",
                "url": "https://example.com/a",
                "title": "A",
                "tier": "unknown",
                "citable": True,
                "registrant": "ceo",
            },
            {
                "id": "#r2",
                "url": "https://zhidao.baidu.com/x",
                "title": "弱",
                "tier": "weak",
                "citable": False,
                "registrant": "worker:w1",
            },
        ]
    )
    assert led.citable_ids() == frozenset({"#r1"})
    assert led.ids == frozenset({"#r1", "#r2"})
    assert led.drain_delta() == []  # cursor at end
    assert (
        led.register_sync(url="https://example.com/c", title="C", registrant="ceo")
        == "#r3"
    )


def test_register_sync_dedup_and_ids():
    led = EvidenceLedgerCore()
    a = led.register_sync(
        url="https://example.com/a",
        title="A",
        registrant="worker:w1",
    )
    b = led.register_sync(
        url="https://example.com/a#frag",
        title="A dup",
        registrant="ceo",
    )
    c = led.register_sync(
        url="https://example.com/b",
        title="B",
        registrant="worker:w1",
        query="foo bar",
        deep_read=True,
    )
    assert a == "#e1"
    assert b == "#e1"
    assert c == "#e2"
    assert led.ids == frozenset({"#e1", "#e2"})
    e1 = led.get("#e1")
    assert e1 is not None
    assert e1["registrant"] == "worker:w1"  # 首登方保留
    assert e1["tier"] == "unknown"
    assert e1["citable"] is True
    e2 = led.get("#e2")
    assert e2 is not None
    assert e2["query"] == "foo bar"
    assert e2["deep_read"] is True


def test_tier_and_citable_stamp():
    led = EvidenceLedgerCore()
    official = led.register_sync(
        url="https://www.gov.cn/zhengce/xxx.htm",
        title="政策",
        registrant="ceo",
    )
    weak = led.register_sync(
        url="https://wenku.baidu.com/view/x",
        title="文库",
        registrant="ceo",
    )
    assert official == "#e1"
    assert weak == "#e2"
    assert led.get("#e1")["tier"] == "official"
    assert led.get("#e1")["citable"] is True
    assert led.get("#e2")["tier"] == "weak"
    assert led.get("#e2")["citable"] is True


def test_blocked_rejected():
    led = EvidenceLedgerCore(reject_blocked=True)
    eid = led.register_sync(
        url="https://zhidao.baidu.com/question/1",
        title="知道",
        registrant="ceo",
    )
    assert eid is None
    assert len(led) == 0
    assert led.ids == frozenset()


def test_blocked_accepted_when_reject_disabled():
    """辩论封装用 reject_blocked=False；核本身仍可登记 blocked（tier 戳记）。"""
    led = EvidenceLedgerCore(reject_blocked=False)
    eid = led.register_sync(
        url="https://zhidao.baidu.com/question/1",
        title="知道",
        registrant="pro",
    )
    assert eid == "#e1"
    assert led.get("#e1")["tier"] == "blocked"
    assert led.get("#e1")["citable"] is False


def test_id_prefix_configurable():
    led = EvidenceLedgerCore(id_prefix="#r")
    eid = led.register_sync(
        url="https://example.com/x",
        title="X",
        registrant="ceo",
    )
    assert eid == "#r1"


def test_concurrent_register_no_id_collision():
    """并行登记不撞号：N 个不同 URL → N 个唯一 id。"""

    async def _run() -> list[str | None]:
        led = EvidenceLedgerCore()
        n = 40

        async def one(i: int) -> str | None:
            return await led.register(
                url=f"https://example.com/page/{i}",
                title=f"P{i}",
                registrant=f"worker:w{i % 3}",
            )

        return list(await asyncio.gather(*[one(i) for i in range(n)]))

    ids = asyncio.run(_run())
    assert None not in ids
    assert len(ids) == 40
    assert len(set(ids)) == 40
    assert set(ids) == {f"#e{i}" for i in range(1, 41)}


def test_concurrent_same_url_dedup():
    """并行登记同一 URL → 全部返回同一 id，台账仅一条。"""

    async def _run() -> tuple[list[str | None], int]:
        led = EvidenceLedgerCore()

        async def one() -> str | None:
            return await led.register(
                url="https://example.com/same",
                title="Same",
                registrant="ceo",
            )

        ids = list(await asyncio.gather(*[one() for _ in range(20)]))
        return ids, len(led)

    ids, n = asyncio.run(_run())
    assert n == 1
    assert set(ids) == {"#e1"}


def test_deep_read_upgrades_existing_entry():
    """web_fetch 对已登记 URL 升级 deep_read，不新建 id。"""
    led = EvidenceLedgerCore(id_prefix="#r")
    eid = led.register_sync(
        url="https://example.com/article",
        title="Art",
        registrant="worker:w1",
        query="q1",
        deep_read=False,
    )
    assert eid == "#r1"
    assert led.get("#r1")["deep_read"] is False
    again = led.register_sync(
        url="https://example.com/article",
        title="Art longer",
        registrant="worker:w1",
        deep_read=True,
    )
    assert again == "#r1"
    assert len(led) == 1
    e = led.get("#r1")
    assert e is not None
    assert e["deep_read"] is True
    assert e["query"] == "q1"  # 首登 query 保留
    assert e["registrant"] == "worker:w1"


def test_draft_citable_requires_deep_read_or_selected():
    led = EvidenceLedgerCore(id_prefix="#r")
    led.register_sync(
        url="https://example.com/search-hit",
        title="Hit",
        registrant="ceo",
        deep_read=False,
    )
    led.register_sync(
        url="https://example.com/read",
        title="Read",
        registrant="ceo",
        deep_read=True,
    )
    assert led.citable_ids() == frozenset({"#r1", "#r2"})
    assert led.draft_citable_ids() == frozenset({"#r2"})
    # web_fetch 升级后进入成稿闸
    led.register_sync(
        url="https://example.com/search-hit",
        title="Hit",
        registrant="ceo",
        deep_read=True,
    )
    assert led.draft_citable_ids() == frozenset({"#r1", "#r2"})


def test_mark_selected_from_content_and_hydrate():
    led = EvidenceLedgerCore(id_prefix="#r")
    led.register_sync(
        url="https://example.com/a",
        title="A",
        registrant="ceo",
        deep_read=True,
    )
    led.register_sync(
        url="https://example.com/b",
        title="B",
        registrant="ceo",
        deep_read=False,
    )
    newly = led.mark_selected_from_content("结论见 #r1 与 #r2。")
    assert newly == frozenset({"#r1"})  # #r2 无 deep_read → 不标
    assert led.get("#r1")["selected"] is True
    assert led.get("#r2")["selected"] is False

    restored = EvidenceLedgerCore(id_prefix="#r")
    restored.load_entries(led.all_entries())
    assert restored.get("#r1")["selected"] is True
    assert restored.draft_citable_ids() == frozenset({"#r1"})


def test_merge_history_ledgers_and_doc_kind():
    led = EvidenceLedgerCore(id_prefix="#r")
    n = led.merge_history_ledgers(
        [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "见 #r1",
                "evidence_ledger": [
                    {
                        "id": "#r1",
                        "url": "https://example.com/thesis-notice",
                        "title": "2024 开题答辩安排公告",
                        "snippet": "公示",
                        "deep_read": True,
                        "selected": True,
                        "registrant": "ceo",
                        "citable": True,
                    }
                ],
            },
        ]
    )
    assert n == 1
    e = led.get("#r1")
    assert e is not None
    assert e["selected"] is True
    assert e["deep_read"] is True
    assert e["doc_kind"] == "announcement"
    from agentcore.runtime.evidence_ledger import format_registered_sources_prompt

    prompt = format_registered_sources_prompt(led)
    assert "<已登记来源>" in prompt
    assert "#r1" in prompt
    assert "deep_read=是" in prompt
    assert "成稿闸仅允许" not in prompt
    assert "成稿可引=" not in prompt
    assert "对话成稿可挂" in prompt
