"""Worker token/timeout 统一 backstop：未显式声明时回填全局 ceiling + 1200s。"""

from __future__ import annotations

from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.types import Deliverable, RunPolicy, RunSpec
from agentcore.runtime.runs.worker_budget import (
    DIRECTED_SEARCH_TOOL_NAMES,
    WORKER_TIMEOUT_BACKSTOP_S,
    apply_worker_budgets_to_specs,
    ensure_directed_search_tools,
    is_deep_deliverable,
    is_directed_search_role,
)


def test_apply_fills_unified_backstop():
    """未声明 token_ceiling / timeout_s → 统一回填。"""
    spec = RunSpec(run_id="x", task="t", role="r", policy=RunPolicy())
    apply_worker_budgets_to_specs([spec], default_token_ceiling=600_000)
    assert spec.token_ceiling == 600_000
    assert spec.policy.timeout_s == WORKER_TIMEOUT_BACKSTOP_S == 1200


def test_apply_preserves_pre_set_token_ceiling_and_timeout():
    """已声明的 token_ceiling / timeout_s 不动。"""
    spec = RunSpec(
        run_id="x",
        task="t",
        role="r",
        deliverable=Deliverable(form="files"),
        token_ceiling=50_000,
        policy=RunPolicy(timeout_s=90),
    )
    apply_worker_budgets_to_specs([spec], default_token_ceiling=600_000)
    assert spec.token_ceiling == 50_000
    assert spec.policy.timeout_s == 90


def test_apply_fills_timeout_when_ceiling_preset():
    """仅 token_ceiling 预置时仍回填 timeout。"""
    spec = RunSpec(
        run_id="x",
        task="t",
        role="r",
        token_ceiling=50_000,
        policy=RunPolicy(),
    )
    apply_worker_budgets_to_specs([spec], default_token_ceiling=600_000)
    assert spec.token_ceiling == 50_000
    assert spec.policy.timeout_s == WORKER_TIMEOUT_BACKSTOP_S


def test_build_plan_applies_unified_backstop_regardless_of_shape():
    """有上游 / 无上游 / 落盘 / 审校 — token/超时均走统一 backstop。"""
    plan, errors = build_run_plan(
        [
            {"id": "r", "role": "研究员", "task": "调研"},
            {
                "id": "s",
                "role": "学术审校员",
                "task": "审校",
                "depends_on": ["r"],
                "deliverable": {"form": "prose"},
            },
            {
                "id": "d",
                "role": "写手",
                "task": "成篇落盘",
                "deliverable": {"form": "files"},
            },
        ],
        complexity_hint="standard",
    )
    assert errors == []
    for node in plan.nodes:
        assert node.token_ceiling == 4_000_000
        assert node.policy.timeout_s == WORKER_TIMEOUT_BACKSTOP_S


def test_build_plan_research_root_still_gets_research_retrieval():
    """非 prose worker 拿统一检索默认（与 token 硬顶同构）。"""
    plan, errors = build_run_plan(
        [
            {
                "role": "数据研究员",
                "task": "深度调研并成篇汇报",
                "deliverable": {"form": "files", "artifacts": ["AgentCore/文档/research/r.md"]},
            }
        ],
        complexity_hint="standard",
    )
    assert errors == []
    node = plan.nodes[0]
    assert node.token_ceiling == 4_000_000
    assert node.policy.timeout_s == WORKER_TIMEOUT_BACKSTOP_S
    from agentcore.runtime.runs.retrieval_budget import DEFAULT_RETRIEVAL_BUDGET

    assert node.retrieval_budget == DEFAULT_RETRIEVAL_BUDGET


def test_explicit_timeout_ms_wins_over_backstop():
    plan, errors = build_run_plan(
        [
            {
                "id": "w1",
                "role": "写手",
                "task": "成篇落盘",
                "timeout_ms": 90_000,
                "deliverable": {"form": "files"},
            }
        ],
        complexity_hint="standard",
    )
    assert errors == []
    node = plan.nodes[0]
    assert node.token_ceiling == 4_000_000
    assert node.policy.timeout_s == 90  # CEO 显式优先


def test_deep_deliverable_signals():
    assert is_deep_deliverable(Deliverable(form="files"))
    assert is_deep_deliverable(Deliverable(artifacts=["report.md"]))
    assert not is_deep_deliverable(Deliverable(form="prose"))
    assert not is_deep_deliverable(Deliverable())
    assert not is_deep_deliverable(None)


