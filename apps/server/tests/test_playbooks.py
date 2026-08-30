"""拆·playbook 固化 (§2.1) — the playbook registry + expansion.

Covers each固化形状's slot validation + emitted DAG shape, the registry's reject paths
(unknown name / bad args / missing required slot), and — most importantly — that every
expanded ``tasks`` list is actually runnable: it round-trips through the REAL
``build_run_plan`` with no errors, so an emitted id / depends_on mismatch can't slip through.
"""

from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.playbooks import (
    CODE_AUDIT_FANOUT,
    PLAYBOOKS,
    available_playbooks,
    expand_playbook,
    playbook_args_schema_description,
)
from agentcore.runtime.runs.playbooks._common import Playbook
from agentcore.runtime.runs.playbooks.audit import (
    CODE_AUDIT_REQUIRED_SECTIONS,
    CODE_AUDIT_SECTION_BY_DESIGN,
    apply_inherited_code_audit_discipline,
)
from agentcore.workspace.stage_dirs import REVIEWS_DIR


def _roles(tasks: list[dict]) -> list[str]:
    return [t["role"] for t in tasks]


def _by_id(tasks: list[dict]) -> dict[str, dict]:
    return {t["id"]: t for t in tasks}


# ── code_audit ────────────────────────────────────────────────────────────────


def test_code_audit_single_module_one_auditor():
    tasks, errors = expand_playbook("code_audit", {"scope": "apps/desktop/src/preload"})
    assert errors == []
    assert len(tasks) == 1
    t = tasks[0]
    assert t["role"] == "代码审计员"
    assert not t.get("depends_on")
    d = t["deliverable"]
    assert d["form"] == "files"
    assert d["strict"] is True
    assert d["code_audit_gate"] is True
    assert "P0" in t["task"] and "P3" in t["task"]
    assert "观察·工程" in t["task"]
    assert len(d["artifacts"]) == 2
    assert d["artifacts"][0] == f"{REVIEWS_DIR}/code-audit-0-main.md"
    assert d["artifacts"][1].endswith(".audit.json")
    assert "〇、人审速览" in d["required_sections"]
    assert d["required_sections"] == list(CODE_AUDIT_REQUIRED_SECTIONS)
    assert CODE_AUDIT_SECTION_BY_DESIGN in t["task"]
    assert "验证方式" in t["task"] and "定案" in t["task"]
    assert "must_contain" not in d
    assert "name" not in d
    assert "requires_files" not in d
    assert "min_length" not in d
    assert "两阶段" in t["task"] or "A 宽扫" in t["task"]
    assert "K=8" in t["task"] or "最多定案 K=8" in t["task"]
    assert ".audit.json" in t["task"]
    assert "缺陷id|严重度|一句话" in t["task"]
    assert "不得以 handoff 替代落盘" in t["task"]
    assert "一次交接" in t["task"]
    assert "二次 handoff" in t["task"]
    assert "收口口径" in t["task"]
    assert "全程只读" not in t["task"]
    assert "通过验收" not in t["task"]
    assert "未改业务源码" in t["task"]
    assert "cite_write_review" not in t["task"]
    # 分段交付：骨架先落 → 边查边填；artifacts 声明仍为 [md, .audit.json]
    assert "骨架先落 → 边查边填" in t["task"]
    assert "分段交付" in t["task"]
    assert "Phase A 结束" in t["task"] and "骨架" in t["task"]
    assert "五章空壳" in t["task"]
    assert "禁止读完再一次性成文" in t["task"]
    assert d["artifacts"] == [
        f"{REVIEWS_DIR}/code-audit-0-main.md",
        f"{REVIEWS_DIR}/code-audit-0-main.audit.json",
    ]
    plan, plan_errs = build_run_plan(tasks)
    assert plan_errs == []
    assert len(plan.nodes) == 1
    assert plan.nodes[0].deliverable is not None
    assert plan.nodes[0].deliverable.strict is True
    assert plan.nodes[0].deliverable.code_audit_gate is True


