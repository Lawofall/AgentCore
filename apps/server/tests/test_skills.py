"""Tests for system Skills + consult (提示词瘦身 P2 — 渐进披露).

Covers the three moving parts of the prompt-slimming slice:

1. ``SkillRegistry`` / ``build_system_skill_registry`` — name lookup (hit/miss) and
   the ``requires_tools`` visibility filter.
2. ``render_skill_directory`` — the always-on 按需目录 lists only skills whose required
   tools are wired this turn (so it never advertises a capability the CEO lacks).
3. ``ConsultTool`` — returns a skill's full body (CONTINUE) on a hit, and
   degrades gracefully (non-fatal, lists names) on an unknown name.

Plus a guard that each skill BODY still teaches the mechanism it owns — the
assertions that used to pin these in the always-on CEO hint, now relocated to the
skills they were externalised into.
"""

import re
from pathlib import Path

from agentcore.config import settings
from agentcore.core.types import ToolCategory
from agentcore.runtime.context.consult_sources import MergedConsultSource, SkillConsultSource
from agentcore.runtime.runs.playbooks.audit import (
    CODE_AUDIT_REQUIRED_SECTIONS,
    CODE_AUDIT_SECTION_BY_DESIGN,
)
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
# verify_and_fix / long_form_writing / data_file_landing ride consult audience
# (worker loop vs CEO 派工); long_form_landing is worker-only landing HOW.
# team_orchestration_advanced / build_* / deep_multi_lens 是主管手册（audience=ceo）。
_FULL_TOOLS = {"delegate", "ask_user", "debate"}
_NO_LIVE_USER = {"delegate", "debate"}  # autonomous path: no ask_user


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


# --- registry ----------------------------------------------------------------


def test_registry_registers_the_system_skills():
    reg = build_system_skill_registry()
    names = {s.name for s in reg.list_all()}
    assert names == {
        "team_orchestration_advanced",
        "team_cross_folder",
        "team_delivery_env",
        "work_discipline",
        "product_help",
        "product_help_map",
        "product_help_faq",
        "product_bug_triage",
        "building_software",
        "debate_and_review",
        "revising_a_product",
        "ask_user_kickoff",
        "ask_user_midtask",
        "delegate_checkpoint",
        "verify_and_fix",
        "long_form_writing",
        "long_form_landing",
        "data_file_landing",
        "deep_multi_lens_research",
    }
    assert "build_toolshed" not in names


def test_registry_get_hit_and_miss():
    reg = build_system_skill_registry()
    assert reg.get("debate_and_review") is not None
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


def test_available_hides_gated_skills_without_required_tools():
    # The ask_user_* skills (and delegate_checkpoint, which pauses for user review)
    # need the ask_user tool. On the autonomous (no live user) path it is not wired,
    # so those skills drop out of the catalog. verify_and_fix is ungated (worker loop;
    # CEO still consults to brief) — not tied to delegate.
    reg = build_system_skill_registry()
    available = {s.name for s in reg.available(_NO_LIVE_USER)}
    assert "team_orchestration_advanced" in available
    assert "product_help" in available  # requires_tools=() — always listed
    assert "product_help_map" in available
    assert "product_help_faq" in available
    assert "product_bug_triage" in available
    assert "build_website" not in available
    assert "building_software" in available
    assert "debate_and_review" in available
    assert "revising_a_product" in available
    assert "verify_and_fix" in available
    assert "data_file_landing" in available
    assert "work_discipline" in available
    assert "ask_user_kickoff" not in available
    assert "ask_user_midtask" not in available
    assert "delegate_checkpoint" not in available


def test_available_shows_gated_skills_when_tools_wired():
    reg = build_system_skill_registry()
    available = {s.name for s in reg.available(_FULL_TOOLS)}
    assert "ask_user_kickoff" in available
    assert "ask_user_midtask" in available
    assert "delegate_checkpoint" in available
    assert "verify_and_fix" in available


def test_available_audience_hides_ceo_only_from_workers():
    """A：队员目录拿掉主管手册；不按任务猜。requires_tools 轴仍独立。"""
    reg = build_system_skill_registry()
    worker = {s.name for s in reg.available(set(), audience="worker")}
    assert "product_help" not in worker
    assert "product_help_map" not in worker
    assert "product_help_faq" not in worker
    assert "product_bug_triage" not in worker
    assert "revising_a_product" not in worker
    assert "team_orchestration_advanced" not in worker
    assert "team_cross_folder" not in worker
    assert "team_delivery_env" not in worker
    assert "build_website" not in worker
    assert "building_software" not in worker
    assert "deep_multi_lens_research" not in worker
    assert "work_discipline" in worker
    assert "long_form_landing" in worker
    assert "verify_and_fix" in worker
    assert "data_file_landing" in worker
    assert "long_form_writing" not in worker
    # 持 delegate 的嵌套 lead 目录也必须与叶子同名，避免队员之间打散前缀。
    lead = {s.name for s in reg.available({"delegate"}, audience="worker")}
    assert lead == worker
    ceo = {s.name for s in reg.available(_FULL_TOOLS, audience="ceo")}
    assert "product_help" in ceo
    assert "revising_a_product" in ceo
    assert "team_orchestration_advanced" in ceo
    assert "team_cross_folder" in ceo
    assert "team_delivery_env" in ceo
    assert "long_form_writing" in ceo
    assert "verify_and_fix" in ceo
    assert "data_file_landing" in ceo
    assert "long_form_landing" not in ceo


async def test_worker_consult_source_hides_ceo_only_listing_and_fetch():
    """目录和查阅同一份来源：列表没有的名字，按名也拉不到。"""
    source = SkillConsultSource(
        registry=build_system_skill_registry(),
        tool_names=set(),
        audience="worker",
    )
    names = {e.name for e in await source.list_directory("u")}
    assert "product_help" not in names
    assert "revising_a_product" not in names
    assert "team_orchestration_advanced" not in names
    assert "team_cross_folder" not in names
    assert "team_delivery_env" not in names
    assert "long_form_landing" in names
    assert "verify_and_fix" in names
    assert "data_file_landing" in names
    assert "long_form_writing" not in names
    assert await source.fetch_by_name("u", "product_help") is None
    assert await source.fetch_by_name("u", "long_form_writing") is None
    assert await source.fetch_by_name("u", "team_orchestration_advanced") is None
    assert await source.fetch_by_name("u", "team_cross_folder") is None
    assert await source.fetch_by_name("u", "team_delivery_env") is None
    assert await source.fetch_by_name("u", "long_form_landing") is not None
    assert await source.fetch_by_name("u", "data_file_landing") is not None


async def test_ceo_consult_source_keeps_product_help():
    source = SkillConsultSource(
        registry=build_system_skill_registry(),
        tool_names=set(_FULL_TOOLS),
        audience="ceo",
    )
    names = {e.name for e in await source.list_directory("u")}
    assert "product_help" in names
    assert "long_form_writing" in names
    assert "data_file_landing" in names
    assert "long_form_landing" not in names
    assert await source.fetch_by_name("u", "product_help") is not None
    assert await source.fetch_by_name("u", "long_form_writing") is not None
    assert await source.fetch_by_name("u", "data_file_landing") is not None
    assert await source.fetch_by_name("u", "long_form_landing") is None


# --- directory rendering -----------------------------------------------------


def test_directory_lists_only_available_skills_with_names_and_summaries():
    reg = build_system_skill_registry()
    out = render_skill_directory(reg, _FULL_TOOLS)
    assert "<按需目录>" in out and "</按需目录>" in out
    assert "consult" in out  # the soft push to pull a skill
    for skill in reg.list_all():
        assert skill.name in out
        assert skill.summary in out


def test_system_skill_summaries_are_short_when_triggers():
    """目录行只写这是什么；Python len ≤80（对照 verify_and_fix 一句名字）。"""
    for skill in build_system_skill_registry(include_legal=True).list_all():
        assert len(skill.summary) <= 80, (skill.name, len(skill.summary), skill.summary)


def test_product_help_consult_carved_out_and_owned_by_catalog():
    """产品用法从常驻核划出；目录摘要只写这是什么，不进常驻核。"""
    from agentcore.runtime.resolve.prompt import _CEO_CORE_HINT

    out = render_skill_directory(build_system_skill_registry(), _FULL_TOOLS)
    hint = _CEO_CORE_HINT
    assert "<platform_knowledge>" not in hint
    assert "按场面：本产品用法" not in hint
    assert "按场面：用户主动查/报产品本身可证伪故障" not in hint
    assert "纯对话式回答自己答即可，无需 consult" not in out
    assert "- product_help：" in out
    assert "- product_help_map：" in out
    assert "- product_help_faq：" in out
    assert "- product_bug_triage：" in out
    reg = build_system_skill_registry()
    help_sum = reg.get("product_help").summary
    faq_sum = reg.get("product_help_faq").summary
    bug_sum = reg.get("product_bug_triage").summary
    assert "官网" in help_sum
    assert "下载" in help_sum
    assert "product_help_map" not in help_sum
    assert "product_help_faq" not in help_sum
    assert "Cursor" in faq_sum
    assert "这是什么项目" not in help_sum
    help_body = reg.get("product_help").body
    assert "这是什么项目" in help_body
    assert "你是什么" in help_body
    assert "你的网站" in help_body
    assert "Cursor" in help_body
    assert ".mdc" in help_body
    assert "改成 AgentCore 规则" in help_body
    assert "故障" in bug_sum or "Bug" in bug_sum
    assert "必查 `product_bug_triage`" not in bug_sum
    assert "出问题必查" not in bug_sum
    assert "出问题必查" not in out


def test_greenfield_recommends_building_software_not_hard_forbid_none():
    """做软件 HOW 在 consult(building_software)；目录只留名字；核不写薄旁路判决。

    去重定案：目录只写名字；HOW 只在 skill body。
    """
    from agentcore.runtime.resolve.prompt import _CEO_CORE_HINT

    out = render_skill_directory(build_system_skill_registry(), _FULL_TOOLS)
    hint = _CEO_CORE_HINT
    assert "做软件手写" not in hint
    assert "必须 build_app" not in hint
    assert "禁 none 手糊" not in hint
    assert "consult(building_software)" not in hint
    skill = build_system_skill_registry().get("building_software")
    assert skill is not None
    orch = build_system_skill_registry().get("team_orchestration_advanced")
    assert orch is not None
    assert "consult(team_orchestration_advanced)" in skill.body
    assert "薄旁路" in skill.body
    assert "边界未钉" in orch.body
    assert "轻切片" in orch.body or "少节点" in orch.body
    assert "先 MVP" in orch.body or "轻切片" in orch.body
    assert "- building_software：" in out
    assert "做软件" in skill.summary
    assert "薄旁路" not in skill.summary
    assert "推荐具名" not in skill.summary
    assert "边界未钉" not in skill.body
    assert 'playbook="build_app"' not in skill.body


def test_directory_omits_gated_skills_on_autonomous_path():
    reg = build_system_skill_registry()
    out = render_skill_directory(reg, _NO_LIVE_USER)
    assert "ask_user_kickoff" not in out
    assert "ask_user_midtask" not in out
    assert "delegate_checkpoint" not in out
    # The delegate-gated + non-gated advanced skills are still offered.
    assert "team_orchestration_advanced" in out
    assert "verify_and_fix" in out


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
    assert schema.category is ToolCategory.ORCHESTRATION


async def test_consult_returns_body_on_hit():
    reg = build_system_skill_registry()
    tool = _skill_consult(reg)
    result = await tool.execute({"name": "debate_and_review"}, _ctx())
    assert result.success
    assert result.output == reg.get("debate_and_review").body


async def test_consult_product_help_hit():
    """验收：consult('product_help') 命中；目录可列出三级披露。"""
    reg = build_system_skill_registry()
    skill = reg.get("product_help")
    assert skill is not None
    assert skill.requires_tools == ()
    tool = _skill_consult(reg)
    result = await tool.execute({"name": "product_help"}, _ctx())
    assert result.success
    assert result.output == skill.body
    directory = render_skill_directory(reg, _NO_LIVE_USER)
    assert "- product_help：" in directory
    assert "- product_help_map：" in directory
    assert "- product_help_faq：" in directory
    assert "- product_bug_triage：" in directory
    assert skill.summary in directory
    for name in ("product_help_map", "product_help_faq", "product_bug_triage"):
        sibling = reg.get(name)
        assert sibling is not None
        hit = await tool.execute({"name": name}, _ctx())
        assert hit.success
        assert hit.output == sibling.body


