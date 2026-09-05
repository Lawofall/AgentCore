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
# run skill is gated on the run tool (CEO+worker HOW).
# data_file_landing ride consult audience (worker loop vs CEO 派工);
# long_form_landing is worker-only landing HOW.
# team_orchestration_advanced 是主管手册（audience=ceo）。
# lead_subteam 是嵌套 lead 手册（audience=worker + requires_tools=delegate）。
_FULL_TOOLS = {"delegate", "ask_user", "debate", "run"}
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
        "lead_subteam",
        "team_cross_folder",
        "team_delivery_env",
        "team_local_desk",
        "product_help",
        "debate_and_review",
        "asking_the_user",
        "run",
        "long_form_landing",
        "data_file_landing",
    }
    assert "build_toolshed" not in names
    assert "product_bug_triage" not in names
    assert "deep_multi_lens_research" not in names


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
    # asking_the_user needs the ask_user tool. On the autonomous (no live user)
    # path it is not wired, so it drops out of the catalog. data_file_landing is
    # ungated (CEO still consults to brief) — not tied to delegate.
    reg = build_system_skill_registry()
    available = {s.name for s in reg.available(_NO_LIVE_USER)}
    assert "team_orchestration_advanced" in available
    assert "product_help" in available  # requires_tools=() — always listed
    assert "product_bug_triage" not in available
    assert "deep_multi_lens_research" not in available
    assert "build_website" not in available
    assert "debate_and_review" in available
    assert "verify_and_fix" not in available
    assert "data_file_landing" in available
    assert "work_discipline" not in available
    assert "asking_the_user" not in available
    assert "ask_user_kickoff" not in available
    assert "ask_user_midtask" not in available
    assert "run" not in available


def test_available_shows_gated_skills_when_tools_wired():
    reg = build_system_skill_registry()
    available = {s.name for s in reg.available(_FULL_TOOLS)}
    assert "asking_the_user" in available
    assert "ask_user_kickoff" not in available
    assert "ask_user_midtask" not in available
    assert "verify_and_fix" not in available
    assert "run" in available


def test_available_audience_hides_ceo_only_from_workers():
    """A：队员目录拿掉主管手册；不按任务猜。requires_tools 轴仍独立。"""
    reg = build_system_skill_registry()
    worker = {s.name for s in reg.available(set(), audience="worker")}
    assert "product_help" not in worker
    assert "product_bug_triage" not in worker
    assert "team_orchestration_advanced" not in worker
    assert "team_cross_folder" not in worker
    assert "team_delivery_env" not in worker
    assert "team_local_desk" not in worker
    assert "build_website" not in worker
    assert "deep_multi_lens_research" not in worker
    assert "work_discipline" not in worker
    assert "long_form_landing" in worker
    assert "verify_and_fix" not in worker
    assert "data_file_landing" in worker
    assert "run" not in worker
    assert "lead_subteam" not in worker
    lead = {s.name for s in reg.available({"delegate"}, audience="worker")}
    assert lead == worker | {"lead_subteam"}
    ceo = {s.name for s in reg.available(_FULL_TOOLS, audience="ceo")}
    assert "product_help" in ceo
    assert "team_orchestration_advanced" in ceo
    assert "lead_subteam" not in ceo
    assert "team_cross_folder" in ceo
    assert "team_delivery_env" in ceo
    assert "team_local_desk" in ceo
    assert "verify_and_fix" not in ceo
    assert "data_file_landing" in ceo
    assert "run" in ceo
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
    assert "team_orchestration_advanced" not in names
    assert "team_cross_folder" not in names
    assert "team_delivery_env" not in names
    assert "team_local_desk" not in names
    assert "long_form_landing" in names
    assert "verify_and_fix" not in names
    assert "data_file_landing" in names
    assert "run" not in names
    assert "lead_subteam" not in names
    assert await source.fetch_by_name("u", "product_help") is None
    assert await source.fetch_by_name("u", "team_orchestration_advanced") is None
    assert await source.fetch_by_name("u", "lead_subteam") is None
    assert await source.fetch_by_name("u", "team_cross_folder") is None
    assert await source.fetch_by_name("u", "team_delivery_env") is None
    assert await source.fetch_by_name("u", "team_local_desk") is None
    assert await source.fetch_by_name("u", "long_form_landing") is not None
    assert await source.fetch_by_name("u", "data_file_landing") is not None


async def test_nested_lead_consult_source_fetches_lead_subteam():
    """持 delegate 的队员：目录与 fetch 都能拿到子队拆法；仍拿不到 CEO 编制本。"""
    source = SkillConsultSource(
        registry=build_system_skill_registry(),
        tool_names={"delegate"},
        audience="worker",
    )
    names = {e.name for e in await source.list_directory("u")}
    assert "lead_subteam" in names
    assert "team_orchestration_advanced" not in names
    assert "team_delivery_env" not in names
    assert "team_local_desk" not in names
    body = await source.fetch_by_name("u", "lead_subteam")
    assert body is not None
    assert "阻塞" in body
    assert "立即返回" not in body


def test_lead_subteam_is_not_ceo_coordination():
    from agentcore.runtime.skills import _LEAD_SUBTEAM

    body = _LEAD_SUBTEAM
    assert "凡大活必嵌套" in body
    assert "阻塞" in body
    assert "优先先" in body and "delegate" in body
    assert "立即返回" not in body
    assert "可静默" not in body
    assert "ask_user" not in body
    assert "team_orchestration_advanced" not in body
    assert "探路" not in body


async def test_expand_skill_tool_names_unlocks_lead_subteam():
    from agentcore.runtime.context.consult_sources import (
        MergedConsultSource,
        SkillConsultSource,
        expand_skill_tool_names,
    )

    leaf = MergedConsultSource(
        skill=SkillConsultSource(
            registry=build_system_skill_registry(),
            tool_names=set(),
            audience="worker",
        )
    )
    assert await leaf.fetch_by_name("u", "lead_subteam") is None
    lead = expand_skill_tool_names(leaf, {"delegate"})
    assert lead is not leaf
    assert await lead.fetch_by_name("u", "lead_subteam") is not None
    assert await leaf.fetch_by_name("u", "lead_subteam") is None


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
    assert "product_help" in names
    assert "team_orchestration_advanced" in names
    assert "lead_subteam" not in names
    assert "data_file_landing" in names
    assert "run" in names
    assert "long_form_landing" not in names
    assert await source.fetch_by_name("u", "product_help") is not None
    assert await source.fetch_by_name("u", "team_orchestration_advanced") is not None
    assert await source.fetch_by_name("u", "lead_subteam") is None
    assert await source.fetch_by_name("u", "data_file_landing") is not None
    assert await source.fetch_by_name("u", "long_form_landing") is None


# --- directory rendering -----------------------------------------------------


def test_directory_lists_only_available_skills_with_names_and_summaries():
    reg = build_system_skill_registry()
    out = render_skill_directory(reg, _FULL_TOOLS)
    assert "<按需目录>" in out and "</按需目录>" in out
    assert "consult" in out  # the soft push to pull a skill
    for skill in reg.available(_FULL_TOOLS):
        assert skill.name in out
        assert skill.summary in out


def test_system_skill_summaries_are_short_when_triggers():
    """目录行只写这是什么；Python len ≤80（对照 run 一句名字）。"""
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
    assert "- product_bug_triage：" not in out
    assert "- product_help_map：" not in out
    assert "- product_help_faq：" not in out
    reg = build_system_skill_registry()
    help_sum = reg.get("product_help").summary
    help_body = reg.get("product_help").body
    assert help_sum == "本产品用法"
    assert "官网" not in help_sum
    assert "下载" not in help_sum
    assert "官网" in help_body
    assert "product_help_map" not in help_sum
    assert "product_help_faq" not in help_sum
    assert "这是什么项目" not in help_sum
    assert "这是什么项目" in help_body
    assert "你是什么" in help_body
    assert "你的网站" in help_body
    assert "Cursor" in help_body
    assert ".mdc" in help_body
    assert "改成 AgentCore 规则" in help_body
    assert "设置 → 反馈" in help_body
    assert "看不到服务端日志" in help_body
    assert reg.get("product_bug_triage") is None
    assert "出问题必查" not in out


