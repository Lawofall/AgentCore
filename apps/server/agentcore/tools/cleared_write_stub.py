"""Hard-reject leftover write-window stubs (landed summary / cleared body).

Authority lives here so ``file_ops.mutate`` does not import ``runtime.engine``.
Window projection (collapse old write args) stays in ``engine.write_args_clear``;
that module imports the constants below so names cannot drift.

Legacy shapes (stub / ``_landed_summary`` / landed-status / bait name
``_write_landed``) are a safety net — retire after consecutive windows of zero hits.
"""

from __future__ import annotations

from typing import Any

# Legacy synthetic name formerly used as projected ``function.name``. Kept only so
# residual imitation can be early-rejected; new projection never emits this name.
LANDED_STATUS_TOOL = "_write_landed"

LANDED_SUMMARY_KEY = "_landed_summary"
LEGACY_CLEARED_KEY = "_cleared"
STUB_BODY_MARKERS = frozenset({"[已清理]", "[已清理·须重填]"})

# Tail shared by every rejection this module emits. The loop controller keys its
# one-strike path-stop off this marker rather than per-branch wording, so rephrasing
# a branch cannot silently orphan the early stop.
REJECTION_MARKER = "不能写入磁盘"

__all__ = [
    "LANDED_STATUS_TOOL",
    "LANDED_SUMMARY_KEY",
    "LEGACY_CLEARED_KEY",
    "REJECTION_MARKER",
    "STUB_BODY_MARKERS",
    "cleared_write_stub_rejection",
    "is_cleared_write_stub_args",
    "is_landed_echo_rejection",
    "landed_status_name_rejection",
]


def is_cleared_write_stub_args(arguments: dict[str, Any]) -> bool:
    """Same surface as ``cleared_write_stub_rejection`` — True for stub / landed summary.

    Narrow surface: projection keys (``_landed_summary`` / ``_cleared``), landed-status
    shape (``status == "landed"``), or body fields whose **entire** value equals a known
    placeholder (``[已清理]`` / ``[已清理·须重填]``). Does **not** scan free prose for
    the substring「已清理」.
    """
    if not isinstance(arguments, dict):
        return False
    if LANDED_SUMMARY_KEY in arguments or LEGACY_CLEARED_KEY in arguments:
        return True
    if arguments.get("status") == "landed":
        return True
    for key in (
        "content",
        "new_string",
        "new_str",
        "replacement",
        "old_string",
        "old_str",
    ):
        val = arguments.get(key)
        if isinstance(val, str) and val.strip() in STUB_BODY_MARKERS:
            return True
    return False


def cleared_write_stub_rejection(arguments: dict[str, Any]) -> str | None:
    """Structured hard-reject when mutate args are a cleared stub / landed summary.

    Narrow surface: see ``is_cleared_write_stub_args``. Does **not** scan free prose
    for the substring「已清理」— normal short text must still write.
    """
    if not is_cleared_write_stub_args(arguments):
        return None
    path = arguments.get("file_path")
    path_s = path.strip().replace("\\", "/") if isinstance(path, str) else ""
    path_bit = f"`{path_s}`" if path_s else "该文件"
    if LANDED_SUMMARY_KEY in arguments or LEGACY_CLEARED_KEY in arguments:
        return (
            f"拒绝：参数是上下文窗口里的只读「已落盘摘要」/清理占位，{REJECTION_MARKER}。"
            f"{path_bit} 的摘要不能当写参。禁止原样重发。"
        )
    if arguments.get("status") == "landed":
        return (
            "拒绝：参数是请求窗里的只读「已落盘」压缩状态，不是可提交写参，"
            f"{REJECTION_MARKER}。"
            f"{path_bit} 的摘要不能当写参。禁止原样重发。"
        )
    for key in (
        "content",
        "new_string",
        "new_str",
        "replacement",
        "old_string",
        "old_str",
    ):
        val = arguments.get(key)
        if isinstance(val, str) and val.strip() in STUB_BODY_MARKERS:
            return (
                "拒绝：正文参数仍是清理占位"
                f"（{val.strip()}），{REJECTION_MARKER}。"
                f"{path_bit} 的占位不能当写参。禁止原样重发。"
            )
    return None


def is_landed_echo_rejection(error_summary: str | None) -> bool:
    """True when ``error_summary`` is a rejection from ``cleared_write_stub_rejection``.

    Lives beside the rejection texts so the two cannot drift apart: the loop
    controller uses this to grant landed-echo attempts a one-strike path stop.
    """
    return REJECTION_MARKER in (error_summary or "")


def landed_status_name_rejection(tool_name: str) -> str | None:
    """Early-reject when the model imitates legacy projected name ``_write_landed``.

    That name is a landed-status marker, not a registered tool. Must not fall through
    to generic allowlist_deny / not_found.
    """
    if (tool_name or "").strip() != LANDED_STATUS_TOOL:
        return None
    return (
        "拒绝：`_write_landed` 是请求窗里的「已落盘」压缩状态，不是可调用工具。"
        "勿仿调该名称。"
    )
