"""Workspace error / ToolResult mapping for file_ops tools."""

from __future__ import annotations

import time
from typing import Any

from agentcore.runtime.facts import (
    CROSS_TURN_RETRY_KEY,
    CrossTurnRetry,
    normalize_cross_turn_retry,
)
from agentcore.tools.protocol import ToolResult
from agentcore.workspace import external_mounts as _external_mounts
from agentcore.workspace import shared_mounts as _shared_mounts
from agentcore.workspace.limits import (
    FILE_TOO_LARGE_DETAIL,
    WORKSPACE_READ_MAX_BYTES,
    channel_dead_error_message,
    channel_dead_retire_metadata,
    is_channel_dead_detail,
    is_file_too_large_detail,
    is_liveness_timeout_detail,
    op_liveness_timeout_metadata,
)
from agentcore.workspace.protocol import WorkspaceError


def _error(
    error: str,
    start: float,
    *,
    contract_failure: bool = False,
    metadata: dict[str, Any] | None = None,
    failure_code: str | None = None,
    user_face: bool = True,
    cross_turn_retry: CrossTurnRetry | str | None = None,
    product_face: str | None = None,
) -> ToolResult:
    """Build a failed ToolResult with elapsed timing.

    ``contract_failure`` marks a self-correctable argument/environment rejection
    (e.g. concurrent-write collision the model fixes by renaming, or path-not-found
    the model fixes by changing ``path``) so the run-scoped tool circuit breaker
    skips normal failure tallies — see :class:`~agentcore.tools.protocol.ToolResult`.
    Explicit ``retire_tools`` in ``metadata`` still hard-disables named tools
    (e.g. workspace channel dead).

    ``cross_turn_retry`` is a recorded fact (futile / not_futile); unknown stays
    omitted — never default. Orthogonal to loop-controller ``error_class``.

    ``product_face``: optional user-channel copy that must stay even when a stable
    ``failure_code`` is set (path-missing sibling listings). Default: lift ``error``
    only when ``user_face`` and there is no code.

    User face (``tool_use_end.failure``):
    - ``user_face=True`` (default) → product Chinese in ``error`` also fills
      ``failure_message`` (dynamic paths with filenames, etc.).
    - ``user_face=False`` + stable ``metadata["code"]`` / ``failure_code`` → curated
      table only (no lift of ``str(exc)`` / internal tokens).
    """
    meta = dict(metadata or {})
    code = failure_code
    if isinstance(code, str) and code.strip():
        meta.setdefault("code", code.strip())
    elif code is None:
        raw = meta.get("code")
        code = raw.strip() if isinstance(raw, str) and raw.strip() else None
    retry = normalize_cross_turn_retry(cross_turn_retry)
    if retry:
        meta[CROSS_TURN_RETRY_KEY] = retry
    face_message: str | None
    if product_face is not None:
        face_message = product_face
    elif user_face and not code:
        face_message = error
    else:
        face_message = None
    return ToolResult(
        tool_call_id="",
        success=False,
        output="",
        error=error,
        duration_ms=int((time.monotonic() - start) * 1000),
        contract_failure=contract_failure,
        metadata=meta,
        failure_message=face_message,
        failure_code=code,
    )


def looks_like_http_url(path: str) -> bool:
    """True when ``path`` is an http(s) URL, not a workspace-relative file path."""
    lowered = (path or "").strip().casefold()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _url_not_workspace_path_error(path: str, start: float) -> ToolResult:
    """file_read was given a public URL — reroute to read_url; do not treat as a path."""
    clipped = path if len(path) <= 200 else path[:199] + "…"
    return _error(
        (
            f"`{clipped}` 是 http(s) 网页地址，不是工作区相对路径。"
            "请改用 read_url(url=该地址) 深读正文；工作区文件才用 file_read。"
            "不要把 URL 改写成路径再重试本工具。"
        ),
        start,
        contract_failure=True,
        failure_code="url_not_workspace_path",
        user_face=False,
        cross_turn_retry=CrossTurnRetry.FUTILE,
    )


