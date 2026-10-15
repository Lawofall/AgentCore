"""云文件夹目录树的路径代数（双模式工作区 §5.4）。

``folders.rel_path`` 是云文件夹物理落点的单一真相源，没有 ``parent_id``：父子关系
就是路径前缀。这里覆盖的三件事都会直接决定盘上会发生什么——名字净化决定目录叫什
么，同层去重决定两个文件夹会不会撞进同一个目录，子树重挂决定一次改名会把多少行
一起改写。
"""

from agentcore.workspace.cloud_tree import (
    DEFAULT_FOLDER_NAME,
    ancestor_chain,
    is_same_or_descendant,
    join_rel_path,
    normalize_rel_path,
    parent_rel_path,
    rel_path_name,
    reparent_rel_path,
    sanitize_folder_name,
    unique_sibling_name,
    would_nest_into_self,
)


def test_separators_collapse_so_a_name_stays_one_segment():
    """名字里的斜杠不得偷偷造出一层嵌套——嵌套只由 rel_path 表达。"""
    assert sanitize_folder_name("报告 2025/Q1") == "报告 2025_Q1"
    assert sanitize_folder_name("设计\\图标") == "设计_图标"


def test_filesystem_hostile_characters_are_replaced():
    assert sanitize_folder_name('a<b>c:d"e|f?g*h') == "a_b_c_d_e_f_g_h"


def test_windows_device_names_are_neutralized():
    """``CON`` 在 Windows 上开不出目录，须让位。"""
    assert sanitize_folder_name("CON") != "CON"


def test_a_name_that_sanitizes_to_nothing_gets_the_fallback():
    assert sanitize_folder_name("...") == DEFAULT_FOLDER_NAME
    assert sanitize_folder_name("") == DEFAULT_FOLDER_NAME


def test_duplicate_siblings_get_numbered():
    assert unique_sibling_name("报告", []) == "报告"
    assert unique_sibling_name("报告", ["报告"]) == "报告 (2)"
    assert unique_sibling_name("报告", ["报告", "报告 (2)"]) == "报告 (3)"


def test_sibling_uniqueness_is_case_insensitive():
    """Windows / macOS 上 ``Report`` 与 ``report`` 是同一个目录。"""
    assert unique_sibling_name("Report", ["report"]) == "Report (2)"


def test_agentcore_root_name_is_reserved_only_when_nested():
    """嵌套子文件夹不能叫 ``GAgent``（会撞上层工作区根的约定目录）；顶层可以。"""
    assert unique_sibling_name("GAgent", [], nested=True) == "GAgent (2)"
    assert unique_sibling_name("GAgent", []) == "GAgent"


def test_rel_path_normalization_and_projection():
    assert normalize_rel_path(None) == ""
    assert normalize_rel_path("/设计/图标/") == "设计/图标"
    assert normalize_rel_path("设计\\图标") == "设计/图标"
    assert parent_rel_path("设计/图标") == "设计"
    assert parent_rel_path("设计") == ""
    assert rel_path_name("设计/图标") == "图标"
    assert join_rel_path("设计", "图标") == "设计/图标"
    assert join_rel_path(None, "设计") == "设计"


def test_descendant_check_compares_segments_not_raw_prefixes():
    """``报告备份`` 不是 ``报告`` 的子树，尽管字符串前缀匹配。"""
    assert is_same_or_descendant("报告/一季度", "报告")
    assert is_same_or_descendant("报告", "报告")
    assert not is_same_or_descendant("报告备份", "报告")


def test_rename_rewrites_the_whole_subtree_by_prefix():
    assert reparent_rel_path("设计", old_prefix="设计", new_prefix="视觉") == "视觉"
    assert (
        reparent_rel_path("设计/图标/线性", old_prefix="设计", new_prefix="视觉")
        == "视觉/图标/线性"
    )


def test_move_to_and_from_the_tree_root():
    assert reparent_rel_path("设计/图标", old_prefix="设计/图标", new_prefix="图标") == "图标"
    assert reparent_rel_path("图标", old_prefix="图标", new_prefix="设计/图标") == "设计/图标"


def test_rows_outside_the_moved_subtree_are_untouched():
    assert reparent_rel_path("其他", old_prefix="设计", new_prefix="视觉") == "其他"


def test_moving_a_folder_into_its_own_subtree_is_detected():
    assert would_nest_into_self(source="设计", new_parent="设计/图标")
    assert would_nest_into_self(source="设计", new_parent="设计")
    assert not would_nest_into_self(source="设计", new_parent="其他")
    # 移回树根永远合法（根不可能在任何文件夹的子树里）。
    assert not would_nest_into_self(source="设计/图标", new_parent=None)


# --- 作用域链（规则 / 记忆沿树由外向里继承） -------------------------------------------

_PLACEMENTS = [
    ("f_design", "设计"),
    ("f_icons", "设计/图标"),
    ("f_line", "设计/图标/线性"),
    ("f_other", "其他"),
    ("f_backup", "设计备份"),
]


def test_ancestor_chain_runs_outermost_first_and_ends_at_self():
    """注入端按这个顺序拼，越靠后越近——顺序即「近覆盖远」的载体。"""
    assert ancestor_chain("设计/图标/线性", _PLACEMENTS) == [
        "f_design",
        "f_icons",
        "f_line",
    ]


def test_top_level_folder_chain_is_just_itself():
    assert ancestor_chain("设计", _PLACEMENTS) == ["f_design"]


def test_siblings_and_prefix_lookalikes_are_not_ancestors():
    """``设计备份`` 只是字符串前缀像，不得把它的约定继承进 ``设计/图标``。"""
    assert ancestor_chain("设计/图标", _PLACEMENTS) == ["f_design", "f_icons"]


def test_chain_ignores_placements_without_a_cloud_directory():
    assert ancestor_chain("设计/图标", [("f_ghost", ""), *_PLACEMENTS]) == [
        "f_design",
        "f_icons",
    ]
