"""Unit tests for the built-in tool catalog (the single-source registry).

``build_builtin_registry`` is the one place that declares "what tools ship with
the platform": the chat pipeline builds the worker toolset from it and the
``GET /tools`` catalog serializes it. These tests pin the roster and the
governance flags the UI renders, and guard that the CEO-only ``delegate``
primitive never leaks into the general catalog.
"""

from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.tools.builtin import (
    build_builtin_registry,
    build_ceo_tool_registry,
    build_worker_registry,
    file_mutation_tool_names,
)
from agentcore.tools.builtin.terminal import TerminalTool

_EXPECTED_NAMES = {
    "web_search",
    "read_url",
    "download_url",
    "file_read",
    "file_write",
    "file_append",
    "str_replace",
    "write_section",
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
    "grep",
    "code_search",
    "code_diagnostics",
    "git",
    "test_run",
    "code_execute",
    "terminal",
}

# The CEO chat agent is a COORDINATOR: it directly holds only the read/retrieval
# tools and delegates every production/mutation tool to a worker (协调者 CEO).
# test_run is NOT here — it is a code-execution tool (GRANTABLE), so it is worker-only
# like code_execute (it runs arbitrary project code through the same sandbox chain).
_CEO_READONLY_NAMES = {
    "web_search",
    "read_url",
    "file_read",
    "file_list",
    "glob",
    "grep",
    "code_search",
    "code_diagnostics",
    "git",
    "terminal",
}
_DELEGATED_MUTATION_NAMES = {
    "file_write",
    "file_append",
    "str_replace",
    "write_section",
    "file_delete",
    "file_move",
    "file_copy",
    "mkdir",
    "file_batch",
    "md_to_docx",
    "md_to_pdf",
    "archive_extract",
    "download_url",
    "code_execute",
}


def test_registry_lists_exactly_the_builtin_tools():
    names = {schema.name for schema in build_builtin_registry().list_all()}
    assert names == _EXPECTED_NAMES


def test_registry_excludes_ceo_only_delegate():
    names = {schema.name for schema in build_builtin_registry().list_all()}
    assert "delegate" not in names


# Worker-only orchestration primitives: present in the worker toolset, but NOT in the
# builtin catalog (GET /tools) nor the CEO's own toolset. `escalate` is the upward
# channel (worker → CEO); `post_note` / `read_notes` / `amend_note` are the sideways
# broadcast / read / 改写·作废 channels to 并行队友 (worker ↔ 团队便签墙, §2.2 通 + §2.4
# 变·worker 的「拉」); `handoff` is the terminal 完工交接简报 submission (结论 / 关键要点 /
# 关键假设 / 建议下一步, read off the call args — never parsed out of prose). All stay
# where they belong instead of leaking platform-wide.
_WORKER_ONLY_NAMES = {
    "escalate",
    "post_note",
    "read_notes",
    "amend_note",
    "handoff",
    "desktop_notify",
}


def test_worker_registry_adds_worker_only_tools_without_leaking_them():
    worker = {s.name for s in build_worker_registry().list_all()}
    builtin = {s.name for s in build_builtin_registry().list_all()}
    ceo = {s.name for s in build_ceo_tool_registry().list_all()}
    assert worker >= _WORKER_ONLY_NAMES
    # builtins + the worker-only primitives, nothing else.
    assert worker == _EXPECTED_NAMES | _WORKER_ONLY_NAMES
    assert builtin.isdisjoint(_WORKER_ONLY_NAMES)
    assert ceo.isdisjoint(_WORKER_ONLY_NAMES)


