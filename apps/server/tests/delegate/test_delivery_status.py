"""交付状态结构化（能力闸门与交付诚实性）：delivery_status 构建与发射单元测试。"""

from __future__ import annotations

import pytest

from agentcore.core.types import AutonomyPolicy, recipe_to_axes
from agentcore.runtime.delegate.delivery_status import (
    build_delivery_status,
    maybe_emit_delivery_status,
)
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.runs.file_acceptance import REASON_PATH_MISMATCH, build_file_acceptance
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import Deliverable, RunPhase, RunSpec, RunState
from agentcore.tools.builtin.delegate import DelegateTool
from agentcore.tools.registry import ToolRegistry
from tests.delegate.conftest import LocalBackend, Provider, ctx, local_ctx


def _plan(*specs: RunSpec) -> RunPlan:
    return RunPlan(nodes=list(specs))


def _accepted(*paths: str) -> list[dict]:
    """Stamp COMPLETED acceptance rows (new contract; no files_touched synthesis)."""
    return build_file_acceptance(list(paths), phase=RunPhase.COMPLETED)


def test_pure_prose_success_stays_silent():
    plan = _plan(RunSpec(run_id="w1", task="调研", role="研究员"))
    results = {"w1": RunState(phase=RunPhase.COMPLETED, content="综述正文")}
    assert build_delivery_status(plan, results, execution_id="e") is None


def test_files_touched_without_file_acceptance_not_synthesized():
    """No legacy acceptance: files_touched alone must not invent delivered_files."""
    plan = _plan(RunSpec(run_id="w1", task="写讲稿", role="撰写"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="已落盘",
            files_touched=["讲稿.md", "notes/大纲.md"],
        )
    }
    assert build_delivery_status(plan, results, execution_id="e-no-acc") is None


def test_all_files_delivered_no_gaps():
    plan = _plan(RunSpec(run_id="w1", task="写讲稿", role="撰写"))
    touched = ["讲稿.md", "notes/大纲.md"]
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="已落盘",
            files_touched=touched,
            file_acceptance=_accepted(*touched),
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e1")
    assert payload is not None
    assert payload["state"] == "delivered"
    assert payload["delivered_files"] == ["讲稿.md", "notes/大纲.md"]
    assert payload["gaps"] == []
    assert payload["actions"] == []
    assert "已交付 2 个文件" in payload["summary"]


def test_partial_with_worker_gaps_and_degraded_debrief():
    # collect_worker_gaps 信号：缺产物 warning 仍 blocking；degraded 交接在已落盘时
    # 降为 notes（刀1 / 方案 A），不抬 partial。
    plan = _plan(
        RunSpec(run_id="w1", task="生成课件", role="课件工程师"),
        RunSpec(run_id="w2", task="写讲稿", role="撰写", depends_on=["w1"]),
    )
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="脚本已写",
            files_touched=["build_pptx.py"],
            file_acceptance=_accepted("build_pptx.py"),
            warnings=["声明产物 course.pptx 未在工作区找到"],
            debrief={"summary": "引擎合成", "degraded": True},
        ),
        "w2": RunState(
            phase=RunPhase.COMPLETED,
            content="讲稿",
            files_touched=["讲稿.md"],
            file_acceptance=_accepted("讲稿.md"),
        ),
    }
    payload = build_delivery_status(plan, results, execution_id="e2")
    assert payload is not None
    # 仍有「course.pptx 未找到」blocking → partial；degraded 为 warning 备注。
    assert payload["state"] == "partial"
    assert set(payload["delivered_files"]) == {"build_pptx.py", "讲稿.md"}
    descriptions = [g["description"] for g in payload["gaps"]]
    assert any("course.pptx" in d for d in descriptions)
    assert any("交接说明不够完整" in d or "降级" in d for d in descriptions)
    degraded = next(g for g in payload["gaps"] if g.get("reason") == "degraded_handoff")
    assert degraded.get("severity") == "warning"
    assert all(g["role"] == "课件工程师" for g in payload["gaps"])


def test_landed_files_with_only_degraded_handoff_are_notes_not_failed():
    """刀1 / 方案 A：strict 场景下已落盘 + 仅 degraded_handoff → notes，非 partial/blocked。"""
    plan = _plan(RunSpec(run_id="w1", task="写片段", role="分区"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="已落盘",
            files_touched=["site/sections/s0.html"],
            file_acceptance=_accepted("site/sections/s0.html"),
            debrief={"summary": "薄", "degraded": True},
            delivery_gaps=[
                {
                    "description": "交接说明不够完整，系统已代为补写摘要",
                    "reason": "degraded_handoff",
                }
            ],
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-deg-ok")
    assert payload is not None
    assert payload["state"] == "notes"
    assert payload["delivered_files"] == ["site/sections/s0.html"]
    assert payload["gaps"][0]["severity"] == "warning"
    assert payload["gaps"][0]["reason"] == "degraded_handoff"
    assert "交接备注" in payload["summary"] or "已交付" in payload["summary"]
    assert all(a.get("status") != "rejected" for a in payload.get("artifacts") or [])


def test_no_landing_with_degraded_handoff_is_notes():
    """无声明产物 + degraded → notes（空交不再整轮 blocked）。"""
    plan = _plan(RunSpec(run_id="w1", task="写片段", role="分区"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="只有文字",
            debrief={"summary": "薄", "degraded": True},
            delivery_gaps=[
                {
                    "description": "交接说明不够完整，系统已代为补写摘要",
                    "reason": "degraded_handoff",
                }
            ],
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-deg-fail")
    assert payload is not None
    assert payload["state"] == "notes"
    assert payload["delivered_files"] == []
    assert any(g.get("reason") == "degraded_handoff" for g in payload["gaps"])
    assert all(
        g.get("severity") == "warning"
        for g in payload["gaps"]
        if g.get("reason") == "degraded_handoff"
    )


def test_plan_cutoff_skip_suppressed_when_continue_from_ran():
    """同图 continue_from 补派已跑 → 不并排挂「计划收口时跳过」。"""
    plan = _plan(
        RunSpec(run_id="a", task="t", role="A"),
        RunSpec(
            run_id="a2",
            task="续",
            role="A续",
            continue_from_run_id="a",
        ),
    )
    results = {
        "a": RunState(phase=RunPhase.SKIPPED),
        "a2": RunState(
            phase=RunPhase.COMPLETED,
            files_touched=["out.md"],
            file_acceptance=_accepted("out.md"),
        ),
    }
    payload = build_delivery_status(plan, results, execution_id="e-skip-cover")
    assert payload is not None
    assert payload["state"] == "delivered"
    assert not any("计划收口时跳过" in (g.get("description") or "") for g in payload["gaps"])
    assert payload["delivered_files"] == ["out.md"]


def test_plan_cutoff_skip_suppressed_when_replaces_ran():
    plan = _plan(
        RunSpec(run_id="old", task="t", role="原"),
        RunSpec(run_id="new", task="接手", role="新", replaces_run_id="old"),
    )
    results = {
        "old": RunState(phase=RunPhase.SKIPPED),
        "new": RunState(
            phase=RunPhase.COMPLETED,
            files_touched=["x.md"],
            file_acceptance=_accepted("x.md"),
        ),
    }
    payload = build_delivery_status(plan, results, execution_id="e-rep-cover")
    assert payload is not None
    assert not any("计划收口时跳过" in (g.get("description") or "") for g in payload["gaps"])


def test_blocked_with_criteria_gap_and_bind_action_on_cloud():
    # 「验收」批次级缺口 + 云端无执行环境 → bind_local_folder 行动项（复用单一真相源判定）。
    # 已是云会话：文案须诚实「沙箱未装配」，禁止再推「导入到云」。
    plan = _plan(RunSpec(run_id="w1", task="运行脚本生成 course.pptx", role="课件工程师"))
    results = {"w1": RunState(phase=RunPhase.COMPLETED, content="只有文字")}
    payload = build_delivery_status(
        plan,
        results,
        execution_id="e3",
        backend=ctx().backend,
        criteria_gaps=["尚无 worker 成功运行 code_execute / test_run 验证代码"],
    )
    assert payload is not None
    assert payload["state"] == "blocked"
    assert payload["delivered_files"] == []
    assert payload["gaps"][0]["role"] == "验收"
    assert payload["actions"] and payload["actions"][0]["kind"] == "bind_local_folder"
    desc = payload["actions"][0]["description"]
    assert "沙箱" in desc or "未装配" in desc
    assert "不要" in desc or "勿" in desc or "禁止" in desc
    assert "导入到云" in desc  # 出现在「不要再引导」语境
    assert "推荐** Composer「导入到云" not in desc
    assert "**推荐** Composer「导入到云" not in desc
    assert "合法非默认" in desc or "本机传统" in desc or "export_to_local" in desc
    assert "改导" not in desc
    assert "勿再绑" not in desc


def test_zero_landing_worker_keeps_role_blocking_gap():
    # 定案 B：per-worker「本队员本波未交卷」；写盘形态 blocking，不得 delivered。
    from agentcore.runtime.runs.serialize import format_file_landing_tools_slash
    from agentcore.runtime.runs.types import Deliverable

    plan = _plan(
        RunSpec(
            run_id="w1",
            task="生成 pptx",
            role="执行工程师",
            deliverable=Deliverable(form="files"),
        )
    )
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="做好了",
            delivery_gaps=[
                {
                    "description": (
                        "本队员本波未交卷：未把产物写入工作区：交付物须用 file_write / "
                        "str_replace / file_append 或 code_execute / file_copy 落盘，"
                        "而非粘在回复正文里"
                    ),
                    "severity": "warning",
                    "reason": "files_not_landed",
                }
            ],
        )
    }
    tools = format_file_landing_tools_slash()
    payload = build_delivery_status(
        plan,
        results,
        execution_id="e-merge",
        criteria_gaps=[f"提醒（不阻断验收）：本批未见落盘（需要 {tools}）"],
    )
    assert payload is not None
    assert payload["state"] == "blocked"
    assert payload["state"] != "delivered"
    zero_gaps = [g for g in payload["gaps"] if g.get("reason") == "files_not_landed"]
    assert len(zero_gaps) == 1
    gap = zero_gaps[0]
    assert gap["role"] == "执行工程师"
    assert gap.get("severity") != "warning"
    assert "本队员本波未交卷" in gap["description"]
    assert "未把产物写入工作区" in gap["description"]
    assert not any(
        g.get("role") == "验收" and g.get("reason") == "files_not_landed" for g in payload["gaps"]
    )


def test_zero_landing_mixed_batch_attributes_empty_worker_only():
    """一人落盘、一人空转 → 仅空转队员可见；整场 partial，不得 delivered。"""
    from agentcore.runtime.runs.types import Deliverable

    plan = _plan(
        RunSpec(
            run_id="ok",
            task="写 A",
            role="修码员",
            deliverable=Deliverable(form="files"),
        ),
        RunSpec(
            run_id="empty",
            task="写 B",
            role="前端工程师",
            deliverable=Deliverable(form="files"),
        ),
    )
    results = {
        "ok": RunState(
            phase=RunPhase.COMPLETED,
            content="done",
            files_touched=["a.ts"],
            file_acceptance=_accepted("a.ts"),
        ),
        "empty": RunState(
            phase=RunPhase.COMPLETED,
            content="还在读",
            delivery_gaps=[
                {
                    "description": (
                        "本队员本波未交卷：未把产物写入工作区：交付物须用 "
                        "file_write 落盘，而非粘在回复正文里"
                    ),
                    "severity": "warning",
                    "reason": "files_not_landed",
                }
            ],
        ),
    }
    payload = build_delivery_status(plan, results, execution_id="e-mixed-wave")
    assert payload is not None
    assert payload["state"] == "partial"
    assert payload["state"] != "delivered"
    assert "a.ts" in payload["delivered_files"]
    zero_gaps = [g for g in payload["gaps"] if g.get("reason") == "files_not_landed"]
    assert len(zero_gaps) == 1
    assert zero_gaps[0]["role"] == "前端工程师"
    assert "本队员本波未交卷" in zero_gaps[0]["description"]
    assert zero_gaps[0].get("severity") != "warning"
    assert not any(g.get("role") == "修码员" for g in zero_gaps)


def test_zero_landing_gap_attributes_channel_dead_from_transcript():
    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
    from agentcore.runtime.engine.tool_exec import with_tool_failed_marker
    from agentcore.runtime.runs.types import Deliverable

    plan = _plan(
        RunSpec(
            run_id="w1",
            task="写文件",
            role="工程师",
            deliverable=Deliverable(form="files"),
        )
    )
    transcript = [
        LLMMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="w1",
                    function=ToolCallFunction(
                        name="file_write",
                        arguments='{"path": "a.md", "content": "x"}',
                    ),
                )
            ],
        ),
        LLMMessage(
            role="tool",
            tool_call_id="w1",
            content=with_tool_failed_marker(
                "local workspace op 'write' rejected: channel dead（活性挂起）"
            ),
        ),
    ]
    results = {
        "w1": RunState(
            phase=RunPhase.FAILED,
            content="",
            error=(
                "本队员本波未交卷：未把产物写入工作区：写盘通道不可用（local workspace "
                "channel dead / 活性挂起），落盘工具已失败——"
                "请在 handoff 或正文交结论，禁止再尝试落盘；"
                "可请用户恢复工作区通道后重试"
            ),
            transcript=transcript,
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-dead")
    assert payload is not None
    gap = payload["gaps"][0]
    # 能力4：FAILED 零落盘 soft 仍保留 node_failed（draft_ack 闩），不退回 files_not_landed。
    assert gap["reason"] == "node_failed"
    assert gap["severity"] == "warning"
    assert gap["role"] == "工程师"
    assert payload["state"] == "notes"
    assert "本队员本波未交卷" in gap["description"]
    assert "写盘通道不可用" in gap["description"]
    assert "handoff 或正文交结论" in gap["description"]
    assert "禁止再尝试落盘" in gap["description"]
    assert "粘在回复正文" not in gap["description"]
    assert "勿改用正文粘贴冒充落盘" not in gap["description"]


def test_batch_zero_landing_gap_channel_dead_asks_prose_handoff():
    """Batch-only channel_dead tip mirrors retire steer (prose/handoff, no paste ban)."""
    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
    from agentcore.runtime.delegate import delivery_status as ds
    from agentcore.runtime.engine.tool_exec import with_tool_failed_marker

    transcript = [
        LLMMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="w1",
                    function=ToolCallFunction(
                        name="file_write",
                        arguments='{"path": "a.md", "content": "x"}',
                    ),
                )
            ],
        ),
        LLMMessage(
            role="tool",
            tool_call_id="w1",
            content=with_tool_failed_marker(
                "local workspace op 'write' rejected: channel dead（活性挂起）"
            ),
        ),
    ]
    results = {
        "w1": RunState(
            phase=RunPhase.FAILED,
            content="",
            transcript=transcript,
        )
    }
    gap = ds._files_not_landed_gap(results)
    assert gap["reason"] == "files_not_landed"
    assert gap["severity"] == "warning"
    assert "写盘通道不可用" in gap["description"]
    assert "handoff 或正文交结论" in gap["description"]
    assert "禁止再尝试落盘" in gap["description"]
    assert "恢复通道后重试" in gap["description"]
    assert "勿改用正文粘贴冒充落盘" not in gap["description"]
    assert "粘在回复正文" not in gap["description"]