async def test_consult_build_website_is_plain_soft_miss():
    """目录与 consult 不再有 build_website（CEO 侧不推具名建站套餐）。"""
    reg = build_system_skill_registry()
    assert reg.get("build_website") is None
    tool = _skill_consult(reg)
    result = await tool.execute({"name": "build_website"}, _ctx())
    assert result.success  # soft miss
    assert "没有名为" in result.output
    directory = render_skill_directory(reg, _FULL_TOOLS)
    assert "- build_website：" not in directory
    assert "build_website：" not in directory


async def test_consult_build_toolshed_removed():
    """旧独立 skill 名 miss；目录不再教独立 build_toolshed playbook。"""
    reg = build_system_skill_registry()
    assert reg.get("build_toolshed") is None
    tool = _skill_consult(reg)
    result = await tool.execute({"name": "build_toolshed"}, _ctx())
    assert result.success  # soft miss
    assert "没有名为" in result.output
    directory = render_skill_directory(reg, _NO_LIVE_USER)
    assert "- build_toolshed：" not in directory
    assert reg.get("build_website") is None


async def test_consult_degrades_on_unknown_name():
    tool = _skill_consult()
    result = await tool.execute({"name": "bogus"}, _ctx())
    # Soft miss: success=True, lists available names (no turn-breaking).
    assert result.success
    assert result.error is None
    assert "没有名为" in result.output
    assert "team_orchestration_advanced" in result.output


async def test_consult_playbook_name_is_plain_soft_miss():
    """Playbook-name special-case removed — unknown name is a plain soft miss."""
    tool = _skill_consult()
    result = await tool.execute({"name": "build_feature"}, _ctx())
    assert result.success
    assert "playbook" not in result.output.lower() or "delegate(playbook=" not in result.output
    assert "没有名为" in result.output


async def test_consult_repair_code_is_plain_soft_miss():
    """repair_code playbook hint removed with 步 1 soft-miss unify."""
    tool = _skill_consult()
    result = await tool.execute({"name": "repair_code"}, _ctx())
    assert result.success
    assert "continue_from_run_id" not in result.output
    assert "没有名为" in result.output


async def test_consult_handles_missing_name_arg():
    tool = _skill_consult()
    result = await tool.execute({}, _ctx())
    assert result.success
    assert "缺少 name" in result.output


# --- skill bodies still teach their mechanisms (relocated from the CEO hint) --


def _body(name: str) -> str:
    return build_system_skill_registry().get(name).body


_RETIRED_SKILL_LITERALS = (
    "required_sections",
    "output_format",
    "strict",
    "citation_mode",
    "workspace_native",
    "artifact_dir",
    "completion_criteria",
    "format_options",
    "continue_writing",
    "origin",
    "provider_id",
    "grant_readonly_folder",
    "handoff degraded",
    "degraded",
    "不硬拒",
    "原开场引导",
    "本档不加提交工具",
    "cloud-local-root-auth-where",
    "build_feature",
    "compare_options",
    "parallel_brief",
    "research_report",
    "multi_lens_research",
    "repair_code",
)

# Playbook ids are identifiers: ``multi_lens_research`` must not fire on the
# kept skill name ``deep_multi_lens_research``.
_RETIRED_PLAYBOOK_IDS = frozenset(
    {
        "parallel_brief",
        "research_report",
        "multi_lens_research",
        "repair_code",
    }
)


def _retired_token_in(blob: str, token: str) -> bool:
    if token in _RETIRED_PLAYBOOK_IDS:
        return (
            re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", blob)
            is not None
        )
    return token in blob


def test_retired_literals_absent_from_skill_injection():
    """现行通道不留废字段名 / 禁复活编年；playbook↔tasks 二选一仍在编排 skill。"""
    orch = _body("team_orchestration_advanced")
    assert "与手写 tasks 二选一" in orch
    assert "同时传" in orch
    for skill in build_system_skill_registry().list_all():
        blob = f"{skill.summary}\n{skill.body}"
        for token in _RETIRED_SKILL_LITERALS:
            assert not _retired_token_in(blob, token), (skill.name, token)


def test_team_orchestration_skill_teaches_shape_vocabulary():
    # 协作优先重设计阶段 2：skill 教形状词汇表 + 组合规则 + 三档，playbook 降为教学示例。
    body = _body("team_orchestration_advanced")
    for term in (
        "并列对象分组",
        "角度扇出",
        "证据驱动流水线",
        "独立审查",
        "有界返工环",
        "契约共享面",
        "独立多透镜诊断",
        "部件一致性对账",
        "对抗辩论",
        "发散挑选",
    ):
        assert term in body, term
    assert "默认中档" in body
    assert "教学示例形状" in body and "对照学形状" in body
    assert "免手搓" not in body  # 旧广告口径已撤
    assert "cite_write_review" in body  # listing still present as teaching examples
    assert "map_fanout" in body
    assert "结局分层" in body or "对齐推进" in body or "禁成文产线 ≠ 禁 brief" in body
    assert "成文交付" in body or "成文专线" in body or "成篇" in body
    assert "默认 A" in body or "少扇出" in body
    assert "材料已齐" in body
    # 做软件手写 tasks；边界未钉 ≠ 整仓满编
    assert "做软件手写" in body or "手写顶层" in body
    assert "不硬拒" not in body
    assert "边界未钉" in body or "轻切片" in body
    assert "轻切片" in body or "少节点" in body or "嵌套" in body
    assert 'playbook="build_app"' not in body
    # 真两段结构口径；禁假两段；桌面壳绿场切片
    assert "真两段" in body or "1 人两段" in body
    assert "假两段" in body
    assert "同一 task" in body
    assert "桌面壳" in body or "多进程" in body
    assert "省略 playbook" in body
    assert "playbook=none" not in body
    assert "playbook_id" not in body
    assert "根委派切片诚实" in body or "嵌套扇出" in body
    # 三路/多路调研缺主体：问卡 HOW 在 ask_user_kickoff
    assert "缺主体" in body
    assert "consult(ask_user_kickoff)" in body
    assert "静默自拟" not in body
    assert "不得 continue 派工" not in body
    # 档 3 满编：落盘文档 + ≥2 角 → 各角与主笔均 files + 末环独立审校
    assert "角 prose" in body and "仅主笔落盘" in body
    assert "form=files" in body and "artifacts" in body
    assert "独立审校" in body
    assert "调研→撰稿" in body
    assert "质量缝" in body
    # 本地修码：手写 1 人 + form=workspace + 短任务；白屏/UI → verify= browser 形
    assert "手写 1 人" in body
    assert "form=workspace" in body
    assert "短任务" in body
    assert "diagnose_fix_verify" in body
    assert "白屏" in body or "挂载" in body
    assert "verify=" in body and "browser" in body
    assert "tsc" in body or "pytest" in body
    for token in ("complexity_hint", "result_handling", "require_upstream"):
        assert token not in body, token
    # 代码审计：模板路 / 手写路互斥，禁 code_audit+tasks 叠用
    assert "代码审计" in body
    assert "模板路" in body and "手写路" in body
    assert "禁止】`code_audit`" in body or "同时传" in body
    assert "云端引擎员" in body or "本地引擎员" in body
    # 整仓走 modules 扇出；「凡审计必两拨人」不得读成禁填 modules
    assert "modules" in body
    assert "整仓只填 scope 当单人" in body or "整仓" in body
    assert "必须扇出" in body  # 仅出现在「仍非必须扇出」
    assert "单缝省略" in body
    assert "产品缝" in body
    assert "2–3" in body or "2-3" in body
    assert "目录树" in body
    assert "上限 8" in body or "上限8" in body
    assert "超限末槽折叠" in body
    assert "无主管" in body
    assert "≥3" in body and "主管" in body
    assert "凡审计必两拨人" not in body
    for title in CODE_AUDIT_REQUIRED_SECTIONS:
        assert title in body
    assert CODE_AUDIT_SECTION_BY_DESIGN in body
    assert "独立成栏" in body
    assert "不扫 role/task" in body or "不扫 role" in body


def test_team_orchestration_skill_teaches_opening_table_and_draft_tiers():
    """开场桌上结果 + 成文后梯度 + 审校不默认（与 CEO 常驻对齐的 HOW）。"""
    body = _body("team_orchestration_advanced")
    # 讨论类 ask_user HOW（三选桌上结果）；挡路脊柱在 CEO 核，本 skill 不复述
    assert "讨论类开场" in body
    assert "先对话对齐" in body
    assert "写成文档并保存" in body
    assert "暂不派队" in body
    assert "对话本身" in body
    assert "编制自选" in body
    assert "自动多人" in body
    assert "干活默认派" in body
    assert "内部编制" in body
    assert "几人几步" in body
    assert "明示成文不拦" in body
    # 禁成文产线 ≠ 禁 brief（核只留短脊柱；本 skill 是划界 HOW）
    assert "禁成文产线 ≠ 禁 brief" in body or "禁成文产线≠禁 brief" in body
    assert "不写盘" in body or "答完维度" in body
    assert "consumer_deps" in body
    assert "短文" in body and "档 1" in body and "1 人" in body
    assert "档 2" in body and "≥2" in body
    # 成文后轻→标准→重；满编 cite_write_review 仅档 3（核已不钉档 2 轻成文全文）
    assert "成文后梯度" in body or "成文梯度" in body
    assert "轻→标准→重" in body
    assert "档 1" in body and "档 2" in body and "档 3" in body
    assert "轻成文" in body
    assert "C 材料已齐" in body or "材料已齐成文" in body
    assert "long_form_writing" in body
    assert "deep_multi_lens_research" in body
    assert "点名开辩" in body or "debate" in body
    # 核下沉独有句：A/档2 不成篇硬门；探路停手在【工作流】（轮次配额不进 skill）
    assert "成篇硬门" in body
    assert "【工作流】" in body
    assert "0～1" not in body
    assert "规模与结构" in body
    assert "定位入口" in body
    assert "探路停手" in body
    assert "cite_write_review" in body
    assert "满编" in body
    assert "套 `cite_write_review` 满编" in body or "cite_write_review` 满编" in body
    # 普通构想不默认学术审校
    assert "【不】默认学术审校" in body
    # 未明示成文仍宜 map_fanout（旧 A 语义保留）
    assert "map_fanout" in body
    assert "未明示" in body
    # 与常驻核同一套明示成文定义：写一篇论文/综述=成文；仅当资料源才 ≠
    assert "写一篇" in body and "综述" in body
    assert "写一篇…论文/综述" in body or "写一篇论文" in body
    assert "收成单人主笔" in body
    # 派摸底验收：手写也要目标·边界·验收；够用即停 + handoff
    assert "派摸底" in body or "摸底·验收" in body or "了解到什么算够" in body
    assert "够用即停" in body
    assert "一页地图" in body
    assert "开局先招人" in body
    assert "凑台账" in body or "上网检索" in body
    assert "一句目标" in body
    assert "必读文件" in body or "书单" in body
    assert "定优化方案" in body
    assert "无限深挖" in body or "更全" in body
    assert "handoff" in body


def test_work_discipline_skill_teaches_design_and_patch_tripwires():
    body = _body("work_discipline")
    assert "设计三问" in body
    assert "补丁绊线" in body
    assert "探索信任" in body
    assert "讨论与查证分相" in body
    assert "work_authority" in body
    assert "consult(team_orchestration_advanced)" in body
    # worker 自主度不进本 skill（已在 identity）；本 skill 只短提醒 blocking 由工人自选
    assert "小问题（路径拼写" not in body
    assert "按题自选" in body or "blocking" in body
    assert "边干边报" in body or "默认 false" in body
    # 已确认约束 / 扇入整合：唯一所有者 = 编排 skill
    orch = _body("team_orchestration_advanced")
    assert "已确认约束" in orch
    assert "自拟的默认" in orch or "自拟默认" in orch
    assert "扇入" in orch or "整合员" in orch
    assert "已确认约束" not in body
    assert "扇入整合" not in body
    # 小步增量：用户偏好小步时首派更切片，勿一口吞绿场
    assert "小步" in body or "增量" in body
    assert "绿场" in body or "切片" in body
    assert "未定案·窄" in body
    assert "架构" in body and "不可逆" in body


