"""Tests for the legal vertical v0 domain Skills.

Two domain skills share the same registry + gating: ``legal_answer_brief`` (「答辩状
作战室」) and ``legal_case_analysis`` (「原告 / 被告 / 法官」三方视角案情研判). The same
three guards apply to each:
1. Opt-in gating — ABSENT from the default registry and PRESENT only with
   ``include_legal=True`` / ``enabled_packs={"legal"}`` (so generic deployments
   never see legal content; the platform system-skill set in test_skills.py
   stays exactly the 7). Production wiring is deployment-listed ∧ user-bound.
2. consult resolves it (CEO can pull the full guidance) when registered, and
   the 按需目录 lists it when its required tools are wired.
3. The body still teaches its mechanism — the orchestration (delegate + debate) and
   the anti-hallucination floor — so it can't silently rot into a generic prompt.
"""

from pathlib import Path

from agentcore.runtime.context.consult_sources import MergedConsultSource, SkillConsultSource
from agentcore.runtime.legal_skills import LEGAL_SKILLS
from agentcore.runtime.skills import build_system_skill_registry, render_skill_directory
from agentcore.tools.builtin.consult import ConsultTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace

# legal_answer_brief gates on delegate（原告红队审校岗也走 delegate）。
_FULL_TOOLS = {"delegate", "ask_user", "debate", "test_run"}


def _ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


# --- opt-in gating -----------------------------------------------------------


def test_legal_skill_absent_by_default():
    # Default (include_legal=False): the platform set only — no legal pollution.
    reg = build_system_skill_registry()
    assert reg.get("legal_answer_brief") is None


def test_legal_skill_present_when_opted_in():
    reg = build_system_skill_registry(include_legal=True)
    skill = reg.get("legal_answer_brief")
    assert skill is not None
    assert skill.requires_tools == ("delegate",)


def test_legal_skill_layers_onto_the_platform_set():
    # include_legal adds the legal pack ON TOP of the platform system skills, same registry.
    base = {s.name for s in build_system_skill_registry().list_all()}
    full = {s.name for s in build_system_skill_registry(include_legal=True).list_all()}
    assert full - base == {s.name for s in LEGAL_SKILLS}
    assert "team_orchestration_advanced" in full  # platform skills still there


# --- catalog + consult -------------------------------------------------------


def test_directory_lists_legal_skill_when_enabled_and_tools_wired():
    reg = build_system_skill_registry(include_legal=True)
    out = render_skill_directory(reg, _FULL_TOOLS)
    assert "- legal_answer_brief：" in out


async def test_consult_resolves_legal_skill_when_enabled():
    reg = build_system_skill_registry(include_legal=True)
    tool = ConsultTool(source=MergedConsultSource(skill=SkillConsultSource(registry=reg, tool_names={"delegate","debate","ask_user"})))
    result = await tool.execute({"name": "legal_answer_brief"}, _ctx())
    assert result.success
    assert result.output == reg.get("legal_answer_brief").body
    assert result.display["origin"] == "system"
    assert "kind" not in result.display


# --- body teaches the mechanism ---------------------------------------------


def _body() -> str:
    return build_system_skill_registry(include_legal=True).get("legal_answer_brief").body


def test_body_teaches_war_room_red_team_orchestration():
    # hero = 对方律师作战室: delegate 起草/原告红队审校岗/核验/格式 + 人审。
    body = _body()
    assert "delegate" in body
    assert "审校岗" in body
    assert "原告红队" in body  # the adversary that single-agent can't fake
    assert "red_team" not in body
    assert "is_subject" not in body


def test_body_teaches_answer_brief_structure():
    body = _body()
    assert "答辩状" in body
    assert "程序" in body and "实体" in body  # 程序性 / 实体性抗辩
    assert "质证" in body


def test_body_enforces_anti_hallucination_floor():
    # 真交付律师档位的底线：未核验不得引法条 / 标法域 / 免责 / 人审闸门。
    body = _body()
    assert "核验" in body and "不得" in body
    assert "中国大陆法" in body
    assert "免责" in body
    assert "checkpoint_after" in body or "人审" in body


def test_body_teaches_inline_final_brief_with_citation_markers():
    # 引用即出处：终稿走 CEO 收口正文（非仅落盘文件），已核验法条带台账 id #rN
    #（玻璃箱可审计 / 可溯源），且 [待核验] 法条不得编引用（交付前核验拦截编造）。
    body = _body()
    assert "终稿" in body
    assert "#rN" in body and "台账" in body
    assert "[待核验]" in body


# --- legal_case_analysis：三方视角案情研判（接案评估 / 诉讼策略） -------------------


def test_case_analysis_absent_by_default():
    # 与 legal_answer_brief 同：默认不污染通用部署。
    assert build_system_skill_registry().get("legal_case_analysis") is None


def test_case_analysis_present_when_opted_in():
    reg = build_system_skill_registry(include_legal=True)
    skill = reg.get("legal_case_analysis")
    assert skill is not None
    # 原被告对抗靠 debate、法官研判 + 核验靠 delegate（两者恒在 CEO 路径）。
    assert skill.requires_tools == ("delegate", "debate")


def test_directory_lists_case_analysis_when_enabled_and_tools_wired():
    reg = build_system_skill_registry(include_legal=True)
    out = render_skill_directory(reg, _FULL_TOOLS)
    assert "- legal_case_analysis：" in out


async def test_consult_resolves_case_analysis_when_enabled():
    reg = build_system_skill_registry(include_legal=True)
    tool = ConsultTool(source=MergedConsultSource(skill=SkillConsultSource(registry=reg, tool_names={"delegate","debate","ask_user"})))
    result = await tool.execute({"name": "legal_case_analysis"}, _ctx())
    assert result.success
    assert result.output == reg.get("legal_case_analysis").body
    assert result.display["origin"] == "system"
    assert "kind" not in result.display


def _case_body() -> str:
    return build_system_skill_registry(include_legal=True).get("legal_case_analysis").body


def test_case_analysis_body_teaches_three_perspective_orchestration():
    # 三方 = 原告/被告（debate form=debate）+ 法官（delegate 中立研判 worker）。
    body = _case_body()
    assert "原告" in body and "被告" in body and "法官" in body
    assert "delegate" in body
    assert "debate" in body and 'form="debate"' in body
    assert "plaintiff" in body and "defendant" in body  # 对称两方
    assert "法官研判" in body and "举证责任" in body  # 中立裁判位的法律大脑


def test_case_analysis_body_teaches_two_scenarios():
    # v0 一支 skill 覆盖两个场景、产物分别给（A 分流）。
    body = _case_body()
    assert "接案评估" in body and "诉讼策略" in body
    assert "ask_user" in body  # A 分流：拿不准反问


def test_case_analysis_body_enforces_anti_hallucination_floor():
    # 同答辩状底线，额外：胜负研判定性为倾向性研判（非判决结果预测）。
    body = _case_body()
    assert "核验" in body and "不得" in body
    assert "中国大陆法" in body
    assert "免责" in body
    assert "checkpoint_after" in body or "人审" in body
    assert "倾向性研判" in body and "非判决结果预测" in body