def test_zero_landing_gap_attributes_write_failed_from_transcript():
    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
    from agentcore.runtime.engine.tool_exec import with_tool_failed_marker
    from agentcore.runtime.runs.types import Deliverable

    plan = _plan(
        RunSpec(
            run_id="w1",
            task="复制成品",
            role="工程师",
            deliverable=Deliverable(form="files"),
        )
    )
    transcript = [
        LLMMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="c1",
                    function=ToolCallFunction(
                        name="file_copy",
                        arguments='{"source": "a", "destination": "b.md"}',
                    ),
                )
            ],
        ),
        LLMMessage(
            role="tool",
            tool_call_id="c1",
            content=with_tool_failed_marker("目标已存在：b.md"),
        ),
    ]
    results = {
        "w1": RunState(
            phase=RunPhase.FAILED,
            content="试过了",
            error=("本队员本波未交卷：未把产物写入工作区：已尝试写盘但未成功落盘（工具失败）"),
            transcript=transcript,
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-wfail")
    assert payload is not None
    gap = payload["gaps"][0]
    # 能力4：FAILED 零落盘 soft 仍保留 node_failed（draft_ack 闩）。
    assert gap["reason"] == "node_failed"
    assert gap["severity"] == "warning"
    assert gap["role"] == "工程师"
    assert payload["state"] == "notes"
    assert "本队员本波未交卷" in gap["description"]
    assert "已尝试写盘但未成功" in gap["description"]
    assert "而非粘在回复正文" in gap["description"] or "粘在回复正文" not in gap["description"]


def test_maybe_emit_sets_current_delivery_verdict():
    from agentcore.runtime.delegate.delivery_status import current_delivery_verdict

    current_delivery_verdict.set(None)
    sink = EventSink()
    plan = _plan(RunSpec(run_id="w1", task="写文件", role="工程师"))
    maybe_emit_delivery_status(
        sink,
        plan,
        {
            "w1": RunState(
                phase=RunPhase.COMPLETED,
                content="ok",
                files_touched=["a.md"],
                file_acceptance=_accepted("a.md"),
            )
        },
        execution_id="e-verdict",
    )
    verdict = current_delivery_verdict.get()
    assert verdict is not None
    assert verdict.state == "delivered"
    assert verdict.delivered_files == ("a.md",)
    assert verdict.execution_id == "e-verdict"


def test_unresolved_write_ownership_forces_partial_delivery_status(monkeypatch):
    """案 P0-B：账本仍有未解 denied → delivery state 不得 delivered。"""
    from agentcore.runtime.closing_posture import (
        clear_unresolved_write_ownership,
        turn_has_unresolved_write_ownership,
    )
    from agentcore.runtime.delegate.delivery_status import (
        REASON_WRITE_OWNERSHIP,
        current_delivery_verdict,
    )
    from agentcore.workspace.write_claims import WriteCoordinator

    clear_unresolved_write_ownership()
    current_delivery_verdict.set(None)
    coord = WriteCoordinator({"plan.md": "del_owner"})
    assert coord.claim("plan.md", "del_merger", frozenset()) == "del_owner"

    monkeypatch.setattr(
        "agentcore.workspace.write_claims.resolve_write_coordinator",
        lambda **_kwargs: coord,
    )

    plan = _plan(
        RunSpec(run_id="del_owner", task="写计划", role="架构师"),
        RunSpec(run_id="del_merger", task="合并", role="合并员"),
    )
    results = {
        "del_owner": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["plan.md"],
            file_acceptance=_accepted("plan.md"),
        ),
        "del_merger": RunState(phase=RunPhase.COMPLETED, content="撞锁"),
    }
    payload = build_delivery_status(plan, results, execution_id="e-own-gap")
    assert payload is not None
    assert payload["state"] == "partial"
    assert any(
        isinstance(g, dict) and g.get("reason") == REASON_WRITE_OWNERSHIP for g in payload["gaps"]
    )
    assert turn_has_unresolved_write_ownership()

    sink = EventSink()
    maybe_emit_delivery_status(sink, plan, results, execution_id="e-own-gap")
    verdict = current_delivery_verdict.get()
    assert verdict is not None
    assert verdict.state == "partial"
    clear_unresolved_write_ownership()
    current_delivery_verdict.set(None)


def test_soft_unverified_note_only_is_delivered_not_notes():
    """轻 B：仅 unverified_note + 已落盘 → delivered（gaps 仍保留 soft 行）。"""
    plan = _plan(RunSpec(run_id="w1", task="写调研", role="调研员"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["findings.md"],
            file_acceptance=_accepted("findings.md"),
            warnings=[
                "含示例/虚构自注（2 处）：`findings.md` · 示例数据 · 「示例」；"
                "`findings.md` · 虚构/示意 · 「估算」。"
            ],
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-notes")
    assert payload is not None
    assert payload["state"] == "delivered"
    assert payload["gaps"][0]["severity"] == "warning"
    assert payload["gaps"][0]["reason"] == "unverified_note"
    assert "findings.md" in (payload["gaps"][0].get("paths") or [])
    assert "待核实备注" in payload["summary"]
    assert payload["actions"] == []


def test_unverified_note_mixed_with_path_mismatch_is_partial():
    """路径失配为 blocking：有 files_touched 时整轮 partial（不再因未进声明清单 blocked）。"""
    plan = _plan(
        RunSpec(
            run_id="w1",
            task="写调研",
            role="调研员",
            deliverable=Deliverable(form="files", artifact_dir="docs/research"),
        )
    )
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["findings.md"],
            file_acceptance=_accepted("findings.md"),
            warnings=["含示例/虚构自注（1 处）：`findings.md` · 示例数据 · 「示例」。"],
            delivery_gaps=[
                {
                    "description": (
                        "产物未写入约定文档目录 `docs/research/`"
                        "（建议落在此目录下，勿写到工作区根）"
                    ),
                    "severity": "warning",
                    "reason": "path_hint",
                }
            ],
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-mix-soft")
    assert payload is not None
    assert payload["state"] == "partial"
    assert payload["state"] != "delivered"
    assert "findings.md" not in payload["delivered_files"]
    assert all(a.get("path") != "findings.md" for a in payload["artifacts"])
    reasons = {g.get("reason") for g in payload["gaps"]}
    assert "unverified_note" in reasons
    assert REASON_PATH_MISMATCH in reasons


def test_overlay_soft_criteria_gaps_are_delivered_not_partial():
    """D2 / auto-graph soft notes via criteria_gaps → delivered（轻 B；非 partial/blocked）。"""
    plan = _plan(RunSpec(run_id="w1", task="写组件", role="前端"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["src/App.tsx"],
            file_acceptance=_accepted("src/App.tsx"),
        )
    }
    payload = build_delivery_status(
        plan,
        results,
        execution_id="e-overlay",
        criteria_gaps=[
            "提醒（不阻断验收）：已落盘 .ts/.tsx，建议补一次验证"
            "（code_execute / test_run / terminal 跑通 tsc|typecheck|test|build；"
            "启动开发服务器不算）"
        ],
    )
    assert payload is not None
    assert payload["state"] == "delivered"
    assert payload["gaps"][0]["severity"] == "warning"
    assert payload["gaps"][0]["reason"] == "unverified_note"
    assert "partial" not in payload["state"]
    assert "blocked" not in payload["state"]
    assert payload["actions"] == []


def test_declared_path_a_landed_b_is_omitted_not_on_card():
    """声明 A 实际落 B → 缺 A 的 gap；B 不进卡、不打未通过。"""
    declared = "external/AgentCode/research/01-topic.md"
    landed = "docs/01-topic.md"
    plan = _plan(
        RunSpec(
            run_id="w1",
            task="写调研",
            role="调研员",
            deliverable=Deliverable(form="files", artifacts=[declared]),
        )
    )
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=[landed],
            file_acceptance=_accepted(landed),
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-path-mismatch")
    assert payload is not None
    assert payload["state"] == "partial"
    assert payload["state"] != "delivered"
    assert payload["delivered_files"] == []
    assert landed not in payload["delivered_files"]
    assert all(a.get("path") != landed for a in payload["artifacts"])
    mismatch = [g for g in payload["gaps"] if g.get("reason") == REASON_PATH_MISMATCH]
    assert mismatch
    assert any(declared in (g.get("description") or "") for g in mismatch)
    assert all(g.get("severity") != "warning" for g in mismatch)


def test_workspace_leftover_dir_keeps_bare_names_delivered_at_worker_path():
    """workspace leftover 目录不 join；worker 落在裸名上 → accepted。"""
    from agentcore.runtime.runs.artifact_dir import apply_artifact_dir_defaults
    from agentcore.workspace.stage_dirs import REVIEWS_DIR

    names = [
        "前端刷新审计-对话页面.md",
        "前端刷新审计-工作台.md",
        "前端刷新审计-协作图.md",
    ]
    nodes: list[RunSpec] = []
    results: dict[str, RunState] = {}
    for i, name in enumerate(names, start=1):
        deliverable = Deliverable(
            form="files",
            artifacts=[name],
            artifact_dir=REVIEWS_DIR,
            workspace_native=True,
        )
        apply_artifact_dir_defaults(deliverable)
        assert deliverable.form == "workspace"
        assert deliverable.artifact_dir == ""
        assert deliverable.artifacts == [name]
        run_id = f"w{i}"
        nodes.append(
            RunSpec(
                run_id=run_id,
                task=f"审 {name}",
                role="审查官",
                deliverable=deliverable,
            )
        )
        results[run_id] = RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=[name],
            file_acceptance=_accepted(name),
        )
    payload = build_delivery_status(
        RunPlan(nodes=nodes),
        results,
        execution_id="e-native-adir-bare",
    )
    assert payload is not None
    assert payload["state"] == "delivered"
    assert payload["delivered_files"] == names
    by_path = {a["path"]: a for a in payload["artifacts"]}
    for name in names:
        assert by_path[name]["status"] == "accepted"
    assert not any(g.get("reason") == REASON_PATH_MISMATCH for g in payload["gaps"])


def test_declared_path_match_still_delivered():
    """声明路径与落盘一致 → 仍可 accepted / delivered。"""
    path = "external/AgentCode/research/01-topic.md"
    plan = _plan(
        RunSpec(
            run_id="w1",
            task="写调研",
            role="调研员",
            deliverable=Deliverable(form="files", artifacts=[path]),
        )
    )
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=[path],
            file_acceptance=_accepted(path),
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-path-ok")
    assert payload is not None
    assert payload["state"] == "delivered"
    assert payload["delivered_files"] == [path]


def test_undeclared_extra_omitted_when_declared_file_accepted():
    """声明件过关时，file_copy 备份不进卡、不打未通过、不撑 artifact_rejected。"""
    declared = "AgentCore/文档/reviews/code-audit-summary.md"
    extra = "AgentCore/文档/reviews/code-audit-summary-时序审计初版.md"
    plan = _plan(
        RunSpec(
            run_id="w1",
            task="更新汇总",
            role="审计主管",
            deliverable=Deliverable(form="files", artifacts=[declared]),
        )
    )
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=[extra, declared],
            file_acceptance=_accepted(extra, declared),
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-extra")
    assert payload is not None
    assert payload["state"] == "delivered"
    assert payload["delivered_files"] == [declared]
    paths = [a["path"] for a in payload["artifacts"]]
    assert paths == [declared]
    assert all(a.get("status") == "accepted" for a in payload["artifacts"])
    assert not any(g.get("reason") == REASON_PATH_MISMATCH for g in payload["gaps"])
    assert not any(g.get("reason") == "artifact_rejected" for g in payload["gaps"])


def test_execution_artifacts_union_across_hops():
    """后一波 1 人改汇总：前一波已验收路径留在卡上；备份省略；同 path 后写覆盖。"""
    first = "AgentCore/文档/reviews/code-audit-0-执行时序推进.md"
    summary = "AgentCore/文档/reviews/code-audit-summary.md"
    extra = "AgentCore/文档/reviews/code-audit-summary-时序审计初版.md"
    ledger = _promotion_ledger()
    from agentcore.runtime.delegate.delivery_status import current_delivery_verdict

    current_delivery_verdict.set(None)
    hop1 = _plan(
        RunSpec(
            run_id="audit_0",
            task="成文",
            role="代码审计员",
            deliverable=Deliverable(form="files", artifacts=[first]),
        )
    )
    maybe_emit_delivery_status(
        EventSink(),
        hop1,
        {
            "audit_0": RunState(
                phase=RunPhase.COMPLETED,
                content="ok",
                files_touched=[first],
                file_acceptance=_accepted(first),
            )
        },
        execution_id="e-union",
        promotion_ledger=ledger,
    )
    current_delivery_verdict.set(None)
    hop2 = _plan(
        RunSpec(
            run_id="synth",
            task="更新汇总",
            role="审计主管",
            deliverable=Deliverable(form="files", artifacts=[summary]),
        )
    )
    payload = build_delivery_status(
        hop2,
        {
            "synth": RunState(
                phase=RunPhase.COMPLETED,
                content="ok",
                files_touched=[extra, summary],
                file_acceptance=_accepted(extra, summary),
            )
        },
        execution_id="e-union",
        promotion_ledger=ledger,
    )
    assert payload is not None
    paths = [a["path"] for a in payload["artifacts"]]
    assert paths == [first, summary]
    assert extra not in paths
    assert payload["delivered_files"] == [first, summary]
    assert all(a["status"] == "accepted" for a in payload["artifacts"])


def test_union_drops_historical_path_mismatch_extras():
    """台账里旧的 path_mismatch 拒收行不再复活到主清单。"""
    ledger = _promotion_ledger()
    ledger.reconciliation = {
        "execution_id": "e-hist",
        "artifacts": [
            {"path": "ok.md", "status": "accepted"},
            {
                "path": "backup.md",
                "status": "rejected",
                "reason": REASON_PATH_MISMATCH,
            },
        ],
    }
    declared = "summary.md"
    plan = _plan(
        RunSpec(
            run_id="w1",
            task="写汇总",
            role="主管",
            deliverable=Deliverable(form="files", artifacts=[declared]),
        )
    )
    payload = build_delivery_status(
        plan,
        {
            "w1": RunState(
                phase=RunPhase.COMPLETED,
                content="ok",
                files_touched=[declared],
                file_acceptance=_accepted(declared),
            )
        },
        execution_id="e-hist",
        promotion_ledger=ledger,
    )
    assert payload is not None
    paths = [a["path"] for a in payload["artifacts"]]
    assert paths == ["ok.md", declared]
    assert "backup.md" not in paths


def test_workspace_prefix_declared_matches_relative_landing():
    """``/workspace/A`` 与相对 ``A`` 是同一路径（normalize），不是失配。"""
    plan = _plan(
        RunSpec(
            run_id="w1",
            task="写首页",
            role="前端",
            deliverable=Deliverable(form="files", artifacts=["/workspace/index.html"]),
        )
    )
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["index.html"],
            file_acceptance=_accepted("index.html"),
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-ws-norm")
    assert payload is not None
    assert payload["state"] == "delivered"
    assert payload["delivered_files"] == ["index.html"]


def test_artifact_dir_landed_outside_is_omitted_not_delivered():
    """仅声明 artifact_dir 时，落到目录外的文件不进卡、不进 delivered_files。"""
    plan = _plan(
        RunSpec(
            run_id="w1",
            task="调研 Miro 落盘",
            role="竞品分析师",
            deliverable=Deliverable(form="files", artifact_dir="docs/research"),
        )
    )
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["miro-research.md"],
            file_acceptance=_accepted("miro-research.md"),
            delivery_gaps=[
                {
                    "description": (
                        "产物未写入约定文档目录 `docs/research/`"
                        "（建议落在此目录下，勿写到工作区根）"
                    ),
                    "severity": "warning",
                    "reason": "path_hint",
                }
            ],
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-adir")
    assert payload is not None
    assert payload["state"] == "partial"
    assert payload["state"] != "delivered"
    assert payload["delivered_files"] == []
    assert all(a.get("path") != "miro-research.md" for a in payload["artifacts"])
    assert any(g.get("reason") == REASON_PATH_MISMATCH for g in payload["gaps"])
    assert all(
        g.get("severity") != "warning"
        for g in payload["gaps"]
        if g.get("reason") == REASON_PATH_MISMATCH
    )


def test_artifact_dir_path_mismatch_from_warnings_alone_is_blocking():
    """未预盖 severity 的契约文案经 marker 升为 path_mismatch blocking。"""
    plan = _plan(
        RunSpec(
            run_id="w1",
            task="调研",
            role="研究员",
            deliverable=Deliverable(form="files", artifact_dir="docs/research"),
        )
    )
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["notes.md"],
            file_acceptance=_accepted("notes.md"),
            warnings=[
                "产物未写入约定文档目录 `docs/research/`（建议落在此目录下，勿写到工作区根）"
            ],
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-adir-warn")
    assert payload is not None
    assert payload["state"] == "partial"
    assert payload["delivered_files"] == []
    mismatch = [g for g in payload["gaps"] if g.get("reason") == REASON_PATH_MISMATCH]
    assert mismatch
    assert all(g.get("severity") != "warning" for g in mismatch)


def test_partial_writing_cutoff_summary_without_continue_writing():
    plan = _plan(RunSpec(run_id="w1", task="写成篇", role="撰稿人"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            files_touched=["报告.md"],
            file_acceptance=_accepted("报告.md"),
            delivery_gaps=[
                {
                    "description": "队员因 token 预算触顶被迫收口，产出可能不完整",
                    "reason": "token_budget",
                }
            ],
            warnings=["含示例/虚构自注（1 处）：`报告.md` · 示例数据 · 「待补」。"],
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-mix")
    assert payload is not None
    assert payload["state"] == "partial"
    assert "成篇未写完" in payload["summary"]
    assert "待核实备注" in payload["summary"]
    assert "continue_writing" not in {a.get("kind") for a in payload.get("actions") or []}


def test_no_bind_action_on_local_backend():
    plan = _plan(RunSpec(run_id="w1", task="运行脚本生成 course.pptx", role="工程师"))
    results = {"w1": RunState(phase=RunPhase.FAILED, error="超时")}
    payload = build_delivery_status(plan, results, execution_id="e4", backend=LocalBackend())
    assert payload is not None
    assert payload["state"] == "blocked"
    assert payload["actions"] == []
    assert "失败" in payload["gaps"][0]["description"]


def test_failed_skipped_cancelled_nodes_become_gaps():
    plan = _plan(
        RunSpec(run_id="a", task="t", role="A"),
        RunSpec(run_id="b", task="t", role="B"),
        RunSpec(run_id="c", task="t", role="C"),
    )
    results = {
        "a": RunState(phase=RunPhase.FAILED, error="炸了"),
        "b": RunState(phase=RunPhase.SKIPPED),
        "c": RunState(phase=RunPhase.CANCELLED),
    }
    payload = build_delivery_status(plan, results, execution_id="e5")
    assert payload is not None
    by_role = {g["role"]: g["description"] for g in payload["gaps"]}
    assert "失败：炸了" in by_role["A"]
    assert "未执行" in by_role["B"]
    assert "取消" in by_role["C"]


def test_cancelled_node_with_completed_revision_is_not_a_gap():
    # 跑一半改方向：原 run 取消但热修修订完成 → 不算缺口；修订产物计入已交付。
    plan = _plan(RunSpec(run_id="w1", task="写页面", role="前端"))
    results = {
        "w1": RunState(phase=RunPhase.CANCELLED),
        "w1_rev1": RunState(
            phase=RunPhase.COMPLETED,
            content="重写完成",
            files_touched=["index.html"],
            file_acceptance=_accepted("index.html"),
        ),
    }
    payload = build_delivery_status(plan, results, execution_id="e6")
    assert payload is not None
    assert payload["state"] == "delivered"
    assert payload["gaps"] == []
    assert payload["delivered_files"] == ["index.html"]


def test_maybe_emit_gates_and_emits():
    sink = EventSink()
    prose_plan = _plan(RunSpec(run_id="w1", task="调研", role="研究员"))
    maybe_emit_delivery_status(
        sink,
        prose_plan,
        {"w1": RunState(phase=RunPhase.COMPLETED, content="正文")},
        execution_id="e",
    )
    assert not any(e.type is EventType.DELIVERY_STATUS for e in sink._history)

    files_plan = _plan(RunSpec(run_id="w1", task="写文件", role="工程师"))
    maybe_emit_delivery_status(
        sink,
        files_plan,
        {
            "w1": RunState(
                phase=RunPhase.COMPLETED,
                content="ok",
                files_touched=["a.md"],
                file_acceptance=_accepted("a.md"),
            )
        },
        execution_id="e7",
    )
    events = [e for e in sink._history if e.type is EventType.DELIVERY_STATUS]
    assert len(events) == 1
    assert events[0].payload["execution_id"] == "e7"
    assert events[0].payload["state"] == "delivered"


def test_maybe_emit_same_execution_same_conclusion_once(monkeypatch):
    """finalize 幂等：同一 sink + execution + 结论只发一次事件、只打一次 emitted 日志。"""
    from agentcore.runtime.delegate import delivery_status as mod
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(mod, "logger", spy)
    sink = EventSink()
    plan = _plan(RunSpec(run_id="w1", task="写文件", role="工程师"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["a.md"],
            file_acceptance=_accepted("a.md"),
        )
    }
    maybe_emit_delivery_status(sink, plan, results, execution_id="e-idem")
    maybe_emit_delivery_status(sink, plan, results, execution_id="e-idem")
    events = [e for e in sink._history if e.type is EventType.DELIVERY_STATUS]
    assert len(events) == 1
    emitted = [name for name, _ in spy.events if name == "delegate.delivery_status_emitted"]
    assert len(emitted) == 1


def test_maybe_emit_same_execution_new_conclusion_reemits():
    """结论变了（补跑覆盖）仍发第二条。"""
    sink = EventSink()
    plan = _plan(RunSpec(run_id="w1", task="写文件", role="工程师"))
    first = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["a.md"],
            file_acceptance=_accepted("a.md"),
        )
    }
    second = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["a.md", "b.md"],
            file_acceptance=_accepted("a.md", "b.md"),
        )
    }
    maybe_emit_delivery_status(sink, plan, first, execution_id="e-idem-2")
    maybe_emit_delivery_status(sink, plan, second, execution_id="e-idem-2")
    events = [e for e in sink._history if e.type is EventType.DELIVERY_STATUS]
    assert len(events) == 2
    assert events[1].payload["delivered_files"] == ["a.md", "b.md"]


