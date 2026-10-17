"""交代注入的两个范围：账号库，加上当前文件夹。祖先不是范围。

没写 ``apply`` 的正文不进回合。快照列表名是已分好的通道。
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
from agentcore.memory.rules_injection import (
    _FOLDER_SETTINGS_LABEL,
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
    description: str = ""


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

    async def list_path_user_rules(self, user_id, folder_id):
        del user_id
        return []

    async def list_scoped_markdown(self, user_id, folder_id):
        del user_id
        return [
            *self._always.get(folder_id, []),
            *self._on_demand.get(folder_id, []),
        ]


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


async def test_user_rules_do_not_fill_settings():
    """未入目录的正文不进 `<设定>`。"""
    repo = _FakeRuleRepo(
        always={
            None: [_Doc("全局.md", "- 全局规则")],
            OUTER: [_Doc("外层.md", "- 外层规则")],
            MIDDLE: [_Doc("中层.md", "- 中层规则")],
            CURRENT: [_Doc("当前.md", "- 当前规则")],
        }
    )
    md = compose_injected_rules(
        await _user_rule_fragments(repo, "u1")  # type: ignore[arg-type]
    )
    assert md == ""


async def test_unenrolled_rules_do_not_enter_settings():
    repo = _FakeRuleRepo(always={OUTER: [_Doc("外层.md", "- 外层规则")]})
    md = compose_injected_rules(
        await _user_rule_fragments(repo, "u1")  # type: ignore[arg-type]
    )
    assert md == ""


async def test_ai_notes_do_not_inject_with_user_rules():
    repo = _FakeRuleRepo(
        always={
            CURRENT: [
                _Doc("用户规则.md", "---\napply: always\n---\n- 当前规则")
            ]
        }
    )
    md = await assemble_injected_rules(
        repo,  # type: ignore[arg-type]
        "u1",
        folder_id=CURRENT,
    )
    assert "当前规则" in md
    assert "外层画像" not in md
    assert "当前画像" not in md
    assert "当前导航" not in md


def _on_demand(body: str, description: str) -> str:
    return f"---\napply: on_demand\ndescription: {description}\n---\n{body}"


async def test_desk_copies_are_not_the_catalog():
    """账号范围只看见账号文档。当前文件夹看见自己的，看不见祖先。"""
    repo = _FakeRuleRepo(
        on_demand={
            None: [_Doc("用户规则.md", _on_demand("- 账号库用中文", "账号"), "账号")],
            OUTER: [_Doc("用户规则.md", _on_demand("- 一律用英文写提交信息", "外层"), "外层")],
            CURRENT: [_Doc("用户规则.md", _on_demand("- 本仓提交用中文", "当前"), "当前")],
        }
    )
    from agentcore.memory.rule_resolve import resolve_rules
    from agentcore.memory.rules_injection import _scope_live_rules

    account = await _scope_live_rules(repo, "u1", None, 0)  # type: ignore[arg-type]
    account_bodies = [rule.content for rule in resolve_rules(account).on_demand]
    assert account_bodies == [_on_demand("- 账号库用中文", "账号")]

    here = await _scope_live_rules(repo, "u1", CURRENT, 1)  # type: ignore[arg-type]
    here_bodies = [rule.content for rule in resolve_rules(here).on_demand]
    assert here_bodies == [_on_demand("- 本仓提交用中文", "当前")]
    assert all("英文" not in body for body in here_bodies)


async def test_assemble_layers_user_rules_by_scope():
    repo = _FakeRuleRepo(
        always={
            None: [_Doc("全局.md", "---\napply: always\n---\n- 全局规则")],
            OUTER: [_Doc("外层.md", "---\napply: always\n---\n- 外层规则")],
            CURRENT: [_Doc("当前.md", "---\napply: always\n---\n- 当前规则")],
        }
    )
    md = await assemble_injected_rules(
        repo,  # type: ignore[arg-type]
        "u1",
        folder_id=CURRENT,
    )
    assert "全局规则" in md
    assert "当前规则" in md
    assert "外层规则" not in md
    assert _FOLDER_SETTINGS_LABEL in md
    assert md.index("全局规则") < md.index("当前规则")


async def test_no_chain_means_current_layer_only():
    """祖先文档不是范围：当前文件夹没有常驻时，外层正文不进来。"""
    repo = _FakeRuleRepo(
        always={OUTER: [_Doc("用户规则.md", "- 外层规则")], CURRENT: []}
    )
    md = await assemble_injected_rules(
        repo,  # type: ignore[arg-type]
        "u1",
        folder_id=CURRENT,
    )
    assert md == ""


# --- account 票路（云载荷 / warm 快照） ---------------------------------------------------


def test_cloud_rules_payload_stacks_account_then_this_folder():
    payload = {
        "global_rules": [{"name": "全局.md", "content": "- 全局规则"}],
        "ancestor_rules": [
            {
                "name": "外层.md",
                "content": "- 外层规则",
                "folder_id": OUTER,
            },
            {
                "name": "中层.md",
                "content": "- 中层规则",
                "folder_id": MIDDLE,
            },
        ],
        "project_rules": [{"name": "当前.md", "content": "- 当前规则"}],
        "folder_chain": [OUTER, MIDDLE, CURRENT],
    }
    md = compose_injected_rules(
        _user_rule_fragments_from_cloud(payload, folder_id=CURRENT)
    )
    assert "全局规则" in md
    assert "当前规则" in md
    assert "外层规则" not in md
    assert "中层规则" not in md
    assert md.index("全局规则") < md.index("当前规则")


def test_older_cloud_without_ancestor_keys_still_stacks_this_folder():
    payload = {
        "global_rules": [{"name": "全局.md", "content": "- 全局规则"}],
        "project_rules": [{"name": "当前.md", "content": "- 当前规则"}],
    }
    md = compose_injected_rules(
        _user_rule_fragments_from_cloud(payload, folder_id=CURRENT)
    )
    assert "全局规则" in md
    assert "当前规则" in md


def test_cloud_untagged_ancestors_zip_when_counts_match():
    payload = {
        "global_rules": [],
        "ancestor_rules": [
            {"name": "外层.md", "content": "- 外层规则"},
            {"name": "中层.md", "content": "- 中层规则"},
        ],
        "project_rules": [],
        "folder_chain": [OUTER, MIDDLE, CURRENT],
    }
    md = compose_injected_rules(
        _user_rule_fragments_from_cloud(payload, folder_id=CURRENT)
    )
    assert md == ""


def test_cloud_untagged_ancestors_bag_on_outermost_when_counts_differ():
    payload = {
        "global_rules": [],
        "ancestor_rules": [
            {"name": "甲.md", "content": "- 规则甲"},
            {"name": "乙.md", "content": "- 规则乙"},
            {"name": "丙.md", "content": "- 规则丙"},
        ],
        "project_rules": [],
        "folder_chain": [OUTER, MIDDLE, CURRENT],
    }
    md = compose_injected_rules(
        _user_rule_fragments_from_cloud(payload, folder_id=CURRENT)
    )
    assert md == ""


def test_cloud_ancestor_rules_without_chain_stay_out():
    payload = {
        "ancestor_rules": [{"name": "外层.md", "content": "- 外层规则"}],
        "project_rules": [{"name": "当前.md", "content": "- 当前规则"}],
    }
    md = compose_injected_rules(
        _user_rule_fragments_from_cloud(payload, folder_id=CURRENT)
    )
    assert "当前规则" in md
    assert "外层规则" not in md


def test_a_chain_that_does_not_contain_this_folder_is_refused():
    """快照不是给这个文件夹 warm 的：按它的祖先注入 = 把别人的约定塞进来。"""
    snapshot = AccountPrepareSnapshot(
        memory_bodies={(OUTER, "画像.md"): "- 外层画像"},
    )
    assert snapshot_scope_chain(snapshot, CURRENT) == (CURRENT,)


def test_cloud_empty_folder_chain_does_not_drop_this_folder():
    """``folder_chain`` 不是注入范围。空链不丢掉当前文件夹的常驻。"""
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
    assert "当前规则" in md
    assert "外层规则" not in md
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
    )
    assert snapshot_scope_chain(snapshot, CURRENT) == ()


def test_on_demand_empty_folder_chain_is_global_only():
    payload = {
        "global_on_demand_rules": [
            {
                "name": "合规.md",
                "content": "---\napply: on_demand\ndescription: 查阅合规\n---\n- 全局合规",
                "description": "查阅合规",
            }
        ],
        "project_on_demand_rules": [
            {
                "name": "接口.md",
                "content": "---\napply: on_demand\ndescription: 查阅接口\n---\n- 当前接口",
                "description": "查阅接口",
            }
        ],
        "folder_chain": [],
    }
    names = [r.name for r in on_demand_user_rules_from_cloud(payload, folder_id=CURRENT)]
    assert names == ["合规", "接口"]
    assert (
        lookup_on_demand_rule_body_from_cloud(payload, folder_id=CURRENT, name="接口")
        == "- 当前接口"
    )
    assert (
        lookup_on_demand_rule_body_from_cloud(payload, folder_id=CURRENT, name="合规")
        == "- 全局合规"
    )


async def test_ticketed_turn_injects_account_not_ancestors(account_creds):
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
        ),
    )
    with account_credentials_scope(account_creds):
        md = await assemble_turn_rules(
            "u1",
            folder_id=CURRENT,
        )
    assert "全局规则" in md
    assert "外层规则" not in md
    assert "外层画像" not in md
    assert "当前画像" not in md


async def test_ticketed_turn_keeps_this_folder_when_chain_is_empty(account_creds):
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
        ),
    )
    with account_credentials_scope(account_creds):
        md = await assemble_turn_rules(
            "u1",
            folder_id=CURRENT,
        )
    assert "全局规则" in md
    assert "当前规则" in md
    assert "沟通偏好" not in md
    assert "当前画像" not in md


# --- 按需目录 / consult 取正文 -------------------------------------------------------------


def test_on_demand_catalog_includes_account_and_this_folder():
    payload = {
        "global_on_demand_rules": [
            {
                "name": "合规.md",
                "content": "---\napply: on_demand\ndescription: 查阅合规\n---\n- 全局合规",
                "description": "查阅合规",
            }
        ],
        "ancestor_on_demand_rules": [
            {
                "name": "发布.md",
                "content": "---\napply: on_demand\ndescription: 查阅发布\n---\n- 外层发布流程",
                "description": "查阅发布",
            }
        ],
        "project_on_demand_rules": [
            {
                "name": "接口.md",
                "content": "---\napply: on_demand\ndescription: 查阅接口\n---\n- 当前接口约定",
                "description": "查阅接口",
            }
        ],
    }
    names = [r.name for r in on_demand_user_rules_from_cloud(payload, folder_id=CURRENT)]
    assert names == ["合规", "接口"]


def test_consult_body_prefers_this_folder():
    payload = {
        "global_on_demand_rules": [
            {
                "name": "发布.md",
                "content": "---\napply: on_demand\ndescription: 查阅发布\n---\n- 全局发布",
                "description": "查阅发布",
            }
        ],
        "ancestor_on_demand_rules": [
            {
                "name": "发布.md",
                "content": "---\napply: on_demand\ndescription: 查阅发布\n---\n- 外层发布",
                "description": "查阅发布",
            }
        ],
        "project_on_demand_rules": [
            {
                "name": "发布.md",
                "content": "---\napply: on_demand\ndescription: 查阅发布\n---\n- 当前发布",
                "description": "查阅发布",
            }
        ],
    }
    assert (
        lookup_on_demand_rule_body_from_cloud(payload, folder_id=CURRENT, name="发布")
        == "- 当前发布"
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
                    {
                        "name": "发布.md",
                        "content": "- 外层发布流程",
                        "description": "查阅发布",
                    }
                ],
                "project_on_demand_rules": [],
                "folder_chain": [OUTER, CURRENT],
            },
        ),
    )
    with account_credentials_scope(account_creds):
        rules = await load_on_demand_user_rules("u1", folder_id=CURRENT)
    assert rules == []
