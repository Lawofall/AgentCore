"""Action inventory harvest + navigation sanitize gates for project ops memory."""

from agentcore.memory.action_inventory import (
    TurnActionInventory,
    inventory_from_journal_entries,
    render_action_inventory_for_prompt,
)
from agentcore.memory.episodic import (
    EpisodeRecord,
    append_episode,
    compose_episode_summary,
    episode_actions,
    split_summary_and_facts,
)
from agentcore.memory.semantic import (
    MEMORY_NAV_MAX_ROUTES,
    SemanticConsolidateResult,
    consolidate_semantic_memory,
    parse_semantic_result,
    sanitize_navigation_rewrite,
)
from agentcore.memory.store import (
    CORE_MEMORY_FILE,
    NAVIGATION_MEMORY_FILE,
    FileMemoryStore,
)


def _tool_entry(
    tool_name: str,
    arguments: dict,
    *,
    result: str | None = "ok",
    status: str = "success",
    run: bool = False,
) -> dict:
    kind = "run_process_tool" if run else "process_tool"
    payload = {
        "kind": "tool",
        "id": "c1",
        "tool_name": tool_name,
        "arguments": arguments,
        "result": result,
        "status": status,
    }
    if run:
        payload["run_id"] = "r1"
    return {"kind": kind, "payload": payload, "ts": None}


def test_inventory_harvests_files_commands_searches_and_redacts_secrets():
    entries = [
        _tool_entry("file_read", {"path": "apps/server/README.md"}),
        _tool_entry("file_write", {"path": "apps/server/foo.py", "content": "x"}),
        _tool_entry(
            "host",
            {"action": "shell", "command": "curl -H 'Authorization: Bearer sk-secretvalue123456' https://x"},
        ),
        _tool_entry(
            "grep",
            {"pattern": "TurnActionInventory"},
            result="apps/server/agentcore/memory/action_inventory.py:1: class TurnActionInventory\n",
        ),
        _tool_entry(
            "terminal",
            {"subcommand": "start", "command": "pnpm --filter agentcore-desktop test"},
        ),
        # list should be ignored
        _tool_entry("terminal", {"subcommand": "list"}, result="…"),
    ]
    inv = inventory_from_journal_entries(entries)
    assert "apps/server/README.md" in inv.files_read
    assert "apps/server/foo.py" in inv.files_written
    assert any(c == "pnpm --filter agentcore-desktop test" for c in inv.commands)
    assert any("[REDACTED]" in c for c in inv.commands)
    assert not any("sk-secretvalue" in c for c in inv.commands)
    assert inv.searches
    assert inv.searches[0].query == "TurnActionInventory"
    assert "apps/server/agentcore/memory/action_inventory.py" in inv.searches[0].hits
    prompt = render_action_inventory_for_prompt(inv)
    assert "files_read:" in prompt
    assert "sk-secretvalue" not in prompt


def test_inventory_skips_error_tools():
    entries = [
        _tool_entry(
            "file_read",
            {"path": "missing.py"},
            result="not found",
            status="error",
        )
    ]
    inv = inventory_from_journal_entries(entries)
    assert inv.is_empty()


def test_compose_episode_keeps_verified_facts_outside_char_budget():
    summary = compose_episode_summary(
        "用户要改后端注入",
        "- 改记忆注入 → 先读 apps/server/agentcore/memory/injection.py\n"
        "- 跑桌面测 → pnpm --filter agentcore-desktop test\n",
        max_chars=20,
    )
    dialogue, facts = split_summary_and_facts(summary)
    assert len(dialogue) <= 20 or dialogue.endswith("…")
    assert "injection.py" in facts
    assert "本场证实的项目事实" in summary


async def test_append_episode_persists_actions_json():
    from agentcore.memory.episode_store import InMemoryEpisodeStore

    store = InMemoryEpisodeStore()
    inv = TurnActionInventory(
        files_read=["apps/server/README.md"],
        commands=["pnpm test"],
    )
    body = (
        "讨论了测试命令\n\n## 本场证实的项目事实\n"
        "- 跑测 → pnpm test\n"
    )
    ep = await append_episode(
        store,
        user_id="u1",
        conversation_id="c1",
        summary=body,
        max_chars=200,
        actions=inv,
        scope="folder-1",
    )
    assert ep.actions_json
    loaded = episode_actions(ep)
    assert "apps/server/README.md" in loaded.files_read
    assert "pnpm test" in loaded.commands
    from agentcore.memory.episodic import list_undigested_episodes

    undigested = await list_undigested_episodes(store, "u1", scope="folder-1")
    assert len(undigested) == 1
    assert "本场证实的项目事实" in undigested[0].summary
    assert episode_actions(undigested[0]).files_read == ["apps/server/README.md"]


def test_parse_semantic_result_reads_navigation():
    raw = """
    {
      "preferences": null,
      "profile": null,
      "folder_profile": null,
      "navigation": "# 导航\\n- 改注入 → 先读 apps/server/agentcore/memory/injection.py\\n",
      "ops": []
    }
    """
    result = parse_semantic_result(raw, folder_id="f1")
    assert result.navigation is not None
    assert "injection.py" in result.navigation


