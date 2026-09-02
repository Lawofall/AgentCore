"""Supervised wave loop: late-bind, replan, and scope escalation tests."""

from agentcore.runtime.events import EventSink
from agentcore.runtime.events.types import EventType, SSEEvent
from agentcore.runtime.runs import BoundaryReason, RunPhase
from agentcore.tools.builtin.replan import ReplanTool
from tests.delegate.conftest import (
    LATE_BIND_DAG,
    SCOPE_DAG,
    DepProvider,
    Provider,
    ScopeProvider,
    _upstream_body,
    ctx,
    scope_tool,
    tool,
)


class _CapturingSink(EventSink):
    """EventSink that also records every emitted event, so a test can assert on the
    「计划已调整」轻痕迹 (plan_revised) the replan path emits."""

    def __init__(self) -> None:
        super().__init__()
        self.emitted: list[SSEEvent] = []

    def emit(self, event: SSEEvent) -> None:
        self.emitted.append(event)
        super().emit(event)


def _plan_revised(sink: _CapturingSink) -> list[SSEEvent]:
    return [e for e in sink.emitted if e.type is EventType.PLAN_REVISED]


async def test_late_bind_yields_brief_then_replan_resumes_to_terminal():
    provider = Provider([_upstream_body("AOUT"), _upstream_body("BOUT")])
    t = tool(provider)
    first = await t.execute({"tasks": LATE_BIND_DAG, "coordinate": False}, ctx())

    assert first.success is True
    assert first.is_terminal is False
    assert "计划已让出" in first.output
    assert "AOUT" in first.output
    assert "BOUT" not in first.output
    # 合·验证 4b (docs/03-AI核心/编排器与CEO主Agent.md §收尾即验收 第一道): the bind brief tells the captain to
    # reconcile the upstream pieces' seams (语义边界对账) before binding downstream — catch a
    # conflict/gap BEFORE the tail builds on it (catch-early at 交下一棒之前).
    assert "语义边界对账" in first.output
    assert "拼图边" in first.output
    assert provider.calls == 1
    # 协作质量 tally (学·度量 §2.5): the bind boundary handed control back to the captain → the
    # opening plan did not run untouched, so boundary_yields is counted (首计划存活 signal).
    assert t.collab["boundary_yields"] == 1
    sup = t._supervised
    assert sup is not None
    bind_id = sup.boundary_run_ids[0]

    replan_tool = ReplanTool(delegate=t)
    result = await replan_tool.execute(
        {"binds": [{"run_id": bind_id, "role": "写手", "task": "据调研写报告"}]}, ctx()
    )

    assert result.success is True
    assert result.is_terminal is False
    assert t._supervised is None
    assert "AOUT" in result.output and "BOUT" in result.output
    assert "写手" in result.output
    assert provider.calls == 2


async def test_replan_binds_and_steers_pending_downstream():
    provider = Provider([_upstream_body("AOUT"), _upstream_body("BOUT"), _upstream_body("COUT")])
    t = tool(provider)
    tasks = [
        {"id": "a", "role": "研究员", "task": "调研"},
        {"id": "b", "role": "待定", "task": "占位", "depends_on": ["a"], "bind_after_deps": True},
        {"id": "c", "role": "整合", "task": "整合下游", "depends_on": ["b"]},
    ]
    await t.execute({"tasks": tasks, "coordinate": False}, ctx())
    sup = t._supervised
    bind_id = sup.boundary_run_ids[0]
    c_id = next(n.run_id for n in sup.plan.nodes if n.role == "整合")

    result = await t.replan(
        {
            "binds": [{"run_id": bind_id, "role": "写手", "task": "写报告"}],
            "steers": [{"run_id": c_id, "note": "强调风险"}],
        }
    )

    assert result.success is True
    assert "BOUT" in result.output and "COUT" in result.output
    c_user = next(
        m.content
        for req in provider.requests
        for m in req.messages
        if m.role == "user" and "整合下游" in (m.content or "")
    )
    assert "强调风险" in c_user


