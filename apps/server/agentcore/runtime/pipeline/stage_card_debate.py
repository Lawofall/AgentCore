"""Mechanism-direct debate turn from a stage_card resolve（批 B）.

Skips CEO react_loop: prepare → assemble tools → ``debate.execute(skip_kickoff=True)``
→ bubble = ``to_ceo_output``（直出省一跳）→ settle. ``authorized_by=stage_card``.
"""

from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace
from typing import Any

from agentcore.attention import bind_attention_scope, reset_attention_scope
from agentcore.core.log_context import get_log_value
from agentcore.core.logging import get_logger
from agentcore.core.types import PermissionAxes, new_id
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.profiles import TurnProfiles as ProfileSet
from agentcore.llm.profiles import turn_profiles_for_turn
from agentcore.llm.provider.protocol import TokenUsage
from agentcore.runtime.audit.hooks import bind_recorder
from agentcore.runtime.events import EventSink, FinishReason, content_delta, message_start
from agentcore.runtime.evidence_ledger import EvidenceLedgerCore
from agentcore.runtime.facts import TurnFactLog, current_fact_log
from agentcore.runtime.journal.writer import TurnJournalWriter, current_journal_writer
from agentcore.runtime.kickoff.stage_card import debate_arguments_from_card
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


async def run_stage_card_debate_pipeline(
    *,
    conversation_id: str,
    user_id: str,
    sink: EventSink,
    backend: WorkspaceBackend,
    card: dict[str, Any],
    note: str = "",
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
    """Run debate from stage-card params; bubble = ``to_ceo_output``."""
    message_id = message_id or new_id()
    captain_run_id = new_id()
    profiles = turn_profiles_for_turn(profile_set, llm_credentials)
    history = list(history or [])
    user_message = f"按此开辩：{str(card.get('motion') or '').strip()}"

    fact_log = TurnFactLog()
    fact_log_token = current_fact_log.set(fact_log)
    journal_writer = TurnJournalWriter(
        turn_id=message_id,
        conversation_id=conversation_id,
        trace_id=get_log_value("trace_id"),
    )
    journal_token = current_journal_writer.set(journal_writer)
    audit_recorder, audit_token = bind_recorder(
        user_id=user_id,
        conversation_id=conversation_id,
        turn_id=message_id,
        trace_id=get_log_value("trace_id"),
        captain_run_id=captain_run_id,
        delegated=False,
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
    llm = None
    execution_id_token = None
    bound_execution_id: str | None = None
    debate_tool = None
    stage_card_finalized = False

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
        llm = prepared.llm
        bound_execution_id = prepared.bound_execution_id
        execution_id_token = prepared.execution_id_token

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

        debate_tool = assembled.debate_tool
        debate_tool._debate_authorized_by = "stage_card"
        debate_tool._debate_stage_card = dict(card)
        args = debate_arguments_from_card(card, note=note)
        result = await debate_tool.execute(
            args, prepared.base_tool_context, skip_kickoff=True
        )
        stage_card_finalized = bool(
            getattr(debate_tool, "_stage_card_finalized_at_start", False)
        )

        final_text = (
            str(result.final_text or result.output or "").strip()
            if result.success
            else str(result.output or "辩论未能开赛。").strip()
        )
        if final_text:
            sink.emit(content_delta(final_text))

        from dataclasses import asdict

        from agentcore.llm.pricing import calculate_cost
        from agentcore.runtime.captain_profile import apply_captain_max_rounds

        profile = apply_captain_max_rounds(profiles.get("chat"))
        model = profiles.model_for("chat") or ""
        # 机制直起无 CEO LLM：settle 折入 debate_tool.usage + run_ledger。
        # captain 行保持 0；若 ledger 空但有 usage（异常短路径）则计价落到 captain，
        # 并清零 debate usage 防 token 双计。
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
        captain_usage = dict(zero_usage)
        captain_cost = dict(zero_cost)
        if not debate_tool.run_ledger:
            debate_usage = TokenUsage.from_usage_dict(debate_tool.usage)
            if debate_usage.total_tokens or debate_usage.reasoning_tokens:
                priced = asdict(calculate_cost(model, debate_usage))
                captain_cost = {
                    **zero_cost,
                    "input": int(priced.get("input", 0) or 0),
                    "cached": int(priced.get("cached", 0) or 0),
                    "output": int(priced.get("output", 0) or 0),
                    "total": int(priced.get("total", 0) or 0),
                    "currency": str(priced.get("currency") or "CNY"),
                    "pricing_source": str(priced.get("pricing_source") or "curated"),
                    "credential_source": str(
                        priced.get("credential_source") or "platform"
                    ),
                }
                captain_usage = debate_usage.as_dict()
                for key in debate_tool._acc.usage:
                    debate_tool._acc.usage[key] = 0
        captain_state = SimpleNamespace(
            content=final_text,
            reasoning="",
            rounds=1,
            usage=captain_usage,
            cost=captain_cost,
            model=model,
            duration_ms=0,
            finish_override=(
                FinishReason.END_TURN if result.success else FinishReason.ERROR
            ),
        )

        settled = await settle_successful_turn(
            message_id=message_id,
            captain_run_id=captain_run_id,
            captain_state=captain_state,
            delegate_tool=assembled.delegate_tool,
            debate_tool=debate_tool,
            profile=profile,
            citations=citations,
            vision_cost_sink=prepared.vision_cost_sink,
            sink=sink,
            fact_log=fact_log,
            audit_recorder=audit_recorder,
            roster_writer=roster_writer,
            journal_writer=journal_writer,
        )
        settled["stage_card_finalized"] = stage_card_finalized
        return settled
    except Exception as e:
        logger.exception("stage_card.debate_pipeline_failed", error=str(e))
        if debate_tool is not None:
            stage_card_finalized = bool(
                getattr(debate_tool, "_stage_card_finalized_at_start", False)
            )
        salvaged = await salvage_pipeline_exception(
            e=e,
            message_id=message_id,
            sink=sink,
            fact_log=fact_log,
            audit_recorder=audit_recorder,
            roster_writer=roster_writer,
        )
        salvaged["stage_card_finalized"] = stage_card_finalized
        return salvaged
    finally:
        with contextlib.suppress(Exception):
            from agentcore.runtime.interaction_orphan import orphan_registry_pending

            await orphan_registry_pending(conversation_id, turn_id=message_id)
        current_fact_log.reset(fact_log_token)
        with contextlib.suppress(Exception):
            await journal_writer.flush()
        current_journal_writer.reset(journal_token)
        from agentcore.runtime.audit.recorder import current_audit_recorder

        with contextlib.suppress(Exception):
            await audit_recorder.flush()
        with contextlib.suppress(Exception):
            if roster_writer is not None:
                await roster_writer.flush()
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
            with contextlib.suppress(Exception):
                await llm.close()
