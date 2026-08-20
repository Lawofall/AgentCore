"""辩论异常收尾契约自测（per-PR 零 LLM）—— 终帧必发 + 已跑完的产物必须留下。

覆盖两条同根缺陷的回归面：

1. **主持人节点终帧必发**：异常路径此前只 ``return err(...)``，``run_started`` 之后再无终帧
   ——协作图上主持人节点永久转圈（CEO 回合本身仍以 completed 收口，前端「整回合失败冻结
   running」的兜底不生效，重载重放照样转），且主持人自身几次 LLM 调用整笔丢账。
2. **收场一次抖动不得吞掉整场**：结辩 ∥ 简报的 ``asyncio.gather`` 未设 ``return_exceptions``，
   任一失败都让已跑完的 N 轮发言 / 质询 / 裁判 / 小结全部作废；``debate_result`` 是收场的
   journal 权威，它不发辩论室就永远停在「进行中」。

降级口径：留下已跑轮次 + 诚实说明缺了什么，**不**补重试、**不**编兜底结论。
"""

import asyncio
import json

import pytest

from agentcore.llm.provider.protocol import LLMChunk, LLMResponse, TokenUsage
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.terminal import RUN_CLOSE_EVENT_TYPES
from agentcore.tools.builtin.debate import DebateTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace

_USAGE = TokenUsage(
    input_tokens=10,
    output_tokens=5,
    reasoning_tokens=0,
    cache_hit_tokens=6,
    cache_miss_tokens=4,
)
_BRIEF = {
    "crux": "做不做 X 的核心权衡",
    "strongest_points": {"pro": "正方最强论点", "con": "反方最强论点"},
    "leaning": "基于事实反方略稳",
    "confidence": "中",
    "recommendation": "先小步验证再决定",
}


class _DebateLLM:
    """假 provider：``crash_step`` 命中的主持人步骤直接抛错，其余按脚本正常返回。

    ``crash_step`` 取 ``brief`` / ``assess`` 等 scenario 末段，模拟收场（或裁判）那一次
    网关抖动；``converge_at`` 控制裁判第几轮判收敛。
    """

    def __init__(self, *, crash_step: str = "", converge_at: int = 1) -> None:
        self.crash_step = crash_step
        self.converge_at = converge_at
        self.judge_calls = 0
        self.brief_calls = 0
        self.stream_calls = 0

    async def complete(self, request):  # noqa: ANN001
        step = (request.scenario or "").rsplit(".", 1)[-1]
        if step == self.crash_step:
            if step == "brief":
                self.brief_calls += 1
            raise RuntimeError(f"upstream 503 on {step}")
        if step == "frame":
            return LLMResponse(content=json.dumps({"focus": "本轮焦点"}), usage=_USAGE)
        if step == "assess":
            self.judge_calls += 1
            converged = self.judge_calls >= self.converge_at
            return LLMResponse(
                content=json.dumps(
                    {
                        "real_clash": True,
                        "new_arguments": not converged,
                        "converged": converged,
                        "stop_reason": "converged",
                        "next_focus": "更深的点",
                        "rationale": "理由",
                        "summary": "本轮小结",
                    }
                ),
                usage=_USAGE,
            )
        if step == "brief":
            self.brief_calls += 1
            return LLMResponse(content=json.dumps(_BRIEF), usage=_USAGE)
        return LLMResponse(content="{}", usage=_USAGE)

    async def stream(self, request):  # noqa: ANN001
        self.stream_calls += 1
        yield LLMChunk(delta_content=f"辩手发言#{self.stream_calls}")
        yield LLMChunk(usage=_USAGE)


class _HangContinueLLM(_DebateLLM):
    """首轮正常说完；一旦 journal 出现续写 ``run_started``，后续 stream 挂住等取消。"""

    def __init__(self, sink: EventSink, **kwargs) -> None:  # noqa: ANN003
        super().__init__(**kwargs)
        self._sink = sink

    async def stream(self, request):  # noqa: ANN001
        if any(
            e.type is EventType.RUN_STARTED and e.payload.get("continues_run_id")
            for e in self._sink._history  # noqa: SLF001
        ):
            self.stream_calls += 1
            yield LLMChunk(delta_content="半成品")
            await asyncio.sleep(60)
            yield LLMChunk(delta_content="…")
            return
        async for chunk in super().stream(request):
            yield chunk


