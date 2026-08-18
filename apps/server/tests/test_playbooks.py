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
    assert "全程只读" in t["task"]
    assert "未改业务源码" in t["task"]
    # 分段交付：骨架先落 → 补全 → 成文；artifacts 声明仍为 [md, .audit.json]
    assert "骨架先落 → 补全 → 成文" in t["task"]
    assert "分段交付" in t["task"]
    assert "Phase A 结束" in t["task"] and "骨架" in t["task"]
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


def test_code_audit_multi_module_parallel_plus_synth():
    tasks, errors = expand_playbook(
        "code_audit",
        {"scope": "AgentCore monorepo", "modules": ["server", "desktop", "town"]},
    )
    assert errors == []
    by_id = _by_id(tasks)
    assert set(by_id) == {"audit_0", "audit_1", "audit_2", "audit_synth"}
    assert all(by_id[i]["role"] == "代码审计员" for i in ("audit_0", "audit_1", "audit_2"))
    assert by_id["audit_0"]["deliverable"]["artifacts"][0] == (
        f"{REVIEWS_DIR}/code-audit-0-server.md"
    )
    assert by_id["audit_1"]["deliverable"]["artifacts"][0] == (
        f"{REVIEWS_DIR}/code-audit-1-desktop.md"
    )
    synth = by_id["audit_synth"]
    assert synth["role"] == "审计主管"
    assert set(synth["depends_on"]) == {"audit_0", "audit_1", "audit_2"}
    assert synth["deliverable"]["artifacts"] == [f"{REVIEWS_DIR}/code-audit-summary.md"]
    assert "设计如此" in synth["deliverable"]["required_sections"]
    assert "设计如此栏" in synth["task"]
    assert "缺陷id|严重度|一句话" in synth["task"]
    assert "不得以 handoff 代落盘" in synth["task"]
    assert "一次交接" in synth["task"]
    assert "显著短于" in synth["task"] or "细节只进落盘" in synth["task"]
    assert "收口口径" in synth["task"]
    assert "通过验收" in synth["task"]
    plan, plan_errs = build_run_plan(tasks)
    assert plan_errs == []
    assert len(plan.waves()) >= 2


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


def test_available_playbooks_lists_code_audit():
    listing = available_playbooks()
    assert "code_audit" in listing
    assert "代码审计" in listing
    assert "4–8" in listing or "4-8" in listing
    assert "自然缝" in listing or "能少则少" in listing
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
    # 必填抽取仍在（建站常驻路径勿先 consult）
    assert "build_website→topic" in desc or ("build_website" in desc and "topic" in desc)


# ── parallel_brief ────────────────────────────────────────────────────────────


