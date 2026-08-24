"""Contract gate with file_write + handoff (empty streamed content)."""

import json

from agentcore.llm.provider.protocol import LLMChunk, ToolCallDelta
from agentcore.runtime.events import EventSink
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.contract import debrief_meets_minimum
from agentcore.runtime.runs.executor import build_agent_executor
from agentcore.runtime.runs.types import RunPhase
from agentcore.runtime.runs.wave import WaveScheduler
from agentcore.tools.builtin.handoff import HandoffTool
from agentcore.tools.registry import ToolRegistry
from tests.runs_executor.conftest import _ContentProvider, _ctx, _FileWriteTool, _ScriptedRounds


async def test_file_write_handoff_empty_content_passes_without_retry():
    """Worker finishes with file_write + handoff and no streamed prose — must not 产出为空 retry."""
    plan, _ = build_run_plan([{"role": "W", "task": "write file"}], id_prefix="t")
    reg = ToolRegistry()
    reg.register(_FileWriteTool())
    reg.register(HandoffTool())
    rounds = [
        [
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id="w1",
                        function_name="file_write",
                        arguments_delta='{"path": "p.txt", "content": "hi"}',
                    )
                ]
            )
        ],
        [
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id="h1",
                        function_name="handoff",
                        arguments_delta='{"summary": "done writing"}',
                    )
                ]
            )
        ],
    ]
    provider = _ScriptedRounds(rounds)
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="req",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    state = res["t_1"]
    assert state.phase is RunPhase.COMPLETED
    assert provider.calls == 2  # no contract retry
    assert state.files_touched == ["p.txt"]
    assert state.debrief == {"summary": "done writing"}


def _is_handoff_gate_feedback(messages) -> bool:  # noqa: ANN001
    """True only for contract handoff-gate retry text (not upstream 交接结论 injection)."""
    joined = "\n".join(m.content or "" for m in messages if m.role == "user")
    return (
        "尚未调用 handoff" in joined
        or "重新调用 handoff" in joined
        or "handoff 交接简报信息量不足" in joined
    )


class _HandoffOnFeedbackProvider:
    """Content first; on handoff-gate feedback, emit a qualifying handoff call."""

    def __init__(self, contents: list[str]) -> None:
        self._contents = contents
        self._content_i = 0
        self.calls = 0
        self.requests: list[list[tuple[str, str]]] = []

    async def stream(self, request):  # noqa: ANN001
        self.calls += 1
        self.requests.append([(m.role, m.content or "") for m in request.messages])
        if _is_handoff_gate_feedback(request.messages):
            args = json.dumps(
                {
                    "summary": "这是一段足够长的合格交接结论，涵盖方案要点与下游接手注意。",
                    "key_points": ["路径 a.py", "约定字段 id"],
                },
                ensure_ascii=False,
            )
            yield LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id=f"h{self.calls}",
                        function_name="handoff",
                        arguments_delta=args,
                    )
                ]
            )
            return
        text = (
            self._contents[self._content_i]
            if self._content_i < len(self._contents)
            else "done"
        )
        self._content_i += 1
        yield LLMChunk(delta_content=text)


async def test_upstream_missing_handoff_forced_then_accepted_on_rework():
    """With handoff offered: missing brief → correction shot → qualifying handoff."""
    plan, _ = build_run_plan(
        [
            {"id": "arch", "role": "架构师", "task": "出方案"},
            {"id": "impl", "role": "实现", "task": "落地", "depends_on": ["arch"]},
        ],
        id_prefix="t",
    )
    reg = ToolRegistry()
    reg.register(HandoffTool())
    provider = _HandoffOnFeedbackProvider(
        [
            (
                "架构草案初版：分层边界、接口形状、错误模型与部署假设已钉死；"
                "鉴权与配额策略也写清，下游可据此实现主路径与错误形状，"
                "观测打点与回滚预案一并列出，缺口处用 escalate 上报即可。"
                "以上内容供下游直接接手，无需再猜接口。"
            ),
            (
                "实现完成正文：主路径与错误形状已覆盖，并与上游接口契约对齐；"
                "关键文件已落盘，可直接联调与验收。"
            ),
        ]
    )
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="req",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    arch = res["t_arch"]
    impl = res["t_impl"]
    assert arch.phase is RunPhase.COMPLETED
    # Correction pass only called handoff — prior prose must not be wiped.
    assert "架构草案初版" in arch.content
    assert arch.debrief is not None
    assert not arch.debrief.get("degraded")
    assert debrief_meets_minimum(arch.debrief)
    assert any(
        "交接" in "\n".join(c for _, c in req if c)
        or "handoff" in "\n".join(c for _, c in req if c).lower()
        for req in provider.requests
    )
    assert impl.phase is RunPhase.COMPLETED
    assert impl.debrief is None


