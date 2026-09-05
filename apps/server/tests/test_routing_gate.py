"""Worker 内部路由 Phase 1 — Escalation Gate.

Gate 不再扫工具输出自由文产 scheme_escalation；职责偏离只走结构化 escalate /
写路径真越界。本文件钉死「越权」等内容词不得升 wire scope，以及 payload 映射诚实性。
"""

from __future__ import annotations

from agentcore.runtime.loop_controller import ToolAttempt
from agentcore.runtime.routing import (
    EscalationKind,
    EscalationSignal,
    ProblemLayer,
    classify_problem,
    evaluate_after_tools,
    signals_as_dicts,
)


def test_execution_layer_tool_failure_continues():
    attempts = [ToolAttempt("fp1", "code_execute", success=False)]
    outputs = ["Traceback (most recent call last):\nFileNotFoundError: No such file"]
    verdict = evaluate_after_tools(attempts=attempts, tool_outputs=outputs, run_id="r1")
    assert verdict.layer is ProblemLayer.EXECUTION
    assert verdict.action == "continue"
    assert not verdict.should_escalate
    assert verdict.signals == []


def test_write_report_yuequan_does_not_scheme_escalate():
    """样本根因：报告含「越权」不得 → CONTRACT → wire scope → UI 职责偏离。"""
    attempts = [ToolAttempt("fp1", "file_write", success=True)]
    outputs = [
        "# 审计报告\n发现上游步骤存在越权写最终交付物的风险，建议复核权限边界。"
    ]
    verdict = evaluate_after_tools(attempts=attempts, tool_outputs=outputs, run_id="r1")
    assert not verdict.should_escalate
    assert verdict.layer is ProblemLayer.EXECUTION
    assert verdict.signals == []
    assert signals_as_dicts(verdict.signals) == []


def test_scheme_flavored_tool_outputs_do_not_escalate():
    """弱内容词扫不得产 scheme_escalation（契约 / 矛盾 / 职责 / 缺输入）。"""
    cases = [
        ("file_write", False, "继续执行会破坏对外契约 / 改接口契约，超出权限"),
        ("str_replace", True, "需求矛盾：无法同时满足 A 与 B"),
        ("str_replace", False, "卡在缺输入：依赖不存在，还没人产出"),
        ("file_write", True, "职责偏离：真正该做的是改文档而非改代码"),
        ("str_replace", False, "this is the wrong scope for the worker"),
        ("str_replace", True, "these requirements contradict each other; cannot ship both"),
        ("file_write", False, "this is a breaking change to the api contract"),
        ("file_write", False, "that is beyond my authority"),
        ("file_write", False, "违反接口契约，接口不兼容"),
        ("file_write", False, "this change is out of scope for the worker"),
    ]
    for tool, success, text in cases:
        attempts = [ToolAttempt("fp1", tool, success=success)]
        verdict = evaluate_after_tools(attempts=attempts, tool_outputs=[text])
        assert not verdict.should_escalate, text
        assert verdict.signals == [], text


def test_corpus_and_coordination_tools_stay_silent():
    for name in (
        "file_read",
        "grep",
        "code_search",
        "web_search",
        "web_fetch",
        "escalate",
        "handoff",
        "delegate",
    ):
        attempts = [ToolAttempt("fp1", name, success=True)]
        outputs = ["需求矛盾：无法同时满足 / 职责偏离 / 越权"]
        verdict = evaluate_after_tools(attempts=attempts, tool_outputs=outputs)
        assert not verdict.should_escalate, name


def test_bare_contradict_and_mixed_failures_stay_execution():
    attempts = [
        ToolAttempt("fp1", "code_execute", success=False),
        ToolAttempt("fp2", "file_write", success=False),
    ]
    outputs = [
        "Traceback (most recent call last):\nFileNotFoundError: No such file",
        "继续执行会破坏对外契约 / 越权",
    ]
    verdict = evaluate_after_tools(attempts=attempts, tool_outputs=outputs)
    assert verdict.layer is ProblemLayer.EXECUTION
    assert not verdict.should_escalate


def test_failure_without_output_stays_execution():
    attempts = [ToolAttempt("fp1", "code_execute", success=False)]
    verdict = evaluate_after_tools(attempts=attempts, tool_outputs=None)
    assert verdict.layer is ProblemLayer.EXECUTION
    assert verdict.action == "continue"
    assert not verdict.should_escalate


def test_classify_problem_never_scheme_from_free_text():
    assert classify_problem("ModuleNotFoundError: x") is ProblemLayer.EXECUTION
    assert classify_problem("超出权限，需改接口契约") is ProblemLayer.EXECUTION
    assert classify_problem("范围不对，与初始计划不符") is ProblemLayer.EXECUTION
    assert classify_problem("completely opaque gibberish xyz") is ProblemLayer.EXECUTION


def test_signals_wire_kind_contract_maps_to_normal_not_scope():
    """若仍构造 CONTRACT 信号，wire kind 诚实为 normal，不得占职责偏离。"""
    signal = EscalationSignal(
        layer=ProblemLayer.SCHEME,
        kind=EscalationKind.CONTRACT,
        question="继续执行可能改动接口契约",
        evidence="越权",
        tool_name="file_write",
        source="escalation_gate",
    )
    payloads = signals_as_dicts([signal])
    assert payloads[0]["kind"] == "normal"
    assert payloads[0]["gate_kind"] == "contract"
    assert payloads[0]["kind"] != "scope"


def test_signals_wire_kind_contradiction_maps_to_normal_not_scope():
    signal = EscalationSignal(
        layer=ProblemLayer.SCHEME,
        kind=EscalationKind.CONTRADICTION,
        question="任务需求存在矛盾",
        source="escalation_gate",
    )
    payloads = signals_as_dicts([signal])
    assert payloads[0]["kind"] == "normal"
    assert payloads[0]["gate_kind"] == "contradiction"


def test_signals_wire_kind_explicit_scope_still_scope():
    """结构化 escalate(kind=scope) 同源语义：SCOPE 仍占 wire scope。"""
    signal = EscalationSignal(
        layer=ProblemLayer.SCHEME,
        kind=EscalationKind.SCOPE,
        question="真正该做的与初始计划不符",
        source="escalate_tool",
    )
    payloads = signals_as_dicts([signal])
    assert payloads[0]["kind"] == "scope"
    assert payloads[0]["gate_kind"] == "scope"


def test_signals_wire_kind_dep_still_dep():
    signal = EscalationSignal(
        layer=ProblemLayer.SCHEME,
        kind=EscalationKind.DEP,
        question="卡在尚不存在的输入",
        source="escalate_tool",
    )
    payloads = signals_as_dicts([signal])
    assert payloads[0]["kind"] == "dep"
    assert payloads[0]["gate_kind"] == "dep"
