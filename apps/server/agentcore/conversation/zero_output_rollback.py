"""Server-side「发送当没发生」for empty first-upstream capability / rate failures.

Class B codes only — do **not** fold into Class A preflight
(``LLM_KEY_REQUIRED`` / ``QUOTA_EXCEEDED`` / ``RATE_LIMITED`` /
``PLATFORM_BILLING_UNAVAILABLE``). Same codes mid-turn (body / tools / tokens)
must stay a failed turn. Pause (``outcome=paused`` or ``finish_reason=paused``)
keeps the send so CEO continue stays. Reload truth is the hard-delete (cloud)
or outbox discard + write-back skip (sidecar); no new SSE.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agentcore.conversation.turn_stats import turn_worker_stats
from agentcore.core.error_codes import ErrorCode
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import MessageRepository
from agentcore.runtime.facts import FactKind

logger = get_logger(__name__)

ZERO_OUTPUT_SEND_REFUSAL_CODES: frozenset[str] = frozenset(
    {
        ErrorCode.LLM_RATE_LIMIT,
        ErrorCode.LLM_KEY_INVALID,
        ErrorCode.LLM_INSUFFICIENT_BALANCE,
    }
)

_TOOL_JOURNAL_KINDS: frozenset[str] = frozenset(
    {
        FactKind.TOOL_CALL.value,
        "tool_use_start",
        "tool_use_end",
        "tool_use_progress",
    }
)

_TOKEN_KEYS: tuple[str, ...] = (
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cache_hit_tokens",
    "cache_miss_tokens",
)


def is_zero_output_send_refusal_code(code: str | None) -> bool:
    return bool(code) and code in ZERO_OUTPUT_SEND_REFUSAL_CODES


def _paused_signal(value: object) -> bool:
    raw = getattr(value, "value", value)
    return isinstance(raw, str) and raw.strip().lower() == "paused"


def should_delete_zero_output_send(
    *,
    error_code: str | None,
    content: str | None,
    tokens: int,
    has_tool_call: bool,
    has_delegated_workers: bool,
    user_created_this_send: bool,
    outcome: object = None,
    finish_reason: object = None,
) -> bool:
    """True only when every Class B empty-fail condition holds."""
    if not user_created_this_send:
        return False
    if not is_zero_output_send_refusal_code(error_code):
        return False
    if _paused_signal(outcome) or _paused_signal(finish_reason):
        return False
    if (content or "").strip():
        return False
    if int(tokens or 0) != 0:
        return False
    return not (has_tool_call or has_delegated_workers)


def error_code_from_turn_result(result: Mapping[str, Any] | None) -> str | None:
    if not isinstance(result, Mapping):
        return None
    raw = result.get("error_code")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    err = result.get("error")
    if isinstance(err, Mapping):
        code = err.get("code")
        if isinstance(code, str) and code.strip():
            return code.strip()
    return None


def token_count_from_turn_result(result: Mapping[str, Any] | None) -> int:
    if not isinstance(result, Mapping):
        return 0
    total = 0
    for key in _TOKEN_KEYS:
        try:
            total += int(result.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


def has_tool_call_in_turn_result(result: Mapping[str, Any] | None) -> bool:
    if not isinstance(result, Mapping):
        return False
    direct = result.get("tool_calls")
    if isinstance(direct, (list, tuple)) and direct:
        return True
    for entry in result.get("journal_entries") or ():
        if not isinstance(entry, Mapping):
            continue
        kind = str(entry.get("kind") or "")
        if kind in _TOOL_JOURNAL_KINDS:
            return True
        if kind == FactKind.LLM_CALL.value:
            payload = entry.get("payload") or {}
            if isinstance(payload, Mapping) and payload.get("tool_calls"):
                return True
    return False


def outcome_from_turn_result(result: Mapping[str, Any] | None) -> object:
    if not isinstance(result, Mapping):
        return None
    return result.get("outcome")


def finish_reason_from_turn_result(result: Mapping[str, Any] | None) -> object:
    if not isinstance(result, Mapping):
        return None
    return result.get("finish_reason")


def should_delete_zero_output_send_result(
    result: Mapping[str, Any] | None,
    *,
    user_created_this_send: bool,
) -> bool:
    if not isinstance(result, Mapping):
        return False
    delegated, _workers = turn_worker_stats(result)
    return should_delete_zero_output_send(
        error_code=error_code_from_turn_result(result),
        content=result.get("content") if isinstance(result.get("content"), str) else "",
        tokens=token_count_from_turn_result(result),
        has_tool_call=has_tool_call_in_turn_result(result),
        has_delegated_workers=delegated,
        user_created_this_send=user_created_this_send,
        outcome=outcome_from_turn_result(result),
        finish_reason=finish_reason_from_turn_result(result),
    )


def result_from_local_turn_writeback(
    *,
    message_id: str | None,
    content: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    reasoning_tokens: int = 0,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
    journal: Sequence[Any] | None = None,
    runs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Map local-turn write-back fields onto the pipeline result the predicate reads.

    ``runs.error`` is the same structured error local finalize already persists.
    When the client omitted it, fold ``journal`` with the existing projection.
    """
    error = runs.get("error") if isinstance(runs, Mapping) else None
    outcome = runs.get("outcome") if isinstance(runs, Mapping) else None
    finish_reason = runs.get("finish_reason") if isinstance(runs, Mapping) else None
    entries: list[dict[str, Any]] = [e for e in (journal or ()) if isinstance(e, dict)]
    if (not isinstance(error, Mapping) or outcome is None or finish_reason is None) and entries:
        from agentcore.runtime.journal import runs_from_entries

        folded = runs_from_entries(entries)
        if isinstance(folded, Mapping):
            if not isinstance(error, Mapping):
                error = folded.get("error")
            if outcome is None:
                outcome = folded.get("outcome")
            if finish_reason is None:
                finish_reason = folded.get("finish_reason")
    return {
        "message_id": message_id,
        "content": content if isinstance(content, str) else "",
        "error": error if isinstance(error, Mapping) else None,
        "outcome": outcome,
        "finish_reason": finish_reason,
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "reasoning_tokens": int(reasoning_tokens or 0),
        "cache_hit_tokens": int(cache_hit_tokens or 0),
        "cache_miss_tokens": int(cache_miss_tokens or 0),
        "journal_entries": entries,
    }


