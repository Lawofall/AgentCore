"""Cold-start explore act — profile probe, section merge, write tool, prompt gate."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

import pytest

from agentcore.memory.explore_profile import (
    build_workspace_key,
    compute_workspace_explore_fingerprint,
    evaluate_explore_fingerprint_drift,
    filter_topics_by_scope_cap,
    folder_profile_explore_reason,
    folder_profile_is_empty,
    folder_profile_needs_explore,
    load_explore_fingerprint,
    load_explore_workspace_key,
    merge_profile_by_sections,
    normalize_explore_topic_slug,
    parse_explore_topics,
    profile_has_substance,
    record_explore_closeout,
    record_explore_workspace_key,
    resolve_hard_explore_reason,
    user_named_explore_refresh,
    user_named_folder_work,
    write_folder_navigation,
    write_folder_profile_cas,
    write_folder_topics_replace,
)
from agentcore.memory.store import CORE_MEMORY_FILE, NAVIGATION_MEMORY_FILE, FileMemoryStore
from agentcore.runtime.resolve.prompt import compose_ceo_chat_prompt
from agentcore.runtime.resolve.prompt.cold_start import (
    _COLD_START_EXPLORE_HINT_EMPTY,
    _COLD_START_EXPLORE_HINT_REBIND,
    _COLD_START_EXPLORE_HINT_REFRESH,
    _COLD_START_EXPLORE_REASON_EMPTY,
    _COLD_START_EXPLORE_REASON_REBIND,
    _COLD_START_EXPLORE_REASON_REFRESH,
)
from agentcore.runtime.skills import build_system_skill_registry
from agentcore.tools.builtin.remember import RememberTool
from agentcore.tools.builtin.update_folder_profile import UpdateFolderProfileTool
from agentcore.tools.protocol import ToolContext, fork_explore_write_scope
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


@dataclass
class _PromptHolder:
    _system_prompt: str = "base prompt"


def _ctx(*, user_id: str | None = None) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id=user_id or str(uuid4()),
        conversation_id=str(uuid4()),
    )


# --- 够用探针 -----------------------------------------------------------------


def test_profile_substance_probe_empty_and_chrome_only():
    assert folder_profile_is_empty("")
    assert folder_profile_is_empty("   \n")
    assert folder_profile_is_empty(
        "# 用户记忆\n> 本文件由 AI 自动维护，你可随时编辑或删除任何条目。\n"
    )
    assert folder_profile_is_empty("## 技术栈与工具\n\n## 项目约束\n")
    assert not profile_has_substance("## 技术栈与工具\n")


def test_profile_substance_probe_with_bullets():
    md = "## 技术栈与工具\n- Python monorepo\n"
    assert profile_has_substance(md)
    assert not folder_profile_is_empty(md)


@pytest.mark.asyncio
async def test_folder_profile_needs_explore_gate(tmp_path):
    store = FileMemoryStore(tmp_path)
    uid = str(uuid4())
    folder = str(uuid4())

    assert await folder_profile_needs_explore(store, uid, None) is False
    assert await folder_profile_needs_explore(store, uid, folder) is True
    assert await folder_profile_explore_reason(store, uid, folder) == "empty"

    await store.save(uid, CORE_MEMORY_FILE, "## 技术栈与工具\n- Go\n", scope=folder)
    # Non-empty without stored key → no hard rebind (legacy).
    assert await folder_profile_needs_explore(store, uid, folder) is False
    assert await folder_profile_explore_reason(store, uid, folder) is None


@pytest.mark.asyncio
async def test_explore_reason_empty_keeps_gate_when_workspace_key_unknown(tmp_path):
    """Degraded key (\"\") must not skip empty-profile explore gate."""
    store = FileMemoryStore(tmp_path)
    uid = str(uuid4())
    folder = str(uuid4())
    reason = await folder_profile_explore_reason(
        store, uid, folder, current_workspace_key=""
    )
    assert reason == "empty"


@pytest.fixture
def ep_store(monkeypatch):
    """In-memory episode/scope-state store; also becomes process default for explore helpers."""
    from agentcore.memory.episode_store import InMemoryEpisodeStore

    store = InMemoryEpisodeStore()
    monkeypatch.setattr(
        "agentcore.memory.episode_store.default_episode_store", lambda: store
    )
    return store


@pytest.mark.asyncio
async def test_explore_reason_no_false_rebind_when_workspace_key_unknown(tmp_path, ep_store):
    """Unknown live key must not forge rebind against a stored local key."""
    store = FileMemoryStore(tmp_path)
    uid = str(uuid4())
    folder = str(uuid4())
    await store.save(
        uid,
        CORE_MEMORY_FILE,
        "## 技术栈与工具\n- Python\n",
        scope=folder,
    )
    await record_explore_workspace_key(ep_store, uid, folder, "local:root-a:")
    reason = await folder_profile_explore_reason(
        store, uid, folder, current_workspace_key=""
    )
    assert reason is None


@pytest.mark.asyncio
async def test_explore_reason_rebind_when_workspace_key_mismatches(tmp_path, ep_store):
    store = FileMemoryStore(tmp_path)
    uid = str(uuid4())
    folder = str(uuid4())
    await store.save(uid, CORE_MEMORY_FILE, "## 技术栈与工具\n- Go\n", scope=folder)
    await record_explore_workspace_key(ep_store, uid, folder, "local:root-a:")
    assert (
        await folder_profile_explore_reason(
            store, uid, folder, current_workspace_key="local:root-a:"
        )
        is None
    )
    assert (
        await folder_profile_explore_reason(
            store, uid, folder, current_workspace_key="local:root-b:"
        )
        == "rebind"
    )
    assert await folder_profile_needs_explore(
        store, uid, folder, current_workspace_key="local:root-b:"
    )


def test_build_workspace_key_local_and_cloud():
    @dataclass
    class _B:
        root_id: str
        subpath: str = ""

    assert build_workspace_key(folder_id="f1", binding=_B("rid", "sub")) == "local:rid:sub"
    assert build_workspace_key(folder_id="f1", binding=None) == "folder:f1"


@pytest.mark.asyncio
async def test_resolve_folder_workspace_key_injected_skips_db(monkeypatch):
    """Injected bind → pure build_workspace_key; never opens session factory."""
    from agentcore.memory.explore_profile import resolve_folder_workspace_key
    from agentcore.workspace.locate import LocalBinding

    def boom_factory():
        raise AssertionError("async_session_factory must not run when binding_injected")

    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        boom_factory,
    )
    key = await resolve_folder_workspace_key(
        "fold-1",
        binding=LocalBinding(root_id="root-a", subpath="app"),
        binding_injected=True,
    )
    assert key == "local:root-a:app"
    cloud = await resolve_folder_workspace_key(
        "fold-1",
        binding=None,
        binding_injected=True,
    )
    assert cloud == "folder:fold-1"


