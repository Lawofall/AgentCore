import asyncio
from dataclasses import replace

from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.llm.provider.protocol import LLMChunk, TokenUsage, ToolCallDelta
from agentcore.runtime.costing import WorkerResultAccumulator
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.executor import build_agent_executor
from agentcore.runtime.runs.executor.identities import LeadSubteam
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import BatchMetrics, RunPhase, RunSpec
from agentcore.runtime.runs.wave import WaveScheduler
from agentcore.tools.builtin.escalate import EscalateTool
from agentcore.tools.protocol import ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from tests.runs_executor.conftest import (
    _ContentProvider,
    _ctx,
    _executor,
    _flash_profiles,
    _ScriptedRounds,
)


class _StubDelegate:
    """A minimal ORCHESTRATION tool named 'delegate' — never executed here; the
    fake LLM emits no tool call, so we only assert it was (or wasn't) minted."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="delegate",
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        return ToolResult(tool_call_id="", success=True, output="")


class _StubReplan:
    """Companion replan on the LeadSubteam bundle — opening offer must omit it."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="replan",
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        return ToolResult(tool_call_id="", success=True, output="")


async def _noop_dispose() -> None:
    return None


def _stub_subteam() -> LeadSubteam:
    """The factory's return shape (受监督子计划 B): a lead's delegate + replan bundle.
    Opening registry registers delegate only; these identity / depth-cap tests
    still only care that a bundle is minted."""
    stub = _StubDelegate()
    replan = _StubReplan()
    return LeadSubteam(
        tools=(stub, replan),
        tool_names=(stub.schema.name, replan.schema.name),
        dispose=_noop_dispose,
    )


class _RecordToolsProvider(_ContentProvider):
    """Records OpenAI tool names offered on each LLM request."""

    def __init__(self, contents: list[str]) -> None:
        super().__init__(contents)
        self.tool_names: list[list[str]] = []

    async def stream(self, request):  # noqa: ANN001
        names: list[str] = []
        for item in request.tools or []:
            if isinstance(item, dict):
                fn = item.get("function") or {}
                if isinstance(fn, dict) and fn.get("name"):
                    names.append(str(fn["name"]))
        self.tool_names.append(names)
        async for chunk in super().stream(request):
            yield chunk


def _spec(run_id: str, *, depth: int):
    return RunSpec(
        run_id=run_id,
        agent_id=run_id,
        role="W",
        task="t",
        depth=depth,
    )


def _nesting_executor(plan: RunPlan, provider, factory):
    return build_agent_executor(
        plan=plan,
        llm=provider,
        tools=ToolRegistry(),
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
        delegate_factory=factory,
    )


async def test_nested_delegate_offered_only_within_depth_cap():
    calls: list[tuple[str, int]] = []

    def factory(captain_run_id: str, captain_depth: int):
        calls.append((captain_run_id, captain_depth))
        return _stub_subteam()

    plan = RunPlan()
    plan.add(_spec("d1", depth=1))
    plan.add(_spec("d2", depth=2))  # within cap (MAX=3)
    plan.add(_spec("d3", depth=3))  # at the cap → leaf
    executor = _nesting_executor(plan, _ContentProvider(["X", "Y", "Z"]), factory)
    await executor(plan.by_id("d1"), {})
    await executor(plan.by_id("d2"), {})
    await executor(plan.by_id("d3"), {})
    # depth-1 and depth-2 workers (within the cap) get a delegate tool;
    # depth-3 (at the cap) never does — delegation is on by default, the
    # depth cap is the hard stop.
    assert calls == [("d1", 1), ("d2", 2)]


async def test_nested_delegate_withheld_at_depth_cap():
    calls: list[str] = []

    def factory(captain_run_id: str, captain_depth: int):
        calls.append(captain_run_id)
        return _stub_subteam()

    plan = RunPlan()
    plan.add(_spec("d3", depth=3))  # at the cap
    executor = _nesting_executor(plan, _ContentProvider(["X"]), factory)
    await executor(plan.by_id("d3"), {})
    assert calls == []  # depth-3 sub-worker → leaf, no delegate tool


