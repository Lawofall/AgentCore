"""Tests for the contract gate's mechanical checks (阶段2 第一刀).

Covers the always-on non-empty baseline, each declared rule (length / keyword /
section / json), failure collection order, and the feedback / requirements
rendering the executor uses for the retry prompt.
"""

from agentcore.runtime.runs.contract import (
    ContractVerdict,
    check_contract,
    debrief_meets_minimum,
    describe_deliverable,
    format_cite_upgrade_feedback,
    format_feedback,
    format_light_repair_feedback,
    format_write_pass_feedback,
    has_salvageable_half_product,
    is_file_deliverable,
    is_format_repairable,
    is_zero_files_gap,
    needs_file_contents,
    node_has_dependents,
    strip_invalid_ledger_refs_from_surfaces,
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


def test_min_length_no_longer_consumed():
    # 已删字段：min_length 不再产生 soft warning / fail。
    short = check_contract("短", RunContract())
    assert short.ok
    assert short.failures == []
    assert not any("少于" in w for w in short.warnings)
    assert check_contract("这是一段足够长的产出内容", RunContract()).ok


def test_must_contain_no_longer_consumed():
    # 已删字段：must_contain 不再产生 soft warning / fail。
    contract = RunContract()
    v = check_contract("这里只讨论了结论", contract)
    assert v.ok
    assert v.failures == []
    assert not any("风险" in w for w in v.warnings)
    assert check_contract("既有风险也有结论", contract).ok


def test_must_contain_retired_no_case_soft_tip():
    # Legacy must_contain is ignored entirely (no soft tip either).
    contract = RunContract()
    assert check_contract("本方案的 api 设计与 roi 测算如下", contract).ok
    v = check_contract("只提到了 api 设计", contract)
    assert v.ok
    assert not any("ROI" in w for w in v.warnings)


def test_required_section_heading_shapes_detected():
    contract = RunContract(required_sections=["结论"])
    assert check_contract("# 结论\n内容", contract).ok
    assert check_contract("## 结论\n内容", contract).ok
    assert check_contract("**结论**\n内容", contract).ok
    assert check_contract("结论：完成了", contract).ok


def test_required_section_missing():
    v = check_contract("# 结论\n正文很长", RunContract(required_sections=["参考来源"]))
    assert not v.ok
    assert any("参考来源" in f for f in v.failures)


def test_required_section_incidental_mention_not_enough():
    # A keyword buried in prose is not a section heading.
    v = check_contract("我们在文中得出结论这件事很复杂", RunContract(required_sections=["结论"]))
    assert not v.ok


def test_json_format_pass_plain_and_fenced():
    contract = RunContract(output_format="json")
    assert check_contract('{"a": 1}', contract).ok
    assert check_contract('```json\n{"a": 1}\n```', contract).ok


def test_json_format_failure_on_prose():
    v = check_contract("这不是 JSON", RunContract(output_format="json"))
    assert not v.ok
    assert any("JSON" in f for f in v.failures)


def test_retired_length_and_keyword_fields_ignored():
    # 已删字段：字数 + 必含词均不消费，不进 warnings / failures。
    v = check_contract("短文本", RunContract())
    assert v.ok
    assert v.failures == []
    assert not any("少于" in w or "素材覆盖" in w for w in v.warnings)


def test_format_feedback_empty_when_only_retired_field_gaps():
    # Retired soft-only fields → ok verdict → format_feedback 空。
    fb = format_feedback(check_contract("短", RunContract()))
    assert fb == ""


def test_format_feedback_steers_worker_to_skip_meta_commentary():
    # Hard shortfall still gets the no-apology steer (sections miss, not length).
    fb = format_feedback(
        check_contract("正文里没有章节", RunContract(required_sections=["结论"]))
    )
    assert "完整最终产出" in fb
    assert "不要解释" in fb
    assert "不要道歉" in fb


def test_format_feedback_empty_when_ok():
    assert format_feedback(check_contract("ok 内容", None)) == ""


def test_is_format_repairable_for_section_only():
    # 已删字数/必含词不再进 light_repair；章节缺失仍可 format repair。
    section = check_contract(
        "正文里没有章节", RunContract(required_sections=["结论"])
    )
    assert is_format_repairable(section)
    short = check_contract("短", RunContract())
    assert short.ok
    assert not is_format_repairable(short)
    keyword = check_contract("只有别的", RunContract())
    assert keyword.ok
    assert not is_format_repairable(keyword)
    mixed = check_contract(
        "短", RunContract( output_format="json")
    )
    assert not is_format_repairable(mixed)
    empty = check_contract("", None)
    assert not is_format_repairable(empty)
    assert not is_format_repairable(check_contract("ok 内容足够长", None))


def test_contract_run_failure_kind_format_vs_quality():
    from agentcore.runtime.runs.contract import contract_run_failure_kind

    section = check_contract(
        "正文里没有章节", RunContract(required_sections=["结论"])
    )
    assert contract_run_failure_kind(section) == "format"
    empty = check_contract("", None)
    assert contract_run_failure_kind(empty) == "quality"
    bad_json = check_contract("not json", RunContract(output_format="json"))
    assert not bad_json.ok
    assert contract_run_failure_kind(bad_json) == "format"


def test_format_light_repair_feedback_carries_prior_and_skips_reinvestigate():
    v = check_contract("草稿缺章", RunContract(required_sections=["结论"]))
    # min_length ignored；缺章节仍 hard → light repair 只谈章节。
    assert is_format_repairable(v)
    fb = format_light_repair_feedback(v, prior_content="草稿缺章\n更多正文")
    assert "不必重新调查" in fb
    assert "缺少必备章节" in fb
    assert "草稿缺章" in fb
    assert "不要重新检索" in fb
    assert "str_replace" in fb
    assert "file_read" in fb  # soft tip: prefer manifest, avoid thrash re-read
    assert format_light_repair_feedback(
        check_contract("ok 内容", None), prior_content="x"
    ) == ""


def test_zero_files_gap_and_write_pass_feedback():
    # 甲⁺：零落盘进 warnings，不再是 hard gap / write_pass 触发条件。
    # 写盘期望认 form=files / workspace / artifacts（漏填=files）。
    v = check_contract(
        "只有文字", Deliverable(form="files"), files_written=0
    )
    assert v.ok
    assert any("本队员本波未交卷" in w for w in v.warnings)
    assert any("未把产物写入工作区" in w for w in v.warnings)
    assert not is_zero_files_gap(v)
    # 显式 prose → 不再产生零落盘 soft tip
    legacy_flag = check_contract(
        "只有文字", Deliverable(form="prose"), files_written=0
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
    assert "file_write" in fb
    assert not is_zero_files_gap(
        check_contract("ok", Deliverable(form="files"), files_written=1)
    )


def test_has_salvageable_half_product_gates_empty_synth():
    assert not has_salvageable_half_product("", [], None)
    assert not has_salvageable_half_product("  ", [], {"summary": "x", "degraded": True})
    assert has_salvageable_half_product("有正文", [], None)
    assert has_salvageable_half_product("", ["a.py"], None)
    assert has_salvageable_half_product(
        "",
        [],
        {
            "summary": (
                "足够长的合格交接简报内容用于下游接力——"
                "根因与拟改路径、风险假设与建议下一步均已写清，满足交接信息量地板。"
            )
        })
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
    desc = describe_deliverable(
        Deliverable(
            required_sections=["结论"], output_format="json"
        )
    )
    # json + required_sections: describe only surfaces JSON (Markdown sections skipped)
    assert "JSON" in desc
    # 已删字段不再渲染进文案
    assert "风险" not in desc
    assert "建议覆盖（软）" not in desc
    assert "200" not in desc
    assert "小标题" not in desc


def test_describe_deliverable_none_is_empty():
    assert describe_deliverable(None) == ""


def test_describe_deliverable_renders_section_skeleton():
    """required_sections appear both as acceptance list and as Markdown skeleton."""
    desc = describe_deliverable(
        Deliverable(required_sections=["Bug清单", "每个Bug的详情"])
    )
    assert "Bug清单" in desc
    assert "每个Bug的详情" in desc
    assert "## Bug清单" in desc
    assert "## 每个Bug的详情" in desc
    assert "建议正文骨架" in desc
    assert "50" not in desc  # min_length retired from describe


def test_describe_deliverable_json_file_channel():
    desc = describe_deliverable(
        Deliverable(
            form="files",
            output_format="json",
            artifacts=["AgentCore/文档/reviews/legal.json"])
    )
    assert "JSON" in desc
    assert "AgentCore/文档/reviews/legal.json" in desc
    assert "文件存在" in desc or "可解析" in desc
    assert "结构化审查" not in desc  # deliverable.name retired from describe


# --- Mix defense: output_format=json vs required_sections (Markdown) -------------


def test_json_skips_required_sections_mix():
    # JSON field names stuffed into required_sections must not cause false failure.
    contract = RunContract(
        output_format="json",
        required_sections=["problems", "suggestions", "score"])
    v = check_contract('{"problems": [], "suggestions": [], "score": 8}', contract)
    assert v.ok
    assert v.failures == []


def test_json_file_channel_accepts_prose_chat_when_file_valid():
    contract = RunContract(
        output_format="json",
        artifacts=["review.json"])
    v = check_contract(
        "已写入审查结果",
        contract,
        files_written=1,
        workspace_paths=["review.json"],
        artifact_contents={"review.json": '{"problems": [], "score": 7}'})
    assert v.ok


def test_json_file_channel_fails_when_file_not_json():
    contract = RunContract(
        output_format="json",
        artifacts=["review.json"])
    v = check_contract(
        "已写入",
        contract,
        files_written=1,
        workspace_paths=["review.json"],
        artifact_contents={"review.json": "这不是 JSON"})
    assert not v.ok
    assert any("JSON" in f for f in v.failures)


def test_json_file_channel_without_contents_still_warns_existence():
    # Callers that only have a path index still get existence warnings (not hard fail).
    contract = RunContract(output_format="json", artifacts=["review.json"])
    v = check_contract(
        "贴了",
        contract,
        files_written=1,
        workspace_paths=[],
        artifact_contents=None)
    assert v.ok
    assert any("review.json" in w for w in v.warnings)


# --- form=files / artifacts: the deliverable-landed gate over files_written -------------


def test_form_files_soft_when_none_written():
    """甲⁺：form=files ∧ 零落盘 → soft warning，不 fail；定案 B 标本队员本波未交卷。"""
    from agentcore.runtime.runs.contract import zero_files_gap_message

    v = check_contract("我把整份代码贴在这里", RunContract(form="files"), files_written=0)
    assert v.ok
    assert not v.failures
    assert any("工作区" in w for w in v.warnings)
    assert any("粘在回复正文" in w for w in v.warnings)
    assert any("本队员本波未交卷" in w for w in v.warnings)
    # Default attribution = paste framing (no landing_failure_kind).
    assert zero_files_gap_message() in v.warnings
    assert not is_zero_files_gap(v)


def test_omitted_form_zero_disk_soft():
    """漏填 form=files → 零落盘仍只 soft warning。"""
    v = check_contract(
        "我把整份代码贴在这里", RunContract(), files_written=0
    )
    assert v.ok
    assert any("未把产物写入工作区" in w for w in v.warnings)


def test_prose_form_no_zero_disk_soft():
    v = check_contract(
        "我把整份代码贴在这里", RunContract(form="prose"), files_written=0
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
        RunContract(form="files"),
        files_written=0,
        landing_failure_kind="channel_dead")
    assert v.ok
    assert tip in v.warnings
    assert any("写盘通道不可用" in w and "粘在回复正文" not in w for w in v.warnings)
    assert any("handoff 或正文交结论" in w for w in v.warnings)
    # Default paste framing must remain for non-channel_dead zero-landing.
    assert "粘在回复正文" in zero_files_gap_message()


def test_form_files_zero_disk_attributes_write_failed_not_paste():
    v = check_contract(
        "试过写盘",
        RunContract(form="files"),
        files_written=0,
        landing_failure_kind="write_failed")
    assert v.ok
    assert any("已尝试写盘但未成功" in w and "此缺口来自写盘失败" in w for w in v.warnings)
    assert not any(w.endswith("而非粘在回复正文里") for w in v.warnings)


def test_form_files_passes_when_a_file_was_written():
    assert check_contract("已写入 index.html", RunContract(form="files"), files_written=1).ok


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
        RunContract(form="files"),
        files_written=len(touched))
    assert v.ok
    assert not any("未把产物写入工作区" in f for f in v.failures)


def test_requires_files_passes_when_str_replace_landed():
    """str_replace / file_append 成功落盘须计入 files_written（分区 worker 增量补丁）。"""
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
                        name="str_replace",
                        arguments='{"path": "site/index.html", "old_string": "a", "new_string": "b"}'))
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
        Deliverable(form="files", artifacts=["site/index.html"]),
        files_written=len(touched),
        workspace_paths=["site/index.html", "site/styles.css"],
        artifact_contents={"site/index.html": "<html></html>"})
    assert v.ok
    assert not any("未把产物写入工作区" in f for f in v.failures)


