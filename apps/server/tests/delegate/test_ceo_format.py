"""CEO synthesis input formatting tests."""

from structlog.testing import capture_logs

from agentcore.runtime.delegate.ceo_format import (
    build_ceo_synthesis,
    format_for_ceo,
    worker_products,
)
from agentcore.runtime.runs.file_acceptance import build_file_acceptance
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState
from agentcore.tools.builtin.delegate import DELEGATE_OUTPUT_LIMIT
from tests.delegate.conftest import Provider, tool


def _accepted(*paths: str) -> list[dict]:
    return build_file_acceptance(list(paths), phase=RunPhase.COMPLETED)


def test_format_for_ceo_surfaces_file_manifest():
    t = tool(Provider([]))
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="建仪表盘", role="前端工程师")])
    touched = ["dashboard.html", "assets/styles.css"]
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="已完成仪表盘",
            files_touched=touched,
            file_acceptance=_accepted(*touched),
        )
    }
    out = format_for_ceo(t, plan, results)
    assert "文件产出（路径已核）" in out
    assert "路径核对：已核" in out
    assert "文件验收：已验收" not in out
    assert "已验收" not in out
    assert "`dashboard.html`" in out
    assert "`assets/styles.css`" in out
    assert "地面真相" in out


def test_format_for_ceo_no_acceptance_without_stamp_still_counts_failed_landings():
    """FAILED 未盖戳但 files_touched 已落盘 → 计入路径已核（与 delivery_status 同源）。"""
    t = tool(Provider([]))
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="写摘要", role="调研员")])
    results = {
        "w1": RunState(
            phase=RunPhase.FAILED,
            content="半成品",
            error="引用未核实",
            files_touched=["AgentCore/文档/research/a.md"],
        )
    }
    out = format_for_ceo(t, plan, results)
    assert "> 文件产出（路径已核）：`AgentCore/文档/research/a.md`" in out
    assert "> 路径未核：" not in out


def test_format_for_ceo_completed_without_stamp_still_silent():
    """COMPLETED 无戳仍不从 files_touched 合成验收行。"""
    t = tool(Provider([]))
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="写摘要", role="调研员")])
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="已落盘",
            files_touched=["AgentCore/文档/research/a.md"],
        )
    }
    out = format_for_ceo(t, plan, results)
    assert "> 路径未核：" not in out
    assert "> 文件产出（路径已核）：" not in out


def test_format_for_ceo_rejected_file_acceptance():
    """FAILED + 显式 rejected 戳 → 「路径未核」，不得冒充路径已核。"""
    t = tool(Provider([]))
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="写摘要", role="调研员")])
    touched = ["AgentCore/文档/research/a.md"]
    results = {
        "w1": RunState(
            phase=RunPhase.FAILED,
            content="半成品",
            error="引用未核实",
            files_touched=touched,
            file_acceptance=build_file_acceptance(
                touched, phase=RunPhase.FAILED, error="引用未核实"
            ),
        )
    }
    out = format_for_ceo(t, plan, results)
    assert "路径未核" in out
    assert "`AgentCore/文档/research/a.md`" in out
    assert "> 文件产出（路径已核）：`AgentCore/文档/research/a.md`" not in out


def test_format_for_ceo_appends_tool_failures_and_hard_constraint():
    t = tool(Provider([]))
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="跑脚本", role="工程师")])
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="脚本已写好",
            files_touched=["run.py"],
            file_acceptance=_accepted("run.py"),
            tool_failures=[
                {
                    "tool_name": "code_execute",
                    "failure_count": 2,
                    "last_error": "Sandbox crash",
                    "succeeded_after": False,
                }
            ],
        )
    }
    out = format_for_ceo(t, plan, results)
    assert "### tool_failures" in out
    assert "code_execute" in out
    assert "failures=2" in out
    assert "succeeded_after=false" in out
    assert "Sandbox crash" in out
    assert "【工具失败硬约束】" in out
    assert "禁止宣称已完成" in out


def test_format_for_ceo_tool_failures_compensated_no_hard_constraint():
    t = tool(Provider([]))
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="跑脚本", role="工程师")])
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="已跑通",
            tool_failures=[
                {
                    "tool_name": "code_execute",
                    "failure_count": 1,
                    "last_error": "tmp",
                    "succeeded_after": True,
                }
            ],
        )
    }
    out = format_for_ceo(t, plan, results)
    assert "### tool_failures" in out
    assert "succeeded_after=true" in out
    assert "【工具失败硬约束】" not in out