async def test_captain_worker_gets_captain_identity_and_delegate_tool():
    provider = _ContentProvider(["X"])
    plan = RunPlan()
    plan.add(_spec("d1", depth=1))
    executor = _nesting_executor(plan, provider, lambda rid, d: _stub_subteam())
    await executor(plan.by_id("d1"), {})
    # A within-cap worker is told it may lead a nested sub-team (on by default).
    assert "再向下委派一层子团队" in provider.system_messages[0]
    # depth-1 children may still nest — honesty must not claim they cannot.
    assert "你的子成员仍可再向下委派一层" in provider.system_messages[0]
    assert "你的子成员不能再向下委派" not in provider.system_messages[0]


async def test_captain_worker_opening_omits_replan():
    """开场只挂 delegate；bundle 里的 companion replan 要等子计划存在才 offer。"""
    provider = _RecordToolsProvider(["X"])
    plan = RunPlan()
    plan.add(_spec("d1", depth=1))
    executor = _nesting_executor(plan, provider, lambda rid, d: _stub_subteam())
    await executor(plan.by_id("d1"), {})
    assert provider.tool_names, "expected at least one LLM request"
    opening = provider.tool_names[0]
    assert "delegate" in opening
    assert "replan" not in opening


async def test_default_worker_is_captain_within_depth_cap():
    provider = _ContentProvider(["X"])
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    # Delegation is on by default — a depth-1 worker within the cap is a captain.
    executor = _nesting_executor(plan, provider, lambda rid, d: _stub_subteam())
    await executor(plan.by_id("t_1"), {})
    sys = provider.system_messages[0]
    # Captain-only markers — the leaf intro carries neither.
    assert "再向下委派一层子团队" in sys
    assert "不要为委派而委派" in sys


async def test_depth_two_captain_children_are_leaves():
    """depth-2 is still captain (MAX=3); honesty says its children cannot nest."""
    provider = _ContentProvider(["X"])
    plan = RunPlan()
    plan.add(_spec("d2", depth=2))
    executor = _nesting_executor(plan, provider, lambda rid, d: _stub_subteam())
    await executor(plan.by_id("d2"), {})
    sys = provider.system_messages[0]
    assert "再向下委派一层子团队" in sys
    assert "只能再嵌套这一层，你的子成员不能再向下委派" in sys
    assert "你的子成员仍可再向下委派一层" not in sys


async def test_captain_identity_carries_when_to_split_guidance():
    provider = _ContentProvider(["X"])
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    executor = _nesting_executor(plan, provider, lambda rid, d: _stub_subteam())
    await executor(plan.by_id("t_1"), {})
    sys = provider.system_messages[0]
    assert "再向下委派一层子团队" in sys
    assert "不要为委派而委派" in sys
    assert "consult(team_orchestration_advanced)" not in sys
    assert "先招人再整合" in sys
    assert "未钉成单切片" in sys
    assert "不是先深读再招" in sys
    assert "薄切片" in sys and "整座仓" in sys
    assert "escalate（范围）" in sys
    assert "禁止默默扩编" in sys
    assert "有 delegate 就可以招" in sys
    assert "先组队" in sys
    assert "本来就小" in sys
    assert "不授权一个人扛里程碑" in sys
    assert "你的子成员仍可再向下委派一层" in sys
    # 嵌套 lead 编排 HOW 住 identity（已有 captain 分叉），不进共享目录。
    assert "怎么拆" in sys
    assert "假两段" in sys
    assert "何时不该拆" in sys
    assert "计划已让出" in sys
    assert "replan" in sys
    # Path-B encyclopedia 仍不进 identity。
    from agentcore.runtime.runs.executor.identities import build_worker_identity

    identity = build_worker_identity(has_dependents=False, captain=True)
    assert "优先先嵌套" not in identity
    assert "未嵌套禁写" not in identity
    assert "凡大活" not in identity
    assert "共写同一目标文件" not in identity
    assert "豁免" not in identity
    assert "4 个 sub-worker" not in identity
    leaf = build_worker_identity(has_dependents=False, captain=False)
    assert "怎么拆" not in leaf
    assert "计划已让出" not in leaf


