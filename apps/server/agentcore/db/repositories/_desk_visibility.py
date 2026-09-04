"""SQL fragments: conversation/folder visibility for collaboration desks."""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.sql.elements import ColumnElement

from agentcore.db.models import Conversation, Folder, FolderMember


def accepted_member_live_folder_ids(user_id: str):
    """Folder ids where ``user_id`` is an accepted member of a live folder."""
    return (
        select(FolderMember.folder_id)
        .join(Folder, Folder.id == FolderMember.folder_id)
        .where(
            FolderMember.user_id == user_id,
            FolderMember.state == "accepted",
            Folder.deleted_at.is_(None),
        )
    )


def owned_folder_ids(user_id: str):
    """Owner's folders including soft-deleted (archived chats after desk trash)."""
    return select(Folder.id).where(Folder.user_id == user_id)


def conversation_visible_clause(user_id: str) -> ColumnElement[bool]:
    """Own bare chats + owned desks (incl. trashed) + accepted live member desks.

    Owned-including-deleted is required so「已归档」still lists chats a folder
    soft-delete archived in place; live lists already exclude
    ``archived_by_folder_delete``. Members only see *live* desks.
    """
    return or_(
        and_(Conversation.folder_id.is_(None), Conversation.user_id == user_id),
        Conversation.folder_id.in_(owned_folder_ids(user_id)),
        Conversation.folder_id.in_(accepted_member_live_folder_ids(user_id)),
    )


def conversation_deleted_visible_clause(user_id: str) -> ColumnElement[bool]:
    """Soft-deleted chats the caller may see in 最近删除 (incl. member-desk rows)."""
    return or_(
        and_(Conversation.folder_id.is_(None), Conversation.user_id == user_id),
        Conversation.folder_id.in_(owned_folder_ids(user_id)),
        Conversation.folder_id.in_(accepted_member_live_folder_ids(user_id)),
    )


def folder_accessible_clause(user_id: str) -> ColumnElement[bool]:
    """Live folder is owned by caller or they are an accepted member."""
    return or_(
        Folder.user_id == user_id,
        Folder.id.in_(accepted_member_live_folder_ids(user_id)),
    )
