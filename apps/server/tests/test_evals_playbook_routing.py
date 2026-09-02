"""playbook 路由回归：零 LLM 校验落点分类 / think-act / 基线 diff / 夹具体量."""

from __future__ import annotations

from dataclasses import replace

import pytest

from agentcore.evals.__main__ import main
from agentcore.evals.playbook_routing import (
    SCENARIOS,
    RoutingTurn,
    aggregate_samples,
    classify_landing,
    diff_fingerprints,
    extract_think_mentions,
    format_playbook_routing_report,
    history_messages,
    landing_fingerprint,
    lint_codebase_fixture,
    lint_scenarios,
    named_playbook,
    parse_delegate_rich,
    slim_baseline,
    think_act_divergences,
)
from agentcore.evals.types import EvalConfigError

_LEGACY_EXPECT = {
    "research_brief_parallel": "map_fanout",
    "research_mit_vs_gpl_chat": "map_fanout",
    "research_knowledge_base_chat": "map_fanout",
}

_AUDIT_HANDWRITTEN = (
    "code_audit_report",
    "audit_check_bugs_save_file",
    "audit_find_issues_workspace_doc",
)

_GREENFIELD_HANDWRITTEN = (
    "greenfield_spa_build_app",
    "app_todo_website_usable",
    "app_todo_web_must_run",
)


def test_scenarios_lint_ok():
    lint_scenarios(SCENARIOS)
    assert sum(1 for s in SCENARIOS if s.phrasing == "colloquial") >= 8
    assert sum(1 for s in SCENARIOS if s.phrasing == "textbook") >= 3
    assert any(s.workspace == "codebase" for s in SCENARIOS)
    keys = {s.key for s in SCENARIOS}
    assert set(_LEGACY_EXPECT) <= keys
    assert set(_GREENFIELD_HANDWRITTEN) <= keys
    assert set(_AUDIT_HANDWRITTEN) <= keys
    assert "discuss_license_no_doc_waiver" in keys
    assert "discuss_license_round2_short_answers" in keys
    assert "write_prd_save_file" in keys
    assert "discuss_worker_params_industry" in keys
    assert "discuss_arch_bug_maintain_facets" in keys
    assert "identity_who_are_you" in keys
    assert "compare_three_js_frameworks" in keys


def test_legacy_named_playbook_scenarios_unchanged():
    by_key = {s.key: s for s in SCENARIOS}
    for key, pb in _LEGACY_EXPECT.items():
        sc = by_key[key]
        assert sc.expect_playbook == pb
        assert sc.expect_action == ""
        assert sc.expect_form is None
        assert sc.expect_max_workers is None
        assert sc.prior_turns == ()
    assert "先别写成文档" in by_key["research_mit_vs_gpl_chat"].user_message
    assert "先别写成文档" not in by_key["discuss_license_no_doc_waiver"].user_message
    assert "存成文件" in by_key["write_prd_save_file"].user_message
    assert "落盘" not in by_key["write_prd_save_file"].user_message


def test_audit_expects_handwritten_delegate():
    by_key = {s.key: s for s in SCENARIOS}
    for key in _AUDIT_HANDWRITTEN:
        sc = by_key[key]
        assert sc.expect_playbook == ""
        assert sc.expect_action == "DELEGATE"
        assert sc.expect_form == "files"
        assert sc.category == "code_audit"
        assert sc.workspace == "codebase"


def test_greenfield_expects_handwritten_delegate():
    by_key = {s.key: s for s in SCENARIOS}
    for key in _GREENFIELD_HANDWRITTEN:
        sc = by_key[key]
        assert sc.expect_playbook == ""
        assert sc.expect_action == "DELEGATE"
        assert sc.expect_form is None
        assert sc.category == "greenfield_app"
    assert "完整可跑" in by_key["greenfield_spa_build_app"].user_message
    assert "SPA" in by_key["greenfield_spa_build_app"].user_message


