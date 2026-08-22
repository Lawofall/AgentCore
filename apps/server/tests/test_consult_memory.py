"""Tests for unified ``consult`` + ``<按需目录>`` (步 1 · 按需三合一).

Covers memory-topic slice of the merged source (forgiving names, soft miss,
directory↔tool gate). Skills / rules covered in ``test_skills`` / ``test_consult_rule``.
"""

from pathlib import Path

from agentcore.core.types import ToolCategory
from agentcore.memory import MemoryTopic
from agentcore.memory.store import CORE_MEMORY_FILE, FileMemoryStore, topic_path
from agentcore.runtime.context.consult_sources import (
    MemoryConsultSource,
    MergedConsultSource,
    build_merged_consult_source,
)
from agentcore.runtime.resolve.prompt import (
    assemble_system_prompt,
    compose_ceo_chat_prompt,
    compose_worker_base_prompt,
    render_on_demand_directory,
)
from agentcore.runtime.skills import build_system_skill_registry
from agentcore.tools.builtin import build_worker_registry
from agentcore.tools.builtin.consult import ConsultTool
from agentcore.tools.ceo_toolset import wire_worker_consult as _wire_worker_consult_tools
from agentcore.tools.protocol import ToolContext
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


def _memory_tool(store: FileMemoryStore, folder_id: str | None = None) -> ConsultTool:
    source = MergedConsultSource(
        memory=MemoryConsultSource(store=store, folder_id=folder_id, enabled=True)
    )
    return ConsultTool(source=source)


def test_consult_schema_is_orchestration_primitive(tmp_path):
    tool = _memory_tool(FileMemoryStore(tmp_path))
    schema = tool.schema
    assert schema.name == "consult"
    assert schema.category is ToolCategory.ORCHESTRATION
    assert "name" in schema.parameters["properties"]


async def test_consult_memory_returns_body_on_hit(tmp_path):
    store = FileMemoryStore(tmp_path)
    body = "## 笔记\n- 用 pnpm dev 起前端\n- 服务端用 uv run\n"
    await store.save("u", topic_path("部署流程"), body)
    result = await _memory_tool(store).execute({"name": "部署流程"}, _ctx())
    assert result.success
    assert result.output == body
    assert result.display["name"] == "部署流程"
    # 来源分类只进日志，不进 display：读侧不向用户暴露 skill/rule/memory 三分。
    assert "kind" not in result.display


async def test_consult_memory_name_spelling_is_forgiving(tmp_path):
    store = FileMemoryStore(tmp_path)
    body = "## 笔记\n- x\n"
    await store.save("u", topic_path("部署流程"), body)
    tool = _memory_tool(store)
    for name in ("部署流程", "主题/部署流程", "部署流程.md", "主题/部署流程.md"):
        result = await tool.execute({"name": name}, _ctx())
        assert result.success, name
        assert result.output == body


async def test_consult_memory_soft_miss_on_unknown(tmp_path):
    store = FileMemoryStore(tmp_path)
    await store.save("u", topic_path("部署流程"), "## x\n")
    result = await _memory_tool(store).execute({"name": "不存在"}, _ctx())
    assert result.success
    assert result.error is None
    assert "没有名为" in result.output
    assert "部署流程" in result.output


async def test_consult_memory_soft_miss_on_empty_name(tmp_path):
    result = await _memory_tool(FileMemoryStore(tmp_path)).execute({"name": ""}, _ctx())
    assert result.success
    assert "缺少 name" in result.output


async def test_consult_memory_skips_core_file(tmp_path):
    store = FileMemoryStore(tmp_path)
    await store.save("u", CORE_MEMORY_FILE, "## 画像\n- 核心\n")
    result = await _memory_tool(store).execute({"name": "画像"}, _ctx())
    assert result.success
    assert "没有名为" in result.output


