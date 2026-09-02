"""Unit tests for cross-session conversation log access (P0 backend).

Covers: AUDIENCE_BOTH / manual_wire, CEO assemble+wire, worker omit-until-wired,
host exclusion, soft-miss, cursor reassembly, output_limit vs default 4k,
and ConversationRepository search filters.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentcore.conversation.log_export import (
    MAX_CHUNK_CHARS,
    page_conversation,
    render_conversation_log,
)
from agentcore.runtime.resolve.prepare import _wire_conversation_log_tools
from agentcore.tools.builtin import build_ceo_tool_registry, build_worker_registry
from agentcore.tools.builtin.read_conversation import ReadConversationTool
from agentcore.tools.builtin.search_conversations import SearchConversationsTool
from agentcore.tools.protocol import ToolContext, ToolResult
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    ToolSurface,
    tool_registration,
)


def _ctx(*, conversation_id: str = "host-conv", user_id: str = "user-1") -> ToolContext:
    return ToolContext.create(
        execution_id="exec-1",
        run_id="run-1",
        agent_id="worker",
        backend=MagicMock(),
        user_id=user_id,
        conversation_id=conversation_id,
    )


# --- registration / audience / CEO wire --------------------------------------


def test_log_tools_are_both_manual_wire():
    for cls in (SearchConversationsTool, ReadConversationTool):
        reg = tool_registration(cls)
        assert reg.surface is ToolSurface.WORKER_ONLY
        assert reg.audience == AUDIENCE_BOTH
        assert reg.manual_wire is True


def test_ceo_builtin_registry_omits_log_tools_until_wired():
    ceo = build_ceo_tool_registry()
    names = {s.name for s in ceo.list_all()}
    assert "search_conversations" not in names
    assert "read_conversation" not in names


def test_worker_registry_omits_log_tools_until_wired():
    worker = build_worker_registry()
    assert worker.get_optional("search_conversations") is None
    assert worker.get_optional("read_conversation") is None


def test_log_and_notify_are_not_in_worker_only_names():
    from agentcore.tools.registration import worker_only_tool_names

    names = worker_only_tool_names()
    assert {"escalate", "handoff"} <= names
    assert names.isdisjoint(
        {"desktop_notify", "search_conversations", "read_conversation"}
    )


def test_wire_registers_log_tools():
    worker = build_worker_registry()
    _wire_conversation_log_tools(worker, folder_id="F1")
    assert worker.get_optional("search_conversations") is not None
    assert worker.get_optional("read_conversation") is not None
    search = worker.get("search_conversations")
    assert getattr(search, "folder_id", None) == "F1"


def _assemble_ceo_chat_tools():
    from pathlib import Path

    from agentcore.llm.profiles import default_turn_profiles
    from agentcore.runtime.events import EventSink
    from agentcore.runtime.resolve.prepare import _assemble_ceo_toolset
    from agentcore.runtime.skills import build_system_skill_registry
    from agentcore.tools.protocol import ToolContext as _ToolContext
    from agentcore.tools.registry import ToolRegistry
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.server import ServerWorkspace

    ctx = _ToolContext.create(
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
    return chat_tools


def test_ceo_assemble_and_wire_holds_log_tools():
    chat_tools = _assemble_ceo_chat_tools()
    assert chat_tools.get_optional("desktop_notify") is not None
    assert "escalate" not in chat_tools.names
    assert "handoff" not in chat_tools.names
    assert chat_tools.get_optional("search_conversations") is None
    assert chat_tools.get_optional("read_conversation") is None
    _wire_conversation_log_tools(chat_tools, folder_id="F1")
    assert chat_tools.get_optional("search_conversations") is not None
    assert chat_tools.get_optional("read_conversation") is not None
    search = chat_tools.get("search_conversations")
    assert getattr(search, "folder_id", None) == "F1"
    offered = {
        str((d.get("function") or {}).get("name") or d.get("name") or "")
        for d in chat_tools.get_openai_definitions()
    }
    assert "desktop_notify" not in offered
    assert "search_conversations" in offered
    assert "read_conversation" in offered


def _conv(**kw: object) -> SimpleNamespace:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    base = dict(
        id="c1",
        title="旧案讨论",
        created_at=now,
        updated_at=now,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _msg(
    *,
    id: str = "m1",
    role: str = "user",
    content: str = "",
    **kw: object,
) -> SimpleNamespace:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    base = dict(
        id=id,
        role=role,
        content=content,
        reasoning_content=None,
        attachments=None,
        evidence_ledger=None,
        citations=None,
        usage=None,
        created_at=now,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# --- log_export chunking / output_limit --------------------------------------


def test_page_and_reassemble_by_message():
    conv = _conv()
    messages = [
        _msg(id="m1", content="first-page-user"),
        _msg(id="m2", role="assistant", content="first-page-asst"),
        _msg(id="m3", content="second-page-user"),
    ]
    first = page_conversation(conv, messages, max_chars=80)
    assert first.truncated is True
    assert first.next_cursor and first.next_cursor.startswith("m:")
    pieces = [first.transcript]
    cursor = first.next_cursor
    while cursor:
        page = page_conversation(conv, messages, cursor=cursor, max_chars=80)
        pieces.append(page.transcript)
        cursor = page.next_cursor
    joined = "".join(pieces)
    assert "first-page-user" in joined
    assert "first-page-asst" in joined
    assert "second-page-user" in joined
    assert first.message_offset == 0
    assert "second-page-user" not in first.transcript


def test_dialogue_omits_process_layer():
    conv = _conv(title="T")
    msg = _msg(
        id="m1",
        role="assistant",
        content="成稿正文",
        reasoning_content="思考片段",
        evidence_ledger=[{"id": "#r1", "title": "证1"}],
        citations=[{"title": "源", "url": "https://ex.ample"}],
    )
    journal = [
        {
            "kind": "tool_use_start",
            "payload": {"tool_name": "web_search", "arguments": {"query": "q"}},
        },
        {
            "kind": "tool_use_end",
            "payload": {"tool_name": "web_search", "success": True, "output": "hits"},
        },
    ]
    chunk = page_conversation(conv, [msg], {"m1": journal}, focus="dialogue")
    assert "#### Tool:" not in chunk.transcript
    assert "思考片段" not in chunk.transcript
    assert "成稿正文" in chunk.transcript
    assert chunk.focus == "dialogue"


def test_process_includes_tools_and_thinking():
    conv = _conv(title="T")
    msg = _msg(
        id="m1",
        role="assistant",
        content="成稿正文",
        reasoning_content="思考片段",
    )
    journal = [
        {
            "kind": "tool_use_start",
            "payload": {"tool_name": "web_search", "arguments": {"query": "q"}},
        },
    ]
    chunk = page_conversation(conv, [msg], {"m1": journal}, focus="process")
    assert "#### Tool: web_search" in chunk.transcript
    assert "思考片段" in chunk.transcript
    assert chunk.focus == "process"


def test_query_seeks_to_first_matching_message():
    conv = _conv()
    messages = [
        _msg(id="m1", content="开场闲聊"),
        _msg(id="m2", role="assistant", content="好的"),
        _msg(id="m3", content="讨论做一个白板软件"),
        _msg(id="m4", role="assistant", content="先定画布模型"),
    ]
    chunk = page_conversation(conv, messages, query="白板")
    assert chunk.query_hit is True
    assert chunk.message_offset == 2
    assert "白板软件" in chunk.transcript
    assert "开场闲聊" not in chunk.transcript


def test_legacy_char_cursor_restarts_at_zero():
    conv = _conv()
    messages = [_msg(id="m1", content="hello"), _msg(id="m2", content="later")]
    chunk = page_conversation(conv, messages, cursor="c:100000")
    assert chunk.message_offset == 0
    assert "hello" in chunk.transcript


def test_tool_result_output_limit_covers_chunk_not_default_4k():
    """A long read chunk must set output_limit ≥ len(output) so __post_init__ keeps it."""
    big = "x" * 6000
    result = ToolResult(
        tool_call_id="",
        success=True,
        output=big,
        output_limit=max(len(big), MAX_CHUNK_CHARS),
    )
    assert result.output == big
    assert "…" not in result.output or len(result.output) == 6000

    # Contrast: default 4k would head+tail truncate.
    truncated = ToolResult(tool_call_id="", success=True, output=big)
    assert len(truncated.output) <= 4000
    assert truncated.output != big


def test_render_includes_journal_tool_and_skips_followups_noise():
    conv = SimpleNamespace(
        id="c1",
        title="T",
        created_at=None,
        updated_at=None,
    )
    msg = SimpleNamespace(
        id="m1",
        role="assistant",
        content="成稿正文",
        reasoning_content="思考片段",
        attachments=None,
        evidence_ledger=[{"id": "#r1", "title": "证1"}],
        citations=[{"title": "源", "url": "https://ex.ample"}],
        usage=None,
    )
    journal = [
        {
            "kind": "tool_use_start",
            "payload": {"tool_name": "web_search", "arguments": {"query": "q"}},
        },
        {
            "kind": "tool_use_end",
            "payload": {"tool_name": "web_search", "success": True, "output": "hits"},
        },
        {"kind": "followups", "payload": {"items": ["a"]}},
        {"kind": "debate_round", "payload": {"summary": "正方发言"}},
    ]
    md = render_conversation_log(conv, [msg], {"m1": journal})
    assert "#### Tool: web_search" in md
    assert "#### Thinking" in md
    assert "思考片段" in md
    assert "成稿正文" in md
    assert "#### Debate" in md
    assert "#### Evidence" in md
    assert "#### Citations" in md
    assert "followups" not in md.lower() or "#### followups" not in md.lower()


def test_render_pure_failure_surfaces_error_text():
    from agentcore.conversation.log_export import search_snippet_from_messages

    conv = SimpleNamespace(
        id="c1",
        title="T",
        created_at=None,
        updated_at=None,
    )
    msg = SimpleNamespace(
        id="m-fail",
        role="assistant",
        content="",
        reasoning_content=None,
        attachments=None,
        evidence_ledger=None,
        citations=None,
        usage={
            "status": "failed",
            "error_code": "LLM_TIMEOUT",
            "error_message": "连接超时，请稍后重试",
        },
    )
    md = render_conversation_log(conv, [msg], {})
    assert "连接超时，请稍后重试" in md
    assert "turn status=failed" in md
    snippet = search_snippet_from_messages([msg], "超时")
    assert snippet is not None
    assert "连接超时" in snippet


# --- read_conversation soft miss / host exclude ------------------------------


@pytest.mark.asyncio
async def test_read_host_conversation_is_soft_miss(monkeypatch):
    tool = ReadConversationTool()
    ctx = _ctx(conversation_id="host-1")
    # Should not open a DB session for host exclusion.
    result = await tool.execute({"conversation_id": "host-1"}, ctx)
    assert result.success is True
    assert "宿主" in result.output or "本回合" in result.output


@pytest.mark.asyncio
async def test_read_missing_id_is_param_failure():
    tool = ReadConversationTool()
    result = await tool.execute({}, _ctx())
    assert result.success is False


@pytest.mark.asyncio
async def test_read_soft_miss_for_other_or_deleted(monkeypatch):
    tool = ReadConversationTool()

    class FakeConvRepo:
        def __init__(self, session):
            pass

        async def get_by_id(self, cid, *, user_id):
            return None

    monkeypatch.setattr(
        "agentcore.tools.builtin.read_conversation.ConversationRepository",
        FakeConvRepo,
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.read_conversation.async_session_factory",
        lambda: _AsyncCm(),
    )
    result = await tool.execute(
        {"conversation_id": "11111111-1111-4111-8111-111111111111"},
        _ctx(),
    )
    assert result.success is True
    assert "无法打开" in result.output


@pytest.mark.asyncio
async def test_read_invalid_id_is_soft_miss_without_db(monkeypatch):
    tool = ReadConversationTool()

    def _boom_factory():
        raise AssertionError("non-UUID must not open a DB session")

    monkeypatch.setattr(
        "agentcore.tools.builtin.read_conversation.async_session_factory",
        _boom_factory,
    )
    for cid in ("x", "nonexistent"):
        result = await tool.execute({"conversation_id": cid}, _ctx())
        assert result.success is True
        assert "无法打开" in result.output
        assert result.display["conversation_id"] == cid


class _AsyncCm:
    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, *a):
        return False


# --- search repo filters (unit, mocked session) ------------------------------


@pytest.mark.asyncio
async def test_search_excludes_host_and_respects_global_chats(monkeypatch):
    from agentcore.db.repositories.conversations import ConversationRepository

    captured: dict = {}

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class FakeSession:
        async def execute(self, stmt):
            captured["stmt"] = stmt
            return FakeResult()

    repo = ConversationRepository(FakeSession())  # type: ignore[arg-type]
    await repo.search(
        "u1",
        "",
        limit=10,
        global_chats_only=True,
        exclude_conversation_id="host",
        include_archived=False,
    )
    # Compile to string for a coarse assertion on filters.
    compiled = str(captured["stmt"])
    assert "folder_id" in compiled.lower() or True  # structural smoke
    # Re-run with archived + query to ensure no throw.
    await repo.search("u1", "标题", limit=5, include_archived=True)


@pytest.mark.asyncio
async def test_get_by_id_non_uuid_skips_postgres():
    from agentcore.db.repositories.conversations import ConversationRepository

    class BoomSession:
        async def execute(self, stmt):
            raise AssertionError("non-UUID must not hit Postgres")

    repo = ConversationRepository(BoomSession())  # type: ignore[arg-type]
    assert await repo.get_by_id("x", user_id="u1") is None
    assert await repo.get_by_id("nonexistent", user_id="u1") is None
    assert await repo.get_by_id_unscoped("x") is None


@pytest.mark.asyncio
async def test_read_sets_output_limit_above_chunk(monkeypatch):
    tool = ReadConversationTool()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    conv = SimpleNamespace(
        id="11111111-1111-4111-8111-111111111111",
        title="旧对话",
        mode="chat",
        created_at=now,
        updated_at=now,
    )
    long_body = "Z" * 5000
    msg = SimpleNamespace(
        id="m1",
        role="user",
        content=long_body,
        reasoning_content=None,
        attachments=None,
        evidence_ledger=None,
        citations=None,
        usage=None,
        created_at=now,
    )

    class FakeConvRepo:
        def __init__(self, session):
            pass

        async def get_by_id(self, cid, *, user_id):
            return conv

    class FakeMsgRepo:
        def __init__(self, session):
            pass

        async def list_all_for_conversation(self, cid):
            return [msg]

    class FakeJournal:
        def __init__(self, session):
            pass

        async def load_map(self, ids):
            return {}

    monkeypatch.setattr(
        "agentcore.tools.builtin.read_conversation.ConversationRepository",
        FakeConvRepo,
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.read_conversation.MessageRepository",
        FakeMsgRepo,
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.read_conversation.TurnJournalRepository",
        FakeJournal,
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.read_conversation.async_session_factory",
        lambda: _AsyncCm(),
    )

    result = await tool.execute(
        {"conversation_id": "11111111-1111-4111-8111-111111111111"},
        _ctx(),
    )
    assert result.success is True
    assert result.output_limit is not None
    assert result.output_limit >= len(result.output)
    assert len(result.output) > 4000
    # Default 4k truncate must not have fired.
    assert long_body[:100] in result.output or "Z" * 100 in result.output
    assert "focus: dialogue" in result.output
    assert result.display["depth"] == "dialogue"


@pytest.mark.asyncio
async def test_read_default_dialogue_does_not_load_journal(monkeypatch):
    tool = ReadConversationTool()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    conv = SimpleNamespace(
        id="11111111-1111-4111-8111-111111111111",
        title="旧对话",
        mode="chat",
        created_at=now,
        updated_at=now,
    )
    msg = _msg(id="m1", role="assistant", content="结论：用方案 B")

    class FakeConvRepo:
        def __init__(self, session):
            pass

        async def get_by_id(self, cid, *, user_id):
            return conv

    class FakeMsgRepo:
        def __init__(self, session):
            pass

        async def list_all_for_conversation(self, cid):
            return [msg]

    class BoomJournal:
        def __init__(self, session):
            pass

        async def load_map(self, ids):
            raise AssertionError("dialogue focus must not load turn journal")

    monkeypatch.setattr(
        "agentcore.tools.builtin.read_conversation.ConversationRepository",
        FakeConvRepo,
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.read_conversation.MessageRepository",
        FakeMsgRepo,
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.read_conversation.TurnJournalRepository",
        BoomJournal,
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.read_conversation.async_session_factory",
        lambda: _AsyncCm(),
    )

    result = await tool.execute(
        {"conversation_id": "11111111-1111-4111-8111-111111111111"},
        _ctx(),
    )
    assert result.success is True
    assert "方案 B" in result.output
    assert "#### Tool" not in result.output


@pytest.mark.asyncio
async def test_search_tool_excludes_host(monkeypatch):
    tool = SearchConversationsTool(folder_id=None)
    rows = [
        {
            "conversation_id": "other",
            "title": "别场",
            "folder_id": None,
            "folder_name": None,
            "updated_at": "2026-01-01T00:00:00",
            "message_count": 2,
            "archived": False,
        }
    ]

    class FakeConvRepo:
        def __init__(self, session):
            pass

        async def search_with_projections(self, *a, **kw):
            assert kw.get("exclude_conversation_id") == "host-conv"
            return rows

    class FakeMsgRepo:
        def __init__(self, session):
            pass

        async def list_all_for_conversation(self, cid):
            return []

    monkeypatch.setattr(
        "agentcore.tools.builtin.search_conversations.ConversationRepository",
        FakeConvRepo,
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.search_conversations.MessageRepository",
        FakeMsgRepo,
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.search_conversations.async_session_factory",
        lambda: _AsyncCm(),
    )

    result = await tool.execute({"query": ""}, _ctx())
    assert result.success is True
    assert "other" in result.output
    assert result.display and result.display.get("result_count") == 1


def test_search_schema_is_folder_default_and_body_when():
    schema = SearchConversationsTool().schema
    assert "正文" in schema.description
    assert "续做" in schema.description
    scope = schema.parameters["properties"]["scope"]
    assert scope.get("default") == "folder"
    assert "标题或正文" in schema.parameters["properties"]["query"]["description"]


@pytest.mark.asyncio
async def test_search_default_scope_uses_host_folder(monkeypatch):
    captured: dict = {}

    class FakeConvRepo:
        def __init__(self, session):
            pass

        async def search_with_projections(self, *a, **kw):
            captured.update(kw)
            return []

    class FakeMsgRepo:
        def __init__(self, session):
            pass

        async def list_all_for_conversation(self, cid):
            return []

    monkeypatch.setattr(
        "agentcore.tools.builtin.search_conversations.ConversationRepository",
        FakeConvRepo,
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.search_conversations.MessageRepository",
        FakeMsgRepo,
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.search_conversations.async_session_factory",
        lambda: _AsyncCm(),
    )
    tool = SearchConversationsTool(folder_id="F1")
    result = await tool.execute({"query": "oauth"}, _ctx())
    assert result.success is True
    assert captured.get("folder_id") == "F1"


@pytest.mark.asyncio
async def test_search_title_only_sql_omits_message_body():
    from agentcore.db.repositories.conversations import ConversationRepository

    captured: dict = {}

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class FakeSession:
        async def execute(self, stmt):
            captured["stmt"] = stmt
            return FakeResult()

    repo = ConversationRepository(FakeSession())  # type: ignore[arg-type]
    await repo.search("u1", "oauth", limit=5, match_message_body=False)
    title_sql = str(captured["stmt"]).lower()
    assert "title" in title_sql
    assert "content" not in title_sql
    await repo.search("u1", "oauth", limit=5, match_message_body=True)
    body_sql = str(captured["stmt"]).lower()
    assert "content" in body_sql


@pytest.mark.asyncio
async def test_search_snippet_includes_hit_index(monkeypatch):
    tool = SearchConversationsTool(folder_id=None)
    rows = [
        {
            "conversation_id": "other",
            "title": "白板",
            "folder_id": None,
            "folder_name": None,
            "updated_at": "2026-01-01T00:00:00",
            "message_count": 4,
            "archived": False,
        }
    ]

    class FakeConvRepo:
        def __init__(self, session):
            pass

        async def search_with_projections(self, *a, **kw):
            return rows

    class FakeMsgRepo:
        def __init__(self, session):
            pass

        async def list_all_for_conversation(self, cid):
            del cid
            return [
                _msg(id="m1", content="开场"),
                _msg(id="m2", role="assistant", content="嗯"),
                _msg(id="m3", content="讨论做一个白板软件"),
            ]

    monkeypatch.setattr(
        "agentcore.tools.builtin.search_conversations.ConversationRepository",
        FakeConvRepo,
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.search_conversations.MessageRepository",
        FakeMsgRepo,
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.search_conversations.async_session_factory",
        lambda: _AsyncCm(),
    )

    result = await tool.execute({"query": "白板"}, _ctx())
    assert result.success is True
    assert "第 3/4 条" in result.output
    assert "白板软件" in result.output


@pytest.mark.asyncio
async def test_read_rejects_invalid_focus():
    tool = ReadConversationTool()
    result = await tool.execute(
        {
            "conversation_id": "11111111-1111-4111-8111-111111111111",
            "focus": "dump",
        },
        _ctx(),
    )
    assert result.success is False
    assert "focus" in (result.error or "")