def test_write_and_exec_tools_are_grantable():
    approvals = {s.name: s.approval for s in build_builtin_registry().list_all()}
    assert approvals["file_write"] is ToolApproval.GRANTABLE
    assert approvals["file_append"] is ToolApproval.GRANTABLE
    assert approvals["str_replace"] is ToolApproval.GRANTABLE
    assert approvals["code_execute"] is ToolApproval.GRANTABLE
    # test_run runs project code through the same sandbox chain as code_execute, so it
    # carries the same execution-class consent — never NEVER (the P0 it slipped through).
    assert approvals["test_run"] is ToolApproval.GRANTABLE
    # Destructive / mutating file ops require the same consent as writes.
    assert approvals["file_delete"] is ToolApproval.GRANTABLE
    assert approvals["file_move"] is ToolApproval.GRANTABLE
    assert approvals["file_copy"] is ToolApproval.GRANTABLE
    assert approvals["mkdir"] is ToolApproval.GRANTABLE
    assert approvals["file_batch"] is ToolApproval.GRANTABLE
    assert approvals["md_to_docx"] is ToolApproval.GRANTABLE
    assert approvals["md_to_pdf"] is ToolApproval.GRANTABLE
    assert approvals["archive_extract"] is ToolApproval.GRANTABLE
    # Read-only tools auto-run (no approval prompt).
    assert approvals["file_read"] is ToolApproval.NEVER
    assert approvals["file_list"] is ToolApproval.NEVER
    assert approvals["glob"] is ToolApproval.NEVER
    assert approvals["web_search"] is ToolApproval.NEVER


def test_file_mutation_class_is_grantable_filesystem_without_code_execute():
    # The「本轮内允许所有文件改动」class = GRANTABLE ∩ FILESYSTEM, so it covers the
    # file-edit tools but NOT code_execute (EXECUTION, higher-risk → its own gate).
    # Pinned so a future tool can't silently widen or narrow what one click grants.
    names = file_mutation_tool_names()
    assert names == {
        "file_write",
        "file_append",
        "str_replace",
        "write_section",
        "file_delete",
        "file_move",
        "file_copy",
        "mkdir",
        "file_batch",
        "md_to_docx",
        "md_to_pdf",
        "archive_extract",
        "download_url",
    }
    assert "code_execute" not in names
    # Exactly the delegated mutation set minus code_execute (stays in lockstep).
    assert names == _DELEGATED_MUTATION_NAMES - {"code_execute"}


def test_code_execute_description_does_not_overpromise_sandbox():
    # Location-aware wording: catalog (no location) must not claim「用户自己的机器」;
    # local registry must; server registry must name the cloud sandbox.
    from agentcore.tools.builtin.code_execute import CodeExecuteTool, code_execute_description

    catalog = {s.name: s for s in build_builtin_registry().list_all()}
    assert "用户自己的机器" not in catalog["code_execute"].description
    assert "可能【直接运行" not in catalog["code_execute"].description

    assert "用户本机" in code_execute_description("local")
    assert "云端沙箱" in code_execute_description("server")
    assert "用户本机" in CodeExecuteTool(location="local").schema.description
    assert "云端沙箱" in CodeExecuteTool(location="server").schema.description


def test_code_execute_description_routes_source_dump_to_file_read():
    from agentcore.tools.builtin.code_execute import code_execute_description
    from agentcore.tools.builtin.file_ops.read import FileReadTool

    ce = code_execute_description("local")
    assert "file_read" in ce
    assert "dump" in ce
    assert "禁止" in ce
    assert "grep" in ce
    assert "正则扫描" in ce
    # Positive path lives on file_read so the skipped tool still names itself.
    fr = FileReadTool().schema.description
    assert "code_execute" in fr
    assert "dump" in fr