@pytest.mark.asyncio
async def test_resolve_folder_workspace_key_non_uuid_skips_db(monkeypatch):
    """Memory-scope folder_id (F1 / test_birth) → folder:<id>; never ::UUID query."""
    from agentcore.memory.explore_profile import resolve_folder_workspace_key

    def boom_factory():
        raise AssertionError("async_session_factory must not run for non-UUID folder_id")

    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        boom_factory,
    )
    assert await resolve_folder_workspace_key("F1") == "folder:F1"
    assert await resolve_folder_workspace_key("test_birth") == "folder:test_birth"


@pytest.mark.asyncio
async def test_resolve_folder_workspace_key_data_error_degrades(monkeypatch):
    """UUID-shaped id + driver DataError → None (no HARD raise; same as connectivity)."""
    from sqlalchemy.exc import DBAPIError

    from agentcore.memory.explore_profile import resolve_folder_workspace_key

    class _FakeAsyncpgDataError(Exception):
        """Stand-in for asyncpg.exceptions.DataError (name + module matter)."""

        __module__ = "asyncpg.exceptions"

    _FakeAsyncpgDataError.__name__ = "DataError"

    class _BoomCM:
        async def __aenter__(self):
            raise DBAPIError(
                "SELECT folders.id FROM folders WHERE folders.id = $1::UUID",
                {"id": "00000000-0000-0000-0000-000000000001"},
                _FakeAsyncpgDataError(
                    "invalid input for query argument $1: invalid UUID"
                ),
            )

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        lambda: _BoomCM(),
    )
    key = await resolve_folder_workspace_key("00000000-0000-0000-0000-000000000001")
    assert key is None