def test_factory_closes_files_and_recon_delivery_idle():
    """交文件空转与调查空转催结论均已关：factory 不注入。"""
    from agentcore.runtime.engine.governance import create_loop_controller
    from agentcore.runtime.runs.worker_budget import is_short_write_posture

    assert not is_short_write_posture(max_rounds=None)
    assert is_short_write_posture(max_rounds=6)
    assert is_short_write_posture(max_rounds=4)

    standard = create_loop_controller(
        frozenset({"file_read"}),
        files_expected=True,
        short_write_posture=False,
    )
    assert standard.delivery_idle_nudge_rounds == 0
    assert standard.delivery_idle_narrow_rounds == 0
    assert standard.delivery_idle_report is False
    assert standard.delivery_idle_recon is False
    report = create_loop_controller(
        frozenset({"file_read", "grep"}),
        files_expected=True,
        report_delivery=True,
    )
    assert report.delivery_idle_nudge_rounds == 0
    assert report.delivery_idle_narrow_rounds == 0
    assert report.delivery_idle_report is False
    short_files = create_loop_controller(
        frozenset({"file_read"}),
        files_expected=True,
        short_write_posture=True,
    )
    assert short_files.delivery_idle_nudge_rounds == 0
    assert short_files.delivery_idle_narrow_rounds == 0
    assert short_files.delivery_idle_report is False
    prose = create_loop_controller(
        frozenset({"file_read"}),
        files_expected=True,
        short_write_posture=True,
        form_prose=True,
    )
    assert prose.delivery_idle_nudge_rounds == 0
    assert prose.delivery_idle_narrow_rounds == 0
    assert prose.delivery_idle_recon is False
    assert prose.delivery_idle_report is False
    no_files = create_loop_controller(
        frozenset({"file_read"}),
        files_expected=False,
    )
    assert no_files.delivery_idle_nudge_rounds == 0
    assert no_files.delivery_idle_narrow_rounds == 0
    assert no_files.delivery_idle_recon is False
    assert no_files.delivery_idle_report is False
    prose_no_files = create_loop_controller(
        frozenset({"file_read"}),
        files_expected=False,
        form_prose=True,
    )
    assert prose_no_files.delivery_idle_nudge_rounds == 0
    assert prose_no_files.delivery_idle_narrow_rounds == 0
    assert prose_no_files.delivery_idle_recon is False


def test_is_directed_search_role_covers_review_and_investigation():
    assert is_directed_search_role("后端核心审查员")
    assert is_directed_search_role("质检官")
    assert is_directed_search_role("调研员")
    assert is_directed_search_role("学术审校员")
    assert is_directed_search_role("独立复核员")
    assert is_directed_search_role("code review")
    assert not is_directed_search_role("撰稿人")
    assert not is_directed_search_role("")


def test_ensure_directed_search_tools_enriches_restricted_allow_list():
    valid = {"file_list", "file_read", "grep", "code_search", "handoff"}
    enriched = ensure_directed_search_tools(
        ["file_list", "file_read"],
        role="前端审查员",
        valid_tools=valid,
    )
    assert enriched is not None
    assert "grep" in enriched
    assert "code_search" in enriched
    assert "file_read" in enriched
    assert (
        ensure_directed_search_tools(None, role="审查员", valid_tools=valid) is None
    )
    assert ensure_directed_search_tools(
        ["file_list"], role="撰稿人", valid_tools=valid
    ) == ["file_list"]
    assert frozenset({"grep", "code_search"}) == DIRECTED_SEARCH_TOOL_NAMES


def test_build_plan_ignores_reviewer_least_privilege_tools():
    """真纯丙：CEO 窄名单不再写入 plan；定向检索 enrichment 对 None 为 no-op。"""
    plan, errors = build_run_plan(
        [
            {
                "role": "后端核心审查员",
                "task": "审查 server/app",
                "tools": ["file_list", "file_read"],
                "deliverable": {
                    "form": "prose",
                    "required_sections": ["问题", "建议", "评分"],
                },
            }
        ],
        valid_tools={"file_list", "file_read", "grep", "code_search", "handoff"},
    )
    assert errors == []
    assert plan.nodes[0].tools is None


