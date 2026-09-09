"""Factory-only system skills on the unified consult source."""

from __future__ import annotations

from agentcore.memory.rules_injection import OnDemandUserRule
from agentcore.runtime.context.consult_sources import (
    MergedConsultSource,
    RuleConsultSource,
    SkillConsultSource,
    expand_skill_tool_names,
)
from agentcore.runtime.skills.registry import SkillRegistry, SystemSkill


def _registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(
        SystemSkill(
            name="asking_the_user",
            summary="向用户提问",
            body="FACTORY HOW",
            requires_tools=("ask_user",),
        )
    )
    return registry


async def test_factory_skill_uses_code_summary_and_body():
    source = SkillConsultSource(registry=_registry(), tool_names={"ask_user"})
    entries = await source.list_directory("u")
    assert [(e.name, e.summary) for e in entries] == [
        ("asking_the_user", "向用户提问")
    ]
    assert await source.fetch_by_name("u", "asking_the_user") == "FACTORY HOW"


async def test_factory_skill_origin_stays_system():
    merged = MergedConsultSource(
        skill=SkillConsultSource(registry=_registry(), tool_names={"ask_user"})
    )
    hit = await merged.fetch_hit("u", "asking_the_user")
    assert hit is not None
    assert hit.origin == "system"


async def test_skill_still_gated_by_requires_tools():
    source = SkillConsultSource(registry=_registry(), tool_names=set())
    assert await source.list_directory("u") == []
    assert await source.fetch_by_name("u", "asking_the_user") is None


async def test_unbound_same_name_rule_still_shadowed(monkeypatch):
    async def fake_load(_user_id: str, folder_id: str | None = None):
        del folder_id
        return [OnDemandUserRule(name="asking_the_user", summary="用户文件")]

    monkeypatch.setattr(
        "agentcore.memory.rules_injection.load_on_demand_user_rules", fake_load
    )
    merged = MergedConsultSource(
        skill=SkillConsultSource(registry=_registry(), tool_names={"ask_user"}),
        rule=RuleConsultSource(),
    )
    entries = await merged.list_directory("u")
    assert [e.section for e in entries if e.name == "asking_the_user"] == ["skill"]
    assert await merged.fetch_by_name("u", "asking_the_user") == "FACTORY HOW"


async def test_rule_source_skips_bound_document_name(monkeypatch):
    async def fake_load(_user_id: str, folder_id: str | None = None):
        del folder_id
        return [
            OnDemandUserRule(name="合同审查", summary="s"),
            OnDemandUserRule(name="其他附录", summary="t"),
        ]

    monkeypatch.setattr(
        "agentcore.memory.rules_injection.load_on_demand_user_rules", fake_load
    )
    src = RuleConsultSource(skip_names=frozenset({"合同审查"}))
    entries = await src.list_directory("u")
    assert [e.name for e in entries] == ["其他附录"]
    assert await src.fetch_by_name("u", "合同审查") is None


async def test_expand_skill_tool_names_unlocks_factory_how():
    leaf = MergedConsultSource(
        skill=SkillConsultSource(registry=_registry(), tool_names=set())
    )
    lead = expand_skill_tool_names(leaf, {"ask_user"})
    assert await leaf.fetch_by_name("u", "asking_the_user") is None
    assert await lead.fetch_by_name("u", "asking_the_user") == "FACTORY HOW"
    hit = await lead.fetch_hit("u", "asking_the_user")
    assert hit is not None and hit.origin == "system"
