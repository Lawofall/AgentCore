"""Local→云 handoff: dispatch a cloud team run (双模式工作区 P2e / e2).

Shared with standing / workflows: only ``spawn_background``. Credentials stay on
the thin ``resolve_user_llm_credentials`` path (no billing preflight / no
``preflight_resolved_llm_credentials``). Pause semantics stay out — no
``paused_turns`` / awaiting_user on handoff jobs.
"""

import time

from agentcore.conversation.background import spawn_background
from agentcore.conversation.common import fallback_title, log_cost_recorded
from agentcore.core.error_codes import ErrorCode
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import (
    ConversationRepository,
    HandoffJobRepository,
    MessageRepository,
)
from agentcore.llm.resolve import LLMCredentials, resolve_user_llm_credentials
from agentcore.runtime.events import EventSink, error_event, handoff_job_started
from agentcore.runtime.journal import persist_turn_journal
from agentcore.runtime.pipeline import run_chat_pipeline
from agentcore.workspace.handoff import snapshot_local
from agentcore.workspace.locate import LocalBinding, build_server_workspace
from agentcore.workspace.snapshots import create_snapshot, restore_into_workspace

logger = get_logger(__name__)


async def persist_job_turn(*, user_id: str, conversation_id: str, result: dict) -> None:
    """Persist a handoff job's assistant reply + cost ledger under the job conv."""
    assistant_reply = result.get("content") or ""
    cost_runs = result.get("cost_runs") or []
    ledger_drained = None
    if cost_runs or result.get("message_id"):
        from agentcore.billing.turn_ledger import drain_cost_ledger_before_reconcile

        # Drain before main-pool session — same pool discipline as cloud finalize.
        ledger_drained = await drain_cost_ledger_before_reconcile(
            conversation_id=conversation_id,
            message_id=result.get("message_id"),
        )
    async with async_session_factory() as session:
        if assistant_reply:
            await MessageRepository(session).create(
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_reply,
                reasoning_content=result.get("reasoning_content") or None,
                citations=result.get("citations") or None,
                message_id=result.get("message_id"),
                metadata={
                    "input_tokens": result.get("input_tokens", 0),
                    "output_tokens": result.get("output_tokens", 0),
                    "reasoning_tokens": result.get("reasoning_tokens", 0),
                    "cache_hit_tokens": result.get("cache_hit_tokens", 0),
                    "cache_miss_tokens": result.get("cache_miss_tokens", 0),
                    "rounds": result.get("rounds", 0),
                },
            )
            await persist_turn_journal(
                session,
                message_id=result.get("message_id"),
                conversation_id=conversation_id,
                trace_id=None,
                entries=result.get("journal_entries"),
                replace=False,
            )
        if ledger_drained is not None:
            try:
                from agentcore.billing.turn_ledger import reconcile_turn_cost_ledger

                ledger_rows = await reconcile_turn_cost_ledger(
                    session,
                    drained=ledger_drained,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message_id=result.get("message_id"),
                    cost_runs=list(cost_runs),
                )
                if ledger_rows:
                    log_cost_recorded(conversation_id, result.get("message_id"), ledger_rows)
            except Exception as e:
                await session.rollback()
                logger.warning(
                    "handoff.cost_ledger_failed",
                    conversation_id=conversation_id,
                    error=str(e),
                )
                from agentcore.billing.cost_ledger_queue import get_cost_ledger_queue

                if cost_runs:
                    get_cost_ledger_queue().enqueue_runs(
                        user_id=user_id,
                        conversation_id=conversation_id,
                        message_id=result.get("message_id"),
                        runs=list(cost_runs),
                        source="handoff",
                    )


