"""Worker / captain mid-run window compaction: fold older ReAct rounds into a rolling summary.

Orthogonal to conversation compaction (cross-user-turn chat). Sliding
``tool_clear`` / ``write_args_clear`` / browser-snapshot omit used to rewrite
mid-history every round; this layer is the only same-window rewrite — once, then
append-only until the next compact.

Canonical ``messages`` and the turn journal stay full. Resume rebuilds the fat
window via ``window_from_journal``, then ``build_request_window`` re-applies this
projection from the latest ``window_compact`` fact. UI is unchanged.

→ 执行引擎架构设计 §三 · 工人回合内 window compact
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.core.text import truncate_head_tail
from agentcore.llm.provider.protocol import LLMMessage, llm_content_text

logger = get_logger(__name__)

_COMPACT_TIMEOUT_SECONDS = 45.0
_ASSISTANT_CLIP = 1500
_TOOL_CLIP = 400
_USER_CLIP = 800
_ELISION = "\n\n……（摘要过长，已保留首尾）……\n\n"

SUMMARY_LEAD = (
    "（以下是你在本任务早前步骤的摘要，由系统自动压缩以控制上下文长度；"
    "需要更早的精确原文时，按摘要中的路径再读，不要重做已完成的步骤。）\n\n"
)
BRIDGE_USER = "（系统）更早的步骤已收入上一条摘要。从下面最近的工作继续。"


def worker_compact_system_prompt() -> str:
    """Production worker-window compact system prompt with the live budget filled in.

    Policy text lives with the chat compaction contract; this call only fills
    the worker character budget.
    """
    from agentcore.conversation.compact_prompt import WORKER_COMPACT_PROMPT_TEMPLATE

    return WORKER_COMPACT_PROMPT_TEMPLATE.replace(
        "__BUDGET__", str(settings.engine_window_compact_summary_char_budget)
    )


# run_id → skip compact until this 0-based round_idx (failure cooldown).
_cooldown_until_round: dict[str, int] = {}


def head_end(messages: Sequence[LLMMessage]) -> int:
    """Index after leading ``system`` messages plus the first ``user`` (the task)."""
    i = 0
    while i < len(messages) and messages[i].role == "system":
        i += 1
    if i < len(messages) and messages[i].role == "user":
        i += 1
    return i


def preamble_end(messages: Sequence[LLMMessage], start: int) -> int:
    """Extend ``start`` through any non-assistant messages before the first round."""
    i = start
    while i < len(messages) and messages[i].role != "assistant":
        i += 1
    return i


def assistant_round_spans(
    messages: Sequence[LLMMessage], *, start: int
) -> list[tuple[int, int]]:
    """Half-open ``[lo, hi)`` spans: each starts at an assistant, includes following
    tool/user notes, and stops before the next assistant."""
    spans: list[tuple[int, int]] = []
    i = start
    n = len(messages)
    while i < n:
        if messages[i].role != "assistant":
            i += 1
            continue
        j = i + 1
        while j < n and messages[j].role != "assistant":
            j += 1
        spans.append((i, j))
        i = j
    return spans


def estimate_text_tokens(text: str) -> int:
    """Fold-cut estimate only: ~1 token per CJK char, ~4 chars per other token.

    Not a measured tokenizer. Do not call from ``build_request_window``.
    """
    if not text:
        return 0
    cjk = 0
    for char in text:
        code = ord(char)
        if (
            0x3000 <= code <= 0x9FFF
            or 0xF900 <= code <= 0xFAFF
            or 0xFF00 <= code <= 0xFFEF
        ):
            cjk += 1
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def estimate_message_tokens(message: LLMMessage) -> int:
    """Estimated tokens of one window message (content, reasoning, tool args)."""
    n = estimate_text_tokens(llm_content_text(message.content))
    n += estimate_text_tokens(message.reasoning_content or "")
    for block in message.thinking_blocks or ():
        if isinstance(block, dict):
            n += estimate_text_tokens(str(block.get("thinking") or ""))
            n += estimate_text_tokens(str(block.get("data") or ""))
    for call in message.tool_calls or ():
        n += estimate_text_tokens(call.function.name or "")
        n += estimate_text_tokens(call.function.arguments or "")
    return n


def estimate_span_tokens(
    messages: Sequence[LLMMessage], span: tuple[int, int]
) -> int:
    lo, hi = span
    return sum(estimate_message_tokens(m) for m in messages[lo:hi])


def recency_keep_rounds(
    messages: Sequence[LLMMessage],
    spans: Sequence[tuple[int, int]],
    *,
    token_budget: int,
) -> int:
    """Newest rounds to keep verbatim at this fold cut.

    Always keeps the latest round, even when it alone exceeds ``token_budget``.
    Keeps two when both fit. Round count 2 is not a hard floor.
    """
    if not spans:
        return 0
    if len(spans) == 1:
        return 1
    newest = estimate_span_tokens(messages, spans[-1])
    previous = estimate_span_tokens(messages, spans[-2])
    if newest + previous <= max(0, token_budget):
        return 2
    return 1


def select_new_fold_spans(
    messages: Sequence[LLMMessage],
    *,
    already_folded: int,
    min_fold_rounds: int,
    recency_rounds: int | None = None,
    recency_token_budget: int | None = None,
) -> list[tuple[int, int]]:
    """Newly foldable assistant-round spans, or ``[]`` when a pass is not worth an LLM.

    Recency is a token budget at the fold cut (newest round always; two when they
    fit). ``recency_rounds`` is a test override. Incremental: skips the first
    ``already_folded`` foldable spans (already inside the rolling summary).
    """
    pre = preamble_end(messages, head_end(messages))
    spans = assistant_round_spans(messages, start=pre)
    if recency_rounds is None:
        budget = (
            settings.engine_window_compact_recency_token_budget
            if recency_token_budget is None
            else recency_token_budget
        )
        recency = recency_keep_rounds(messages, spans, token_budget=budget)
    else:
        recency = max(0, recency_rounds)
    if len(spans) <= recency:
        return []
    foldable = spans[:-recency] if recency else spans
    new = foldable[max(0, already_folded) :]
    if len(new) < min_fold_rounds:
        return []
    return new


def project_compacted_window(
    messages: list[LLMMessage],
    *,
    summary: str,
    folded_rounds: int,
    recency_rounds: int | None = None,
) -> list[LLMMessage]:
    """Replace folded assistant rounds with an assistant summary + user bridge.

    Never mutates ``messages``. No-op (same object) when there is nothing to fold.
    Uses the stored ``folded_rounds`` watermark; does not re-estimate tokens.
    Never eats the newest assistant round, even if ``folded_rounds`` is stale-high
    after resume (``recency_rounds`` override for tests).
    """
    text = (summary or "").strip()
    if not text or folded_rounds <= 0:
        return messages
    recency = 1 if recency_rounds is None else max(0, recency_rounds)
    pre = preamble_end(messages, head_end(messages))
    spans = assistant_round_spans(messages, start=pre)
    max_fold = max(0, len(spans) - recency)
    n = min(folded_rounds, max_fold)
    if n <= 0:
        return messages
    fold_hi = spans[n - 1][1]
    out = list(messages[:pre])
    out.append(LLMMessage(role="assistant", content=SUMMARY_LEAD + text))
    out.append(LLMMessage(role="user", content=BRIDGE_USER))
    out.extend(messages[fold_hi:])
    return out


def latest_window_compact(run_id: str) -> dict | None:
    """Last ``window_compact`` payload for ``run_id`` on the ambient fact log."""
    if not run_id:
        return None
    from agentcore.runtime.facts import FactKind, current_fact_log

    log = current_fact_log.get()
    if log is None:
        return None
    last: dict | None = None
    for entry in log.entries():
        if (entry.get("kind") or "") != FactKind.WINDOW_COMPACT.value:
            continue
        payload = entry.get("payload") or {}
        if payload.get("run_id") == run_id and isinstance(payload, dict):
            last = payload
    return last


def last_prompt_tokens_from_facts(run_id: str) -> int:
    """Most recent worker LLM ``last_prompt`` for this run (resume seed)."""
    if not run_id:
        return 0
    from agentcore.runtime.facts import FactKind, current_fact_log

    log = current_fact_log.get()
    if log is None:
        return 0
    last = 0
    for entry in log.entries():
        if (entry.get("kind") or "") != FactKind.LLM_CALL.value:
            continue
        payload = entry.get("payload") or {}
        if payload.get("run_id") != run_id:
            continue
        usage = payload.get("usage") or {}
        last = int(usage.get("last_prompt") or usage.get("input") or 0)
    return last


def apply_stored_window_compact(messages: list[LLMMessage], run_id: str) -> list[LLMMessage]:
    """Projection half: apply the latest stored summary, or return ``messages``."""
    payload = latest_window_compact(run_id)
    if not payload:
        return messages
    summary = str(payload.get("summary") or "")
    folded = int(payload.get("folded_rounds") or 0)
    return project_compacted_window(messages, summary=summary, folded_rounds=folded)


def window_compact_due(
    *,
    new_spans: Sequence[tuple[int, int]],
    last_prompt_tokens: int,
    near: bool,
    min_fold_rounds: int,
    trigger_fold_rounds: int,
    trigger_prompt_tokens: int,
) -> bool:
    """Token / round / near-ceiling dual trigger (after a non-empty new-span list)."""
    n = len(new_spans)
    if n <= 0:
        return False
    if near:
        return True
    if last_prompt_tokens >= trigger_prompt_tokens and n >= min_fold_rounds:
        return True
    return n >= trigger_fold_rounds


def near_window_ceiling(prompt_tokens: int, context_length: int | None) -> bool:
    """True when the last request is near this worker's model window."""
    if prompt_tokens <= 0:
        return False
    ratio = settings.engine_window_compact_near_ratio
    if context_length is not None and context_length > 0:
        return prompt_tokens >= int(context_length * ratio)
    return prompt_tokens >= settings.engine_window_compact_near_tokens


