"""Built-in tool: code_diagnostics — language-service inner verify loop (TS/JS).

Complements outer-loop ``run``（验收员 / typecheck·build·test）:

- **Inner** ``code_diagnostics`` — desktop language service for landed / named paths
- **Outer** ``run`` — project typecheck / build / test exit 0

Cloud desks return ``unavailable`` honestly; sidecar / 过桥 route to the
desktop language service. Never fakes a full ``tsc`` as the inner loop.
Approval NEVER (read-only probe).
"""

from __future__ import annotations

import time
from typing import Any

from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.tools.builtin.write_diagnostics import (
    JS_TS_SUFFIXES,
    diagnostics_display,
    is_js_ts_path,
)
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)
from agentcore.workspace.limits import (
    channel_dead_error_message,
    channel_dead_retire_metadata,
    is_liveness_timeout_detail,
    is_presence_disconnected_detail,
    is_workspace_reconnect_detail,
    op_liveness_timeout_metadata,
)
from agentcore.workspace.protocol import WorkspaceError

_OUTPUT_LIMIT = 12000


def _paths_from_landed(context: ToolContext) -> list[str]:
    kinds = getattr(context, "landed_artifact_kinds", None) or {}
    out: list[str] = []
    for path in kinds:
        p = str(path or "").strip()
        if p and is_js_ts_path(p):
            out.append(p)
    return out


def _normalize_paths(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        p = str(item or "").strip().replace("\\", "/")
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _format_full_output(payload: dict[str, Any], *, paths: list[str]) -> str:
    status = str(payload.get("status") or "")
    reason = str(payload.get("reason") or "").strip()
    raw = payload.get("diagnostics") or []
    items = [d for d in raw if isinstance(d, dict)] if isinstance(raw, list) else []

    if status == "unavailable":
        detail = reason or "语言服务不可用"
        return (
            f"内环诊断不可用：{detail}\n"
            "说明：无语言服务通道时诚实降级；"
            "验收请用 run（typecheck/build/test）。"
            + (f"\n请求路径：{', '.join(paths)}" if paths else "")
        )

    errors = [
        d for d in items if str(d.get("severity") or "").lower() in ("error", "err")
    ]
    warnings = [
        d
        for d in items
        if str(d.get("severity") or "").lower() in ("warning", "warn")
    ]
    lines = [
        "## 内环诊断（code_diagnostics）",
        "",
        "- 状态：ok",
        f"- 路径数：{len(paths)}",
        f"- error：{len(errors)} · warning：{len(warnings)}",
    ]
    if not items:
        lines.append("")
        lines.append("无诊断项（语言服务未报错/警告，或路径无 TS/JS 诊断）。")
        return "\n".join(lines)

    by_file: dict[str, list[dict[str, Any]]] = {}
    for d in items:
        p = str(d.get("path") or "?")
        by_file.setdefault(p, []).append(d)

    for file_path, diags in by_file.items():
        lines.extend(["", f"### `{file_path}`"])
        for d in diags:
            sev = str(d.get("severity") or "info")
            line = int(d.get("line") or 0)
            col = int(d.get("column") or 0)
            msg = str(d.get("message") or "").strip() or "(no message)"
            code = str(d.get("code") or "").strip()
            loc = f"{line}:{col}" if line else "?"
            code_bit = f" [{code}]" if code else ""
            lines.append(f"- {loc} {sev}{code_bit}: {msg}")
    return "\n".join(lines)


class CodeDiagnosticsTool:
    """Language-service diagnostics for TS/JS (inner verify loop)."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        file_products=FileProductsContract.READ_ONLY,
    )

    @property
    def schema(self) -> ToolSchema:
        suffixes = "/".join(JS_TS_SUFFIXES)
        return ToolSchema(
            name="code_diagnostics",
            description=(
                "内环语言服务诊断（本地 TS/JS）：对指定路径或本 run 已落盘的 "
                f"{suffixes} 文件拉取 error/warning。"
                "写盘回执常已附带短诊断；主动复查用本工具。验收用 run。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "可选：工作区相对路径列表。省略或空则用本 run "
                            f"landed_artifact_kinds 中的 {suffixes} 文件。"
                        ),
                    },
                },
                "required": [],
            },
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.NEVER,
            timeout_seconds=25,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        paths = _normalize_paths(arguments.get("paths"))
        if not paths:
            paths = _paths_from_landed(context)
        if not paths:
            return ToolResult(
                tool_call_id="",
                success=True,
                output=(
                    "未指定 paths，且本 run 尚无已落盘的 TS/JS 文件。"
                    "请传入 paths，或先 file_write / str_replace 落盘后再查。"
                ),
                duration_ms=int((time.monotonic() - start) * 1000),
                display={
                    "kind": "code_diagnostics",
                    "status": "ok",
                    "diagnostics": [],
                },
                output_limit=_OUTPUT_LIMIT,
            )

        diag_fn = getattr(context.backend, "diagnostics", None)
        if diag_fn is None:
            payload = {
                "status": "unavailable",
                "reason": "工作区未实现 diagnostics",
                "diagnostics": [],
            }
            return ToolResult(
                tool_call_id="",
                success=True,
                output=_format_full_output(payload, paths=paths),
                duration_ms=int((time.monotonic() - start) * 1000),
                display=diagnostics_display(payload),
                output_limit=_OUTPUT_LIMIT,
            )

        try:
            payload = await diag_fn(paths)
        except WorkspaceError as e:
            detail = str(e)
            if is_presence_disconnected_detail(detail):
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=channel_dead_error_message("code_diagnostics"),
                    duration_ms=int((time.monotonic() - start) * 1000),
                    metadata=channel_dead_retire_metadata(),
                    contract_failure=True,
                )
            if is_workspace_reconnect_detail(detail):
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=detail,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            if is_liveness_timeout_detail(detail):
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=(
                        f"本地工作区通道操作超时（活性挂起）：{detail}。"
                        "请缩小范围或换策略后重试；禁止原样重试同一操作。"
                    ),
                    duration_ms=int((time.monotonic() - start) * 1000),
                    metadata=op_liveness_timeout_metadata(),
                )
            payload = {
                "status": "unavailable",
                "reason": detail or type(e).__name__,
                "diagnostics": [],
            }
        except Exception as e:  # noqa: BLE001 — honest degrade
            payload = {
                "status": "unavailable",
                "reason": str(e).strip() or type(e).__name__,
                "diagnostics": [],
            }

        if not isinstance(payload, dict):
            payload = {
                "status": "unavailable",
                "reason": "malformed diagnostics result",
                "diagnostics": [],
            }

        return ToolResult(
            tool_call_id="",
            success=True,
            output=_format_full_output(payload, paths=paths),
            duration_ms=int((time.monotonic() - start) * 1000),
            display=diagnostics_display(payload),
            metadata={"status": payload.get("status"), "path_count": len(paths)},
            output_limit=_OUTPUT_LIMIT,
        )