def _ctx(tmp_path) -> ToolContext:  # noqa: ANN001
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox()),
        user_id="u",
    )


def _sides() -> list[dict]:
    return [
        {"key": "pro", "name": "正方", "stance": "支持做 X"},
        {"key": "con", "name": "反方", "stance": "反对做 X"},
    ]


def _tool(llm, *, ctx, sink) -> DebateTool:  # noqa: ANN001
    return DebateTool(
        llm=llm,
        sink=sink,
        system_prompt="SYS",
        user_message="原始请求",
        tools=ToolRegistry(),
        base_tool_context=ctx,
        captain_run_id="captain1",
        approval_gate=None,
    )


async def _drain(sink: EventSink) -> list:
    sink.close()
    return [e async for e in sink]


def _moderator_run_id(events: list) -> str:
    """主持人节点 id —— 取自它自己的 run_plan 声明（辩手 run_id 同以 ``debate_`` 起头）。"""
    for e in events:
        if e.type != EventType.RUN_PLAN:
            continue
        for agent in e.payload.get("agents") or []:
            if agent.get("role") == "主持人":
                return str(agent["id"])
    raise AssertionError("未找到主持人节点声明")


def _terminal_frames(events: list, run_id: str) -> list:
    """某个 run 的全部终帧（完成 / 失败 / 取消）——收尾必有一帧、且只有一帧。"""
    terminal = {EventType.RUN_COMPLETED, EventType.RUN_FAILED, EventType.RUN_CANCELLED}
    return [
        e for e in events if e.type in terminal and e.payload.get("run_id") == run_id
    ]


def _assert_started_runs_have_terminal(events: list) -> None:
    """每个 ``run_started`` 的 run_id 之后必有 occupancy 关帧之一。"""
    started: set[str] = set()
    closed: set[str] = set()
    for e in events:
        rid = (e.payload or {}).get("run_id")
        if not isinstance(rid, str) or not rid:
            continue
        if e.type is EventType.RUN_STARTED:
            started.add(rid)
        elif e.type in RUN_CLOSE_EVENT_TYPES:
            closed.add(rid)
    missing = sorted(started - closed)
    assert not missing, f"run_started 之后无终态帧: {missing}"


def _moderator_ledger_row(tool: DebateTool):
    """主持人那一行账目（parent = captain）——异常不是不花钱，用量不得丢账。"""
    rows = [r for r in tool.run_ledger if r.parent_run_id == "captain1"]
    assert len(rows) == 1, f"主持人账目行应恰好 1 行，实得 {len(rows)}"
    return rows[0]


async def test_brief_crash_at_closing_keeps_rounds_and_settles_moderator(tmp_path):
    """收场简报抛异常（结辩 ∥ 简报 gather 一半崩）→ 已跑轮次 + 结辩照常交付，简报诚实降级。

    这是「一次 LLM 抖动吞掉整场双产物」的正面回归：此前 gather 未设 return_exceptions，
    异常一路冲出 Moderator.run，整场作废、debate_result 永不发射。
    """
    llm = _DebateLLM(crash_step="brief")
    sink = EventSink()
    tool = _tool(llm, ctx=_ctx(tmp_path), sink=sink)
    result = await tool.execute(
        {"motion": "该不该做 X", "form": "debate", "sides": _sides()},
        _ctx(tmp_path),
    )
    assert llm.brief_calls == 1  # 确实走到了收场简报、且确实抛了

    # 整场没有作废：工具照常回双产物位（简报那半诚实缺位），CEO 拿得到已跑轮次。
    assert result.success is True
    assert "交锋叙事线" in result.output
    assert "【收场简报缺失】" in result.output

    events = await _drain(sink)
    results = [e for e in events if e.type == EventType.DEBATE_RESULT]
    assert len(results) == 1, "debate_result 是收场的 journal 权威，缺它辩论室永远转圈"
    payload = results[0].payload
    assert len(payload["rounds"]) == 1
    assert payload["rounds"][0]["sides"]

    # 降级产物 = 有已跑轮次 + 缺结辩/简报，且诚实说明缺了什么、不编结论。
    brief = payload["brief"]
    assert "【收场简报缺失】" in brief["recommendation"]
    assert "完整保留" in brief["recommendation"]
    assert brief["leaning"] == ""
    assert brief["confidence"] == ""
    assert brief["handoffs"] == []
    # 结辩那一半没崩，照常落地（不因简报失败连坐）。
    assert [c["key"] for c in payload["closings"]] == ["pro", "con"]
    assert all(c["ok"] for c in payload["closings"])

    # 主持人节点落终态（正常收场：run_completed），且只有一帧。
    mod_run_id = payload["moderator_run_id"]
    frames = _terminal_frames(events, mod_run_id)
    assert [f.type for f in frames] == [EventType.RUN_COMPLETED]

    # 用量已入账（主持人 frame / assess 那几次调用照样花了钱）。
    assert _moderator_ledger_row(tool).tokens["input"] > 0
    assert tool.usage["input"] > 0