def test_greenfield_recommends_handwritten_software_not_hard_forbid_none():
    """做软件专段已撤；目录只留名字；核不写形态禁令。"""
    from agentcore.runtime.resolve.prompt import _CEO_CORE_HINT

    out = render_skill_directory(build_system_skill_registry(), _FULL_TOOLS)
    hint = _CEO_CORE_HINT
    assert "做软件手写" not in hint
    assert "必须 build_app" not in hint
    assert "禁 none 手糊" not in hint
    assert "consult(building_software)" not in hint
    assert "尚无工程清单" not in hint
    assert "一块模块" not in hint
    orch = build_system_skill_registry().get("team_orchestration_advanced")
    assert orch is not None
    assert "【做软件】" not in orch.body
    assert "假两段" not in orch.body
    assert "形态跟桌上结果" not in orch.body
    assert "桌上结果是什么就派什么" not in orch.body
    assert "薄旁路" not in orch.body
    assert "尚无工程清单" not in orch.body
    assert "范围没钉" not in orch.body
    assert "轻切片" not in orch.body
    assert "先 MVP" not in orch.body
    assert "真两段" not in orch.body
    assert "- team_orchestration_advanced：" in out
    assert "薄旁路" not in orch.summary
    assert 'playbook="build_app"' not in orch.body


def test_directory_omits_gated_skills_on_autonomous_path():
    reg = build_system_skill_registry()
    out = render_skill_directory(reg, _NO_LIVE_USER)
    assert "asking_the_user" not in out
    assert "ask_user_kickoff" not in out
    assert "ask_user_midtask" not in out
    # The delegate-gated + non-gated advanced skills are still offered.
    assert "team_orchestration_advanced" in out
    assert "verify_and_fix" not in out


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
    """验收：consult('product_help') 命中；目录只列用法 + 故障，废名缺席。"""
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
    assert "- product_bug_triage：" not in directory
    assert "- product_help_map：" not in directory
    assert "- product_help_faq：" not in directory
    assert skill.summary in directory
    assert reg.get("product_help_map") is None
    assert reg.get("product_help_faq") is None
    assert reg.get("product_bug_triage") is None
    miss_triage = await tool.execute({"name": "product_bug_triage"}, _ctx())
    assert miss_triage.success
    assert "没有名为" in miss_triage.output
    miss = await tool.execute({"name": "product_help_map"}, _ctx())
    assert miss.success
    assert "没有名为" in miss.output


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
    "verify_and_fix",
    "work_discipline",
    "consumer_deps",
    "design_impl_same_grant",
    "root_slice_honesty",
    "先一条主路径",
    "一次做完再拆",
    "默认切成 MVP",
    "完整可玩 N 屏",
    "deep_multi_lens_research",
    "product_bug_triage",
)

# Playbook ids are identifiers: ``multi_lens_research`` must not fire as a live name.
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
    """现行通道不留废字段名 / 禁复活编年；playbook↔tasks 二选一在 schema。"""
    from agentcore.tools.builtin.delegate.schema import DELEGATE_PARAMETERS

    playbook_desc = DELEGATE_PARAMETERS["properties"]["playbook"]["description"]
    assert "二选一" in playbook_desc
    for skill in build_system_skill_registry().list_all():
        blob = f"{skill.summary}\n{skill.body}"
        for token in _RETIRED_SKILL_LITERALS:
            assert not _retired_token_in(blob, token), (skill.name, token)


def test_team_orchestration_skill_teaches_staffing_constitution():
    """对比边界：按结构组队 / 探路停手 / 审≠写；废形状词表与做软件专段缺席。"""
    body = _body("team_orchestration_advanced")
    for term in (
        "并列对象分组",
        "角度扇出",
        "证据驱动流水线",
        "契约共享面",
        "独立多透镜诊断",
        "部件一致性对账",
        "对抗辩论",
        "发散挑选",
        "形状词汇",
        "默认中档",
        "保底编制",
        "教学示例形状",
        "对照学形状",
        "默认 A",
        "真两段",
        "1 人两段",
        "根委派切片诚实",
        "尚无工程清单",
        "云端引擎员",
        "本地引擎员",
        "假两段",
        "【做软件】",
        "先设计再实现",
        "局部单功能",
        "形态跟桌上结果",
        "桌上结果是什么就派什么",
    ):
        assert term not in body, term
    assert "真冲突才开局部辩" not in body
    assert "结论真冲突" not in body
    assert "人数不是优化目标" in body
    assert "活本身是一块" in body
    assert "单 lead" in body and "二选一" in body
    assert "探路停手" in body
    assert "定位入口" in body
    assert "成品文件只装成品" in body
    assert "专业方案归专家" in body
    assert "同一套原文" in body
    assert "form=prose" in body
    assert "审查者" in body and "作者" in body
    assert "只报告" in body
    assert "consult(asking_the_user)" in body
    assert "consult(team_cross_folder)" in body
    assert "consult(team_delivery_env)" in body
    assert "不硬拒" not in body
    assert "范围没钉" not in body
    assert 'playbook="build_app"' not in body
    assert "playbook=none" not in body
    assert "playbook_id" not in body
    assert "静默自拟" not in body
    assert "不得 continue 派工" not in body
    assert "怎么算修好" not in body
    assert "同一人验" not in body
    assert "已有代码上一个缺口" not in body
    assert "diagnose_fix_verify" not in body
    assert "browser" not in body
    assert "白屏" not in body
    run_body = _body("run")
    assert "tsc" in run_body or "pytest" in run_body
    for token in ("complexity_hint", "result_handling", "require_upstream"):
        assert token not in body, token
    assert "模板路" not in body and "手写路" not in body
    assert "playbook=code_audit" not in body
    assert "code_audit" not in body
    assert "上限 8" not in body and "上限8" not in body
    assert "主管速览" not in body
    assert "凡审计必两拨人" not in body
    assert "〇、人审速览" not in body
    assert "不扫 role/task" not in body
    from agentcore.runtime.runs.playbooks import PLAYBOOKS

    cite = PLAYBOOKS["cite_write_review"].summary
    assert "可提交长文" in cite and "点名审校" in cite
    assert "学术审校" in cite
    assert "map_fanout" in PLAYBOOKS


def test_team_orchestration_skill_teaches_opening_and_writing_without_lettered_tree():
    """闲聊 / 不必派在 delegate description；探路停手 + 成品只装成品；不设档位编号树。"""
    from agentcore.runtime.runs.playbooks import PLAYBOOKS
    from agentcore.tools.builtin.delegate.schema import DELEGATE_DESCRIPTION

    body = _body("team_orchestration_advanced")
    assert "讨论类开场" not in body
    assert "先对话对齐" not in body
    assert "写成文档并保存" not in body
    assert "暂不派队" not in body
    assert "默认 A" not in body
    assert "成文后梯度" not in body
    assert "轻→标准→重" not in body
    assert "档 1" not in body
    assert "档 2" not in body
    assert "档 3" not in body
    assert "禁成文产线" not in body
    assert "明示成文不拦" not in body
    assert "自动多人" not in body
    assert "闲聊" in DELEGATE_DESCRIPTION and "不必派" in DELEGATE_DESCRIPTION
    assert "闲聊自己回" not in body
    assert "干活默认派" not in body
    assert "consumer_deps" not in body
    assert "design_impl_same_grant" not in body
    assert "root_slice_honesty" not in body
    assert "成品文件只装成品" in body
    assert "deep_multi_lens_research" not in body
    assert "点名开辩" in body or "debate" in body
    assert "0～1" not in body
    assert "定位入口" in body
    assert "探路停手" in body
    assert "了解到什么算够" in body
    assert "够用即停" not in body
    assert "一页地图" not in body
    assert "一句目标" not in body
    assert "开局先招人" not in body
    assert "凑台账" not in body
    assert "定优化方案" not in body
    cite = PLAYBOOKS["cite_write_review"].summary
    assert "可提交长文" in cite
    assert "学术审校" in cite
    fanout = PLAYBOOKS["map_fanout"].summary
    assert "handoff" in fanout


