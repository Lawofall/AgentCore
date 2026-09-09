"""证据台账 M1 / 笔记绑定前移单测（per-PR 零真实 LLM）。

覆盖：登记 / URL 去重 / 底料预登记 / tier 规则表 / 笔记 id 规格 / 闸新基准
（含结辩历轮并集）/ 登记收窄 / 二次降级 / 成稿 hint 子集。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from agentcore.llm.profiles import ProfileParams
from agentcore.llm.provider.protocol import LLMChunk, LLMResponse, TokenUsage
from agentcore.runtime.citations import citation_tier_for_url
from agentcore.runtime.debate.evidence_guard import (
    demote_verified_tags,
    extract_verified_tags,
    format_evidence_ledger_steer,
    invalid_verified_tags,
    ledger_id_in_tag,
)
from agentcore.runtime.debate.evidence_ledger import (
    MODERATOR_SIDE_KEY,
    EvidenceLedger,
    extract_ledger_ids,
    format_evidence_ledger_hint,
    preregister_background,
    side_cited_ledger_ids,
)
from agentcore.runtime.debate.prompt import EVIDENCE_NOTES_SPEC, EVIDENCE_RULE, debater_task
from agentcore.runtime.debate.speech_pipeline import research_then_draft
from agentcore.runtime.debate.types import (
    CrossExamExchange,
    CrossExamQa,
    DebateConfig,
    DebateForm,
    DebateSide,
    JudgeVerdict,
    RoundPolicy,
    RoundResult,
    SideTurn,
)
from agentcore.runtime.events.types import EventType
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def test_citation_tier_rules():
    assert citation_tier_for_url("https://wenshu.court.gov.cn/case/1") == "official"
    assert citation_tier_for_url("https://www.gov.cn/zhengce/xxx.htm") == "official"
    assert citation_tier_for_url("https://www.reuters.com/world/foo") == "media"
    assert citation_tier_for_url("https://www.caixin.com/a/b") == "media"
    assert citation_tier_for_url("https://www.bjnews.com.cn/detail/1.html") == "media"
    assert citation_tier_for_url("https://zh.wikipedia.org/wiki/X") == "unknown"
    assert citation_tier_for_url("") == "unknown"
    assert citation_tier_for_url("https://random-blog.example/post") == "unknown"
    assert citation_tier_for_url("https://zhidao.baidu.com/question/1") == "blocked"
    assert citation_tier_for_url("https://wenku.baidu.com/view/x") == "weak"


def test_ledger_append_dedup_and_ids():
    led = EvidenceLedger()
    a = led.register(
        url="https://example.com/a",
        title="A",
        side_key="pro",
    )
    b = led.register(
        url="https://example.com/a#frag",
        title="A dup",
        side_key="con",
    )
    c = led.register(
        url="https://example.com/b",
        title="B",
        side_key="pro",
    )
    assert a == "#e1"
    assert b == "#e1"  # URL 去重
    assert c == "#e2"
    assert led.ids == frozenset({"#e1", "#e2"})
    assert led.get("#e1")["side_key"] == "pro"  # 首登方保留
    delta1 = led.drain_delta()
    assert [e["id"] for e in delta1] == ["#e1", "#e2"]
    assert led.drain_delta() == []
    led.register(url="https://example.com/c", title="C", side_key="con")
    assert [e["id"] for e in led.drain_delta()] == ["#e3"]
    assert len(led.all_entries()) == 3


def test_preregister_background_rewrites_tags():
    led = EvidenceLedger()
    bg = (
        "一审判赔 500 万【已核实·判决书】；"
        "媒体报道称将上诉【已核实·财新报道】；"
        "行业规模【待核实·推断】。"
    )
    out = preregister_background(led, bg)
    assert "【已核实·#e1】" in out
    assert "【已核实·#e2】" in out
    assert "【待核实·推断】" in out
    assert "【已核实·判决书】" not in out
    assert led.get("#e1")["url"] == ""
    assert led.get("#e1")["tier"] == "unknown"
    assert led.get("#e1")["side_key"] == MODERATOR_SIDE_KEY
    assert led.get("#e1")["title"] == "判决书"


def test_preregister_background_maps_ceo_r_refs():
    """主持人底料中的 CEO 回合 #rN：注入即登记为场级 #eN，正文改写，避免辩手引用悬空。"""
    from agentcore.runtime.citations import invalid_ledger_ref_ids

    led = EvidenceLedger()
    bg = (
        "一审判决认定构成商标近似#r1；"
        "赔偿口径参考同类判例#r2与补充说明#r10。"
    )
    out = preregister_background(led, bg)
    assert "#r1" not in out
    assert "#r2" not in out
    assert "#r10" not in out
    assert "#e1" in out and "#e2" in out and "#e3" in out
    assert led.get("#e1")["origin_id"] == "#r1"
    assert led.get("#e2")["origin_id"] == "#r2"
    assert led.get("#e3")["origin_id"] == "#r10"
    assert led.get("#e1")["side_key"] == MODERATOR_SIDE_KEY
    # 改写后正文不再含平台闸所扫的悬空 #rN
    assert invalid_ledger_ref_ids(out, led.ids) == []
    # 幂等：再跑一次不重号、不残留 #r
    out2 = preregister_background(led, out)
    assert out2 == out
    assert len(led.all_entries()) == 3


