"""Official 极简 / 轻量 / 完整 recipes stay live until the user edits them."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentcore.assembly.bind import (
    arm_conversation_assembly,
    current_omit_desk_rules,
    current_omit_factory_catalog,
    disarm_conversation_assembly,
)
from agentcore.assembly.recipes import (
    CHAT,
    WEB,
    capability_preset_id,
    capability_recipe_for_key,
    clear_recipe,
    surface_from_recipe,
    surface_from_row,
)
from agentcore.runtime.context.envelope_switches import FILE_INDEX, RUNTIME_DATE, all_projection_ids
from agentcore.tools.switchboard import all_switch_ids, current_disabled_tools


def test_chat_denies_every_current_switch_and_every_envelope_line():
    surface = surface_from_recipe(capability_recipe_for_key(CHAT))  # type: ignore[arg-type]
    assert set(surface.disabled_tools) == set(all_switch_ids())
    assert set(surface.omitted_projections) == set(all_projection_ids())
    assert surface.omit_factory_catalog is True
    assert surface.omit_desk_rules is True


def test_web_keeps_search_and_the_date():
    surface = surface_from_recipe(capability_recipe_for_key(WEB))  # type: ignore[arg-type]
    assert "web" not in surface.disabled_tools
    assert "ask_user" in surface.disabled_tools
    assert "files" in surface.disabled_tools
    assert RUNTIME_DATE not in surface.omitted_projections
    assert FILE_INDEX in surface.omitted_projections
    assert surface.omit_factory_catalog is True
    assert surface.omit_desk_rules is False


def test_recipe_key_wins_over_a_stale_empty_deny_list():
    row = SimpleNamespace(
        recipe=CHAT,
        disabled_tools=[],
        omitted_projections=[],
        omit_factory_catalog=False,
        omit_desk_rules=False,
    )
    surface = surface_from_row(row)
    assert "web" in surface.disabled_tools
    clear_recipe(row)
    assert surface_from_row(row).disabled_tools == ()


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
async def test_arm_applies_chat_recipe_on_the_owned_row():
    row = SimpleNamespace(
        user_id="u1",
        recipe=CHAT,
        disabled_tools=[],
        omitted_projections=[],
        omit_factory_catalog=False,
        omit_desk_rules=False,
        enabled_mcp_server_ids=[],
    )
    conv = SimpleNamespace(assembly_id="asm", user_id="u1")
    token = await arm_conversation_assembly(_Session(row), conv)
    try:
        assert "web" in current_disabled_tools()
        assert current_omit_factory_catalog() is True
        assert current_omit_desk_rules() is True
    finally:
        disarm_conversation_assembly(token)
    assert current_omit_desk_rules() is False


@pytest.mark.asyncio
async def test_arm_virtual_web_preset_has_no_row():
    conv = SimpleNamespace(assembly_id=capability_preset_id(WEB), user_id="u1")
    token = await arm_conversation_assembly(_Session(None), conv)
    try:
        assert "web" not in current_disabled_tools()
        assert "files" in current_disabled_tools()
        assert current_omit_desk_rules() is False
    finally:
        disarm_conversation_assembly(token)