def test_file_deliverable_empty_body_passes_when_artifact_text_loaded():
    """QA 等 file_write 收尾 + 空 streamed 正文：契约应读落盘文件，勿判「产出为空」。"""
    qa_body = "# QA\n\n## 通过项\n- HTML 结构完整\n"
    v = check_contract(
        "",
        Deliverable(form="files", artifacts=["site/QA.md"]),
        files_written=1,
        workspace_paths=["site/QA.md"],
        artifact_contents={"site/QA.md": qa_body})
    assert v.ok
    assert "产出为空" not in str(v.failures)


def test_file_deliverable_empty_body_still_fails_baseline_when_nothing_landed():
    """甲⁺：零落盘不再单独 fail；但空正文+零盘+无 handoff 仍触「产出为空」基线。"""
    v = check_contract(
        "",
        Deliverable(form="files", artifacts=["site/QA.md"]),
        files_written=0,
        workspace_paths=["site/index.html"],
        artifact_contents=None)
    assert not v.ok
    assert any("产出为空" in f for f in v.failures)


def test_form_files_reviews_landing_counts_as_product():
    """Dossier notes under reviews count toward files_written (product landing)."""
    from agentcore.workspace.stage_dirs import REVIEWS_DIR

    v = check_contract(
        "已写修复方案",
        Deliverable(form="files"),
        files_written=1,
        workspace_paths=[f"{REVIEWS_DIR}/修复方案.md"])
    assert v.ok
    assert not is_zero_files_gap(v)


