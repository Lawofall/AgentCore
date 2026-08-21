"""On-demand tool roster: fixed name set, directory + consult, no intent classifier."""

from __future__ import annotations

import json
from pathlib import Path

from agentcore.runtime.context.consult_sources import (
    ToolConsultSource,
    build_merged_consult_source,
)
from agentcore.runtime.memory_consult_cache import consulted_memory_cache, remember_consult
from agentcore.runtime.resolve.prompt.ceo_core import _CEO_CORE_HINT
from agentcore.runtime.resolve.prompt.compose import _on_demand_preamble
from agentcore.runtime.runs.executor.shared import _registry_without
from agentcore.tools.builtin import build_builtin_registry
from agentcore.tools.builtin.consult import ConsultTool
from agentcore.tools.builtin.file_ops.mutate import WriteSectionTool
from agentcore.tools.builtin.file_ops.read import FileReadTool
from agentcore.tools.builtin.host import HostTool
from agentcore.tools.builtin.md_to_docx import MdToDocxTool
from agentcore.tools.builtin.md_to_pdf import MdToPdfTool
from agentcore.tools.builtin.terminal import TerminalTool
from agentcore.tools.mcp.dynamic import McpDynamicTool
from agentcore.tools.mcp.wire import McpDiscoverResult, McpToolSpec, register_mcp_tools
from agentcore.tools.on_demand import (
    ON_DEMAND_SUMMARIES,
    ON_DEMAND_TOOL_NAMES,
    family_of,
    is_mcp_tool_name,
    is_on_demand_tool,
)
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registration import (
    ToolSurface,
    declared_tools,
    instantiate_declared,
    tool_registration,
)
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def _ctx(user_id: str = "u") -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id=user_id,
    )


def _def_names(reg: ToolRegistry) -> set[str]:
    names: set[str] = set()
    for d in reg.get_openai_definitions():
        fn = d.get("function") or {}
        names.add(str(fn.get("name") or d.get("name") or ""))
    return names


def test_roster_summaries_cover_every_on_demand_name():
    assert set(ON_DEMAND_SUMMARIES) == set(ON_DEMAND_TOOL_NAMES)


def test_resident_tools_are_not_on_the_roster():
    for name in (
        "consult",
        "delegate",
        "debate",
        "ask_user",
        "git",
        "file_read",
        "web_search",
        "escalate",
        "handoff",
        "post_note",
        "read_notes",
        "amend_note",
        "list_folders",
        "remember",
    ):
        assert not is_on_demand_tool(name), name
    # Dynamic MCP names are not in the static set, but still ride the same gate.
    assert "mcp_playwright_browser_navigate" not in ON_DEMAND_TOOL_NAMES
    assert is_mcp_tool_name("mcp_playwright_browser_navigate")
    assert is_on_demand_tool("mcp_playwright_browser_navigate")
    assert not is_on_demand_tool("mcp")  # prefix is mcp_ + server + tool


def test_openai_defs_omit_deferred_until_offer():
    reg = ToolRegistry()
    reg.register(FileReadTool())
    reg.register(MdToDocxTool())
    reg.register(MdToPdfTool())
    assert "md_to_docx" in reg.names
    assert "file_read" in reg.names
    assert set(reg.deferred_names) == {"md_to_docx", "md_to_pdf"}
    assert _def_names(reg) == {"file_read"}

    assert reg.offer("md_to_docx") is True
    # Family promote: both assembled export siblings land together.
    assert _def_names(reg) == {"file_read", "md_to_docx", "md_to_pdf"}
    assert not reg.deferred_names
    assert reg.offer("md_to_docx") is False  # idempotent


def test_execute_path_works_while_deferred():
    """Zero-loss: catalog + get still see the tool before consult promotes it."""
    reg = ToolRegistry()
    host = HostTool()
    reg.register(host)
    assert "host" not in _def_names(reg)
    assert reg.get_optional("host") is host
    assert any(s.name == "host" for s in reg.list_all())


