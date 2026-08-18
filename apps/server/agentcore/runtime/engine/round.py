"""Single ReAct round: request projection, LLM streaming, facts, no-tool finish paths."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agentcore.config import settings
from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import LLMUpstreamError, error_fields_for
from agentcore.core.logging import get_logger
from agentcore.llm.model_selection import SelectedCall, build_selected_request
from agentcore.llm.profiles import ProfileParams
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.protocol import LLMMessage, TokenUsage
from agentcore.llm.tools_gate import TOOLS_UNAVAILABLE_RUNTIME_MESSAGE
from agentcore.runtime.citations import (
    invalid_ledger_ref_ids,
    strip_invalid_ledger_refs,
)
from agentcore.runtime.events import FinishReason
from agentcore.runtime.evidence_ledger import EvidenceLedgerCore
from agentcore.runtime.facts import LlmCallFact, NoteFact, RoundBoundaryFact, record_turn_fact
from agentcore.runtime.ledger_channel import emit_ledger_delta
from agentcore.runtime.loop_controller import Intervention, LoopController
from agentcore.runtime.verify import (
    finish_guard,
    format_guard_steer,
    uncitable_ledger_refs_only,
)
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry

from .browser_snapshot_clear import project_omitted_browser_snapshots
from .directive import Continue, LoopDirective, Return, Rework
from .outcome import RoundOutcome
from .segments import tool_calls_to_dicts
from .stream import stream_llm_round
from .tool_clear import EXEC_OUTPUT_CLEAR_TOOLS, project_cleared_window
from .write_args_clear import project_cleared_write_args

logger = get_logger(__name__)

# 成稿闸「仅 search-only #rN」：每次 finish 尝试最多自动深读的 URL 数。
AUTO_DEEP_READ_PER_FINISH = 5


def record_round_start(*, round_idx: int, run_id: str, role: str) -> None:
    """Mark a ReAct round boundary for journal fold (§8.3)."""
    record_turn_fact(RoundBoundaryFact(round_idx=round_idx, run_id=run_id, role=role).to_fact())


def build_request_window(
    messages: list[LLMMessage],
    investigation_tools: frozenset[str],
    round_idx: int,
) -> list[LLMMessage]:
    """Project the LLM window with optional tool-result / write-args clearing."""
    # B1：在投影清理前用完整 transcript 闩锁 browser_* 成功（收口对账真源）。
    from agentcore.runtime.closing_posture import note_browser_tool_success_from_messages

    note_browser_tool_success_from_messages(messages)
    window = messages
    if investigation_tools:
        cleared = project_cleared_window(
            window,
            clearable_tools=investigation_tools,
            keep_recent=settings.engine_tool_clear_keep_recent,
            min_chars=settings.engine_tool_clear_min_chars,
            summary_max_chars=settings.engine_tool_clear_file_read_summary_max_chars,
        )
        if cleared is not window:
            chars_saved = sum(
                len(old.content or "") - len(new.content or "")
                for old, new in zip(window, cleared, strict=True)
                if old.content != new.content
            )
            n_cleared = sum(
                1
                for old, new in zip(window, cleared, strict=True)
                if old.content != new.content
            )
            logger.info(
                "engine.tool_clear",
                cleared=n_cleared,
                chars_saved=chars_saved,
                round=round_idx,
            )
            window = cleared
    exec_cleared = project_cleared_window(
        window,
        clearable_tools=EXEC_OUTPUT_CLEAR_TOOLS,
        keep_recent=settings.engine_tool_clear_exec_keep_recent,
        min_chars=settings.engine_tool_clear_min_chars,
        summary_max_chars=0,
        already_executed=True,
    )
    if exec_cleared is not window:
        chars_saved = sum(
            len(old.content or "") - len(new.content or "")
            for old, new in zip(window, exec_cleared, strict=True)
            if old.content != new.content
        )
        n_cleared = sum(
            1
            for old, new in zip(window, exec_cleared, strict=True)
            if old.content != new.content
        )
        logger.info(
            "engine.tool_clear_exec",
            cleared=n_cleared,
            chars_saved=chars_saved,
            round=round_idx,
        )
        window = exec_cleared
    # Handoff 缓存崩塌：落盘后的 file_write 等大 body 仍在 assistant tool_calls.args，
    # 后续轮（含 handoff）整段 cache_miss。投影侧坍缩正文，canonical messages 不动。
    write_cleared = project_cleared_write_args(window, min_chars=500)
    if write_cleared is not window:
        n_writes = sum(
            1
            for old, new in zip(window, write_cleared, strict=True)
            if old is not new and old.role == "assistant"
        )
        logger.info(
            "engine.write_args_clear",
            cleared=n_writes,
            round=round_idx,
        )
        window = write_cleared
    # Browser snapshot trees: keep only the newest full elements/accessibility_tree;
    # older browser_* results drop those fields and gain ref_delta vs the next tree
    # (field-level omit, not whole [已清理]).
    browser_cleared = project_omitted_browser_snapshots(window, keep_recent=1)
    if browser_cleared is not window:
        n_omitted = sum(
            1
            for old, new in zip(window, browser_cleared, strict=True)
            if old.content != new.content
        )
        logger.info(
            "engine.browser_snapshot_clear",
            omitted=n_omitted,
            round=round_idx,
        )
        window = browser_cleared
    return window


@dataclass(frozen=True)
class LlmRoundOutput:
    """Successful LLM round: streamed content, reasoning, and optional tool calls.

    ``aborted`` means the provider signaled a post-commit disconnect: the content
    / reasoning here are salvageable partials and the loop should finish DEGRADED
    rather than treating the round as a clean stop.
    """

    content: str
    reasoning: str
    tool_calls: list[Any]
    usage: TokenUsage | None
    empty_diagnosis: str | None = None
    empty_raw_preview: str | None = None
    aborted: bool = False
    finish_reason: str | None = None
    provider_base_url: str | None = None


def _fact_finish_reason(
    *,
    aborted: bool,
    upstream: str | None,
    has_tool_calls: bool,
) -> str:
    """Resolve LlmCallFact finish_reason without erasing upstream ``length``.

    Semantic boundaries retained: ``aborted`` always wins; when upstream is
    missing, fall back to tool_calls/stop invention (same as call_fence).
    """
    if aborted:
        return "aborted"
    if upstream:
        return upstream
    if has_tool_calls:
        return "tool_calls"
    return "stop"

@dataclass(frozen=True)
class LlmRoundFailure:
    """LLM call failed on the non-raising path.

    Carries the ``(error_code, error_message)`` an SSE ``error`` event would show,
    but does NOT emit it: the loop defers surfacing the error until
    ``decide_llm_failure`` returns a terminal directive. The ``raise_on_error``
    (worker) path re-raises instead of returning this.
    """

    error_code: str
    error_message: str
    error_context: dict | None = None
    upstream_error: bool = False


async def run_llm_round(
    *,
    llm: OpenAICompatibleProvider,
    profile: ProfileParams,
    messages: list[LLMMessage],
    investigation_tools: frozenset[str],
    tool_defs: list[dict[str, Any]] | None,
    active_model: str | None,
    emit_content: Callable[[str], None],
    emit_reasoning: Callable[[str], None],
    on_tool_progress: Callable[[str, int], None] | None,
    round_idx: int,
    run_id: str,
    raise_on_error: bool,
    on_reset: Callable[[], None] | None = None,
) -> LlmRoundOutput | LlmRoundFailure:
    """Stream one LLM round; record facts and round_end log on success."""
    request_window = build_request_window(messages, investigation_tools, round_idx)
    request = build_selected_request(
        SelectedCall(model=active_model, profile=profile),
        request_window,
        tools=tool_defs,
        tool_choice="auto" if tool_defs else "none",
    )
    from agentcore.runtime.runs.timeout_hard import mark_llm_inflight

    mark_llm_inflight(run_id, True)
    try:
        try:
            streamed = await stream_llm_round(
                llm,
                request,
                emit_content,
                emit_reasoning,
                on_tool_progress,
                on_reset=on_reset,
            )
        except Exception as e:
            # ``llm.call_failed`` is emitted by the leaf ``observe_provider`` fence
            # (or stream_closed on consumer cancel). Avoid a second emit here.
            if raise_on_error:
                raise
            code, message, context = error_fields_for(
                e,
                fallback_code=ErrorCode.LLM_ERROR,
                fallback_message="出了点问题，请稍后重试。",
            )
            return LlmRoundFailure(
                error_code=code,
                error_message=message,
                error_context=context,
                upstream_error=isinstance(e, LLMUpstreamError),
            )
    finally:
        mark_llm_inflight(run_id, False)

    round_content = streamed.content
    round_reasoning = streamed.reasoning
    round_tool_calls = streamed.tool_calls
    usage = streamed.usage
    empty_diagnosis = streamed.empty_diagnosis
    empty_raw_preview = streamed.empty_raw_preview
    upstream_finish = streamed.finish_reason

    # Provider path usually stamps diagnosis; scripted/test streams may only send
    # finish_reason — backfill so user-facing copy can distinguish truncation.
    if (
        not round_content
        and not round_tool_calls
        and upstream_finish == "length"
        and not empty_diagnosis
    ):
        from agentcore.llm.errors import EmptyResponseDiagnosis

        empty_diagnosis = EmptyResponseDiagnosis.LENGTH_EMPTY.value

    record_turn_fact(
        LlmCallFact(
            run_id=run_id,
            round_idx=round_idx,
            content=round_content,
            reasoning_content=round_reasoning,
            tool_calls=tool_calls_to_dicts(round_tool_calls),
            usage=usage.as_dict() if usage else {},
            finish_reason=_fact_finish_reason(
                aborted=streamed.aborted,
                upstream=upstream_finish,
                has_tool_calls=bool(round_tool_calls),
            ),
        ).to_fact()
    )

    if streamed.aborted:
        logger.warning(
            "llm.stream_aborted",
            round=round_idx,
            content_chars=len(round_content),
            reasoning_chars=len(round_reasoning),
        )

    logger.info(
        "react.round_end",
        round=round_idx,
        tools=len(round_tool_calls) if round_tool_calls else 0,
        input_tokens=usage.input_tokens if usage else 0,
        output_tokens=usage.output_tokens if usage else 0,
        reasoning_tokens=usage.reasoning_tokens if usage else 0,
        done=not round_tool_calls and not streamed.aborted,
        aborted=streamed.aborted or None,
        finish_reason=upstream_finish,
    )

    return LlmRoundOutput(
        content=round_content,
        reasoning=round_reasoning,
        tool_calls=round_tool_calls or [],
        usage=usage,
        empty_diagnosis=empty_diagnosis,
        empty_raw_preview=empty_raw_preview,
        aborted=streamed.aborted,
        finish_reason=upstream_finish,
        provider_base_url=_provider_base_url(llm),
    )


def _provider_base_url(llm: object) -> str | None:
    """Best-effort upstream root for empty-response diagnostics; never fail the turn."""
    url = getattr(llm, "base_url", None)
    return str(url) if url else None


def _citable_ids(
    turn_evidence_ledger: EvidenceLedgerCore | None,
) -> frozenset[str] | None:
    """成稿闸可引用集：``None`` = 未接通；空 frozenset = 台账空 / 无可成稿引用。

    使用 ``draft_citable_ids``（``deep_read ∪ selected``），非登记宽 ``citable_ids``。
    """
    if turn_evidence_ledger is None:
        return None
    return turn_evidence_ledger.draft_citable_ids()


def _ledger_entries(
    turn_evidence_ledger: EvidenceLedgerCore | None,
) -> list[dict] | None:
    if turn_evidence_ledger is None:
        return None
    return turn_evidence_ledger.all_entries()


def finish_guard_max_reworks(
    *,
    annotate_citations: bool,
    turn_evidence_ledger: EvidenceLedgerCore | None,
) -> int:
    """分路径回炉上限：调研 worker（有台账且不开 [n] 查）对齐辩论 O2 = 1；CEO 跟配置。"""
    if turn_evidence_ledger is not None and not annotate_citations:
        return 1
    return settings.engine_finish_guard_max_reworks


def search_only_deep_read_targets(
    bad_ids: list[str],
    ledger: EvidenceLedgerCore,
    *,
    limit: int = AUTO_DEEP_READ_PER_FINISH,
) -> list[tuple[str, str]]:
    """正文非法 ``#rN`` 中：台账有 URL、尚未 deep_read/selected 的 (id, url)，上限 ``limit``。"""
    out: list[tuple[str, str]] = []
    for eid in bad_ids:
        entry = ledger.get(eid)
        if entry is None:
            continue
        if entry.get("deep_read") or entry.get("selected"):
            continue
        url = str(entry.get("url") or "").strip()
        if not url:
            continue
        out.append((eid, url))
        if len(out) >= limit:
            break
    return out