def test_artifact_path_mismatch_is_warning_not_zero_gap():
    """Declared artifacts missing → warning only; not a zero-disk write_pass gap."""
    v = check_contract(
        "已写别处",
        Deliverable(form="files", artifacts=["expected.md"]),
        files_written=1,
        workspace_paths=["other.md"])
    assert v.ok
    assert any("expected.md" in w for w in v.warnings)
    assert not is_zero_files_gap(v)


def test_prose_form_ignores_file_count():
    # 显式 prose 从不因零写失败。
    assert check_contract("纯文字分析", RunContract(form="prose"), files_written=0).ok


def test_describe_deliverable_form_files_without_artifacts():
    desc = describe_deliverable(Deliverable(form="files"))
    assert "file_write" in desc
    assert "str_replace" in desc
    assert "工作区" in desc


def test_describe_deliverable_omitted_form_has_must_write_line():
    # 漏填 = files，须写桌。
    desc = describe_deliverable(Deliverable())
    assert "必须调用 file_write" in desc or "必须 file_write" in desc


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
    assert any("未把产物写入工作区" in w for w in v.warnings)
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


def test_debrief_meets_minimum_summary_or_key_points():
    assert not debrief_meets_minimum(None)
    assert not debrief_meets_minimum({"summary": "太短"})
    assert debrief_meets_minimum({"summary": "x" * 50})
    assert debrief_meets_minimum({"summary": "短", "key_points": ["a", "b"]})