def test_code_execute_description_routes_long_running_to_terminal():
    # Long-lived servers must not be waited on via code_execute (60s timeout trap).
    from agentcore.tools.builtin.code_execute import code_execute_description
    from agentcore.tools.builtin.terminal import TerminalTool

    ce = code_execute_description("local")
    assert "禁止" in ce
    assert "terminal" in ce
    assert "npm run dev" in ce
    # Bounded verify is the home for slow project checks — not code_execute.
    assert "test_run" in ce
    assert "npm run build" in ce  # mentioned as forbidden / redirect, not promoted
    # Local short-CLI guidance: prefer node/javascript over bash shell.
    assert "language=javascript" in ce
    assert "WSL" in ce

    td = TerminalTool().schema.description
    assert "禁止改走 code_execute" in td
    assert "host(action=shell)" in td
    assert "wait_for" in td
    assert "code_execute" in td  # short commands still pointed there
    assert "CEO" in td
    assert "仅本地" not in td
    assert "仅本地" not in TerminalTool(location="server").schema.description


def test_ceo_registry_holds_terminal_with_execution_class():
    assert "terminal" in {s.name for s in build_ceo_tool_registry().list_all()}
    assert "terminal" in {
        s.name for s in build_ceo_tool_registry(backend_location="server").list_all()
    }
    assert "terminal" in {
        s.name for s in build_ceo_tool_registry(backend_location="local").list_all()
    }
    assert "terminal" not in {
        s.name
        for s in build_ceo_tool_registry(include_execution_tools=False).list_all()
    }
    assert (
        build_ceo_tool_registry(backend_location="server").get("terminal").schema.approval
        is TerminalTool().schema.approval
    )


def test_code_execute_description_server_omits_local_wsl_hint():
    from agentcore.tools.builtin.code_execute import code_execute_description

    server = code_execute_description("server")
    assert "WSL" not in server
    # Server copy still steers project verify to test_run (capability, not WSL hint).
    assert "test_run" in server
    assert "npx tsc" not in server


def test_read_url_description_does_not_overclaim_completeness():
    # read_url caps extracted text at max_chars (default 8000), so a long page is
    # truncated — the description must disclose that and not promise the "complete"
    # body, or the model may state it read the whole page when it saw only the head.
    schemas = {s.name: s for s in build_builtin_registry().list_all()}
    desc = schemas["read_url"].description
    assert "max_chars" in desc  # truncation is disclosed
    assert "完整正文" not in desc  # no blanket "complete body" claim
    # 可信优先：成稿挂 #rN 须先深读；仅 search 不可挂号
    assert "#rN" in desc
    assert "深读" in desc
    assert "search" in desc


def test_ceo_registry_is_read_only_subset():
    # 协调者 CEO: it looks + answers directly, so its direct toolset is exactly the
    # read/retrieval tools — no production/mutation tool leaks into the CEO's hands.
    names = {schema.name for schema in build_ceo_tool_registry().list_all()}
    assert names == _CEO_READONLY_NAMES


def test_ceo_registry_excludes_every_mutation_tool():
    names = {schema.name for schema in build_ceo_tool_registry().list_all()}
    assert names.isdisjoint(_DELEGATED_MUTATION_NAMES)


def test_ceo_registry_holds_only_auto_run_tools():
    # The split is by approval level: the CEO keeps only NEVER tools (auto-run, no
    # consent), while every GRANTABLE (env-mutating) tool is delegated — **except**
    # Host P3 ``host`` / browser ``browser`` (gated; not in the
    # default no-desktop / no-browser set).
    schemas = build_ceo_tool_registry().list_all()
    assert schemas, "CEO must retain its read/retrieval tools"
    assert all(s.approval is ToolApproval.NEVER for s in schemas)


def test_ceo_registry_host_when_desktop_online():
    schemas = {
        s.name: s for s in build_ceo_tool_registry(desktop_online=True).list_all()
    }
    assert "host" in schemas
    assert schemas["host"].approval is ToolApproval.NEVER
    # Retired 13 names stay off the CEO roster.
    for retired in (
        "host_ping",
        "host_shell",
        "host_open_settings",
        "host_package_install",
    ):
        assert retired not in schemas
    # All CEO tools remain NEVER (browser GRANTABLE is a separate include_browser test).
    for name, schema in schemas.items():
        assert schema.approval is ToolApproval.NEVER, name


