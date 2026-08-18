"""回合内工具结果清理 (clear_tool_uses): collapse OLD tool results in the LLM view.

Within one ReAct run a long worker re-reads many files / pages and may also pile
``host_shell`` / ``terminal`` stdout; every round re-sends those bodies. This module
collapses OLD results into a compact, stable pointer so the model still knows the
call happened without carrying the full body.

Two families, **independent** keep-windows (do not share ``keep_recent``; do not
put exec tools into ``investigation_tools`` — that set also drives idle-governance):

- Investigation (read-only, re-fetchable): pointer says use remaining
  verbatim; only invite a fresh call when that body is gone from context.
- Exec output (``host_shell`` / ``terminal``): pointer forbids re-run-to-recover.
  ``code_execute`` / ``test_run`` stay verbatim.

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
SEARCH / RESEARCH) past ``keep_recent`` and ≥ ``min_chars``. Exec clear =
``EXEC_OUTPUT_CLEAR_TOOLS`` on a separate pass. Never cleared: ``code_execute`` /
``test_run`` / ``file_write`` / interaction cards / steers / assistant / system.

R1 (file_read): cleared ``file_read`` results become a structured stub (path /
content_cleared / reread=allowed) plus an optional deterministic digest (no LLM).
The same projection writes ``file_read_cleared_paths`` (fully-cleared: stub
present, zero verbatim). Recovery reads of those paths do not increment
``FILE_READ_SAME_PATH_MAX``. Write-success / citation refresh still uses
``refresh_file_read_reread_grant`` to override while verbatim remains.
"""

from __future__ import annotations

import json
from dataclasses import replace

from agentcore.llm.provider.protocol import LLMMessage
from agentcore.tools.protocol import ToolContext

# Argument keys, in priority order, that identify WHICH call was cleared.
_HINT_KEYS = (
    "path",
    "file_path",
    "query",
    "url",
    "pattern",
    "process_id",
    "command",
    "subcommand",
)
_HINT_COMMAND_MAX = 80

# Independent of ``investigation_tools`` (governance / 空转). Name allowlist only.
EXEC_OUTPUT_CLEAR_TOOLS = frozenset({"host_shell", "terminal"})

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

    ``already_executed``: exec-family stdout (host_shell / terminal). The command
    already ran; the pointer must not invite a re-issue just to recover text.

    ``file_read`` stubs are structured (path / content_cleared / reread=allowed)
    so the model and the same-path ceiling share one ledger produced here — not
    reconstructed later from journal prose.
    """
    hint = _key_arg(arguments)
    head = f"{tool_name}({hint})" if hint else tool_name
    if already_executed:
        return (
            f"{_CLEARED_PREFIX}: {head} 的输出（{original_len} 字符）"
            "已从上下文窗口移除以节省 token；"
            "该调用已发生，勿仅为回看而重跑（长驻新日志用 terminal read）。]"
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

    # tool_call_id → (tool_name, arguments), from the assistant messages that issued
    # the calls — lets us decide clearability and build a re-fetch hint without the
    # executor's state.
    call_info: dict[str, tuple[str, str]] = {}
    for message in messages:
        if message.role == "assistant" and message.tool_calls:
            for call in message.tool_calls:
                call_info[call.id] = (call.function.name, call.function.arguments or "")

    # Positions of clearable tool results (read-only tool + large enough), in order.
    clearable_indices: list[int] = []
    for index, message in enumerate(messages):
        if message.role != "tool" or message.tool_call_id is None:
            continue
        info = call_info.get(message.tool_call_id)
        if info is None:
            continue
        name, _arguments = info
        if name not in clearable_tools:
            continue
        if len(message.content or "") < min_chars:
            continue
        clearable_indices.append(index)

    # Keep the most recent ``keep_recent`` verbatim; clear everything older.
    if len(clearable_indices) <= keep_recent:
        return messages
    to_clear = set(clearable_indices[: len(clearable_indices) - keep_recent])

    projected: list[LLMMessage] = []
    for index, message in enumerate(messages):
        if index in to_clear:
            name, arguments = call_info[message.tool_call_id]  # present by construction
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


def _call_info_map(messages: list[LLMMessage]) -> dict[str, tuple[str, str]]:
    call_info: dict[str, tuple[str, str]] = {}
    for message in messages:
        if message.role == "assistant" and message.tool_calls:
            for call in message.tool_calls:
                call_info[call.id] = (call.function.name, call.function.arguments or "")
    return call_info


def _file_read_projection_path_sets(
    messages: list[LLMMessage],
) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(verbatim_paths, stub_paths)`` from a projected window.

    Path identity comes from the originating tool-call args — the same source
    ``project_cleared_window`` used when it wrote the stub. Does not parse stub
    prose or inspect an unprojected journal.
    """
    call_info = _call_info_map(messages)
    verbatim: set[str] = set()
    stubs: set[str] = set()
    for message in messages:
        if message.role != "tool" or message.tool_call_id is None:
            continue
        info = call_info.get(message.tool_call_id)
        if info is None:
            continue
        name, arguments = info
        if name != "file_read":
            continue
        path = _path_from_arguments(arguments)
        if not path:
            continue
        if is_cleared_tool_content(message.content):
            stubs.add(path)
        else:
            verbatim.add(path)
    return frozenset(verbatim), frozenset(stubs)


