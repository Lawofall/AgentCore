"""Delegate：场面账（automation delivery / style）硬闸已拆除。"""

from __future__ import annotations

import pytest

from agentcore.core.types import (
    AutonomyPolicy,
    CommandAxis,
    FileWriteAxis,
    HostAxis,
    PermissionAxes,
    recipe_to_axes,
)
from agentcore.runtime.events import EventSink
from agentcore.runtime.runs.automation_delivery import (
    clear_delivery_confirmation,
    record_delivery_confirmation,
)
from agentcore.tools.builtin.delegate import DelegateTool
from agentcore.tools.registry import ToolRegistry
from tests.delegate.conftest import Provider, local_ctx

_KICKOFF_RULES = PermissionAxes(
    FileWriteAxis.SESSION,
    CommandAxis.AUTO,
    HostAxis.ASK,
)


def _delegate(
    *,
    user_message: str,
    conversation_id: str,
    base_ctx,
    autonomy: AutonomyPolicy | None = None,
    permission_axes: PermissionAxes | None = None,
) -> DelegateTool:
    axes = permission_axes or (
        recipe_to_axes(autonomy) if autonomy is not None else _KICKOFF_RULES
    )
    return DelegateTool(
        llm=Provider(["X"]),
        sink=EventSink(),
        system_prompt="SYS",
        user_message=user_message,
        history=[],
        tools=ToolRegistry(),
        base_tool_context=base_ctx,
        permission_axes=axes,
        conversation_id=conversation_id,
        folder_id="test_birth",
        approval_gate=None,
    )


@pytest.mark.asyncio
async def test_execute_allows_automation_text_without_delivery_ledger():
    cid = "auto-gate-missing"
    clear_delivery_confirmation(cid)
    t = _delegate(
        user_message="做短视频自动化 Agent",
        conversation_id=cid,
        base_ctx=local_ctx(),
    )
    result = await t.execute(
        {
            "tasks": [{"role": "工程师", "task": "搭自动发帖流水线"}],
            "coordinate": False,
        },
        local_ctx(),
    )
    assert result.success is True
    clear_delivery_confirmation(cid)


@pytest.mark.asyncio
async def test_execute_allows_toolshed_shaped_handwrite_without_style_ledger():
    """无视觉风格账 + 非 full_auto 亦可手写控制台形 tasks（场面账硬闸已拆）。"""
    cid = "auto-gate-toolshed-no-style"
    clear_delivery_confirmation(cid)
    t = _delegate(
        user_message="随便聊聊",
        conversation_id=cid,
        base_ctx=local_ctx(),
    )
    result = await t.execute(
        {
            "tasks": [{"role": "前端", "task": "搭运营控制台外壳与数据表格"}],
            "coordinate": False,
        },
        local_ctx(),
    )
    assert result.success is True
    clear_delivery_confirmation(cid)

@pytest.mark.asyncio
async def test_execute_allows_site_handwrite_without_style_ledger():
    cid = "auto-gate-website-no-style"
    t = _delegate(
        user_message="做个落地页",
        conversation_id=cid,
        base_ctx=local_ctx(),
    )
    result = await t.execute(
        {
            "tasks": [{"role": "前端", "task": "实现 Acme 落地页首页"}],
            "coordinate": False,
        },
        local_ctx(),
    )
    assert result.success is True

@pytest.mark.asyncio
async def test_execute_runnable_ledger_no_longer_rejects_website_toolshed_style():
    """即便残留 delivery 账，也不再硬拒控制台形手写 tasks。"""
    cid = "auto-gate-runnable-toolshed"
    clear_delivery_confirmation(cid)
    record_delivery_confirmation(
        cid, format_id="f0", label="可运行自动化", source="ask_user"
    )
    t = _delegate(
        user_message="做短视频自动化 Agent",
        conversation_id=cid,
        base_ctx=local_ctx(),
    )
    result = await t.execute(
        {
            "tasks": [{"role": "前端", "task": "搭运营控制台外壳与数据表格"}],
            "coordinate": False,
        },
        local_ctx(),
    )
    assert result.success is True
    clear_delivery_confirmation(cid)

@pytest.mark.asyncio
async def test_execute_plan_ledger_no_longer_rejects_website():
    cid = "auto-gate-plan-only"
    clear_delivery_confirmation(cid)
    record_delivery_confirmation(
        cid, format_id="f2", label="仅方案", source="ask_user"
    )
    t = _delegate(
        user_message="打造内容分发工作流",
        conversation_id=cid,
        base_ctx=local_ctx(),
    )
    r1 = await t.execute(
        {
            "tasks": [{"role": "前端", "task": "搭控制台应用外壳"}],
            "coordinate": False,
        },
        local_ctx(),
    )
    assert r1.success is True

    r2 = await t.execute(
        {
            "tasks": [{"role": "前端", "task": "实现落地页首页"}],
            "coordinate": False,
        },
        local_ctx(),
    )
    assert r2.success is True
    clear_delivery_confirmation(cid)
