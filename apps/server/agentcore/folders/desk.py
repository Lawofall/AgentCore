"""Collaboration-desk access: Folder.user_id is owner; members via folder_members.

Exported ``resolve_desk_access`` is the single gate for accepted membership
(双模式工作区 §八). Outsiders get None → routes 404 (no existence leak).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models import Conversation, Folder
from agentcore.db.repositories.folder_members import FolderMemberRepository
from agentcore.db.repositories.folders import FolderRepository

DeskRole = Literal["owner", "editor", "viewer"]
WRITABLE_ROLES: frozenset[str] = frozenset({"owner", "editor"})


@dataclass(frozen=True)
class DeskAccess:
    """Accepted access to a live cloud folder desk."""

    folder: Folder
    role: DeskRole
    state: Literal["accepted"] = "accepted"

    @property
    def owner_user_id(self) -> str:
        return self.folder.user_id

    @property
    def can_write(self) -> bool:
        return self.role in WRITABLE_ROLES

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    @property
    def is_member_actor(self) -> bool:
        """True when the caller is not the desk owner (editor/viewer member turn)."""
        return self.role != "owner"


@dataclass(frozen=True)
class ConversationAccess:
    conversation: Conversation
    desk: DeskAccess | None

    @property
    def can_write(self) -> bool:
        if self.desk is None:
            return True
        return self.desk.can_write

    @property
    def workspace_user_id(self) -> str:
        if self.desk is not None:
            return self.desk.owner_user_id
        return self.conversation.user_id

    @property
    def is_member_turn(self) -> bool:
        return self.desk is not None and self.desk.is_member_actor


async def resolve_desk_access(
    session: AsyncSession, *, folder_id: str, user_id: str
) -> DeskAccess | None:
    """Accepted member (incl. owner via Folder.user_id) or None → 404."""
    folder = await FolderRepository(session).get_by_id_unscoped(folder_id)
    if folder is None:
        return None
    if folder.user_id == user_id:
        return DeskAccess(folder=folder, role="owner")
    member = await FolderMemberRepository(session).get_member(folder_id, user_id)
    if member is None or member.state != "accepted":
        return None
    role: DeskRole = member.role  # type: ignore[assignment]
    if role not in ("editor", "viewer"):
        return None
    return DeskAccess(folder=folder, role=role)


async def resolve_conversation_access(
    session: AsyncSession, *, conversation_id: str, user_id: str
) -> ConversationAccess | None:
    """Bare chat: owner only. Folder chat: accepted desk member."""
    from agentcore.core.types import is_uuid_id
    from agentcore.db.repositories.conversations import ConversationRepository

    if not is_uuid_id(conversation_id):
        return None
    conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
    if conv is None:
        return None
    if conv.folder_id:
        desk = await resolve_desk_access(
            session, folder_id=conv.folder_id, user_id=user_id
        )
        if desk is None:
            return None
        return ConversationAccess(conversation=conv, desk=desk)
    if conv.user_id != user_id:
        return None
    return ConversationAccess(conversation=conv, desk=None)


async def resolve_folder_owner_user_id(
    folder_id: str | None, *, session: AsyncSession | None = None
) -> str | None:
    """Folder.user_id for disk / lock / folder-layer injection. None if missing."""
    from agentcore.core.types import is_uuid_id

    if not folder_id or not is_uuid_id(folder_id):
        return None
    from agentcore.db.base import async_session_factory

    if session is not None:
        folder = await FolderRepository(session).get_by_id_unscoped(folder_id)
        return folder.user_id if folder is not None else None
    async with async_session_factory() as owned:
        folder = await FolderRepository(owned).get_by_id_unscoped(folder_id)
        return folder.user_id if folder is not None else None


def billing_actor_user_id(*, caller_user_id: str) -> str:
    """Quota / BYOK / preflight / meter follow the sender, never the desk owner."""
    return caller_user_id


def desk_workspace_user_id(*, folder_owner_user_id: str | None, caller_user_id: str) -> str:
    """Folder-layer disk/lock key: desk owner when sitting a folder, else caller."""
    return folder_owner_user_id or caller_user_id