@pytest.mark.asyncio
async def test_resolve_folder_workspace_key_db_unavailable_degrades(monkeypatch):
    """PG down without injection → None (no HARD raise; no forged local key)."""
    from agentcore.db.errors import DATABASE_UNAVAILABLE_MESSAGE, DatabaseUnavailableError
    from agentcore.memory.explore_profile import resolve_folder_workspace_key

    class _BoomCM:
        async def __aenter__(self):
            raise DatabaseUnavailableError(DATABASE_UNAVAILABLE_MESSAGE)

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        lambda: _BoomCM(),
    )
    key = await resolve_folder_workspace_key("00000000-0000-0000-0000-000000000099")
    assert key is None


# --- 合并语义 -----------------------------------------------------------------


def test_merge_profile_bootstrap_when_empty():
    new = "## 技术栈与工具\n- Python\n\n## 项目约束\n- 禁止 jQuery\n"
    merged = merge_profile_by_sections("", new)
    assert "Python" in merged
    assert "禁止 jQuery" in merged
    assert merged.startswith("## 技术栈与工具")
    assert "用户记忆" not in merged


def test_merge_profile_keeps_unmentioned_sections():
    old = (
        "## 技术栈与工具\n- Python\n\n"
        "## 关于用户的事实\n- 这是支付结算 monorepo\n\n"
        "## 项目约束\n- 必须 PostgreSQL\n"
    )
    new = "## 技术栈与工具\n- Python + FastAPI\n- pnpm workspace\n"
    merged = merge_profile_by_sections(old, new)
    assert "Python + FastAPI" in merged
    assert "pnpm workspace" in merged
    assert "支付结算" in merged  # untouched section kept
    assert "必须 PostgreSQL" in merged


def test_merge_profile_rejects_empty_new_as_wipe():
    old = "## 技术栈与工具\n- Keep me\n"
    assert merge_profile_by_sections(old, "") == old
    assert merge_profile_by_sections(old, "   ") == old


# --- CAS 写入 -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_folder_profile_cas_merge(tmp_path):
    store = FileMemoryStore(tmp_path)
    uid = str(uuid4())
    folder = str(uuid4())
    await store.save(
        uid,
        CORE_MEMORY_FILE,
        "## 技术栈与工具\n- Python\n\n## 项目约束\n- 保留约束\n",
        scope=folder,
    )
    ok, resulting, conflict = await write_folder_profile_cas(
        store=store,
        user_id=uid,
        folder_id=folder,
        new_markdown="## 技术栈与工具\n- Python 3.12\n",
    )
    assert ok and not conflict
    assert "Python 3.12" in resulting
    assert "保留约束" in resulting
    loaded = await store.load(uid, CORE_MEMORY_FILE, scope=folder)
    assert loaded == resulting


@pytest.mark.asyncio
async def test_write_folder_profile_cas_rejects_empty_content(tmp_path):
    store = FileMemoryStore(tmp_path)
    uid = str(uuid4())
    folder = str(uuid4())
    ok, resulting, conflict = await write_folder_profile_cas(
        store=store,
        user_id=uid,
        folder_id=folder,
        new_markdown="## 技术栈与工具\n\n",
    )
    assert not ok and not conflict and resulting == ""


# --- 工具：裸聊 / remember 不碰画像 ---------------------------------


@pytest.mark.asyncio
async def test_update_folder_profile_refuses_bare_chat(tmp_path):
    tool = UpdateFolderProfileTool(folder_id=None, store=FileMemoryStore(tmp_path))
    res = await tool.execute(
        {"content": "## 技术栈与工具\n- X\n"},
        _ctx(),
    )
    assert not res.success
    assert res.error == "no_folder"


