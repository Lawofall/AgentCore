"""Tests for the contract gate's mechanical checks.

Covers the always-on non-empty baseline, pinned-path existence warnings,
handoff brief presence, and the feedback the executor uses for retry.
"""

from agentcore.runtime.runs.contract import (
    ContractVerdict,
    check_contract,
    debrief_meets_minimum,
    describe_deliverable,
    format_feedback,
    format_handoff_feedback,
    format_write_pass_feedback,
    has_salvageable_half_product,
    is_file_deliverable,
    is_zero_files_gap,
    node_has_dependents,
    synthesize_debrief,
)
from agentcore.runtime.runs.types import Deliverable, RunContract


def test_empty_fails_baseline_without_contract():
    v = check_contract("   ", None)
    assert not v.ok
    assert "空" in v.failures[0]


def test_empty_passes_when_files_written():
    v = check_contract("", None, files_written=1)
    assert v.ok


def test_empty_passes_when_handoff_debrief_present():
    v = check_contract("", None, debrief={"summary": "已完成写入 index.html"})
    assert v.ok


def test_empty_passes_when_handoff_has_key_points_only():
    v = check_contract("", None, debrief={"key_points": ["要点一"]})
    assert v.ok


def test_empty_still_fails_with_no_alternate_signals():
    v = check_contract("", None, files_written=0, debrief=None)
    assert not v.ok
    assert "空" in v.failures[0]


def test_non_empty_passes_without_contract():
    v = check_contract("有内容", None)
    assert v.ok
    assert v.failures == []


def test_format_feedback_steers_worker_to_skip_meta_commentary():
    fb = format_feedback(check_contract("", None))
    assert "完整最终产出" in fb
    assert "不要解释" in fb
    assert "不要道歉" in fb


def test_format_feedback_empty_when_ok():
    assert format_feedback(check_contract("ok 内容", None)) == ""


def test_zero_files_gap_and_write_pass_feedback():
    # 甲⁺：零落盘进 warnings，不再是 hard gap / write_pass 触发条件。
    # 写盘期望认非空 artifacts / artifact_dir。
    v = check_contract(
        "只有文字", Deliverable(artifacts=["out.md"]), files_written=0
    )
    assert v.ok
    assert any("本队员本波未交卷" in w for w in v.warnings)
    assert any("没有写成文件" in w for w in v.warnings)
    assert not is_zero_files_gap(v)
    # 显式 prose → 不再产生零落盘 soft tip
    legacy_flag = check_contract(
        "只有文字", Deliverable(), files_written=0
    )
    assert legacy_flag.ok
    assert not any("未把产物写入工作区" in w for w in legacy_flag.warnings)
    # format_write_pass_feedback 仍可对遗留 hard verdict 拼文案（防御保留）。
    legacy = ContractVerdict(
        ok=False, failures=["未把产物写入工作区：粘贴正文"], warnings=[]
    )
    assert is_zero_files_gap(legacy)
    fb = format_write_pass_feedback(legacy)
    assert "短写盘 pass" in fb
    assert "write / edit" in fb
    assert not is_zero_files_gap(
        check_contract("ok", Deliverable(artifacts=["out.md"]), files_written=1)
    )


def test_has_salvageable_half_product_gates_empty_synth():
    assert not has_salvageable_half_product("", [], None)
    assert has_salvageable_half_product("  ", [], {"summary": "x", "degraded": True})
    assert has_salvageable_half_product("有正文", [], None)
    assert has_salvageable_half_product("", ["a.py"], None)
    assert has_salvageable_half_product("", [], {"summary": "短便条"})
    # Empty inventory → synthesize still returns a shell, but callers must not use it.
    empty = synthesize_debrief("", [])
    assert empty.get("degraded") is True
    assert "无正文" in empty["summary"]


