"""On-demand tool roster: fixed name set, directory + consult, no intent classifier."""

from __future__ import annotations

from pathlib import Path

from agentcore.runtime.context.consult_sources import (
    MergedConsultSource,
    SkillConsultSource,
    ToolConsultSource,
    build_merged_consult_source,
)
from agentcore.runtime.context.consultable import ConsultDirectoryEntry
from agentcore.runtime.memory_consult_cache import consulted_memory_cache, remember_consult
from agentcore.runtime.resolve.prompt.base import _DEFAULT_SYSTEM_PROMPT
from agentcore.runtime.resolve.prompt.ceo_core import _CEO_CORE_HINT
from agentcore.runtime.resolve.prompt.compose import (
    _on_demand_preamble,
    assemble_system_prompt,
    compose_ceo_chat_prompt,
    render_on_demand_directory,
)
from agentcore.runtime.runs.executor.shared import _registry_without
from agentcore.runtime.skills import build_system_skill_registry
from agentcore.tools.builtin import build_builtin_registry
from agentcore.tools.builtin.browser import BrowserTool
from agentcore.tools.builtin.consult import ConsultTool
from agentcore.tools.builtin.file_ops.read import FileReadTool
from agentcore.tools.builtin.host import HostTool
from agentcore.tools.builtin.md_export import MdExportTool
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


def test_browser_catalog_summary_is_what_not_when():
    """目录行只写这是什么；browser 无独立手册。"""
    summary = BrowserTool.registration.catalog_summary
    assert summary == "真实浏览器。"
    assert "右坞" not in summary
    assert "打开网页" not in summary
    assert "短操作" not in summary
    assert "直播" not in summary


def test_host_catalog_summary_is_what_not_when():
    summary = HostTool.registration.catalog_summary
    assert summary == "这台电脑。"
    assert "排查" not in summary
    assert "修理" not in summary


def test_resident_tools_are_not_on_the_roster():
    for name in (
        "consult",
        "delegate",
        "ask_user",
        "read",
        "web_search",
        "escalate",
        "handoff",
        "folders",
        "run",
        "search_conversations",
        "read_conversation",
        "file_delete",
        "file_batch",
        "debate",
        "md_export",
        "git",
        "host",
        "browser",
    ):
        assert not is_on_demand_tool(name), name
    assert not ON_DEMAND_TOOL_NAMES
    assert "read_image" not in ON_DEMAND_TOOL_NAMES
    assert not is_on_demand_tool("read_image")
    # Dynamic MCP names are not in the static set, but still ride the same gate.
    assert "mcp_playwright_browser_navigate" not in ON_DEMAND_TOOL_NAMES
    assert is_mcp_tool_name("mcp_playwright_browser_navigate")
    assert is_on_demand_tool("mcp_playwright_browser_navigate")
    assert not is_on_demand_tool("mcp")  # prefix is mcp_ + server + tool


def test_openai_defs_include_assembled_on_demand():
    reg = ToolRegistry()
    reg.register(FileReadTool())
    reg.register(MdExportTool())
    assert "md_export" in reg.names
    assert "read" in reg.names
    assert _def_names(reg) == {"read", "md_export"}


def test_execute_path_sees_opening_table():
    reg = ToolRegistry()
    host = HostTool()
    reg.register(host)
    assert "host" in _def_names(reg)
    assert reg.get_optional("host") is host
    assert any(s.name == "host" for s in reg.list_all())


async def test_directory_lists_only_how_bearing_assembled_tools():
    """host / browser 无手册，不进模型按需目录。"""
    reg = ToolRegistry()
    reg.register(FileReadTool())
    reg.register(HostTool())
    reg.register(BrowserTool())
    src = ToolConsultSource(registry=reg)
    entries = await src.list_directory("u")
    names = [e.name for e in entries]
    assert names == []
    assert "read" not in names
    assert "host" not in names
    assert "browser" not in names


def test_directory_groups_sections_and_compacts_tool_families():
    """两栏才打栏名；技能决策时刻组只排序，不印子标题。"""
    browser = ConsultDirectoryEntry(name="browser", summary="真实浏览器", section="tool")
    skill = ConsultDirectoryEntry(
        name="data_file_landing", summary="整理表", section="skill", group="交付"
    )
    out = render_on_demand_directory([browser, skill])
    assert "能力指引：" in out
    assert "交付：" not in out
    assert "低频工具：" in out
    assert '- 真实浏览器 consult("browser")' in out
    assert '- 整理表 consult("data_file_landing")' in out
    assert "- data_file_landing：" not in out


