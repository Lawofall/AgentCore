"""Tests for system Skills + consult (提示词瘦身 P2 — 渐进披露).

Covers the three moving parts of the prompt-slimming slice:

1. ``SkillRegistry`` / ``build_system_skill_registry`` — name lookup (hit/miss) and
   the ``requires_tools`` visibility filter.
2. ``render_skill_directory`` — the always-on 按需目录 lists only skills whose required
   tools are wired this turn (so it never advertises a capability the CEO lacks).
3. ``ConsultTool`` — returns a skill's full body (CONTINUE) on a hit, and
    degrades gracefully (non-fatal, lists names) on an unknown name.

Skill HOW 钉英文键 / 层 / 标签 / ``consult(name)`` / 手册路径，不钉现行教学句，
也不钉目录摘要精确等于。一条契约一个所有者。
"""

from pathlib import Path

from agentcore.core.types import ToolFace
from agentcore.runtime.context.consult_sources import MergedConsultSource, SkillConsultSource
from agentcore.runtime.skills import (
    SkillRegistry,
    SystemSkill,
    build_system_skill_registry,
    render_skill_directory,
)
from agentcore.tools.builtin.consult import ConsultTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace

# debate / delegate are wired on every path; ask_user is live-user only.
# data_file_landing / page_ui ride consult audience (worker loop vs CEO 派工).
# 编制 HOW 在 delegate 按钮；填卡 HOW 在 ask_user 按钮。均不进本 registry。
_FULL_TOOLS = {"delegate", "ask_user", "debate", "run"}
_NO_LIVE_USER = {"delegate", "debate"}  # autonomous path: no ask_user / run


def _skill_consult(
    registry: SkillRegistry | None = None, tool_names: set[str] | None = None
) -> ConsultTool:
    reg = registry or build_system_skill_registry()
    names = tool_names if tool_names is not None else set(_FULL_TOOLS)
    return ConsultTool(
        source=MergedConsultSource(
            skill=SkillConsultSource(registry=reg, tool_names=names)
        )
    )


def _ctx() -> ToolContext:
    # consult never touches the backend; a real one only satisfies the shape.
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


def _body(name: str) -> str:
    return build_system_skill_registry().get(name).body


# --- registry ----------------------------------------------------------------


def test_registry_registers_the_system_skills():
    reg = build_system_skill_registry()
    names = {s.name for s in reg.list_all()}
    assert names == {
        "product_help",
        "data_file_landing",
        "page_ui",
    }


def test_registry_get_hit_and_miss():
    reg = build_system_skill_registry()
    assert reg.get("page_ui") is not None
    assert reg.get("no_such_skill") is None


def test_registry_rejects_duplicate_name():
    reg = SkillRegistry()
    reg.register(SystemSkill(name="x", summary="s", body="b"))
    try:
        reg.register(SystemSkill(name="x", summary="s2", body="b2"))
    except ValueError:
        pass
    else:  # pragma: no cover - the register must raise
        raise AssertionError("duplicate skill name should raise ValueError")


def test_available_system_skills_are_ungated():
    """出厂系统 Skill 现无 requires_tools 门；有无 run 工具同一份目录。"""
    reg = build_system_skill_registry()
    expected = {
        "product_help",
        "data_file_landing",
        "page_ui",
    }
    assert {s.name for s in reg.available(_NO_LIVE_USER)} == expected
    assert {s.name for s in reg.available(_FULL_TOOLS)} == expected


def test_available_audience_hides_ceo_only_from_workers():
    """A：队员目录拿掉主管手册；不按任务猜。requires_tools 轴仍独立。"""
    reg = build_system_skill_registry()
    worker = {s.name for s in reg.available(set(), audience="worker")}
    assert worker == {"data_file_landing", "page_ui"}
    lead = {s.name for s in reg.available({"delegate"}, audience="worker")}
    assert lead == worker
    ceo = {s.name for s in reg.available(_FULL_TOOLS, audience="ceo")}
    assert ceo == {
        "product_help",
        "data_file_landing",
        "page_ui",
    }