def test_work_discipline_skill_is_gone():
    """仓内贡献纪律不是产品 AI skill；.bat HOW 在 run；已确认约束在 task 参数。"""
    from agentcore.tools.builtin.delegate.schema import DELEGATE_PARAMETERS

    assert build_system_skill_registry().get("work_discipline") is None
    task_desc = DELEGATE_PARAMETERS["properties"]["tasks"]["items"]["properties"]["task"][
        "description"
    ]
    assert "已确认约束" in task_desc
    assert "自拟默认" in task_desc or "自拟" in task_desc


def test_product_help_skill_teaches_short_answers_and_manual_deeplinks():
    """用法 + 入口 + FAQ 同一 body；答法按节取，不拆目录名。"""
    help_body = _body("product_help")

    assert "短答" in help_body
    assert "禁内部名" in help_body
    # 官网域名只在 skill（核不常驻 URL）；三条各一次
    assert "https://fashitianxia.xyz" in help_body
    assert "https://fashitianxia.xyz/download" in help_body
    assert "https://app.fashitianxia.xyz" in help_body
    assert help_body.count("https://fashitianxia.xyz/download") == 1
    assert help_body.count("https://app.fashitianxia.xyz") == 1
    assert "同名他品" not in help_body
    assert "只用下方【官网 / 下载】域名" in help_body or "域名只许用这三条" in help_body
    assert "ask_user" in help_body  # teach: don't say these to users
    assert "功能总览" in help_body and "≤3" in help_body
    assert "product_help_map" not in help_body
    assert "product_help_faq" not in help_body
    assert "【入口地图】" in help_body
    assert "【FAQ】" in help_body
    assert "一人答更快就直接干" in help_body
    assert "Multi-Agent" in help_body or "工作台" in help_body
    assert "RAG" in help_body  # forbid
    assert "playbook=" not in help_body
    assert "SSE" in help_body  # ban list only

    assert "【产品面地图·高频入口】" in help_body
    assert "唯一对话入口" in help_body
    assert "怎么用本产品" in help_body
    assert "模型与偏好等" in help_body
    assert "产物与「完整预览」" not in help_body
    assert "HTML「完整预览」（仅桌面）" in help_body or "仅桌面" in help_body
    assert "完整预览" in help_body
    assert "关键拍板与正反交锋入口" in help_body
    assert "#/toolbox/manual/" in help_body
    assert "手机" in help_body and "勿承诺" in help_body
    assert "页名也按端写" in help_body
    assert "我的 → 服务商" in help_body
    assert "我的 → 模型" in help_body
    assert "我的 → 用量" in help_body
    assert "窄屏不上工具箱" in help_body
    assert "?s=workspace" in help_body or "workspace" in help_body
    # .md 阅读预览（文件面板）≠ HTML「完整预览」（右坞）
    assert "阅读预览" in help_body
    assert "文件」面板" in help_body or "文件面板" in help_body
    assert "不是一路" in help_body

    assert "为什么没组团" in help_body
    assert "?s=faq" in help_body
    assert ".md" in help_body and "阅读预览" in help_body
    assert "Markdown 语法" in help_body or ".md 怎么打开" in help_body
    # 文件夹：入口条 + 删除语义同一 body；保留期跟随服务端设置，勿手写漂移
    assert "删除文件夹…" in help_body and "⋯" in help_body
    assert "删文件夹会怎样？" in help_body
    assert "一并归档" in help_body and "已归档" in help_body
    assert f"约 {settings.workspace_retention_days} 天后由系统自动清理" in help_body
    assert "立即永久清除" in help_body and "不可恢复" in help_body
    assert "这张桌的 AI 设定退出注入" in help_body
    assert "这张桌的设定一起带回来" in help_body
    # 本机磁盘不受影响（线上 trace 曾编造「删本地项目会动本机目录」）
    assert "两种删法都不动你电脑上的文件" in help_body
    # Cursor 规则对照只写一次
    assert (
        help_body.count("Cursor `.cursor/rules` / `.mdc` ≠ AgentCore 用户规则") == 1
    )
    assert "AgentCore 用户规则 = `AgentCore/规则/` + `remember`" in help_body
    assert "`skills/*.json` = 技能/能力包" in help_body
    assert "不是" in help_body and "平台规则" in help_body
    assert "未钉死目标载体前禁止默认迁成 skill JSON" in help_body
    assert "至多一次窄 list `.cursor/rules`" in help_body
    assert "禁多轮 list / 通读 `.mdc` 再问" in help_body
    assert "改成 AgentCore 规则" in help_body
    # summary 只写这是什么。身份问自己答，不在摘要里必查。
    help_skill = build_system_skill_registry().get("product_help")
    assert help_skill is not None
    assert "这是什么项目" not in help_skill.summary
    assert "你是什么" not in help_skill.summary
    assert "官网" not in help_skill.summary
    assert "官网" in help_body
    assert "product_help_map" not in help_skill.summary
    assert "product_help_faq" not in help_skill.summary
    assert build_system_skill_registry().get("product_help_map") is None
    assert build_system_skill_registry().get("product_help_faq") is None

    # 上报入口是产品事实，跟用法同一 WHEN；不另立排查 skill
    assert "product_bug_triage" not in help_body
    assert "【L1" not in help_body
    assert "【L2" not in help_body
    assert "`product_bug`" not in help_body and "`model_limit`" not in help_body
    assert "设置 → 反馈" in help_body
    assert "看不到服务端日志" in help_body


def test_product_help_teaches_identity_first_then_other_topics():
    """身份问：可见正文先一句话答我方产品；他品落地禁令缺席。"""
    help_body = _body("product_help")
    assert "这是什么项目" in help_body
    assert "你是什么" in help_body
    assert "首句" in help_body
    assert "consult 不能代替作答" in help_body
    assert "同名他品" not in help_body
    assert "第三方 Skill" not in help_body
    assert "用户气泡空着" not in help_body
    assert "写成工作区规则" not in help_body
    # 08-15 官网钉：本条不改坏
    assert "https://fashitianxia.xyz" in help_body
    assert "https://fashitianxia.xyz/download" in help_body


def test_product_bug_triage_skill_absent_from_mass_catalog():
    """排查仪式不进大众目录；上报入口是 product_help 产品事实。"""
    reg = build_system_skill_registry()
    assert reg.get("product_bug_triage") is None
    help_body = _body("product_help")
    assert "设置 → 反馈" in help_body
    assert "看不到服务端日志" in help_body
    assert "手机没有应用内反馈入口" in help_body
    assert "【L1" not in help_body
    assert "ExecEnvProbeFailed" not in help_body
    assert "product_bug_triage" not in render_skill_directory(reg, _NO_LIVE_USER)


def test_product_help_does_not_host_diagnostic_ritual():
    """用法 skill 不承载四类归因 / 复现包；诚实对照结构面。"""
    body = _body("product_help")
    assert "四类结论" not in body
    assert "复现要点" not in body
    assert "dogfood" not in body
    assert "扫长文" not in body
    assert "勿假装读了" in body or "看不到服务端日志" in body