def test_discuss_and_prd_fixture_fields():
    by_key = {s.key: s for s in SCENARIOS}
    discuss = by_key["discuss_license_no_doc_waiver"]
    assert discuss.expect_playbook == "map_fanout"
    assert "DELEGATE" in discuss.expect_action and "ASK" in discuss.expect_action
    assert "DIRECT" not in discuss.expect_action
    round2 = by_key["discuss_license_round2_short_answers"]
    assert round2.prior_turns
    assert "DELEGATE" in round2.expect_action and "ASK" in round2.expect_action
    assert round2.user_message.startswith("1.")
    assert history_messages(round2.prior_turns)[0][0] == "user"
    prd = by_key["write_prd_save_file"]
    assert prd.expect_playbook == ""
    assert prd.expect_action == "DELEGATE"
    assert prd.expect_max_workers == 1
    assert prd.expect_form == "files"
    bound = by_key["discuss_worker_params_industry"]
    assert bound.workspace == "codebase"
    assert bound.expect_playbook == ""
    assert bound.expect_action == "DELEGATE"
    assert bound.expect_form == "prose"
    assert bound.expect_min_workers == 1
    assert bound.expect_max_recon_rounds == 1
    facets = by_key["discuss_arch_bug_maintain_facets"]
    assert facets.workspace == "empty"
    assert facets.expect_playbook == ""
    assert "DELEGATE" in facets.expect_action and "ASK" in facets.expect_action
    assert "DIRECT" not in facets.expect_action
    assert facets.expect_max_workers == 2
    assert facets.expect_min_workers == 1
    assert facets.expect_form == "prose"
    assert "写成文档" in facets.user_message
    identity = by_key["identity_who_are_you"]
    assert identity.expect_playbook == ""
    assert "DIRECT" in identity.expect_action and "ASK" in identity.expect_action
    compare = by_key["compare_three_js_frameworks"]
    assert compare.expect_playbook == ""
    assert compare.expect_action == "DELEGATE"
    assert compare.expect_min_workers == 3
    assert "React" in compare.user_message and "Vue" in compare.user_message
    assert "Svelte" in compare.user_message
    assert "讨论删除worker" in bound.user_message
    assert "行业实践" in bound.user_message


def test_codebase_fixture_has_real_volume():
    lint_codebase_fixture()


def test_colloquial_rejects_playbook_jargon():
    bad = replace(
        SCENARIOS[3],
        user_message="请用 playbook=code_audit 帮我审计",
    )
    with pytest.raises(EvalConfigError, match="提示词术语"):
        lint_scenarios((*SCENARIOS[:3], bad, *SCENARIOS[4:]))


def test_colloquial_rejects_jargon_in_prior_turns():
    base = next(s for s in SCENARIOS if s.prior_turns)
    bad = replace(
        base,
        prior_turns=(RoutingTurn(role="assistant", content="请调用 ask_user 确认"),),
    )
    patched = tuple(bad if s.key == base.key else s for s in SCENARIOS)
    with pytest.raises(EvalConfigError, match="提示词术语"):
        lint_scenarios(patched)


def test_lint_empty_playbook_requires_expect_action():
    bad = replace(SCENARIOS[0], expect_playbook="", expect_action="")
    with pytest.raises(EvalConfigError, match="expect_action"):
        lint_scenarios((bad, *SCENARIOS[1:]))


def test_lint_rejects_unknown_expect_playbook():
    bad = replace(SCENARIOS[0], expect_playbook="not_a_real_playbook")
    with pytest.raises(EvalConfigError, match="未知 expect_playbook"):
        lint_scenarios((bad, *SCENARIOS[1:]))


def test_lint_rejects_illegal_expect_action():
    bad = replace(SCENARIOS[0], expect_action="CHAT")
    with pytest.raises(EvalConfigError, match="expect_action 非法"):
        lint_scenarios((bad, *SCENARIOS[1:]))


def test_lint_requires_recon_round_cap_scenario():
    trimmed = tuple(s for s in SCENARIOS if s.expect_max_recon_rounds is None)
    with pytest.raises(EvalConfigError, match="expect_max_recon_rounds"):
        lint_scenarios(trimmed)


