"""Single AGENT-node execution (contract retries, escalate, notes, salvage).

Thin facade — implementation split by axis:

* ``.setup`` — registry / identity / opening messages
* ``.loop`` — react+capture + contract decision ladder body
* ``.retry`` — light-repair / write-pass / budget skip predicates
* ``.hooks`` — visual / retrieval / citation domain hooks
* ``.terminal`` — salvage / cancel / terminal RunState

Stable imports (``execute_agent_node``, ``should_skip_contract_retry_for_budget``,
and existing test ``_`` helpers) re-export from this module.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Any

from agentcore.llm.provider.protocol import LLMMessage, TokenUsage
from agentcore.runtime.events import run_started
from agentcore.runtime.runs.executor.env import AgentExecutorEnv
from agentcore.runtime.runs.executor.hooks import (
    _stamp_retrieval_evidence_gap,
    _two_phase_citation,
)
from agentcore.runtime.runs.executor.identities import LeadSubteam
from agentcore.runtime.runs.executor.loop import run_contract_loop
from agentcore.runtime.runs.executor.retry import (
    _can_light_repair,
    _can_write_pass,
    _files_expected,
    _narrow_for_light_repair,
    _retry_token_budget,
    _wind_down_entered,
    should_skip_contract_retry_for_budget,
)
from agentcore.runtime.runs.executor.setup import prepare_agent_node
from agentcore.runtime.runs.executor.started_run_close import (
    emit_run_cancelled_if_unterminated,
)
from agentcore.runtime.runs.executor.terminal import (
    build_terminal_run_state,
    dispose_agent_node,
    handle_agent_node_cancel,
    handle_agent_node_exception,
)
from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState

# Re-exports for existing test imports (do not grow new external ``_`` callers).
__all__ = [
    "execute_agent_node",
    "should_skip_contract_retry_for_budget",
    "_stamp_retrieval_evidence_gap",
    "_narrow_for_light_repair",
    "_wind_down_entered",
    # Kept importable for in-tree callers / tests that already reach them.
    "_files_expected",
    "_retry_token_budget",
    "_can_light_repair",
    "_can_write_pass",
    "_two_phase_citation",
]


def _is_infra_hot_continue(
    completed: Mapping[str, RunState], spec: RunSpec
) -> bool:
    """True when a prior hop seeded this run_id with a transient FAILED transcript."""
    prior = completed.get(spec.run_id)
    return (
        prior is not None
        and prior.phase is RunPhase.FAILED
        and bool(prior.transcript)
        and prior.error_retryable
    )


async def execute_agent_node(
    env: AgentExecutorEnv,
    spec: RunSpec,
    completed: Mapping[str, RunState],
    agent_id: str,
) -> RunState:
    # Same run_id already started: a Wave seed-continue must not emit a second
    # run_started (fold last-write-wins would hide the live extra frame).
    if not _is_infra_hot_continue(completed, spec):
        env.sink.emit(
            run_started(
                spec.run_id,
                agent_id,
                parent_run_id=spec.parent_run_id,
                kind=spec.kind,
                replaces_run_id=spec.replaces_run_id,
            )
        )
    from agentcore.runtime.runs.run_phase_emit import emit_run_phase

    emit_run_phase(env.sink, spec.run_id, agent_id, "thinking")
    start = time.monotonic()
    # Hoisted out of the try so a hard exception can still bill what this run
    # already spent (B-deep 失败计费): ``run_usage``/``run_rounds`` accumulate the
    # completed contract-retry attempts, ``inflight`` mirrors the in-flight pass's
    # spend (filled by react_loop, read only if that pass raises), and
    # ``priced_model`` is the tier to price against once the profile resolves
    # (None before that → an early setup failure carries no usage to price).
    run_usage_box: list[TokenUsage] = [TokenUsage()]
    run_rounds_box: list[int] = [0]
    inflight: list[TokenUsage] = []
    priced_model: str | None = None
    # Hoisted so mid-flight CancelledError can salvage partial transcript (run_redirect 热续写).
    messages: list[LLMMessage] = []
    # Live draft chunks (run_output_delta) — may exist before the final assistant
    # turn is appended to ``messages``; folded into salvage on redirect cancel.
    streamed_content: list[str] = []
    # 阻塞式求决策: this worker's blocking-escalate resolutions, keyed by question, so the
    # transcript harvest below can fold the user's answer / timeout disposition into
    # ``RunState.escalations`` for CEO synthesis — driven by the structured channel below,
    # NOT by re-parsing the tool result prose (防补丁绊线, 设计 §4.7). A worker is
    # sequential, so escalates land here in call order, one at a time.
    resolutions: dict[str, dict[str, Any]] = {}
    # Escalation Gate (routing Phase 1): scheme-layer signals collected during react
    # rounds, merged into RunState.escalations alongside transcript-harvested escalate
    # tool calls.
    gate_escalations: list[dict[str, Any]] = []
    # 受监督子计划 B: a lead's nested-delegation handle (delegate + companion
    # replan on the bundle + dispose). Opening offer is delegate; replan after
    # a sub-plan exists. Hoisted so the finally can fold a sub-plan the lead
    # yielded-but-never-resumed back into the ledger before the parent absorbs
    # this child (堵漏账). Stays None for a leaf worker (no opt-in / at the
    # depth cap / no factory wired).
    lead_subteam: LeadSubteam | None = None
    product_landing_artifacts: list[str] | None = None
    tool_ctx: Any | None = None
    try:
        prepared = await prepare_agent_node(
            env,
            spec,
            completed,
            agent_id,
            messages=messages,
            resolutions=resolutions,
        )
        priced_model = prepared.priced_model
        lead_subteam = prepared.lead_subteam
        product_landing_artifacts = prepared.product_landing_artifacts
        tool_ctx = prepared.tool_ctx

        # Transient infra (rate-limit / 5xx) is the leaf's job. A 429 that
        # reaches here is already past the leaf — emit one run_failed and
        # let the layer above go partial. Do not remount the node.
        loop_result = await run_contract_loop(
            env,
            spec,
            agent_id,
            prepared,
            messages=messages,
            streamed_content=streamed_content,
            inflight=inflight,
            gate_escalations=gate_escalations,
            run_usage_box=run_usage_box,
            run_rounds_box=run_rounds_box,
        )

        duration_ms = int((time.monotonic() - start) * 1000)
        return build_terminal_run_state(
            env,
            spec,
            agent_id,
            messages=messages,
            content=loop_result.content,
            reasoning=loop_result.reasoning,
            verdict=loop_result.verdict,
            deliverable=loop_result.deliverable,
            product_landing_artifacts=loop_result.product_landing_artifacts,
            resolutions=resolutions,
            gate_escalations=gate_escalations,
            worker_citations=loop_result.worker_citations,
            priced_model=loop_result.priced_model,
            run_usage=loop_result.run_usage,
            run_rounds=loop_result.run_rounds,
            duration_ms=duration_ms,
            finish_override=loop_result.finish_override,
            cutoff_reasons=loop_result.cutoff_reasons,
            tool_failures=loop_result.tool_failures,
            write_pass_used=loop_result.write_pass_used,
            visual_rework_used=loop_result.visual_rework_used,
            received_blocks=prepared.received_blocks,
            tool_ctx=loop_result.tool_ctx,
        )
    except asyncio.CancelledError as e:
        salvaged = handle_agent_node_cancel(
            env,
            spec,
            agent_id,
            e,
            messages=messages,
            streamed_content=streamed_content,
            inflight=inflight,
            run_usage=run_usage_box[0],
            run_rounds=run_rounds_box[0],
            priced_model=priced_model,
        )
        if salvaged is not None:
            return salvaged
        raise
    except Exception as e:  # noqa: BLE001 — surface any run failure to UI/state
        return handle_agent_node_exception(
            env,
            spec,
            agent_id,
            e,
            start=start,
            messages=messages,
            inflight=inflight,
            run_usage=run_usage_box[0],
            run_rounds=run_rounds_box[0],
            priced_model=priced_model,
            product_landing_artifacts=product_landing_artifacts,
            tool_ctx=tool_ctx,
        )
    finally:
        emit_run_cancelled_if_unterminated(
            env.sink, spec.run_id, agent_id, execution_id=env.execution_id
        )
        await dispose_agent_node(spec, lead_subteam)