def test_team_cross_folder_skill_teaches_parallel_command():
    """对照句：派工换桌 / 认桌≠摸底 / 挂载≠换桌。指认·过闸·空壳百科不回潮。"""
    body = _body("team_cross_folder")
    orch = _body("team_orchestration_advanced")
    desk = _body("team_local_desk")
    assert "target_folder_id" in body
    assert "delegate" in body
    assert "只读摸底" in body and "改盘" in body
    assert "云端草稿" in body and "读不到" in body
    assert "list_folder_dir" in body and "read_folder_file" in body
    assert "认桌" in body and "摸底" in body
    assert "挂载" in body and "换桌" in body
    assert "consult(team_local_desk)" in body
    assert "list_folders" not in body
    assert "resolve_folder" not in body
    assert "ask_user" not in body
    assert "create_folder" not in body
    assert "mkdir" not in body
    assert "文件：空" not in body
    assert "先建后派" not in body
    assert "猜最近" not in body
    assert "导入到云" not in body
    assert "bind_local_folder" not in body
    assert "open_local_project" not in body
    assert "external_mount_readonly" not in body
    assert "拒后禁塌缩" not in body
    assert "bare_chat_no_target" not in body
    assert "写仍派工换桌" not in body
    assert "只读跨桌摸底" not in body
    assert "【跨文件夹并行指挥】" not in body
    skill = build_system_skill_registry().get("team_cross_folder")
    assert skill is not None
    assert skill.summary == "跨文件夹"
    assert "target_folder_id" not in orch
    assert "consult(team_cross_folder)" in orch
    assert "consult(team_cross_folder)" in desk
    for n in range(1, 11):
        assert f"{n}. **" not in body, n


def test_team_orchestration_skill_teaches_review_narrowing():
    """审查：默认 prose、只报告默认 1 人；废「收窄专段 / 配额」字面。"""
    body = _body("team_orchestration_advanced")
    assert "form=prose" in body
    assert "只报告" in body and "1 人" in body
    assert "冷启动建档除外" not in body
    assert "并列不打架" not in body
    assert "冷启动建档「≥2" not in body


def test_team_delivery_env_skill_teaches_empty_desk_no_project_shell():
    body = _body("team_delivery_env")
    desk = _body("team_local_desk")
    orch = _body("team_orchestration_advanced")
    from agentcore.tools.builtin.file_ops.meta import MkdirTool

    desc = MkdirTool().schema.description
    assert "【空桌勿套工程壳】" not in body
    assert "工程壳" not in body
    assert "本文件夹根即工作区根" not in body
    assert "create_folder" in desk and "桌内工程根" in desk
    assert "mkdir" in desk
    assert "court-game/" not in desk
    assert "要不要再套一层" not in desk
    assert "结构目录" in desc
    assert "src/" in desc
    assert "套应用名/话题名当工程根" in desc
    assert "whiteboard" not in desc
    assert "【空桌勿套工程壳】" not in orch
    assert "空桌工程根" not in orch
    assert "【本机进桌" not in body
    assert "<本机进桌>" in desk
    assert "【进桌】" in desk
    assert "可**推荐** Composer" not in desk
    assert "consult(team_delivery_env)" in orch
    assert "consult(team_local_desk)" in orch


def test_team_orchestration_skill_teaches_delegate_knobs():
    """图语义 / 填参在 schema；skill 只留 DAG vs lead 对比。废字段不回潮。"""
    from agentcore.runtime.resolve.prompt import _CEO_CORE_HINT
    from agentcore.tools.builtin.delegate.schema import DELEGATE_PARAMETERS

    body = _body("team_orchestration_advanced")
    assert "coordinate=false" not in body
    assert "轻量直出" not in body
    assert "单人直出" not in body
    assert "finalize" not in body
    hint = _CEO_CORE_HINT
    assert "finalize" not in hint
    assert "讨论/判断默认自己答不必查" not in hint
    orch_sum = build_system_skill_registry().get("team_orchestration_advanced").summary
    assert orch_sum == "团队拆法"
    delivery_sum = build_system_skill_registry().get("team_delivery_env").summary
    assert delivery_sum == "交付环境"
    desk_sum = build_system_skill_registry().get("team_local_desk").summary
    assert desk_sum == "本机进桌"
    deps = DELEGATE_PARAMETERS["properties"]["tasks"]["items"]["properties"]["depends_on"][
        "description"
    ]
    assert "生产者→消费者" in deps
    assert "新开一队" in deps
    append = DELEGATE_PARAMETERS["properties"]["append_to_execution_id"]["description"]
    assert "latest" in append
    assert "正例·串行" not in body and "反例·勿串" not in body
    assert "并行写盘" not in body
    assert "sibling_artifact" not in body
    assert "假两段" not in body
    assert "单 lead" in body and "二选一" in body
    assert "再平铺" in body
    assert "人已派出" not in body
    assert "谁还在跑" not in body
    assert "谁在后台、完成后会再汇报" not in body
    assert "【跨回合延续】" not in body
    assert "向用户汇报用" not in body
    assert "已往上方协作图追加" not in body
    assert "`desktop_notify`" not in body
    assert "worker 可用 `desktop_notify`" not in body
    assert "CEO 不可" not in body
    assert "协调预算" not in body
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
    """认知分工在 skill；提纲停靠 playbook slots；废字段 / 事故名不进编排手册。"""
    from agentcore.runtime.runs.playbooks import PLAYBOOKS

    body = _body("team_orchestration_advanced")
    assert "专业方案归专家" in body
    assert "同一套原文" in body
    assert "required_sections" not in body
    for token in (
        "checkpoint_after",
        "bind_after_deps",
        'coordination="wall"',
        "complexity_hint",
        "result_handling",
        "require_upstream",
    ):
        assert token not in body, token
    assert "consult(team_delivery_env)" in body
    assert "md_to_pdf" not in body
    assert "reportlab" not in body
    assert "Marp" not in body
    slots = PLAYBOOKS["cite_write_review"].slots
    assert "checkpoint" in slots
    assert "明文" in slots or "提纲" in slots


def test_team_delivery_env_skill_teaches_presentation_pptx_honesty():
    """点名后缀只认开场表；不可产不空派；等效替代先干。库名 / 说满空派不进本条。"""
    body = _body("team_delivery_env")
    orch = _body("team_orchestration_advanced")
    assert "开场表" in body
    assert "静默" in body and ".md" in body
    assert "交付缺口" in body or "标缺口" in body
    assert "form=files" in body or "artifacts" in body
    assert "file_copy" in body
    assert "当模板" in body
    assert "Presentation()" not in body
    assert "python-pptx" not in body
    assert "再派" in body and "跑脚本" in body
    assert "consult(data_file_landing)" in body
    assert "结构报告" not in body
    assert "待跑变换脚本" not in body
    assert "暂时不可用" not in body
    assert "表格 → `.csv`" not in body
    assert "压体积" not in body
    assert "模板保真" not in body
    assert "*_slim.pptx" not in body and "slim.pptx" not in body
    assert "相对模板" not in body
    assert "图形组织图" in body
    assert "直接拒" in body
    assert "文本" in body and "表格" in body
    assert "说满" not in body
    assert "SmartArt" not in body and "DrawingML" not in body
    assert "凭印象" in body
    assert "先干" in body
    assert "点名载体/手段" in body
    assert "顾问短对齐" not in body
    assert "源数据文件下一步" not in body
    assert "无法可靠解析的源数据文件" not in body
    assert "无法可靠解析的源数据文件" in _body("data_file_landing")
    assert "表质量基线" in body
    assert "冒充表结构" in body
    assert "交付对账" in body
    assert "python-pptx" not in orch


def test_team_delivery_env_skill_teaches_deterministic_word_pdf_export():
    """`.docx`/`.pdf` 走 md_to_docx / md_to_pdf，与执行沙箱正交；无执行缺口只覆盖 pptx/xlsx。"""
    body = _body("team_delivery_env")
    orch = _body("team_orchestration_advanced")
    assert "md_to_docx" in body
    assert "python-docx" not in body
    assert "不可产" in body
    assert "与执行正交" in body
    assert "确定性导出器" in body
    assert "`.docx`/`.pptx`/`.xlsx` 等且能力行" not in body
    assert "与执行正交" not in orch


