"""Execute one standing-task fire (代跑, approvals_enabled=True).

Shared with handoff / workflows: only ``spawn_background``. Credential preflight
matches ``workflows.runner`` (``preflight_resolved_llm_credentials``) — **not**
handoff's thin ``resolve_user_llm_credentials``. No unified job framework; pause
truth stays in ``paused_turns`` (not handoff tables).
"""

from __future__ import annotations

from datetime import UTC, datetime

from agentcore.billing.gate import preflight_resolved_llm_credentials
from agentcore.conversation.background import spawn_background
from agentcore.conversation.common import (
    resolve_permission_axes,
    resolve_profile_set,
)
from agentcore.conversation.history import load_chat_context
from agentcore.conversation.turn_backend import build_turn_backend
from agentcore.conversation.turn_runner import run_and_persist
from agentcore.core.errors import AgentCoreError
from agentcore.core.logging import get_logger
from agentcore.core.types import PermissionAxes
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import (
    ConversationRepository,
    CostEventRepository,
    FolderRepository,
    MessageRepository,
    PausedTurnRepository,
    UserRepository,
)
from agentcore.db.repositories.standing_tasks import (
    StandingTaskRepository,
    StandingTaskRunRepository,
)
from agentcore.llm.resolve import resolve_conversation_model_selection
from agentcore.runtime.events import EventSink, FinishReason
from agentcore.standing_tasks.schedule import next_run_after
from agentcore.standing_tasks.templates import (
    DAILY_CONVERSATION_REVIEW,
    compose_template_fire_message,
    is_known_template,
)
from agentcore.standing_tasks.webhook import build_fire_message

logger = get_logger(__name__)

_SUMMARY_MAX = 500

# Inbox copy for a fire the task-level mutex refused (STD-A4). Terminal on
# purpose: the event is lost, not queued — the user decides whether to re-fire.
_LEASE_BUSY_ERROR = "上一次代跑仍在进行中，本次触发未执行（未自动补跑）"