def test_format_for_ceo_omits_manifest_when_worker_touched_no_files():
    t = tool(Provider([]))
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="查资料", role="研究员")])
    results = {"w1": RunState(phase=RunPhase.COMPLETED, content="一段研究综述")}
    out = format_for_ceo(t, plan, results)
    assert "> 文件产出" not in out


def test_format_for_ceo_footer_guards_against_claiming_unwritten_files():
    t = tool(Provider([]))
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="建文件", role="工程师")])
    results = {"w1": RunState(phase=RunPhase.COMPLETED, content="我已创建 app.py 并写入代码")}
    out = format_for_ceo(t, plan, results)
    assert "防幻觉" in out
    assert "未真正落盘" in out
    assert "未达成" in out
    assert "属正常" in out


def test_format_for_ceo_includes_goal_verification_and_completion_judgment():
    # 合·验证 4a：收尾仍提示对照用户请求做完工核验（瘦 footer 后保留关键词）。
    t = tool(Provider([]))
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="建登录接口", role="后端")])
    results = {"w1": RunState(phase=RunPhase.COMPLETED, content="登录接口已完成")}
    out = format_for_ceo(t, plan, results)
    assert "完工核验" in out
    assert "未达成" in out or "已达成" in out


def test_format_for_ceo_includes_semantic_boundary_reconciliation():
    # 合·验证 4b：瘦 footer 仍保留语义边界对账（冲突/缺口/重复），且排在完工核验前。
    t = tool(Provider([]))
    plan = RunPlan(
        nodes=[
            RunSpec(run_id="w1", task="建登录接口", role="后端"),
            RunSpec(run_id="w2", task="建登录页面", role="前端", depends_on=["w1"]),
        ]
    )
    results = {
        "w1": RunState(phase=RunPhase.COMPLETED, content="接口已完成"),
        "w2": RunState(phase=RunPhase.COMPLETED, content="页面已完成"),
    }
    out = format_for_ceo(t, plan, results)
    assert "语义边界对账" in out
    assert "冲突" in out and "缺口" in out and "重复" in out
    assert out.index("语义边界对账") < out.index("完工核验")
    assert "队员过程中广播的【当前有效】" not in out
    assert "便签" not in out


def test_format_for_ceo_surfaces_escalations_blockers_first():
    t = tool(Provider([]))
    plan = RunPlan(
        nodes=[
            RunSpec(run_id="w1", task="查行情", role="调研"),
            RunSpec(run_id="w2", task="建后端", role="后端"),
        ]
    )
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="软的备注",
            escalations=[
                {"question": "目标受众是谁?", "assumption": "暂按大众", "blocking": False}
            ],
        ),
        "w2": RunState(
            phase=RunPhase.COMPLETED,
            content="后端骨架",
            escalations=[
                {"question": "用 Postgres 还是 MySQL?", "assumption": "暂用 PG", "blocking": True}
            ],
        ),
    }
    out = format_for_ceo(t, plan, results)
    assert "队员升级了待决问题" in out
    assert "用 Postgres 还是 MySQL?" in out and "目标受众是谁?" in out
    assert "其暂用假设：暂用 PG" in out
    assert "【关键阻塞】" in out
    assert out.index("Postgres") < out.index("目标受众")
    assert "ask_user" in out and "continue_from_run_id" in out
    assert "已升级 1 项待决问题" in out


def test_format_for_ceo_no_escalation_section_when_none():
    t = tool(Provider([]))
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="查资料", role="研究员")])
    results = {"w1": RunState(phase=RunPhase.COMPLETED, content="一段综述")}
    out = format_for_ceo(t, plan, results)
    assert "队员升级了待决问题" not in out


def test_format_for_ceo_digests_file_producer_not_full_content():
    t = tool(Provider([]))
    long_body = "开头摘要。" + ("废" * 5_000) + "结尾独特标记XYZ"
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="写报告", role="撰稿")])
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content=long_body,
            files_touched=["report.md"],
            file_acceptance=_accepted("report.md"),
        )
    }
    out = format_for_ceo(t, plan, results)
    assert "`report.md`" in out
    # HEAD+TAIL digest (not head-only): the product is still digested — its 5000-char
    # middle is elided, so it is NOT the full content — but BOTH ends now survive, so the
    # 收尾 / 关键取舍 at the tail reach the CEO instead of being silently dropped.
    assert "开头摘要" in out
    assert "结尾独特标记XYZ" in out
    assert "系统视图截断" in out
    assert "中间省略" not in out
    assert ("废" * 5_000) not in out
    assert len(out) < len(long_body)


