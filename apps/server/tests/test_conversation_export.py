"""Unit tests for the export serializers and share snapshot / public render.

Pure functions over ORM objects (no DB): Markdown / JSON export shape, the
content-only share snapshot, and — critically for a public unauthenticated surface
— that the share HTML renderer neutralizes XSS (raw HTML escaped, ``javascript:``
links rejected).
"""

from datetime import UTC, datetime

from agentcore.conversation.export import (
    conversation_to_json,
    conversation_to_markdown,
)
from agentcore.conversation.sharing import build_share_snapshot, render_share_html
from agentcore.db.models import Conversation, Message

_TS = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)


def _conv(title: str = "测试对话") -> Conversation:
    return Conversation(id="11111111-1111-1111-1111-111111111111", title=title)


def _msg(role: str, content: str | None, **kw) -> Message:
    return Message(role=role, content=content, created_at=_TS, **kw)


def test_markdown_has_title_roles_and_content():
    conv = _conv("我的对话")
    messages = [
        _msg("user", "你好"),
        _msg("assistant", "你好，我能帮你什么？"),
    ]
    md = conversation_to_markdown(conv, messages)
    assert md.startswith("# 我的对话")
    assert "## 用户" in md
    assert "## GAgent" in md
    assert "你好，我能帮你什么？" in md


def test_markdown_includes_citations_and_attachments():
    conv = _conv()
    messages = [
        _msg(
            "assistant",
            "见来源。",
            citations=[{"url": "https://e.com", "title": "示例"}],
            attachments=[{"name": "report.pdf"}],
        ),
    ]
    md = conversation_to_markdown(conv, messages)
    assert "[示例](https://e.com)" in md
    assert "report.pdf" in md


def test_markdown_skips_empty_and_non_dialogue_rows():
    conv = _conv()
    messages = [
        _msg("user", "问题"),
        _msg("assistant", "   "),  # blank assistant turn — dropped
        _msg("system", "internal"),  # non-dialogue role — dropped
    ]
    md = conversation_to_markdown(conv, messages)
    assert "问题" in md
    assert "internal" not in md
    # Only the one user section remains.
    assert md.count("## ") == 1


def test_markdown_keeps_pure_failure_from_error_metadata():
    """Empty content + failed usage still exports the structured error sentence."""
    conv = _conv()
    messages = [
        _msg("user", "问题"),
        _msg(
            "assistant",
            "",
            usage={
                "status": "failed",
                "error_code": "LLM_TIMEOUT",
                "error_message": "连接超时，请稍后重试",
            },
        ),
    ]
    md = conversation_to_markdown(conv, messages)
    assert "连接超时，请稍后重试" in md
    assert md.count("## ") == 2


def test_markdown_pure_failure_from_journal_error():
    assistant = _msg("assistant", "", usage={"status": "failed", "finish_reason": "error"})
    assistant.id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    journal_map = {
        assistant.id: [
            {
                "kind": "turn_end",
                "payload": {
                    "finish_reason": "error",
                    "error": {"code": "LLM_ERROR", "message": "上游模型不可用"},
                },
            }
        ]
    }
    md = conversation_to_markdown(_conv(), [_msg("user", "q"), assistant], journal_map=journal_map)
    assert "上游模型不可用" in md


def test_markdown_untitled_fallback():
    md = conversation_to_markdown(_conv(""), [_msg("user", "hi")])
    assert md.startswith("# 未命名对话")


def test_json_export_is_full_fidelity():
    conv = _conv("J")
    messages = [
        _msg("user", "q", attachments=[{"name": "a.txt"}]),
        _msg(
            "assistant",
            "a",
            reasoning_content="思考",
            citations=[{"url": "https://x.io", "title": "X"}],
            usage={"status": "complete", "finish_reason": "cancelled"},
        ),
    ]
    out = conversation_to_json(conv, messages)
    assert out["title"] == "J"
    assert out["conversation_id"] == conv.id
    assert "exported_at" in out
    assert len(out["messages"]) == 2
    assistant = out["messages"][1]
    assert assistant["reasoning_content"] == "思考"
    # finish_reason is projected from usage when journal is absent.
    assert assistant["finish_reason"] == "cancelled"
    assert assistant["citations"] == [{"url": "https://x.io", "title": "X"}]