def test_product_help_skill_teaches_short_answers_and_manual_deeplinks():
    """二级披露：答法在 product_help；入口/深链在 map；FAQ 在 faq。"""
    help_body = _body("product_help")
    map_body = _body("product_help_map")
    faq_body = _body("product_help_faq")

    assert "短答" in help_body
    assert "禁内部名" in help_body
    # 官网域名只在 skill（核不常驻 URL）；同名他品禁的是身份落地，不是厂商站字面
    assert "https://fashitianxia.xyz" in help_body
    assert "https://fashitianxia.xyz/download" in help_body
    assert "https://app.fashitianxia.xyz" in help_body
    assert "同名他品" in help_body
    assert "只用下方【官网 / 下载】域名" in help_body or "域名只许用这三条" in help_body
    assert "ask_user" in help_body  # teach: don't say these to users
    assert "功能总览" in help_body and "≤3" in help_body
    assert "product_help_map" in help_body and "product_help_faq" in help_body
    assert "【入口地图】" not in help_body
    assert "【FAQ 精华】" not in help_body
    # 答法可举例 FAQ 题名作分流；完整短答须只在 faq body
    assert "一人答更快就直接干" not in help_body
    assert "Multi-Agent" in help_body or "工作台" in help_body
    assert "RAG" in help_body  # forbid
    assert "playbook=" not in help_body
    assert "SSE" in help_body  # ban list only

    assert "【入口地图】" in map_body
    assert "【产品面地图·高频入口】" in map_body
    assert "唯一对话入口" in map_body
    assert "怎么用本产品" in map_body
    assert "模型与偏好等" in map_body
    assert "产物与「完整预览」" in map_body
    assert "关键拍板与正反交锋入口" in map_body
    assert "#/toolbox/manual/" in map_body
    assert "手机" in map_body and "勿承诺" in map_body
    assert "页名也按端写" in map_body
    assert "我的 → 服务商" in help_body
    assert "我的 → 服务商" in faq_body
    assert "我的 → 模型" in faq_body
    assert "我的 → 用量" in faq_body
    assert "窄屏不上工具箱" in map_body
    assert "?s=workspace" in map_body or "workspace" in map_body
    # .md 阅读预览（文件面板）≠ HTML「完整预览」（右坞）
    assert "阅读预览" in map_body
    assert "完整预览" in map_body
    assert "文件」面板" in map_body or "文件面板" in map_body

    assert "【FAQ 精华】" in faq_body
    assert "为什么没组团" in faq_body
    assert "https://fashitianxia.xyz" in faq_body
    assert "官网" in faq_body and "下载" in faq_body
    assert "?s=faq" in faq_body
    assert "playbook=" not in faq_body
    assert "怎么打开 .md" in faq_body or ".md" in faq_body and "阅读预览" in faq_body
    assert "Markdown 是什么" in faq_body or "语法" in faq_body  # 禁科普口径
    assert "完整预览" in faq_body and "不是一路" in faq_body
    # 文件夹：入口条在 map、删除语义在 faq；保留期跟随服务端设置，勿手写漂移
    assert "删除文件夹…" in map_body and "⋯" in map_body
    assert "删文件夹会怎样？" in faq_body
    assert "项目" not in map_body and "项目" not in faq_body
    assert "一并归档" in faq_body and "已归档" in faq_body
    assert f"约 {settings.workspace_retention_days} 天后由系统自动清理" in faq_body
    assert "立即永久清除" in faq_body and "不可恢复" in faq_body
    # 本机磁盘不受影响（线上 trace 曾编造「删本地项目会动本机目录」）
    assert "两种删法都不动你电脑上的文件" in faq_body
    # 定案 A：Cursor 规则 ↔ AgentCore 用户规则（权威对照须字面在 faq，可独立短答）
    assert "Cursor `.cursor/rules` / `.mdc` ≠ AgentCore 用户规则" in faq_body
    assert "AgentCore 用户规则 = `AgentCore/规则/` + `remember`" in faq_body
    assert "`skills/*.json` = 技能/能力包" in faq_body
    assert "不是" in faq_body and "平台规则" in faq_body
    assert "必查 `product_help`" in faq_body
    assert "未钉死目标载体前禁止默认迁成 skill JSON" in faq_body
    # 歧义路径压缩：consult 后至多一次窄 list；禁多轮 list/通读 .mdc 再问
    assert "至多一次窄 list `.cursor/rules`" in faq_body
    assert "禁多轮 list / 通读 `.mdc` 再问" in faq_body
    # help 正反例短钩；整本对照勿膨胀进 help
    assert "Cursor" in help_body and "改成 AgentCore 规则" in help_body
    assert "skills/*.json" in help_body
    assert "至多一次窄 list `.cursor/rules`" in help_body
    assert "多轮 list / 通读 `.mdc`" in help_body
    assert "Cursor `.cursor/rules` / `.mdc` ≠ AgentCore 用户规则" not in help_body
    # summary 只写这是什么。身份问自己答，不在摘要里必查。map/faq 分流在 body。
    help_skill = build_system_skill_registry().get("product_help")
    assert help_skill is not None
    assert "这是什么项目" not in help_skill.summary
    assert "你是什么" not in help_skill.summary
    assert "官网" in help_skill.summary
    assert "product_help_map" not in help_skill.summary
    assert "product_help_faq" not in help_skill.summary
    map_skill = build_system_skill_registry().get("product_help_map")
    assert map_skill is not None
    assert "入口" in map_skill.summary
    assert "这是什么项目" not in map_skill.summary
    faq_skill = build_system_skill_registry().get("product_help_faq")
    assert faq_skill is not None
    assert "FAQ" in faq_skill.summary
    assert "Cursor" in faq_skill.summary
    assert "官网" in faq_skill.summary
    assert "这是什么项目" not in faq_skill.summary

    assert "Markdown 语法" in help_body or ".md 怎么打开" in help_body
    # 与 product_bug_triage 分轨：用法短答不承载诊断仪式
    assert "product_bug_triage" in help_body
    assert "product_bug_triage" in faq_body
    assert "【L1" not in help_body and "【L1" not in faq_body
    assert "【L2" not in help_body and "【L2" not in faq_body
    assert "`product_bug`" not in help_body and "`model_limit`" not in help_body


def test_product_help_teaches_identity_first_then_other_topics():
    """身份问：可见正文先一句话答我方产品；禁把同名他品 / 第三方 Skill 仓当成本项目落地。"""
    help_body = _body("product_help")
    assert "这是什么项目" in help_body
    assert "你是什么" in help_body
    assert "首句" in help_body
    assert "consult 不能代替作答" in help_body
    assert "第三方" in help_body and "Skill" in help_body
    assert "落地" in help_body
    assert "同名他品" in help_body
    assert "用户气泡空着" in help_body or "写成工作区规则" in help_body
    # 08-15 官网钉：本条不改坏
    assert "https://fashitianxia.xyz" in help_body
    assert "https://fashitianxia.xyz/download" in help_body
    # map/faq 既有「项目」漂移锁：身份 HOW 只留在 product_help
    assert "项目" not in _body("product_help_map")
    assert "项目" not in _body("product_help_faq")


def test_product_bug_triage_skill_teaches_l1_l2_and_ceilings():
    """P0：定性四类 + 复现要点 + FAQ 分轨 + 证据上限 + 禁区；无 L4/新工具教法。"""
    body = _body("product_bug_triage")
    assert "product_bug" in body and "usage" in body
    assert "model_limit" in body and "unclear" in body
    assert "复现" in body
    assert "证据不足" in body
    assert "本会话" in body or "本会话可见" in body
    assert "ask_user" in body
    assert "服务端日志" in body
    assert "product_help" in body or "product_help_faq" in body
    assert "设置" in body and "反馈" in body
    assert "L4" in body or "开 PR" in body
    assert "dogfood" in body
    assert "跨用户" in body
    assert "扫长文" in body or "意图" in body
    # 目录 summary 只写这是什么；HOW（不预设归属）在 body
    skill = build_system_skill_registry().get("product_bug_triage")
    assert skill is not None
    assert skill.requires_tools == ()
    assert "故障" in skill.summary
    assert "主动" not in skill.summary
    assert "不预设归属" in body
    assert "product_bug_triage" in render_skill_directory(
        build_system_skill_registry(), _NO_LIVE_USER
    )


def test_product_bug_triage_does_not_attribute_our_selfcheck_to_user_env():
    """回归（cid bceeaa74）：我方自检/运行时报错不得被定性为用户环境 / 「不是产品 bug」。"""
    body = _body("product_bug_triage")
    assert "【我方报错·不预设归属】" in body
    assert "ExecEnvProbeFailed" in body
    assert "用户机器" in body or "本机环境" in body
    assert "这不是产品 bug" in body
    assert "一律认成" in body and "product_bug" in body
    assert "还缺什么证据" in body
    assert "L3" in body
    # 结论约束，不是扫原文分叉
    assert "不是意图分类器" in body
    assert "勿扫用户长文" in body


def test_team_cross_folder_skill_teaches_parallel_command():
    """跨文件夹并行指挥：对照句（坐哪张桌 / 认桌≠摸底 / 先建后派≠过闸催建）；不写 1–10 步菜单。"""
    body = _body("team_cross_folder")
    orch = _body("team_orchestration_advanced")
    assert "【跨文件夹并行指挥】" in body
    assert "list_folders" in body and "resolve_folder" in body
    # 嵌套：resolve 按路径，歧义候选带完整 rel_path。
    assert "按路径" in body and "rel_path" in body
    assert "ask_user" in body and "choice" in body
    assert "禁止" in body and "最近" in body
    assert "target_folder_id" in body
    assert "换桌" in body and "记忆" in body
    assert "folder_id" in body
    assert "默认桌" in body
    assert "scratch" in body
    assert "create_folder" in body
    # 与 mkdir 划清界限：容器 vs 当前工作区里的普通子目录。
    assert "mkdir" in body
    assert "自动建云文件夹" in body
    assert "register_local_project" in body  # 本机传统可教（禁当跨仓捷径）
    assert "open_local_project" in body
    assert "consult(ask_user_midtask)" in body
    assert "导入到云" not in body
    assert "连接 Git" not in body
    assert "本机传统" in body
    assert "新会话" in body or "遗留" in body or "打开当出生" in body or "只建云" in body
    assert "混部" in body
    assert "多" in body and "并行" in body
    assert "暂不支持" not in body
    assert "协作图不改" in body or "并行支线" in body
    # 先建齐再派（仅显式新建/多线；禁先扇出再补建）vs 拒后禁塌缩窄例外 vs 一般少派
    assert "先建后派" in body
    assert "先扇出再补建" in body and "禁止" in body
    assert "ask 齐" in body or "点名新建" in body
    assert "拒后禁塌缩" in body
    assert "bare_chat_no_target" in body
    assert "窄例外" in body
    assert "裸聊单目标" in body or "运行时继承" in body
    assert "能少则少" in body and "拿不准先少派" in body
    # 跨文件夹读写通吃派工换桌；CEO 只读跨桌仅认桌（禁「云端读不到本地」当唯一路径）
    assert "list_folder_dir" in body and "read_folder_file" in body
    assert "认桌" in body or "抽样" in body
    assert "出生桌" in body
    assert "读写通吃" in body or "只读摸底与改盘" in body or "只读摸底" in body
    assert "写仍派工换桌" not in body
    assert "只读跨桌摸底" not in body
    assert "云端读不到本地" in body and "禁止" in body
    # 空壳/双文件夹 kickoff：先问、同次两路、≠open/bind/挂载冒充
    assert "空壳" in body or "近空" in body
    assert "file_list" in body
    assert "file_list(pattern)" not in body
    assert "同一次" in body or "同次" in body
    assert "external_mount_readonly" in body
    assert "开发双仓" in body or "区外挂载" in body or "冒充" in body
    skill = build_system_skill_registry().get("team_cross_folder")
    assert skill is not None
    assert "跨文件夹" in skill.summary or "target_folder_id" in skill.summary
    assert "读写通吃" not in orch
    assert "target_folder_id" not in orch
    assert "consult(team_cross_folder)" in orch
    for n in range(1, 11):
        assert f"{n}. **" not in body, n


