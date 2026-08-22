"""Dispatch + run a user workflow via mechanism-direct (no CEO 编队).

Turn envelope is ``run_mechanism_direct_and_persist`` (shared with standing
bound-workflow): placeholder / lease / log_context / persist. Inner execution
remains ``run_workflow_pipeline``. Credential preflight shares
``preflight_resolved_llm_credentials`` with ``standing_tasks.runner``. Shared
with handoff: only ``spawn_background``. Pause truth is ``paused_turns``.
"""

from __future__ import annotations

from collections.abc import Mapping

from agentcore.billing.gate import preflight_resolved_llm_credentials
from agentcore.conversation.background import spawn_background
from agentcore.conversation.common import (
    default_permission_axes_for_user,
    resolve_permission_axes,
    resolve_profile_set,
)
from agentcore.conversation.history import load_chat_context
from agentcore.conversation.turn_backend import build_turn_backend
from agentcore.core.logging import get_logger
from agentcore.core.types import PermissionAxes
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import (
    ConversationRepository,
    CostEventRepository,
    FolderRepository,
    MessageRepository,
    UserRepository,
)
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.resolve import (
    resolve_account_default_model,
    resolve_conversation_model_selection,
)
from agentcore.runtime.events import EventSink
from agentcore.workflows.definition import (
    WorkflowDefinitionError,
    expand_workflow_to_tasks,
)

logger = get_logger(__name__)


def build_workflow_user_message(*, workflow_name: str, note: str | None = None) -> str:
    """User-visible kickoff line for a workflow direct-start turn."""
    base = f"按工作流「{workflow_name}」执行。"
    cleaned = (note or "").strip()
    if cleaned:
        return f"{base}\n\n本轮补充：\n{cleaned}"
    return base


async def dispatch_workflow_run(
    *,
    user_id: str,
    workflow_id: str,
    workflow_version: int,
    definition: dict,
    folder_id: str,
    note: str | None = None,
    conversation_id: str | None = None,
    workflow_name: str = "工作流",
    permission_axes: dict | None = None,
    slot_values: Mapping[str, str] | None = None,
) -> str:
    """Validate definition, preflight credentials, ensure conversation, spawn job.

    Credential gate runs synchronously (same semantics as conversation turn
    preflight) before create/spawn so a refused run returns 402/429/503 instead
    of a 200 「已开跑」 shell. Returns conversation id only after admit.

    ``slot_values`` swaps this run's inputs into the definition's ``{{key}}``
    placeholders; unfilled slots keep their default (the value the workflow was
    frozen from), so an untouched form reruns the original turn verbatim.
    """
    try:
        tasks = expand_workflow_to_tasks(definition, slot_values=slot_values)
    except WorkflowDefinitionError as e:
        raise ValueError(str(e)) from e
    if not tasks:
        raise ValueError("工作流没有可执行的队员步骤")

    async with async_session_factory() as session:
        folder = await FolderRepository(session).get_by_id(folder_id, user_id=user_id)
        if folder is None:
            raise LookupError("工作区不存在")
        user = await UserRepository(session).get_by_id(user_id)
        if user is None:
            raise LookupError("用户不存在")
        axes = permission_axes
        if axes is None:
            axes = (await default_permission_axes_for_user(session, user_id)).to_dict()
        conv_id = conversation_id
        if conv_id:
            conv = await ConversationRepository(session).get_by_id(
                conv_id, user_id=user_id
            )
            if conv is None:
                raise LookupError("对话不存在")
            if conv.folder_id and conv.folder_id != folder_id:
                raise ValueError("对话不属于所选工作区")
            selection = await resolve_conversation_model_selection(
                session, conv, user_id
            )
        else:
            # No conversation yet — match turn preflight with conv=None (account default).
            selection = await resolve_account_default_model(session, user_id)

        # Raise BYOKKeyMissingError / QuotaExceededError / PlatformBillingUnavailableError
        # before create or spawn (route maps AgentCoreError → 402/429/503).
        credentials = await preflight_resolved_llm_credentials(
            session=session,
            user=user,
            cost_repo=CostEventRepository(session),
            byok_missing_message="跑工作流需要可用的模型凭证，请先在设置中配置。",
            selection=selection,
        )

        if not conv_id:
            conv = await ConversationRepository(session).create(
                user_id=user_id,
                title=workflow_name,
                folder_id=folder_id,
                mode="workflow",
                permission_axes=PermissionAxes.from_mapping(axes).to_dict(),
            )
            conv_id = conv.id

    spawn_background(
        run_workflow_job(
            conversation_id=conv_id,
            user_id=user_id,
            folder_id=folder_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            workflow_name=workflow_name,
            tasks=tasks,
            note=note,
            llm_credentials=credentials,
        )
    )
    return conv_id


async def run_workflow_job(
    *,
    conversation_id: str,
    user_id: str,
    folder_id: str,
    workflow_id: str,
    workflow_version: int,
    workflow_name: str,
    tasks: list[dict],
    note: str | None = None,
    llm_credentials: LLMCredentials | None = None,
) -> None:
    """Background: persist user message + mechanism-direct turn envelope.

    Inner pipeline remains ``run_workflow_pipeline``; outer envelope is
    ``run_mechanism_direct_and_persist`` (same as standing bound-workflow).
    ``llm_credentials`` come from the sync dispatch preflight (do not re-gate here).
    """
    sink = EventSink()
    try:
        user_message = build_workflow_user_message(workflow_name=workflow_name, note=note)
        async with async_session_factory() as session:
            conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
            if conv is None:
                logger.error(
                    "workflow.run_conversation_missing",
                    conversation_id=conversation_id,
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
            await MessageRepository(session).create(
                conversation_id=conversation_id,
                role="user",
                content=user_message,
            )
            history = await load_chat_context(session, conversation_id)

        from agentcore.conversation.turn_runner import (
            run_mechanism_direct_and_persist,
        )

        # Shared envelope with standing bound-workflow (placeholder/lease/persist).
        await run_mechanism_direct_and_persist(
            conversation_id=conversation_id,
            user_id=user_id,
            user_message=user_message,
            tasks=tasks,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            sink=sink,
            backend=backend,
            history=history[:-1],
            folder_id=folder_id,
            permission_axes=axes,
            profile_set=profile_set,
            llm_credentials=llm_credentials,
        )
        logger.info(
            "workflow.run_finished",
            conversation_id=conversation_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
        )
    except Exception as e:
        logger.error(
            "workflow.run_failed",
            conversation_id=conversation_id,
            workflow_id=workflow_id,
            error=str(e),
            exc_info=True,
        )
    finally:
        if not sink._closed:
            sink.close(reason="workflow_finally")