def test_sanitize_navigation_drops_paths_absent_from_inventory():
    old = "# 导航\n一句话：示例仓\n\n- 改后端 → apps/server\n"
    new = (
        "# 导航\n一句话：示例仓\n\n"
        "- 改后端 → apps/server\n"
        "- 改注入 → 先读 apps/server/agentcore/memory/injection.py\n"
        "- 改幽灵 → 先读 apps/server/does/not/exist.py\n"
    )
    inv = TurnActionInventory(files_read=["apps/server/agentcore/memory/injection.py"])
    cleaned = sanitize_navigation_rewrite(new, old_md=old, inventory=inv)
    assert "injection.py" in cleaned
    assert "does/not/exist.py" not in cleaned
    assert "改后端 → apps/server" in cleaned  # pre-existing preserved


def test_sanitize_navigation_caps_routes_preferring_old():
    old_routes = [f"- 旧路由{i} → path/old{i}.py" for i in range(MEMORY_NAV_MAX_ROUTES)]
    old = "# 导航\n" + "\n".join(old_routes) + "\n"
    new_routes = old_routes + [
        "- 新路由 → apps/server/agentcore/memory/injection.py",
        "- 又一新 → apps/server/agentcore/memory/semantic.py",
    ]
    new = "# 导航\n" + "\n".join(new_routes) + "\n"
    inv = TurnActionInventory(
        files_read=[
            "apps/server/agentcore/memory/injection.py",
            "apps/server/agentcore/memory/semantic.py",
        ]
    )
    cleaned = sanitize_navigation_rewrite(
        new, old_md=old, inventory=inv, max_routes=MEMORY_NAV_MAX_ROUTES
    )
    routes = [ln for ln in cleaned.splitlines() if ln.strip().startswith("- ")]
    assert len(routes) == MEMORY_NAV_MAX_ROUTES
    assert all(r in old_routes for r in routes)


class _FakeConsolidator:
    def __init__(self, result: SemanticConsolidateResult) -> None:
        self.result = result
        self.calls = 0
        self.last_input = None

    async def consolidate(self, data) -> SemanticConsolidateResult:
        self.calls += 1
        self.last_input = data
        return self.result


async def test_consolidate_with_actions_writes_navigation_route(tmp_path):
    store = FileMemoryStore(tmp_path)
    folder = "folder-nav"
    await store.save(
        "u1",
        CORE_MEMORY_FILE,
        "# 用户记忆\n\n## 技术栈与工具\n- Python\n",
        scope=folder,
    )
    await store.save(
        "u1",
        NAVIGATION_MEMORY_FILE,
        "# 导航\n一句话：示例仓\n\n- 改后端 → apps/server\n",
        scope=folder,
    )
    actions = TurnActionInventory(
        files_read=["apps/server/agentcore/memory/injection.py"],
        commands=["pnpm --filter agentcore-desktop test"],
    )
    episodes = [
        EpisodeRecord(
            id="e1",
            conversation_id="c1",
            summary=(
                "修了记忆注入\n\n## 本场证实的项目事实\n"
                "- 改注入 → 先读 apps/server/agentcore/memory/injection.py\n"
            ),
            created_at="2026-08-12T00:00:00+00:00",
            actions_json=actions.to_json(),
        )
    ]
    new_nav = (
        "# 导航\n一句话：示例仓\n\n"
        "- 改后端 → apps/server\n"
        "- 改注入 → 先读 apps/server/agentcore/memory/injection.py\n"
    )
    fake = _FakeConsolidator(
        SemanticConsolidateResult(navigation=new_nav, ops=[], parse_failed=False)
    )
    outcome = await consolidate_semantic_memory(
        user_id="u1",
        episodes=episodes,
        consolidator=fake,
        store=store,
        folder_id=folder,
    )
    assert outcome is True
    assert fake.last_input is not None
    assert fake.last_input.action_inventory is not None
    assert not fake.last_input.action_inventory.is_empty()
    body = await store.load("u1", NAVIGATION_MEMORY_FILE, scope=folder)
    assert "injection.py" in body
    assert "改后端 → apps/server" in body


async def test_consolidate_chat_only_leaves_navigation_unchanged(tmp_path):
    store = FileMemoryStore(tmp_path)
    folder = "folder-chat"
    old_nav = "# 导航\n一句话：示例仓\n\n- 改后端 → apps/server\n"
    await store.save("u1", NAVIGATION_MEMORY_FILE, old_nav, scope=folder)
    await store.save(
        "u1",
        CORE_MEMORY_FILE,
        "# 用户记忆\n\n## 关于用户的事实\n- 用中文\n",
        scope=folder,
    )
    episodes = [
        EpisodeRecord(
            id="e1",
            conversation_id="c1",
            summary="闲聊天气，用户偏好继续用中文。",
            created_at="2026-08-12T00:00:00+00:00",
            actions_json="",
        )
    ]
    # Model tries to invent a route anyway — sanitize must drop it; file unchanged.
    invented = (
        "# 导航\n一句话：示例仓\n\n"
        "- 改后端 → apps/server\n"
        "- 改幽灵 → 先读 apps/server/ghost.py\n"
    )
    fake = _FakeConsolidator(
        SemanticConsolidateResult(
            preferences=None,
            navigation=invented,
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
    # After sanitize, navigation equals old → no durable nav change; preferences null
    # → overall False (noop) or True only if something else changed.
    body = await store.load("u1", NAVIGATION_MEMORY_FILE, scope=folder)
    assert "ghost.py" not in body
    assert body.strip() == old_nav.strip()
    assert outcome is False
