"""Prepare-budget abort copy still surfaces as an honest channel-down error."""

from __future__ import annotations

from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import error_fields_for
from agentcore.workspace.limits import CHANNEL_DEAD_PREPARE_ABORT, is_channel_dead_detail
from agentcore.workspace.protocol import WorkspaceIOError


def test_error_fields_for_surfaces_channel_dead_prepare_abort():
    code, message, _ctx = error_fields_for(
        WorkspaceIOError(CHANNEL_DEAD_PREPARE_ABORT),
        fallback_code=ErrorCode.STREAM_ERROR,
        fallback_message="服务出错了，请稍后重试。",
    )
    assert code == ErrorCode.STREAM_ERROR
    assert "本机工作区通道无响应" in message
    assert "服务出错了" not in message
    assert is_channel_dead_detail(CHANNEL_DEAD_PREPARE_ABORT)