def test_leaf_did_substantial_work_and_worker_expects_handoff():
    from agentcore.llm.provider.protocol import LLMMessage
    from agentcore.runtime.runs.contract import (
        LEAF_SUBSTANTIAL_BODY_CHARS,
        handoff_expectation_met,
        leaf_did_substantial_work,
        worker_expects_handoff,
    )
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec

    short = "调研结论一段"
    assert not leaf_did_substantial_work(short)
    assert leaf_did_substantial_work("x" * LEAF_SUBSTANTIAL_BODY_CHARS)
    assert leaf_did_substantial_work("", files_touched=["a.md"])
    msgs = [LLMMessage(role="tool", content="ok: listed")]
    assert leaf_did_substantial_work("", messages=msgs)

    plan = RunPlan(
        nodes=[
            RunSpec(run_id="a", task="t"),
            RunSpec(run_id="b", task="t", depends_on=["a"]),
        ]
    )
    assert worker_expects_handoff(plan, "a", content=short)  # upstream
    assert not worker_expects_handoff(plan, "b", content=short)  # short leaf
    assert worker_expects_handoff(plan, "b", content=short, files_touched=["n.md"])
    assert worker_expects_handoff(
        plan, "b", content="x" * LEAF_SUBSTANTIAL_BODY_CHARS
    )

    # Leaf: any author brief counts; upstream still needs the floor.
    thin = {"summary": "短"}
    assert handoff_expectation_met(thin, for_dependents=False)
    assert not handoff_expectation_met(thin, for_dependents=True)
    assert not handoff_expectation_met(None, for_dependents=False)

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
    assert is_file_deliverable(Deliverable(form="files"))
    assert is_file_deliverable(Deliverable(form="workspace"))
    assert is_file_deliverable(Deliverable(artifacts=["a.md"]))
    assert not is_file_deliverable(Deliverable(form="prose"))
    assert is_file_deliverable(Deliverable())
    assert not is_file_deliverable(None)
    assert not hasattr(Deliverable(), "requires_files")
    assert not hasattr(Deliverable(), "min_length")
    assert not hasattr(Deliverable(), "must_contain")
    assert not hasattr(Deliverable(), "name")
    assert not hasattr(Deliverable(), "must_contain_soft")


