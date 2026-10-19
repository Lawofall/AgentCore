"""Unit tests for the built-in tool catalog (the single-source registry).

``build_builtin_registry`` is the one place that declares "what tools ship with
the platform": the chat pipeline builds the worker toolset from it and the
``GET /tools`` catalog serializes it. These tests pin the roster and the
governance flags the UI renders, and guard that the CEO-only ``delegate``
primitive never leaks into the general catalog.
"""

from agentcore.core.types import ToolApproval, ToolFace
from agentcore.tools.builtin import (
    build_builtin_registry,
    build_ceo_tool_registry,
    build_worker_registry,
    file_mutation_tool_names,
)

_EXPECTED_NAMES = {
    "web_search",
    "web_fetch",
    "read",
    "write",
    "edit",
    "file_list",
    "glob",
    "file_delete",
    "file_batch",
    "grep",
    "run",
}

# CEO default roster = folder boundary (read + write + execute, no Host).
_CEO_DEFAULT_NAMES = {
    "web_search",
    "web_fetch",
    "read",
    "write",
    "edit",
    "file_list",
    "glob",
    "file_delete",
    "file_batch",
    "grep",
    "run",
}
_MUTATION_NAMES = {
    "write",
    "edit",
    "file_delete",
    "file_batch",
}


def test_registry_lists_exactly_the_builtin_tools():
    names = {schema.name for schema in build_builtin_registry().list_all()}
    assert names == _EXPECTED_NAMES


def test_registry_excludes_ceo_only_delegate():
    names = {schema.name for schema in build_builtin_registry().list_all()}
    assert "delegate" not in names


# Worker-surface extras auto-registered on the worker roster (not builtin catalog).
# escalate / handoff stay CEO-absent forever.
_WORKER_SURFACE_NAMES = {
    "escalate",
    "handoff",
}


def test_worker_registry_adds_worker_surface_tools_without_leaking_them():
    worker = {s.name for s in build_worker_registry().list_all()}
    builtin = {s.name for s in build_builtin_registry().list_all()}
    ceo = {s.name for s in build_ceo_tool_registry().list_all()}
    assert worker >= _WORKER_SURFACE_NAMES
    # builtins + the worker-surface primitives, nothing else.
    assert worker == _EXPECTED_NAMES | _WORKER_SURFACE_NAMES
    assert builtin.isdisjoint(_WORKER_SURFACE_NAMES)
    # Default CEO registry omits worker-surface names (escalate/handoff never join CEO).
    assert ceo.isdisjoint(_WORKER_SURFACE_NAMES)


def test_write_and_exec_tools_are_grantable():
    approvals = {s.name: s.approval for s in build_builtin_registry().list_all()}
    assert approvals["write"] is ToolApproval.GRANTABLE
    assert approvals["edit"] is ToolApproval.GRANTABLE
    assert approvals["run"] is ToolApproval.GRANTABLE
    # Destructive / mutating file ops require the same consent as writes.
    assert approvals["file_delete"] is ToolApproval.GRANTABLE
    assert approvals["file_batch"] is ToolApproval.GRANTABLE
    # Read-only tools auto-run (no approval prompt).
    assert approvals["read"] is ToolApproval.NEVER
    assert approvals["file_list"] is ToolApproval.NEVER
    assert approvals["glob"] is ToolApproval.NEVER
    assert approvals["web_search"] is ToolApproval.NEVER


def test_file_mutation_class_is_grantable_filesystem_without_code_execute():
    # The「本轮内允许所有文件改动」class = GRANTABLE ∩ FILE, so it covers the
    # file-edit tools but NOT code_execute (EXECUTION, higher-risk → its own gate).
    # Pinned so a future tool can't silently widen or narrow what one click grants.
    names = file_mutation_tool_names()
    assert names == {
        "write",
        "edit",
        "file_delete",
        "file_batch",
    }
    assert "code_execute" not in names
    assert names == _MUTATION_NAMES - {"code_execute"}


def test_run_description_does_not_overpromise_sandbox():
    # Location-aware wording: catalog (no location) must not claim「用户自己的机器」;
    # local registry must; server registry must name the cloud desk.
    from agentcore.tools.builtin.run import RunTool, run_description

    catalog = {s.name: s for s in build_builtin_registry().list_all()}
    assert "用户自己的机器" not in catalog["run"].description
    assert "可能【直接运行" not in catalog["run"].description

    assert "用户本机" in run_description("local")
    assert "云桌" in run_description("server")
    assert "web_fetch" in run_description("server")
    assert "私网" in run_description("server")
    assert "无任意 HTTPS" not in run_description("server")
    assert "用户本机" in RunTool(location="local").schema.description
    assert "云桌" in RunTool(location="server").schema.description


def test_run_consult_is_not_how_owner_file_read_keeps_dump_steer():
    from agentcore.tools.builtin.file_ops.read import FileReadTool
    from agentcore.tools.builtin.run import run_description

    desc = run_description("local")
    assert "HOW→consult(run)" not in desc
    # dump 纠偏在 source_inspect 回执，不进 read 目录行
    fr = FileReadTool().schema.description
    assert "dump" not in fr
    assert "code_execute" not in fr
    assert "test_run" not in fr


def test_run_description_routes_long_running_to_background():
    from agentcore.tools.builtin.run import RunTool, run_description

    desc = run_description("local")
    assert "HOW→consult(run)" not in desc
    assert "background=true" not in desc
    schema = RunTool().schema
    assert "wait_for" in schema.parameters["properties"]
    assert "background" in schema.parameters["properties"]
    wait_desc = schema.parameters["properties"]["wait_for"]["description"]
    bg_desc = schema.parameters["properties"]["background"]["description"]
    assert "省略" in wait_desc
    assert "默认就绪" not in wait_desc
    assert "dev" in bg_desc.lower() or "watch" in bg_desc
    assert "终端" in schema.parameters["properties"]["command"]["description"]
    assert "pnpm" not in schema.parameters["properties"]["command"]["description"]
    assert "purpose" not in schema.parameters["properties"]
    assert "仅本地" not in RunTool(location="server").schema.description


