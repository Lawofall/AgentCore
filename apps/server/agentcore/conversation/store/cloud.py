"""Cloud ConversationStore — Postgres-backed turn-authority persistence."""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy.exc import IntegrityError

from agentcore.billing.gate import BackgroundLlmResult, BackgroundLlmSkip, run_background_llm
from agentcore.config import settings
from agentcore.conversation.common import (
    fallback_title,
    log_cost_recorded,
    log_title_degraded,
)
from agentcore.conversation.common import (
    generate_title as mint_title,
)
from agentcore.conversation.compaction import schedule_compaction_if_due
from agentcore.conversation.store.merge import (
    DEFAULT_FAILED_ERROR_MESSAGE,
    MESSAGE_STATUS_COMPLETE,
    MESSAGE_STATUS_FAILED,
    MESSAGE_STATUS_INCOMPLETE,
    MESSAGE_STATUS_RUNNING,
    merge_usage_status,
    pick_merged_content,
    visible_failed_assistant_content,
)
from agentcore.conversation.turn_stats import turn_worker_stats
from agentcore.core.error_codes import ErrorCode
from agentcore.core.logging import get_logger
from agentcore.core.types import is_uuid_id
from agentcore.db.base import async_session_factory, telemetry_session_factory
from agentcore.db.models.conversations import is_execution_harvest_conflict
from agentcore.db.repositories import (
    ConversationRepository,
    MessageRepository,
    TurnJournalRepository,
    TurnMetricsRepository,
    TurnStreamStateRepository,
)
from agentcore.folders.placement import resolve_folder_placement
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.factory import build_provider
from agentcore.llm.resolve import resolve_turn_model as resolve_user_model
from agentcore.memory import TitleResult
from agentcore.memory.consolidation import schedule_consolidation
from agentcore.runtime.events import (
    EventSink,
    FinishReason,
    workspace_snapshot_done,
    workspace_snapshot_failed,
)
from agentcore.runtime.journal import (
    KIND_TURN_END,
    journal_entries_from_display_runs,
    persist_turn_journal,
)
from agentcore.runtime.turn.outcome import coerce_produced_outcome
from agentcore.workspace.protocol import WorkspaceBackend
from agentcore.workspace.snapshots import create_snapshot

logger = get_logger(__name__)

_RUN_ERROR_MESSAGE_CAP = 2000
# Desktop outbox C2 historically filled this to pass min_length=1; must never become
# a new visible user bubble (ffafc42b · local-turn recovery placeholder).
LOCAL_TURN_RECOVERY_PLACEHOLDER = "[local-turn recovery]"
_SKIP_DERIVED_FINISH = frozenset(
    {
        FinishReason.PAUSED.value,
        FinishReason.ERROR.value,
        FinishReason.CANCELLED.value,
    }
)


def _incomplete_body(content: str) -> str:
    """Streamed captain text only; interrupt chrome is metadata + UI, not body copy."""
    return (content or "").strip()


def _is_synthetic_local_user_message(user_message: str) -> bool:
    """True when write-back has no real user intent (empty or recovery placeholder)."""
    text = (user_message or "").strip()
    return not text or text == LOCAL_TURN_RECOVERY_PLACEHOLDER


def _ensure_structured_run_error(
    *,
    existing: dict[str, Any] | None = None,
    error_code: Any = None,
    error_message: Any = None,
) -> dict[str, Any]:
    """Build structured error for FAILED settle journal/usage (never for content).

    Always returns ``code`` + ``message``. When ``existing`` carries a ``context``
    dict (e.g. BYOK deconfigured), keep it so cold-load / remedy UI can act on it.
    """
    code: Any = None
    message: Any = None
    if isinstance(existing, dict):
        code = existing.get("code")
        message = existing.get("message")
    code = code or error_code or ErrorCode.PIPELINE_ERROR
    raw = message if message is not None else error_message
    text = str(raw).strip() if raw is not None else ""
    if not text:
        text = DEFAULT_FAILED_ERROR_MESSAGE
    out: dict[str, Any] = {"code": str(code), "message": text[:_RUN_ERROR_MESSAGE_CAP]}
    if isinstance(existing, dict):
        ctx = existing.get("context")
        if isinstance(ctx, dict):
            out["context"] = ctx
    return out


def _merge_run_error_into_journal_entries(
    entries: list[dict[str, Any]] | None,
    run_error: dict[str, Any],
    *,
    finish_reason: Any = None,
) -> list[dict[str, Any]]:
    """FAILED settle: durable ``turn_end`` must carry structured error.

    Progressive journals may omit ``turn_end`` or ship a partial ``error``. Merge
    before persist so cold-load cards see code/message; never drop existing fields
    (including ``context``) when completing a sparse error object.
    """
    base: list[dict[str, Any]] = [dict(e) for e in entries] if entries else []
    turn_end_idx: int | None = None
    for i in range(len(base) - 1, -1, -1):
        if (base[i].get("kind") or "") == KIND_TURN_END:
            turn_end_idx = i
            break

    if turn_end_idx is None:
        payload: dict[str, Any] = {"error": dict(run_error)}
        if finish_reason is not None:
            payload["finish_reason"] = finish_reason
        base.append({"kind": KIND_TURN_END, "payload": payload, "ts": None})
        return base

    entry = dict(base[turn_end_idx])
    payload = dict(entry.get("payload") or {})
    existing_err = payload.get("error")
    if not isinstance(existing_err, dict):
        payload["error"] = dict(run_error)
    else:
        merged = dict(existing_err)
        for key in ("code", "message"):
            cur = merged.get(key)
            missing = (
                not cur.strip()
                if isinstance(cur, str)
                else cur is None or cur == ""
            )
            if missing and run_error.get(key) is not None:
                merged[key] = run_error[key]
        if "context" not in merged and isinstance(run_error.get("context"), dict):
            merged["context"] = run_error["context"]
        payload["error"] = merged
    if finish_reason is not None and not payload.get("finish_reason"):
        payload["finish_reason"] = finish_reason
    entry["payload"] = payload
    base[turn_end_idx] = entry
    return base


