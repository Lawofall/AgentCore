"""Basic delegate execute, validation, events, and schema tests."""

import asyncio

import agentcore.runtime.delegate.prelude as delegate_prelude_mod
import agentcore.tools.builtin.delegate.tool as delegate_tool_mod
from agentcore.core.types import ToolEffect
from agentcore.llm.provider.protocol import TokenUsage
from agentcore.runtime.events import EventSink, EventType
from tests.conftest import LogSpy
from tests.delegate.conftest import Provider, _upstream_body, ctx, tool


async def test_parallel_delegate_returns_products_non_terminal():
    """经典阻塞路径：coordinate=false 时多 worker 等全队完成再返回产物。"""
    t = tool(Provider(["AOUT", "BOUT"]))
    result = await t.execute(
        {
            "tasks": [{"role": "研究员", "task": "做A"}, {"role": "写手", "task": "做B"}],
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success is True
    assert result.is_terminal is False
    assert "AOUT" in result.output
    assert "BOUT" in result.output
    assert "研究员" in result.output
    assert "写手" in result.output
    usage_keys = {
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_hit_tokens",
        "cache_miss_tokens",
    }
    assert usage_keys <= set(result.metadata)
    assert result.metadata.get("batch_nodes") == 2
    assert result.metadata.get("batch_has_deps") is False


async def test_dag_delegate_completes_with_both_products():
    tasks = [
        {"id": "s1", "role": "研究员", "task": "调研"},
        {"id": "s2", "role": "写手", "task": "撰写", "depends_on": ["s1"]},
    ]
    t = tool(Provider([_upstream_body("UPSTREAM"), _upstream_body("FINAL")]))
    result = await t.execute({"tasks": tasks, "coordinate": False}, ctx())
    assert result.success is True
    assert result.is_terminal is False
    assert "UPSTREAM" in result.output
    assert "FINAL" in result.output


async def test_single_worker_success_folds_to_ceo_not_handoff():
    """单 worker 成功：一律 format_for_ceo 回灌，不 HANDOFF、不灌主气泡。

    arguments 残留 finalize 键静默忽略（不报错、不直出）。
    """
    usage = TokenUsage(
        input_tokens=10,
        output_tokens=5,
        reasoning_tokens=0,
        cache_hit_tokens=6,
        cache_miss_tokens=4,
    )
    sink = EventSink()
    t = tool(Provider(["DIRECT"], usage=usage), sink=sink)
    result = await t.execute(
        {
            "tasks": [{"role": "工程师", "task": "建文件"}],
            "finalize": True,
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success is True
    assert result.is_terminal is False
    assert result.effect is not ToolEffect.HANDOFF
    assert result.final_text is None
    assert "DIRECT" in result.output
    assert "团队执行结果" in result.output
    assert t.usage["input"] == 10
    sink.close()
    deltas = [e.payload["delta"] async for e in sink if e.type == EventType.CONTENT_DELTA]
    assert deltas == []


async def test_leftover_finalize_ignored_for_multi_worker_batch():
    t = tool(Provider(["A", "B"]))
    result = await t.execute(
        {
            "tasks": [{"role": "A", "task": "a"}, {"role": "B", "task": "b"}],
            "finalize": True,
            "coordinate": False,
        },
        ctx(),
    )
    assert result.is_terminal is False
    assert result.effect is not ToolEffect.HANDOFF
    assert "A" in result.output and "B" in result.output


def _system_prompts(provider: Provider) -> list[str]:
    return [
        next((m.content or "" for m in req.messages if m.role == "system"), "")
        for req in provider.requests
    ]


async def test_leftover_finalize_keeps_plain_worker_identity():
    """残留 finalize 键不换身份口径：产出仍回主管合成。"""
    provider = Provider(["DIRECT"])
    t = tool(provider)
    result = await t.execute(
        {
            "tasks": [{"role": "工程师", "task": "改一行"}],
            "finalize": True,
            "coordinate": False,
        },
        ctx(),
    )
    assert result.effect is not ToolEffect.HANDOFF
    assert all("正文直达用户" not in sys for sys in _system_prompts(provider))


async def test_single_worker_keeps_plain_worker_identity():
    """单节点产出仍回主管合成 ⇒ 不换直出口径。"""
    provider = Provider(["OUT"])
    t = tool(provider)
    await t.execute(
        {"tasks": [{"role": "工程师", "task": "改一行"}], "coordinate": False}, ctx()
    )
    assert all("正文直达用户" not in sys for sys in _system_prompts(provider))


def test_should_auto_light_delegate():
    assert delegate_prelude_mod._should_auto_light_delegate(
        [{"role": "工程师", "task": "做A"}]
    )
    assert delegate_prelude_mod._should_auto_light_delegate(
        [{"role": "工程师", "task": "做A", "deliverable": {"form": "prose"}}]
    )
    assert not delegate_prelude_mod._should_auto_light_delegate(
        [{"role": "A", "task": "a"}, {"role": "B", "task": "b"}]
    )
    assert not delegate_prelude_mod._should_auto_light_delegate(
        [{"role": "A", "task": "a", "depends_on": ["x"]}]
    )
    # leftover bind_after_deps is dropped, not a wave-boundary feature
    assert delegate_prelude_mod._should_auto_light_delegate(
        [{"role": "A", "task": "a", "bind_after_deps": True}]
    )
    # 深度交付与编排结构正交：单 worker 无波边界也不 auto-light
    assert not delegate_prelude_mod._should_auto_light_delegate(
        [
            {
                "role": "工程师",
                "task": "写代码落盘",
                "deliverable": {"form": "files", "artifacts": ["app.py"]},
            }
        ]
    )
    # light 不再盖短轮：browser_* 工具面可走 auto-light（须显式 prose）
    assert delegate_prelude_mod._should_auto_light_delegate(
        [
            {
                "role": "浏览器操作员",
                "task": "打开百度搜一下",
                "tools": ["browser"],
                "deliverable": {"form": "prose"},
            }
        ]
    )


async def test_single_worker_auto_infers_light_complexity_hint(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(delegate_tool_mod, "logger", spy)
    t = tool(Provider(["OUT"]))
    await t.execute(
        {
            "tasks": [
                {
                    "role": "工程师",
                    "task": "做A",
                    "deliverable": {"form": "prose"},
                }
            ]
        },
        ctx(),
    )
    assert spy.get("delegate.started")["complexity_hint"] == "light"


async def test_single_worker_deep_deliverable_skips_auto_light(monkeypatch):
    """单 worker 无波边界，但 deep deliverable 时不推断 light，保持 standard。"""
    spy = LogSpy()
    monkeypatch.setattr(delegate_tool_mod, "logger", spy)
    # 档位推断日志出自前奏模块；同一个 spy 挂两处，负向断言才仍盯着真实发射点。
    monkeypatch.setattr(delegate_prelude_mod, "logger", spy)
    t = tool(Provider(["OUT"]))
    await t.execute(
        {
            "tasks": [
                {
                    "role": "工程师",
                    "task": "实现功能并落盘",
                    "deliverable": {"form": "files", "artifacts": ["src/main.py"]},
                }
            ],
        },
        ctx(),
    )
    assert spy.get("delegate.started")["complexity_hint"] == "standard"
    assert not any(name == "delegate.complexity_hint_inferred" for name, _ in spy.events)


async def test_explicit_light_with_file_deliverable_kept_for_repair(monkeypatch):
    """显式 light + requires_files/artifacts → 保留 light（修码快修）；不再缩 max_rounds。"""
    import agentcore.runtime.runs as runs_mod

    spy = LogSpy()
    monkeypatch.setattr(delegate_tool_mod, "logger", spy)
    monkeypatch.setattr(delegate_prelude_mod, "logger", spy)
    captured: dict = {}
    real_build = runs_mod.build_run_plan

    def _capture_build(*args, **kwargs):
        plan, errors = real_build(*args, **kwargs)
        captured["complexity_hint"] = kwargs.get("complexity_hint")
        captured["max_rounds"] = plan.nodes[0].max_rounds if plan.nodes else None
        return plan, errors

    monkeypatch.setattr(runs_mod, "build_run_plan", _capture_build)
    t = tool(Provider(["OUT"]))
    await t.execute(
        {
            "tasks": [
                {
                    "role": "工程师",
                    "task": "修缺 export 并落盘",
                    "deliverable": {"form": "files", "artifacts": ["app.py"]},
                }
            ],
            "complexity_hint": "light",
        },
        ctx(),
    )
    assert spy.get("delegate.started")["complexity_hint"] == "light"
    assert not any(name == "delegate.complexity_hint_ignored" for name, _ in spy.events)
    assert captured["complexity_hint"] == "light"
    assert captured["max_rounds"] is None


async def test_multi_worker_keeps_standard_complexity_hint(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(delegate_tool_mod, "logger", spy)
    t = tool(Provider(["AOUT", "BOUT"]))
    await t.execute(
        {"tasks": [{"role": "研究员", "task": "做A"}, {"role": "写手", "task": "做B"}]},
        ctx(),
    )
    assert spy.get("delegate.started")["complexity_hint"] == "standard"


async def test_explicit_standard_complexity_hint_not_overridden(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(delegate_tool_mod, "logger", spy)
    t = tool(Provider(["OUT"]))
    await t.execute(
        {
            "tasks": [{"role": "工程师", "task": "做A"}],
            "complexity_hint": "standard",
        },
        ctx(),
    )
    assert spy.get("delegate.started")["complexity_hint"] == "standard"


async def test_explicit_light_with_dag_features_ignored(monkeypatch):
    """显式 light + depends_on 时忽略 light；无 BIND 让出，DAG 直跑。"""
    spy = LogSpy()
    monkeypatch.setattr(delegate_tool_mod, "logger", spy)
    provider = Provider([_upstream_body("AOUT"), _upstream_body("BOUT")])
    t = tool(provider)
    first = await t.execute(
        {
            "tasks": [
                {"id": "a", "role": "研究员", "task": "调研"},
                {"id": "b", "role": "写手", "task": "撰写", "depends_on": ["a"]},
            ],
            "complexity_hint": "light",
            "coordinate": False,
        },
        ctx(),
    )
    assert spy.get("delegate.started")["complexity_hint"] == "standard"
    assert first.success is True
    assert t._supervised is None
    assert "AOUT" in first.output and "BOUT" in first.output


async def test_multi_worker_does_not_emit_note_events(monkeypatch):
    """多节点委派不再建墙、不授便签工具、不发 team_note_posted。"""
    from agentcore.runtime.runs.types import RunPhase, RunState

    spy = LogSpy()
    monkeypatch.setattr(delegate_tool_mod, "logger", spy)
    captured: dict = {}

    async def _exec(spec, completed):  # noqa: ANN001
        return RunState(phase=RunPhase.COMPLETED, content=f"{spec.role}_OUT")

    def _capture_build(**kwargs):  # noqa: ANN003
        captured["keys"] = set(kwargs)
        return _exec

    monkeypatch.setattr("agentcore.runtime.runs.build_agent_executor", _capture_build)
    sink = EventSink()
    t = tool(Provider([]), sink=sink)
    result = await t.execute(
        {
            "tasks": [{"role": "A", "task": "a"}, {"role": "B", "task": "b"}],
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success is True
    assert "coordination" not in spy.get("delegate.started")
    assert "note_wall" not in captured["keys"]
    assert "collaboration" not in captured["keys"]
    assert not hasattr(t, "_note_wall")
    assert not any(str(e.type) == "team_note_posted" for e in sink._history)  # noqa: SLF001


async def test_team_brief_parses_without_materializing_notes(monkeypatch):
    """非空 team_brief 仍解析挂到工具上，但不物化便签事件。"""
    from agentcore.runtime.runs.types import RunPhase, RunState

    async def _exec(spec, completed):  # noqa: ANN001
        return RunState(phase=RunPhase.COMPLETED, content="OK")

    monkeypatch.setattr(
        "agentcore.runtime.runs.build_agent_executor", lambda **kwargs: _exec
    )
    sink = EventSink()
    t = tool(Provider([]), sink=sink)
    result = await t.execute(
        {
            "tasks": [{"role": "A", "task": "a"}, {"role": "B", "task": "b"}],
            "team_brief": "自研画布\n协作后置",
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success is True
    assert t._team_brief == "自研画布\n协作后置"
    assert not any(str(e.type) == "team_note_posted" for e in sink._history)  # noqa: SLF001


async def test_delegate_started_logs_who_what_and_first_wave_parallel(monkeypatch):
    # 决策可观测: delegate.started must carry「派了谁·干什么」(agents) + 首波扇出 (parallel),
    # not just a node count — so an offline analysis can see the delegation's input basis.
    spy = LogSpy()
    monkeypatch.setattr(delegate_tool_mod, "logger", spy)
    t = tool(Provider(["AOUT", "BOUT"]))
    await t.execute(
        {
            "tasks": [
                {"role": "研究员", "task": "调研市场规模"},
                {"role": "写手", "task": "撰写初稿"},
            ]
        },
        ctx(),
    )
    started = spy.get("delegate.started")
    assert started["nodes"] == 2
    assert started["call"] == 1
    # both nodes are dependency-free → the whole batch is one parallel wave
    assert started["parallel"] == 2
    # who + what, in plan order — the delegation's actual content
    assert started["agents"] == ["研究员: 调研市场规模", "写手: 撰写初稿"]
    # 80-char preview is not the only evidence: full task lengths ride the same event.
    assert started["task_chars"] == [len("调研市场规模"), len("撰写初稿")]


async def test_delegate_started_logs_full_task_chars_not_just_preview(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(delegate_tool_mod, "logger", spy)
    long_task = "已确认约束：" + ("甲" * 200)
    t = tool(Provider(["OUT"]))
    await t.execute({"tasks": [{"role": "写手", "task": long_task}]}, ctx())
    started = spy.get("delegate.started")
    assert started["task_chars"] == [len(long_task)]
    preview = started["agents"][0]
    assert "写手:" in preview
    assert len(preview) < len(long_task)


async def test_delegate_started_parallel_reflects_dag_first_wave(monkeypatch):
    # A DAG (s2 depends_on s1) is NOT fully parallel: the first-wave width is 1 (only s1
    # has no deps), which `parallel` must reflect even though `nodes` is 2.
    spy = LogSpy()
    monkeypatch.setattr(delegate_tool_mod, "logger", spy)
    t = tool(Provider([_upstream_body("UP"), _upstream_body("FINAL")]))
    await t.execute(
        {
            "tasks": [
                {"id": "s1", "role": "研究员", "task": "调研"},
                {"id": "s2", "role": "写手", "task": "撰写", "depends_on": ["s1"]},
            ]
        },
        ctx(),
    )
    started = spy.get("delegate.started")
    assert started["nodes"] == 2
    assert started["parallel"] == 1


async def test_empty_tasks_rejected():
    t = tool(Provider([]))
    result = await t.execute({"tasks": []}, ctx())
    assert result.success is False
    assert result.is_terminal is False
    assert result.error
    assert result.contract_failure is True
    # Error text lives only in ``error`` — duplicate fill into ``output`` made
    # tool_exec join the same string twice for the model / UI.
    assert result.output == ""


async def test_all_invalid_tasks_rejected():
    t = tool(Provider([]))
    result = await t.execute({"tasks": [{"role": "A"}]}, ctx())
    assert result.success is False
    assert result.error
    assert result.contract_failure is True


async def test_build_plan_validation_contract_failure_skips_circuit_breaker():
    """depends_on / 参数校验打回标 contract_failure → 连拒不进熔断。"""
    from agentcore.runtime.loop_controller import LoopController, ToolAttempt

    t = tool(Provider([]))
    result = await t.execute(
        {
            "tasks": [
                {"id": "a", "role": "A", "task": "a"},
                {"id": "b", "role": "B", "task": "b", "depends_on": ["不存在的上游"]},
            ]
        },
        ctx(),
    )
    assert result.success is False
    assert result.contract_failure is True

    c = LoopController()
    for i in range(5):
        c.record(
            [
                ToolAttempt(
                    f"fp{i}",
                    "delegate",
                    success=False,
                    contract_failure=True,
                )
            ]
        )
        assert not c.tool_circuit_breaker()


async def test_worker_usage_accumulates_across_calls():
    # 显式 coordinate=False：默认协调臂会在同回合二次合入后提前返回，usage 尚未计入。
    usage = TokenUsage(
        input_tokens=10,
        output_tokens=5,
        reasoning_tokens=2,
        cache_hit_tokens=6,
        cache_miss_tokens=4,
    )
    t = tool(Provider(["X", "Y", "Z", "W"], usage=usage))
    first = await t.execute(
        {"tasks": [{"role": "A", "task": "a"}], "coordinate": False}, ctx()
    )
    assert first.metadata["input_tokens"] == 10
    assert first.metadata["cache_hit_tokens"] == 6
    assert t.usage == {
        "input": 10,
        "output": 5,
        "reasoning": 2,
        "cache_hit": 6,
        "cache_miss": 4,
    }
    await t.execute(
        {"tasks": [{"role": "B", "task": "b"}], "coordinate": False}, ctx()
    )
    assert t.usage == {
        "input": 20,
        "output": 10,
        "reasoning": 4,
        "cache_hit": 12,
        "cache_miss": 8,
    }


async def test_emits_plan_and_lifecycle_events():
    # 默认协调臂：lifecycle 在后台 drive；等 drive_task 后再读 sink。
    from agentcore.runtime.coordination.session import (
        active_coordination,
        clear_active_coordination,
    )

    clear_active_coordination()
    sink = EventSink()
    t = tool(Provider(["X"]), sink=sink)
    await t.execute({"tasks": [{"role": "A", "task": "做A"}]}, ctx())
    session = active_coordination("e")
    assert session is not None and session.drive_task is not None
    await asyncio.wait_for(session.drive_task, timeout=10)
    clear_active_coordination("e")
    sink.close()
    types = [e.type async for e in sink]
    assert EventType.RUN_PLAN in types
    assert EventType.RUN_STARTED in types
    assert EventType.RUN_COMPLETED in types
    assert EventType.RUN_PROGRESS in types


async def test_run_plan_carries_stance_and_group_tags():
    sink = EventSink()
    t = tool(Provider(["PRO", "CON"]), sink=sink)
    await t.execute(
        {
            "tasks": [
                {"role": "正方", "task": "支持", "stance": "pro", "group": "g1"},
                {"role": "反方", "task": "反对", "stance": "con", "group": "g1"},
            ]
        },
        ctx(),
    )
    sink.close()
    plan_runs = [r async for e in sink if e.type == EventType.RUN_PLAN for r in e.payload["runs"]]
    by_task = {r["task"]: r for r in plan_runs}
    assert by_task["支持"]["stance"] == "pro"
    assert by_task["支持"]["group"] == "g1"
    assert by_task["反对"]["stance"] == "con"
    assert by_task["反对"]["group"] == "g1"


async def test_run_plan_carries_round_tag():
    sink = EventSink()
    t = tool(Provider(["R1", "R2"]), sink=sink)
    await t.execute(
        {
            "tasks": [
                {"id": "p1", "role": "正方", "task": "首轮", "stance": "pro", "round": 1},
                {
                    "id": "p2",
                    "role": "正方",
                    "task": "次轮",
                    "stance": "pro",
                    "round": 2,
                    "depends_on": ["p1"],
                },
            ]
        },
        ctx(),
    )
    sink.close()
    plan_runs = [r async for e in sink if e.type == EventType.RUN_PLAN for r in e.payload["runs"]]
    by_task = {r["task"]: r for r in plan_runs}
    assert by_task["首轮"]["round"] == 1
    assert by_task["次轮"]["round"] == 2


async def test_run_plan_omits_tags_for_ordinary_batch():
    sink = EventSink()
    t = tool(Provider(["X", "Y"]), sink=sink)
    await t.execute({"tasks": [{"role": "A", "task": "a"}, {"role": "B", "task": "b"}]}, ctx())
    sink.close()
    plan_runs = [r async for e in sink if e.type == EventType.RUN_PLAN for r in e.payload["runs"]]
    assert plan_runs
    assert all("stance" not in r and "group" not in r and "round" not in r for r in plan_runs)


async def test_handwritten_parallel_team_runs():
    t = tool(Provider([]))
    result = await t.execute(
        {
            "tasks": [
                {"role": "方向 A 专员", "task": "摸底方向 A"},
                {"role": "方向 B 专员", "task": "摸底方向 B"},
            ],
        },
        ctx(),
    )
    assert result.success is True
    assert result.is_terminal is False
    assert "方向 A 专员" in result.output
    assert "方向 B 专员" in result.output


async def test_empty_delegate_is_contract_failure_not_circuit_break():
    """缺 tasks 连拒须标 contract_failure，勿熔断 delegate。"""
    from agentcore.runtime.loop_controller import LoopController, ToolAttempt

    t = tool(Provider([]))
    empty = await t.execute({}, ctx())
    assert empty.success is False
    assert empty.contract_failure is True

    hoist = await t.execute(
        {
            "tasks": [
                {
                    "role": "实现",
                    "task": "写 CLI",
                    "completion_criteria": {
                        "type": "custom",
                        "description": "包文件已创建",
                    },
                },
                {
                    "role": "测试",
                    "task": "写测试",
                    "completion_criteria": {
                        "type": "custom",
                        "description": "pytest 通过",
                    },
                },
            ],
        },
        ctx(),
    )
    # S3: nested completion_criteria ignored (field retired); not a hoist reject.
    assert "tasks[].completion_criteria" not in (hoist.error or "")

    c = LoopController()
    for i in range(3):
        c.record(
            [
                ToolAttempt(
                    f"fp{i}",
                    "delegate",
                    success=False,
                    contract_failure=True,
                )
            ]
        )
        assert not c.tool_circuit_breaker().disabled


def test_schema_omits_retired_completion_criteria():
    t = tool(Provider([]))
    props = t.schema.parameters["properties"]
    assert "playbook" not in props
    assert "playbook_args" not in props
    assert "completion_criteria" not in props
    assert "finalize" not in props
    # S3 字段已删 ⇒ 描述里也不留负面清单；误传仍由 execute 静默忽略 + 打点。
    assert "completion_criteria" not in t.schema.description


def test_ceo_deliverable_schema_is_artifacts_only():
    """CEO 填参面只见顶层 artifacts，不套 deliverable 对象。"""
    t = tool(Provider([]))
    task_props = t.schema.parameters["properties"]["tasks"]["items"]["properties"]
    assert "deliverable" not in task_props
    artifacts = task_props["artifacts"]
    assert artifacts["type"] == "array"
    assert artifacts["description"] == "可选路径；省略不催写盘。"


def test_nested_delegate_description_switches_at_depth():
    from agentcore.tools.builtin.delegate.schema import (
        DELEGATE_DESCRIPTION,
        NESTED_DELEGATE_DESCRIPTION,
    )

    t = tool(Provider([]))
    assert t.schema.description == DELEGATE_DESCRIPTION
    t._depth = 1
    assert t.schema.description == NESTED_DELEGATE_DESCRIPTION
