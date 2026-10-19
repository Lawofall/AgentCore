"""Host GUI face — attach a window, snapshot its control tree, click/type by ref.

Transport is ``DesktopClientChannel.request_host`` (``HostOp.GUI_*``).
The accessibility tree is untrusted input (does not raise permission).
"""

from __future__ import annotations

import base64
import json
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.desktop.channel import HostOp, HostOpError
from agentcore.tools.protocol import ToolContext, ToolResult

logger = get_logger(__name__)

GUI_ACTIONS = frozenset({"attach", "snapshot", "click", "type"})
_GUI_TIMEOUT_SECONDS = 45.0
_ATTACH_TIMEOUT_SECONDS = 180.0
_GUI_CHANNEL_SLACK = 15.0
_ATTACH_CHANNEL_SLACK = 20.0
_KEYFRAME_MAX_BYTES = 512 * 1024
_TREE_OUTPUT_LIMIT = 12_000

_OP_BY_ACTION: dict[str, HostOp] = {
    "attach": HostOp.GUI_ATTACH,
    "snapshot": HostOp.GUI_SNAPSHOT,
    "click": HostOp.GUI_CLICK,
    "type": HostOp.GUI_TYPE,
}

_SEQ: dict[str, int] = {}

_KIND_MESSAGES: dict[str, str] = {
    "HostGuiUnsupportedPlatform": (
        "本机窗口操作第一刀仅 Windows。当前桌面不是 Windows，无法附着窗口。"
    ),
    "HostGuiNotAttached": (
        "还没有附着窗口。请先 host(action=attach)，在系统选窗里选出要操作的那一扇。"
    ),
    "HostGuiEmptyTree": (
        "看不见该窗控件（窗口没有暴露可读的 UI Automation 树）。"
        "Electron / 自绘界面常见如此。请如实说明，不要猜坐标去点。"
    ),
    "HostGuiRefStale": "控件 ref 已过期或找不到。请再 snapshot 一次拿到新的 ref。",
    "HostGuiUserAbort": "你在该窗口里动了键鼠，本轮窗口操作已停止。需要的话请重新附着后再继续。",
    "HostGuiPasswordDenied": "该控件是密码框（IsPassword），不能代填。请你在窗口里自己输入。",
    "HostGuiPickerCancelled": "没有选出窗口（取消了选窗）。需要操作窗口时请再 attach 一次。",
    "HostGuiNoInvoke": (
        "该控件不能点（没有 Invoke / Toggle / Selection 模式）。请 snapshot 改点别的控件。"
    ),
    "HostGuiNoValue": "该控件不能键入（没有 Value 模式）。请 snapshot 改填别的输入框。",
}


def host_gui_timeout_seconds(action: str) -> float:
    if action == "attach":
        return _ATTACH_TIMEOUT_SECONDS + _ATTACH_CHANNEL_SLACK
    return _GUI_TIMEOUT_SECONDS + _GUI_CHANNEL_SLACK


def _fail(error: str, *, contract_failure: bool = False, code: str | None = None) -> ToolResult:
    return ToolResult(
        tool_call_id="",
        success=False,
        output="",
        error=error,
        contract_failure=contract_failure,
        metadata={"code": code} if code else {},
    )


def _no_channel_error() -> ToolResult:
    return ToolResult(
        tool_call_id="",
        success=False,
        output="",
        error=(
            "host 需要桌面回填通道：当前无在线桌面客户端，"
            "无法操作本机窗口。请如实说明限制，勿假装已点过。"
        ),
    )


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {"value": value}


def _model_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _next_frame_path(conversation_id: str) -> str:
    n = _SEQ.get(conversation_id, 0) + 1
    _SEQ[conversation_id] = n
    return f"host/step-{n:04d}.jpg"


def _kind_error(kind: str, detail: str) -> str:
    mapped = _KIND_MESSAGES.get(kind)
    if mapped:
        return mapped
    text = (detail or "").strip()
    return text or "本机窗口操作失败。"


def _log_gui_kind(kind: str) -> None:
    # Event names must be literals so sync_log_event_registry can see them.
    if kind == "HostGuiNotAttached":
        logger.info("host.gui_unattached")
    elif kind == "HostGuiEmptyTree":
        logger.info("host.gui_empty_tree")
    elif kind == "HostGuiRefStale":
        logger.info("host.gui_ref_stale")
    elif kind == "HostGuiUserAbort":
        logger.info("host.gui_user_abort")
    elif kind == "HostGuiPasswordDenied":
        logger.info("host.gui_password_denied")
    elif kind == "HostGuiUnsupportedPlatform":
        logger.info("host.gui_unsupported")


def _human_body(action: str, payload: dict[str, Any]) -> str:
    title = str(payload.get("title") or "").strip()
    name = str(payload.get("control_name") or "").strip()
    if action == "attach":
        return f"已附着「{title}」" if title else "已附着窗口"
    if action == "snapshot":
        return f"读了「{title}」的控件" if title else "读取窗口控件"
    if action == "click":
        if name and title:
            return f"在「{title}」里点了「{name}」"
        if name:
            return f"点了「{name}」"
        return "点了控件"
    if action == "type":
        if name and title:
            return f"在「{title}」的「{name}」里键入"
        return "键入了文本"
    return action