def test_needs_file_contents_predicate():
    # file-form + section check → must read the file
    assert needs_file_contents(Deliverable(form="files", required_sections=["X"]))
    assert needs_file_contents(Deliverable(artifacts=["a.md"], required_sections=["X"]))
    # existence-only files（漏填默认 files）→ no read needed
    assert not needs_file_contents(Deliverable())
    # JSON file gate still needs contents
    assert needs_file_contents(Deliverable(output_format="json", artifacts=["a.json"]))
    # file-form but existence-only (no content rule) → no read needed
    assert not needs_file_contents(Deliverable(form="files"))
    assert not needs_file_contents(Deliverable(artifacts=["a.md"]))
    # prose (body-only) → no file read
    assert not needs_file_contents(Deliverable(form="prose"))
    assert not needs_file_contents(None)
    # web batch (HTML+CSS/JS) → read even for existence-only / no deliverable
    assert needs_file_contents(
        Deliverable(form="files"),
        landed_paths=["index.html", "style.css"])
    assert needs_file_contents(None, landed_paths=["index.html", "app.js"])
    # content surface (Markdown) → placeholder scan needs a read
    assert needs_file_contents(
        Deliverable(form="files"),
        landed_paths=["report.md"])
    # code-only landing → still no read for existence-only deliverable
    assert not needs_file_contents(
        Deliverable(form="files"),
        landed_paths=["main.py"])