async def test_depth_three_subworker_keeps_leaf_identity():
    provider = _ContentProvider(["X"])
    plan, _ = build_run_plan(
        [{"role": "A", "task": "做A"}],
        id_prefix="t",
        parent_run_id="cap",
        depth=3,
    )
    # At the depth cap: delegate tools withheld — depth-3 sub-workers are always leaves.
    executor = _nesting_executor(plan, provider, lambda rid, d: _stub_subteam())
    await executor(plan.by_id("t_1"), {})
    assert "不能再向下委派" in provider.system_messages[0]
    assert "再向下委派一层子团队" not in provider.system_messages[0]


async def test_worker_identities_carry_tool_safety_caution():
    # 按角色 right-size (反向): the environment-mutation caution (<tool_safety>) moved OUT of
    # the shared base (where the read-only coordinator CEO carried it inertly) INTO the worker
    # identities — workers hold the mutating tools (file_write / code_execute / file_delete…),
    # so the caution rides them now. Pin it on BOTH the leaf and the captain identity so a
    # refactor can't drop the mutation caution from the agents that can actually act
    # (the absence-from-base/CEO side is pinned in tests/test_prompt.py).
    leaf_provider = _ContentProvider(["X"])
    leaf_plan, _ = build_run_plan(
        [{"role": "A", "task": "做A"}],
        id_prefix="t",
        parent_run_id="cap",
        depth=3,  # depth cap → leaf identity
    )
    leaf_exec = _nesting_executor(leaf_plan, leaf_provider, lambda rid, d: _stub_subteam())
    await leaf_exec(leaf_plan.by_id("t_1"), {})
    leaf_sys = leaf_provider.system_messages[0]
    assert "<tool_safety>" in leaf_sys
    assert "本地模式" in leaf_sys

    captain_provider = _ContentProvider(["Y"])
    captain_plan = RunPlan()
    captain_plan.add(_spec("d1", depth=1))
    captain_exec = _nesting_executor(captain_plan, captain_provider, lambda rid, d: _stub_subteam())
    await captain_exec(captain_plan.by_id("d1"), {})
    captain_sys = captain_provider.system_messages[0]
    assert "再向下委派一层子团队" in captain_sys  # captain identity in play
    assert "<tool_safety>" in captain_sys


