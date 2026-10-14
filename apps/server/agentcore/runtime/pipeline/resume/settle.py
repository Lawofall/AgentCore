"""Resume settle helpers — transcript splice + façade over ``recover_turn``.

``SettledSuspension`` lives in :mod:`agentcore.runtime.recover` (avoids a
pipeline↔recover import cycle); re-exported here for historical imports.
"""

from __future__ import annotations

from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.events import EventSink, tool_use_end
from agentcore.runtime.facts import ToolCallFact, record_turn_fact
from agentcore.runtime.recover import SettledSuspension
from agentcore.runtime.suspension import TurnSuspension
from agentcore.runtime.turn.state import TurnState
from agentcore.tools.builtin.debate import DebateTool
from agentcore.tools.builtin.graph import GraphTool

__all__ = [
    "SettledSuspension",
    "append_resumed_tool_results",
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
    (``delegate`` / ``debate`` / ``ask_user`` — the pause
    happened inside it). Append that call's settled result so the loop continues
    from a valid assistant-tool_call → tool-result pair.

    Asking pauses are exclusive (one ``ask_user`` per model round); leftover
    unpaired calls after this append are a resume error, not another card.
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
    folds a closed assistant→tool pair via ``window_from_journal``.
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
    from agentcore.runtime.context.working_set import file_working_set_digest

    record_turn_fact(
        ToolCallFact(
            run_id=run_id,
            tool_call_id=target,
            name=name,
            arguments=args,
            result=output,
            success=True,
            working_set_digest=file_working_set_digest(
                name=name, arguments=args, result=output, success=True
            ),
        ).to_fact()
    )
    sink.emit(tool_use_end(target, name, success=True, output=output, run_id=run_id))


async def settle_resumed_suspension(
    suspension: TurnSuspension,
    *,
    decision: CheckpointDecision,
    note: str,
    selected: list[str],
    sink: EventSink,
    delegate_tool: GraphTool,
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