def test_run_skill_teaches_windows_bat_crlf_ascii():
    """Windows .bat HOW 在 run；编排 skill 只留派工钩。"""
    run = _body("run")
    assert ".bat" in run
    assert "CRLF" in run
    assert "ASCII" in run
    assert ".ps1" in run
    assert "转码" in run or "改换行" in run
    delivery = _body("team_delivery_env")
    assert "consult(run)" not in delivery
    assert ".bat" not in delivery
    assert "work_discipline" not in delivery
    assert "双击即用" not in delivery
    assert "ASCII-only" not in delivery
    orch = _body("team_orchestration_advanced")
    assert "双击即用" not in orch


def test_team_delivery_env_skill_teaches_image_gen_key_boundary():
    """出图边界在 run skill；凭据卫生在基座；交付手册不抄。"""
    body = _body("team_delivery_env")
    run = _body("run")
    orch = _body("team_orchestration_advanced")
    assert "生图" not in body
    assert "代调" not in body
    assert "无原生生图" in run
    assert "明文" not in body and "跨会话凭据脱敏" not in body
    from agentcore.runtime.resolve.prompt import assemble_system_prompt

    shared = assemble_system_prompt()
    assert "<凭据卫生>" not in shared
    assert "<工作权威>" in shared
    assert "密钥" in shared and "明文" in shared
    assert "已识别凭据" in shared
    assert "面板可见·落盘对账" not in body
    assert "交付下载·面板路径" not in body
    assert "404" not in body
    assert "生图" not in orch


def test_orchestration_skill_teaches_cloud_install_boundary():
    """云端不能装包时：结构自检 ≠ 外环已跑通。验绿对账不进编排 skill。"""
    body = _body("team_delivery_env")
    orch = _body("team_orchestration_advanced")
    assert "install" in body.lower()
    assert "结构自检" in body or "export_to_local" in body
    assert "consult(team_delivery_env)" in orch
    assert "外环验绿对账" not in body
    assert "最后一次同命令" not in body
    assert "分项分开写" not in body


def test_dispatch_writing_how_lives_in_skill_not_core():
    """交需求不代写骨架：skill 留认知分工；已确认约束在 task 参数；核不复述百科。"""
    from agentcore.runtime.resolve.prompt import _CEO_CORE_HINT
    from agentcore.tools.builtin.delegate.schema import DELEGATE_PARAMETERS

    hint = _CEO_CORE_HINT
    orch = _body("team_orchestration_advanced")
    task_desc = DELEGATE_PARAMETERS["properties"]["tasks"]["items"]["properties"]["task"][
        "description"
    ]

    assert "专业方案归专家" in orch
    assert "正例·交需求" not in orch
    assert "反例·替它设计完" not in orch
    assert "向量数据库" not in orch
    assert "required_sections" not in orch
    assert "读全局规则" not in orch
    assert "读全局规则" not in hint
    assert "已确认约束" in task_desc
    assert "同一套原文" in orch

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
    """第五刀：内环 code_diagnostics / 修码外环 HOW 在 run skill；挂门长字面不在核。"""
    from agentcore.runtime.resolve.prompt import _CEO_CORE_HINT

    hint = _CEO_CORE_HINT
    orch = _body("team_orchestration_advanced")
    run_body = _body("run")
    assert "code_diagnostics" in run_body
    assert "tsc -b" in run_body
    assert "写盘回执" in run_body
    assert "同一人验" not in orch
    assert "consult(run)" not in orch
    assert "验收员" not in orch
    assert "wait_for" not in hint
    assert "口头假验收" not in hint
    assert "已登录，继续" not in hint
    assert "code_diagnostics" not in hint


def test_delivery_landing_how_lives_in_skills_not_core():
    """第四刀：create_folder vs 桌内根在 Skill；空桌 when-to-use 在 mkdir；核只留开火短卡。"""
    from agentcore.runtime.resolve.prompt import _CEO_CORE_HINT
    from agentcore.tools.builtin.file_ops.meta import MkdirTool

    hint = _CEO_CORE_HINT
    delivery = _body("team_delivery_env")
    desk = _body("team_local_desk")
    orch = _body("team_orchestration_advanced")
    help_map = _body("product_help")
    mkdir_desc = MkdirTool().schema.description

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

    assert "src/" in mkdir_desc
    assert "mkdir" in desk
    assert "create_folder" in desk and "桌内工程根" in desk
    assert "court-game/" not in desk
    assert "【空桌勿套工程壳】" not in delivery
    assert "写完再搬" in delivery
    assert "工作区相对完整路径" in delivery or "工作区相对**完整**路径" in delivery
    assert "归位" in delivery and "移到工作区" in delivery
    assert "约定文档出口" in delivery
    assert "用户工程源码仍写业务路径" in delivery
    assert "裸 `reviews/…`" in delivery
    assert "混用两套前缀" in delivery
    assert "交付下载·面板路径" not in delivery
    assert "404" not in delivery
    assert "site/" not in orch
    assert "consult(team_delivery_env)" in orch

    assert "产物出口" in help_map
    assert "双击打开" in help_map
    assert "禁止给本机磁盘路径" in help_map
    assert "工作区根目录" in help_map
    assert "文件夹名" in help_map
    assert "文件名" in help_map
    assert "完整预览" in help_map
    assert "文件」面板" in help_map or "文件面板" in help_map


def test_slice_honesty_how_lives_in_skills_not_core():
    """真两段 / 做软件专段已撤；核不写编制 HOW。skill 只留「先组队≠已拆」与 DAG/lead 二选一。"""
    from agentcore.runtime.resolve.prompt import _CEO_CORE_HINT

    hint = _CEO_CORE_HINT
    orch = _body("team_orchestration_advanced")

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
        "开新层",
        "做一期",
        "写满步骤",
        "已切薄",
        "局部单功能",
        "根委派切片诚实",
        "目标·约束·验收",
        "真两段",
        "轻切片",
        "假两段",
        "【做软件】",
    ):
        assert token not in hint, token

    assert "根委派切片诚实" not in orch
    assert "不知轻重" not in orch
    assert "轻切片" not in orch
    assert "真两段" not in orch
    assert "假两段" not in orch
    assert "【做软件】" not in orch
    assert "开新层" not in orch
    assert "局部单功能" not in orch
    assert "形态跟桌上结果" not in orch
    assert "桌上结果是什么就派什么" not in orch
    assert "目标·约束·验收" in orch
    assert "你可以组队" in orch and "先组队" in orch
    assert "已拆编制" in orch
    assert "单 lead" in orch and "二选一" in orch
    assert "薄旁路" not in orch
    assert "薄旁路" not in hint


def test_orchestration_skill_teaches_software_admission():
    """做软件专段已撤；手写 tasks 在 delegate description；不默认真两段。"""
    from agentcore.runtime.runs.playbooks import PLAYBOOKS
    from agentcore.tools.builtin.delegate.schema import DELEGATE_DESCRIPTION

    body = _body("team_orchestration_advanced")
    assert "手写" in DELEGATE_DESCRIPTION and "tasks" in DELEGATE_DESCRIPTION
    assert "【做软件】" not in body
    assert "假两段" not in body
    assert "形态跟桌上结果" not in body
    assert "桌上结果是什么就派什么" not in body
    assert "薄旁路" not in body
    assert "能少则少" not in body
    assert "单 HTML" not in body
    assert "营销皮" not in body
    assert "不硬拒" not in body
    assert "真两段" not in body
    assert "轻切片" not in body
    assert "尚无工程清单" not in body
    assert "范围没钉" not in body
    assert "单 lead" in body
    assert "可提交长文" in PLAYBOOKS["cite_write_review"].summary
    assert 'playbook="build_app"' not in body
    assert "手写 1 人" not in body
    assert "局部单功能" not in body
    assert "开新层" not in body
    assert "diagnose_fix_verify" not in body
    assert "build_website" not in body
    assert "intensity=solo" not in body
    assert "style=toolshed" not in body
    assert "Vue3" not in body
    assert "intensity=lean" not in body
    assert "intensity=full" not in body


