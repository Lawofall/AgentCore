"""主持人判定过程挂既有 run 时间线（思考整段 + 人读 markdown）。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from agentcore.llm.provider.protocol import LLMResponse
from agentcore.runtime.debate import (
    DebateConfig,
    DebateForm,
    DebateSide,
    Moderator,
    RoundPolicy,
    SideTurn,
)
from agentcore.runtime.debate.moderator_timeline import format_moderator_output
from agentcore.runtime.events import EventType


@dataclass
class _RecordingSink:
    events: list = field(default_factory=list)

    def emit(self, event) -> None:  # noqa: ANN001
        self.events.append(event)


class _ThinkLLM:
    """带 reasoning_content 的 fake complete；断言 stream 仍为 False。"""

    def __init__(self, payloads: dict[str, dict]) -> None:
        self.payloads = payloads
        self.requests: list = []

    async def complete(self, request):  # noqa: ANN001
        self.requests.append(request)
        assert request.stream is False
        step = request.scenario.rsplit(".", 1)[-1]
        body = self.payloads.get(step, {})
        return LLMResponse(
            content=json.dumps(body),
            reasoning_content=f"想了想·{step}",
        )


def _sides() -> list[DebateSide]:
    return [
        DebateSide(key="pro", name="正方", stance="支持"),
        DebateSide(key="con", name="反方", stance="反对"),
    ]


def _deltas(sink: _RecordingSink, kind: EventType) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for e in sink.events:
        if e.type is kind:
            p = e.payload
            out.append((p["run_id"], p["agent_id"], p["delta"]))
    return out


def test_complete_emits_reasoning_then_output_on_moderator_run():
    """注入可记录 sink + 带思考的 fake complete → 两路 delta 都挂主持人 run_id。"""
    sink = _RecordingSink()
    llm = _ThinkLLM(
        {
            "frame": {"focus": "成本争议", "opening": "现在开辩，先看成本。"},
        }
    )
    mod = Moderator(provider=llm, model="m", run_id="debate_mod1", sink=sink)
    mod._round_no = 1
    data = asyncio.run(mod._complete_json("sys", "user", "frame"))
    assert data["focus"] == "成本争议"

    reasoning = _deltas(sink, EventType.RUN_REASONING_DELTA)
    content = _deltas(sink, EventType.RUN_OUTPUT_DELTA)
    assert reasoning == [("debate_mod1", "debate_mod1", "想了想·frame")]
    assert len(content) == 1
    assert content[0][:2] == ("debate_mod1", "debate_mod1")
    text = content[0][2]
    assert "## 开场" in text
    assert "现在开辩，先看成本。" in text
    assert "## 第 1 轮焦点" in text
    assert "成本争议" in text
    assert '{"focus"' not in text
    kinds = [e.type for e in sink.events]
    assert kinds == [EventType.RUN_REASONING_DELTA, EventType.RUN_OUTPUT_DELTA]


def test_complete_without_sink_is_silent():
    """旧 Moderator() 调用面：无 sink 不发事件、JSON 解析照旧。"""
    llm = _ThinkLLM({"assess": {"summary": "本轮小结", "converged": True}})
    mod = Moderator(provider=llm, model="m")
    data = asyncio.run(mod._complete_json("sys", "user", "assess"))
    assert data["summary"] == "本轮小结"


def test_complete_without_run_id_skips_emit():
    """有 sink 但无 run_id → 静默跳过。"""
    sink = _RecordingSink()
    llm = _ThinkLLM({"frame": {"focus": "成本"}})
    mod = Moderator(provider=llm, model="m", sink=sink)
    asyncio.run(mod._complete_json("sys", "user", "frame"))
    assert sink.events == []


def test_complete_without_reasoning_still_emits_human_content():
    """无思考只发人读 content；不造空 reasoning delta。"""

    class _NoThink(_ThinkLLM):
        async def complete(self, request):  # noqa: ANN001
            self.requests.append(request)
            assert request.stream is False
            return LLMResponse(content=json.dumps({"focus": "成本"}))

    sink = _RecordingSink()
    llm = _NoThink({})
    mod = Moderator(provider=llm, model="m", run_id="mod", sink=sink)
    asyncio.run(mod._complete_json("sys", "user", "frame"))
    assert _deltas(sink, EventType.RUN_REASONING_DELTA) == []
    content = _deltas(sink, EventType.RUN_OUTPUT_DELTA)
    assert len(content) == 1
    assert "## 第 1 轮焦点" in content[0][2]


def test_format_verdict_is_user_facing_only():
    """终审只写倾向 / 胜负手 / 置信 / 交接，不写比分、不倒灌简报全文。"""
    text = format_moderator_output(
        "brief",
        {
            "leaning": "反方略稳",
            "decisive": "正方熔断成本无据",
            "confidence": "medium",
            "value_disputes": ["你更看重速度还是稳妥？"],
            "factual_disputes": ["成本到底多少"],
            "open_questions": ["政策会不会变"],
            "crux": "不该出现在时间线",
            "recommendation": "也不该倒灌",
            "strongest_points": {"pro": "命门"},
            "scores": {"pro": {"total": 12}},
        },
        round_no=1,
    )
    assert "## 终审" in text
    assert "**倾向**：反方略稳" in text
    assert "**胜负手**：正方熔断成本无据" in text
    assert "**置信**：medium" in text
    assert "需你定夺" in text
    assert "事实分歧" in text
    assert "待解问题" in text
    assert "不该出现在时间线" not in text
    assert "也不该倒灌" not in text
    assert "命门" not in text
    assert "12" not in text
    assert "scores" not in text


def test_format_questions_cross_exam_and_witness():
    qs = {"pro": ["收益计入尾部风险了吗？"], "con": ["替代方案是什么？"]}
    cx = format_moderator_output("cross_exam", {"questions": qs}, round_no=1)
    wit = format_moderator_output(
        "witness_exam", {"questions": {"lens_0": ["条款原文怎么写？"]}}, round_no=1
    )
    assert cx.startswith("## 质询题")
    assert "收益计入尾部风险了吗？" in cx
    assert wit.startswith("## 质询题")
    assert "条款原文怎么写？" in wit


async def test_run_emits_frame_assess_brief_on_moderator_run():
    """整场循环：开场/焦点 → 小结 → 终审，都挂同一 run_id；辩手正文不进主持人 delta。"""
    sink = _RecordingSink()
    llm = _ThinkLLM(
        {
            "frame": {"focus": "成本", "opening": "现在开辩。"},
            "assess": {
                "real_clash": True,
                "new_arguments": False,
                "converged": True,
                "stop_reason": "converged",
                "summary": "双方见底。",
                "scores": {"pro": {"argument": 5, "engagement": 5, "evidence": 5}},
            },
            "brief": {
                "leaning": "反方略稳",
                "decisive": "正方无据",
                "confidence": "high",
                "value_disputes": ["你更看重稳妥吗？"],
            },
        }
    )

    async def runner(**_kw):  # noqa: ANN003
        return [SideTurn("pro", "正方", "pro_r1", "正方长篇发言不应进主持人时间线", ok=True)]

    config = DebateConfig(
        motion="该不该做 X",
        form=DebateForm.DEBATE,
        sides=_sides(),
        policy=RoundPolicy(thorough=False, max_rounds=1),
    )
    mod = Moderator(provider=llm, model="m", run_id="debate_mod1", sink=sink)
    await mod.run(config, run_round=runner)

    reasoning = _deltas(sink, EventType.RUN_REASONING_DELTA)
    content = _deltas(sink, EventType.RUN_OUTPUT_DELTA)
    assert all(r[0] == "debate_mod1" for r in reasoning)
    assert all(c[0] == "debate_mod1" for c in content)
    assert [r[2] for r in reasoning] == ["想了想·frame", "想了想·assess", "想了想·brief"]
    joined = "".join(c[2] for c in content)
    assert "## 开场" in joined
    assert "## 第 1 轮焦点" in joined
    assert "## 小结" in joined
    assert "双方见底。" in joined
    assert "## 终审" in joined
    assert "反方略稳" in joined
    assert "正方长篇发言不应进主持人时间线" not in joined
    assert '"argument"' not in joined
    assert "scores" not in joined
    # 思考夹在 content 之间：sink 合并 content 时仍能靠标题读。
    kinds = [e.type for e in sink.events]
    assert kinds == [
        EventType.RUN_REASONING_DELTA,
        EventType.RUN_OUTPUT_DELTA,
        EventType.RUN_REASONING_DELTA,
        EventType.RUN_OUTPUT_DELTA,
        EventType.RUN_REASONING_DELTA,
        EventType.RUN_OUTPUT_DELTA,
    ]
