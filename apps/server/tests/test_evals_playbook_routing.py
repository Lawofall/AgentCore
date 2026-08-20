"""playbook 路由回归：零 LLM 校验落点分类 / think-act / 基线 diff / 夹具体量."""

from __future__ import annotations

from dataclasses import replace

import pytest

from agentcore.evals.__main__ import main
from agentcore.evals.playbook_routing import (
    SCENARIOS,
    aggregate_samples,
    classify_landing,
    diff_fingerprints,
    extract_think_mentions,
    format_playbook_routing_report,
    landing_fingerprint,
    lint_codebase_fixture,
    lint_scenarios,
    named_playbook,
    parse_delegate_rich,
    slim_baseline,
    think_act_divergences,
)
from agentcore.evals.types import EvalConfigError


def test_scenarios_lint_ok():
    lint_scenarios(SCENARIOS)
    assert sum(1 for s in SCENARIOS if s.phrasing == "colloquial") >= 6
    assert sum(1 for s in SCENARIOS if s.phrasing == "textbook") >= 3
    assert any(s.workspace == "codebase" for s in SCENARIOS)


def test_codebase_fixture_has_real_volume():
    lint_codebase_fixture()


def test_colloquial_rejects_playbook_jargon():
    bad = replace(
        SCENARIOS[3],
        user_message="请用 playbook=code_audit 帮我审计",
    )
    with pytest.raises(EvalConfigError, match="提示词术语"):
        lint_scenarios((*SCENARIOS[:3], bad, *SCENARIOS[4:]))


def test_named_playbook_strips_none():
    assert named_playbook("build_app") == "build_app"
    assert named_playbook("none") is None
    assert named_playbook("") is None
    assert named_playbook(None) is None


def test_parse_delegate_reads_intensity():
    raw = parse_delegate_rich(
        '{"playbook":"build_website","playbook_args":{"topic":"待办","intensity":"solo"}}'
    )
    assert raw["playbook"] == "build_website"
    assert raw["intensity"] == "solo"
    assert raw["task_count"] == 0


def test_classify_landing_variants():
    offered = True
    expected = classify_landing(
        action="DELEGATE", playbook="code_audit", expect="code_audit", offered=offered, task_count=0
    )
    assert expected["landing"] == "selected_expected"
    other = classify_landing(
        action="DELEGATE", playbook="build_website", expect="build_app", offered=offered, task_count=0
    )
    assert other["landing"] == "selected_other"
    hand = classify_landing(
        action="DELEGATE", playbook=None, expect="code_audit", offered=offered, task_count=1
    )
    assert hand["landing"] == "handwritten_tasks"
    ask = classify_landing(
        action="ASK", playbook=None, expect="build_app", offered=offered, task_count=0
    )
    assert ask["landing"] == "no_delegate"


def test_think_act_catches_build_app_then_ask_user():
    reasoning = (
        "这是绿场 SPA，推荐 playbook=\"build_app\"。\n"
        "我认为应该用 playbook=\"build_app\"，intensity=lean。\n"
        "我直接 delegate build_app。\n"
        "让我派工。"
    )
    mentions = extract_think_mentions(reasoning)
    assert "build_app" in mentions["playbooks"]
    assert "lean" in mentions["intensities"]
    div = think_act_divergences(
        mentions, action="ASK", playbook=None, intensity=None
    )
    kinds = {(d["kind"], d["mentioned"]) for d in div}
    assert ("playbook", "build_app") in kinds


def test_think_act_on_recorded_colloquial_excerpt():
    """手搓口语跑里抓到的形状：思考写 build_app，实际发卡。"""
    reasoning = (
        "这是一个绿场 SPA 应用。根据规则，真 SPA / 用户明示完整可跑 → "
        "推荐 playbook=\"build_app\"。\n"
        "我直接 delegate build_app。"
    )
    mentions = extract_think_mentions(reasoning)
    div = think_act_divergences(mentions, action="ASK", playbook=None, intensity=None)
    assert "build_app" in mentions["playbooks"]
    assert any(d["kind"] == "playbook" and d["mentioned"] == "build_app" for d in div)


def test_think_act_ignores_negated_playbook():
    reasoning = "不要用 playbook=research_report，改走对话对齐。"
    mentions = extract_think_mentions(reasoning)
    assert "research_report" not in mentions["playbooks"]
    assert think_act_divergences(mentions, action="DIRECT", playbook=None, intensity=None) == []


def test_aggregate_expresses_distribution():
    samples = []
    for i, action in enumerate(["DELEGATE", "DELEGATE", "DELEGATE", "ASK", "DIRECT"]):
        pb = "parallel_brief" if action == "DELEGATE" else None
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
    assert agg["playbook_counts"]["parallel_brief"] == 3


def test_diff_fingerprints_reports_changed_scenario():
    def _row(key: str, delegated_n: int) -> dict:
        samples = [
            {
                "ok": True,
                "action": "DELEGATE" if i < delegated_n else "ASK",
                "playbook": "build_app" if i < delegated_n else None,
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
                "expect_playbook": "build_app",
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
                    "expect_playbook": "build_app",
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
                reasoning_content='决定 playbook="build_app" intensity=lean',
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        function=ToolCallFunction(
                            name="delegate",
                            arguments=json.dumps(
                                {
                                    "playbook": "build_app",
                                    "playbook_args": {
                                        "app": "待办清单",
                                        "intensity": "lean",
                                    },
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
    assert packed["playbook"] == "build_app"
    assert packed["intensity"] == "lean"
    assert packed["delegated"] is True
    assert packed["tool_surface"]["offered"] is True
    assert "build_app" in (packed["tool_surface"].get("playbook_enum") or [])
    assert packed["think_act_divergences"] == []
