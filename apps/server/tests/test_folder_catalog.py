"""Tests for derived CEO ``<文件夹清单>`` injection (跨文件夹找文件夹)."""

from agentcore.memory.store import CORE_MEMORY_FILE, FileMemoryStore
from agentcore.runtime.context.folder_catalog import (
    FolderCatalogEntry,
    build_folder_catalog_entries,
    catalog_label_for,
    load_folder_catalog,
    prioritize_current_folder,
    render_folder_catalog,
)
from agentcore.runtime.resolve.prompt import (
    assemble_system_prompt,
    compose_ceo_chat_prompt,
)
from agentcore.runtime.skills import build_system_skill_registry


def test_render_folder_catalog_empty_omits_block():
    assert render_folder_catalog([]) == ""
    assert render_folder_catalog(()) == ""


def test_render_folder_catalog_one_line_per_folder():
    text = render_folder_catalog(
        [
            FolderCatalogEntry("f1", "支付网关", "处理 Stripe 回调的结算服务"),
            FolderCatalogEntry("f2", "空壳", ""),
        ]
    )
    assert text.startswith("<文件夹清单>")
    assert text.endswith("</文件夹清单>")
    assert "- 支付网关（id=`f1`）：处理 Stripe 回调的结算服务" in text
    assert "- 空壳（id=`f2`）" in text
    assert "- 空壳（id=`f2`）：" not in text
    assert "list_folders" not in text
    assert "resolve_folder" not in text
    assert "target_folder_id" not in text


def test_render_uses_full_path_so_resolve_can_disambiguate():
    """嵌套后同名末段合法；只给末段等于把每次 resolve 逼进歧义回合。"""
    text = render_folder_catalog(
        [
            FolderCatalogEntry("f1", "图标", "线上图标库", rel_path="设计/图标"),
            FolderCatalogEntry("f2", "图标", "旧版存档", rel_path="归档/图标"),
        ]
    )
    assert "- 设计/图标（id=`f1`）：线上图标库" in text
    assert "- 归档/图标（id=`f2`）：旧版存档" in text


def test_build_sort_preserved_and_hard_limit_truncates():
    folders = [
        ("a", "最近"),
        ("b", "次近"),
        ("c", "更早"),
        ("d", "最旧"),
    ]
    profiles = {
        "a": "## 关于\n- 支付相关\n",
        "b": "## 关于\n- 博客\n",
        "c": "## 关于\n- 工具\n",
        "d": "## 关于\n- 遗留\n",
    }
    entries = build_folder_catalog_entries(folders, profiles, limit=2)
    assert [e.name for e in entries] == ["最近", "次近"]
    assert entries[0].summary == "支付相关"
    assert entries[1].summary == "博客"


def test_build_carries_rel_path_when_present():
    entries = build_folder_catalog_entries(
        [("a", "图标", "设计/图标"), ("b", "顶层")],
        {},
        limit=12,
    )
    assert entries[0].rel_path == "设计/图标"
    assert entries[0].label == "设计/图标"
    # No rel_path (legacy / local folder) still has to be addressable.
    assert entries[1].label == "顶层"


def test_build_limit_zero_or_empty_folders():
    assert build_folder_catalog_entries([("a", "x")], {"a": "hi"}, limit=0) == []
    assert build_folder_catalog_entries([], {}, limit=12) == []


def test_derived_rename_and_profile_update_reflected_immediately():
    """No cache in the pure builder — next assemble sees rename / 画像 edits."""
    folders_v1 = [("f1", "旧名")]
    profiles_v1 = {"f1": "## 关于\n- 旧定位\n"}
    v1 = build_folder_catalog_entries(folders_v1, profiles_v1, limit=12)
    assert render_folder_catalog(v1) == render_folder_catalog(
        [FolderCatalogEntry("f1", "旧名", "旧定位")]
    )

    folders_v2 = [("f1", "新名")]
    profiles_v2 = {"f1": "## 关于\n- 新定位：支付结算\n"}
    v2 = build_folder_catalog_entries(folders_v2, profiles_v2, limit=12)
    text = render_folder_catalog(v2)
    assert "新名（id=`f1`）：新定位：支付结算" in text
    assert "旧名" not in text
    assert "旧定位" not in text


def test_compose_ceo_includes_catalog_outside_rules():
    base = assemble_system_prompt(rules_markdown="## 偏好\n- 用中文\n")
    catalog = [
        FolderCatalogEntry("f1", "支付网关", "结算服务"),
    ]
    ceo = compose_ceo_chat_prompt(
        base,
        skill_registry=build_system_skill_registry(),
        ceo_tool_names={"delegate", "consult"},
        folder_catalog=catalog,
        current_folder_id="f1",
    )
    assert "<文件夹清单>" in ceo
    assert "- 支付网关（id=`f1`，当前出生桌）：结算服务" in ceo
    # Always memory stays in <rules>; catalog is a sibling section.
    assert "<rules>" in ceo
    assert "用中文" in ceo
    rules_end = ceo.index("</rules>")
    catalog_start = ceo.index("<文件夹清单>")
    assert rules_end < catalog_start


