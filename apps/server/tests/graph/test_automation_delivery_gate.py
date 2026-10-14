"""Delegate：场面账（automation delivery / style）硬闸已拆除。"""

from __future__ import annotations

import pytest

from agentcore.core.types import WorkspaceBoundary
from agentcore.runtime.events import EventSink
from agentcore.runtime.runs.automation_delivery import (
    clear_delivery_confirmation,
    record_delivery_confirmation,
)
from agentcore.tools.builtin.graph import GraphTool
from agentcore.tools.registry import ToolRegistry
from tests.delegate.conftest import Provider, local_ctx

_KICKOFF_RULES = WorkspaceBoundary.FOLDER


def _delegate(
    *,
    user_message: str,
    conversation_id: str,
    base_ctx,
    permission_axes: WorkspaceBoundary | None = None,
) -> GraphTool:
    axes = permission_axes or _KICKOFF_RULES
    return GraphTool(
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