def test_file_form_section_satisfied_by_file_only():
    # The paper's sections live ONLY in the landed file; the chat body is a terse note.
    contract = Deliverable(
        form="files", required_sections=["方法", "结论"]
    )
    v = check_contract(
        "论文已写入 paper.md",
        contract,
        files_written=1,
        workspace_paths=["paper.md"],
        artifact_contents={"paper.md": "# 方法\n做法……\n\n# 结论\n结果……"})
    assert v.ok
    assert v.failures == []


def test_file_form_section_missing_in_both_fails():
    contract = Deliverable(
        form="files", required_sections=["参考文献"]
    )
    v = check_contract(
        "论文已写入",
        contract,
        files_written=1,
        workspace_paths=["paper.md"],
        artifact_contents={"paper.md": "# 方法\n# 结论"})
    assert not v.ok
    assert any("参考文献" in f for f in v.failures)


def test_file_form_must_contain_ignored():
    # 已删 must_contain：即使文件/正文都缺词也不再 soft tip。
    contract = Deliverable(form="files")
    v = check_contract(
        "见 paper.md",
        contract,
        files_written=1,
        workspace_paths=["paper.md"],
        artifact_contents={"paper.md": "本文没有约定主题词。"})
    assert v.ok
    assert not any("素材覆盖" in w for w in v.warnings)


def test_file_form_min_length_ignored():
    # 已删 min_length：短正文+短文件也不再 soft tip。
    contract = Deliverable(form="files")
    v = check_contract(
        "正" * 60,
        contract,
        files_written=1,
        workspace_paths=["paper.md"],
        artifact_contents={"paper.md": "正" * 60})
    assert v.ok
    assert not any("少于" in w for w in v.warnings)


def test_prose_deliverable_ignores_file_contents():
    # A prose (non-file) deliverable keeps body-only semantics even if contents are passed.
    contract = Deliverable(form="prose", required_sections=["结论"])
    v = check_contract(
        "正文没有结论章节",
        contract,
        artifact_contents={"note.md": "# 结论\n有的"})
    assert not v.ok
    assert any("结论" in f for f in v.failures)


def test_artifacts_deliverable_section_from_file_without_form():
    # artifacts (non-empty) alone marks it file-form → sections read the file.
    contract = Deliverable(artifacts=["report.md"], required_sections=["结论"])
    v = check_contract(
        "已写入",
        contract,
        files_written=1,
        workspace_paths=["report.md"],
        artifact_contents={"report.md": "# 结论\n完成"})
    assert v.ok


def test_file_form_falls_back_to_body_when_contents_unavailable():
    # A read failure (no artifact_contents) degrades to body-only rather than crashing.
    contract = Deliverable(form="files", required_sections=["结论"])
    ok_body = check_contract(
        "# 结论\n正文里有章节", contract, files_written=1, workspace_paths=["p.md"]
    )
    assert ok_body.ok
    miss = check_contract(
        "正文里没有章节", contract, files_written=1, workspace_paths=["p.md"]
    )
    assert not miss.ok


def test_format_feedback_annotates_checked_channels():
    v = check_contract(
        "缺章节",
        Deliverable(form="files", required_sections=["结论"]),
        files_written=1,
        workspace_paths=["paper.md"],
        artifact_contents={"paper.md": "# 方法"})
    fb = format_feedback(v, checked_files=["paper.md"])
    assert "paper.md" in fb
    assert "回复正文" in fb
    assert "落盘文件" in fb


def test_format_feedback_no_channel_note_for_prose():
    fb = format_feedback(check_contract("短", RunContract()))
    assert "落盘文件" not in fb


