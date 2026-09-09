"""Forced finalization when governance trips or rounds exhaust."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.llm.model_selection import SelectedCall, build_selected_request
from agentcore.llm.profiles import ProfileParams
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.protocol import LLMMessage, TokenUsage, ToolCall
from agentcore.runtime.facts import NoteFact, record_turn_fact
from agentcore.tools.registry import ToolRegistry

from .constants import FINALIZE_INSTRUCTION, FINALIZE_INSTRUCTION_FILES
from .governance import (
    finalize_allows_persist,
    finalize_tool_allowlist,
    resolve_finalize_coordination_tools,
)
from .segments import deliverable_continuity_instruction, join_segments
from .stream import stream_llm_round

logger = get_logger(__name__)


class FinalizeWallTimeoutError(TimeoutError):
    """Absolute wall clock expired while streaming a force-finalize LLM round."""


@dataclass(frozen=True)
class FinalizeRoundResult:
    """Outcome of one forced-finalize LLM round."""

    kind: Literal["answer", "coordination_tools", "empty"]
    content: str
    reasoning: str
    usage: TokenUsage | None
    tool_calls: list[ToolCall] | None = None


def _record_note(*, content: str, reason: str, run_id: str) -> None:
    record_turn_fact(
        NoteFact(
            role="user",
            content=content,
            reason=reason,
            run_id=run_id,
        ).to_fact()
    )


def _inject_finalize_instructions(
    messages: list[LLMMessage],
    *,
    run_id: str,
    prior_deliverable: str = "",
    persist: bool = False,
    outstanding_tool_failures: list | None = None,
    ceiling_reason: str = "",
) -> None:
    """Inject continuity (when prior交付 exists) then the standard finalize steer."""
    if outstanding_tool_failures:
        from agentcore.runtime.tool_failures import sync_tool_failure_constraint_in_system

        sync_tool_failure_constraint_in_system(messages, outstanding_tool_failures)
    prior = prior_deliverable.strip()
    if prior:
        continuity = deliverable_continuity_instruction(prior_deliverable=prior)
        messages.append(LLMMessage(role="user", content=continuity))
        _record_note(content=continuity, reason="continuity", run_id=run_id)
    if ceiling_reason:
        from agentcore.runtime.closing_posture import ceiling_honesty_steer

        honesty = ceiling_honesty_steer(reason=ceiling_reason)
        if honesty:
            messages.append(LLMMessage(role="user", content=honesty))
            _record_note(content=honesty, reason="ceiling_honesty", run_id=run_id)
    instruction = FINALIZE_INSTRUCTION_FILES if persist else FINALIZE_INSTRUCTION
    messages.append(LLMMessage(role="user", content=instruction))
    _record_note(content=instruction, reason="finalize", run_id=run_id)


async def run_finalize_round(
    *,
    messages: list[LLMMessage],
    llm: OpenAICompatibleProvider,
    profile: ProfileParams,
    active_model: str,
    tools: ToolRegistry,
    allowed_tool_names: list[str] | None,
    disabled_tools: set[str],
    emit_content: Callable[[str], None],
    emit_reasoning: Callable[[str], None],
    run_id: str = "",
    hard_tool_free: bool = False,
    inject_instruction: bool = True,
    on_reset: Callable[[str], None] | None = None,
    prior_deliverable: str = "",
    outstanding_tool_failures: list | None = None,
    files_expected: bool = False,
    expects_landing: bool = False,
    ceiling_reason: str = "",
    workspace_channel_dead: bool = False,
) -> FinalizeRoundResult:
    """One finalize LLM round: coordination (+ persist when files), or tool-free."""
    persist = finalize_allows_persist(
        tools,
        allowed_tool_names,
        files_expected=files_expected,
        expects_landing=expects_landing,
        workspace_channel_dead=workspace_channel_dead,
    )
    if inject_instruction:
        _inject_finalize_instructions(
            messages,
            run_id=run_id,
            prior_deliverable=prior_deliverable,
            persist=persist,
            outstanding_tool_failures=outstanding_tool_failures,
            ceiling_reason=ceiling_reason,
        )

    if hard_tool_free:
        tool_defs = None
        tool_choice = "none"
    else:
        from agentcore.runtime.resolve.ceo_surface import (
            ensure_coordination_surface_before_llm,
        )

        ensure_coordination_surface_before_llm(tools)
        tool_defs = resolve_finalize_coordination_tools(
            tools,
            allowed_tool_names,
            disabled_tools,
            files_expected=files_expected,
            expects_landing=expects_landing,
            workspace_channel_dead=workspace_channel_dead,
        )
        tool_choice = "auto" if tool_defs else "none"

    request = build_selected_request(
        SelectedCall(model=active_model, profile=profile),
        messages,
        tools=tool_defs,
        tool_choice=tool_choice,
    )
    from agentcore.runtime.runs.timeout_hard import mark_llm_inflight

    wall_s = float(settings.engine_force_finalize_wall_seconds or 0.0)
    mark_llm_inflight(run_id, True)
    try:
        try:
            async with asyncio.timeout(wall_s if wall_s > 0 else None):
                streamed = await stream_llm_round(
                    llm, request, emit_content, emit_reasoning, on_reset=on_reset
                )
        except TimeoutError as e:
            # Absolute wall (not idle): salvage via caller's except → prior deliverable.
            logger.warning(
                "engine.force_finalize_wall_timeout",
                run_id=run_id,
                wall_s=wall_s,
                hard_tool_free=hard_tool_free,
            )
            raise FinalizeWallTimeoutError(
                f"force_finalize wall clock exceeded ({wall_s}s)"
            ) from e
    finally:
        mark_llm_inflight(run_id, False)
    content = streamed.content
    reasoning = streamed.reasoning
    tool_calls = streamed.tool_calls
    usage = streamed.usage

    if hard_tool_free and tool_calls:
        tool_calls = None

    if tool_calls:
        allow = finalize_tool_allowlist(persist=persist)
        allowed = [tc for tc in tool_calls if tc.function.name in allow]
        if allowed:
            return FinalizeRoundResult(
                kind="coordination_tools",
                content=content,
                reasoning=reasoning,
                usage=usage,
                tool_calls=allowed,
            )
        if content.strip():
            return FinalizeRoundResult(
                kind="answer",
                content=content,
                reasoning=reasoning,
                usage=usage,
            )
        return FinalizeRoundResult(
            kind="empty",
            content=content,
            reasoning=reasoning,
            usage=usage,
        )
    if content.strip():
        return FinalizeRoundResult(
            kind="answer",
            content=content,
            reasoning=reasoning,
            usage=usage,
        )
    return FinalizeRoundResult(
        kind="empty",
        content=content,
        reasoning=reasoning,
        usage=usage,
    )


async def force_finalize(
    *,
    messages: list[LLMMessage],
    llm: OpenAICompatibleProvider,
    profile: ProfileParams,
    active_model: str,
    tools: ToolRegistry,
    allowed_tool_names: list[str] | None,
    disabled_tools: set[str],
    emit_content: Callable[[str], None],
    emit_reasoning: Callable[[str], None],
    final_content: str,
    final_reasoning: str,
    total_usage: TokenUsage,
    rounds: int,
    reason: str,
    run_id: str = "",
    on_reset: Callable[[str], None] | None = None,
    outstanding_tool_failures: list | None = None,
    files_expected: bool = False,
    expects_landing: bool = False,
    workspace_channel_dead: bool = False,
) -> tuple[str, str, TokenUsage, int, FinalizeRoundResult | None]:
    """Attempt a coordination-tool finalize round, then fall back to tool-free.

    Returns ``(content, reasoning, usage, rounds, coordination_result)``.
    When ``coordination_result.kind == "coordination_tools"``, the caller must execute
    those tools and continue the loop instead of ending the turn.

    Empty inventory (零正文 ∧ 零落盘 ∧ 无合格 brief ∧ 无工具结果) skips meaningless
    LLM salvage — wall-clock semantics stay absolute; callers hard-fail the empty
    product. Non-empty tool results count as salvage inventory (one soft/hard round).
    """
    from agentcore.runtime.runs.contract import (
        debrief_meets_minimum,
        should_attempt_force_finalize_salvage,
    )
    from agentcore.runtime.runs.serialize import (
        debrief_from_transcript,
        files_touched_from_transcript,
    )

    prior_brief = debrief_from_transcript(messages)
    prior_files = files_touched_from_transcript(messages)
    if not should_attempt_force_finalize_salvage(
        final_content, prior_files, prior_brief, messages=messages
    ):
        # No half-product or tool inventory — skip soft/hard LLM rounds.
        logger.info(
            "engine.force_finalize_skipped_empty",
            reason=reason,
            run_id=run_id,
            had_author_brief=debrief_meets_minimum(prior_brief),
        )
        body = (final_content or "").strip()
        if not body:
            from agentcore.runtime.turn.interrupt import empty_close_user_visible

            body = empty_close_user_visible(reason)
            if body:
                emit_content(body)
        return body, final_reasoning, total_usage, rounds, None

    try:
        soft = await run_finalize_round(
            messages=messages,
            llm=llm,
            profile=profile,
            active_model=active_model,
            tools=tools,
            allowed_tool_names=allowed_tool_names,
            disabled_tools=disabled_tools,
            emit_content=emit_content,
            emit_reasoning=emit_reasoning,
            run_id=run_id,
            hard_tool_free=False,
            on_reset=on_reset,
            prior_deliverable=final_content,
            outstanding_tool_failures=outstanding_tool_failures,
            files_expected=files_expected,
            expects_landing=expects_landing,
            ceiling_reason=reason,
            workspace_channel_dead=workspace_channel_dead,
        )
    except Exception as e:
        logger.error("engine.force_finalize_failed", reason=reason, error=str(e))
        return final_content, final_reasoning, total_usage, rounds, None

    if soft.kind == "coordination_tools":
        if soft.usage:
            total_usage = total_usage + soft.usage
        return final_content, final_reasoning, total_usage, rounds, soft

    if soft.kind == "answer":
        if soft.usage:
            total_usage = total_usage + soft.usage
        combined_content = join_segments(final_content, soft.content)
        combined_reasoning = (
            f"{final_reasoning}{soft.reasoning}" if final_reasoning else soft.reasoning
        )
        return combined_content, combined_reasoning, total_usage, rounds, None

    # Empty soft round → hard tool-free fallback (instruction already injected once).
    try:
        hard = await run_finalize_round(
            messages=messages,
            llm=llm,
            profile=profile,
            active_model=active_model,
            tools=tools,
            allowed_tool_names=allowed_tool_names,
            disabled_tools=disabled_tools,
            emit_content=emit_content,
            emit_reasoning=emit_reasoning,
            run_id=run_id,
            hard_tool_free=True,
            inject_instruction=False,
            on_reset=on_reset,
            files_expected=files_expected,
            expects_landing=expects_landing,
            ceiling_reason=reason,
            workspace_channel_dead=workspace_channel_dead,
        )
    except Exception as e:
        logger.error("engine.force_finalize_hard_failed", reason=reason, error=str(e))
        return final_content, final_reasoning, total_usage, rounds, None

    if hard.usage:
        total_usage = total_usage + hard.usage
    combined_content = join_segments(final_content, hard.content)
    combined_reasoning = (
        f"{final_reasoning}{hard.reasoning}" if final_reasoning else hard.reasoning
    )
    return combined_content, combined_reasoning, total_usage, rounds, None