class _EmptyHandoffOnFeedbackProvider:
    """Good prose first; on handoff-gate feedback, call handoff with empty summary."""

    def __init__(self, body: str) -> None:
        self._body = body
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001
        self.calls += 1
        if _is_handoff_gate_feedback(request.messages):
            yield LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id=f"h{self.calls}",
                        function_name="handoff",
                        arguments_delta="{}",
                    )
                ]
            )
            return
        yield LLMChunk(delta_content=self._body)


async def test_handoff_retry_preserves_prior_content_avoids_empty_false_fail():
    """Bug A: handoff-only correction must not drop ~合格正文 →「产出为空」."""
    plan, _ = build_run_plan(
        [
            {"id": "arch", "role": "架构师", "task": "出方案"},
            {"id": "impl", "role": "实现", "task": "落地", "depends_on": ["arch"]},
        ],
        id_prefix="t",
    )
    reg = ToolRegistry()
    reg.register(HandoffTool())
    body = (
        "这是一段已经写好的合格调研正文，篇幅足够，不应在纠正轮被清空。"
        "补充方案边界、依赖假设与建议下一步，以满足上游交接字数门槛；"
        "并列出关键接口形状与错误码约定，供下游直接接手。"
    )
    assert len(body) >= 80
    provider = _EmptyHandoffOnFeedbackProvider(body)
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="req",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    arch = res["t_arch"]
    assert arch.phase is RunPhase.COMPLETED
    assert arch.content == body
    assert not arch.error
    # arch: prose + empty-handoff correction (≥2); impl may add more on shared provider
    assert provider.calls >= 2
    # Thin/empty handoff → engine synth; body still available for synth/downstream.
    assert arch.debrief is not None
    assert arch.debrief.get("degraded") is True
    assert res["t_impl"].phase is RunPhase.COMPLETED


async def test_upstream_without_handoff_tool_synthesizes_degraded_without_rework():
    """Empty registry cannot call handoff — skip correction shot, synth degraded."""
    plan, _ = build_run_plan(
        [
            {"id": "arch", "role": "架构师", "task": "出方案"},
            {"id": "impl", "role": "实现", "task": "落地", "depends_on": ["arch"]},
        ],
        id_prefix="t",
    )
    arch_body = (
        "架构草案初版：选定分层结构、关键接口形状、错误模型与部署边界；"
        "鉴权、配额与观测打点也一并钉死，下游可按此实现并在缺口处 escalate；"
        "回滚预案与兼容窗口也写在交接里，避免下游空转猜测。"
    )
    assert len(arch_body) >= 80, len(arch_body)
    provider = _ContentProvider(
        [arch_body, "实现完成正文，覆盖主路径与错误形状，对齐上游契约，并补充联调说明。"]
    )
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=ToolRegistry(),
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="req",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    arch = res["t_arch"]
    assert arch.phase is RunPhase.COMPLETED
    assert arch.content == arch_body
    assert arch.debrief is not None
    assert arch.debrief.get("degraded") is True
    assert provider.calls == 2  # no wasted rework round
    assert res["t_impl"].debrief is None


async def test_leaf_without_dependents_does_not_force_handoff():
    plan, _ = build_run_plan([{"role": "分析", "task": "只读调研"}], id_prefix="t")
    provider = _ContentProvider(["调研结论一段"])
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=ToolRegistry(),
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="req",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    state = res["t_1"]
    assert state.phase is RunPhase.COMPLETED
    assert state.debrief is None
    assert provider.calls == 1


async def test_leaf_with_tools_missing_handoff_gets_supplement_or_degraded():
    """实质工作（工具轮）却无 handoff 的叶子：补要一轮或 degraded/gap 对账可见."""
    plan, _ = build_run_plan([{"role": "调研", "task": "摸底项目"}], id_prefix="t")
    reg = ToolRegistry()
    reg.register(_FileWriteTool())
    reg.register(HandoffTool())
    # Round 1: tool + short body, no handoff → light-repair 补要.
    # Round 2: still no handoff → terminal degraded synth + delivery_gaps.
    rounds = [
        [
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id="w1",
                        function_name="file_write",
                        arguments_delta='{"path": "notes.md", "content": "# notes"}',
                    )
                ]
            )
        ],
        [LLMChunk(delta_content="摸底笔记已写入 notes.md，技术栈与入口已标出。")],
        [LLMChunk(delta_content="补要轮仍未提交 handoff，保持已写笔记。")],
    ]
    provider = _ScriptedRounds(rounds)
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="req",
        execution_id="e-leaf-tool",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    state = res["t_1"]
    assert state.phase is RunPhase.COMPLETED
    assert provider.calls >= 2  # at least one 补要 / light-repair pass
    assert state.debrief is not None
    assert state.debrief.get("degraded") is True
    assert any(
        isinstance(g, dict) and g.get("reason") == "degraded_handoff"
        for g in (state.delivery_gaps or [])
    )