def test_team_orchestration_skill_teaches_review_narrowing():
    """审查收窄 / modules 不是配额：仍在组队手册（与跨文件夹分本）。"""
    body = _body("team_orchestration_advanced")
    assert "审查·收窄" in body or "审查收窄" in body
    assert "父目录" in body and "通读" in body
    assert "单点展示" in body
    assert "凑工种" in body
    assert "不是配额" in body
    assert "全面摸底" in body or "整仓审查" in body
    assert "编制自选" in body
    assert "冷启动建档除外" not in body
    assert "并列不打架" not in body
    assert "冷启动建档「≥2" not in body
    assert "不" in body and "覆盖" in body


def test_team_delivery_env_skill_teaches_empty_desk_no_project_shell():
    body = _body("team_delivery_env")
    orch = _body("team_orchestration_advanced")
    assert "【空桌勿套工程壳】" in body
    assert "本文件夹根即工作区根" in body
    assert "工程壳" in body
    assert "空桌" in body
    assert "site/" in body and "app/" in body
    assert "create_folder" in body
    assert "mkdir" in body
    assert "同名" in body and ("顶层" in body or "再套" in body)
    assert "court-game/" not in body
    assert "要不要再套一层" not in body
    assert "file_write" in body
    assert "【空桌勿套工程壳】" not in orch
    assert "consult(ask_user_midtask)" in body
    assert "可**推荐** Composer" not in body
    assert "consult(team_delivery_env)" in orch


def test_team_orchestration_skill_teaches_delegate_knobs():
    # Relocated from the old always-on hint: quality contract, output shaping,
    # the DAG-vs-nesting distinction (model tiers were removed).
    body = _body("team_orchestration_advanced")
    assert "deliverable" in body
    assert "coordinate=false" not in body
    assert "轻量直出" not in body
    assert "单人直出" not in body
    assert "finalize" not in body
    from agentcore.runtime.resolve.prompt import _CEO_CORE_HINT

    hint = _CEO_CORE_HINT
    assert "finalize" not in hint
    assert "讨论/判断默认自己答不必查" not in hint
    orch_sum = build_system_skill_registry().get("team_orchestration_advanced").summary
    assert "成文编制" in orch_sum
    assert "明示成文" in body
    assert "资料源" in body
    delivery_sum = build_system_skill_registry().get("team_delivery_env").summary
    assert "Office" in delivery_sum
    assert "depends_on" in body and "同一层" in body
    # 依赖流水线 bullet 须教「派前先判生产者→消费者」+ 正反例（何时串行 / 何时并行），
    # 而非只讲 DAG 机械怎么填——修复 CEO 默认全平铺把有先后的流水线拍平的根因。
    assert "生产者→消费者" in body
    assert "正例·串行" in body and "反例·勿串" in body
    assert "并行写盘" in body and "同路径" in body
    assert "私有产出" in body or "私有 path" in body
    assert "sibling_artifact" in body or "硬拒" in body
    assert "整合员" in body and "先 file_read" in body
    assert "再派同名" in body
    assert "嵌套委派" in body and "大模块" in body
    assert "编排自主" in body
    assert "摸底波" in body
    assert "假两段" in body
    assert "为编排而编排" in body or "禁为编排而编排" in body
    assert "凡大活必嵌套" in body
    assert "不是" in body and "modules" in body
    assert "仍非" in body and "必须扇出" in body
    assert "默认" in body and "二选一" in body
    assert "禁止再平铺" in body
    # 协调补派失败节点须标 replaces_run_id，引擎改写下游 depends_on
    assert "replaces_run_id" in body and "补派" in body
    # 对用户用人话；字段名只留工具通道
    assert "重新安排人补上" in body or "谁没交齐" in body
    assert "勿复述字段名" in body or "工具通道用语" in body
    # 派完「人已派出」唯一所有者 = how_you_act；本 skill 只留协调期可静默。
    assert "人已派出" not in body
    assert "可静默" in body
    assert "谁还在跑" not in body
    assert "谁在后台、完成后会再汇报" not in body
    # 纠正「一次只能一个 delegate / 同步阻塞到全队完成」误述：一回合一张图 + 同回合可再追加；
    # 禁止同构重派在跑任务。
    assert "一回合一张协作图" in body
    assert "再调" in body and "delegate" in body
    assert "同构" in body
    assert "不必" in body or "不是" in body  # 否定「必须等全队完成」
    # 新回合新图：跨回合新开一队接续（latest 不含图 id）；同回合合图仍在。
    assert "【新回合新图】" in body
    assert "【跨回合延续】" not in body
    assert "新开一队、接续上一张图" in body
    assert "向用户汇报用" not in body
    assert "不要填图 id" in body
    assert "append_to_execution_id" in body
    assert "已往上方协作图追加" not in body
    assert "同回合再调" in body or "并入当前活跃图" in body
    # 下游未定：先跑上游，再 replan(add=…) / 再 delegate（不教占位晚绑定填参）
    assert "下游未定" in body
    assert "replan(add=" in body
    assert "再 `delegate`" in body or "再 delegate" in body
    assert "有文件系统递路径" in body
    assert "没文件尽量给全文" in body
    assert "过长引擎" in body
    for token in (
        "checkpoint_after",
        "bind_after_deps",
        'coordination="wall"',
        "complexity_hint",
        "result_handling",
        "require_upstream",
    ):
        assert token not in body, token


def test_team_orchestration_skill_teaches_constraint_vs_solution_and_outline_step():
    # 认知分工 + 结构跟着证据走（L3/L4，法律论文案例的根因修复）: the skill teaches that
    # a deliverable's professional STRUCTURE belongs to the expert worker (not the
    # CEO's task), that review sections in the task body are an acceptance floor (not a
    # structure blueprint), and that研究级大型交付 should make「定结构」an evidence-driven,
    # user-gated outline step (调研 → 提纲：套餐停 / 先派再问 → 全文) rather than the
    # CEO fixing the skeleton up front. Pins the范式 so it can't silently drop out.
    # 必备章节标题与验收同字面；禁藏裸报错。
    body = _body("team_orchestration_advanced")
    assert "方案" in body  # 约束 vs 方案
    assert "审查章节" in body
    assert "task 正文" in body
    assert "required_sections" not in body
    assert "同字面" in body or "同一套原文" in body
    assert "近义" in body
    assert "裸报错" in body or "藏起契约" in body or "藏契约" in body
    assert "面向用户·大白话" in body or "用人话概括" in body
    assert "缺章失败须诚实可见" in body or "缺章失败如实可见" in body
    assert "提纲" in body
    assert "playbook=cite_write_review" in body or "套餐提纲步" in body
    assert "先只派提纲" in body and "ask_user" in body
    for token in (
        "checkpoint_after",
        "bind_after_deps",
        'coordination="wall"',
        "complexity_hint",
        "result_handling",
        "require_upstream",
    ):
        assert token not in body, token
    # 定案 A：调研驱动大型交付 — 各角 MD 笔记 files，禁三人 prose 只靠主笔落盘
    assert "三人 prose" in body or "角 prose" in body
    assert "仅主笔落盘" in body or "只靠主笔落盘" in body
    # 成文 PDF：HOW 唯一所有者 = team_delivery_env；编排 skill 只留 consult 钩
    assert "consult(team_delivery_env)" in body
    assert "md_to_pdf" not in body
    assert "reportlab" not in body
    # 演讲/PPT：task 交需求 ≠ 代写章节大纲；事故名 Marp 不进 skill
    assert "代写全章节大纲" in body
    assert "Marp" not in body


def test_team_delivery_env_skill_teaches_presentation_pptx_honesty():
    """有执行须真 pptx；无执行禁再派跑脚本、禁假称 Office 已落盘。

    案 20260803-ppt-office A+B + docx-office-exec-capability-lie B/C。
    案 1eb5eb99 A：压体积与模板保真解耦。
    案 5d25bfc3 / 08-08④：图形组织图直接拒+替代；仅文本/表格 Word；禁说满空派。
    """
    body = _body("team_delivery_env")
    orch = _body("team_orchestration_advanced")
    assert "python-pptx" in body
    assert "静默" in body and ".md" in body
    assert "PPT 已落盘可直接使用" in body or "Word 已落盘可直接使用" in body
    assert "交付缺口" in body or "标缺口" in body
    assert "form=files" in body or "artifacts" in body
    assert "file_copy" in body
    assert "当模板" in body
    assert "Presentation()" in body
    assert "再派" in body and "跑脚本" in body
    assert "数据文件整理" in body
    assert "consult(data_file_landing)" in body
    assert "结构报告" not in body
    assert "待跑变换脚本" not in body
    assert "暂时不可用" not in body
    assert "表格 → `.csv`" not in body
    assert "不算过闸" in body or "不算" in body
    assert "压体积" in body and "模板保真" in body
    assert "*_slim.pptx" in body or "slim.pptx" in body
    assert "相对模板" in body
    # 08-08④：图形组织图拒+替代；勿点名 SmartArt/DrawingML 当能力
    assert "图形组织图" in body
    assert "直接拒" in body
    assert "文本" in body and "表格" in body
    assert "说满" in body and "空派" in body
    assert "SmartArt" not in body and "DrawingML" not in body
    assert "凭印象" in body
    assert "先干再问" in body
    assert "顾问短对齐" in body
    assert "源数据文件下一步" in body
    assert "无法可靠解析的源数据文件" in body
    assert "表质量基线" in body
    assert "冒充表结构" in body
    assert "form/artifacts" in body
    assert "python-pptx" not in orch


def test_team_delivery_env_skill_teaches_deterministic_word_pdf_export():
    """`.docx`/`.pdf` 走 md_to_docx / md_to_pdf，与执行沙箱正交；无执行缺口只覆盖 pptx/xlsx。"""
    body = _body("team_delivery_env")
    orch = _body("team_orchestration_advanced")
    assert "md_to_docx" in body
    assert "python-docx" in body  # 只作禁用主路径出现
    assert "目标格式标不可产" in body
    assert "与执行正交" in body
    assert "确定性导出器" in body
    assert "`.docx`/`.pptx`/`.xlsx` 等且能力行" not in body
    assert "与执行正交" not in orch


def test_work_discipline_skill_teaches_windows_bat_crlf_ascii():
    """案 261bfc46 A：Windows .bat HOW 在 work_discipline；编排 skill 只留派工钩。"""
    wd = _body("work_discipline")
    assert ".bat" in wd
    assert "CRLF" in wd
    assert "ASCII" in wd
    assert ".ps1" in wd
    assert "转码" in wd or "改换行" in wd
    delivery = _body("team_delivery_env")
    assert "work_discipline" in delivery and ".bat" in delivery
    assert "双击即用" in delivery
    assert "ASCII-only" not in delivery
    orch = _body("team_orchestration_advanced")
    assert "双击即用" not in orch


def test_team_delivery_env_skill_teaches_image_gen_key_boundary():
    """案 image-gen-byok-egress-boundary：无 egress 禁代出图；Key 不进工作区。"""
    body = _body("team_delivery_env")
    orch = _body("team_orchestration_advanced")
    assert "生图" in body
    assert "代调" in body or "出图" in body
    assert "明文" not in body and "跨会话凭据脱敏" not in body
    from agentcore.runtime.resolve.prompt import assemble_system_prompt

    shared = assemble_system_prompt()
    assert "<credential_hygiene>" in shared
    assert "密钥" in shared and "明文" in shared
    assert "已识别凭据" in shared
    # 下载 404 须列目录核对（路径 HOW）；禁语表已出手册
    assert "面板可见·落盘对账" not in body
    assert "交付下载·面板路径" in body
    assert "404" in body or "下载失败" in body
    assert "file_list" in body
    assert "file_list(pattern)" not in body
    assert "生图" not in orch


def test_building_software_skill_teaches_cloud_install_verify_honesty():
    """案 cloud-web-install-deny A：云端不能装包时禁「自检全过/跑绿」。"""
    body = _body("building_software")
    assert "install" in body.lower()
    assert "结构自检" in body or "export_to_local" in body
    assert "自检全过" in body or "跑绿" in body
    assert "单测已绿" in body or "跑绿" in body
    # 案 88625：记分板对账
    assert "外环验绿对账" in body
    assert "test_run" in body
    # 巡检定案 B：末次同命令退出码（skill 与核同轴，不扩姿势 A）
    assert "最后一次同命令" in body
    assert "分项分开写" in body