def test_maybe_emit_logs_empty_gate_counts(monkeypatch):
    """无物质静默仍打诊断日志：三个闸条件各自为 0，巡检可证「为何没出卡」。"""
    from agentcore.runtime.delegate import delivery_status as mod
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(mod, "logger", spy)
    sink = EventSink()
    plan = _plan(RunSpec(run_id="w1", task="调研", role="研究员"))
    maybe_emit_delivery_status(
        sink,
        plan,
        {"w1": RunState(phase=RunPhase.COMPLETED, content="综述正文")},
        execution_id="e-empty-obs",
    )
    assert not any(e.type is EventType.DELIVERY_STATUS for e in sink._history)
    fields = spy.get("delegate.delivery_status_empty")
    assert fields["execution_id"] == "e-empty-obs"
    assert fields["delivered_count"] == 0
    assert fields["gaps_count"] == 0
    assert fields["rejected_count"] == 0
    assert not any(name == "delegate.delivery_status_emitted" for name, _ in spy.events)


def test_gaps_cap_emits_context_capped(monkeypatch):
    """缺口列表超过 _MAX_GAPS 时切掉并打 context_capped（只落条数）。"""
    from agentcore.runtime import context_cap
    from agentcore.runtime.delegate.delivery_status import _MAX_GAPS
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(context_cap, "logger", spy)
    plan = _plan(RunSpec(run_id="w1", task="写文件", role="工程师"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["a.md"],
            file_acceptance=_accepted("a.md"),
        )
    }
    raw_n = _MAX_GAPS + 8
    payload = build_delivery_status(
        plan,
        results,
        execution_id="e-gaps-cap",
        criteria_gaps=[f"验收缺口{i}" for i in range(raw_n)],
    )
    assert payload is not None
    assert len(payload["gaps"]) == _MAX_GAPS
    fields = spy.get("delegate.context_capped")
    assert fields["site"] == "delivery_gaps"
    assert fields["original_count"] >= raw_n
    assert fields["final_count"] == _MAX_GAPS
    assert fields["execution_id"] == "e-gaps-cap"


