"""Snapshot lock: builtin / worker / CEO registries + capability catalog.

Written BEFORE the declarative registration convergence so a refactor that
changes roster, order, audience, or approval fails loudly. New tools update
this snapshot alongside their declaration.
"""

from __future__ import annotations

from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.runtime.always_confirm import requires_always_confirm
from agentcore.tools.builtin import (
    approval_class_tool_names,
    build_builtin_registry,
    build_ceo_tool_registry,
    build_worker_registry,
    delegation_grantable_tool_names,
    file_mutation_tool_names,
    per_call_tool_names,
)
from agentcore.tools.catalog import (
    AVAILABLE_TO_CEO,
    AVAILABLE_TO_WORKER,
    build_capability_catalog,
)

# Ordered names — registration order is part of the public surface (catalog /
# OpenAI defs). Keep in lockstep with tools.registration.DECLARED_TOOLS.
_BUILTIN_ORDER = [
    "web_search",
    "web_fetch",
    "file_read",
    "file_write",
    "file_append",
    "str_replace",
    "file_list",
    "glob",
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
    "grep",
    "code_search",
    "code_diagnostics",
    "git",
    "run",
]

# Host face is host_class — only appears when desktop_online=True (not default roster).
_HOST_ORDER = [
    "host",
]

# C1: CEO+worker NEVER · desktop_online_class（仅 desktop_online 装配；目录仍常挂）
_DESKTOP_ONLINE_ORDER = [
    "external_mount_readonly",
]

# CEO+worker GRANTABLE：单一 ``browser``（builtin · browser_class · include_browser 闸）
_BROWSER_CEO_ORDER = [
    "browser",
]

_WORKER_ONLY_ORDER = [
    "escalate",
    "handoff",
    "desktop_notify",
]

# manual_wire conversation log tools: catalog-advertised, not in default
# ``build_worker_registry`` — wired after assemble (CEO + worker).
_WORKER_GATED_ORDER = [
    "search_conversations",
    "read_conversation",
]

_CEO_BUILTIN_ORDER = list(_BUILTIN_ORDER)

_CATALOG_ORCHESTRATION_ORDER = [
    "delegate",
    "replan",
    "debate",
    "consult",
    "list_folders",
    "resolve_folder",
    "create_folder",
    "delete_folder",
    "list_folder_dir",
    "read_folder_file",
    "remember",
    "update_folder_profile",
    "ask_user",
    "read_image",
    "board_ops",
    "board_read",
]

_CATALOG_AVAILABLE_TO: dict[str, tuple[str, ...]] = {
    # Shared read/retrieval built-ins
    "web_search": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "web_fetch": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "file_read": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "file_list": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "glob": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "grep": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "code_search": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "code_diagnostics": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "git": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    # Write / execute: CEO + worker (same GRANTABLE ApprovalGate)
    "file_write": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "file_append": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "str_replace": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "file_delete": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "file_move": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "file_copy": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "mkdir": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "file_batch": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "md_to_docx": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "md_to_pdf": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "archive_extract": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "archive_create": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "download_url": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "run": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "host": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "external_mount_readonly": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "escalate": (AVAILABLE_TO_WORKER,),
    "handoff": (AVAILABLE_TO_WORKER,),
    "desktop_notify": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "search_conversations": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "read_conversation": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    # CEO orchestration (catalog advertise)
    "delegate": (AVAILABLE_TO_CEO,),
    "replan": (AVAILABLE_TO_CEO,),
    "debate": (AVAILABLE_TO_CEO,),
    "consult": (AVAILABLE_TO_CEO, AVAILABLE_TO_WORKER),
    "list_folders": (AVAILABLE_TO_CEO,),
    "resolve_folder": (AVAILABLE_TO_CEO,),
    "create_folder": (AVAILABLE_TO_CEO,),
    "delete_folder": (AVAILABLE_TO_CEO,),
    "list_folder_dir": (AVAILABLE_TO_CEO,),
    "read_folder_file": (AVAILABLE_TO_CEO,),
    "remember": (AVAILABLE_TO_CEO,),
    "update_folder_profile": (AVAILABLE_TO_CEO,),
    "ask_user": (AVAILABLE_TO_CEO,),
    "read_image": (AVAILABLE_TO_CEO,),
    "board_ops": (AVAILABLE_TO_CEO,),
    "board_read": (AVAILABLE_TO_CEO,),
}


