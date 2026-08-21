"""Tests for the deterministic memory ops applier (MarkdownMemoryApplier)."""

from datetime import date

from agentcore.memory.store import CORE_MEMORY_FILE, PREFERENCES_MEMORY_FILE
from agentcore.memory.user_memory import (
    MarkdownMemoryApplier,
    MemoryAction,
    MemoryOp,
    merge_global_core,
    parse_bullet_timestamp,
    split_global_core,
    strip_bullet_timestamp,
    strip_memory_chrome,
)

_FIXED_TODAY = "2026-07-06"

SAMPLE = """\
# 用户记忆
> 本文件由 AI 自动维护，你可随时编辑或删除任何条目。

## 沟通偏好
- 用简体中文回复
- 先给结论，再给细节

## 技术栈与工具
- 后端 Python + FastAPI
"""


def apply(markdown: str, *ops: MemoryOp) -> str:
    return MarkdownMemoryApplier(today=_FIXED_TODAY).apply(markdown, list(ops))


def test_add_appends_bullet_under_section():
    out = apply(
        SAMPLE,
        MemoryOp(MemoryAction.ADD, "技术栈与工具", content="偏好 pnpm 管理 Node 依赖"),
    )
    assert "- 偏好 pnpm 管理 Node 依赖" in out
    # appended after the existing bullet in that section
    assert out.index("后端 Python") < out.index("偏好 pnpm")


def test_add_is_deduped_case_and_space_insensitive():
    out = apply(
        SAMPLE,
        MemoryOp(MemoryAction.ADD, "技术栈与工具", content="后端   python + fastapi"),
    )
    assert out.count("FastAPI") == 1  # original kept, normalized duplicate not added


def test_remove_deletes_matching_bullet():
    out = apply(SAMPLE, MemoryOp(MemoryAction.REMOVE, "沟通偏好", match="先给结论"))
    assert "先给结论" not in out
    assert "用简体中文回复" in out  # sibling untouched


def test_update_replaces_matching_bullet():
    out = apply(
        SAMPLE,
        MemoryOp(MemoryAction.UPDATE, "沟通偏好", match="用简体中文回复", content="用英文回复"),
    )
    assert "用英文回复" in out
    assert "用简体中文回复" not in out


def test_update_upserts_when_no_match():
    out = apply(
        SAMPLE,
        MemoryOp(MemoryAction.UPDATE, "工作习惯", match="不存在的条目", content="倾向小步快跑"),
    )
    assert "## 工作习惯" in out
    assert "- 倾向小步快跑" in out


def test_add_creates_missing_section():
    out = apply(SAMPLE, MemoryOp(MemoryAction.ADD, "关于用户的事实", content="在做 AgentCore"))
    assert "## 关于用户的事实" in out
    assert "- 在做 AgentCore" in out


def test_bootstrap_from_empty_input():
    out = apply("", MemoryOp(MemoryAction.ADD, "沟通偏好", content="用简体中文回复"))
    assert not out.startswith("# 用户记忆")
    assert "本文件由 AI 自动维护" not in out
    assert out.startswith("## 沟通偏好")
    assert "- 用简体中文回复" in out


def test_retired_chrome_dropped_on_rewrite():
    # SAMPLE is a leftover on-disk file; parse/render must not echo the retired shell.
    out = apply(SAMPLE, MemoryOp(MemoryAction.ADD, "沟通偏好", content="多用例子说明"))
    assert not out.startswith("# 用户记忆")
    assert "本文件由 AI 自动维护" not in out
    assert out.startswith("## 沟通偏好")
    assert "多用例子说明" in out


def test_empty_apply_is_empty_string():
    assert apply("") == ""


def test_non_user_memory_preamble_is_kept():
    # Navigation-style chrome must survive parse/render — only H1「用户记忆」is retired.
    navish = "# 导航\n> 一句话定位：Python 支付结算仓\n\n## 技术栈与工具\n- Python\n"
    out = apply(navish)
    assert out.startswith("# 导航")
    assert "一句话定位：Python 支付结算仓" in out
    assert "## 技术栈与工具" in out
    assert "- Python" in out


def test_strip_memory_chrome_drops_title_and_note():
    # The injection projection must shed the human chrome (H1 title + "可随时编辑/删除"
    # note) — verbatim it's mid-prompt noise — while keeping the substantive sections.
    body = strip_memory_chrome(SAMPLE)
    assert "用户记忆" not in body
    assert "本文件由 AI 自动维护" not in body
    assert body.startswith("## 沟通偏好")
    assert "- 用简体中文回复" in body
    assert "## 技术栈与工具" in body


def test_strip_memory_chrome_passes_through_freeform():
    # No leading H1 chrome → nothing stripped (respect a freeform user edit).
    freeform = "## 沟通偏好\n- 用简体中文回复"
    assert strip_memory_chrome(freeform) == freeform
    assert strip_memory_chrome("") == ""


