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
from agentcore.tools.builtin.host import HostInfoTool, HostPingTool
from agentcore.tools.builtin.terminal import TerminalTool
from agentcore.tools.on_demand import (
    ON_DEMAND_SUMMARIES,
    ON_DEMAND_TOOL_NAMES,
    family_of,
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


def test_openai_defs_omit_deferred_until_offer():
    reg = ToolRegistry()
    reg.register(FileReadTool())
    reg.register(HostPingTool())
    reg.register(HostInfoTool())
    assert "host_ping" in reg.names
    assert "file_read" in reg.names
    assert set(reg.deferred_names) == {"host_ping", "host_info"}
    assert _def_names(reg) == {"file_read"}

    assert reg.offer("host_ping") is True
    # Family promote: both assembled host_* siblings land together.
    assert _def_names(reg) == {"file_read", "host_ping", "host_info"}
    assert not reg.deferred_names
    assert reg.offer("host_ping") is False  # idempotent


def test_execute_path_works_while_deferred():
    """Zero-loss: catalog + get still see the tool before consult promotes it."""
    reg = ToolRegistry()
    ping = HostPingTool()
    reg.register(ping)
    assert "host_ping" not in _def_names(reg)
    assert reg.get_optional("host_ping") is ping
    assert any(s.name == "host_ping" for s in reg.list_all())


async def test_directory_lists_only_assembled_on_demand_tools():
    reg = ToolRegistry()
    reg.register(FileReadTool())
    reg.register(HostPingTool())
    src = ToolConsultSource(registry=reg)
    entries = await src.list_directory("u")
    names = [e.name for e in entries]
    assert names == ["host_ping"]
    assert all(e.summary for e in entries)
    assert "file_read" not in names
    # Unassembled family siblings do not appear (host_info never registered).
    assert "host_info" not in names


async def test_consult_offers_family_and_returns_schema():
    reg = ToolRegistry()
    reg.register(HostPingTool())
    reg.register(HostInfoTool())
    src = ToolConsultSource(registry=reg, audience="ceo")
    body = await src.fetch_by_name("u", "host_ping")
    assert body is not None
    assert "已启用工具 `host_ping`" in body
    assert "host_info" in body
    assert "【本机 Host】" in body  # CEO HOW rides consult, not the opening core
    assert _def_names(reg) == {"host_ping", "host_info"}


async def test_consult_unknown_or_unassembled_is_miss():
    reg = ToolRegistry()
    src = ToolConsultSource(registry=reg)
    assert await src.fetch_by_name("u", "host_ping") is None  # not assembled
    assert await src.fetch_by_name("u", "file_read") is None  # resident, not on roster


async def test_consult_cache_does_not_skip_tool_offer():
    """Resume may restore a consult cache; tool consults must still call offer()."""
    token = consulted_memory_cache.set({})
    try:
        remember_consult("host_ping", "STALE — must not skip offer")
        reg = ToolRegistry()
        reg.register(HostPingTool())
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
        result = await tool.execute({"name": "host_ping"}, _ctx())
        assert result.success
        assert result.output != "STALE — must not skip offer"
        assert "已启用工具 `host_ping`" in (result.output or "")
        assert "host_ping" in _def_names(reg)
    finally:
        consulted_memory_cache.reset(token)


def test_clone_preserves_already_offered_tools():
    base = ToolRegistry()
    base.register(HostPingTool())
    base.register(FileReadTool())
    base.offer("host_ping")
    cloned = _registry_without(base, "file_read")
    assert "host_ping" in cloned.names
    assert "host_ping" in _def_names(cloned)
    assert "file_read" not in cloned.names


def test_preamble_and_core_make_consult_discoverable():
    preamble = "\n".join(_on_demand_preamble(with_summaries=True))
    assert "低频工具" in preamble
    assert "consult(name)" in preamble
    assert "下一轮" in preamble
    assert "consult(terminal)" in _CEO_CORE_HINT
    assert "consult(browser_navigate)" in _CEO_CORE_HINT


def test_family_of_covers_browser_and_solo_tools():
    browser = family_of("browser_navigate")
    assert "browser_click" in browser and "browser_screenshot" in browser
    assert family_of("terminal") == frozenset({"terminal"})
    assert family_of("desktop_notify") == frozenset({"desktop_notify"})
    assert family_of("write_section") == frozenset({"write_section"})


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
    """Locks the opening FC win: 51 registered; consult 另 wire，不在此表."""
    registry = _stuffed_worker()
    assert registry.count == 51
    offered = _def_names(registry)
    assert offered == _STUFFED_WORKER_RESIDENT
    chars = sum(
        len(json.dumps(d, ensure_ascii=False)) for d in registry.get_openai_definitions()
    )
    # 2026-08-19：瘦身后实测 21920。多出的 20 字（0.09%）不是往开场表回灌教法
    # （本轮恢复的指针在 CEO 的 ask_user/delegate；worker 按钮是换说法、字数未涨）。
    # worker 常驻钮已顶各自 per-tool 帽，再削会伤刚判定仍在的短教法。锁回实测整十。
    assert chars <= 21920, f"队员开场工具表变胖：{chars}"
    deferred = set(registry.deferred_names)
    assert deferred <= ON_DEMAND_TOOL_NAMES
    assert "terminal" in deferred and "browser_navigate" in deferred
    assert "md_to_docx" in deferred
    assert "write_section" in deferred
    assert "post_note" not in deferred