def test_code_audit_section_titles_literal_across_playbook_inherit_skill():
    """契约定义 / 继承函数 / skill 三处小标题必须同字面。"""
    from agentcore.runtime.skills import build_system_skill_registry

    tasks, errors = expand_playbook("code_audit", {"scope": "x"})
    assert errors == []
    playbook_secs = tasks[0]["deliverable"]["required_sections"]
    assert playbook_secs == list(CODE_AUDIT_REQUIRED_SECTIONS)
    handwritten = apply_inherited_code_audit_discipline(
        [
            {
                "role": "审计员",
                "task": "审",
                "deliverable": {
                    "form": "files",
                    "artifacts": ["AgentCore/文档/reviews/a.md"],
                },
            }
        ],
        only_shaped=True,
    )
    assert handwritten[0]["deliverable"]["required_sections"] == playbook_secs
    body = build_system_skill_registry().get("team_orchestration_advanced").body
    json_lit = "[" + ", ".join(f'"{s}"' for s in CODE_AUDIT_REQUIRED_SECTIONS) + "]"
    assert json_lit in body
    for title in CODE_AUDIT_REQUIRED_SECTIONS:
        assert title in tasks[0]["task"]
        assert title in body


def test_code_audit_two_modules_parallel_no_synth():
    """2 路并行：只有审计员，不上主管（CEO 收口）。"""
    tasks, errors = expand_playbook(
        "code_audit",
        {"scope": "AgentCore", "modules": ["server", "desktop"]},
    )
    assert errors == []
    by_id = _by_id(tasks)
    assert set(by_id) == {"audit_0", "audit_1"}
    assert all(by_id[i]["role"] == "代码审计员" for i in ("audit_0", "audit_1"))
    assert "audit_synth" not in by_id
    plan, plan_errs = build_run_plan(tasks)
    assert plan_errs == []
    assert len(plan.nodes) == 2
    assert len(plan.waves()) == 1


def test_code_audit_three_modules_parallel_no_synth():
    """≥3 路仍只扩审计员；跨模块表由引擎在 CEO 收口注入，不派主管。"""
    tasks, errors = expand_playbook(
        "code_audit",
        {"scope": "AgentCore monorepo", "modules": ["server", "desktop", "admin"]},
    )
    assert errors == []
    by_id = _by_id(tasks)
    assert set(by_id) == {"audit_0", "audit_1", "audit_2"}
    assert all(by_id[i]["role"] == "代码审计员" for i in ("audit_0", "audit_1", "audit_2"))
    assert "audit_synth" not in by_id
    assert all(t["role"] != "审计主管" for t in tasks)
    assert all(
        "code-audit-summary.md" not in str(t.get("deliverable", {}).get("artifacts", []))
        for t in tasks
    )
    assert by_id["audit_0"]["deliverable"]["artifacts"][0] == (
        f"{REVIEWS_DIR}/code-audit-0-server.md"
    )
    assert by_id["audit_1"]["deliverable"]["artifacts"][0] == (
        f"{REVIEWS_DIR}/code-audit-1-desktop.md"
    )
    plan, plan_errs = build_run_plan(tasks)
    assert plan_errs == []
    assert len(plan.nodes) == 3
    assert len(plan.waves()) == 1


def test_code_audit_artifact_uses_task_id_slug_not_essay_filename():
    """Long module essays must not become truncated filenames (dogfood d3b6f1b8)."""
    essay = (
        "desktop-renderer：apps_desktop_src_renderer 的流式订阅与 UI 更新"
        "（stores_hooks_services_lib）"
    )
    tasks, errors = expand_playbook(
        "code_audit",
        {
            "scope": "前端刷新",
            "modules": [
                essay,
                "desktop-sidecar：apps_desktop_src_main 的 sidecar 事件链",
            ],
        },
    )
    assert errors == []
    by_id = _by_id(tasks)
    md0 = by_id["audit_0"]["deliverable"]["artifacts"][0]
    md1 = by_id["audit_1"]["deliverable"]["artifacts"][0]
    assert md0 == f"{REVIEWS_DIR}/code-audit-0-desktop-renderer.md"
    assert md1 == f"{REVIEWS_DIR}/code-audit-1-desktop-sidecar.md"
    assert essay in by_id["audit_0"]["task"]
    assert md0.endswith(".md")
    assert by_id["audit_0"]["deliverable"]["artifacts"][1] == md0[:-3] + ".audit.json"


