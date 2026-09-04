"""Conversation data access (the chat itself; shares/folders/messages are siblings)."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import and_, case, delete, exists, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.core.types import is_uuid_id, new_id
from agentcore.db.models import (
    Conversation,
    ConversationExternalGrant,
    ConversationPreference,
    CostEvent,
    Folder,
    MemoryUpdateRow,
    Message,
    MessageBookmark,
    TurnLeaseRow,
    TurnMetricsRow,
    User,
)
from agentcore.db.repositories._desk_visibility import (
    conversation_deleted_visible_clause,
    conversation_visible_clause,
)

from ._audit_cascade import delete_audit_for_conversation
from ._base import (
    HIDDEN_CONVERSATION_MODES,
    _ilike_pattern,
    _sum_int,
    commit_or_flush,
)
from ._journal_cascade import delete_journal_for_conversation
from ._stream_state_cascade import delete_stream_state_for_conversation


class ConversationRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self,
        *,
        user_id: str,
        title: str | None = None,
        folder_id: str | None = None,
        mode: str = "chat",
        local_container_root_id: str | None = None,
        permission_axes: dict | None = None,
        deep_research_auto: bool | None = None,
        model_profile_id: str | None = None,
        client_request_id: str | None = None,
        commit: bool = True,
    ) -> Conversation:
        # Omit title when not provided so the DB server_default ('') applies.
        # The live `conversations.title` column is NOT NULL; passing an explicit
        # None would emit `INSERT ... title=NULL` and violate the constraint.
        #
        # ``folder_id`` files the chat at creation (a "新建对话 from a folder"):
        # filing it here, rather than with a follow-up move, keeps a chat born in a
        # folder in its folder's workspace from its very first turn — and avoids
        # racing the workspace-lock guard, which would otherwise reject the move
        # once that first turn has landed a message (双模式工作区 §九 ⑩).
        #
        # ``mode`` is "chat" for a normal conversation; a "handoff" conversation is
        # the hidden host for a local→云 cloud job's team run (双模式工作区 P2e /
        # e2), kept out of the sidebar by the list filters below.
        #
        # ``model_profile_id``: HTTP create snapshots account default (or client
        # pick). Internal callers may omit (NULL) — expand still falls back.
        #
        # ``client_request_id`` is the caller's idempotency key; passing one makes
        # this insert racy-by-design against the partial unique index, so HTTP
        # callers go through :meth:`create_idempotent` rather than here.
        #
        # Pass ``commit=False`` when pairing with HandoffJobRepository.create or
        # StandingTaskRepository.attach_conversation.
        conv = Conversation(id=new_id(), user_id=user_id)
        if title is not None:
            conv.title = title
        if folder_id is not None:
            conv.folder_id = folder_id
        if mode != "chat":
            conv.mode = mode
        # Desktop local-container hint for 裸聊: effective bind may fall back to this
        # when ``local_root_id`` is unset (双模式工作区). NULL = cloud intent. Project
        # chats ignore it (inherit the folder's immutable binding). Auto-promote is vetoed.
        if local_container_root_id is not None:
            conv.local_container_root_id = local_container_root_id
        if permission_axes is not None:
            conv.permission_axes = permission_axes
        if deep_research_auto is not None:
            conv.deep_research_auto = bool(deep_research_auto)
        if model_profile_id is not None:
            conv.model_profile_id = model_profile_id
        if client_request_id is not None:
            conv.client_request_id = client_request_id
        self._session.add(conv)
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(conv)
        return conv

    async def get_by_client_request_id(
        self, *, user_id: str, client_request_id: str
    ) -> Conversation | None:
        """The conversation this user's idempotency key already created, if any.

        Deliberately **not** filtered by ``deleted_at``: this predicate has to match
        ``uq_conversations_user_client_request`` exactly, or a key whose conversation
        was since deleted would miss here, fail the insert on the still-live index
        row, and then miss the re-query too — a request that can only 500.
        """
        result = await self._session.execute(
            select(Conversation).where(
                Conversation.user_id == user_id,
                Conversation.client_request_id == client_request_id,
            )
        )
        return result.scalar_one_or_none()

    async def create_idempotent(
        self,
        *,
        user_id: str,
        client_request_id: str,
        title: str | None = None,
        folder_id: str | None = None,
        local_container_root_id: str | None = None,
        permission_axes: dict | None = None,
        model_profile_id: str | None = None,
    ) -> tuple[Conversation, bool]:
        """Create a conversation once per ``client_request_id``; returns ``(conv, created)``.

        Two layers, because one send can arrive twice on very different clocks: a
        user re-pressing「新建」seconds later is caught by the lookup, while two
        sockets racing 14ms apart both miss it and only the unique index can pick a
        winner. The loser's ``IntegrityError`` is not an error — it means the other
        request already did the work, so we roll back and hand back its row.

        ``created=False`` marks the second (and later) arrival for the caller's log.
        """
        existing = await self.get_by_client_request_id(
            user_id=user_id, client_request_id=client_request_id
        )
        if existing is not None:
            return existing, False
        try:
            # SAVEPOINT: losing the race must cost this request its INSERT and
            # nothing else. A plain ``session.rollback()`` also expires every
            # object the caller still holds — including the authenticated user
            # the route reads right after, which then reloads outside the async
            # context and 500s the request the constraint just saved.
            async with self._session.begin_nested():
                conv = await self.create(
                    user_id=user_id,
                    title=title,
                    folder_id=folder_id,
                    local_container_root_id=local_container_root_id,
                    permission_axes=permission_axes,
                    model_profile_id=model_profile_id,
                    client_request_id=client_request_id,
                    commit=False,
                )
        except IntegrityError:
            # READ COMMITTED gives the re-query a fresh snapshot, so the winner's
            # row is visible here even though this transaction started before it
            # committed.
            winner = await self.get_by_client_request_id(
                user_id=user_id, client_request_id=client_request_id
            )
            if winner is None:
                # Some other constraint failed — not our race; let it surface.
                raise
            return winner, False
        await self._session.commit()
        return conv, True

    async def touch_activity(self, conversation_id: str, *, commit: bool = False) -> None:
        """Stamp ``updated_at`` to UTC now — the only writer of「最近活动」.

        Message ``create`` / ``upsert_assistant`` call this in the same unit-of-work
        (``commit=False``) so a user turn or assistant placeholder can bump the
        sidebar without an ORM ``onupdate`` firing on rename / pin / compact.
        """
        await self._session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=datetime.now(UTC))
        )
        await commit_or_flush(self._session, commit=commit)

    async def set_permission_axes(
        self,
        conversation_id: str,
        *,
        user_id: str,
        permission_axes: dict,
        commit: bool = True,
    ) -> Conversation | None:
        """Owner-scoped update of the session permission axes. Returns None if missing.

        Pass ``commit=False`` to land this in the caller's unit-of-work — the
        standing-task PATCH writes the task row and its pinned thread's axes in
        one transaction so an edit can never authorize half of a fire.
        """
        conv = await self.get_by_id(conversation_id, user_id=user_id)
        if not conv:
            return None
        conv.permission_axes = permission_axes
        await commit_or_flush(self._session, commit=commit)
        if commit:
            await self._session.refresh(conv)
        return conv

    async def set_model_profile(
        self,
        conversation_id: str,
        model_profile_id: str | None,
        *,
        user_id: str,
    ) -> Conversation | None:
        """Owner-scoped set of the session model combination pin.

        Callers should pass a concrete profile id (new-chat snapshot / user pick).
        ``None`` is allowed only for legacy clear paths; HTTP PATCH null re-pins
        to the account default before reaching here.
        """
        conv = await self.get_by_id(conversation_id, user_id=user_id)
        if not conv:
            return None
        conv.model_profile_id = model_profile_id
        await self._session.commit()
        await self._session.refresh(conv)
        return conv

    async def reassign_model_profile_refs(
        self, user_id: str, profile_id: str, *, to_profile_id: str | None
    ) -> int:
        """Point conversations pinned to ``profile_id`` at ``to_profile_id`` (or NULL)."""
        from sqlalchemy import update as sa_update

        result = await self._session.execute(
            sa_update(Conversation)
            .where(
                Conversation.user_id == user_id,
                Conversation.model_profile_id == profile_id,
            )
            .values(model_profile_id=to_profile_id)
        )
        await self._session.commit()
        return int(result.rowcount or 0)

    async def clear_model_profile_refs(self, user_id: str, profile_id: str) -> int:
        """Deprecated alias: null out pins (prefer ``reassign_model_profile_refs``)."""
        return await self.reassign_model_profile_refs(
            user_id, profile_id, to_profile_id=None
        )

    async def set_deep_research_auto(
        self, conversation_id: str, enabled: bool, *, user_id: str
    ) -> Conversation | None:
        """Owner-scoped toggle of 深度研究自治. Returns None if missing."""
        conv = await self.get_by_id(conversation_id, user_id=user_id)
        if not conv:
            return None
        conv.deep_research_auto = bool(enabled)
        await self._session.commit()
        await self._session.refresh(conv)
        return conv

    async def increment_deep_research_auto_debate_count(
        self, conversation_id: str
    ) -> int:
        """Bump the session auto-debate counter (unscoped; trusted runtime path).

        Returns the new count. Missing conversation ⇒ 0 (no-op).
        """
        conv = await self.get_by_id_unscoped(conversation_id)
        if not conv:
            return 0
        conv.deep_research_auto_debate_count = int(
            conv.deep_research_auto_debate_count or 0
        ) + 1
        await self._session.commit()
        await self._session.refresh(conv)
        return int(conv.deep_research_auto_debate_count)

    async def get_by_id(self, conversation_id: str, *, user_id: str) -> Conversation | None:
        """Accepted-member fetch: a non-member (or unknown id) gets None → 404.

        Bare chats stay owner-only. Folder chats are visible to the desk owner and
        every accepted folder_members row (including threads others opened).

        ``user_id`` is mandatory so owner-scoping is the structural default rather than
        a caller convention (SEC-002). Trusted internal / admin callers that legitimately
        cross owners use :meth:`get_by_id_unscoped`.

        Non-UUID ids never hit Postgres (``id`` is ``PG_UUID``); treat as unknown.
        """
        if not is_uuid_id(conversation_id):
            return None
        result = await self._session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.deleted_at.is_(None),
                conversation_visible_clause(user_id),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id_unscoped(
        self, conversation_id: str, *, include_deleted: bool = False
    ) -> Conversation | None:
        """Cross-owner fetch for trusted internal / admin callers — the turn pipeline,
        background consolidation/compaction, admin cross-user views — that operate on an
        already-authorized ``conversation_id`` without a user in hand.

        The explicit ``_unscoped`` name keeps the un-scoped surface greppable and out of
        user-facing routes (SEC-002); it is not reachable from a user request without an
        upstream owner check.

        ``include_deleted`` is for admin tombstone views (名册默认含软删，复盘必须对得上).
        The turn pipeline and other product callers must keep the default (False) so a
        soft-deleted conversation stays invisible to owner-scoped work.

        Non-UUID ids never hit Postgres; treat as unknown.
        """
        if not is_uuid_id(conversation_id):
            return None
        cond = [Conversation.id == conversation_id]
        if not include_deleted:
            cond.append(Conversation.deleted_at.is_(None))
        result = await self._session.execute(select(Conversation).where(*cond))
        return result.scalar_one_or_none()

    async def get_folder_id(self, conversation_id: str) -> str | None:
        """The conversation's ``folder_id`` straight from the DB, bypassing the
        identity map — the idempotent re-check for lazy promotion (工作区对称化 D1a
        §并发提升).

        A scalar *column* read, not a full-entity load, so it returns the live DB
        value even when this session already holds a stale full ``Conversation``
        (under ``expire_on_commit=False`` a committed object is never auto-expired).
        That lets the loser of two racing first-writes see the winner's just-committed
        folder under the promotion lock and reuse it instead of minting a duplicate.
        Returns None when the conversation has no folder yet (or doesn't exist).
        """
        result = await self._session.execute(
            select(Conversation.folder_id).where(Conversation.id == conversation_id)
        )
        return result.scalar_one_or_none()

    async def list_by_user(
        self,
        user_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        archived: bool = False,
    ) -> tuple[Sequence[Conversation], int]:
        # Hidden system conversations never show in the sidebar:
        # handoff (双模式 P2e/e2) hosts local→云 job runs; standing hosts 站立任务钉对话.
        # ``archived`` selects one side of the archive split: the default (False) is
        # the live list (sidebar / 全部对话), True backs the「已归档」view.
        pref_pinned = func.coalesce(ConversationPreference.pinned, False)
        pref_archived = func.coalesce(ConversationPreference.archived, False)
        if archived:
            archive_where = or_(
                pref_archived.is_(True),
                Conversation.archived_by_folder_delete.is_(True),
            )
        else:
            archive_where = and_(
                pref_archived.is_(False),
                Conversation.archived_by_folder_delete.is_(False),
            )
        base_query = (
            select(Conversation)
            .outerjoin(
                ConversationPreference,
                and_(
                    ConversationPreference.conversation_id == Conversation.id,
                    ConversationPreference.user_id == user_id,
                ),
            )
            .where(
                Conversation.deleted_at.is_(None),
                Conversation.mode.notin_(("handoff", "standing")),
                conversation_visible_clause(user_id),
                archive_where,
            )
        )

        count_result = await self._session.execute(
            select(func.count()).select_from(base_query.subquery())
        )
        total = count_result.scalar_one()

        result = await self._session.execute(
            base_query.order_by(pref_pinned.desc(), Conversation.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all(), total

    async def list_admin(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        query: str | None = None,
        user_id: str | None = None,
        has_errors: bool | None = None,
        has_delegated: bool | None = None,
        include_deleted: bool = True,
        since: datetime | None = None,
        until: datetime | None = None,
        sort: str = "updated_at",
        order: str = "desc",
    ) -> tuple[Sequence[tuple[Conversation, User | None]], int]:
        """Cross-user conversation roster for the admin 对话 page.

        Excludes hidden handoff/standing host conversations (same as the user sidebar).
        ``include_deleted`` controls soft-deleted conversations; owner identity
        is always joined (tombstone accounts carry ``User.deleted_at``). Filters
        AND-combine: ``query`` ILIKEs title, ``user_id`` scopes to one account,
        ``has_errors`` keeps only conversations with ≥1 errored turn,
        ``has_delegated`` keeps only conversations with ≥1 multi-agent turn,
        ``since``/``until`` bound ``updated_at`` (inclusive).
        ``sort`` accepts ``updated_at`` / ``created_at`` / ``cost`` / ``delegated``
        (multi-agent turn count).
        """
        # Account-level ledger rows (NULL conversation — AI 改写 / 文档 description)
        # are filtered out rather than grouped into a NULL bucket that joins to
        # nothing: they are the account's spend, shown on 用量页 / 全站看板, and no
        # conversation on this roster may claim them.
        cost_subq = (
            select(
                CostEvent.conversation_id.label("conversation_id"),
                _sum_int(CostEvent.cost_total_nano).label("cost_total"),
            )
            .where(CostEvent.conversation_id.is_not(None))
            .group_by(CostEvent.conversation_id)
            .subquery()
        )
        # Multi-agent rollup for ``sort=delegated`` (count of delegated turns).
        delegated_subq = (
            select(
                TurnMetricsRow.conversation_id.label("conversation_id"),
                func.sum(case((TurnMetricsRow.delegated.is_(True), 1), else_=0)).label(
                    "delegated_turns"
                ),
            )
            .group_by(TurnMetricsRow.conversation_id)
            .subquery()
        )
        base = (
            select(Conversation, User)
            .outerjoin(User, User.user_id == Conversation.user_id)
            .outerjoin(cost_subq, cost_subq.c.conversation_id == Conversation.id)
            .outerjoin(
                delegated_subq, delegated_subq.c.conversation_id == Conversation.id
            )
            .where(Conversation.mode.notin_(("handoff", "standing")))
        )
        if not include_deleted:
            base = base.where(Conversation.deleted_at.is_(None))
        if user_id is not None:
            base = base.where(Conversation.user_id == user_id)
        if query:
            base = base.where(Conversation.title.ilike(_ilike_pattern(query)))
        if since is not None:
            base = base.where(Conversation.updated_at >= since)
        if until is not None:
            base = base.where(Conversation.updated_at <= until)
        if has_errors is True:
            error_ids = (
                select(TurnMetricsRow.conversation_id)
                .where(TurnMetricsRow.status == "error")
                .distinct()
                .scalar_subquery()
            )
            base = base.where(Conversation.id.in_(error_ids))
        elif has_errors is False:
            error_ids = (
                select(TurnMetricsRow.conversation_id)
                .where(TurnMetricsRow.status == "error")
                .distinct()
                .scalar_subquery()
            )
            base = base.where(Conversation.id.not_in(error_ids))
        if has_delegated is True:
            delegated_ids = (
                select(TurnMetricsRow.conversation_id)
                .where(TurnMetricsRow.delegated.is_(True))
                .distinct()
                .scalar_subquery()
            )
            base = base.where(Conversation.id.in_(delegated_ids))
        elif has_delegated is False:
            delegated_ids = (
                select(TurnMetricsRow.conversation_id)
                .where(TurnMetricsRow.delegated.is_(True))
                .distinct()
                .scalar_subquery()
            )
            base = base.where(Conversation.id.not_in(delegated_ids))

        count_result = await self._session.execute(
            select(func.count()).select_from(base.subquery())
        )
        total = count_result.scalar_one()

        if sort == "cost":
            sort_col = func.coalesce(cost_subq.c.cost_total, 0)
        elif sort == "delegated":
            sort_col = func.coalesce(delegated_subq.c.delegated_turns, 0)
        elif sort == "created_at":
            sort_col = Conversation.created_at
        else:
            sort_col = Conversation.updated_at
        order_by = sort_col.asc() if order == "asc" else sort_col.desc()
        offset = (page - 1) * page_size
        result = await self._session.execute(
            base.order_by(order_by).limit(page_size).offset(offset)
        )
        return result.all(), total

    async def search(
        self,
        user_id: str,
        query: str,
        *,
        limit: int,
        updated_after: datetime | None = None,
        folder_id: str | None = None,
        include_archived: bool = False,
        global_chats_only: bool = False,
        exclude_conversation_id: str | None = None,
        match_message_body: bool = False,
    ) -> Sequence[Conversation]:
        """Owner-scoped conversation search (全局搜索 Tier 1 / 跨会话日志工具).

        Default: ILIKE over ``title``. With ``match_message_body``, also hit when any
        message ``content`` matches (same substring as ``MessageRepository.search`` /
        GET /v1/search 的消息面，但收成对话行). Newest-activity first, capped at
        ``limit``. Excludes soft-deleted and hidden handoff/standing hosts — the same
        visibility as the sidebar, so a hit is always something the user can open.

        GET /v1/search 的 conversation 段保持默认（只搜标题）；跨会话日志工具走
        ``search_with_projections``（有 query 时开正文）。

        The optional facets (搜索结果过滤) narrow the same result set server-side so
        the cap is spent on matching rows rather than filtered-away ones:
        ``updated_after`` keeps only recently-active chats (时间过滤), ``folder_id``
        scopes to one folder/工作区.

        Cross-session log tool extras (跨会话对话日志访问定案):
        ``include_archived`` (default False), ``global_chats_only`` (``folder_id IS NULL``),
        ``exclude_conversation_id`` (host turn's own chat). Empty ``query`` lists by
        ``updated_at`` without a title or body filter.
        """
        stmt = select(Conversation).where(
            conversation_visible_clause(user_id),
            Conversation.deleted_at.is_(None),
            Conversation.mode.notin_(("handoff", "standing")),
        )
        q = (query or "").strip()
        if q:
            title_hit = Conversation.title.ilike(_ilike_pattern(q))
            if match_message_body:
                body_hit = exists(
                    select(Message.id).where(
                        Message.conversation_id == Conversation.id,
                        Message.content.is_not(None),
                        Message.content.ilike(_ilike_pattern(q)),
                    )
                )
                stmt = stmt.where(or_(title_hit, body_hit))
            else:
                stmt = stmt.where(title_hit)
        if not include_archived:
            personally_archived = select(ConversationPreference.conversation_id).where(
                ConversationPreference.user_id == user_id,
                ConversationPreference.archived.is_(True),
            )
            stmt = stmt.where(
                Conversation.archived_by_folder_delete.is_(False),
                Conversation.id.not_in(personally_archived),
            )
        if global_chats_only:
            stmt = stmt.where(Conversation.folder_id.is_(None))
        if updated_after is not None:
            stmt = stmt.where(Conversation.updated_at >= updated_after)
        if folder_id is not None:
            stmt = stmt.where(Conversation.folder_id == folder_id)
        if exclude_conversation_id:
            stmt = stmt.where(Conversation.id != exclude_conversation_id)
        result = await self._session.execute(
            stmt.order_by(Conversation.updated_at.desc()).limit(limit)
        )
        return result.scalars().all()

    async def search_with_projections(
        self,
        user_id: str,
        query: str,
        *,
        limit: int,
        folder_id: str | None = None,
        include_archived: bool = False,
        global_chats_only: bool = False,
        exclude_conversation_id: str | None = None,
        updated_after: datetime | None = None,
    ) -> list[dict]:
        """Like :meth:`search` but projects ``folder_name`` + ``message_count``.

        Returns plain dicts for the conversation-log tools (no ORM leakage into
        tool JSON). Non-empty ``query`` matches title **or** message body.
        Message counts come from one grouped query (same as sidebar
        ``counts_for_conversations``).
        """
        convs = list(
            await self.search(
                user_id,
                query,
                limit=limit,
                folder_id=folder_id,
                include_archived=include_archived,
                global_chats_only=global_chats_only,
                exclude_conversation_id=exclude_conversation_id,
                updated_after=updated_after,
                match_message_body=bool((query or "").strip()),
            )
        )
        if not convs:
            return []
        folder_ids = {c.folder_id for c in convs if c.folder_id}
        folder_names: dict[str, str] = {}
        if folder_ids:
            fres = await self._session.execute(
                select(Folder.id, Folder.name).where(Folder.id.in_(folder_ids))
            )
            folder_names = {row[0]: row[1] for row in fres.all()}
        from agentcore.db.repositories.messages import MessageRepository

        counts = await MessageRepository(self._session).counts_for_conversations(
            [c.id for c in convs]
        )
        flags = await self.preference_flags_for(user_id, [c.id for c in convs])
        out: list[dict] = []
        for c in convs:
            pinned, archived = flags.get(c.id, (False, False))
            out.append(
                {
                    "conversation_id": c.id,
                    "title": (c.title or "").strip() or "未命名对话",
                    "folder_id": c.folder_id,
                    "folder_name": folder_names.get(c.folder_id) if c.folder_id else None,
                    "updated_at": c.updated_at.isoformat() if c.updated_at else None,
                    "message_count": int(counts.get(c.id, 0)),
                    "archived": archived or bool(c.archived_by_folder_delete),
                    "pinned": pinned,
                }
            )
        return out

    async def update_title(
        self, conversation_id: str, title: str, *, user_id: str
    ) -> Conversation | None:
        """Owner-scoped rename (user-facing). ``user_id`` mandatory (SEC-002)."""
        conv = await self.get_by_id(conversation_id, user_id=user_id)
        return await self._write_title(conv, title)

    async def update_title_unscoped(
        self, conversation_id: str, title: str
    ) -> Conversation | None:
        """Title write for trusted internal callers — post-turn auto-title minting — that
        hold an already-authorized ``conversation_id`` but no user (SEC-002)."""
        conv = await self.get_by_id_unscoped(conversation_id)
        return await self._write_title(conv, title)

    async def update_title_if_empty(
        self, conversation_id: str, title: str
    ) -> Conversation | None:
        """Auto-mint write: only when the conversation title is still empty.

        Closes the race with a user rename that lands between schedule and LLM return.
        Returns ``None`` when the row is missing or already titled (no write).
        """
        conv = await self.get_by_id_unscoped(conversation_id)
        if conv is None or (conv.title and str(conv.title).strip()):
            return None
        return await self._write_title(conv, title)

    async def _write_title(
        self,
        conv: Conversation | None,
        title: str,
    ) -> Conversation | None:
        if conv:
            conv.title = title
            await self._session.commit()
            await self._session.refresh(conv)
        return conv

    async def preference_flags_for(
        self, user_id: str, conversation_ids: Sequence[str]
    ) -> dict[str, tuple[bool, bool]]:
        """``{conversation_id: (pinned, archived)}`` for the caller. Missing → (False, False)."""
        if not conversation_ids:
            return {}
        result = await self._session.execute(
            select(ConversationPreference).where(
                ConversationPreference.user_id == user_id,
                ConversationPreference.conversation_id.in_(list(conversation_ids)),
            )
        )
        return {
            row.conversation_id: (bool(row.pinned), bool(row.archived))
            for row in result.scalars().all()
        }

    async def _upsert_preference(
        self,
        conversation_id: str,
        user_id: str,
        *,
        pinned: bool | None = None,
        archived: bool | None = None,
    ) -> ConversationPreference:
        result = await self._session.execute(
            select(ConversationPreference).where(
                ConversationPreference.conversation_id == conversation_id,
                ConversationPreference.user_id == user_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = ConversationPreference(
                conversation_id=conversation_id,
                user_id=user_id,
                pinned=bool(pinned) if pinned is not None else False,
                archived=bool(archived) if archived is not None else False,
            )
            self._session.add(row)
        else:
            if pinned is not None:
                row.pinned = pinned
            if archived is not None:
                row.archived = archived
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def delete_preferences_for_user(self, user_id: str) -> int:
        result = await self._session.execute(
            delete(ConversationPreference).where(ConversationPreference.user_id == user_id)
        )
        await self._session.commit()
        return int(result.rowcount or 0)

    async def set_pinned(
        self, conversation_id: str, pinned: bool, *, user_id: str
    ) -> Conversation | None:
        """Pin / unpin for this caller only (per-user preference)."""
        conv = await self.get_by_id(conversation_id, user_id=user_id)
        if conv:
            await self._upsert_preference(conversation_id, user_id, pinned=pinned)
        return conv

    async def set_archived(
        self, conversation_id: str, archived: bool, *, user_id: str
    ) -> Conversation | None:
        """Archive / unarchive for this caller only (per-user preference)."""
        conv = await self.get_by_id(conversation_id, user_id=user_id)
        if conv:
            await self._upsert_preference(conversation_id, user_id, archived=archived)
        return conv

    async def soft_delete(self, conversation_id: str, *, user_id: str) -> bool:
        """Owner-scoped soft delete (user-facing, recoverable from 最近删除).

        ``user_id`` mandatory (SEC-002); account-wide deletion uses
        :meth:`soft_delete_all_for_user`.

        Two properties the recycle bin rests on, both invisible until a chat comes
        back:

        * ``deleted_at`` is UTC-aware. The column is ``TIMESTAMPTZ`` and asyncpg binds
          a naive datetime as UTC, so a naive local ``now()`` would shift the stamp by
          the box's offset — and「删除于」/「还剩几天」are rendered straight off it.
        * ``updated_at`` self-assigns. The column has no ORM ``onupdate`` (only
          ``touch_activity`` restamps it), but restamping here would still overwrite
          the chat's real last-turn time with the delete moment — a restore would
          land it in「今天」instead of the recency group it belongs to.
        """
        conv = await self.get_by_id(conversation_id, user_id=user_id)
        if not conv:
            return False
        if conv.user_id != user_id:
            from agentcore.db.repositories.folders import FolderRepository

            folder = (
                await FolderRepository(self._session).get_by_id_unscoped(conv.folder_id)
                if conv.folder_id
                else None
            )
            if folder is None or folder.user_id != user_id:
                return False
        # 现场跟随对话：软删也清 run_sessions，避免唤回已删对话的现场。
        from agentcore.db.repositories.runs import RunSessionRepository

        await RunSessionRepository(self._session).delete_for_conversation(conversation_id)
        await self._session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(deleted_at=datetime.now(UTC), updated_at=Conversation.updated_at)
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()
        return True

    async def soft_delete_all_for_user(self, user_id: str) -> int:
        """Soft-delete every live conversation owned by a user (账户注销级联).

        One bulk update so deleting an account doesn't N+1 over its history; already
        soft-deleted rows are skipped. Returns the number newly soft-deleted. The
        retention sweeper later reclaims their workspaces just like any soft delete —
        which is why the stamp is UTC-aware here too (see :meth:`soft_delete`).
        """
        # Collect ids first so we can cascade-clear run_sessions for those chats.
        ids_result = await self._session.execute(
            select(Conversation.id).where(
                Conversation.user_id == user_id,
                Conversation.deleted_at.is_(None),
            )
        )
        conv_ids = list(ids_result.scalars().all())
        if not conv_ids:
            return 0
        from agentcore.db.models import RunSessionRow

        await self._session.execute(
            delete(RunSessionRow).where(RunSessionRow.conversation_id.in_(conv_ids))
        )
        result = await self._session.execute(
            update(Conversation)
            .where(Conversation.id.in_(conv_ids))
            .values(deleted_at=datetime.now(UTC))
        )
        await self._session.commit()
        return int(result.rowcount or 0)

    async def list_deleted_by_user(
        self, user_id: str, *, not_before: datetime, limit: int
    ) -> Sequence[Conversation]:
        """Soft-deleted chats still inside the retention window (最近删除), newest first.

        ``not_before`` is the same cutoff :meth:`list_purgeable` selects against, so a
        chat is listed as restorable only while the sweeper is still forbidden to purge
        it — the bin never offers a recovery it cannot honour.

        Hidden infrastructure rows (``handoff`` / ``standing``) are excluded, matching
        every other user-facing read: those are soft-deleted by machine paths
        (handoff reclaim), and the user has no idea what they are. No ``delete_origin``
        discriminator is needed beyond that — unlike folders, nothing machine-driven
        soft-deletes a *visible* chat.
        """
        if limit <= 0:
            return []
        result = await self._session.execute(
            select(Conversation)
            .where(
                conversation_deleted_visible_clause(user_id),
                Conversation.deleted_at.is_not(None),
                Conversation.deleted_at > not_before,
                Conversation.mode.notin_(HIDDEN_CONVERSATION_MODES),
            )
            .order_by(Conversation.deleted_at.desc(), Conversation.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_deleted_by_id(
        self, conversation_id: str, *, user_id: str
    ) -> Conversation | None:
        """One soft-deleted chat regardless of retention window (owner-scoped).

        Unbounded by design: the restore route needs an expired chat to still resolve
        so it can answer 409「已过保留期」instead of an indistinguishable 404.
        """
        result = await self._session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                conversation_deleted_visible_clause(user_id),
                Conversation.deleted_at.is_not(None),
                Conversation.mode.notin_(HIDDEN_CONVERSATION_MODES),
            )
        )
        return result.scalar_one_or_none()

    async def restore(
        self, conversation_id: str, *, user_id: str, not_before: datetime
    ) -> Conversation | None:
        """Un-delete a chat; ``None`` when there was nothing restorable.

        A single conditional UPDATE guarded on rowcount, carrying the retention
        predicate: losing the race against the purge sweeper simply restores nothing
        and surfaces as a failure — no reconciliation, no retry.

        The chat keeps everything the delete left alone, which is everything except
        ``deleted_at``: ``folder_id``, ``pinned``, ``archived`` and its real
        ``updated_at`` all come back untouched, so it reappears in the group and the
        recency bucket it left from. Two things do **not** come back, and the UI says
        so rather than pretending: public share links (cascade-revoked at delete time,
        by design — a stale snapshot must not outlive the delete) and the run sessions
        cleared with it. A chat whose project was soft-deleted meanwhile keeps pointing
        at it and reads as 未分组 until that project is restored too.
        """
        visible = await self.get_deleted_by_id(conversation_id, user_id=user_id)
        if visible is None:
            return None
        result = await self._session.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.deleted_at.is_not(None),
                Conversation.deleted_at > not_before,
                Conversation.mode.notin_(HIDDEN_CONVERSATION_MODES),
            )
            # Plain Core UPDATE: ``rowcount`` is the whole decision, so the ORM's
            # RETURNING-based session sync stays out of it. ``updated_at`` self-assigns
            # — restoring a chat is not a turn and must not call ``touch_activity``.
            .values(deleted_at=None, updated_at=Conversation.updated_at)
            .execution_options(synchronize_session=False)
        )
        if cast("CursorResult[Any]", result).rowcount != 1:
            return None
        await self._session.commit()
        # populate_existing: the route's pre-check already holds this row in the
        # identity map, and ``expire_on_commit=False`` would hand back a stale copy
        # still carrying ``deleted_at``.
        refreshed = await self._session.execute(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .execution_options(populate_existing=True)
        )
        return refreshed.scalar_one_or_none()

    async def list_purgeable(self, *, before: datetime, limit: int) -> Sequence[Conversation]:
        """Soft-deleted conversations whose ``deleted_at`` is at/older than ``before``.

        Backs retention cleanup (决策⑦): these have outlived the grace period and
        are ready for physical removal. Oldest-deleted first, capped by ``limit``.
        """
        result = await self._session.execute(
            select(Conversation)
            .where(
                Conversation.deleted_at.is_not(None),
                Conversation.deleted_at <= before,
            )
            .order_by(Conversation.deleted_at.asc())
            .limit(limit)
        )
        return result.scalars().all()

    async def list_ids_by_folder(self, folder_id: str, *, user_id: str) -> list[str]:
        """Every conversation filed in ``folder_id`` (live, archived, or soft-deleted).

        Used by permanent project wipe to cascade hard-delete all member chats,
        including threads opened by folder members (``Conversation.user_id`` may
        differ from the wiping owner). ``user_id`` is accepted at the call site
        and is not a SQL filter.

        Handoff-host conversations are excluded (hidden infra rows).
        """
        del user_id
        result = await self._session.execute(
            select(Conversation.id).where(
                Conversation.folder_id == folder_id,
                Conversation.mode.notin_(("handoff", "standing")),
            )
        )
        return list(result.scalars().all())

    async def hard_delete(self, conversation_id: str) -> None:
        """Physically remove a conversation and all its rows (messages + cost ledger
        + turn journal).

        App-level cascade (no DB FK, per repo convention). Used only by retention
        after the grace period — distinct from ``soft_delete`` (the user-facing
        recoverable delete). The ``turn_journal`` replay stream (唯一事实源, §8.3)
        is dropped here too — it would otherwise orphan (it has no own TTL sweep).
        Per-user ``conversation_preferences`` have no DB FK and go first.
        In-flight ``turn_stream_state`` snapshots go next (keyed by message id,
        no ``conversation_id`` — must run before the message delete).
        ``run_sessions`` are also cleared (现场跟随对话).
        """
        await self._session.execute(
            delete(ConversationPreference).where(
                ConversationPreference.conversation_id == conversation_id
            )
        )
        await delete_stream_state_for_conversation(self._session, conversation_id)
        await self._session.execute(
            delete(Message).where(Message.conversation_id == conversation_id)
        )
        # 消息收藏 pointers into this conversation (app-level cascade; no message left
        # for them to reference after the bulk delete above).
        await self._session.execute(
            delete(MessageBookmark).where(
                MessageBookmark.conversation_id == conversation_id
            )
        )
        await self._session.execute(
            delete(CostEvent).where(CostEvent.conversation_id == conversation_id)
        )
        await delete_journal_for_conversation(self._session, conversation_id)
        await delete_audit_for_conversation(self._session, conversation_id)
        await self._session.execute(
            delete(TurnLeaseRow).where(TurnLeaseRow.conversation_id == conversation_id)
        )
        # Conversation-tail 记忆已更新 records (keyed by conversation_id, no message FK).
        await self._session.execute(
            delete(MemoryUpdateRow).where(MemoryUpdateRow.conversation_id == conversation_id)
        )
        # W3 external grants (conversation-scoped; absolute paths live on desktop only).
        await self._session.execute(
            delete(ConversationExternalGrant).where(
                ConversationExternalGrant.conversation_id == conversation_id
            )
        )
        # 现场跟随对话：硬删级联清 run_sessions（与 soft_delete 对称）。
        from agentcore.db.repositories.runs import RunSessionRepository

        await RunSessionRepository(self._session).delete_for_conversation(conversation_id)
        await self._session.execute(delete(Conversation).where(Conversation.id == conversation_id))
        await self._session.commit()

    async def set_memory_synced_at(self, conversation_id: str, synced_at: datetime) -> None:
        """Advance the long-term-memory consolidation watermark (Agent记忆 §1.5).

        ``synced_at`` is the created_at of the last message folded into the user's
        memory. The runner stamps it after each pass (even a no-op one) so neither
        the debounce nor the sweeper reprocesses already-consolidated messages.
        """
        await self._session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(memory_synced_at=synced_at)
        )
        await self._session.commit()

    async def reset_memory_synced_at_for_user(self, user_id: str) -> int:
        """Clear ``memory_synced_at`` on live chat conversations (memory backfill).

        Only rows that currently hold a watermark are updated, so repeated runs are
        idempotent. Returns the number of conversations reset.
        """
        result = await self._session.execute(
            update(Conversation)
            .where(
                Conversation.user_id == user_id,
                Conversation.deleted_at.is_(None),
                Conversation.mode == "chat",
                Conversation.memory_synced_at.isnot(None),
            )
            .values(memory_synced_at=None)
            .returning(Conversation.id)
        )
        count = len(result.all())
        await self._session.commit()
        return count

    async def count_memory_watermarked_chat_conversations(self, user_id: str) -> int:
        """Live chat conversations that would be reset by ``reset_memory_synced_at_for_user``."""
        result = await self._session.execute(
            select(func.count())
            .select_from(Conversation)
            .where(
                Conversation.user_id == user_id,
                Conversation.deleted_at.is_(None),
                Conversation.mode == "chat",
                Conversation.memory_synced_at.isnot(None),
            )
        )
        return int(result.scalar_one())

    async def set_compaction(
        self,
        conversation_id: str,
        *,
        summary: str,
        compacted_through: datetime,
        input_tokens: int | None,
    ) -> None:
        """Persist the rolling compaction summary + its watermark (执行引擎 §三 长对话压缩).

        ``summary`` is the merged rolling digest, ``compacted_through`` the created_at
        of the last message folded into it (the loader replays only messages strictly
        newer than this), and ``input_tokens`` the turn-input size that triggered this
        (re)compaction — observability for tuning the threshold. Written once per fold
        by the off-turn background pass (conversation/compaction.py); reused verbatim
        across turns so the DeepSeek exact-prefix cache holds.
        """
        await self._session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(
                compaction_summary=summary,
                compacted_through=compacted_through,
                compaction_input_tokens=input_tokens,
            )
        )
        await self._session.commit()

    async def list_pending_memory_consolidation(
        self, *, idle_before: datetime, limit: int
    ) -> Sequence[str]:
        """Ids of settled chats that have un-consolidated messages (sweeper work list).

        A conversation qualifies when its latest message is newer than its
        ``memory_synced_at`` watermark (有未整合的新内容) yet is at/older than
        ``idle_before`` (已静默, the debounce window has elapsed). Restricted to
        normal chats — hidden handoff hosts (P2e) carry agent runs, not user talk.
        Oldest-settled first, capped by ``limit``. Backs the periodic backstop that
        covers a debounce dropped by a restart / closed client.
        """
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        last_msg = func.max(Message.created_at)
        result = await self._session.execute(
            select(Conversation.id)
            .join(Message, Message.conversation_id == Conversation.id)
            .where(
                Conversation.deleted_at.is_(None),
                Conversation.mode == "chat",
            )
            .group_by(Conversation.id, Conversation.memory_synced_at)
            .having(
                and_(
                    last_msg > func.coalesce(Conversation.memory_synced_at, epoch),
                    last_msg <= idle_before,
                )
            )
            .order_by(last_msg.asc())
            .limit(limit)
        )
        return [row[0] for row in result.all()]

    async def list_all_by_user(self, user_id: str) -> Sequence[Conversation]:
        """Every live (non-archived) conversation for a user, pinned-first then
        newest activity.

        Unpaginated — backs the folder-grouped sidebar, which groups the full
        set client-side (the flat list is small in the desktop MVP). Archived
        conversations are excluded here (they live in the separate「已归档」view);
        pinned ones sort to the top (置顶对话).
        """
        pref_pinned = func.coalesce(ConversationPreference.pinned, False)
        pref_archived = func.coalesce(ConversationPreference.archived, False)
        result = await self._session.execute(
            select(Conversation)
            .outerjoin(
                ConversationPreference,
                and_(
                    ConversationPreference.conversation_id == Conversation.id,
                    ConversationPreference.user_id == user_id,
                ),
            )
            .where(
                Conversation.deleted_at.is_(None),
                Conversation.mode.notin_(("handoff", "standing")),
                conversation_visible_clause(user_id),
                pref_archived.is_(False),
                Conversation.archived_by_folder_delete.is_(False),
            )
            .order_by(pref_pinned.desc(), Conversation.updated_at.desc())
        )
        return result.scalars().all()

    async def set_local_binding(
        self, conversation_id: str, *, root_id: str | None, subpath: str | None = None
    ) -> None:
        """Set the conversation's scratch workspace local binding."""
        await self._session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(local_root_id=root_id, local_subpath=subpath)
        )
        await self._session.commit()

    async def set_auto_desk_folder_id(
        self,
        conversation_id: str,
        folder_id: str,
        *,
        user_id: str,
    ) -> tuple[str | None, bool]:
        """Atomically persist bare-chat auto cloud desk id (never touches birth ``folder_id``).

        Conditional first-write: ``UPDATE … WHERE auto_desk_folder_id IS NULL`` so
        concurrent mints cannot overwrite each other (no read-modify-write window).

        Returns ``(effective_id, won)``:
        - ``(folder_id, True)`` — this call wrote the pointer
        - ``(existing_id, False)`` — pointer already set; caller lost the race
        - ``(None, False)`` — conversation missing / not owned / empty ``folder_id``
        """
        cleaned = folder_id.strip() if isinstance(folder_id, str) else ""
        if not cleaned:
            return None, False
        result = await self._session.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
                Conversation.deleted_at.is_(None),
                Conversation.auto_desk_folder_id.is_(None),
            )
            .values(auto_desk_folder_id=cleaned)
            .returning(Conversation.auto_desk_folder_id)
        )
        written = result.scalar_one_or_none()
        if written is not None:
            await self._session.commit()
            return cleaned, True

        # Lost race or missing row — scalar read bypasses identity-map staleness.
        await self._session.commit()
        existing = await self._session.execute(
            select(Conversation.auto_desk_folder_id).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
                Conversation.deleted_at.is_(None),
            )
        )
        raw = existing.scalar_one_or_none()
        if isinstance(raw, str) and raw.strip():
            return raw.strip(), False
        return None, False

    async def clear_auto_desk_folder_id(
        self,
        conversation_id: str,
        *,
        user_id: str,
        expected_folder_id: str,
    ) -> bool:
        """Clear ``auto_desk_folder_id`` when it still matches ``expected_folder_id``.

        CAS-style so a concurrent first-write of a fresh desk is not wiped. Used when
        bind discovers the pointed Folder is missing / soft-deleted (next turn remints).
        """
        expected = (
            expected_folder_id.strip()
            if isinstance(expected_folder_id, str)
            else ""
        )
        if not expected:
            return False
        result = await self._session.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
                Conversation.deleted_at.is_(None),
                Conversation.auto_desk_folder_id == expected,
            )
            .values(auto_desk_folder_id=None)
        )
        await self._session.commit()
        return (result.rowcount or 0) > 0
