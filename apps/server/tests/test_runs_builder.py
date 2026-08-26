"""Tests for build_run_plan: raw delegate args → RunPlan (第一阶段内联角色版).

Covers the single/parallel/DAG shape inference, run-id minting (flat declared id
or numbering vs DAG namespacing + edge rewrite), inline-role field mapping, the
fan-out sibling summary, the tool allow-list filter, knob validation, and the
reject-on-error / reject-when-none-valid contract.
"""

import agentcore.runtime.runs.builder as builder_mod
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.types import RunKind
from agentcore.workspace.stage_dirs import DRAFTS_DIR
from tests.conftest import LogSpy


def test_single_task_one_node():
    plan, errs = build_run_plan([{"role": "研究员", "task": "调研X"}], id_prefix="t")
    assert errs == []
    assert len(plan.nodes) == 1
    n = plan.nodes[0]
    assert n.run_id == "t_1"
    assert n.agent_id == "t_1"
    assert n.agent_name == "研究员"
    assert n.role == "研究员"
    assert n.task == "调研X"
    assert n.depends_on == []
    assert n.kind is RunKind.AGENT
    assert n.sibling_summary == ""


def test_parallel_batch_sets_sibling_summary():
    plan, errs = build_run_plan(
        [{"role": "A", "task": "做A"}, {"role": "B", "task": "做B"}], id_prefix="t"
    )
    assert errs == []
    a, b = plan.nodes
    assert (a.run_id, b.run_id) == ("t_1", "t_2")
    assert "B" in a.sibling_summary and "做B" in a.sibling_summary
    assert "A" in b.sibling_summary and "做A" in b.sibling_summary
    assert len(plan.waves()) == 1


def test_counter_start_offsets_ids():
    plan, _ = build_run_plan([{"role": "A", "task": "a"}], id_prefix="t", counter_start=5)
    assert plan.nodes[0].run_id == "t_6"


def test_inline_model_override_passthrough():
    """显式 model 覆写透传到 RunSpec.model（真·多模型辩手）：set→透传、缺省→空、非字符串→空。

    ``provider/model`` 前缀（如 doubao/...）须原样落到 RunSpec.model，执行器据此覆写
    profile.model 并经路由器分发；普通 worker 不带 model → 空 = 按 tier 解析默认模型。
    """
    plan, errs = build_run_plan(
        [
            {"role": "A", "task": "a", "model": "doubao/doubao-seed-2-1-turbo-260628"},
            {"role": "B", "task": "b"},
            {"role": "C", "task": "c", "model": 123},
        ],
        id_prefix="t",
    )
    assert errs == []
    a, b, c = plan.nodes
    assert a.model == "doubao/doubao-seed-2-1-turbo-260628"
    assert b.model == ""
    assert c.model == ""