def test_dispatch_writing_how_lives_in_skill_not_core():
    """第七刀：必读锚点 / 正反例 / 审查章节细则在编排 skill；核不复述百科。"""
    from agentcore.runtime.resolve.prompt import _CEO_CORE_HINT

    hint = _CEO_CORE_HINT
    orch = _body("team_orchestration_advanced")
    discipline = _body("work_discipline")

    assert "必读锚点" in orch
    assert "正例·交需求" in orch
    assert "反例·替它设计完" in orch
    assert "required_sections" not in orch
    assert "worker 看不到对话历史" in orch
    assert "权威线索" in orch
    assert "设计稿" in orch
    assert "读全局规则" not in orch
    assert "读全局规则" not in hint
    assert "团队负责人口吻" in orch
    assert "已确认约束" in orch
    assert "同字面" in orch or "同一套原文" in orch
    assert "写 task" in discipline or "【写 task】" in discipline

    for token in (
        "必读锚点",
        "正例·交需求",
        "反例·替它设计完",
        'required_sections": [',
        "must_contain",
        "deliverable.name",
        "requires_files",
        "少吓用户",
        "缺章失败如实可见",
        "引擎按小标题字面验",
        "设计三问",
        "补丁绊线",
        "探索信任",
        "团队负责人口吻",
        "动笔前在思考",
        "用户点名或导航指向的设计稿",
        "规则已在共享基座",
    ):
        assert token not in hint, token


def test_execution_how_lives_in_skill_not_core_encyclopedia():
    """第五刀：内环 code_diagnostics / 修码外环 HOW 在编排 skill；挂门长字面不在核。"""
    from agentcore.runtime.resolve.prompt import _CEO_CORE_HINT

    hint = _CEO_CORE_HINT
    orch = _body("team_orchestration_advanced")
    assert "code_diagnostics" in orch
    assert "tsc -b" in orch
    assert "写盘回执" in orch
    assert "验收员" in orch
    assert "wait_for" not in hint
    assert "口头假验收" not in hint
    assert "已登录，继续" not in hint
    assert "code_diagnostics" not in hint


def test_delivery_landing_how_lives_in_skills_not_core():
    """第四刀：空桌目录枚举 / create_folder vs mkdir / 分道百科在 Skill；核只留开火短卡。"""
    from agentcore.runtime.resolve.prompt import _CEO_CORE_HINT

    hint = _CEO_CORE_HINT
    delivery = _body("team_delivery_env")
    orch = _body("team_orchestration_advanced")
    help_map = _body("product_help_map")

    for token in (
        "site/",
        "mkdir",
        "court-game/",
        "执行位置分道",
        "收口硬约束",
        "系统浏览器",
        "约定文档出口",
        "写完再搬",
        "本文件夹根即工作区根",
    ):
        assert token not in hint, token

    assert "site/" in delivery and "app/" in delivery
    assert "src/" in delivery and "docs/" in delivery
    assert "mkdir" in delivery
    assert "create_folder" in delivery and "桌内工程根" in delivery
    assert "court-game/" not in delivery
    assert "同名" in delivery and ("顶层" in delivery or "再套" in delivery)
    assert "写完再搬" in delivery
    assert "工作区相对完整路径" in delivery
    assert "归位" in delivery and "移到工作区" in delivery
    assert "约定文档出口" in delivery
    assert "裸 `reviews/…`" in delivery
    assert "混用两套前缀" in delivery
    assert "交付下载·面板路径" in delivery
    assert "404" in delivery or "下载失败" in delivery
    assert "file_list" in delivery
    assert "file_list(pattern)" not in delivery
    assert "site/" not in orch
    assert "consult(team_delivery_env)" in orch

    assert "执行位置分道" in help_map
    assert "双击打开" in help_map
    assert "系统浏览器" in help_map
    assert "禁止给本机磁盘路径" in help_map
    assert "工作区根目录" in help_map
    assert "文件夹名" in help_map
    assert "文件名" in help_map
    assert "完整预览" in help_map
    assert "文件」面板" in help_map or "文件面板" in help_map


def test_slice_honesty_how_lives_in_skills_not_core():
    """第三刀：立刻派≠全量 HOW 真源在编排 / building_software；核只留开火短卡。"""
    from agentcore.runtime.resolve.prompt import _CEO_CORE_HINT

    hint = _CEO_CORE_HINT
    orch = _body("team_orchestration_advanced")
    build = _body("building_software")

    for token in (
        "编排自主",
        "摸底波",
        "路径 A",
        "路径 B",
        "不知轻重",
        "一人能扛整座成果",
        "不必先称重量",
        "你可以组队",
        "不算已拆编制",
        "整个里程碑",
        "临时交成果组长",
        "凡大活必嵌套",
        "根委派切片诚实",
        "目标·约束·验收",
    ):
        assert token not in hint, token

    assert "不知轻重" in orch
    assert "按块派" in orch and "不必先称重量" in orch
    assert "缝不清" in orch and "真两波" in orch
    assert "整个里程碑" in orch
    assert "目标·约束·验收" in orch
    assert "你可以组队" in orch and "先组队" in orch
    assert "不算" in orch and "已拆编制" in orch
    assert "路径 A" in orch and "路径 B" in orch
    assert "根委派切片诚实" in orch
    assert "编排自主" in orch and "摸底波" in orch
    assert "凡大活必嵌套" in orch
    assert "嵌套扇出" in orch
    assert "单 lead" in orch or "轻切片" in orch
    assert "薄旁路" in build
    assert "薄旁路" not in hint


def test_building_software_skill_teaches_admission_and_agent_diversion():
    """做软件 HOW：手写 tasks；薄旁路 / 验绿；切片 HOW 在编排 skill。"""
    body = _body("building_software")
    orch = _body("team_orchestration_advanced")
    assert "手写" in body and "tasks" in body
    assert "薄旁路" in body
    assert "不硬拒" not in body
    assert "consult(team_orchestration_advanced)" in body
    assert "轻切片" in orch or "真两段" in orch or "单 lead" in orch
    assert "边界未钉" in orch or "轻切片" in orch
    assert "满编" in orch
    assert "轻切片" not in body
    assert "真两段" not in body
    assert 'playbook="build_app"' not in body
    assert "diagnose_fix_verify" in body
    assert "build_website" not in body
    assert "intensity=solo" not in body
    assert "style=toolshed" not in body
    assert "Vue3" not in body
    assert "intensity=lean" not in body
    assert "intensity=full" not in body
    assert "app/" in body
    assert "满编" not in body


def test_team_orchestration_skill_teaches_sections_not_deleted_deliverable_keys():
    # 定案：已删 name/must_contain/min_length/requires_files；主题约束进 task /
    # team_brief；审查章节写进 task 正文。
    # 写盘用 form 三档 + artifacts。已删字段不留负面清单（与 schema 棘轮同向）。
    body = _body("team_orchestration_advanced")
    assert "审查章节" in body
    assert "task 正文" in body
    assert "验收项" in body or "验收要点" in body
    assert "2–4" in body or "2-4" in body
    assert "【看】" in body and "【存文档】" in body and "【改工程】" in body
    assert "form=workspace" in body
    assert "form=files" in body or "form\": \"files\"" in body or '"form": "files"' in body
    assert "artifacts" in body
    assert "team_brief" in body
    assert "required_sections" not in body
    assert "must_contain" not in body
    assert "细则清单进 `deliverable.must_contain`" not in body
    assert "细则清单进 deliverable.must_contain" not in body
    assert '"name": "审查意见' not in body
    assert "deliverable.name" not in body
    assert "requires_files" not in body
    assert "min_length" not in body
    assert "task.objective" not in body
    assert "Stanford" not in body and "McKinsey" not in body
    assert "取证路径" in body or "机构名" in body


def test_team_orchestration_skill_teaches_parallel_review_via_brief():
    # 并行审查靠 team_brief 对齐口径，不再经 post_note 广播。
    body = _body("team_orchestration_advanced")
    assert "post_note" not in body
    assert "heads_up" not in body
    assert "team_brief" in body


def test_team_orchestration_skill_teaches_review_contract_template():
    # 审查默认 prose；章节写进 task 正文；结构化 JSON 走文件通道（artifacts）。
    # 钉：task 写审查章节、web_search 软引导；废字段名缺席。
    body = _body("team_orchestration_advanced")
    assert "审查类任务的统一契约" in body
    assert "默认 prose" in body
    assert "问题" in body and "建议" in body and "评分" in body
    assert "审查章节写进 task 正文" in body or "审查章节" in body
    assert "required_sections" not in body
    assert "结构化交付走文件通道" in body
    assert "artifacts" in body
    assert "output_format" not in body
    assert "禁止" in body  # 瞒报等现行禁令
    assert "web_search" in body
    assert "全文" in body or "复制" in body
    assert "deliverable" in body
    assert "problems" in body and "suggestions" in body and "score" in body


def test_team_orchestration_skill_teaches_team_brief():
    body = _body("team_orchestration_advanced")
    assert "team_brief" in body
    assert "seed_notes" not in body
    assert "建墙" not in body
    assert "正交扇出" in body
    assert "不写 brief" in body
    assert "一行一条" in body
    assert "同一行" in body
    assert "换行" in body
    assert "一次完整写入" in body
    assert "两篇成稿" in body
    assert "短规格" in body
    assert "互填" in body
    assert "成环" in body
    for token in (
        "checkpoint_after",
        "bind_after_deps",
        'coordination="wall"',
        "complexity_hint",
        "result_handling",
        "require_upstream",
    ):
        assert token not in body, token
    # 定稿漂移 A′：team_brief / task 固定「已确认约束」；约束优先于附件旧表；自拟默认不进该块
    assert "已确认约束" in body
    assert "约束块优先" in body or "优先" in body
    assert "自拟的默认" in body
    assert "冒充拍板" in body
    assert "无已拍板项" in body


def test_team_orchestration_skill_teaches_coordination_wall_vs_none():
    body = _body("team_orchestration_advanced")
    assert "team_brief" in body
    assert "建墙" not in body
    assert "正交扇出" in body and "不写 brief" in body
    for token in (
        "checkpoint_after",
        "bind_after_deps",
        'coordination="wall"',
        "complexity_hint",
        "result_handling",
        "require_upstream",
    ):
        assert token not in body, token
    assert "consult `build_website`" not in body
    assert "consult(build_website)" not in body
    assert "style=toolshed" not in body
    assert "intensity=solo" not in body
    assert "单页一人" in body or "一人做完" in body
    assert "营销皮" in body
    assert "自动静态质检" in body
    assert "可开 web_quality_scan" not in body
    assert "web_quality_scan" not in body
    assert "visual_critic" not in body


def test_debate_skill_teaches_debate_tool_forms_and_dual_products():
    """HOW：三形态、双产物、收尾诚实；何时用工具不写编号树（那在 schema）。"""
    body = _body("debate_and_review")
    assert "debate" in body and "辩论" in body
    assert "red_team" in body and "roundtable" in body
    assert "is_subject" in body
    assert "motion" in body and "sides" in body
    assert "决策简报" in body and "交锋叙事线" in body
    assert "delegate" in body and "ask_user" in body
    assert "别抹平证据状态" in body or "既定事实" in body
    assert "升格" in body or "核实状态" in body
    assert "原样传达" in body or "保留意见" in body
    assert "不引入场外量化" in body
    assert "量化估算" in body
    assert "deep_multi_lens_research" in body
    assert "跨维度" in body or "各透镜" in body
    assert "不可" in body or "跳过" in body
    assert "未辩先写简报" in body or "编造" in body
    assert "辩论收报" in body or "正反拍板" in body
    assert "收尾分流" in body or "分维" in body
    assert "硬上限" in body or "拒绝调用" in body
    assert "论点清单" in body
    for mark in ("①", "②", "③", "④"):
        assert mark not in body
    assert "何时用 `debate`（而非" not in body


