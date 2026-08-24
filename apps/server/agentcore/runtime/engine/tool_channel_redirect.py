"""Wrong-tool-channel steer — a process outcome, not a fault.

The model called a tool that is the wrong *channel* for the job (``code_execute``
as grep, ``read_url`` as ``file_read``, …). Runtime refused to execute and told
the model which tool to use instead. User files are untouched.

Wire ``tool_use_end.status`` is ``redirect`` (not ``error``). The LLM transcript
still carries a failed tool result so the model switches. Closed set — a new
channel-mismatch code must be added here or it stays a user-facing fault.
"""

from __future__ import annotations

from typing import Any, Literal

ToolWireStatus = Literal["success", "error", "redirect"]

CHANNEL_REDIRECT_CODES: frozenset[str] = frozenset(
    {
        "source_grep_redirect",
        "source_dump_redirect",
        "project_verify_redirect",
        "long_running_redirect",
        "not_a_web_url",
        "url_not_workspace_path",
        "loopback_host",
    }
)


def is_channel_redirect_code(code: str | None) -> bool:
    return (code or "").strip() in CHANNEL_REDIRECT_CODES


def tool_wire_status(*, success: bool, failure_code: str | None) -> ToolWireStatus:
    if success:
        return "success"
    if is_channel_redirect_code(failure_code):
        return "redirect"
    return "error"


def failure_code_from_end_payload(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    failure = payload.get("failure")
    if isinstance(failure, dict):
        code = failure.get("code")
        if isinstance(code, str) and code.strip():
            return code.strip()
    nested = payload.get("code")
    if isinstance(nested, str) and nested.strip():
        return nested.strip()
    return None


def normalize_tool_step_status(
    status: str | None, failure_code: str | None
) -> Literal["running", "success", "error", "redirect"]:
    """Journal compat: pre-redirect events stored channel steers as ``status=error``."""
    raw = (status or "success").strip() or "success"
    if raw == "running":
        return "running"
    if raw == "redirect":
        return "redirect"
    if raw == "error" and is_channel_redirect_code(failure_code):
        return "redirect"
    if raw == "error":
        return "error"
    return "success"


def process_tool_status_from_end(payload: dict[str, Any] | None) -> str:
    """Resolve ``ProcessStep.status`` from a ``tool_use_end`` payload."""
    if not isinstance(payload, dict):
        return "success"
    raw = payload.get("status")
    return normalize_tool_step_status(
        raw if isinstance(raw, str) else None,
        failure_code_from_end_payload(payload),
    )