@pytest.mark.asyncio
async def test_update_folder_profile_writes_and_hot_refreshes(tmp_path, ep_store):
    store = FileMemoryStore(tmp_path)
    uid = str(uuid4())
    folder = str(uuid4())
    holder = _PromptHolder(_system_prompt="worker base\n<设定>\nold\n</设定>")
    tool = UpdateFolderProfileTool(
        folder_id=folder,
        store=store,
        prompt_holders=[holder],
        workspace_key=f"folder:{folder}",
    )
    res = await tool.execute(
        {"content": "## 技术栈与工具\n- TypeScript\n"},
        _ctx(user_id=uid),
    )
    assert res.success
    assert res.display["kind"] == "folder_profile"
    assert "TypeScript" in res.output
    assert "<文件夹画像已更新>" in holder._system_prompt
    assert "TypeScript" in holder._system_prompt
    loaded = await store.load(uid, CORE_MEMORY_FILE, scope=folder)
    assert "TypeScript" in loaded
    assert await load_explore_workspace_key(ep_store, uid, folder) == f"folder:{folder}"


@pytest.mark.asyncio
async def test_remember_does_not_touch_folder_profile(tmp_path, monkeypatch):
    """remember → user rule only; folder 画像.md stays untouched."""
    from agentcore.tools.builtin import remember as remember_mod

    store = FileMemoryStore(tmp_path)
    uid = str(uuid4())
    folder = str(uuid4())

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):  # noqa: ANN002
            return None

    def _fake_factory():
        return _FakeSession()

    async def _fake_mutate(repo, user_id, *, folder_id, action="add", content=None, replaces=None):  # noqa: ANN001
        from agentcore.memory.rules_injection import UserRuleMutationResult

        return UserRuleMutationResult(
            action=action,
            changed=True,
            message=f"已追加规则：{content}",
            markdown=f"- {content}\n",
            content=content,
        )

    monkeypatch.setattr(remember_mod, "async_session_factory", _fake_factory)
    monkeypatch.setattr(remember_mod, "mutate_user_rule", _fake_mutate)
    monkeypatch.setattr(remember_mod, "DocumentRepository", lambda session: object())

    tool = RememberTool(folder_id=folder)
    res = await tool.execute({"content": "以后都用中文回复"}, _ctx(user_id=uid))
    assert res.success
    assert res.display["kind"] == "user_rule"
    assert await store.load(uid, CORE_MEMORY_FILE, scope=folder) == ""
    assert "文件夹画像" in tool.schema.description
    assert "update_folder_profile" in tool.schema.description


# --- 提示词闸：画像空注入 / 闲聊纪律文案 -----------------------------------------


def test_compose_prompt_cold_start_block_only_when_flagged():
    skills = build_system_skill_registry()
    names = {"consult", "update_folder_profile", "delegate"}
    without = compose_ceo_chat_prompt(
        "BASE",
        skill_registry=skills,
        ceo_tool_names=names,
        cold_start_explore=False,
    )
    with_flag = compose_ceo_chat_prompt(
        "BASE",
        skill_registry=skills,
        ceo_tool_names=names,
        cold_start_explore=True,
    )
    assert "当前文件夹约定记忆「画像.md」为空" not in without
    assert "当前文件夹约定记忆「画像.md」为空" in with_flag
    assert "<冷启动探索>" in with_flag
    assert "闲聊" in with_flag or "问候" in with_flag
    assert "假画像" in with_flag
    assert "update_folder_profile" in with_flag
    assert "remember" in with_flag
    assert "立刻继续" in with_flag
    assert "重新了解" in with_flag
    assert "轻探" in with_flag
    assert "摸完整仓" in with_flag
    assert "与巩固侧「冷启动」无关" in with_flag
    block = with_flag[
        with_flag.find("<冷启动探索>") : with_flag.find("</冷启动探索>")
    ]
    assert "team_preview" not in block


