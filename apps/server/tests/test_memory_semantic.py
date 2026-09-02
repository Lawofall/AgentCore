"""Semantic consolidation: full-file rewrite anti-loss + episode merge apply."""

from agentcore.memory.episodic import EpisodeRecord, append_episode
from agentcore.memory.semantic import (
    SemanticConsolidateResult,
    apply_core_rewrite,
    apply_explicit_memory_ops,
    consolidate_semantic_memory,
    diff_memory_markdown,
    parse_semantic_result,
    rewrite_preserves_enough,
    sanitize_profile_rewrite,
)
from agentcore.memory.store import CORE_MEMORY_FILE, PREFERENCES_MEMORY_FILE, FileMemoryStore
from agentcore.memory.user_memory import MemoryAction, MemoryOp


class _FakeConsolidator:
    def __init__(self, result: SemanticConsolidateResult) -> None:
        self.result = result
        self.calls = 0

    async def consolidate(self, data) -> SemanticConsolidateResult:
        self.calls += 1
        return self.result


def test_rewrite_preserves_enough_rejects_mass_drop():
    old = "# 用户记忆\n\n## 关于用户的事实\n- 用 pnpm\n- 用中文\n- 喜欢简洁\n"
    new = "# 用户记忆\n\n## 关于用户的事实\n- 用 pnpm\n"
    assert not rewrite_preserves_enough(old, new, min_keep_ratio=0.5)
    assert rewrite_preserves_enough(old, old)


def test_apply_core_rewrite_rejects_empty_wipe():
    old = "# 用户记忆\n\n## 沟通偏好\n- 用中文\n"
    assert apply_core_rewrite(old, "") == old
    assert apply_core_rewrite(old, "   ") == old


def test_apply_core_rewrite_drops_retired_chrome():
    new = "# 用户记忆\n\n## 关于用户的事实\n- 用 pnpm\n"
    out = apply_core_rewrite("", new)
    assert "用户记忆" not in out
    assert "本文件由 AI 自动维护" not in out
    assert out.startswith("## 关于用户的事实")


def test_diff_memory_markdown_detects_add_and_remove():
    old = "# 用户记忆\n\n## 沟通偏好\n- 用中文\n"
    new = "# 用户记忆\n\n## 沟通偏好\n- 用英文\n"
    items = diff_memory_markdown(old, new, file=PREFERENCES_MEMORY_FILE, scope=None)
    actions = {it.action for it in items}
    assert MemoryAction.ADD.value in actions
    assert MemoryAction.REMOVE.value in actions


def test_parse_semantic_result_drops_topic_ops():
    raw = """
    {
      "preferences": null,
      "profile": "# 用户记忆\\n\\n## 关于用户的事实\\n- 用 Rust\\n",
      "folder_profile": null,
      "ops": [
        {"action": "add", "file": "主题/部署.md", "content": "用 docker compose"},
        {"action": "add", "file": "画像.md", "section": "关于用户的事实", "content": "应被丢弃"}
      ]
    }
    """
    result = parse_semantic_result(raw)
    assert result.profile is not None
    assert result.preferences is None
    assert result.ops == []


def test_sanitize_global_profile_strips_project_constraints():
    dirty = (
        "# 用户记忆\n\n"
        "## 关于用户的事实\n- 用中文\n\n"
        "## 项目约束\n- 禁止 jQuery\n- 必须用白板栈\n"
    )
    clean = sanitize_profile_rewrite(dirty, scope=None)
    assert "项目约束" not in clean
    assert "用中文" in clean
    assert "禁止 jQuery" not in clean
    assert "用户记忆" not in clean


def test_sanitize_folder_profile_keeps_fixed_sections_drops_free():
    messy = (
        "# 用户记忆\n\n"
        "## 技术栈\n- React\n\n"
        "## 技术栈与工具\n- TypeScript\n\n"
        "## 当前状态\n- 正在改白板\n\n"
        "## 项目约束\n- 禁止 jQuery\n\n"
        "## 纠正记录\n- AI曾认为用 Vue，实际为 React\n"
    )
    clean = sanitize_profile_rewrite(messy, scope="folder-1")
    assert "## 技术栈与工具" in clean
    assert "TypeScript" in clean
    assert "## 项目约束" in clean
    assert "禁止 jQuery" in clean
    assert "## 技术栈\n" not in clean and "## 技术栈\r" not in clean
    assert "当前状态" not in clean
    assert "正在改白板" not in clean
    assert "纠正记录" not in clean  # global-only