async def test_handoff_prompt_splits_by_topology():
    """Identity handoff wording tracks DAG dependents (接力契约 + 增量交代).

    Upstream (has_dependents) gets the imperative「必须调用」; a leaf gets
    substantial-work guidance + short-answer exemption「不必为交而交」— aligned
    with the engine gate and the handoff tool description.
    """
    from agentcore.runtime.runs.executor.identities import build_worker_identity
    from agentcore.tools.builtin.handoff import HandoffTool

    upstream = build_worker_identity(has_dependents=True, captain=False)
    leaf = build_worker_identity(has_dependents=False, captain=False)
    assert "必须调用 handoff" in upstream
    assert "接力契约 + 增量交代" in upstream
    assert "不必为交而交" not in upstream

    prose_up = build_worker_identity(
        has_dependents=True, captain=False, form="prose"
    )
    assert "summary 不算正文" in prose_up
    assert "加长 summary 也不能代替正文" in prose_up
    assert "交接勿回灌" in prose_up
    files_leaf = build_worker_identity(
        has_dependents=False, captain=False, form="files"
    )
    assert "交接勿回灌" in files_leaf
    assert "落盘产物是给人读的完整说明" in files_leaf
    # files 叶子走 pointer：简报是 CEO 唯一信息源，必须保留结论性。
    assert "summary（结论）" in files_leaf
    assert "一句话说清你这次做出了什么" in files_leaf
    assert "正文里已经写过的结论" not in files_leaf
    assert "一行标题" not in files_leaf
    artifacts_leaf = build_worker_identity(
        has_dependents=False, artifacts=["report.md"]
    )
    assert "form=files" in artifacts_leaf
    assert "summary（结论）" in artifacts_leaf
    assert "正文里已经写过的结论" not in artifacts_leaf

    prose_leaf = build_worker_identity(
        has_dependents=False, captain=False, form="prose"
    )
    assert "给人读的说明" in prose_leaf
    assert "结论、根因、关键取舍" in prose_leaf
    assert "一行标题" in prose_leaf
    assert "接力状态" in prose_leaf
    assert "正文里已经写过的结论" in prose_leaf
    assert "summary（结论）" not in prose_leaf
    assert "一句话说清你这次做出了什么" not in prose_leaf

    assert "不必为交而交" in leaf
    assert "接力契约 + 增量交代" in leaf
    assert "必须调用 handoff" not in leaf
    # 省略 form 的叶子也可能落盘 → 保留结论性，宁可重复不要空洞。
    assert "给人读的说明" in leaf
    assert "结论、根因、关键取舍" in leaf
    assert "summary（结论）" in leaf
    assert "一句话说清你这次做出了什么" in leaf
    assert "正文里已经写过的结论" not in leaf
    assert "一行标题" not in leaf
    assert "正文里已经写过的结论" not in upstream
    assert "summary（结论）" in upstream
    assert "一句话说清你这次做出了什么" in upstream
    assert "一行标题" not in upstream
    # 巡检定案 B：交付各一句防回灌（leaf / upstream / 各 form 同源）
    assert "交接勿回灌" in leaf and "交接勿回灌" in upstream
    assert "修复完成" in leaf and "已修复" in leaf
    assert "现象已消除" in leaf and "已全部落地" in leaf
    assert "系统已就绪" in leaf and "界面没改" in leaf
    assert "最后一次同命令" in leaf and "分项分开写" in leaf
    assert "有工具活动或较长交付" in leaf
    assert "汇报不完整" in leaf
    assert "权威文档冲突" in leaf
    assert "静默改权威稿" in leaf
    # 开局找路径轻 nudge：含糊「根」先 list/grep
    assert "找路径" in leaf
    assert "含糊" in leaf and "根" in leaf
    assert "file_list" in leaf

    # Executor wires topology into the live system prompt (not just the helper).
    plan, _ = build_run_plan(
        [
            {"id": "arch", "role": "调研", "task": "查资料"},
            {
                "id": "impl",
                "role": "写手",
                "task": "成文",
                "depends_on": ["arch"],
            },
        ],
        id_prefix="t",
    )
    up_provider = _ContentProvider(["UP"])
    leaf_provider = _ContentProvider(["LEAF"])
    await _nesting_executor(plan, up_provider, lambda rid, d: _stub_subteam())(
        plan.by_id("t_arch"), {}
    )
    await _nesting_executor(plan, leaf_provider, lambda rid, d: _stub_subteam())(
        plan.by_id("t_impl"), {}
    )
    assert "必须调用 handoff" in up_provider.system_messages[0]
    assert "不必为交而交" in leaf_provider.system_messages[0]
    assert "必须调用 handoff" not in leaf_provider.system_messages[0]

    # Tool description covers both branches so it never fights either prompt.
    desc = HandoffTool().schema.description
    assert "接力契约 + 增量交代" in desc
    assert "必须" in desc
    assert "不必为交而交" in desc or "短答自明可省" in desc