def test_should_tighten_verify_exec_thrash_for_repair_verify_posture():
    """E3：修码验证短姿态启用收紧；files 短写不再走 zero_write催写（已删）。"""
    from agentcore.runtime.engine.governance import create_loop_controller
    from agentcore.runtime.loop_controller import ToolAttempt
    from agentcore.runtime.runs.worker_budget import should_tighten_verify_exec_thrash

    # verify / diagnose：短预算 + 执行工具 + 非落盘 → tighten
    assert should_tighten_verify_exec_thrash(
        short_write_posture=True,
        files_expected=False,
        has_execution_tools=True,
    )
    # patch 落盘节点：不 tighten（曾走 zero_write；催写已删，仍不走 verify 熔断）
    assert not should_tighten_verify_exec_thrash(
        short_write_posture=True,
        files_expected=True,
        has_execution_tools=True,
    )
    # 无执行工具 / 非短姿态 → 不收紧
    assert not should_tighten_verify_exec_thrash(
        short_write_posture=True,
        files_expected=False,
        has_execution_tools=False,
    )
    assert not should_tighten_verify_exec_thrash(
        short_write_posture=False,
        files_expected=False,
        has_execution_tools=True,
    )

    tightened = create_loop_controller(
        frozenset({"code_execute", "file_read"}),
        files_expected=False,
        short_write_posture=True,
        tighten_verify_exec_thrash=True,
        max_rounds=4,
    )
    # Recon-idle is closed at factory the same way as files delivery_idle.
    assert tightened.delivery_idle_nudge_rounds == 0
    assert tightened.delivery_idle_narrow_rounds == 0
    assert tightened.delivery_idle_recon is False
    # disable<=2：两次同工具失败即 disable（默认 3 才 disable）
    tightened.record(
        [ToolAttempt(fingerprint="fp0", tool_name="code_execute", success=False)]
    )
    assert not tightened.tool_circuit_breaker().disabled
    tightened.record(
        [ToolAttempt(fingerprint="fp1", tool_name="code_execute", success=False)]
    )
    assert tightened.tool_circuit_breaker().disabled == ("code_execute",)

    # unproductive threshold<=2：两轮无产出即 early stop（默认 3）
    tight_u = create_loop_controller(
        frozenset({"code_execute"}),
        files_expected=False,
        short_write_posture=True,
        tighten_verify_exec_thrash=True,
    )
    tight_u.note_round_productivity(
        had_tool_calls=True, all_failed=True, had_content=False
    )
    assert not tight_u.unproductive_early_stop()
    tight_u.note_round_productivity(
        had_tool_calls=True, all_failed=True, had_content=False
    )
    assert tight_u.unproductive_early_stop()

    baseline = create_loop_controller(
        frozenset({"code_execute"}),
        files_expected=False,
        short_write_posture=True,
        tighten_verify_exec_thrash=False,
    )
    for _ in range(2):
        baseline.note_round_productivity(
            had_tool_calls=True, all_failed=True, had_content=False
        )
    assert not baseline.unproductive_early_stop()
    baseline.note_round_productivity(
        had_tool_calls=True, all_failed=True, had_content=False
    )
    assert baseline.unproductive_early_stop()


def test_factory_delivery_idle_not_finalize():
    """Idle 读不中途 FINALIZE；交文件 factory 不累计 delivery_idle_rounds（tracking 已关）。"""
    from agentcore.runtime.engine.governance import create_loop_controller
    from agentcore.runtime.loop_controller import Intervention, ToolAttempt

    ctrl = create_loop_controller(
        frozenset({"file_read", "grep"}),
        files_expected=False,
        short_write_posture=True,
        max_rounds=4,
    )
    assert ctrl.delivery_idle_nudge_rounds == 0
    assert ctrl.delivery_idle_narrow_rounds == 0
    assert ctrl.delivery_idle_recon is False
    for i in range(12):
        ctrl.record([ToolAttempt(fingerprint=f"r{i}", tool_name="file_read", success=True)])
    assert ctrl.convergence_action() is Intervention.CONTINUE

    files = create_loop_controller(
        frozenset({"file_read"}),
        files_expected=True,
        short_write_posture=True,
        max_rounds=4,
    )
    assert files.delivery_idle_nudge_rounds == 0
    assert files.delivery_idle_narrow_rounds == 0
    assert files.delivery_idle_report is False
    for i in range(12):
        files.record([ToolAttempt(fingerprint=f"f{i}", tool_name="file_read", success=True)])
    assert files.convergence_action() is Intervention.CONTINUE
    assert files.delivery_idle_rounds == 0