async def test_directory_lists_only_assembled_on_demand_tools():
    reg = ToolRegistry()
    reg.register(FileReadTool())
    reg.register(HostTool())
    src = ToolConsultSource(registry=reg)
    entries = await src.list_directory("u")
    names = [e.name for e in entries]
    assert names == ["host"]
    assert all(e.summary for e in entries)
    assert "file_read" not in names


async def test_consult_offers_family_and_returns_schema():
    reg = ToolRegistry()
    reg.register(MdToDocxTool())
    reg.register(MdToPdfTool())
    src = ToolConsultSource(registry=reg, audience="ceo")
    body = await src.fetch_by_name("u", "md_to_docx")
    assert body is not None
    assert "已启用工具 `md_to_docx`" in body
    assert "md_to_pdf" in body
    assert _def_names(reg) == {"md_to_docx", "md_to_pdf"}


async def test_host_consult_returns_how():
    reg = ToolRegistry()
    reg.register(HostTool())
    src = ToolConsultSource(registry=reg, audience="ceo")
    body = await src.fetch_by_name("u", "host")
    assert body is not None
    assert "已启用工具 `host`" in body
    assert "本回合下一模型轮" in body
    assert "【本机 Host】" in body
    assert _def_names(reg) == {"host"}


async def test_consult_unknown_or_unassembled_is_miss():
    reg = ToolRegistry()
    src = ToolConsultSource(registry=reg)
    assert await src.fetch_by_name("u", "host") is None  # not assembled
    assert await src.fetch_by_name("u", "file_read") is None  # resident, not on roster


async def test_consult_cache_does_not_skip_tool_offer():
    """Resume may restore a consult cache; tool consults must still call offer()."""
    token = consulted_memory_cache.set({})
    try:
        remember_consult("host", "STALE — must not skip offer")
        reg = ToolRegistry()
        reg.register(HostTool())
        tool = ConsultTool(
            source=build_merged_consult_source(
                skill_registry=None,
                tool_names=set(reg.names),
                memory_store=None,
                folder_id=None,
                include_rules=False,
                tool_registry=reg,
                skill_audience="ceo",
            )
        )
        result = await tool.execute({"name": "host"}, _ctx())
        assert result.success
        assert result.output != "STALE — must not skip offer"
        assert "已启用工具 `host`" in (result.output or "")
        assert "host" in _def_names(reg)
    finally:
        consulted_memory_cache.reset(token)


def test_clone_preserves_already_offered_tools():
    base = ToolRegistry()
    base.register(HostTool())
    base.register(FileReadTool())
    base.offer("host")
    cloned = _registry_without(base, "file_read")
    assert "host" in cloned.names
    assert "host" in _def_names(cloned)
    assert "file_read" not in cloned.names


def test_preamble_and_core_make_consult_discoverable():
    preamble = "\n".join(_on_demand_preamble(with_summaries=True))
    assert "低频工具" in preamble
    assert "consult(name)" in preamble
    assert "下一模型轮" in preamble
    assert "不必等用户再发一条" in preamble
    assert "consult(terminal)" in _CEO_CORE_HINT
    assert "consult(browser)" in _CEO_CORE_HINT


def test_family_of_covers_browser_and_solo_tools():
    browser = family_of("browser")
    assert browser == frozenset({"browser"})
    assert family_of("terminal") == frozenset({"terminal"})
    assert family_of("host") == frozenset({"host"})
    assert family_of("desktop_notify") == frozenset({"desktop_notify"})
    assert family_of("write_section") == frozenset({"write_section"})
    # Without a live registry the Server siblings are unknown — name stands alone.
    assert family_of("mcp_playwright_browser_navigate") == frozenset(
        {"mcp_playwright_browser_navigate"}
    )


async def test_terminal_consult_returns_runtime_how():
    reg = ToolRegistry()
    reg.register(TerminalTool())
    src = ToolConsultSource(registry=reg, audience="ceo")
    body = await src.fetch_by_name("u", "terminal")
    assert body is not None
    assert "wait_for" in body
    assert "【本机运行态】" in body