def test_directory_row_does_not_add_a_second_full_stop():
    """句号属于摘要。行模板再插一枚会叠成两个。"""
    out = render_on_demand_directory(
        [
            ConsultDirectoryEntry(
                name="data_file_landing",
                summary="要交表时才查阅。成篇不查阅。",
            )
        ]
    )
    assert "。。" not in out
    assert '成篇不查阅。 consult("data_file_landing")' in out


async def test_merged_consult_copies_group_and_face():
    """MergedConsultSource 合并时必须拷贝 group 和 face（face 曾漏拷）。"""
    from agentcore.tools.registry import ToolRegistry

    skill_reg = build_system_skill_registry()
    tools = ToolRegistry()
    tools.register(BrowserTool())
    merged = MergedConsultSource(
        skill=SkillConsultSource(
            registry=skill_reg,
            tool_names={"delegate", "ask_user", "debate", "run"},
            audience="ceo",
        ),
        tool=ToolConsultSource(registry=tools),
    )
    entries = await merged.list_directory("u")
    by_name = {e.name: e for e in entries}
    landing = by_name["data_file_landing"]
    assert landing.group == "交付"
    assert landing.section == "skill"
    assert "browser" not in by_name


def _named_stub(name: str):
    from agentcore.core.types import ToolApproval, ToolFace
    from agentcore.tools.protocol import ToolSchema

    class _Stub:
        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(
                name=name,
                description="stub",
                parameters={"type": "object", "properties": {}},
                face=ToolFace.ORCHESTRATION,
                approval=ToolApproval.NEVER,
            )

    return _Stub()


async def test_consult_user_skill_strips_frontmatter():
    reg = ToolRegistry()
    reg.register(HostTool())
    assert "host" in _def_names(reg)
    body = "---\napply: on_demand\ndescription: 审\n---\n怎么审\n"

    class _FakeRule:
        async def list_directory(self, user_id: str):
            del user_id
            return [
                ConsultDirectoryEntry(
                    name="合同审查", summary="审", section="rule"
                )
            ]

        async def fetch_by_name(self, user_id: str, name: str):
            del user_id
            return body if name == "合同审查" else None

    merged = MergedConsultSource(
        tool=ToolConsultSource(registry=reg),
        rule=_FakeRule(),  # type: ignore[arg-type]
    )
    hit = await merged.fetch_hit("u", "合同审查")
    assert hit is not None
    assert "怎么审" in hit.body
    assert "apply:" not in hit.body
    assert "已在开场表" not in hit.body
    assert _def_names(reg) == {"host"}


async def test_consult_always_skill_is_not_a_consult_hit():
    """Always-mode user skills live in 设定, not the consult directory."""
    reg = ToolRegistry()
    reg.register(HostTool())

    class _FakeRule:
        async def list_directory(self, user_id: str):
            del user_id
            return []

        async def fetch_by_name(self, user_id: str, name: str):
            del user_id, name
            return None

    merged = MergedConsultSource(
        tool=ToolConsultSource(registry=reg),
        rule=_FakeRule(),  # type: ignore[arg-type]
    )
    entries = await merged.list_directory("u")
    assert all(e.name != "合同审查" for e in entries)
    assert await merged.fetch_hit("u", "合同审查") is None
    assert _def_names(reg) == {"host"}


async def test_consult_cache_returns_cached_user_skill():
    token = consulted_memory_cache.set({})
    try:
        remember_consult("合同审查", "怎么审")
        reg = ToolRegistry()
        reg.register(HostTool())
        tool = ConsultTool(
            source=MergedConsultSource(tool=ToolConsultSource(registry=reg))
        )
        result = await tool.execute({"name": "合同审查"}, _ctx())
        assert result.success
        assert "怎么审" in str(result.output or "")
        assert _def_names(reg) == {"host"}
    finally:
        consulted_memory_cache.reset(token)



async def test_consult_offers_export_and_resolves_retired_names():
    """md_export has no consult HOW; it is already on the opening table."""
    reg = ToolRegistry()
    reg.register(MdExportTool())
    src = ToolConsultSource(registry=reg, audience="ceo")
    assert "md_export" in _def_names(reg)
    assert await src.fetch_by_name("u", "md_export") is None
    assert await src.fetch_by_name("u", "md_to_docx") is None


async def test_host_consult_already_on_table():
    """No host HOW: consult misses; the tool is already on the opening table."""
    reg = ToolRegistry()
    reg.register(HostTool())
    src = ToolConsultSource(registry=reg, audience="ceo")
    assert "host" in _def_names(reg)
    assert await src.fetch_by_name("u", "host") is None
    worker = ToolConsultSource(registry=reg, audience="worker")
    assert await worker.fetch_by_name("u", "host") is None