async def test_render_on_demand_directory_lists_topics():
    entries = [
        __import__(
            "agentcore.runtime.context.consultable", fromlist=["ConsultDirectoryEntry"]
        ).ConsultDirectoryEntry(name="部署流程", summary="怎么起前后端")
    ]
    out = render_on_demand_directory(entries)
    assert "<按需目录>" in out and "</按需目录>" in out
    assert "consult(name)" in out or "`consult(name)`" in out
    assert "部署流程：怎么起前后端" in out


def test_compose_ceo_renders_directory_when_consult_wired():
    topics = [MemoryTopic(name="部署流程", summary="怎么起")]
    base = assemble_system_prompt()
    reg = build_system_skill_registry()
    with_tool = compose_ceo_chat_prompt(
        base,
        skill_registry=reg,
        ceo_tool_names={"delegate", "consult"},
        memory_topics=topics,
    )
    assert "<按需目录>" in with_tool
    assert "部署流程" in with_tool
    without = compose_ceo_chat_prompt(
        base,
        skill_registry=reg,
        ceo_tool_names={"delegate"},
        memory_topics=topics,
    )
    assert "<按需目录>" not in without


def test_compose_worker_directory_includes_summaries():
    topics = [MemoryTopic(name="部署流程", summary="怎么起")]
    base = assemble_system_prompt()
    out = compose_worker_base_prompt(base, memory_topics=topics)
    assert "<按需目录>" in out
    assert "部署流程：怎么起" in out
    assert "name＋一行摘要" in out


def test_compose_worker_base_observe_sections(monkeypatch):
    captured: list[dict] = []

    class _Spy:
        def info(self, event: str, **kwargs: object) -> None:
            captured.append({"event": event, **kwargs})

    monkeypatch.setattr("agentcore.runtime.context.assembler.logger", _Spy())
    topics = [MemoryTopic(name="部署流程", summary="怎么起")]
    compose_worker_base_prompt(assemble_system_prompt(), memory_topics=topics)
    rows = [r for r in captured if r.get("event") == "cost.prompt_assembled"]
    assert len(rows) == 1
    row = rows[0]
    assert row["scope"] == "worker_base"
    assert "shared_base" in row["sections"]
    assert "on_demand_directory" in row["sections"]
    assert row["sections"]["on_demand_directory"] > 0


async def test_wire_worker_consult_when_topics_exist(tmp_path, monkeypatch):
    store = FileMemoryStore(tmp_path)
    await store.save("u", topic_path("部署流程"), "## x\n")
    monkeypatch.setattr(
        "agentcore.runtime.resolve.prepare.default_memory_store", lambda: store
    )
    monkeypatch.setattr(
        "agentcore.tools.ceo_toolset.default_memory_store", lambda: store, raising=False
    )
    from agentcore.runtime.resolve import prepare as prep

    monkeypatch.setattr(prep, "default_memory_store", lambda: store)
    registry = build_worker_registry(
        backend=ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())
    )
    await _wire_worker_consult_tools(
        registry,
        skill_registry=build_system_skill_registry(),
        user_id="u",
    )
    assert "consult" in registry.names
    consult = registry.get("consult")
    assert consult is not None
    names = {e.name for e in await consult.source.list_directory("u")}
    assert "部署流程" in names
    assert "team_orchestration_advanced" not in names
    assert "product_help" not in names
    assert "revising_a_product" not in names
    assert "long_form_landing" in names
    assert "long_form_writing" not in names


async def test_merged_source_directory_and_fetch_agree(tmp_path):
    store = FileMemoryStore(tmp_path)
    await store.save("u", topic_path("部署流程"), "## body\n")
    source = build_merged_consult_source(
        skill_registry=None,
        tool_names=set(),
        memory_store=store,
        folder_id=None,
        include_rules=False,
    )
    entries = await source.list_directory("u")
    names = {e.name for e in entries}
    assert "部署流程" in names
    body = await source.fetch_by_name("u", "部署流程")
    assert body and "body" in body
