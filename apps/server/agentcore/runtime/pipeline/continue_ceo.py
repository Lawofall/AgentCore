"""Cloud CEO continue after a rate-limit pause — fold the window, skip settle."""

from __future__ import annotations

import contextlib
import json
from contextvars import Token
from types import SimpleNamespace
from typing import Any, cast

import agentcore.runtime.pipeline as pipeline_pkg
from agentcore.attention import bind_attention_scope, reset_attention_scope
from agentcore.core.logging import get_logger
from agentcore.core.types import DEFAULT_PERMISSION_AXES, PermissionAxes, new_id
from agentcore.llm.credentials import LLMCredentials, bind_credential_pricing_context
from agentcore.llm.profiles import TurnProfiles as ProfileSet
from agentcore.llm.profiles import turn_profiles_for_turn
from agentcore.runtime.audit.hooks import bind_recorder
from agentcore.runtime.events import EventSink, message_start
from agentcore.runtime.evidence_ledger import EvidenceLedgerCore
from agentcore.runtime.facts import TurnFactLog, current_fact_log
from agentcore.runtime.journal import runs_from_entries
from agentcore.runtime.journal.writer import TurnJournalWriter, current_journal_writer
from agentcore.runtime.pipeline.resume.finish import finish_resume_turn
from agentcore.runtime.pipeline.resume.rehydrate import (
    arm_content_reset_reinjection,
    rehydrate_from_turn_paused,
)
from agentcore.runtime.pipeline.resume.window import pre_pause_content, resumed_captain_window
from agentcore.runtime.pipeline.resume.wire import wire_crash_turn
from agentcore.runtime.pipeline.settle import (
    salvage_failed_captain,
    salvage_pipeline_exception,
)
from agentcore.runtime.resolve.prompt.rebuild import rebuild_fresh_worker_base_prompt
from agentcore.runtime.runs import RunKind, RunPhase, RunSpec, build_captain_resumer
from agentcore.runtime.session_persistence import SessionRosterWriter, wire_roster_for_turn
from agentcore.runtime.sessions import SessionLoader, SessionSaver
from agentcore.runtime.suspension import (
    SuspensionDeleter,
    SuspensionSaver,
    turn_citations,
    turn_evidence_ledger,
    turn_history,
)
from agentcore.workspace.protocol import WorkspaceBackend

logger = get_logger(__name__)


def _journal_window_carrier(journal_entries: list[dict[str, Any]], message_id: str) -> Any:
    """Duck-type for :func:`resumed_captain_window` — no ``SuspensionKind``."""
    return SimpleNamespace(
        journal_entries=journal_entries,
        transcript=[],
        journal_degraded=False,
        message_id=message_id,
    )


def _seed_continue_display(
    *,
    sink: EventSink,
    message_id: str,
    conversation_id: str,
    journal_entries: list[dict[str, Any]],
) -> Any:
    """``message_start`` + journal seed + ``turn_paused`` rehydrate. No ``*_required``."""
    sink.emit(message_start(message_id, conversation_id=conversation_id))
    runs = runs_from_entries(journal_entries)
    sink.seed_journal(list((runs or {}).get("events") or []))
    return rehydrate_from_turn_paused(
        sink=sink,
        suspension=SimpleNamespace(  # type: ignore[arg-type]
            journal_entries=journal_entries,
            citations=[],
            message_id=message_id,
            conversation_id=conversation_id,
        ),
    )