def test_hard_explore_three_reasons_share_one_principle():
    """硬挡三因共用一条原则，只用 reason_line 区分。"""

    def body(hint: str, reason: str) -> str:
        assert reason in hint
        return hint.replace(reason, "", 1)

    empty = body(_COLD_START_EXPLORE_HINT_EMPTY, _COLD_START_EXPLORE_REASON_EMPTY)
    rebind = body(_COLD_START_EXPLORE_HINT_REBIND, _COLD_START_EXPLORE_REASON_REBIND)
    refresh = body(_COLD_START_EXPLORE_HINT_REFRESH, _COLD_START_EXPLORE_REASON_REFRESH)
    assert empty == rebind == refresh
    assert "【冷启动探索幕 · " not in empty


def test_compose_prompt_rebind_gate():
    skills = build_system_skill_registry()
    text = compose_ceo_chat_prompt(
        "BASE",
        skill_registry=skills,
        ceo_tool_names={"update_folder_profile", "delegate"},
        cold_start_explore="rebind",
    )
    assert "绑定已变" in text
    assert "轻探" in text
    assert "合并更新" in text
    assert "<冷启动探索>" in text
    assert "画像.md」为空" not in text


def test_compose_prompt_refresh_gate():
    skills = build_system_skill_registry()
    text = compose_ceo_chat_prompt(
        "BASE",
        skill_registry=skills,
        ceo_tool_names={"update_folder_profile", "delegate"},
        cold_start_explore="refresh",
    )
    assert "用户点名刷新" in text
    assert "<冷启动探索>" in text
    assert "合并" in text
    assert "画像.md」为空" not in text
    assert "【冷启动探索幕 · 绑定已变】" not in text
    assert "写盘不得出 AgentCore/" in text
    assert "create_folder 新建的云文件夹除外" in text
    assert "文档/项目" not in text
    assert "勿让 worker 以 form=files" not in text


def test_user_named_explore_refresh_allow_list():
    """产品口径改「文件夹」，但用户仍会说「项目」——两种说法都得认。"""
    assert user_named_explore_refresh("请重新了解项目") is True
    assert user_named_explore_refresh("先了解一下这个仓库") is True
    assert user_named_explore_refresh("刷新项目记忆") is True
    assert user_named_explore_refresh("刷新文件夹记忆") is True
    assert user_named_explore_refresh("帮我改一下 README") is False
    assert user_named_explore_refresh("探索一下这个 API") is False
    assert user_named_explore_refresh("") is False


def test_user_named_folder_work_allow_list():
    assert user_named_folder_work("请继续开发这个功能") is True
    assert user_named_folder_work("改这个项目的 README") is True
    assert user_named_folder_work("改这个文件夹的 README") is True
    assert user_named_folder_work("在这个项目里加测试") is True
    assert user_named_folder_work("在这个文件夹里加测试") is True
    assert user_named_folder_work("全面摸底一下架构") is True
    assert user_named_folder_work("摸清这个项目结构") is True
    assert user_named_folder_work("摸清这个文件夹结构") is True
    assert user_named_folder_work("先摸仓再动手") is True
    assert user_named_folder_work("今天天气怎么样") is False
    assert user_named_folder_work("帮我改一下 README") is False
    assert user_named_folder_work("") is False


def test_resolve_hard_explore_reason_soft_empty_and_named_work():
    """Empty alone → soft; empty+工程点名 → hard; refresh phrase → hard."""
    hard, soft = resolve_hard_explore_reason("empty", "进度条卡 0% 请修一下")
    assert hard is None and soft is True
    hard, soft = resolve_hard_explore_reason("empty", "请继续开发这个功能")
    assert hard == "empty" and soft is False
    hard, soft = resolve_hard_explore_reason(None, "请重新了解项目")
    assert hard == "refresh" and soft is False
    hard, soft = resolve_hard_explore_reason("rebind", "随便说说")
    assert hard == "rebind" and soft is False


def test_compose_prompt_without_profile_tool_skips_write_hint():
    skills = build_system_skill_registry()
    text = compose_ceo_chat_prompt(
        "BASE",
        skill_registry=skills,
        ceo_tool_names={"delegate"},
        cold_start_explore=False,
    )
    assert "【文件夹画像写入】" not in text