def test_code_audit_slug_matches_write_sanitize_for_dunder_dir_and_file_ext():
    """Declared artifacts must be write-sanitizer fixpoints (dogfood ace942af).

    ``__tests__`` must not keep collapsed-vs-raw underscore drift; a ``.tsx``
    module must not land as ``Name..md`` / ``Name..audit.json``.
    """
    from agentcore.runtime.runs.playbooks.audit import _module_slug
    from agentcore.workspace._paths import sanitize_write_relpath

    dunder = "apps/admin/src/pages/__tests__/AnalyticsPage.tsx"
    tsx = "apps/admin/src/components/GoWindowsCard.tsx"
    assert "___" not in _module_slug(dunder)
    assert _module_slug(dunder) == "apps_admin_src_pages_tests_AnalyticsPage"
    assert _module_slug(tsx) == "apps_admin_src_components_GoWindowsCard"
    assert not _module_slug(tsx).endswith(".")
    assert ".tsx" not in _module_slug(tsx)

    tasks, errors = expand_playbook(
        "code_audit",
        {"scope": "apps/admin", "modules": [dunder, tsx]},
    )
    assert errors == []
    by_id = _by_id(tasks)
    md0, json0 = by_id["audit_0"]["deliverable"]["artifacts"]
    md1, json1 = by_id["audit_1"]["deliverable"]["artifacts"]
    assert md0 == (
        f"{REVIEWS_DIR}/code-audit-0-apps_admin_src_pages_tests_AnalyticsPage.md"
    )
    assert json0 == md0[:-3] + ".audit.json"
    assert "___tests___" not in md0
    assert md1 == (
        f"{REVIEWS_DIR}/code-audit-1-apps_admin_src_components_GoWindowsCard.md"
    )
    assert json1 == md1[:-3] + ".audit.json"
    assert ".." not in md1
    assert ".." not in json1
    assert ".tsx" not in md1
    for path in (md0, json0, md1, json1):
        assert sanitize_write_relpath(path) == path


def test_code_audit_requires_scope_and_rejects_single_module_list():
    tasks, errors = expand_playbook("code_audit", {"modules": ["a", "b"]})
    assert tasks == []
    assert any("scope" in e for e in errors)
    tasks2, errors2 = expand_playbook(
        "code_audit", {"scope": "x", "modules": ["only-one"]}
    )
    assert tasks2 == []
    assert any("≥2" in e or "modules" in e for e in errors2)


def test_code_audit_fans_out_up_to_eight_modules_without_fold():
    """code_audit 扇出上限 8：恰好 8 个模块不折叠（全局 MAX_PLAYBOOK_FANOUT 仍为 6）。"""
    from agentcore.runtime.runs.playbooks import (
        CODE_AUDIT_FANOUT,
        MAX_PLAYBOOK_FANOUT,
        collect_playbook_notes,
    )

    assert CODE_AUDIT_FANOUT == 8
    assert MAX_PLAYBOOK_FANOUT == 6
    modules = [f"m{i}" for i in range(CODE_AUDIT_FANOUT)]
    tasks, errors = expand_playbook(
        "code_audit", {"scope": "monorepo", "modules": modules}
    )
    assert errors == []
    auditors = [t for t in tasks if t["role"] == "代码审计员"]
    assert len(auditors) == CODE_AUDIT_FANOUT
    for i, mod in enumerate(modules):
        assert f"audit_{i}" in {t["id"] for t in auditors}
        assert mod in _by_id(tasks)[f"audit_{i}"]["task"]
    assert collect_playbook_notes(tasks) == []
    assert "audit_synth" not in {t["id"] for t in tasks}
    assert all(t["role"] == "代码审计员" for t in tasks)


def test_code_audit_folds_modules_beyond_eight_into_last_slot():
    """>8 模块：折叠进末审计槽（合并不丢弃），带 playbook_note。"""
    from agentcore.runtime.runs.playbooks import CODE_AUDIT_FANOUT, collect_playbook_notes

    n = CODE_AUDIT_FANOUT + 3
    modules = [f"m{i}" for i in range(n)]
    tasks, errors = expand_playbook(
        "code_audit", {"scope": "monorepo", "modules": modules}
    )
    assert errors == []
    auditors = [t for t in tasks if t["role"] == "代码审计员"]
    assert len(auditors) == CODE_AUDIT_FANOUT
    last = auditors[-1]
    for i in range(CODE_AUDIT_FANOUT - 1, n):
        assert f"m{i}" in last["task"]
    notes = collect_playbook_notes(tasks)
    assert notes and "扇出折叠" in notes[0]
    assert f"m{CODE_AUDIT_FANOUT}" in notes[0]
    assert str(CODE_AUDIT_FANOUT) in notes[0]
    assert "audit_synth" not in {t["id"] for t in tasks}
    assert all(t["role"] == "代码审计员" for t in tasks)