def _usage_metadata(
    result: dict,
    *,
    status: str,
    extra: dict | None = None,
    duration_ms: int | None = None,
) -> dict:
    meta = {
        "status": status,
        "input_tokens": result.get("input_tokens", 0),
        "output_tokens": result.get("output_tokens", 0),
        "reasoning_tokens": result.get("reasoning_tokens", 0),
        "cache_hit_tokens": result.get("cache_hit_tokens", 0),
        "cache_miss_tokens": result.get("cache_miss_tokens", 0),
        "rounds": result.get("rounds", 0),
    }
    finish = result.get("finish_reason")
    finish_value = getattr(finish, "value", finish)
    if finish_value is not None:
        meta["finish_reason"] = finish_value
    error_code = result.get("error_code")
    if error_code:
        meta["error_code"] = error_code
    collab = result.get("collab")
    if collab:
        meta["collab"] = collab
    outcome = coerce_produced_outcome(result.get("outcome"))
    if outcome is not None:
        meta["outcome"] = outcome
    # 回合用时：优先 result，其次 finalize 参数（与 turn_metrics / message_end 同锚）。
    dm = result.get("duration_ms", duration_ms)
    if dm is not None:
        meta["duration_ms"] = int(dm)
    if extra:
        meta.update(extra)
    return meta


def _local_metrics_status(
    *,
    is_paused: bool,
    terminal_status: str,
    local_outcome: str | None,
) -> str:
    """Map local settle facts onto ``turn_metrics.status`` (ok|partial|paused|error)."""
    if is_paused:
        return "paused"
    if terminal_status == MESSAGE_STATUS_FAILED:
        return "error"
    if local_outcome in ("ok", "partial", "paused", "error"):
        return local_outcome
    return "ok"


def _local_metrics_error(run_error: object) -> str | None:
    if isinstance(run_error, dict):
        raw = run_error.get("message") or run_error.get("code")
        return str(raw)[:1000] if raw else None
    if run_error:
        return str(run_error)[:1000]
    return None


def _local_metrics_duration_ms(runs: dict | None) -> int:
    """Wall-clock if the write-back already carried it; otherwise 0 (no invented clock)."""
    if not isinstance(runs, dict):
        return 0
    raw = runs.get("duration_ms")
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