async def test_brief_crash_without_closing_beat_still_lands_result(tmp_path):
    """快速对碰（无结辩拍，走单发 _brief 分支）：简报抛异常同样只作废简报，不作废本轮。"""
    llm = _DebateLLM(crash_step="brief")
    sink = EventSink()
    tool = _tool(llm, ctx=_ctx(tmp_path), sink=sink)
    result = await tool.execute(
        {"motion": "该不该做 X", "form": "debate", "sides": _sides(), "thorough": False},
        _ctx(tmp_path),
    )
    assert result.success is True

    events = await _drain(sink)
    results = [e for e in events if e.type == EventType.DEBATE_RESULT]
    assert len(results) == 1
    payload = results[0].payload
    assert len(payload["rounds"]) == 1
    assert payload["closings"] == []  # 快速档本就无结辩
    assert "【收场简报缺失】" in payload["brief"]["recommendation"]
    assert _terminal_frames(events, payload["moderator_run_id"])


async def test_closing_crash_keeps_brief_and_rounds(tmp_path, monkeypatch):
    """结辩整拍崩（gather 的另一半）→ 结辩区留空，但简报与叙事线照常交付。"""
    from agentcore.tools.builtin.debate import tool as tool_mod

    def _boom_closing_runner(*_a, **_k):  # noqa: ANN002, ANN003
        async def run_closing(*, sides, rounds):  # noqa: ANN001, ARG001
            raise RuntimeError("closing wave 503")

        return run_closing

    monkeypatch.setattr(tool_mod, "make_closing_runner", _boom_closing_runner)

    llm = _DebateLLM()
    sink = EventSink()
    tool = _tool(llm, ctx=_ctx(tmp_path), sink=sink)
    result = await tool.execute(
        {"motion": "该不该做 X", "form": "debate", "sides": _sides()},
        _ctx(tmp_path),
    )
    assert result.success is True

    events = await _drain(sink)
    payload = next(e for e in events if e.type == EventType.DEBATE_RESULT).payload
    assert len(payload["rounds"]) == 1
    assert payload["closings"] == []  # 结辩缺位（前端据空列表不出结辩区）
    # 简报没被连坐：真结论仍在。
    assert payload["brief"]["recommendation"] == "先小步验证再决定"
    assert payload["brief"]["leaning"] == "基于事实反方略稳"
    assert _terminal_frames(events, payload["moderator_run_id"])


async def test_moderator_crash_emits_run_failed_terminal_frame(tmp_path):
    """主持人循环中途崩（裁判那次调用抛异常）→ run_failed 终帧必发 + 用量仍入账。

    这是「协作图永久转圈 + 主持人 token 全额丢账」的正面回归：此前 run_started 之后
    异常路径只 return err(...)，节点再无终帧。
    """
    llm = _DebateLLM(crash_step="assess")
    sink = EventSink()
    tool = _tool(llm, ctx=_ctx(tmp_path), sink=sink)
    result = await tool.execute(
        {"motion": "该不该做 X", "form": "debate", "sides": _sides(), "thorough": False},
        _ctx(tmp_path),
    )
    assert result.success is False
    assert "辩论执行失败" in (result.error or "")

    events = await _drain(sink)
    mod_run_id = _moderator_run_id(events)
    started = [
        e
        for e in events
        if e.type == EventType.RUN_STARTED and e.payload.get("run_id") == mod_run_id
    ]
    assert len(started) == 1, "主持人节点开播帧"

    frames = _terminal_frames(events, mod_run_id)
    assert [f.type for f in frames] == [EventType.RUN_FAILED], "开播了就必须收尾"
    assert "upstream 503" in frames[0].payload["error"]

    # 崩溃不等于不花钱：主持人已跑完的 LLM 调用照常落一行账目 + 折回回合用量。
    assert _moderator_ledger_row(tool).tokens["input"] > 0
    assert tool.usage["input"] > 0