def test_debate_skill_teaches_intent_alignment_before_opening():
    """开辩前：对立极不得偷换；指代模糊先澄清；形态冲突不是跳过调研通行证。"""
    body = _body("debate_and_review")
    assert "对立极" in body
    assert "偷换" in body
    assert "先澄清" in body
    assert "ask_user" in body
    assert "形态冲突" in body
    assert "非跳过调研通行证" in body or "不】构成跳过前置调研" in body
    assert "已有" in body and ("motion_card" in body or "调研产物" in body)
    assert "deep_multi_lens_research" in body
    assert "模拟法庭" in body or "庭审" in body
    assert "以用户点名形态为准" in body or "用户点名形态" in body
    assert "减轻派" not in body
    assert "最近很火的那个" not in body


def test_debate_skill_teaches_thin_stance():
    """stance 一句立场；形状闸在 schema；skill 只留正例与剧本吸引子。"""
    from agentcore.tools.builtin.debate.schema import DEBATE_PARAMETERS, STANCE_MAX_CHARS

    body = _body("debate_and_review")
    assert "立场倾向" in body
    assert "一句话" in body or "单句" in body
    assert "支持一审判决正确" in body or "判赔过重" in body
    assert "核心论点" in body or "系统论证" in body
    assert "background" in body
    assert "剧本" in body or "工作产出" in body
    assert "48" not in body
    assert str(STANCE_MAX_CHARS) in body or "80" in body
    assert "首先/其次" not in body

    stance_desc = DEBATE_PARAMETERS["properties"]["sides"]["items"]["properties"]["stance"][
        "description"
    ]
    assert "一句话立场" in stance_desc or "立场倾向" in stance_desc
    assert "单句" in stance_desc or "一句话" in stance_desc
    assert "background" in stance_desc
    assert "核心论点" in stance_desc or "系统论证" in stance_desc or "主张结论" in stance_desc
    assert stance_desc.count(str(STANCE_MAX_CHARS)) >= 1
    assert DEBATE_PARAMETERS["properties"]["sides"]["items"]["properties"]["stance"][
        "maxLength"
    ] == STANCE_MAX_CHARS


def test_debate_skill_teaches_background_for_concrete_cases():
    """具体案件可传客观事实底料；纯价值观不必。条数配方不进 skill。"""
    body = _body("debate_and_review")
    assert "background" in body
    assert "具体案件" in body or "真实事件" in body
    assert "客观事实" in body
    assert "不必传" in body or "不必" in body
    assert "3–5" not in body
    assert "某中院" not in body


def test_revise_skill_teaches_recall_and_delegate_fallback():
    body = _body("revising_a_product")
    assert "continue_from_run_id" in body
    assert "delegate" in body
    # 调查批确认修 → 默认乙；换 title ≠ 换职能；禁再套 diagnose_fix_verify 冷开。
    assert "默认乙" in body or "确认按结论修" in body
    assert "不算" in body and ("换职能" in body or "title" in body)
    assert "diagnose_fix_verify" in body and "禁止" in body
    # 甲边界：真换职能 / 无现场 / 合并 → 冷委派 + replaces_run_id.
    assert "冷委派" in body and "replaces_run_id" in body
    assert "真换职能" in body or "非仅改 title" in body
    assert "补派" in body or "接手" in body
    # 成篇未写完 → continue_from（短指针，细则在 long_form）。
    assert "成篇未写完" in body
    # 队员坐本任务桌相关工具面；不教 tools 白名单。
    assert "只增不减" not in body
    assert "声明超集" not in body
    assert "白名单" not in body
    assert "相关工具" in body
    assert "test_run" in body
    # 修订落盘纪律：优先 str_replace / file_append；整盖允许但勿惰性省略。
    assert "str_replace" in body and "file_append" in body
    assert "file_write" in body
    assert "优先" in body
    assert "中间省略" in body
    assert "全文重写" not in body
    assert "**禁止**对已有成篇成品再 `file_write`" not in body
    assert "禁止骨架/最小实现" not in body
    # 写参收成已落盘短状态后：先 file_read 取真文，再 str_replace/按真文写，禁止把短状态当正文重发。
    assert "已落盘短状态" in body
    assert "_landed_summary" not in body
    assert "file_read" in body
    assert "真文" in body
    assert "str_replace" in body
    assert "禁止" in body and "重发" in body


def test_team_orchestration_skill_teaches_revision_local_edit():
    body = _body("team_orchestration_advanced")
    assert "有界返工环" in body
    assert "str_replace" in body
    assert "中间省略" in body or "已保留首尾" in body


def test_ask_user_kickoff_skill_teaches_short_clarify():
    skill = build_system_skill_registry().get("ask_user_kickoff")
    assert skill.requires_tools == ("ask_user",)
    body = skill.body
    assert "assumptions" in body
    assert "2–6 字项名" in body
    assert "questions" in body
    assert "要什么" in body and "给谁" in body
    assert "短问" in body or "短澄清" in body
    assert "催收敛" in body or "候选菜单" in body
    assert "开工提案卡" not in body
    assert "提案体硬闸" not in body
    assert "一键开做" not in body
    assert "缺信息" in body and "短问" in body
    assert "开工卡取消" not in body
    assert "机制软注入" in body
    assert "DESIGN" in body
    assert "checkpoint_after" not in body
    # 缺主体：派工跟勾选/人话走；空 continue 回灌才「按确认默认」
    assert "缺主体" in body
    assert "按确认默认" in body
    assert "default" in body
    assert "自拟主体" in body or "无勾选" in body
    # 案 ask-empty-continue-default-dispatch：决策/澄清短问同样须 default
    assert "决策/澄清短问" in body
    assert "先问你" not in body
    # 午后巡 d4d5/53f0：继续须承接上轮确认项；新建仓库/本地目录须 default 路径
    assert "继续·承接确认项" in body
    assert "至少复述" in body or "承接确认" in body
    assert "默认路径" in body
    # 短确认·只补缺口：核只留指针；全文在本 Skill
    assert "短确认·只补缺口" in body
    assert "prior_delivery_gaps" in body
    assert "整锅重派" in body
    assert "静默自拟" in body
    # 交付档：桌上结果 label，不映射编制套餐；建站只留形态消歧
    assert "交付档" in body
    assert "桌上结果" in body
    assert "不映射编制套餐" in body
    assert "一页先上" not in body
    assert "品牌站流水线" not in body
    assert "已下线" not in body
    assert "手写" in body and "tasks" in body
    assert "intensity=solo" not in body
    assert "style=toolshed" not in body
    assert "工具壳" in body
    assert "先一条主路径" in body
    assert "一次做完再拆" in body
    assert "只改一处" in body
    assert "编制" in body
    assert "做个网站" in body
    assert "展示页" in body or "业务应用" in body
    assert "已钉形态" in body or "形态" in body
    assert "consult(team_orchestration_advanced)" in body
    assert "自动静态质检" in body
    assert "可开 web_quality_scan" not in body
    assert "web_quality_scan" not in body
    assert "visual_critic" not in body
    assert "营销皮" in body
    assert "满编" in body
    assert 'playbook="build_app"' not in body
    assert "playbook=\"build_website\"" not in body
    assert "consult(build_website)" not in body
    # 点名载体/手段·顾问短对齐（与规格已齐正交；禁硬闸；禁单场景剧本）
    assert "点名载体" in body or "载体/手段" in body
    assert "顾问" in body
    assert "（推荐）" in body
    assert "放第一" in body
    assert "零摩擦" in body
    assert "盖不住" in body or "做不到" in body
    assert "规格已齐" in body
    assert "内容齐" in body or "手段已核" in body
    assert "可读" in body or "可扫" in body or "可编辑" in body
    assert "不得" in body or "禁止" in body
    assert "SmartArt" not in body and "DrawingML" not in body
    # 图形组织图拒+替代：唯一 HOW 在 team_delivery_env
    assert "consult(team_delivery_env)" in body
    assert "图形组织图" not in body
    assert "直接拒" not in body
    assert "说满" in body and "空派" in body
    assert "话术锚点" not in body
    assert "极宽" not in body
    assert "载体" in body
    assert "format_options" not in body
    assert "提案墙" in body


def test_ask_user_kickoff_skill_omits_retired_format_fields():
    body = _body("ask_user_kickoff")
    assert "style_options" not in body
    assert "format_options" not in body
    assert "提案墙" in body
    assert "短问" in body or "短澄清" in body


def test_ask_user_kickoff_skill_teaches_software_delivery_not_default_html():
    body = _body("ask_user_kickoff")
    assert "软件" in body or "应用" in body
    assert "交付形态" in body
    assert "单 HTML" in body
    assert "diagnose_fix_verify" in body
    assert 'playbook="build_app"' not in body
    # 切片 / 边界未钉 HOW 在 building_software
    assert "consult(building_software)" in body
    assert "满编" in body


def test_ask_user_skills_ordinary_choice_is_one_line():
    """普通短问权衡写进选项名；第二句仅专用 card。"""
    kickoff = _body("ask_user_kickoff")
    midtask = _body("ask_user_midtask")
    assert "可配 `detail`" not in kickoff
    assert "`message`/`detail`" not in kickoff
    assert "`label` / `detail` / `message`" not in kickoff
    assert "权衡写进选项名" in kickoff or "权衡写进 `label`" in kickoff
    assert "勿填 `detail`" in kickoff
    assert all("问句写" in b and "prompt" in b for b in (kickoff, midtask))
    assert "配一行 `detail`" not in midtask
    assert "发散挑选" in midtask
    assert "continue_from_run_id" in midtask
    assert "str_replace" in midtask


def test_ask_user_midtask_skill_teaches_carrier_advisory_crossref():
    body = _body("ask_user_midtask")
    assert "载体" in body or "手段" in body
    assert "顾问" in body or "kickoff" in body
    assert "零摩擦" in body
    assert "落地页 HTML" in body or "不打扰" in body


def test_catalog_has_no_build_website_skill():
    """目录不再登记 build_website；不新开 skill / playbook。"""
    reg = build_system_skill_registry()
    names = {s.name for s in reg.list_all()}
    assert "build_website" not in names
    assert "building_software" in names
    assert reg.get("build_website") is None


def test_ask_user_midtask_skill_teaches_fork_and_annotate():
    # 途中拍板 split: the mid-task fork + 何时不打断 (proceed-and-annotate) +
    # debate closing handed to the user. Gated on ask_user; the checkpoint
    # mechanism is now its own skill, not part of midtask.
    skill = build_system_skill_registry().get("ask_user_midtask")
    assert skill.requires_tools == ("ask_user",)
    body = skill.body
    assert "采纳正方" in body  # debate closing handed to the user
    assert "假设" in body and "若不符请指正" in body  # proceed-and-annotate
    assert "blocking=false" not in body
    assert "unlocks" not in body
    assert "立刻按默认继续把回合做完" not in body
    assert "绝不等待" not in body
    assert "checkpoint_after" not in body
    # 定向修订委派须写明局部改纪律（有界返工环）。
    assert "str_replace" in body
    assert "中间省略" in body or "file_write" in body
    # 定案：优化项目 ≠ 默认催 open_local；附件收窄范围时先干活；本机传统可教非默认。
    assert "open_local_project" in body
    assert "≠默认开文件夹卡" in body or "收窄本轮" in body
    assert "在哪工作" in body
    assert "仅新建会话" in body
    assert "勿推销本机草稿" in body
    assert "开工前置" in body
    assert "register_local_project" in body
    assert "导入到云" in body
    assert "从 Git 克隆" in body
    assert "连接 Git" not in body
    assert "合法非默认" in body or "非默认" in body
    assert "本机传统" in body
    assert "Ask" not in body or "改导" not in body  # 不得再写 Ask 点了会改导
    assert "team_cross_folder" in body
    assert "consult(team_cross_folder)" in body
    assert "consult(team_delivery_env)" in body
    assert "开发双仓" not in body
    assert "target_folder_id" not in body
    assert "读写通吃" not in body
    assert "跨文件夹须派工换桌" not in body
    # 已绑/本机传统工程：跑当前；换工程优先导入/连 Git。
    assert "本机传统" in body or "已绑" in body or "跑" in body
    assert "跑" in body and "当前" in body
    # Web 假确认修复：引导桌面下载 + 未见挂载勿称已确认
    assert "https://fashitianxia.xyz/download" in body
    assert "授权已确认" in body
    assert "本对话已授权区外目录" in body
    # 区外授权 HOW 唯一所有者 = consult(external_mount_readonly)
    assert "consult(external_mount_readonly)" in body
    assert "授权后发现" not in body
    assert "口头同意" not in body
    assert "失败分型" not in body
    from agentcore.runtime.resolve.prompt import capability_how_suffix

    granted = capability_how_suffix({"external_mount_readonly"})
    assert "well_known" in granted
    assert "target_name" in granted
    assert "先写工作区" in granted and "file_copy" in granted
    assert "只读已挂" in granted
    assert "口头同意" in granted
    # 案 20260803-cloud-local-root-auth-where A：自称桌面须复检；禁「就好办了」/臆造 Folders
    assert "通道复检" in body
    assert "就好办了" in body
    assert "口述不得覆盖" in body
    assert "Folders" in body
    assert "导入到云" in body
    assert "从 Git 克隆" in body
    assert "连接 Git" not in body
    assert "Composer" in body
    assert "授权在哪里" in body
    # 案 79789150：承诺落盘前对齐 / 用户点名确认后再存 → 阻塞短问 + default
    assert "落盘前对齐" in body
    assert "按当前设计落盘" in body
    assert "阻塞短问" in body
    assert "本回合明示" in body
    # 午后巡 e670：标完成前先报真实断点
    assert "收尾·先报断点" in body
    assert "都实现了" in body or "收尾完成" in body
    assert "断点" in body