def test_lint_rejects_min_workers_above_max():
    bad = replace(
        next(s for s in SCENARIOS if s.key == "discuss_worker_params_industry"),
        expect_min_workers=4,
        expect_max_workers=2,
    )
    patched = tuple(bad if s.key == bad.key else s for s in SCENARIOS)
    with pytest.raises(EvalConfigError, match="expect_min_workers"):
        lint_scenarios(patched)


def test_named_playbook_strips_none():
    assert named_playbook("cite_write_review") == "cite_write_review"
    assert named_playbook("none") is None
    assert named_playbook("") is None
    assert named_playbook(None) is None


def test_parse_delegate_reads_intensity():
    raw = parse_delegate_rich(
        '{"playbook":"cite_write_review","playbook_args":{"topic":"待办","intensity":"lean"}}'
    )
    assert raw["playbook"] == "cite_write_review"
    assert raw["intensity"] == "lean"
    assert raw["task_count"] == 0
    assert raw["forms"] == []
    assert raw["form"] is None


def test_parse_delegate_reads_deliverable_form_and_max_workers():
    raw = parse_delegate_rich(
        '{"tasks":[{"role":"撰稿","task":"写 PRD","deliverable":{"form":"files"}}],'
        '"playbook_args":{"max_workers":1}}'
    )
    assert raw["playbook"] is None
    assert raw["task_count"] == 1
    assert raw["form"] == "files"
    assert raw["forms"] == ["files"]
    assert raw["max_workers"] == 1
    assert raw["tasks_preview"][0]["form"] == "files"


def test_classify_landing_variants():
    offered = True
    expected = classify_landing(
        action="DELEGATE", playbook="map_fanout", expect="map_fanout", offered=offered, task_count=0
    )
    assert expected["landing"] == "selected_expected"
    other = classify_landing(
        action="DELEGATE", playbook="cite_write_review", expect="map_fanout", offered=offered, task_count=0
    )
    assert other["landing"] == "selected_other"
    hand = classify_landing(
        action="DELEGATE", playbook=None, expect="map_fanout", offered=offered, task_count=1
    )
    assert hand["landing"] == "handwritten_tasks"
    ask = classify_landing(
        action="ASK", playbook=None, expect="map_fanout", offered=offered, task_count=0
    )
    assert ask["landing"] == "no_delegate"


def test_classify_landing_extended_observation():
    offered = True
    allowed = classify_landing(
        action="DIRECT",
        playbook=None,
        expect="map_fanout",
        offered=offered,
        task_count=0,
        expect_action="DIRECT|ASK",
    )
    assert allowed["landing"] == "allowed_action"
    duo = classify_landing(
        action="DELEGATE",
        playbook=None,
        expect="map_fanout",
        offered=offered,
        task_count=2,
        form="files",
        expect_action="DIRECT|ASK",
    )
    assert duo["landing"] == "files_duo"
    assert duo["files_duo"] is True
    one = classify_landing(
        action="DELEGATE",
        playbook=None,
        expect="",
        offered=offered,
        task_count=1,
        form="files",
        expect_action="DELEGATE",
        expect_max_workers=1,
        expect_form="files",
    )
    assert one["landing"] == "handwritten_expected"
    over = classify_landing(
        action="DELEGATE",
        playbook=None,
        expect="",
        offered=offered,
        task_count=2,
        form="files",
        expect_action="DELEGATE",
        expect_max_workers=1,
        expect_form="files",
    )
    assert over["landing"] == "files_duo"
    mismatch = classify_landing(
        action="DELEGATE",
        playbook=None,
        expect="",
        offered=offered,
        task_count=1,
        form="prose",
        expect_action="DELEGATE",
        expect_max_workers=1,
        expect_form="files",
    )
    assert mismatch["landing"] == "form_mismatch"
    recon = classify_landing(
        action="DELEGATE",
        playbook="map_fanout",
        expect="map_fanout",
        offered=offered,
        task_count=0,
        expect_action="DELEGATE",
        expect_form="prose",
        expect_min_workers=2,
        recon_rounds=3,
        expect_max_recon_rounds=1,
    )
    assert recon["landing"] == "recon_over"
    under = classify_landing(
        action="DELEGATE",
        playbook=None,
        expect="map_fanout",
        offered=offered,
        task_count=1,
        form="prose",
        expect_action="DELEGATE",
        expect_form="prose",
        expect_min_workers=2,
        expect_max_recon_rounds=1,
    )
    assert under["landing"] == "workers_under"
    facet_over = classify_landing(
        action="DELEGATE",
        playbook=None,
        expect="",
        offered=offered,
        task_count=3,
        form="prose",
        expect_action="DELEGATE|ASK",
        expect_form="prose",
        expect_min_workers=1,
        expect_max_workers=2,
    )
    assert facet_over["landing"] == "workers_over"
    assert facet_over["workers"] == 3
    brief_hand = classify_landing(
        action="DELEGATE",
        playbook=None,
        expect="map_fanout",
        offered=offered,
        task_count=2,
        form="prose",
        expect_action="DELEGATE",
        expect_form="prose",
        expect_min_workers=2,
        expect_max_recon_rounds=1,
        recon_rounds=1,
    )
    assert brief_hand["landing"] == "handwritten_expected"
    brief_named = classify_landing(
        action="DELEGATE",
        playbook="map_fanout",
        expect="map_fanout",
        offered=offered,
        task_count=0,
        expect_action="DELEGATE",
        expect_form="prose",
        expect_min_workers=2,
        expect_max_recon_rounds=1,
        recon_rounds=0,
    )
    assert brief_named["landing"] == "selected_expected"


