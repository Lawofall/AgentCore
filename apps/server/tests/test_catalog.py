"""Unit tests for the capability catalog (tools.catalog) and the CEO prompt composer.

These are the GUARD the catalog docstring promises: ``build_capability_catalog`` reads
the CEO-only orchestration tools' schemas off uninitialised instances (their ``schema``
is a pure static descriptor). If a future schema starts touching instance state, the
``name``/``description``/``parameters`` assertions here fail loudly instead of the
endpoint silently serving half-built metadata. Also pins the CEO/worker reach annotation
and the single-source prompt composer's 按需目录 gating.
"""

from agentcore.runtime.resolve.prompt import assemble_system_prompt, compose_ceo_chat_prompt
from agentcore.runtime.skills import build_system_skill_registry
from agentcore.tools.catalog import (
    AVAILABLE_TO_CEO,
    AVAILABLE_TO_WORKER,
    build_capability_catalog,
)

# What the CEO holds beyond the read-only built-ins (mirrors pipeline._assemble_ceo_toolset).
# ``consult`` is AUDIENCE_BOTH — asserted separately.
_CEO_ORCHESTRATION = {
    "delegate",
    "replan",
    "debate",
    "list_folders",
    "resolve_folder",
    "create_folder",
    "ask_user",
}
# Worker-only collaboration channel. Write / execute built-ins are CEO+worker.
_WORKER_ONLY_COLLAB = {
    "escalate",
    "handoff",
}
_CEO_AND_WORKER_MUTATION = {
    "file_write",
    "file_append",
    "str_replace",
    "file_delete",
    "file_move",
    "file_copy",
    "mkdir",
    "file_batch",
    "md_to_docx",
    "md_to_pdf",
    "archive_extract",
    "archive_create",
    "download_url",
    "run",
}


def _by_name() -> dict[str, object]:
    return {e.schema.name: e for e in build_capability_catalog()}


def test_every_catalog_tool_has_usable_metadata():
    """Guards the static-schema read: no half-built schema slips into the catalog."""
    catalog = build_capability_catalog()
    assert catalog, "catalog must not be empty"
    for entry in catalog:
        schema = entry.schema
        assert schema.name and isinstance(schema.name, str)
        assert schema.description and isinstance(schema.description, str)
        assert isinstance(schema.parameters, dict)
        assert schema.parameters.get("type") == "object"
        assert entry.available_to, f"{schema.name} must declare available_to"
        assert set(entry.available_to) <= {AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER}


def test_catalog_has_no_duplicate_tools():
    names = [e.schema.name for e in build_capability_catalog()]
    assert len(names) == len(set(names))


def test_ceo_orchestration_tools_are_present_and_ceo_only():
    """The drift the old GET /tools had: delegate/replan/consult/ask_user missing."""
    entries = _by_name()
    for name in _CEO_ORCHESTRATION:
        assert name in entries, f"{name} missing from catalog"
        assert entries[name].available_to == (AVAILABLE_TO_CEO,)


def test_consult_is_shared_between_ceo_and_worker():
    entries = _by_name()
    assert "consult" in entries
    assert set(entries["consult"].available_to) == {
        AVAILABLE_TO_CEO,
        AVAILABLE_TO_WORKER,
    }


def test_read_only_builtins_are_shared_with_ceo():
    entries = _by_name()
    # Read/retrieval built-ins the coordinator looks with.
    for name in (
        "web_search",
        "read_url",
        "file_read",
        "file_list",
        "glob",
        "grep",
        "code_search",
        "code_diagnostics",
        "git",
    ):
        assert name in entries
        assert set(entries[name].available_to) == {AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER}


def test_notify_and_conversation_logs_are_ceo_and_worker():
    entries = _by_name()
    for name in ("desktop_notify", "search_conversations", "read_conversation"):
        assert name in entries, f"{name} missing from catalog"
        assert set(entries[name].available_to) == {
            AVAILABLE_TO_CEO,
            AVAILABLE_TO_WORKER,
        }


def test_escalate_and_handoff_are_worker_only():
    entries = _by_name()
    for name in _WORKER_ONLY_COLLAB:
        assert name in entries, f"{name} missing from catalog"
        assert entries[name].available_to == (AVAILABLE_TO_WORKER,)


def test_mutation_and_execution_are_shared_with_ceo():
    entries = _by_name()
    for name in _CEO_AND_WORKER_MUTATION:
        assert name in entries, f"{name} missing from catalog"
        assert set(entries[name].available_to) == {
            AVAILABLE_TO_CEO,
            AVAILABLE_TO_WORKER,
        }


def test_ceo_prompt_lists_skill_directory_when_ask_user_wired():
    """compose_ceo_chat_prompt is the single source for runtime + 能力图鉴; its 按需目录
    must gate asking_the_user on ask_user being wired (the live-user invariant)."""
    registry = build_system_skill_registry()
    base = assemble_system_prompt()

    # The directory renders one「- {name}：{summary}」line per visible skill; match that
    # marker (not the bare name, which also appears in the CEO core hint's prose).
    with_ask = compose_ceo_chat_prompt(
        base,
        skill_registry=registry,
        ceo_tool_names={"delegate", "consult", "ask_user"},
    )
    assert "按需目录" in with_ask
    assert "- asking_the_user：" in with_ask
    assert "- team_orchestration_advanced：" in with_ask

    without_ask = compose_ceo_chat_prompt(
        base,
        skill_registry=registry,
        ceo_tool_names={"delegate", "consult"},
    )
    # asking_the_user requires the ask_user tool — its directory line is gated out…
    assert "- asking_the_user：" not in without_ask
    assert "- ask_user_kickoff：" not in without_ask
    # …but the un-gated advanced skills still list.
    assert "- team_orchestration_advanced：" in without_ask
