"""Episodic layer: session summaries, trigger conditions, table-backed storage."""

from datetime import UTC, datetime, timedelta

from agentcore.memory.episode_store import InMemoryEpisodeStore
from agentcore.memory.episodic import (
    append_episode,
    clamp_summary,
    list_undigested_episodes,
    load_scope_meta,
    mark_episodes_digested,
    purge_digested_episodes,
    should_run_semantic,
)


def test_clamp_summary_truncates_with_ellipsis():
    assert clamp_summary("  hello   world  ", 20) == "hello world"
    assert clamp_summary("abcdefghij", 5) == "abcd…"
    assert clamp_summary("", 10) == ""


def test_should_run_semantic_count_trigger():
    assert should_run_semantic(
        undigested_count=3,
        last_semantic_at=datetime.now(UTC),
        min_episodes=3,
        max_age_hours=24,
    )
    assert not should_run_semantic(
        undigested_count=2,
        last_semantic_at=datetime.now(UTC),
        min_episodes=3,
        max_age_hours=24,
    )


def test_should_run_semantic_age_trigger():
    old = datetime.now(UTC) - timedelta(hours=25)
    assert should_run_semantic(
        undigested_count=1,
        last_semantic_at=old,
        min_episodes=3,
        max_age_hours=24,
    )
    assert not should_run_semantic(
        undigested_count=1,
        last_semantic_at=datetime.now(UTC) - timedelta(hours=1),
        min_episodes=3,
        max_age_hours=24,
    )


def test_should_run_semantic_cold_start_uses_oldest_episode():
    oldest = datetime.now(UTC) - timedelta(hours=25)
    assert should_run_semantic(
        undigested_count=1,
        last_semantic_at=None,
        min_episodes=3,
        max_age_hours=24,
        oldest_undigested_at=oldest,
    )
    assert not should_run_semantic(
        undigested_count=1,
        last_semantic_at=None,
        min_episodes=3,
        max_age_hours=24,
        oldest_undigested_at=datetime.now(UTC),
    )


def test_should_run_semantic_zero_undigested():
    assert not should_run_semantic(
        undigested_count=0,
        last_semantic_at=None,
        min_episodes=3,
        max_age_hours=24,
        oldest_undigested_at=datetime.now(UTC) - timedelta(days=2),
    )


async def test_append_and_list_undigested():
    store = InMemoryEpisodeStore()
    ep = await append_episode(
        store,
        user_id="u1",
        conversation_id="c1",
        summary="用户倾向用 pnpm，本场讨论了部署。",
        max_chars=200,
    )
    assert ep.summary
    undigested = await list_undigested_episodes(store, "u1")
    assert len(undigested) == 1
    assert undigested[0].id == ep.id
    assert undigested[0].conversation_id == "c1"


async def test_append_episode_strips_verified_facts_on_naked_chat():
    store = InMemoryEpisodeStore()
    body = (
        "查了直播伴侣日志\n\n## 本场证实的项目事实\n"
        "- 日志在 AppData\\直播伴侣\n"
    )
    ep = await append_episode(
        store, user_id="u1", conversation_id="c1", summary=body, max_chars=200
    )
    assert "本场证实的项目事实" not in ep.summary
    assert "AppData" not in ep.summary
    assert "直播伴侣" in ep.summary


async def test_append_episode_keeps_verified_facts_when_folder_bound():
    store = InMemoryEpisodeStore()
    body = (
        "改了注入\n\n## 本场证实的项目事实\n"
        "- 改注入 → 先读 injection.py\n"
    )
    ep = await append_episode(
        store,
        user_id="u1",
        conversation_id="c1",
        summary=body,
        max_chars=200,
        scope="folder-1",
    )
    assert "本场证实的项目事实" in ep.summary
    assert "injection.py" in ep.summary


async def test_mark_digested_hides_from_undigested():
    store = InMemoryEpisodeStore()
    ep = await append_episode(
        store, user_id="u1", conversation_id="c1", summary="摘要一", max_chars=200
    )
    await mark_episodes_digested(store, "u1", [ep.id])
    assert await list_undigested_episodes(store, "u1") == []
    meta = await load_scope_meta(store, "u1")
    assert meta.last_semantic_at is not None


async def test_purge_digested_older_than_30_days():
    store = InMemoryEpisodeStore()
    old = await append_episode(
        store, user_id="u1", conversation_id="c1", summary="old", max_chars=200
    )
    recent = await append_episode(
        store, user_id="u1", conversation_id="c2", summary="recent", max_chars=200
    )
    old_stamp = datetime.now(UTC) - timedelta(days=31)
    recent_stamp = datetime.now(UTC) - timedelta(days=1)
    await mark_episodes_digested(store, "u1", [old.id], consolidated_at=old_stamp)
    await mark_episodes_digested(store, "u1", [recent.id], consolidated_at=recent_stamp)
    removed = await purge_digested_episodes(store, older_than_days=30)
    assert removed == 1
    assert await list_undigested_episodes(store, "u1") == []
    assert all(e.id != old.id for e in store._episodes.get(("u1", None), []))  # noqa: SLF001
    assert any(e.id == recent.id for e in store._episodes.get(("u1", None), []))  # noqa: SLF001


def test_parse_legacy_meta_strips_polluted_frontmatter():
    from agentcore.memory.episodic import (
        legacy_digested_ids_from_meta_json,
        parse_legacy_scope_meta_json,
    )

    polluted = (
        "---\napply: always\n---\n"
        '{"digested_ids": ["abc"], "explore_workspace_key": "ws:x",'
        ' "explore_fingerprint": "fp", "explore_fingerprint_dirty": true}\n'
    )
    meta = parse_legacy_scope_meta_json(polluted)
    assert meta.explore_workspace_key == "ws:x"
    assert meta.explore_fingerprint == "fp"
    assert meta.explore_fingerprint_dirty is True
    assert legacy_digested_ids_from_meta_json(polluted) == {"abc"}
    # Bare JSON still works.
    clean = parse_legacy_scope_meta_json('{"explore_workspace_key": "ws:y"}')
    assert clean.explore_workspace_key == "ws:y"
