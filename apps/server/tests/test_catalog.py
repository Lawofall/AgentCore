"""Unit tests for the capability catalog (tools.catalog) and the CEO prompt composer.

These are the GUARD the catalog docstring promises: ``build_capability_catalog`` reads
the CEO-only orchestration tools' schemas off uninitialised instances (their ``schema``
is a pure static descriptor). If a future schema starts touching instance state, the
``name``/``description``/``parameters`` assertions here fail loudly instead of the
endpoint silently serving half-built metadata. Also pins the CEO/worker reach annotation
and the single-source prompt composer's 按需目录 gating.
"""

from agentcore.core.types import ToolFace
from agentcore.runtime.context.consultable import ConsultDirectoryEntry
from agentcore.runtime.resolve.prompt import assemble_system_prompt, compose_ceo_chat_prompt
from agentcore.runtime.resolve.prompt.compose import render_on_demand_directory
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
    "folders",
    "ask_user",
}
# Worker-only collaboration channel. Write / execute built-ins are CEO+worker.
_WORKER_ONLY_COLLAB = {
    "escalate",
    "handoff",
}
_CEO_AND_WORKER_MUTATION = {
    "write",
    "edit",
    "file_delete",
    "file_batch",
    "md_export",
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
        assert isinstance(schema.face, ToolFace)
        assert isinstance(entry.resident, bool)
        assert isinstance(entry.summary, str)
        assert entry.available_to, f"{schema.name} must declare available_to"
        assert set(entry.available_to) <= {AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER}
        summary = entry.summary.strip()
        assert summary, f"{schema.name} needs catalog_summary for the toolbox shelf"
        assert summary != schema.name, schema.name
        assert len(summary) <= 80, (schema.name, summary)
        blurb = entry.blurb.strip()
        assert blurb, f"{schema.name} needs blurb for the toolbox shelf"
        assert blurb != summary, schema.name
        assert blurb != schema.name, schema.name
        assert blurb != schema.description.strip(), schema.name
        assert len(blurb) <= 80, (schema.name, blurb)


def test_catalog_has_no_duplicate_tools():
    names = [e.schema.name for e in build_capability_catalog()]
    assert len(names) == len(set(names))


def test_factory_tools_are_opening_resident():
    from agentcore.tools.registration import (
        declared_tool_name,
        declared_tools,
        tool_registration,
    )

    by_name = {
        declared_tool_name(cls): tool_registration(cls) for cls in declared_tools()
    }
    for name in (
        "host",
        "browser",
        "debate",
        "md_export",
        "file_batch",
    ):
        assert by_name[name].resident is True, name


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
        "web_fetch",
        "read",
        "file_list",
        "glob",
        "grep",
    ):
        assert name in entries
        assert set(entries[name].available_to) == {AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER}


def test_conversation_logs_are_ceo_and_worker():
    entries = _by_name()
    assert "desktop_notify" not in entries
    for name in ("search_conversations", "read_conversation"):
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


def test_ceo_prompt_skill_directory_lists_ungated():
    """compose_ceo_chat_prompt 按需目录列出出厂系统 Skill；不跟 run 工具显隐。"""
    registry = build_system_skill_registry()
    base = assemble_system_prompt()

    with_run = compose_ceo_chat_prompt(
        base,
        skill_registry=registry,
        ceo_tool_names={"delegate", "consult", "run"},
    )
    assert "按需目录" in with_run
    assert "编排：" not in with_run
    assert 'consult("product_help")' in with_run

    without_run = compose_ceo_chat_prompt(
        base,
        skill_registry=registry,
        ceo_tool_names={"delegate", "consult"},
    )
    assert 'consult("product_help")' in without_run


def test_tool_blurbs_stay_off_directory_and_prompt():
    catalog = build_capability_catalog()
    registry = build_system_skill_registry()
    base = assemble_system_prompt()
    ceo_tool_names = {
        entry.schema.name for entry in catalog if AVAILABLE_TO_CEO in entry.available_to
    }
    ceo = compose_ceo_chat_prompt(
        base,
        skill_registry=registry,
        ceo_tool_names=ceo_tool_names,
    )
    directory = render_on_demand_directory(
        [
            ConsultDirectoryEntry(
                name=entry.schema.name,
                summary=entry.summary,
                section="tool",
                face=entry.schema.face.value,
            )
            for entry in catalog
            if not entry.resident
        ]
    )
    for entry in catalog:
        blurb = entry.blurb.strip()
        assert blurb not in ceo, entry.schema.name
        assert blurb not in directory, entry.schema.name


# Display face ≠ ceo_orchestration surface. Pin so Folder tools
# cannot slide back into the orchestration dumpster.
_CATALOG_FACE: dict[str, ToolFace] = {
    "delegate": ToolFace.ORCHESTRATION,
    "replan": ToolFace.ORCHESTRATION,
    "debate": ToolFace.ORCHESTRATION,
    "consult": ToolFace.ORCHESTRATION,
    "ask_user": ToolFace.ORCHESTRATION,
    "escalate": ToolFace.ORCHESTRATION,
    "handoff": ToolFace.ORCHESTRATION,
    "folders": ToolFace.FOLDER,
}


def test_capability_catalog_omits_retired_update_folder_profile():
    catalog = build_capability_catalog()
    names = {e.schema.name for e in catalog}
    assert "update_folder_profile" not in names
    assert "remember" not in names
    assert "code_search" not in names
    assert "project_shell" not in names
    assert all(e.summary != "更新文件夹画像" for e in catalog)


def test_catalog_faces_are_not_an_orchestration_dumpster():
    by_name = {e.schema.name: e.schema.face for e in build_capability_catalog()}
    for name, face in _CATALOG_FACE.items():
        assert by_name[name] is face, name
    orchestration = {n for n, f in by_name.items() if f is ToolFace.ORCHESTRATION}
    folder = {n for n, f in by_name.items() if f is ToolFace.FOLDER}
    assert orchestration == {n for n, f in _CATALOG_FACE.items() if f is ToolFace.ORCHESTRATION}
    assert folder == {n for n, f in _CATALOG_FACE.items() if f is ToolFace.FOLDER}


def test_on_demand_directory_splits_folder_off_orchestration():
    out = render_on_demand_directory(
        [
            ConsultDirectoryEntry(
                name="folders",
                summary="列出或解析云文件夹",
                section="tool",
                face=ToolFace.FOLDER.value,
            ),
            ConsultDirectoryEntry(
                name="delegate",
                summary="派活",
                section="tool",
                face=ToolFace.ORCHESTRATION.value,
            ),
        ]
    )
    assert "文件夹：" in out
    assert "白板：" not in out
    assert "表格：" not in out
    assert "文档：" not in out
    assert "编排：" in out
    assert out.index("文件夹：") < out.index('consult("folders")') < out.index("编排：")
    assert out.index("编排：") < out.index('consult("delegate")')