def test_delegate_checkpoint_skill_teaches_wave_boundary_pause():
    # 委派途中把关：套餐提纲步会停，或先只派提纲、收回后再问。Gated on
    # ask_user (the live-user proxy) since it pauses for user review.
    skill = build_system_skill_registry().get("delegate_checkpoint")
    assert skill.requires_tools == ("ask_user",)
    body = skill.body
    assert "playbook=cite_write_review" in body
    assert "套餐提纲步" in body
    assert "先只派提纲" in body
    assert "ask_user" in body
    assert "再派撰稿" in body
    # B1 轻教法：用户明文要把关 → 必用结构化路径，禁止纯聊天代卡。
    assert "明文" in body
    assert "必用" in body
    assert "禁止纯聊天" in body
    assert "cite_write_review" in body
    for token in (
        "checkpoint_after",
        "bind_after_deps",
        'coordination="wall"',
        "require_upstream",
    ):
        assert token not in body, token


def test_verify_and_fix_skill_teaches_test_run_loop():
    skill = build_system_skill_registry().get("verify_and_fix")
    # Ungated: consult is CEO+worker; body is the worker loop. Do not gate on
    # test_run (CEO has none) or delegate (would hide it from workers).
    assert skill.requires_tools == ()
    body = skill.body
    assert "test_run" in body
    assert "str_replace" in body
    assert "escalate" in body
    # 阶段3：编辑以磁盘为真源；禁骨架 file_write 冒充修复
    assert "磁盘" in body
    assert "骨架" in body
    assert "file_write" in body
    assert "部分完成" in body
    assert "degraded" not in body


def test_long_form_writing_skill_teaches_skeleton_fill():
    skill = build_system_skill_registry().get("long_form_writing")
    assert skill.requires_tools == ("delegate",)
    body = skill.body
    assert "file_write" in body
    assert "file_append" in body
    assert "大纲" in body
    assert "骨架" in body
    assert "Artifact-first" in body or "骨架填空" in body
    assert "file_read 抽查" not in body
    assert "manifest" in body
    assert "code_execute" in body
    assert "handoff" in body
    assert "禁止" in body and "file_read" in body
    # 成篇修订例外：≠ 验真空转回读；清参后改稿才可先 file_read，禁短状态重发。
    assert "验真" in body and "例外" in body
    assert "已落盘短状态" in body
    assert "_landed_summary" not in body
    assert "清参" in body or "改稿" in body
    assert "真文" in body
    assert "str_replace" in body
    assert "重发" in body
    # 定案 A：主路径一次完整 write；可选骨架；禁「禁止整篇一次 file_write」硬教条
    assert "主路径" in body and "完整正文" in body
    assert "禁止】整篇一次" not in body and "禁止】无骨架整篇一次" not in body
    assert "连续写失败" in body or "分段" in body
    # 提纲过目 / 成文后梯度 HOW 在编排手册
    assert "consult(team_orchestration_advanced)" in body
    assert "先只派提纲" not in body
    assert "map_fanout" not in body
    assert "cite_write_review" not in body
    for token in (
        "checkpoint_after",
        "bind_after_deps",
        'coordination="wall"',
        "require_upstream",
    ):
        assert token not in body, token
    # 与多角协作划界：选档走编排；材料已齐才单写手
    assert "划界" in body
    assert "材料已齐" in body
    assert "成文后梯度" in body or "档 3" in body
    # 论文并行拆章：单主文件 + 合并责任（禁各写各的就交）；不误伤多产物。
    assert "单主文件" in body or "同一主文件" in body or "最终主文件" in body
    assert "合并责任" in body or "merge" in body.lower()
    assert "各写各的" in body
    assert "建站" in body
    # 单写手超长分波 + 成篇未写完 continue_from；MD 禁 write_section
    assert "分波" in body
    assert "章节范围" in body or "第 1" in body
    assert "continue_from_run_id" in body
    assert "成篇未写完" in body
    assert "write_section" in body and "禁止" in body
    assert "FILL" in body or "str_replace" in body
    assert "continue_writing" not in body
    assert "replaces_run_id" in body  # 仅冷接手对照
    # 多源合并核独有句下沉：骨架禁审校清理连环、禁 CEO 自写、极低 max_rounds
    assert "多源合并" in body and "成篇优先" in body
    assert "CEO 自写" in body
    assert "审校" in body and "清理" in body
    assert "max_rounds" in body
    assert "流水线已在执行" in body or "合并进行中" in body
    assert "SECTION:" in body or "骨架" in body
    # 成品文件只装成品：从常驻核迁入（task 只写正文；元信息进回复/handoff）
    assert "【成品文件只装成品】" in body
    assert "起诉状" in body and "合同" in body
    assert "使用前请核对" in body
    assert "原样打印" in body
    assert "提交出去" in body
    # MD→PDF：CEO 编排只留 consult；HOW 在 team_delivery_env
    assert "consult(team_delivery_env)" in body
    assert "md_to_pdf" not in body
    assert "reportlab" not in body
    # 目录只 WHEN（超长成篇）；file_write 主路径 / write_section 等 HOW 已钉 body
    assert "成篇" in skill.summary or "超长" in skill.summary

    landing = build_system_skill_registry().get("long_form_landing")
    assert landing is not None
    assert landing.requires_tools == ()
    assert landing.audience == ("worker",)
    assert "file_write" in landing.body
    assert "write_section" in landing.body
    assert "manifest" in landing.body
    # Worker 不能 consult CEO-only team_delivery_env；落盘末步仍在 landing。
    assert "md_to_pdf" in landing.body
    assert "consult(team_delivery_env)" not in landing.body
    # Worker book is landing HOW, not 派工百科.
    assert "continue_from_run_id" not in landing.body
    assert "map_fanout" not in landing.body
    assert "checkpoint_after" not in landing.body


def test_long_form_landing_teaches_omission_and_shrink_hard_reject():
    """反例与缩水硬拒钉 landing；清参步骤已有则勿双写。"""
    landing = build_system_skill_registry().get("long_form_landing")
    assert landing is not None
    body = landing.body
    assert "中间省略" in body
    assert "50%" in body and "800" in body
    assert "硬拒" in body
    assert "清参后改稿" in body
    assert body.count("清参后改稿") == 1


def test_data_file_landing_skill_teaches_script_transform_and_invariants():
    skill = build_system_skill_registry().get("data_file_landing")
    assert skill is not None
    # Ungated: consult is CEO+worker; body is the worker loop. Do not gate on
    # code_execute (CEO has none → skill would vanish from the supervisor catalog).
    assert skill.requires_tools == ()
    assert skill.audience == ("ceo", "worker")
    body = skill.body
    assert "微信" not in body
    assert "手抄" in body
    assert "code_execute" in body
    assert "不变量" in body
    assert "分类笔数" in body or "源记录总数" in body
    assert "口径" in body
    assert "改口" in body
    assert "人质" in body
    assert "先交" in body
    assert "未装配" in body
    assert "账单" in body and "报表" in body and "导出记录" in body
    assert "看原件" in body
    assert "认形态" in body
    assert "一次性变换脚本" in body
    # Technique only — library names live in cloud_python.txt, not this skill.
    assert "抽表" in body
    assert "按页抽文本" in body
    # 有框线 vs 文本流：抽表空表不是死胡同；预解析残文仍不可信。
    assert "格子表" in body
    assert "文本流" in body
    assert "文本层" in body
    assert "空表" in body
    assert "预解析" in body and "残文" in body
    assert "不要用按页抽文本的库" not in body
    assert "pypdf" not in body
    assert "openpyxl" not in body
    assert "pandas" not in body
    assert "pdfplumber" not in body
    assert "汇总页" in body
    assert "form=files" in body
    # 目录只 WHEN（丢数据文件要可打开表）；表质量基线等 HOW 钉 body
    assert skill.summary
    assert "账单" in skill.summary and "报表" in skill.summary
    assert "质量基线" in body
    assert "明细与汇总" in body
    assert "冻结" in body and "筛选" in body
    assert "合计" in body
    assert "口径写进表里" in body or "口径写进表内" in body


def test_data_file_landing_table_quality_baseline_is_generic():
    """交表质量基线对任意表格成立；禁止写成账单/财务专用模板。"""
    skill = build_system_skill_registry().get("data_file_landing")
    assert skill is not None
    body = skill.body
    assert "【表格产物·质量基线】" in body
    baseline = body.split("【表格产物·质量基线】", 1)[1].split("【口径】", 1)[0]
    assert "明细" in baseline and "汇总" in baseline
    assert "日期" in baseline and "数值" in baseline
    assert "千分位" in baseline
    assert "冻结" in baseline and "筛选" in baseline
    assert "合计" in baseline
    assert "口径" in baseline and "产物内" in baseline
    assert "required_sections" not in baseline
    assert "task" in baseline or "team_brief" in baseline
    # 通用：不绑数据源 / 行业剧本。
    assert "微信" not in baseline
    assert "支付宝" not in baseline
    assert "账单" not in baseline
    assert "财务" not in baseline
    assert "专用模板" in baseline or "数据源" in baseline


def test_data_file_landing_no_exec_is_complete_delivery():
    """无执行：交付形态是结构报告+待跑脚本+一句人话，不是「表的缺口」。"""
    skill = build_system_skill_registry().get("data_file_landing")
    assert skill is not None
    body = skill.body
    assert "【有执行】" in body
    assert "【无执行】" in body
    no_exec = body.split("【无执行】", 1)[1]
    with_exec = body.split("【无执行】", 1)[0]
    assert "结构报告" in no_exec
    assert "待跑变换脚本" in no_exec
    assert "暂时不可用" in no_exec
    assert "稍后再试" in no_exec
    assert "artifacts" in no_exec
    assert "form=files" in no_exec
    assert "不是缺口" in no_exec or "正常完成" in no_exec
    assert "绑本机" in no_exec
    assert "导入到云" in no_exec
    assert "终端跑脚本" in no_exec
    assert "凭空" in no_exec
    assert "已校验" in no_exec
    assert "标缺口" not in no_exec
    assert "不可靠" not in no_exec
    # 有执行路径仍是脚本变换 + 校验后交文件。
    assert "不变量校验" in with_exec
    assert "产出可打开文件" in with_exec
    assert "暂时不可用" not in with_exec
    assert "终端跑脚本" not in with_exec


def test_data_file_landing_pdf_text_stream_uses_text_layer():
    """有执行：文本流 PDF 以执行环境抽出的文本层为真值；抽表空表≠失败。

    锁的是 skill 文本语义。本机 Win32 沙箱不健康，该修复未经行为验证。
    """
    skill = build_system_skill_registry().get("data_file_landing")
    assert skill is not None
    with_exec = skill.body.split("【无执行】", 1)[0]
    assert "框线" in with_exec and "格子表" in with_exec
    assert "文本流" in with_exec
    assert "文本层" in with_exec
    assert "空表" in with_exec and "失败" in with_exec
    assert "预解析" in with_exec
    assert "残文" in with_exec
    assert ".md" in with_exec
    assert "file_read" in with_exec
    assert "不是" in with_exec and "真源" in with_exec
    # 旧死胡同：一律禁抽文本层。
    assert "不要用按页抽文本的库" not in with_exec
    assert "PDF 重抽须用" not in with_exec