def test_available_playbooks_lists_code_audit():
    listing = available_playbooks()
    assert "code_audit" in listing
    assert "代码审计" in listing
    assert "2–3" in listing or "2-3" in listing
    assert "产品缝" in listing or "能少则少" in listing
    assert "上限" in listing and str(CODE_AUDIT_FANOUT) in listing


def test_playbook_args_schema_surfaces_code_audit_modules():
    """CEO 工具面必须看见 code_audit.modules（扇出靠填槽；引擎不从 scope 拆）。"""
    desc = playbook_args_schema_description()
    slots = PLAYBOOKS["code_audit"].slots
    assert "modules(" in slots
    assert "modules" in desc
    assert "code_audit" in desc
    assert "可选" in desc
    assert "不从 scope 自动拆" in desc
    assert "并行" in desc or "扇出" in desc
    assert "整仓" in desc or "多子系统" in desc
    # 上限 / 单缝 / 折叠 HOW 在 slots（校验报错）+ 编排 skill，不占每轮 schema
    assert str(CODE_AUDIT_FANOUT) in slots
    assert "单缝省略" in slots
    # 必填抽取仍在（常驻路径勿先 consult）
    assert "code_audit→scope" in desc
    assert "diagnose_fix_verify" not in desc
    assert "lens_crosscheck" not in desc
    assert "build_website" not in desc


# ── map_fanout ────────────────────────────────────────────────────────────


def test_map_fanout_fans_out_notes_without_write_pipeline():
    tasks, errors = expand_playbook(
        "map_fanout",
        {"topic": "P vs NP", "angles": ["为何难", "若解决", "下界", "攻击失败"]},
    )
    assert errors == []
    by_id = _by_id(tasks)
    assert len(tasks) == 4
    assert all(t["role"] == "方向专员" for t in tasks)
    assert all(not t.get("depends_on") for t in tasks)
    assert "outline" not in by_id and "write" not in by_id and "review" not in by_id
    expected = {
        "AgentCore/文档/research/为何难方向笔记.md",
        "AgentCore/文档/research/若解决方向笔记.md",
        "AgentCore/文档/research/下界方向笔记.md",
        "AgentCore/文档/research/攻击失败方向笔记.md",
    }
    for t in tasks:
        d = t["deliverable"]
        assert d["form"] == "files"
        assert d["artifacts"][0] in expected
        assert "方向笔记" in t["task"]
        assert "终稿" in t["task"]
        # 摸底验收：一页地图 + 够用即停 + handoff 必交（提示词纪律，非完成硬闸）
        assert "摸底验收" in t["task"] or "够用即停" in t["task"]
        assert "一页地图" in t["task"]
        assert "白皮书" in t["task"]
        assert "开局先招人" not in t["task"]
        assert "已经做了很久" not in t["task"]
        assert "分段追加" in t["task"]
        assert "给用户看的回复" in t["task"]
        assert "法条" not in t["task"]
        assert "完整要点须用" not in t["task"]
        assert "read_notes" not in t["task"]
        assert "重复通读" not in t["task"]
        assert "web_search" not in t["task"]
        assert "权威出处" not in t["task"]
        assert "≤12 词" not in t["task"]
        assert "为凑台账编号" in t["task"]
        assert "文件名或路径" in t["task"]
        assert d.get("citation_mode") in (None, "")
        assert "定位" in t["task"] and "技术栈" in t["task"]
        assert "必读书单" in t["task"] or "章节大纲" in t["task"]
        assert "定优化方案" in t["task"]
        assert "handoff" in t["task"].lower()
        assert "禁" in t["task"] and "业务代码" in t["task"]
        assert "够用即停" in t["task"] or "handoff" in t["task"].lower()
        assert "name" not in d
        # A 档摸底不盖学术检索挡位
        assert t.get("search_policy") in (None, "", False) or not t.get("search_policy")
        assert "学术检索" not in t["task"]
    plan, plan_errs = build_run_plan(tasks)
    assert plan_errs == []
    assert len(plan.nodes) == 4
    assert all(n.search_policy == "" for n in plan.nodes)