def _clip(text: str, limit: int) -> str:
    stripped = (text or "").strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 1] + "…"


def _tool_name_and_args(message: LLMMessage) -> str:
    if not message.tool_calls:
        return ""
    parts: list[str] = []
    for call in message.tool_calls:
        name = call.function.name or "?"
        args = _clip(call.function.arguments or "", 240)
        parts.append(f"{name}({args})" if args else name)
    return "; ".join(parts)


def render_window_fold(old_summary: str, folded: Sequence[LLMMessage]) -> str:
    """Summarizer user payload: prior rolling summary + clipped step transcript."""
    lines: list[str] = []
    paths: list[str] = []
    seen_paths: set[str] = set()
    for message in folded:
        if message.role == "assistant":
            body = _clip(llm_content_text(message.content), _ASSISTANT_CLIP)
            tools = _tool_name_and_args(message)
            chunk = body
            if tools:
                chunk = f"{chunk}\n  tools: {tools}" if chunk else f"tools: {tools}"
            if chunk:
                lines.append(f"assistant：{chunk}")
            for call in message.tool_calls or []:
                path = _path_from_args(call.function.arguments or "")
                if path and path not in seen_paths:
                    seen_paths.add(path)
                    paths.append(path)
            continue
        if message.role == "tool":
            body = _clip(llm_content_text(message.content), _TOOL_CLIP)
            lines.append(f"tool：{body}" if body else "tool：（空）")
            continue
        if message.role == "user":
            body = _clip(llm_content_text(message.content), _USER_CLIP)
            if body:
                lines.append(f"user：{body}")
    convo = "\n\n".join(lines) if lines else "（无正文）"
    prior = old_summary.strip() or "（无，这是本任务的首次压缩）"
    files = ""
    if paths:
        listed = "\n".join(f"- {p}" for p in paths[:16])
        files = f"# 本批涉及的文件（必须并入「涉及的文件与标识符」）\n{listed}\n\n"
    return (
        f"# 已有滚动摘要\n{prior}\n\n"
        f"{files}"
        f"# 待并入摘要的更早步骤（按时间先后）\n{convo}\n\n"
        "请输出更新后的滚动摘要。"
    )