def test_team_orchestration_skill_teaches_sections_not_deleted_deliverable_keys():
    """已删 deliverable 字段不回潮；form 三档在 schema；审查默认 prose 在 skill。"""
    body = _body("team_orchestration_advanced")
    assert "验收项" in body
    assert "2–4" not in body
    assert "2-4" not in body
    assert "字数门槛" not in body
    from agentcore.tools.builtin.delegate.schema import (
        DELEGATE_PARAMETERS,
        TASK_DELIVERABLE_SCHEMA,
    )

    form_desc = TASK_DELIVERABLE_SCHEMA["properties"]["form"]["description"]
    assert "【看】" in form_desc and "【存文档】" in form_desc and "【改工程】" in form_desc
    assert "form=prose" in body
    assert "【看】" not in body
    assert "form=workspace" not in body
    assert "workspace" in form_desc
    assert "artifacts" in TASK_DELIVERABLE_SCHEMA["properties"]
    brief_desc = DELEGATE_PARAMETERS["properties"]["team_brief"]["description"]
    assert "有共享口径才写" in brief_desc
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


def test_team_orchestration_skill_teaches_parallel_review_via_brief():
    """并行口径走 team_brief 参数；skill 不教 post_note。"""
    from agentcore.tools.builtin.delegate.schema import DELEGATE_PARAMETERS

    body = _body("team_orchestration_advanced")
    brief_desc = DELEGATE_PARAMETERS["properties"]["team_brief"]["description"]
    assert "post_note" not in body
    assert "heads_up" not in body
    assert "有共享口径才写" in brief_desc


def test_team_orchestration_skill_teaches_review_contract_template():
    """审查默认 prose；废章节模板 / 评分字段不进 skill。"""
    body = _body("team_orchestration_advanced")
    assert "form=prose" in body
    assert "章节须含" not in body
    assert "问题 / 建议 / 评分" not in body
    assert "required_sections" not in body
    assert "output_format" not in body
    assert "legal.json" not in body
    assert "problems" not in body
    assert "suggestions" not in body
    assert '"score"' not in body and "score（0–10）" not in body


def test_team_orchestration_skill_teaches_team_brief():
    from agentcore.tools.builtin.delegate.schema import DELEGATE_PARAMETERS

    body = _body("team_orchestration_advanced")
    brief_desc = DELEGATE_PARAMETERS["properties"]["team_brief"]["description"]
    task_desc = DELEGATE_PARAMETERS["properties"]["tasks"]["items"]["properties"]["task"][
        "description"
    ]
    assert "seed_notes" not in body
    assert "建墙" not in body
    assert "有共享口径才写" in brief_desc
    assert "一行一条" in brief_desc
    assert "正交扇出" not in body
    assert "不写 brief" not in body
    assert "换行堆" not in body
    for token in (
        "checkpoint_after",
        "bind_after_deps",
        'coordination="wall"',
        "complexity_hint",
        "result_handling",
        "require_upstream",
    ):
        assert token not in body, token
    assert "已确认约束" in task_desc
    assert "自拟默认" in task_desc or "自拟" in task_desc


def test_team_orchestration_skill_teaches_coordination_wall_vs_none():
    from agentcore.tools.builtin.delegate.schema import DELEGATE_PARAMETERS

    body = _body("team_orchestration_advanced")
    brief_desc = DELEGATE_PARAMETERS["properties"]["team_brief"]["description"]
    assert "建墙" not in body
    assert "有共享口径才写" in brief_desc
    assert "正交扇出" not in body
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
    assert "单页一人" not in body
    assert "一人做完" not in body
    assert "营销皮" not in body
    assert "自动静态质检" not in body
    assert "可开 web_quality_scan" not in body
    assert "web_quality_scan" not in body
    assert "placeholder_scan" not in body
    assert "consumer_deps" not in body
    assert "design_impl_same_grant" not in body
    assert "root_slice_honesty" not in body
    assert "visual_critic" not in body


def test_debate_skill_teaches_adversarial_entry_and_dual_products():
    """HOW：开辩=正反；挑刺/多视角走 delegate；双产物、收尾诚实。不教三种形态。"""
    body = _body("debate_and_review")
    assert "debate" in body and "辩论" in body
    assert "正反" in body
    assert "三种形态" not in body
    assert "motion" in body and "sides" in body
    assert "决策简报" in body and "交锋叙事线" in body
    assert "delegate" in body and "ask_user" in body
    assert "审校岗" in body
    assert "多视角" in body
    assert "别抹平证据状态" in body or "既定事实" in body
    assert "升格" in body or "核实状态" in body
    assert "原样传达" in body or "保留意见" in body
    assert "不引入场外量化" in body
    assert "量化估算" in body
    assert "deep_multi_lens_research" not in body
    assert "硬上限" in body or "拒绝调用" in body
    assert "论点清单" in body
    for mark in ("①", "②", "③", "④"):
        assert mark not in body
    assert "何时用 `debate`（而非" not in body
    assert "未点名" in body
    assert "推进卡" not in body
    assert "挑刺" in body or "压测" in body


def test_debate_skill_teaches_intent_alignment_before_opening():
    """开辩前：对立极不得偷换；指代模糊先澄清；已有调研仍开辩 ≠ 跳过调研。"""
    body = _body("debate_and_review")
    assert "对立极" in body
    assert "偷换" in body
    assert "先澄清" in body
    assert "ask_user" in body
    assert "入口冲突" in body
    assert "跳过调研" in body
    assert "通行证" not in body
    assert "已有" in body and "调研产物" in body
    assert "deep_multi_lens_research" not in body
    assert "模拟法庭" in body or "庭审" in body
    assert "以开辩为准" in body
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


def test_orchestration_skill_teaches_recall_and_delegate_fallback():
    from agentcore.tools.builtin.delegate.schema import DELEGATE_PARAMETERS

    body = _body("team_orchestration_advanced")
    landing = _body("long_form_landing")
    cont_desc = DELEGATE_PARAMETERS["properties"]["tasks"]["items"]["properties"][
        "continue_from_run_id"
    ]["description"]
    repl_desc = DELEGATE_PARAMETERS["properties"]["tasks"]["items"]["properties"][
        "replaces_run_id"
    ]["description"]
    assert "调查后确认修" in cont_desc
    assert "默认乙" not in body
    assert "diagnose_fix_verify" not in body
    assert "补缺口" in repl_desc or "接手" in repl_desc
    assert "只增不减" not in body
    assert "声明超集" not in body
    assert "白名单" not in body
    assert "相关工具" not in body
    assert "str_replace" in landing
    assert "file_append" in landing
    assert "全文重写" not in body
    assert "**禁止**对已有成篇成品再 `file_write`" not in body
    assert "禁止骨架/最小实现" not in body


def test_team_orchestration_skill_teaches_revision_local_edit():
    """修订落盘在 long_form_landing；编排手册不复述 str_replace 剧本。"""
    orch = _body("team_orchestration_advanced")
    landing = _body("long_form_landing")
    assert "str_replace" in landing
    assert "str_replace" not in orch
    assert "有界返工环" not in orch


def test_ask_user_kickoff_skill_teaches_short_clarify():
    skill = build_system_skill_registry().get("asking_the_user")
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
    assert "机制软注入" not in body
    assert "DESIGN" not in body
    assert "任务卡" not in body
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
    assert "短确认·只补缺口" not in body
    assert "<上轮交付缺口>" not in body
    assert "整锅重派" not in body
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
    assert "编制" in body
    assert "做个网站" in body
    assert "展示页" in body or "业务应用" in body
    assert "已钉形态" in body or "形态" in body
    assert "consult(team_orchestration_advanced)" in body
    assert "自动静态质检" not in body
    assert "可开 web_quality_scan" not in body
    assert "web_quality_scan" not in body
    assert "placeholder_scan" not in body
    assert "consumer_deps" not in body
    assert "design_impl_same_grant" not in body
    assert "root_slice_honesty" not in body
    assert "visual_critic" not in body
    assert "营销皮" not in body
    assert "形态跟桌上结果" in body or "糊则短问" in body
    assert 'playbook="build_app"' not in body
    assert "playbook=\"build_website\"" not in body
    assert "consult(build_website)" not in body
    # 点名载体：盖不住才短问；次优标假设继续
    assert "点名载体" in body or "载体/手段" in body
    assert "零摩擦" in body
    assert "盖不住" in body or "做不到" in body
    assert "标假设继续" in body
    assert "规格已齐" in body
    assert "内容齐" not in body
    assert "手段已核" not in body
    assert "SmartArt" not in body and "DrawingML" not in body
    assert "consult(team_delivery_env)" in body
    assert "图形组织图" not in body
    assert "直接拒" not in body
    assert "说满" in body and "空派" in body
    assert "话术锚点" not in body
    assert "极宽" not in body
    assert "载体" in body
    assert "format_options" not in body
    assert "提案墙" not in body