async def continue_ceo_pipeline(
    *,
    conversation_id: str,
    message_id: str,
    user_id: str,
    user_message: str,
    journal_entries: list[dict[str, Any]],
    captain_run_id: str,
    sink: EventSink,
    backend: WorkspaceBackend,
    history: list[dict] | None = None,
    board_id: str | None = None,
    folder_id: str | None = None,
    llm_credentials: LLMCredentials | None = None,
    profile_set: ProfileSet | None = None,
    session_saver: SessionSaver | None = None,
    session_loader: SessionLoader | None = None,
    suspension_saver: SuspensionSaver | None = None,
    suspension_deleter: SuspensionDeleter | None = None,
    llm_supports_tools: bool | None = None,
    permission_axes: PermissionAxes | None = None,
    trace_id: str | None = None,
) -> dict:
    """Continue a cloud CEO turn paused on exhausted rate limit.

    Folds the CEO window from the journal and runs the captain resumer. Skips
    ``recover_turn`` / checkpoint settle — there is no decision card.
    """
    if permission_axes is None:
        permission_axes = DEFAULT_PERMISSION_AXES
    profiles = turn_profiles_for_turn(profile_set, llm_credentials)
    captain_run_id = captain_run_id or new_id()
    bind_credential_pricing_context(llm_credentials)
    llm = await pipeline_pkg.build_turn_router(
        llm_credentials, user_id=user_id, profiles=profiles
    )
    history_token = turn_history.set(history or [])
    memory_cache_token = None
    from agentcore.core.log_context import get_log_value

    journal_base = len(journal_entries)
    db_max_seq: int | None = None
    initial_seq = journal_base
    try:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories import TurnJournalRepository

        async with async_session_factory() as db:
            db_max_seq = await TurnJournalRepository(db).max_seq(message_id)
        if db_max_seq is not None:
            initial_seq = max(journal_base, db_max_seq + 1)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "pipeline.continue_initial_seq_fallback",
            message_id=message_id,
            journal_entries_count=journal_base,
            error=str(e),
        )
        initial_seq = journal_base

    logger.info(
        "pipeline.continue_start",
        message_id=message_id,
        conversation_id=conversation_id,
        journal_entries_count=journal_base,
        initial_seq=initial_seq,
        db_max_seq=db_max_seq,
    )

    journal_writer = TurnJournalWriter(
        turn_id=message_id,
        conversation_id=conversation_id,
        trace_id=trace_id or get_log_value("trace_id"),
        initial_seq=initial_seq,
    )
    journal_writer_token = current_journal_writer.set(journal_writer)
    audit_recorder, audit_token = bind_recorder(
        user_id=user_id,
        conversation_id=conversation_id,
        turn_id=message_id,
        trace_id=trace_id or get_log_value("trace_id"),
        captain_run_id=captain_run_id,
        delegated=False,
        permission_axes=(
            json.dumps(permission_axes.to_dict()) if permission_axes is not None else None
        ),
    )
    attention_token = bind_attention_scope(
        user_id=user_id,
        conversation_id=conversation_id,
        turn_id=message_id,
    )
    roster_writer = SessionRosterWriter.wrap(session_saver)
    session_saver = roster_writer.save if roster_writer is not None else None
    wire_roster_for_turn(
        conversation_id, roster_writer=roster_writer, session_loader=session_loader
    )
    fact_log = TurnFactLog(inherited_entries=list(journal_entries))
    fact_log_token = current_fact_log.set(fact_log)
    from agentcore.llm.turn_auth_dead import bind_turn_auth_dead, reset_turn_auth_dead
    from agentcore.runtime.turn.token_budget import (
        bind_turn_token_meter,
        reset_turn_token_meter,
        tokens_from_journal_entries,
    )

    turn_token_meter_token = bind_turn_token_meter(
        seed=tokens_from_journal_entries(journal_entries)
    )
    turn_auth_dead_token = bind_turn_auth_dead()
    execution_id_token: Token[str | None] | None = None
    bound_execution_id: str | None = None
    pre_pause = ""
    pre_pause_reasoning = ""
    citations_token = None
    ledger_token = None
    try:
        base_system_prompt = await rebuild_fresh_worker_base_prompt(
            user_id=user_id,
            folder_id=folder_id,
            backend=backend,
            permission_axes=permission_axes,
            desktop_online=False,
        )
        wired = await wire_crash_turn(
            llm=llm,
            sink=sink,
            backend=backend,
            board_id=board_id,
            conversation_id=conversation_id,
            message_id=message_id,
            captain_run_id=captain_run_id,
            user_id=user_id,
            folder_id=folder_id,
            base_system_prompt=base_system_prompt,
            user_message=user_message,
            journal_entries=journal_entries,
            profiles=profiles,
            permission_axes=permission_axes,
            session_saver=session_saver,
            session_loader=session_loader,
            suspension_saver=suspension_saver,
            suspension_deleter=suspension_deleter,
        )
        bound_execution_id = wired.bound_execution_id
        execution_id_token = cast(Token[str | None], wired.execution_id_token)

        hydrated = _seed_continue_display(
            sink=sink,
            message_id=message_id,
            conversation_id=conversation_id,
            journal_entries=journal_entries,
        )
        pre_pause_reasoning = hydrated.pre_pause_reasoning
        citations: list[dict] = list(hydrated.citations)
        citations_token = turn_citations.set(citations)
        resume_ledger = EvidenceLedgerCore(id_prefix="#r")
        if hydrated.evidence_ledger:
            resume_ledger.load_entries(hydrated.evidence_ledger)
        ledger_token = turn_evidence_ledger.set(resume_ledger)

        messages = resumed_captain_window(
            _journal_window_carrier(journal_entries, message_id),
            history,
        )
        pre_pause = hydrated.pre_pause_content or pre_pause_content(messages)
        arm_content_reset_reinjection(sink, pre_pause)

        from agentcore.runtime.memory_consult_cache import (
            consulted_memory_cache,
            get_consult_cache,
            seed_consult_cache_from_window,
        )

        memory_cache_token = consulted_memory_cache.set({})
        seeded = seed_consult_cache_from_window(messages)
        from agentcore.tools.on_demand import offer_tools_from_window

        offer_tools_from_window(wired.chat_tools, messages)
        if seeded or get_consult_cache():
            logger.info(
                "consult.cache_seeded",
                from_frame=0,
                from_window=seeded,
                total=len(get_consult_cache()),
            )

        from agentcore.runtime.captain_profile import apply_captain_max_rounds

        profile = apply_captain_max_rounds(profiles.get("chat"))
        turn_model = profiles.model_for("chat")
        captain_spec = RunSpec(
            run_id=captain_run_id,
            agent_id=captain_run_id,
            agent_name="CEO",
            kind=RunKind.CAPTAIN,
            task=user_message,
            role="CEO",
            depth=0,
            parent_run_id=None,
        )
        run_captain = build_captain_resumer(
            llm=llm,
            tools=wired.chat_tools,
            sink=sink,
            base_tool_context=wired.base_tool_context,
            profile=profile,
            turn_model=turn_model,
            citation_sink=citations,
            approval_gate=wired.approval_gate,
            supports_tools=llm_supports_tools,
            controller_seed=hydrated.controller_seed,
            turn_evidence_ledger=turn_evidence_ledger.get(),
        )
        captain_state = await run_captain(captain_spec, messages)

        if captain_state.phase is RunPhase.FAILED:
            from agentcore.runtime.closing_posture import reconcile_resume_closing

            result = await salvage_failed_captain(
                message_id=message_id,
                captain_run_id=captain_run_id,
                captain_state=captain_state,
                vision_cost_sink=wired.vision_cost_sink,
                sink=sink,
                audit_recorder=audit_recorder,
                roster_writer=roster_writer,
            )
            result["content"] = reconcile_resume_closing(pre_pause, result.get("content") or "")
            return result

        return await finish_resume_turn(
            message_id=message_id,
            captain_run_id=captain_run_id,
            captain_state=captain_state,
            pre_pause_content=pre_pause,
            delegate_tool=wired.delegate_tool,
            debate_tool=wired.debate_tool,
            profile=profile,
            citations=citations,
            sink=sink,
            fact_log=fact_log,
            audit_recorder=audit_recorder,
            roster_writer=roster_writer,
            journal_writer=journal_writer,
            vision_cost_runs=wired.vision_cost_sink,
            pre_pause_reasoning=pre_pause_reasoning,
        )

    except Exception as e:
        from agentcore.runtime.closing_posture import reconcile_resume_closing

        result = await salvage_pipeline_exception(
            e=e,
            message_id=message_id,
            sink=sink,
            fact_log=fact_log,
            audit_recorder=audit_recorder,
            roster_writer=roster_writer,
        )
        post = result.get("content") or ""
        result["content"] = (
            reconcile_resume_closing(pre_pause, post) if pre_pause or post else ""
        )
        return result
    finally:
        from agentcore.conversation.stage_card_resolve import (
            maybe_orphan_stage_cards_at_turn_end,
        )
        from agentcore.runtime.interaction_orphan import orphan_registry_pending
        from agentcore.runtime.pipeline.teardown import teardown_step

        await teardown_step(
            orphan_registry_pending(conversation_id, turn_id=message_id),
            step="orphan_registry_pending",
        )
        await teardown_step(
            maybe_orphan_stage_cards_at_turn_end(conversation_id, sink=sink),
            step="orphan_stage_cards",
        )
        current_fact_log.reset(fact_log_token)
        await teardown_step(journal_writer.flush(), step="journal_flush")
        current_journal_writer.reset(journal_writer_token)
        from agentcore.runtime.audit.recorder import current_audit_recorder

        await teardown_step(audit_recorder.flush(), step="audit_flush")
        if roster_writer is not None:
            await teardown_step(roster_writer.flush(), step="roster_flush")
        current_audit_recorder.reset(audit_token)
        reset_attention_scope(attention_token)
        turn_history.reset(history_token)
        if memory_cache_token is not None:
            from agentcore.runtime.memory_consult_cache import consulted_memory_cache

            consulted_memory_cache.reset(memory_cache_token)
        if citations_token is not None:
            turn_citations.reset(citations_token)
        if ledger_token is not None:
            turn_evidence_ledger.reset(ledger_token)
        reset_turn_token_meter(turn_token_meter_token)
        reset_turn_auth_dead(turn_auth_dead_token)
        if execution_id_token is not None:
            from agentcore.runtime.coordination.session import (
                current_execution_id,
                release_turn_coordination,
            )

            eid = current_execution_id.get() or bound_execution_id
            if eid:
                with contextlib.suppress(Exception):
                    release_turn_coordination(eid, conversation_id=conversation_id)
            current_execution_id.reset(execution_id_token)
        await teardown_step(llm.close(), step="llm_close")