async def test_replan_stop_wraps_up_partial_without_running_tail():
    provider = Provider([_upstream_body("AOUT"), _upstream_body("BOUT")])
    t = tool(provider)
    await t.execute({"tasks": LATE_BIND_DAG, "coordinate": False}, ctx())

    result = await t.replan({"stop": True})

    assert result.success is True
    assert result.is_terminal is False
    assert t._supervised is None
    assert "AOUT" in result.output
    assert provider.calls == 1


async def test_replan_without_supervised_run_errors():
    t = tool(Provider([]))
    result = await t.replan({"binds": [{"run_id": "x", "role": "r", "task": "t"}]})
    assert result.success is False
    assert "没有待续跑" in (result.error or "")


async def test_replan_requires_binds_or_stop():
    t = tool(Provider([_upstream_body("AOUT")]))
    await t.execute({"tasks": LATE_BIND_DAG, "coordinate": False}, ctx())
    result = await t.replan({})
    assert result.success is False
    assert t._supervised is not None


async def test_replan_rejects_unknown_bind_and_keeps_run_open():
    t = tool(Provider([_upstream_body("AOUT"), _upstream_body("BOUT")]))
    await t.execute({"tasks": LATE_BIND_DAG, "coordinate": False}, ctx())
    result = await t.replan({"binds": [{"run_id": "nope", "role": "写手", "task": "写报告"}]})
    assert result.success is False
    assert "不在当前计划" in (result.error or "")
    assert t._supervised is not None