def test_multiple_ops_applied_in_order():
    out = apply(
        SAMPLE,
        MemoryOp(MemoryAction.ADD, "技术栈与工具", content="用 pytest 测试"),
        MemoryOp(MemoryAction.REMOVE, "技术栈与工具", match="后端 Python + FastAPI"),
        MemoryOp(MemoryAction.UPDATE, "沟通偏好", match="先给结论", content="结论先行，再展开"),
    )
    assert "用 pytest 测试" in out
    assert "后端 Python + FastAPI" not in out
    assert "结论先行，再展开" in out


def test_remove_missing_section_is_noop():
    out = apply(SAMPLE, MemoryOp(MemoryAction.REMOVE, "不存在的小节", match="任何"))
    assert "用简体中文回复" in out  # content unchanged


def test_adding_existing_bullet_is_idempotent():
    op = MemoryOp(MemoryAction.ADD, "沟通偏好", content="用简体中文回复")
    once = apply(SAMPLE, op)
    twice = MarkdownMemoryApplier(today=_FIXED_TODAY).apply(once, [op])
    assert once == twice  # adding an existing bullet changes nothing


def test_output_has_trailing_newline_and_section_spacing():
    out = apply(SAMPLE)
    assert out.endswith("\n")
    assert "\n\n## 技术栈与工具" in out  # blank line between sections
    assert not out.startswith("# 用户记忆")


# --- containment dedup (near-duplicate ADDs collapse to the more specific one) ---


def test_add_substring_of_existing_is_dropped():
    # The new bullet is fully contained in an existing one → keep the existing
    # (more specific) wording, do not add a vaguer near-duplicate.
    out = apply(SAMPLE, MemoryOp(MemoryAction.ADD, "技术栈与工具", content="后端 Python"))
    assert out.count("- 后端") == 1
    assert "后端 Python + FastAPI" in out  # the longer original survived


def test_add_superset_replaces_existing_in_place():
    # The new bullet contains an existing one → upgrade to the more specific
    # wording, replacing in place rather than appending a duplicate.
    out = apply(
        SAMPLE,
        MemoryOp(MemoryAction.ADD, "技术栈与工具", content="后端 Python + FastAPI + SQLAlchemy"),
    )
    assert out.count("- 后端") == 1
    assert "后端 Python + FastAPI + SQLAlchemy" in out


def test_add_unrelated_bullet_is_not_merged():
    # No containment either way → the bullet is appended, nothing is merged.
    out = apply(SAMPLE, MemoryOp(MemoryAction.ADD, "技术栈与工具", content="前端 React"))
    assert "后端 Python + FastAPI" in out
    assert "- 前端 React" in out


# --- topic notes (free-form: section optional → default bucket) ---


def test_topic_op_without_section_lands_in_default_bucket():
    # A topic note op may omit the section; the applier files it under the default
    # bucket so the same section/bullet machinery serves core and topic notes.
    out = apply("", MemoryOp(MemoryAction.ADD, content="部署用 docker compose", file="主题/部署.md"))
    assert "## 要点" in out
    assert "- 部署用 docker compose" in out


def test_topic_op_with_free_section_is_kept():
    out = apply(
        "",
        MemoryOp(MemoryAction.ADD, section="踩坑", content="忘了跑迁移", file="主题/部署.md"),
    )
    assert "## 踩坑" in out
    assert "- 忘了跑迁移" in out


# --- section_cap (deterministic backstop that bounds section growth) ---


def test_section_cap_trims_to_most_recent():
    applier = MarkdownMemoryApplier(section_cap=2, today=_FIXED_TODAY)
    # 沟通偏好 starts with 2 bullets; adding a 3rd overflows the cap of 2.
    out = applier.apply(SAMPLE, [MemoryOp(MemoryAction.ADD, "沟通偏好", content="多用例子说明")])
    assert "多用例子说明" in out  # newest kept
    assert "先给结论，再给细节" in out  # second-newest kept
    assert "用简体中文回复" not in out  # oldest dropped from the front


def test_section_cap_only_trims_overflowing_section():
    applier = MarkdownMemoryApplier(section_cap=2, today=_FIXED_TODAY)
    out = applier.apply(
        SAMPLE, [MemoryOp(MemoryAction.ADD, "关于用户的事实", content="在做 AgentCore")]
    )
    # 技术栈与工具 has a single bullet — under cap, untouched.
    assert "后端 Python + FastAPI" in out
    assert "在做 AgentCore" in out


def test_non_positive_section_cap_means_no_cap():
    applier = MarkdownMemoryApplier(section_cap=0, today=_FIXED_TODAY)
    out = applier.apply(
        SAMPLE,
        [
            MemoryOp(MemoryAction.ADD, "沟通偏好", content="第三条"),
            MemoryOp(MemoryAction.ADD, "沟通偏好", content="第四条"),
        ],
    )
    # 0 is treated as "no cap" so a misconfig can never wipe a section.
    assert "用简体中文回复" in out
    assert "先给结论，再给细节" in out
    assert "第三条" in out
    assert "第四条" in out


# --- global core combine/split (editor surface + organic 偏好/画像 migration) ---


