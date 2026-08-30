"""Always-on rule injection (read-side full injection · Agent记忆与知识系统).

Pure, DB-free: compose + frontmatter strip + equal-authority ``<设定>`` wording
(``assemble_system_prompt``). The DB loader (``assemble_injected_rules``) is covered in
``tests/integration/test_documents.py``.
"""

from agentcore.memory.rules_injection import (
    RuleFragment,
    compose_injected_rules,
    strip_entry_frontmatter,
)
from agentcore.runtime.resolve.prompt import assemble_system_prompt


def test_compose_joins_all_fragments_in_order():
    # Display order join — no user/AI split on the read side.
    frags = [
        RuleFragment(body="偏好体"),
        RuleFragment(body="画像体"),
        RuleFragment(body="（项目标签）\n项目体"),
    ]
    assert (
        compose_injected_rules(frags)
        == "偏好体\n\n画像体\n\n（项目标签）\n项目体"
    )


def test_compose_user_rules_then_memory_same_block():
    frags = [
        RuleFragment(body="规则A"),
        RuleFragment(body="（项目规则）\n规则B"),
        RuleFragment(body="画像体"),
    ]
    assert (
        compose_injected_rules(frags)
        == "规则A\n\n（项目规则）\n规则B\n\n画像体"
    )


def test_compose_empty_when_no_fragments():
    assert compose_injected_rules([]) == ""


def test_compose_admits_all_fragments_no_budget():
    # Read side never drops — even many / large always-on entries all survive.
    frags = [
        RuleFragment(body="r1"),
        RuleFragment(body="r2"),
        RuleFragment(body="m1"),
        RuleFragment(body="G" * 10_000),
        RuleFragment(body="（项目）\n" + "P" * 10_000),
    ]
    md = compose_injected_rules(frags)
    assert md.startswith("r1\n\nr2\n\nm1\n\n")
    assert "G" * 10_000 in md
    assert "P" * 10_000 in md


def test_strip_entry_frontmatter_removes_fence():
    # Body bytes are preserved exactly (the injector trims); read and write share one parser.
    raw = "---\napply: always\ndescription: 摘要\n---\n- 必须用中文\n"
    assert strip_entry_frontmatter(raw) == "- 必须用中文\n"


def test_strip_entry_frontmatter_passthrough_without_fence():
    raw = "- 必须用中文\n"
    assert strip_entry_frontmatter(raw) == raw
    assert strip_entry_frontmatter("") == ""


def test_strip_entry_frontmatter_unclosed_returns_none():
    assert strip_entry_frontmatter("---\napply: always\n- body") is None


def test_assemble_system_prompt_equal_authority_wording():
    out = assemble_system_prompt(
        rules_markdown="- 必须始终用中文\n\n- 倾向简洁回复",
    )
    assert "<设定>" in out and "</设定>" in out
    # Both entries present; display order preserved; no hard/soft subsections.
    assert "必须始终用中文" in out and "倾向简洁回复" in out
    assert out.index("必须始终用中文") < out.index("倾向简洁回复")
    assert "用户规则 · 须严格遵守" not in out
    assert "软性偏好" not in out
    assert "以下条目请一并遵循" in out
    # Routing fence still guards topic-preference override of this-turn routing.
    assert "不得改变本回合路由" in out


def test_assemble_system_prompt_single_flat_block():
    out = assemble_system_prompt(rules_markdown="- 倾向简洁回复")
    assert "用户规则 · 须严格遵守" not in out
    assert "软性偏好" not in out
    assert "以下条目请一并遵循" in out
    assert "倾向简洁回复" in out
