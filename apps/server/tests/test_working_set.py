"""Journal file-path ledger: extract / merge / compact fold (no product-prompt XML)."""

from __future__ import annotations

import pytest

from agentcore.runtime.context.working_set import (
    WorkingSetItem,
    extract_working_set_items,
    file_working_set_digest,
    item_from_tool_call,
    load_working_set_items,
    merge_working_set,
    render_file_ledger,
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
    assert "非全文" not in digest
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


@pytest.mark.asyncio
async def test_load_working_set_items_empty_without_hits(monkeypatch):
    async def _empty(**_kwargs):
        return []

    monkeypatch.setattr(
        "agentcore.runtime.context.working_set._load_hits_from_db", _empty
    )
    assert await load_working_set_items(conversation_id="c1") == []


@pytest.mark.asyncio
async def test_load_working_set_items_merges_live_entries(monkeypatch):
    async def _empty(**_kwargs):
        return []

    monkeypatch.setattr(
        "agentcore.runtime.context.working_set._load_hits_from_db", _empty
    )
    items = await load_working_set_items(
        conversation_id="c1",
        live_entries=[_entry("file_read", '{"path":"live.py"}')],
    )
    assert [i.path for i in items] == ["live.py"]
    assert items[0].action == "read"


@pytest.mark.asyncio
async def test_load_working_set_items_survives_db_failure(monkeypatch):
    async def _boom(**_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "agentcore.runtime.context.working_set._load_hits_from_db", _boom
    )
    assert await load_working_set_items(conversation_id="c1") == []


def test_render_file_ledger_is_bare_list():
    assert render_file_ledger([]) == ""
    ledger = render_file_ledger([WorkingSetItem(path="a.py", action="read")])
    assert ledger == "- read a.py"
    assert "<工作集>" not in ledger
    with_digest = render_file_ledger(
        [WorkingSetItem(path="a.py", action="read", digest="Foo, bar()")]
    )
    assert with_digest == "- read a.py  ·  Foo, bar()"
