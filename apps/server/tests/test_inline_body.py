"""Inline body markers in user content."""

from agentcore.core.inline_body import (
    apply_inline_body,
    has_inline_markers,
    mention_inline_stub,
    migrate_legacy_draft,
    parse_inline_body,
    plain_text,
    render_inline_labels,
    render_request_text,
    serialize_inline_body,
    token,
    weave_inline_body,
)


def test_roundtrip_mixed():
    raw = f"先看{token('A', 0)}再改{token('M', 0)}收工"
    assert parse_inline_body(raw) == [
        ("text", "先看"),
        ("attachment", 0),
        ("text", "再改"),
        ("mention", 0),
        ("text", "收工"),
    ]
    assert serialize_inline_body(parse_inline_body(raw)) == raw


def test_plain_text_and_has_markers():
    assert plain_text(f"按这个{token('A', 0)}原则") == "按这个原则"
    assert has_inline_markers(f"x{token('A', 0)}") is True
    assert has_inline_markers("按这个原则") is False


def test_migrate_legacy_appends_once():
    assert migrate_legacy_draft("hello", 2, 1) == (
        f"hello{token('A', 0)}{token('A', 1)}{token('M', 0)}"
    )
    already = f"hello{token('A', 0)}"
    assert migrate_legacy_draft(already, 2, 0) == already


def test_render_labels_keeps_order():
    content = f"左{token('A', 0)}中{token('M', 0)}右{token('A', 1)}"
    out = render_inline_labels(
        content,
        [{"name": "a.md", "kind": "file"}, {"name": "b/", "kind": "dir"}],
        [{"role": "研究员", "agent_id": "w1"}],
    )
    assert out == "左[文件 a.md]中[点名 研究员]右[文件夹 b/]"


def test_weave_puts_file_bodies_at_pills():
    content = f"先{token('A', 0)}后{token('M', 0)}"
    woven = weave_inline_body(
        content,
        ["--- File: a.md ---\nbody"],
        [mention_inline_stub({"role": "写手", "agent_id": "w"})],
    )
    assert woven is not None
    assert "先" in woven
    assert "--- File: a.md ---\nbody" in woven
    assert "（点名 写手）" in woven
    assert woven.index("先") < woven.index("--- File")
    assert woven.index("--- File") < woven.index("（点名 写手）")


def test_apply_inline_body_noop_without_markers():
    msg = apply_inline_body("hello", [], [])
    assert msg == "hello"


def test_apply_inline_body_weaves_file_at_pill():
    msg = apply_inline_body(
        f"x{token('A', 0)}{token('M', 0)}",
        ["BLOCK"],
        [{"role": "法务", "agent_id": "a"}],
    )
    assert "BLOCK" in msg
    assert "（点名 法务）" in msg


def test_render_request_text_drops_attachment_pills():
    raw = f"整理下{token('A', 0)}这个"
    out = render_request_text(raw, None, 1)
    assert out == "整理下这个"
    assert "---" not in out


def test_render_request_text_keeps_mention_stubs():
    raw = f"请{token('M', 0)}看"
    out = render_request_text(raw, [{"role": "研究员", "agent_id": "w"}], 0)
    assert out == "请（点名 研究员）看"