def test_think_act_catches_named_playbook_then_ask_user():
    reasoning = (
        "这是成文专线，推荐 playbook=\"cite_write_review\"。\n"
        "我认为应该用 playbook=\"cite_write_review\"，intensity=lean。\n"
        "我直接 delegate cite_write_review。\n"
        "让我派工。"
    )
    mentions = extract_think_mentions(reasoning)
    assert "cite_write_review" in mentions["playbooks"]
    assert "lean" in mentions["intensities"]
    div = think_act_divergences(
        mentions, action="ASK", playbook=None, intensity=None
    )
    kinds = {(d["kind"], d["mentioned"]) for d in div}
    assert ("playbook", "cite_write_review") in kinds


def test_think_act_on_recorded_colloquial_excerpt():
    """手搓口语跑里抓到的形状：思考写具名 playbook，实际发卡。"""
    reasoning = (
        "用户要落盘成文。根据规则，明示成文 → "
        "推荐 playbook=\"cite_write_review\"。\n"
        "我直接 delegate cite_write_review。"
    )
    mentions = extract_think_mentions(reasoning)
    div = think_act_divergences(mentions, action="ASK", playbook=None, intensity=None)
    assert "cite_write_review" in mentions["playbooks"]
    assert any(
        d["kind"] == "playbook" and d["mentioned"] == "cite_write_review" for d in div
    )


def test_think_act_ignores_negated_playbook():
    reasoning = "不要用 playbook=cite_write_review，改走对话对齐。"
    mentions = extract_think_mentions(reasoning)
    assert "cite_write_review" not in mentions["playbooks"]
    assert think_act_divergences(mentions, action="DIRECT", playbook=None, intensity=None) == []


def test_aggregate_expresses_distribution():
    samples = []
    for i, action in enumerate(["DELEGATE", "DELEGATE", "DELEGATE", "ASK", "DIRECT"]):
        pb = "map_fanout" if action == "DELEGATE" else None
        landing = "selected_expected" if pb else "no_delegate"
        samples.append(
            {
                "ok": True,
                "action": action,
                "playbook": pb,
                "intensity": None,
                "delegated": action == "DELEGATE",
                "card_issued": action == "ASK",
                "outcome": {"landing": landing},
                "think_act_divergences": (
                    [{"kind": "playbook", "mentioned": "x", "actual": "y"}] if i == 4 else []
                ),
            }
        )
    agg = aggregate_samples(samples)
    assert agg["delegated"] == "3/5"
    assert agg["card_issued"] == "1/5"
    assert agg["expected_playbook"] == "3/5"
    assert agg["think_act_divergence"] == "1/5"
    assert agg["playbook_counts"]["map_fanout"] == 3