def test_map_fanout_requires_topic_and_two_angles():
    tasks, errors = expand_playbook("map_fanout", {"angles": ["甲", "乙"]})
    assert tasks == []
    assert any("topic" in e for e in errors)
    tasks2, errors2 = expand_playbook(
        "map_fanout", {"topic": "X", "angles": ["仅一角"]}
    )
    assert tasks2 == []
    assert any("≥2" in e or "angles" in e for e in errors2)


def test_available_playbooks_lists_map_fanout_before_cite_write_review_semantics():
    listing = available_playbooks()
    assert "map_fanout" in listing
    assert "对齐推进" in listing or "方向笔记" in listing
    assert "讨论对齐" in listing or "摸清" in listing
    assert "人数跟缝走" in listing or "独立缝" in listing
    assert "够用即停" in listing or "handoff" in listing
    assert "一页地图" in listing
    assert "一句目标" in listing or "必读文件" in listing
    assert "cite_write_review" in listing
    assert "成文专线" in listing
    assert "明示" in listing
    assert "正式长文" in listing or "可提交" in listing or "审校满编" in listing
    assert "形态未定" in listing or "勿默认学术审校" in listing
    assert "consult(team_delivery_env)" in listing
    assert "reportlab" not in listing


# ── cite_write_review ───────────────────────────────────────────────────────────


def test_cite_write_review_fans_out_one_researcher_per_angle_then_outline_then_write():
    tasks, errors = expand_playbook(
        "cite_write_review",
        {"topic": "向量数据库", "angles": ["原理", "选型", "成本"], "checkpoint": True},
    )
    assert errors == []
    by_id = _by_id(tasks)
    # one 调研员 per angle, then 提纲(依赖全部调研), then 写作(依赖提纲).
    research_ids = [f"research_{i}" for i in range(3)]
    assert all(rid in by_id for rid in research_ids)
    assert set(by_id["outline"]["depends_on"]) == set(research_ids)
    assert by_id["write"]["depends_on"] == ["outline"]
    assert by_id["review"]["depends_on"] == ["write"]
    assert by_id["review"]["role"] == "学术审校员"
    # 审校落盘契约写死在 playbook（form=files + reviews/），不靠运行时扫角色名抬契约。
    review_d = by_id["review"]["deliverable"]
    assert review_d["form"] == "files"
    assert "requires_files" not in review_d
    assert "name" not in review_d
    assert "min_length" not in review_d
    assert review_d["artifacts"] == ["AgentCore/文档/reviews/审校报告.md"]
    assert "复核落盘" in by_id["review"]["task"]
    # 审校节点显式墙钟 300s（CEO 显式 timeout_ms 恒优先于统一 backstop）。
    assert by_id["review"]["timeout_ms"] == 300_000
    # checkpoint flag rides the 提纲 step (成纲后写作前过目); the write step requires file landing.
    assert by_id["outline"]["checkpoint_after"] is True
    assert "requires_files" not in by_id["write"]["deliverable"]
    assert "name" not in by_id["write"]["deliverable"]
    assert by_id["write"]["deliverable"]["form"] == "files"
    assert by_id["write"]["deliverable"]["artifacts"] == ["AgentCore/文档/research/报告.md"]
    assert "单主文件" in by_id["write"]["task"]
    assert "AgentCore/文档/research/报告.md" in by_id["write"]["task"]
    assert "AgentCore/文档/research/报告.md" in by_id["review"]["task"]
    # MD 为主；要 PDF → md_to_pdf；禁 HTML 顶替 / reportlab 主路径
    write_task = by_id["write"]["task"]
    assert "md_to_pdf" in write_task
    assert "HTML" in write_task
    assert "reportlab" in write_task
    assert "MD 为主" in write_task or "`.md`" in write_task or ".md" in write_task
    # 中间环与终稿同走约定文档：各路调研 + 提纲 form=files，路径钉 RESEARCH_DIR，角度名入文件名。
    expected_research_artifacts = {
        "AgentCore/文档/research/原理调研报告.md",
        "AgentCore/文档/research/选型调研报告.md",
        "AgentCore/文档/research/成本调研报告.md",
    }
    for rid in research_ids:
        d = by_id[rid]["deliverable"]
        assert d["form"] == "files"
        assert d["artifacts"] and d["artifacts"][0] in expected_research_artifacts
        assert d["artifacts"][0] in by_id[rid]["task"]
        assert "file_write" in by_id[rid]["task"]
        assert "≤12 词" not in by_id[rid]["task"]
        assert "截断" not in by_id[rid]["task"]
        assert "规范化" not in by_id[rid]["task"]
    outline_d = by_id["outline"]["deliverable"]
    assert outline_d["form"] == "files"
    assert outline_d["artifacts"] == ["AgentCore/文档/research/提纲.md"]
    assert "AgentCore/文档/research/提纲.md" in by_id["outline"]["task"]
    # Artifact-first writer brief：主路径一次完整 write；可选骨架；禁半章散文再 append。
    # 定案对齐：分波范围 + continue_from 待续；填空正向 file_append / str_replace（废工具不点名）。
    # （write_task 已在上方绑定；含 MD→PDF 纪律）
    assert "主路径" in write_task or "一次 file_write 完整" in write_task
    assert "短骨架" in write_task or "骨架" in write_task
    assert "禁止首写半章散文" in write_task
    assert "首写必须是短骨架" not in write_task
    assert "file_append" in write_task and "str_replace" in write_task
    assert "write_section" not in write_task
    assert "continue_from_run_id" in write_task
    assert "章节范围" in write_task or "前几章" in write_task
    assert "artifact manifest" in write_task or "禁止再对本文件" in write_task
    assert "file_read" in write_task
    # each angle is named into its researcher's task so the fan-out doesn't run blind/overlapping.
    assert "选型" in by_id["research_1"]["task"]
    assert "read_notes" not in by_id["research_1"]["task"]
    assert "post_note" not in by_id["research_1"]["task"]
    assert "#rN" not in by_id["research_1"]["task"]
    assert "待核实" not in by_id["research_1"]["task"]
    assert "read_url" not in by_id["research_1"]["task"]
    assert "法条" not in by_id["research_1"]["task"]
    # 成文综述：调研员盖学术检索挡位 + 纪律句；提纲/撰稿/审校不盖。
    for rid in research_ids:
        assert by_id[rid]["search_policy"] == "academic_literature"
        assert "学术检索" in by_id[rid]["task"]
        assert "论文库" in by_id[rid]["task"] or "arxiv" in by_id[rid]["task"]
        assert "全面综述" in by_id[rid]["task"]
    assert by_id["outline"].get("search_policy") in (None, "")
    assert by_id["write"].get("search_policy") in (None, "")
    assert by_id["review"].get("search_policy") in (None, "")
    plan, plan_errs = build_run_plan(tasks)
    assert plan_errs == []
    research_nodes = [n for n in plan.nodes if n.role == "调研员"]
    assert research_nodes
    assert all(n.search_policy == "academic_literature" for n in research_nodes)
    assert all(
        n.search_policy == "" for n in plan.nodes if n.role != "调研员"
    )
    assert "read_url" not in by_id["review"]["task"]
    assert "法条" not in by_id["review"]["task"]
    assert "tools" not in by_id["review"]
    assert "检索纪律" not in by_id["review"]["task"]
    assert "code_search" not in by_id["review"]["task"]
    assert "整目录" not in by_id["review"]["task"]


