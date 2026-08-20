"""Sidecar local-turn write-back — ``CloudStore.finalize(mode="local")``."""

from collections.abc import Sequence
from typing import Any

from agentcore.conversation.store import get_cloud_store
from agentcore.conversation.zero_output_rollback import (
    error_code_from_turn_result,
    result_from_local_turn_writeback,
    should_delete_zero_output_send_result,
)
from agentcore.core.log_context import log_context
from agentcore.core.logging import get_logger
from agentcore.llm.resolve import LLMCredentials

logger = get_logger(__name__)


async def record_local_turn(
    *,
    conversation_id: str,
    user_id: str,
    user_message: str,
    assistant_content: str,
    assistant_reasoning: str | None = None,
    citations: list[dict] | None = None,
    evidence_ledger: list[dict] | None = None,
    runs: dict | None = None,
    journal: list[dict] | None = None,
    tool_failures: Sequence[dict[str, Any]] | None = None,
    user_message_id: str,
    message_id: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    reasoning_tokens: int = 0,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
    rounds: int = 0,
    trace_id: str,
    finish_reason: str | None = None,
    llm_credentials: LLMCredentials | None = None,
    origin: str | None = None,
    execution_id: str | None = None,
    harvest_kind: str | None = None,
    agent_mentions: list[dict] | None = None,
) -> dict:
    """Persist a turn that ran on the user's machine via the sidecar.

    Routes through ``CloudStore.finalize(mode="local")`` so D7 merge rules apply
    (content monotonic, status gate, journal seq upsert, no early-return).
    Spend is NOT recorded here — metered at ``/v1/inference``.
    ``tool_failures`` is observability-only (logged, not persisted to message tables).
    """
    with log_context(trace_id=trace_id, conversation_id=conversation_id, user_id=user_id):
        failures = [f for f in (tool_failures or ()) if isinstance(f, dict)]
        if failures:
            codes = [str(f.get("code") or "other") for f in failures]
            tools = [str(f.get("tool") or "") for f in failures]
            # Wire already carries ``message`` (≤200); log it so the ``other`` bucket
            # can be split (timeout / sandbox / fence / HTTP) without a client change.
            messages = [str(f.get("message") or "")[:200] for f in failures]
            logger.info(
                "chat.local_turn_tool_failures",
                conversation_id=conversation_id,
                message_id=message_id,
                count=len(failures),
                codes=codes,
                tools=tools,
                messages=messages,
            )
        # Harvest / origin write-backs are not this-send creates (cloud continue
        # / workflow pass False). Ordinary startTurn write-back is this-send.
        user_created_this_send = not (
            (origin or "").strip() or (harvest_kind or "").strip()
        )
        result_like = result_from_local_turn_writeback(
            message_id=message_id,
            content=assistant_content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
            journal=journal,
            runs=runs,
        )
        if should_delete_zero_output_send_result(
            result_like, user_created_this_send=user_created_this_send
        ):
            logger.info(
                "chat.zero_output_send_deleted",
                conversation_id=conversation_id,
                message_id=str(message_id or ""),
                user_message_id=user_message_id,
                error_code=error_code_from_turn_result(result_like),
            )
            return {
                "user_message_id": user_message_id,
                "assistant_message_id": None,
                "title": None,
                "followups": None,
                "noop": True,
            }
        result = await get_cloud_store().finalize(
            mode="local",
            conversation_id=conversation_id,
            user_id=user_id,
            user_message=user_message,
            assistant_content=assistant_content,
            assistant_reasoning=assistant_reasoning,
            citations=citations,
            evidence_ledger=evidence_ledger,
            runs=runs,
            journal=journal,
            user_message_id=user_message_id,
            message_id=message_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
            rounds=rounds,
            trace_id=trace_id,
            finish_reason=finish_reason,
            llm_credentials=llm_credentials,
            origin=origin,
            execution_id=execution_id,
            harvest_kind=harvest_kind,
            agent_mentions=agent_mentions,
        )
        assert result is not None
        return result