async def test_leaf_short_body_with_handoff_tool_still_skips_when_no_tools():
    """短叶子纯正文 + handoff 已装配：仍可无 handoff 完成（勿误伤）."""
    plan, _ = build_run_plan([{"role": "分析", "task": "一句话结论"}], id_prefix="t")
    reg = ToolRegistry()
    reg.register(HandoffTool())
    provider = _ContentProvider(["调研结论一段"])
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="req",
        execution_id="e-leaf-short",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    state = res["t_1"]
    assert state.phase is RunPhase.COMPLETED
    assert state.debrief is None
    assert provider.calls == 1
    assert not any(
        isinstance(g, dict) and g.get("reason") == "degraded_handoff"
        for g in (state.delivery_gaps or [])
    )


async def test_leaf_tool_missing_handoff_then_accepted_on_rework():
    """叶子工具活动缺 handoff → 补要反馈后合格 handoff 过关，非 degraded."""
    plan, _ = build_run_plan([{"role": "调研", "task": "摸底"}], id_prefix="t")
    reg = ToolRegistry()
    reg.register(_FileWriteTool())
    reg.register(HandoffTool())

    class _WriteThenHandoffFeedback:
        def __init__(self) -> None:
            self.calls = 0
            self.requests: list = []
            self._wrote = False

        async def stream(self, request):  # noqa: ANN001
            self.calls += 1
            self.requests.append([(m.role, m.content or "") for m in request.messages])
            if not self._wrote:
                self._wrote = True
                yield LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0,
                            id="w1",
                            function_name="file_write",
                            arguments_delta='{"path": "n.md", "content": "x"}',
                        )
                    ]
                )
                return
            if _is_handoff_gate_feedback(request.messages):
                args = json.dumps(
                    {
                        "summary": "这是一段足够长的合格交接结论，涵盖方案要点与下游接手注意。",
                        "key_points": ["路径 n.md", "技术栈已确认"],
                    },
                    ensure_ascii=False,
                )
                yield LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0,
                            id=f"h{self.calls}",
                            function_name="handoff",
                            arguments_delta=args,
                        )
                    ]
                )
                return
            yield LLMChunk(
                delta_content=(
                    "摸底正文：已读入口与 README，技术栈与模块边界已标出，"
                    "进度与风险亦写明，可直接给主管汇总。"
                )
            )

    provider = _WriteThenHandoffFeedback()
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="req",
        execution_id="e-leaf-rework",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    state = res["t_1"]
    assert state.phase is RunPhase.COMPLETED
    assert state.debrief is not None
    assert not state.debrief.get("degraded")
    assert debrief_meets_minimum(state.debrief)
    assert any(
        "实质工作" in "\n".join(c for _, c in req if c)
        or "尚未调用 handoff" in "\n".join(c for _, c in req if c)
        for req in provider.requests
    )

async def test_artifacts_missing_soft_completes_without_write_pass():
    """甲⁺：artifacts 隐含 requires_files；零落盘 soft-complete，不 write_pass / FAILED。

    路径对账（声明文件名）仍为 warning；零落盘亦 soft tip。
    """
    plan, _ = build_run_plan(
        [
            {
                "role": "集成",
                "task": "收口",
                "deliverable": {"artifacts": ["README.md", "examples/demo.py"]},
            }
        ],
        id_prefix="t",
    )
    provider = _ContentProvider(["只写了正文一", "只写了正文二仍缺文件"])
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=ToolRegistry(),
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="req",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    state = res["t_1"]
    assert state.phase is RunPhase.COMPLETED
    assert provider.calls == 1  # 无 write_pass
    assert state.files_touched == []
    assert any("未把产物写入工作区" in w for w in (state.warnings or []))
    assert any("README.md" in w for w in (state.warnings or []))