async def test_write_section_consult_promotes_onto_table():
    """建站 HTML 笔仍注册；consult 后才进 FC 表（少一轮也不能写不出 SECTION）。"""
    reg = ToolRegistry()
    tool = WriteSectionTool()
    reg.register(tool)
    assert "write_section" in reg.names
    assert "write_section" not in _def_names(reg)
    src = ToolConsultSource(registry=reg)
    entries = await src.list_directory("u")
    assert [e.name for e in entries] == ["write_section"]
    body = await src.fetch_by_name("u", "write_section")
    assert body is not None
    assert "已启用工具 `write_section`" in body
    assert "SECTION" in body
    assert _def_names(reg) == {"write_section"}


_STUFFED_WORKER_RESIDENT = frozenset(
    {
        "web_search",
        "read_url",
        "file_read",
        "file_write",
        "file_append",
        "str_replace",
        "file_list",
        "file_delete",
        "file_move",
        "file_copy",
        "mkdir",
        "file_batch",
        "grep",
        "code_search",
        "code_diagnostics",
        "git",
        "test_run",
        "code_execute",
        "escalate",
        "handoff",
        "post_note",
        "read_notes",
        "amend_note",
    }
)


def _stuffed_worker() -> ToolRegistry:
    """desktop_online + local + browser + host — same recipe as cost.tools_offered."""
    registry = build_builtin_registry(
        include_execution_tools=True,
        include_host_tools=True,
        include_browser=True,
        include_desktop_online_tools=True,
        include_git=True,
        location="local",
    )
    for cls in declared_tools(surface=ToolSurface.WORKER_ONLY):
        meta = tool_registration(cls)
        if meta.manual_wire:
            continue
        registry.register(instantiate_declared(cls, location="local"))
    return registry


def test_stuffed_worker_opening_table_omits_on_demand_tools():
    """Locks the opening FC win: 33 registered; consult 另 wire，不在此表."""
    registry = _stuffed_worker()
    assert registry.count == 33
    offered = _def_names(registry)
    assert offered == _STUFFED_WORKER_RESIDENT
    chars = sum(
        len(json.dumps(d, ensure_ascii=False)) for d in registry.get_openai_definitions()
    )
    # 2026-08-22：实测 22007。不是往开场表回灌教法（sandboxd / 出队身份后的工具描述微调）。
    # worker 常驻钮已顶各自 per-tool 帽，再削会伤仍在的短教法。锁回实测整十。
    assert chars <= 22010, f"队员开场工具表变胖：{chars}"
    deferred = set(registry.deferred_names)
    assert deferred <= ON_DEMAND_TOOL_NAMES
    assert "terminal" in deferred and "browser" in deferred
    assert "host" in deferred
    assert "md_to_docx" in deferred
    assert "write_section" in deferred
    assert "post_note" not in deferred


def _playwright_mcp_result(*, tool_count: int = 24) -> McpDiscoverResult:
    """A Playwright-sized batch: many tools, none should land on the opening table."""
    specs = tuple(
        McpToolSpec(
            server_id="playwright",
            server_name="Playwright",
            mcp_tool_name=f"browser_{i}",
            description=f"Playwright browser action {i}",
            input_schema={"type": "object", "properties": {}},
        )
        for i in range(tool_count)
    )
    return McpDiscoverResult(
        ready_servers=1,
        tool_count=tool_count,
        server_labels=("Playwright",),
        specs=specs,
    )