def test_format_for_ceo_bounds_wide_fanout_keeping_all_workers_and_closing():
    t = tool(Provider([]))
    nodes = [RunSpec(run_id=f"w{i}", task="分析", role=f"分析{i}") for i in range(8)]
    plan = RunPlan(nodes=nodes)
    results = {
        f"w{i}": RunState(phase=RunPhase.COMPLETED, content=f"头{i}" + ("数" * 8_000) + f"尾{i}")
        for i in range(8)
    }
    out = format_for_ceo(t, plan, results)
    for i in range(8):
        assert f"run_id: `w{i}`" in out
    assert "防幻觉" in out and "简短概览" in out
    assert len(out) < DELEGATE_OUTPUT_LIMIT
    assert "系统视图截断" in out
    assert "中间省略" not in out


def test_format_for_ceo_short_prose_passes_through_whole():
    t = tool(Provider([]))
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="查资料", role="研究员")])
    results = {"w1": RunState(phase=RunPhase.COMPLETED, content="一段不长的研究综述，结论是甲。")}
    out = format_for_ceo(t, plan, results)
    assert "一段不长的研究综述，结论是甲。" in out
    assert "中间省略" not in out


def test_format_for_ceo_surfaces_next_steps_advisory_and_leads_with_summary():
    # 完工交接简报: structured brief still leads; a leaf (no files, no dependents)
    # also keeps the body — conclusions now live there after debrief de-conclusioning.
    # The 240-char pointer cap must not clip that body, and truncated follows allowance.
    t = tool(Provider([]))
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="调研", role="研究员")])
    long_tail = "详" * 300
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="一段研究综述正文。" + long_tail,
            debrief={
                "summary": "结论是甲",
                "key_points": ["要点一", "要点二"],
                "next_steps": "补做竞品对比",
            },
        )
    }
    products = worker_products(t, plan, results)
    assert products[0]["fidelity"] == "pass_through"
    assert products[0]["truncated"] is False
    out = format_for_ceo(t, plan, results)
    assert "队员建议的下一步" in out
    assert "补做竞品对比" in out
    assert "交接结论：结论是甲" in out
    assert "要点一" in out and "要点二" in out
    assert "一段研究综述正文。" in out
    assert long_tail in out


def test_format_for_ceo_mid_node_with_dependents_keeps_brief_only():
    """有下游的中间节点：CEO 只吃简报；叶子仍带正文。"""
    t = tool(Provider([]))
    plan = RunPlan(
        nodes=[
            RunSpec(run_id="w1", task="调研", role="研究员"),
            RunSpec(run_id="w2", task="写稿", role="撰稿", depends_on=["w1"]),
        ]
    )
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="一段研究综述正文。",
            debrief={
                "summary": "接力给撰稿",
                "key_points": ["上游已交"],
            },
        ),
        "w2": RunState(
            phase=RunPhase.COMPLETED,
            content="终稿正文。",
            debrief={"summary": "稿已成"},
        ),
    }
    products = worker_products(t, plan, results)
    by_id = {p["run_id"]: p for p in products}
    assert "交接结论：接力给撰稿" in by_id["w1"]["body"]
    assert "一段研究综述正文。" not in by_id["w1"]["body"]
    assert by_id["w1"]["truncated"] is True
    assert "终稿正文。" in by_id["w2"]["body"]
    out = format_for_ceo(t, plan, results)
    assert "一段研究综述正文。" not in out
    assert "终稿正文。" in out


def test_format_for_ceo_no_next_steps_section_when_none():
    t = tool(Provider([]))
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="调研", role="研究员")])
    results = {"w1": RunState(phase=RunPhase.COMPLETED, content="只有正文，没有交接简报小节。")}
    out = format_for_ceo(t, plan, results)
    # The advisory SECTION (its unique intro) is absent; the closing instruction's conditional
    # mention of 『队员建议的下一步』 may still appear and is fine.
    assert "顺带提的后续方向" not in out


def test_format_for_ceo_includes_final_synthesis_discipline():
    # 终稿纪律（瘦 footer）：交付物在前、过程简述从简、名册铁律、PPT 诚实一句。
    t = tool(Provider([]))
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="做课件", role="课件工程师")])
    results = {"w1": RunState(phase=RunPhase.COMPLETED, content="脚本已写好")}
    out = format_for_ceo(t, plan, results)
    assert "【终稿纪律】" in out
    assert "交付物在前" in out
    assert "过程简述从简" in out
    assert "至多一段" not in out
    assert "队员终态名册" in out
    assert "禁止整段粘进终稿" in out
    assert "禁止编造" in out and "全部交付" in out
    assert "PPT 已落盘" in out and ".pptx" in out
    # 无命题卡时不塞开辩死文案
    assert "建议开辩" not in out.split("以上为团队产出", 1)[-1]