async def test_consult_unknown_or_unassembled_is_miss():
    reg = ToolRegistry()
    src = ToolConsultSource(registry=reg)
    assert await src.fetch_by_name("u", "host") is None  # not assembled
    assert await src.fetch_by_name("u", "read") is None  # resident, not on roster


async def test_browser_consult_already_on_table():
    """No browser HOW: consult misses; the tool is already on the opening table."""
    reg = ToolRegistry()
    reg.register(BrowserTool())
    src = ToolConsultSource(registry=reg, audience="ceo")
    assert "browser" in _def_names(reg)
    assert await src.fetch_by_name("u", "browser") is None
    worker = ToolConsultSource(registry=reg, audience="worker")
    assert await worker.fetch_by_name("u", "browser") is None


async def test_consult_cache_does_not_return_stale_tool_handbook():
    """Resume may restore a consult cache; tool names with no handbook still miss."""
    token = consulted_memory_cache.set({})
    try:
        remember_consult("browser", "STALE — must not skip offer")
        reg = ToolRegistry()
        reg.register(BrowserTool())
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
        result = await tool.execute({"name": "browser"}, _ctx())
        assert result.success
        assert result.output != "STALE — must not skip offer"
        assert "没有名为" in (result.output or "")
        assert "browser" in _def_names(reg)
    finally:
        consulted_memory_cache.reset(token)


def test_clone_preserves_already_offered_tools():
    base = ToolRegistry()
    base.register(HostTool())
    base.register(FileReadTool())
    cloned = _registry_without(base, "read")
    assert "host" in cloned.names
    assert "host" in _def_names(cloned)
    assert "read" not in cloned.names


def test_preamble_and_core_make_consult_discoverable():
    preamble = "\n".join(_on_demand_preamble())
    desc = ConsultTool(source=None).schema.description  # type: ignore[arg-type]
    assert "按需目录" in preamble
    assert "consult(name)" in preamble
    assert "目录行" in preamble
    assert "目录行" not in _DEFAULT_SYSTEM_PROMPT
    assert "低频工具" not in preamble
    assert "下一模型轮" not in preamble
    assert "成套" not in preamble
    assert "不必等用户再发一条" not in preamble
    assert "下一模型轮" not in _DEFAULT_SYSTEM_PROMPT
    assert "已装配工具在开场表" in desc
    assert "不必靠查阅进表" in desc
    assert "下一模型轮" not in desc
    assert "成套" not in desc
    assert "consult(browser)" not in _CEO_CORE_HINT
    # consult 钩在按需目录 / 装配后的 CEO 串，不进 <身份> 核。
    assert "consult(name)" not in _CEO_CORE_HINT
    ceo = compose_ceo_chat_prompt(
        assemble_system_prompt(),
        skill_registry=build_system_skill_registry(),
        ceo_tool_names={"delegate", "consult", "ask_user", "debate"},
    )
    assert "consult(name)" in ceo


def test_family_of_covers_browser_and_solo_tools():
    browser = family_of("browser")
    assert browser == frozenset({"browser"})
    assert family_of("run") == frozenset({"run"})
    assert family_of("host") == frozenset({"host"})
    assert family_of("debate") == frozenset({"debate"})
    assert family_of("md_export") == frozenset({"md_export"})
    assert "md_to_docx" not in ON_DEMAND_TOOL_NAMES
    assert "md_to_pdf" not in ON_DEMAND_TOOL_NAMES
    assert "desktop_notify" not in ON_DEMAND_TOOL_NAMES
    # Without a live registry the Server siblings are unknown — name stands alone.
    assert family_of("mcp_playwright_browser_navigate") == frozenset(
        {"mcp_playwright_browser_navigate"}
    )


async def test_run_host_and_browser_have_no_consult_how_suffix():
    from agentcore.runtime.resolve.prompt import capability_how_suffix

    assert capability_how_suffix({"run"}) == ""
    assert capability_how_suffix({"host"}) == ""
    assert capability_how_suffix({"browser"}) == ""


async def test_browser_consult_has_no_handbook():
    browser_reg = ToolRegistry()
    browser_reg.register(BrowserTool())
    browser = await ToolConsultSource(registry=browser_reg, audience="ceo").fetch_by_name(
        "u", "browser"
    )
    assert browser is None

    missing = await ToolConsultSource(
        registry=ToolRegistry(), audience="ceo"
    ).fetch_by_name("u", "external_mount_readonly")
    assert missing is None


_STUFFED_WORKER_RESIDENT = frozenset(
    {
        "web_search",
        "web_fetch",
        "read",
        "write",
        "edit",
        "file_list",
        "glob",
        "file_delete",
        "grep",
        "run",
        "escalate",
        "handoff",
    }
)


