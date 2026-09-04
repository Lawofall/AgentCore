"""This-conversation mutation list for the CEO file index (writes / exports, not reads)."""

from __future__ import annotations

from agentcore.runtime.context.conversation_edits import (
    ConversationEdit,
    edits_from_tool_call,
    extract_conversation_edits,
    load_conversation_edits,
    merge_conversation_edits,
)
from agentcore.runtime.facts import FactKind
from agentcore.tools.file_products import file_product, render_file_products_marker


def test_ignores_reads_and_failures():
    assert edits_from_tool_call(name="file_read", arguments='{"path":"a.md"}') == []
    assert (
        edits_from_tool_call(
            name="file_write",
            arguments='{"path":"a.md"}',
            success=False,
        )
        == []
    )


def test_write_append_replace_delete_labels():
    assert edits_from_tool_call(
        name="file_write", arguments='{"path":"稿.md"}'
    ) == [ConversationEdit(path="稿.md", label="写过")]
    assert edits_from_tool_call(
        name="str_replace", arguments='{"path":"稿.md"}'
    ) == [ConversationEdit(path="稿.md", label="更新")]
    assert edits_from_tool_call(
        name="file_append", arguments='{"path":"稿.md"}'
    ) == [ConversationEdit(path="稿.md", label="更新")]
    assert edits_from_tool_call(
        name="file_delete", arguments='{"path":"稿.md"}'
    ) == [ConversationEdit(path="稿.md", label="已删除")]


def test_docx_export_lists_sibling_not_source_md():
    assert edits_from_tool_call(
        name="md_to_docx",
        arguments='{"path":"AgentCore/文档/工作稿/民事上诉状.md"}',
    ) == [
        ConversationEdit(
            path="AgentCore/文档/工作稿/民事上诉状.docx",
            label="已转 Word",
        )
    ]


def test_docx_prefers_file_products_marker_over_sibling():
    marker = render_file_products_marker(
        [file_product("out/final.docx", derived_from="src.md")]
    )
    assert edits_from_tool_call(
        name="md_to_docx",
        arguments='{"path":"src.md"}',
        result=f"已导出\n{marker}",
    ) == [ConversationEdit(path="out/final.docx", label="已转 Word")]


def test_pdf_and_copy_move():
    assert edits_from_tool_call(
        name="md_to_pdf", arguments='{"path":"a.md"}'
    ) == [ConversationEdit(path="a.pdf", label="已转 PDF")]
    assert edits_from_tool_call(
        name="file_copy",
        arguments='{"source":"a.md","destination":"b.md"}',
    ) == [ConversationEdit(path="b.md", label="已复制")]
    assert edits_from_tool_call(
        name="file_move",
        arguments='{"source":"a.md","destination":"c.md"}',
    ) == [ConversationEdit(path="c.md", label="已移动")]


def test_batch_skips_mkdir():
    items = edits_from_tool_call(
        name="file_batch",
        arguments=(
            '{"operations":['
            '{"op":"mkdir","path":"dir"},'
            '{"op":"delete","path":"gone.md"},'
            '{"op":"copy","source":"a.md","destination":"b.md"}'
            "]}"
        ),
    )
    assert items == [
        ConversationEdit(path="gone.md", label="已删除"),
        ConversationEdit(path="b.md", label="已复制"),
    ]


def test_merge_last_write_wins_newest_first_capped():
    chronological = extract_conversation_edits(
        [
            {
                "kind": FactKind.TOOL_CALL.value,
                "payload": {"name": "file_write", "arguments": '{"path":"a.md"}'},
            },
            {
                "kind": FactKind.TOOL_CALL.value,
                "payload": {"name": "str_replace", "arguments": '{"path":"a.md"}'},
            },
            {
                "kind": FactKind.TOOL_CALL.value,
                "payload": {"name": "file_write", "arguments": '{"path":"b.md"}'},
            },
        ]
    )
    merged = merge_conversation_edits(chronological, max_paths=8)
    assert merged[0] == ConversationEdit(path="b.md", label="写过")
    assert merged[1] == ConversationEdit(path="a.md", label="更新")

    many = [
        ConversationEdit(path=f"f{i}.md", label="写过") for i in range(12)
    ]
    assert len(merge_conversation_edits(many, max_paths=8)) == 8
    assert merge_conversation_edits(many, max_paths=8)[0].path == "f11.md"


async def test_load_conversation_edits_db_failure_returns_empty(monkeypatch):
    async def boom(**_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "agentcore.runtime.context.conversation_edits._load_hits_from_db",
        boom,
    )
    assert await load_conversation_edits(conversation_id="cid") == []
