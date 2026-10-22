"""``read`` of workspace raster images → native multimodal on the same model."""

from __future__ import annotations

import base64
import time
from typing import Any

from agentcore.tools.builtin.file_ops.errors import (
    _maybe_channel_dead_error,
    _outside_workspace_error,
)
from agentcore.tools.builtin.file_ops.observe import format_observe_envelope
from agentcore.tools.protocol import ToolContext, ToolResult
from agentcore.workspace.image_files import image_data_mime
from agentcore.workspace.protocol import (
    NotAFile,
    OutsideWorkspace,
    PathNotFound,
    WorkspaceError,
)

_IMAGE_NATIVE_OUTPUT = (
    "已把工作区图片发给当前模型（多模态）。勿假定未看见像素。"
)


def _observe(
    *,
    kind: str,
    path: str,
    start: float,
    size: int | None = None,
    text: str = "",
    success: bool = True,
    metadata: dict[str, Any] | None = None,
) -> ToolResult:
    output = format_observe_envelope(
        kind=kind,
        path=path,
        type_label="image",
        size=size,
        text=text,
    )
    return ToolResult(
        tool_call_id="",
        success=success,
        output=output,
        duration_ms=int((time.monotonic() - start) * 1000),
        metadata=metadata or {},
    )


async def read_raster_image(
    rel_path: str,
    *,
    path_key: str,
    context: ToolContext,
    start: float,
) -> ToolResult:
    """Send pixels to the current model, or an honest envelope when it cannot see."""
    display = path_key or rel_path
    if not getattr(context, "accepts_images", False):
        return _observe(
            kind="image",
            path=display,
            start=start,
            text="当前主模型不收图，本回合未把像素发给模型。勿把路径当作已读像素。",
        )
    try:
        raw = await context.backend.read_bytes(rel_path)
    except OutsideWorkspace as e:
        return _outside_workspace_error(
            rel_path, start, location=context.backend.location, reason=str(e)
        )
    except PathNotFound:
        from agentcore.tools.builtin.file_ops.read import _file_not_found_error

        return await _file_not_found_error(rel_path, start=start, context=context)
    except NotAFile:
        from agentcore.tools.builtin.file_ops.read import _not_a_file_error

        return _not_a_file_error(rel_path, start)
    except WorkspaceError as e:
        dead = _maybe_channel_dead_error(e, start)
        if dead is not None:
            return dead
        return ToolResult(
            tool_call_id="",
            success=False,
            output=f"无法读取工作区图片：{e}",
            error=str(e),
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    if not raw:
        return _observe(
            kind="image",
            path=display,
            start=start,
            size=0,
            text="工作区图片为空，没有像素可发。",
        )
    mime = image_data_mime(display)
    b64 = base64.b64encode(raw).decode("ascii")
    part = {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{b64}"},
    }
    return ToolResult(
        tool_call_id="",
        success=True,
        output=f"{_IMAGE_NATIVE_OUTPUT}\npath: `{display}`",
        duration_ms=int((time.monotonic() - start) * 1000),
        metadata={"native_image_parts": [part]},
    )