def test_ceo_registry_browser_interactive_grantable_when_include_browser():
    schemas = {
        s.name: s for s in build_ceo_tool_registry(include_browser=True).list_all()
    }
    assert "browser" in schemas
    assert schemas["browser"].approval is ToolApproval.GRANTABLE
    assert "browser_screenshot" not in schemas
    for name, schema in schemas.items():
        if name == "browser":
            continue
        assert schema.approval is ToolApproval.NEVER, name


def test_ceo_registry_excludes_browser_navigate_by_default():
    names = {schema.name for schema in build_ceo_tool_registry().list_all()}
    assert "browser" not in names
    assert names == _CEO_READONLY_NAMES


def test_ceo_registry_excludes_delegate_primitive():
    # build_ceo_tool_registry returns only the read subset; the pipeline wires the
    # CEO-only delegate primitive separately, so it must not appear here.
    names = {schema.name for schema in build_ceo_tool_registry().list_all()}
    assert "delegate" not in names


def test_every_tool_exposes_catalog_fields():
    # The catalog endpoint serializes these straight to the UI, so each must be
    # populated with the right shapes.
    for schema in build_builtin_registry().list_all():
        assert schema.name and isinstance(schema.name, str)
        assert schema.description and isinstance(schema.description, str)
        assert isinstance(schema.category, ToolCategory)
        assert isinstance(schema.approval, ToolApproval)
        assert isinstance(schema.parameters, dict)


def test_worker_registry_omits_execution_class_on_cloud_server():
    # 生产安全: the WHOLE code-execution class (code_execute + test_run) is withheld from
    # a cloud server worker with no real sandbox — both run untrusted code through the
    # same subprocess chain inside the API container (SEC-005). Pinning test_run here
    # closes the P0 where it was registered unconditionally and ran as cloud RCE.
    from pathlib import Path

    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.server import ServerWorkspace

    backend = ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox(), location="server")
    names = {s.name for s in build_worker_registry(backend=backend).list_all()}
    assert "code_execute" not in names
    assert "test_run" not in names
    assert "escalate" in names


def test_worker_registry_keeps_execution_class_on_local_server_workspace():
    from pathlib import Path

    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.server import ServerWorkspace

    backend = ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox(), location="local")
    names = {s.name for s in build_worker_registry(backend=backend).list_all()}
    assert "code_execute" in names
    assert "test_run" in names
    assert "terminal" in names


def test_worker_registry_omits_terminal_when_cloud_desk_unhealthy():
    """Cloud without a healthy desk withholds the whole execution class, including terminal."""
    from pathlib import Path

    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.server import ServerWorkspace

    backend = ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox(), location="server")
    names = {s.name for s in build_worker_registry(backend=backend).list_all()}
    assert "terminal" not in names
    assert "code_execute" not in names


def test_worker_registry_assembles_terminal_when_cloud_desk_healthy(
    monkeypatch,
):
    from pathlib import Path

    from agentcore.config import settings
    from agentcore.tools.sandbox.cloud_health import set_cloud_sandbox_health_for_tests
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.server import ServerWorkspace

    monkeypatch.setattr(settings, "gvisor_enabled", True)
    set_cloud_sandbox_health_for_tests(True)
    backend = ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox(), location="server")
    names = {s.name for s in build_worker_registry(backend=backend).list_all()}
    assert "terminal" in names
    assert "browser" in names
    desc = build_worker_registry(backend=backend).get("terminal").schema.description
    assert "云桌" in desc
    assert "仅本地" not in desc
    # Catalog / default CEO (execution class on) advertise terminal.
    assert "terminal" in {s.name for s in build_worker_registry().list_all()}
    assert "terminal" in {s.name for s in build_builtin_registry().list_all()}
    assert "terminal" in {s.name for s in build_ceo_tool_registry().list_all()}