async def test_consolidate_strips_project_constraints_from_global(tmp_path):
    store = FileMemoryStore(tmp_path)
    await store.save(
        "u1",
        CORE_MEMORY_FILE,
        "# 用户记忆\n\n## 关于用户的事实\n- 用 pnpm\n",
    )
    episodes = [
        EpisodeRecord(
            id="e1",
            conversation_id="c1",
            summary="用户说改用 bun",
            created_at="2026-07-19T00:00:00+00:00",
        )
    ]
    polluted = (
        "# 用户记忆\n\n"
        "## 关于用户的事实\n- 用 bun\n- 用 pnpm\n\n"
        "## 项目约束\n- 本项目必须用白板\n"
    )
    fake = _FakeConsolidator(
        SemanticConsolidateResult(profile=polluted, ops=[], parse_failed=False)
    )
    outcome = await consolidate_semantic_memory(
        user_id="u1",
        episodes=episodes,
        consolidator=fake,
        store=store,
    )
    assert outcome is True
    body = await store.load("u1", CORE_MEMORY_FILE)
    assert "bun" in body
    assert "项目约束" not in body
    assert "白板" not in body


async def test_consolidate_routes_folder_profile_when_folder(tmp_path):
    store = FileMemoryStore(tmp_path)
    folder = "c5ab5b86-test"
    await store.save(
        "u1",
        CORE_MEMORY_FILE,
        "# 用户记忆\n\n## 关于用户的事实\n- 个人用中文\n",
    )
    await store.save(
        "u1",
        CORE_MEMORY_FILE,
        "# 用户记忆\n\n## 技术栈与工具\n- 旧栈\n",
        scope=folder,
    )
    episodes = [
        EpisodeRecord(
            id="e1",
            conversation_id="c1",
            summary="本项目改用 TypeScript + 白板引擎",
            created_at="2026-07-19T00:00:00+00:00",
        )
    ]
    new_global = "# 用户记忆\n\n## 关于用户的事实\n- 个人用中文\n"
    new_folder = (
        "# 用户记忆\n\n"
        "## 技术栈与工具\n- 旧栈\n- TypeScript\n- 白板引擎\n\n"
        "## 项目约束\n- 禁止 jQuery\n\n"
        "## 数据模型\n- 不应保留\n"
    )
    fake = _FakeConsolidator(
        SemanticConsolidateResult(
            profile=new_global,
            folder_profile=new_folder,
            ops=[],
            parse_failed=False,
        )
    )
    outcome = await consolidate_semantic_memory(
        user_id="u1",
        episodes=episodes,
        consolidator=fake,
        store=store,
        folder_id=folder,
    )
    assert outcome is True
    global_body = await store.load("u1", CORE_MEMORY_FILE)
    assert "项目约束" not in global_body
    assert "白板引擎" not in global_body
    folder_body = await store.load("u1", CORE_MEMORY_FILE, scope=folder)
    assert "TypeScript" in folder_body
    assert "项目约束" in folder_body
    assert "禁止 jQuery" in folder_body
    assert "数据模型" not in folder_body


async def test_consolidate_semantic_rewrites_profile(tmp_path):
    store = FileMemoryStore(tmp_path)
    await store.save(
        "u1",
        CORE_MEMORY_FILE,
        "# 用户记忆\n\n## 关于用户的事实\n- 用 pnpm\n",
    )
    episodes = [
        EpisodeRecord(
            id="e1",
            conversation_id="c1",
            summary="用户说改用 bun",
            created_at="2026-07-19T00:00:00+00:00",
        )
    ]
    new_profile = "# 用户记忆\n\n## 关于用户的事实\n- 用 bun\n- 用 pnpm\n"
    fake = _FakeConsolidator(
        SemanticConsolidateResult(profile=new_profile, ops=[], parse_failed=False)
    )
    collected = []
    outcome = await consolidate_semantic_memory(
        user_id="u1",
        episodes=episodes,
        consolidator=fake,
        store=store,
        collect_items=collected,
    )
    assert outcome is True
    body = await store.load("u1", CORE_MEMORY_FILE)
    assert "bun" in body
    assert "用户记忆" not in body
    assert collected  # diff items for the card