async def maybe_auto_deep_read_search_only_refs(
    content: str,
    *,
    annotate_citations: bool,
    citation_sink: list[dict[str, Any]] | None,
    turn_evidence_ledger: EvidenceLedgerCore | None,
    tools: ToolRegistry,
    tool_context: ToolContext,
    ledger_registrant: str,
    sink: Any,
    run_id: str = "",
) -> None:
    """finish_guard 前：若**仅**因 search-only ``#rN`` 不过闸，引擎代 ``read_url`` 升级台账。

    复用工具 ``citations`` → ``register_citation``（``deep_read=True``）路径；读失败不假装
    升级。书目形态等其它闸问题不走此捷径。每次 finish 尝试最多
    :data:`AUTO_DEEP_READ_PER_FINISH` 个 URL。
    """
    if not content or turn_evidence_ledger is None:
        return
    from agentcore.runtime.closing_posture import (
        downgrade_verdict_for_unresolved_write_ownership,
    )
    from agentcore.runtime.delegate.delivery_status import read_delivery_verdict

    ledger = tool_context.promotion_ledger
    downgrade_verdict_for_unresolved_write_ownership(promotion_ledger=ledger)
    bad_only = uncitable_ledger_refs_only(
        content,
        citation_count=len(citation_sink or []),
        check_citations=annotate_citations,
        citable_ids=_citable_ids(turn_evidence_ledger),
        ledger_entries=_ledger_entries(turn_evidence_ledger),
        delivery_verdict=read_delivery_verdict(promotion_ledger=ledger),
    )
    if bad_only is None or not bad_only:
        return
    targets = search_only_deep_read_targets(bad_only, turn_evidence_ledger)
    if not targets:
        return
    read_tool = tools.get_optional("read_url")
    if read_tool is None:
        logger.info(
            "engine.finish_guard_auto_deep_read",
            run_id=run_id or None,
            attempted=0,
            upgraded=0,
            skipped_no_tool=True,
            ids=[eid for eid, _ in targets],
        )
        return

    upgraded: list[str] = []
    failed: list[str] = []
    for eid, url in targets:
        try:
            result = await read_tool.execute({"url": url}, tool_context)
        except Exception as exc:  # noqa: BLE001 — 单 URL 失败不阻断其余
            logger.warning(
                "engine.finish_guard_auto_deep_read",
                run_id=run_id or None,
                id=eid,
                url=url,
                error=str(exc)[:200],
                success=False,
            )
            failed.append(eid)
            continue
        cites = list(result.citations or []) if result.success else []
        if not result.success or not cites:
            failed.append(eid)
            continue
        registrant = ledger_registrant or "engine:auto_deep_read"
        await turn_evidence_ledger.register_citations(cites, registrant=registrant)
        # 仅当真升入成稿可引用集才记 upgraded（禁假装 deep_read）。
        if eid in turn_evidence_ledger.draft_citable_ids():
            upgraded.append(eid)
        else:
            failed.append(eid)

    if upgraded or failed:
        emit_ledger_delta(sink, turn_evidence_ledger)
    logger.info(
        "engine.finish_guard_auto_deep_read",
        run_id=run_id or None,
        attempted=len(targets),
        upgraded=upgraded,
        failed=failed,
    )