def test_worker_products_failed_with_body_surfaces_error_not_pass_through():
    """Contract-failed workers often still have a body — CEO must see 失败, not the body."""
    t = tool(Provider([]))
    plan = RunPlan(nodes=[RunSpec(run_id="w_pr", task="写公关稿", role="舆情分析师")])
    results = {
        "w_pr": RunState(
            phase=RunPhase.FAILED,
            content="invoke tool file_write path=lv_jasmine_pr.md",
            error="未把产物写入工作区：交付物须用 file_write 落盘",
        )
    }
    products = worker_products(t, plan, results)
    assert len(products) == 1
    assert products[0]["status"] == "failed"
    assert products[0]["fidelity"] == ""
    assert "失败" in products[0]["body"]
    assert "未把产物写入工作区" in products[0]["body"]
    assert "invoke tool file_write" not in products[0]["body"]


def test_worker_products_empty_body_with_files_and_debrief_is_pointer():
    """A3: 空正文 + files_touched + debrief → pointer，交接结论不丢；失败节点不装交付。"""
    t = tool(Provider([]))
    plan = RunPlan(
        nodes=[
            RunSpec(run_id="w_ok", task="写报告", role="撰稿"),
            RunSpec(run_id="w_fail", task="写附录", role="附录"),
        ]
    )
    results = {
        "w_ok": RunState(
            phase=RunPhase.COMPLETED,
            content="",
            files_touched=["report.md"],
            file_acceptance=_accepted("report.md"),
            debrief={
                "summary": "报告已落盘",
                "key_points": ["结论甲", "路径 report.md"],
                "next_steps": "可做竞品对比",
            },
        ),
        "w_fail": RunState(
            phase=RunPhase.FAILED,
            content="",
            files_touched=["draft.md"],
            error="契约未达标",
            debrief={"summary": "半成品勿当真交付"},
        ),
    }
    products = worker_products(t, plan, results)
    by_id = {p["run_id"]: p for p in products}
    ok = by_id["w_ok"]
    assert ok["fidelity"] == "pointer"
    assert ok["status"] == "completed"
    assert "交接结论：报告已落盘" in ok["body"]
    assert "结论甲" in ok["body"]
    assert "文件产出（路径已核）" in ok["body"]
    fail = by_id["w_fail"]
    assert fail["fidelity"] == ""
    assert fail["status"] == "failed"
    assert "失败" in fail["body"] and "契约未达标" in fail["body"]
    assert "交接结论" not in fail["body"]  # 勿把失败装成已交付 pointer
    out = format_for_ceo(t, plan, results)
    assert "交接结论：报告已落盘" in out
    assert "可做竞品对比" in out  # debrief.next_steps 仍进建议区


def test_format_for_ceo_footer_is_lean_but_keeps_iron_laws():
    """B: 无条件瘦 footer — 防幻觉/文件产出/名册铁律仍在，自然长度下降。"""
    t = tool(Provider([]))
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="写摘要", role="调研员")])
    results = {"w1": RunState(phase=RunPhase.COMPLETED, content="短综述")}
    out = format_for_ceo(t, plan, results)
    footer = out.split("以上为团队产出", 1)[-1]
    assert "防幻觉" in footer
    assert "文件产出（路径已核）" in footer
    assert "文件产出（已验收）" not in footer
    assert "脚本已跑通" in footer
    assert "未真正落盘" in footer or "未达成" in footer
    assert "队员终态名册" in footer or "全部交付" in footer
    assert "失败" in footer or "接替" in footer or "禁止编造" in footer
    # Old footer was ~900+ chars of packaging; lean target stays well under that.
    assert len(footer) < 550
    assert "工作日志" not in footer
    assert "上方若有【建议开辩】" not in footer


def test_format_for_ceo_roster_forbids_all_delivered_when_partial_failure():
    """Partial failure + replaces_run_id must surface; CEO must not invent 全部交付."""
    t = tool(Provider([]))
    plan = RunPlan(
        nodes=[
            RunSpec(run_id="w_ms", task="调研微软", role="Microsoft 调研员"),
            RunSpec(
                run_id="w_ms2", task="补调研微软", role="Microsoft 补派", replaces_run_id="w_ms"
            ),
            RunSpec(run_id="w_ok", task="调研 OpenAI", role="OpenAI 调研员"),
        ]
    )
    results = {
        "w_ms": RunState(phase=RunPhase.FAILED, content="", error="timeout"),
        "w_ms2": RunState(phase=RunPhase.COMPLETED, content="补派完成"),
        "w_ok": RunState(phase=RunPhase.COMPLETED, content="OpenAI 完成"),
    }
    out = format_for_ceo(t, plan, results)
    assert "队员终态名册" in out
    assert "失败" in out and "w_ms" in out
    assert "接替" in out and "replaces_run_id" in out
    assert "禁止编造" in out or "全部交付" in out
    assert "【接替】" in out
    assert "有队员失败/被跳过/被接替" in out