async def test_worker_consult_source_hides_ceo_only_listing_and_fetch():
    """目录和查阅同一份来源：列表没有的名字，按名也拉不到。"""
    source = SkillConsultSource(
        registry=build_system_skill_registry(),
        tool_names=set(),
        audience="worker",
    )
    names = {e.name for e in await source.list_directory("u")}
    assert names == {"data_file_landing", "page_ui"}
    assert await source.fetch_by_name("u", "product_help") is None
    assert await source.fetch_by_name("u", "product_help:workspace") is None
    assert await source.fetch_by_name("u", "no_such_skill") is None
    assert await source.fetch_by_name("u", "data_file_landing") is not None
    assert await source.fetch_by_name("u", "page_ui") is not None


async def test_nested_lead_consult_source_matches_leaf_directory():
    """持 delegate 不另开编制手册；HOW 在 nested delegate 按钮。"""
    source = SkillConsultSource(
        registry=build_system_skill_registry(),
        tool_names={"delegate"},
        audience="worker",
    )
    names = {e.name for e in await source.list_directory("u")}
    assert names == {"data_file_landing", "page_ui"}


async def test_expand_skill_tool_names_unlocks_gated_skill():
    from agentcore.runtime.context.consult_sources import (
        MergedConsultSource,
        SkillConsultSource,
        expand_skill_tool_names,
    )

    gated = SkillRegistry()
    gated.register(
        SystemSkill(name="gated", summary="s", body="b", requires_tools=("run",))
    )
    leaf = MergedConsultSource(
        skill=SkillConsultSource(
            registry=gated,
            tool_names=set(),
            audience="ceo",
        )
    )
    assert await leaf.fetch_by_name("u", "gated") is None
    expanded = expand_skill_tool_names(leaf, {"run"})
    assert expanded is not leaf
    assert await expanded.fetch_by_name("u", "gated") is not None
    assert await leaf.fetch_by_name("u", "gated") is None


def test_splice_on_demand_directory_replaces_block():
    from agentcore.runtime.resolve.prompt.compose import splice_on_demand_directory

    prompt = "BASE\n\n<按需目录>\nold\n</按需目录>\n\n<工作区>facts"
    out = splice_on_demand_directory(prompt, "<按需目录>\nnew\n</按需目录>")
    assert "old" not in out
    assert "new" in out
    assert "<工作区>facts" in out


async def test_offer_nested_lead_consult_noop_without_consult():
    from agentcore.runtime.runs.executor.captain_consult import offer_nested_lead_consult
    from agentcore.tools.registry import ToolRegistry

    reg = ToolRegistry()
    out_reg, prompt = await offer_nested_lead_consult(reg, "SYS", user_id="u")
    assert prompt == "SYS"
    assert out_reg is reg


async def test_ceo_consult_source_keeps_product_help():
    source = SkillConsultSource(
        registry=build_system_skill_registry(),
        tool_names=set(_FULL_TOOLS),
        audience="ceo",
    )
    names = {e.name for e in await source.list_directory("u")}
    assert names == {
        "product_help",
        "data_file_landing",
        "page_ui",
    }
    assert await source.fetch_by_name("u", "product_help") is not None
    assert await source.fetch_by_name("u", "product_help:workspace") is not None
    assert await source.fetch_by_name("u", "data_file_landing") is not None
    assert await source.fetch_by_name("u", "page_ui") is not None
    assert await source.fetch_by_name("u", "no_such_skill") is None


# --- directory rendering -----------------------------------------------------


def test_directory_lists_only_available_skills_with_names_and_summaries():
    reg = build_system_skill_registry()
    out = render_skill_directory(reg, _FULL_TOOLS)
    assert "<按需目录>" in out and "</按需目录>" in out
    assert "consult" in out  # the soft push to pull a skill
    for skill in reg.available(_FULL_TOOLS):
        assert skill.name in out
        assert skill.summary in out


def test_skill_directory_orders_by_decision_moment_without_subtitles():
    """只一栏时不打栏名；决策时刻组只排序，不印子标题。"""
    ceo = render_skill_directory(build_system_skill_registry(), _FULL_TOOLS)
    assert "能力指引：" not in ceo
    for heading in ("交付：", "产品：", "编排：", "工作区：", "工具："):
        assert heading not in ceo
    assert ceo.index('consult("data_file_landing")') < ceo.index('consult("page_ui")')
    assert ceo.index('consult("page_ui")') < ceo.index('consult("product_help")')
    assert 'consult("page_ui")' in ceo
    worker_src_names = {
        s.name
        for s in build_system_skill_registry().available(
            {"delegate", "ask_user", "debate", "run"}, audience="worker"
        )
    }
    assert worker_src_names == {
        "data_file_landing",
        "page_ui",
    }
    from agentcore.runtime.context.consultable import ConsultDirectoryEntry
    from agentcore.runtime.resolve.prompt.compose import render_on_demand_directory

    worker_entries = [
        ConsultDirectoryEntry(
            name=s.name, summary=s.summary, section="skill", group=s.group
        )
        for s in build_system_skill_registry().available(
            {"delegate", "ask_user", "debate", "run"}, audience="worker"
        )
    ]
    worker = render_on_demand_directory(worker_entries, with_summaries=True)
    assert "能力指引：" not in worker
    assert "交付：" not in worker
    assert "编排：" not in worker
    assert 'consult("page_ui")' in worker
    assert "工作区：" not in worker
    assert "产品：" not in worker