def _file_too_large_error(path: str, start: float, *, size: int | None = None) -> ToolResult:
    """Capacity contract: oversized **text** whole-file read (not Office extract)."""
    max_mib = WORKSPACE_READ_MAX_BYTES // (1024 * 1024)
    size_bit = f"（{size}字节）" if size is not None else ""
    return _error(
        (
            f"`{path}` {FILE_TOO_LARGE_DETAIL}{size_bit}（上限 {max_mib} MiB）。"
            "不要原样重试整文件读取。"
        ),
        start,
        contract_failure=True,
        failure_code="too_large",
        user_face=False,
        metadata={"capacity_contract": "bytes"},
    )


def _liveness_workspace_error(detail: str, start: float) -> ToolResult:
    """Sticky channel-dead: family retire + steer (after consecutive settle timeouts)."""
    return _error(
        channel_dead_error_message(detail),
        start,
        metadata=channel_dead_retire_metadata(),
        # Model-facing text may embed channel detail; user face uses curated code.
        user_face=False,
    )


def _op_liveness_timeout_error(detail: str, start: float) -> ToolResult:
    """Single-op settle timeout: fail this call only (no family sticky / notice)."""
    return _error(
        (
            f"本地工作区通道操作超时（活性挂起）：{detail}。"
            "请缩小范围或换策略后重试；禁止原样重试同一操作。"
        ),
        start,
        metadata=op_liveness_timeout_metadata(),
        user_face=False,
        cross_turn_retry=CrossTurnRetry.NOT_FUTILE,
    )


def _maybe_channel_dead_error(exc: WorkspaceError, start: float) -> ToolResult | None:
    """Map channel liveness failures: sticky-dead vs single-op settle timeout."""
    detail = str(exc)
    if is_channel_dead_detail(detail):
        return _liveness_workspace_error(detail, start)
    if is_liveness_timeout_detail(detail):
        return _op_liveness_timeout_error(detail, start)
    return None


def _map_workspace_read_error(exc: WorkspaceError, *, path: str, start: float) -> ToolResult:
    """Map backend read failures to capacity vs liveness vs generic I/O."""
    from agentcore.workspace.file_kind import parse_too_large_size

    detail = str(exc)
    if is_file_too_large_detail(detail):
        return _file_too_large_error(path, start, size=parse_too_large_size(detail))
    dead = _maybe_channel_dead_error(exc, start)
    if dead is not None:
        return dead
    return _error(f"读取文件失败：{exc}", start, user_face=False)


def _file_read_same_window_hit(path: str, *, max_reads: int, start: float) -> ToolResult:
    """Same-path cap: success + short pointer (no full dump, not a hard reject).

    Keep the stable cues ``已多次读取`` / ``勿再读`` so desktop+mobile guidance
    detectors still match (legacy error tapes and new success hits).
    """
    output = (
        f"已多次读取 `{path}`（本 run 上限 {max_reads} 次）。"
        "请求范围仍在对话投影窗中，本次不重复灌入全文。"
        "请直接使用已有正文，勿再读全文；未读过的行号请改 offset/limit；"
        "仅当该正文已被清理、对话中不再有全文时才可再读。"
        "可换其它文件，或基于已有正文落盘 / handoff。"
    )
    return ToolResult(
        tool_call_id="",
        success=True,
        output=output,
        duration_ms=int((time.monotonic() - start) * 1000),
        output_limit=max(len(output), ToolResult._MAX_OUTPUT_LEN),
    )


def _path_missing_error(error: str, start: float) -> ToolResult:
    """Path / entry does not exist — fix by changing args; skip breaker tally.

    Platform bugs (missing attachment in a delegated workspace) and model path
    mistakes share this marker: neither should disable ``file_read`` / mutate tools.
    Same-path thrash is constrained elsewhere (validation fingerprint streak /
    same-path ``file_read`` cheap-hit), not by burning the run-scoped tool fuse.
    """
    return _error(
        error,
        start,
        contract_failure=True,
        failure_code="not_found",
        product_face=error,
    )


