"""Captain wrap-up: keep outstanding-tool-failure constraint in the system prompt.

Debate-commitment and cite_write_review audit gates (content_reset + [系统提示])
are withdrawn — playbook nodes and the model own those paths.
"""

from __future__ import annotations

from collections.abc import Callable

from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.loop_controller import LoopController

from .directive import LoopDirective, Return
from .outcome import RoundOutcome


def maybe_soft_gate_no_tool_return(
    *,
    directive: LoopDirective,
    outcome: RoundOutcome,
    controller: LoopController,
    messages: list[LLMMessage],
    role: str,
    round_idx: int,
    run_id: str,
    content_before_round: str,
    emit_reset: Callable[[str], None],
) -> tuple[LoopDirective, str | None]:
    """Sync tool-failure constraint on wrap-up. Never discards a captain draft.

    ``role`` / ``round_idx`` / ``run_id`` / ``content_before_round`` /
    ``emit_reset`` kept so call sites stay stable.
    """
    del role, round_idx, run_id, content_before_round, emit_reset
    if not isinstance(directive, Return) or not outcome.content:
        return directive, None

    from agentcore.runtime.tool_failures import (
        sync_tool_failure_constraint_in_system,
        team_outstanding_constraint_from_messages,
    )

    outstanding = controller.outstanding_tool_failures()
    team_text = team_outstanding_constraint_from_messages(messages)
    sync_tool_failure_constraint_in_system(
        messages,
        outstanding,
        constraint_text=None if outstanding else team_text,
    )
    return directive, None