def test_diff_fingerprints_reports_changed_scenario():
    def _row(key: str, delegated_n: int) -> dict:
        samples = [
            {
                "ok": True,
                "action": "DELEGATE" if i < delegated_n else "ASK",
                "playbook": "cite_write_review" if i < delegated_n else None,
                "intensity": "lean" if i < delegated_n else None,
                "delegated": i < delegated_n,
                "card_issued": i >= delegated_n,
                "outcome": {"landing": "selected_expected" if i < delegated_n else "no_delegate"},
                "think_act_divergences": [],
            }
            for i in range(3)
        ]
        agg = aggregate_samples(samples)
        return {"key": key, "aggregate": agg, "fingerprint": landing_fingerprint(agg)}

    prev = {"scenarios": [_row("app_todo_web_must_run", 3)]}
    curr = [_row("app_todo_web_must_run", 1)]
    diff = diff_fingerprints(prev, curr)
    assert diff["available"] is True
    assert diff["n_changed"] == 1
    assert diff["changed"][0]["key"] == "app_todo_web_must_run"


def test_slim_baseline_drops_reasoning():
    report = {
        "meta": {"timestamp": "t", "samples": 3, "model": "m", "report_only": True},
        "scenarios": [
            {
                "key": "x",
                "phrasing": "colloquial",
                "expect_playbook": "",
                "fingerprint": {"n": 1, "delegated_n": 0},
                "aggregate": {"n": 1, "delegated": "0/1"},
                "samples": [{"reasoning": {"full": "secret"}}],
            }
        ],
    }
    slim = slim_baseline(report)
    blob = str(slim)
    assert "secret" not in blob
    assert slim["scenarios"][0]["key"] == "x"


def test_format_report_mentions_no_baseline():
    text = format_playbook_routing_report(
        {
            "meta": {"model": "x", "samples": 3, "cost_note": "note"},
            "scenarios": [],
            "diff": {"available": False},
        }
    )
    assert "无上次基线" in text
    assert "不卡门禁" in text


def test_cli_lint_only_exit_zero():
    assert main(["--playbook-routing", "--lint-only"]) == 0


def test_cli_report_only_does_not_red_on_miss(monkeypatch, tmp_path):
    async def _fake_run(**_kwargs):
        agg = aggregate_samples(
            [
                {
                    "ok": True,
                    "action": "ASK",
                    "playbook": None,
                    "intensity": None,
                    "delegated": False,
                    "card_issued": True,
                    "outcome": {"landing": "no_delegate"},
                    "think_act_divergences": [],
                }
            ]
        )
        return {
            "ok": True,
            "gate": False,
            "meta": {"model": "fake", "samples": 1, "tokens": {}, "cost_note": ""},
            "scenarios": [
                {
                    "key": "app_todo_web_must_run",
                    "phrasing": "colloquial",
                    "expect_playbook": "",
                    "aggregate": agg,
                    "fingerprint": agg["fingerprint"],
                }
            ],
            "diff": {"available": False, "changed": []},
        }

    monkeypatch.setattr("agentcore.evals.playbook_routing_loop.run_playbook_routing", _fake_run)
    out = tmp_path / "r.json"
    baseline = tmp_path / "b.json"
    code = main(
        [
            "--playbook-routing",
            "--out",
            str(out),
            "--baseline",
            str(baseline),
            "--update-baseline",
        ]
    )
    assert code == 0
    assert out.is_file()
    assert baseline.is_file()
    assert "secret" not in baseline.read_text(encoding="utf-8")


def test_loop_does_not_load_archive_scripts():
    """回归：决策环必须在 eval 包内，禁止运行时 exec 归档脚本。"""
    import inspect

    from agentcore.evals import playbook_routing_decision as decision
    from agentcore.evals import playbook_routing_loop as loop

    assert not hasattr(loop, "_archive")
    loop_src = inspect.getsource(loop)
    decision_src = inspect.getsource(decision)
    assert "spec_from_file_location" not in loop_src
    assert "exec_module" not in loop_src
    assert "probe_routing_think" not in loop_src
    assert "platform_llm_credentials(" not in loop_src
    assert "platform_llm_credentials(" not in decision_src