def _path_from_args(arguments: str) -> str:
    try:
        data = json.loads(arguments) if arguments else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    # Mixed tools in one fold: read/write/edit use file_path; grep/delete use path.
    raw = data.get("file_path") or data.get("path") or ""
    if not isinstance(raw, str):
        return ""
    return raw.strip().replace("\\", "/")


def _slice_messages(
    messages: Sequence[LLMMessage], spans: Sequence[tuple[int, int]]
) -> list[LLMMessage]:
    out: list[LLMMessage] = []
    for lo, hi in spans:
        out.extend(messages[lo:hi])
    return out


async def maybe_compact_worker_window(
    messages: list[LLMMessage],
    *,
    run_id: str,
    role: str,
    round_idx: int,
    last_prompt_tokens: int,
    conversation_id: str,
    user_id: str,
    model_id: str | None,
    tools: list[dict] | None = None,
) -> bool:
    """If due, fold older worker rounds and record a ``window_compact`` fact.

    Never raises. Never mutates ``messages``. Returns whether a new summary was stored.
    """
    if role not in ("worker", "captain") or not settings.engine_window_compact_enabled:
        return False
    if not run_id or not conversation_id or not user_id:
        return False

    already = int((latest_window_compact(run_id) or {}).get("folded_rounds") or 0)
    min_fold = settings.engine_window_compact_min_fold_rounds
    pre = preamble_end(messages, head_end(messages))
    spans = assistant_round_spans(messages, start=pre)
    recency = recency_keep_rounds(
        messages,
        spans,
        token_budget=settings.engine_window_compact_recency_token_budget,
    )
    new_spans = select_new_fold_spans(
        messages,
        recency_rounds=recency,
        already_folded=already,
        min_fold_rounds=1,
    )
    context_length = None
    if model_id:
        from agentcore.llm.model_metadata import model_metadata_for

        context_length = model_metadata_for(model_id).context_length
    near = near_window_ceiling(last_prompt_tokens, context_length)
    due = window_compact_due(
        new_spans=new_spans,
        last_prompt_tokens=last_prompt_tokens,
        near=near,
        min_fold_rounds=min_fold,
        trigger_fold_rounds=settings.engine_window_compact_trigger_fold_rounds,
        trigger_prompt_tokens=settings.engine_window_compact_prompt_tokens,
    )
    if not due:
        return False
    # Token/near due still needs the internal empty-run gate (min_fold), except
    # near-ceiling which may fold a smaller batch so the next request can fit.
    internal_min = 1 if near else min_fold
    if len(new_spans) < internal_min:
        return False
    new_spans = list(new_spans[: settings.engine_window_compact_max_fold_rounds])

    until = _cooldown_until_round.get(run_id, 0)
    if round_idx <= until and not near:
        logger.info(
            "engine.window_compact_skip",
            run_id=run_id,
            reason="cooldown",
            until_round=until,
        )
        return False

    folded = _slice_messages(messages, new_spans)
    old_summary = str((latest_window_compact(run_id) or {}).get("summary") or "")
    try:
        summary = await _summarize_worker_fold(
            old_summary,
            folded,
            conversation_id=conversation_id,
            user_id=user_id,
            window=messages,
            tools=tools,
            model_id=model_id,
        )
    except Exception as exc:
        _cooldown_until_round[run_id] = round_idx + settings.engine_window_compact_cooldown_rounds
        logger.warning(
            "engine.window_compact_failed",
            run_id=run_id,
            error=str(exc),
        )
        return False
    if not summary:
        _cooldown_until_round[run_id] = round_idx + settings.engine_window_compact_cooldown_rounds
        logger.warning("engine.window_compact_failed", run_id=run_id, error="empty")
        return False

    from agentcore.runtime.facts import WindowCompactFact, record_turn_fact

    folded_rounds = already + len(new_spans)
    record_turn_fact(
        WindowCompactFact(
            run_id=run_id,
            summary=summary,
            folded_rounds=folded_rounds,
        ).to_fact()
    )
    _cooldown_until_round.pop(run_id, None)
    logger.info(
        "engine.window_compact",
        run_id=run_id,
        folded_rounds=folded_rounds,
        new_rounds=len(new_spans),
        kept_rounds=recency,
        summary_chars=len(summary),
        prompt_tokens=last_prompt_tokens,
        near=near or None,
    )
    return True