def test_ceo_registry_holds_run_with_execution_class():
    assert "run" in {s.name for s in build_ceo_tool_registry().list_all()}
    assert "run" in {
        s.name for s in build_ceo_tool_registry(backend_location="server").list_all()
    }
    assert "run" in {
        s.name for s in build_ceo_tool_registry(backend_location="local").list_all()
    }
    assert "run" not in {
        s.name
        for s in build_ceo_tool_registry(include_execution_tools=False).list_all()
    }
    assert (
        build_ceo_tool_registry(backend_location="server").get("run").schema.approval
        is ToolApproval.GRANTABLE
    )


def test_run_description_server_omits_local_machine_wording():
    from agentcore.tools.builtin.run import run_description

    server = run_description("server")
    assert "WSL" not in server
    assert "用户本机" not in server
    assert "云桌" in server


def test_web_fetch_description_does_not_overclaim_completeness():
    # 截断是执行回执事实，不进按钮冒充「完整正文」，也不再广告可调配额。
    # 深不深读由模型自判；按钮只写这是什么。工作区改道在失败回执。
    schemas = {s.name: s for s in build_builtin_registry().list_all()}
    desc = schemas["web_fetch"].description
    props = schemas["web_fetch"].parameters["properties"]
    assert "max_chars" not in props
    assert "完整正文" not in desc
    assert "#rN" not in desc
    assert "max_chars" not in desc
    assert "截断" not in desc
    assert "摘要不够" not in desc
    assert "核对原文" not in desc
    assert "http" in desc


def test_ceo_registry_holds_full_builtin_surface():
    names = {schema.name for schema in build_ceo_tool_registry().list_all()}
    assert names == _CEO_DEFAULT_NAMES


def test_ceo_registry_includes_mutation_tools():
    names = {schema.name for schema in build_ceo_tool_registry().list_all()}
    assert names >= _MUTATION_NAMES


def test_ceo_registry_write_tools_are_grantable():
    schemas = {s.name: s for s in build_ceo_tool_registry().list_all()}
    assert schemas, "CEO must retain its builtin tools"
    for name in ("write", "edit", "file_delete", "run"):
        assert schemas[name].approval is ToolApproval.GRANTABLE, name
    for name in ("read", "file_list", "web_search"):
        assert schemas[name].approval is ToolApproval.NEVER, name


def test_computer_boundary_holds_host_regardless_of_desktop_online():
    from agentcore.core.types import WorkspaceBoundary

    schemas = {
        s.name: s
        for s in build_ceo_tool_registry(
            desktop_online=False, permission_axes=WorkspaceBoundary.COMPUTER
        ).list_all()
    }
    assert "host" in schemas
    assert schemas["host"].approval is ToolApproval.NEVER
    online = {
        s.name
        for s in build_ceo_tool_registry(
            desktop_online=True, permission_axes=WorkspaceBoundary.COMPUTER
        ).list_all()
    }
    assert "host" in online
    folder = {s.name for s in build_ceo_tool_registry().list_all()}
    assert "host" not in folder


def test_ceo_registry_browser_interactive_grantable_when_include_browser():
    schemas = {
        s.name: s for s in build_ceo_tool_registry(include_browser=True).list_all()
    }
    assert set(schemas) == _CEO_DEFAULT_NAMES | {"browser"}
    assert schemas["browser"].approval is ToolApproval.GRANTABLE
    assert schemas["write"].approval is ToolApproval.GRANTABLE


def test_ceo_registry_excludes_browser_navigate_by_default():
    names = {schema.name for schema in build_ceo_tool_registry().list_all()}
    assert "browser" not in names
    assert names == _CEO_DEFAULT_NAMES


def test_ceo_registry_excludes_delegate_primitive():
    # build_ceo_tool_registry is the builtin surface; the pipeline wires the
    # CEO-only delegate primitive separately, so it must not appear here.
    names = {schema.name for schema in build_ceo_tool_registry().list_all()}
    assert "delegate" not in names


def test_every_tool_exposes_catalog_fields():
    # The catalog endpoint serializes these straight to the UI, so each must be
    # populated with the right shapes.
    for schema in build_builtin_registry().list_all():
        assert schema.name and isinstance(schema.name, str)
        assert schema.description and isinstance(schema.description, str)
        assert isinstance(schema.face, ToolFace)
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
    assert "run" not in names
    assert "escalate" in names


def test_worker_registry_keeps_execution_class_on_local_server_workspace():
    from pathlib import Path

    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.server import ServerWorkspace

    backend = ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox(), location="local")
    names = {s.name for s in build_worker_registry(backend=backend).list_all()}
    assert "run" in names


def test_worker_registry_omits_terminal_when_cloud_desk_unhealthy():
    """Cloud without a healthy desk withholds the whole execution class, including terminal."""
    from pathlib import Path

    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.server import ServerWorkspace

    backend = ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox(), location="server")
    names = {s.name for s in build_worker_registry(backend=backend).list_all()}
    assert "run" not in names


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
    assert "run" in names
    assert "browser" in names
    desc = build_worker_registry(backend=backend).get("run").schema.description
    assert "云桌" in desc
    assert "仅本地" not in desc
    # Catalog / default CEO (execution class on) advertise run.
    assert "run" in {s.name for s in build_worker_registry().list_all()}
    assert "run" in {s.name for s in build_builtin_registry().list_all()}
    assert "run" in {s.name for s in build_ceo_tool_registry().list_all()}
