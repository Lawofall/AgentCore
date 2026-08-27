"""Message (对话消息 / turn) data access."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.core.types import is_uuid_id, new_id
from agentcore.db.models import (
    Conversation,
    Message,
    MessageBookmark,
    PausedTurnOutcomeRow,
    PausedTurnRow,
    TurnLeaseRow,
)

from ._audit_cascade import delete_audit_after, delete_audit_for_message
from ._base import _ilike_pattern, commit_or_flush, strip_nul
from ._journal_cascade import delete_journal_after, delete_journal_for_message
from ._stream_state_cascade import delete_stream_state_after, delete_stream_state_for_message


class MessageRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        reasoning_content: str | None = None,
        metadata: dict | None = None,
        attachments: list | None = None,
        agent_mentions: list | None = None,
        citations: list | None = None,
        evidence_ledger: list | None = None,
        message_id: str | None = None,
        trace_id: str | None = None,
    ) -> Message:
        # `message_id` lets the caller pin the row id to the pipeline's id (the
        # one already sent to the client on `message_start`), so the streamed and
        # persisted assistant message agree; defaults to a fresh id otherwise.
        # Non-UUID pins (legacy sidecar ``resume-{turn_id}``) are dropped — the
        # column is PG UUID and must not bind an illegal key.
        # `trace_id` (the turn's log correlation key) is supplied by the caller —
        # which owns the contextvar scope — so this row joins to its log trace.
        pinned_id = message_id if is_uuid_id(message_id) else None
        msg = Message(
            id=pinned_id or new_id(),
            conversation_id=conversation_id,
            role=role,
            content=strip_nul(content),
            reasoning_content=strip_nul(reasoning_content),
            usage=strip_nul(metadata),
            trace_id=trace_id,
        )
        if attachments is not None:
            msg.attachments = strip_nul(attachments)
        if agent_mentions is not None:
            msg.agent_mentions = strip_nul(agent_mentions)
        if citations is not None:
            msg.citations = strip_nul(citations)
        if evidence_ledger is not None:
            msg.evidence_ledger = strip_nul(evidence_ledger)
        self._session.add(msg)
        await self._touch_activity(conversation_id)
        await self._session.commit()
        await self._session.refresh(msg)
        return msg

    async def create_assistant_placeholder(
        self,
        *,
        conversation_id: str,
        message_id: str,
        trace_id: str | None = None,
    ) -> Message:
        """Insert an empty assistant row at turn start (progressive persistence).

        The pipeline's ``message_id`` / journal ``turn_id`` is pinned up front so a
        mid-turn refresh can find this row before the turn finishes.
        """
        return await self.create(
            conversation_id=conversation_id,
            role="assistant",
            content="",
            metadata={"status": "running"},
            message_id=message_id,
            trace_id=trace_id,
        )

    async def list_non_paused_running_assistants(
        self,
        conversation_id: str,
        *,
        exclude_message_id: str | None = None,
    ) -> Sequence[Message]:
        """Assistant rows still ``usage.status=running`` without a pause latch.

        Used to settle dead-registry / no-lease zombies before a new attempt's
        ``begin_turn``. Does not consult ``paused_turns`` — callers that need that
        filter check the frame table separately.
        """
        q = select(Message).where(
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
            Message.usage["status"].astext == "running",
        )
        if exclude_message_id:
            q = q.where(Message.id != exclude_message_id)
        result = await self._session.execute(q.order_by(Message.created_at.asc()))
        out: list[Message] = []
        for row in result.scalars().all():
            usage = row.usage if isinstance(row.usage, dict) else {}
            if usage.get("paused"):
                continue
            out.append(row)
        return out

    async def get_execution_harvest_user(
        self,
        *,
        conversation_id: str,
        execution_id: str,
    ) -> Message | None:
        """The synthetic 系统收口 user row for this execution, if the claim exists."""
        result = await self._session.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == "user",
                Message.usage["origin"].astext == "execution_harvest",
                Message.usage["execution_id"].astext == execution_id,
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_first_assistant_after(
        self,
        *,
        conversation_id: str,
        after: datetime,
        after_id: str,
    ) -> Message | None:
        """Earliest assistant row strictly after ``(after, after_id)``."""
        result = await self._session.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == "assistant",
                or_(
                    Message.created_at > after,
                    and_(Message.created_at == after, Message.id > after_id),
                ),
            )
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def upsert_assistant(
        self,
        *,
        conversation_id: str,
        message_id: str | None = None,
        content: str,
        reasoning_content: str | None = None,
        metadata: dict | None = None,
        citations: list | None = None,
        evidence_ledger: list | None = None,
        trace_id: str | None = None,
        merge: bool = False,
    ) -> Message:
        """Insert or update an assistant row keyed by ``message_id``.

        Used for pause snapshots (first write) and resume completion (update in place)
        so the same pipeline ``message_id`` never hits a unique-constraint error.

        When ``merge=True`` (D7 / ConversationStore finalize), applies content-merge
        (complete delivery authoritative; other statuses length-monotonic) and
        status-gate rules against any existing row before writing.
        """
        mid = message_id or new_id()
        write_content = content
        write_usage = metadata
        write_reasoning = reasoning_content
        if merge:
            from agentcore.core.message_merge import (
                merge_usage_status,
                pick_merged_content,
            )

            existing = await self.get_by_id(mid, conversation_id=conversation_id)
            if existing is not None:
                incoming_status = (metadata or {}).get("status")
                write_content = pick_merged_content(
                    existing.content,
                    content,
                    incoming_status=incoming_status,
                )
                write_reasoning = pick_merged_content(
                    existing.reasoning_content,
                    reasoning_content,
                    incoming_status=incoming_status,
                )
                write_usage = merge_usage_status(existing.usage, metadata)
        # Final gate: strip vendor DSML / tool-protocol residue + length ceiling
        # (complete / paused / incomplete all land here). Mid-turn prose stays in
        # ``turn_stream_state`` until finalize; incomplete / cancel salvage also
        # truncates at the first DSML open tag.
        from agentcore.core.assistant_content import prepare_assistant_content
        from agentcore.core.message_merge import MESSAGE_STATUS_INCOMPLETE

        status_for_prep = (write_usage or {}).get("status") or (metadata or {}).get("status")
        write_content = prepare_assistant_content(
            write_content or "",
            salvage=status_for_prep == MESSAGE_STATUS_INCOMPLETE,
        )
        values: dict = {
            "id": mid,
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": strip_nul(write_content),
            "reasoning_content": strip_nul(write_reasoning),
            "usage": strip_nul(write_usage),
            "trace_id": trace_id,
        }
        if citations is not None:
            values["citations"] = strip_nul(citations)
        if evidence_ledger is not None:
            values["evidence_ledger"] = strip_nul(evidence_ledger)
        update_set: dict = {
            "content": strip_nul(write_content),
            "reasoning_content": strip_nul(write_reasoning),
            "usage": strip_nul(write_usage),
            "trace_id": trace_id,
        }
        if citations is not None:
            update_set["citations"] = strip_nul(citations)
        if evidence_ledger is not None:
            update_set["evidence_ledger"] = strip_nul(evidence_ledger)
        await self._session.execute(
            pg_insert(Message)
            .values(**values)
            .on_conflict_do_update(index_elements=["id"], set_=update_set)
        )
        await self._touch_activity(conversation_id)
        await self._session.commit()
        row = await self.get_by_id(mid, conversation_id=conversation_id)
        assert row is not None
        return row

    async def set_followups(
        self, message_id: str, *, conversation_id: str, followups: list[str]
    ) -> None:
        """Update ``messages.followups`` (historical / admin; live mint path retired).

        Scoped by conversation_id (defense in depth); a no-match id is a harmless no-op.
        """
        await self._session.execute(
            update(Message)
            .where(Message.id == message_id, Message.conversation_id == conversation_id)
            .values(followups=followups)
        )
        await self._session.commit()

    async def set_baseline_snapshot_id(
        self, message_id: str, *, conversation_id: str, snapshot_id: str
    ) -> None:
        """Stamp the A1+ turn-baseline snapshot id onto an assistant row (best-effort path).

        Scoped by conversation_id; a no-match id is a no-op. Called right after a labeled
        workspace snapshot succeeds at turn start — never blocks the pipeline.
        """
        await self._session.execute(
            update(Message)
            .where(Message.id == message_id, Message.conversation_id == conversation_id)
            .values(baseline_snapshot_id=snapshot_id)
        )
        await self._session.commit()

    async def set_cost(self, message_id: str, *, conversation_id: str, cost: dict) -> None:
        """Backfill the turn's cost snapshot onto an existing assistant row (P2 DERIVED).

        Same finalize-tail pattern as :meth:`set_followups`: the ledger write happens in the
        same session as the assistant upsert, then this targeted UPDATE stamps the
        ``message_end.cost`` shape (nano-CNY components + currency) so reload footers do not
        need a second round-trip. Scoped by conversation_id; a no-match id is a no-op.
        """
        await self._session.execute(
            update(Message)
            .where(Message.id == message_id, Message.conversation_id == conversation_id)
            .values(cost=cost)
        )
        await self._session.commit()

    async def merge_usage(self, message_id: str, *, conversation_id: str, usage: dict) -> None:
        """Merge keys onto an existing assistant ``usage`` JSON (finalize-tail).

        Interrupt close writes incomplete chrome first, then stamps ledger token
        totals so the bubble matches ``cost_events`` for the same ``message_id``.
        Status-gate merge keeps ``incomplete`` / ``interrupt_reason``. A no-match
        id is a no-op.
        """
        from agentcore.core.message_merge import merge_usage_status

        existing = await self.get_by_id(message_id, conversation_id=conversation_id)
        if existing is None:
            return
        merged = merge_usage_status(existing.usage, usage)
        await self._session.execute(
            update(Message)
            .where(Message.id == message_id, Message.conversation_id == conversation_id)
            .values(usage=strip_nul(merged))
        )
        await self._session.commit()

    async def set_feedback(
        self, message_id: str, *, conversation_id: str, feedback: str | None
    ) -> bool:
        """Set / clear the user's 点赞/点踩 on a message (回复反馈). Returns whether a row matched.

        Scoped by ``conversation_id`` (defense in depth — the route has already proven
        ownership of the conversation, so a guessed id from another chat won't match →
        IDOR-safe). ``feedback`` is ``"up"`` / ``"down"`` to rate, or ``None`` to clear
        the rating back to 未评价. A no-match id is a harmless False (route 404s).
        """
        result = await self._session.execute(
            update(Message)
            .where(Message.id == message_id, Message.conversation_id == conversation_id)
            .values(feedback=feedback)
        )
        await self._session.commit()
        return (result.rowcount or 0) > 0

    async def copy_all(self, source_conversation_id: str, target_conversation_id: str) -> int:
        """Copy every message of one conversation into another (对话克隆). Returns the count.

        Backs 克隆对话 (duplicate a conversation): the target is a freshly-created empty
        conversation, so this bulk-inserts fresh-id copies of the source's rows, preserving
        render order (``created_at`` copied verbatim) and content-level fields (role /
        content / reasoning / usage / attachments / agent_mentions / citations /
        followups / cost).

        Intentionally NOT copied: ``trace_id`` (a copy is not a real turn — reusing it would
        double-link the original turn's logs), ``feedback`` (a rating belongs to the turn the
        user actually rated), and the separate ``turn_journal`` replay stream (§8.3, keyed by
        message id) — so a cloned multi-agent turn keeps its final text but re-renders as a
        plain bubble rather than replaying its team graph. A pragmatic MVP scope for 克隆.
        """
        rows = await self.list_all_for_conversation(source_conversation_id)
        copies = [
            Message(
                id=new_id(),
                conversation_id=target_conversation_id,
                role=r.role,
                content=r.content,
                reasoning_content=r.reasoning_content,
                usage=r.usage,
                attachments=list(r.attachments or []),
                agent_mentions=list(r.agent_mentions or []),
                citations=list(r.citations or []),
                evidence_ledger=list(r.evidence_ledger or []),
                followups=list(r.followups or []),
                cost=dict(r.cost) if r.cost else None,
                created_at=r.created_at,
            )
            for r in rows
        ]
        if not copies:
            return 0
        self._session.add_all(copies)
        await self._session.commit()
        return len(copies)

    async def _touch_activity(self, conversation_id: str) -> None:
        from agentcore.db.repositories.conversations import ConversationRepository

        await ConversationRepository(self._session).touch_activity(conversation_id, commit=False)

    async def count_by_conversation(self, conversation_id: str) -> int:
        """Number of messages in a conversation (0 for a brand-new, unsent one).

        Backs the "started?" check the move guard uses to lock a conversation's
        workspace once it has begun (双模式工作区 §九 ⑩).
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )
        return result.scalar_one()

    async def counts_for_conversations(self, conversation_ids: Sequence[str]) -> dict[str, int]:
        """Message counts keyed by conversation id, for the ids given.

        One GROUP BY for the whole sidebar so per-conversation counts don't fan out
        into an N+1. Ids with no messages are simply absent from the map (callers
        default them to 0).
        """
        if not conversation_ids:
            return {}
        result = await self._session.execute(
            select(Message.conversation_id, func.count())
            .where(Message.conversation_id.in_(conversation_ids))
            .group_by(Message.conversation_id)
        )
        return {row[0]: row[1] for row in result.all()}

    async def previews_for_conversations(self, conversation_ids: Sequence[str]) -> dict[str, str]:
        """Last visible assistant sentence per conversation (sidebar list preview).

        One windowed read for the whole list — same batch shape as
        :meth:`counts_for_conversations`. Walks back past empty / running /
        chrome-only assistant rows. Empty and stop-chrome bodies are dropped in
        SQL so the lookback window is not consumed by them (walk-back still
        holds under truncation). Ids with no qualifying assistant are absent
        (callers default them to ``None``). Never falls back to a user turn.
        """
        if not conversation_ids:
            return {}
        from agentcore.core.list_preview import (
            PREVIEW_CHROME_ONLY,
            PREVIEW_SQL_LOOKBACK,
            pick_last_visible_assistant_preview,
        )

        lookback = PREVIEW_SQL_LOOKBACK
        stripped = func.btrim(func.coalesce(Message.content, ""))
        ranked = (
            select(
                Message.conversation_id,
                Message.content,
                Message.usage,
                Message.created_at,
                func.row_number()
                .over(
                    partition_by=Message.conversation_id,
                    order_by=Message.created_at.desc(),
                )
                .label("rn"),
            )
            .where(
                Message.conversation_id.in_(conversation_ids),
                Message.role == "assistant",
                stripped != "",
                stripped.notin_(tuple(sorted(PREVIEW_CHROME_ONLY))),
            )
            .subquery()
        )
        result = await self._session.execute(
            select(
                ranked.c.conversation_id,
                ranked.c.content,
                ranked.c.usage,
                ranked.c.created_at,
            )
            .where(ranked.c.rn <= lookback)
            .order_by(ranked.c.conversation_id, ranked.c.created_at.desc())
        )
        grouped: dict[str, list[tuple[str | None, object | None]]] = {}
        for cid, content, usage, _created in result.all():
            grouped.setdefault(cid, []).append((content, usage))
        out: dict[str, str] = {}
        for cid, rows in grouped.items():
            preview = pick_last_visible_assistant_preview(rows)
            if preview:
                out[cid] = preview
        return out

    async def first_user_contents_for_conversations(
        self, conversation_ids: Sequence[str]
    ) -> dict[str, str]:
        """Earliest non-empty user-message body per conversation (display-title fallback).

        One windowed read for the whole list — same batch shape as
        :meth:`counts_for_conversations` / :meth:`previews_for_conversations`.
        Ids with no non-empty user row are absent (callers leave ``title`` empty).
        """
        if not conversation_ids:
            return {}
        stripped = func.btrim(func.coalesce(Message.content, ""))
        ranked = (
            select(
                Message.conversation_id,
                Message.content,
                func.row_number()
                .over(
                    partition_by=Message.conversation_id,
                    order_by=Message.created_at.asc(),
                )
                .label("rn"),
            )
            .where(
                Message.conversation_id.in_(conversation_ids),
                Message.role == "user",
                stripped != "",
            )
            .subquery()
        )
        result = await self._session.execute(
            select(ranked.c.conversation_id, ranked.c.content).where(ranked.c.rn == 1)
        )
        return {cid: content for cid, content in result.all() if content}

    async def unfolded_counts_for_conversations(
        self, conversation_ids: Sequence[str]
    ) -> dict[str, int]:
        """Messages each conversation's rolling summary does NOT cover yet.

        Everything newer than that conversation's own ``compacted_through`` — the whole
        chat where compaction has never written a watermark. Joining the watermark in
        keeps this to one query for the whole list instead of a per-conversation read,
        the same shape as :meth:`counts_for_conversations`, which the caller pairs it
        with to tell an un-folded backlog apart from a merely long chat
        (``conversation/context_gap.py``). Ids with no un-folded messages are absent
        from the map (callers default them to 0).
        """
        if not conversation_ids:
            return {}
        result = await self._session.execute(
            select(Message.conversation_id, func.count())
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.conversation_id.in_(conversation_ids),
                (Conversation.compacted_through.is_(None))
                | (Message.created_at > Conversation.compacted_through),
            )
            .group_by(Message.conversation_id)
        )
        return {row[0]: row[1] for row in result.all()}

    async def search(
        self,
        user_id: str,
        query: str,
        *,
        limit: int,
        updated_after: datetime | None = None,
        folder_id: str | None = None,
    ) -> Sequence[tuple[Message, str]]:
        """Owner-scoped message-content substring search (全局搜索 Tier 1).

        ``messages`` carries no ``user_id``, so this JOINs ``conversations`` to scope
        by owner (never another user's content — IDOR-safe) and to exclude
        soft-deleted and hidden handoff-host conversations. ILIKE over ``content``,
        newest-first, capped at ``limit``. Returns ``(message, conversation_title)``
        pairs so the route can render the owning conversation as list-row context
        without an N+1.

        The optional facets (搜索结果过滤) filter the same set server-side:
        ``updated_after`` bounds a hit's ``created_at`` (时间过滤 — the message's own
        time, which the route surfaces as its ``updated_at``); ``folder_id`` keeps
        only hits whose owning conversation is filed in that folder/工作区.
        """
        stmt = (
            select(Message, Conversation.title)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.user_id == user_id,
                Conversation.deleted_at.is_(None),
                Conversation.mode != "handoff",
                Message.content.is_not(None),
                Message.content.ilike(_ilike_pattern(query)),
            )
        )
        if updated_after is not None:
            stmt = stmt.where(Message.created_at >= updated_after)
        if folder_id is not None:
            stmt = stmt.where(Conversation.folder_id == folder_id)
        result = await self._session.execute(stmt.order_by(Message.created_at.desc()).limit(limit))
        return [(row[0], row[1]) for row in result.all()]

    async def list_by_conversation(
        self, conversation_id: str, *, limit: int = 100, offset: int = 0
    ) -> tuple[Sequence[Message], int]:
        base_query = select(Message).where(Message.conversation_id == conversation_id)

        count_result = await self._session.execute(
            select(func.count()).select_from(base_query.subquery())
        )
        total = count_result.scalar_one()

        result = await self._session.execute(
            base_query.order_by(Message.created_at.asc()).limit(limit).offset(offset)
        )
        return result.scalars().all(), total

    async def list_all_for_conversation(self, conversation_id: str) -> Sequence[Message]:
        """Every message of a conversation, oldest-first — the full transcript.

        Backs export (导出对话) and the share snapshot (分享对话): both freeze/serialize
        the WHOLE conversation, not a scroll window, so an unbounded ordered read is
        the right shape (an explicit, infrequent operation, unlike the paginated chat
        view). Returns rows in render order (created_at asc).
        """
        result = await self._session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        return result.scalars().all()

    async def list_recent(self, conversation_id: str, *, limit: int) -> Sequence[Message]:
        """The most recent ``limit`` messages, returned in chronological order.

        Unlike ``list_by_conversation`` (oldest-first page), this tails the
        conversation — the window the offline memory consolidation reconciles
        against the existing memory (memory/consolidation.py).
        """
        result = await self._session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    async def list_recent_after(
        self, conversation_id: str, *, after: datetime, limit: int
    ) -> Sequence[Message]:
        """The most recent ``limit`` messages STRICTLY NEWER than ``after``, chronological.

        The compaction loader's window above the watermark (执行引擎 §三 长对话压缩):
        replays the un-folded tail (everything after ``compacted_through``) prefixed by
        the rolling summary. Recent-biased like ``list_recent`` — under a stalled
        compaction the tail can outgrow ``limit``, and dropping the OLDEST of it (which
        are at least near the summary boundary) is safer than dropping the newest, which
        would re-introduce the very recency loss compaction exists to avoid.
        """
        result = await self._session.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.created_at > after,
            )
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    # --- Cursor windows (载入模型: 最新窗口 + 上下滚动 + 命中定位, load-around B) ---
    # The client opens on the latest window, then scrolls up (``list_before``) /
    # down (``list_after``), or jumps to a window centered on a message
    # (``window_around``) for a search hit. Each fetches ``limit + 1`` rows so the
    # extra row exactly answers "is there more in this direction?" without a second
    # count query. All return messages chronological (oldest-first) to match render
    # order. Cursors are ``created_at`` (strict ``<`` / ``>``): a tie at the
    # boundary is a tolerated MVP simplification — conversation turns are inserted
    # seconds apart, unlike rapid IM.

    async def list_latest(
        self, conversation_id: str, *, limit: int
    ) -> tuple[Sequence[Message], bool]:
        """The newest ``limit`` messages, chronological. ``(messages, has_more_before)``."""
        rows = (
            (
                await self._session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at.desc())
                    .limit(limit + 1)
                )
            )
            .scalars()
            .all()
        )
        has_more_before = len(rows) > limit
        return list(reversed(rows[:limit])), has_more_before

    async def list_before(
        self, conversation_id: str, *, before: datetime, limit: int
    ) -> tuple[Sequence[Message], bool]:
        """``limit`` messages strictly older than ``before``, chronological (scroll up).

        ``(messages, has_more_before)`` — whether even older messages remain.
        """
        rows = (
            (
                await self._session.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.created_at < before,
                    )
                    .order_by(Message.created_at.desc())
                    .limit(limit + 1)
                )
            )
            .scalars()
            .all()
        )
        has_more_before = len(rows) > limit
        return list(reversed(rows[:limit])), has_more_before

    async def list_after(
        self, conversation_id: str, *, after: datetime, limit: int
    ) -> tuple[Sequence[Message], bool]:
        """``limit`` messages strictly newer than ``after``, chronological (scroll down).

        ``(messages, has_more_after)`` — whether even newer messages remain.
        """
        rows = (
            (
                await self._session.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.created_at > after,
                    )
                    .order_by(Message.created_at.asc())
                    .limit(limit + 1)
                )
            )
            .scalars()
            .all()
        )
        has_more_after = len(rows) > limit
        return rows[:limit], has_more_after

    async def window_around(
        self, conversation_id: str, *, message_id: str, before: int, after: int
    ) -> tuple[Sequence[Message], bool, bool] | None:
        """A window centered on a message (search-hit jump, load-around B).

        ``before`` older + the target + ``after`` newer, chronological. Returns
        ``(messages, has_more_before, has_more_after)``, or ``None`` if the message
        is not in this conversation (the route 404s).
        """
        target = await self.get_by_id(message_id, conversation_id=conversation_id)
        if target is None:
            return None
        older, has_more_before = await self.list_before(
            conversation_id, before=target.created_at, limit=before
        )
        newer, has_more_after = await self.list_after(
            conversation_id, after=target.created_at, limit=after
        )
        return [*older, target, *newer], has_more_before, has_more_after

    async def latest_created_at(self, conversation_id: str) -> datetime | None:
        """created_at of the newest message (None when the conversation is empty).

        The memory consolidation watermark: the runner skips when this is not newer
        than the stored watermark, and stamps the watermark to it after a pass.
        """
        result = await self._session.execute(
            select(func.max(Message.created_at)).where(Message.conversation_id == conversation_id)
        )
        return result.scalar_one_or_none()

    async def user_message_for_assistant(
        self, *, conversation_id: str, assistant_message_id: str
    ) -> Message | None:
        """The user row that opened the turn anchored by ``assistant_message_id``.

        Resume / re-pause write-backs may carry a fresh client-minted ``user_message_id``;
        the assistant ``message_id`` is stable, so look up the paired user row by timeline
        order instead of trusting the retried id.
        """
        assistant = await self.get_by_id(assistant_message_id, conversation_id=conversation_id)
        if assistant is None or assistant.role != "assistant":
            return None
        result = await self._session.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == "user",
                Message.created_at <= assistant.created_at,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, message_id: str, *, conversation_id: str) -> Message | None:
        result = await self._session.execute(
            select(Message).where(
                Message.id == message_id,
                Message.conversation_id == conversation_id,
            )
        )
        return result.scalar_one_or_none()

    async def update_content(
        self,
        message_id: str,
        content: str | None = None,
        *,
        attachments: list | None = None,
        agent_mentions: list | None = None,
        commit: bool = True,
    ) -> None:
        values: dict = {}
        if content is not None:
            values["content"] = content
        if attachments is not None:
            values["attachments"] = strip_nul(attachments)
        if agent_mentions is not None:
            values["agent_mentions"] = strip_nul(agent_mentions)
        if not values:
            return
        await self._session.execute(
            update(Message).where(Message.id == message_id).values(**values)
        )
        await commit_or_flush(self._session, commit=commit)

    async def delete_after(
        self, conversation_id: str, *, after_created_at: datetime, commit: bool = True
    ) -> int:
        """Hard-delete messages created strictly after a point in time.

        Used by regenerate / edit-and-resend to drop the superseded assistant
        reply (and any later turns) before re-running. Messages have no
        soft-delete column — replacing a turn means the old branch is gone
        (conversation branching is a separate, later feature). Each dropped
        message's ``turn_journal`` replay stream goes with it (§8.3 唯一事实源 — it
        could never project without its message), as do leftover ``turn_stream_state``
        snapshots. Any matching ``paused_turns`` frame
        is dropped too — otherwise resume would find a frame whose journal is gone —
        together with the frame's recorded outcome: a card whose turn no longer exists
        must read as「已重新生成」, not go on reporting the decision that once settled it.

        Pass ``commit=False`` when pairing with :meth:`update_content` in one txn.
        """
        await delete_stream_state_after(
            self._session, conversation_id, after_created_at=after_created_at
        )
        await delete_journal_after(
            self._session, conversation_id, after_created_at=after_created_at
        )
        await delete_audit_after(self._session, conversation_id, after_created_at=after_created_at)
        dropped_ids = select(Message.id).where(
            Message.conversation_id == conversation_id,
            Message.created_at > after_created_at,
        )
        await self._session.execute(
            delete(PausedTurnRow).where(
                PausedTurnRow.conversation_id == conversation_id,
                PausedTurnRow.message_id.in_(dropped_ids),
            )
        )
        await self._session.execute(
            delete(PausedTurnOutcomeRow).where(
                PausedTurnOutcomeRow.conversation_id == conversation_id,
                PausedTurnOutcomeRow.message_id.in_(dropped_ids),
            )
        )
        await self._session.execute(
            delete(TurnLeaseRow).where(
                TurnLeaseRow.conversation_id == conversation_id,
                TurnLeaseRow.message_id.in_(dropped_ids),
            )
        )
        # 消息收藏 pointers to any superseded message go with it (a regenerate drops
        # the old branch — its bookmarks would otherwise dangle).
        await self._session.execute(
            delete(MessageBookmark).where(
                MessageBookmark.conversation_id == conversation_id,
                MessageBookmark.message_id.in_(dropped_ids),
            )
        )
        result = await self._session.execute(
            delete(Message).where(
                Message.conversation_id == conversation_id,
                Message.created_at > after_created_at,
            )
        )
        await commit_or_flush(self._session, commit=commit)
        return result.rowcount or 0

    async def delete_by_id(
        self, message_id: str, *, conversation_id: str, commit: bool = True
    ) -> bool:
        """Hard-delete one message (单条消息删除). Returns whether a row was removed.

        Scoped to ``conversation_id`` so a guessed id from another conversation
        won't match (the route has already proven ownership of this conversation —
        IDOR-safe; the turn_journal delete is scoped the same way, so a cross-tenant
        id touches neither row). Messages have no soft-delete column, so this is a
        physical delete; its ``turn_journal`` replay stream is dropped with it (§8.3
        唯一事实源), leftover ``turn_stream_state`` snapshots go too, and any matching
        ``paused_turns`` frame is dropped too (otherwise
        resume would find a frame whose journal is gone) along with that frame's
        recorded outcome (a deleted turn reports「已重新生成或删除」, never its old
        decision), but the append-only ``cost_events`` ledger is intentionally left
        intact (real spend is never rewritten — 不变量 #1). No-op (False) if absent.

        Pass ``commit=False`` when pairing two deletes in one txn. Default
        ``commit=True`` keeps standalone delete atomicity for other callers.
        """
        await delete_stream_state_for_message(self._session, conversation_id, message_id)
        await delete_journal_for_message(self._session, conversation_id, message_id)
        await delete_audit_for_message(self._session, conversation_id, message_id)
        await self._session.execute(
            delete(PausedTurnRow).where(
                PausedTurnRow.message_id == message_id,
                PausedTurnRow.conversation_id == conversation_id,
            )
        )
        await self._session.execute(
            delete(PausedTurnOutcomeRow).where(
                PausedTurnOutcomeRow.message_id == message_id,
                PausedTurnOutcomeRow.conversation_id == conversation_id,
            )
        )
        await self._session.execute(
            delete(TurnLeaseRow).where(
                TurnLeaseRow.message_id == message_id,
                TurnLeaseRow.conversation_id == conversation_id,
            )
        )
        # Drop any 消息收藏 pointer to this message (else it would dangle).
        await self._session.execute(
            delete(MessageBookmark).where(
                MessageBookmark.message_id == message_id,
                MessageBookmark.conversation_id == conversation_id,
            )
        )
        result = await self._session.execute(
            delete(Message).where(
                Message.id == message_id,
                Message.conversation_id == conversation_id,
            )
        )
        await commit_or_flush(self._session, commit=commit)
        return (result.rowcount or 0) > 0