def decide_no_tool_round(
    outcome: RoundOutcome,
    *,
    final_content: str,
    controller: LoopController,
    annotate_citations: bool,
    citation_sink: list[dict[str, Any]] | None,
    finish_guard_reworks: int,
    tools_offered: bool = False,
    supports_tools: bool | None = None,
    turn_evidence_ledger: EvidenceLedgerCore | None = None,
    promotion_ledger: Any = None,
) -> LoopDirective:
    """Pick the directive for a round with no tool calls.

    A round that produced text either finishes cleanly (``Return``) or, if
    finish_guard rejects it and reworks remain, is reworked (``Rework``). An empty
    round walks the convergence controller's degraded ladder: finish degraded
    (``Return`` + DEGRADED) or retry on the same model (``Continue``). Upstream
    ``finish_reason=length`` with empty body skips the one-shot Continue.

    **仅非法 ``#rN``**（search-only / 伪造 / 越界，且无书目等其它闸）：不回炉——
    调用方已尝试自动深读；出口由 :func:`apply_exit_ledger_ref_strip` 剥号放行。
    """
    if outcome.content:
        from agentcore.runtime.closing_posture import (
            downgrade_verdict_for_unresolved_write_ownership,
        )
        from agentcore.runtime.delegate.delivery_status import read_delivery_verdict

        # P0-B: latch from write collisions may exist without a delivery card.
        downgrade_verdict_for_unresolved_write_ownership(
            promotion_ledger=promotion_ledger,
        )
        verdict = read_delivery_verdict(promotion_ledger=promotion_ledger)
        citable = _citable_ids(turn_evidence_ledger)
        entries = _ledger_entries(turn_evidence_ledger)
        cite_count = len(citation_sink or [])
        # 仅 #rN 成稿闸问题：禁止 content_reset + 整篇 Rework（深读已在 decide 前尝试）。
        if (
            uncitable_ledger_refs_only(
                final_content,
                citation_count=cite_count,
                check_citations=annotate_citations,
                citable_ids=citable,
                ledger_entries=entries,
                delivery_verdict=verdict,
            )
            is not None
        ):
            return Return()
        reworks = finish_guard(
            final_content,
            citation_count=cite_count,
            check_citations=annotate_citations,
            citable_ids=citable,
            ledger_entries=entries,
            delivery_verdict=verdict,
        )
        max_reworks = finish_guard_max_reworks(
            annotate_citations=annotate_citations,
            turn_evidence_ledger=turn_evidence_ledger,
        )
        if reworks and finish_guard_reworks < max_reworks:
            return Rework()
        return Return()

    action = controller.empty_response_action(finish_reason=outcome.finish_reason)
    if action is Intervention.FINALIZE:
        logger.warning(
            "engine.degraded",
            finish_reason=outcome.finish_reason,
            empty_diagnosis=outcome.empty_diagnosis,
        )
        if tools_offered and supports_tools is False and not outcome.content:
            return Return(
                finish_reason=FinishReason.ERROR,
                extra_content=TOOLS_UNAVAILABLE_RUNTIME_MESSAGE,
            )
        return Return(finish_reason=FinishReason.DEGRADED)
    return Continue()