def test_artifact_unbound_bibliography_fails_when_ledger_connected():
    """File deliverable with GB/T [D] and no #rN fails contract when ledger is on."""
    contract = Deliverable(form="files")
    body = "郝万鑫. 某问题研究[D]. 长江大学, 2026."
    v = check_contract(
        "已写入综述",
        contract,
        files_written=1,
        workspace_paths=["paper.md"],
        artifact_contents={"paper.md": body},
        ledger_entries=[],
        citable_ids=frozenset())
    assert not v.ok
    assert any("paper.md" in f and ("#rN" in f or "编造" in f or "未核验" in f) for f in v.failures)


def test_artifact_bound_bibliography_passes():
    contract = Deliverable(form="files")
    entries = [
        {
            "id": "#r1",
            "url": "https://example.com/paper",
            "title": "正式论文",
            "snippet": "",
            "deep_read": True,
            "doc_kind": "thesis",
        }
    ]
    v = check_contract(
        "已写入",
        contract,
        files_written=1,
        workspace_paths=["paper.md"],
        artifact_contents={"paper.md": "张三. 某问题研究[D]. #r1\n\n正文足够长了。"},
        ledger_entries=entries,
        citable_ids=frozenset({"#r1"}))
    assert v.ok


def test_artifact_bibliography_skipped_without_ledger():
    """Without ledger connection, unbound [D] in files does not fail (legacy scope)."""
    contract = Deliverable(form="files")
    v = check_contract(
        "已写入综述",
        contract,
        files_written=1,
        workspace_paths=["paper.md"],
        artifact_contents={"paper.md": "郝万鑫. 某问题研究[D]. 长江大学, 2026."})
    assert v.ok


def test_artifact_invalid_r_ref_fails():
    contract = Deliverable(form="files")
    v = check_contract(
        "已写入",
        contract,
        files_written=1,
        workspace_paths=["note.md"],
        artifact_contents={"note.md": "结论见 #r99，详见上文分析。"},
        ledger_entries=[],
        citable_ids=frozenset({"#r1"}))
    assert not v.ok
    assert any("note.md" in f and "#r99" in f for f in v.failures)


def test_phase_a_skips_citation_gate():
    """调研阶段 A：enforce_citations=False → unbound #rN 不过成稿引用闸。"""
    from agentcore.runtime.runs.contract import partition_citation_failures

    contract = Deliverable(
        form="files",
        citation_mode="two_phase")
    body = "结论见 #r99，广搜摘要。"
    skipped = check_contract(
        "已落盘草案",
        contract,
        files_written=1,
        workspace_paths=["AgentCore/文档/research/角度.md"],
        artifact_contents={"AgentCore/文档/research/角度.md": body},
        ledger_entries=[],
        citable_ids=frozenset({"#r1"}),
        enforce_citations=False)
    assert skipped.ok
    enforced = check_contract(
        "已落盘草案",
        contract,
        files_written=1,
        workspace_paths=["AgentCore/文档/research/角度.md"],
        artifact_contents={"AgentCore/文档/research/角度.md": body},
        ledger_entries=[],
        citable_ids=frozenset({"#r1"}),
        enforce_citations=True)
    assert not enforced.ok
    cite, other = partition_citation_failures(enforced.failures)
    assert cite and not other


def test_non_research_citation_gate_unchanged():
    """非调研路径（citation_mode 默认）仍立刻跑引用闸。"""
    contract = Deliverable(form="files")
    v = check_contract(
        "已写入",
        contract,
        files_written=1,
        workspace_paths=["site/copy.md"],
        artifact_contents={"site/copy.md": "售价 99 元#r99。"},
        ledger_entries=[],
        citable_ids=frozenset({"#r1"}))
    assert not v.ok
    assert any("#r99" in f for f in v.failures)


def test_needs_file_contents_loads_md_for_citation_surfaces():
    assert needs_file_contents(
        Deliverable(),
        landed_paths=["AgentCore/文档/research/综述.md"])