def test_compose_prompt_profile_write_how_lives_on_tool_schema():
    """画像写入 HOW 在工具 schema；有工具也不挂冻结核。"""
    skills = build_system_skill_registry()
    text = compose_ceo_chat_prompt(
        "BASE",
        skill_registry=skills,
        ceo_tool_names={"update_folder_profile", "delegate"},
        cold_start_explore=False,
    )
    assert "【文件夹画像写入】" not in text
    desc = UpdateFolderProfileTool().schema.description
    assert "topics" in desc
    assert "立刻继续" in desc


# --- P1：主题拆分 -----------------------------------------------------------------


def test_normalize_and_parse_explore_topics():
    assert normalize_explore_topic_slug("Desktop") == "desktop"
    assert normalize_explore_topic_slug("../etc") is None
    assert normalize_explore_topic_slug("有中文") is None
    topics, warnings = parse_explore_topics(
        [
            {"slug": "runtime", "content": "## 入口\n- FastAPI\n"},
            {"slug": "desktop", "content": "## 入口\n- Electron\n"},
            {"slug": "admin", "content": "x"},
            {"slug": "mobile", "content": "y"},
            {"slug": "docs", "content": "z"},
            {"slug": "extra", "content": "should warn"},
        ]
    )
    assert [s for s, _ in topics] == ["runtime", "desktop", "admin", "mobile", "docs"]
    assert any("超过" in w for w in warnings)


@pytest.mark.asyncio
async def test_update_folder_profile_writes_topics(tmp_path):
    store = FileMemoryStore(tmp_path)
    uid = str(uuid4())
    folder = str(uuid4())
    tool = UpdateFolderProfileTool(
        folder_id=folder, store=store, workspace_key=f"folder:{folder}"
    )
    res = await tool.execute(
        {
            "content": "## 技术栈与工具\n- Monorepo\n",
            "topics": [
                {"slug": "runtime", "content": "## 结构\n- apps/server\n"},
                {"slug": "desktop", "content": "## 结构\n- apps/desktop\n"},
            ],
        },
        _ctx(user_id=uid),
    )
    assert res.success
    assert res.display["topics"] == ["主题/runtime.md", "主题/desktop.md"]
    assert "立刻继续" in res.output
    assert "需要我继续吗" in res.output
    assert "Monorepo" in await store.load(uid, CORE_MEMORY_FILE, scope=folder)
    assert "apps/server" in await store.load(uid, "主题/runtime.md", scope=folder)
    assert "apps/desktop" in await store.load(uid, "主题/desktop.md", scope=folder)


@pytest.mark.asyncio
async def test_update_folder_profile_soft_top_five_topics(tmp_path):
    """T2: >5 topics truncate with warning; do not hard-reject the call."""
    store = FileMemoryStore(tmp_path)
    uid = str(uuid4())
    folder = str(uuid4())
    tool = UpdateFolderProfileTool(
        folder_id=folder, store=store, workspace_key=f"folder:{folder}"
    )
    topics = [{"slug": f"t{i}", "content": f"body {i}"} for i in range(7)]
    res = await tool.execute(
        {"content": "## 技术栈与工具\n- Soft top\n", "topics": topics},
        _ctx(user_id=uid),
    )
    assert res.success
    assert len(res.display["topics"]) == 5
    assert "超过" in res.output
    assert await store.load(uid, "主题/t5.md", scope=folder) == ""
    assert "body 4" in await store.load(uid, "主题/t4.md", scope=folder)