async def maybe_discard_zero_output_outbox(
    outbox: Any,
    *,
    conversation_id: str,
    user_message_id: str,
    result: Mapping[str, Any] | None,
    user_created_this_send: bool,
) -> bool:
    """Drop the local outbox file when Class B holds so write-back cannot resurrect.

    Caller must skip ``finalize`` when this returns True. Same predicate as
    :func:`maybe_delete_zero_output_send` — no second judgment.
    """
    if not should_delete_zero_output_send_result(
        result, user_created_this_send=user_created_this_send
    ):
        return False
    umid = str(user_message_id or "").strip()
    if umid:
        discard = getattr(outbox, "discard", None)
        if callable(discard):
            await discard(umid)
    logger.info(
        "chat.zero_output_send_deleted",
        conversation_id=conversation_id,
        message_id=str((result or {}).get("message_id") or ""),
        user_message_id=umid,
        error_code=error_code_from_turn_result(result),
    )
    return True


async def maybe_delete_zero_output_send(
    *,
    conversation_id: str,
    user_message_id: str,
    result: Mapping[str, Any] | None,
    user_created_this_send: bool,
) -> bool:
    """Hard-delete assistant then user in one transaction when Class B holds.

    Both rows stay if either delete fails. Leaves ``cost_events``.
    ``user_created_this_send`` must be True (stream_chat this-send create only)
    — regenerate / continue / workflow pass False or skip.
    """
    if not should_delete_zero_output_send_result(
        result, user_created_this_send=user_created_this_send
    ):
        return False
    assistant_id = str((result or {}).get("message_id") or "").strip()
    user_id = str(user_message_id or "").strip()
    if not assistant_id or not user_id:
        return False
    try:
        await delete_assistant_and_paired_user(
            conversation_id=conversation_id,
            assistant_message_id=assistant_id,
            user_message_id=user_id,
        )
    except Exception:
        logger.exception(
            "chat.zero_output_send_delete_failed",
            conversation_id=conversation_id,
            message_id=assistant_id,
            user_message_id=user_id,
        )
        return False
    logger.info(
        "chat.zero_output_send_deleted",
        conversation_id=conversation_id,
        message_id=assistant_id,
        user_message_id=user_id,
        error_code=error_code_from_turn_result(result),
    )
    return True


async def delete_assistant_and_paired_user(
    *,
    conversation_id: str,
    assistant_message_id: str,
    user_message_id: str,
) -> None:
    """Hard-delete assistant then user in one transaction.

    Both rows stay if either delete fails. Leaves ``cost_events``. No Class B
    predicate — callers decide whether the pair should go (zero-output send vs
    explicit local-turn abort of a still-running placeholder).
    """
    async with async_session_factory() as session:
        repo = MessageRepository(session)
        await repo.delete_by_id(
            assistant_message_id, conversation_id=conversation_id, commit=False
        )
        await repo.delete_by_id(
            user_message_id, conversation_id=conversation_id, commit=False
        )
        await session.commit()