def _untrusted_window(payload: dict[str, Any]) -> dict[str, Any]:
    block: dict[str, Any] = {}
    title = payload.get("title")
    if isinstance(title, str) and title.strip():
        block["title"] = title.strip()
    tree = payload.get("controls")
    if isinstance(tree, str) and tree.strip():
        block["controls"] = tree
    elements = payload.get("elements")
    if isinstance(elements, str) and elements.strip():
        block["elements"] = elements
    return block


def _output_payload(
    action: str, payload: dict[str, Any], *, keyframe: str | None
) -> dict[str, Any]:
    out: dict[str, Any] = {"action": action}
    version = payload.get("snapshot_version")
    if version is not None:
        out["snapshot_version"] = version
    if keyframe:
        out["keyframe"] = keyframe
        out["note"] = "关键帧只给人看，不要根据像素描述或猜坐标。"
    untrusted = _untrusted_window(payload)
    if untrusted:
        out["untrusted_window_content"] = untrusted
    clicked = payload.get("clicked")
    if isinstance(clicked, dict):
        out["clicked"] = clicked
    typed = payload.get("typed")
    if isinstance(typed, dict):
        out["typed"] = typed
    return out


async def _persist_keyframe(
    context: ToolContext,
    payload: dict[str, Any],
) -> str | None:
    raw = payload.get("jpeg_b64")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        frame = base64.b64decode(raw, validate=False)
    except Exception:
        logger.warning("host.keyframe_decode_failed")
        return None
    if len(frame) > _KEYFRAME_MAX_BYTES:
        logger.info("host.keyframe_skipped_too_large", n_bytes=len(frame))
        return None
    cid = context.conversation_id or ""
    path = _next_frame_path(cid)
    try:
        await context.backend.write_bytes(path, frame)
    except Exception as exc:  # noqa: BLE001 — keyframe must not fail the action
        logger.warning("host.keyframe_write_failed", error=str(exc))
        return None
    return path


def _transport_args(action: str, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "conversation_id": context.conversation_id or "",
    }
    if action in {"click", "type"}:
        ref = str(arguments.get("ref") or "").strip()
        payload["ref"] = ref
        version = arguments.get("snapshot_version")
        if version is not None:
            payload["snapshot_version"] = version
    if action == "type":
        payload["text"] = str(arguments.get("text") or "")
    return payload


def _postcondition_error(action: str, payload: dict[str, Any]) -> str | None:
    if action == "click":
        clicked = payload.get("clicked")
        if isinstance(clicked, dict) and clicked.get("was_disabled") is True:
            return "点了但控件仍是禁用状态（postcondition_failed）。请 snapshot 后改点别的。"
        if isinstance(clicked, dict) and clicked.get("matched") is False:
            return "没有点到目标控件（postcondition_failed）。请再 snapshot。"
    if action == "type":
        typed = payload.get("typed")
        if isinstance(typed, dict) and typed.get("matched") is False:
            return "键入后内容与请求不一致（postcondition_failed）。请再 snapshot。"
    return None


async def execute_gui(
    action: str,
    arguments: dict[str, Any],
    context: ToolContext,
) -> ToolResult:
    if action in {"click", "type"} and not str(arguments.get("ref") or "").strip():
        return _fail(
            "缺少必填参数：ref（先 host(action=snapshot)）。",
            contract_failure=True,
        )
    if action == "type" and not str(arguments.get("text") or ""):
        return _fail("缺少必填参数：text。", contract_failure=True)

    channel = context.desktop_channel
    if channel is None:
        return _no_channel_error()

    op = _OP_BY_ACTION[action]
    timeout = host_gui_timeout_seconds(action)
    logger.info(
        "desktop.host_op_request",
        run_id=context.run_id,
        conversation_id=context.conversation_id,
        op=op.value,
        timeout_seconds=timeout,
    )
    try:
        value = await channel.request_host(
            op,
            _transport_args(action, arguments, context),
            timeout=timeout,
        )
    except HostOpError as e:
        kind = e.kind or ""
        _log_gui_kind(kind)
        return _fail(_kind_error(kind, str(e)), code=kind or None)

    payload = _as_dict(value)
    fail_kind = str(payload.get("kind") or "")
    if payload.get("ok") is False or fail_kind in _KIND_MESSAGES:
        kind = fail_kind if fail_kind in _KIND_MESSAGES else ""
        _log_gui_kind(kind)
        detail = str(payload.get("detail") or payload.get("error") or "")
        return _fail(_kind_error(kind, detail), code=kind or None)

    keyframe = await _persist_keyframe(context, payload)
    output = _output_payload(action, payload, keyframe=keyframe)
    title = str(payload.get("title") or "").strip()
    display: dict[str, Any] = {
        "kind": "host",
        "action": action,
        "body": _human_body(action, payload),
    }
    if title:
        display["title"] = title
    if keyframe:
        display["frame"] = keyframe
    post_err = _postcondition_error(action, payload)
    if post_err:
        return ToolResult(
            tool_call_id="",
            success=False,
            output=_model_json(output),
            error=post_err,
            output_limit=_TREE_OUTPUT_LIMIT,
            display=display,
            metadata={"code": "postcondition_failed"},
        )
    return ToolResult(
        tool_call_id="",
        success=True,
        output=_model_json(output),
        output_limit=_TREE_OUTPUT_LIMIT,
        display=display,
    )