async def test_plain_dag_runs_straight_through_without_yielding():
    provider = Provider([_upstream_body("AOUT"), _upstream_body("BOUT")])
    t = tool(provider)
    result = await t.execute(
        {
            "tasks": [
                {"id": "a", "role": "研究员", "task": "调研"},
                {"id": "b", "role": "写手", "task": "撰写", "depends_on": ["a"]},
            ],
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success is True
    assert t._supervised is None
    assert "AOUT" in result.output and "BOUT" in result.output


async def test_scope_escalation_yields_brief_then_replan_steers_resumes():
    provider = ScopeProvider()
    t = scope_tool(provider)
    first = await t.execute({"tasks": SCOPE_DAG, "coordinate": False}, ctx())

    assert first.success is True
    assert first.is_terminal is False
    assert "计划已让出" in first.output
    assert "职责偏离" in first.output
    assert "真问题是X不是Y" in first.output
    assert "BOUT" not in first.output
    # 合·验证 4b (主动版): a scope deviation likely ripples to siblings, so the brief tells the
    # captain to proactively reconcile the OTHER pieces' seams (语义边界对账), not just react to
    # the one that raised — the active counterpart of waiting for a worker to escalate.
    assert "语义边界对账" in first.output
    assert "波及兄弟步骤" in first.output
    sup = t._supervised
    assert sup is not None
    assert sup.reason is BoundaryReason.SCOPE
    # 协作质量 tally (学·度量 §2.5): a scope boundary is BOTH a boundary_yield (首计划存活) and a
    # drift signal (漂移率, from the worker's escalate kind=scope).
    assert t.collab["boundary_yields"] == 1
    assert t.collab["scope_signals"] >= 1
    b_id = next(n.run_id for n in sup.plan.nodes if n.role == "写手")

    result = await t.replan({"steers": [{"run_id": b_id, "note": "改写X方向"}]})

    assert result.success is True
    assert t._supervised is None
    assert "BOUT" in result.output
    b_user = next(
        m.content
        for req in provider.requests
        for m in req.messages
        if m.role == "user" and "撰写最终报告" in (m.content or "")
    )
    assert "改写X方向" in b_user


async def test_dep_escalation_yields_brief_then_replan_add_resumes():
    # §2.4 变·worker 的「拉」(case b): a worker卡在缺一个还不存在的输入 (escalate kind=dep) rides
    # the SAME reactive boundary as a scope deviation — control yields to the captain with a brief
    # that flags it as 缺输入·依赖缺口 and steers toward replan(add) a producer, then the captain
    # adds the missing step and the plan resumes on the same DAG.
    provider = DepProvider()
    t = scope_tool(provider)
    first = await t.execute({"tasks": SCOPE_DAG, "coordinate": False}, ctx())

    assert first.success is True
    assert first.is_terminal is False
    assert "计划已让出" in first.output
    # The brief distinguishes a dep gap from a scope deviation and points at replan(add).
    assert "卡在缺输入" in first.output
    assert "缺输入：缺错误返回结构才能写完整测试" in first.output
    assert "add" in first.output
    sup = t._supervised
    assert sup is not None
    assert sup.reason is BoundaryReason.SCOPE
    # 协作质量 tally: a dep boundary is a boundary_yield (首计划存活) but NOT a drift signal
    # (漂移率 stays scope-only) — it is still counted in the total escalation tally.
    assert t.collab["boundary_yields"] == 1
    assert t.collab["scope_signals"] == 0
    assert t.collab["escalations"] >= 1

    # The captain replan(add)s a producer for the missing input; the plan resumes to terminal.
    result = await t.replan(
        {"add": [{"role": "接口设计", "task": "定义错误返回结构 {code,msg}"}]}
    )
    assert result.success is True
    assert t._supervised is None
    assert "BOUT" in result.output


async def test_scope_replan_bare_resume_runs_tail_unchanged():
    provider = ScopeProvider()
    t = scope_tool(provider)
    await t.execute({"tasks": SCOPE_DAG, "coordinate": False}, ctx())
    assert t._supervised is not None

    result = await t.replan({})

    assert result.success is True
    assert t._supervised is None
    assert "BOUT" in result.output


async def test_replan_bind_and_steer_emits_plan_revised_trace():
    # 「计划已调整」轻痕迹 (设计 §7.2): a replan that finalises a late-bound node (bind) AND
    # re-steers a pending downstream (steer) emits ONE plan_revised naming both nodes + kinds,
    # so every end paints a non-interrupting trace. Carries the turn's execution id.
    sink = _CapturingSink()
    provider = Provider([_upstream_body("AOUT"), _upstream_body("BOUT"), _upstream_body("COUT")])
    t = tool(provider, sink=sink)
    tasks = [
        {"id": "a", "role": "研究员", "task": "调研"},
        {"id": "b", "role": "待定", "task": "占位", "depends_on": ["a"], "bind_after_deps": True},
        {"id": "c", "role": "整合", "task": "整合下游", "depends_on": ["b"]},
    ]
    await t.execute({"tasks": tasks, "coordinate": False}, ctx())
    sup = t._supervised
    bind_id = sup.boundary_run_ids[0]
    c_id = next(n.run_id for n in sup.plan.nodes if n.role == "整合")

    result = await t.replan(
        {
            "binds": [{"run_id": bind_id, "role": "写手", "task": "写报告"}],
            "steers": [{"run_id": c_id, "note": "强调风险"}],
        }
    )
    assert result.success is True

    revised = _plan_revised(sink)
    assert len(revised) == 1
    payload = revised[0].payload
    assert payload["execution_id"] == sup.execution_id
    kinds = {r["run_id"]: r["kind"] for r in payload["revisions"]}
    assert kinds == {bind_id: "bind", c_id: "steer"}


async def test_replan_node_both_bound_and_steered_reports_bind():
    # Dedup rule (设计 §7.2): a node named in BOTH binds and steers reads as the bigger event
    # (bind wins) — one entry, kind=bind, never a duplicate or a steer.
    sink = _CapturingSink()
    t = tool(Provider([_upstream_body("AOUT"), _upstream_body("BOUT")]), sink=sink)
    await t.execute({"tasks": LATE_BIND_DAG, "coordinate": False}, ctx())
    sup = t._supervised
    bind_id = sup.boundary_run_ids[0]

    result = await t.replan(
        {
            "binds": [{"run_id": bind_id, "role": "写手", "task": "写报告"}],
            "steers": [{"run_id": bind_id, "note": "顺带强调风险"}],
        }
    )
    assert result.success is True

    revised = _plan_revised(sink)
    assert len(revised) == 1
    assert revised[0].payload["revisions"] == [{"run_id": bind_id, "kind": "bind"}]


async def test_scope_bare_resume_emits_no_plan_revised():
    # A no-op SCOPE resume (replan() with no binds/steers — just 续跑) changed nothing, so it
    # emits NO「计划已调整」trace (the badge only fires on a real autonomous adjustment).
    sink = _CapturingSink()
    t = scope_tool(ScopeProvider())
    t._sink = sink  # scope_tool builds its own sink; swap in the capturing one
    await t.execute({"tasks": SCOPE_DAG, "coordinate": False}, ctx())
    assert t._supervised is not None

    result = await t.replan({})

    assert result.success is True
    assert _plan_revised(sink) == []


async def test_replan_stop_emits_no_plan_revised():
    # stop=true收口 (no binds/steers) is not a plan adjustment — no「计划已调整」trace.
    sink = _CapturingSink()
    t = tool(Provider([_upstream_body("AOUT"), _upstream_body("BOUT")]), sink=sink)
    await t.execute({"tasks": LATE_BIND_DAG, "coordinate": False}, ctx())

    result = await t.replan({"stop": True})

    assert result.success is True
    assert _plan_revised(sink) == []


async def test_dispose_open_supervised_folds_completed_work_then_releases():
    # 受监督的波循环 P5「Edge」: the CEO yielded at a late-bind boundary but the turn ends
    # WITHOUT a replan. The yield path did NOT fold the已完成 upstream's spend; disposal must
    # (implicit stop) so it isn't stranded unbilled, then release the dangling plan.
    from agentcore.llm.provider.protocol import TokenUsage

    provider = Provider([_upstream_body("AOUT"), _upstream_body("BOUT")], usage=TokenUsage(input_tokens=100, output_tokens=20))
    t = tool(provider)
    first = await t.execute({"tasks": LATE_BIND_DAG, "coordinate": False}, ctx())
    assert first.is_terminal is False
    assert t._supervised is not None
    assert t.usage.get("input", 0) == 0  # yield path left the upstream's tokens un-folded

    disposed = await t.dispose_open_supervised()

    assert disposed is not None
    assert t._supervised is None  # dangling plan released
    assert t.usage.get("input") == 100  # upstream "a" folded in as an implicit stop
    assert "AOUT" in disposed.output  # completed work surfaced
    assert provider.calls == 1  # the un-run late-bound tail never ran
    assert await t.dispose_open_supervised() is None  # idempotent


async def test_dispose_open_supervised_noop_without_supervised():
    # A normal turn (no boundary yield) has nothing paused → disposal is a pure no-op.
    t = tool(Provider(["only"]))
    assert await t.dispose_open_supervised() is None


async def test_scope_yield_rejournals_consumed_for_durable_seed():
    # 单一事实源 (P5): a SCOPE yield marks the deviating node consumed IN PLACE; drive.py
    # re-journals its terminal RunState so a durable re-drive (completed_from_journal) carries
    # consumed and won't re-fire the boundary. Assert the refreshed message_final fact reflects
    # the consumed scope escalation.
    from agentcore.runtime.facts import FactKind, TurnFactLog, current_fact_log

    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        t = scope_tool(ScopeProvider())
        await t.execute({"tasks": SCOPE_DAG, "coordinate": False}, ctx())
    finally:
        current_fact_log.reset(token)

    assert t._supervised is not None
    a_id = next(n.run_id for n in t._supervised.plan.nodes if n.role == "研究员")
    finals = [
        e
        for e in log.entries()
        if e.get("kind") == FactKind.MESSAGE_FINAL.value
        and (e.get("payload") or {}).get("run_id") == a_id
    ]
    assert finals, "expected a message_final fact for the deviating node"
    consumed = [
        esc
        for esc in (finals[-1]["payload"].get("escalations") or [])
        if esc.get("kind") == "scope" and esc.get("consumed")
    ]
    assert consumed, "the re-journaled run-final must carry the consumed scope escalation"


async def test_partial_failure_stashes_plan_and_replan_add_resumes(monkeypatch):
    """When some workers fail at terminal, stash the plan for replan(add=...) on the same DAG."""
    from agentcore.runtime.runs.types import RunState

    executed_roles: list[str] = []

    async def _exec(spec, completed):  # noqa: ANN001
        executed_roles.append(spec.role)
        if spec.role == "B":
            return RunState(phase=RunPhase.FAILED, error="boom", content="")
        return RunState(phase=RunPhase.COMPLETED, content=f"{spec.role}_OUT")

    monkeypatch.setattr("agentcore.runtime.runs.build_agent_executor", lambda **kw: _exec)
    t = tool(Provider([]))
    tasks = [
        {"id": "a", "role": "A", "task": "task a"},
        {"id": "b", "role": "B", "task": "task b"},
    ]
    first = await t.execute({"tasks": tasks, "coordinate": False}, ctx())

    assert first.success is True
    assert "failed" in first.output
    assert t._supervised is not None
    assert t._supervised.reason is BoundaryReason.SCOPE
    a_id = next(n.run_id for n in t._supervised.plan.nodes if n.role == "A")

    result = await t.replan(
        {
            "add": [
                {
                    "role": "B_retry",
                    "task": "retry task b",
                    "depends_on": [a_id],
                }
            ]
        }
    )

    assert result.success is True
    assert t._supervised is None
    assert "B_retry_OUT" in result.output
    # B fails once (transient no longer 整跑s at Wave); B_retry succeeds.
    assert executed_roles == ["A", "B", "B_retry"]


async def test_upstream_contract_fail_skips_synth_until_replaces_replan(monkeypatch):
    """Upstream FAILED (retry policy) cascade-skips synth; replaces_run_id replan revives it."""
    from agentcore.runtime.runs.types import RunState

    executed: list[str] = []

    async def _exec(spec, completed):  # noqa: ANN001
        executed.append(spec.role)
        if spec.role == "pr":
            # Real contract hard-fail (terminal.py) sets error_retryable=False.
            return RunState(
                phase=RunPhase.FAILED,
                error="contract.failed",
                content="",
                error_retryable=False,
            )
        if spec.role == "synth":
            # Replacement must be COMPLETED in the dep snapshot — never run on failed-only.
            assert any(
                st.phase is RunPhase.COMPLETED and st.content == "pr_fix_OUT"
                for st in completed.values()
            )
            assert all(
                st.phase is not RunPhase.FAILED or rid not in spec.depends_on
                for rid, st in completed.items()
            )
            return RunState(phase=RunPhase.COMPLETED, content="synth_OUT")
        return RunState(phase=RunPhase.COMPLETED, content=f"{spec.role}_OUT")

    monkeypatch.setattr("agentcore.runtime.runs.build_agent_executor", lambda **kw: _exec)
    t = tool(Provider([]))
    first = await t.execute(
        {
            "tasks": [
                {"id": "pr", "role": "pr", "task": "do pr"},
                {"id": "synth", "role": "synth", "task": "summarize", "depends_on": ["pr"]},
            ],
            "coordinate": False,
        },
        ctx(),
    )
    assert first.success is True
    assert t._supervised is not None
    pr_id = next(n.run_id for n in t._supervised.plan.nodes if n.role == "pr")
    synth_node = next(n for n in t._supervised.plan.nodes if n.role == "synth")
    assert synth_node.run_id in t._supervised.completed
    assert t._supervised.completed[synth_node.run_id].phase is RunPhase.SKIPPED
    # First wave: pr failed (Wave does not 整跑 transient) and synth was cascade-skipped.
    assert executed == ["pr"]

    result = await t.replan(
        {
            "add": [
                {
                    "id": "pr_fix",
                    "role": "pr_fix",
                    "task": "redo pr",
                    "replaces_run_id": pr_id,
                }
            ]
        }
    )
    assert result.success is True
    assert t._supervised is None
    assert "synth_OUT" in result.output
    assert executed == ["pr", "pr_fix", "synth"]
    # Edge rewrite: synth now depends on the replacement, not the failed original.
    assert pr_id not in synth_node.depends_on
    assert len(synth_node.depends_on) == 1


async def test_all_success_does_not_stash_supervised(monkeypatch):
    """A fully successful batch must not leave a dangling supervised plan."""
    from agentcore.runtime.runs.types import RunState

    async def _exec(spec, completed):  # noqa: ANN001
        return RunState(phase=RunPhase.COMPLETED, content="OK")

    monkeypatch.setattr("agentcore.runtime.runs.build_agent_executor", lambda **kw: _exec)
    t = tool(Provider([]))
    result = await t.execute(
        {"tasks": [{"id": "a", "role": "A", "task": "task a"}]}, ctx()
    )

    assert result.success is True
    assert t._supervised is None
    assert "replan(add" not in result.output