def test_tool_registry_builtin_order_and_roster():
    names = [s.name for s in build_builtin_registry().list_all()]
    assert names == _BUILTIN_ORDER


def test_tool_registry_worker_default_order_and_roster():
    names = [s.name for s in build_worker_registry().list_all()]
    assert names == _BUILTIN_ORDER + _WORKER_ONLY_ORDER


def test_tool_registry_ceo_builtin_order_and_roster():
    names = [s.name for s in build_ceo_tool_registry().list_all()]
    assert names == _CEO_BUILTIN_ORDER


def test_tool_registry_builtin_includes_navigate_when_include_browser():
    names = [s.name for s in build_builtin_registry(include_browser=True).list_all()]
    assert names == _BUILTIN_ORDER + _BROWSER_CEO_ORDER


def test_tool_registry_ceo_includes_navigate_when_include_browser():
    names = [s.name for s in build_ceo_tool_registry(include_browser=True).list_all()]
    assert names == _CEO_BUILTIN_ORDER + _BROWSER_CEO_ORDER


def test_browser_tools_ceo_holds_interactive_screenshot_worker_only():
    from agentcore.tools.registration import (
        AUDIENCE_BOTH,
        declared_tool_name,
        declared_tools,
        tool_registration,
    )

    by_name = {
        declared_tool_name(cls): tool_registration(cls)
        for cls in declared_tools()
    }
    for name in _BROWSER_CEO_ORDER:
        reg = by_name[name]
        assert reg.audience == AUDIENCE_BOTH, name
        assert reg.surface.value == "builtin", name
        assert reg.browser_class and reg.execution_class, name
    assert "browser_screenshot" not in by_name


def test_tool_registry_builtin_approvals_snapshot():
    approvals = {s.name: s.approval for s in build_builtin_registry().list_all()}
    never = {
        "web_search",
        "web_fetch",
        "file_read",
        "file_list",
        "glob",
        "grep",
        "code_search",
        "code_diagnostics",
        "git",
    }
    grantable = set(_BUILTIN_ORDER) - never
    for name in never:
        assert approvals[name] is ToolApproval.NEVER
    for name in grantable:
        assert approvals[name] is ToolApproval.GRANTABLE


def test_tool_registry_grant_sets_snapshot():
    assert file_mutation_tool_names() == frozenset(
        {
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
        }
    )
    assert approval_class_tool_names() == file_mutation_tool_names() | frozenset({"git"})
    assert delegation_grantable_tool_names() == approval_class_tool_names() | frozenset(
        {
            "run",
            # L3 团队浏览器 (D11): execution_class → covered by a delegation grant.
            "browser",
        }
    )
    assert per_call_tool_names() == frozenset()


def test_tool_registry_worker_with_host_order():
    names = [s.name for s in build_worker_registry(desktop_online=True).list_all()]
    assert names == (
        _BUILTIN_ORDER
        + _HOST_ORDER
        + _DESKTOP_ONLINE_ORDER
        + _WORKER_ONLY_ORDER
    )


def test_catalog_order_and_available_to_snapshot():
    catalog = build_capability_catalog()
    names = [e.schema.name for e in catalog]
    assert names == (
        _BUILTIN_ORDER
        + _HOST_ORDER
        + _DESKTOP_ONLINE_ORDER
        + _WORKER_ONLY_ORDER
        + _WORKER_GATED_ORDER
        + _CATALOG_ORCHESTRATION_ORDER
    )
    by_name = {e.schema.name: e for e in catalog}
    assert set(by_name) == set(_CATALOG_AVAILABLE_TO)
    for name, expected in _CATALOG_AVAILABLE_TO.items():
        assert by_name[name].available_to == expected, name


