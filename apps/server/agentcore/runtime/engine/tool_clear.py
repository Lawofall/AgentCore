"""回合内工具结果清理 (clear_tool_uses): collapse OLD tool results in the LLM view.

Within one ReAct run a long worker re-reads many files / pages and may also pile
``host`` / ``run`` stdout; every round re-sends those bodies. This module
collapses OLD results into a compact, stable pointer so the model still knows the
call happened without carrying the full body.

Two families, **independent** keep-windows (do not share ``keep_recent``; do not
put exec tools into ``investigation_tools`` — that set also drives idle-governance):

- Investigation (read-only, re-fetchable): pointer says use remaining
  verbatim; only invite a fresh call when that body is gone from context.
- Exec output (``host`` / ``run``): pointer forbids re-run-to-recover.

Design — a PURE projection applied at request-assembly time only (``build_request``),
NOT a mutation of the canonical window:

- The canonical ``messages`` list AND the durable Turn Journal keep the FULL output.
  Resume rebuilds the full window via ``window_from_journal`` then runs the same
  ``react_loop``, so the projection re-applies and lands byte-for-byte on the live
  window — no journal change, no resume divergence. (执行引擎架构设计 §三)
- The UI is unaffected: tool results render from ``tool_use_end`` / the journal, not
  the cleared LLM window, so the user still sees full output. Clearing is invisible.
- Prefix-cache safe: a cleared result's pointer is a pure function of its own
  (tool, args, original length), so once a result falls out of the keep-window its
  pointer bytes are FIXED across rounds and stay at the same position. The cleared
  region therefore remains cache-hittable; only the moving boundary near the tail
  (which re-caches every round anyway) misses.

Investigation clear = the run's ``investigation_tools`` (NEVER + FILESYSTEM /
SEARCH / RESEARCH) past ``keep_recent`` **assistant rounds** (not N calls) and
≥ ``min_chars``. A ReAct round that issued several ``file_read`` in parallel
keeps the whole batch until that round falls out — same unit as write-args
clear. Exec clear = ``EXEC_OUTPUT_CLEAR_TOOLS`` on a separate pass. Never
cleared: ``file_write`` / interaction cards / steers / assistant / system.

R1 (file_read): cleared ``file_read`` results become a structured stub (path /
content_cleared / reread=allowed) plus an optional deterministic digest (no LLM).
"""

from __future__ import annotations

import json

from agentcore.llm.provider.protocol import LLMMessage

# Argument keys, in priority order, that identify WHICH call was cleared.
_HINT_KEYS = (
    "path",
    "file_path",
    "query",
    "url",
    "pattern",
    "process_id",
    "command",
    "action",
    "subcommand",
)
_HINT_COMMAND_MAX = 80

# Independent of ``investigation_tools`` (governance / 空转). Name allowlist only.
EXEC_OUTPUT_CLEAR_TOOLS = frozenset({"host", "run"})

_CLEARED_PREFIX = "[已清理"


def is_cleared_tool_content(content: str | None) -> bool:
    """True when ``content`` is a tool_clear pointer (with or without digest)."""
    return (content or "").startswith(_CLEARED_PREFIX)


def _key_arg(arguments: str) -> str:
    """A short ``key=value`` identifier for the cleared call's pointer.

    Best-effort and never raises: a non-JSON / unexpected argument shape yields an
    empty hint (the pointer then names just the tool).
    """
    try:
        data = json.loads(arguments) if arguments else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    for key in _HINT_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value:
            shown = value
            if key == "command" and len(shown) > _HINT_COMMAND_MAX:
                shown = shown[: _HINT_COMMAND_MAX - 1] + "…"
            return f"{key}={shown!r}"
    return ""


def _path_from_arguments(arguments: str) -> str:
    """Normalize ``path`` / ``file_path`` from tool-call args (empty on failure)."""
    try:
        data = json.loads(arguments) if arguments else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    raw = data.get("path") or data.get("file_path") or ""
    if not isinstance(raw, str):
        return ""
    return raw.strip().replace("\\", "/")


def cleared_placeholder(
    tool_name: str,
    arguments: str,
    original_len: int,
    *,
    already_executed: bool = False,
) -> str:
    """The stable pointer that replaces a cleared tool result's content.

    Deterministic in its inputs (no time / counters), so the same cleared result
    yields byte-identical bytes on every round — the prefix-cache invariant.

    ``already_executed``: exec-family stdout (host / run). The command
    already ran; the pointer must not invite a re-issue just to recover text.

    ``file_read`` stubs are structured (path / content_cleared / reread=allowed)
    so the model can re-read from disk after the body leaves the projection.
    """
    hint = _key_arg(arguments)
    head = f"{tool_name}({hint})" if hint else tool_name
    if already_executed:
        return (
            f"{_CLEARED_PREFIX}: {head} 的输出（{original_len} 字符）"
            "已从上下文窗口移除以节省 token；"
            "该调用已发生，勿仅为回看而重跑（长驻新日志用 run action=read）。]"
        )
    if tool_name == "file_read":
        path = _path_from_arguments(arguments)
        path_part = f" path={path!r}" if path else ""
        return (
            f"{_CLEARED_PREFIX}: file_read{path_part} "
            f"chars={original_len} status=content_cleared reread=allowed]"
        )
    return (
        f"{_CLEARED_PREFIX}: {head} 的输出（{original_len} 字符）"
        "已从上下文窗口移除以节省 token。"
        "仍有该正文则勿重调；仅正文不在上下文时才可重新调用该工具获取。]"
    )