@pytest.mark.asyncio
async def test_update_folder_profile_writes_navigation_and_fingerprint(tmp_path, ep_store):
    store = FileMemoryStore(tmp_path)
    uid = str(uuid4())
    folder = str(uuid4())
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "README.md").write_text("# Hello\n", encoding="utf-8")
    (ws / "apps").mkdir()
    backend = ServerWorkspace(root=ws, sandbox=SubprocessSandbox())
    tool = UpdateFolderProfileTool(
        folder_id=folder, store=store, workspace_key=f"folder:{folder}"
    )
    ctx = _ctx(user_id=uid)
    ctx.backend = backend
    res = await tool.execute(
        {
            "content": "## 技术栈与工具\n- Python\n",
            "navigation": "# 导航\n一句话：示例仓\n\n## 任务路由\n- 改后端 → apps/server\n",
        },
        ctx,
    )
    assert res.success
    assert res.display["navigation"] == NAVIGATION_MEMORY_FILE
    nav = await store.load(uid, NAVIGATION_MEMORY_FILE, scope=folder)
    assert "示例仓" in nav
    assert await load_explore_workspace_key(ep_store, uid, folder) == f"folder:{folder}"
    fp = await load_explore_fingerprint(ep_store, uid, folder)
    assert fp
    live = await compute_workspace_explore_fingerprint(backend)
    assert fp == live


@pytest.mark.asyncio
async def test_fingerprint_drift_marks_dirty_without_explore_reason(tmp_path, ep_store):
    store = FileMemoryStore(tmp_path)
    uid = str(uuid4())
    folder = str(uuid4())
    await store.save(uid, CORE_MEMORY_FILE, "## 技术栈与工具\n- Go\n", scope=folder)
    await record_explore_closeout(
        ep_store,
        uid,
        folder,
        workspace_key="local:root-a:",
        fingerprint="fp-old",
    )
    assert await folder_profile_explore_reason(
        store, uid, folder, current_workspace_key="local:root-a:"
    ) is None
    stale = await evaluate_explore_fingerprint_drift(
        ep_store,
        uid,
        folder,
        live_fingerprint="fp-new",
        current_workspace_key="local:root-a:",
    )
    assert stale is True
    from agentcore.memory.episodic import load_scope_meta

    meta = await load_scope_meta(ep_store, uid, scope=folder)
    assert meta.explore_fingerprint_dirty is True
    assert await folder_profile_explore_reason(
        store, uid, folder, current_workspace_key="local:root-a:"
    ) is None


def test_compose_prompt_folder_nav_stale_soft_hint():
    skills = build_system_skill_registry()
    text = compose_ceo_chat_prompt(
        "BASE",
        skill_registry=skills,
        ceo_tool_names={"update_folder_profile", "delegate"},
        cold_start_explore=False,
        folder_nav_stale=True,
    )
    assert "【文件夹结构提示】" in text
    assert "当前文件夹约定记忆「画像.md」为空" not in text
    assert "【冷启动探索幕 · 绑定已变】" not in text
    # Blocking explore wins over soft hint.
    blocked = compose_ceo_chat_prompt(
        "BASE",
        skill_registry=skills,
        ceo_tool_names={"update_folder_profile", "delegate"},
        cold_start_explore="empty",
        folder_nav_stale=True,
    )
    assert "当前文件夹约定记忆「画像.md」为空" in blocked
    assert "【文件夹结构提示】" not in blocked
    assert "写盘不得出 AgentCore/" in blocked
    assert "勿让 worker 以 form=files" not in blocked


def test_compose_prompt_folder_profile_empty_soft_hint():
    skills = build_system_skill_registry()
    soft = compose_ceo_chat_prompt(
        "BASE",
        skill_registry=skills,
        ceo_tool_names={"update_folder_profile", "delegate"},
        cold_start_explore=False,
        folder_profile_empty_soft=True,
    )
    assert "<文件夹画像空>" in soft
    assert "【文件夹画像提示】" in soft
    assert "不挡" in soft
    assert "</冷启动探索>" not in soft
    assert "写盘不得出 AgentCore/" not in soft
    assert "不可当跳过" in soft
    # Hard empty wins over soft empty.
    hard = compose_ceo_chat_prompt(
        "BASE",
        skill_registry=skills,
        ceo_tool_names={"update_folder_profile", "delegate"},
        cold_start_explore="empty",
        folder_profile_empty_soft=True,
    )
    assert "</冷启动探索>" in hard
    assert "</文件夹画像空>" not in hard
    assert "写盘不得出 AgentCore/" in hard
    assert "不可跳过" in hard