def test_ask_user_kickoff_skill_omits_retired_format_fields():
    body = _body("asking_the_user")
    assert "style_options" not in body
    assert "format_options" not in body
    assert "提案墙" not in body
    assert "短问" in body or "短澄清" in body


def test_ask_user_kickoff_skill_teaches_software_delivery_form_clarify():
    body = _body("asking_the_user")
    assert "软件" in body or "应用" in body
    assert "交付形态" in body
    assert "单 HTML" not in body
    assert "薄旁路" not in body
    assert "手写" in body
    assert "diagnose_fix_verify" not in body
    assert 'playbook="build_app"' not in body
    # 形态消歧 HOW 在编排手册；挡路问档在本 skill / 检查点
    assert "consult(team_orchestration_advanced)" in body


def test_ask_user_skills_ordinary_choice_is_one_line():
    """普通短问权衡写进选项名；第二句仅专用 card。"""
    ask = _body("asking_the_user")
    assert "可配 `detail`" not in ask
    assert "`message`/`detail`" not in ask
    assert "`label` / `detail` / `message`" not in ask
    assert "权衡写进选项名" in ask or "权衡写进 `label`" in ask
    assert "勿填 `detail`" in ask
    assert "问句写" in ask and "prompt" in ask
    assert "配一行 `detail`" not in ask
    assert "发散挑选" in ask
    assert "continue_from_run_id" in ask
    assert "consult(team_orchestration_advanced)" in ask


def test_ask_user_skill_teaches_carrier_advisory():
    body = _body("asking_the_user")
    assert "载体" in body or "手段" in body
    assert "盖不住" in body
    assert "零摩擦" in body
    assert "标假设继续" in body
    assert "不打扰" not in body
    assert "ask_user_kickoff" not in body
    assert "ask_user_midtask" not in body


def test_catalog_has_no_build_website_skill():
    """目录不再登记 build_website；不新开 skill / playbook。"""
    reg = build_system_skill_registry()
    names = {s.name for s in reg.list_all()}
    assert "build_website" not in names
    assert reg.get("build_website") is None


def test_ask_user_skill_teaches_fork_and_annotate():
    skill = build_system_skill_registry().get("asking_the_user")
    assert skill.requires_tools == ("ask_user",)
    body = skill.body
    assert "采纳正方" in body
    assert "标假设继续" in body
    assert "若不符请指正" not in body
    assert "blocking=false" not in body
    assert "unlocks" not in body
    assert "立刻按默认继续把回合做完" not in body
    assert "绝不等待" not in body
    assert "checkpoint_after" not in body
    assert "consult(team_orchestration_advanced)" in body
    assert "落盘前对齐" in body
    assert "按当前设计落盘" in body
    assert "阻塞短问" in body
    assert "本回合明示" in body
    assert "收尾·先报断点" in body
    assert "都实现了" in body or "收尾完成" in body
    assert "断点" in body
    assert "ask_user_kickoff" not in body
    assert "ask_user_midtask" not in body
    desk = _body("team_local_desk")
    assert "open_local_project" in desk
    assert "≠默认开文件夹卡" in desk or "收窄本轮" in desk
    assert "在哪工作" in desk
    assert "仅新建会话" in desk
    assert "勿把云沙箱当桌面默认新建" in desk
    assert "勿推销本机草稿" not in desk
    assert "开工前置" in desk
    assert "register_local_project" in desk
    assert "导入到云" in desk
    assert "从 Git 克隆" in desk
    assert "连接 Git" not in desk
    assert "桌面默认本地对话" in desk
    assert "本机传统" in desk
    assert "Ask" not in desk or "改导" not in desk
    assert "consult(team_cross_folder)" in desk
    assert "开发双仓" not in desk
    assert "target_folder_id" not in desk
    assert "读写通吃" not in desk
    assert "跨文件夹须派工换桌" not in desk
    assert "已绑" in desk or "跑" in desk
    assert "跑" in desk and "当前" in desk
    help_body = _body("product_help")
    assert "https://fashitianxia.xyz/download" in help_body
    assert "consult(product_help)" in desk
    assert "授权已确认" in desk
    assert "区外：" in desk
    assert "consult(external_mount_readonly)" in desk
    assert "授权后发现" not in desk
    assert "口头同意" not in desk
    assert "失败分型" not in desk
    from agentcore.runtime.resolve.prompt import capability_how_suffix

    granted = capability_how_suffix({"external_mount_readonly"})
    assert "well_known" in granted
    assert "target_name" in granted
    assert "先写工作区" in granted and "file_copy" in granted
    assert "只读已挂" in granted
    assert "口头同意" in granted
    assert "客户端行" in desk and "缺口" in desk
    assert "就好办了" not in desk
    assert "口述覆盖" in desk
    assert "Folders" not in desk
    assert "Composer" in desk
    assert "授权在哪里" not in desk
    ask_body = skill.body
    assert "consult(team_local_desk)" in ask_body
    assert "consult(team_delivery_env)" in ask_body
    assert "整题授权" in ask_body
    assert "grant_*" in ask_body
    assert "grant_organize_folder" not in ask_body
    assert "导入到云" not in ask_body
    assert "就好办了" not in ask_body
    assert "open_local_project" not in ask_body
    assert "授权在哪里" not in ask_body
    assert "https://fashitianxia.xyz/download" not in ask_body


def test_orchestration_skill_teaches_wave_boundary_pause():
    """提纲过目在 cite_write_review slots；编排手册只留短问钩。"""
    from agentcore.runtime.runs.playbooks import PLAYBOOKS

    body = _body("team_orchestration_advanced")
    slots = PLAYBOOKS["cite_write_review"].slots
    assert "checkpoint" in slots
    assert "明文" in slots or "提纲" in slots
    assert "consult(asking_the_user)" in body
    for token in (
        "checkpoint_after",
        "bind_after_deps",
        'coordination="wall"',
        "require_upstream",
    ):
        assert token not in body, token


def test_delegate_checkpoint_skill_is_gone():
    """提纲过目不是独立 skill；停靠 playbook slots，不进目录。"""
    assert build_system_skill_registry().get("delegate_checkpoint") is None
    assert "delegate_checkpoint" not in render_skill_directory(
        build_system_skill_registry(), _FULL_TOOLS
    )
    assert "提纲过目" not in render_skill_directory(
        build_system_skill_registry(), _FULL_TOOLS
    )


def test_run_skill_teaches_command_face():
    skill = build_system_skill_registry().get("run")
    assert skill is not None
    assert skill.requires_tools == ("run",)
    assert skill.summary == "跑命令 / 启服"
    body = skill.body
    assert ".bat" in body and "CRLF" in body
    assert "background" in body
    assert "wait_for" in body
    assert "command" in body
    assert "code_diagnostics" in body
    assert "tsc -b" in body
    assert "写盘回执" in body
    assert "CEO 只启停" not in body
    assert "验收与短命令由队员" not in body
    assert "禁止自己跑" not in body
    assert "- run：" in render_skill_directory(
        build_system_skill_registry(), _FULL_TOOLS
    )
    assert "- run：" not in render_skill_directory(
        build_system_skill_registry(), _NO_LIVE_USER
    )