def test_deep_multi_lens_research_listed_and_gated_on_delegate():
    skill = build_system_skill_registry().get("deep_multi_lens_research")
    assert skill is not None
    assert skill.requires_tools == ("delegate",)
    directory = render_skill_directory(build_system_skill_registry(), _NO_LIVE_USER)
    assert "deep_multi_lens_research" in directory
    assert skill.summary in directory


def test_deep_multi_lens_research_teaches_parallel_lenses_and_motion_card():
    """异质透镜 + 命题卡 + 幕 2 先辩后报；入口用对照句，不写编号树。"""
    body = _body("deep_multi_lens_research")
    assert "法律" in body and "品牌商业" in body
    assert "舆情公关" in body and "文化社会" in body
    assert "depends_on" in body
    assert "lens_crosscheck" in body
    assert "品牌危机" in body or "公共事件" in body
    assert "lenses" in body and "≥2" in body
    assert "凡大事" not in body
    assert "motion_card" in body
    assert "handoff" in body
    # 缺主体：问卡 HOW 在 ask_user_kickoff
    assert "缺主体" in body
    assert "consult(ask_user_kickoff)" in body
    assert "静默自拟" not in body
    # 幕 1 约定文档落盘：research/ + form=files / artifacts（叠加 handoff，不替代）
    assert "AgentCore/文档/research/" in body
    assert "透镜报告" in body
    assert "汇总与命题卡" in body
    assert "form=files" in body or "form\": \"files\"" in body or "`form=files`" in body
    assert "artifacts" in body
    assert "叠加" in body or "不得替代" in body
    # 薄立场 + rationale 铁律（与 debate_and_review / motion_card 契约相容）
    assert "薄立场" in body or "一句话" in body
    assert "48" not in body  # 旧字符闸口径已退役
    assert "rationale" in body
    assert "继续调研" in body or "对抗检验" in body
    assert "见分歧" in body  # 严禁见分歧就建议开辩
    assert "真对立轴" in body  # 存在真对立轴则必须产卡
    assert "对比综述" in body
    assert "不出辩题" in body
    # CEO 禁止自搜替代四路；探路停手指向编排【工作流】。查询词数契约不在本 skill（基座 + web_search schema）。
    assert "【工作流】" in body
    assert "0～1 轮" not in body and "默认 0～1" not in body
    assert "禁止自搜" in body or ("禁止" in body and "替代四路" in body)
    assert "2–3 核心词" not in body and "2–3 个核心词" not in body
    assert "极端过长" not in body
    assert "≤8 词" not in body
    assert "本回合" in body and "debate" in body
    assert "用户同意" in body or "批准" in body
    # 批 B：推进卡即授权；勿口头征求、勿本回合自调 debate
    assert "阶段推进卡" in body or "推进卡" in body
    assert "勿口头征求" in body
    # 任务书须点名结构化字段；禁 markdown / Followups 旁路
    assert "handoff.motion_card" in body or "对象字段" in body
    assert "markdown" in body.lower() or "正文" in body
    assert "Followups" in body or "芯片" in body or "推进卡" in body
    # 幕 2：先真辩完赛再跨维简报；已辩才可复用；赛况忠实禁编造；不抹平证据/裁决
    assert "先辩后报" in body or "先真辩" in body
    assert "跨维度" in body or "各透镜" in body
    assert "跳过" in body and "debate" in body  # 禁因简报形状跳过 debate
    assert "已辩复用" in body or "不重开辩" in body
    assert "编造" in body
    assert "辩论收报" in body or "正反拍板" in body  # 禁塌成默认辩后收尾
    assert "分维" in body or "小标题" in body
    assert "待核实" in body or "保留" in body
    # 引用即出处 P3：透镜成稿主张须证 + 汇总继承（prompt 软约束；不强迫辩词二分）。
    assert "主张" in body or "关键数字" in body or "关键结论" in body
    assert "#rN" in body
    assert "不强迫" in body or "二分" in body
    assert "继承" in body and ("#rN" in body or "待核实" in body)
    # 入口分流：调研意图走本 skill；点名开辩勿抢拦（分流句前置）
    assert "入口分流" in body or "按意图" in body
    assert "debate_and_review" in body
    assert "直调" in body or "勿" in body
    assert "也可直接开辩" in body or "意图模糊" in body
    # 超笼统调研输入仍先 ask 确认再挂 playbook
    assert "ask_user" in body
    assert "确认" in body and ("启动" in body or "多视角" in body)
    # 命题保真：收卡呈报前校验；延伸辩题不得替换主命题
    assert "命题保真" in body
    assert "延伸辩题" in body
    assert "替换主命题" in body or "替换" in body
    # 批 D+：透镜检索分工——首透镜查全公共底料，其余盯独有缺口（并行静态分工）
    assert "检索分工" in body
    assert "首个透镜" in body or "首透镜" in body
    assert "简要确认" in body
    assert "独有" in body
    assert "并行" in body
    # 检索额度统一默认（不再半格差异化）
    assert "统一默认" in body or "同额" in body
    assert "半格" not in body
    assert "略高" not in body
    assert "差异化" not in body
    for mark in ("①", "②", "③", "④"):
        assert mark not in body
    assert "正文分流前置" not in body
    assert "顺序铁律" not in body
    assert "2–3 个核心词" not in body


def test_deep_multi_lens_research_summary_intent_routing():
    """目录摘要只写这是什么；入口分流长文在 body。"""
    deep = build_system_skill_registry().get("deep_multi_lens_research")
    assert deep is not None
    summary = deep.summary
    assert "多维公共事件" in summary
    assert "调研" in summary or "研究" in summary
    assert "debate_and_review" not in summary
    assert "抢拦" not in summary
    assert "模拟法庭" not in summary
    assert "庭审对抗" not in summary
    assert "对簿公堂" not in summary
    # 长分流教法在 body
    assert "入口分流" in deep.body
    assert "勿抢拦" in deep.body
    assert "debate_and_review" in deep.body
    assert "模拟法庭" in deep.body or "庭审对抗" in deep.body or "对簿公堂" in deep.body
    assert "同句点名终局对抗仍先取证" not in summary


def test_named_debate_routes_to_debate_not_mlr():
    """点名开辩 / 模拟庭审 / 终局对抗 → debate_and_review 直调 debate，勿 MLR 抢拦。"""
    debate = build_system_skill_registry().get("debate_and_review")
    deep = build_system_skill_registry().get("deep_multi_lens_research")
    assert debate is not None and deep is not None
    assert "入口分流" in debate.body or "按意图" in debate.body
    assert "直调" in debate.body and "debate" in debate.body
    assert "庭前取证" in debate.body or "辩论机制" in debate.body
    from agentcore.runtime.skills.deep_multi_lens_research import MULTI_LENS_COURTROOM_TRIGGERS

    for t in MULTI_LENS_COURTROOM_TRIGGERS:
        assert t in debate.body
    # summary 只写这是什么，不复述核心长分流
    assert "deep_multi_lens_research" not in debate.summary
    assert "debate_and_review" not in deep.summary
    assert "抢拦" not in deep.summary
    assert "勿抢拦" in deep.body
    assert "debate_and_review" in deep.body
    assert "同句点名终局对抗仍先取证" not in deep.summary
    assert "同句点名" not in deep.body or "仍【先】" not in deep.body


def test_research_intent_routes_to_mlr():
    """调研 / 研究意图 → deep_multi_lens_research（body 钉分流；summary 只写名字）。"""
    debate = build_system_skill_registry().get("debate_and_review")
    deep = build_system_skill_registry().get("deep_multi_lens_research")
    assert debate is not None and deep is not None
    for text in (debate.body, deep.body):
        assert "调研" in text or "研究" in text
    assert "调研" in deep.summary
    assert "deep_multi_lens_research" not in debate.summary
    assert "deep_multi_lens_research" in debate.body
    assert "平行取证" in deep.summary or "平行取证" in deep.body
    assert "命题卡" in deep.summary or "motion_card" in deep.body
    # 模糊意图：保守走 MLR + 提示也可直接开辩
    assert "意图模糊" in debate.summary or "意图模糊" in debate.body
    assert "也可直接开辩" in debate.summary or "也可直接开辩" in debate.body
    assert "也可直接开辩" in deep.summary or "也可直接开辩" in deep.body


def test_deep_multi_lens_research_summary_forbids_ceo_solo_search():
    """防 CEO 自搜替代四路：细则在 body，不进目录行。"""
    deep = build_system_skill_registry().get("deep_multi_lens_research")
    assert deep is not None
    assert "自搜" in deep.body or "禁止" in deep.body
    assert "playbook" in deep.body


def test_debate_and_review_summary_intent_routes_research_to_mlr():
    """目录只写这是什么；调研归 MLR 的分流长文在 body。"""
    debate = build_system_skill_registry().get("debate_and_review")
    assert debate is not None
    summary = debate.summary
    assert "辩论" in summary
    assert "deep_multi_lens_research" not in summary
    assert "入口分流" in debate.body or "按意图" in debate.body
    assert "直调" in debate.body and "debate" in debate.body
    # 旧「除外·公共事件先走 MLR（含模拟法庭终局）」口径已退役
    assert "除外" not in summary or "模拟法庭类终局诉求" not in summary


def test_legal_case_analysis_summary_excludes_public_mock_court():
    """目录只写这是什么；除外口径钉 body；摘要与 MLR「多维公共事件」互斥。"""
    legal_reg = build_system_skill_registry(include_legal=True)
    case = legal_reg.get("legal_case_analysis")
    deep = build_system_skill_registry().get("deep_multi_lens_research")
    assert case is not None and deep is not None
    assert "接案" in case.summary or "诉讼策略" in case.summary
    assert "多维公共事件" not in case.summary
    assert "多维公共事件" in deep.summary
    assert "除外" not in case.summary
    assert "先对抗后研判" not in case.summary
    assert "deep_multi_lens_research" not in case.summary
    body = case.body
    assert "除外" in body
    assert "模拟法庭" in body
    assert "多维取证" in body
    assert "deep_multi_lens_research" in body
    assert "先对抗后研判" in body


def test_legal_case_analysis_body_redirects_public_mock_court_to_mlr():
    """正文须与目录除外同口径：consult 后仍能把公共模拟法庭打回 MLR。"""
    legal_reg = build_system_skill_registry(include_legal=True)
    body = legal_reg.get("legal_case_analysis").body
    assert "模拟法庭" in body
    assert "deep_multi_lens_research" in body
    assert "lens_crosscheck" in body
    assert "停止" in body or "勿用本 skill" in body or "不走本 skill" in body


def test_deep_multi_lens_and_legal_summaries_are_mutually_exclusive():
    """目录触发分流：legal 系 vs 多维公共事件——互斥动词域，避免商标案抢触发。"""
    deep = build_system_skill_registry().get("deep_multi_lens_research")
    legal_reg = build_system_skill_registry(include_legal=True)
    case_skill = legal_reg.get("legal_case_analysis")
    brief_skill = legal_reg.get("legal_answer_brief")
    legal_summaries = [case_skill.summary, brief_skill.summary]
    # WHEN 互斥：多维公共事件归 MLR；步骤字面在 body，不进目录行。
    assert "多维公共事件" in deep.summary
    for ls in legal_summaries:
        assert "多维公共事件" not in ls
    for marker in ("平行取证", "命题卡"):
        assert marker in deep.body, marker
        for ls in legal_summaries:
            assert marker not in ls, (marker, ls)
    assert "接案" in case_skill.summary or "诉讼策略" in case_skill.summary
    assert "答辩状" in brief_skill.summary
    assert "先对抗后研判" in case_skill.body
    assert "red_team" in brief_skill.body
    for marker in ("律师作业", "接案", "诉讼策略", "先对抗后研判"):
        assert marker not in deep.summary, marker