def test_maybe_emit_logs_emitted_counts(monkeypatch):
    """有物质发射时打成功日志，载荷带 artifacts/accepted/rejected/gaps 数量。"""
    from agentcore.runtime.delegate import delivery_status as mod
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(mod, "logger", spy)
    sink = EventSink()
    plan = _plan(RunSpec(run_id="w1", task="写文件", role="工程师"))
    maybe_emit_delivery_status(
        sink,
        plan,
        {
            "w1": RunState(
                phase=RunPhase.COMPLETED,
                content="ok",
                files_touched=["a.md", "b.md"],
                file_acceptance=_accepted("a.md", "b.md"),
            )
        },
        execution_id="e-emit-obs",
    )
    events = [e for e in sink._history if e.type is EventType.DELIVERY_STATUS]
    assert len(events) == 1
    fields = spy.get("delegate.delivery_status_emitted")
    assert fields["execution_id"] == "e-emit-obs"
    assert fields["state"] == "delivered"
    assert fields["artifacts_count"] == 2
    assert fields["accepted_count"] == 2
    assert fields["rejected_count"] == 0
    assert fields["gaps_count"] == 0
    assert not any(name == "delegate.delivery_status_empty" for name, _ in spy.events)


def test_qa_deferred_budget_does_not_emit_website_verify_action():
    from agentcore.runtime.runs.types import Deliverable

    plan = _plan(
        RunSpec(
            run_id="qa",
            role="页面 QA",
            task="独立【整页验收】站点【GEO 官网】…",
            deliverable=Deliverable(
                form="files",
                artifacts=["site/QA.md"],
                visual_critic=True,
            ),
        )
    )
    results = {
        "qa": RunState(
            phase=RunPhase.SKIPPED,
            delivery_gaps=[
                {
                    "description": "整页验收波未跑（本回合预算用尽）",
                    "reason": "qa_deferred_budget",
                }
            ],
            files_touched=[],
        )
    }
    # Need a delivered file elsewhere so state is partial (or gaps alone → blocked).
    plan.nodes.insert(
        0,
        RunSpec(run_id="s0", role="区0", task="分区"),
    )
    results["s0"] = RunState(
        phase=RunPhase.COMPLETED,
        files_touched=["site/index.html"],
        file_acceptance=_accepted("site/index.html"),
    )
    payload = build_delivery_status(plan, results, execution_id="e-qa")
    assert payload is not None
    assert payload["state"] == "partial"
    assert not any(a.get("kind") == "website_verify" for a in payload["actions"])
    assert all("build_website_verify" not in str(a.get("prompt") or "") for a in payload["actions"])


@pytest.mark.asyncio
async def test_execute_ignores_retired_completion_criteria_kind():
    # S3：completion_criteria kind 已删；误传字段被忽略，不再走 criteria_unmet 硬路径。
    sink = EventSink()
    t = DelegateTool(
        llm=Provider(["X"]),
        sink=sink,
        system_prompt="SYS",
        user_message="原始请求",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=local_ctx(),
        permission_axes=recipe_to_axes(AutonomyPolicy.MANAGED),
        folder_id="test_birth",
        approval_gate=None,
    )
    result = await t.execute(
        {
            "tasks": [{"role": "工程师", "task": "修好构建脚本"}],
            "completion_criteria": {
                "type": "code_verified",
                "verify_command": "pytest -q",
            },
            "complexity_hint": "standard",
            "coordinate": False,
        },
        local_ctx(),
    )
    assert result.success is True
    assert "完成条件未满足" not in (result.output or "")
    assert result.metadata is None or not result.metadata.get("criteria_unmet")


def _failed_browser_transcript():
    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction

    return [
        LLMMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="nav1",
                    type="function",
                    function=ToolCallFunction(
                        name="browser_navigate",
                        arguments='{"url":"https://example.com"}',
                    ),
                )
            ],
        ),
        LLMMessage(
            role="tool",
            content="浏览器操作失败：连接超时\n<!--agentcore:tool_failed-->",
            tool_call_id="nav1",
        ),
    ]


def _failed_test_run_transcript():
    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction

    return [
        LLMMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="tr1",
                    type="function",
                    function=ToolCallFunction(name="test_run", arguments="{}"),
                )
            ],
        ),
        LLMMessage(
            role="tool",
            content="测试未通过（退出码 1）\n- 通过：0 / 失败：2 / 错误：0\n<!--agentcore:tool_failed-->",
            tool_call_id="tr1",
        ),
    ]


def _budget_exhausted_test_run_transcript():
    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction

    return [
        LLMMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="trb1",
                    type="function",
                    function=ToolCallFunction(
                        name="test_run",
                        arguments='{"check":"test","scope":"all"}',
                    ),
                )
            ],
        ),
        LLMMessage(
            role="tool",
            content=(
                "## 验证结果：未完成（预算耗尽）\n"
                "- 说明：验证未在 300s 预算内完成；这是验证未完成，不是执行工具故障。\n"
                "验证未在 300s 预算内完成（验证未完成，非工具故障）\n"
                "<!--agentcore:tool_failed-->"
            ),
            tool_call_id="trb1",
        ),
    ]


def _failed_verify_tsc_transcript():
    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction

    return [
        LLMMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="tc1",
                    type="function",
                    function=ToolCallFunction(
                        name="code_execute",
                        arguments='{"code":"npx tsc -b","language":"bash"}',
                    ),
                )
            ],
        ),
        LLMMessage(
            role="tool",
            content="stdout:\nerror TS2304\n\n退出码：1\n<!--agentcore:tool_failed-->",
            tool_call_id="tc1",
        ),
    ]


def _failed_browser_unified_transcript():
    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction

    return [
        LLMMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="nav2",
                    type="function",
                    function=ToolCallFunction(
                        name="browser",
                        arguments='{"action":"navigate","url":"https://example.com"}',
                    ),
                )
            ],
        ),
        LLMMessage(
            role="tool",
            content="浏览器操作失败：连接超时\n<!--agentcore:tool_failed-->",
            tool_call_id="nav2",
        ),
    ]