def test_force_finalize_salvage_accepts_tool_inventory_without_widening_half_product():
    from agentcore.llm.provider.protocol import LLMMessage
    from agentcore.runtime.engine.tool_exec import with_tool_failed_marker
    from agentcore.runtime.runs.contract import (
        has_salvageable_half_product,
        should_attempt_force_finalize_salvage,
        transcript_has_tool_inventory,
    )

    msgs = [
        LLMMessage(role="user", content="go"),
        LLMMessage(role="tool", content="file body here", tool_call_id="c1"),
    ]
    assert transcript_has_tool_inventory(msgs)
    assert not has_salvageable_half_product("", [], None)
    assert should_attempt_force_finalize_salvage("", [], None, messages=msgs)
    assert not should_attempt_force_finalize_salvage(
        "", [], None, messages=[LLMMessage(role="user", content="go")]
    )
    failed_only = [
        LLMMessage(
            role="tool",
            content=with_tool_failed_marker("boom"),
            tool_call_id="f1")
    ]
    assert not transcript_has_tool_inventory(failed_only)
    assert not should_attempt_force_finalize_salvage("", [], None, messages=failed_only)


def test_describe_deliverable_renders_rules():
    desc = describe_deliverable(Deliverable())
    assert desc == ""
    assert "form=" not in desc


def test_describe_deliverable_none_is_empty():
    assert describe_deliverable(None) == ""


def test_form_files_soft_when_none_written():
    """甲⁺：form=files ∧ 零落盘 → soft warning，不 fail；定案 B 标本队员本波未交卷。"""
    from agentcore.runtime.runs.contract import zero_files_gap_message

    v = check_contract("我把整份代码贴在这里", RunContract(artifacts=["out.md"]), files_written=0)
    assert v.ok
    assert not v.failures
    assert any("本队员本波未交卷" in w for w in v.warnings)
    assert any("没有写成文件" in w for w in v.warnings)
    assert zero_files_gap_message() in v.warnings
    assert not is_zero_files_gap(v)


def test_omitted_form_zero_disk_not_soft():
    """省略不催写：零落盘不进 files_not_landed 软提醒。"""
    v = check_contract(
        "我把整份代码贴在这里", RunContract(), files_written=0
    )
    assert v.ok
    assert not any("未把产物写入工作区" in w for w in v.warnings)


def test_prose_form_no_zero_disk_soft():
    v = check_contract(
        "我把整份代码贴在这里", RunContract(), files_written=0
    )
    assert v.ok
    assert not any("未把产物写入工作区" in w for w in v.warnings)


def test_form_files_zero_disk_attributes_channel_dead_not_paste():
    from agentcore.runtime.runs.contract import zero_files_gap_message

    tip = zero_files_gap_message(landing_failure_kind="channel_dead")
    assert "写盘通道不可用" in tip
    assert "handoff 或正文交结论" in tip
    assert "禁止再尝试落盘" in tip
    assert "恢复工作区通道后重试" in tip
    assert "勿改用正文粘贴冒充落盘" not in tip
    assert "粘在回复正文" not in tip

    v = check_contract(
        "写了但通道挂了",
        RunContract(artifacts=["out.md"]),
        files_written=0,
        landing_failure_kind="channel_dead")
    assert v.ok
    assert tip in v.warnings
    assert any("写盘通道不可用" in w and "粘在回复正文" not in w for w in v.warnings)
    assert any("handoff 或正文交结论" in w for w in v.warnings)
    assert "没有写成文件" in zero_files_gap_message()
    assert "粘在回复正文" not in zero_files_gap_message()


def test_form_files_zero_disk_attributes_write_failed_not_paste():
    v = check_contract(
        "试过写盘",
        RunContract(artifacts=["out.md"]),
        files_written=0,
        landing_failure_kind="write_failed")
    assert v.ok
    assert any("已尝试写盘但未成功" in w and "此缺口来自写盘失败" in w for w in v.warnings)
    assert not any(w.endswith("而非粘在回复正文里") for w in v.warnings)


def test_form_files_passes_when_a_file_was_written():
    assert check_contract("已写入 index.html", RunContract(artifacts=["out.md"]), files_written=1).ok