def apply_finish_guard_rework(
    *,
    messages: list[LLMMessage],
    emit_reset: Callable[[str], None],
    final_content: str,
    content_before_round: str,
    round_idx: int,
    run_id: str,
    annotate_citations: bool,
    citation_sink: list[dict[str, Any]] | None,
    finish_guard_reworks: int,
    turn_evidence_ledger: EvidenceLedgerCore | None = None,
    promotion_ledger: Any = None,
) -> tuple[str, int]:
    """Discard rejected content, inject steer, return updated content and rework count.

    ``emit_reset`` clears the producer's already-streamed draft on the right surface —
    ``content_reset`` for the CEO bubble, ``run_output_reset`` for a worker card — so the
    rewrite presents as a clean「违规版 → 修正版」replacement, not an append (统一底线).
    reason=``finish_guard`` is the ONLY reset that folds into the「已按交付规范重写」chip."""
    from agentcore.runtime.closing_posture import (
        downgrade_verdict_for_unresolved_write_ownership,
    )
    from agentcore.runtime.delegate.delivery_status import read_delivery_verdict

    downgrade_verdict_for_unresolved_write_ownership(
        promotion_ledger=promotion_ledger,
    )
    reworks = finish_guard(
        final_content,
        citation_count=len(citation_sink or []),
        check_citations=annotate_citations,
        citable_ids=_citable_ids(turn_evidence_ledger),
        ledger_entries=_ledger_entries(turn_evidence_ledger),
        delivery_verdict=read_delivery_verdict(promotion_ledger=promotion_ledger),
    )
    steer = format_guard_steer(reworks)
    logger.info(
        "engine.finish_guard_rework",
        round=round_idx,
        attempt=finish_guard_reworks + 1,
        issues=len(reworks),
        issues_preview=[(r[:120] + "…" if len(r) > 120 else r) for r in reworks[:5]],
    )
    emit_reset("finish_guard")
    messages.append(LLMMessage(role="user", content=steer))
    record_turn_fact(
        NoteFact(
            role="user",
            content=steer,
            reason="finish_guard",
            run_id=run_id,
        ).to_fact()
    )
    return content_before_round, finish_guard_reworks + 1


def apply_exit_ledger_ref_strip(
    content: str,
    *,
    turn_evidence_ledger: EvidenceLedgerCore | None,
    emit_reset: Callable[[str], None],
    emit_content: Callable[[str], None],
    run_id: str = "",
) -> str:
    """回炉耗尽后剥离非法 ``#rN``（Q3：放行 + 必发观测，禁止静默）。

    若正文被改写：``finish_guard`` reset 清空已流式草稿，再把剥离后正文作为新 delta
    推上对应表面（对齐辩论 evidence demote）。
    """
    citable = _citable_ids(turn_evidence_ledger)
    bad = invalid_ledger_ref_ids(content, citable)
    if not bad:
        return content
    cleaned = strip_invalid_ledger_refs(content, set(bad))
    logger.warning(
        "citations.invalid_ledger_ref",
        run_id=run_id or None,
        markers=bad,
        citable_count=len(citable or ()),
    )
    if cleaned != content:
        emit_reset("finish_guard")
        emit_content(cleaned)
    return cleaned