def _truncate_summary(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = text.strip()
    if len(cleaned) <= _SUMMARY_MAX:
        return cleaned
    return cleaned[: _SUMMARY_MAX - 1] + "…"


def _finish_is_paused(finish: object) -> bool:
    if finish is FinishReason.PAUSED:
        return True
    return getattr(finish, "value", finish) == "paused"


async def _ensure_pinned_conversation(
    *,
    task_id: str,
    user_id: str,
    folder_id: str,
    name: str,
    permission_axes: dict,
) -> str:
    """Return the pinned conversation id, creating one on first fire.

    Create + attach share one unit-of-work (caller ``session.commit()``) so a
    mid-path failure cannot leave a committed orphan conversation.
    """
    async with async_session_factory() as session:
        tasks = StandingTaskRepository(session)
        task = await tasks.get_by_id(task_id)
        if task is None:
            raise RuntimeError(f"standing task gone: {task_id}")
        if task.conversation_id:
            return task.conversation_id
        axes = PermissionAxes.from_mapping(permission_axes).to_dict()
        conv = await ConversationRepository(session).create(
            user_id=user_id,
            title=name,
            folder_id=folder_id,
            mode="standing",
            permission_axes=axes,
            commit=False,
        )
        await tasks.attach_conversation(
            task_id, conversation_id=conv.id, commit=False
        )
        await session.commit()
        return conv.id


async def run_standing_task_job(
    *,
    run_id: str,
    task_id: str,
    lease_owner: str | None = None,
    advance_schedule: bool = True,
    event_text: str | None = None,
    trigger_source: str = "schedule",
) -> None:
    """Run one claimed standing-task fire end-to-end.

    ``trigger_source`` — not ``advance_schedule`` — decides whether a task
    disabled between claim and start still runs: the scheduler pre-advances the
    clock and so passes ``advance_schedule=False``, which used to make that guard
    unreachable. Webhook fires are refused at the route while manual「立即跑一次」
    deliberately runs disabled tasks (验收 / 收件箱重跑), so only the schedule path
    aborts here.
    """
    sink = EventSink()
    conversation_id: str | None = None
    try:
        async with async_session_factory() as session:
            task = await StandingTaskRepository(session).get_by_id(task_id)
            if task is None:
                await StandingTaskRunRepository(session).mark_failed(
                    run_id, error="站立任务不存在"
                )
                return
            if not task.enabled and trigger_source == "schedule":
                # Disabled after claim (race) — abort without advancing further.
                await StandingTaskRunRepository(session).mark_failed(
                    run_id, error="站立任务已停用"
                )
                return
            user_id = task.user_id
            folder_id = task.folder_id
            goal = task.goal
            name = task.name
            permission_axes = dict(task.permission_axes or {})
            cron = task.cron
            trigger_kind = getattr(task, "trigger_kind", None) or "schedule"
            template_key = getattr(task, "template_key", None)
            template_config = dict(getattr(task, "template_config", None) or {})
            workflow_id = getattr(task, "workflow_id", None)
            # Cloud folder guard (defense in depth; create already rejects local).
            folder = await FolderRepository(session).get_by_id(folder_id, user_id=user_id)
            if folder is None or folder.local_root_id:
                await StandingTaskRunRepository(session).mark_failed(
                    run_id, error="站立任务仅支持云工作区"
                )
                return
            folder_names: dict[str, str] = {}
            if is_known_template(template_key):
                scope_ids = list(template_config.get("folder_ids") or [])
                if folder_id and folder_id not in scope_ids:
                    folder_names[folder_id] = folder.name
                for fid in scope_ids:
                    frow = await FolderRepository(session).get_by_id(fid, user_id=user_id)
                    if frow is not None:
                        folder_names[fid] = frow.name
            pinned_conversation_id = task.conversation_id

            workflow_definition: dict | None = None
            workflow_version = 1
            workflow_name = name
            if workflow_id:
                from agentcore.db.repositories.user_workflows import UserWorkflowRepository

                wf = await UserWorkflowRepository(session).get_by_id(
                    workflow_id, user_id=user_id
                )
                if wf is None:
                    await StandingTaskRunRepository(session).mark_failed(
                        run_id, error="绑定的工作流不存在或已删除"
                    )
                    return
                workflow_definition = dict(wf.definition or {})
                workflow_version = int(wf.version or 1)
                workflow_name = wf.name

        # Hard gate: daily review with nothing in scope → succeed without LLM.
        if template_key == DAILY_CONVERSATION_REVIEW:
            from agentcore.standing_tasks.review_preflight import (
                EMPTY_REVIEW_SUMMARY,
                count_recent_conversations_in_scope,
            )

            recent_n = await count_recent_conversations_in_scope(
                user_id=user_id,
                template_config=template_config,
                exclude_conversation_id=pinned_conversation_id,
            )
            if recent_n == 0:
                async with async_session_factory() as session:
                    await StandingTaskRunRepository(session).mark_succeeded(
                        run_id, summary=EMPTY_REVIEW_SUMMARY
                    )
                    if advance_schedule and trigger_kind == "schedule" and cron:
                        try:
                            nxt = next_run_after(cron, datetime.now(UTC))
                            await StandingTaskRepository(session).advance_next_run(
                                task_id, next_run_at=nxt
                            )
                        except Exception as e:  # noqa: BLE001
                            logger.warning(
                                "standing_task.next_run_failed",
                                task_id=task_id,
                                error=str(e),
                            )
                logger.info(
                    "standing_task.empty_review_skip",
                    run_id=run_id,
                    task_id=task_id,
                )
                return

        if is_known_template(template_key):
            user_message = compose_template_fire_message(
                template_key=template_key or "",
                goal=goal,
                template_config=template_config,
                folder_names=folder_names,
                event_text=event_text,
            )
        elif workflow_id and workflow_definition is not None:
            from agentcore.workflows.runner import build_workflow_user_message

            # Bound workflow: goal + event_text are optional per-run supplements.
            supplement_parts = []
            goal_s = (goal or "").strip()
            if goal_s:
                supplement_parts.append(goal_s)
            event_s = (event_text or "").strip()
            if event_s:
                supplement_parts.append(event_s)
            note = "\n\n".join(supplement_parts) if supplement_parts else None
            user_message = build_workflow_user_message(
                workflow_name=workflow_name, note=note
            )
        else:
            user_message = build_fire_message(goal=goal, event_text=event_text)

        conversation_id = await _ensure_pinned_conversation(
            task_id=task_id,
            user_id=user_id,
            folder_id=folder_id,
            name=name,
            permission_axes=permission_axes,
        )

        async with async_session_factory() as session:
            user = await UserRepository(session).get_by_id(user_id)
            if user is None:
                await StandingTaskRunRepository(session).mark_failed(
                    run_id, error="用户不存在"
                )
                return
            conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
            if conv is None:
                await StandingTaskRunRepository(session).mark_failed(
                    run_id, error="绑定对话不存在"
                )
                return
            try:
                selection = await resolve_conversation_model_selection(
                    session, conv, user_id
                )
                credentials = await preflight_resolved_llm_credentials(
                    session=session,
                    user=user,
                    cost_repo=CostEventRepository(session),
                    byok_missing_message="站立任务代跑需要可用的模型凭证，请先在设置中配置。",
                    selection=selection,
                )
            except AgentCoreError as e:
                await StandingTaskRunRepository(session).mark_failed(
                    run_id, error=e.message or str(e)
                )
                return
            profile_set = await resolve_profile_set(session, conv, user_id)
            axes = await resolve_permission_axes(session, conversation_id)

        backend = await build_turn_backend(
            user_id=user_id,
            conversation_id=conversation_id,
            folder_id=folder_id,
            sink=sink,
            local_binding=None,
        )

        async with async_session_factory() as session:
            user_msg = await MessageRepository(session).create(
                conversation_id=conversation_id,
                role="user",
                content=user_message,
            )
            history = await load_chat_context(session, conversation_id)
            await StandingTaskRunRepository(session).set_conversation_and_message(
                run_id,
                conversation_id=conversation_id,
                user_message_id=user_msg.id,
            )

        # Monkeypatch seam for unit tests (see test_standing_tasks.py).
        if workflow_id and workflow_definition is not None:
            result = await _run_workflow_pipeline(
                conversation_id=conversation_id,
                user_message=user_message,
                user_id=user_id,
                folder_id=folder_id,
                sink=sink,
                history=history[:-1],
                backend=backend,
                llm_credentials=credentials,
                profile_set=profile_set,
                permission_axes=axes,
                workflow_id=workflow_id,
                workflow_version=workflow_version,
                workflow_name=workflow_name,
                definition=workflow_definition,
            )
        else:
            result = await _run_pipeline(
                conversation_id=conversation_id,
                user_message=user_message,
                user_id=user_id,
                folder_id=folder_id,
                sink=sink,
                history=history[:-1],
                backend=backend,
                llm_credentials=credentials,
                profile_set=profile_set,
                permission_axes=axes,
            )

        finish = (result or {}).get("finish_reason") if isinstance(result, dict) else None
        summary = _truncate_summary(
            (result or {}).get("content") if isinstance(result, dict) else None
        )
        turn_message_id = (
            (result or {}).get("message_id") if isinstance(result, dict) else None
        )
        paused = _finish_is_paused(finish)

        async with async_session_factory() as session:
            # ST-1 / option B: probe THIS fire's turn only — conversation-level ANY
            # would mis-label a successful fire when an older cold pause still sits.
            if not paused and turn_message_id:
                paused = await PausedTurnRepository(session).exists_for_message(
                    str(turn_message_id)
                )
            runs = StandingTaskRunRepository(session)
            if paused:
                await runs.mark_awaiting_user(run_id, summary=summary)
            elif isinstance(result, dict) and (
                result.get("error")
                or getattr(finish, "value", finish) in ("error", "cancelled")
            ):
                await runs.mark_failed(
                    run_id, error=str(result.get("error") or "回合失败")
                )
            else:
                # Prefer assistant content from DB when pipeline result lacked it.
                if not summary:
                    recent = await MessageRepository(session).list_recent(
                        conversation_id, limit=1
                    )
                    if recent and recent[0].role == "assistant":
                        summary = _truncate_summary(recent[0].content)
                await runs.mark_succeeded(run_id, summary=summary)

            # Only advance cron for schedule triggers that still have a cron expression.
            if advance_schedule and trigger_kind == "schedule" and cron:
                try:
                    nxt = next_run_after(cron, datetime.now(UTC))
                    await StandingTaskRepository(session).advance_next_run(
                        task_id, next_run_at=nxt
                    )
                except Exception as e:  # noqa: BLE001 — schedule math must not hide run status
                    logger.warning(
                        "standing_task.next_run_failed",
                        task_id=task_id,
                        error=str(e),
                    )

        logger.info(
            "standing_task.run_finished",
            run_id=run_id,
            task_id=task_id,
            conversation_id=conversation_id,
            paused=bool(paused) if conversation_id else False,
            turn_message_id=turn_message_id,
        )
    except Exception as e:
        logger.error(
            "standing_task.run_failed",
            run_id=run_id,
            task_id=task_id,
            error=str(e),
            exc_info=True,
        )
        async with async_session_factory() as session:
            await StandingTaskRunRepository(session).mark_failed(run_id, error=str(e))
    finally:
        if lease_owner is not None:
            async with async_session_factory() as session:
                await StandingTaskRepository(session).clear_lease(
                    task_id, owner=lease_owner
                )
        if not sink._closed:
            sink.close(reason="standing_task_finally")


async def _run_pipeline(**kwargs):
    """Run the chat turn. Returns the pipeline result (incl. ``message_id``).

    Production uses ``run_and_persist``; pause truth for inbox settlement is
    ``paused_turns`` keyed by that turn's ``message_id`` (not conversation ANY).
    """
    # Production: full persist path (suspension + cost + journal).
    return await run_and_persist(
        conversation_id=kwargs["conversation_id"],
        user_message=kwargs["user_message"],
        user_id=kwargs["user_id"],
        folder_id=kwargs["folder_id"],
        sink=kwargs["sink"],
        history=kwargs["history"],
        attachments=None,
        backend=kwargs["backend"],
        llm_credentials=kwargs["llm_credentials"],
        profile_set=kwargs.get("profile_set"),
        permission_axes=kwargs.get("permission_axes"),
    )


async def _run_workflow_pipeline(**kwargs):
    """Standing fire with a bound workflow: direct-start (no CEO 编队).

    Shares :func:`~agentcore.conversation.turn_runner.run_mechanism_direct_and_persist`
    with ``workflows.runner`` (placeholder / lease / persist). Test seam: monkeypatch
    this like ``_run_pipeline``.
    """
    from agentcore.conversation.turn_runner import run_mechanism_direct_and_persist
    from agentcore.workflows.definition import (
        WorkflowDefinitionError,
        expand_workflow_to_tasks,
    )

    try:
        tasks = expand_workflow_to_tasks(kwargs["definition"])
    except WorkflowDefinitionError as e:
        return {"error": str(e), "finish_reason": "error"}

    return await run_mechanism_direct_and_persist(
        conversation_id=kwargs["conversation_id"],
        user_id=kwargs["user_id"],
        user_message=kwargs["user_message"],
        tasks=tasks,
        workflow_id=kwargs["workflow_id"],
        workflow_version=kwargs["workflow_version"],
        sink=kwargs["sink"],
        backend=kwargs["backend"],
        history=kwargs["history"],
        folder_id=kwargs["folder_id"],
        permission_axes=kwargs.get("permission_axes"),
        profile_set=kwargs.get("profile_set"),
        llm_credentials=kwargs["llm_credentials"],
    )


def spawn_standing_task_run(
    *,
    run_id: str,
    task_id: str,
    lease_owner: str | None = None,
    advance_schedule: bool = True,
    event_text: str | None = None,
    trigger_source: str = "schedule",
) -> None:
    """Fire-and-forget a standing-task job."""
    spawn_background(
        run_standing_task_job(
            run_id=run_id,
            task_id=task_id,
            lease_owner=lease_owner,
            advance_schedule=advance_schedule,
            event_text=event_text,
            trigger_source=trigger_source,
        )
    )


async def dispatch_standing_task(
    *,
    task_id: str,
    user_id: str,
    advance_schedule: bool = False,
    lease_owner: str | None = None,
    event_text: str | None = None,
    trigger_source: str = "manual",
) -> str:
    """Create a running inbox row and spawn the job. Returns ``run_id``.

    Used by the scheduler (``advance_schedule=True``), webhook hook, and the
    manual「立即跑一次」endpoint (``advance_schedule=False`` so the cron clock
    is untouched).

    Task-level mutex: when ``lease_owner`` is omitted (webhook / manual), claims
    the existing lease columns before spawning. An unexpired lease →
    ``ConflictError`` (HTTP 409) — never a silent second run. The scheduler
    path already claimed via ``claim_due`` and passes ``lease_owner``.

    A refused claim also lands a terminal inbox row for non-manual triggers, so a
    webhook event dropped by the mutex is visible to the user instead of只回一个
    409 到没人看的调用方。Manual「立即跑一次」skips the row: its caller *is* the
    user and already sees the 409. The dropped fire is **not** re-run.
    """
    from uuid import uuid4

    from agentcore.config import settings
    from agentcore.core.errors import ConflictError

    claimed_owner = lease_owner
    async with async_session_factory() as session:
        tasks = StandingTaskRepository(session)
        task = await tasks.get_by_id(task_id, user_id=user_id)
        if task is None:
            raise LookupError("standing task not found")
        # Capture before claim commit (expire_on_commit may detach the row).
        pinned_conversation_id = task.conversation_id

        if claimed_owner is None:
            claimed_owner = f"dispatch-{uuid4().hex[:12]}"
            claimed = await tasks.claim_dispatch(
                task_id,
                owner=claimed_owner,
                lease_seconds=settings.standing_task_lease_seconds,
            )
            if claimed is None:
                if trigger_source != "manual":
                    runs = StandingTaskRunRepository(session)
                    skipped = await runs.create(
                        standing_task_id=task_id,
                        user_id=user_id,
                        conversation_id=pinned_conversation_id,
                        status="failed",
                        trigger_source=trigger_source,
                    )
                    await runs.mark_failed(skipped.id, error=_LEASE_BUSY_ERROR)
                    logger.info(
                        "standing_task.fire_skipped_busy",
                        run_id=skipped.id,
                        task_id=task_id,
                        trigger_source=trigger_source,
                    )
                raise ConflictError("站立任务正在执行中，请稍后再试")

        try:
            run = await StandingTaskRunRepository(session).create(
                standing_task_id=task_id,
                user_id=user_id,
                conversation_id=pinned_conversation_id,
                status="running",
                trigger_source=trigger_source,
            )
            run_id = run.id
        except Exception:
            if lease_owner is None and claimed_owner is not None:
                await tasks.clear_lease(task_id, owner=claimed_owner)
            raise

    spawn_standing_task_run(
        run_id=run_id,
        task_id=task_id,
        lease_owner=claimed_owner,
        advance_schedule=advance_schedule,
        event_text=event_text,
        trigger_source=trigger_source,
    )
    return run_id
