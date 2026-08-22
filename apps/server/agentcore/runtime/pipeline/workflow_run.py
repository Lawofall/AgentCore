"""Mechanism-direct workflow turn — skip CEO react_loop, run fixed delegate DAG.

Mirrors ``stage_card_debate``: prepare → assemble tools → ``delegate.execute`` with
pre-expanded tasks + topology lock → bubble output → settle.
"""

from __future__ import annotations

import contextlib
from contextvars import Token
from types import SimpleNamespace
from typing import Any, Protocol, cast

from agentcore.attention import bind_attention_scope, reset_attention_scope
from agentcore.core.log_context import get_log_value
from agentcore.core.logging import get_logger
from agentcore.core.types import PermissionAxes, new_id
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.profiles import TurnProfiles as ProfileSet
from agentcore.llm.profiles import turn_profiles_for_turn
from agentcore.runtime.audit.hooks import bind_recorder
from agentcore.runtime.events import EventSink, FinishReason, content_delta, message_start
from agentcore.runtime.evidence_ledger import EvidenceLedgerCore
from agentcore.runtime.facts import TurnFactLog, current_fact_log
from agentcore.runtime.journal.writer import TurnJournalWriter, current_journal_writer
from agentcore.runtime.pipeline.assemble import assemble_ceo_turn
from agentcore.runtime.pipeline.prepare import prepare_fresh_turn
from agentcore.runtime.pipeline.settle import salvage_pipeline_exception, settle_successful_turn
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


class _AsyncCloseable(Protocol):
    async def close(self) -> Any: ...


