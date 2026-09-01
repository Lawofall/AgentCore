"""Collapse old write-tool arguments in the model-facing window (handoff 缓存崩塌).

After a worker ``file_write`` / ``file_append`` / ``str_replace`` lands, the assistant
message still carries the FULL body inside ``tool_calls[].function.arguments``. Older
rounds re-pay that body as cache_miss (case: handoff round ~28k in / ~27k miss).

This projection — applied at request-assembly time only, like ``tool_clear`` — keeps the
**original write tool name**. Writes that have fallen out of the near window reduce args
to ``{"path": …}`` and append a size / structure digest to that call's **tool result**.
The latest ``keep_recent`` assistant rounds that contain a completed bulky write keep
full ``content`` / ``old_string`` / ``new_string`` so the next thought can chain an edit.
Canonical ``messages`` / journal keep the full args; resume rebuilds then re-applies.

定案：掉出近端窗的写，参数槽只留 path（模型不能把摘要当正文回灌）。近端保留全文。
历代投影（``content:"[已清理]"`` 假稿纸 → ``_landed_summary`` 模板 → 合成名
``_write_landed`` → ``{"status": "landed", …}``）都被模型当写参回灌过——复用上一次
调用的 arguments 是 tool-calling 的正常语义，改措辞治不住。裸 ``path`` 显然不是一份
可提交的载荷；真被回灌也只撞普通的「缺必填 content」。结果本就属于 tool result，
摘要因此挪到那里。

遗留形态（stub / ``_landed_summary`` / landed-status / 仿调 ``_write_landed``）的结构化
硬拒**保留为安全网**，见 ``cleared_write_stub_rejection`` / ``landed_status_name_rejection``；
待线上连续数窗零命中再退休。
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction

WRITE_ARG_TOOLS = frozenset({"file_write", "file_append", "str_replace"})

# Legacy synthetic name formerly used as projected ``function.name``. Kept only so
# residual imitation can be early-rejected; new projection never emits this name.
LANDED_STATUS_TOOL = "_write_landed"

# Argument keys that hold the bulky body for each write tool.
_BODY_KEYS = ("content", "new_str", "new_string", "replacement")

# Cap digest size (~几百 token): keep contract signal, not a second full body.
_STRUCTURE_MAX_CHARS = 1200
_STRUCTURE_MAX_ITEMS = 80

# Legacy projection / stub markers (rejection兜底 only; new projection does not emit these).
_LANDED_SUMMARY_KEY = "_landed_summary"
_LEGACY_CLEARED_KEY = "_cleared"
_STUB_BODY_MARKERS = frozenset({"[已清理]", "[已清理·须重填]"})

# Tail shared by every rejection this module emits. The loop controller keys its
# one-strike path-stop off this marker rather than per-branch wording, so rephrasing
# a branch cannot silently orphan the early stop.
_REJECTION_MARKER = "不能写入磁盘"

_HTML_ID_RE = re.compile(r"""\bid\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_HTML_CLASS_RE = re.compile(r"""\bclass\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_CSS_SELECTOR_RE = re.compile(
    r"(?m)^\s*([.#]?[A-Za-z_][\w-]*(?:\s*[.#][A-Za-z_][\w-]*)*)\s*\{"
)
_MD_HEADING_RE = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*$")
_JS_EXPORT_RE = re.compile(
    r"(?m)^\s*(?:export\s+(?:default\s+)?(?:async\s+)?)?"
    r"(?:function\*?|class|const|let|var)\s+([A-Za-z_$][\w$]*)"
)


def _dedupe_preserve(items: list[str], *, limit: int = _STRUCTURE_MAX_ITEMS) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _clip(text: str, *, max_chars: int = _STRUCTURE_MAX_CHARS) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _sniff_kind(path: str, content: str) -> str:
    ext = Path(path or "").suffix.lower()
    head = content.lstrip()[:200].lower()
    if ext in {".html", ".htm", ".xhtml"} or head.startswith(
        ("<!doctype html", "<html")
    ):
        return "html"
    if ext == ".css" or (ext == "" and "{" in content and re.search(r"[.#][\w-]+\s*\{", content)):
        return "css"
    if ext in {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"}:
        return "js"
    if ext in {".md", ".markdown"}:
        return "md"
    if ext == ".json" or head[:1] in {"{", "["}:
        return "json"
    if "<" in content and ("class=" in head or "id=" in head):
        return "html"
    return "text"


def _summarize_html(content: str) -> str | None:
    ids = _dedupe_preserve(_HTML_ID_RE.findall(content))
    classes: list[str] = []
    for group in _HTML_CLASS_RE.findall(content):
        classes.extend(group.split())
    classes = _dedupe_preserve(classes)
    if not ids and not classes:
        return None
    parts: list[str] = ["HTML 结构摘要"]
    if ids:
        parts.append("ids=[" + ", ".join(ids) + "]")
    if classes:
        parts.append("classes=[" + ", ".join(classes) + "]")
    return _clip("; ".join(parts))


def _summarize_css(content: str) -> str | None:
    selectors = _dedupe_preserve(_CSS_SELECTOR_RE.findall(content))
    if not selectors:
        return None
    return _clip("CSS 选择器摘要: [" + ", ".join(selectors) + "]")


def _summarize_md(content: str) -> str | None:
    headings: list[str] = []
    for marks, title in _MD_HEADING_RE.findall(content):
        headings.append(f"{marks} {title.strip()}")
    headings = _dedupe_preserve(headings)
    if not headings:
        return None
    return _clip("Markdown 标题摘要: [" + ", ".join(headings) + "]")


def _summarize_js(content: str) -> str | None:
    names = _dedupe_preserve(_JS_EXPORT_RE.findall(content))
    if not names:
        return None
    return _clip("JS/TS 符号摘要: [" + ", ".join(names) + "]")


def _summarize_json(content: str) -> str | None:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if isinstance(data, dict):
        keys = _dedupe_preserve([str(k) for k in data])
        if not keys:
            return None
        return _clip("JSON 顶层键: [" + ", ".join(keys) + "]")
    if isinstance(data, list):
        return _clip(f"JSON 数组摘要: len={len(data)}")
    return None


def structural_write_summary(path: str, content: str) -> str | None:
    """Compact structural digest of a write body (class/id / selectors / headings…).

    Returns None when no useful structure is found. Size-capped for context budget.
    """
    if not isinstance(content, str) or not content.strip():
        return None
    kind = _sniff_kind(path, content)
    if kind == "html":
        return _summarize_html(content)
    if kind == "css":
        return _summarize_css(content)
    if kind == "md":
        return _summarize_md(content)
    if kind == "js":
        return _summarize_js(content)
    if kind == "json":
        return _summarize_json(content)
    return None


def _body_text(data: dict) -> str:
    for key in _BODY_KEYS:
        val = data.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _is_projected_write_args(arguments: str) -> bool:
    """True when args are already a landed-status / legacy cleared projection.

    New projection keeps the original write tool name and emits ``status: landed``
    compact JSON (no body keys). Legacy forms used ``_landed_summary`` / ``_cleared``
    under a write name, or renamed the call to ``LANDED_STATUS_TOOL``.
    """
    if (
        f'"{_LANDED_SUMMARY_KEY}"' in arguments
        or f'"{_LEGACY_CLEARED_KEY}"' in arguments
    ):
        return True
    try:
        data = json.loads(arguments) if arguments else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("status") == "landed":
        return True
    # Identity-only projection: path survived, every body key is gone.
    return bool(data) and set(data) <= {"path", "file_path"}


def _path_and_body(arguments: str) -> tuple[str, str]:
    try:
        data = json.loads(arguments) if arguments else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    return str(data.get("path") or data.get("file_path") or ""), _body_text(data)


def write_args_identity(arguments: str) -> str:
    """Identity-only args for a landed write: keep ``path``, drop the body.

    Earlier projections left a rich status object in this slot (``_landed_summary``,
    then ``_write_landed``, then ``{"status": "landed", …}``) and each one got echoed
    back as a write submission — reusing the previous call's arguments is ordinary
    tool-calling behaviour, so no amount of rewording stopped it. A bare ``path`` is
    visibly not a payload; an echo of it fails plain "content is required" validation.
    The outcome moves to the tool result, where a result belongs.
    """
    path, _ = _path_and_body(arguments)
    return json.dumps({"path": path} if path else {}, ensure_ascii=False)


def landed_result_note(arguments: str, original_len: int) -> str | None:
    """Annotation appended to a landed write's tool result.

    Carries the two facts the write's own result line lacks: how large the body was,
    and its structural outline — both useful when the model later edits the file it
    can no longer see. Pure function of (path, body, len), so the projected window
    stays prefix-cache stable.
    """
    path, body = _path_and_body(arguments)
    if not body:
        return None
    structure = structural_write_summary(path, body)
    tail = f" · {structure}" if structure else ""
    return f"（已落盘 {original_len} 字符{tail}）"


def is_cleared_write_stub_args(arguments: dict[str, Any]) -> bool:
    """Same surface as ``cleared_write_stub_rejection`` — True for stub / landed summary.

    Narrow surface: projection keys (``_landed_summary`` / ``_cleared``), landed-status
    shape (``status == "landed"``), or body fields whose **entire** value equals a known
    placeholder (``[已清理]`` / ``[已清理·须重填]``). Does **not** scan free prose for
    the substring「已清理」.
    """
    if not isinstance(arguments, dict):
        return False
    if _LANDED_SUMMARY_KEY in arguments or _LEGACY_CLEARED_KEY in arguments:
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
        if isinstance(val, str) and val.strip() in _STUB_BODY_MARKERS:
            return True
    return False


def cleared_write_stub_rejection(arguments: dict[str, Any]) -> str | None:
    """Structured hard-reject when mutate args are a cleared stub / landed summary.

    Narrow surface: see ``is_cleared_write_stub_args``. Does **not** scan free prose
    for the substring「已清理」— normal short text must still write.
    """
    if not is_cleared_write_stub_args(arguments):
        return None
    path = arguments.get("path") or arguments.get("file_path")
    path_s = path.strip().replace("\\", "/") if isinstance(path, str) else ""
    path_bit = f"`{path_s}`" if path_s else "该文件"
    read_hint = (
        f'file_read(path="{path_s}")' if path_s else "file_read(该 path)"
    )
    if _LANDED_SUMMARY_KEY in arguments or _LEGACY_CLEARED_KEY in arguments:
        return (
            f"拒绝：参数是上下文窗口里的只读「已落盘摘要」/清理占位，{_REJECTION_MARKER}。"
            f"下一步（针对 {path_bit}）：① {read_hint} 取盘上真文；"
            "② 再 str_replace（优先）或 file_write，按真文填完整 "
            "content / old_string / new_string。"
            "禁止把 `_landed_summary`、清理条或摘要原样当写盘参数重发。"
        )
    if arguments.get("status") == "landed":
        return (
            "拒绝：参数是请求窗里的只读「已落盘」压缩状态，不是可提交写参，"
            f"{_REJECTION_MARKER}。"
            f"下一步（针对 {path_bit}）：① {read_hint} 取盘上真文；"
            "② 再 str_replace（优先）或 file_write，按真文填完整 "
            "content / old_string / new_string。"
            "禁止把 landed 状态原样当写盘参数重发。"
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
        if isinstance(val, str) and val.strip() in _STUB_BODY_MARKERS:
            return (
                "拒绝：正文参数仍是清理占位"
                f"（{val.strip()}），{_REJECTION_MARKER}。"
                f"下一步（针对 {path_bit}）：① {read_hint} 取盘上真文；"
                "② 再 str_replace（优先）或按真文重填后再写。禁止原样重发 stub。"
            )
    return None


def is_landed_echo_rejection(error_summary: str | None) -> bool:
    """True when ``error_summary`` is a rejection from ``cleared_write_stub_rejection``.

    Lives beside the rejection texts so the two cannot drift apart: the loop
    controller uses this to grant landed-echo attempts a one-strike path stop.
    """
    return _REJECTION_MARKER in (error_summary or "")


def landed_status_name_rejection(tool_name: str) -> str | None:
    """Early-reject when the model imitates legacy projected name ``_write_landed``.

    That name is a landed-status marker, not a registered tool. Must not fall through
    to generic allowlist_deny / not_found.
    """
    if (tool_name or "").strip() != LANDED_STATUS_TOOL:
        return None
    return (
        "拒绝：`_write_landed` 是请求窗里的「已落盘」压缩状态，不是可调用工具。"
        "勿仿调该名称。改稿：先 file_read 取盘上真文，再 str_replace（优先）或 file_write。"
    )


def _body_len(arguments: str) -> int:
    try:
        data = json.loads(arguments) if arguments else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return len(arguments or "")
    if not isinstance(data, dict):
        return len(arguments or "")
    total = 0
    for key in _BODY_KEYS:
        val = data.get(key)
        if isinstance(val, str):
            total += len(val)
    return total or len(arguments or "")


def _via_from_landed_args(arguments: str) -> str | None:
    """Restore original write tool name from compact landed-status args."""
    try:
        data = json.loads(arguments) if arguments else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    via = data.get("via")
    if isinstance(via, str) and via in WRITE_ARG_TOOLS:
        return via
    return None


def project_cleared_write_args(
    messages: list[LLMMessage],
    *,
    min_chars: int = 500,
    keep_recent: int = 1,
) -> list[LLMMessage]:
    """Collapse bulky write-tool args once their tool result is present.

    Keeps the original write ``function.name``. Completed bulky writes **older than**
    the last ``keep_recent`` assistant messages that contain such a write reduce args
    to ``path`` alone, and append a size / structure digest to that call's tool result.
    The near window keeps full bodies so the next thought can chain ``str_replace``.
    ``keep_recent=0`` collapses every completed bulky write (legacy). ``keep_recent<0``
    is a no-op. Also migrates leftover ``_write_landed`` names back to ``via``.
    Returns the same list when nothing qualifies. Prefix-cache safe for a given
    collapsed write: the projection is a pure function of (path, body, original_len).
    """
    if min_chars < 0 or keep_recent < 0:
        return messages

    # tool_call_id → (tool_name, arguments, assistant_msg_index, call_index)
    call_meta: dict[str, tuple[str, str, int, int]] = {}
    # Legacy bait names to rewrite back to via (args already compact).
    bait_ids: dict[str, tuple[str, int, int]] = {}  # id → (restore_name, mi, ci)
    for mi, message in enumerate(messages):
        if message.role != "assistant" or not message.tool_calls:
            continue
        for ci, call in enumerate(message.tool_calls):
            name = call.function.name
            args = call.function.arguments or ""
            if name == LANDED_STATUS_TOOL:
                restore = _via_from_landed_args(args) or "file_write"
                bait_ids[call.id] = (restore, mi, ci)
                continue
            if name not in WRITE_ARG_TOOLS:
                continue
            if _body_len(args) < min_chars:
                continue
            # Already projected (landed status / legacy summary under a write name).
            if _is_projected_write_args(args):
                continue
            call_meta[call.id] = (name, args, mi, ci)

    if not call_meta and not bait_ids:
        return messages

    # Only collapse writes that already have a tool result (completed round).
    completed_ids = {
        m.tool_call_id
        for m in messages
        if m.role == "tool" and m.tool_call_id and m.tool_call_id in call_meta
    }
    if not completed_ids and not bait_ids:
        return messages

    # Near window = last N assistant messages that contain a completed bulky write
    # (one ReAct round may issue several writes in parallel; all stay until the
    # next write-round). keep_recent=0 → collapse all. Do not use lst[-0:].
    write_round_indices: list[int] = []
    for mi, message in enumerate(messages):
        if message.role != "assistant" or not message.tool_calls:
            continue
        if any(call.id in completed_ids for call in message.tool_calls):
            write_round_indices.append(mi)
    keep_rounds: set[int] = (
        set()
        if keep_recent == 0
        else set(write_round_indices[-keep_recent:])
    )
    collapse_ids = {
        cid for cid in completed_ids if call_meta[cid][2] not in keep_rounds
    }
    if not collapse_ids and not bait_ids:
        return messages

    # The digest the collapsed body is worth keeping, keyed by the call it describes.
    notes: dict[str, str] = {}
    for cid in collapse_ids:
        _, args, _, _ = call_meta[cid]
        note = landed_result_note(args, _body_len(args))
        if note:
            notes[cid] = note

    # Rebuild only assistant messages that need a call rewritten.
    touch_indices = {call_meta[cid][2] for cid in collapse_ids} | {
        bait_ids[cid][1] for cid in bait_ids
    }
    projected: list[LLMMessage] = []
    for mi, message in enumerate(messages):
        if message.role == "tool" and message.tool_call_id in notes:
            base = message.content if isinstance(message.content, str) else ""
            projected.append(
                replace(message, content=f"{base}\n{notes[message.tool_call_id]}")
            )
            continue
        if mi not in touch_indices or not message.tool_calls:
            projected.append(message)
            continue
        new_calls: list[ToolCall] = []
        changed = False
        for call in message.tool_calls:
            if call.id in collapse_ids:
                name, args, _, _ = call_meta[call.id]
                new_calls.append(
                    ToolCall(
                        id=call.id,
                        function=ToolCallFunction(
                            name=name,
                            arguments=write_args_identity(args),
                        ),
                    )
                )
                changed = True
            elif call.id in bait_ids:
                restore_name, _, _ = bait_ids[call.id]
                new_calls.append(
                    ToolCall(
                        id=call.id,
                        function=ToolCallFunction(
                            name=restore_name,
                            arguments=call.function.arguments or "",
                        ),
                    )
                )
                changed = True
            else:
                new_calls.append(call)
        if changed:
            projected.append(
                LLMMessage(
                    role="assistant",
                    content=message.content,
                    tool_calls=new_calls,
                    reasoning_content=message.reasoning_content,
                )
            )
        else:
            projected.append(message)
    return projected
