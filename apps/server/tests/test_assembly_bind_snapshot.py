"""Sidecar session capability binds the same surface as a cloud assembly arm."""

from __future__ import annotations

from agentcore.assembly.bind import (
    arm_assembly_bind,
    current_mcp_server_ids,
    current_omit_desk_rules,
    current_skill_membership,
    disarm_conversation_assembly,
)
from agentcore.assembly.recipes import CHAT, FULL, capability_preset_id
from agentcore.runtime.skills.factory_switches import current_omit_factory_catalog
from agentcore.runtime.turn.snapshot import AssemblyBind
from agentcore.tools.switchboard import current_disabled_tools, switch_blocks


def test_official_chat_id_wins_over_client_lists():
    token = arm_assembly_bind(
        AssemblyBind(
            assembly_id=capability_preset_id(CHAT),
            disabled_tools=(),
            omitted_projections=(),
            omitted_factory_skills=(),
            omit_desk_rules=False,
            enabled_mcp_server_ids=("fs",),
            skill_membership={"doc-1": "always"},
        )
    )
    try:
        assert "web" in current_disabled_tools()
        assert current_omit_factory_catalog() is True
        assert current_omit_desk_rules() is True
        assert current_skill_membership() is None
        assert current_mcp_server_ids() == frozenset()
    finally:
        disarm_conversation_assembly(token)


def test_official_full_keeps_account_prompts_and_no_plugs():
    token = arm_assembly_bind(
        AssemblyBind(
            assembly_id=capability_preset_id(FULL),
            disabled_tools=("web",),
            omitted_projections=(),
            omitted_factory_skills=("page_ui",),
            omit_desk_rules=True,
            enabled_mcp_server_ids=("fs",),
            skill_membership={"doc-1": "always"},
        )
    )
    try:
        assert current_disabled_tools() == frozenset()
        assert current_omit_factory_catalog() is False
        assert current_omit_desk_rules() is False
        assert current_skill_membership() is None
        assert current_mcp_server_ids() == frozenset()
        assert switch_blocks("web") is False
    finally:
        disarm_conversation_assembly(token)


def test_user_assembly_uses_lists_and_membership():
    token = arm_assembly_bind(
        AssemblyBind(
            assembly_id="asm-mine",
            disabled_tools=("web",),
            omitted_projections=(),
            omitted_factory_skills=("page_ui",),
            omit_desk_rules=False,
            enabled_mcp_server_ids=("fs",),
            skill_membership={"doc-1": "always"},
        )
    )
    try:
        assert "web" in current_disabled_tools()
        assert switch_blocks("web_search") is True
        assert current_skill_membership() == {"doc-1": "always"}
        assert current_mcp_server_ids() == frozenset({"fs"})
        from agentcore.runtime.skills.factory_switches import include_factory_skill

        assert include_factory_skill("page_ui") is False
    finally:
        disarm_conversation_assembly(token)


def test_user_null_membership_names_none():
    token = arm_assembly_bind(
        AssemblyBind(
            assembly_id="asm-mine",
            disabled_tools=(),
            omitted_projections=(),
            omitted_factory_skills=(),
            omit_desk_rules=False,
            enabled_mcp_server_ids=(),
            skill_membership=None,
        )
    )
    try:
        assert current_skill_membership() == {}
        assert current_mcp_server_ids() == frozenset()
    finally:
        disarm_conversation_assembly(token)