def test_execution_entry_assembles_surface_and_parses_delegate():
    """真正走到装配 + 一次决策：LLM 打桩，模块装载与参数解析必须真走。"""
    import asyncio
    import json

    from agentcore.evals.eval_modes import KNOWN_MODELS, resolve_profile_set
    from agentcore.evals.playbook_routing_loop import run_scripted_sample
    from agentcore.llm.provider.protocol import LLMResponse, TokenUsage, ToolCall, ToolCallFunction

    sc = next(s for s in SCENARIOS if s.key == "app_todo_website_usable")

    class _StubProvider:
        def __init__(self) -> None:
            self.requests: list = []

        async def complete(self, request):  # noqa: ANN001
            self.requests.append(request)
            assert request.tools, "CEO tool surface must be assembled before the first decision"
            names = [
                ((d.get("function") or {}).get("name") if isinstance(d, dict) else None)
                for d in (request.tools or [])
            ]
            assert "delegate" in names
            return LLMResponse(
                content="先派团队。",
                reasoning_content="手写 tasks 从零搭可跑待办。",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        function=ToolCallFunction(
                            name="delegate",
                            arguments=json.dumps(
                                {
                                    "tasks": [
                                        {
                                            "role": "实现",
                                            "task": "从零搭待办 SPA，打开就能用",
                                        }
                                    ],
                                },
                                ensure_ascii=False,
                            ),
                        ),
                    )
                ],
                usage=TokenUsage(input_tokens=11, output_tokens=7),
            )

    stub = _StubProvider()
    profiles = resolve_profile_set(
        "economy", custom_modes={}, ceiling=frozenset(KNOWN_MODELS)
    )
    packed = asyncio.run(
        run_scripted_sample(
            stub,
            sc,
            profiles=profiles,
            model=profiles.model_for("chat"),
            rounds=2,
        )
    )
    assert packed["ok"] is True, packed.get("error")
    assert stub.requests, "provider.complete must be called"
    assert packed["action"] == "DELEGATE"
    assert packed["playbook"] is None
    assert packed["task_count"] == 1
    assert packed["delegated"] is True
    assert packed["tool_surface"]["offered"] is True
    enum = packed["tool_surface"].get("playbook_enum") or []
    assert "cite_write_review" in enum
    assert "map_fanout" in enum
    assert "build_app" not in enum
    assert packed["think_act_divergences"] == []


def test_prior_turns_reach_the_model():
    import asyncio

    from agentcore.evals.eval_modes import KNOWN_MODELS, resolve_profile_set
    from agentcore.evals.playbook_routing_loop import run_scripted_sample
    from agentcore.llm.provider.protocol import LLMResponse, TokenUsage

    sc = next(s for s in SCENARIOS if s.key == "discuss_license_round2_short_answers")

    class _StubProvider:
        def __init__(self) -> None:
            self.requests: list = []

        async def complete(self, request):  # noqa: ANN001
            self.requests.append(request)
            return LLMResponse(
                content="限制和风险如下。",
                reasoning_content="桌上短答即可",
                tool_calls=[],
                usage=TokenUsage(input_tokens=4, output_tokens=2),
            )

    stub = _StubProvider()
    profiles = resolve_profile_set(
        "economy", custom_modes={}, ceiling=frozenset(KNOWN_MODELS)
    )
    packed = asyncio.run(
        run_scripted_sample(
            stub,
            sc,
            profiles=profiles,
            model=profiles.model_for("chat"),
            rounds=2,
        )
    )
    assert packed["ok"] is True, packed.get("error")
    assert stub.requests
    texts = [str(getattr(m, "content", "") or "") for m in stub.requests[0].messages]
    assert sc.prior_turns[0].content in texts
    assert sc.prior_turns[1].content in texts
    assert sc.user_message in texts
    assert packed["action"] == "DIRECT"