def test_json_export_finish_reason_from_journal():
    """Journal ``turn_end`` is the preferred source over usage."""
    conv = _conv("J")
    assistant = _msg(
        "assistant",
        "a",
        usage={"status": "complete", "finish_reason": "cancelled"},
    )
    assistant.id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    journal_map = {
        assistant.id: [
            {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": None},
        ]
    }
    out = conversation_to_json(conv, [assistant], journal_map=journal_map)
    assert out["messages"][0]["finish_reason"] == "end_turn"


def test_json_export_omits_finish_reason_when_unknown():
    """No journal / usage finish_reason → omit the key (no AttributeError, no fake)."""
    conv = _conv("J")
    out = conversation_to_json(conv, [_msg("assistant", "a", usage={"status": "complete"})])
    assert "finish_reason" not in out["messages"][0]


def test_json_export_empty_failure_adds_visible_text():
    """Pure failure: content stays empty; visible_text mirrors Markdown readable projection."""
    conv = _conv("J")
    assistant = _msg(
        "assistant",
        "",
        usage={
            "status": "failed",
            "error_code": "LLM_TIMEOUT",
            "error": {"code": "LLM_TIMEOUT", "message": "连接超时，请稍后重试"},
        },
    )
    out = conversation_to_json(conv, [_msg("user", "q"), assistant])
    row = out["messages"][1]
    assert row["content"] == ""
    assert row["visible_text"] == "连接超时，请稍后重试"
    # Successful / non-empty rows must not invent visible_text.
    assert "visible_text" not in out["messages"][0]


def test_json_export_empty_failure_visible_text_from_journal():
    assistant = _msg("assistant", "", usage={"status": "failed", "finish_reason": "error"})
    assistant.id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    journal_map = {
        assistant.id: [
            {
                "kind": "turn_end",
                "payload": {
                    "finish_reason": "error",
                    "error": {"code": "PIPELINE_ERROR", "message": "管线崩溃"},
                },
            }
        ]
    }
    out = conversation_to_json(_conv(), [assistant], journal_map=journal_map)
    row = out["messages"][0]
    assert row["content"] == ""
    assert row["visible_text"] == "管线崩溃"


def test_share_snapshot_is_content_only():
    messages = [
        _msg("user", "问题", reasoning_content="should not leak"),
        _msg("assistant", "回答", usage={"input": 10}),
        _msg("assistant", ""),  # empty — dropped
        _msg("tool", "internal"),  # non-dialogue — dropped
    ]
    snap = build_share_snapshot(messages)
    assert len(snap) == 2
    assert snap[0] == {"role": "user", "content": "问题", "created_at": _TS.isoformat()}
    # Only role/content/created_at — no reasoning / usage keys leak.
    assert set(snap[1].keys()) == {"role", "content", "created_at"}


def test_share_html_renders_markdown_and_title():
    snap = [
        {"role": "user", "content": "**bold** question", "created_at": _TS.isoformat()},
        {"role": "assistant", "content": "answer with `code`", "created_at": None},
    ]
    page = render_share_html(title="标题", snapshot=snap, created_at=_TS)
    assert "<title>标题</title>" in page
    assert "<strong>bold</strong>" in page
    assert "<code>code</code>" in page
    assert "用户" in page and "GAgent" in page


def test_share_html_escapes_raw_html_xss():
    # Untrusted content with an HTML/script injection attempt must be escaped, never
    # passed through (html=False in the renderer).
    snap = [
        {
            "role": "assistant",
            "content": "<script>alert('xss')</script>\n<img src=x onerror=alert(1)>",
            "created_at": None,
        }
    ]
    page = render_share_html(title="t", snapshot=snap, created_at=_TS)
    # No live tags: the injection is rendered as escaped text, not real elements.
    assert "<script" not in page
    assert "<img" not in page
    assert "&lt;script&gt;" in page


def test_share_html_rejects_javascript_link():
    snap = [
        {
            "role": "assistant",
            "content": "[click me](javascript:alert(1))",
            "created_at": None,
        }
    ]
    page = render_share_html(title="t", snapshot=snap, created_at=_TS)
    # markdown-it rejects the dangerous scheme: no anchor / no executable href is
    # emitted (the raw text may remain, inert).
    assert 'href="javascript:' not in page
    assert "<a " not in page


def test_share_html_escapes_title_xss():
    page = render_share_html(title="<script>alert(1)</script>", snapshot=[], created_at=_TS)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert "（空对话）" in page
