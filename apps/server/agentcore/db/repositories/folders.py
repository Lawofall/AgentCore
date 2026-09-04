"""Folder (项目 = 工作区) data access."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import delete, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.core.types import new_id
from agentcore.db.models import Conversation, ConversationPreference, Folder, FolderMember
from agentcore.db.repositories._desk_visibility import folder_accessible_clause
from agentcore.folders.unbind import clear_folder_session_pointers
from agentcore.workspace.cloud_tree import (
    ancestor_chain,
    is_same_or_descendant,
    join_rel_path,
    normalize_rel_path,
    rel_path_name,
    reparent_rel_path,
    sanitize_folder_name,
    unique_sibling_name,
    would_nest_into_self,
)
from agentcore.workspace.cloud_tree import (
    parent_rel_path as parent_rel_path_of,
)

from ._base import HIDDEN_CONVERSATION_MODES, _ilike_pattern

# ``Folder.delete_origin`` values — who asked for the soft-delete.
#
# The recycle bin (最近删除) lists and restores ONLY ``USER`` rows. The auto-desk
# reclaim path calls the very same :meth:`FolderRepository.soft_delete`, and its
# folders are named after a conversation title, so without this discriminator a
# fresh recycle bin would fill up with machine litter that looks like real projects.
FOLDER_DELETE_ORIGIN_USER = "user"
FOLDER_DELETE_ORIGIN_AUTO_DESK_RECLAIM = "auto_desk_reclaim"
# Nested folders that went down with their parent. They are restored *by* the
# parent (one directory moved, one entry in the bin) — listing each of them as its
# own restorable project would offer a restore that cannot bring the files back,
# because the child's files sit inside the parent's tombstone directory.
FOLDER_DELETE_ORIGIN_CASCADE = "cascade"


class FolderTreeError(ValueError):
    """A rename / move the folder tree cannot represent (routes map this to 400)."""


@dataclass(frozen=True)
class TreeRewrite:
    """Result of a subtree ``rel_path`` rewrite — what the disk still has to do."""

    folder_id: str
    old_rel_path: str
    new_rel_path: str

    @property
    def moved(self) -> bool:
        return self.old_rel_path != self.new_rel_path


class FolderRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def _live_placements(self, user_id: str) -> list[tuple[str, str]]:
        """``(folder_id, rel_path)`` for every live folder of ``user_id``.

        Sibling-uniqueness and subtree rewrites are decided in Python over this
        list rather than with ``LIKE`` prefixes: a folder name may legitimately
        contain ``%`` or ``_``, and a mis-escaped pattern would silently rename the
        wrong subtree. Folder counts are human-scale.
        """
        rows = await self._session.execute(
            select(Folder.id, Folder.rel_path).where(
                Folder.user_id == user_id,
                Folder.deleted_at.is_(None),
                Folder.rel_path.is_not(None),
            )
        )
        return [(fid, rel) for fid, rel in rows.all() if rel]

    @staticmethod
    def _allocate_rel_path(
        *,
        desired_name: str,
        parent_rel_path: str | None,
        placements: Sequence[tuple[str, str]],
        exclude_id: str | None = None,
    ) -> str:
        """Sanitized, sibling-unique ``rel_path`` for a folder under ``parent``."""
        parent = normalize_rel_path(parent_rel_path)
        taken = {
            rel_path_name(rel)
            for fid, rel in placements
            if fid != exclude_id and parent_rel_path_of(rel) == parent
        }
        name = unique_sibling_name(
            sanitize_folder_name(desired_name), taken, nested=bool(parent)
        )
        return join_rel_path(parent, name)

    async def create(
        self,
        *,
        user_id: str,
        name: str,
        local_root_id: str | None = None,
        local_subpath: str | None = None,
        parent_rel_path: str | None = None,
    ) -> Folder:
        """Create a project workspace.

        ``local_root_id`` set → local project; both binding columns NULL → cloud
        project (shared ``folder:<id>`` scope). Binding is immutable after create.

        Every folder gets a ``rel_path`` slot under ``parent_rel_path`` (default:
        the tree root), sanitized and de-duplicated against its siblings. Local
        folders get one too — their files live on the user's disk, but the server
        still needs one unambiguous place for any residue, and one rule beats two.
        """
        rel_path = self._allocate_rel_path(
            desired_name=name,
            parent_rel_path=parent_rel_path,
            placements=await self._live_placements(user_id),
        )
        folder = Folder(
            id=new_id(),
            user_id=user_id,
            name=name,
            rel_path=rel_path,
            local_root_id=local_root_id,
            local_subpath=local_subpath,
        )
        self._session.add(folder)
        await self._session.commit()
        await self._session.refresh(folder)
        return folder

    async def find_active_by_local_binding(
        self,
        *,
        user_id: str,
        local_root_id: str,
        local_subpath: str | None,
    ) -> Folder | None:
        """Live local project for ``(user, root, subpath)``; empty subpath ≡ NULL.

        Oldest row wins when historical duplicates exist (created_at asc).
        Lookup treats stored ``""`` as NULL so legacy rows still reuse.
        """
        subpath_clause = (
            or_(Folder.local_subpath.is_(None), Folder.local_subpath == "")
            if local_subpath is None
            else Folder.local_subpath == local_subpath
        )
        result = await self._session.execute(
            select(Folder)
            .where(
                Folder.user_id == user_id,
                Folder.deleted_at.is_(None),
                Folder.local_root_id == local_root_id,
                subpath_clause,
            )
            .order_by(Folder.created_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, folder_id: str, *, user_id: str) -> Folder | None:
        """Owner-scoped fetch (non-owner / unknown id → None → route 404). ``user_id``
        mandatory so scoping is the structural default (SEC-002); trusted internal callers
        use :meth:`get_by_id_unscoped`."""
        result = await self._session.execute(
            select(Folder).where(
                Folder.id == folder_id,
                Folder.deleted_at.is_(None),
                Folder.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_accessible(self, folder_id: str, *, user_id: str) -> Folder | None:
        """Live folder if caller is owner or an accepted member (else None → 404)."""
        result = await self._session.execute(
            select(Folder).where(
                Folder.id == folder_id,
                Folder.deleted_at.is_(None),
                folder_accessible_clause(user_id),
            )
        )
        return result.scalar_one_or_none()

    async def list_owned_ids(
        self, user_id: str, *, include_deleted: bool = True
    ) -> list[str]:
        stmt = select(Folder.id).where(Folder.user_id == user_id)
        if not include_deleted:
            stmt = stmt.where(Folder.deleted_at.is_(None))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id_unscoped(self, folder_id: str) -> Folder | None:
        """Cross-owner fetch for trusted internal callers resolving a conversation's own
        folder (already authorized via that conversation). The explicit name keeps the
        unscoped surface greppable (SEC-002)."""
        result = await self._session.execute(
            select(Folder).where(Folder.id == folder_id, Folder.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def list_by_user(self, user_id: str) -> Sequence[Folder]:
        """A user's live folders, in creation order (sidebar group order)."""
        result = await self._session.execute(
            select(Folder)
            .where(Folder.user_id == user_id, Folder.deleted_at.is_(None))
            .order_by(Folder.created_at.asc())
        )
        return result.scalars().all()

    async def search(
        self,
        user_id: str,
        query: str,
        *,
        limit: int,
        updated_after: datetime | None = None,
    ) -> Sequence[Folder]:
        """Owner-scoped folder-name substring search (全局搜索 Tier 1)."""
        stmt = select(Folder).where(
            Folder.deleted_at.is_(None),
            Folder.name.ilike(_ilike_pattern(query)),
            folder_accessible_clause(user_id),
        )
        if updated_after is not None:
            stmt = stmt.where(Folder.updated_at >= updated_after)
        result = await self._session.execute(
            stmt.order_by(Folder.updated_at.desc()).limit(limit)
        )
        return result.scalars().all()

    async def update(
        self,
        folder_id: str,
        *,
        user_id: str,
        name: str | None = None,
    ) -> Folder | None:
        """Rename only — workspace binding is immutable after create.

        DB-only: the visible name changes but ``rel_path`` does not. Callers that
        must keep the directory in step go through
        :func:`agentcore.folders.tree_ops.rename_folder`, which pairs this with the
        subtree rewrite and the physical move under the workspace lock.
        """
        folder = await self.get_by_id(folder_id, user_id=user_id)
        if not folder:
            return None
        if name is not None:
            folder.name = name
        await self._session.commit()
        await self._session.refresh(folder)
        return folder

    async def replace_subtree_rel_path(
        self,
        folder_id: str,
        *,
        user_id: str,
        new_name: str | None = None,
        new_parent_rel_path: str | None = None,
        move: bool = False,
        commit: bool = True,
    ) -> TreeRewrite | None:
        """Rewrite a folder's slot **and every descendant's**, in one transaction.

        Rename and move are the same operation on ``rel_path``: rename keeps the
        parent and swaps the last segment, move keeps the segment and swaps the
        parent. Descendants follow by prefix, which is the whole reason there is no
        ``parent_id`` — there is no second structure that could disagree.

        Returns the old / new paths so the caller can ``mv`` the directory, or
        ``None`` when the folder is gone. Raises :class:`FolderTreeError` for the
        one move that cannot mean anything: into the folder's own subtree. A name a
        live sibling already holds is not an error — it gets the next free
        ``(2)`` suffix, same as at create time.

        ``commit=False`` leaves the transaction open so the caller can commit only
        after the directory actually moved: a failed ``mv`` then rolls back to a DB
        that still matches the disk, instead of needing a compensating rename.
        """
        folder = await self.get_by_id(folder_id, user_id=user_id)
        if folder is None:
            return None
        old_rel = normalize_rel_path(folder.rel_path)
        if not old_rel:
            raise FolderTreeError("该文件夹还没有云端目录，无法改名或移动")

        parent = (
            normalize_rel_path(new_parent_rel_path)
            if move
            else parent_rel_path_of(old_rel)
        )
        if move and would_nest_into_self(source=old_rel, new_parent=parent):
            raise FolderTreeError("不能把文件夹移动到它自己的子目录里")

        placements = await self._live_placements(user_id)
        desired = new_name if new_name is not None else rel_path_name(old_rel)
        new_rel = self._allocate_rel_path(
            desired_name=desired,
            parent_rel_path=parent,
            placements=placements,
            exclude_id=folder_id,
        )
        if new_name is not None:
            folder.name = new_name
        if new_rel != old_rel:
            # The folder itself goes through the ORM object so the identity map does
            # not hand the route a stale ``rel_path`` after the commit; descendants
            # are bulk Core updates (they are not loaded).
            folder.rel_path = new_rel
            for other_id, other_rel in placements:
                if other_id == folder_id or not is_same_or_descendant(other_rel, old_rel):
                    continue
                await self._session.execute(
                    update(Folder)
                    .where(Folder.id == other_id)
                    .values(
                        rel_path=reparent_rel_path(
                            other_rel, old_prefix=old_rel, new_prefix=new_rel
                        )
                    )
                    .execution_options(synchronize_session=False)
                )
        if commit:
            await self._session.commit()
        else:
            await self._session.flush()
        return TreeRewrite(
            folder_id=folder_id, old_rel_path=old_rel, new_rel_path=new_rel
        )

    async def list_live_subtree_ids(self, folder_id: str, *, user_id: str) -> list[str]:
        """Ids of ``folder_id`` and every live folder nested inside it."""
        folder = await self.get_by_id(folder_id, user_id=user_id)
        if folder is None:
            return []
        if not folder.rel_path:
            return [folder_id]
        root_rel = normalize_rel_path(folder.rel_path)
        return [
            fid
            for fid, rel in await self._live_placements(user_id)
            if is_same_or_descendant(rel, root_rel)
        ]

    async def list_ancestor_chain_ids(self, folder_id: str, *, user_id: str) -> list[str]:
        """``folder_id`` 的作用域链，由外向里，末位是它自己（规则 / 记忆沿树继承）。

        没有云端目录（``rel_path`` 为空的历史行）或文件夹不存在时只回它自己 / 空——
        宁可不继承，也不要凭 id 猜一条链出来。
        """
        folder = await self.get_by_id(folder_id, user_id=user_id)
        if folder is None:
            return []
        rel = normalize_rel_path(folder.rel_path)
        if not rel:
            return [folder_id]
        return ancestor_chain(rel, await self._live_placements(user_id))

    async def soft_delete(
        self,
        folder_id: str,
        *,
        user_id: str,
        origin: str = FOLDER_DELETE_ORIGIN_USER,
    ) -> bool:
        """Soft-delete a project; archive its conversations (keep ``folder_id``).

        Conversations are archived in place — not ungrouped — so project membership
        survives soft-delete. Soft-pointers (boards, bare-chat auto desk) NULL out via
        :func:`clear_folder_session_pointers`; those are deliberately **not** restored
        (see :meth:`restore`).

        ``origin`` decides whether the project shows up in the recycle bin; the auto
        cloud-desk reclaim passes ``FOLDER_DELETE_ORIGIN_AUTO_DESK_RECLAIM``. Nested
        folders go down with the parent and are marked ``CASCADE`` so the bin shows
        one entry, matching the one directory that moved to the tombstone.

        The member-archive UPDATE is deliberately narrow:

        * ``updated_at`` self-assigns. Conversation recency is turn-stamped only
          (``touch_activity``); restamping members here would still destroy the
          sidebar「最近活动」order — unrecoverable after the fact.
        * Already-soft-deleted chats and hidden ``handoff``/``standing`` infrastructure
          rows are excluded, matching every user-facing read path.
        * Only rows that are still un-archived get ``archived_by_folder_delete``, so
          restore can put back exactly what this delete took away and leave chats the
          user archived themselves alone. Un-archived means both the legacy
          ``Conversation.archived`` flag and the folder owner's
          ``conversation_preferences.archived`` row.
        """
        folder = await self.get_by_id(folder_id, user_id=user_id)
        if not folder:
            return False
        # Deleting a folder deletes what is inside it. Nested folders live inside
        # the parent's directory, which is about to move to the tombstone area
        # wholesale — leaving the children live would point their ``rel_path`` at a
        # directory that is no longer there.
        subtree_ids = await self.list_live_subtree_ids(folder_id, user_id=user_id)
        now = datetime.now(UTC)
        descendant_ids = [fid for fid in subtree_ids if fid != folder_id]
        await self._session.execute(
            update(Folder)
            .where(Folder.id == folder_id, Folder.user_id == user_id)
            .values(deleted_at=now, delete_origin=origin)
            .execution_options(synchronize_session=False)
        )
        if descendant_ids:
            await self._session.execute(
                update(Folder)
                .where(Folder.id.in_(descendant_ids), Folder.user_id == user_id)
                .values(deleted_at=now, delete_origin=FOLDER_DELETE_ORIGIN_CASCADE)
                .execution_options(synchronize_session=False)
            )
        owner_archived = select(ConversationPreference.conversation_id).where(
            ConversationPreference.user_id == user_id,
            ConversationPreference.archived.is_(True),
        )
        await self._session.execute(
            update(Conversation)
            .where(
                Conversation.folder_id.in_(subtree_ids),
                Conversation.deleted_at.is_(None),
                Conversation.mode.notin_(HIDDEN_CONVERSATION_MODES),
                Conversation.archived.is_(False),
                Conversation.id.not_in(owner_archived),
            )
            .values(
                archived=True,
                archived_by_folder_delete=True,
                updated_at=Conversation.updated_at,
            )
        )
        for member_id in subtree_ids:
            await clear_folder_session_pointers(
                self._session, folder_id=member_id, user_id=user_id
            )
        await self._session.commit()
        return True

    async def list_deleted_subtree(
        self, folder_id: str, *, user_id: str, deleted_at: datetime
    ) -> Sequence[Folder]:
        """The rows one soft-delete took down together (same ``deleted_at`` batch)."""
        result = await self._session.execute(
            select(Folder).where(
                Folder.user_id == user_id,
                Folder.deleted_at == deleted_at,
                Folder.id != folder_id,
            )
        )
        return result.scalars().all()

    async def list_deleted_by_user(
        self, user_id: str, *, not_before: datetime, limit: int
    ) -> Sequence[Folder]:
        """User-deleted projects still inside the retention window (最近删除), newest first.

        ``not_before`` is the retention cutoff the purge sweeper uses: anything older is
        already due for hard purge, so listing it as restorable would be a lie. Rows the
        machine soft-deleted (auto cloud-desk reclaim) and rows that predate
        ``delete_origin`` never appear — under-listing beats surfacing junk.
        """
        if limit <= 0:
            return []
        result = await self._session.execute(
            select(Folder)
            .where(
                Folder.user_id == user_id,
                Folder.deleted_at.is_not(None),
                Folder.deleted_at > not_before,
                Folder.delete_origin == FOLDER_DELETE_ORIGIN_USER,
            )
            .order_by(Folder.deleted_at.desc(), Folder.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_deleted_by_id(self, folder_id: str, *, user_id: str) -> Folder | None:
        """One user-deleted project regardless of retention window (owner-scoped).

        Unbounded by design: the restore route needs an expired project to still resolve
        so it can answer 409「已过保留期」instead of an indistinguishable 404.
        """
        result = await self._session.execute(
            select(Folder).where(
                Folder.id == folder_id,
                Folder.user_id == user_id,
                Folder.deleted_at.is_not(None),
                Folder.delete_origin == FOLDER_DELETE_ORIGIN_USER,
            )
        )
        return result.scalar_one_or_none()

    async def restore(
        self, folder_id: str, *, user_id: str, not_before: datetime
    ) -> Folder | None:
        """Bring a soft-deleted project back; ``None`` when nothing was restorable.

        The folder clear is a conditional UPDATE guarded on rowcount, so this row's
        restore is atomic on its own. It carries the retention predicate too: losing the
        race against the purge sweeper simply restores nothing and surfaces as a failure
        — no reconciliation, no retry.

        Only conversations this project's delete archived are un-archived, and their
        ``updated_at`` self-assigns for the same reason the delete's does. Soft-pointers
        cleared on delete (board ``folder_id``, bare-chat ``auto_desk_folder_id``) stay
        cleared: the board's original owner is unrecoverable, and re-pointing a bare chat
        at a resurrected desk is the ghost-workspace bug
        ``tests/integration/test_folder_unbind_auto_desk.py`` exists to prevent.

        The tree slot is re-allocated rather than assumed: while the folder sat in
        the tombstone its name was free, so a live sibling may hold it now (and
        ``uq_folders_user_rel_path_live`` would refuse the collision). The folder
        comes back as ``名字 (2)`` in that case, and if its former parent is itself
        gone it comes back at the tree root instead of pointing into thin air. The
        new slot rides **in** the un-delete UPDATE rather than following it: the
        moment ``deleted_at`` goes NULL the row is subject to the live-unique index,
        so a two-step restore would trip over the squatter it is trying to avoid.
        Descendants follow the same prefix rewrite as a move. The physical move
        back out of :func:`agentcore.workspace.locate.folder_tombstone_path` is the
        caller's half — :func:`agentcore.folders.tree_ops.restore_folder_tree`.
        """
        doomed = await self._session.execute(
            select(Folder).where(
                Folder.id == folder_id,
                Folder.user_id == user_id,
                Folder.deleted_at.is_not(None),
                Folder.deleted_at > not_before,
                Folder.delete_origin == FOLDER_DELETE_ORIGIN_USER,
            )
        )
        stored = doomed.scalar_one_or_none()
        stored_rel = normalize_rel_path(stored.rel_path) if stored is not None else ""
        stored_deleted_at = stored.deleted_at if stored is not None else None
        values: dict[str, object] = {"deleted_at": None, "delete_origin": None}
        new_rel = ""
        if stored_rel:
            new_rel = await self._free_slot_for_restore(
                folder_id, user_id=user_id, stored_rel=stored_rel
            )
            values["rel_path"] = new_rel
        result = await self._session.execute(
            update(Folder)
            .where(
                Folder.id == folder_id,
                Folder.user_id == user_id,
                Folder.deleted_at.is_not(None),
                Folder.deleted_at > not_before,
                Folder.delete_origin == FOLDER_DELETE_ORIGIN_USER,
            )
            .values(**values)
            # Plain Core UPDATE: ``rowcount`` is the whole decision here, so the ORM's
            # RETURNING-based session sync stays out of it. The re-read below is what
            # refreshes any stale identity-mapped copy.
            .execution_options(synchronize_session=False)
        )
        if cast("CursorResult[Any]", result).rowcount != 1:
            return None
        restored_ids = [folder_id]
        if stored_rel and stored_deleted_at is not None:
            restored_ids += await self._restore_subtree_slots(
                folder_id,
                user_id=user_id,
                stored_rel=stored_rel,
                new_rel=new_rel,
                deleted_at=stored_deleted_at,
            )
        await self._session.execute(
            update(Conversation)
            .where(
                Conversation.folder_id.in_(restored_ids),
                Conversation.archived_by_folder_delete.is_(True),
            )
            .values(
                archived=False,
                archived_by_folder_delete=False,
                updated_at=Conversation.updated_at,
            )
        )
        await self._session.commit()
        # populate_existing: the pre-check may already hold this row in the identity
        # map, and ``expire_on_commit=False`` would otherwise hand back a stale copy.
        refreshed = await self._session.execute(
            select(Folder)
            .where(Folder.id == folder_id, Folder.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        return refreshed.scalar_one_or_none()

    async def _free_slot_for_restore(
        self, folder_id: str, *, user_id: str, stored_rel: str
    ) -> str:
        """Where a soft-deleted folder can come back to, given who is live now."""
        placements = await self._live_placements(user_id)
        parent = parent_rel_path_of(stored_rel)
        # A parent that is no longer live cannot host anything; land at the root.
        if parent and not any(rel == parent for _, rel in placements):
            parent = ""
        return self._allocate_rel_path(
            desired_name=rel_path_name(stored_rel),
            parent_rel_path=parent,
            placements=placements,
            exclude_id=folder_id,
        )

    async def _restore_subtree_slots(
        self,
        folder_id: str,
        *,
        user_id: str,
        stored_rel: str,
        new_rel: str,
        deleted_at: datetime,
    ) -> list[str]:
        """Un-delete the batch that went down with ``folder_id``; return their ids.

        Runs inside the caller's transaction (no commit). Each descendant is
        un-deleted and re-prefixed in one UPDATE — the live-unique index applies the
        instant ``deleted_at`` clears, so the two cannot be separate statements.
        """
        descendants = await self.list_deleted_subtree(
            folder_id, user_id=user_id, deleted_at=deleted_at
        )
        ids: list[str] = []
        for row in descendants:
            rel = normalize_rel_path(row.rel_path)
            if not is_same_or_descendant(rel, stored_rel):
                continue
            ids.append(row.id)
            await self._session.execute(
                update(Folder)
                .where(Folder.id == row.id)
                .values(
                    deleted_at=None,
                    delete_origin=None,
                    rel_path=reparent_rel_path(
                        rel, old_prefix=stored_rel, new_prefix=new_rel
                    ),
                )
                .execution_options(synchronize_session=False)
            )
        return ids

    async def list_purgeable(self, *, before: datetime, limit: int) -> Sequence[Folder]:
        """Soft-deleted folders whose ``deleted_at`` is at/older than ``before``."""
        result = await self._session.execute(
            select(Folder)
            .where(Folder.deleted_at.is_not(None), Folder.deleted_at <= before)
            .order_by(Folder.deleted_at.asc())
            .limit(limit)
        )
        return result.scalars().all()

    async def hard_delete(self, folder_id: str) -> None:
        """Physically remove a folder record and its membership roster."""
        await self.hard_delete_many([folder_id])

    async def hard_delete_many(self, folder_ids: Sequence[str]) -> None:
        """Physically remove a whole subtree's rows — one transaction.

        A per-row loop could commit the parent and then fail on a child, which is the
        one outcome 彻底删除 must not produce: the child would survive as a live folder
        whose directory was already purged with the parent's.

        ``folder_members`` has no DB FK, so the roster is deleted first in this
        same transaction — otherwise hard-deleting the folder orphans memberships.
        """
        if not folder_ids:
            return
        ids = list(folder_ids)
        await self._session.execute(
            delete(FolderMember).where(FolderMember.folder_id.in_(ids))
        )
        await self._session.execute(delete(Folder).where(Folder.id.in_(ids)))
        await self._session.commit()
