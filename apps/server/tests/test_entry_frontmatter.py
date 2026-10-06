"""Entry frontmatter: sole writable source for apply / description / paths."""

from __future__ import annotations

import pytest

from agentcore.documents.frontmatter import (
    FrontmatterEditError,
    FrontmatterError,
    ParsedFrontmatter,
    ensure_apply_key,
    frontmatter_error_message,
    parse_entry_frontmatter,
    set_entry_frontmatter,
    set_entry_frontmatter_total,
    strip_entry_frontmatter,
)


def test_no_frontmatter_defaults_on_demand():
    parsed = parse_entry_frontmatter("# hello\n")
    assert isinstance(parsed, ParsedFrontmatter)
    assert parsed.apply == "on_demand"
    assert parsed.description == ""
    assert parsed.has_frontmatter is False
    assert parsed.body == "# hello\n"


def test_missing_apply_key_defaults_on_demand():
    content = "---\ndescription: 摘要\n---\nbody\n"
    parsed = parse_entry_frontmatter(content)
    assert isinstance(parsed, ParsedFrontmatter)
    assert parsed.apply == "on_demand"
    assert parsed.description == "摘要"
    assert parsed.apply_present is False
    assert parsed.description_present is True


def test_parse_always_and_description():
    content = "---\napply: always\ndescription: 一行\n---\n正文\n"
    parsed = parse_entry_frontmatter(content)
    assert isinstance(parsed, ParsedFrontmatter)
    assert parsed.apply == "always"
    assert parsed.description == "一行"
    assert parsed.body == "正文\n"


def test_unclosed_fence_is_an_error():
    err = parse_entry_frontmatter("---\napply: always\nno close")
    assert isinstance(err, FrontmatterError)
    assert err.message == "unclosed frontmatter"
    assert frontmatter_error_message("---\napply: always\nno close") == "unclosed frontmatter"


def test_apply_value_is_case_insensitive():
    parsed = parse_entry_frontmatter("---\napply: Always\n---\n")
    assert isinstance(parsed, ParsedFrontmatter)
    assert parsed.apply == "always"


def test_unrecognized_apply_value_is_an_error():
    """A stated intent we cannot read must not silently downgrade an entry to 按需."""
    for raw in ("conditional", "alway", ""):
        err = parse_entry_frontmatter(f"---\napply: {raw}\n---\n")
        assert isinstance(err, FrontmatterError), raw
        assert "apply" in err.message


def test_unknown_keys_ignored_on_parse():
    content = "---\nglobs: [\"**/*.ts\"]\napply: always\nalwaysApply: true\n---\nx\n"
    parsed = parse_entry_frontmatter(content)
    assert isinstance(parsed, ParsedFrontmatter)
    assert parsed.apply == "always"


def test_roundtrip_preserves_unknown_keys_comments_and_order():
    original = (
        "---\n"
        "# keep me\n"
        "globs: [\"**/*.md\"]\n"
        "apply: on_demand  # inline\n"
        "description: old\n"
        "custom: leave\n"
        "---\n"
        "body line\n"
    )
    edited = set_entry_frontmatter(original, apply="always")
    # Unknown keys / comment line / key order / inline comment preserved.
    assert "# keep me" in edited
    assert 'globs: ["**/*.md"]' in edited
    assert "custom: leave" in edited
    assert "apply: always # inline" in edited
    assert "description: old" in edited
    # globs still before apply; custom still after description.
    assert edited.index("globs:") < edited.index("apply:")
    assert edited.index("description:") < edited.index("custom:")
    assert edited.endswith("body line\n")

    parsed = parse_entry_frontmatter(edited)
    assert isinstance(parsed, ParsedFrontmatter)
    assert parsed.apply == "always"
    assert parsed.description == "old"


def test_roundtrip_add_missing_key_appends_before_close():
    original = "---\nglobs: x\n---\nbody\n"
    edited = set_entry_frontmatter(original, apply="always", description="摘要")
    assert edited == (
        "---\n"
        "globs: x\n"
        "apply: always\n"
        "description: 摘要\n"
        "---\n"
        "body\n"
    )


def test_set_prepends_block_when_absent():
    edited = set_entry_frontmatter("hello\n", apply="always")
    assert edited.startswith("---\napply: always\n---\n")
    assert edited.endswith("hello\n")


def test_set_raises_on_unclosed():
    with pytest.raises(FrontmatterEditError):
        set_entry_frontmatter("---\napply: always\n", apply="on_demand")


def test_total_unclosed_preserves_column_always():
    """Migration corpus: opening ``---`` with no close must not silently downgrade always."""
    original = "---\nthis was never frontmatter, just prose that starts with a fence\n"
    out, prepended = set_entry_frontmatter_total(original, apply="always")
    assert prepended is True
    assert out == "---\napply: always\n---\n" + original
    parsed = parse_entry_frontmatter(out)
    assert isinstance(parsed, ParsedFrontmatter)
    assert parsed.apply == "always"
    assert parsed.body == original
    # Runtime path still rejects the same corpus.
    with pytest.raises(FrontmatterEditError):
        set_entry_frontmatter(original, apply="always")


def test_total_unclosed_with_bom_demotes_whole_text():
    original = "\ufeff---\nno close"
    out, prepended = set_entry_frontmatter_total(original, apply="on_demand")
    assert prepended is True
    assert out == "\ufeff---\napply: on_demand\n---\n---\nno close"
    parsed = parse_entry_frontmatter(out)
    assert isinstance(parsed, ParsedFrontmatter)
    assert parsed.apply == "on_demand"
    assert parsed.body == "---\nno close"


def test_total_matches_set_when_minimal_edit_works():
    cases = [
        ("hello\n", "always"),
        ("---\nglobs: x\n---\nbody\n", "always"),
        ("---\napply: on_demand\n---\n", "always"),
        ("", "on_demand"),
    ]
    for content, mode in cases:
        expected = set_entry_frontmatter(content, apply=mode)  # type: ignore[arg-type]
        out, prepended = set_entry_frontmatter_total(content, apply=mode)  # type: ignore[arg-type]
        assert prepended is False
        assert out == expected


def test_ensure_apply_key_does_not_overwrite():
    content = "---\napply: on_demand\n---\n"
    assert ensure_apply_key(content, "always") == content


def test_ensure_apply_key_inserts_when_absent():
    content = "---\ndescription: x\n---\n"
    out = ensure_apply_key(content, "always")
    parsed = parse_entry_frontmatter(out)
    assert isinstance(parsed, ParsedFrontmatter)
    assert parsed.apply == "always"
    assert parsed.description == "x"


def test_strip_frontmatter():
    content = "---\napply: always\n---\nbody\n"
    assert strip_entry_frontmatter(content) == "body\n"
    assert strip_entry_frontmatter("plain") == "plain"
    assert strip_entry_frontmatter("---\nunclosed") is None


def test_derive_indexes_match_parse():
    from agentcore.db.repositories.documents import _derive_indexes

    mode, desc = _derive_indexes("---\napply: always\ndescription: d\n---\n")
    assert mode == "always" and desc == "d"
    mode, desc = _derive_indexes("no fm")
    assert mode == "on_demand" and desc == ""
    mode, desc = _derive_indexes("---\nunclosed")
    assert mode == "on_demand" and desc == ""