def test_ledger_id_in_tag_and_invalid():
    assert ledger_id_in_tag("【已核实·#e3】") == "#e3"
    assert ledger_id_in_tag("【已核实·判决书 #e3】") == "#e3"  # 双写可解析
    assert ledger_id_in_tag("【已核实·街访数据】") is None
    assert ledger_id_in_tag("【已核实·#e9") is None  # 残缺
    known = frozenset({"#e1", "#e2"})
    speech = "A【已核实·#e1】；B【已核实·街访数据】；C【已核实·#e9】；D【待核实·推断】。"
    bad = invalid_verified_tags(speech, known)
    assert bad == ["【已核实·#e9】", "【已核实·街访数据】"]


def test_demote_and_steer():
    text = "主张【已核实·#e99】成立。"
    demoted = demote_verified_tags(text, ["【已核实·#e99】"])
    assert demoted == "主张【待核实·推断】成立。"
    steer = format_evidence_ledger_steer(["【已核实·#e99】"])
    assert steer.startswith("[系统提示]")
    assert "笔记" in steer


def test_format_evidence_ledger_for_judge_and_brief():
    """M2：裁判/简报块带 tier 标注；无引用时裁判空串、简报回退全量。"""
    from agentcore.runtime.debate.evidence_ledger import (
        format_evidence_ledger_for_brief,
        format_evidence_ledger_for_judge,
    )
    from agentcore.runtime.debate.types import SideTurn

    led = EvidenceLedger()
    eid = led.register(
        url="https://www.gov.cn/zhengce/x.htm",
        title="政策原文",
        side_key="pro",
    )
    assert led.get(eid)["tier"] == "official"
    turns = [
        SideTurn(
            side_key="pro",
            side_name="正方",
            run_id="r1",
            content=f"依据【已核实·{eid}】。",
        )
    ]
    judge_block = format_evidence_ledger_for_judge(led, turns)
    assert "本轮引用证据台账" in judge_block
    assert f"{eid} · tier=official" in judge_block
    assert "官方原文" in judge_block
    assert "深读=" in judge_block
    assert "弱源" not in judge_block
    assert format_evidence_ledger_for_judge(led, []) == ""
    assert format_evidence_ledger_for_judge(None, turns) == ""

    brief_block = format_evidence_ledger_for_brief(led, [])
    assert "本场证据台账" in brief_block
    assert f"{eid} · tier=official" in brief_block
    assert "不得抹平" in brief_block
    assert "弱源实锤" not in brief_block


def test_evidence_notes_spec_requires_line_tail_id():
    """笔记规格：事实要点行尾绑定 #eN（检索阶段，非成稿盲配）。"""
    assert "行尾标注来源 #eN" in EVIDENCE_NOTES_SPEC
    assert "刚读完" in EVIDENCE_NOTES_SPEC or "刚决定采用" in EVIDENCE_NOTES_SPEC
    assert "只能沿用本笔记出现过的 id" in EVIDENCE_NOTES_SPEC
    assert "本方本轮证据笔记" in EVIDENCE_RULE or "证据笔记" in EVIDENCE_RULE


def test_evidence_rule_teaches_id_format():
    assert "【已核实·#eN】" in EVIDENCE_RULE
    assert "自由出处短语" in EVIDENCE_RULE or "只写 id" in EVIDENCE_RULE