def test_cite_write_review_without_angles_uses_single_researcher():
    tasks, errors = expand_playbook("cite_write_review", {"topic": "X"})
    assert errors == []
    by_id = _by_id(tasks)
    assert by_id["outline"]["depends_on"] == ["research_0"]
    assert by_id["outline"]["checkpoint_after"] is True  # default: checkpoint on outline
    assert by_id["review"]["depends_on"] == ["write"]
    assert "#rN" not in by_id["research_0"]["task"]
    assert "待核实" not in by_id["research_0"]["task"]
    assert by_id["research_0"]["search_policy"] == "academic_literature"
    assert "学术检索" in by_id["research_0"]["task"]
    # 无 angles 时默认约定文档路径（仍落 RESEARCH_DIR，不用角色名）。
    d = by_id["research_0"]["deliverable"]
    assert d["form"] == "files"
    assert d["artifacts"] == ["AgentCore/文档/research/调研要点.md"]
    assert by_id["outline"]["deliverable"]["artifacts"] == ["AgentCore/文档/research/提纲.md"]


def test_cite_write_review_output_path_overrides_main_artifact():
    tasks, errors = expand_playbook(
        "cite_write_review",
        {"topic": "T", "output_path": "paper/main.md"},
    )
    assert errors == []
    by_id = _by_id(tasks)
    assert by_id["write"]["deliverable"]["artifacts"] == ["paper/main.md"]
    assert "paper/main.md" in by_id["write"]["task"]
    assert "paper/main.md" in by_id["review"]["task"]