def test_worker_identity_states_no_execution_capability():
    """能写≠能跑（能力闸门与交付诚实性）：执行类未装配时 identity 自述能力边界。

    can_execute=False（云端无沙箱 → registry 扣掉执行类）追加「执行环境未装配」块：
    能写脚本落盘、不能运行、不能生成需运行程序才产出的二进制文件、禁止谎称已运行/已生成；
    can_execute=True（默认）保持原样，本地/沙箱路径字节不变。
    """
    from agentcore.runtime.runs.executor.identities import build_worker_identity

    no_exec = build_worker_identity(has_dependents=False, can_execute=False)
    assert "本回合执行环境未装配" in no_exec
    assert "能】用写文件工具" in no_exec
    assert "不能】运行" in no_exec
    assert "二进制" in no_exec
    assert "已运行 / 已验证 / 已生成" in no_exec
    assert "未运行验证" in no_exec
    assert "手抄" in no_exec
    assert "表格" in no_exec
    assert "结构报告" in no_exec
    assert "待跑" in no_exec
    assert "暂时不可用" in no_exec
    assert "不是缺口" in no_exec
    assert "无法可靠完成" not in no_exec
    assert "不可靠" not in no_exec

    with_exec = build_worker_identity(has_dependents=False, can_execute=True)
    assert "本回合执行环境未装配" not in with_exec
    assert "手抄" not in with_exec
    assert "刚落盘的表格" in with_exec
    assert "file_read 回读自检" in with_exec
    assert "consult(data_file_landing)" in with_exec
    # 默认参数与显式 True 字节一致（不惊扰既有路径）。
    assert with_exec == build_worker_identity(has_dependents=False)


def test_worker_identity_teaches_escalate_blocking_choice():
    """Worker 按题自选：有把握报一声继续；猜错作废就停。不再钉 blocking= 字面。"""
    from agentcore.runtime.runs.executor.identities import build_worker_identity

    body = build_worker_identity(has_dependents=False)
    assert "报一声" in body and "继续" in body
    assert "猜错" in body and "作废" in body and "停" in body
    assert "blocking=false" not in body
    assert "blocking=true" not in body
    assert "escalate 不会打断你" not in body


async def test_executor_never_wires_direct_to_user_register():
    """单人 / 多节点 / 嵌套 lead：身份提示词都不长直出段。"""
    solo, _ = build_run_plan([{"role": "工程师", "task": "改一行"}], id_prefix="s")
    solo_provider = _ContentProvider(["OUT"])
    await _nesting_executor(solo, solo_provider, lambda rid, d: _stub_subteam())(
        solo.nodes[0], {}
    )
    assert "正文直达用户" not in solo_provider.system_messages[0]

    multi, _ = build_run_plan(
        [{"role": "A", "task": "做A"}, {"role": "B", "task": "做B"}], id_prefix="m"
    )
    multi_provider = _ContentProvider(["A", "B"])
    await _nesting_executor(multi, multi_provider, lambda rid, d: _stub_subteam())(
        multi.nodes[0], {}
    )
    assert "正文直达用户" not in multi_provider.system_messages[0]

    nested, _ = build_run_plan(
        [{"role": "子队员", "task": "改一行"}], id_prefix="n", depth=2
    )
    nested_provider = _ContentProvider(["OUT"])
    await _nesting_executor(nested, nested_provider, lambda rid, d: _stub_subteam())(
        nested.nodes[0], {}
    )
    assert "正文直达用户" not in nested_provider.system_messages[0]


async def test_executor_passes_registry_capability_into_identity():
    """Executor 把 registry 能力事实接进 identity：空 registry（无 code_execute）→
    worker system prompt 带「执行环境未装配」自述。"""
    plan, _ = build_run_plan(
        [{"role": "工程师", "task": "写脚本"}],
        id_prefix="cap",
    )
    provider = _ContentProvider(["OUT"])
    await _nesting_executor(plan, provider, lambda rid, d: _stub_subteam())(
        plan.nodes[0], {}
    )
    assert "本回合执行环境未装配" in provider.system_messages[0]


async def test_worker_escalation_is_harvested_and_nonblocking():
    plan, _ = build_run_plan([{"role": "调研", "task": "查不清楚的事"}], id_prefix="t")
    reg = ToolRegistry()
    reg.register(EscalateTool())
    rounds = [
        [
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id="c1",
                        function_name="escalate",
                        arguments_delta=(
                            '{"question": "用 Postgres 还是 MySQL?", '
                            '"assumption": "暂用 Postgres", "blocking": true}'
                        ),
                    )
                ]
            )
        ],
        [LLMChunk(delta_content="已按 Postgres 完成调研")],
    ]
    provider = _ScriptedRounds(rounds)
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    state = res["t_1"]
    assert state.phase is RunPhase.COMPLETED  # non-blocking: it still delivered
    assert state.content == "已按 Postgres 完成调研"
    assert len(state.escalations) == 1
    esc = state.escalations[0]
    assert esc["question"] == "用 Postgres 还是 MySQL?"
    assert esc["assumption"] == "暂用 Postgres"
    assert esc["blocking"] is True


