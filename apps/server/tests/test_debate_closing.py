"""结辩收束（P4·阶段化发言角色）prompt / 材料注入 / 台账 id 闸自测（per-PR 零 LLM）。

验收面：
1. 阶段角色契约（胜负手 / 禁新论据）仍在场；字数课不进 brief；
2. brief 携带三类材料（历轮论点 / 质询让步 / clash 命门）且裁剪封顶；
3. closing_context_blocks 投喂==展示（task.body 逐字）+ 材料孪生块；
4. 证据台账 id 闸：结辩引用不存在 id → 回炉；二次违规降级（与立论同闸，见
   test_debate_evidence_ledger）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from agentcore.llm.profiles import ProfileParams
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, LLMResponse, TokenUsage
from agentcore.runtime.debate import (
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
from agentcore.runtime.debate.evidence_ledger import EvidenceLedger
from agentcore.runtime.debate.prompt import (
    _CLOSING_ARGS_TOTAL,
    _CLOSING_POINT_CLIP,
    _clip,
    closing_context_blocks,
    closing_task,
)
from agentcore.runtime.debate.speech_pipeline import research_then_draft
from agentcore.runtime.debate.types import DebateClash
from agentcore.runtime.events.types import EventType
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def _two_sides() -> list[DebateSide]:
    return [
        DebateSide(key="pro", name="正方", stance="支持做 X"),
        DebateSide(key="con", name="反方", stance="反对做 X"),
    ]


def _config() -> DebateConfig:
    return DebateConfig(
        motion="该不该做 X",
        form=DebateForm.DEBATE,
        sides=_two_sides(),
        policy=RoundPolicy(max_rounds=5),
    )


def _rounds_with_materials() -> list[RoundResult]:
    """一场带三类材料的迷你叙事线：论点含已核实标签、质询让步、对方 clash。"""
    pro = SideTurn(
        side_key="pro",
        side_name="正方",
        run_id="r1_pro",
        content=(
            "### 降本可核实\n首年降本 12%【已核实·2024成本审计】，熔断可回滚。\n\n"
            "### 风险可控\n尾部风险有预案【已核实·灰度预案】。"
        ),
        ok=True,
        arguments=[
            {
                "id": "arg-0",
                "title": "降本可核实",
                "body": "首年降本 12%【已核实·2024成本审计】，熔断可回滚。",
            },
            {
                "id": "arg-1",
                "title": "风险可控",
                "body": "尾部风险有预案【已核实·灰度预案】。",
            },
        ],
    )
    con = SideTurn(
        side_key="con",
        side_name="反方",
        run_id="r1_con",
        content="### 尾部被低估\n熔断触发成本未入账。",
        ok=True,
        arguments=[{"id": "arg-0", "title": "尾部被低估", "body": "熔断触发成本未入账。"}],
    )
    return [
        RoundResult(
            round_no=1,
            focus="成本与风险",
            turns=[pro, con],
            verdict=JudgeVerdict(
                real_clash=True,
                new_arguments=True,
                converged=False,
                clashes=[
                    DebateClash(
                        from_key="con",
                        to_key="pro",
                        point="熔断触发成本未入账，降本口径不完整",
                    )
                ],
            ),
            cross_exam=[
                CrossExamExchange(
                    target="pro",
                    exchanges=[
                        CrossExamQa(
                            question="降本是否含尾部触发成本？",
                            answer=(
                                "部分成立：承认口径未含尾部触发成本，"
                                "但主路径降本仍成立【已核实·2024成本审计】。"
                            ),
                        ),
                        CrossExamQa(
                            question="有无独立第三方审计？",
                            answer="是，见年报附注，已正面回应。",
                        ),
                    ],
                )
            ],
        )
    ]


def test_closing_task_demands_winning_moves_and_bans_new_arguments():
    """结辩 feedback：点明是【最后陈词】、要求只讲胜负手、且明令【不得引入新论据 / 新事实】。"""
    fb = closing_task(_config(), _two_sides()[0], _rounds_with_materials())
    assert "结辩" in fb and "最后陈词" in fb
    assert "胜负手" in fb
    assert "不得引入" in fb and "新论据" in fb


def test_closing_task_carries_phased_length_budget():
    """字数课不进结辩 brief。"""
    fb = closing_task(_config(), _two_sides()[0])
    assert "150–250" not in fb
    assert "400–600" not in fb
    assert "宁深勿长" not in fb


def test_closing_task_carries_three_material_kinds():
    """结辩 brief 携带三类材料：本方论点、质询让步、对方 clash 命门。"""
    fb = closing_task(_config(), _two_sides()[0], _rounds_with_materials())
    assert "本方历轮论点" in fb
    assert "降本可核实" in fb
    assert "【已核实·2024成本审计】" in fb
    assert "交叉质询中做过的关键让步" in fb or "质询" in fb and "让步" in fb
    assert "承认口径未含尾部" in fb
    assert "对方对本方的交锋命门" in fb
    assert "熔断触发成本未入账" in fb
    assert "不得翻供" in fb or "翻供" in fb


def test_closing_task_clips_oversized_argument_bodies():
    """历轮论点要点按 _CLOSING_POINT_CLIP / 总预算裁剪封顶。"""
    long_body = "前段保留_" + ("中" * (_CLOSING_POINT_CLIP + 800)) + "_尾段保留"
    assert len(long_body) > _CLOSING_POINT_CLIP
    rounds = [
        RoundResult(
            round_no=1,
            focus="焦点",
            turns=[
                SideTurn(
                    side_key="pro",
                    side_name="正方",
                    run_id="r1",
                    content=f"### 长论点\n{long_body}",
                    ok=True,
                    arguments=[{"id": "arg-0", "title": "长论点", "body": long_body}],
                )
            ],
            verdict=JudgeVerdict(real_clash=False, new_arguments=True, converged=True),
        )
    ]
    fb = closing_task(_config(), _two_sides()[0], rounds)
    assert "长论点" in fb
    assert "中段略" in fb or len(fb) < len(long_body)
    # 总预算硬顶：材料段不应接近未裁全文量级
    assert "本方历轮论点" in fb
    # 粗上界：单段论点裁后远小于原文
    clipped = _clip(long_body, _CLOSING_POINT_CLIP)
    assert len(clipped) <= _CLOSING_POINT_CLIP + 40
    assert clipped in fb or "……（中段略）……" in fb
    assert _CLOSING_ARGS_TOTAL >= _CLOSING_POINT_CLIP


def test_closing_context_blocks_shows_closing_framing_and_materials():
    """结辩『收到的上下文』：task 逐字复用 + closing 标记 + 材料孪生块。"""
    cfg, side = _config(), _two_sides()[0]
    rounds = _rounds_with_materials()
    fb = closing_task(cfg, side, rounds)
    blocks = closing_context_blocks(cfg, side, fb, rounds)
    assert blocks[0].channel == "task"
    assert blocks[0].body == fb
    assert "结辩" in blocks[0].heading
    channels = [b.channel for b in blocks]
    assert "closing" in channels
    assert "history" in channels  # 本方论点
    assert "cross_exam" in channels  # 让步
    assert "challenge" in channels  # clash
    closing = next(b for b in blocks if b.channel == "closing")
    assert "胜负手" not in closing.body
    assert "新论据" not in closing.body
    history = next(b for b in blocks if b.channel == "history")
    assert "降本可核实" in history.body
    challenge = next(b for b in blocks if b.channel == "challenge")
    assert "熔断触发成本未入账" in challenge.body


@dataclass
class _FakeSink:
    events: list = field(default_factory=list)

    def emit(self, event) -> None:  # noqa: ANN001
        self.events.append(event)


class _SequenceDraftLLM:
    """按调用次序返回成稿正文（用于标签闸回炉）。"""

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


def test_closing_evidence_guard_rewrites_once_on_unknown_id():
    """结辩引用不在台账的 id → reset + 回炉一次；第二次引用合法 id 则放行。"""
    led = EvidenceLedger()
    led.register(url="https://ex.com/audit", title="2024成本审计", side_key="pro")
    llm = _SequenceDraftLLM(
        [
            "结辩：街访支持【已核实·#r99】。",
            "结辩：降本可核实【已核实·#r1】，应有条件采用。",
        ]
    )
    sink = _FakeSink()
    speech, _, _, rounds = asyncio.run(
        research_then_draft(
            [LLMMessage(role="system", content="sys")],
            llm=llm,
            tools=ToolRegistry(),
            sink=sink,  # type: ignore[arg-type]
            tool_ctx=_ctx(),
            profile=ProfileParams(temperature=0.4, max_rounds=4, name="agent"),
            turn_model="fake",
            allowed_tools=[],
            run_id="closing_pro",
            agent_id="closing_pro",
            citation_sink=[],
            approval_gate=None,
            draft_system="成稿",
            draft_brief="请结辩。",
            allow_research=False,
            evidence_ledger=led,
            side_key="pro",
            check_evidence_ledger=True,
            allowed_ledger_ids=frozenset({"#r1"}),
        )
    )
    assert llm.stream_calls == 2
    assert "【已核实·#r1】" in speech
    assert "【已核实·#r99】" not in speech
    assert "[系统提示]" in llm.last_user
    resets = [e for e in sink.events if e.type is EventType.RUN_OUTPUT_RESET]
    assert len(resets) == 1
    assert resets[0].payload.get("reason") == "finish_guard"
    assert rounds == 2


def test_closing_evidence_guard_demotes_after_second_violation():
    """回炉后仍违规 → 剥离违规【已核实】降级为【待核实·推断】后放行（O2）。"""
    led = EvidenceLedger()
    led.register(url="https://ex.com/audit", title="2024成本审计", side_key="pro")
    bad = "结辩：配方秘密【已核实·街访数据】。"
    llm = _SequenceDraftLLM([bad, bad])
    sink = _FakeSink()
    speech, _, _, n_rounds = asyncio.run(
        research_then_draft(
            [LLMMessage(role="system", content="sys")],
            llm=llm,
            tools=ToolRegistry(),
            sink=sink,  # type: ignore[arg-type]
            tool_ctx=_ctx(),
            profile=ProfileParams(temperature=0.4, max_rounds=4, name="agent"),
            turn_model="fake",
            allowed_tools=[],
            run_id="closing_pro",
            agent_id="closing_pro",
            citation_sink=[],
            approval_gate=None,
            draft_system="成稿",
            draft_brief="请结辩。",
            allow_research=False,
            evidence_ledger=led,
            side_key="pro",
            check_evidence_ledger=True,
            allowed_ledger_ids=frozenset({"#r1"}),
        )
    )
    assert llm.stream_calls == 2
    assert "【已核实·街访数据】" not in speech
    assert "【待核实·推断】" in speech
    assert n_rounds == 2
    resets = [e for e in sink.events if e.type is EventType.RUN_OUTPUT_RESET]
    assert len(resets) >= 2