async def run_workflow_pipeline(
    *,
    conversation_id: str,
    user_id: str,
    user_message: str,
    tasks: list[dict[str, Any]],
    workflow_id: str,
    workflow_version: int,
    sink: EventSink,
    backend: WorkspaceBackend,
    history: list[dict] | None = None,
    folder_id: str | None = None,
    board_id: str | None = None,
    permission_axes: PermissionAxes | None = None,
    profile_set: ProfileSet | None = None,
    llm_credentials: LLMCredentials | None = None,
    session_saver: SessionSaver | None = None,
    session_loader: SessionLoader | None = None,
    suspension_saver: SuspensionSaver | None = None,
    suspension_deleter: SuspensionDeleter | None = None,
    message_id: str | None = None,
    x_client_platform: str | None = None,
) -> dict:
    """Run a fixed workflow DAG; bubble = delegate output (no CEO 编队)."""
    message_id = message_id or new_id()
    captain_run_id = new_id()
    profiles = turn_profiles_for_turn(profile_set, llm_credentials)
    history = list(history or [])

    fact_log = TurnFactLog()
    fact_log_token = current_fact_log.set(fact_log)
    journal_writer = TurnJournalWriter(
        turn_id=message_id,
        conversation_id=conversation_id,
        trace_id=get_log_value("trace_id"),
    )
    journal_token = current_journal_writer.set(journal_writer)
    import json

    audit_recorder, audit_token = bind_recorder(
        user_id=user_id,
        conversation_id=conversation_id,
        turn_id=message_id,
        trace_id=get_log_value("trace_id"),
        captain_run_id=captain_run_id,
        delegated=True,
        permission_axes=(
            json.dumps(permission_axes.to_dict()) if permission_axes is not None else None
        ),
    )
    # 云对话多端同权 B2 §2.2: attention addressee for cards raised inside this run.
    attention_token = bind_attention_scope(
        user_id=user_id,
        conversation_id=conversation_id,
        turn_id=message_id,
    )
    roster_writer = SessionRosterWriter.wrap(session_saver)
    session_saver_wrapped = roster_writer.save if roster_writer is not None else None
    wire_roster_for_turn(
        conversation_id, roster_writer=roster_writer, session_loader=session_loader
    )
    history_token = turn_history.set(history)
    citations: list[dict] = []
    citations_token = turn_citations.set(citations)
    evidence_ledger = EvidenceLedgerCore(id_prefix="#r")
    ledger_token = turn_evidence_ledger.set(evidence_ledger)
    llm: _AsyncCloseable | None = None
    execution_id_token: Token[str | None] | None = None
    bound_execution_id: str | None = None
    delegate_tool = None

    try:
        prepared = await prepare_fresh_turn(
            conversation_id=conversation_id,
            user_id=user_id,
            backend=backend,
            sink=sink,
            folder_id=folder_id,
            board_id=board_id,
            attachments=None,
            permission_axes=permission_axes,
            llm_credentials=llm_credentials,
            x_client_platform=x_client_platform,
            profiles=profiles,
        )
        llm = cast(_AsyncCloseable, prepared.llm)
        bound_execution_id = prepared.bound_execution_id
        execution_id_token = cast(Token[str | None], prepared.execution_id_token)

        assembled = await assemble_ceo_turn(
            prepared=prepared,
            conversation_id=conversation_id,
            user_message=user_message,
            history=history,
            sink=sink,
            backend=backend,
            folder_id=folder_id,
            approvals_enabled=True,
            permission_axes=permission_axes,
            profiles=profiles,
            captain_run_id=captain_run_id,
            message_id=message_id,
            session_saver=session_saver_wrapped,
            session_loader=session_loader,
            suspension_saver=suspension_saver,
            suspension_deleter=suspension_deleter,
            x_client_platform=x_client_platform,
        )

        sink.emit(message_start(message_id, conversation_id=conversation_id))

        delegate_tool = assembled.delegate_tool
        delegate_tool._topology_lock = True
        delegate_tool._workflow_id = workflow_id
        delegate_tool._workflow_version = workflow_version

        result = await delegate_tool.execute(
            {"tasks": tasks},
            prepared.base_tool_context,
        )

        final_text = (
            str(result.final_text or result.output or "").strip()
            if result.success
            else str(result.error or result.output or "工作流未能执行。").strip()
        )
        if final_text:
            sink.emit(content_delta(final_text))

        from agentcore.runtime.captain_profile import apply_captain_max_rounds

        profile = apply_captain_max_rounds(profiles.get("chat"))
        model = profiles.model_for("chat") or ""
        zero_usage = {
            "input": 0,
            "output": 0,
            "reasoning": 0,
            "cache_hit": 0,
            "cache_miss": 0,
        }
        zero_cost = {
            "input": 0,
            "cached": 0,
            "output": 0,
            "total": 0,
            "currency": "CNY",
            "pricing_source": "curated",
            "credential_source": "platform",
        }
        # Workers bill via run_ledger; captain row stays zero (mechanism-direct).
        finish = FinishReason.END_TURN
        if getattr(result, "effect", None) is not None:
            from agentcore.core.types import ToolEffect

            if result.effect is ToolEffect.SUSPEND:
                finish = FinishReason.PAUSED
        if not result.success and finish is not FinishReason.PAUSED:
            finish = FinishReason.ERROR

        captain_state = SimpleNamespace(
            content=final_text,
            reasoning="",
            rounds=1,
            usage=dict(zero_usage),
            cost=dict(zero_cost),
            model=model,
            duration_ms=0,
            finish_override=finish,
        )

        settled = await settle_successful_turn(
            message_id=message_id,
            captain_run_id=captain_run_id,
            captain_state=captain_state,
            delegate_tool=delegate_tool,
            debate_tool=assembled.debate_tool,
            profile=profile,
            citations=citations,
            vision_cost_sink=prepared.vision_cost_sink,
            sink=sink,
            fact_log=fact_log,
            audit_recorder=audit_recorder,
            roster_writer=roster_writer,
            journal_writer=journal_writer,
        )
        settled["workflow_id"] = workflow_id
        settled["workflow_version"] = workflow_version
        return settled
    except Exception as e:
        logger.exception("workflow.pipeline_failed", error=str(e), workflow_id=workflow_id)
        salvaged = await salvage_pipeline_exception(
            e=e,
            message_id=message_id,
            sink=sink,
            fact_log=fact_log,
            audit_recorder=audit_recorder,
            roster_writer=roster_writer,
        )
        salvaged["workflow_id"] = workflow_id
        salvaged["workflow_version"] = workflow_version
        return salvaged
    finally:
        # Cancel-safe teardown (see ``teardown_step``): a second Stop must not pierce
        # the block and skip the remaining flushes / release.
        from agentcore.runtime.interaction_orphan import orphan_registry_pending
        from agentcore.runtime.pipeline.teardown import teardown_step

        await teardown_step(
            orphan_registry_pending(conversation_id, turn_id=message_id),
            step="orphan_registry_pending",
        )
        current_fact_log.reset(fact_log_token)
        await teardown_step(journal_writer.flush(), step="journal_flush")
        current_journal_writer.reset(journal_token)
        from agentcore.runtime.audit.recorder import current_audit_recorder

        await teardown_step(audit_recorder.flush(), step="audit_flush")
        if roster_writer is not None:
            await teardown_step(roster_writer.flush(), step="roster_flush")
        current_audit_recorder.reset(audit_token)
        reset_attention_scope(attention_token)
        turn_history.reset(history_token)
        turn_citations.reset(citations_token)
        turn_evidence_ledger.reset(ledger_token)
        if execution_id_token is not None:
            from agentcore.runtime.coordination.session import (
                current_execution_id,
                release_turn_coordination,
            )

            live_eid = current_execution_id.get() or bound_execution_id
            if live_eid:
                with contextlib.suppress(Exception):
                    release_turn_coordination(
                        live_eid, conversation_id=conversation_id
                    )
            current_execution_id.reset(execution_id_token)
        if llm is not None:
            await teardown_step(llm.close(), step="llm_close")