def test_format_for_ceo_roster_budget_skipped_continue_hint():
    t = tool(Provider([]))
    plan = RunPlan(
        nodes=[
            RunSpec(run_id="a", task="基建", role="基建"),
            RunSpec(run_id="b", task="整合", role="整合", depends_on=["a"]),
        ]
    )
    results = {
        "a": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["x.ts"],
            file_acceptance=_accepted("x.ts"),
        ),
        "b": RunState(
            phase=RunPhase.SKIPPED,
            delivery_gaps=[
                {
                    "description": "额度触顶跳过",
                    "reason": "turn_token_budget",
                }
            ],
        ),
    }
    out = format_for_ceo(t, plan, results)
    assert "因额度跳过" in out
    assert "整合" in out
    assert "下一回合" in out
    assert "续" in out
    assert "假装" in out or "禁止" in out


def test_format_for_ceo_emits_uncapped_synthesis_metric():
    t = tool(Provider([]))
    nodes = [RunSpec(run_id=f"w{i}", task="分析", role=f"分析{i}") for i in range(8)]
    plan = RunPlan(nodes=nodes)
    results = {
        f"w{i}": RunState(phase=RunPhase.COMPLETED, content=f"头{i}" + ("数" * 8_000))
        for i in range(8)
    }
    with capture_logs() as logs:
        format_for_ceo(t, plan, results)
    metric = next(e for e in logs if e["event"] == "delegate.synthesis")
    assert metric["capped"] is False
    assert metric["workers"] == 8 and metric["prose"] == 8
    assert metric["ratio"] < 1.0
    assert metric["ratio_capped"] is False


def test_build_ceo_synthesis_same_conclusion_logs_once():
    """finalize 幂等：同一 tool + execution + 结论只打一次 delegate.synthesis。"""
    t = tool(Provider([]))
    t._base_tool_context.execution_id = "e-syn-idem"
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="分析", role="分析")])
    results = {"w1": RunState(phase=RunPhase.COMPLETED, content="综述正文")}
    with capture_logs() as logs:
        first = build_ceo_synthesis(t, plan, results, call_idx=1)
        second = build_ceo_synthesis(t, plan, results, call_idx=1)
    assert first.text == second.text
    synth = [e for e in logs if e["event"] == "delegate.synthesis"]
    assert len(synth) == 1


def test_format_for_ceo_caps_short_raw_expansion_ratio():
    """Short pointer-like raw must not expand into ~6k packaging (ratio~12)."""
    from agentcore.runtime.runs.constants import CEO_SYNTHESIS_MAX_CHARS

    t = tool(Provider([]))
    # Many file producers with tiny orientation notes — the old path bloated via
    # per-worker digests + footer even when raw_chars was tiny.
    nodes = [RunSpec(run_id=f"w{i}", task="写一段", role=f"写手{i}") for i in range(12)]
    plan = RunPlan(nodes=nodes)
    results = {
        f"w{i}": RunState(
            phase=RunPhase.COMPLETED,
            content=f"ok{i}",
            files_touched=[f"out/{i}.md"],
            file_acceptance=_accepted(f"out/{i}.md"),
            debrief={"summary": f"完成{i}", "key_points": [f"路径 out/{i}.md"]},
        )
        for i in range(12)
    }
    with capture_logs() as logs:
        out = format_for_ceo(t, plan, results)
    metric = next(e for e in logs if e["event"] == "delegate.synthesis")
    raw = metric["raw_chars"]
    assert raw < 200
    assert len(out) <= CEO_SYNTHESIS_MAX_CHARS
    assert metric["final_chars"] <= CEO_SYNTHESIS_MAX_CHARS
    # Prefer-brief keeps natural size well under the old ~6k regime (log ratio~12).
    assert metric["final_chars"] < 3500
    assert metric["ratio_capped"] is False  # natural size under cap
    assert "交接结论" in out and "要点：" in out
    assert "队员终态名册" in out or "写手0" in out
    assert "文件产出" in out or "out/0.md" in out