def test_cite_write_review_checkpoint_can_be_disabled():
    tasks, errors = expand_playbook("cite_write_review", {"topic": "X", "checkpoint": False})
    assert errors == []
    assert _by_id(tasks)["outline"]["checkpoint_after"] is False


def test_cite_write_review_review_explicit_wall_clock_survives_build():
    """审校节点显式 timeout_ms=300000 经真实 builder 落成 policy.timeout_s=300；
    token 顶走统一 backstop（200k）。"""
    from agentcore.runtime.runs.worker_budget import WORKER_TIMEOUT_BACKSTOP_S

    tasks, errors = expand_playbook(
        "cite_write_review", {"topic": "T", "angles": ["a", "b"]}
    )
    assert errors == []
    assert _by_id(tasks)["review"]["timeout_ms"] == 300_000
    plan, plan_errors = build_run_plan(tasks, id_prefix="pb_rr_review_to")
    assert plan_errors == []
    by_role = {n.role: n for n in plan.nodes}
    # review 有上游；墙钟显式 300s；token 顶走统一 backstop。
    assert by_role["学术审校员"].policy.timeout_s == 300
    assert by_role["学术审校员"].token_ceiling == 4_000_000
    # 提纲同为依赖上游的节点、未显式声明墙钟 → 统一 backstop 1200s / 4M。
    assert by_role["提纲编辑"].policy.timeout_s == WORKER_TIMEOUT_BACKSTOP_S
    assert by_role["提纲编辑"].token_ceiling == 4_000_000


def test_cite_write_review_requires_topic():
    tasks, errors = expand_playbook("cite_write_review", {})
    assert tasks == []
    assert errors and "topic" in errors[0]


def test_cite_write_review_folds_angle_fanout_with_note():
    """angles 超扇出上限：折叠进末节点（合并不丢弃），带 playbook_note。"""
    from agentcore.runtime.runs.playbooks import MAX_PLAYBOOK_FANOUT, collect_playbook_notes

    n = MAX_PLAYBOOK_FANOUT + 5
    tasks, errors = expand_playbook(
        "cite_write_review", {"topic": "X", "angles": [f"a{i}" for i in range(n)]}
    )
    assert errors == []
    researchers = [t for t in tasks if t["role"] == "调研员"]
    assert len(researchers) == MAX_PLAYBOOK_FANOUT
    last = researchers[-1]
    # Tail angles folded into last researcher (not silently dropped).
    for i in range(MAX_PLAYBOOK_FANOUT - 1, n):
        assert f"a{i}" in last["task"]
    notes = collect_playbook_notes(tasks)
    assert notes and "扇出折叠" in notes[0]
    assert f"a{MAX_PLAYBOOK_FANOUT}" in notes[0]


# ── build_app（已撤：工厂图纸，expand 未知）────────────────────────────────────


def test_build_app_playbook_is_unknown():
    """具名工厂图纸已撤：expand 未知；登记表与 schema 不再出现该槽。"""
    listing = available_playbooks()
    desc = playbook_args_schema_description()
    tasks, errors = expand_playbook("build_app", {"app": "面向运营的 Vue3 数据看板"})
    assert tasks == []
    assert errors and "未知" in errors[0]
    assert "build_app" in errors[0]
    assert "build_app" not in PLAYBOOKS
    assert "build_app" not in listing
    assert "build_app→app" not in desc


# ── 已撤登记：建站不再是具名 DAG ──────────────────────────────────────────────


def test_build_website_playbooks_are_unknown():
    """build_website / verify 必须未知；登记表与 schema 不再出现建站槽。"""
    listing = available_playbooks()
    desc = playbook_args_schema_description()
    for name in ("build_website", "build_website_verify"):
        tasks, errors = expand_playbook(name, {"topic": "Landing"})
        assert tasks == []
        assert errors and "未知" in errors[0]
        assert name in errors[0]
        assert name not in PLAYBOOKS
        assert name not in listing
        assert name not in desc


