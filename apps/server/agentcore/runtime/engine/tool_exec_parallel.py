"""Parallel tool execution + same-round file_read coalesce for one ReAct round."""

from __future__ import annotations

import asyncio
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolEffect
from agentcore.llm.provider.protocol import LLMMessage, ToolCall, llm_content_text
from agentcore.runtime.approvals import ApprovalGate
from agentcore.runtime.engine.tool_call_fact_code import (
    tool_call_fact_code,
    tool_call_fact_cross_turn_retry,
)
from agentcore.runtime.events import EventSink
from agentcore.runtime.evidence_ledger import EvidenceLedgerCore
from agentcore.runtime.facts import ToolCallFact, record_turn_fact
from agentcore.runtime.loop_controller import ToolAttempt
from agentcore.tools.protocol import ToolContext, ToolResult
from agentcore.tools.registry import ToolRegistry

from .constants import MAX_PARALLEL_TOOLS
from .tool_exec_call import ToolCallQuad, run_one_tool
from .tool_exec_citations import apply_round_citation_side_effects
from .tool_protocol_sanitize import sanitize_tool_name

logger = get_logger(__name__)


async def execute_tools(
    tool_calls: list[ToolCall],
    registry: ToolRegistry,
    context: ToolContext,
    sink: EventSink,
    *,
    approval_gate: ApprovalGate | None,
    citation_sink: list[dict[str, Any]] | None = None,
    annotate_citations: bool = True,
    turn_evidence_ledger: EvidenceLedgerCore | None = None,
    ledger_registrant: str = "",
    run_id: str = "",
    role: str = "",
    allowed_tool_names: list[str] | None = None,
) -> tuple[list[LLMMessage], ToolResult | None, list[ToolAttempt]]:
    """Execute tool calls (parallel, capped).

    Returns ``(tool_messages, terminal, attempts)`` where ``terminal`` is the
    chosen terminal-effect ToolResult (a tool that already produced the turn's
    final answer — handoff / ask_user-stop — or a SUSPEND pause) or ``None``, and
    ``attempts`` carries the per-call fingerprint + success used by convergence
    governance to detect mechanical loops. When multiple terminals appear in one
    round, SUSPEND wins (durable pause must not lose to call-order luck) and a
    warning is logged; normal agent toolsets never hold both classes.

    ``approval_gate`` has no default on purpose: this is the last hop before the
    approval chokepoint, so「忘了传」must be a ``TypeError`` rather than a silent
    ungate. Pass the turn's gate, or an explicit ``None`` to declare that this path
    has no user to ask — approval-requiring calls then fail closed.

    ``allowed_tool_names`` is the run's least-privilege allow-list (``None`` = no
    restriction). Schema offering already filters to this list; this parameter
    **also enforces at execute** so a model cannot land side effects by calling a
    registered tool that was never granted (e.g. debater ``file_write``).

    When ``citation_sink`` is provided, web sources surfaced by successful research
    tools are merged into it (arrival order, deduped, capped) — **池语义不变**.
    When ``turn_evidence_ledger`` is set, the same hits are also registered into the
    turn-shared ledger (except ``blocked``); tool messages get ``#rN=url`` stable-id
    annotation for both CEO and workers (引用即出处 P1). Without a ledger,
    ``annotate_citations`` keeps the legacy ``[n]=url`` CEO path.

    Display/trace split for ``role == "captain"``: SSE tool events omit ``run_id``
    so the UI renders them as turn-level inline steps (same as captain
    ``content_delta``); ``ToolCallFact`` and circuit-breaker audit still keep
    ``run_id`` for §8.3 fold / audit. Workers keep ``run_id`` on SSE too.

    Same-round parallel ``file_read`` calls that share a normalized path execute
    the underlying read once; sibling tool_calls receive fan-out clones (one
    count bump when the shared result is a full read).
    """
    # Captain self-tools: inline timeline (no run_id on wire); facts/audit keep run_id.
    event_run_id = "" if role == "captain" else run_id
    allowed_set = None if allowed_tool_names is None else frozenset(allowed_tool_names)
    # Same-round file_read path coalesce (leader Future → fan-out clones).
    file_read_inflight: dict[str, asyncio.Future[ToolResult]] = {}

    async def _run_one(tc: ToolCall) -> ToolCallQuad:
        return await run_one_tool(
            tc,
            registry=registry,
            context=context,
            sink=sink,
            event_run_id=event_run_id,
            run_id=run_id,
            role=role,
            allowed_set=allowed_set,
            approval_gate=approval_gate,
            file_read_inflight=file_read_inflight,
        )

    sem = asyncio.Semaphore(MAX_PARALLEL_TOOLS)

    async def _bounded(tc: ToolCall) -> ToolCallQuad:
        async with sem:
            return await _run_one(tc)

    # Same-batch handoff after writes: ``landed_artifact_kinds`` is a shared dict, but
    # parallel gather can still let handoff observe an empty stamp if it races ahead of
    # file_write/file_append. Run non-handoff tools first (still parallel among
    # themselves), then handoff — message order stays call-list order below.
    def _is_handoff_call(tc: ToolCall) -> bool:
        return sanitize_tool_name(tc.function.name or "") == "handoff"

    has_handoff = any(_is_handoff_call(tc) for tc in tool_calls)
    has_non_handoff = any(not _is_handoff_call(tc) for tc in tool_calls)
    if has_handoff and has_non_handoff:
        by_id: dict[str, ToolCallQuad] = {}
        first = [tc for tc in tool_calls if not _is_handoff_call(tc)]
        second = [tc for tc in tool_calls if _is_handoff_call(tc)]
        for tc, quad in zip(
            first, await asyncio.gather(*[_bounded(tc) for tc in first]), strict=True
        ):
            by_id[tc.id] = quad
        for tc, quad in zip(
            second, await asyncio.gather(*[_bounded(tc) for tc in second]), strict=True
        ):
            by_id[tc.id] = quad
        quads = [by_id[tc.id] for tc in tool_calls]
    else:
        quads = await asyncio.gather(*[_bounded(tc) for tc in tool_calls])

    # 挂起即收口 (②): a SUSPEND terminal leaves its call PENDING — the suspended tool_call
    # gets NO result message AND NO §8.3 tool_call fact (recorded below), so the resumed
    # window ends exactly at the assistant (the fold reads the missing result as「still
    # pending」). This reproduces the shape the old blocking pause produced by never
    # returning from ``execute``. INTERACT / HANDOFF differ: they DID produce the turn's
    # answer, so they keep their tool message + fact like any completed call.
    def _suspends(t: ToolResult | None) -> bool:
        return t is not None and t.effect is ToolEffect.SUSPEND

    messages = [m for m, t, _, _ in quads if not _suspends(t)]
    # Terminal selection: prefer SUSPEND over HANDOFF/INTERACT when a round somehow
    # yields multiple terminals (defense — normal agent toolsets never hold both).
    # A durable pause must not be overridden by call-order luck with a non-SUSPEND
    # terminal in the same gather batch. Warn when more than one terminal appears.
    terminals = [t for _, t, _, _ in quads if t is not None]
    if len(terminals) > 1:
        logger.warning(
            "tool.multi_terminal",
            count=len(terminals),
            effects=[t.effect.value for t in terminals],
        )
    terminal = next((t for t in terminals if t.effect is ToolEffect.SUSPEND), None)
    if terminal is None and terminals:
        terminal = terminals[0]
    attempts = [a for _, _, a, _ in quads]

    # Merge web sources into mid-turn sink (deterministic call order) for pause /
    # legacy ``[n]``；台账登记 ``#rN`` 并 annotate。P2：用户可见卡由 settle 按
    # ``cited_ids`` 投影，不在此发射 ``citations_event``。
    await apply_round_citation_side_effects(
        quads,
        sink=sink,
        citation_sink=citation_sink,
        turn_evidence_ledger=turn_evidence_ledger,
        ledger_registrant=ledger_registrant,
        annotate_citations=annotate_citations,
    )

    # 执行级事件溯源 (§8.3 / Phase 2 边界①): record each completed call's FINAL
    # model-facing result as a tool_call fact — captured HERE, after the citation
    # annotation above, so it is byte-for-byte what the next round's window carried (the
    # forwarded tool_use_end fires inside _run_one with the pre-annotation text). The
    # window fold reads tool results from these facts. ``tool_calls`` is positionally
    # aligned with ``quads`` (asyncio.gather preserves order), so zip pairs each result
    # to its issuing call. A SUSPEND call is skipped (挂起即收口 ②): recording its fact
    # would inject a phantom result into the resumed window — matching the old blocking
    # pause, where ``gather`` never returned so no fact was recorded for the parked call.
    from agentcore.runtime.context.working_set import file_working_set_digest

    for tc, (message, terminal_q, attempt, _citations) in zip(tool_calls, quads, strict=False):
        if _suspends(terminal_q):
            continue
        name = tc.function.name
        arguments = tc.function.arguments or ""
        result = llm_content_text(message.content)
        record_turn_fact(
            ToolCallFact(
                run_id=run_id,
                tool_call_id=message.tool_call_id or tc.id,
                name=name,
                arguments=arguments,
                result=result,
                success=attempt.success,
                code=tool_call_fact_code(attempt),
                cross_turn_retry=tool_call_fact_cross_turn_retry(attempt),
                working_set_digest=file_working_set_digest(
                    name=name,
                    arguments=arguments,
                    result=result,
                    success=attempt.success,
                ),
            ).to_fact()
        )

    # CEO 图内处置插话：本步成功用过 update_synthesis / delegate / cancel_worker
    # → 统一清 pending 并标 addressed（不在各工具实现里逐个补）。
    if role == "captain" and attempts:
        from agentcore.runtime.coordination.interjections import (
            address_interjections_after_ceo_tools,
        )

        address_interjections_after_ceo_tools(
            role=role,
            attempts=attempts,
            sink=sink,
        )

    return messages, terminal, attempts