def _stuffed_worker() -> ToolRegistry:
    """desktop_online + local + browser + host — same recipe as cost.tools_offered."""
    registry = build_builtin_registry(
        include_execution_tools=True,
        include_host_tools=True,
        include_browser=True,
        include_desktop_online_tools=True,
        location="local",
    )
    for cls in declared_tools(surface=ToolSurface.WORKER_ONLY):
        meta = tool_registration(cls)
        if meta.manual_wire:
            continue
        registry.register(instantiate_declared(cls, location="local"))
    return registry


def test_stuffed_worker_opening_table_includes_assembled_tools():
    """Assembled tools — including former on-demand — sit on the opening FC table."""
    registry = _stuffed_worker()
    assert registry.count == 16
    assert "read_image" not in registry.names
    offered = _def_names(registry)
    assert offered == set(registry.names)
    assert offered >= _STUFFED_WORKER_RESIDENT
    assert {"browser", "host", "md_export"} <= offered


def _playwright_mcp_result(*, tool_count: int = 24) -> McpDiscoverResult:
    """A Playwright-sized batch: assembled MCP lands on the opening table."""
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


async def test_stuffed_worker_opening_table_includes_mcp_tools():
    """Assembled MCP lands on the opening FC table; consult is HOW-only (miss)."""
    registry = _stuffed_worker()
    opening_before = _def_names(registry)
    count_before = registry.count
    assert count_before == 16
    assert opening_before == set(registry.names)

    registered = register_mcp_tools(registry, _playwright_mcp_result(tool_count=24))
    assert registered == 24
    assert registry.count == 40
    mcp_names = {n for n in registry.names if n.startswith("mcp_")}
    assert len(mcp_names) == 24
    offered = _def_names(registry)
    assert mcp_names <= offered
    assert offered == set(registry.names)

    first = next(iter(sorted(mcp_names)))
    src = ToolConsultSource(registry=registry)
    assert await src.fetch_by_name("u", first) is None
    playwright_family = family_of(first, registry=registry)
    assert playwright_family == mcp_names


def test_ceo_chat_tools_after_assemble_wire_include_work_tools():
    """CEO holds logs + mcp_* + debate on the opening table."""
    from agentcore.llm.profiles import default_turn_profiles
    from agentcore.runtime.events import EventSink
    from agentcore.runtime.resolve.prepare import (
        _assemble_ceo_toolset,
        _wire_conversation_log_tools,
    )
    from agentcore.runtime.skills import build_system_skill_registry

    ctx = ToolContext.create(
        execution_id="exec-assembly",
        run_id="r",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )
    _, _, chat_tools = _assemble_ceo_toolset(
        llm=object(),
        sink=EventSink(),
        base_system_prompt="SYS",
        user_message="原始请求",
        history=[],
        worker_tools=ToolRegistry(),
        base_tool_context=ctx,
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
    registered = register_mcp_tools(chat_tools, _playwright_mcp_result(tool_count=2))
    assert registered == 2
    _wire_conversation_log_tools(chat_tools, folder_id="F1")

    names = set(chat_tools.names)
    mcp_names = {n for n in names if n.startswith("mcp_")}
    assert {"search_conversations", "read_conversation"} <= names
    assert "desktop_notify" not in names
    assert len(mcp_names) == 2
    assert names.isdisjoint({"escalate", "handoff"})

    offered = _def_names(chat_tools)
    assert mcp_names <= offered
    assert {"search_conversations", "read_conversation"} <= offered
    assert "debate" in offered


async def test_mcp_directory_omits_assembled_tools_already_on_table():
    """MCP is on the opening table; the model catalog does not list it as HOW."""
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
    assert _def_names(registry) == {
        "read",
        "mcp_echo_ping",
        "mcp_echo_list",
        "mcp_fs_read",
    }
    src = ToolConsultSource(registry=registry)
    entries = await src.list_directory("u")
    assert [e.name for e in entries] == []
    assert await src.fetch_by_name("u", "mcp_echo_ping") is None


async def test_consult_mcp_server_alias_is_not_a_how_entry():
    registry = ToolRegistry()
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
    src = ToolConsultSource(registry=registry)
    entries = await src.list_directory("u")
    rendered = render_on_demand_directory(entries)
    assert "连接器：" not in rendered
    assert await src.fetch_by_name("u", "echo") is None
    assert "mcp_echo_ping" in _def_names(registry)
    assert "mcp_echo_list" in _def_names(registry)


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
        assert "没有名为" in (result.output or "")
        assert "mcp_echo_ping" in _def_names(reg)
    finally:
        consulted_memory_cache.reset(token)
