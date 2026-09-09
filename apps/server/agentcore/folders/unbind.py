"""Clear session/board soft-pointers that reference a folder being deleted.

Birth ``Conversation.folder_id`` (project affiliation) is **not** handled here —
callers keep their distinct semantics (soft-delete archive, retention NULL,
permanent wipe of member chats). This module only NULLs columns that *point at*
a folder without belonging to it as members.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models import Board, Conversation

# Columns on Conversation / Board that soft-point at a Folder and must NULL out
# when that folder is deleted (or purged). Driven by :func:`clear_folder_session_pointers`.
#
# Adding a new folder-pointer column on Conversation or Board? Register it here
# (and it will be cleared automatically). Affiliation-only columns go in
# :data:`FOLDER_AFFILIATION_COLUMNS` instead; leftover unread columns go in
# :data:`FOLDER_UNREAD_COLUMNS`. The exhaustiveness test will fail if a new
# ``*folder_id`` column is omitted from all three sets.
FOLDER_NULL_ON_DELETE_POINTERS: Sequence[tuple[type[Any], str]] = (
    (Conversation, "auto_desk_folder_id"),
)

# Conversation/Board columns that reference folders but are **not** cleared by
# :func:`clear_folder_session_pointers` (call-site membership / birth semantics).
FOLDER_AFFILIATION_COLUMNS: frozenset[tuple[type[Any], str]] = frozenset(
    {
        (Conversation, "folder_id"),
    }
)

# Leftover columns still on the table, unread, not written, not cleared on delete.
# ``Board.folder_id``: 否决 board ∈ folder; column kept to avoid a migration.
FOLDER_UNREAD_COLUMNS: frozenset[tuple[type[Any], str]] = frozenset(
    {
        (Board, "folder_id"),
    }
)


async def clear_folder_session_pointers(
    session: AsyncSession,
    *,
    folder_id: str,
    user_id: str | None = None,
) -> None:
    """NULL session/board soft-pointers to ``folder_id`` (same session; no commit).

    ``user_id`` scopes the UPDATE when provided (API soft / permanent delete).
    Omit it for retention's global sweep.
    """
    for model, column in FOLDER_NULL_ON_DELETE_POINTERS:
        clauses = [getattr(model, column) == folder_id]
        if user_id is not None:
            clauses.append(model.user_id == user_id)
        await session.execute(update(model).where(*clauses).values(**{column: None}))
