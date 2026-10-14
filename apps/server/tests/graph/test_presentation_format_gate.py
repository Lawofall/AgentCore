"""Delegate 演讲/PPT：ledger 后果闸（pptx+执行+仅 md 拒）；无文案意图拒整批。"""

from __future__ import annotations

import pytest

from agentcore.core.types import WorkspaceBoundary
from agentcore.runtime.events import EventSink
from agentcore.runtime.runs.presentation_format import (
    clear_format_confirmation,
    is_pptx_format_confirmation,
    record_format_confirmation,
    tasks_silently_downgrade_pptx_to_md,
)
from agentcore.tools.builtin.graph import GraphTool
from agentcore.tools.registry import ToolRegistry
from tests.delegate.conftest import Provider, ctx, local_ctx

_KICKOFF_RULES = WorkspaceBoundary.FOLDER


def _delegate(
    *,
    user_message: str,
    conversation_id: str,
    base_ctx,
    permission_axes: WorkspaceBoundary | None = None,
) -> GraphTool:
    return GraphTool(
        llm=Provider(["X"]),
        sink=EventSink(),
        system_prompt="SYS",
        user_message=user_message,
        history=[],
        tools=ToolRegistry(),
        base_tool_context=base_ctx,
        permission_axes=permission_axes or _KICKOFF_RULES,
        conversation_id=conversation_id,
        folder_id="test_birth",
        approval_gate=None,
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def test_is_pptx_format_confirmation_by_label():
    from agentcore.runtime.runs.presentation_format import FormatConfirmation

    assert is_pptx_format_confirmation(
        FormatConfirmation(format_id="f0", label="PowerPoint（.pptx）", source="ask_user")
    )
    assert not is_pptx_format_confirmation(
        FormatConfirmation(format_id="f1", label="Marp Markdown 幻灯片", source="ask_user")
    )
    assert not is_pptx_format_confirmation(
        FormatConfirmation(format_id="f2", label="仅讲稿大纲", source="ask_user")
    )


def test_tasks_silently_downgrade_pptx_to_md():
    assert tasks_silently_downgrade_pptx_to_md(
        [
            {
                "role": "课件工程师",
                "task": "写 Markdown（Slidev/Marp）幻灯片",
                "deliverable": {"form": "files", "artifacts": ["deck/multi-agent-ppt.md"]},
            }
        ]
    )
    assert not tasks_silently_downgrade_pptx_to_md(
        [
            {
                "role": "课件工程师",
                "task": "用 python-pptx 生成 course.pptx",
                "deliverable": {"form": "files", "artifacts": ["course.pptx"]},
            }
        ]
    )
    assert not tasks_silently_downgrade_pptx_to_md(
        [{"role": "写手", "task": "整理讲稿要点"}]
    )


# ── execute 级：无账不因文案拒 ────────────────────────────────────────────────