def test_parallel_brief_fans_out_notes_without_write_pipeline():
    tasks, errors = expand_playbook(
        "parallel_brief",
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
        assert "≤12 词" in t["task"]
        # 摸底验收：够用即停 + handoff 必交（提示词纪律，非完成硬闸）
        assert "摸底验收" in t["task"] or "够用即停" in t["task"]
        assert "定位" in t["task"] and "技术栈" in t["task"]
        assert "file_list" in t["task"] and ("grep" in t["task"] or "code_search" in t["task"])
        assert "每个 app" in t["task"] and "package.json" in t["task"]
        assert "禁止" in t["task"] and "名单" in t["task"]
        assert "已知路径" in t["task"]
        assert "含糊" in t["task"] and "根" in t["task"]
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


def test_parallel_brief_requires_topic_and_two_angles():
    tasks, errors = expand_playbook("parallel_brief", {"angles": ["甲", "乙"]})
    assert tasks == []
    assert any("topic" in e for e in errors)
    tasks2, errors2 = expand_playbook(
        "parallel_brief", {"topic": "X", "angles": ["仅一角"]}
    )
    assert tasks2 == []
    assert any("≥2" in e or "angles" in e for e in errors2)


def test_available_playbooks_lists_parallel_brief_before_research_report_semantics():
    listing = available_playbooks()
    assert "parallel_brief" in listing
    assert "对齐推进" in listing or "方向笔记" in listing
    assert "讨论对齐" in listing or "摸清" in listing
    assert "少扇出" in listing or "常 2" in listing
    assert "够用即停" in listing or "handoff" in listing
    assert "research_report" in listing
    assert "成文专线" in listing
    assert "明示" in listing
    assert "正式长文" in listing or "可提交" in listing or "审校满编" in listing
    assert "形态未定" in listing or "勿默认学术审校" in listing


# ── research_report ───────────────────────────────────────────────────────────


def test_research_report_fans_out_one_researcher_per_angle_then_outline_then_write():
    tasks, errors = expand_playbook(
        "research_report",
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
        # A3 查询契约进调研员任务书（超限自动规范化/截断并明示；仅极端过长拒）
        assert "≤12 词" in by_id[rid]["task"]
        assert "截断" in by_id[rid]["task"] or "规范化" in by_id[rid]["task"]
        assert "明示" in by_id[rid]["task"]
    outline_d = by_id["outline"]["deliverable"]
    assert outline_d["form"] == "files"
    assert outline_d["artifacts"] == ["AgentCore/文档/research/提纲.md"]
    assert "AgentCore/文档/research/提纲.md" in by_id["outline"]["task"]
    # Artifact-first writer brief：主路径一次完整 write；可选骨架；禁半章散文再 append。
    # 定案对齐：分波范围 + continue_from 待续 + md 禁 write_section。
    # （write_task 已在上方绑定；含 MD→PDF 纪律）
    assert "主路径" in write_task or "一次 file_write 完整" in write_task
    assert "短骨架" in write_task or "骨架" in write_task
    assert "禁止首写半章散文" in write_task
    assert "首写必须是短骨架" not in write_task
    assert "write_section" in write_task
    assert "continue_from_run_id" in write_task
    assert "章节范围" in write_task or "前几章" in write_task
    assert "artifact manifest" in write_task or "禁止再对本文件" in write_task
    assert "file_read" in write_task
    # each angle is named into its researcher's task so the fan-out doesn't run blind/overlapping.
    assert "选型" in by_id["research_1"]["task"]
    assert "read_notes" in by_id["research_1"]["task"]
    assert "post_note" in by_id["research_1"]["task"]
    # 引用即出处 P3：调研员成稿主张须证（#rN 或待核实）。
    assert "#rN" in by_id["research_1"]["task"]
    assert "待核实" in by_id["research_1"]["task"]
    # 深读姿态：关键法条 / 司法解释 / 判例须 read_url 核对原文。
    assert "read_url" in by_id["research_1"]["task"]
    assert "法条" in by_id["research_1"]["task"]
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
    assert "read_url" in by_id["review"]["task"]
    assert "法条" in by_id["review"]["task"]
    # 真纯丙：审校不再靠显式 tools 名单；定向检索纪律写在 task 正文。
    assert "tools" not in by_id["review"]
    assert "检索纪律" in by_id["review"]["task"]
    assert "grep" in by_id["review"]["task"]
    assert "code_search" in by_id["review"]["task"]
    assert "整目录" in by_id["review"]["task"]


def test_research_report_without_angles_uses_single_researcher():
    tasks, errors = expand_playbook("research_report", {"topic": "X"})
    assert errors == []
    by_id = _by_id(tasks)
    assert by_id["outline"]["depends_on"] == ["research_0"]
    assert by_id["outline"]["checkpoint_after"] is True  # default: checkpoint on outline
    assert by_id["review"]["depends_on"] == ["write"]
    # 单调研员路径同样钉住主张须证教法。
    assert "#rN" in by_id["research_0"]["task"]
    assert "待核实" in by_id["research_0"]["task"]
    assert by_id["research_0"]["search_policy"] == "academic_literature"
    assert "学术检索" in by_id["research_0"]["task"]
    # 无 angles 时默认约定文档路径（仍落 RESEARCH_DIR，不用角色名）。
    d = by_id["research_0"]["deliverable"]
    assert d["form"] == "files"
    assert d["artifacts"] == ["AgentCore/文档/research/调研要点.md"]
    assert by_id["outline"]["deliverable"]["artifacts"] == ["AgentCore/文档/research/提纲.md"]


def test_research_report_output_path_overrides_main_artifact():
    tasks, errors = expand_playbook(
        "research_report",
        {"topic": "T", "output_path": "paper/main.md"},
    )
    assert errors == []
    by_id = _by_id(tasks)
    assert by_id["write"]["deliverable"]["artifacts"] == ["paper/main.md"]
    assert "paper/main.md" in by_id["write"]["task"]
    assert "paper/main.md" in by_id["review"]["task"]


def test_research_report_checkpoint_can_be_disabled():
    tasks, errors = expand_playbook("research_report", {"topic": "X", "checkpoint": False})
    assert errors == []
    assert _by_id(tasks)["outline"]["checkpoint_after"] is False


def test_research_report_review_explicit_wall_clock_survives_build():
    """审校节点显式 timeout_ms=300000 经真实 builder 落成 policy.timeout_s=300；
    token 顶走统一 backstop（200k）。"""
    from agentcore.runtime.runs.worker_budget import WORKER_TIMEOUT_BACKSTOP_S

    tasks, errors = expand_playbook(
        "research_report", {"topic": "T", "angles": ["a", "b"]}
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


def test_research_report_requires_topic():
    tasks, errors = expand_playbook("research_report", {})
    assert tasks == []
    assert errors and "topic" in errors[0]


def test_research_report_folds_angle_fanout_with_note():
    """angles 超扇出上限：折叠进末节点（合并不丢弃），带 playbook_note。"""
    from agentcore.runtime.runs.playbooks import MAX_PLAYBOOK_FANOUT, collect_playbook_notes

    n = MAX_PLAYBOOK_FANOUT + 5
    tasks, errors = expand_playbook(
        "research_report", {"topic": "X", "angles": [f"a{i}" for i in range(n)]}
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


# ── build_feature ─────────────────────────────────────────────────────────────


def test_build_feature_defaults_to_api_plus_parallel_ui_and_test():
    tasks, errors = expand_playbook("build_feature", {"feature": "用户登录", "stack": "FastAPI+React"})
    assert errors == []
    by_id = _by_id(tasks)
    assert set(by_id) == {"api", "ui", "test"}
    # ui & test both fan out from api (share its dep set → parallel siblings on the same seam).
    assert by_id["ui"]["depends_on"] == ["api"]
    assert by_id["test"]["depends_on"] == ["api"]
    # the api task tells the worker to broadcast its interface contract on the note wall (4b 对账 hook).
    assert "post_note" in by_id["api"]["task"]
    assert "FastAPI+React" in by_id["api"]["task"]


def test_build_feature_code_nodes_land_in_workspace_not_dossier():
    """三个写码节点的产物是工作区源码 → 不得被派去 AI 工作间的默认落点。"""
    from agentcore.runtime.runs.builder import build_run_plan

    tasks, errors = expand_playbook("build_feature", {"feature": "用户登录"})
    assert errors == []
    assert all(t["deliverable"]["workspace_native"] is True for t in tasks)

    plan, plan_errors = build_run_plan(tasks, id_prefix="pb_bf")
    assert plan_errors == []
    assert all(n.deliverable and n.deliverable.artifact_dir == "" for n in plan.nodes)


def test_build_feature_include_filters_steps():
    tasks, _ = expand_playbook("build_feature", {"feature": "X", "include": ["ui"]})
    assert set(_by_id(tasks)) == {"api", "ui"}
    tasks, _ = expand_playbook("build_feature", {"feature": "X", "include": ["test"]})
    assert set(_by_id(tasks)) == {"api", "test"}


def test_build_feature_requires_feature():
    tasks, errors = expand_playbook("build_feature", {})
    assert tasks == []
    assert errors and "feature" in errors[0]


# ── build_app ─────────────────────────────────────────────────────────────────


def test_build_app_lean_three_nodes_default():
    """默认 intensity=lean：scaffold → implement → smoke（≤3 节点）。"""
    tasks, errors = expand_playbook(
        "build_app", {"app": "面向运营的 Vue3 数据看板", "stack": "Vue3+Vite+TS"}
    )
    assert errors == []
    by_id = _by_id(tasks)
    assert set(by_id) == {"scaffold", "implement", "smoke"}
    assert len(tasks) == 3
    assert by_id["implement"]["depends_on"] == ["scaffold"]
    assert by_id["smoke"]["depends_on"] == ["implement"]
    assert by_id["implement"]["role"] == "应用实现"
    assert "shared" not in by_id
    assert "integrate" not in by_id
    assert "module_0" not in by_id
    assert by_id["scaffold"]["deliverable"]["strict"] is True
    assert "铁律" in by_id["scaffold"]["task"]
    assert "悬空" in by_id["scaffold"]["task"]
    assert "npm" in by_id["smoke"]["task"].lower() or "build" in by_id["smoke"]["task"]
    assert "test_run" in by_id["smoke"]["task"]
    assert "code_execute" not in by_id["smoke"]["task"] or "勿" in by_id["smoke"]["task"]
    assert "自检全过" in by_id["smoke"]["task"] or "跑绿" in by_id["smoke"]["task"]
    assert "单测已绿" in by_id["smoke"]["task"] or "跑绿" in by_id["smoke"]["task"]
    scaffold_arts = by_id["scaffold"]["deliverable"]["artifacts"]
    assert scaffold_arts
    assert all(a.startswith("app/") for a in scaffold_arts)
    assert "vue3/" not in " ".join(scaffold_arts)
    stub_arts = [a for a in scaffold_arts if "/src/views/" in a and a.endswith(".vue")]
    assert len(stub_arts) == 1  # 默认仅总览页 stub
    impl_arts = by_id["implement"]["deliverable"]["artifacts"]
    assert stub_arts[0] in impl_arts
    assert all(a.startswith("app/") for a in impl_arts)
    assert any(a.endswith("tokens.css") for a in impl_arts)
    assert by_id["smoke"]["deliverable"]["artifacts"] == ["app/QA.md"]
    assert "总览页" in by_id["implement"]["task"]


def test_build_app_lean_modules_coverage_no_fanout():
    """lean：modules 只作文案/覆盖清单，不扇出 module_*。"""
    tasks, errors = expand_playbook(
        "build_app",
        {
            "app": "看板",
            "modules": ["仪表盘", "告警"],
            "root": "ops-board",
        },
    )
    assert errors == []
    by_id = _by_id(tasks)
    assert set(by_id) == {"scaffold", "implement", "smoke"}
    assert "ops-board/" in by_id["scaffold"]["task"]
    arts = by_id["scaffold"]["deliverable"]["artifacts"]
    assert any(a.startswith("ops-board/") for a in arts)
    assert "ops-board/src/views/" in " ".join(arts)
    assert "ops-board/src/router/index.ts" in arts
    assert "仪表盘" in by_id["implement"]["task"]
    assert "告警" in by_id["implement"]["task"]
    stub_arts = [a for a in arts if "/src/views/" in a and a.endswith(".vue")]
    assert len(stub_arts) == 2


def test_build_app_full_five_waves_default_modules():
    tasks, errors = expand_playbook(
        "build_app",
        {
            "app": "面向运营的 Vue3 数据看板",
            "stack": "Vue3+Vite+TS",
            "intensity": "full",
        },
    )
    assert errors == []
    by_id = _by_id(tasks)
    assert "scaffold" in by_id
    assert "shared" in by_id
    assert "module_0" in by_id
    assert "module_1" not in by_id  # 默认仅 1 模块
    assert "integrate" in by_id
    assert "smoke" in by_id
    assert len(tasks) == 5
    assert by_id["shared"]["depends_on"] == ["scaffold"]
    assert by_id["module_0"]["depends_on"] == ["shared"]
    assert set(by_id["integrate"]["depends_on"]) == {"module_0"}
    assert by_id["smoke"]["depends_on"] == ["integrate"]
    scaffold_arts = by_id["scaffold"]["deliverable"]["artifacts"]
    assert scaffold_arts
    assert all(a.startswith("app/") for a in scaffold_arts)
    assert "vue3/" not in " ".join(scaffold_arts)
    stub_arts = [a for a in scaffold_arts if "/src/views/" in a and a.endswith(".vue")]
    assert len(stub_arts) == 1
    assert by_id["module_0"]["deliverable"]["artifacts"][0] in scaffold_arts


def test_build_app_full_custom_modules_and_root():
    tasks, errors = expand_playbook(
        "build_app",
        {
            "app": "看板",
            "modules": ["仪表盘", "告警"],
            "root": "ops-board",
            "intensity": "full",
        },
    )
    assert errors == []
    by_id = _by_id(tasks)
    assert set(by_id) == {
        "scaffold",
        "shared",
        "module_0",
        "module_1",
        "integrate",
        "smoke",
    }
    assert "ops-board/" in by_id["scaffold"]["task"]
    arts = by_id["scaffold"]["deliverable"]["artifacts"]
    assert any(a.startswith("ops-board/") for a in arts)
    assert "ops-board/src/views/" in " ".join(arts)
    assert "ops-board/src/router/index.ts" in arts


def test_build_app_modules_fold_over_cap():
    """full：显式 modules 超过扇出上限 → 折叠到末槽，不丢弃。"""
    from agentcore.runtime.runs.build_app import _MAX_MODULE_FANOUT
    from agentcore.runtime.runs.playbooks import collect_playbook_notes

    mods = [f"模{i}" for i in range(_MAX_MODULE_FANOUT + 2)]
    tasks, errors = expand_playbook(
        "build_app", {"app": "大盘", "modules": mods, "intensity": "full"}
    )
    assert errors == []
    by_id = _by_id(tasks)
    module_nodes = [t for t in tasks if str(t["id"]).startswith("module_")]
    assert len(module_nodes) == _MAX_MODULE_FANOUT
    assert "module_0" in by_id and f"module_{_MAX_MODULE_FANOUT - 1}" in by_id
    assert f"module_{_MAX_MODULE_FANOUT}" not in by_id
    last = by_id[f"module_{_MAX_MODULE_FANOUT - 1}"]
    for name in mods[_MAX_MODULE_FANOUT - 1 :]:
        assert name in last["task"]
    notes = collect_playbook_notes(tasks)
    assert notes and "扇出折叠" in notes[0]
    scaffold_arts = by_id["scaffold"]["deliverable"]["artifacts"]
    assert scaffold_arts
    assert all(a.startswith("app/") for a in scaffold_arts)
    assert not any(a.startswith("大盘/") for a in scaffold_arts)
    stub_arts = [a for a in scaffold_arts if "/src/views/" in a and a.endswith(".vue")]
    assert len(stub_arts) == len(mods)


def test_build_app_rejects_unknown_intensity():
    tasks, errors = expand_playbook(
        "build_app", {"app": "看板", "intensity": "mega"}
    )
    assert tasks == []
    assert errors and "intensity" in errors[0]
    assert "mega" in errors[0]


def test_build_app_requires_app():
    tasks, errors = expand_playbook("build_app", {})
    assert tasks == []
    assert errors and "app" in errors[0]


def test_build_app_default_root_is_app_not_name_slug():
    """无显式 root 时工程根固定 app/，不从应用名派生 slug。"""
    assert "默认从 app 简述派生" not in PLAYBOOKS["build_app"].slots
    assert "默认固定 app/" in PLAYBOOKS["build_app"].slots
    tasks, errors = expand_playbook(
        "build_app", {"app": "Ops board", "modules": ["overview", "list"]}
    )
    assert errors == []
    by_id = _by_id(tasks)
    arts = by_id["scaffold"]["deliverable"]["artifacts"]
    assert arts
    assert all(a.startswith("app/") for a in arts)
    joined = " ".join(arts)
    assert "ops-board/" not in joined
    assert "ops_board/" not in joined
    impl_arts = by_id["implement"]["deliverable"]["artifacts"]
    assert all(a.startswith("app/") for a in impl_arts)
    assert by_id["smoke"]["deliverable"]["artifacts"] == ["app/QA.md"]


# ── repair_code ───────────────────────────────────────────────────────────────


def test_repair_code_diagnose_patch_verify_shape():
    tasks, errors = expand_playbook(
        "repair_code",
        {
            "problem": "Module missing export foo",
            "verify": "npx tsc -b",
            "target": "src/app.ts",
        },
    )
    assert errors == []
    by_id = _by_id(tasks)
    assert set(by_id) == {"diagnose", "patch", "verify"}
    assert by_id["patch"]["depends_on"] == ["diagnose"]
    assert by_id["verify"]["depends_on"] == ["patch"]
    assert by_id["diagnose"]["max_rounds"] == 4
    assert by_id["patch"]["max_rounds"] == 6
    assert by_id["patch"]["deliverable"]["form"] == "files"
    # 就地改工作区源码 → 无约定落点（不套 AI 工作间）。
    assert by_id["patch"]["deliverable"]["workspace_native"] is True
    assert "requires_files" not in by_id["patch"]["deliverable"]
    assert "name" not in by_id["patch"]["deliverable"]
    assert "src/app.ts" in by_id["patch"]["deliverable"]["artifacts"]
    # 真纯丙：repair_* 不再靠显式 tools 名单收窄；纪律写在 task 正文。
    assert "tools" not in by_id["diagnose"]
    assert "tools" not in by_id["patch"]
    assert "tools" not in by_id["verify"]
    assert "code_diagnostics" in by_id["patch"]["task"]
    assert "test_run" in by_id["verify"]["task"]
    assert "npx tsc -b" in by_id["verify"]["task"]
    assert "纯 prose" in by_id["verify"]["task"]
    assert "禁止在本步改文件" in by_id["diagnose"]["task"]
    assert "str_replace" in by_id["patch"]["task"]
    # 白屏/UI 分流：诊断先 browser；验证 CLI vs UI；禁 typecheck 冒充白屏。
    assert "browser_navigate" in by_id["diagnose"]["task"]
    assert "snapshot" in by_id["diagnose"]["task"]
    assert "勿空等用户 F12" in by_id["diagnose"]["task"]
    assert "CLI" in by_id["verify"]["task"]
    assert "browser_navigate" in by_id["verify"]["task"]
    assert "冒充白屏" in by_id["verify"]["task"]
    assert "verify_policy=inner" not in by_id["verify"]["task"]
    assert "verify_policy" not in by_id["verify"]
    # 原 min_length 迁出：可消费短文 / 通过或失败证据写在 task，勿填已删键。
    assert "可消费短文" in by_id["diagnose"]["task"]
    assert "通过或失败证据" in by_id["verify"]["task"]
    assert "min_length" not in by_id["diagnose"]["deliverable"]
    assert "min_length" not in by_id["verify"]["deliverable"]
    assert by_id["diagnose"]["deliverable"]["form"] == "prose"
    assert by_id["verify"]["deliverable"]["form"] == "prose"


def test_repair_code_patch_without_target_still_lands_in_workspace():
    """无 target/artifacts 的 patch 节点最易被默认落点误导——这里钉死无落点。"""
    from agentcore.runtime.runs.builder import build_run_plan

    tasks, errors = expand_playbook(
        "repair_code", {"problem": "Dashboard 白屏", "verify": "pytest -q"}
    )
    assert errors == []
    patch = _by_id(tasks)["patch"]
    assert patch["deliverable"] == {"form": "files", "workspace_native": True}

    plan, plan_errors = build_run_plan(tasks, id_prefix="pb_rc")
    assert plan_errors == []
    node = next(n for n in plan.nodes if n.run_id.endswith("patch"))
    assert node.deliverable is not None
    assert node.deliverable.artifact_dir == ""


def test_repair_code_ui_verify_slot_flows_into_verify_task():
    """UI 复现形 verify 原样注入验证员约定；slots 并列 CLI 与 UI 例示。"""
    from agentcore.runtime.runs.playbooks import PLAYBOOKS

    tasks, errors = expand_playbook(
        "repair_code",
        {
            "problem": "Dashboard 白屏",
            "verify": "打开 /app 白屏消失+snapshot 可见主内容",
        },
    )
    assert errors == []
    by_id = _by_id(tasks)
    assert "打开 /app 白屏消失+snapshot 可见主内容" in by_id["verify"]["task"]
    assert "页面/UI 复现" in by_id["verify"]["task"]
    pb = PLAYBOOKS["repair_code"]
    assert "pytest tests/test_app.py -q" in pb.slots
    assert "白屏消失" in pb.slots or "snapshot 可见主内容" in pb.slots
    assert "CLI" in pb.summary or "UI" in pb.summary
    assert "白屏" in pb.summary


def test_repair_code_requires_problem():
    tasks, errors = expand_playbook("repair_code", {})
    assert tasks == []
    assert errors and "problem" in errors[0]


def test_repair_code_requires_verify_how_fixed():
    tasks, errors = expand_playbook(
        "repair_code",
        {"problem": "Module missing export foo", "target": "src/app.ts"},
    )
    assert tasks == []
    assert errors and "verify" in errors[0]
    # 缺 verify 时错误文案并列 CLI 与 UI 例示
    assert "pytest" in errors[0] or "CLI" in errors[0]
    assert "白屏" in errors[0] or "snapshot" in errors[0]


# ── build_website ─────────────────────────────────────────────────────────────


def test_build_website_three_chain_default_sections():
    tasks, errors = expand_playbook(
        "build_website",
        {"topic": "GEO 官网落地页", "stack": "静态 HTML", "audience": "中小商家"},
    )
    assert errors == []
    by_id = _by_id(tasks)
    assert set(by_id) == {"copy", "frontend", "qa"}
    assert len(tasks) == 3
    assert by_id["frontend"]["depends_on"] == ["copy"]
    assert by_id["qa"]["depends_on"] == ["frontend"]
    assert by_id["copy"]["role"] == "内容文案"
    assert by_id["frontend"]["role"] == "前端开发者"
    assert by_id["qa"]["role"] == "页面 QA"
    # 全节点 form=files + 约定路径
    assert by_id["copy"]["deliverable"]["form"] == "files"
    assert by_id["copy"]["deliverable"]["artifacts"] == ["site/copy.md"]
    assert by_id["copy"]["deliverable"].get("strict") is True
    copy_secs = by_id["copy"]["deliverable"]["required_sections"]
    assert copy_secs[0] == "视觉 thesis"
    assert "品牌一句话" in copy_secs
    assert by_id["copy"]["deliverable"].get("must_contain_soft") is True
    assert "visual thesis" in by_id["copy"]["task"]
    assert "anti-slop" in by_id["copy"]["task"]
    assert "首屏英雄区" in by_id["copy"]["task"]
    assert "卖点能力区" in by_id["copy"]["task"]
    assert "行动号召区" in by_id["copy"]["task"]
    # 前端一人包 DESIGN + 整页 + CONTRACT
    assert by_id["frontend"]["deliverable"]["form"] == "files"
    assert by_id["frontend"]["deliverable"]["artifacts"] == [
        "site/DESIGN.md",
        "site/index.html",
        "site/styles.css",
        "site/main.js",
        "site/CONTRACT.md",
    ]
    assert by_id["frontend"]["deliverable"].get("web_quality_scan") is True
    assert by_id["frontend"]["deliverable"].get("strict") is True
    assert by_id["frontend"]["deliverable"]["placeholder_hard_exempt_artifacts"] == [
        "site/CONTRACT.md",
        "site/DESIGN.md",
    ]
    assert "site/copy.md" in by_id["frontend"]["task"]
    assert "DESIGN" in by_id["frontend"]["task"]
    # 无风格确认 → design_prompt_block 软注入 s_default 正向配方
    assert "正向配方" in by_id["frontend"]["task"]
    assert "单一视觉焦点" in by_id["frontend"]["task"]
    assert "静态 HTML" in by_id["frontend"]["task"]
    assert "pack=marketing" in by_id["frontend"]["task"]
    # QA
    assert by_id["qa"]["deliverable"].get("web_quality_scan") is True
    assert by_id["qa"]["deliverable"].get("visual_critic") is True
    assert by_id["qa"]["deliverable"].get("strict") is True
    assert by_id["qa"].get("ceiling_priority") is True
    assert by_id["qa"]["deliverable"]["form"] == "files"
    assert by_id["qa"]["deliverable"]["artifacts"] == ["site/QA.md"]
    assert by_id["qa"]["deliverable"]["web_seam_scope"] == "site/"
    assert by_id["qa"]["deliverable"]["placeholder_hard_exempt"] is True
    assert "web_seam" in by_id["qa"]["task"]
    assert "browser_screenshot" in by_id["qa"]["task"]
    assert "未目验" in by_id["qa"]["task"] or "谎称" in by_id["qa"]["task"]
    assert by_id["qa"]["timeout_ms"] == 300_000
    # 文案 / 受众嵌入任务书
    assert "GEO 官网落地页" in by_id["copy"]["task"]
    assert "中小商家" in by_id["copy"]["task"]
    # 无旧五波节点
    assert "design" not in by_id
    assert "skeleton" not in by_id
    assert "assemble" not in by_id
    assert not any(t["id"].startswith("section_") for t in tasks)


def test_build_website_sections_coverage_only_no_fanout():
    """sections 仅作文案/前端覆盖清单，节点数恒为 3。"""
    tasks, errors = expand_playbook(
        "build_website",
        {"topic": "S", "sections": ["导航", "定价", "FAQ"]},
    )
    assert errors == []
    by_id = _by_id(tasks)
    assert set(by_id) == {"copy", "frontend", "qa"}
    assert "导航" in by_id["copy"]["task"] and "导航" in by_id["frontend"]["task"]
    assert "定价" in by_id["copy"]["task"] and "定价" in by_id["frontend"]["task"]
    assert "FAQ" in by_id["copy"]["task"] and "FAQ" in by_id["frontend"]["task"]
    assert not any(t["id"].startswith("section_") for t in tasks)
    assert "assemble" not in by_id

    eight = [f"区{i}" for i in range(8)]
    tasks8, errors8 = expand_playbook("build_website", {"topic": "S", "sections": eight})
    assert errors8 == []
    assert len(tasks8) == 3
    by_id8 = _by_id(tasks8)
    assert set(by_id8) == {"copy", "frontend", "qa"}
    assert "区0" in by_id8["copy"]["task"]
    assert "区7" in by_id8["frontend"]["task"]


def test_build_website_custom_sections_still_three_nodes():
    tasks, errors = expand_playbook(
        "build_website",
        {"topic": "S", "sections": ["导航", "定价"]},
    )
    assert errors == []
    by_id = _by_id(tasks)
    assert set(by_id) == {"copy", "frontend", "qa"}
    assert by_id["qa"]["depends_on"] == ["frontend"]
    assert by_id["copy"]["deliverable"]["artifacts"] == ["site/copy.md"]
    assert "定价" in by_id["frontend"]["task"]


def test_build_website_requires_topic():
    tasks, errors = expand_playbook("build_website", {})
    assert tasks == []
    assert errors and "topic" in errors[0]
    # 旧键 site 不兼容：缺 topic 即报错，不映射
    legacy, legacy_err = expand_playbook("build_website", {"site": "旧简述"})
    assert legacy == []
    assert legacy_err and "topic" in legacy_err[0]


def test_build_website_topic_brief_aliases():
    """purpose/brief/description → topic；有 topic 时不以别名覆盖；site 永不映射。"""
    for key in ("purpose", "brief", "description"):
        tasks, errors = expand_playbook("build_website", {key: f"简述via_{key}"})
        assert errors == []
        assert len(tasks) == 3
        assert f"简述via_{key}" in tasks[0]["task"]

    prefer, prefer_err = expand_playbook(
        "build_website", {"topic": "规范键", "purpose": "别名不应覆盖"}
    )
    assert prefer_err == []
    assert "规范键" in prefer[0]["task"]
    assert "别名不应覆盖" not in prefer[0]["task"]

    verify, verify_err = expand_playbook(
        "build_website_verify", {"purpose": "续验简述"}
    )
    assert verify_err == []
    assert len(verify) == 1
    assert "续验简述" in verify[0]["task"]


def test_build_website_style_toolshed_three_chain_injects_tool_dense():
    """build_website + style=toolshed forces tool_dense + domain=tool."""
    from agentcore.runtime.runs.website_catalog import (
        PACK_TOOL_DENSE,
        TOOL_DENSE_POINTER_PREFIX,
    )

    tasks, errors = expand_playbook(
        "build_website",
        {
            "topic": "订单运营控制台",
            "style": "toolshed",
            "sections": ["应用外壳", "侧栏导航", "数据表格"],
        },
    )
    assert errors == []
    by_id = {t["id"]: t for t in tasks}
    assert set(by_id) == {"copy", "frontend", "qa"}
    assert len(tasks) == 3
    assert by_id["frontend"]["depends_on"] == ["copy"]
    assert by_id["qa"]["depends_on"] == ["frontend"]
    fe = by_id["frontend"]["task"]
    assert f"pack={PACK_TOOL_DENSE}" in fe
    assert "catalog:app_shell" in fe
    assert f"{TOOL_DENSE_POINTER_PREFIX}/app_shell.html" in fe
    assert "catalog:sidebar" in fe
    assert "catalog:data_table" in fe
    assert "审美域·工具页" in by_id["copy"]["task"]
    assert "信息架构" in by_id["copy"]["task"]
    assert "正向配方·工具台" in fe
    assert "#2563eb" in fe
    assert "单一视觉焦点" not in fe
    toolshed_secs = by_id["copy"]["deliverable"]["required_sections"]
    assert toolshed_secs[0] == "信息架构"
    assert "产品一句话" in toolshed_secs
    assert by_id["copy"]["deliverable"].get("must_contain_soft") is True
    assert "website_catalog/marketing/" not in fe
    assert by_id["frontend"]["deliverable"]["artifacts"] == [
        "site/DESIGN.md",
        "site/index.html",
        "site/styles.css",
        "site/main.js",
        "site/CONTRACT.md",
    ]
    # 无旧五波节点
    assert "design" not in by_id
    assert "skeleton" not in by_id
    assert "assemble" not in by_id
    assert not any(t["id"].startswith("section_") for t in tasks)


def test_build_toolshed_playbook_removed():
    """旧独立 playbook 名直接未知失败——无别名 / 静默改写。"""
    tasks, errors = expand_playbook("build_toolshed", {"topic": "Ops"})
    assert tasks == []
    assert errors and "未知" in errors[0]
    assert "build_toolshed" in errors[0]


def test_build_website_rejects_unknown_style():
    tasks, errors = expand_playbook(
        "build_website", {"topic": "S", "style": "neon"}
    )
    assert tasks == []
    assert errors and "style" in errors[0]
    assert "neon" in errors[0]


def test_build_website_solo_single_frontend_node():
    """intensity=solo：单节点 frontend，文案+DESIGN+页面合并，无独立 copy/qa。"""
    tasks, errors = expand_playbook(
        "build_website",
        {"topic": "GEO 单页", "intensity": "solo", "sections": ["英雄区", "CTA"]},
    )
    assert errors == []
    by_id = _by_id(tasks)
    assert set(by_id) == {"frontend"}
    assert len(tasks) == 1
    fe = by_id["frontend"]
    assert fe["role"] == "前端开发者"
    assert fe.get("depends_on") in (None, [], ())
    arts = fe["deliverable"]["artifacts"]
    assert arts == [
        "site/copy.md",
        "site/DESIGN.md",
        "site/index.html",
        "site/styles.css",
        "site/main.js",
        "site/CONTRACT.md",
    ]
    assert fe["deliverable"].get("web_quality_scan") is True
    assert fe["deliverable"].get("strict") is True
    assert "视觉 thesis" in fe["deliverable"]["required_sections"]
    assert "intensity=solo" in fe["task"] or "单人整页" in fe["task"]
    assert "英雄区" in fe["task"] and "CTA" in fe["task"]
    assert "轻验收" in fe["task"]
    assert "copy" not in by_id
    assert "qa" not in by_id


def test_build_website_solo_toolshed_keeps_style_pack():
    """solo 仍尊重 style=toolshed pack / domain。"""
    from agentcore.runtime.runs.website_catalog import PACK_TOOL_DENSE

    tasks, errors = expand_playbook(
        "build_website",
        {"topic": "控制台", "intensity": "solo", "style": "toolshed"},
    )
    assert errors == []
    assert len(tasks) == 1
    fe = tasks[0]
    assert fe["id"] == "frontend"
    assert f"pack={PACK_TOOL_DENSE}" in fe["task"]
    assert "信息架构" in fe["deliverable"]["required_sections"]
    assert "审美域·工具页" in fe["task"] or "工具台" in fe["task"]


def test_build_website_rejects_unknown_intensity():
    tasks, errors = expand_playbook(
        "build_website", {"topic": "S", "intensity": "turbo"}
    )
    assert tasks == []
    assert errors and "intensity" in errors[0]
    assert "turbo" in errors[0]


def test_build_website_verify_qa_only_no_rebuild():
    """Second-act verify: single QA node, deferred_ok=False, requires topic."""
    tasks, errors = expand_playbook("build_website_verify", {"topic": "GEO 官网"})
    assert errors == []
    assert len(tasks) == 1
    qa = tasks[0]
    assert qa["id"] == "qa"
    assert qa.get("depends_on") in (None, [], ())
    assert "勿重做文案" in qa["task"] or "勿重做" in qa["task"]
    assert "预算不足可跳过" not in qa["task"]
    assert qa["deliverable"]["artifacts"] == ["site/QA.md"]
    assert qa["deliverable"].get("visual_critic") is True
    assert qa.get("ceiling_priority") is True

    empty, err = expand_playbook("build_website_verify", {})
    assert empty == []
    assert err and "topic" in err[0]
    legacy, legacy_err = expand_playbook("build_website_verify", {"site": "旧简述"})
    assert legacy == []
    assert legacy_err and "topic" in legacy_err[0]


def test_build_website_qa_shares_helper_deferred_ok():
    tasks, _ = expand_playbook("build_website", {"topic": "S", "sections": ["A"]})
    qa = next(t for t in tasks if t["id"] == "qa")
    assert "预算不足可跳过" in qa["task"]
    assert "站点【S】" in qa["task"]


def test_build_website_files_form_builds_run_plan():
    """form=files + artifacts 经真实 builder 接通；三串 DAG 可 waves()。"""
    tasks, errors = expand_playbook(
        "build_website", {"topic": "T", "sections": ["A", "B"]}
    )
    assert errors == []
    plan, plan_errors = build_run_plan(tasks, id_prefix="pb_bw")
    assert plan_errors == []
    assert len(plan.nodes) == 3  # copy + frontend + qa
    waves = plan.waves()
    assert waves  # no cycle
    by_role = {n.role: n for n in plan.nodes}
    assert by_role["内容文案"].deliverable is not None
    assert by_role["内容文案"].deliverable.form == "files"
    assert by_role["内容文案"].deliverable.strict is True
    assert by_role["前端开发者"].deliverable.artifacts == [
        "site/DESIGN.md",
        "site/index.html",
        "site/styles.css",
        "site/main.js",
        "site/CONTRACT.md",
    ]
    assert by_role["前端开发者"].deliverable.placeholder_hard_exempt_artifacts == [
        "site/CONTRACT.md",
        "site/DESIGN.md",
    ]
    assert by_role["页面 QA"].deliverable.form == "files"
    assert by_role["页面 QA"].deliverable.placeholder_hard_exempt is True
    assert by_role["页面 QA"].policy.timeout_s == 300
    assert by_role["页面 QA"].ceiling_priority is True
    assert "设计契约" not in by_role
    assert "骨架工程师" not in by_role
    assert "页面组装" not in by_role


def test_build_website_many_sections_still_three_nodes_run_plan():
    """多分区仍三节点，经真实 builder 接通。"""
    eight = [f"区{i}" for i in range(8)]
    tasks, errors = expand_playbook("build_website", {"topic": "T", "sections": eight})
    assert errors == []
    plan, plan_errors = build_run_plan(tasks, id_prefix="pb_bw8")
    assert plan_errors == []
    assert len(plan.nodes) == 3
    assert plan.waves()
    by_role = {n.role: n for n in plan.nodes}
    assert by_role["内容文案"].deliverable.artifacts == ["site/copy.md"]
    assert by_role["前端开发者"].deliverable.artifacts[0] == "site/DESIGN.md"
    assert "页面 QA" in by_role


# ── compare_options ───────────────────────────────────────────────────────────


def test_compare_options_evaluates_each_then_summarises():
    tasks, errors = expand_playbook(
        "compare_options",
        {"question": "选 Postgres 还是 MySQL", "options": ["Postgres", "MySQL"], "criteria": ["性能", "生态"]},
    )
    assert errors == []
    by_id = _by_id(tasks)
    assert {"eval_0", "eval_1", "summary"} == set(by_id)
    assert set(by_id["summary"]["depends_on"]) == {"eval_0", "eval_1"}
    # each evaluator is pinned to ONE option and carries the criteria.
    assert "Postgres" in by_id["eval_0"]["task"] and "性能" in by_id["eval_0"]["task"]


def test_compare_options_requires_question_and_two_options():
    _, errors = expand_playbook("compare_options", {"options": ["only-one"]})
    joined = "；".join(errors)
    assert "question" in joined and "options" in joined


def test_compare_options_rejects_over_fanout():
    """options>6：显式拒绝，不折叠、不静默截断。"""
    from agentcore.runtime.runs.playbooks import MAX_PLAYBOOK_FANOUT

    opts = [f"opt{i}" for i in range(MAX_PLAYBOOK_FANOUT + 1)]
    tasks, errors = expand_playbook(
        "compare_options", {"question": "Q", "options": opts}
    )
    assert tasks == []
    assert errors and "上限" in errors[0]
    assert "短名单" in errors[0] or "收敛" in errors[0]
    assert str(MAX_PLAYBOOK_FANOUT + 1) in errors[0] or str(len(opts)) in errors[0]
    # Exactly at cap still works.
    tasks_ok, errors_ok = expand_playbook(
        "compare_options",
        {"question": "Q", "options": [f"opt{i}" for i in range(MAX_PLAYBOOK_FANOUT)]},
    )
    assert errors_ok == []
    assert len([t for t in tasks_ok if t["id"].startswith("eval_")]) == MAX_PLAYBOOK_FANOUT


# ── multi_lens_research ───────────────────────────────────────────────────────


def test_multi_lens_research_default_four_lenses_plus_synthesizer():
    tasks, errors = expand_playbook(
        "multi_lens_research", {"topic": "LV 诉茉莉奶白商标案"}
    )
    assert errors == []
    by_id = _by_id(tasks)
    lens_ids = [f"lens_{i}" for i in range(4)]
    assert set(by_id) == {*lens_ids, "synthesizer"}
    assert set(by_id["synthesizer"]["depends_on"]) == set(lens_ids)
    assert by_id["synthesizer"]["role"] == "汇总分析师"
    # 默认四异质透镜角色名嵌入 role
    roles = {by_id[lid]["role"] for lid in lens_ids}
    assert roles == {"法律视角", "品牌商业视角", "舆情公关视角", "文化社会视角"}
    # 幕 1 约定文档：各透镜自写 research/{透镜}透镜报告.md（form=files + artifacts）
    expected_lens_artifacts = {
        "AgentCore/文档/research/法律透镜报告.md",
        "AgentCore/文档/research/品牌商业透镜报告.md",
        "AgentCore/文档/research/舆情公关透镜报告.md",
        "AgentCore/文档/research/文化社会透镜报告.md",
    }
    for lid in lens_ids:
        d = by_id[lid]["deliverable"]
        assert d["form"] == "files"
        assert d["artifacts"] and d["artifacts"][0] in expected_lens_artifacts
        assert "file_write" in by_id[lid]["task"]
        assert d["artifacts"][0] in by_id[lid]["task"]
        assert "完整" in by_id[lid]["task"]  # 完整报告，非摘要复制
        assert "handoff" in by_id[lid]["task"]  # 落盘叠加，不得替代 handoff
        # 引用即出处 P3：透镜成稿主张须证（#rN 或待核实；不强迫辩词二分）。
        assert "#rN" in by_id[lid]["task"] or "#r1" in by_id[lid]["task"]
        assert "待核实" in by_id[lid]["task"]
        assert "不强迫" in by_id[lid]["task"]
    # 汇总员落盘汇总与命题卡；motion_card 仍走 handoff
    synth_d = by_id["synthesizer"]["deliverable"]
    assert synth_d["form"] == "files"
    assert synth_d["artifacts"] == ["AgentCore/文档/research/汇总与命题卡.md"]
    synth_task = by_id["synthesizer"]["task"]
    assert "AgentCore/文档/research/汇总与命题卡.md" in synth_task
    assert "file_write" in synth_task
    assert "motion_card" in synth_task
    assert "handoff" in synth_task
    assert "继续调研" in synth_task or "对抗" in synth_task
    assert "见分歧" in synth_task
    # 存在真对立轴则必须产卡（升格条款）
    assert "真对立轴" in synth_task
    assert "必须" in synth_task and "motion_card" in synth_task
    # 结构化字段唯一载体：禁止正文表 / 散文 / 自写 Followups 冒充
    assert "对象" in synth_task or "结构化" in synth_task
    assert "Followups" in synth_task or "芯片" in synth_task
    # 命题保真教法：锚定对象/形态；模拟法庭=本案对抗；禁抬制度层
    assert "命题保真" in synth_task
    assert "模拟法庭" in synth_task or "庭审" in synth_task
    assert "制度" in synth_task
    assert "替换命题对象" in synth_task or "抬成制度层" in synth_task
    # P3：汇总继承关键数字须带 #rN 或待核实语
    assert "待核实" in synth_task
    assert "#rN" in synth_task


def test_multi_lens_research_injects_user_message_into_synthesizer():
    """机制：expand 时注入用户原话全文到汇总员任务书（不依赖 CEO topic）。"""
    user_line = "茉莉奶白使用四叶花卉图形是否侵犯 LV 商标权，进行模拟法庭"
    tasks, errors = expand_playbook(
        "multi_lens_research",
        {"topic": "LV 诉茉莉奶白"},  # 故意丢「模拟法庭」——任务书仍须含原话
        user_message=user_line,
    )
    assert errors == []
    synth_task = _by_id(tasks)["synthesizer"]["task"]
    assert user_line in synth_task
    assert "用户原话" in synth_task or "机制注入" in synth_task
    # 透镜任务书不强制塞全文（只汇总员需要保真锚）
    assert user_line not in _by_id(tasks)["lens_0"]["task"]


def test_multi_lens_research_without_user_message_omits_anchor_block():
    tasks, errors = expand_playbook("multi_lens_research", {"topic": "X"})
    assert errors == []
    synth_task = _by_id(tasks)["synthesizer"]["task"]
    assert "机制注入" not in synth_task
    # 教法条款仍在（不依赖原话块）
    assert "命题保真" in synth_task


def test_multi_lens_research_custom_lenses():
    tasks, errors = expand_playbook(
        "multi_lens_research",
        {"topic": "X", "lenses": ["技术", "伦理", "监管"]},
    )
    assert errors == []
    by_id = _by_id(tasks)
    assert set(by_id["synthesizer"]["depends_on"]) == {"lens_0", "lens_1", "lens_2"}
    assert by_id["lens_1"]["role"] == "伦理视角"
    assert "伦理" in by_id["lens_1"]["task"]
    assert by_id["lens_1"]["deliverable"]["artifacts"] == ["AgentCore/文档/research/伦理透镜报告.md"]


def test_multi_lens_research_folds_lenses_with_note_keeps_base_owner():
    """lenses 超扇出：折叠进末节点带 note；首透镜仍独占公共底料分工。"""
    from agentcore.runtime.runs.playbooks import MAX_PLAYBOOK_FANOUT, collect_playbook_notes

    n = MAX_PLAYBOOK_FANOUT + 2
    lenses = [f"透镜{i}" for i in range(n)]
    tasks, errors = expand_playbook(
        "multi_lens_research", {"topic": "T", "lenses": lenses}
    )
    assert errors == []
    by_id = _by_id(tasks)
    lens_nodes = [t for t in tasks if t["id"].startswith("lens_")]
    assert len(lens_nodes) == MAX_PLAYBOOK_FANOUT
    last = by_id[f"lens_{MAX_PLAYBOOK_FANOUT - 1}"]
    for name in lenses[MAX_PLAYBOOK_FANOUT - 1 :]:
        assert name in last["role"] or name in last["task"]
    notes = collect_playbook_notes(tasks)
    assert notes and "扇出折叠" in notes[0]
    # First lens remains single primary base owner (fold only hits the last slot).
    assert by_id["lens_0"]["role"] == "透镜0视角"
    assert "负责人" in by_id["lens_0"]["task"] or "查全" in by_id["lens_0"]["task"]
    # playbook 不再显式写 retrieval_budget；统一默认由 builder 填。
    assert "retrieval_budget" not in by_id["lens_0"]
    assert "retrieval_budget" not in by_id[f"lens_{MAX_PLAYBOOK_FANOUT - 1}"]
    assert "负责人" not in last["task"]


def test_multi_lens_research_lens_retrieval_division():
    """教法：首透镜查全公共底料；其余透镜简要确认、预算盯独有缺口；并行无运行时依赖。"""
    tasks, errors = expand_playbook(
        "multi_lens_research", {"topic": "LV 诉茉莉奶白商标案"}
    )
    assert errors == []
    by_id = _by_id(tasks)
    base = by_id["lens_0"]["task"]
    assert "检索分工" in base
    assert "公共基础事实" in base or "公共底料" in base
    assert "时间线" in base and "主体" in base
    assert "负责人" in base or "查全" in base
    assert "并行" in base or "互不等待" in base
    assert "retrieval_budget" not in by_id["lens_0"]
    for lid in ("lens_1", "lens_2", "lens_3"):
        task = by_id[lid]["task"]
        assert "检索分工" in task
        assert "简要确认" in task
        assert "独有" in task
        assert "负责人" not in task  # 非首透镜不背公共底料全责
        assert "retrieval_budget" not in by_id[lid]
    # 汇总员任务不动（命题保真已定案；本条只改透镜）
    synth = by_id["synthesizer"]["task"]
    assert "检索分工" not in synth
    assert "命题保真" in synth
    assert "retrieval_budget" not in by_id["synthesizer"]


def test_multi_lens_research_lens_budgets_survive_build_run_plan():
    """透镜无显式预算时经 builder 得统一默认。"""
    from agentcore.runtime.runs.retrieval_budget import DEFAULT_RETRIEVAL_BUDGET

    tasks, errors = expand_playbook(
        "multi_lens_research", {"topic": "T", "lenses": ["法律", "品牌商业"]}
    )
    assert errors == []
    plan, plan_errors = build_run_plan(tasks, id_prefix="pb_mlr_budget")
    assert plan_errors == []
    by_role = {n.role: n for n in plan.nodes}
    assert by_role["法律视角"].retrieval_budget == DEFAULT_RETRIEVAL_BUDGET
    assert by_role["品牌商业视角"].retrieval_budget == DEFAULT_RETRIEVAL_BUDGET


def test_multi_lens_research_files_form_builds_run_plan_with_artifacts():
    """form=files + artifacts 经真实 builder 接通写盘验收。"""
    tasks, errors = expand_playbook(
        "multi_lens_research", {"topic": "T", "lenses": ["法律", "品牌商业"]}
    )
    assert errors == []
    plan, plan_errors = build_run_plan(tasks, id_prefix="pb_mlr_files")
    assert plan_errors == []
    by_role = {n.role: n for n in plan.nodes}
    legal = by_role["法律视角"]
    assert legal.deliverable is not None
    assert legal.deliverable.form == "files"
    assert legal.deliverable.artifacts == ["AgentCore/文档/research/法律透镜报告.md"]
    synth = by_role["汇总分析师"]
    assert synth.deliverable is not None
    assert synth.deliverable.form == "files"
    assert synth.deliverable.artifacts == ["AgentCore/文档/research/汇总与命题卡.md"]


def test_multi_lens_research_requires_topic():
    tasks, errors = expand_playbook("multi_lens_research", {})
    assert tasks == []
    assert errors and "topic" in errors[0]


# ── registry reject paths ─────────────────────────────────────────────────────


def test_expand_unknown_playbook_lists_available():
    tasks, errors = expand_playbook("nope", {})
    assert tasks == []
    assert errors and "未知 playbook" in errors[0]
    for name in PLAYBOOKS:
        assert name in errors[0]


def test_expand_rejects_non_object_args():
    tasks, errors = expand_playbook("research_report", ["not", "a", "dict"])  # type: ignore[arg-type]
    assert tasks == []
    assert errors and "playbook_args" in errors[0]


def test_available_playbooks_lists_all_registered():
    listing = available_playbooks()
    assert set(PLAYBOOKS) == {
        "code_audit",
        "parallel_brief",
        "research_report",
        "build_feature",
        "repair_code",
        "build_app",
        "build_website",
        "build_website_verify",
        "compare_options",
        "multi_lens_research",
    }
    for name in PLAYBOOKS:
        assert name in listing
    assert "build_toolshed" not in PLAYBOOKS


# ── every expansion is a runnable plan (the real builder, not a mock) ──────────


def test_every_playbook_expansion_builds_a_valid_run_plan():
    samples = {
        "code_audit": {"scope": "apps/server", "modules": ["auth", "storage"]},
        "parallel_brief": {"topic": "T", "angles": ["a", "b", "c"]},
        "research_report": {"topic": "T", "angles": ["a", "b"], "checkpoint": True},
        "build_feature": {"feature": "F", "stack": "S"},
        "repair_code": {
            "problem": "missing export",
            "verify": "pytest -q",
            "target": "app.ts",
        },
        "build_app": {"app": "Ops board", "modules": ["overview", "list"]},
        "build_website": {"topic": "Landing", "sections": ["hero", "cta"]},
        "build_website_verify": {"topic": "Landing"},
        "compare_options": {"question": "Q", "options": ["A", "B", "C"]},
        "multi_lens_research": {"topic": "T"},
    }
    expected_nodes = {
        "code_audit": 3,  # 2 auditors + synth
        "parallel_brief": 3,
        "research_report": 5,
        "build_feature": 3,
        "repair_code": 3,
        "build_app": 3,  # lean 默认：scaffold + implement + smoke
        "build_website": 3,  # standard 默认：copy + frontend + qa
        "build_website_verify": 1,  # qa only
        "compare_options": 4,
        "multi_lens_research": 5,  # 4 lenses + synthesizer
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
        if name == "repair_code":
            assert all(
                (n.max_rounds or 0) > 0 and (n.max_rounds or 99) <= 6 for n in plan.nodes
            )