def test_compare_options_and_build_feature_are_unknown():
    """已删具名本必须未知；登记表与 schema 不再出现废名。"""
    listing = available_playbooks()
    desc = playbook_args_schema_description()
    for name in ("compare_options", "build_feature"):
        tasks, errors = expand_playbook(name, {"topic": "X"})
        assert tasks == []
        assert errors and "未知" in errors[0]
        assert name in errors[0]
        assert name not in PLAYBOOKS
        assert name not in listing
        assert name not in desc


def test_build_toolshed_playbook_removed():
    """旧独立 playbook 名直接未知失败——无别名 / 静默改写。"""
    tasks, errors = expand_playbook("build_toolshed", {"topic": "Ops"})
    assert tasks == []
    assert errors and "未知" in errors[0]
    assert "build_toolshed" in errors[0]


def test_renamed_playbook_ids_are_unknown():
    """旧具名 id 与拼写错误同等未知；无别名。"""
    listing = available_playbooks()
    desc = playbook_args_schema_description()
    for name in (
        "parallel_brief",
        "research_report",
        "multi_lens_research",
        "lens_crosscheck",
        "repair_code",
        "diagnose_fix_verify",
    ):
        tasks, errors = expand_playbook(name, {"topic": "X"})
        assert tasks == []
        assert errors and "未知" in errors[0]
        assert name in errors[0]
        assert name not in PLAYBOOKS
        assert name not in listing
        assert name not in desc


# ── registry reject paths ─────────────────────────────────────────────────────


def test_expand_unknown_playbook_lists_available():
    tasks, errors = expand_playbook("nope", {})
    assert tasks == []
    assert errors and "未知 playbook" in errors[0]
    for name in PLAYBOOKS:
        assert name in errors[0]


def test_expand_rejects_non_object_args():
    tasks, errors = expand_playbook("cite_write_review", ["not", "a", "dict"])  # type: ignore[arg-type]
    assert tasks == []
    assert errors and "playbook_args" in errors[0]


def test_expand_playbook_missing_packaged_resource_lists_error(monkeypatch):
    """build 缺打包资源时不得 raise；errors 点名 playbook、禁泄露路径。"""
    missing_path = "C:\\Secret\\packaged\\skill.md"
    pb = PLAYBOOKS["map_fanout"]

    def _raise_missing(_args: dict) -> tuple[list[dict], list[str]]:
        raise FileNotFoundError(missing_path)

    monkeypatch.setitem(
        PLAYBOOKS,
        "map_fanout",
        Playbook(name=pb.name, summary=pb.summary, slots=pb.slots, build=_raise_missing),
    )
    tasks, errors = expand_playbook("map_fanout", {"topic": "T", "angles": ["a", "b"]})

    assert tasks == []
    assert errors
    assert "map_fanout" in errors[0]
    assert "内部打包资源缺失" in errors[0]
    assert "手写 tasks" in errors[0]
    assert missing_path not in errors[0]
    assert "FileNotFoundError" not in errors[0]


def test_available_playbooks_lists_all_registered():
    listing = available_playbooks()
    assert set(PLAYBOOKS) == {
        "code_audit",
        "map_fanout",
        "cite_write_review",
    }
    for name in PLAYBOOKS:
        assert name in listing
    assert "lens_crosscheck" not in listing
    assert "diagnose_fix_verify" not in listing


# ── every expansion is a runnable plan (the real builder, not a mock) ──────────


def test_every_playbook_expansion_builds_a_valid_run_plan():
    samples = {
        "code_audit": {"scope": "apps/server", "modules": ["auth", "storage"]},
        "map_fanout": {"topic": "T", "angles": ["a", "b", "c"]},
        "cite_write_review": {"topic": "T", "angles": ["a", "b"], "checkpoint": True},
    }
    expected_nodes = {
        "code_audit": 2,  # 2 auditors, no synth
        "map_fanout": 3,
        "cite_write_review": 5,
    }
    assert set(samples) == set(PLAYBOOKS)  # 名副其实的 every：新增 playbook 必须补样本
    for name, args in samples.items():
        tasks, errors = expand_playbook(name, args)
        assert errors == [], name
        plan, plan_errors = build_run_plan(tasks, id_prefix=f"pb_{name}")
        assert plan_errors == [], (name, plan_errors)
        assert len(plan.nodes) == expected_nodes[name], name
        # waves() raises on a cycle / dangling edge — a clean call proves the DAG is sound.
        assert plan.waves()
        assert all(
            "workspace_native" not in (t.get("deliverable") or {}) for t in tasks
        ), name