def test_verify_and_fix_skill_is_gone():
    """改码循环不是 skill：不进目录。何时跑测试在 run description。"""
    assert build_system_skill_registry().get("verify_and_fix") is None
    assert "verify_and_fix" not in render_skill_directory(
        build_system_skill_registry(), _FULL_TOOLS
    )


def test_long_form_dispatch_and_landing_how():
    from agentcore.runtime.runs.playbooks import PLAYBOOKS

    orch = _body("team_orchestration_advanced")
    assert "成文后梯度" not in orch
    assert "档 3" not in orch
    assert "【成品文件只装成品】" in orch
    assert "提交" in orch
    assert "consult(team_delivery_env)" in orch
    assert "md_to_pdf" not in orch
    assert "reportlab" not in orch
    assert "write_section" not in orch
    assert "continue_writing" not in orch
    assert "流水线已在执行" not in orch
    assert "合并进行中" not in orch
    assert "参数不是合法 JSON" not in orch
    cite = PLAYBOOKS["cite_write_review"].summary
    assert "可提交长文" in cite or "点名审校" in cite
    for token in (
        "checkpoint_after",
        "bind_after_deps",
        'coordination="wall"',
        "require_upstream",
    ):
        assert token not in orch, token

    landing = build_system_skill_registry().get("long_form_landing")
    assert landing is not None
    assert landing.requires_tools == ()
    assert landing.audience == ("worker",)
    body = landing.body
    assert "file_write" in body
    assert "file_append" in body
    assert "骨架填空" in body
    assert "file_read 抽查" not in body
    assert "manifest" in body
    assert "run" in body
    assert "handoff" in body
    assert "禁止" in body and "file_read" in body
    assert "验真" in body and "例外" in body
    assert "已落盘短状态" in body
    assert "_landed_summary" not in body
    assert "清参" in body or "改稿" in body
    assert "真文" in body
    assert "str_replace" in body
    assert "重发" in body
    assert "主路径" in body and "完整正文" in body
    assert "禁止】整篇一次" not in body and "禁止】无骨架整篇一次" not in body
    assert "连续写失败" in body or "分段" in body
    assert "参数不是合法 JSON" in body
    assert "write_section" not in body
    assert "md_to_pdf" in body
    assert "consult(team_delivery_env)" not in body
    assert "continue_from_run_id" not in body
    assert "map_fanout" not in body
    assert "checkpoint_after" not in body
    assert "建站" in body
    assert "FILL" in body or "str_replace" in body


def test_long_form_landing_does_not_teach_completeness_hard_reject():
    landing = build_system_skill_registry().get("long_form_landing")
    assert landing is not None
    body = landing.body
    assert "中间省略" not in body
    assert "allow_shrink" not in body
    assert "硬拒" not in body
    assert "清参后改稿" in body
    assert body.count("清参后改稿") == 1


def test_data_file_landing_skill_teaches_script_transform_and_invariants():
    skill = build_system_skill_registry().get("data_file_landing")
    assert skill is not None
    # Ungated: consult is CEO+worker; body is the worker loop. Do not gate on
    # run (CEO may lack it this turn → skill would vanish from the supervisor catalog).
    assert skill.requires_tools == ()
    assert skill.audience == ("ceo", "worker")
    body = skill.body
    assert "微信" not in body
    assert "手抄" in body
    assert "run" in body
    assert "不变量" in body
    assert "分类笔数" in body or "源记录总数" in body
    assert "口径" in body
    assert "改口" in body
    assert "人质" in body
    assert "先交" in body
    assert "缺口含" in body
    assert "无法可靠解析的源数据文件" in body
    assert "账单" in body and "报表" in body and "导出记录" in body
    assert "看原件" in body
    assert "认形态" not in body
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
    # 目录只 WHEN；账单/报表等 HOW 钉 body
    assert "落盘" in skill.summary
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
    assert "form=files" in no_exec
    assert "两份写进 task" in no_exec
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


def test_deep_multi_lens_research_absent_from_catalog():
    """场面配方不另立 consult 名；开辩须点名留 debate。"""
    reg = build_system_skill_registry()
    assert reg.get("deep_multi_lens_research") is None
    directory = render_skill_directory(reg, _NO_LIVE_USER)
    assert "deep_multi_lens_research" not in directory
    orch = _body("team_orchestration_advanced")
    assert "deep_multi_lens_research" not in orch
    assert "点名开辩" in orch or "debate" in orch


def test_named_debate_routes_to_debate_without_mlr():
    """点名开辩 / 模拟庭审 / 终局对抗 → debate_and_review 直调 debate。"""
    debate = build_system_skill_registry().get("debate_and_review")
    assert debate is not None
    assert "入口" in debate.body
    assert "直调" in debate.body and "debate" in debate.body
    assert "庭前取证" in debate.body or "辩论机制" in debate.body
    assert "未点名" in debate.body
    assert "不调" in debate.body and "`debate`" in debate.body
    assert "挑刺" in debate.body or "压测" in debate.body
    from agentcore.runtime.skills.debate_and_review import MULTI_LENS_COURTROOM_TRIGGERS

    for t in MULTI_LENS_COURTROOM_TRIGGERS:
        assert t in debate.body
    assert "deep_multi_lens_research" not in debate.summary
    assert "deep_multi_lens_research" not in debate.body
    assert "也可直接开辩" not in debate.body


def test_debate_and_review_summary_is_what_not_when():
    """目录只写这是什么；何时开辩在工具 description + body。"""
    debate = build_system_skill_registry().get("debate_and_review")
    assert debate is not None
    summary = debate.summary
    assert "辩论" in summary
    assert "deep_multi_lens_research" not in summary
    assert "直调" in debate.body and "debate" in debate.body
    assert "除外" not in summary or "模拟法庭类终局诉求" not in summary


def test_legal_case_analysis_summary_excludes_public_mock_court():
    """目录只写这是什么；公共终局对抗除外钉 body，不另指已删 skill。"""
    legal_reg = build_system_skill_registry(include_legal=True)
    case = legal_reg.get("legal_case_analysis")
    assert case is not None
    assert "接案" in case.summary or "诉讼策略" in case.summary
    assert "公共事件" not in case.summary
    assert "除外" not in case.summary
    assert "先对抗后研判" not in case.summary
    assert "deep_multi_lens_research" not in case.summary
    body = case.body
    assert "除外" in body
    assert "模拟法庭" in body
    assert "多维取证" in body
    assert "deep_multi_lens_research" not in body
    assert "team_orchestration_advanced" in body
    assert "先对抗后研判" in body


def test_legal_case_analysis_body_redirects_public_mock_court_to_orchestration():
    """公共模拟法庭不走接案 skill，改按结构组队。"""
    legal_reg = build_system_skill_registry(include_legal=True)
    body = legal_reg.get("legal_case_analysis").body
    assert "模拟法庭" in body
    assert "deep_multi_lens_research" not in body
    assert "lens_crosscheck" not in body
    assert "停止" in body or "勿用本 skill" in body or "不走本 skill" in body
    assert "team_orchestration_advanced" in body


def test_legal_summaries_do_not_claim_public_events():
    """法律目录行不抢公共事件 WHEN。"""
    legal_reg = build_system_skill_registry(include_legal=True)
    case_skill = legal_reg.get("legal_case_analysis")
    brief_skill = legal_reg.get("legal_answer_brief")
    for ls in (case_skill.summary, brief_skill.summary):
        assert "公共事件" not in ls
        assert "平行取证" not in ls
        assert "命题卡" not in ls
    assert "接案" in case_skill.summary or "诉讼策略" in case_skill.summary
    assert "答辩状" in brief_skill.summary
    assert "→ 本条" not in brief_skill.summary
    assert "→ 本条" not in case_skill.summary
    assert "先对抗后研判" in case_skill.body
    assert "red_team" not in brief_skill.body
