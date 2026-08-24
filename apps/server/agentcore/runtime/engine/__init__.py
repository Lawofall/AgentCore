"""ReAct engine: turn control, LLM calls, tool execution.

Single-agent ReAct loop for MVP:
  1. Build messages (system + history + user)
  2. Call LLM (streaming)
  3. If tool_calls → execute tools → append results → loop
  4. If text response → done

All intermediate events are emitted to an EventSink for SSE delivery.
"""

from .segments import deliverable_continuity_instruction, join_segments
from .timeout import resolve_tool_timeout

__all__ = [
    "ReactLoopOut",
    "deliverable_continuity_instruction",
    "join_segments",
    "react_loop",
    "resolve_tool_timeout",
]


def __getattr__(name: str):
    # Lazy: ``loop`` imports ``ApprovalGate`` → ``events`` while ``events`` may still
    # be initializing (``sink_process`` imports ``tool_channel_redirect`` from this pkg).
    if name == "ReactLoopOut":
        from .loop import ReactLoopOut

        return ReactLoopOut
    if name == "react_loop":
        from .loop import react_loop

        return react_loop
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