def test_form_files_passes_when_file_copy_landed():
    """file_copy 成功落盘须计入 files_written（产物复制进工作区）。"""
    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
    from agentcore.runtime.runs.serialize import files_touched_from_transcript
    from agentcore.tools.file_products import file_product, with_file_products_marker

    transcript = [
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="cp1",
                    function=ToolCallFunction(
                        name="file_copy",
                        arguments=(
                            '{"source": "tmp/out.pptx",'
                            ' "destination": "deck.pptx"}'
                        )))
            ]),
        LLMMessage(
            role="tool",
            content=with_file_products_marker("已复制", [file_product("deck.pptx")]),
            tool_call_id="cp1"),
    ]
    touched = files_touched_from_transcript(transcript)
    assert touched == ["deck.pptx"]
    v = check_contract(
        "已复制成品",
        RunContract(artifacts=["out.md"]),
        files_written=len(touched))
    assert v.ok
    assert not any("未把产物写入工作区" in f for f in v.failures)


def test_requires_files_passes_when_str_replace_landed():
    """edit 成功落盘须计入 files_written（分区 worker 增量补丁）。"""
    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
    from agentcore.runtime.runs.serialize import files_touched_from_transcript
    from agentcore.tools.file_products import file_product, with_file_products_marker

    transcript = [
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="s1",
                    function=ToolCallFunction(
                        name="edit",
                        arguments='{"file_path": "site/index.html", "old_string": "a", "new_string": "b"}'))
            ]),
        LLMMessage(
            role="tool",
            content=with_file_products_marker(
                "已替换 site/index.html", [file_product("site/index.html")]
            ),
            tool_call_id="s1"),
    ]
    touched = files_touched_from_transcript(transcript)
    assert touched == ["site/index.html"]
    v = check_contract(
        "",
        Deliverable(artifacts=["site/index.html"]),
        files_written=len(touched),
        workspace_paths=["site/index.html", "site/styles.css"],
    )
    assert v.ok
    assert not any("未把产物写入工作区" in f for f in v.failures)


def test_file_deliverable_empty_body_passes_when_files_written():
    """write 收尾 + 空 streamed 正文：有落盘即过基线，勿判「产出为空」。"""
    v = check_contract(
        "",
        Deliverable(artifacts=["site/QA.md"]),
        files_written=1,
        workspace_paths=["site/QA.md"],
    )
    assert v.ok
    assert "产出为空" not in str(v.failures)


def test_file_deliverable_empty_body_still_fails_baseline_when_nothing_landed():
    """甲⁺：零落盘不再单独 fail；但空正文+零盘+无 handoff 仍触「产出为空」基线。"""
    v = check_contract(
        "",
        Deliverable( artifacts=["site/QA.md"]),
        files_written=0,
        workspace_paths=["site/index.html"],
    )
    assert not v.ok
    assert any("产出为空" in f for f in v.failures)


def test_form_files_reviews_landing_counts_as_product():
    """Any landed prose path counts toward files_written (product landing)."""
    v = check_contract(
        "已写修复方案",
        Deliverable(artifacts=["out.md"]),
        files_written=1,
        workspace_paths=["notes/修复方案.md"])
    assert v.ok
    assert not is_zero_files_gap(v)


def test_artifact_path_mismatch_is_warning_not_zero_gap():
    """Declared artifacts missing → warning only; not a zero-disk write_pass gap."""
    v = check_contract(
        "已写别处",
        Deliverable( artifacts=["expected.md"]),
        files_written=1,
        workspace_paths=["other.md"])
    assert v.ok
    assert any("expected.md" in w for w in v.warnings)
    assert not is_zero_files_gap(v)


def test_artifact_dir_miss_with_landing_is_silent():
    """仅 artifact_dir 未命中但已落盘 → 不发约定目录软提醒。"""
    v = check_contract(
        "已写",
        Deliverable( artifact_dir="notes", artifacts=[]),
        files_written=1,
        workspace_paths=["docs/法庭迷局侦探游戏_GDD.md"],
    )
    assert v.ok
    assert v.warnings == []
    assert not is_zero_files_gap(v)


def test_prose_form_ignores_file_count():
    # 显式 prose 从不因零写失败。
    assert check_contract("纯文字分析", RunContract(), files_written=0).ok


def test_describe_deliverable_form_files_without_artifacts():
    desc = describe_deliverable(Deliverable(artifacts=["out.md"]))
    assert "form=" not in desc
    assert "`out.md`" in desc


