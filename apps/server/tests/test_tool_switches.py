"""Tool switches: deny-list normalize, withheld names, registry omit."""

from types import SimpleNamespace

from agentcore.tools.builtin import build_worker_registry
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.switchboard import (
    bind_disabled_tool_groups,
    normalize_disabled_tools,
    omit_disabled_tools,
    reset_disabled_tool_groups,
    switch_member_names,
    withheld_names,
)


def test_normalize_drops_unknown_and_rejects_a_bare_string():
    assert normalize_disabled_tools("files") == ()
    assert normalize_disabled_tools(None) == ()
    assert normalize_disabled_tools(["nope", "web", "files", "files"]) == (
        "files",
        "web",
    )


def test_switch_member_names_follow_the_group_and_delegate_companions():
    assert switch_member_names("files") == (
        "edit",
        "file_batch",
        "file_delete",
        "md_export",
        "write",
    )
    assert switch_member_names("delegate") == (
        "cancel_worker",
        "delegate",
        "replan",
    )
    assert switch_member_names("nope") == ()


def test_delegate_off_withholds_companions_and_leaves_debate():
    names = withheld_names(["delegate"])
    assert {"delegate", "replan", "cancel_worker"} <= names
    assert "debate" not in names
    assert "ask_user" not in names


def test_files_and_web_name_sets():
    files = withheld_names(["files"])
    assert files == frozenset(
        {"write", "edit", "file_delete", "file_batch", "md_export"}
    )
    assert withheld_names(["web"]) == frozenset({"web_search", "web_fetch"})
    assert withheld_names(["run"]) == frozenset({"run"})
    assert "browser" not in withheld_names(["run"])
    assert "host" in withheld_names(["host"])
    assert withheld_names(["look"]) == frozenset(
        {"read", "file_list", "glob", "grep"}
    )
    assert withheld_names(["debate"]) == frozenset({"debate"})
    assert withheld_names(["folders"]) == frozenset({"folders"})
    assert withheld_names(["chats"]) == frozenset(
        {"search_conversations", "read_conversation"}
    )


def test_omit_unregisters_only_the_bound_set():
    registry = ToolRegistry()
    for name in ("run", "read", "delegate", "replan", "debate"):
        registry.register(SimpleNamespace(schema=SimpleNamespace(name=name)))
    token = bind_disabled_tool_groups(["delegate", "run", "nope"])
    try:
        omit_disabled_tools(registry)
    finally:
        reset_disabled_tool_groups(token)
    assert registry.get_optional("run") is None
    assert registry.get_optional("delegate") is None
    assert registry.get_optional("replan") is None
    assert registry.get_optional("read") is not None
    assert registry.get_optional("debate") is not None


def test_roster_skips_bound_names_before_register():
    plain = set(build_worker_registry().names)
    assert "web_search" in plain
    assert "write" in plain
    token = bind_disabled_tool_groups(["web", "files"])
    try:
        names = set(build_worker_registry().names)
    finally:
        reset_disabled_tool_groups(token)
    assert "web_search" not in names
    assert "web_fetch" not in names
    assert "write" not in names
    assert "read" in names


def test_ceo_roster_skips_delegate_and_ask_user():
    from agentcore.llm.profiles import default_turn_profiles
    from agentcore.runtime.events import EventSink
    from agentcore.runtime.skills import build_system_skill_registry
    from agentcore.tools.ceo_toolset import _assemble_ceo_toolset

    token = bind_disabled_tool_groups(["delegate", "ask_user"])
    try:
        _delegate, _debate, chat_tools = _assemble_ceo_toolset(
            llm=object(),
            sink=EventSink(),
            base_system_prompt="SYS",
            user_message="原始请求",
            history=[],
            worker_tools=ToolRegistry(),
            base_tool_context=None,  # type: ignore[arg-type]
            profiles=default_turn_profiles(),
            approval_gate=None,
            session_store=None,
            session_saver=None,
            session_loader=None,
            conversation_id="c",
            captain_run_id="cap",
            checkpoint_enabled=True,
            message_id="m",
            suspension_saver=None,
            suspension_deleter=None,
            backend_location="cloud",
            skill_registry=build_system_skill_registry(),
        )
    finally:
        reset_disabled_tool_groups(token)
    names = set(chat_tools.names)
    assert "delegate" not in names
    assert "replan" not in names
    assert "cancel_worker" not in names
    assert "ask_user" not in names
    assert "debate" in names