async def test_worker_escalation_emits_live_event_before_completion():
    # 升级实时可见: the executor wires the worker's escalate to a run-scoped RUN_ESCALATION
    # so the team UI surfaces it the INSTANT it is raised — well before the worker's node
    # completes (ordering proves "live", not a post-hoc harvest at run end).
    plan, _ = build_run_plan([{"role": "调研", "task": "查不清楚的事"}], id_prefix="t")
    reg = ToolRegistry()
    reg.register(EscalateTool())
    rounds = [
        [
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id="c1",
                        function_name="escalate",
                        arguments_delta=(
                            '{"question": "用 Postgres 还是 MySQL?", '
                            '"assumption": "暂用 Postgres", "blocking": true}'
                        ),
                    )
                ]
            )
        ],
        [LLMChunk(delta_content="已按 Postgres 完成调研")],
    ]
    sink = EventSink()
    executor = build_agent_executor(
        plan=plan,
        llm=_ScriptedRounds(rounds),
        tools=reg,
        sink=sink,
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    await WaveScheduler().run(plan, executor)
    sink.close()
    events = [e async for e in sink]
    types = [e.type for e in events]
    assert EventType.RUN_ESCALATION in types
    esc = next(e for e in events if e.type == EventType.RUN_ESCALATION)
    assert esc.payload["run_id"] == "t_1"
    assert esc.payload["question"] == "用 Postgres 还是 MySQL?"
    assert esc.payload["assumption"] == "暂用 Postgres"
    assert esc.payload["blocking"] is True
    # Live, not a harvest: the escalation surfaces strictly before the run finishes.
    assert types.index(EventType.RUN_ESCALATION) < types.index(EventType.RUN_COMPLETED)


async def test_worker_without_escalation_has_empty_list():
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    res = await WaveScheduler().run(plan, _executor(plan, _ContentProvider(["OUT"]), EventSink()))
    assert res["t_1"].escalations == []


async def test_escalate_tool_rejects_empty_question_and_acks_otherwise():
    tool = EscalateTool()
    bad = await tool.execute({"question": "  "}, _ctx())
    assert bad.success is False and "question" in (bad.error or "")
    # A valid escalation is acknowledged with a CONTINUE (non-terminal) result that
    # steers the worker to keep delivering — it is not a stop.
    ok = await tool.execute({"question": "Postgres 还是 MySQL?"}, _ctx())
    assert ok.success is True and ok.is_terminal is False
    assert "继续" in ok.output


async def test_escalate_invokes_on_escalate_callback_with_triple():
    # 升级实时可见: the tool hands the executor-provided live channel its (question,
    # assumption, blocking, kind) quadruple. An empty question is rejected BEFORE any emit.
    tool = EscalateTool()
    seen: list[tuple[str, str, bool, str]] = []
    ctx = replace(
        _ctx(), on_escalate=lambda q, a, b, k="normal": seen.append((q, a, b, k))
    )
    await tool.execute({"question": "  "}, ctx)
    assert seen == []  # rejected first, nothing surfaced
    await tool.execute({"question": "Q?", "assumption": "暂定 A", "blocking": True}, ctx)
    assert seen == [("Q?", "暂定 A", True, "normal")]


async def test_escalate_callback_failure_is_non_fatal():
    # The durable path (transcript → RunState.escalations) is unconditional, so a live-emit
    # hiccup must never sink the escalation or the worker — the tool still ACKs CONTINUE.
    def _boom(_q: str, _a: str, _b: bool, _k: str = "normal") -> None:
        raise RuntimeError("sink closed")

    ctx = replace(_ctx(), on_escalate=_boom)
    ok = await EscalateTool().execute({"question": "Q?"}, ctx)
    assert ok.success is True and ok.is_terminal is False


async def test_escalate_dep_kind_acks_with_replan_add_steer():
    # §2.4 变·worker 的「拉」(case b): escalate(kind="dep") flags a依赖缺口·卡在缺输入. It is a
    # non-blocking CONTINUE — the worker keeps going on its assumption while the CEO/lead补 a
    # producer at the boundary; the ACK names the replan(add) lever and the「绝不空等」rule.
    ok = await EscalateTool().execute(
        {"question": "缺错误返回结构才能写测试", "kind": "dep"}, _ctx()
    )
    assert ok.success is True and ok.is_terminal is False
    assert "replan" in ok.output
    assert "继续" in ok.output


async def test_cancel_worker_keeps_escalations_and_member_usage():
    """派 N 人、若干已 escalate、随后全部 cancel_worker → escalations / usage 不蒸发.

    Pins the cancel-terminal honesty fix: CANCELLED RunState must still carry
    transcript-harvested escalations and priced usage so BatchMetrics /
    member ledger see the real failure mode instead of「从未派工」.
    """

    class _EscalateThenHang:
        base_url = "http://test.invalid/v1"

        def __init__(self, *, escalate_workers: int) -> None:
            self._escalate_workers = escalate_workers
            self.escalate_done = asyncio.Event()
            self._escalated = 0
            self._lock = asyncio.Lock()

        async def stream(self, request):  # noqa: ANN001
            already = any(m.role == "tool" for m in request.messages)
            if already:
                await asyncio.sleep(30)
                yield LLMChunk(delta_content="unreachable")
                return
            async with self._lock:
                idx = self._escalated
                self._escalated += 1
                done = self._escalated >= self._escalate_workers
            # Every worker escalates then hangs on the next round so cancel_worker
            # hits mid-flight AFTER escalate + usage are already on the transcript.
            yield LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id=f"e{idx}",
                        function_name="escalate",
                        arguments_delta=(
                            f'{{"question": "Q{idx}?", '
                            f'"assumption": "A{idx}", "blocking": false}}'
                        ),
                    )
                ]
            )
            yield LLMChunk(
                usage=TokenUsage(
                    input_tokens=10_000 + idx,
                    cache_miss_tokens=10_000 + idx,
                    output_tokens=100,
                )
            )
            if done:
                self.escalate_done.set()
            return

    n = 3
    plan, _ = build_run_plan(
        [{"role": f"W{i}", "task": f"做{i}"} for i in range(n)],
        id_prefix="c",
    )
    provider = _EscalateThenHang(escalate_workers=n)
    reg = ToolRegistry()
    reg.register(EscalateTool())
    cancel_all = asyncio.Event()

    async def _arm_cancel() -> None:
        await provider.escalate_done.wait()
        cancel_all.set()

    arm = asyncio.create_task(_arm_cancel())
    metrics: list[BatchMetrics] = []
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        profile_set=_flash_profiles(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    results = await WaveScheduler().run(
        plan,
        executor,
        cancel_run_ids=lambda: frozenset(node.run_id for node in plan.nodes)
        if cancel_all.is_set()
        else frozenset(),
        metrics_sink=metrics,
    )
    await arm

    assert all(s.phase is RunPhase.CANCELLED for s in results.values())
    assert all(len(s.escalations) == 1 for s in results.values())
    assert {e["question"] for s in results.values() for e in s.escalations} == {
        "Q0?",
        "Q1?",
        "Q2?",
    }
    assert all(s.usage.get("input", 0) > 0 for s in results.values())
    assert all(s.cost for s in results.values())

    m = metrics[0]
    assert m.cancelled == n
    assert m.completed == 0
    assert m.escalations == n  # harvested off CANCELLED states, not evaporated

    acc = WorkerResultAccumulator()
    for node in plan.nodes:
        acc.add_run(node, results[node.run_id], parent_run_id="ceo")
    assert len(acc.run_ledger) == n
    assert acc.usage["input"] == sum(s.usage["input"] for s in results.values())
