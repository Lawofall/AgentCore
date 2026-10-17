"""Path-scoped rules: glob, nearest-name resolve, index, and quota."""

from __future__ import annotations

from agentcore.documents.frontmatter import (
    FrontmatterError,
    parse_entry_frontmatter,
    set_entry_frontmatter,
)
from agentcore.documents.path_rules import glob_match
from agentcore.memory.rule_resolve import (
    LiveRule,
    counts_as_always_content,
    resolve_rules,
)


def test_star_does_not_cross_a_slash():
    assert glob_match("*.tsx", "App.tsx")
    assert not glob_match("*.tsx", "src/App.tsx")
    assert glob_match("**/*.tsx", "src/App.tsx")
    assert glob_match("src/**", "src/a/b.ts")
    assert not glob_match("src/*", "src/a/b.ts")


def test_unbounded_patterns_count_as_always():
    body = "---\napply: paths\npaths: **/*\ndescription: 全部\n---\n正文\n"
    assert counts_as_always_content(body)
    bounded = "---\napply: paths\npaths: **/*.tsx\ndescription: 组件\n---\n正文\n"
    assert not counts_as_always_content(bounded)


def test_paths_without_a_pattern_is_an_error():
    err = parse_entry_frontmatter("---\napply: paths\n---\n正文\n")
    assert isinstance(err, FrontmatterError)
    assert "paths" in err.message


def test_paths_round_trip():
    out = set_entry_frontmatter(
        "正文\n",
        apply="paths",
        description="改组件时",
        paths=("**/*.tsx", "src/*.ts"),
    )
    parsed = parse_entry_frontmatter(out)
    assert not isinstance(parsed, FrontmatterError)
    assert parsed.apply == "paths"
    assert parsed.paths == ("**/*.tsx", "src/*.ts")
    assert parsed.description == "改组件时"


def test_same_name_nearest_enrolled_wins():
    outer = LiveRule(
        name="提交",
        content="---\napply: on_demand\ndescription: 外层\n---\n外层正文\n",
        description="外层",
        apply="on_demand",
        patterns=(),
        rank=0,
    )
    inner = LiveRule(
        name="提交",
        content="---\napply: paths\npaths: **/*.py\ndescription: 改 Python 时\n---\n用中文\n",
        description="改 Python 时",
        apply="paths",
        patterns=("**/*.py",),
        rank=1,
    )
    resolved = resolve_rules([outer, inner])
    assert len(resolved.path) == 1
    assert resolved.path[0].description == "改 Python 时"
    assert len(resolved.on_demand) == 1
    assert resolved.on_demand[0].description == "外层"


def test_empty_description_does_not_hide_an_enrolled_rule():
    outer = LiveRule(
        name="合规",
        content="---\napply: on_demand\ndescription: 外层\n---\n外层\n",
        description="外层",
        apply="on_demand",
        patterns=(),
        rank=0,
    )
    inner = LiveRule(
        name="合规",
        content="---\napply: on_demand\n---\n内层但没说明\n",
        description="",
        apply="on_demand",
        patterns=(),
        rank=1,
    )
    resolved = resolve_rules([outer, inner])
    assert len(resolved.on_demand) == 1
    assert resolved.on_demand[0].description == "外层"


def test_path_frontmatter_is_the_path_channel():
    resolved = resolve_rules(
        [
            LiveRule(
                name="组件",
                content="---\napply: paths\npaths: **/*.tsx\ndescription: 组件用函数\n---\n不要 class\n",
                description="组件用函数",
                apply="paths",
                patterns=("**/*.tsx",),
                rank=0,
            )
        ]
    )
    assert resolved.on_demand == ()
    assert len(resolved.path) == 1
    assert resolved.path[0].body == "不要 class"