def test_describe_deliverable_omitted_form_has_no_must_write_line():
    desc = describe_deliverable(Deliverable())
    assert desc == ""
    assert "form=" not in desc


# --- artifacts: declarative path reconciliation ---------------------------------


def test_artifacts_pass_when_exact_path_present():
    v = check_contract(
        "done",
        RunContract(artifacts=["README.md"]),
        files_written=1,
        workspace_paths=["README.md", "src/main.py"])
    assert v.ok


def test_artifacts_warn_when_path_missing():
    v = check_contract(
        "done",
        RunContract(artifacts=["README.md", "examples/demo.py"]),
        files_written=1,
        workspace_paths=["src/main.py"])
    assert v.ok
    assert any("README.md" in w for w in v.warnings)
    assert any("examples/demo.py" in w for w in v.warnings)


def test_artifacts_glob_and_directory_match():
    d = RunContract(artifacts=["src/**/*.py", "examples/", "pkg/"])
    assert check_contract(
        "ok",
        d,
        files_written=2,
        workspace_paths=["src/a/b.py", "examples/x.txt", "pkg/__init__.py"]).ok
    v = check_contract(
        "ok",
        d,
        files_written=1,
        workspace_paths=["src/a/b.py"])
    assert v.ok
    assert any("examples/" in w for w in v.warnings)


def test_artifacts_empty_workspace_all_missing():
    v = check_contract(
        "贴了代码",
        RunContract(artifacts=["a.py"]),
        files_written=0,
        workspace_paths=[])
    # artifacts 非空 ⇒ 写盘期望：零落盘 soft tip + 路径对账 warning；仍不 hard-fail。
    assert v.ok
    assert any("a.py" in w for w in v.warnings)
    assert any("没有写成文件" in w for w in v.warnings)
    assert not is_zero_files_gap(v)


def test_artifacts_workspace_prefix_vs_relative_index_no_false_missing():
    """Accident shape: handoff declares /workspace/… while index/touched are relative.

    Bare ``lstrip("./")`` used to rewrite ``/workspace/index.html`` → ``workspace/index.html``,
    which never equals ``index.html`` → false「声明的交付物路径未落盘」despite a real write.
    """
    landed = ["index.html", "css/style.css", "js/main.js"]
    # Absolute sandbox prefix on one path + relatives (trace 7dbb0174… mix).
    declared = ["/workspace/index.html", "css/style.css", "js/main.js"]
    assert check_contract(
        "done",
        RunContract(artifacts=declared),
        files_written=len(landed),
        workspace_paths=landed).ok
    # Reverse: relative declaration, absolute-shaped index/touched entries.
    assert check_contract(
        "done",
        RunContract(artifacts=["index.html", "css/style.css", "js/main.js"]),
        files_written=3,
        workspace_paths=[
            "/workspace/index.html",
            "/workspace/css/style.css",
            "/workspace/js/main.js",
        ]).ok
    # All-absolute declaration against relative index.
    assert check_contract(
        "done",
        RunContract(
            artifacts=[
                "/workspace/index.html",
                "/workspace/css/style.css",
                "/workspace/js/main.js",
            ]
        ),
        files_written=3,
        workspace_paths=landed).ok


def test_artifacts_workspace_prefix_still_warns_when_truly_missing():
    """Prefix rewrite must not invent a hit — absent relative files still warn."""
    v = check_contract(
        "done",
        RunContract(artifacts=["/workspace/index.html", "/workspace/missing.css"]),
        files_written=1,
        workspace_paths=["index.html"])
    assert v.ok
    assert any("missing.css" in w for w in v.warnings)
    assert not any("index.html" in w for w in v.warnings)


def test_describe_deliverable_renders_artifacts():
    desc = describe_deliverable(Deliverable(artifacts=["README.md", "examples/*"]))
    assert "README.md" in desc
    assert "examples/*" in desc