def test_system_skill_summaries_are_short_when_triggers():
    """目录行是打开前的触发器；Python len ≤80。不钉教学句。"""
    for skill in build_system_skill_registry().list_all():
        assert len(skill.summary) <= 80, (skill.name, len(skill.summary), skill.summary)
        assert "查阅" in skill.summary, skill.name


def test_system_skill_blurbs_stay_off_directory():
    """货架简介只给工具箱，不进 <按需目录>。"""
    registry = build_system_skill_registry()
    directory = render_skill_directory(registry, _FULL_TOOLS)
    for skill in registry.list_all():
        assert skill.blurb.strip(), skill.name
        assert skill.blurb != skill.summary, skill.name
        assert len(skill.blurb) <= 80, (skill.name, skill.blurb)
        assert skill.blurb not in directory, skill.name


def test_product_help_consult_carved_out_and_owned_by_catalog():
    """产品用法从常驻核划出；目录有名，不进常驻核。"""
    from agentcore.runtime.resolve.prompt import _CEO_CORE_HINT

    out = render_skill_directory(build_system_skill_registry(), _FULL_TOOLS)
    hint = _CEO_CORE_HINT
    assert 'consult("product_help")' in out
    assert "product_help" not in hint


async def test_consult_product_help_section_hit():
    """验收：限定名取节；目录不列出节 id。"""
    from agentcore.runtime.skills.product_help import fetch_product_help_section

    reg = build_system_skill_registry()
    tool = _skill_consult(reg)
    result = await tool.execute({"name": "product_help:workspace"}, _ctx())
    assert result.success
    assert result.output == fetch_product_help_section("workspace")
    assert "#/toolbox/manual/" in result.output
    assert "s=workspace" in result.output
    directory = render_skill_directory(reg, _NO_LIVE_USER)
    assert 'consult("product_help")' in directory
    assert "product_help:workspace" not in directory


async def test_consult_product_help_unknown_section_returns_card():
    """对不上节：回索引卡，不倒出节 id 菜单。"""
    from agentcore.runtime.skills.product_help import build_product_help_body

    tool = _skill_consult()
    result = await tool.execute({"name": "product_help:nope_section"}, _ctx())
    assert result.success
    assert result.error is None
    assert result.output == build_product_help_body()
    assert "没有名为" not in (result.output or "")
    assert "progress" not in (result.output or "")


def test_product_help_user_words_match_title_or_card():
    """用户原话含节标题才回那一节；短尾词与多节命中只回索引卡。"""
    from agentcore.runtime.skills.product_help import (
        build_product_help_body,
        fetch_product_help_section,
        format_product_help_section,
        load_product_help_corpus,
    )

    sections = {str(s["id"]): s for s in load_product_help_corpus()["sections"]}
    card = build_product_help_body()
    assert fetch_product_help_section("检查点在哪") == format_product_help_section(
        sections["checkpoint"]
    )
    assert fetch_product_help_section("工作区里的文件在哪") == format_product_help_section(
        sections["workspace"]
    )
    assert fetch_product_help_section("整理这个文件") == card
    assert fetch_product_help_section("用工具整理表") == card
    assert fetch_product_help_section("   ") == card
    multi = fetch_product_help_section("检查点和辩论室")
    assert multi.startswith("对上多节：检查点与审批、辩论室。")
    assert "# 检查点与审批" not in multi
    assert "# 辩论室" not in multi
    assert "看进度" not in multi


async def test_consult_product_help_section_alias():
    from agentcore.runtime.skills.product_help import fetch_product_help_section

    tool = _skill_consult()
    result = await tool.execute({"name": "product_help:chat"}, _ctx())
    assert result.success
    assert result.output == fetch_product_help_section("faq")


