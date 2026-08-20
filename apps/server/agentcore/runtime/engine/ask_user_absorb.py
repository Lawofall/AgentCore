"""Content absorption for blocking ``ask_user``.

When the model streams prose and calls blocking ``ask_user`` without its own
``message``, the engine folds that prose into the card. When the model already
wrote ``message``, the same-round guidance stays in the bubble (CheckpointCard
pending renders nothing so that body remains the visible face).

Parse failure never rewrites arguments — the unique parse
(:func:`parse_tool_call_arguments`) either succeeds or the call falls through
to the existing ``args_parse_failed`` path.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from agentcore.core.types import ToolEffect
from agentcore.llm.provider.protocol import LLMMessage, ToolCall
from agentcore.runtime.engine.tool_protocol_sanitize import parse_tool_call_arguments
from agentcore.runtime.facts import FactKind, current_fact_log
from agentcore.runtime.loop_controller import ToolAttempt

from .segments import tool_calls_to_dicts


def _try_parse_args(tc: ToolCall) -> tuple[dict[str, Any], str | None] | None:
    """``None`` = parse failed (do not rewrite). Empty dict = parsed empty object."""
    try:
        parsed, repaired = parse_tool_call_arguments(
            tc.function.arguments or "",
            tool_name=tc.function.name or "",
        )
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed, repaired


def is_blocking_ask_user(tc: ToolCall) -> bool:
    return tc.function.name == "ask_user"


def _patch_tool_call_args(tc: ToolCall, args: dict[str, Any]) -> ToolCall:
    return replace(
        tc,
        function=replace(
            tc.function,
            arguments=json.dumps(args, ensure_ascii=False),
        ),
    )


def _with_repaired_args(tc: ToolCall, repaired: str) -> ToolCall:
    return replace(tc, function=replace(tc.function, arguments=repaired))


def prepare_blocking_ask_user_tool_calls(
    tool_calls: list[ToolCall],
    round_content: str,
) -> tuple[list[ToolCall], bool]:
    """Inject ``round_content`` into a blocking ``ask_user`` when ``message`` is empty.

    Returns ``(patched_calls, content_folded)``. ``content_folded`` is True only
    when the engine actually wrote ``message`` from this round's prose.

    Parse failure leaves the raw arguments untouched. Does not rewrite card copy
    when the model already set ``message``.
    """
    content = (round_content or "").strip()
    patched: list[ToolCall] = []
    content_folded = False
    for tc in tool_calls:
        if tc.function.name != "ask_user":
            patched.append(tc)
            continue
        parsed = _try_parse_args(tc)
        if parsed is None:
            patched.append(tc)
            continue
        args, repaired = parsed
        if repaired is not None:
            tc = _with_repaired_args(tc, repaired)
        if str(args.get("message") or "").strip() or not content:
            patched.append(tc)
            continue
        args["message"] = content
        patched.append(_patch_tool_call_args(tc, args))
        content_folded = True
    return patched, content_folded


def _blocking_ask_user_succeeded(
    tool_calls: list[ToolCall],
    attempts: list[ToolAttempt],
) -> bool:
    for tc, attempt in zip(tool_calls, attempts, strict=False):
        if is_blocking_ask_user(tc) and attempt.success:
            return True
    return False


def _amend_last_llm_call(
    *,
    content: str,
    tool_calls: list[ToolCall] | None = None,
) -> None:
    log = current_fact_log.get()
    if log is None:
        return
    facts = log._facts  # noqa: SLF001 - paired write-back for the in-memory journal
    for i in range(len(facts) - 1, -1, -1):
        if facts[i].kind != FactKind.LLM_CALL.value:
            continue
        payload = dict(facts[i].payload)
        payload["content"] = content
        if tool_calls is not None:
            payload["tool_calls"] = tool_calls_to_dicts(tool_calls)
        from agentcore.runtime.facts import Fact

        facts[i] = Fact(kind=facts[i].kind, payload=payload, ts=facts[i].ts)
        return


def absorb_blocking_ask_user_content(
    *,
    messages: list[LLMMessage],
    tool_calls: list[ToolCall],
    attempts: list[ToolAttempt],
    terminal_effect: ToolEffect | None,
    emit_reset: Any,
    content_folded: bool,
) -> bool:
    """Clear absorbed assistant prose after a successful blocking ``ask_user`` pause.

    Only when the engine folded this round's prose into ``message``. Returns
    ``True`` when content was absorbed (caller should roll back ``final_content``).
    """
    if not content_folded:
        return False
    if terminal_effect is not ToolEffect.SUSPEND:
        return False
    if not _blocking_ask_user_succeeded(tool_calls, attempts):
        return False
    if not messages or messages[-1].role != "assistant":
        return False

    messages[-1] = replace(messages[-1], content=None)
    _amend_last_llm_call(content="", tool_calls=tool_calls)
    emit_reset("ask_user")
    return True