# Backend ``OutsideWorkspace`` is reused for two different facts:
# 1) path is not inside any known root (traversal) — message is the path;
# 2) path *is* inside a mounted root (``external/<alias>/`` / ``shared/…``)
#    but the op is not authorized — message is a policy sentence.
# File tools must not rewrite (2) into 「超出了工作区范围」: that root is legal;
# the model then copies the in-project example and writes into a forbidden tree.
# Markers are the module ``*_MSG`` constants (imported, never re-spelled). A
# prefix / 「（拒绝」 scrape would go silent the moment either end retouched
# copy — that is how the model last got an inaccurate reason.
# Desktop ``sessionRoot.ts`` mirrors the three external-grant sentences;
# ``tests/test_external_op_parity.py`` ratchets that mirror.
_MOUNT_OP_DENIED_MARKERS: tuple[str, ...] = (
    _external_mounts._READONLY_MSG,
    _external_mounts._ORGANIZE_DENY_MSG,
    _external_mounts._PERMANENT_EXTERNAL_MSG,
    _external_mounts._CROSS_COPY_MSG,
    _external_mounts._CROSS_MOUNT_COPY_MSG,
    _external_mounts._CROSS_MOVE_MSG,
    _external_mounts._CROSS_MOUNT_MOVE_MSG,
    _shared_mounts._READONLY_MSG,
    _shared_mounts._REVOKED_MSG,
)


def _is_mount_op_denied_reason(text: str) -> bool:
    """True when ``OutsideWorkspace`` carries a mount-policy sentence, not a path."""
    t = (text or "").strip()
    return bool(t) and any(m in t for m in _MOUNT_OP_DENIED_MARKERS)


def _outside_workspace_msg(
    path: str,
    *,
    location: str | None = None,
    reason: str | None = None,
) -> str:
    """Actionable OutsideWorkspace text.

    Two cases (do not collapse them):

    - Mounted root, op not authorized: surface the backend sentence as-is.
      ``external/<alias>/`` is a legal root — never say the path is out of
      range, and never offer an in-project relative-path example.
    - True out-of-root: keep the existing range + relative-path hint.

    Path contract lives in ``normalize_workspace_path`` / ``resolve_safe_path``.
    On cloud (``location=server``), the out-of-root branch redirects to
    Composer import / Git — do not teach bind/open_local as the product path.

    ``reason`` is ``str(OutsideWorkspace)``. Callers that historically stuffed
    ``str(e)`` into ``path`` (move/copy) are also recognized here so the
    policy sentence is not interpolated into 「路径 '…' 超出了工作区范围」.
    """
    detail = (reason or "").strip()
    if _is_mount_op_denied_reason(detail):
        return detail
    if _is_mount_op_denied_reason(path):
        return path.strip()

    relative_fix = (
        "请使用工作区相对路径（如 AgentCore/文档/research/report.md；"
        "`.` 或裸 `/` 表示整仓）；勿使用工作区外的绝对路径（如 /etc、盘符）。"
    )
    if location == "server":
        return (
            f"路径 '{path}' 超出了工作区范围。"
            "若要把该本机目录进产品工作区：**推荐**引导 Composer「导入到云 / 连接 Git」；"
            "仅当用户明确要求新建云文件夹时才用 create_folder"
            "（禁止为过写盘闸而建；裸聊写盘缺桌由运行时自动建云桌）；"
            "本机传统 open_local_project / register_local_project / "
            "bind_local_folder 合法非默认（≠离线）。"
            f"若本意是工作区内文件：{relative_fix}"
        )
    return f"路径 '{path}' 超出了工作区范围。{relative_fix}"


def _outside_workspace_error(
    path: str,
    start: float,
    *,
    location: str | None = None,
    reason: str | None = None,
) -> ToolResult:
    """OutsideWorkspace / mount-policy deny — same action next turn is futile."""
    return _error(
        _outside_workspace_msg(path, location=location, reason=reason),
        start,
        cross_turn_retry=CrossTurnRetry.FUTILE,
    )

