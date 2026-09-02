"""Unit tests for ``sanitize_write_relpath`` (write-path safety + dossier flatten)."""

from __future__ import annotations

from agentcore.workspace._paths import sanitize_write_relpath
from agentcore.workspace.stage_dirs import (
    DEBATE_PREFIX,
    DRAFTS_PREFIX,
    RESEARCH_PREFIX,
    REVIEWS_PREFIX,
)


def test_safe_relative_path_unchanged():
    assert sanitize_write_relpath("site/index.html") == "site/index.html"
    assert sanitize_write_relpath("src/a.py") == "src/a.py"


def test_dangerous_chars_in_segment():
    assert sanitize_write_relpath('site/foo:bar?.html') == "site/foo_bar_.html"
    assert sanitize_write_relpath("docs/a*b.md") == "docs/a_b.md"


def test_preserves_meaningful_leading_underscore_and_dot():
    """Leading ``_`` / ``.`` are intentional names — must not be stripped."""
    assert sanitize_write_relpath("_inventory") == "_inventory"
    assert sanitize_write_relpath("_inventory/items.json") == "_inventory/items.json"
    assert sanitize_write_relpath(".gitignore") == ".gitignore"
    assert sanitize_write_relpath("pkg/.env.local") == "pkg/.env.local"
    # Trailing Windows-dangerous dots/spaces still cleaned.
    assert sanitize_write_relpath("docs/report.") == "docs/report"
    assert sanitize_write_relpath("docs/report ") == "docs/report"
    # Dossier flatten must also keep a leading underscore in the flat name.
    assert (
        sanitize_write_relpath(f"{RESEARCH_PREFIX}_inventory/note.md")
        == f"{RESEARCH_PREFIX}_inventory_note.md"
    )


def test_dossier_flattens_nested_to_filename():
    assert (
        sanitize_write_relpath(f"{RESEARCH_PREFIX}法庭迷局/UX系统设计.md")
        == f"{RESEARCH_PREFIX}法庭迷局_UX系统设计.md"
    )
    assert (
        sanitize_write_relpath(f"{REVIEWS_PREFIX}a/b/c.md")
        == f"{REVIEWS_PREFIX}a_b_c.md"
    )
    assert (
        sanitize_write_relpath(f"{DEBATE_PREFIX}子题\\笔记.md")
        == f"{DEBATE_PREFIX}子题_笔记.md"
    )
    # 工作稿同样扁平（柜内禁自造子树）。
    assert (
        sanitize_write_relpath(f"{DRAFTS_PREFIX}某案/起诉状.md")
        == f"{DRAFTS_PREFIX}某案_起诉状.md"
    )
    # 非阶段目录保留目录结构（步 3 后 ``文档/`` 下只有约定 stage 目录扁平）。
    assert (
        sanitize_write_relpath("AgentCore/文档/背景/深/案.md")
        == "AgentCore/文档/背景/深/案.md"
    )


def test_dossier_filename_truncated_under_name_max():
    """Angle-as-filename must stay under Linux NAME_MAX (255 UTF-8 bytes)."""
    from agentcore.workspace._paths import _MAX_FILENAME_BYTES

    long_label = (
        "竞品定价：中国大陆主流 SaaS 项目管理工具（如 Worktile、Teambition、禅道、"
        "明道云、飞书项目、ONES_PingCode、Tapd、Jira 中国区等）的定价结构与价位带分布，"
        "重点看 200–500 元_月档的竞争格局与定价策略（按席_按量_免费层）"
    )
    path = sanitize_write_relpath(f"{RESEARCH_PREFIX}{long_label}方向笔记.md")
    assert path.startswith(RESEARCH_PREFIX)
    basename = path[len(RESEARCH_PREFIX) :]
    assert len(basename.encode()) <= _MAX_FILENAME_BYTES
    assert basename.endswith(".md")
    assert len(basename.encode()) < len(f"{long_label}方向笔记.md".encode())


def test_dossier_collapses_dunder_directory_underscores():
    """``__tests__`` flattened through reviews must match playbook slug collapse."""
    assert (
        sanitize_write_relpath(
            f"{REVIEWS_PREFIX}code-audit-0-pages___tests___Analytics.md"
        )
        == f"{REVIEWS_PREFIX}code-audit-0-pages_tests_Analytics.md"
    )


def test_dossier_strips_isolated_dot_left_by_chopped_extension():
    """``Name.tsx`` truncated at the dot must not land as ``Name..md``."""
    assert (
        sanitize_write_relpath(f"{REVIEWS_PREFIX}code-audit-0-GoWindowsCard..md")
        == f"{REVIEWS_PREFIX}code-audit-0-GoWindowsCard.md"
    )


def test_dossier_unsafe_chars_in_flat_name():
    assert (
        sanitize_write_relpath(f'{RESEARCH_PREFIX}报告:终稿?.md')
        == f"{RESEARCH_PREFIX}报告_终稿_.md"
    )


def test_absolute_workspace_prefix_stripped_before_sanitize():
    assert sanitize_write_relpath("/workspace/research/x.md") == "research/x.md"
    assert (
        sanitize_write_relpath(f"/workspace/{RESEARCH_PREFIX}a/b.md")
        == f"{RESEARCH_PREFIX}a_b.md"
    )


def test_other_absolute_keeps_leading_slash():
    assert sanitize_write_relpath("/etc/passwd") == "/etc/passwd"


def test_empty_and_dot_passthrough():
    assert sanitize_write_relpath("") == ""
    assert sanitize_write_relpath(".") == "."


def test_traversal_segments_preserved_for_containment():
    assert sanitize_write_relpath("../etc/passwd") == "../etc/passwd"
    assert sanitize_write_relpath("a/../b") == "a/../b"


def test_windows_reserved_device_names_neutralized():
    """Bare + extension forms get a leading ``_``; lookalikes untouched."""
    assert sanitize_write_relpath("nul") == "_nul"
    assert sanitize_write_relpath("NUL") == "_NUL"
    assert sanitize_write_relpath("con") == "_con"
    assert sanitize_write_relpath("PRN") == "_PRN"
    assert sanitize_write_relpath("aux") == "_aux"
    assert sanitize_write_relpath("COM1") == "_COM1"
    assert sanitize_write_relpath("lpt9") == "_lpt9"
    assert sanitize_write_relpath("nul.txt") == "_nul.txt"
    assert sanitize_write_relpath("docs/Con.log") == "docs/_Con.log"
    # ordinary names
    assert sanitize_write_relpath("null.txt") == "null.txt"
    assert sanitize_write_relpath("console") == "console"
    assert sanitize_write_relpath("com10") == "com10"
    # dossier flatten also neutralizes
    assert (
        sanitize_write_relpath(f"{RESEARCH_PREFIX}nul.md")
        == f"{RESEARCH_PREFIX}_nul.md"
    )
