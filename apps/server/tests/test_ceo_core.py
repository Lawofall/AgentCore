"""常驻核权威归属。基座与核现为空。

同一件事只能有一处权威。

- 环境能不能做某事 = **算出来的事实**，住 ``<工作区>``（执行 / 桌 / 缺口 /
  Git …）。核里复述一份就会漂——案 0a71 就是核里
  散文断言「``md_export`` 无条件装配」，而装配态只能由开场表表达，模型于是花了整段思考链猜「队员到底有没有」，最后把用户的三个选项
  连同提问一起丢了。**下面那条 assembly-claim 测试就是这个 bug 的回归守卫。**
- 可履约的操作手册 = **skill 正文**。工具侧 ``capability_how_suffix`` 现恒为空，不挂冻结核。
- **目录行 ≠ 已查阅**在按需目录前言。来源编号在回执尾，不进基座。
  不按可用性下线。变体表不进核。全员基座现空。
"""

from __future__ import annotations

from agentcore.runtime.resolve.prompt import (
    _CEO_CORE_HINT,
    assemble_system_prompt,
    capability_how_suffix,
)
from agentcore.runtime.resolve.prompt.compose import _on_demand_preamble


def test_resident_core_is_empty():
    """基座与核现为空。"""
    assert assemble_system_prompt() == ""
    assert _CEO_CORE_HINT == ""


def test_core_states_no_tool_assembly_claims():
    """案 0a71 回归守卫：装配态只能由 `<工作区>` 算，核里不许用散文断言。"""
    hint = _CEO_CORE_HINT
    assert "无条件装配" not in hint
    # 现行导出器名字出现在核里，几乎总是为了断言「它一定在」——装没装配看开场表。
    assert "md_export" not in hint
    # 后缀枚举同理：能产什么由开场表 + 缺口表达，核只教对照结构面。
    for suffix in ("pptx", "xlsx", "docx"):
        assert suffix not in hint.lower(), f"核不枚举 .{suffix}；对照开场表"
    assert "产物格式" not in hint
    assert "产物格式" not in assemble_system_prompt()


def test_core_does_not_restate_computed_workspace_facts():
    """已在事实行算出来的事实，核里不留第二份（第三份就是漂移的开始）。"""
    hint = _CEO_CORE_HINT
    # 出网/生图声称走基座诚实对照，核不点名。
    assert "无原生生图工具" not in hint
    assert "出站网络" not in hint
    assert "出站网络" not in assemble_system_prompt()


def test_capability_how_suffix_is_empty():
    """工具侧无 consult 手册。"""
    assert capability_how_suffix({"browser", "host", "run"}) == ""


def test_catalog_preamble_owns_row_vs_consulted():
    """目录行 ≠ 已查阅只在按需目录前言。"""
    hint = _CEO_CORE_HINT
    base = assemble_system_prompt()
    preamble = "\n".join(_on_demand_preamble())
    assert "目录行" in preamble
    assert "目录行" not in base
    assert "目录行" not in hint
    assert "已落盘" not in base
    assert "已落盘" not in hint
    assert "邻格" not in base
