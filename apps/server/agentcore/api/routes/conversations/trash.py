"""Conversation-scoped AgentCore/trash list + restore (alias of workspace routes)."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_conversation_repo, get_db
from agentcore.api.schemas import (
    StatusResponse,
    TrashEntrySummary,
    TrashListResponse,
)
from agentcore.conversation.common import resolve_local_binding
from agentcore.core.errors import ConflictError, NotFoundError, ValidationError
from agentcore.db.models import Conversation
from agentcore.db.repositories import ConversationRepository
from agentcore.folders.placement import resolve_workspace_paths
from agentcore.workspace.locate import workspace_storage_key
from agentcore.workspace.locks import workspace_lock
from agentcore.workspace.protocol import AlreadyExists, OutsideWorkspace, WorkspaceIOError
from agentcore.workspace.trash import (
    TrashExpiredError,
    TrashNotFound,
    list_trash_entries,
    restore_from_trash,
    trash_retention_days,
)

from ._helpers import _get_owned_conversation

router = APIRouter(prefix="/conversations", tags=["conversations"])


async def _refuse_local_trash(
    session: AsyncSession, conv: Conversation, *, action: str
) -> None:
    """Local files are not on the server — AgentCore/trash REST is cloud-only.

    Local OS recycle-bin deletes are never listed here. Local no-OS-trash
    fallback entries live on the desktop root; restore via desktop UI.
    """
    if await resolve_local_binding(session, conv) is not None:
        raise ConflictError(
            f"本地工作区不能经 REST {action}工作区软删区；"
            "系统回收站请在本机恢复，GAgent/trash 兜底请在桌面端还原"
        )


@router.get("/{conversation_id}/trash", response_model=TrashListResponse)
async def list_conversation_trash(
    conversation_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    session: AsyncSession = Depends(get_db),
):
    """List ``AgentCore/trash`` entries for this conversation's workspace."""
    conv = await _get_owned_conversation(conversation_id, user.user_id, conv_repo)
    await _refuse_local_trash(session, conv, action="列出")
    root, internal_root = await resolve_workspace_paths(
        user_id=user.user_id,
        folder_id=conv.folder_id,
        conversation_id=conversation_id,
        session=session,
    )
    entries = list_trash_entries(root=root, internal_root=internal_root)
    days = trash_retention_days()
    return TrashListResponse(
        data=[
            TrashEntrySummary(
                entry_id=e.entry_id,
                original_path=e.original_path,
                name=e.name,
                is_dir=e.is_dir,
                deleted_at=e.deleted_at,
            )
            for e in entries
        ],
        total=len(entries),
        retention_days=days,
    )


@router.post(
    "/{conversation_id}/trash/{entry_id}/restore",
    response_model=StatusResponse,
)
async def restore_conversation_trash(
    conversation_id: str,
    entry_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    session: AsyncSession = Depends(get_db),
):
    """Restore one ``AgentCore/trash`` entry (cloud workspace only)."""
    conv = await _get_owned_conversation(conversation_id, user.user_id, conv_repo)
    await _refuse_local_trash(session, conv, action="还原")
    key = workspace_storage_key(
        user_id=user.user_id, folder_id=conv.folder_id, conversation_id=conversation_id
    )
    root, internal_root = await resolve_workspace_paths(
        user_id=user.user_id,
        folder_id=conv.folder_id,
        conversation_id=conversation_id,
        session=session,
    )
    try:
        async with workspace_lock(key):
            restore_from_trash(root=root, entry_id=entry_id, internal_root=internal_root)
    except TrashNotFound as e:
        raise NotFoundError("软删条目不存在") from e
    except TrashExpiredError as e:
        raise ConflictError(str(e) or "软删条目已过期") from e
    except AlreadyExists as e:
        raise ConflictError(f"目标路径已存在，无法还原：{e}") from e
    except OutsideWorkspace as e:
        raise ValidationError(f"软删元数据路径非法：{e}") from e
    except WorkspaceIOError as e:
        raise ValidationError(str(e) or "还原失败") from e
    return StatusResponse()
