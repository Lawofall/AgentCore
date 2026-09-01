"""规则 / 记忆沿文件夹树由外向里继承（双模式工作区 §5.4）。

外层文件夹定的约定，里层自动适用；**近的覆盖远的**。注入没有硬覆盖结构——「近」由
两件事表达：更近的层排在后面，且层标签明说以更近的为准。所以这里断言的主要是**顺序
与标签**，那就是模型实际读到的全部。

存储不搬家：条目仍按 ``folder_id`` 分区，这里覆盖的只有读侧（本机 DB 路 + account 票
的云载荷 / 快照路）。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from agentcore.account.credentials import AccountCredentials, account_credentials_scope
from agentcore.memory.account_prepare_cache import (
    AccountPrepareSnapshot,
    clear_account_rules_memory_cache,
    seed_account_rules_memory_cache,
)
from agentcore.memory.injection import (
    _ANCESTOR_SETTINGS_LABEL,
    _FOLDER_SETTINGS_LABEL,
)
from agentcore.memory.rules_injection import (
    _memory_fragments,
    _memory_fragments_from_snapshot,
    _user_rule_fragments,
    _user_rule_fragments_from_cloud,
    assemble_injected_rules,
    assemble_turn_rules,
    compose_injected_rules,
    load_on_demand_user_rules,
    lookup_on_demand_rule_body_from_cloud,
    on_demand_user_rules_from_cloud,
)
from agentcore.memory.scope_chain import (
    cloud_scope_chain,
    db_scope_chain,
    snapshot_scope_chain,
)

pytestmark = pytest.mark.anyio

# 一条三层链：外层「客户」→ 中层「项目」→ 当前「子模块」。
OUTER, MIDDLE, CURRENT = "f_outer", "f_middle", "f_current"
CHAIN = (OUTER, MIDDLE, CURRENT)


@dataclass(frozen=True)
class _Doc:
    name: str
    content: str


class _FakeRuleRepo:
    """按 ``folder_id`` 分区的规则库（``None`` = 全局）——存储形状与生产一致。"""

    def __init__(
        self,
        always: dict[str | None, list[_Doc]] | None = None,
        on_demand: dict[str | None, list[_Doc]] | None = None,
    ) -> None:
        self._always = always or {}
        self._on_demand = on_demand or {}

    async def list_injectable_rules(self, user_id, folder_id, *, ai_maintained):
        del user_id, ai_maintained
        return list(self._always.get(folder_id, []))

    async def list_on_demand_user_rules(self, user_id, folder_id):
        del user_id
        return list(self._on_demand.get(folder_id, []))


class _FakeMemoryStore:
    """``(scope, path) → markdown``；缺文件回 ``""``（与 MemoryStore 契约一致）。"""

    def __init__(self, bodies: dict[tuple[str | None, str], str]) -> None:
        self._bodies = bodies

    async def load(self, user_id, path, scope=None):
        del user_id
        return self._bodies.get((scope, path), "")

    async def list(self, user_id, scope=None):
        del user_id, scope
        return []


@pytest.fixture
def account_creds() -> AccountCredentials:
    return AccountCredentials(
        api_key="account-jwt", base_url="https://example.test/v1/account"
    )


# --- 链从哪儿来（本机 DB 路） ---------------------------------------------------------------


def _patch_chain(monkeypatch, result):
    """把 ``FolderRepository.list_ancestor_chain_ids`` 换成固定答案 / 固定异常。"""
    from agentcore.db.repositories import folders as folders_repo

    async def _fake(self, folder_id, *, user_id):
        del self, folder_id, user_id
        if isinstance(result, Exception):
            raise result
        return list(result)

    monkeypatch.setattr(
        folders_repo.FolderRepository, "list_ancestor_chain_ids", _fake
    )


async def test_db_chain_reads_the_rel_path_tree(monkeypatch):
    _patch_chain(monkeypatch, [OUTER, MIDDLE, CURRENT])
    assert await db_scope_chain("u1", CURRENT, session=object()) == CHAIN  # type: ignore[arg-type]


async def test_db_chain_refuses_a_chain_that_is_not_about_this_folder(monkeypatch):
    """解析结果不含当前层 = 没对上；注它的祖先就是把别人的约定塞进来。"""
    _patch_chain(monkeypatch, [OUTER, "f_someone_else"])
    assert await db_scope_chain("u1", CURRENT, session=object()) == (CURRENT,)  # type: ignore[arg-type]


async def test_db_chain_survives_a_broken_lookup(monkeypatch):
    """继承解析失败绝不能打断回合——退回只当前层，症状是不继承而非报错。"""
    _patch_chain(monkeypatch, RuntimeError("no folders table here"))
    assert await db_scope_chain("u1", CURRENT, session=object()) == (CURRENT,)  # type: ignore[arg-type]


async def test_db_chain_missing_folder_is_not_a_scope(monkeypatch):
    """软删 / 未知 id：list_ancestor 空列表。不能退回只当前层，否则已删桌的设定还会灌。"""
    _patch_chain(monkeypatch, [])
    assert await db_scope_chain("u1", CURRENT, session=object()) == ()  # type: ignore[arg-type]


async def test_no_folder_means_no_chain():
    assert await db_scope_chain("u1", None) == ()


async def test_a_folder_with_no_cloud_directory_inherits_nothing(monkeypatch):
    """``rel_path`` 为空的历史行：不知道它在树的哪一层，凭 id 猜链比不继承更糟。"""
    from agentcore.db.repositories import folders as folders_repo

    async def _get(self, folder_id, *, user_id):
        del self, user_id
        return SimpleNamespace(id=folder_id, rel_path=None)

    monkeypatch.setattr(folders_repo.FolderRepository, "get_by_id", _get)
    repo = folders_repo.FolderRepository(object())  # type: ignore[arg-type]
    assert await repo.list_ancestor_chain_ids(CURRENT, user_id="u1") == [CURRENT]


async def test_an_unknown_folder_has_no_chain_at_all(monkeypatch):
    from agentcore.db.repositories import folders as folders_repo

    async def _missing(self, folder_id, *, user_id):
        del self, folder_id, user_id
        return None

    monkeypatch.setattr(folders_repo.FolderRepository, "get_by_id", _missing)
    repo = folders_repo.FolderRepository(object())  # type: ignore[arg-type]
    assert await repo.list_ancestor_chain_ids(CURRENT, user_id="u1") == []


# --- 本机 DB 路 -------------------------------------------------------------------------


async def test_user_rules_inject_global_then_ancestors_then_current():
    repo = _FakeRuleRepo(
        always={
            None: [_Doc("用户规则.md", "- 全局规则")],
            OUTER: [_Doc("用户规则.md", "- 外层规则")],
            MIDDLE: [_Doc("用户规则.md", "- 中层规则")],
            CURRENT: [_Doc("用户规则.md", "- 当前规则")],
        }
    )
    md = compose_injected_rules(
        await _user_rule_fragments(repo, "u1", scope_chain=CHAIN)  # type: ignore[arg-type]
    )
    positions = [md.index(t) for t in ("全局规则", "外层规则", "中层规则", "当前规则")]
    assert positions == sorted(positions)
    assert md.count(_ANCESTOR_SETTINGS_LABEL) == 2
    assert md.count(_FOLDER_SETTINGS_LABEL) == 1


async def test_ancestor_layers_carry_the_nearer_wins_wording():
    """没有硬覆盖结构：冲突靠措辞 + 就近，所以标签必须自己说清楚。"""
    repo = _FakeRuleRepo(always={OUTER: [_Doc("用户规则.md", "- 外层规则")]})
    md = compose_injected_rules(
        await _user_rule_fragments(repo, "u1", scope_chain=CHAIN)  # type: ignore[arg-type]
    )
    assert "以更近的为准" in md
    assert md.index(_ANCESTOR_SETTINGS_LABEL) < md.index("外层规则")


async def test_profile_memory_inherits_but_navigation_stays_local():
    """导航是工作区根相对路径的路由表，外层的路由从里层根解析不到 → 不继承。"""
    store = _FakeMemoryStore(
        {
            (None, "偏好.md"): "- 沟通偏好",
            (OUTER, "画像.md"): "- 外层画像",
            (OUTER, "导航.md"): "- 外层导航",
            (CURRENT, "画像.md"): "- 当前画像",
            (CURRENT, "导航.md"): "- 当前导航",
        }
    )
    md = compose_injected_rules(
        await _memory_fragments(store, "u1", scope_chain=(OUTER, CURRENT))  # type: ignore[arg-type]
    )
    assert "外层画像" in md
    assert "外层导航" not in md
    assert "当前导航" in md
    assert md.index(_ANCESTOR_SETTINGS_LABEL) < md.index(_FOLDER_SETTINGS_LABEL)


async def test_nearer_layer_is_injected_later_than_farther_one():
    """「近覆盖远」在提示词里就是这个顺序事实——外层在前，当前层贴着任务。"""
    repo = _FakeRuleRepo(
        always={OUTER: [_Doc("用户规则.md", "- 一律用英文写提交信息")]}
    )
    store = _FakeMemoryStore(
        {
            (OUTER, "画像.md"): "- 本组用 Java",
            (CURRENT, "画像.md"): "- 本仓用 Rust",
        }
    )
    md = await assemble_injected_rules(
        store,  # type: ignore[arg-type]
        repo,  # type: ignore[arg-type]
        "u1",
        folder_id=CURRENT,
        enabled=True,
        scope_chain=(OUTER, CURRENT),
    )
    assert md.index("本组用 Java") < md.index("一律用英文写提交信息")
    assert md.index("一律用英文写提交信息") < md.index("本仓用 Rust")


async def test_assemble_interleaves_by_scope_not_author():
    """同一层里槽位先于用户常驻；不是先倒完全部规则再倒画像。"""
    repo = _FakeRuleRepo(
        always={
            None: [_Doc("用户规则.md", "- 全局规则")],
            OUTER: [_Doc("用户规则.md", "- 外层规则")],
            CURRENT: [_Doc("用户规则.md", "- 当前规则")],
        }
    )
    store = _FakeMemoryStore(
        {
            (None, "偏好.md"): "- 沟通偏好",
            (OUTER, "画像.md"): "- 外层画像",
            (CURRENT, "画像.md"): "- 当前画像",
        }
    )
    md = await assemble_injected_rules(
        store,  # type: ignore[arg-type]
        repo,  # type: ignore[arg-type]
        "u1",
        folder_id=CURRENT,
        enabled=True,
        scope_chain=(OUTER, CURRENT),
    )
    order = (
        "沟通偏好",
        "全局规则",
        "外层画像",
        "外层规则",
        "当前画像",
        "当前规则",
    )
    positions = [md.index(t) for t in order]
    assert positions == sorted(positions)
    assert "专属规则" not in md
    assert "专属记忆" not in md
    assert "专属设定" in md


async def test_no_chain_means_current_layer_only():
    """未解析出链（无嵌套 / 解析失败）时的行为与继承落地前逐字一致。"""
    repo = _FakeRuleRepo(
        always={OUTER: [_Doc("用户规则.md", "- 外层规则")], CURRENT: []}
    )
    store = _FakeMemoryStore({(OUTER, "画像.md"): "- 外层画像"})
    md = await assemble_injected_rules(
        store,  # type: ignore[arg-type]
        repo,  # type: ignore[arg-type]
        "u1",
        folder_id=CURRENT,
        enabled=True,
    )
    assert md == ""


# --- account 票路（云载荷 / warm 快照） ---------------------------------------------------


def test_cloud_rules_payload_labels_ancestors_ahead_of_current():
    payload = {
        "global_rules": [{"name": "用户规则.md", "content": "- 全局规则"}],
        "ancestor_rules": [
            {
                "name": "用户规则.md",
                "content": "- 外层规则",
                "folder_id": OUTER,
            },
            {
                "name": "用户规则.md",
                "content": "- 中层规则",
                "folder_id": MIDDLE,
            },
        ],
        "project_rules": [{"name": "用户规则.md", "content": "- 当前规则"}],
        "folder_chain": [OUTER, MIDDLE, CURRENT],
    }
    md = compose_injected_rules(
        _user_rule_fragments_from_cloud(payload, folder_id=CURRENT)
    )
    positions = [md.index(t) for t in ("全局规则", "外层规则", "中层规则", "当前规则")]
    assert positions == sorted(positions)
    assert md.count(_ANCESTOR_SETTINGS_LABEL) == 2


def test_older_cloud_without_ancestor_keys_simply_does_not_inherit():
    payload = {
        "global_rules": [{"name": "用户规则.md", "content": "- 全局规则"}],
        "project_rules": [{"name": "用户规则.md", "content": "- 当前规则"}],
    }
    md = compose_injected_rules(
        _user_rule_fragments_from_cloud(payload, folder_id=CURRENT)
    )
    assert "全局规则" in md and "当前规则" in md
    assert _ANCESTOR_SETTINGS_LABEL not in md


def test_cloud_untagged_ancestors_zip_when_counts_match():
    payload = {
        "global_rules": [],
        "ancestor_rules": [
            {"name": "用户规则.md", "content": "- 外层规则"},
            {"name": "用户规则.md", "content": "- 中层规则"},
        ],
        "project_rules": [],
        "folder_chain": [OUTER, MIDDLE, CURRENT],
    }
    md = compose_injected_rules(
        _user_rule_fragments_from_cloud(payload, folder_id=CURRENT)
    )
    assert md.index("外层规则") < md.index("中层规则")
    assert md.count(_ANCESTOR_SETTINGS_LABEL) == 2


def test_cloud_untagged_ancestors_bag_on_outermost_when_counts_differ():
    payload = {
        "global_rules": [],
        "ancestor_rules": [
            {"name": "用户规则.md", "content": "- 规则甲"},
            {"name": "用户规则.md", "content": "- 规则乙"},
            {"name": "用户规则.md", "content": "- 规则丙"},
        ],
        "project_rules": [],
        "folder_chain": [OUTER, MIDDLE, CURRENT],
    }
    md = compose_injected_rules(
        _user_rule_fragments_from_cloud(payload, folder_id=CURRENT)
    )
    assert md.index("规则甲") < md.index("规则乙") < md.index("规则丙")
    assert md.count(_ANCESTOR_SETTINGS_LABEL) == 1


def test_cloud_ancestor_rules_without_chain_dump_as_one_layer():
    payload = {
        "ancestor_rules": [{"name": "用户规则.md", "content": "- 外层规则"}],
        "project_rules": [{"name": "用户规则.md", "content": "- 当前规则"}],
    }
    md = compose_injected_rules(
        _user_rule_fragments_from_cloud(payload, folder_id=CURRENT)
    )
    assert md.index("外层规则") < md.index("当前规则")
    assert _ANCESTOR_SETTINGS_LABEL in md


def test_snapshot_memory_walks_the_folder_chain():
    snapshot = AccountPrepareSnapshot(
        memory_bodies={
            ("", "偏好.md"): "- 沟通偏好",
            (OUTER, "画像.md"): "- 外层画像",
            (OUTER, "导航.md"): "- 外层导航",
            (CURRENT, "画像.md"): "- 当前画像",
        },
        folder_chain=(OUTER, CURRENT),
    )
    md = compose_injected_rules(
        _memory_fragments_from_snapshot(snapshot, folder_id=CURRENT)
    )
    assert md.index("外层画像") < md.index("当前画像")
    assert "外层导航" not in md


def test_a_chain_that_does_not_contain_this_folder_is_refused():
    """快照不是给这个文件夹 warm 的：按它的祖先注入 = 把别人的约定塞进来。"""
    snapshot = AccountPrepareSnapshot(
        memory_bodies={(OUTER, "画像.md"): "- 外层画像"},
        folder_chain=(OUTER, "f_someone_else"),
    )
    assert snapshot_scope_chain(snapshot, CURRENT) == (CURRENT,)
    md = compose_injected_rules(
        _memory_fragments_from_snapshot(snapshot, folder_id=CURRENT)
    )
    assert "外层画像" not in md


def test_cloud_empty_folder_chain_means_the_desk_is_gone():
    """云显式 ``folder_chain: []`` = 这张桌不在活树，不要退回只当前层。"""
    payload = {
        "global_rules": [{"name": "用户规则.md", "content": "- 全局规则"}],
        "ancestor_rules": [{"name": "用户规则.md", "content": "- 外层规则"}],
        "project_rules": [{"name": "用户规则.md", "content": "- 当前规则"}],
        "folder_chain": [],
    }
    md = compose_injected_rules(
        _user_rule_fragments_from_cloud(payload, folder_id=CURRENT)
    )
    assert "全局规则" in md
    assert "外层规则" not in md
    assert "当前规则" not in md
    assert cloud_scope_chain({"folder_chain": []}, CURRENT) == ()


def test_cloud_chain_falls_back_to_current_folder_when_absent_or_junk():
    assert cloud_scope_chain({}, CURRENT) == (CURRENT,)
    assert cloud_scope_chain({"folder_chain": "nope"}, CURRENT) == (CURRENT,)
    assert cloud_scope_chain({"folder_chain": [OUTER, CURRENT]}, CURRENT) == (
        OUTER,
        CURRENT,
    )
    assert cloud_scope_chain({"folder_chain": [OUTER]}, None) == ()


def test_snapshot_empty_folder_chain_skips_the_dead_desk():
    snapshot = AccountPrepareSnapshot(
        rules_payload={"folder_chain": []},
        memory_bodies={
            ("", "偏好.md"): "- 沟通偏好",
            (CURRENT, "画像.md"): "- 当前画像",
        },
        folder_chain=(),
    )
    md = compose_injected_rules(
        _memory_fragments_from_snapshot(snapshot, folder_id=CURRENT)
    )
    assert "沟通偏好" in md
    assert "当前画像" not in md
    assert snapshot_scope_chain(snapshot, CURRENT) == ()


def test_on_demand_empty_folder_chain_is_global_only():
    payload = {
        "global_on_demand_rules": [{"name": "合规.md", "content": "- 全局合规"}],
        "ancestor_on_demand_rules": [{"name": "发布.md", "content": "- 外层发布"}],
        "project_on_demand_rules": [{"name": "接口.md", "content": "- 当前接口"}],
        "folder_chain": [],
    }
    names = [r.name for r in on_demand_user_rules_from_cloud(payload, folder_id=CURRENT)]
    assert names == ["合规"]
    assert (
        lookup_on_demand_rule_body_from_cloud(payload, folder_id=CURRENT, name="接口")
        is None
    )
    assert (
        lookup_on_demand_rule_body_from_cloud(payload, folder_id=CURRENT, name="合规")
        == "- 全局合规"
    )


async def test_ticketed_turn_injects_the_inherited_layers(account_creds):
    clear_account_rules_memory_cache()
    seed_account_rules_memory_cache(
        "u1",
        CURRENT,
        AccountPrepareSnapshot(
            rules_payload={
                "global_rules": [{"name": "用户规则.md", "content": "- 全局规则"}],
                "ancestor_rules": [
                    {
                        "name": "用户规则.md",
                        "content": "- 外层规则",
                        "folder_id": OUTER,
                    }
                ],
                "project_rules": [],
                "folder_chain": [OUTER, CURRENT],
            },
            memory_bodies={
                (OUTER, "画像.md"): "- 外层画像",
                (CURRENT, "画像.md"): "- 当前画像",
            },
            folder_chain=(OUTER, CURRENT),
        ),
    )
    with account_credentials_scope(account_creds):
        md = await assemble_turn_rules(
            _FakeMemoryStore({}),  # type: ignore[arg-type]
            "u1",
            folder_id=CURRENT,
            enabled=True,
        )
    assert "外层规则" in md
    assert md.index("外层画像") < md.index("外层规则")
    assert md.index("外层规则") < md.index("当前画像")


async def test_ticketed_turn_skips_dead_desk_settings(account_creds):
    clear_account_rules_memory_cache()
    seed_account_rules_memory_cache(
        "u1",
        CURRENT,
        AccountPrepareSnapshot(
            rules_payload={
                "global_rules": [{"name": "用户规则.md", "content": "- 全局规则"}],
                "project_rules": [{"name": "用户规则.md", "content": "- 当前规则"}],
                "folder_chain": [],
            },
            memory_bodies={
                ("", "偏好.md"): "- 沟通偏好",
                (CURRENT, "画像.md"): "- 当前画像",
            },
            folder_chain=(),
        ),
    )
    with account_credentials_scope(account_creds):
        md = await assemble_turn_rules(
            _FakeMemoryStore({}),  # type: ignore[arg-type]
            "u1",
            folder_id=CURRENT,
            enabled=True,
        )
    assert "全局规则" in md
    assert "沟通偏好" in md
    assert "当前规则" not in md
    assert "当前画像" not in md


# --- 按需目录 / consult 取正文 -------------------------------------------------------------


def test_on_demand_catalog_includes_ancestor_layers():
    payload = {
        "global_on_demand_rules": [{"name": "合规.md", "content": "- 全局合规"}],
        "ancestor_on_demand_rules": [{"name": "发布.md", "content": "- 外层发布流程"}],
        "project_on_demand_rules": [{"name": "接口.md", "content": "- 当前接口约定"}],
    }
    names = [r.name for r in on_demand_user_rules_from_cloud(payload, folder_id=CURRENT)]
    assert set(names) == {"合规", "发布", "接口"}


def test_consult_body_comes_from_the_nearest_layer_that_has_it():
    payload = {
        "global_on_demand_rules": [{"name": "发布.md", "content": "- 全局发布"}],
        "ancestor_on_demand_rules": [
            {"name": "发布.md", "content": "- 外层发布"},
            {"name": "发布.md", "content": "- 中层发布"},
        ],
        "project_on_demand_rules": [{"name": "发布.md", "content": "- 当前发布"}],
    }
    assert (
        lookup_on_demand_rule_body_from_cloud(payload, folder_id=CURRENT, name="发布")
        == "- 当前发布"
    )
    del payload["project_on_demand_rules"]
    assert (
        lookup_on_demand_rule_body_from_cloud(payload, folder_id=CURRENT, name="发布")
        == "- 中层发布"
    )
    del payload["ancestor_on_demand_rules"]
    assert (
        lookup_on_demand_rule_body_from_cloud(payload, folder_id=CURRENT, name="发布")
        == "- 全局发布"
    )


async def test_ticketed_on_demand_catalog_reads_the_snapshot(account_creds):
    clear_account_rules_memory_cache()
    seed_account_rules_memory_cache(
        "u1",
        CURRENT,
        AccountPrepareSnapshot(
            rules_payload={
                "global_on_demand_rules": [],
                "ancestor_on_demand_rules": [
                    {"name": "发布.md", "content": "- 外层发布流程"}
                ],
                "project_on_demand_rules": [],
                "folder_chain": [OUTER, CURRENT],
            },
            folder_chain=(OUTER, CURRENT),
        ),
    )
    with account_credentials_scope(account_creds):
        rules = await load_on_demand_user_rules("u1", folder_id=CURRENT)
    assert [r.name for r in rules] == ["发布"]
