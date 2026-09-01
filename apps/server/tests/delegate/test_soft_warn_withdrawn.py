"""委派一次性软提示族已撤：旧命中形仍成功入图，结果尾无告警文案。"""

from __future__ import annotations

import pytest

from tests.delegate.conftest import Provider, ctx, tool

_WARN_SNIPPETS = (
    "吃同批队友产出",
    "同时塞了设计与实现",
    "首批单节点手写写工程",
)


@pytest.mark.asyncio
async def test_execute_former_consumer_deps_hit_has_no_soft_tail():
    t = tool(Provider(["调研甲产出", "调研乙产出", "综述"]))
    result = await t.execute(
        {
            "tasks": [
                {"id": "r1", "role": "调研甲", "task": "调研偶数哥德巴赫猜想相关文献"},
                {"id": "r2", "role": "调研乙", "task": "调研奇数哥德巴赫猜想相关文献"},
                {
                    "id": "s",
                    "role": "汇总",
                    "task": "基于前两位队员的产出，整理一份综述报告",
                },
            ],
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success is True
    assert result.error in (None, "")
    for snip in _WARN_SNIPPETS:
        assert snip not in (result.output or "")


@pytest.mark.asyncio
async def test_execute_former_design_impl_hit_has_no_soft_tail():
    t = tool(Provider(["骨架产出"]))
    result = await t.execute(
        {
            "tasks": [
                {
                    "id": "fs",
                    "role": "全栈工程师",
                    "task": "新建 MVP 骨架",
                    "deliverable": {
                        "form": "files",
                        "artifacts": [
                            "agent-editor/DESIGN.md",
                            "agent-editor/src/main.ts",
                        ],
                    },
                }
            ],
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success is True
    assert result.error in (None, "")
    for snip in _WARN_SNIPPETS:
        assert snip not in (result.output or "")


@pytest.mark.asyncio
async def test_execute_former_root_slice_hit_has_no_soft_tail():
    t = tool(Provider(["OUT"]))
    result = await t.execute(
        {
            "tasks": [
                {
                    "role": "工程师",
                    "task": "从零实现应用 MVP",
                    "deliverable": {"form": "workspace"},
                }
            ],
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success is True
    assert result.error in (None, "")
    for snip in _WARN_SNIPPETS:
        assert snip not in (result.output or "")