async def run_handoff_job(
    *,
    job_id: str,
    user_id: str,
    source_folder_id: str | None,
    source_conversation_id: str,
    job_conversation_id: str,
    base_snapshot_id: str,
    task: str,
    llm_credentials: LLMCredentials | None = None,
) -> None:
    """Run a dispatched cloud team on the local snapshot, detached (P2e / e2)."""
    async with async_session_factory() as session:
        await HandoffJobRepository(session).mark_running(job_id)
        await MessageRepository(session).create(
            conversation_id=job_conversation_id, role="user", content=task
        )

    sink = EventSink()
    try:
        # restore / create_snapshot hold workspace_lock at the sink; pipeline must not.
        await restore_into_workspace(
            source_user_id=user_id,
            source_folder_id=source_folder_id,
            source_conversation_id=source_conversation_id,
            snapshot_id=base_snapshot_id,
            dest_user_id=user_id,
            dest_folder_id=None,
            dest_folder_rel_path=None,
            dest_conversation_id=job_conversation_id,
        )
        backend = build_server_workspace(
            user_id=user_id,
            folder_id=None,
            folder_rel_path=None,
            conversation_id=job_conversation_id,
        )
        result = await run_chat_pipeline(
            conversation_id=job_conversation_id,
            user_message=task,
            history=[],
            sink=sink,
            user_id=user_id,
            backend=backend,
            approvals_enabled=False,
            llm_credentials=llm_credentials,
        )
        await persist_job_turn(
            user_id=user_id, conversation_id=job_conversation_id, result=result
        )
        result_ref = await create_snapshot(
            user_id=user_id,
            folder_id=None,
            folder_rel_path=None,
            conversation_id=job_conversation_id,
            label=f"result:{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        )
        async with async_session_factory() as session:
            await HandoffJobRepository(session).mark_succeeded(
                job_id, result_snapshot_id=result_ref.snapshot_id
            )
        logger.info(
            "handoff.job_succeeded",
            job_id=job_id,
            job_conversation_id=job_conversation_id,
            result_snapshot_id=result_ref.snapshot_id,
        )
    except Exception as e:
        logger.error("handoff.job_failed", job_id=job_id, error=str(e), exc_info=True)
        async with async_session_factory() as session:
            await HandoffJobRepository(session).mark_failed(job_id, error=str(e))
    finally:
        if not sink._closed:
            sink.close(reason="handoff_job_finally")


async def dispatch_handoff(
    *,
    conversation_id: str,
    user_id: str,
    folder_id: str | None,
    binding: LocalBinding,
    task: str,
    sink: EventSink,
) -> None:
    """Snapshot the local workspace, then spawn the cloud team run (P2e / e2)."""
    try:
        base_ref = await snapshot_local(
            user_id=user_id,
            folder_id=folder_id,
            conversation_id=conversation_id,
            binding=binding,
            sink=sink,
        )
        async with async_session_factory() as session:
            job_conv = await ConversationRepository(session).create(
                user_id=user_id,
                title=fallback_title(task) or "云端作业",
                mode="handoff",
                commit=False,
            )
            job = await HandoffJobRepository(session).create(
                user_id=user_id,
                source_conversation_id=conversation_id,
                job_conversation_id=job_conv.id,
                base_snapshot_id=base_ref.snapshot_id,
                task=task,
                commit=False,
            )
            await session.commit()
            credentials = await resolve_user_llm_credentials(session, user_id)

        spawn_background(
            run_handoff_job(
                job_id=job.id,
                user_id=user_id,
                source_folder_id=folder_id,
                source_conversation_id=conversation_id,
                job_conversation_id=job_conv.id,
                base_snapshot_id=base_ref.snapshot_id,
                task=task,
                llm_credentials=credentials,
            )
        )
        sink.emit(
            handoff_job_started(
                job_id=job.id,
                conversation_id=conversation_id,
                job_conversation_id=job_conv.id,
            )
        )
    except Exception as e:
        logger.warning("handoff.dispatch_failed", conversation_id=conversation_id, error=str(e))
        sink.emit(error_event(ErrorCode.HANDOFF_DISPATCH_FAILED, str(e)))
    finally:
        if not sink._closed:
            sink.close(reason="handoff_dispatch_finally")
