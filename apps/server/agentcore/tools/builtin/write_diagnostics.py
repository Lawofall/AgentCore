"""Best-effort language-service diagnostics after TS/JS writes (inner verify loop).

On in-process desks, runtime attaches a short diagnostics block to successful
``file_write`` / ``str_replace`` / ``file_append`` receipts. Over a desktop
fulfill hop the write returns without waiting — ``code_diagnostics`` stays the
explicit inner-loop tool. Failures and ``unavailable`` never flip a write success
into failure.
"""

from __future__ import annotations

from typing import Any

from agentcore.tools.protocol import ToolContext, ToolResult
from agentcore.workspace.presence import diagnostics_rides_fulfill_channel

# First-ship local TS/JS surface (inner loop). Keep in sync with code_diagnostics.
JS_TS_SUFFIXES: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

_SEVERITY_ORDER = {"error": 0, "warning": 1, "information": 2, "hint": 3}


def is_js_ts_path(path: str) -> bool:
    """True when ``path`` looks like a first-ship TS/JS source file."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return any(name.endswith(suffix) for suffix in JS_TS_SUFFIXES)


def format_diagnostics_block(payload: dict[str, Any], *, path_hint: str | None = None) -> str:
    """Model-facing diagnostics appendix (short; errors first)."""
    status = str(payload.get("status") or "")
    reason = str(payload.get("reason") or "").strip()
    raw = payload.get("diagnostics") or []
    items = [d for d in raw if isinstance(d, dict)] if isinstance(raw, list) else []

    if status == "unavailable":
        detail = reason or "语言服务不可用"
        return f"\n内环诊断不可用：{detail}（验收请用 run）"

    errors = [
        d
        for d in items
        if str(d.get("severity") or "").lower() in ("error", "err")
    ]
    # Prefer errors; if none, show a thin clean receipt.
    show = errors if errors else []
    header_path = path_hint or (show[0].get("path") if show else None)
    if not show:
        label = f"`{header_path}` " if header_path else ""
        return f"\n内环诊断：{label}无 error"

    # Cap so write receipts stay readable.
    cap = 12
    lines = ["", "内环诊断（code_diagnostics）："]
    by_file: dict[str, list[dict[str, Any]]] = {}
    for d in show:
        p = str(d.get("path") or header_path or "?")
        by_file.setdefault(p, []).append(d)
    shown = 0
    for file_path, diags in by_file.items():
        lines.append(f"- `{file_path}`")
        diags_sorted = sorted(
            diags,
            key=lambda d: (
                _SEVERITY_ORDER.get(str(d.get("severity") or "").lower(), 9),
                int(d.get("line") or 0),
                int(d.get("column") or 0),
            ),
        )
        for d in diags_sorted:
            if shown >= cap:
                remaining = len(show) - shown
                lines.append(
                    f"  …另有 {remaining} 条 error 省略；可再调 code_diagnostics 看全部"
                )
                return "\n".join(lines)
            line = int(d.get("line") or 0)
            col = int(d.get("column") or 0)
            msg = str(d.get("message") or "").strip() or "(no message)"
            code = str(d.get("code") or "").strip()
            loc = f"{line}:{col}" if line else "?"
            code_bit = f" [{code}]" if code else ""
            lines.append(f"  · {loc} error{code_bit}: {msg}")
            shown += 1
    return "\n".join(lines)


def diagnostics_display(payload: dict[str, Any]) -> dict[str, Any]:
    """Client display payload for ``kind=code_diagnostics``."""
    status = str(payload.get("status") or "unavailable")
    raw = payload.get("diagnostics") or []
    diagnostics = [d for d in raw if isinstance(d, dict)] if isinstance(raw, list) else []
    out: dict[str, Any] = {
        "kind": "code_diagnostics",
        "status": status if status in ("ok", "unavailable") else "unavailable",
        "diagnostics": diagnostics,
    }
    reason = payload.get("reason")
    if isinstance(reason, str) and reason.strip():
        out["reason"] = reason.strip()
    return out


async def attach_write_diagnostics(
    result: ToolResult,
    *,
    context: ToolContext,
    path: str,
) -> ToolResult:
    """Append inner-loop diagnostics to a successful write receipt (best-effort)."""
    if not result.success or not is_js_ts_path(path):
        return result
    if diagnostics_rides_fulfill_channel(getattr(context, "backend", None)):
        # Write already landed. Inner-loop LS is a separate capability over the
        # fulfill hop — do not wait on it here. Explicit ``code_diagnostics`` may.
        return result

    diag_fn = getattr(context.backend, "diagnostics", None)
    if diag_fn is None:
        return result

    try:
        payload = await diag_fn([path])
        if not isinstance(payload, dict):
            result.output = (result.output or "") + "\n内环诊断不可用：返回格式异常"
            return result
        result.output = (result.output or "") + format_diagnostics_block(
            payload, path_hint=path
        )
        display = diagnostics_display(payload)
        if result.display:
            merged = dict(result.display)
            merged.update(display)
            result.display = merged
        else:
            result.display = display
    except Exception as e:  # noqa: BLE001 — never fail a successful write
        detail = str(e).strip() or type(e).__name__
        # Keep one line; trim noise.
        if len(detail) > 120:
            detail = detail[:117] + "…"
        result.output = (result.output or "") + f"\n内环诊断不可用：{detail}"
    return result