def test_format_cite_upgrade_feedback_is_light_strip_only():
    """短修文案：禁 read_url/广搜/deep_read；只要求去掉未核实编号或改待核实。"""
    fb = format_cite_upgrade_feedback(
        ["`note.md`：正文用了 #r99 …"],
        checked_files=["note.md"])
    assert "引用短修" in fb
    assert "待核实" in fb
    assert "handoff" in fb
    assert "read_url" not in fb
    assert "deep_read" not in fb
    assert "广搜" in fb  # 禁止广搜
    assert "`note.md`" in fb


def test_format_cite_upgrade_feedback_empty():
    assert format_cite_upgrade_feedback([]) == ""


def test_strip_invalid_ledger_refs_from_surfaces_artifacts_and_body():
    arts, body, stripped = strip_invalid_ledger_refs_from_surfaces(
        artifact_contents={"note.md": "结论见 #r1 与 #r99。"},
        body="摘要 #r99。",
        citable_ids=frozenset({"#r1"}))
    assert stripped == ["#r99"]
    assert arts is not None
    assert "#r99" not in arts["note.md"]
    assert "#r1" in arts["note.md"]
    assert "#r99" not in body
    # 剥完后引用闸应过
    v = check_contract(
        "已写入",
        Deliverable(form="files"),
        files_written=1,
        workspace_paths=["note.md"],
        artifact_contents=arts,
        ledger_entries=[],
        citable_ids=frozenset({"#r1"}),
        enforce_citations=True)
    assert v.ok


def test_strip_invalid_ledger_refs_from_surfaces_noop_when_clean():
    arts, body, stripped = strip_invalid_ledger_refs_from_surfaces(
        artifact_contents={"note.md": "结论见 #r1。"},
        body="ok",
        citable_ids=frozenset({"#r1"}))
    assert stripped == []
    assert arts == {"note.md": "结论见 #r1。"}
    assert body == "ok"


def test_strip_invalid_ledger_refs_from_surfaces_skips_without_citable():
    arts, body, stripped = strip_invalid_ledger_refs_from_surfaces(
        artifact_contents={"note.md": "见 #r99。"},
        body="见 #r99。",
        citable_ids=None)
    assert stripped == []
    assert arts == {"note.md": "见 #r99。"}
    assert body == "见 #r99。"


def test_strip_invalid_ledger_refs_from_debrief_summary_and_pointers():
    from agentcore.runtime.runs.contract import strip_invalid_ledger_refs_from_debrief

    debrief = {
        "summary": "结论 #r1 与假引用 #r99",
        "key_points": ["要点 #r2", "坏 #r88"],
        "next_steps": "下一步 #r99",
        "motion_card": {
            "motion": "命题",
            "fact_pointers": ["#r1", "#r77", "path/ok.md"],
        },
    }
    cleaned, stripped = strip_invalid_ledger_refs_from_debrief(
        debrief, frozenset({"#r1", "#r2"})
    )
    assert stripped == ["#r77", "#r88", "#r99"]
    assert cleaned is not None
    assert "#r99" not in cleaned["summary"]
    assert "#r1" in cleaned["summary"]
    assert "#r88" not in cleaned["key_points"][1]
    assert "#r2" in cleaned["key_points"][0]
    assert "#r99" not in cleaned["next_steps"]
    ptrs = cleaned["motion_card"]["fact_pointers"]
    assert "#r1" in ptrs
    assert not any("#r77" in str(p) for p in ptrs)
    assert "path/ok.md" in ptrs


def test_strip_invalid_ledger_refs_from_debrief_noop_when_clean():
    from agentcore.runtime.runs.contract import strip_invalid_ledger_refs_from_debrief

    debrief = {"summary": "仅 #r1", "key_points": ["#r1"]}
    cleaned, stripped = strip_invalid_ledger_refs_from_debrief(
        debrief, frozenset({"#r1"})
    )
    assert stripped == []
    assert cleaned == debrief