async def _record_local_turn_metrics(
    session: object,
    *,
    turn_id: str,
    conversation_id: str,
    user_id: str,
    trace_id: str,
    kind: str,
    status: str,
    finish_reason: str | None,
    error: str | None,
    rounds: int,
    duration_ms: int,
    durable: list[dict[str, Any]] | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """Best-effort sidecar ``turn_metrics`` row.

    Token fields are the same finalize values already stamped onto
    ``messages.usage``. ``delegated`` / ``workers`` use ``turn_worker_stats`` on
    the journal (no ``cost_runs`` on local write-back — same journal-half
    fallback cloud uses when pause defers ledger fold). ``turn_id`` is the
    assistant message id: local write-back has no engine ``attempt_id``.
    """
    delegated, workers = turn_worker_stats({"journal_entries": durable or []})
    try:
        await TurnMetricsRepository(session).record(  # type: ignore[arg-type]
            turn_id=turn_id,
            conversation_id=conversation_id,
            user_id=user_id,
            trace_id=trace_id,
            agent_id="CEO",
            kind=kind,
            mode="local",
            status=status,
            finish_reason=finish_reason,
            error=error,
            rounds=int(rounds or 0),
            duration_ms=duration_ms,
            delegated=delegated,
            workers=workers,
            input_tokens=int(input_tokens or 0),
            output_tokens=int(output_tokens or 0),
        )
    except Exception as e:
        with contextlib.suppress(Exception):
            await session.rollback()  # type: ignore[union-attr]
        logger.warning(
            "observability.turn_metrics_write_failed",
            conversation_id=conversation_id,
            turn_id=turn_id,
            error=str(e),
        )


class CloudStore:
    """Postgres ConversationStore (收编 placeholder / journal / finalize / salvage)."""

    async def begin_turn(
        self,
        *,
        conversation_id: str,
        message_id: str,
        trace_id: str,
    ) -> None:
        """Create the running assistant row at turn start (progressive persistence).

        Before inserting the new placeholder, settles earlier non-paused RUNNING
        assistants in this conversation (dead registry / no-lease zombies) via
        ``close_turn_interrupted``. Failures on the new placeholder propagate: a
        turn must not run SSE / pipeline without a durable assistant row.
        """
        from agentcore.runtime.turn.interrupt import settle_prior_running_assistants

        await settle_prior_running_assistants(
            conversation_id=conversation_id,
            keep_message_id=message_id,
        )
        try:
            async with async_session_factory() as session:
                await MessageRepository(session).create_assistant_placeholder(
                    conversation_id=conversation_id,
                    message_id=message_id,
                    trace_id=trace_id,
                )
        except Exception as e:
            logger.error(
                "chat.assistant_placeholder_failed",
                conversation_id=conversation_id,
                message_id=message_id,
                error=str(e),
            )
            raise

    async def append_journal(
        self,
        *,
        turn_id: str,
        seq: int | None,
        conversation_id: str,
        trace_id: str | None,
        entry: dict[str, Any],
    ) -> int | None:
        """Append-on-emit journal fact via the telemetry pool (no primary-pool contention).

        ``seq=None`` ⇒ DB 原子分配（live）；``seq=int`` ⇒ merge 幂等去重（outbox 回写）。
        Returns the durable seq on insert, or ``None`` on merge duplicate no-op.
        """
        from agentcore.runtime.audit.hooks import on_journal_fact_appended

        async with telemetry_session_factory() as db:
            allocated = await TurnJournalRepository(db).append(
                turn_id=turn_id,
                seq=seq,
                conversation_id=conversation_id,
                trace_id=trace_id,
                entry=entry,
            )
        if allocated is not None:
            on_journal_fact_appended(entry)
        return allocated

    async def finalize(
        self,
        *,
        mode: Literal["cloud", "local"] = "cloud",
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        if mode == "local":
            return await self._finalize_local(**kwargs)
        await self._finalize_cloud(**kwargs)
        return None

    async def salvage(
        self,
        *,
        journal: list[dict[str, Any]],
        content: str,
        conversation_id: str,
        trace_id: str,
        message_id: str | None,
    ) -> None:
        """Persist a cancelled turn's already-streamed reply + finished work."""
        from agentcore.core.assistant_content import prepare_assistant_content

        streamed = (content or "").strip()
        # Salvage B: cut at first DSML open; upsert still applies strip + length top.
        body = prepare_assistant_content(streamed, salvage=True)
        if not message_id:
            logger.warning(
                "chat.incomplete_persist_skipped",
                conversation_id=conversation_id,
                reason="no_message_id",
            )
            return
        try:
            async with async_session_factory() as session:
                await MessageRepository(session).upsert_assistant(
                    conversation_id=conversation_id,
                    message_id=message_id,
                    content=body,
                    trace_id=trace_id,
                    metadata={
                        "status": MESSAGE_STATUS_INCOMPLETE,
                        "incomplete": True,
                        "finish_reason": FinishReason.CANCELLED.value,
                    },
                    merge=True,
                )
                await persist_turn_journal(
                    session,
                    message_id=message_id,
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    entries=journal_entries_from_display_runs(
                        {
                            "events": journal,
                            "finish_reason": FinishReason.CANCELLED.value,
                        }
                    ),
                    replace=False,
                )
            # 时序不变量: terminal snapshot landed → drop in-flight segments.
            with contextlib.suppress(Exception):
                await self.clear_stream_segments(turn_id=message_id)
            logger.info(
                "chat.incomplete_persisted",
                conversation_id=conversation_id,
                events=len(journal),
                content_chars=len(streamed),
            )
        except Exception as e:
            logger.warning(
                "chat.incomplete_persist_failed",
                conversation_id=conversation_id,
                error=str(e),
            )

    async def upsert_stream_segments(
        self,
        *,
        turn_id: str,
        segments: Sequence[tuple[str, str, int]],
    ) -> None:
        if not segments:
            return
        async with async_session_factory() as session:
            await TurnStreamStateRepository(session).upsert_many(
                turn_id=turn_id,
                segments=segments,
            )

    async def list_stream_segments(
        self,
        *,
        turn_id: str,
    ) -> list[dict[str, Any]]:
        async with async_session_factory() as session:
            rows = await TurnStreamStateRepository(session).list_for_turn(turn_id)
        return [
            {"channel": r.channel, "text": r.text, "generation": r.generation} for r in rows
        ]

    async def list_stream_segments_map(
        self,
        *,
        turn_ids: Sequence[str],
    ) -> dict[str, list[dict[str, Any]]]:
        if not turn_ids:
            return {}
        async with async_session_factory() as session:
            by_turn = await TurnStreamStateRepository(session).list_for_turns(turn_ids)
        return {
            tid: [
                {"channel": r.channel, "text": r.text, "generation": r.generation} for r in rows
            ]
            for tid, rows in by_turn.items()
        }

    async def clear_stream_segments(
        self,
        *,
        turn_id: str,
    ) -> None:
        async with async_session_factory() as session:
            await TurnStreamStateRepository(session).delete_for_turn(turn_id)

    async def _finalize_cloud(
        self,
        *,
        result: dict,
        conversation_id: str,
        user_id: str,
        folder_id: str | None,
        backend: WorkspaceBackend,
        sink: EventSink,
        user_message: str,
        llm_credentials: LLMCredentials | None,
        trace_id: str,
        turn_id: str,
        duration_ms: int,
        kind: str = "turn",
    ) -> None:
        """Cloud end-of-turn: assistant row + journal + ledger + telemetry + derived."""
        assistant_reply = result.get("content") or ""
        assistant_reasoning = result.get("reasoning_content") or None
        assistant_citations = result.get("citations") or None
        assistant_evidence_ledger = result.get("evidence_ledger") or None
        journal_entries = result.get("journal_entries")
        cost_runs = result.get("cost_runs") or []

        finish = result.get("finish_reason")
        finish_value = getattr(finish, "value", finish)
        if finish_value == FinishReason.PAUSED.value:
            outcome = coerce_produced_outcome(result.get("outcome"))
            if outcome == "paused":
                await self._finalize_ceo_continue_pause(
                    result=result,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    assistant_reply=assistant_reply,
                    assistant_reasoning=assistant_reasoning,
                    assistant_citations=assistant_citations,
                    assistant_evidence_ledger=assistant_evidence_ledger,
                    journal_entries=journal_entries,
                    trace_id=trace_id,
                    turn_id=turn_id,
                    duration_ms=duration_ms,
                    kind=kind,
                    finish_value=finish_value,
                )
                return
            logger.info(
                "chat.turn_paused",
                conversation_id=conversation_id,
                message_id=result.get("message_id"),
            )
            message_id = result.get("message_id")
            if message_id:
                try:
                    async with async_session_factory() as session:
                        await MessageRepository(session).upsert_assistant(
                            conversation_id=conversation_id,
                            message_id=message_id,
                            content=assistant_reply,
                            reasoning_content=assistant_reasoning,
                            citations=assistant_citations,
                            evidence_ledger=assistant_evidence_ledger,
                            trace_id=trace_id,
                            metadata=_usage_metadata(
                                result,
                                status=MESSAGE_STATUS_RUNNING,
                                duration_ms=duration_ms,
                                extra={"paused": True},
                            ),
                            merge=True,
                        )
                    # 时序不变量: pause snapshot landed → drop segments (paused 列优先).
                    with contextlib.suppress(Exception):
                        await self.clear_stream_segments(turn_id=message_id)
                except Exception as e:
                    logger.warning(
                        "chat.pause_snapshot_failed",
                        conversation_id=conversation_id,
                        message_id=message_id,
                        error=str(e),
                    )
            return

        turn_error = result.get("error")
        # String errors are the common cloud shape; dict is rare but accepted.
        if isinstance(turn_error, dict):
            turn_error_message = turn_error.get("message")
        elif turn_error:
            turn_error_message = str(turn_error)
        else:
            turn_error_message = None
        message_id = result.get("message_id")
        outcome = coerce_produced_outcome(result.get("outcome"))
        if outcome is None:
            outcome = (
                "error"
                if turn_error or finish_value == FinishReason.ERROR.value
                else "ok"
            )
        # Message lifecycle (complete/failed) is orthogonal to turn outcome.
        # Partial with a visible reply is COMPLETE so the body is the face;
        # structured error still rides usage + journal (ERROR SSE is not journaled).
        has_reply = bool((assistant_reply or "").strip())
        if outcome == "partial" and has_reply:
            terminal_status = MESSAGE_STATUS_COMPLETE
        elif turn_error or finish_value == FinishReason.ERROR.value:
            terminal_status = MESSAGE_STATUS_FAILED
        else:
            terminal_status = MESSAGE_STATUS_COMPLETE
        stamp_error = bool(turn_error) or finish_value == FinishReason.ERROR.value
        # FAILED/ERROR settle: structured error is the authority for failure copy.
        # Synthesize {code, message} when missing so journal/usage never lack an
        # error object while content stays empty (or partial only). Also stamp
        # when the reply was salvaged into a COMPLETE partial face.
        run_error = (
            _ensure_structured_run_error(
                existing=turn_error if isinstance(turn_error, dict) else None,
                error_code=result.get("error_code"),
                error_message=turn_error_message,
            )
            if stamp_error
            else None
        )
        abnormal = bool(turn_error) or (
            finish_value is not None and finish_value != FinishReason.END_TURN.value
        )
        synth_entries = (
            journal_entries_from_display_runs(
                {
                    "finish_reason": finish_value,
                    "error": run_error,
                    **({"outcome": outcome} if outcome else {}),
                }
            )
            if journal_entries is None and abnormal
            else None
        )
        durable_entries = journal_entries if journal_entries is not None else synth_entries
        # Progressive journal may lack / sparseness turn_end.error — merge before
        # persist so durable facts carry the same structured error as usage.
        if stamp_error and run_error is not None:
            durable_entries = _merge_run_error_into_journal_entries(
                durable_entries,
                run_error,
                finish_reason=finish_value,
            )
        if terminal_status == MESSAGE_STATUS_FAILED:
            assistant_reply = visible_failed_assistant_content(content=assistant_reply)
        usage_extra: dict[str, Any] = {"paused": False, "outcome": outcome}
        if run_error is not None:
            usage_extra["error_code"] = run_error["code"]
            usage_extra["error"] = run_error

        # Drain telemetry outbox before opening the main-pool session (no priority
        # inversion). Proof is required by reconcile_turn_cost_ledger.
        ledger_drained = None
        if cost_runs or message_id:
            from agentcore.billing.turn_ledger import drain_cost_ledger_before_reconcile

            ledger_drained = await drain_cost_ledger_before_reconcile(
                conversation_id=conversation_id,
                message_id=result.get("message_id"),
            )

        async with async_session_factory() as session:
            msg_repo = MessageRepository(session)

            if message_id:
                # Partial stays in content via merge; pure failure writes empty body.
                # Structured error rides usage/journal (not message.content).
                await msg_repo.upsert_assistant(
                    conversation_id=conversation_id,
                    message_id=message_id,
                    content=assistant_reply,
                    reasoning_content=assistant_reasoning,
                    citations=assistant_citations,
                    evidence_ledger=assistant_evidence_ledger,
                    trace_id=trace_id,
                    metadata=_usage_metadata(
                        result,
                        status=terminal_status,
                        duration_ms=duration_ms,
                        # Non-pause settle must clear the cold pause latch (resume
                        # continuation / terminal) — merge_usage_status only drops it
                        # on terminal OR explicit paused:false.
                        extra=usage_extra,
                    ),
                    merge=True,
                )
                if durable_entries is not None:
                    await persist_turn_journal(
                        session,
                        message_id=message_id,
                        conversation_id=conversation_id,
                        trace_id=trace_id,
                        entries=durable_entries,
                        replace=False,
                    )

            # Reconcile even when in-memory cost_runs is thin: cost_calls (call meter)
            # is authority for captain+worker spend; orphans (vision) still fold from cost_runs.
            if ledger_drained is not None:
                try:
                    from agentcore.billing.turn_ledger import reconcile_turn_cost_ledger
                    from agentcore.runtime.costing import aggregate_cost

                    ledger_rows = await reconcile_turn_cost_ledger(
                        session,
                        drained=ledger_drained,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        message_id=result.get("message_id"),
                        cost_runs=list(cost_runs),
                        trace_id=trace_id,
                    )
                    if ledger_rows:
                        log_cost_recorded(
                            conversation_id, result.get("message_id"), ledger_rows
                        )
                except Exception as e:
                    await session.rollback()
                    logger.warning(
                        "cost.ledger_write_failed",
                        conversation_id=conversation_id,
                        message_id=result.get("message_id"),
                        error=str(e),
                    )
                    from agentcore.billing.cost_ledger_queue import get_cost_ledger_queue

                    if cost_runs:
                        get_cost_ledger_queue().enqueue_runs(
                            user_id=user_id,
                            conversation_id=conversation_id,
                            message_id=result.get("message_id"),
                            runs=list(cost_runs),
                            trace_id=trace_id,
                            source="turn",
                        )
                else:
                    # P2 DERIVED: stamp turn total onto messages.cost (footer reload).
                    # Best-effort sibling of the ledger write — failure must not undo ledger.
                    if message_id and ledger_rows:
                        try:
                            await msg_repo.set_cost(
                                message_id,
                                conversation_id=conversation_id,
                                cost=dict(aggregate_cost(ledger_rows)),
                            )
                        except Exception as e:
                            await session.rollback()
                            logger.warning(
                                "cost.message_column_write_failed",
                                conversation_id=conversation_id,
                                message_id=message_id,
                                error=str(e),
                            )

            delegated, workers = turn_worker_stats(result)
            collab = result.get("collab") or {}
            try:
                await TurnMetricsRepository(session).record(
                    turn_id=turn_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    trace_id=trace_id,
                    agent_id="CEO",
                    kind=kind,
                    mode="cloud",
                    status=outcome,
                    finish_reason=finish_value,
                    error=str(turn_error)[:1000] if turn_error else None,
                    rounds=int(result.get("rounds", 0) or 0),
                    duration_ms=duration_ms,
                    delegated=delegated,
                    workers=workers,
                    input_tokens=int(result.get("input_tokens", 0) or 0),
                    output_tokens=int(result.get("output_tokens", 0) or 0),
                    boundary_yields=int(collab.get("boundary_yields", 0) or 0),
                    scope_signals=int(collab.get("scope_signals", 0) or 0),
                    revises=int(collab.get("revises", 0) or 0),
                    escalations=int(collab.get("escalations", 0) or 0),
                    audit_drops=int(result.get("audit_drops", 0) or 0),
                )
            except Exception as e:
                await session.rollback()
                logger.warning(
                    "observability.turn_metrics_write_failed",
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    error=str(e),
                )

        # 时序不变量: terminal snapshot (above) landed → drop in-flight segments.
        if message_id:
            with contextlib.suppress(Exception):
                await self.clear_stream_segments(turn_id=message_id)

        # END_TURN + reply → last compliant motion_card becomes 阶段推进卡.
        # CEO→user followups chips are offline (no mint / emit / set_followups).
        wants_stage_card = (
            finish_value == FinishReason.END_TURN.value and bool(assistant_reply.strip())
        )
        if wants_stage_card and message_id:
            from agentcore.memory.followups import select_motion_card_from_journal
            from agentcore.runtime.kickoff.stage_card import emit_stage_card_for_motion

            journal_entries = result.get("journal_entries")
            motion_card = select_motion_card_from_journal(journal_entries)
            if isinstance(motion_card, dict):
                await emit_stage_card_for_motion(
                    sink,
                    conversation_id=conversation_id,
                    motion_card=motion_card,
                    turn_id=str(message_id),
                    journal_entries=(
                        journal_entries if isinstance(journal_entries, list) else None
                    ),
                )

        schedule_consolidation(conversation_id)
        await schedule_compaction_if_due(conversation_id, result.get("input_tokens", 0))

        if (
            settings.workspace_snapshot_enabled
            and backend.location == "server"
            and getattr(backend, "dirty", False)
        ):
            try:
                ref = await create_snapshot(
                    user_id=user_id,
                    folder_id=folder_id,
                    folder_rel_path=(
                        await resolve_folder_placement(folder_id)
                    ).rel_path,
                    conversation_id=conversation_id,
                )
                logger.info(
                    "workspace.snapshot_created",
                    conversation_id=conversation_id,
                    snapshot_id=ref.snapshot_id,
                    size_bytes=ref.size_bytes,
                )
                sink.emit(
                    workspace_snapshot_done(
                        snapshot_id=ref.snapshot_id,
                        conversation_id=conversation_id,
                        size_bytes=ref.size_bytes,
                    )
                )
            except Exception as e:
                logger.warning(
                    "workspace.snapshot_failed",
                    conversation_id=conversation_id,
                    error=str(e),
                )
                sink.emit(workspace_snapshot_failed(conversation_id=conversation_id))

    async def _finalize_ceo_continue_pause(
        self,
        *,
        result: dict[str, Any],
        conversation_id: str,
        user_id: str,
        assistant_reply: str,
        assistant_reasoning: str | None,
        assistant_citations: list[dict] | None,
        assistant_evidence_ledger: list[dict] | None,
        journal_entries: list[dict[str, Any]] | None,
        trace_id: str,
        turn_id: str,
        duration_ms: int,
        kind: str,
        finish_value: str,
    ) -> None:
        """Persist a CEO rate-limit pause: journal + ``turn_metrics`` + continue lock."""
        from agentcore.runtime.turn.ceo_continue import save_ceo_continue_lock

        message_id = result.get("message_id")
        logger.info(
            "chat.turn_paused",
            conversation_id=conversation_id,
            message_id=message_id,
            outcome="paused",
        )
        if not message_id:
            return
        turn_error = result.get("error")
        try:
            async with async_session_factory() as session:
                await MessageRepository(session).upsert_assistant(
                    conversation_id=conversation_id,
                    message_id=message_id,
                    content=assistant_reply,
                    reasoning_content=assistant_reasoning,
                    citations=assistant_citations,
                    evidence_ledger=assistant_evidence_ledger,
                    trace_id=trace_id,
                    metadata=_usage_metadata(
                        result,
                        status=MESSAGE_STATUS_RUNNING,
                        duration_ms=duration_ms,
                        extra={"paused": True, "outcome": "paused"},
                    ),
                    merge=True,
                )
                if journal_entries:
                    await persist_turn_journal(
                        session,
                        message_id=message_id,
                        conversation_id=conversation_id,
                        trace_id=trace_id,
                        entries=journal_entries,
                        replace=False,
                    )
                delegated, workers = turn_worker_stats(result)
                collab = result.get("collab") or {}
                try:
                    await TurnMetricsRepository(session).record(
                        turn_id=turn_id,
                        conversation_id=conversation_id,
                        user_id=user_id,
                        trace_id=trace_id,
                        agent_id="CEO",
                        kind=kind,
                        mode="cloud",
                        status="paused",
                        finish_reason=finish_value,
                        error=str(turn_error)[:1000] if turn_error else None,
                        rounds=int(result.get("rounds", 0) or 0),
                        duration_ms=duration_ms,
                        delegated=delegated,
                        workers=workers,
                        input_tokens=int(result.get("input_tokens", 0) or 0),
                        output_tokens=int(result.get("output_tokens", 0) or 0),
                        boundary_yields=int(collab.get("boundary_yields", 0) or 0),
                        scope_signals=int(collab.get("scope_signals", 0) or 0),
                        revises=int(collab.get("revises", 0) or 0),
                        escalations=int(collab.get("escalations", 0) or 0),
                        audit_drops=int(result.get("audit_drops", 0) or 0),
                    )
                except Exception as e:
                    await session.rollback()
                    logger.warning(
                        "observability.turn_metrics_write_failed",
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        error=str(e),
                    )
            with contextlib.suppress(Exception):
                await self.clear_stream_segments(turn_id=message_id)
        except Exception as e:
            logger.warning(
                "chat.pause_snapshot_failed",
                conversation_id=conversation_id,
                message_id=message_id,
                error=str(e),
            )
        await save_ceo_continue_lock(
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            trace_id=trace_id,
        )

    async def _finalize_local(
        self,
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
    ) -> dict[str, Any]:
        """Local write-back via finalize(mode=local): content + status + journal."""
        origin = (origin or "").strip() or None
        execution_id = (execution_id or "").strip() or None
        harvest_kind = (harvest_kind or "").strip() or None
        from agentcore.conversation.mentions import to_stored_agent_mentions

        stored_mentions = to_stored_agent_mentions(agent_mentions)
        finish_value = finish_reason
        is_paused = finish_value == FinishReason.PAUSED.value
        is_incomplete = finish_value == FinishReason.CANCELLED.value
        skip_derived = finish_value in _SKIP_DERIVED_FINISH

        synthetic_user = _is_synthetic_local_user_message(user_message)
        # Observability for the ffafc42b dirty sample (umid keyed as assistant id).
        dirty_umid_collision = bool(
            message_id and user_message_id and user_message_id == message_id
        )

        async with async_session_factory() as session:
            msg_repo = MessageRepository(session)
            # Non-UUID keys (legacy sidecar ``resume-{turn_id}``) miss before
            # the PG UUID bind so assistant-pairing can run. Do not wait for
            # asyncpg: it wraps as ``DBAPIError``, not ``DataError``.
            existing_user = (
                await msg_repo.get_by_id(
                    user_message_id, conversation_id=conversation_id
                )
                if is_uuid_id(user_message_id)
                else None
            )
            if existing_user is not None and getattr(existing_user, "role", None) != "user":
                existing_user = None
            existing_assistant = (
                await msg_repo.get_by_id(message_id, conversation_id=conversation_id)
                if message_id
                else None
            )

        turn_user = existing_user
        if turn_user is None and existing_assistant is not None and message_id:
            async with async_session_factory() as session:
                turn_user = await MessageRepository(session).user_message_for_assistant(
                    conversation_id=conversation_id,
                    assistant_message_id=message_id,
                )
            if turn_user is not None:
                logger.info(
                    "chat.local_turn_reuse_paired_user",
                    conversation_id=conversation_id,
                    message_id=message_id,
                    user_message_id=turn_user.id,
                )

        user_msg_id = turn_user.id if turn_user is not None else user_message_id
        if turn_user is None:
            # No real user intent → do not insert a visible recovery/empty user row
            # (ffafc42b). Still settle assistant/journal below when should_settle.
            # Covers umid≈message_id dirty samples when content is synthetic.
            if synthetic_user:
                logger.info(
                    "chat.local_turn_skip_synthetic_user",
                    conversation_id=conversation_id,
                    message_id=message_id,
                    user_message_id=user_message_id,
                    dirty_umid_collision=dirty_umid_collision,
                )
            else:
                try:
                    user_usage: dict[str, Any] = {}
                    if origin:
                        user_usage["origin"] = origin
                    if execution_id:
                        user_usage["execution_id"] = execution_id
                    if harvest_kind:
                        user_usage["harvest_kind"] = harvest_kind
                    async with async_session_factory() as session:
                        user_msg = await MessageRepository(session).create(
                            conversation_id=conversation_id,
                            role="user",
                            content=user_message,
                            message_id=(
                                user_message_id
                                if is_uuid_id(user_message_id)
                                else None
                            ),
                            metadata=user_usage or None,
                            agent_mentions=stored_mentions or None,
                        )
                        user_msg_id = user_msg.id
                except IntegrityError as exc:
                    if origin == "execution_harvest" and is_execution_harvest_conflict(exc):
                        from agentcore.runtime.leases.repo import TurnLeaseRepository

                        claimed = None
                        following = None
                        in_flight = False
                        async with async_session_factory() as lookup:
                            repo = MessageRepository(lookup)
                            if execution_id:
                                claimed = await repo.get_execution_harvest_user(
                                    conversation_id=conversation_id,
                                    execution_id=execution_id,
                                )
                            fresh_after = datetime.now(UTC) - timedelta(
                                seconds=settings.turn_lease_ttl_seconds
                            )
                            in_flight = await TurnLeaseRepository(
                                lookup
                            ).exists_fresh_for_conversation(
                                conversation_id, after=fresh_after
                            )
                            if claimed is not None:
                                following = await repo.get_first_assistant_after(
                                    conversation_id=conversation_id,
                                    after=claimed.created_at,
                                    after_id=claimed.id,
                                )
                        if claimed is not None:
                            user_msg_id = claimed.id
                        following_status = (
                            following.usage.get("status")
                            if following is not None and isinstance(following.usage, dict)
                            else None
                        )
                        settled = following_status in {
                            MESSAGE_STATUS_COMPLETE,
                            MESSAGE_STATUS_FAILED,
                            MESSAGE_STATUS_INCOMPLETE,
                        }
                        if settled or in_flight:
                            logger.info(
                                "chat.local_turn_harvest_idempotent",
                                conversation_id=conversation_id,
                                message_id=message_id,
                                execution_id=execution_id,
                                settled=settled,
                                in_flight=in_flight,
                            )
                            return {
                                "user_message_id": user_msg_id,
                                "assistant_message_id": None,
                                "title": None,
                                "followups": None,
                                "noop": True,
                            }
                        logger.info(
                            "chat.local_turn_harvest_claim_continue",
                            conversation_id=conversation_id,
                            message_id=message_id,
                            execution_id=execution_id,
                        )
                    else:
                        logger.info(
                            "chat.local_turn_idempotent_race",
                            conversation_id=conversation_id,
                            message_id=message_id,
                        )
                        user_msg_id = user_message_id

        assistant_message_id: str | None = None
        if is_paused:
            terminal_status = MESSAGE_STATUS_RUNNING
        elif is_incomplete:
            terminal_status = MESSAGE_STATUS_INCOMPLETE
        elif finish_value == FinishReason.ERROR.value:
            raw_local_outcome = (
                coerce_produced_outcome(runs.get("outcome"))
                if isinstance(runs, dict)
                else None
            )
            if raw_local_outcome == "partial" and (assistant_content or "").strip():
                terminal_status = MESSAGE_STATUS_COMPLETE
            else:
                terminal_status = MESSAGE_STATUS_FAILED
        else:
            terminal_status = MESSAGE_STATUS_COMPLETE

        content_to_write = (
            _incomplete_body(assistant_content) if is_incomplete else assistant_content
        )

        usage_metadata: dict[str, Any] = {
            "status": terminal_status,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "cache_hit_tokens": cache_hit_tokens,
            "cache_miss_tokens": cache_miss_tokens,
            "rounds": rounds,
        }
        local_outcome = (
            coerce_produced_outcome(runs.get("outcome"))
            if isinstance(runs, dict)
            else None
        )
        if local_outcome is not None:
            usage_metadata["outcome"] = local_outcome
        if is_paused:
            usage_metadata["paused"] = True
        else:
            # Resume / non-pause local settle: clear cold pause latch.
            usage_metadata["paused"] = False
            if is_incomplete:
                usage_metadata["incomplete"] = True
                usage_metadata["finish_reason"] = FinishReason.CANCELLED.value
            elif finish_value is not None:
                usage_metadata["finish_reason"] = finish_value
        run_error = runs.get("error") if isinstance(runs, dict) else None
        runs_for_journal = runs
        if terminal_status == MESSAGE_STATUS_FAILED:
            run_error = _ensure_structured_run_error(
                existing=run_error if isinstance(run_error, dict) else None,
                error_code=(
                    run_error.get("code") if isinstance(run_error, dict) else None
                ),
                error_message=(
                    run_error.get("message") if isinstance(run_error, dict) else None
                ),
            )
            usage_metadata["error_code"] = run_error["code"]
            usage_metadata["error"] = run_error
            # Ensure journal projection carries structured error even when the
            # client omitted ``runs.error`` on an ERROR finish.
            if isinstance(runs, dict):
                runs_for_journal = {**runs, "error": run_error}
            else:
                runs_for_journal = {
                    "finish_reason": finish_value,
                    "error": run_error,
                }
        elif isinstance(run_error, dict):
            err_code = run_error.get("code")
            if err_code:
                usage_metadata["error_code"] = err_code
            if err_code or run_error.get("message"):
                usage_metadata["error"] = run_error

        # Settle whenever the turn has a terminal/pause surface — including empty
        # ERROR (soft-fail / first-turn crash) and empty bubble with process state
        # (runs/journal — aligns with cloud live: process projection must land).
        # Also settle when message_id already has a running/paused assistant row
        # (empty final must not noop — that leaves a ghost in-flight bubble).
        # True no-op (no orphan row): empty body AND no process state AND not
        # paused/incomplete/failed AND no open assistant row. Desktop deletes
        # outbox only on assistant id or noop.
        has_process_state = bool(
            (isinstance(runs, dict) and bool(runs))
            or (isinstance(journal, list) and len(journal) > 0)
        )
        existing_usage = (
            (getattr(existing_assistant, "usage", None) or {})
            if existing_assistant is not None
            else {}
        )
        prior_paused = bool(existing_usage.get("paused"))
        has_open_assistant = bool(
            existing_assistant is not None
            and (
                existing_usage.get("status") == MESSAGE_STATUS_RUNNING
                or bool(existing_usage.get("paused"))
            )
        )
        should_settle = bool(
            message_id
            and (
                content_to_write
                or is_paused
                or is_incomplete
                or terminal_status == MESSAGE_STATUS_FAILED
                or has_process_state
                or has_open_assistant
            )
        )
        # Intentional skip — client may delete outbox; never a silent "200 + null id".
        noop = bool(message_id) and not should_settle
        if should_settle:
            async with async_session_factory() as session:
                # D7: idempotent merge upsert (no early-return when rows already exist).
                existing = await MessageRepository(session).get_by_id(
                    message_id, conversation_id=conversation_id
                )
                existing_usage = (existing.usage if existing else None) or {}
                merged_usage = merge_usage_status(existing_usage, usage_metadata)
                content = pick_merged_content(
                    existing.content if existing else None,
                    content_to_write,
                    incoming_status=terminal_status,
                )
                if terminal_status == MESSAGE_STATUS_FAILED:
                    content = visible_failed_assistant_content(content=content)
                assistant_msg = await MessageRepository(session).upsert_assistant(
                    conversation_id=conversation_id,
                    message_id=message_id,
                    content=content,
                    reasoning_content=assistant_reasoning,
                    citations=citations,
                    evidence_ledger=evidence_ledger,
                    trace_id=trace_id,
                    metadata=merged_usage,
                    merge=True,
                )
                assistant_message_id = assistant_msg.id
                # Pause must snapshot journal too: sidecar has no ``save_paused_turn``
                # → PG path, and GET messages projects ``team_batch`` only from
                # ``turn_journal``. Same choke point as complete/cancel
                # (``persist_turn_journal`` → ``obs.turn_spans``). Cloud live pause
                # still skips this in ``_finalize_cloud`` because ``save_paused_turn``
                # already wrote the table. Title / compaction stay skipped below.
                # Progressive journal is the sole fact source when present
                # (execution-only facts like late run_completed). Else project
                # display ``runs``; crash salvage may pass journal alone.
                if isinstance(journal, list) and journal:
                    durable = journal
                elif runs_for_journal is not None:
                    durable = journal_entries_from_display_runs(runs_for_journal)
                else:
                    durable = None
                # Same口径 as cloud live: FAILED must land structured error on
                # turn_end even when progressive journal omitted / sparsed it.
                if (
                    terminal_status == MESSAGE_STATUS_FAILED
                    and run_error is not None
                ):
                    durable = _merge_run_error_into_journal_entries(
                        durable,
                        run_error,
                        finish_reason=finish_value,
                    )
                if durable is not None:
                    # Outbox writeback holds this turn's authoritative stream
                    # (pause snapshot or resume complete rewrite). Replace so a
                    # resume reusing ``turn_id`` overwrites the pause prefix.
                    await persist_turn_journal(
                        session,
                        message_id=assistant_msg.id,
                        conversation_id=conversation_id,
                        trace_id=trace_id,
                        entries=durable,
                        replace=True,
                    )
                await _record_local_turn_metrics(
                    session,
                    turn_id=assistant_msg.id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    trace_id=trace_id,
                    kind="resume" if prior_paused else "turn",
                    status=_local_metrics_status(
                        is_paused=is_paused,
                        terminal_status=terminal_status,
                        local_outcome=local_outcome,
                    ),
                    finish_reason=finish_value,
                    error=_local_metrics_error(run_error),
                    rounds=rounds,
                    duration_ms=_local_metrics_duration_ms(
                        runs if isinstance(runs, dict) else None
                    ),
                    durable=durable,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            # 时序不变量: local terminal/pause snapshot landed → drop segments.
            with contextlib.suppress(Exception):
                await self.clear_stream_segments(turn_id=message_id)

        if skip_derived:
            # Mirror cloud: ERROR/CANCELLED still arm compaction; PAUSED does not.
            if not is_paused:
                await schedule_compaction_if_due(conversation_id, input_tokens)
            logger.info(
                "chat.local_turn_recorded",
                conversation_id=conversation_id,
                message_id=message_id,
                finish_reason=finish_value,
                chars=len(content_to_write or ""),
                rounds=rounds,
            )
            return {
                "user_message_id": user_msg_id,
                "assistant_message_id": assistant_message_id,
                "title": None,
                "followups": None,
                "noop": noop,
            }

        async with async_session_factory() as session:
            conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
            needs_title = bool(conv and not conv.title)
            existing_title = conv.title if conv else None

        # Synthetic / empty um has no real user intent — never mint title from it.
        # Harvest user text is real-looking but system-authored; skip title from it.
        if synthetic_user or origin == "execution_harvest":
            needs_title = False

        # Parallel auto-title (desktop REST) may already be minting — skip write-back
        # mint to avoid a second LLM call; ``update_title_if_empty`` is the write guard.
        if needs_title:
            from agentcore.conversation.common import _title_inflight

            if conversation_id in _title_inflight:
                needs_title = False

        title: str | None = existing_title
        wants_stage_card = (
            finish_value == FinishReason.END_TURN.value
            and bool((assistant_content or "").strip())
            and bool(assistant_message_id)
        )
        if wants_stage_card:
            from agentcore.memory.followups import select_motion_card_from_journal
            from agentcore.runtime.kickoff.stage_card import emit_stage_card_for_motion

            journal_src = journal
            if journal_src is None and isinstance(runs, dict):
                events = runs.get("events")
                journal_src = events if isinstance(events, list) else None
            motion_card = select_motion_card_from_journal(journal_src)
            if isinstance(motion_card, dict) and assistant_message_id:
                # Local write-back has no live SSE sink; journal via
                # prewrite_settlement_direct still lands the durable card.
                await emit_stage_card_for_motion(
                    None,
                    conversation_id=conversation_id,
                    motion_card=motion_card,
                    turn_id=str(assistant_message_id),
                    journal_entries=(journal_src if isinstance(journal_src, list) else None),
                )

        if needs_title:
            from agentcore.llm.background_failure import classify_background_llm_failure

            async def _title_runner(credentials: LLMCredentials) -> TitleResult:
                model = resolve_user_model(credentials)
                provider = build_provider(credentials, purpose="platform_internal")
                try:
                    # Align with cloud early mint: first user message only.
                    return await mint_title(
                        provider=provider,
                        conversation_id=conversation_id,
                        user_message=user_message,
                        assistant_reply="",
                        model=model,
                    )
                finally:
                    await provider.close()

            try:
                bg = await run_background_llm(
                    user_id, purpose="title", runner=_title_runner
                )
                if isinstance(bg, BackgroundLlmResult) and bg.value is not None:
                    minted = bg.value
                    if minted.degraded_reason or not minted.title.strip():
                        log_title_degraded(
                            conversation_id=conversation_id,
                            reason=minted.degraded_reason or "empty_model_title",
                            title_chars=len(minted.title),
                            persisted=False,
                        )
                    else:
                        async with async_session_factory() as session:
                            updated = await ConversationRepository(
                                session
                            ).update_title_if_empty(conversation_id, minted.title)
                            if updated is not None:
                                title = updated.title
                            else:
                                conv = await ConversationRepository(
                                    session
                                ).get_by_id_unscoped(conversation_id)
                                title = conv.title if conv else existing_title
                elif isinstance(bg, BackgroundLlmSkip):
                    skipped = fallback_title(user_message)
                    log_title_degraded(
                        conversation_id=conversation_id,
                        reason=f"gate_{bg.reason.value}",
                        title_chars=len(skipped),
                        persisted=False,
                    )
            except Exception as e:
                reason = classify_background_llm_failure(e)
                logger.warning(
                    "chat.local_derived_provider_unavailable",
                    conversation_id=conversation_id,
                    error=str(e),
                    reason=reason,
                )

        schedule_consolidation(conversation_id)
        await schedule_compaction_if_due(conversation_id, input_tokens)

        logger.info(
            "chat.local_turn_recorded",
            conversation_id=conversation_id,
            message_id=message_id,
            chars=len(assistant_content or ""),
            rounds=rounds,
        )
        return {
            "user_message_id": user_msg_id,
            "assistant_message_id": assistant_message_id,
            "title": title,
            "followups": None,
            "noop": noop,
        }


_cloud_store: CloudStore | None = None


def get_cloud_store() -> CloudStore:
    """Process-wide CloudStore singleton (host + /local-turns share one impl)."""
    global _cloud_store
    if _cloud_store is None:
        _cloud_store = CloudStore()
    return _cloud_store