def test_catalog_categories_present():
    """Every catalog entry keeps a real ToolCategory (governance UI)."""
    for entry in build_capability_catalog():
        assert isinstance(entry.schema.category, ToolCategory)
        assert isinstance(entry.schema.approval, ToolApproval)


def test_tool_registry_declarations_cover_roster():
    """Every declared class has ``registration``; CEO write/execute/browser are GRANTABLE.
    """
    from agentcore.tools.registration import (
        AUDIENCE_CEO,
        CeoWire,
        ToolSurface,
        declared_tool_name,
        declared_tools,
        tool_registration,
    )

    _ceo_grantable = frozenset(_BUILTIN_ORDER) - {
        "web_search",
        "web_fetch",
        "file_read",
        "file_list",
        "glob",
        "grep",
        "code_search",
        "code_diagnostics",
        "git",
    } | frozenset(_BROWSER_CEO_ORDER)

    declared = declared_tools()
    assert declared, "DECLARED_TOOLS must not be empty"
    names = [declared_tool_name(cls) for cls in declared]
    assert len(names) == len(set(names)), f"duplicate declared names: {names}"
    _retired_host_names = {
        "host_ping",
        "host_info",
        "host_audio_devices",
        "host_storage",
        "host_power",
        "host_network_summary",
        "host_apps",
        "host_os_log_summary",
        "host_shell",
        "host_open_settings",
        "host_audio_set_default",
        "host_service_restart",
        "host_package_install",
    }
    assert not (_retired_host_names & set(names)), _retired_host_names & set(names)
    assert "host" in names

    for cls in declared_tools(surface=ToolSurface.BUILTIN):
        reg = tool_registration(cls)
        if AUDIENCE_CEO in reg.audience:
            schema = cls().schema if not reg.needs_location else cls(location=None).schema
            if schema.name in _ceo_grantable:
                assert schema.approval is ToolApproval.GRANTABLE, schema.name
                if schema.name == "browser":
                    assert reg.browser_class, schema.name
                    assert reg.execution_class, schema.name
            else:
                assert schema.approval is ToolApproval.NEVER, schema.name
                if schema.name == "host":
                    assert reg.host_class, schema.name
                    assert not reg.execution_class, schema.name

    # CEO orchestration wire gates (construction stays in prepare / ceo_surface / board).
    wire_by_name = {
        declared_tool_name(cls): tool_registration(cls).ceo_wire
        for cls in declared_tools(surface=ToolSurface.CEO_ORCHESTRATION)
    }
    assert wire_by_name == {
        "delegate": CeoWire.ALWAYS,
        "replan": CeoWire.COORDINATION,
        "debate": CeoWire.ALWAYS,
        "consult": CeoWire.CONSULT,
        "list_folders": CeoWire.ALWAYS,
        "resolve_folder": CeoWire.ALWAYS,
        "create_folder": CeoWire.ALWAYS,
        "delete_folder": CeoWire.ALWAYS,
        "list_folder_dir": CeoWire.ALWAYS,
        "read_folder_file": CeoWire.ALWAYS,
        "remember": CeoWire.MEMORY,
        "update_folder_profile": CeoWire.MEMORY,
        "ask_user": CeoWire.CHECKPOINT,
        "read_image": CeoWire.ALWAYS,
        "board_ops": CeoWire.BOARD,
        "board_read": CeoWire.BOARD,
    }

    # 指挥面同样「CEO 永不持 GRANTABLE」，唯一破例是 delete_folder：删文件夹每次都要
    # 用户点确认卡（恒确认，见 runtime.always_confirm），破例本身钉在这里可审。
    # 走目录取 schema——delegate / debate 是重依赖手工装配，直接 cls() 构不出来。
    catalog_approvals = {e.schema.name: e.schema.approval for e in build_capability_catalog()}
    for name in _CATALOG_ORCHESTRATION_ORDER:
        expected = (
            ToolApproval.GRANTABLE if name == "delete_folder" else ToolApproval.NEVER
        )
        assert catalog_approvals[name] is expected, name
    assert requires_always_confirm("delete_folder", {}) is True