def test_debate_and_look_leave_the_table_when_their_switches_are_off():
    from agentcore.llm.profiles import default_turn_profiles
    from agentcore.runtime.events import EventSink
    from agentcore.runtime.skills import build_system_skill_registry
    from agentcore.tools.ceo_toolset import _assemble_ceo_toolset

    token = bind_disabled_tool_groups(["debate", "look", "folders"])
    try:
        _delegate, _debate, chat_tools = _assemble_ceo_toolset(
            llm=object(),
            sink=EventSink(),
            base_system_prompt="SYS",
            user_message="原始请求",
            history=[],
            worker_tools=ToolRegistry(),
            base_tool_context=None,  # type: ignore[arg-type]
            profiles=default_turn_profiles(),
            approval_gate=None,
            session_store=None,
            session_saver=None,
            session_loader=None,
            conversation_id="c",
            captain_run_id="cap",
            checkpoint_enabled=True,
            message_id="m",
            suspension_saver=None,
            suspension_deleter=None,
            backend_location="cloud",
            skill_registry=build_system_skill_registry(),
        )
        worker = set(build_worker_registry().names)
    finally:
        reset_disabled_tool_groups(token)
    names = set(chat_tools.names)
    assert "debate" not in names
    assert "folders" not in names
    assert "read" not in worker
    assert "grep" not in worker
    assert "write" in worker
    assert "file_list" not in worker


def test_chats_off_skips_wire_and_default_still_wires():
    from agentcore.runtime.resolve.prepare import _wire_conversation_log_tools

    off = build_worker_registry()
    token = bind_disabled_tool_groups(["chats"])
    try:
        _wire_conversation_log_tools(off, folder_id="F1")
    finally:
        reset_disabled_tool_groups(token)
    assert off.get_optional("search_conversations") is None
    assert off.get_optional("read_conversation") is None

    on = build_worker_registry()
    _wire_conversation_log_tools(on, folder_id="F1")
    assert on.get_optional("search_conversations") is not None
    assert on.get_optional("read_conversation") is not None
    assert getattr(on.get("search_conversations"), "folder_id", None) == "F1"


async def test_factory_catalog_omit_hides_official_rows():
    from agentcore.assembly.bind import (
        bind_omit_factory_catalog,
        reset_omit_factory_catalog,
    )
    from agentcore.runtime.context.consult_sources import SkillConsultSource
    from agentcore.runtime.skills import build_system_skill_registry

    source = SkillConsultSource(
        registry=build_system_skill_registry(),
        tool_names={"read"},
        audience="ceo",
    )
    token = bind_omit_factory_catalog(True)
    try:
        assert await source.list_directory("u") == []
        assert await source.fetch_by_name("u", "page_ui") is None
    finally:
        reset_omit_factory_catalog(token)
    listed = await source.list_directory("u")
    assert any(row.name == "page_ui" for row in listed)


async def test_consult_stays_off_the_table_when_the_directory_is_empty(monkeypatch):
    from agentcore.runtime.skills import build_system_skill_registry
    from agentcore.tools.ceo_toolset import _wire_consult_if_entries

    async def empty(self, user_id):  # noqa: ANN001
        del self, user_id
        return []

    monkeypatch.setattr(
        "agentcore.runtime.context.consult_sources.MergedConsultSource.list_directory",
        empty,
    )
    registry = ToolRegistry()
    wired = await _wire_consult_if_entries(
        registry,
        skill_registry=build_system_skill_registry(),
        folder_id=None,
        user_id="u",
        skill_audience="ceo",
    )
    assert wired is False
    assert registry.get_optional("consult") is None


async def test_create_pins_the_snapshotted_assembly(monkeypatch):
    written, seed = await _create_with_tools(monkeypatch, account=["web"])
    assert written["assembly_id"] == "sys-default"
    assert "disabled_tools" not in written
    assert seed.await_count == 0


async def _create_with_tools(monkeypatch, *, account: list[str], body=None):
    from datetime import datetime
    from unittest.mock import AsyncMock

    from agentcore.api.routes.conversations import crud
    from agentcore.api.schemas import CreateConversationRequest

    written: dict = {}
    conv = SimpleNamespace(
        id="c-new",
        title="",
        updated_at=datetime.now(),
        created_at=datetime.now(),
        message_count=0,
        folder_id=None,
        local_container_root_id=None,
        pinned=False,
        archived=False,
        permission_axes={"boundary": "folder"},
        deep_research_auto=False,
        assembly_id="sys-default",
        compaction_summary=None,
        compacted_through=None,
    )

    class _Repo:
        _session = object()

        async def create(self, **kwargs):
            written.update(kwargs)
            conv.assembly_id = kwargs.get("assembly_id")
            return conv

    seed = AsyncMock(return_value=list(account))
    monkeypatch.setattr(
        "agentcore.tools.switchboard.disabled_tools_for_user", seed
    )
    monkeypatch.setattr(
        "agentcore.api.routes.conversations.crud.default_permission_axes_for_user",
        AsyncMock(
            return_value=SimpleNamespace(to_dict=lambda: {"boundary": "folder"})
        ),
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.snapshot_default_profile_id",
        AsyncMock(return_value="sys-default"),
    )
    await crud.create_conversation(
        body if body is not None else CreateConversationRequest(),
        SimpleNamespace(user_id="u1"),
        repo=_Repo(),
        folder_repo=SimpleNamespace(),
    )
    return written, seed


def test_omit_is_a_noop_when_nothing_is_bound():
    registry = ToolRegistry()
    registry.register(SimpleNamespace(schema=SimpleNamespace(name="run")))
    omit_disabled_tools(registry)
    assert registry.get_optional("run") is not None