async def test_artifacts_hit_when_file_write_covers_declared_path():
    plan, _ = build_run_plan(
        [
            {
                "role": "集成",
                "task": "收口",
                "deliverable": {"artifacts": ["README.md"]},
            }
        ],
        id_prefix="t",
    )
    reg = ToolRegistry()
    reg.register(_FileWriteTool())
    rounds = [
        [
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id="w1",
                        function_name="file_write",
                        arguments_delta='{"path": "README.md", "content": "# hi"}',
                    )
                ]
            )
        ],
        [LLMChunk(delta_content="已写入 README")],
    ]
    provider = _ScriptedRounds(rounds)
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="req",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    state = res["t_1"]
    assert state.phase is RunPhase.COMPLETED
    assert "README.md" in (state.files_touched or [])
    # 叶子有落盘却无 handoff → degraded 对账可见（空 registry 无补要轮，仍 stamp gap）。
    assert state.debrief is not None and state.debrief.get("degraded")
    assert any(
        isinstance(g, dict) and g.get("reason") == "degraded_handoff"
        for g in (state.delivery_gaps or [])
    )


async def test_strict_degraded_handoff_completes_when_files_landed():
    """刀1 / 方案 A：strict + 已落盘 + degraded synth → COMPLETED（备注，非整单 FAILED）。"""
    plan, _ = build_run_plan(
        [
            {
                "id": "sec",
                "role": "分区",
                "task": "写片段",
                "deliverable": {
                    "form": "files",
                    "artifacts": ["site/sections/s0.html"],
                    "strict": True,
                },
            },
            {
                "id": "asm",
                "role": "组装",
                "task": "组装",
                "depends_on": ["sec"],
                "deliverable": {"form": "files", "artifacts": ["site/index.html"]},
            },
        ],
        id_prefix="t",
    )
    reg = ToolRegistry()
    reg.register(_FileWriteTool())
    # Write artifact so contract exists, but never call handoff → degraded synth
    # after handoff correction shot still empty → 有落盘则放行 COMPLETED。
    from agentcore.runtime.runs.research_quality import MIN_UPSTREAM_BODY_CHARS

    body_pad = "分区正文填充。" * ((MIN_UPSTREAM_BODY_CHARS // 7) + 1)
    rounds = [
        [
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id="w1",
                        function_name="file_write",
                        arguments_delta=(
                            '{"path": "site/sections/s0.html",'
                            ' "content": "<section>hero</section>"}'
                        ),
                    )
                ]
            )
        ],
        [
            LLMChunk(
                delta_content=(
                    "分区片段已写入 site/sections/s0.html，含英雄区结构与文案键位。"
                    + body_pad
                )
            )
        ],
        [LLMChunk(delta_content="纠正轮仍未提交合格 handoff，保持已落盘片段不变。")],
    ]
    provider = _ScriptedRounds(rounds)
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="req",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    sec = res["t_sec"]
    assert sec.phase is RunPhase.COMPLETED
    assert sec.debrief and sec.debrief.get("degraded")
    assert "site/sections/s0.html" in (sec.files_touched or [])
    # 已落盘文件不得因交接降级被整单 rejected。
    assert any(
        isinstance(a, dict) and a.get("path") == "site/sections/s0.html"
        and a.get("status") == "accepted"
        for a in (sec.file_acceptance or [])
    )
    assert any(
        isinstance(g, dict) and g.get("reason") == "degraded_handoff"
        for g in (sec.delivery_gaps or [])
    )


async def test_strict_zero_landing_soft_completes_without_degraded_dependents():
    """甲⁺：单节点 strict + 零落盘（无下游 degraded）→ soft-complete，不 FAILED。"""
    plan, _ = build_run_plan(
        [
            {
                "id": "sec",
                "role": "分区",
                "task": "写片段",
                "deliverable": {
                    "form": "files",
                    "artifacts": ["site/sections/s0.html"],
                    "strict": True,
                    "requires_files": True,
                },
            },
        ],
        id_prefix="t",
    )
    from agentcore.runtime.runs.research_quality import MIN_UPSTREAM_BODY_CHARS

    body_pad = "分区正文填充。" * ((MIN_UPSTREAM_BODY_CHARS // 7) + 1)
    provider = _ContentProvider(["只有文字没有落盘。" + body_pad])
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=ToolRegistry(),
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="req",
        execution_id="e-nofile",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    sec = res.get("t_sec") or res.get("t_1") or next(iter(res.values()))
    assert sec.phase is RunPhase.COMPLETED
    assert not (sec.files_touched or [])
    assert any("未把产物写入工作区" in w for w in (sec.warnings or []))