def test_dag_fanout_siblings_get_sibling_summary():
    # The fix: parallel researchers that fan out from the same point (here both have
    # no deps → same dep set) now see each other — they used to get nothing and ran
    # blind/overlapping. The downstream writer fans in alone (its own dep set), so it
    # gets NO sibling (it receives r1/r2 via depends_on instead).
    tasks = [
        {"id": "r1", "role": "调研员A", "task": "查行业数据"},
        {"id": "r2", "role": "调研员B", "task": "查竞品案例"},
        {"id": "w", "role": "写手", "task": "汇总成稿", "depends_on": ["r1", "r2"]},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs == []
    r1, r2, w = plan.by_id("t_r1"), plan.by_id("t_r2"), plan.by_id("t_w")
    assert "调研员B" in r1.sibling_summary and "查竞品案例" in r1.sibling_summary
    assert "调研员A" in r2.sibling_summary and "查行业数据" in r2.sibling_summary
    # A node never lists itself, and the lone writer has no fan-out peer.
    assert "调研员A" not in r1.sibling_summary
    assert w.sibling_summary == ""


def test_dag_shared_upstream_fanout_are_siblings():
    # Siblings = same dep set, not just「no deps」: two nodes that both depend on the
    # SAME upstream fan out together and must see each other.
    tasks = [
        {"id": "u", "role": "设计", "task": "出规格"},
        {"id": "a", "role": "前端", "task": "实现页面", "depends_on": ["u"]},
        {"id": "b", "role": "后端", "task": "实现接口", "depends_on": ["u"]},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs == []
    a, b = plan.by_id("t_a"), plan.by_id("t_b")
    assert "后端" in a.sibling_summary and "前端" in b.sibling_summary


def test_dag_independent_chains_in_same_wave_are_not_siblings():
    # Narrower than「same wave」on purpose: s2 (deps [s1]) and u2 (deps [u1]) land in
    # the same topological wave but belong to independent chains → NOT siblings, so a
    # worker isn't told about unrelated concurrent work and branch independence holds.
    tasks = [
        {"id": "s1", "role": "研究员", "task": "调研"},
        {"id": "s2", "role": "写手", "task": "撰写", "depends_on": ["s1"]},
        {"id": "u1", "role": "采购", "task": "比价"},
        {"id": "u2", "role": "出纳", "task": "付款", "depends_on": ["u1"]},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs == []
    assert plan.by_id("t_s2").sibling_summary == ""
    assert plan.by_id("t_u2").sibling_summary == ""
    # The two roots DO share the empty dep set (a root-level flat fan-out), so they
    # are siblings — consistent with a flat batch.
    assert "采购" in plan.by_id("t_s1").sibling_summary
    assert "研究员" in plan.by_id("t_u1").sibling_summary


def test_dag_suspect_missing_dep_warns_when_task_mentions_upstream(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(builder_mod, "logger", spy)
    tasks = [
        {"id": "r1", "role": "研究员", "task": "调研"},
        {
            "id": "w",
            "role": "写手",
            "task": "基于上游产出撰写成稿",
        },
        {"id": "x", "role": "其他", "task": "收尾", "depends_on": ["r1"]},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs == []
    assert len(plan.nodes) == 3
    kw = spy.get("builder.suspect_missing_dep")
    assert kw["run_id"] == "t_w"
    assert kw["role"] == "写手"
    assert "depends_on" in kw["hint"]


def test_dag_suspect_missing_dep_warns_on_read_upstream_request(monkeypatch):
    """真像漏挂：请读取上游产出 + 空依赖 → 仍告。"""
    spy = LogSpy()
    monkeypatch.setattr(builder_mod, "logger", spy)
    plan, errs = build_run_plan(
        [
            {"id": "r1", "role": "研究员", "task": "调研"},
            {"id": "w", "role": "写手", "task": "请读取上游产出后撰写"},
            # 另有 depends_on 边，使本批走 DAG 建图路径（advisory 挂在该路径）。
            {"id": "x", "role": "其他", "task": "收尾", "depends_on": ["r1"]},
        ],
        id_prefix="t",
    )
    assert errs == []
    kw = spy.get("builder.suspect_missing_dep")
    assert kw["run_id"] == "t_w"
    assert any("depends_on 为空" in a for a in plan.advisories)


def test_dag_suspect_missing_dep_silent_for_seed_as_upstream(monkeypatch):
    """起点种子自称「作为上游产出」且 depends_on 空 → 不再误报。"""
    spy = LogSpy()
    monkeypatch.setattr(builder_mod, "logger", spy)
    plan, errs = build_run_plan(
        [
            {
                "id": "seed",
                "role": "研究员",
                "task": "作为上游产出调研纪要，供后续节点使用",
            },
            {"id": "w", "role": "写手", "task": "撰写成稿", "depends_on": ["seed"]},
        ],
        id_prefix="t",
    )
    assert errs == []
    assert not any(name == "builder.suspect_missing_dep" for name, _ in spy.events)
    assert plan.advisories == []


def test_dag_suspect_missing_dep_silent_on_negated_hint(monkeypatch):
    """否定消费向措辞（不基于上游产出）不告。"""
    spy = LogSpy()
    monkeypatch.setattr(builder_mod, "logger", spy)
    plan, errs = build_run_plan(
        [
            {"id": "a", "role": "独立", "task": "不基于上游产出，自行调研"},
            {"id": "b", "role": "收尾", "task": "汇总", "depends_on": ["a"]},
        ],
        id_prefix="t",
    )
    assert errs == []
    assert not any(name == "builder.suspect_missing_dep" for name, _ in spy.events)
    assert plan.advisories == []


def test_dag_suspect_missing_dep_silent_when_dep_declared(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(builder_mod, "logger", spy)
    tasks = [
        {"id": "r1", "role": "研究员", "task": "调研"},
        {
            "id": "w",
            "role": "写手",
            "task": "基于上游产出撰写成稿",
            "depends_on": ["r1"],
        },
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs == []
    assert not any(name == "builder.suspect_missing_dep" for name, _ in spy.events)


def test_dag_linear_chain_has_no_siblings():
    # A pure A→B→C pipeline gives every node a unique dep set, so none has a peer.
    tasks = [
        {"id": "a", "role": "A", "task": "a"},
        {"id": "b", "role": "B", "task": "b", "depends_on": ["a"]},
        {"id": "c", "role": "C", "task": "c", "depends_on": ["b"]},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs == []
    assert all(n.sibling_summary == "" for n in plan.nodes)


def test_sibling_summary_task_excerpt_capped():
    # A long sibling task is truncated to the per-sibling cap with an ellipsis, so a
    # wide fan-out's awareness block can't blow up a worker's context.
    long_task = "x" * 500
    plan, errs = build_run_plan(
        [{"role": "A", "task": long_task}, {"role": "B", "task": "短"}], id_prefix="t"
    )
    assert errs == []
    b = plan.nodes[1]
    assert "x" * 150 in b.sibling_summary
    assert "x" * 200 not in b.sibling_summary
    assert b.sibling_summary.endswith("…")


def test_sibling_summary_uses_task_only():
    # Scope is always the task instruction (objective / deliverable.name removed).
    plan, errs = build_run_plan(
        [
            {
                "role": "后端",
                "task": "实现下单接口",
                "objective": "负责服务端 API",
                "deliverable": {"name": "OpenAPI 契约 + 实现", "form": "files"},
            },
            {
                "role": "前端",
                "task": "做下单页",
                "objective": "负责下单页面",
                "deliverable": {"name": "可交互页面", "form": "files"},
            },
        ],
        id_prefix="t",
    )
    assert errs == []
    backend, frontend = plan.nodes
    assert "实现下单接口" in frontend.sibling_summary
    assert "负责服务端 API" not in frontend.sibling_summary
    assert "预期产出" not in frontend.sibling_summary
    assert "做下单页" in backend.sibling_summary


def test_sibling_summary_falls_back_to_task_without_objective():
    # task instruction is the scope so a peer is never blank.
    plan, errs = build_run_plan(
        [{"role": "A", "task": "做A"}, {"role": "B", "task": "做B"}], id_prefix="t"
    )
    assert errs == []
    a = plan.nodes[0]
    assert a.sibling_summary == "- B：做B"


def test_sibling_summary_ignores_deleted_deliverable_name():
    # Deleted name key is not consumed; sibling summary is role + task only.
    plan, errs = build_run_plan(
        [
            {"role": "A", "task": "a", "deliverable": {"name": "y" * 300}},
            {"role": "B", "task": "b"},
        ],
        id_prefix="t",
    )
    assert errs == []
    b = plan.nodes[1]
    assert b.sibling_summary == "- A：a"
    assert plan.nodes[0].deliverable is not None
    assert plan.nodes[0].deliverable.form == "files"


def test_dag_namespaces_ids_and_rewrites_edges():
    tasks = [
        {"id": "s1", "role": "A", "task": "a"},
        {"id": "s2", "role": "B", "task": "b", "depends_on": ["s1"]},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs == []
    assert [n.run_id for n in plan.nodes] == ["t_s1", "t_s2"]
    assert plan.nodes[1].depends_on == ["t_s1"]
    assert [[n.run_id for n in w] for w in plan.waves()] == [["t_s1"], ["t_s2"]]


def test_require_upstream_parsed_onto_spec():
    plan, errs = build_run_plan(
        [
            {"id": "a", "role": "A", "task": "a"},
            {
                "id": "s",
                "role": "汇总",
                "task": "合",
                "depends_on": ["a"],
                "require_upstream": True,
            },
        ],
        id_prefix="t",
    )
    assert errs == []
    assert plan.by_id("t_s").require_upstream is True
    assert plan.by_id("t_a").require_upstream is False


def test_empty_tasks_is_error():
    plan, errs = build_run_plan([])
    assert errs
    assert not plan.nodes


def test_all_invalid_flat_is_error():
    # Two role-only rows cannot coalesce into a node (no task anywhere).
    plan, errs = build_run_plan([{"role": "A"}, {"role": "B"}], id_prefix="t")
    assert errs
    assert not plan.nodes


def test_role_only_plus_task_only_coalesces_to_one_node():
    # The former "all invalid" fixture is exactly the split the model emits — coalesce it.
    plan, errs = build_run_plan([{"role": "A"}, {"task": "x"}], id_prefix="t")
    assert errs == []
    assert len(plan.nodes) == 1
    assert plan.nodes[0].role == "A" and plan.nodes[0].task == "x"


def test_dag_missing_role_collects_error():
    tasks = [
        {"id": "s1", "task": "a"},
        {"id": "s2", "role": "B", "task": "b", "depends_on": ["s1"]},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert any("s1" in e for e in errs)


def test_dag_cycle_is_error():
    tasks = [
        {"id": "s1", "role": "A", "task": "a", "depends_on": ["s2"]},
        {"id": "s2", "role": "B", "task": "b", "depends_on": ["s1"]},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs
    assert any("cycle" in e for e in errs)


def test_tools_declaration_ignored_true_pure_c():
    """真纯丙：CEO/入参填 tools 不再写入 RunSpec 白名单。"""
    plan, _ = build_run_plan(
        [{"role": "A", "task": "a", "tools": ["web_search", "ghost"]}],
        id_prefix="t",
        valid_tools={"web_search"},
    )
    assert plan.nodes[0].tools is None


def test_omitted_tools_means_no_restriction():
    # Fail-safe default: omitting ``tools`` must leave the worker UNrestricted
    # (None → react_loop offers all team tools), NOT stranded tool-less ([]). This is
    # the root fix for the "worker dumps file content as text, workspace stays empty,
    # CEO hallucinates success" bug.
    plan, _ = build_run_plan(
        [{"role": "A", "task": "a"}], id_prefix="t", valid_tools={"web_search"}
    )
    assert plan.nodes[0].tools is None


def test_all_invalid_tools_falls_back_to_no_restriction():
    # 真纯丙：任意声明（含未知名）一律忽略 → None。
    plan, _ = build_run_plan(
        [{"role": "A", "task": "a", "tools": ["ghost", "phantom"]}],
        id_prefix="t",
        valid_tools={"web_search"},
    )
    assert plan.nodes[0].tools is None


def test_explicit_empty_tools_is_no_restriction():
    # An explicit empty list is meaningless for a worker (a tool-less worker can do
    # nothing), so it too means "no restriction", not "no tools".
    plan, _ = build_run_plan([{"role": "A", "task": "a", "tools": []}], id_prefix="t")
    assert plan.nodes[0].tools is None


# --- 辩论/审查 呈现标记 (前端UX设计.md §四: stance/group, display-only) -----------


def test_stance_and_group_parsed_onto_spec():
    plan, _ = build_run_plan(
        [
            {"role": "正方", "task": "支持", "stance": "pro", "group": "g1"},
            {"role": "反方", "task": "反对", "stance": "con", "group": "g1"},
        ],
        id_prefix="t",
    )
    a, b = plan.nodes
    assert (a.stance, a.group) == ("pro", "g1")
    assert (b.stance, b.group) == ("con", "g1")


def test_invalid_stance_dropped():
    # Lenient like tier/effort: an unknown side leaves no tag (no debate signal).
    plan, _ = build_run_plan([{"role": "A", "task": "a", "stance": "maybe"}], id_prefix="t")
    assert plan.nodes[0].stance == ""


def test_group_trimmed_and_tags_default_blank():
    plan, _ = build_run_plan(
        [
            {"role": "A", "task": "a", "stance": "pro", "group": "  g  "},
            {"role": "B", "task": "b"},
        ],
        id_prefix="t",
    )
    assert plan.nodes[0].group == "g"
    # An ordinary task carries no tags (守住「形状是数据不是模式」: a debate is just
    # 普通并行 + a presentation hint, so an untagged batch is byte-identical to before).
    assert plan.nodes[1].stance == "" and plan.nodes[1].group == ""


def test_stance_parsed_on_dag_step():
    tasks = [
        {"id": "s1", "role": "正方", "task": "支持", "stance": "pro"},
        {"id": "s2", "role": "反方", "task": "反对", "stance": "con", "depends_on": ["s1"]},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs == []
    assert plan.by_id("t_s1").stance == "pro"
    assert plan.by_id("t_s2").stance == "con"


def test_round_parsed_onto_spec():
    # 真·多轮辩论 (前端UX设计.md §四): round 标轮次, display-only, 与 stance/group 正交.
    plan, _ = build_run_plan(
        [
            {"role": "正方", "task": "r1", "stance": "pro", "round": 1},
            {"role": "正方", "task": "r2", "stance": "pro", "round": 2},
        ],
        id_prefix="t",
    )
    a, b = plan.nodes
    assert a.round == 1
    assert b.round == 2


def test_invalid_round_dropped():
    # Lenient like stance: 非正整数 / 非 int / None 都落 0 (无多轮信号). bool 尤其要挡——
    # True 是 int 子类, 不可被当成「第 1 轮」.
    plan, _ = build_run_plan(
        [
            {"role": "A", "task": "zero", "round": 0},
            {"role": "B", "task": "neg", "round": -2},
            {"role": "C", "task": "str", "round": "2"},
            {"role": "D", "task": "boolean", "round": True},
            {"role": "E", "task": "none"},
        ],
        id_prefix="t",
    )
    assert [n.round for n in plan.nodes] == [0, 0, 0, 0, 0]


# --- 结构化挂起 2a (checkpoint_after, 计划期挂起标记) -------------------------------


def test_checkpoint_after_parsed_onto_spec():
    # 计划期挂起标记: 宽松读取 (bool(...)), WaveScheduler 据此在节点后波间挂起.
    plan, _ = build_run_plan(
        [
            {"role": "A", "task": "a", "checkpoint_after": True},
            {"role": "B", "task": "b"},
        ],
        id_prefix="t",
    )
    # An untagged node defaults False, so a plan with no checkpoint is byte-identical.
    assert plan.nodes[0].checkpoint_after is True
    assert plan.nodes[1].checkpoint_after is False


def test_checkpoint_after_parsed_on_dag_step():
    tasks = [
        {"id": "s1", "role": "A", "task": "a", "checkpoint_after": True},
        {"id": "s2", "role": "B", "task": "b", "depends_on": ["s1"]},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs == []
    assert plan.by_id("t_s1").checkpoint_after is True
    assert plan.by_id("t_s2").checkpoint_after is False


def test_checkpoint_after_truthy_coerced():
    # Lenient like the other flags: any falsy value (missing / 0 / "") → False.
    plan, _ = build_run_plan(
        [
            {"role": "A", "task": "a", "checkpoint_after": 0},
            {"role": "B", "task": "b", "checkpoint_after": ""},
        ],
        id_prefix="t",
    )
    assert plan.nodes[0].checkpoint_after is False
    assert plan.nodes[1].checkpoint_after is False


def test_dag_invalid_on_failure_falls_back_to_default():
    tasks = [
        {"id": "s1", "role": "A", "task": "a"},
        {"id": "s2", "role": "B", "task": "b", "depends_on": ["s1"], "on_failure": "explode"},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs == []
    assert plan.by_id("t_s2").policy.on_failure == "retry"


def test_deliverable_parsed_onto_policy():
    plan, _ = build_run_plan(
        [
            {
                "role": "A",
                "task": "a",
                "deliverable": {
                    "required_sections": ["结论", "  "],  # blank dropped
                    "must_contain": ["风险"],  # deleted — ignored
                    "min_length": 100,  # deleted — ignored
                    "must_contain_soft": True,  # deleted — ignored
                    "output_format": "json",
                    "strict": True,
                },
            }
        ],
        id_prefix="t",
    )
    c = plan.nodes[0].deliverable
    assert c is not None
    assert c.required_sections == ["结论"]
    assert c.output_format == "json"
    assert c.strict is True
    assert not hasattr(c, "must_contain")
    assert not hasattr(c, "min_length")
    assert not hasattr(c, "must_contain_soft")


def test_no_deliverable_defaults_to_files_with_drafts():
    plan, _ = build_run_plan([{"role": "A", "task": "a"}], id_prefix="t")
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.form == "files"
    assert d.artifact_dir == DRAFTS_DIR


def test_empty_deliverable_object_defaults_to_files():
    plan, _ = build_run_plan(
        [{"role": "A", "task": "a", "deliverable": {}}], id_prefix="t"
    )
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.form == "files"
    assert d.artifact_dir == DRAFTS_DIR


def test_deliverable_block_with_internal_knob_still_files():
    plan, _ = build_run_plan(
        [{"role": "A", "task": "a", "deliverable": {"strict": True}}], id_prefix="t"
    )
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.form == "files"
    assert d.strict is True


def test_requires_files_alone_defaults_to_files():
    # Deleted requires_files key is not consumed; omitted form still files.
    plan, _ = build_run_plan(
        [{"role": "A", "task": "a", "deliverable": {"requires_files": True}}], id_prefix="t"
    )
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.form == "files"


def test_requires_files_false_alone_defaults_to_files():
    plan, _ = build_run_plan(
        [{"role": "A", "task": "a", "deliverable": {"requires_files": False}}], id_prefix="t"
    )
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.form == "files"


def test_artifacts_parsed_without_requires_files_backfill():
    plan, _ = build_run_plan(
        [
            {
                "role": "集成",
                "task": "收口",
                "deliverable": {"artifacts": ["README.md", "examples/", "pkg/**/*.py"]},
            }
        ],
        id_prefix="t",
    )
    d = plan.nodes[0].deliverable
    assert d is not None
    assert d.artifacts == ["README.md", "examples/", "pkg/**/*.py"]
    assert not hasattr(d, "requires_files")


def test_dag_step_deliverable_parsed_independently():
    tasks = [
        {"id": "s1", "role": "A", "task": "a", "deliverable": {"form": "prose"}},
        {"id": "s2", "role": "B", "task": "b", "depends_on": ["s1"]},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs == []
    assert plan.by_id("t_s1").deliverable.form == "prose"
    assert plan.by_id("t_s2").deliverable is not None
    assert plan.by_id("t_s2").deliverable.form == "files"


def test_prose_with_downstream_keeps_form():
    """交付契约是唯一真理源：prose∧有下游不再抬 min。"""
    plan, errs = build_run_plan(
        [
            {
                "id": "diagnose",
                "role": "诊断员",
                "task": "短诊断",
                "deliverable": {"form": "prose"},
            },
            {
                "id": "patch",
                "role": "修补员",
                "task": "修补",
                "depends_on": ["diagnose"],
                "deliverable": {"form": "files"},
            },
        ],
        id_prefix="t",
    )
    assert errs == []
    d = plan.by_id("t_diagnose").deliverable
    assert d is not None
    assert d.form == "prose"
    assert not hasattr(d, "min_length")


def test_deliverable_invalid_output_format_falls_back_to_text():
    plan, _ = build_run_plan(
        [
            {
                "role": "A",
                "task": "a",
                "deliverable": {"output_format": "xml", "form": "prose"},
            }
        ],
        id_prefix="t",
    )
    assert plan.nodes[0].deliverable.output_format == "text"


# --- 阶段2 嵌套子任务: tree-position stamping (delegation on by default) --------


def test_defaults_top_level_depth_one_parent_none():
    # The common caller (CEO delegate) makes depth-1 workers parented to the root.
    # Delegation is on by default within the depth cap — there is no per-node flag.
    plan, _ = build_run_plan([{"role": "A", "task": "a"}], id_prefix="t")
    n = plan.nodes[0]
    assert n.depth == 1
    assert n.parent_run_id is None


def test_stamps_parent_and_depth_on_flat_batch():
    plan, _ = build_run_plan(
        [{"role": "A", "task": "a"}, {"role": "B", "task": "b"}],
        id_prefix="t",
        parent_run_id="cap",
        depth=2,
    )
    assert all(n.parent_run_id == "cap" and n.depth == 2 for n in plan.nodes)


def test_stamps_parent_and_depth_on_dag_batch():
    tasks = [
        {"id": "s1", "role": "A", "task": "a"},
        {"id": "s2", "role": "B", "task": "b", "depends_on": ["s1"]},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t", parent_run_id="cap", depth=2)
    assert errs == []
    assert all(n.parent_run_id == "cap" and n.depth == 2 for n in plan.nodes)


def test_over_max_tasks_rejects_entire_batch():
    from agentcore.runtime.runs.constants import MAX_DELEGATION_TASKS

    n = MAX_DELEGATION_TASKS + 1
    tasks = [{"role": f"R{i}", "task": f"t{i}"} for i in range(n)]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs
    # 拒绝回执要给出可照做的分批指引（本次传上限个、其余下次 delegate 再传），而非只报一句超限
    # ——否则 CEO 撞上限后得整轮重规划（trace 4d715ea0 的浪费来源）。
    msg = errs[0]
    assert str(n) in msg and "超过" in msg
    assert "delegate" in msg and "分" in msg
    assert not plan.nodes


def test_over_max_tasks_dag_batch_gives_dependency_aware_guidance():
    # 有依赖批超限：不能按数量硬切，回执须给依赖感知的分批指引（提到 depends_on 跨批衔接）。
    from agentcore.runtime.runs.constants import MAX_DELEGATION_TASKS

    n = MAX_DELEGATION_TASKS + 1
    tasks = [
        {"id": f"n{i}", "role": f"R{i}", "task": f"t{i}", "depends_on": ["n0"] if i else []}
        for i in range(n)
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs
    msg = errs[0]
    assert "超过" in msg and "depends_on" in msg
    assert not plan.nodes


def test_coalesce_split_id_task_rows_counts_real_nodes():
    """Models sometimes emit id 与 role/task 拆成两条 → 计数翻倍撞上限；须按真实节点计。"""
    from agentcore.runtime.runs.builder import coalesce_split_tasks
    from agentcore.runtime.runs.constants import MAX_DELEGATION_TASKS

    # 10 intended workers as 15 raw rows (5 id-stubs + 10 complete) — classic inflate.
    raw: list = []
    for i in range(5):
        raw.append({"id": f"w{i}"})
        raw.append({"role": f"调研{i}", "task": f"查{i}", "depends_on": []})
    for i in range(5, 10):
        raw.append({"id": f"w{i}", "role": f"调研{i}", "task": f"查{i}", "depends_on": []})
    assert len(raw) == 15
    coalesced = coalesce_split_tasks(raw)
    assert len(coalesced) == 10
    assert all(c.get("id") and c.get("role") and c.get("task") for c in coalesced)

    plan, errs = build_run_plan(raw, id_prefix="t")
    assert errs == []
    assert len(plan.nodes) == 10
    # Still under the raised cap; the point is we did not reject as「15 > 10」.
    assert len(plan.nodes) <= MAX_DELEGATION_TASKS


def test_coalesce_role_only_then_task_only_pair():
    from agentcore.runtime.runs.builder import coalesce_split_tasks

    raw = [
        {"id": "a", "role": "研究员"},
        {"task": "调研 OpenAI", "depends_on": []},
        {"id": "b", "role": "写手", "task": "汇总", "depends_on": ["a"]},
    ]
    out = coalesce_split_tasks(raw)
    assert len(out) == 2
    assert out[0]["id"] == "a" and out[0]["role"] == "研究员" and "OpenAI" in out[0]["task"]
    plan, errs = build_run_plan(raw, id_prefix="t")
    assert errs == []
    assert len(plan.nodes) == 2


def test_flat_invalid_task_rejects_entire_batch():
    tasks = [
        {"role": "A", "task": "a"},
        {"role": "B"},  # missing task
        {"role": "C", "task": "c"},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs
    assert any("tasks[1]" in e and "role" in e and "task" in e for e in errs)
    assert not plan.nodes


def test_depends_on_empty_string_normalized_still_uses_dag():
    tasks = [
        {"id": "a", "role": "A", "task": "a"},
        {"id": "b", "role": "B", "task": "b", "depends_on": ["", "a"]},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs == []
    assert len(plan.nodes) == 2
    b = plan.by_id("t_b")
    assert b.depends_on == ["t_a"]


def test_dag_duplicate_id_rejects_entire_batch():
    tasks = [
        {"id": "foo", "role": "A", "task": "a"},
        {"id": "foo", "role": "B", "task": "b", "depends_on": ["foo"]},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs
    assert any("重复" in e and "foo" in e for e in errs)
    assert not plan.nodes


def test_depends_on_role_name_resolves_unambiguously():
    """CEO 把中文角色名当 depends_on → 无歧义时解析为对应 id（故障样本修复）。"""
    tasks = [
        {"id": "api", "role": "后端API审查员", "task": "审 API"},
        {"id": "service", "role": "后端服务审查员", "task": "审服务"},
        {
            "id": "summary",
            "role": "汇总员",
            "task": "汇总",
            "depends_on": ["后端服务审查员", "后端API审查员"],
        },
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs == []
    summary = plan.by_id("t_summary")
    assert set(summary.depends_on) == {"t_service", "t_api"}


def test_flat_preserves_declared_id():
    """flat 批声明非空 id → 铸 {prefix}_{raw}（与 DAG 同形），非序号。"""
    plan, errs = build_run_plan(
        [{"id": "recon", "role": "调研员", "task": "查"}],
        id_prefix="t",
    )
    assert errs == []
    assert len(plan.nodes) == 1
    assert plan.nodes[0].run_id == "t_recon"


def test_flat_undeclared_id_still_uses_counter():
    plan, errs = build_run_plan([{"role": "A", "task": "a"}], id_prefix="t")
    assert errs == []
    assert plan.nodes[0].run_id == "t_1"


def test_flat_duplicate_declared_id_rejects_batch():
    plan, errs = build_run_plan(
        [
            {"id": "x", "role": "A", "task": "a"},
            {"id": "x", "role": "B", "task": "b"},
        ],
        id_prefix="t",
    )
    assert errs
    assert any("重复的 id" in e for e in errs)
    assert not plan.nodes


def test_flat_then_append_depends_on_declared_id():
    """验收：flat+id=recon 后再批 depends_on:[recon]+existing_plan → 通。"""
    host, host_errs = build_run_plan(
        [{"id": "recon", "role": "调研员", "task": "查"}],
        id_prefix="del_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )
    assert host_errs == []
    assert host.by_id("del_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee_recon") is not None

    plan, errs = build_run_plan(
        [
            {
                "id": "write",
                "role": "写手",
                "task": "基于调研写",
                "depends_on": ["recon"],
            }
        ],
        id_prefix="del_ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee",
        existing_plan=host,
    )
    assert errs == []
    assert plan.nodes[0].depends_on == [
        "del_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee_recon"
    ]


def test_append_batch_depends_on_previous_batch_id():
    """跨批 append：depends_on 上一批已有节点 id → 成功（对齐 build_added_nodes）。"""
    # 宿主可走 flat：声明 id 现已保留（不再忽略）。
    host, host_errs = build_run_plan(
        [{"id": "l2_a", "role": "调研A", "task": "查A"}],
        id_prefix="bt",
    )
    assert host_errs == []
    assert host.by_id("bt_l2_a") is not None

    plan, errs = build_run_plan(
        [
            {
                "id": "l2_b",
                "role": "写手",
                "task": "基于上游写",
                "depends_on": ["bt_l2_a"],
            }
        ],
        id_prefix="bt2",
        existing_plan=host,
    )
    assert errs == []
    assert len(plan.nodes) == 1
    assert plan.nodes[0].run_id == "bt2_l2_b"
    assert plan.nodes[0].depends_on == ["bt_l2_a"]


def test_append_batch_unknown_dep_lists_host_nodes():
    """append + host plan：未知依赖的「可用节点」须带出历史节点。"""
    host, _ = build_run_plan(
        [{"id": "l2_a", "role": "调研A", "task": "查A"}],
        id_prefix="bt",
    )
    plan, errs = build_run_plan(
        [
            {
                "id": "l2_b",
                "role": "写手",
                "task": "写",
                "depends_on": ["不存在的节点"],
            }
        ],
        id_prefix="bt2",
        existing_plan=host,
    )
    assert errs
    msg = " ".join(errs)
    assert "可用节点" in msg
    assert "bt_l2_a" in msg
    assert "调研A" in msg
    assert "下一步" in msg
    assert "append_to_execution_id" not in msg
    assert "当前活跃图" in msg or "本批" in msg


def test_depends_on_unknown_lists_available_run_ids():
    """未知依赖报错须可操作：列出当前可用 id/角色 + 可执行下一步。"""
    tasks = [
        {"id": "api", "role": "后端API审查员", "task": "审 API"},
        {
            "id": "summary",
            "role": "汇总员",
            "task": "汇总",
            "depends_on": ["不存在的审查员"],
        },
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs
    msg = " ".join(errs)
    assert "不存在的审查员" in msg
    assert "可用节点" in msg
    assert "api" in msg
    assert "后端API审查员" in msg
    assert "下一步" in msg


def test_depends_on_ambiguous_role_rejects():
    tasks = [
        {"id": "a1", "role": "审查员", "task": "审A"},
        {"id": "a2", "role": "审查员", "task": "审B"},
        {"id": "s", "role": "汇总", "task": "汇总", "depends_on": ["审查员"]},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs
    assert any("歧义" in e for e in errs)


def test_non_append_unknown_dep_lists_only_current_batch():
    """非 append：未知依赖仍只列本批，不假装有宿主图。"""
    tasks = [
        {"id": "a", "role": "A", "task": "a"},
        {"id": "b", "role": "B", "task": "b", "depends_on": ["bt_l2_a"]},
    ]
    plan, errs = build_run_plan(tasks, id_prefix="t")
    assert errs
    msg = " ".join(errs)
    assert "bt_l2_a" in msg
    assert "可用节点" in msg
    assert "a" in msg
    assert "A" in msg
    # 本批只有 a/b，不应冒出别的 execution 节点
    assert "调研" not in msg
    assert "bt_l2_a（" not in msg  # host-style catalog entry absent


def test_append_batch_depends_on_host_short_raw():
    """跨批 append：depends_on 上一批短 raw（bt_l2_a）→ 解析到 del_<uuid>_bt_l2_a。"""
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec

    host_id = "del_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee_bt_l2_a"
    host = RunPlan(
        nodes=[
            RunSpec(run_id=host_id, agent_id=host_id, role="调研A", task="查A"),
        ]
    )
    plan, errs = build_run_plan(
        [
            {
                "id": "l2_b",
                "role": "写手",
                "task": "基于上游写",
                "depends_on": ["bt_l2_a"],
            }
        ],
        id_prefix="del_ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee",
        existing_plan=host,
    )
    assert errs == []
    assert len(plan.nodes) == 1
    assert plan.nodes[0].depends_on == [host_id]


def test_build_added_nodes_depends_on_host_short_raw():
    """build_added_nodes：depends_on 宿主短 raw（与 append 同规则）。"""
    from agentcore.runtime.runs.builder import build_added_nodes
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec

    host_id = "del_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee_bt_l2_a"
    host = RunPlan(
        nodes=[
            RunSpec(run_id=host_id, agent_id=host_id, role="调研A", task="查A"),
        ]
    )
    specs, errs = build_added_nodes(
        [{"id": "l2_b", "role": "写手", "task": "基于上游写", "depends_on": ["bt_l2_a"]}],
        host,
    )
    assert errs == []
    assert len(specs) == 1
    assert specs[0].depends_on == [host_id]


def test_append_batch_short_raw_conflict_rejects():
    """两宿主节点剥出同一短 raw 且 run_id 不同 → 歧义报错列候选，勿静默抢先。"""
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec

    a = "del_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee_bt_l2_a"
    b = "del_bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee_bt_l2_a"
    host = RunPlan(
        nodes=[
            RunSpec(run_id=a, agent_id=a, role="调研A", task="查A"),
            RunSpec(run_id=b, agent_id=b, role="调研B", task="查B"),
        ]
    )
    plan, errs = build_run_plan(
        [
            {
                "id": "l2_c",
                "role": "写手",
                "task": "写",
                "depends_on": ["bt_l2_a"],
            }
        ],
        id_prefix="del_cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee",
        existing_plan=host,
    )
    assert errs
    msg = " ".join(errs)
    assert "短 id 有歧义" in msg
    assert a in msg
    assert b in msg
    assert not plan.nodes


def test_build_added_nodes_short_raw_conflict_rejects():
    """build_added_nodes：宿主短 id 冲突 → 与 append 同类明确错误。"""
    from agentcore.runtime.runs.builder import build_added_nodes
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec

    a = "del_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee_star_ct"
    b = "add_bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee_star_ct"
    host = RunPlan(
        nodes=[
            RunSpec(run_id=a, agent_id=a, role="星图A", task="A"),
            RunSpec(run_id=b, agent_id=b, role="星图B", task="B"),
        ]
    )
    specs, errs = build_added_nodes(
        [{"id": "n", "role": "汇总", "task": "汇总", "depends_on": ["star_ct"]}],
        host,
    )
    assert errs
    msg = " ".join(errs)
    assert "短 id 有歧义" in msg
    assert a in msg
    assert b in msg
    assert specs == []


def test_non_append_short_raw_still_unknown():
    """非 append：短 raw 不因「像 host id」而凭空可解析。"""
    plan, errs = build_run_plan(
        [
            {"id": "a", "role": "A", "task": "a"},
            {"id": "b", "role": "B", "task": "b", "depends_on": ["bt_l2_a"]},
        ],
        id_prefix="del_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )
    assert errs
    assert any("无法解析" in e for e in errs)
    assert plan.nodes  # a 仍入图；b 因 dep 失败被跳过，整批仍带 errs