@pytest.mark.asyncio
async def test_update_folder_profile_clears_explore_pending(tmp_path):
    """画像写入成功须翻转 ToolContext.cold_start_explore_pending，避免误伤同回合交付批。"""
    store = FileMemoryStore(tmp_path)
    uid = str(uuid4())
    folder = str(uuid4())
    tool = UpdateFolderProfileTool(
        folder_id=folder, store=store, workspace_key=f"folder:{folder}"
    )
    context = _ctx(user_id=uid)
    context.cold_start_explore_pending = True
    context.write_scope = "explore_memory"
    res = await tool.execute(
        {"content": "## 技术栈与工具\n- Python\n"},
        context,
    )
    assert res.success
    assert context.cold_start_explore_pending is False
    assert context.write_scope == "project"


@pytest.mark.asyncio
async def test_update_folder_profile_clears_pending_across_replace_copy(tmp_path):
    """引擎 replace(on_phase=…) 后写画像，pipeline base 上的 delegate 必须立刻看见。"""
    store = FileMemoryStore(tmp_path)
    uid = str(uuid4())
    folder = str(uuid4())
    tool = UpdateFolderProfileTool(
        folder_id=folder, store=store, workspace_key=f"folder:{folder}"
    )
    base = _ctx(user_id=uid)
    base.cold_start_explore_pending = True
    base.write_scope = "explore_memory"
    copy = replace(base, on_phase=lambda _phase: None)
    res = await tool.execute(
        {"content": "## 技术栈与工具\n- Python\n"},
        copy,
    )
    assert res.success
    assert copy.cold_start_explore_pending is False
    assert copy.write_scope == "project"
    assert base.cold_start_explore_pending is False
    assert base.write_scope == "project"


def test_fork_explore_write_scope_does_not_share_write_permission():
    base = _ctx()
    base.cold_start_explore_pending = True
    base.write_scope = "explore_memory"
    worker = replace(
        base,
        run_id="w1",
        _explore_gate=fork_explore_write_scope(base, "none"),
    )
    assert worker.write_scope == "none"
    assert worker.cold_start_explore_pending is True
    base.write_scope = "project"
    base.cold_start_explore_pending = False
    assert worker.write_scope == "none"
    assert worker.cold_start_explore_pending is True
    assert base.write_scope == "project"
    assert worker.turn_created_folder_ids == base.turn_created_folder_ids
    assert base.cold_start_explore_pending is False


@pytest.mark.asyncio
async def test_write_folder_topics_replace_overwrites(tmp_path):
    store = FileMemoryStore(tmp_path)
    uid = str(uuid4())
    folder = str(uuid4())
    await write_folder_topics_replace(
        store=store,
        user_id=uid,
        folder_id=folder,
        topics=[("runtime", "old")],
    )
    await write_folder_topics_replace(
        store=store,
        user_id=uid,
        folder_id=folder,
        topics=[("runtime", "new body")],
    )
    assert (await store.load(uid, "主题/runtime.md", scope=folder)).strip() == "new body"


@pytest.mark.asyncio
async def test_write_folder_navigation(tmp_path):
    store = FileMemoryStore(tmp_path)
    uid = str(uuid4())
    folder = str(uuid4())
    path = await write_folder_navigation(
        store=store,
        user_id=uid,
        folder_id=folder,
        markdown="# 导航\n- 改 X → 先读 Y\n",
    )
    assert path == NAVIGATION_MEMORY_FILE
    assert "改 X" in await store.load(uid, NAVIGATION_MEMORY_FILE, scope=folder)


@pytest.mark.asyncio
async def test_filter_topics_by_scope_cap(tmp_path):
    store = FileMemoryStore(tmp_path)
    uid = str(uuid4())
    folder = str(uuid4())
    await write_folder_topics_replace(
        store=store,
        user_id=uid,
        folder_id=folder,
        topics=[("a", "1"), ("b", "2")],
    )
    kept, warnings = await filter_topics_by_scope_cap(
        store,
        uid,
        folder,
        [("a", "replace"), ("c", "new"), ("d", "new2")],
        max_topic_files=3,
    )
    assert kept == [("a", "replace"), ("c", "new")]
    assert any("上限" in w for w in warnings)
