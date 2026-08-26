"""Read-only visible failure text for conversation exits.

After settle stops dual-writing error into ``message.content``, pure-failure
assistant rows keep empty content and put the cause on usage / journal
(``turn_end.error``). Export, log transcripts, search snippets, and compaction
fold text must still surface a readable sentence from that metadata.

History for the LLM window is different: it folds empty failures into a short
system note (see ``history.py``) and must never inject error prose as ordinary
assistant content.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import UNCLASSIFIED_EXCEPTION_USER_MESSAGE
from agentcore.runtime.journal import KIND_TURN_END

_DEFAULT_FAILURE_TEXT = UNCLASSIFIED_EXCEPTION_USER_MESSAGE

# Error codes → short zh category labels (history notes + export fallback).
FAILURE_CATEGORY_LABELS: dict[str, str] = {
    ErrorCode.LLM_TIMEOUT: "连接超时",
    ErrorCode.LLM_KEY_INVALID: "鉴权失败（API Key 无效或无权限）",
    ErrorCode.LLM_KEY_REQUIRED: "未配置 API Key",
    ErrorCode.LLM_INSUFFICIENT_BALANCE: "上游账户余额不足",
    ErrorCode.LLM_RATE_LIMIT: "上游限流",
    ErrorCode.LLM_ERROR: "模型调用失败",
    ErrorCode.PIPELINE_ERROR: "管线执行失败",
    ErrorCode.QUOTA_EXCEEDED: "额度已用尽",
    ErrorCode.KEY_STORAGE_UNAVAILABLE: "密钥存储不可用",
}


def usage_of(msg: Any) -> dict:
    usage = getattr(msg, "usage", None)
    return usage if isinstance(usage, dict) else {}


def is_failed_empty_assistant(msg: Any) -> bool:
    """True when an assistant row is a soft/hard failure with no deliverable text."""
    if getattr(msg, "role", None) != "assistant":
        return False
    if (getattr(msg, "content", None) or "").strip():
        return False
    usage = usage_of(msg)
    if usage.get("status") == "failed":
        return True
    finish = usage.get("finish_reason")
    return finish in ("error", "degraded")


def failure_category_label(msg: Any) -> str:
    usage = usage_of(msg)
    code = usage.get("error_code") or ""
    if isinstance(code, str) and code in FAILURE_CATEGORY_LABELS:
        return FAILURE_CATEGORY_LABELS[code]
    finish = usage.get("finish_reason")
    if finish == "degraded":
        return "模型空响应（降级收尾）"
    return "本轮未能完成"


def _message_from_error_value(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, Mapping):
        msg = value.get("message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
    return None


def error_message_from_usage(usage: Mapping[str, Any] | None) -> str | None:
    """Prefer explicit usage error fields written at settle (when present)."""
    if not usage:
        return None
    for key in ("error_message", "error"):
        found = _message_from_error_value(usage.get(key))
        if found:
            return found
    return None


def error_message_from_journal(entries: Sequence[Mapping[str, Any]] | None) -> str | None:
    """Latest ``turn_end.error.message`` from durable journal facts."""
    if not entries:
        return None
    for entry in reversed(list(entries)):
        if entry.get("kind") != KIND_TURN_END:
            continue
        payload = entry.get("payload") or {}
        if not isinstance(payload, Mapping):
            return None
        return _message_from_error_value(payload.get("error"))
    return None


def visible_failure_text(
    msg: Any,
    *,
    journal_entries: Sequence[Mapping[str, Any]] | None = None,
) -> str | None:
    """Human-visible failure sentence for a pure-failure assistant row.

    Returns ``None`` when the row is not an empty failed assistant (caller should
    use content or skip). Prefer structured error message, then category label,
    then a short default — never invent long assistant prose.
    """
    if not is_failed_empty_assistant(msg):
        return None
    usage = usage_of(msg)
    for candidate in (
        error_message_from_usage(usage),
        error_message_from_journal(journal_entries),
    ):
        if candidate:
            return candidate
    label = failure_category_label(msg)
    return label or _DEFAULT_FAILURE_TEXT


def export_visible_text(
    msg: Any,
    *,
    journal_entries: Sequence[Mapping[str, Any]] | None = None,
) -> str | None:
    """Body for export / log / snippet: content wins; else failure visible text.

    Returns ``None`` when there is nothing readable (empty non-failure row).
    """
    body = (getattr(msg, "content", None) or "").strip()
    if body:
        return body
    return visible_failure_text(msg, journal_entries=journal_entries)