def test_verify_failed_browser_navigate_depresses_delivered():
    """丙：COMPLETED + browser_navigate 失败 → verify_failed，不得 delivered。"""
    plan = _plan(RunSpec(run_id="w1", task="打开验收", role="质检"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="已尝试打开",
            files_touched=["site/index.html"],
            file_acceptance=_accepted("site/index.html"),
            transcript=_failed_browser_transcript(),
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-vf-nav")
    assert payload is not None
    assert payload["state"] == "partial"
    assert any(g.get("reason") == "verify_failed" for g in payload["gaps"])
    assert any("未成功打开目标页" in g["description"] for g in payload["gaps"])


def test_verify_failed_browser_action_navigate_depresses_delivered():
    """丙：COMPLETED + browser(action=navigate) 失败 → verify_failed。"""
    plan = _plan(RunSpec(run_id="w1", task="打开验收", role="质检"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="已尝试打开",
            files_touched=["site/index.html"],
            file_acceptance=_accepted("site/index.html"),
            transcript=_failed_browser_unified_transcript(),
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-vf-nav-new")
    assert payload is not None
    assert payload["state"] == "partial"
    assert any(g.get("reason") == "verify_failed" for g in payload["gaps"])
    assert any("未成功打开目标页" in g["description"] for g in payload["gaps"])


def test_verify_failed_test_run_depresses_delivered():
    plan = _plan(RunSpec(run_id="w1", task="跑测", role="工程师"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="测完",
            files_touched=["src/a.ts"],
            file_acceptance=_accepted("src/a.ts"),
            transcript=_failed_test_run_transcript(),
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-vf-test")
    assert payload is not None
    assert payload["state"] != "delivered"
    assert any(g.get("reason") == "verify_failed" for g in payload["gaps"])


def test_verify_budget_exhausted_gap_not_still_running():
    """预算耗尽 → verify_budget 缺口；文案明示已中止、非仍在跑。"""
    from agentcore.runtime.closing_posture import (
        clear_verify_budget_exhausted,
        closing_honesty_rework,
        note_verify_budget_from_delivery,
        turn_has_verify_budget_exhausted,
    )
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    clear_verify_budget_exhausted()
    plan = _plan(RunSpec(run_id="w1", task="跑测", role="验证员"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="还在等验证",
            files_touched=["src/a.ts"],
            file_acceptance=_accepted("src/a.ts"),
            transcript=_budget_exhausted_test_run_transcript(),
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-vf-budget")
    assert payload is not None
    assert payload["state"] != "delivered"
    budget_gaps = [g for g in payload["gaps"] if g.get("reason") == "verify_budget"]
    assert budget_gaps
    desc = budget_gaps[0]["description"]
    assert "验证未完成" in desc
    assert "非仍在跑" in desc or "已中止" in desc
    assert "预算耗尽" in desc or "无响应" in desc or "强制中止" in desc
    assert "仍在进行" not in desc
    assert not any(
        g.get("reason") == "verify_failed" and "测试未通过" in g.get("description", "")
        for g in payload["gaps"]
    )

    note_verify_budget_from_delivery(payload["gaps"])
    assert turn_has_verify_budget_exhausted()
    rework = closing_honesty_rework(
        "验证员仍在进行，请继续等待结果。",
        DeliveryVerdict(
            state="partial",
            delivered_files=("src/a.ts",),
            execution_id="e-vf-budget",
            requires_draft_ack=True,
        ),
    )
    assert rework is not None
    assert "仍在进行" in rework or "强制中止" in rework or "无响应" in rework
    clear_verify_budget_exhausted()


def test_verify_failed_tsc_depresses_delivered():
    plan = _plan(RunSpec(run_id="w1", task="类型检查", role="工程师"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="tsc 过了？",
            files_touched=["src/a.ts"],
            file_acceptance=_accepted("src/a.ts"),
            transcript=_failed_verify_tsc_transcript(),
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-vf-tsc")
    assert payload is not None
    assert payload["state"] != "delivered"
    assert any(g.get("reason") == "verify_failed" for g in payload["gaps"])


def test_landed_files_without_verify_failure_still_delivered():
    """无验证失败且仅落盘 → 仍可为 delivered。"""
    plan = _plan(RunSpec(run_id="w1", task="写讲稿", role="撰写"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="已落盘",
            files_touched=["讲稿.md"],
            file_acceptance=_accepted("讲稿.md"),
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-ok")
    assert payload is not None
    assert payload["state"] == "delivered"
    assert payload["gaps"] == []


def test_cloud_delivered_adds_export_to_local():
    """云端 backend + delivered_files → 含 export_to_local（即使 state=delivered）。"""
    plan = _plan(RunSpec(run_id="w1", task="写 SPA", role="前端"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="已落盘",
            files_touched=["app/package.json", "app/src/main.ts"],
            file_acceptance=_accepted("app/package.json", "app/src/main.ts"),
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-export", backend=ctx().backend)
    assert payload is not None
    assert payload["state"] == "delivered"
    kinds = [a["kind"] for a in payload["actions"]]
    assert "export_to_local" in kinds
    action = next(a for a in payload["actions"] if a["kind"] == "export_to_local")
    assert "云端" in action["description"]
    assert "npm" in action["description"]


def test_local_delivered_omits_export_to_local():
    plan = _plan(RunSpec(run_id="w1", task="写 SPA", role="前端"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="已落盘",
            files_touched=["app/package.json"],
            file_acceptance=_accepted("app/package.json"),
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-local", backend=LocalBackend())
    assert payload is not None
    assert payload["state"] == "delivered"
    assert "export_to_local" not in {a["kind"] for a in payload["actions"]}


def test_is_availability_status_question_narrow():
    from agentcore.runtime.delegate.delivery_status import is_availability_status_question

    assert is_availability_status_question("可以使用了吗")
    assert is_availability_status_question("能不能用")
    assert is_availability_status_question("好了吗")
    assert is_availability_status_question("完成了吗？")
    assert not is_availability_status_question("请继续补全质检面板并接好 API")
    assert not is_availability_status_question(
        "刚才做好的那个页面，你能在本地直接打开浏览器帮我验证一下能不能用吗？"
    )


def test_cite_failure_path_rejected_not_in_delivered_files():
    """Soft-COMPLETED + cite-tier path reject → artifacts rejected, not delivered_files."""
    from agentcore.runtime.runs.file_acceptance import (
        REASON_CITATIONS_UNVERIFIED,
        build_file_acceptance,
        path_rejections_from_contract_messages,
    )

    cite_msg = (
        "`paper.md`：正文出现学位论文/期刊式著录标记（[D]）但未就地绑定本回合台账 #rN——"
        "属于未核验或编造引用。"
    )
    path_rej = path_rejections_from_contract_messages([cite_msg])
    acceptance = build_file_acceptance(
        ["paper.md", "outline.md"],
        phase=RunPhase.COMPLETED,
        path_rejections=path_rej,
    )
    plan = _plan(RunSpec(run_id="w1", task="写综述", role="撰写"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="已落盘",
            files_touched=["paper.md", "outline.md"],
            file_acceptance=acceptance,
            warnings=[cite_msg],
            delivery_gaps=[{"description": cite_msg, "reason": REASON_CITATIONS_UNVERIFIED}],
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-cite")
    assert payload is not None
    assert payload["delivered_files"] == ["outline.md"]
    by_path = {a["path"]: a for a in payload["artifacts"]}
    assert by_path["paper.md"]["status"] == "rejected"
    assert by_path["paper.md"]["reason"] == REASON_CITATIONS_UNVERIFIED
    assert by_path["outline.md"]["status"] == "accepted"
    assert payload["state"] == "partial"


def test_failed_with_landed_files_rejected_in_artifacts():
    """FAILED + 正式 rejected 戳 → 产物在 artifacts（非 delivered_files）；有落盘 → partial。"""
    from agentcore.runtime.runs.file_acceptance import build_file_acceptance

    err = "`site/index.html`：交付正文含未替换占位符/硬信号"
    acceptance = build_file_acceptance(
        ["site/index.html", "site/style.css"],
        phase=RunPhase.FAILED,
        error=err,
    )
    plan = _plan(RunSpec(run_id="w1", task="建站", role="前端"))
    results = {
        "w1": RunState(
            phase=RunPhase.FAILED,
            content="半成品",
            error=err,
            files_touched=["site/index.html", "site/style.css"],
            file_acceptance=acceptance,
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-fail-land")
    assert payload is not None
    assert payload["delivered_files"] == []
    assert payload["state"] == "partial"
    assert {a["path"] for a in payload["artifacts"]} == {
        "site/index.html",
        "site/style.css",
    }
    assert all(a["status"] == "rejected" for a in payload["artifacts"])


def test_priced_failure_landings_are_partial_not_blocked():
    """异常构造点盖上的落盘账：workspace_native + 成功写入 + 队员失败 → partial。"""
    from agentcore.llm.provider.protocol import LLMMessage, TokenUsage
    from agentcore.runtime.runs.executor.shared import _priced_failure
    from agentcore.runtime.runs.types import Deliverable
    from agentcore.tools.file_products import file_product, with_file_products_marker

    paths = ["收入.csv", "支出.csv", "汇总.csv"]
    transcript = [
        LLMMessage(
            role="tool",
            tool_call_id="w1",
            content=with_file_products_marker("已写入", [file_product(p) for p in paths]),
        )
    ]
    failed = _priced_failure(
        "上游限流，暂时无法继续本回合。",
        model="m",
        usage=TokenUsage(),
        rounds=2,
        duration_ms=10,
        transcript=transcript,
    )
    plan = _plan(
        RunSpec(
            run_id="w1",
            task="整理成 CSV",
            role="数据整理员",
            deliverable=Deliverable(form="files", workspace_native=True),
        )
    )
    payload = build_delivery_status(plan, {"w1": failed}, execution_id="e-priced-fail")
    assert payload is not None
    assert payload["state"] == "partial"
    assert payload["delivered_files"] == paths
    assert [a["path"] for a in payload["artifacts"]] == paths
    assert all(a["status"] == "accepted" for a in payload["artifacts"])
    assert any(g.get("reason") == "node_failed" for g in payload["gaps"])


def test_failed_undeclared_transcript_landings_count_as_partial():
    """失败前 file_write 已 ok、未盖 file_acceptance → 计入交付账，state=partial。"""
    from agentcore.llm.provider.protocol import LLMMessage
    from agentcore.tools.file_products import file_product, with_file_products_marker

    paths = ["sales.csv", "inventory.csv", "customers.csv"]
    transcript = [
        LLMMessage(
            role="tool",
            tool_call_id="c1",
            content=with_file_products_marker("已写入", [file_product(p) for p in paths]),
        )
    ]
    plan = _plan(RunSpec(run_id="w1", task="导出三表", role="分析"))
    results = {
        "w1": RunState(
            phase=RunPhase.FAILED,
            error="LLM hung after writes",
            content="半成品",
            files_touched=[],
            file_acceptance=[],
            transcript=transcript,
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-fail-tx")
    assert payload is not None
    assert payload["state"] == "partial"
    assert payload["delivered_files"] == paths
    assert [a["path"] for a in payload["artifacts"]] == paths
    assert all(a["status"] == "accepted" for a in payload["artifacts"])
    assert any(g.get("reason") == "node_failed" for g in payload["gaps"])


def test_failed_undeclared_files_touched_count_as_partial():
    """FAILED + files_touched 无戳 → 同样计入交付账。"""
    paths = ["a.csv", "b.csv", "c.csv"]
    plan = _plan(RunSpec(run_id="w1", task="导出表", role="分析"))
    results = {
        "w1": RunState(
            phase=RunPhase.FAILED,
            error="boom",
            content="半成品",
            files_touched=paths,
            file_acceptance=[],
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-fail-ft")
    assert payload is not None
    assert payload["state"] == "partial"
    assert payload["delivered_files"] == paths
    assert [a["path"] for a in payload["artifacts"]] == paths


def test_clean_completed_artifacts_all_accepted():
    plan = _plan(RunSpec(run_id="w1", task="写讲稿", role="撰写"))
    from agentcore.runtime.runs.file_acceptance import build_file_acceptance

    touched = ["讲稿.md"]
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=touched,
            file_acceptance=build_file_acceptance(touched, phase=RunPhase.COMPLETED),
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-ok-acc")
    assert payload is not None
    assert payload["delivered_files"] == ["讲稿.md"]
    assert payload["artifacts"] == [{"path": "讲稿.md", "status": "accepted"}]


def test_artifacts_carry_self_reported_kind_and_derived_from():
    """导出件上线 wire：md + 派生 docx 都进 artifacts，行内带自报 kind / derived_from。

    事故面：产物卡只认 ``artifacts``，导出的 .docx 不在表里就等于不存在（用户看到 md
    判 AI 吹牛）。派生关系也必须随行走，客户端才能把源 md 折成中间稿。
    """
    from agentcore.tools.file_products import FileProduct

    md = "抚养费起诉状-昝雯.md"
    docx = "抚养费起诉状-昝雯.docx"
    acceptance = build_file_acceptance(
        [md, docx],
        phase=RunPhase.COMPLETED,
        products=[
            FileProduct(path=md, kind="md"),
            FileProduct(path=docx, kind="docx", derived_from=md),
        ],
    )
    plan = _plan(RunSpec(run_id="w1", task="起草并导出 Word", role="文书撰写"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="Word 已生成",
            files_touched=[md, docx],
            file_acceptance=acceptance,
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-export")
    assert payload is not None
    assert payload["artifacts"] == [
        {"path": md, "status": "accepted", "kind": "md"},
        {"path": docx, "status": "accepted", "kind": "docx", "derived_from": md},
    ]
    # 折叠是客户端呈现层的事：两份都仍是已交付文件。
    assert payload["delivered_files"] == [md, docx]


def test_artifacts_omit_product_meta_when_not_self_reported():
    """没自报就不带字段——禁止按扩展名替工具补 kind / 猜派生关系。"""
    plan = _plan(RunSpec(run_id="w1", task="写讲稿", role="撰写"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["讲稿.md"],
            file_acceptance=_accepted("讲稿.md"),
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-no-meta")
    assert payload is not None
    assert payload["artifacts"] == [{"path": "讲稿.md", "status": "accepted"}]


def test_artifacts_workspace_id_from_target_folder_id():
    """delegate + target_folder_id + file_acceptance → artifacts[].workspace_id."""
    desk = "11111111-2222-3333-4444-555555555555"
    plan = _plan(
        RunSpec(
            run_id="w1",
            task="写到目标桌",
            role="撰写",
            target_folder_id=desk,
        )
    )
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="已落盘",
            files_touched=["out.md"],
            file_acceptance=_accepted("out.md"),
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-desk")
    assert payload is not None
    assert payload["artifacts"] == [
        {
            "path": "out.md",
            "status": "accepted",
            "workspace_id": f"folder:{desk}",
        }
    ]


def test_artifacts_omit_workspace_id_without_target_folder():
    """无 target_folder_id → 不带 workspace_id（客户端回退会话出生桌）。"""
    plan = _plan(RunSpec(run_id="w1", task="写讲稿", role="撰写"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="已落盘",
            files_touched=["讲稿.md"],
            file_acceptance=_accepted("讲稿.md"),
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-no-desk")
    assert payload is not None
    assert len(payload["artifacts"]) == 1
    assert "workspace_id" not in payload["artifacts"][0]


def test_phase_b_cite_fail_rejected_not_in_delivered_files():
    """阶段 B 引用不过闸 → rejected(citations_unverified)，不进 delivered_files；无 draft 行。"""
    from agentcore.runtime.runs.file_acceptance import (
        REASON_CITATIONS_UNVERIFIED,
        build_file_acceptance,
        path_rejections_from_contract_messages,
    )

    cite_msg = (
        "`AgentCore/文档/research/渠道.md`：正文用了 #r3 这些台账引用来源，但它们不在"
        "本回合成稿可引用集中（须 deep_read 或 selected；search-only / 伪造 / 越界均不可）。"
    )
    path_rej = path_rejections_from_contract_messages([cite_msg])
    acceptance = build_file_acceptance(
        ["AgentCore/文档/research/渠道.md"],
        phase=RunPhase.COMPLETED,
        path_rejections=path_rej,
    )
    # draft 不进 file_acceptance / artifacts（仅 accepted|rejected）
    assert all(a["status"] in ("accepted", "rejected") for a in acceptance)
    assert acceptance[0]["status"] == "rejected"
    assert acceptance[0]["reason"] == REASON_CITATIONS_UNVERIFIED

    plan = _plan(RunSpec(run_id="w1", task="调研渠道", role="调研员"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="草案已升 B 仍不过闸",
            files_touched=["AgentCore/文档/research/渠道.md"],
            file_acceptance=acceptance,
            warnings=[cite_msg],
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-phase-b")
    assert payload is not None
    assert payload["delivered_files"] == []
    assert len(payload["artifacts"]) == 1
    assert payload["artifacts"][0]["status"] == "rejected"
    assert payload["artifacts"][0]["reason"] == REASON_CITATIONS_UNVERIFIED
    assert all(a.get("status") != "draft" for a in payload["artifacts"])


def test_two_phase_predicate_and_playbook_stamp():
    from agentcore.runtime.runs.playbooks import PLAYBOOKS
    from agentcore.runtime.runs.research_quality import is_two_phase_citation_deliverable
    from agentcore.runtime.runs.types import Deliverable
    from agentcore.workspace.stage_dirs import RESEARCH_PREFIX

    assert is_two_phase_citation_deliverable(
        Deliverable(citation_mode="two_phase", form="files", artifacts=["a.md"])
    )
    assert not is_two_phase_citation_deliverable(Deliverable(form="files", artifacts=["a.md"]))
    assert not is_two_phase_citation_deliverable(None)
    # 路径入口已撤：约定文档落点由扫 role·task 的正则填出，不得当两阶段入口。
    from agentcore.workspace.stage_dirs import REVIEWS_PREFIX

    research_path = f"{RESEARCH_PREFIX}pricing_summary.md"
    reviews_path = f"{REVIEWS_PREFIX}legal_review.md"
    assert not is_two_phase_citation_deliverable(
        Deliverable(form="files", artifacts=[research_path])
    )
    assert not is_two_phase_citation_deliverable(
        Deliverable(form="files", artifacts=[reviews_path])
    )
    assert not is_two_phase_citation_deliverable(
        Deliverable(form="files", artifact_dir=RESEARCH_PREFIX)
    )
    # 显式盖戳仍进；省略退出
    assert is_two_phase_citation_deliverable(
        Deliverable(citation_mode="two_phase", form="files", artifacts=[research_path])
    )
    assert not is_two_phase_citation_deliverable(
        Deliverable(
            form="files",
            artifacts=[research_path],
        )
    )

    tasks, errs = PLAYBOOKS["cite_write_review"].build({"topic": "测试主题", "angles": ["甲", "乙"]})
    assert not errs
    research_tasks = [t for t in tasks if str(t.get("id", "")).startswith("research_")]
    assert research_tasks
    for t in research_tasks:
        assert t["deliverable"].get("citation_mode") == "two_phase"
    write = next(t for t in tasks if t.get("id") == "write")
    assert write["deliverable"].get("citation_mode") == "two_phase"

    ml_tasks, ml_errs = PLAYBOOKS["lens_crosscheck"].build({"topic": "测试事件"})
    assert not ml_errs
    for t in ml_tasks:
        assert t["deliverable"].get("citation_mode") == "two_phase"

    brief, brief_errs = PLAYBOOKS["map_fanout"].build(
        {"topic": "测试主题", "angles": ["甲", "乙"]}
    )
    assert not brief_errs
    for t in brief:
        assert t["deliverable"].get("citation_mode") in (None, "")


def _literature_report_plan() -> RunPlan:
    """Minimal cite_write_review-shaped plan: writer + review + two_phase main file."""
    from agentcore.runtime.runs.types import Deliverable
    from agentcore.workspace.stage_dirs import RESEARCH_PREFIX, REVIEWS_PREFIX

    main = f"{RESEARCH_PREFIX}报告.md"
    return _plan(
        RunSpec(
            run_id="write",
            task="成文",
            role="撰稿人",
            deliverable=Deliverable(
                form="files",
                artifacts=[main],
                citation_mode="two_phase",
            ),
        ),
        RunSpec(
            run_id="review",
            task="学术审校",
            role="学术审校员",
            depends_on=["write"],
            deliverable=Deliverable(
                form="files",
                artifacts=[f"{REVIEWS_PREFIX}审校报告.md"],
            ),
        ),
    )


def test_literature_evidence_deficit_depresses_delivered():
    """证据不足（几乎无学术源）→ cite_write_review 形对账不得 delivered。"""
    from agentcore.workspace.stage_dirs import RESEARCH_PREFIX

    main = f"{RESEARCH_PREFIX}报告.md"
    plan = _literature_report_plan()
    results = {
        "write": RunState(
            phase=RunPhase.COMPLETED,
            content="长文成稿",
            files_touched=[main],
            file_acceptance=_accepted(main),
            citations=[
                {"url": "https://baike.baidu.com/item/x", "title": "百科"},
                {"url": "https://www.iciba.com/word", "title": "词典"},
                {"url": "https://www.163.com/dy/article/a.html", "title": "门户"},
            ],
        ),
        "review": RunState(phase=RunPhase.COMPLETED, content="审校通过（形式）"),
    }
    payload = build_delivery_status(plan, results, execution_id="e-ev-def")
    assert payload is not None
    assert payload["state"] == "partial"
    assert payload["state"] != "delivered"
    assert any(g.get("reason") == "evidence_deficit" for g in payload["gaps"])
    assert any("证据不足" in g["description"] for g in payload["gaps"])


def test_literature_evidence_deficit_from_prior_knowledge_marker():
    """审校标明无参考文献 / 靠先验 → blocking evidence_deficit。"""
    from agentcore.workspace.stage_dirs import RESEARCH_PREFIX

    main = f"{RESEARCH_PREFIX}报告.md"
    plan = _literature_report_plan()
    results = {
        "write": RunState(
            phase=RunPhase.COMPLETED,
            content="基于对该领域的了解整理成文",
            files_touched=[main],
            file_acceptance=_accepted(main),
            citations=[{"url": "https://arxiv.org/abs/2301.00001", "title": "paper"}],
        ),
        "review": RunState(
            phase=RunPhase.COMPLETED,
            content="主要问题：无参考文献表；对比表缺引用。",
        ),
    }
    payload = build_delivery_status(plan, results, execution_id="e-ev-prior")
    assert payload is not None
    assert payload["state"] != "delivered"
    assert any(g.get("reason") == "evidence_deficit" for g in payload["gaps"])


def test_literature_evidence_deficit_from_search_seam_signal():
    """学术搜索块接缝：RunState.evidence_meta.evidence_gap + academic_literature → 降档。"""
    from agentcore.workspace.stage_dirs import RESEARCH_PREFIX

    main = f"{RESEARCH_PREFIX}报告.md"
    plan = _literature_report_plan()
    writer = RunState(
        phase=RunPhase.COMPLETED,
        content="成稿",
        files_touched=[main],
        file_acceptance=_accepted(main),
        citations=[{"url": "https://arxiv.org/abs/2301.00001", "title": "ok"}],
    )
    # Search true source: evidence_gap + academic_literature (executor may also
    # copy RetrievalBudgetState sticky onto state.evidence_gap / evidence_meta).
    writer.evidence_meta = {
        "evidence_gap": True,
        "search_policy": "academic_literature",
    }
    results = {
        "write": writer,
        "review": RunState(phase=RunPhase.COMPLETED, content="形式审校"),
    }
    payload = build_delivery_status(plan, results, execution_id="e-ev-seam")
    assert payload is not None
    assert payload["state"] == "partial"
    assert any(g.get("reason") == "evidence_deficit" for g in payload["gaps"])


def test_literature_evidence_deficit_from_legacy_evidence_deficit_stamp():
    """兼容：旧 evidence_deficit 戳仍可触发降档。"""
    from agentcore.workspace.stage_dirs import RESEARCH_PREFIX

    main = f"{RESEARCH_PREFIX}报告.md"
    plan = _literature_report_plan()
    writer = RunState(
        phase=RunPhase.COMPLETED,
        content="成稿",
        files_touched=[main],
        file_acceptance=_accepted(main),
        citations=[{"url": "https://arxiv.org/abs/2301.00001", "title": "ok"}],
    )
    writer.evidence_meta = {"evidence_deficit": True, "evidence_quality": "poor"}
    results = {
        "write": writer,
        "review": RunState(phase=RunPhase.COMPLETED, content="形式审校"),
    }
    payload = build_delivery_status(plan, results, execution_id="e-ev-seam-legacy")
    assert payload is not None
    assert payload["state"] == "partial"
    assert any(g.get("reason") == "evidence_deficit" for g in payload["gaps"])


def test_literature_worker_delivery_gap_evidence_deficit_depresses():
    """Worker 已 stamp delivery_gaps.reason=evidence_deficit → 经 collect_worker_gaps 降档。"""
    from agentcore.workspace.stage_dirs import RESEARCH_PREFIX

    main = f"{RESEARCH_PREFIX}报告.md"
    plan = _literature_report_plan()
    results = {
        "write": RunState(
            phase=RunPhase.COMPLETED,
            content="成稿",
            files_touched=[main],
            file_acceptance=_accepted(main),
            citations=[{"url": "https://arxiv.org/abs/2301.00001", "title": "ok"}],
            delivery_gaps=[
                {
                    "description": "学术检索 junk 过高",
                    "reason": "evidence_deficit",
                }
            ],
        ),
        "review": RunState(phase=RunPhase.COMPLETED, content="形式审校"),
    }
    payload = build_delivery_status(plan, results, execution_id="e-ev-gap")
    assert payload is not None
    assert payload["state"] == "partial"
    assert any(g.get("reason") == "evidence_deficit" for g in payload["gaps"])
    # 不因接缝谓词再叠一条重复的验收缺口
    ev_gaps = [g for g in payload["gaps"] if g.get("reason") == "evidence_deficit"]
    assert len(ev_gaps) == 1


def test_literature_adequate_evidence_stays_delivered():
    """学术源充足且无先验缺口 → 不误伤，仍可为 delivered。"""
    from agentcore.workspace.stage_dirs import RESEARCH_PREFIX, REVIEWS_PREFIX

    main = f"{RESEARCH_PREFIX}报告.md"
    review_path = f"{REVIEWS_PREFIX}审校报告.md"
    plan = _literature_report_plan()
    results = {
        "write": RunState(
            phase=RunPhase.COMPLETED,
            content="据 arXiv / PubMed 文献综述。",
            files_touched=[main],
            file_acceptance=_accepted(main),
            citations=[
                {"url": "https://arxiv.org/abs/2301.00001", "title": "A"},
                {"url": "https://pubmed.ncbi.nlm.nih.gov/12345/", "title": "B"},
                {"url": "https://doi.org/10.1000/xyz", "title": "C"},
            ],
        ),
        "review": RunState(
            phase=RunPhase.COMPLETED,
            content="引用规范可接受",
            files_touched=[review_path],
            file_acceptance=_accepted(review_path),
        ),
    }
    payload = build_delivery_status(plan, results, execution_id="e-ev-ok")
    assert payload is not None
    assert payload["state"] == "delivered"
    assert all(g.get("reason") != "evidence_deficit" for g in payload["gaps"])
    assert all(g.get("reason") != "thin_review" for g in payload["gaps"])


def test_map_fanout_junk_citations_not_evidence_deficit():
    """map_fanout 默认不套证据降档（即使 citation 全是水站）。"""
    from agentcore.runtime.runs.types import Deliverable
    from agentcore.workspace.stage_dirs import RESEARCH_PREFIX

    note = f"{RESEARCH_PREFIX}方向笔记.md"
    plan = _plan(
        RunSpec(
            run_id="brief_0",
            task="摸底",
            role="方向专员",
            deliverable=Deliverable(
                form="files",
                artifacts=[note],
                citation_mode="two_phase",
            ),
        ),
        RunSpec(
            run_id="brief_1",
            task="摸底2",
            role="方向专员",
        ),
    )
    results = {
        "brief_0": RunState(
            phase=RunPhase.COMPLETED,
            content="方向笔记",
            files_touched=[note],
            file_acceptance=_accepted(note),
            citations=[
                {"url": "https://baike.baidu.com/item/x", "title": "百科"},
                {"url": "https://www.iciba.com/word", "title": "词典"},
            ],
        ),
        "brief_1": RunState(phase=RunPhase.COMPLETED, content="另一方向"),
    }
    payload = build_delivery_status(plan, results, execution_id="e-brief-ok")
    assert payload is not None
    assert payload["state"] == "delivered"
    assert all(g.get("reason") != "evidence_deficit" for g in payload["gaps"])


def test_non_literature_landed_files_unaffected():
    """普通落盘批次不受文献证据闸影响。"""
    plan = _plan(RunSpec(run_id="w1", task="写讲稿", role="撰写"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="讲稿",
            files_touched=["讲稿.md"],
            file_acceptance=_accepted("讲稿.md"),
            citations=[
                {"url": "https://baike.baidu.com/item/x", "title": "百科"},
                {"url": "https://www.iciba.com/word", "title": "词典"},
            ],
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-plain")
    assert payload is not None
    assert payload["state"] == "delivered"
    assert all(g.get("reason") != "evidence_deficit" for g in payload["gaps"])


def _thin_review_plan(report_path: str):
    """Declared reviews/ files contract on the independent review node (no role scan)."""
    from agentcore.runtime.runs.types import Deliverable

    return _plan(
        RunSpec(
            run_id="fix",
            task="修 bug",
            role="修复工程师",
            deliverable=Deliverable(form="files", artifacts=["src/a.ts"]),
        ),
        RunSpec(
            run_id="review",
            task="独立复核并落盘短报告",
            role="独立复核员",
            depends_on=["fix"],
            deliverable=Deliverable(
                form="files",
                artifacts=[report_path],
            ),
        ),
    )


def test_thin_review_missing_report_partial_and_draft_ack():
    """案 A′：已声明 reviews/ 无合格报告 → partial + thin_review + requires_draft_ack。"""
    from agentcore.runtime.delegate.delivery_status import current_delivery_verdict
    from agentcore.workspace.stage_dirs import REVIEWS_PREFIX

    report = f"{REVIEWS_PREFIX}M1-复核报告.md"
    plan = _thin_review_plan(report)
    results = {
        "fix": RunState(
            phase=RunPhase.COMPLETED,
            content="已修",
            files_touched=["src/a.ts"],
            file_acceptance=_accepted("src/a.ts"),
        ),
        # COMPLETED + 短 handoff + 写了别的路径，声明复核报告未 accepted。
        "review": RunState(
            phase=RunPhase.COMPLETED,
            content="通过",
            debrief={"summary": "全链路通过"},
            files_touched=["docs/wrong-path.md"],
            file_acceptance=_accepted("docs/wrong-path.md"),
        ),
    }
    current_delivery_verdict.set(None)
    payload = build_delivery_status(plan, results, execution_id="e-thin-miss")
    assert payload is not None
    assert payload["state"] == "partial"
    assert any(g.get("reason") == "thin_review" for g in payload["gaps"])
    assert any("复核落盘契约" in (g.get("description") or "") for g in payload["gaps"])

    sink = EventSink()
    maybe_emit_delivery_status(sink, plan, results, execution_id="e-thin-miss")
    verdict = current_delivery_verdict.get()
    assert verdict is not None
    assert verdict.state == "partial"
    assert verdict.requires_draft_ack is True
    current_delivery_verdict.set(None)


def test_thin_review_accepted_report_short_handoff_not_hurt():
    """有合格 accepted 报告时短 handoff 不误伤降档。"""
    from agentcore.workspace.stage_dirs import REVIEWS_PREFIX

    report = f"{REVIEWS_PREFIX}M1-复核报告.md"
    plan = _thin_review_plan(report)
    results = {
        "fix": RunState(
            phase=RunPhase.COMPLETED,
            content="已修",
            files_touched=["src/a.ts"],
            file_acceptance=_accepted("src/a.ts"),
        ),
        "review": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",  # 短 handoff 叙事
            debrief={"summary": "通过"},
            files_touched=[report],
            file_acceptance=_accepted(report),
        ),
    }
    payload = build_delivery_status(plan, results, execution_id="e-thin-ok")
    assert payload is not None
    assert payload["state"] == "delivered"
    assert all(g.get("reason") != "thin_review" for g in payload["gaps"])


def test_thin_review_shell_report_depresses():
    """声明路径已 accepted 但骨架/篇幅软提醒 → 空壳 thin_review。"""
    from agentcore.workspace.stage_dirs import REVIEWS_PREFIX

    report = f"{REVIEWS_PREFIX}审校报告.md"
    plan = _thin_review_plan(report)
    results = {
        "fix": RunState(
            phase=RunPhase.COMPLETED,
            content="已修",
            files_touched=["src/a.ts"],
            file_acceptance=_accepted("src/a.ts"),
        ),
        "review": RunState(
            phase=RunPhase.COMPLETED,
            content="骨架",
            files_touched=[report],
            file_acceptance=_accepted(report),
            warnings=["篇幅提醒（软）：产出 12 字，少于要求的 80 字"],
        ),
    }
    payload = build_delivery_status(plan, results, execution_id="e-thin-shell")
    assert payload is not None
    assert payload["state"] == "partial"
    assert any(g.get("reason") == "thin_review" for g in payload["gaps"])
    assert any("空壳" in (g.get("description") or "") for g in payload["gaps"])


def test_thin_review_does_not_expand_posture_a():
    """A′ 不扩姿势 A：裸「全链路通过」仍不进姿势 A 闭集。"""
    from agentcore.runtime.closing_posture import claims_posture_a

    assert not claims_posture_a("审阅 → 修复 → 复核 → 打包，全链路通过 ✅")
    assert not claims_posture_a("独立复核通过")


def test_verify_failed_latches_requires_draft_ack():
    """丙轴 verify_failed 与 draft-ack 共用闩（可与 soft latch 并存）。"""
    from agentcore.runtime.delegate.delivery_status import current_delivery_verdict

    plan = _plan(RunSpec(run_id="w1", task="修并验证", role="工程师"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="已修好",
            files_touched=["src/a.ts"],
            file_acceptance=_accepted("src/a.ts"),
            transcript=_failed_test_run_transcript(),
        )
    }
    current_delivery_verdict.set(None)
    sink = EventSink()
    maybe_emit_delivery_status(sink, plan, results, execution_id="e-vf-ack")
    verdict = current_delivery_verdict.get()
    assert verdict is not None
    assert verdict.state != "delivered"
    assert verdict.requires_draft_ack is True
    current_delivery_verdict.set(None)


def test_undeclared_review_role_not_thin_review():
    """未 stamp form=files+reviews/ 的「独立复核员」不因角色名抬 thin_review。"""
    plan = _plan(
        RunSpec(run_id="fix", task="修", role="修复工程师"),
        RunSpec(run_id="review", task="只读复核", role="独立复核员", depends_on=["fix"]),
    )
    results = {
        "fix": RunState(phase=RunPhase.COMPLETED, content="ok"),
        "review": RunState(phase=RunPhase.COMPLETED, content="通过"),
    }
    payload = build_delivery_status(plan, results, execution_id="e-no-stamp")
    # 纯 prose 成功批保持无声，或即便有卡也无 thin_review。
    if payload is None:
        return
    assert all(g.get("reason") != "thin_review" for g in payload["gaps"])


def test_node_failed_latches_requires_draft_ack():
    """能力4：contract.failed → RunPhase.FAILED → node_failed + requires_draft_ack。"""
    from agentcore.runtime.delegate.delivery_status import current_delivery_verdict

    plan = _plan(
        RunSpec(run_id="ok", task="写 A", role="模块A"),
        RunSpec(run_id="bad", task="写 B", role="模块B"),
    )
    results = {
        "ok": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["a.md"],
            file_acceptance=_accepted("a.md"),
        ),
        "bad": RunState(
            phase=RunPhase.FAILED,
            content="半成品",
            error="缺少必备章节：结论；severity 枚举非法",
            files_touched=[],
            file_acceptance=[],
        ),
    }
    payload = build_delivery_status(plan, results, execution_id="e-node-fail")
    assert payload is not None
    assert payload["state"] == "partial"
    assert any(g.get("reason") == "node_failed" for g in payload["gaps"])

    current_delivery_verdict.set(None)
    maybe_emit_delivery_status(EventSink(), plan, results, execution_id="e-node-fail")
    verdict = current_delivery_verdict.get()
    assert verdict is not None
    assert verdict.state == "partial"
    assert verdict.requires_draft_ack is True
    current_delivery_verdict.set(None)


def test_artifact_rejected_latches_requires_draft_ack():
    """能力4：rejected 产物 → artifact_rejected gap + requires_draft_ack；至少 partial。"""
    from agentcore.runtime.delegate.delivery_status import current_delivery_verdict
    from agentcore.runtime.runs.file_acceptance import (
        build_file_acceptance,
        path_rejections_from_contract_messages,
    )

    cite_msg = (
        "`paper.md`：正文出现学位论文/期刊式著录标记（[D]）但未就地绑定本回合台账 #rN——"
        "属于未核验或编造引用。"
    )
    path_rej = path_rejections_from_contract_messages([cite_msg])
    acceptance = build_file_acceptance(
        ["paper.md", "outline.md"],
        phase=RunPhase.COMPLETED,
        path_rejections=path_rej,
    )
    plan = _plan(RunSpec(run_id="w1", task="写综述", role="撰写"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="已落盘",
            files_touched=["paper.md", "outline.md"],
            file_acceptance=acceptance,
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-rej-ack")
    assert payload is not None
    assert payload["state"] == "partial"
    assert any(g.get("reason") == "artifact_rejected" for g in payload["gaps"])
    assert "paper.md" not in payload["delivered_files"]
    assert "outline.md" in payload["delivered_files"]

    current_delivery_verdict.set(None)
    maybe_emit_delivery_status(EventSink(), plan, results, execution_id="e-rej-ack")
    verdict = current_delivery_verdict.get()
    assert verdict is not None
    assert verdict.requires_draft_ack is True
    current_delivery_verdict.set(None)


def test_failed_zero_landing_soft_preserves_node_failed_draft_ack():
    """甲⁺零落盘 soft 投影仍保留 node_failed → draft_ack（不丢闩）。"""
    from agentcore.runtime.delegate.delivery_status import current_delivery_verdict
    from agentcore.runtime.runs.contract import zero_files_gap_message

    tip = zero_files_gap_message()
    plan = _plan(
        RunSpec(run_id="ok", task="写 A", role="模块A"),
        RunSpec(run_id="bad", task="写 B", role="模块B"),
    )
    results = {
        "ok": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["a.md"],
            file_acceptance=_accepted("a.md"),
        ),
        "bad": RunState(
            phase=RunPhase.FAILED,
            content="",
            error=tip,
            files_touched=[],
        ),
    }
    payload = build_delivery_status(plan, results, execution_id="e-soft-fail")
    assert payload is not None
    assert any(g.get("reason") == "node_failed" for g in payload["gaps"])

    current_delivery_verdict.set(None)
    maybe_emit_delivery_status(EventSink(), plan, results, execution_id="e-soft-fail")
    verdict = current_delivery_verdict.get()
    assert verdict is not None
    assert verdict.requires_draft_ack is True
    current_delivery_verdict.set(None)


def test_acceptance_counts_match_delivered_and_rejected():
    """条数同源：acceptance_counts ↔ delivered_files / rejected artifacts。"""
    from agentcore.runtime.delegate.delivery_status import acceptance_counts
    from agentcore.runtime.runs.file_acceptance import (
        build_file_acceptance,
        path_rejections_from_contract_messages,
    )

    cite_msg = "`x.md`：未核验引用。"
    path_rej = path_rejections_from_contract_messages([cite_msg])
    acceptance = build_file_acceptance(
        ["x.md", "y.md"],
        phase=RunPhase.COMPLETED,
        path_rejections=path_rej,
    )
    plan = _plan(RunSpec(run_id="w1", task="写", role="撰写"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["x.md", "y.md"],
            file_acceptance=acceptance,
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-counts")
    assert payload is not None
    accepted_n, rejected_n = acceptance_counts(results)
    assert accepted_n == len(payload["delivered_files"])
    assert rejected_n == sum(1 for a in payload["artifacts"] if a.get("status") == "rejected")
    assert accepted_n + rejected_n == len(payload["artifacts"])


def test_b1_empty_handoff_storm_does_not_block():
    """多席空交接不再打 empty_handoff_storm blocking。"""
    from agentcore.runtime.closing_posture import (
        clear_b1_closing_latches,
        turn_has_empty_handoff_storm,
    )
    from agentcore.runtime.delegate.delivery_status import REASON_EMPTY_HANDOFF_STORM

    clear_b1_closing_latches()
    specs = [RunSpec(run_id=f"w{i}", task="审", role=f"席{i}") for i in range(5)]
    plan = _plan(*specs)
    results = {
        f"w{i}": RunState(
            phase=RunPhase.COMPLETED,
            content="",
            delivery_gaps=[{"description": "交接说明不够完整", "reason": "degraded_handoff"}],
        )
        for i in range(5)
    }
    payload = build_delivery_status(plan, results, execution_id="e-storm")
    assert payload is None or payload["state"] in ("notes", "delivered")
    if payload is not None:
        assert not any(g.get("reason") == REASON_EMPTY_HANDOFF_STORM for g in payload["gaps"])
    assert not turn_has_empty_handoff_storm()
    clear_b1_closing_latches()


def test_b1_cancel_zero_does_not_add_checklist_gap():
    """cancel + 零声明产物：不再附加「未交付清单」blocking / draft_ack latch。"""
    from agentcore.runtime.closing_posture import (
        clear_b1_closing_latches,
        turn_has_cancel_zero_output,
    )
    from agentcore.runtime.delegate.delivery_status import REASON_CANCELLED

    clear_b1_closing_latches()
    plan = _plan(
        RunSpec(run_id="a", task="梳理", role="调研员"),
        RunSpec(run_id="b", task="评审", role="审校"),
    )
    results = {
        "a": RunState(phase=RunPhase.CANCELLED),
        "b": RunState(phase=RunPhase.CANCELLED),
    }
    payload = build_delivery_status(plan, results, execution_id="e-cancel0")
    assert payload is not None
    assert not any("未交付清单" in str(g.get("description") or "") for g in payload["gaps"])
    assert any(g.get("reason") == REASON_CANCELLED for g in payload["gaps"])
    assert not turn_has_cancel_zero_output()
    clear_b1_closing_latches()


# ── 成品归位（promoted）契约 ────────────────────────────────────────────────


def _promotion_ledger():
    from agentcore.tools.protocol import TurnPromotionLedger

    return TurnPromotionLedger()


def test_promoted_absent_when_nothing_was_promoted():
    """零归位是合法状态：wire 上连 key 都不多一个，客户端按缺省空数组读。"""
    from agentcore.runtime.events import delivery_status
    from agentcore.runtime.events.payloads.run import DeliveryStatusPayload

    plan = _plan(RunSpec(run_id="w1", task="写文件", role="工程师"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["a.md"],
            file_acceptance=_accepted("a.md"),
        )
    }
    payload = build_delivery_status(
        plan, results, execution_id="e-no-promo", promotion_ledger=_promotion_ledger()
    )
    assert payload is not None
    assert "promoted" not in payload

    event = delivery_status(**payload)
    assert "promoted" not in event.payload
    model = DeliveryStatusPayload.model_validate(event.payload)
    assert model.promoted == []


def test_promoted_rows_ride_the_wire_as_from_to():
    """``{from, to}``：``from`` 是关键字，模型用别名——wire 上必须是 ``from``。"""
    from agentcore.runtime.events import delivery_status
    from agentcore.runtime.events.payloads.run import DeliveryStatusPayload

    event = delivery_status(
        execution_id="e-promo",
        state="delivered",
        summary="已交付",
        delivered_files=["讲稿.md"],
        gaps=[],
        actions=[],
        artifacts=[{"path": "讲稿.md", "status": "accepted"}],
        promoted=[{"from": "AgentCore/文档/工作稿/讲稿.md", "to": "讲稿.md"}],
    )
    assert event.payload["promoted"] == [{"from": "AgentCore/文档/工作稿/讲稿.md", "to": "讲稿.md"}]
    model = DeliveryStatusPayload.model_validate(event.payload)
    assert model.promoted[0].from_path == "AgentCore/文档/工作稿/讲稿.md"
    assert model.promoted[0].to == "讲稿.md"


def test_promoted_paths_are_rewritten_on_a_later_batch():
    """同回合第二批的对账从 worker 台账重建，仍记旧路径——必须重映射到归位后的位置。"""
    from agentcore.workspace.stage_dirs import DRAFTS_DIR

    old = f"{DRAFTS_DIR}/讲稿.md"
    ledger = _promotion_ledger()
    ledger.promotions.append({"from": old, "to": "讲稿.md"})

    plan = _plan(RunSpec(run_id="w1", task="改讲稿", role="撰写"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=[old],
            file_acceptance=_accepted(old),
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-batch2", promotion_ledger=ledger)
    assert payload is not None
    assert payload["delivered_files"] == ["讲稿.md"]
    assert [a["path"] for a in payload["artifacts"]] == ["讲稿.md"]
    assert payload["promoted"] == [{"from": old, "to": "讲稿.md"}]


@pytest.mark.asyncio
async def test_availability_reinject_keeps_promoted_rows(monkeypatch):
    """短问重发的是同一张卡（同 execution_id）：丢了 promoted 就抹掉旧路径的回查线索。"""
    from agentcore.runtime.delegate.delivery_status import (
        current_delivery_verdict,
        maybe_reinject_recent_delivery_for_availability_ask,
    )
    from agentcore.runtime.delegate.promotion import turn_promotions
    from agentcore.workspace.stage_dirs import DRAFTS_DIR

    old = f"{DRAFTS_DIR}/讲稿.md"
    journaled = {
        "execution_id": "e-short-ask",
        "state": "delivered",
        "summary": "已交付",
        "delivered_files": ["讲稿.md"],
        "gaps": [],
        "actions": [],
        "artifacts": [{"path": "讲稿.md", "status": "accepted"}],
        "promoted": [{"from": old, "to": "讲稿.md"}],
    }

    class _Repo:
        def __init__(self, _session):
            pass

        async def find_latest_delivery_status(self, *, conversation_id, exclude_turn_id=None):
            return dict(journaled)

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr("agentcore.db.base.async_session_factory", lambda: _Session())
    monkeypatch.setattr("agentcore.db.repositories.TurnJournalRepository", _Repo)
    current_delivery_verdict.set(None)

    sink = EventSink()
    ledger = _promotion_ledger()
    ok = await maybe_reinject_recent_delivery_for_availability_ask(
        sink,
        conversation_id="conv-1",
        user_message="好了吗？",
        promotion_ledger=ledger,
    )

    assert ok is True
    cards = [e.payload for e in sink.history_snapshot() if e.type == EventType.DELIVERY_STATUS]
    assert cards[0]["promoted"] == [{"from": old, "to": "讲稿.md"}]
    # 台账接手旧行，本回合再归位时重发才不会把它们抹掉。
    assert turn_promotions(ledger) == [{"from": old, "to": "讲稿.md"}]
    current_delivery_verdict.set(None)


def test_maybe_emit_notes_reconciliation_for_the_accepted_gate():
    """发射对账时必须记进回合台账（journal 重放 / 后续批次改写读这一份）。"""
    from agentcore.runtime.runs.file_acceptance import (
        build_file_acceptance,
        path_rejections_from_contract_messages,
    )

    ledger = _promotion_ledger()
    assert ledger.reconciliation is None

    cite_msg = (
        "`bad.md`：正文出现学位论文/期刊式著录标记（[D]）但未就地绑定本回合台账 #rN——"
        "属于未核验或编造引用。"
    )
    acceptance = build_file_acceptance(
        ["good.md", "bad.md"],
        phase=RunPhase.COMPLETED,
        path_rejections=path_rejections_from_contract_messages([cite_msg]),
    )
    plan = _plan(RunSpec(run_id="w1", task="写稿", role="撰写"))
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="ok",
            files_touched=["good.md", "bad.md"],
            file_acceptance=acceptance,
        )
    }
    maybe_emit_delivery_status(
        EventSink(),
        plan,
        results,
        execution_id="e-gate",
        promotion_ledger=ledger,
    )
    assert ledger.reconciliation is not None
    accepted = [
        a["path"]
        for a in ledger.reconciliation.get("artifacts") or []
        if isinstance(a, dict) and a.get("status") == "accepted"
    ]
    assert accepted == ["good.md"]


def test_prose_wave_keeps_files_not_landed_soft():
    """全员 form=prose：甲⁺ 仍 notes，不挡用户面收工。"""
    plan = _plan(
        RunSpec(
            run_id="w1",
            task="口头结论",
            role="研究员",
            deliverable=Deliverable(form="prose"),
        )
    )
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="结论在正文",
            delivery_gaps=[
                {
                    "description": "本队员本波未交卷：未把产物写入工作区",
                    "severity": "warning",
                    "reason": "files_not_landed",
                }
            ],
        )
    }
    payload = build_delivery_status(plan, results, execution_id="e-prose-soft")
    assert payload is not None
    assert payload["state"] == "notes"
    gap = payload["gaps"][0]
    assert gap["reason"] == "files_not_landed"
    assert gap.get("severity") == "warning"


def test_path_mismatch_latches_draft_ack():
    from agentcore.runtime.delegate.delivery_status import current_delivery_verdict

    declared = "build/icon.ico"
    plan = _plan(
        RunSpec(
            run_id="w1",
            task="做图标",
            role="工程师",
            deliverable=Deliverable(form="workspace", artifacts=[declared]),
        )
    )
    results = {
        "w1": RunState(phase=RunPhase.COMPLETED, content="做好了"),
    }
    current_delivery_verdict.set(None)
    maybe_emit_delivery_status(EventSink(), plan, results, execution_id="e-icon-miss")
    verdict = current_delivery_verdict.get()
    assert verdict is not None
    assert verdict.state != "delivered"
    assert verdict.requires_draft_ack is True
    assert declared in verdict.missing_declared
    current_delivery_verdict.set(None)