async def test_debater_crash_does_not_sink_the_whole_wave(tmp_path, monkeypatch):
    """一方续轮 continue_run 抛异常 → 该方本轮缺席，同波他方发言与整场照常收场。

    默认 gather 会让第一个异常掀翻整波、再冲出 Moderator.run 把前面所有轮次一起带走。
    """
    import agentcore.runtime.runs as runs_mod

    real_continue = runs_mod.continue_run

    async def _crashing_continue(*, continuation_run_id, **kwargs):  # noqa: ANN003
        if continuation_run_id.endswith("_r2_pro"):
            raise RuntimeError("gateway boom")
        return await real_continue(continuation_run_id=continuation_run_id, **kwargs)

    monkeypatch.setattr(runs_mod, "continue_run", _crashing_continue)

    llm = _DebateLLM(converge_at=2)
    sink = EventSink()
    tool = _tool(llm, ctx=_ctx(tmp_path), sink=sink)
    result = await tool.execute(
        {"motion": "该不该做 X", "form": "debate", "sides": _sides()},
        _ctx(tmp_path),
    )
    assert result.success is True

    events = await _drain(sink)
    payload = next(e for e in events if e.type == EventType.DEBATE_RESULT).payload
    assert len(payload["rounds"]) == 2, "崩的那一方不该把整场带走"
    round2 = {s["key"]: s for s in payload["rounds"][1]["sides"]}
    assert round2["pro"]["ok"] is False
    assert round2["pro"]["absent"] is True  # 缺席轮一等语义，与网关重试耗尽同口径
    assert round2["con"]["ok"] is True
    assert _terminal_frames(events, payload["moderator_run_id"])


async def test_later_round_cancel_closes_started_continue_runs(tmp_path):
    """后续轮 continue_run 中途取消：已 ``run_started`` 的续写必有终态帧。

    ``_gather_settled`` 对 CancelledError 再抛且不补帧，主持人 finally 只收主持人自己。
    修在 ``continue_run`` 的 finally（与 Wave 队员 ``run_cancelled`` 同口径），取消仍上抛。
    """
    sink = EventSink()
    llm = _HangContinueLLM(sink, converge_at=2)
    tool = _tool(llm, ctx=_ctx(tmp_path), sink=sink)
    task = asyncio.create_task(
        tool.execute(
            {"motion": "该不该做 X", "form": "debate", "sides": _sides()},
            _ctx(tmp_path),
        )
    )
    for _ in range(500):
        if any(
            e.type is EventType.RUN_STARTED and e.payload.get("continues_run_id")
            for e in sink._history  # noqa: SLF001
        ):
            break
        await asyncio.sleep(0.02)
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        pytest.fail("辩论后续轮 continue_run 从未发出 run_started")

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    events = list(sink._history)  # noqa: SLF001
    _assert_started_runs_have_terminal(events)
    cont_ids = {
        e.payload["run_id"]
        for e in events
        if e.type is EventType.RUN_STARTED and e.payload.get("continues_run_id")
    }
    assert cont_ids, "应至少有一个续写 run 已开播"
    for rid in cont_ids:
        terms = [
            e
            for e in events
            if e.type in RUN_CLOSE_EVENT_TYPES and e.payload.get("run_id") == rid
        ]
        assert terms, f"{rid} started without terminal"
        assert terms[0].type is EventType.RUN_CANCELLED


async def test_gather_settled_cancel_waits_for_children_and_reraises():
    """整轮停止不得当成某方缺席吞掉；子任务必须拆完（Wave shield 口径）再上抛。"""
    from agentcore.runtime.debate.rounds import _gather_settled

    cleaned: list[str] = []

    async def _hang(key: str):
        try:
            await asyncio.sleep(30)
            return key, 1
        except asyncio.CancelledError:
            cleaned.append(key)
            raise

    task = asyncio.create_task(
        _gather_settled(
            (_hang("a"), _hang("b")),
            fallback=(None, 0),
            beat="statement",
            round_no=2,
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert set(cleaned) == {"a", "b"}