def test_debater_task_arms_ledger_check():
    cfg = DebateConfig(
        motion="是否推进 X",
        form=DebateForm.DEBATE,
        sides=[
            DebateSide(key="pro", name="正方", stance="支持"),
            DebateSide(key="con", name="反方", stance="反对"),
        ],
        policy=RoundPolicy.for_form(DebateForm.DEBATE, thorough=True),
    )
    payload = debater_task(cfg, cfg.sides[0], 0, round_no=1, focus="成本")
    assert payload["evidence_ledger_check"] is True
    assert payload["side_key"] == "pro"
    assert payload["search_policy"] == "debate_evidence"
    assert "source_grounding_check" not in payload


def test_format_ledger_hint_lists_ids():
    led = EvidenceLedger()
    led.register(url="https://a.example/x", title="甲", side_key="pro")
    led.register(url="https://b.example/y", title="乙", side_key="pro")
    hint_all = format_evidence_ledger_hint(led)
    assert "#e1" in hint_all and "#e2" in hint_all
    hint_sub = format_evidence_ledger_hint(led, ids={"#e1"})
    assert "#e1" in hint_sub
    assert "#e2" not in hint_sub
    assert "本方已绑定来源" in hint_sub


def test_extract_ledger_ids_and_side_cited_union():
    assert extract_ledger_ids("要点 12% #e3\n另一条 #e1") == frozenset({"#e1", "#e3"})
    rounds = [
        RoundResult(
            round_no=1,
            focus="成本",
            turns=[
                SideTurn(
                    side_key="pro",
                    side_name="正方",
                    run_id="r1",
                    content="降本 12%【已核实·#e1】。",
                ),
                SideTurn(
                    side_key="con",
                    side_name="反方",
                    run_id="r2",
                    content="对方夸大【已核实·#e9】。",
                ),
            ],
            verdict=JudgeVerdict(real_clash=True, new_arguments=True, converged=False),
            cross_exam=[
                CrossExamExchange(
                    target="pro",
                    exchanges=[
                        CrossExamQa(question="数字？", answer="见年报【已核实·#e2】。")
                    ],
                )
            ],
        )
    ]
    assert side_cited_ledger_ids(rounds, "pro") == frozenset({"#e1", "#e2"})
    assert side_cited_ledger_ids(rounds, "con") == frozenset({"#e9"})


def test_commit_research_narrows_registration():
    """登记收窄：search 噪声不上 wire；deep_read + 笔记引用才 commit。"""
    led = EvidenceLedger()
    # 模拟检索期写入核（未经 EvidenceLedger.register → 未 commit）
    core = led.research_proxy()
    e1 = core.register_sync(
        url="https://noise.example/pizza",
        title="披萨站",
        registrant="pro",
        deep_read=False,
    )
    e2 = core.register_sync(
        url="https://court.example/case",
        title="判决书",
        registrant="pro",
        deep_read=True,
    )
    e3 = core.register_sync(
        url="https://media.example/story",
        title="财新",
        registrant="pro",
        deep_read=False,
    )
    assert e1 == "#e1" and e2 == "#e2" and e3 == "#e3"
    assert led.ids == frozenset()  # 尚未 commit
    newly = led.commit_research(note_cited_ids={"#e3"})
    assert newly == frozenset({"#e2", "#e3"})  # deep_read + 笔记引用
    assert led.ids == frozenset({"#e2", "#e3"})
    assert "#e1" not in led.ids
    wire_ids = {e["id"] for e in led.all_entries()}
    assert wire_ids == {"#e2", "#e3"}
    delta = led.drain_delta()
    assert {e["id"] for e in delta} == {"#e2", "#e3"}


@dataclass
class _FakeSink:
    events: list = field(default_factory=list)

    def emit(self, event) -> None:  # noqa: ANN001
        self.events.append(event)


class _SequenceDraftLLM:
    def __init__(self, speeches: list[str]) -> None:
        self.speeches = list(speeches)
        self.stream_calls = 0
        self.last_user = ""

    async def complete(self, request):  # noqa: ANN001
        return LLMResponse(content=self.speeches[min(self.stream_calls, len(self.speeches) - 1)])

    async def stream(self, request):  # noqa: ANN001
        speech = self.speeches[min(self.stream_calls, len(self.speeches) - 1)]
        self.stream_calls += 1
        self.last_user = request.messages[1].content or ""
        yield LLMChunk(delta_content=speech)
        yield LLMChunk(
            finish_reason="stop", usage=TokenUsage(input_tokens=10, output_tokens=5)
        )


def _ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="r1",
        agent_id="r1",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