def structural_file_read_summary(path: str, content: str, *, max_chars: int) -> str | None:
    """Deterministic digest of a cleared ``file_read`` body (no LLM).

    Aligns with ``structural_write_summary`` (class/id / selectors / headings…).
    When no structure is found, returns a short head preview; returns ``None`` when
    ``max_chars <= 0`` or content is empty. Pure in (path, content, max_chars).
    """
    if max_chars <= 0 or not isinstance(content, str) or not content.strip():
        return None
    from agentcore.runtime.engine.write_args_clear import structural_write_summary

    structure = structural_write_summary(path, content)
    if structure:
        text = f"【自动结构摘录，非全文】 {structure}"
    else:
        lines = [ln.strip() for ln in content.strip().splitlines() if ln.strip()][:4]
        if not lines:
            return None
        preview = " | ".join(ln[:80] for ln in lines)
        text = f"【自动摘录，非全文】 {preview}"
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return "…"
    return text[: max_chars - 1].rstrip() + "…"


def cleared_tool_content(
    tool_name: str,
    arguments: str,
    original_content: str,
    *,
    min_chars: int,
    summary_max_chars: int,
    already_executed: bool = False,
) -> str:
    """Pointer (+ optional ``file_read`` digest) replacing a cleared tool body.

    Hard invariant: ``len(result) < min_chars`` so the stub is never re-cleared
    (idempotent projection / prefix-cache). Exec-family stubs never append a digest.
    """
    body = original_content or ""
    pointer = cleared_placeholder(
        tool_name, arguments, len(body), already_executed=already_executed
    )
    if (
        already_executed
        or tool_name != "file_read"
        or summary_max_chars <= 0
        or min_chars <= 0
    ):
        return pointer
    room = min_chars - len(pointer) - 1  # newline between pointer and digest
    if room <= 0:
        return pointer
    budget = min(summary_max_chars, room)
    path = _path_from_arguments(arguments)
    summary = structural_file_read_summary(path, body, max_chars=budget)
    if not summary:
        return pointer
    combined = f"{pointer}\n{summary}"
    if len(combined) >= min_chars:
        return pointer
    return combined


def project_cleared_window(
    messages: list[LLMMessage],
    *,
    clearable_tools: frozenset[str] | set[str],
    keep_recent: int,
    min_chars: int,
    summary_max_chars: int = 0,
    already_executed: bool = False,
) -> list[LLMMessage]:
    """Return ``messages`` with old clearable tool results collapsed to pointers.

    ``already_executed`` selects the exec-family pointer (no re-fetch invite).

    ``keep_recent`` counts assistant **rounds** that issued a completed clearable
    call (write-args clear uses the same unit). A parallel batch of six
    ``file_read`` in one assistant message is one round: all six stay until that
    round falls out. ``keep_recent=0`` collapses every qualifying result.

    Returns the SAME list object unchanged when nothing qualifies (a short turn with
    few reads never triggers clearing), so callers can cheaply detect a no-op with
    ``result is messages``. Otherwise returns a new list; the kept messages are the
    same objects (only cleared ``tool`` messages are rebuilt), and structure is
    preserved (role / ``tool_call_id`` untouched) so the assistant↔tool pairing the
    OpenAI API requires never breaks.

    Idempotent: a pointer (optionally + digest) is below ``min_chars`` and so is
    never re-cleared, hence ``project(project(x)) == project(x)``.

    ``summary_max_chars``: only ``file_read`` may append a digest; ``0`` = pointer only
    (rollback knob). Grep / web_search stay pointer-only.
    """
    if not clearable_tools or keep_recent < 0:
        return messages

    # tool_call_id → (tool_name, arguments, assistant_msg_index)
    call_info: dict[str, tuple[str, str, int]] = {}
    for assistant_index, message in enumerate(messages):
        if message.role == "assistant" and message.tool_calls:
            for call in message.tool_calls:
                call_info[call.id] = (
                    call.function.name,
                    call.function.arguments or "",
                    assistant_index,
                )

    # Clearable tool results (read-only tool + large enough), in order.
    clearable: list[tuple[int, int]] = []  # (tool_msg_index, assistant_index)
    for index, message in enumerate(messages):
        if message.role != "tool" or message.tool_call_id is None:
            continue
        info = call_info.get(message.tool_call_id)
        if info is None:
            continue
        name, _arguments, assistant_index = info
        if name not in clearable_tools:
            continue
        if len(message.content or "") < min_chars:
            continue
        clearable.append((index, assistant_index))

    if not clearable:
        return messages

    # Near window = last N assistant messages that issued a completed clearable
    # call (one ReAct round may issue several reads in parallel; the whole batch
    # stays). keep_recent=0 → collapse all. Do not use lst[-0:].
    round_indices: list[int] = []
    seen_rounds: set[int] = set()
    for _tool_index, assistant_index in clearable:
        if assistant_index not in seen_rounds:
            seen_rounds.add(assistant_index)
            round_indices.append(assistant_index)
    keep_rounds: set[int] = (
        set() if keep_recent == 0 else set(round_indices[-keep_recent:])
    )
    to_clear = {
        tool_index
        for tool_index, assistant_index in clearable
        if assistant_index not in keep_rounds
    }
    if not to_clear:
        return messages

    projected: list[LLMMessage] = []
    for index, message in enumerate(messages):
        if index in to_clear:
            name, arguments, _assistant_index = call_info[message.tool_call_id]
            projected.append(
                LLMMessage(
                    role="tool",
                    content=cleared_tool_content(
                        name,
                        arguments,
                        message.content or "",
                        min_chars=min_chars,
                        summary_max_chars=summary_max_chars,
                        already_executed=already_executed,
                    ),
                    tool_call_id=message.tool_call_id,
                )
            )
        else:
            projected.append(message)
    return projected