def test_debrief_meets_minimum_measures_summary_only():
    assert not debrief_meets_minimum(None)
    assert debrief_meets_minimum({"summary": "短"})
    assert not debrief_meets_minimum({"summary": "  "})
    assert not debrief_meets_minimum({"summary": "", "key_points": ["a", "b"]})
    assert not debrief_meets_minimum({"key_points": ["a", "b"]})


def test_format_handoff_feedback_requires_call_when_dependents():
    text = format_handoff_feedback()
    assert "尚未调用 handoff" in text
    assert "请调用 handoff" in text
    assert "现在什么已成立" not in text
    assert "下一棒要接" not in text
    assert "做出了什么" not in text
    assert "2–4" not in text


def test_worker_expects_handoff_only_with_dependents():
    from agentcore.runtime.runs.contract import (
        handoff_expectation_met,
        worker_expects_handoff,
    )
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec

    plan = RunPlan(
        nodes=[
            RunSpec(run_id="a", task="t"),
            RunSpec(run_id="b", task="t", depends_on=["a"]),
        ]
    )
    assert worker_expects_handoff(plan, "a")  # upstream
    assert not worker_expects_handoff(plan, "b")  # leaf, even after tools / long body

    assert handoff_expectation_met({"summary": "短"})
    assert not handoff_expectation_met(None)
    assert not handoff_expectation_met({"summary": ""})

def test_synthesize_debrief_marks_degraded():
    d = synthesize_debrief("正文结论一段", ["a.py", "b.py"])
    assert d["degraded"] is True
    assert "正文结论" in d["summary"]
    assert d["key_points"]


def test_node_has_dependents():
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec

    plan = RunPlan(
        nodes=[
            RunSpec(run_id="a", task="t"),
            RunSpec(run_id="b", task="t", depends_on=["a"]),
        ]
    )
    assert node_has_dependents(plan, "a")
    assert not node_has_dependents(plan, "b")


# --- 交付形态对齐: file-form deliverables check body + landed files ------------------


def test_is_file_deliverable_predicate():
    assert is_file_deliverable(Deliverable(artifacts=["out.md"]))
    assert is_file_deliverable(Deliverable(artifact_dir="src"))
    assert is_file_deliverable(Deliverable(artifacts=["a.md"]))
    assert not is_file_deliverable(Deliverable())
    assert not is_file_deliverable(None)


def test_landed_html_css_skip_web_quality_and_seam():
    """Former anti-slop / fake-phone / unused-class gates no longer flip the contract."""
    html = """
    <html><head>
    <link rel="stylesheet" href="style.css">
    <style>body { font-family: Inter, "Poppins", sans-serif; }</style>
    </head><body>
    <h1>🏠 首页</h1>
    <p>咨询请拨 400-888-0000</p>
    <div class="site-header unused-slot">ok</div>
    </body></html>
    """
    css = ".hero { display: grid; } .never-used-in-html { color: red; }"
    contents = {"index.html": html, "style.css": css}
    v = check_contract(
        "已落盘网页",
        Deliverable(artifacts=["out.md"]),
        files_written=2,
        workspace_paths=list(contents),
    )
    assert v.ok
    assert v.failures == []
    assert v.soft_failures == []
    v_none = check_contract(
        "已落盘网页",
        None,
        files_written=2,
        workspace_paths=list(contents),
    )
    assert v_none.ok
    assert v_none.failures == []
    assert v_none.soft_failures == []
    assert not any("400" in w or "骨架" in w or "占位" in w for w in v.warnings)
    assert not any("400" in w or "骨架" in w or "占位" in w for w in v_none.warnings)


def test_landed_copy_self_notes_are_not_contract_warnings():
    """示例/虚构/TODO 文案不再进合同；质量交给模型。"""
    v = check_contract(
        "报告已写入\n本页客户证言为示例，关键指标为示例数据（虚构示意）。\nTODO: 补真实转化率。",
        Deliverable(artifacts=["report.md"]),
        files_written=1,
        workspace_paths=["report.md"],
    )
    assert v.ok
    assert v.failures == []
    assert v.warnings == []
    assert v.warning_rows == []


def test_format_feedback_no_channel_note_for_prose():
    fb = format_feedback(check_contract("短", RunContract()))
    assert "落盘文件" not in fb