async def _summarize_worker_fold(
    old_summary: str,
    folded: Sequence[LLMMessage],
    *,
    conversation_id: str,
    user_id: str,
    window: Sequence[LLMMessage] | None = None,
    tools: list[dict] | None = None,
    model_id: str | None = None,
) -> str:
    """One non-thinking compaction call. ``""`` on skip / timeout / empty.

    When ``window`` + ``tools`` are the live ReAct request, reuse that header
    (append a fenced instruction) and the worker's ``model_id``. Otherwise fall
    back to the isolated dump.
    """
    from agentcore.billing.gate import BackgroundLlmSkip, run_compaction_llm
    from agentcore.llm.credentials import LLMCredentials
    from agentcore.llm.factory import build_provider
    from agentcore.llm.model_selection import build_selected_request, select_call
    from agentcore.llm.provider.call_budget import complete_within_budget
    from agentcore.llm.resolve import resolve_turn_model as resolve_user_model
    from agentcore.runtime.resolve.prompt.envelope import TURN_ENVELOPE_FENCE

    live = list(window or ())
    reuse_header = bool(live and tools)
    if reuse_header:
        instruction = (
            f"{TURN_ENVELOPE_FENCE}\n{worker_compact_system_prompt()}\n\n"
            f"# 已有滚动摘要\n{old_summary.strip() or '（无，这是本任务的首次压缩）'}\n\n"
            "较早步骤已在上面的对话里；最近若干轮会保留原文。"
            "只输出更新后的滚动摘要正文。"
        )
        req_messages = [*live, LLMMessage(role="user", content=instruction)]
    else:
        req_messages = [
            LLMMessage(role="system", content=worker_compact_system_prompt()),
            LLMMessage(role="user", content=render_window_fold(old_summary, folded)),
        ]

    async def _runner(credentials: LLMCredentials) -> str:
        pinned = (model_id or "").strip()
        model = pinned if reuse_header and pinned else resolve_user_model(credentials)
        provider = build_provider(credentials, purpose="platform_internal")
        request = build_selected_request(
            select_call("compaction", model),
            req_messages,
            tools=list(tools) if reuse_header and tools is not None else None,
            tool_choice="none" if reuse_header else "auto",
            stream=False,
        )
        try:
            response = await complete_within_budget(
                provider,
                request,
                budget=_COMPACT_TIMEOUT_SECONDS,
                user_waiting=True,
            )
        except TimeoutError:
            logger.warning(
                "engine.window_compact_timeout",
                conversation_id=conversation_id,
            )
            return ""
        finally:
            close = getattr(provider, "close", None)
            if close is not None:
                await close()
        return truncate_head_tail(
            (response.content or "").strip(),
            settings.engine_window_compact_summary_char_budget,
            marker=_ELISION,
        )

    bg = await run_compaction_llm(user_id, conversation_id, runner=_runner)
    if isinstance(bg, BackgroundLlmSkip):
        return ""
    return (bg.value or "").strip()