def test_split_routes_sections_to_preferences_and_profile():
    combined = "## 沟通偏好\n- 用中文\n\n## 技术栈与工具\n- 用 Python\n"
    files = split_global_core(combined)
    assert "用中文" in files[PREFERENCES_MEMORY_FILE]
    assert "用中文" not in files[CORE_MEMORY_FILE]
    assert "用 Python" in files[CORE_MEMORY_FILE]
    assert "用 Python" not in files[PREFERENCES_MEMORY_FILE]
    assert "用户记忆" not in files[PREFERENCES_MEMORY_FILE]
    assert "用户记忆" not in files[CORE_MEMORY_FILE]


def test_split_routes_unknown_section_to_profile():
    # A freeform user-typed section is never lost — it lands in 画像.md.
    files = split_global_core("## 我的怪癖\n- 喜欢深色\n")
    assert "我的怪癖" in files[CORE_MEMORY_FILE]
    assert files[PREFERENCES_MEMORY_FILE] == ""


def test_merge_then_split_round_trips_sections():
    merged = merge_global_core("## 沟通偏好\n- 用中文\n", "## 技术栈与工具\n- 用 Python\n")
    assert "用中文" in merged and "用 Python" in merged
    assert "用户记忆" not in merged
    files = split_global_core(merged)
    assert "用中文" in files[PREFERENCES_MEMORY_FILE]
    assert "用 Python" in files[CORE_MEMORY_FILE]


def test_merge_of_two_empty_files_is_empty():
    # A brand-new user sees an empty editor, not a stray preamble.
    assert merge_global_core("", "") == ""


def test_merge_of_chrome_only_files_is_empty():
    chrome = "# 用户记忆\n> 本文件由 AI 自动维护，你可随时编辑或删除任何条目。\n"
    assert merge_global_core(chrome, chrome) == ""


def test_split_of_empty_clears_both_files():
    files = split_global_core("")
    assert files[PREFERENCES_MEMORY_FILE] == ""
    assert files[CORE_MEMORY_FILE] == ""


def test_split_migrates_legacy_profile_with_preference_sections():
    # An old 画像.md still carrying preference sections splits on save (organic migration).
    legacy = "## 沟通偏好\n- 用中文\n\n## 关于用户的事实\n- 在做 AgentCore\n"
    files = split_global_core(legacy)
    assert "用中文" in files[PREFERENCES_MEMORY_FILE]
    assert "在做 AgentCore" in files[CORE_MEMORY_FILE]
    assert "用中文" not in files[CORE_MEMORY_FILE]


# --- bullet timestamp metadata (<!-- ts:YYYY-MM-DD -->) ---


def test_add_stamps_new_bullet():
    out = apply(
        SAMPLE,
        MemoryOp(MemoryAction.ADD, "技术栈与工具", content="偏好 pnpm 管理 Node 依赖"),
    )
    assert f"<!-- ts:{_FIXED_TODAY} -->" in out
    assert f"- 偏好 pnpm 管理 Node 依赖 <!-- ts:{_FIXED_TODAY} -->" in out


def test_update_refreshes_timestamp():
    stamped_sample = apply(
        SAMPLE,
        MemoryOp(MemoryAction.UPDATE, "沟通偏好", match="用简体中文回复", content="用英文回复"),
    )
    assert f"- 用英文回复 <!-- ts:{_FIXED_TODAY} -->" in stamped_sample
    # sibling without update keeps no timestamp
    assert "- 先给结论，再给细节" in stamped_sample


def test_legacy_bullet_without_timestamp_still_matches():
    out = apply(
        SAMPLE,
        MemoryOp(MemoryAction.UPDATE, "沟通偏好", match="用简体中文回复", content="倾向中文"),
    )
    assert "- 倾向中文 <!-- ts:2026-07-06 -->" in out
    assert "先给结论，再给细节" in out  # untouched legacy bullet, no timestamp


def test_parse_bullet_timestamp_extracts_date():
    line = "- 使用 pnpm <!-- ts:2026-07-06 -->"
    assert parse_bullet_timestamp(line) == date.fromisoformat("2026-07-06")


def test_parse_bullet_timestamp_returns_none_for_legacy():
    assert parse_bullet_timestamp("- 后端 Python + FastAPI") is None
    assert parse_bullet_timestamp("- bad <!-- ts:not-a-date -->") is None


def test_strip_bullet_timestamp_removes_suffix():
    line = "- 使用 pnpm <!-- ts:2026-07-06 -->"
    assert strip_bullet_timestamp(line) == "- 使用 pnpm"


def test_remove_matches_bullet_with_timestamp():
    stamped = apply(
        SAMPLE,
        MemoryOp(MemoryAction.ADD, "技术栈与工具", content="前端 React"),
    )
    out = apply(
        stamped,
        MemoryOp(MemoryAction.REMOVE, "技术栈与工具", match="前端 React"),
    )
    assert "前端 React" not in out
    assert "后端 Python + FastAPI" in out  # legacy bullet preserved
