"""Legal domain Skill templates: not system Skills; HOW + anti-hallucination floor."""

from agentcore.runtime.legal_skills import LEGAL_SKILLS
from agentcore.runtime.skills import build_system_skill_registry
from agentcore.runtime.skills.platform_shelf import (
    PLATFORM_AUTHOR,
    get_platform_template,
    platform_listing_id,
    platform_matches_query,
    platform_templates,
    platform_version_id,
)


def _skill(name: str):
    return next(s for s in LEGAL_SKILLS if s.name == name)


def _body() -> str:
    return _skill("legal_answer_brief").body


def _case_body() -> str:
    return _skill("legal_case_analysis").body


def _complaint_body() -> str:
    return _skill("legal_complaint").body


def _contract_body() -> str:
    return _skill("legal_contract_review").body


def test_legal_skills_absent_from_system_registry():
    reg = build_system_skill_registry()
    names = {s.name for s in LEGAL_SKILLS}
    assert names.isdisjoint({s.name for s in reg.list_all()})
    for name in names:
        assert reg.get(name) is None


def test_platform_shelf_exposes_all_templates():
    names = {s.name for s in platform_templates()}
    assert names == {s.name for s in LEGAL_SKILLS}
    for skill in LEGAL_SKILLS:
        listing_id = platform_listing_id(skill.name)
        assert get_platform_template(listing_id) is skill
        assert platform_version_id(skill.name, skill.body)
        assert skill.title
        assert skill.title != skill.summary
        assert platform_matches_query(skill, None)
        assert platform_matches_query(skill, skill.title)
        assert platform_matches_query(skill, skill.summary[:2])
        assert skill.group == "legal"
        assert platform_matches_query(skill, None, "legal")
        assert not platform_matches_query(skill, None, "writing")
    assert platform_matches_query(LEGAL_SKILLS[0], PLATFORM_AUTHOR)
    assert get_platform_template("00000000-0000-0000-0000-000000000000") is None


def test_body_teaches_war_room_red_team_orchestration():
    body = _body()
    assert "delegate" in body
    assert "审校岗" in body
    assert "原告红队" in body
    assert "red_team" not in body
    assert "is_subject" not in body


def test_body_teaches_answer_brief_structure():
    body = _body()
    assert "答辩状" in body
    assert "程序" in body and "实体" in body
    assert "质证" in body


def test_body_enforces_anti_hallucination_floor():
    body = _body()
    assert "核验" in body and "不得" in body
    assert "中国大陆法" in body
    assert "免责" in body
    assert "checkpoint_after" in body or "人审" in body


def test_body_teaches_inline_final_brief_with_citation_markers():
    body = _body()
    assert "终稿" in body
    assert "#rN" in body and "台账" in body
    assert "[待核验]" in body


def test_case_analysis_body_teaches_three_perspective_orchestration():
    body = _case_body()
    assert "原告" in body and "被告" in body and "法官" in body
    assert "delegate" in body
    assert "debate" in body and 'form="debate"' in body
    assert "plaintiff" in body and "defendant" in body
    assert "法官研判" in body and "举证责任" in body


def test_case_analysis_body_teaches_two_scenarios():
    body = _case_body()
    assert "接案评估" in body and "诉讼策略" in body
    assert "ask_user" in body


def test_case_analysis_body_enforces_anti_hallucination_floor():
    body = _case_body()
    assert "核验" in body and "不得" in body
    assert "中国大陆法" in body
    assert "免责" in body
    assert "checkpoint_after" in body or "人审" in body
    assert "倾向性研判" in body and "非判决结果预测" in body


def test_complaint_body_teaches_defendant_red_team_orchestration():
    body = _complaint_body()
    assert "delegate" in body
    assert "审校岗" in body
    assert "被告红队" in body
    assert "red_team" not in body
    assert "is_subject" not in body


def test_complaint_body_teaches_complaint_structure():
    body = _complaint_body()
    assert "起诉状" in body
    assert "请求权" in body
    assert "诉讼请求" in body


def test_complaint_body_enforces_anti_hallucination_floor():
    body = _complaint_body()
    assert "核验" in body and "不得" in body
    assert "中国大陆法" in body
    assert "免责" in body
    assert "checkpoint_after" in body or "人审" in body
    assert "#rN" in body and "台账" in body
    assert "[待核验]" in body


def test_contract_review_body_teaches_three_perspective_orchestration():
    body = _contract_body()
    assert "我方" in body and "对方" in body and "风险官" in body
    assert "delegate" in body
    assert "debate" in body and 'form="debate"' in body
    assert "our_side" in body and "counterparty" in body


def test_contract_review_body_teaches_two_scenarios():
    body = _contract_body()
    assert "对方来稿" in body and "我方待签" in body
    assert "ask_user" in body


def test_contract_review_body_enforces_anti_hallucination_floor():
    body = _contract_body()
    assert "核验" in body and "不得" in body
    assert "中国大陆法" in body
    assert "免责" in body
    assert "checkpoint_after" in body or "人审" in body
    assert "倾向性意见" in body and "非可以签署的承诺" in body
    assert "合同载明" in body