def collect_file_read_verbatim_paths(messages: list[LLMMessage]) -> frozenset[str]:
    """Paths whose ``file_read`` tool results are still verbatim in ``messages``.

    ``messages`` should already be a ``project_cleared_window`` view (or equivalent).
    Cleared stubs (``[已清理…``) do not count. Small never-cleared results do.
    """
    verbatim, _stubs = _file_read_projection_path_sets(messages)
    return verbatim


def collect_file_read_cleared_paths(messages: list[LLMMessage]) -> frozenset[str]:
    """Paths whose ``file_read`` bodies are fully gone in the projected window.

    Fully cleared = at least one stub written by this projection family, and
    zero verbatim bodies left for that path. Partial clear (keep_recent still
    holds a body) is not in this set — idle re-reads of remaining text still
    count toward the same-path ceiling.
    """
    verbatim, stubs = _file_read_projection_path_sets(messages)
    return stubs - verbatim


def apply_file_read_clear_state(
    context: ToolContext,
    messages: list[LLMMessage],
    *,
    investigation_tools: frozenset[str] | set[str],
    keep_recent: int | None = None,
    min_chars: int | None = None,
    summary_max_chars: int | None = None,
) -> ToolContext:
    """Project the canonical window and sync clear dual-state onto ``ToolContext``.

    Call before each ``execute_tools``. Judgment uses the same
    ``project_cleared_window`` settings as ``build_request_window`` — never journal /
    unprojected messages alone.

    Writes ``file_read_verbatim_paths`` (bodies still in the projection) and
    ``file_read_cleared_paths`` (fully-cleared stubs). Recovery reads of the
    latter do not consume ``FILE_READ_SAME_PATH_MAX``. Write-success / citation
    still refresh ``file_read_reread_remaining`` separately.
    """
    from agentcore.config import settings

    keep = (
        settings.engine_tool_clear_keep_recent if keep_recent is None else keep_recent
    )
    min_c = settings.engine_tool_clear_min_chars if min_chars is None else min_chars
    sum_max = (
        settings.engine_tool_clear_file_read_summary_max_chars
        if summary_max_chars is None
        else summary_max_chars
    )

    if not investigation_tools:
        return replace(
            context,
            file_read_verbatim_paths=frozenset(),
            file_read_cleared_paths=frozenset(),
        )

    projected = project_cleared_window(
        messages,
        clearable_tools=investigation_tools,
        keep_recent=keep,
        min_chars=min_c,
        summary_max_chars=sum_max,
    )
    verbatim, stubs = _file_read_projection_path_sets(projected)
    return replace(
        context,
        file_read_verbatim_paths=verbatim,
        file_read_cleared_paths=stubs - verbatim,
    )


def refresh_file_read_reread_grant(
    context: ToolContext,
    paths: list[str] | tuple[str, ...] | set[str] | None,
    *,
    grant: int | None = None,
) -> list[str]:
    """Issue or refresh sticky reread grant for named paths (citation/contract rework).

    When contract.retry / cite_upgrade points workers back at landed drafts whose
    verbatim bodies were tool_cleared, the same-path ceiling would otherwise deny
    ``file_read``. Refreshing the existing sticky grant (not a third grant system)
    lets the worker re-read each named path at least once.
    """
    from agentcore.config import settings

    amount = (
        settings.engine_file_read_reread_grant if grant is None else max(0, int(grant))
    )
    if amount <= 0 or not paths:
        return []
    refreshed: list[str] = []
    issued = context.file_read_reread_issued
    remaining = context.file_read_reread_remaining
    for raw in paths:
        path = (raw or "").strip().replace("\\", "/")
        if not path:
            continue
        issued[path] = True
        remaining[path] = amount
        refreshed.append(path)
    return refreshed
