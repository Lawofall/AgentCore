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
from agentcore.runtime.runs.website_style import clear_style_confirmation
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
async def test_execute_allows_build_website_toolshed_style_without_style_ledger():
    """无视觉风格账 + 非 full_auto 亦可 build_website style=toolshed（场面账硬闸已拆）。"""
    cid = "auto-gate-toolshed-no-style"
    clear_delivery_confirmation(cid)
    clear_style_confirmation(cid)
    t = _delegate(
        user_message="随便聊聊",
        conversation_id=cid,
        base_ctx=local_ctx(),
    )
    result = await t.execute(
        {
            "playbook": "build_website",
            "playbook_args": {
                "topic": "Ops",
                "style": "toolshed",
                "sections": ["应用外壳", "数据表格"],
            },
            "coordinate": False,
        },
        local_ctx(),
    )
    assert result.success is True
    clear_delivery_confirmation(cid)
    clear_style_confirmation(cid)


@pytest.mark.asyncio
async def test_execute_allows_build_website_without_style_ledger():
    cid = "auto-gate-website-no-style"
    clear_style_confirmation(cid)
    t = _delegate(
        user_message="做个落地页",
        conversation_id=cid,
        base_ctx=local_ctx(),
    )
    result = await t.execute(
        {
            "playbook": "build_website",
            "playbook_args": {"topic": "Acme 落地页", "sections": ["首页"]},
            "coordinate": False,
        },
        local_ctx(),
    )
    assert result.success is True
    clear_style_confirmation(cid)


@pytest.mark.asyncio
async def test_execute_runnable_ledger_no_longer_rejects_website_toolshed_style():
    """即便残留 delivery 账，也不再硬拒 website + style=toolshed。"""
    cid = "auto-gate-runnable-toolshed"
    clear_delivery_confirmation(cid)
    clear_style_confirmation(cid)
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
            "playbook": "build_website",
            "playbook_args": {
                "topic": "Ops",
                "style": "toolshed",
                "sections": ["应用外壳", "数据表格"],
            },
            "coordinate": False,
        },
        local_ctx(),
    )
    assert result.success is True
    clear_delivery_confirmation(cid)
    clear_style_confirmation(cid)


@pytest.mark.asyncio
async def test_execute_plan_ledger_no_longer_rejects_website():
    cid = "auto-gate-plan-only"
    clear_delivery_confirmation(cid)
    clear_style_confirmation(cid)
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
            "playbook": "build_website",
            "playbook_args": {
                "topic": "X",
                "style": "toolshed",
                "sections": ["应用外壳"],
            },
            "coordinate": False,
        },
        local_ctx(),
    )
    assert r1.success is True

    r2 = await t.execute(
        {
            "playbook": "build_website",
            "playbook_args": {"topic": "X", "sections": ["首页"]},
            "coordinate": False,
        },
        local_ctx(),
    )
    assert r2.success is True
    clear_delivery_confirmation(cid)
    clear_style_confirmation(cid)