def test_compose_ceo_omits_empty_catalog():
    base = assemble_system_prompt()
    ceo = compose_ceo_chat_prompt(
        base,
        skill_registry=build_system_skill_registry(),
        ceo_tool_names={"delegate"},
        folder_catalog=[],
    )
    assert "<文件夹清单>" not in ceo


async def test_load_folder_catalog_wires_folder_repo_and_profiles(
    tmp_path, monkeypatch
):
    store = FileMemoryStore(tmp_path)
    await store.save(
        "u1",
        CORE_MEMORY_FILE,
        "# 画像\n> note\n\n## 关于\n- 支付结算服务\n",
        scope="fid-pay",
    )
    await store.save(
        "u1",
        CORE_MEMORY_FILE,
        "## 关于\n- 个人博客\n",
        scope="fid-blog",
    )

    class _Folder:
        def __init__(self, fid: str, name: str, rel_path: str = "") -> None:
            self.id = fid
            self.name = name
            self.rel_path = rel_path

    class _Repo:
        def __init__(self, session) -> None:  # noqa: ANN001
            self._session = session

        async def list_by_user_recently_active(self, user_id: str, *, limit: int):
            assert user_id == "u1"
            # Already activity-sorted; caller passes the hard cap as ``limit``.
            rows = [
                _Folder("fid-pay", "支付", "工作/支付"),
                _Folder("fid-blog", "博客"),
                _Folder("fid-old", "遗留"),
            ]
            return rows[:limit]

        async def get_by_id(self, folder_id: str, *, user_id: str):
            assert user_id == "u1"
            if folder_id == "fid-old":
                return _Folder("fid-old", "遗留")
            return None

    class _CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return False

    import agentcore.runtime.context.folder_catalog as catalog_mod

    monkeypatch.setattr(catalog_mod, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(catalog_mod, "FolderRepository", _Repo)

    entries = await load_folder_catalog(store, "u1", limit=2)
    assert [e.folder_id for e in entries] == ["fid-pay", "fid-blog"]
    assert entries[0].name == "支付"
    assert entries[0].label == "工作/支付"
    assert entries[0].summary == "支付结算服务"
    assert entries[1].summary == "个人博客"
    assert render_folder_catalog(entries)

    pinned = await load_folder_catalog(
        store, "u1", limit=2, current_folder_id="fid-old"
    )
    assert [e.folder_id for e in pinned] == ["fid-old", "fid-pay"]


async def test_load_folder_catalog_no_folders_returns_empty(tmp_path, monkeypatch):
    class _Repo:
        def __init__(self, session) -> None:  # noqa: ANN001
            pass

        async def list_by_user_recently_active(self, user_id: str, *, limit: int):
            return []

    class _CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return False

    import agentcore.runtime.context.folder_catalog as catalog_mod

    monkeypatch.setattr(catalog_mod, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(catalog_mod, "FolderRepository", _Repo)

    store = FileMemoryStore(tmp_path)
    assert await load_folder_catalog(store, "u1") == []


def test_render_marks_current_birth_desk_and_omits_tool_how():
    text = render_folder_catalog(
        [
            FolderCatalogEntry("f1", "白板", "", rel_path="白板"),
            FolderCatalogEntry("f2", "图标", "线上图标库", rel_path="设计/图标"),
        ],
        current_folder_id="f1",
    )
    assert "当前出生桌已在行内标出" in text
    assert "- 白板（id=`f1`，当前出生桌）" in text
    assert "- 设计/图标（id=`f2`）：线上图标库" in text
    assert "list_folders" not in text
    assert "resolve_folder" not in text
    assert "file_list" not in text
    assert "target_folder_id" not in text


def test_catalog_label_for_and_prioritize_current_folder():
    entries = [
        FolderCatalogEntry("fid-pay", "支付", "", rel_path="工作/支付"),
        FolderCatalogEntry("fid-blog", "博客"),
    ]
    assert catalog_label_for(entries, "fid-pay") == "工作/支付"
    assert catalog_label_for(entries, "missing") is None
    assert catalog_label_for(entries, None) is None

    class _Row:
        def __init__(self, fid: str) -> None:
            self.id = fid

    recent = [_Row("a"), _Row("b"), _Row("c")]
    pinned = prioritize_current_folder(
        recent, current_id="c", current_row=None, limit=2
    )
    assert [row.id for row in pinned] == ["c", "a"]

    extra = _Row("z")
    inserted = prioritize_current_folder(
        recent, current_id="z", current_row=extra, limit=2
    )
    assert [row.id for row in inserted] == ["z", "a"]