async def test_stuffed_worker_opening_table_omits_mcp_tools():
    """Hanging MCP must not grow the opening FC table (Playwright = 24 schemas)."""
    registry = _stuffed_worker()
    opening_before = _def_names(registry)
    count_before = registry.count
    assert count_before == 33
    assert opening_before == _STUFFED_WORKER_RESIDENT

    registered = register_mcp_tools(registry, _playwright_mcp_result(tool_count=24))
    assert registered == 24
    assert registry.count == 57
    offered = _def_names(registry)
    assert offered == opening_before
    mcp_names = {n for n in registry.names if n.startswith("mcp_")}
    assert len(mcp_names) == 24
    assert mcp_names <= set(registry.deferred_names)
    assert mcp_names.isdisjoint(offered)

    first = next(iter(sorted(mcp_names)))
    src = ToolConsultSource(registry=registry)
    body = await src.fetch_by_name("u", first)
    assert body is not None
    assert f"已启用工具 `{first}`" in body
    offered_after = _def_names(registry)
    assert mcp_names <= offered_after
    assert offered_after == opening_before | mcp_names
    playwright_family = family_of(first, registry=registry)
    assert playwright_family == mcp_names


async def test_mcp_directory_lists_assembled_tools_and_consult_promotes_server_family():
    """Catalog lists MCP by live description; consult offers the whole Server."""
    registry = ToolRegistry()
    registry.register(FileReadTool())
    registry.register(
        McpDynamicTool(
            fc_name="mcp_echo_ping",
            server_id="echo",
            server_name="Echo",
            mcp_tool_name="ping",
            description="Ping the echo server",
            input_schema={"type": "object", "properties": {}},
        )
    )
    registry.register(
        McpDynamicTool(
            fc_name="mcp_echo_list",
            server_id="echo",
            server_name="Echo",
            mcp_tool_name="list",
            description="List echo resources",
            input_schema={"type": "object", "properties": {}},
        )
    )
    registry.register(
        McpDynamicTool(
            fc_name="mcp_fs_read",
            server_id="filesystem",
            server_name="Filesystem",
            mcp_tool_name="read",
            description="Read a file",
            input_schema={"type": "object", "properties": {}},
        )
    )
    assert _def_names(registry) == {"file_read"}
    src = ToolConsultSource(registry=registry)
    entries = await src.list_directory("u")
    names = [e.name for e in entries]
    assert names == ["mcp_echo_ping", "mcp_echo_list", "mcp_fs_read"]
    by_name = {e.name: e.summary for e in entries}
    assert "Ping the echo server" in by_name["mcp_echo_ping"]
    assert "Echo" in by_name["mcp_echo_ping"]
    assert "file_read" not in names

    body = await src.fetch_by_name("u", "mcp_echo_ping")
    assert body is not None
    assert "已启用工具 `mcp_echo_ping`" in body
    assert "mcp_echo_list" in body
    assert _def_names(registry) == {"file_read", "mcp_echo_ping", "mcp_echo_list"}
    assert "mcp_fs_read" in registry.deferred_names


async def test_consult_unknown_mcp_name_is_miss_until_assembled():
    src = ToolConsultSource(registry=ToolRegistry())
    assert await src.fetch_by_name("u", "mcp_echo_ping") is None


async def test_consult_tool_promotes_mcp_and_skips_stale_cache():
    """Same wire as other on-demand tools: consult always calls offer()."""
    token = consulted_memory_cache.set({})
    try:
        remember_consult("mcp_echo_ping", "STALE — must not skip offer")
        reg = ToolRegistry()
        reg.register(
            McpDynamicTool(
                fc_name="mcp_echo_ping",
                server_id="echo",
                server_name="Echo",
                mcp_tool_name="ping",
                description="Ping",
                input_schema=None,
            )
        )
        tool = ConsultTool(
            source=build_merged_consult_source(
                skill_registry=None,
                tool_names=set(reg.names),
                memory_store=None,
                folder_id=None,
                include_rules=False,
                tool_registry=reg,
                skill_audience="worker",
            )
        )
        result = await tool.execute({"name": "mcp_echo_ping"}, _ctx())
        assert result.success
        assert result.output != "STALE — must not skip offer"
        assert "已启用工具 `mcp_echo_ping`" in (result.output or "")
        assert "mcp_echo_ping" in _def_names(reg)
    finally:
        consulted_memory_cache.reset(token)
