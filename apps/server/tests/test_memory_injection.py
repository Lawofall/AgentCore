"""Tests for memory topic directory assembly (Agent记忆与知识系统 §二).

The 按需目录 summary is the entry's frontmatter ``description`` — written for retrieval —
never the note's first content line, which says nothing about WHEN to consult the note.
"""

from agentcore.memory.injection import MemoryTopic, load_memory_topics
from agentcore.memory.store import FileMemoryStore


def _note(description: str, body: str = "## 要点\n- 内容\n") -> str:
    return f"---\napply: on_demand\ndescription: {description}\n---\n{body}"


async def test_memory_topics_merge_global_and_project(tmp_path):
    store = FileMemoryStore(tmp_path)
    await store.save("u1", "主题/全局主题.md", _note("全局的检索描述"))
    await store.save("u1", "主题/项目主题.md", _note("项目的检索描述"), scope="F1")
    topics = await load_memory_topics(store, "u1", folder_id="F1", enabled=True)
    # Names merged + sorted, each carrying the description written for retrieval.
    assert topics == [
        MemoryTopic("全局主题", "全局的检索描述"),
        MemoryTopic("项目主题", "项目的检索描述"),
    ]


async def test_memory_topics_dedupe_across_scopes(tmp_path):
    store = FileMemoryStore(tmp_path)
    await store.save("u1", "主题/部署.md", _note("全局描述"))
    await store.save("u1", "主题/部署.md", _note("项目描述"), scope="F1")
    topics = await load_memory_topics(store, "u1", folder_id="F1", enabled=True)
    # Same name in both scopes appears once; the GLOBAL summary wins (stable-prefix layer).
    assert topics == [MemoryTopic("部署", "全局描述")]


async def test_memory_topics_empty_when_disabled(tmp_path):
    store = FileMemoryStore(tmp_path)
    await store.save("u1", "主题/部署.md", _note("描述"))
    assert await load_memory_topics(store, "u1", folder_id=None, enabled=False) == []


async def test_memory_topic_summary_is_description_not_first_line(tmp_path):
    """A note whose first line reads nothing like「何时该读」still gets a real summary."""
    store = FileMemoryStore(tmp_path)
    body = "# 用户记忆\n> 本文件由 AI 自动维护。\n\n## 要点\n- 先 build 再 deploy\n- 二线\n"
    await store.save(
        "u1", "主题/部署流程.md", _note("本项目怎么发版、卡在哪一步时来查", body)
    )
    topics = await load_memory_topics(store, "u1", folder_id=None, enabled=True)
    assert topics == [MemoryTopic("部署流程", "本项目怎么发版、卡在哪一步时来查")]


async def test_memory_topic_without_description_shows_name_only(tmp_path):
    """No description ⇒ empty summary — visibly unlabelled, never a fake first-line one."""
    store = FileMemoryStore(tmp_path)
    await store.save("u1", "主题/部署流程.md", "## 要点\n- 先 build 再 deploy\n")
    assert await load_memory_topics(store, "u1", folder_id=None, enabled=True) == [
        MemoryTopic("部署流程", "")
    ]
