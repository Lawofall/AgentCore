"""Account-level 换用 overlay: consult listing/fetch share the replacement body."""

from __future__ import annotations

from agentcore.memory.rules_injection import OnDemandUserRule
from agentcore.runtime.context.consult_sources import (
    MergedConsultSource,
    RuleConsultSource,
    SkillConsultSource,
    expand_skill_tool_names,
)
from agentcore.runtime.skills.registry import SkillRegistry, SystemSkill
from agentcore.runtime.skills.replacements import (
    SkillOverlay,
    SkillReplacement,
    merge_skill_overlays,
    overlay_layer,
    skill_replacements_from_payload,
)


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


def _replacement() -> SkillReplacement:
    return SkillReplacement(
        summary="用户触发语",
        body="USER HOW",
        document_id="d1",
        document_name="合同审查",
    )


async def test_replaced_slot_uses_user_summary_and_body():
    source = SkillConsultSource(
        registry=_registry(),
        tool_names={"ask_user"},
        replacements={"asking_the_user": _replacement()},
    )
    entries = await source.list_directory("u")
    assert [(e.name, e.summary) for e in entries] == [
        ("asking_the_user", "用户触发语")
    ]
    assert await source.fetch_by_name("u", "asking_the_user") == "USER HOW"


async def test_unbound_slot_keeps_factory_body():
    source = SkillConsultSource(registry=_registry(), tool_names={"ask_user"})
    entries = await source.list_directory("u")
    assert [(e.name, e.summary) for e in entries] == [
        ("asking_the_user", "向用户提问")
    ]
    assert await source.fetch_by_name("u", "asking_the_user") == "FACTORY HOW"


async def test_replaced_slot_still_gated_by_requires_tools():
    source = SkillConsultSource(
        registry=_registry(),
        tool_names=set(),
        replacements={"asking_the_user": _replacement()},
    )
    assert await source.list_directory("u") == []
    assert await source.fetch_by_name("u", "asking_the_user") is None


async def test_explicit_replacement_origin_is_user():
    merged = MergedConsultSource(
        skill=SkillConsultSource(
            registry=_registry(),
            tool_names={"ask_user"},
            replacements={"asking_the_user": _replacement()},
        )
    )
    hit = await merged.fetch_hit("u", "asking_the_user")
    assert hit is not None
    assert hit.body == "USER HOW"
    assert hit.origin == "user"


async def test_factory_skill_origin_stays_system():
    merged = MergedConsultSource(
        skill=SkillConsultSource(registry=_registry(), tool_names={"ask_user"})
    )
    hit = await merged.fetch_hit("u", "asking_the_user")
    assert hit is not None
    assert hit.origin == "system"


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


async def test_expand_skill_tool_names_keeps_replacements():
    leaf = MergedConsultSource(
        skill=SkillConsultSource(
            registry=_registry(),
            tool_names=set(),
            replacements={"asking_the_user": _replacement()},
        )
    )
    lead = expand_skill_tool_names(leaf, {"ask_user"})
    assert await leaf.fetch_by_name("u", "asking_the_user") is None
    assert await lead.fetch_by_name("u", "asking_the_user") == "USER HOW"
    hit = await lead.fetch_hit("u", "asking_the_user")
    assert hit is not None and hit.origin == "user"


async def test_muted_slot_leaves_directory_and_fetch():
    source = SkillConsultSource(
        registry=_registry(),
        tool_names={"ask_user"},
        replacements={"asking_the_user": _replacement()},
        muted=frozenset({"asking_the_user"}),
    )
    assert await source.list_directory("u") == []
    assert await source.fetch_by_name("u", "asking_the_user") is None


async def test_expand_skill_tool_names_keeps_mutes():
    leaf = MergedConsultSource(
        skill=SkillConsultSource(
            registry=_registry(),
            tool_names=set(),
            muted=frozenset({"asking_the_user"}),
        )
    )
    lead = expand_skill_tool_names(leaf, {"ask_user"})
    assert await lead.fetch_by_name("u", "asking_the_user") is None


def test_payload_parser_skips_junk():
    parsed = skill_replacements_from_payload(
        {
            "skill_replacements": [
                {
                    "slot": "asking_the_user",
                    "document_id": "d1",
                    "document_name": "合同审查",
                    "description": "用户触发语",
                    "content": "USER HOW",
                },
                {"slot": "run"},
                "nope",
            ]
        }
    )
    assert parsed["asking_the_user"].body == "USER HOW"
    assert "run" not in parsed


def test_mute_payload_parser():
    from agentcore.runtime.skills.replacements import overlay_from_payload

    overlay = overlay_from_payload(
        {"skill_mutes": ["asking_the_user", "", "run"]}
    )
    assert overlay.muted == frozenset({"asking_the_user", "run"})
    assert overlay.replacements == {}


def test_nearer_layer_wins_and_clearing_inherits():
    account = SkillOverlay(
        replacements={"asking_the_user": _replacement()},
        muted=frozenset({"asking_the_user"}),
    )
    folder = SkillOverlay(
        replacements={
            "asking_the_user": SkillReplacement(
                summary="本夹触发语",
                body="FOLDER HOW",
                document_id="d2",
                document_name="本夹审查",
            )
        },
        muted=frozenset(),
    )
    merged = merge_skill_overlays(account, folder)
    assert merged.replacements["asking_the_user"].body == "FOLDER HOW"
    assert merged.muted == frozenset({"asking_the_user"})
    assert overlay_layer(merged, folder, kind="replaced", slot="asking_the_user") == "here"
    assert overlay_layer(merged, folder, kind="muted", slot="asking_the_user") == "inherited"

    after_restore = merge_skill_overlays(account, SkillOverlay(replacements={}, muted=frozenset()))
    assert after_restore.replacements["asking_the_user"].body == "USER HOW"