async def test_consolidate_parse_failed_returns_none(tmp_path):
    store = FileMemoryStore(tmp_path)
    fake = _FakeConsolidator(SemanticConsolidateResult(parse_failed=True))
    outcome = await consolidate_semantic_memory(
        user_id="u1",
        episodes=[
            EpisodeRecord(
                id="e1",
                conversation_id="c1",
                summary="x",
                created_at="2026-07-19T00:00:00+00:00",
            )
        ],
        consolidator=fake,
        store=store,
    )
    assert outcome is None


async def test_explicit_remember_writes_immediately(tmp_path):
    store = FileMemoryStore(tmp_path)
    collected = []
    changed = await apply_explicit_memory_ops(
        user_id="u1",
        ops=[
            MemoryOp(
                action=MemoryAction.ADD,
                section="关于用户的事实",
                content="生日是 3 月 1 日",
                file=CORE_MEMORY_FILE,
            )
        ],
        store=store,
        collect_items=collected,
    )
    assert changed
    body = await store.load("u1", CORE_MEMORY_FILE)
    assert "生日是 3 月 1 日" in body
    assert "用户记忆" not in body
    assert collected[0].action == "add"


async def test_episodic_then_semantic_count_path(tmp_path):
    """Three undigested episodes + fake consolidator → semantic apply."""
    from agentcore.memory.episode_store import InMemoryEpisodeStore

    ep_store = InMemoryEpisodeStore()
    for i in range(3):
        await append_episode(
            ep_store,
            user_id="u1",
            conversation_id=f"c{i}",
            summary=f"摘要{i}",
            max_chars=200,
        )
    from agentcore.memory.episodic import list_undigested_episodes, should_run_semantic

    undigested = await list_undigested_episodes(ep_store, "u1")
    assert len(undigested) == 3
    assert should_run_semantic(
        undigested_count=len(undigested),
        last_semantic_at=None,
        min_episodes=3,
        max_age_hours=24,
    )


async def test_genre_preference_stripped_without_writing_topic(tmp_path):
    """题材条目退出偏好.md；巩固不把它们写进主题/*.md。"""
    store = FileMemoryStore(tmp_path)
    # Keep ≥50% bullets so rewrite_preserves_enough accepts the domain-split rewrite.
    stale_prefs = (
        "# 用户记忆\n\n"
        "## 沟通偏好\n"
        "- 用中文\n"
        "- 回复简洁\n"
        "- 偏好法律分析\n"
        "- 喜欢模拟法庭形式讨论\n"
    )
    await store.save("u1", PREFERENCES_MEMORY_FILE, stale_prefs)
    episodes = [
        EpisodeRecord(
            id="e1",
            conversation_id="c1",
            summary="用户明确说继续用中文，并提到法律分析是题材偏好。",
            created_at="2026-07-19T00:00:00+00:00",
        )
    ]
    cleaned_prefs = "# 用户记忆\n\n## 沟通偏好\n- 用中文\n- 回复简洁\n"
    fake = _FakeConsolidator(
        SemanticConsolidateResult(
            preferences=cleaned_prefs,
            ops=[
                MemoryOp(
                    action=MemoryAction.ADD,
                    file="主题/法律.md",
                    content="偏好法律分析与模拟法庭形式讨论",
                )
            ],
            parse_failed=False,
        )
    )
    outcome = await consolidate_semantic_memory(
        user_id="u1",
        episodes=episodes,
        consolidator=fake,
        store=store,
    )
    assert outcome is True
    prefs = await store.load("u1", PREFERENCES_MEMORY_FILE)
    assert "用中文" in prefs
    assert "偏好法律分析" not in prefs
    assert "模拟法庭" not in prefs
    assert await store.load("u1", "主题/法律.md") == ""
