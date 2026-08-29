"""Resume settle helpers — transcript splice + façade over ``recover_turn``.

``SettledSuspension`` lives in :mod:`agentcore.runtime.recover` (avoids a
pipeline↔recover import cycle); re-exported here for historical imports.
"""

from __future__ import annotations

from typing import Any

from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.checkpoints import CheckpointDecision, coerce_ask_checkpoint_intent
from agentcore.runtime.events import EventSink, tool_use_end
from agentcore.runtime.facts import ToolCallFact, record_turn_fact
from agentcore.runtime.recover import SettledSuspension
from agentcore.runtime.suspension import AskUserSuspension, TurnSuspension
from agentcore.runtime.turn.state import TurnState
from agentcore.tools.builtin.debate import DebateTool
from agentcore.tools.builtin.delegate import DelegateTool

__all__ = [
    "SettledSuspension",
    "append_resumed_tool_results",
    "next_pending_ask_user_suspension",
    "persist_resumed_tool_results",
    "settle_resumed_suspension",
    "unclosed_tool_call_ids",
]


def unclosed_tool_call_ids(messages: list[LLMMessage]) -> list[str]:
    """Tool-call ids issued in the window that still have no matching tool result.

    Order follows the issuing assistant messages (then call order inside each).
    """
    issued: list[str] = []
    closed: set[str] = set()
    for message in messages:
        if message.role == "assistant" and message.tool_calls:
            issued.extend(tc.id for tc in message.tool_calls)
        elif message.role == "tool" and message.tool_call_id:
            closed.add(message.tool_call_id)
    return [tcid for tcid in issued if tcid not in closed]


def append_resumed_tool_results(
    messages: list[LLMMessage], tool_call_id: str, output: str
) -> None:
    """Close the settled tool-call in the rebuilt CEO transcript (结构化挂起 2b).

    The transcript ends with the assistant message that issued the suspended call
    (``delegate`` / ``debate`` for kickoff, ``ask_user`` for ask_user — the pause
    happened inside it). Append that call's settled result so the loop continues
    from a valid assistant-tool_call → tool-result pair.

    Same-batch siblings stay open when they have no result yet (parallel
    ``ask_user`` cards that also SUSPENDED). Closing them with a skip placeholder
    discarded their cards; the resume path re-pauses on the next pending sibling
    instead of feeding the CEO an incomplete pair.
    """
    target = tool_call_id or ""
    last = messages[-1] if messages else None
    if last is not None and last.role == "assistant" and last.tool_calls:
        target = target or last.tool_calls[0].id
    if not target:
        return
    messages.append(LLMMessage(role="tool", content=output, tool_call_id=target))


def persist_resumed_tool_results(
    transcript: list[LLMMessage],
    *,
    tool_call_id: str,
    output: str,
    run_id: str,
    sink: EventSink,
    tool_name: str = "",
) -> None:
    """Persist the settled tool result into the turn journal after resume settle.

    Pause deliberately skips ``ToolCallFact`` / ``tool_use_end`` (no phantom result).
    Once the user answers, the result is real — record it so a later same-turn re-pause
    folds a closed assistant→tool pair via ``window_from_journal``. Sibling calls in
    the same assistant message are left pending (no skip placeholder).
    """
    last = transcript[-1] if transcript else None
    target = tool_call_id or ""
    name = tool_name or "tool"
    args = ""
    if last is not None and last.role == "assistant" and last.tool_calls:
        target = target or last.tool_calls[0].id
        matched = next((tc for tc in last.tool_calls if tc.id == target), None)
        if matched is not None:
            name = matched.function.name or name
            args = matched.function.arguments or ""
    if not target:
        return
    record_turn_fact(
        ToolCallFact(
            run_id=run_id,
            tool_call_id=target,
            name=name,
            arguments=args,
            result=output,
            success=True,
        ).to_fact()
    )
    sink.emit(tool_use_end(target, name, success=True, output=output, run_id=run_id))


def next_pending_ask_user_suspension(
    suspension: TurnSuspension,
    messages: list[LLMMessage],
    journal_entries: list[dict[str, Any]],
) -> AskUserSuspension | None:
    """Next still-open same-batch ``ask_user`` card, or ``None``.

    Pairs remaining unclosed ``ask_user`` tool_calls (assistant order) with
    still-pending ``checkpoint_required`` records (journal order). Does not emit
    a new card — the original ``*_required`` is already in the stream.
    """
    from agentcore.runtime.journal.pending_interactions import fold_interactions

    open_ids = set(unclosed_tool_call_ids(messages))
    if not open_ids:
        return None
    last_assistant: LLMMessage | None = None
    for message in reversed(messages):
        if message.role == "assistant" and message.tool_calls:
            last_assistant = message
            break
    if last_assistant is None or not last_assistant.tool_calls:
        return None
    open_ask = [
        tc
        for tc in last_assistant.tool_calls
        if tc.id in open_ids and (tc.function.name or "") == "ask_user"
    ]
    if not open_ask:
        return None
    pending = [
        rec
        for rec in fold_interactions(journal_entries)
        if rec.status == "pending"
        and rec.kind == "ask_user"
        and rec.id != suspension.checkpoint_id
    ]
    if not pending:
        return None
    card = pending[0]
    tool_call = open_ask[0]
    payload = card.payload
    return AskUserSuspension(
        message_id=suspension.message_id,
        conversation_id=suspension.conversation_id,
        user_id=suspension.user_id,
        captain_run_id=suspension.captain_run_id,
        checkpoint_id=card.id,
        tool_call_id=tool_call.id,
        base_system_prompt=suspension.base_system_prompt,
        user_message=suspension.user_message,
        folder_id=suspension.folder_id,
        folder_binding_injected=suspension.folder_binding_injected,
        folder_local_root_id=suspension.folder_local_root_id,
        folder_local_subpath=suspension.folder_local_subpath,
        transcript=list(messages),
        history=list(suspension.history),
        journal_entries=list(journal_entries),
        citations=list(suspension.citations),
        consulted_memory=dict(suspension.consulted_memory or {}),
        trace_id=suspension.trace_id,
        question=str(payload.get("question") or ""),
        assumptions=list(payload.get("assumptions") or []),
        questions=list(payload.get("questions") or []),
        intent=coerce_ask_checkpoint_intent(payload.get("intent")),
        browser_login=payload.get("browser_login") is True,
    )


async def settle_resumed_suspension(
    suspension: TurnSuspension,
    *,
    decision: CheckpointDecision,
    note: str,
    selected: list[str],
    sink: EventSink,
    delegate_tool: DelegateTool,
    execution_id: str,
    debate_tool: DebateTool | None = None,
) -> SettledSuspension:
    """Façade: project via ``TurnState.from_journal``, then ``recover_turn``."""
    from agentcore.runtime.recover import recover_turn

    state = TurnState.from_journal(
        suspension.journal_entries,
        display_journal=suspension.journal,
    )
    return await recover_turn(
        state=state,
        sink=sink,
        delegate_tool=delegate_tool,
        debate_tool=debate_tool,
        execution_id=execution_id,
        suspension=suspension,
        decision=decision,
        note=note,
        selected=selected,
    )
