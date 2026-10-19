"""Official 标准, 完整, and 极简 stay live. Create is a snapshot of 标准."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentcore.assembly.bind import (
    arm_conversation_assembly,
    current_omit_desk_rules,
    current_skill_membership,
    disarm_conversation_assembly,
)
from agentcore.assembly.recipes import (
    CHAT,
    FULL,
    STANDARD,
    STANDARD_TOOLS,
    capability_preset_id,
    capability_recipe_for_id,
    capability_recipe_for_key,
    clear_recipe,
    factory_default_id,
    surface_for,
    surface_from_recipe,
    surface_from_row,
)
from agentcore.runtime.context.envelope_switches import all_projection_ids
from agentcore.runtime.skills.factory_switches import (
    all_factory_skill_names,
    current_omit_factory_catalog,
)
from agentcore.tools.switchboard import all_switch_ids, current_disabled_tools


def test_chat_recipe_id_stays_bound():
    chat_id = capability_preset_id(CHAT)
    assert capability_recipe_for_id(chat_id) is not None
    surface = surface_for(chat_id, None)
    assert surface is not None
    assert surface.recipe == CHAT


def test_standard_whitelist_turns_off_infrequent_switches():
    surface = surface_from_recipe(capability_recipe_for_key(STANDARD))  # type: ignore[arg-type]
    assert set(surface.disabled_tools) == set(all_switch_ids()) - STANDARD_TOOLS
    assert "debate" in surface.disabled_tools
    assert "folders" in surface.disabled_tools
    assert "chats" in surface.disabled_tools
    assert "files" not in surface.disabled_tools
    assert "delegate" not in surface.disabled_tools
    assert surface.omitted_projections == ()
    assert surface.omit_factory_catalog is False
    assert surface.omit_desk_rules is False
    assert surface.recipe == STANDARD


def test_full_keeps_every_switch_and_envelope_line():
    surface = surface_from_recipe(capability_recipe_for_key(FULL))  # type: ignore[arg-type]
    assert surface.disabled_tools == ()
    assert surface.omitted_projections == ()
    assert surface.omitted_factory_skills == ()
    assert surface.omit_factory_catalog is False
    assert surface.omit_desk_rules is False


def test_chat_recipe_denies_every_current_switch_and_envelope_line():
    surface = surface_from_recipe(capability_recipe_for_key(CHAT))  # type: ignore[arg-type]
    assert set(surface.disabled_tools) == set(all_switch_ids())
    assert set(surface.omitted_projections) == set(all_projection_ids())
    assert set(surface.omitted_factory_skills) == set(all_factory_skill_names())
    assert surface.omit_factory_catalog is True
    assert surface.omit_desk_rules is True
    assert surface.recipe == CHAT


def test_stale_full_recipe_key_still_wins_until_cleared():
    row = SimpleNamespace(
        recipe=FULL,
        disabled_tools=["web"],
        omitted_projections=[],
        omitted_factory_skills=[],
        omit_desk_rules=False,
    )
    surface = surface_from_row(row)
    assert surface.disabled_tools == ()
    clear_recipe(row)
    assert surface_from_row(row).disabled_tools == ("web",)


def test_loosened_row_omits_one_factory_skill():
    row = SimpleNamespace(
        recipe=None,
        disabled_tools=[],
        omitted_projections=[],
        omitted_factory_skills=["nope", "page_ui"],
        omit_desk_rules=False,
    )
    assert surface_from_row(row).omitted_factory_skills == ("page_ui",)


class _Skills:
    def scalars(self):
        return []


class _Session:
    def __init__(self, row: object) -> None:
        self._row = row

    async def get(self, _model: object, _id: str) -> object:
        return self._row

    async def execute(self, _stmt: object) -> _Skills:
        return _Skills()


@pytest.mark.asyncio
async def test_arm_virtual_chat_omits_tools_and_desk_rules():
    conv = SimpleNamespace(assembly_id=capability_preset_id(CHAT), user_id="u1")
    token = await arm_conversation_assembly(_Session(None), conv)
    try:
        assert "web" in current_disabled_tools()
        assert current_omit_factory_catalog() is True
        assert current_omit_desk_rules() is True
        assert current_skill_membership() is None
    finally:
        disarm_conversation_assembly(token)
    assert current_omit_desk_rules() is False


@pytest.mark.asyncio
async def test_arm_virtual_full_keeps_account_prompts():
    conv = SimpleNamespace(assembly_id=capability_preset_id(FULL), user_id="u1")
    token = await arm_conversation_assembly(_Session(None), conv)
    try:
        assert current_disabled_tools() == frozenset()
        assert current_skill_membership() is None
        assert current_omit_factory_catalog() is False
    finally:
        disarm_conversation_assembly(token)


def test_factory_default_is_standard():
    assert factory_default_id() == capability_preset_id(STANDARD)


@pytest.mark.asyncio
async def test_arm_virtual_standard_turns_off_infrequent_only():
    conv = SimpleNamespace(
        assembly_id=capability_preset_id(STANDARD), user_id="u1"
    )
    token = await arm_conversation_assembly(_Session(None), conv)
    try:
        assert current_disabled_tools() == frozenset(
            {"debate", "folders", "chats"}
        )
        assert current_omit_factory_catalog() is False
        assert current_omit_desk_rules() is False
    finally:
        disarm_conversation_assembly(token)


def test_standard_off_tool_miss_asks_to_switch_to_full():
    from agentcore.runtime.engine.tool_exec_args import _missing_tool_feedback
    from agentcore.tools.registry import ToolRegistry
    from agentcore.tools.switchboard import (
        bind_disabled_tool_groups,
        reset_disabled_tool_groups,
    )

    token = bind_disabled_tool_groups(["debate", "folders", "chats"])
    try:
        msg, status, policy = _missing_tool_feedback(
            "debate", raw_name=None, registry=ToolRegistry()
        )
        assert "完整" in msg
        assert "不要重试" in msg
        assert status == "not_assembled"
        assert policy is True
        msg2, _, _ = _missing_tool_feedback(
            "search_conversations", raw_name=None, registry=ToolRegistry()
        )
        assert "完整" in msg2
        assert "@" in msg2
    finally:
        reset_disabled_tool_groups(token)