def test_pipeline_ledger_guard_rework_then_pass():
    """闸基准 = 笔记引用集：台账有 #e1 但笔记未绑 → 拦；笔记绑了才放行。"""
    led = EvidenceLedger()
    led.register(url="https://ex.com/a", title="年报", side_key="pro")
    led.register(url="https://ex.com/noise", title="噪声", side_key="pro")
    llm = _SequenceDraftLLM(
        [
            "论点【已核实·#e2】。",  # 台账有但笔记未绑 → 违规
            "论点【已核实·#e1】。",
        ]
    )
    sink = _FakeSink()

    async def _run():
        return await research_then_draft(
            [],
            llm=llm,
            tools=ToolRegistry(),
            sink=sink,  # type: ignore[arg-type]
            tool_ctx=_ctx(),
            profile=ProfileParams(temperature=0.4, max_rounds=4, name="agent"),
            turn_model="fake",
            allowed_tools=[],
            run_id="r1",
            agent_id="r1",
            citation_sink=[],
            approval_gate=None,
            draft_system="sys",
            draft_brief="brief",
            allow_research=False,
            evidence_ledger=led,
            side_key="pro",
            check_evidence_ledger=True,
            # 模拟本轮笔记只绑定了 #e1（成稿阶段 notes 为空时靠 allowed 传入）
            allowed_ledger_ids=frozenset({"#e1"}),
        )

    speech, *_ = asyncio.run(_run())
    assert speech == "论点【已核实·#e1】。"
    assert llm.stream_calls == 2
    assert any(e.type is EventType.RUN_OUTPUT_RESET for e in sink.events)
    # 成稿 hint 只列已绑定 #e1（回炉 steer 会点名违规标签，故不断言 last_user 全无 #e2）
    assert "- #e1 · 年报" in llm.last_user
    assert "- #e2 ·" not in llm.last_user


def test_pipeline_ledger_guard_demote_on_second_fail():
    """二次仍违规 → 降级【待核实·推断】。"""
    led = EvidenceLedger()
    led.register(url="https://ex.com/a", title="年报", side_key="pro")
    llm = _SequenceDraftLLM(
        [
            "编造【已核实·街访数据】。",
            "仍编造【已核实·街访数据】。",
        ]
    )
    sink = _FakeSink()

    async def _run():
        return await research_then_draft(
            [],
            llm=llm,
            tools=ToolRegistry(),
            sink=sink,  # type: ignore[arg-type]
            tool_ctx=_ctx(),
            profile=ProfileParams(temperature=0.4, max_rounds=4, name="agent"),
            turn_model="fake",
            allowed_tools=[],
            run_id="r1",
            agent_id="r1",
            citation_sink=[],
            approval_gate=None,
            draft_system="sys",
            draft_brief="brief",
            allow_research=False,
            evidence_ledger=led,
            side_key="pro",
            check_evidence_ledger=True,
            allowed_ledger_ids=frozenset({"#e1"}),
        )

    speech, *_ = asyncio.run(_run())
    assert "【待核实·推断】" in speech
    assert "【已核实·街访数据】" not in speech
    assert extract_verified_tags(speech) == set()


def test_pipeline_closing_allows_prior_cited_union_only():
    """结辩：只许沿用历轮已引用 id；台账里有但从未引用过的 #e2 仍拦。"""
    led = EvidenceLedger()
    led.register(url="https://ex.com/a", title="年报", side_key="pro")
    led.register(url="https://ex.com/b", title="披萨", side_key="pro")
    llm = _SequenceDraftLLM(
        [
            "结辩【已核实·#e2】。",
            "结辩【已核实·#e1】。",
        ]
    )
    sink = _FakeSink()

    async def _run():
        return await research_then_draft(
            [],
            llm=llm,
            tools=ToolRegistry(),
            sink=sink,  # type: ignore[arg-type]
            tool_ctx=_ctx(),
            profile=ProfileParams(temperature=0.4, max_rounds=4, name="agent"),
            turn_model="fake",
            allowed_tools=[],
            run_id="closing",
            agent_id="closing",
            citation_sink=[],
            approval_gate=None,
            draft_system="sys",
            draft_brief="结辩",
            allow_research=False,
            evidence_ledger=led,
            side_key="pro",
            check_evidence_ledger=True,
            allowed_ledger_ids=frozenset({"#e1"}),  # 历轮只引过 #e1
        )

    speech, *_ = asyncio.run(_run())
    assert "【已核实·#e1】" in speech
    assert llm.stream_calls == 2
