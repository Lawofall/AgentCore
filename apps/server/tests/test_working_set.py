"""Conversation working set: extract / merge / render (no full file bytes)."""

from __future__ import annotations

import pytest

from agentcore.runtime.context.working_set import (
    WorkingSetItem,
    extract_working_set_items,
    file_working_set_digest,
    item_from_tool_call,
    merge_working_set,
    render_file_ledger,
    render_working_set,
)
from agentcore.runtime.facts import FactKind


def _entry(name: str, arguments: str, *, success: bool = True) -> dict:
    return {
        "kind": FactKind.TOOL_CALL.value,
        "payload": {"name": name, "arguments": arguments, "success": success},
    }


def test_item_from_successful_file_read():
    item = item_from_tool_call(
        name="file_read",
        arguments='{"path":"src/foo.py"}',
    )
    assert item == WorkingSetItem(path="src/foo.py", action="read")


def test_item_normalizes_windows_path_and_file_path_key():
    item = item_from_tool_call(
        name="file_write",
        arguments=r'{"file_path":"docs\\bar.md"}',
    )
    assert item is not None
    assert item.path == "docs/bar.md"
    assert item.action == "write"


def test_item_skips_failed_and_non_file_tools():
    assert (
        item_from_tool_call(
            name="file_read",
            arguments='{"path":"a.py"}',
            success=False,
        )
        is None
    )
    assert item_from_tool_call(name="web_search", arguments='{"query":"x"}') is None
    assert item_from_tool_call(name="file_list", arguments='{"path":"."}') is None


def test_item_keeps_read_window_only_when_not_full_file():
    full = item_from_tool_call(name="file_read", arguments='{"path":"a.py","offset":1}')
    assert full is not None and full.start_line is None
    window = item_from_tool_call(
        name="file_read",
        arguments='{"path":"a.py","offset":40,"limit":20}',
    )
    assert window == WorkingSetItem(
        path="a.py", action="read", start_line=40, end_line=59
    )


def test_extract_skips_non_tool_and_empty():
    assert extract_working_set_items(None) == []
    assert extract_working_set_items([{"kind": "note", "payload": {}}]) == []
    items = extract_working_set_items(
        [
            _entry("file_read", '{"path":"a.py"}'),
            _entry("str_replace", '{"path":"a.py"}'),
            _entry("file_write", '{"path":"b.md"}'),
        ]
    )
    assert [i.path for i in items] == ["a.py", "a.py", "b.md"]
    assert items[1].action == "write"


def test_merge_last_action_wins_newest_first_and_caps():
    hits = [
        WorkingSetItem(path="a.py", action="read"),
        WorkingSetItem(path="b.md", action="write"),
        WorkingSetItem(path="a.py", action="write"),
        WorkingSetItem(path="c.ts", action="read"),
    ]
    merged = merge_working_set(hits, max_paths=2)
    assert merged == [
        WorkingSetItem(path="c.ts", action="read"),
        WorkingSetItem(path="a.py", action="write"),
    ]


def test_file_working_set_digest_from_read_result():
    body = (
        "class Loader:\n"
        "    def load_chat_context(self):\n"
        "        return []\n"
    )
    digest = file_working_set_digest(
        name="file_read",
        arguments='{"path":"src/history.py"}',
        result=body,
    )
    assert digest
    assert "Loader" in digest or "load_chat_context" in digest
    assert "【自动" not in digest
    assert "\n" not in digest
    assert len(digest) <= 120


def test_file_working_set_digest_from_write_body():
    digest = file_working_set_digest(
        name="file_write",
        arguments='{"path":"docs/a.md","content":"# 标题\\n\\n正文"}',
        result="已写入",
    )
    assert digest
    assert "标题" in digest


def test_file_working_set_digest_skips_non_file_and_failure():
    assert (
        file_working_set_digest(
            name="web_search", arguments='{"query":"x"}', result="hits"
        )
        == ""
    )
    assert (
        file_working_set_digest(
            name="file_read",
            arguments='{"path":"a.py"}',
            result="class A: pass",
            success=False,
        )
        == ""
    )


def test_extract_keeps_persisted_digest():
    items = extract_working_set_items(
        [
            {
                "kind": FactKind.TOOL_CALL.value,
                "payload": {
                    "name": "file_read",
                    "arguments": '{"path":"a.py"}',
                    "success": True,
                    "working_set_digest": "Foo, bar()",
                },
            }
        ]
    )
    assert items == [
        WorkingSetItem(path="a.py", action="read", digest="Foo, bar()")
    ]


def test_render_working_set_empty_drops_section():
    assert render_working_set([]) == ""
    text = render_working_set(
        [
            WorkingSetItem(path="src/foo.py", action="read", start_line=10, end_line=30),
            WorkingSetItem(path="docs/a.md", action="write"),
        ]
    )
    assert text.startswith("<工作集>")
    assert text.endswith("</工作集>")
    assert "正文以磁盘为准" in text
    assert "file_read" in text
    assert "- read src/foo.py:10-30" in text
    assert "- write docs/a.md" in text
    with_digest = render_working_set(
        [WorkingSetItem(path="src/foo.py", action="read", digest="Foo, bar()")]
    )
    assert "- read src/foo.py  ·  Foo, bar()" in with_digest
    # 补集禁止：正向已覆盖「要细节就重读」，不写「不要凭记忆」。
    assert "不要" not in text
    assert "禁止" not in text


@pytest.mark.asyncio
async def test_build_working_set_block_empty_without_hits(monkeypatch):
    async def _empty(**_kwargs):
        return []

    monkeypatch.setattr(
        "agentcore.runtime.context.working_set._load_hits_from_db", _empty
    )
    from agentcore.runtime.context.working_set import build_working_set_block

    assert await build_working_set_block(conversation_id="c1") == ""


@pytest.mark.asyncio
async def test_build_working_set_block_merges_live_entries(monkeypatch):
    async def _empty(**_kwargs):
        return []

    monkeypatch.setattr(
        "agentcore.runtime.context.working_set._load_hits_from_db", _empty
    )
    from agentcore.runtime.context.working_set import build_working_set_block

    text = await build_working_set_block(
        conversation_id="c1",
        live_entries=[_entry("file_read", '{"path":"live.py"}')],
    )
    assert "- read live.py" in text
    assert text.startswith("<工作集>")


@pytest.mark.asyncio
async def test_build_working_set_block_survives_db_failure(monkeypatch):
    async def _boom(**_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "agentcore.runtime.context.working_set._load_hits_from_db", _boom
    )
    from agentcore.runtime.context.working_set import build_working_set_block

    assert await build_working_set_block(conversation_id="c1") == ""


def test_render_file_ledger_is_bare_list():
    assert render_file_ledger([]) == ""
    ledger = render_file_ledger([WorkingSetItem(path="a.py", action="read")])
    assert ledger == "- read a.py"
    assert "<工作集>" not in ledger
    with_digest = render_file_ledger(
        [WorkingSetItem(path="a.py", action="read", digest="Foo, bar()")]
    )
    assert with_digest == "- read a.py  ·  Foo, bar()"
