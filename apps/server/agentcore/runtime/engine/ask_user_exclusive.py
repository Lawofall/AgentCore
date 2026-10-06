"""A pause-to-ask round may issue at most one ``ask_user`` call.

Multiple questions belong on that one card (``questions[]``). Other tools in the
same model round run first, then the single ask freezes. Two asks in one round
must not freeze — the batch fails closed and the CEO retries. Resume then has
one open ask with one card; leftover / sibling / skip handling does not belong
on that path.
"""

from __future__ import annotations

from agentcore.core.logging import get_logger
from agentcore.llm.provider.protocol import ToolCall
from agentcore.runtime.engine.tool_exec_args import _failed_tool_message
from agentcore.runtime.engine.tool_failure_face import tool_failure_fields
from agentcore.runtime.engine.tool_protocol_sanitize import sanitize_tool_name
from agentcore.runtime.events import EventSink, tool_use_end, tool_use_start
from agentcore.runtime.loop_controller import (
    ERROR_CLASS_VALIDATION,
    ToolAttempt,
    fingerprint_tool_call,
)

logger = get_logger(__name__)

ASK_USER_NOT_EXCLUSIVE_CODE = "ask_user_not_exclusive"

ASK_USER_NOT_EXCLUSIVE_MSG = (
    "同一轮只能调用一次 ask_user（多题写在 questions 里）。本批未执行。"
)


def _is_ask_user_call(tc: ToolCall) -> bool:
    return sanitize_tool_name(tc.function.name or "") == "ask_user"


def exclusive_ask_user_violation(tool_calls: list[ToolCall]) -> bool:
    """True when this round issued more than one asking card."""
    return sum(1 for tc in tool_calls if _is_ask_user_call(tc)) > 1


def reject_non_exclusive_ask_user_batch(
    tool_calls: list[ToolCall],
    *,
    sink: EventSink,
    event_run_id: str,
) -> list[tuple]:
    """Fail every call in the batch without executing. No SUSPEND terminal."""
    n_ask = sum(1 for tc in tool_calls if _is_ask_user_call(tc))
    logger.info(
        "ask_user.not_exclusive",
        n_calls=len(tool_calls),
        n_ask=n_ask,
    )
    quads: list[tuple] = []
    for tc in tool_calls:
        name = tc.function.name or ""
        sink.emit(tool_use_start(tc.id, name, {}, run_id=event_run_id))
        sink.emit(
            tool_use_end(
                tc.id,
                name,
                success=False,
                output=ASK_USER_NOT_EXCLUSIVE_MSG,
                failure=tool_failure_fields(code=ASK_USER_NOT_EXCLUSIVE_CODE),
                run_id=event_run_id,
            )
        )
        logger.info(
            "tool.execute_end",
            tool=name,
            status=ASK_USER_NOT_EXCLUSIVE_CODE,
            duration_ms=0,
        )
        quads.append(
            (
                _failed_tool_message(tc.id, ASK_USER_NOT_EXCLUSIVE_MSG, status="validation"),
                None,
                ToolAttempt(
                    fingerprint_tool_call(name, tc.function.arguments or ""),
                    name,
                    success=False,
                    parse_failure=True,
                    error_summary=ASK_USER_NOT_EXCLUSIVE_MSG,
                    meta={"error_class": ERROR_CLASS_VALIDATION},
                ),
                [],
            )
        )
    return quads