def test_product_help_pins_section_ids_and_manual_paths():
    """节 id / 手册路径 / 合同键；不钉 FAQ 整句。"""
    from agentcore.runtime.skills.product_help import (
        format_product_help_section,
        list_product_help_section_ids,
        load_product_help_corpus,
    )

    help_body = _body("product_help")
    assert 'consult("product_help:' in help_body
    assert "【可查事实】" not in help_body
    assert "progress —" not in help_body
    assert "#/toolbox/manual/" in help_body
    assert "https://fashitianxia.xyz" in help_body
    assert "https://fashitianxia.xyz/download" in help_body
    assert "https://app.fashitianxia.xyz" in help_body
    assert ".cursor/rules" in help_body
    assert ".agentcore/rules" in help_body
    ids = list_product_help_section_ids()
    assert "workspace" in ids
    assert "what" in ids
    assert "prompts" in ids
    for sec in load_product_help_corpus()["sections"]:
        href = str(sec.get("href") or "")
        assert href.startswith("#/toolbox/manual/")
        assert f"?s={sec['id']}" in href
        assert "action=" not in format_product_help_section(sec)


def test_directory_on_autonomous_path_lists_ungated():
    reg = build_system_skill_registry()
    out = render_skill_directory(reg, _NO_LIVE_USER)
    assert "product_help" in out
    assert "page_ui" in out
    assert "data_file_landing" in out


def test_directory_empty_when_nothing_available():
    # A registry whose every skill is gated behind an un-wired tool renders nothing,
    # so the caller appends nothing (no empty <按需目录> block).
    reg = SkillRegistry()
    reg.register(SystemSkill(name="x", summary="s", body="b", requires_tools=("missing_tool",)))
    assert render_skill_directory(reg, set()) == ""


# --- consult tool ------------------------------------------------------


def test_consult_schema_is_ceo_orchestration_primitive():
    # consult is a CEO orchestration primitive (not a「技能」-category tool):
    # 技能 are Prompt injection shown in the「AI 提示词」catalog, never a tool group.
    tool = _skill_consult()
    schema = tool.schema
    assert schema.name == "consult"
    assert schema.face is ToolFace.ORCHESTRATION


async def test_consult_returns_body_on_hit():
    reg = build_system_skill_registry()
    tool = _skill_consult(reg)
    result = await tool.execute({"name": "page_ui"}, _ctx())
    assert result.success
    assert result.output == reg.get("page_ui").body


async def test_consult_product_help_hit():
    """验收：consult('product_help') 命中；目录列出该名。"""
    reg = build_system_skill_registry()
    skill = reg.get("product_help")
    assert skill is not None
    assert skill.requires_tools == ()
    tool = _skill_consult(reg)
    result = await tool.execute({"name": "product_help"}, _ctx())
    assert result.success
    assert result.output == skill.body
    directory = render_skill_directory(reg, _NO_LIVE_USER)
    assert 'consult("product_help")' in directory


async def test_consult_degrades_on_unknown_name():
    tool = _skill_consult()
    result = await tool.execute({"name": "bogus"}, _ctx())
    # Soft miss: success=True, lists available names (no turn-breaking).
    assert result.success
    assert result.error is None
    assert "没有名为" in result.output
    assert "page_ui" in result.output


async def test_consult_unknown_name_is_plain_soft_miss():
    """Unknown name is a plain soft miss."""
    tool = _skill_consult()
    result = await tool.execute({"name": "no_such_handbook"}, _ctx())
    assert result.success
    assert "没有名为" in result.output


async def test_consult_handles_missing_name_arg():
    tool = _skill_consult()
    result = await tool.execute({}, _ctx())
    assert result.success
    assert "缺少 name" in result.output


# --- skill 层：英文键与指针 -------------------------------------------------


def test_skill_layer_keys_and_pointers():
    """一层一处：键在所有者，不在别层。不钉目录摘要、不钉教学中文。"""
    from agentcore.tools.builtin.delegate.schema import (
        DELEGATE_DESCRIPTION,
        DELEGATE_PARAMETERS,
    )

    task_props = DELEGATE_PARAMETERS["properties"]["tasks"]["items"]["properties"]
    top_props = DELEGATE_PARAMETERS["properties"]

    assert "target_folder_id" not in DELEGATE_DESCRIPTION
    assert "depends_on" in task_props
    assert "playbook" not in top_props
    assert "playbook_args" not in top_props