def test_narrow_for_light_repair_strips_investigation():
    """Light repair 去掉调查工具，保留 light-repair 集（含写盘）；无名单补写半成品。"""
    from agentcore.core.types import ToolCategory
    from agentcore.runtime.runs.executor.node import _narrow_for_light_repair
    from agentcore.tools.protocol import ToolResult, ToolSchema
    from agentcore.tools.registry import ToolRegistry

    class _T:
        def __init__(self, name: str) -> None:
            self._name = name

        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(
                name=self._name,
                description="t",
                parameters={"type": "object", "properties": {}},
                category=ToolCategory.FILESYSTEM,
            )

        async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
            return ToolResult(tool_call_id="", success=True, output="ok")

    reg = ToolRegistry()
    for n in (
        "file_read",
        "grep",
        "handoff",
        "file_write",
        "str_replace",
        "web_search",
    ):
        reg.register(_T(n))

    _r, unrestricted = _narrow_for_light_repair(reg, None)
    assert "file_write" in unrestricted
    assert "str_replace" in unrestricted
    assert "handoff" in unrestricted
    assert "grep" not in unrestricted
    assert "web_search" not in unrestricted

    _r2, narrowed = _narrow_for_light_repair(
        reg, ["file_read", "grep", "handoff", "file_write"]
    )
    assert "file_write" in narrowed
    assert "handoff" in narrowed
    assert "grep" not in narrowed
    # 缺写盘的显式名单不再补写（真纯丙退役 merge_persist）
    _r3, no_grant = _narrow_for_light_repair(reg, ["file_read", "grep", "handoff"])
    assert "file_write" not in no_grant
    assert "handoff" in no_grant
    assert "grep" not in no_grant


def test_should_skip_contract_retry_for_budget_handoff_ok_wind_down():
    """定案 B：handoff_ok + wind_down → 短路；缺一不可。"""
    from agentcore.runtime.runs.executor.node import (
        _wind_down_entered,
        should_skip_contract_retry_for_budget,
    )

    assert should_skip_contract_retry_for_budget(
        handoff_ok=True, wind_down_entered=True
    )
    assert not should_skip_contract_retry_for_budget(
        handoff_ok=True, wind_down_entered=False
    )
    assert not should_skip_contract_retry_for_budget(
        handoff_ok=False, wind_down_entered=True
    )
    assert not should_skip_contract_retry_for_budget(
        handoff_ok=False, wind_down_entered=False
    )

    # wind_down via cutoff reason (token/timeout) also counts as entered.
    assert _wind_down_entered(
        cutoff_reasons=["token_budget"],
        token_ceiling=100_000,
        tokens_spent=1,
    )
    assert should_skip_contract_retry_for_budget(
        handoff_ok=True,
        wind_down_entered=_wind_down_entered(
            cutoff_reasons=["token_budget"],
            token_ceiling=100_000,
            tokens_spent=1,
        ),
    )
    # Soft reserve path: spent past ceiling − reserve.
    assert _wind_down_entered(
        cutoff_reasons=[],
        token_ceiling=80_000,
        tokens_spent=55_000,  # reserve default 30k → enter at ≥50k
    )
    assert not _wind_down_entered(
        cutoff_reasons=[],
        token_ceiling=80_000,
        tokens_spent=40_000,
    )


def test_should_skip_full_contract_retry_for_round_ceiling():
    from agentcore.runtime.runs.executor.node import (
        should_skip_full_contract_retry_for_round_ceiling,
    )

    assert should_skip_full_contract_retry_for_round_ceiling(
        cutoff_reasons=["max_rounds"]
    )
    assert should_skip_full_contract_retry_for_round_ceiling(
        cutoff_reasons=["token_budget", "max_rounds"]
    )
    assert should_skip_full_contract_retry_for_round_ceiling(
        cutoff_reasons=[],
        prior_round_ceiling=True,
    )
    assert not should_skip_full_contract_retry_for_round_ceiling(
        cutoff_reasons=["token_budget"]
    )
    assert not should_skip_full_contract_retry_for_round_ceiling(cutoff_reasons=[])
