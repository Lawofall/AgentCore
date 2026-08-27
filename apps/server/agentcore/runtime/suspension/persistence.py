"""DB-backed save / claim / delete / restore for paused turns (结构化挂起 2b turn 级落盘).

Bridges the DB-unaware ``delegate`` checkpoint hook + the resume entry point to the
``paused_turns`` table. The pipeline wires :func:`save_paused_turn` / :func:`delete_paused_turn`
into ``delegate`` (persist a frame before the wait; drop it after a live in-process
resolve), and ``resume_chat`` calls :func:`claim_paused_turn` (atomic read-and-delete,
so a turn is never resumed twice) + :func:`list_paused_turns` (a conversation's pending
frames on reopen). On cloud resume failure, :func:`restore_paused_turn` re-upserts the
claimed frame (sidecar ``rollback_claim`` parity) so the user can retry. Uses
``async_session_factory`` directly (not an injected request session), matching the
cost-ledger / session-roster persistence posture.

The claim's winner also writes down what it settled (``paused_turn_outcomes``, in the
same transaction as the delete), so everyone who lost that race reads the winner's
decision instead of inferring one — see :func:`claim_paused_turn` and
``runtime/suspension/consumed.py``.

The paused turn's journal-so-far rides the ``turn_journal`` table (唯一事实源, §8.3),
NOT the frame. Facts are normally appended on emit during the turn; at pause time
:func:`save_paused_turn` also snapshots the suspending face's ``journal_entries`` (the
authoritative fact-log stream, incl. the trailing ``*_required`` card) into
``turn_journal`` so a cold resume can rebuild the CEO window even when the append-on-emit
writer lagged or degraded. :func:`claim_paused_turn` re-hydrates from that table.

D11: save failures raise (no silent degrade). Claim competition → ``None`` (route 404);
claim-then-hydrate failure restores the frame and raises (route 5xx, retryable).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agentcore.attention import (
    attention_kind_of,
    attention_title,
    signal_attention_required,
    signal_attention_resolved,
)
from agentcore.core.errors import GoneError
from agentcore.core.log_context import get_log_value
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import PausedTurnRepository, TurnJournalRepository
from agentcore.fulfill.user_signal import push_paused_card_settled
from agentcore.push import PushNotification, notify_user
from agentcore.runtime.journal.writer import current_journal_writer
from agentcore.runtime.kickoff.retired import (
    is_leftover_team_preview_frame,
    refuse_if_leftover_team_preview,
)
from agentcore.runtime.settlement import align_cold_resume_resolved_to_winner
from agentcore.runtime.suspension import (
    SuspensionKind,
    TurnSuspension,
    suspension_from_json,
)
from agentcore.runtime.turn.ceo_continue import is_ceo_continue_frame

logger = get_logger(__name__)


async def save_paused_turn(suspension: TurnSuspension) -> None:
    """Persist one paused-turn frame, keyed by its ``message_id``.

    Stamps the ambient turn ``trace_id`` (this runs inside the pipeline's trace
    scope) so the persisted pause links back to its originating turn's logs.
    Upsert: re-pausing the same turn (resume → pause again) overwrites in place.
    The journal-so-far is NOT in the frame — it is written to ``turn_journal`` here
    (唯一事实源, §8.3) from the suspending face's ``journal_entries`` snapshot.
    Raises on persistence failure (D11 — no fake saved).
    """
    trace_id = suspension.trace_id or get_log_value("trace_id") or None
    writer = current_journal_writer.get()
    if writer is not None:
        await writer.flush()
        if writer.degraded:
            suspension.journal_degraded = True
    journal_entries = list(suspension.journal_entries or [])
    try:
        async with async_session_factory() as db:
            if journal_entries:
                repo = TurnJournalRepository(db)
                await repo.record(
                    turn_id=suspension.message_id,
                    conversation_id=suspension.conversation_id,
                    trace_id=trace_id,
                    entries=journal_entries,
                )
            await PausedTurnRepository(db).upsert(
                message_id=suspension.message_id,
                conversation_id=suspension.conversation_id,
                user_id=suspension.user_id,
                frame=suspension.to_json(),
                trace_id=trace_id,
            )
    except Exception as e:
        # D11：saver 失败必须如实暴露（假 saved 缝）——不再吞异常报 saved=True。
        logger.warning(
            "suspension.persist_failed",
            message_id=suspension.message_id,
            error=str(e),
        )
        raise
    # Hard boundary: after the durable snapshot is the canonical journal, seal the
    # append-on-emit writer so post-save emits (trailing *_required, suspending
    # tool_use_end) cannot diverge DB rows from the paused snapshot.
    if writer is not None:
        await writer.seal()
    # A durable pause is the canonical 需要你 (attention) event: the turn is now BLOCKED
    # on the user and stays so until they act.
    await _signal_pause_attention(suspension)
    # Fan a native push out to their devices so they learn even with the app
    # backgrounded (SSE gone). Best-effort + default-off (notify_user short-circuits
    # when push is unconfigured) — never blocks the pause.
    await _notify_pause(suspension)


def _pause_attention_fields(suspension: TurnSuspension) -> dict[str, Any] | None:
    """The ``ai_attention`` envelope for a durable pause; ``None`` for an unknown kind."""
    kind = attention_kind_of(suspension.kind.value)
    if kind is None or not suspension.user_id:
        return None
    return {
        "user_id": suspension.user_id,
        "conversation_id": suspension.conversation_id,
        # The paused turn IS the assistant message (message_id ≡ journal turn_id).
        "turn_id": suspension.message_id,
        "interaction_id": suspension.checkpoint_id,
        "kind": kind,
        "title": attention_title(
            kind, {"question": getattr(suspension, "question", "")}
        ),
    }


async def _signal_pause_attention(suspension: TurnSuspension) -> None:
    """Tell every live client of this user that the turn stopped on their card.

    Firehose only. The durable pause already has its own push trigger
    (:func:`_notify_pause`) with its own copy and audience; re-routing an
    established user-visible notification is a separate decision from adding the
    signal that was missing, so it is left exactly as it was.
    """
    fields = _pause_attention_fields(suspension)
    if fields is None:
        return
    await signal_attention_required(**fields, push=False)


async def _notify_pause(suspension: TurnSuspension) -> None:
    """Push a 需要你 notification for a durable pause (best-effort, default-off).

    Copy is keyed by the suspend kind; the ``data`` carries the ids the mobile client
    deep-links on tap (conversation + the paused turn). ``notify_user`` itself swallows
    all errors, so this never affects the pause.
    """
    if suspension.kind == SuspensionKind.PLAN_REVIEW:
        title = "AI 计划待确认"
        body = "团队已产出阶段成果，待你确认是否继续。"
    else:
        title = "AI 需要你的回应"
        question = (getattr(suspension, "question", "") or "").strip()
        body = question[:120] if question else "AI 正在等待你的回应以继续任务。"
    await notify_user(
        suspension.user_id,
        PushNotification(
            title=title,
            body=body,
            data={
                "conversation_id": suspension.conversation_id,
                "message_id": suspension.message_id,
                "kind": suspension.kind.value,
            },
        ),
    )


async def delete_paused_turn(message_id: str) -> None:
    """Drop a paused-turn frame (a live in-process resolve / timeout settled it).

    Best-effort: a stale frame left by a failed delete is harmless — ``claim`` would
    only resurrect a turn the user can re-decide, and the next live resolve overwrites
    it. NEVER raises into the turn.

    Removing a real row means the card is gone without a cold resume, so this is the
    other place an ``ai_attention(resolved)`` has to come from — otherwise the badge
    on the user's other devices would stay lit forever.
    """
    removed: dict[str, Any] | None = None
    try:
        async with async_session_factory() as db:
            row = await PausedTurnRepository(db).delete(message_id)
            if row is not None:
                # Materialize before the session closes (mirrors ``claim``).
                removed = {
                    "user_id": row.user_id,
                    "conversation_id": row.conversation_id,
                    "frame": dict(row.frame) if isinstance(row.frame, dict) else row.frame,
                }
    except Exception as e:  # noqa: BLE001 — cleanup must never break the turn
        logger.warning("suspension.delete_failed", message_id=message_id, error=str(e))
        return
    if removed is not None:
        await _signal_frame_resolved(
            user_id=removed["user_id"],
            conversation_id=removed["conversation_id"],
            frame=removed["frame"],
            message_id=message_id,
        )


async def _upsert_paused_frame(
    *,
    message_id: str,
    conversation_id: str,
    user_id: str,
    frame: dict[str, Any],
    trace_id: str | None,
) -> None:
    """Re-upsert a raw paused frame (restore / claim-hydrate rollback). Best-effort."""
    try:
        async with async_session_factory() as db:
            await PausedTurnRepository(db).upsert(
                message_id=message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                frame=frame,
                trace_id=trace_id,
            )
    except Exception as e:  # noqa: BLE001 — restore must never break the error path
        logger.warning(
            "suspension.restore_failed",
            message_id=message_id,
            error=str(e),
        )


async def _signal_frame_resolved(
    *,
    user_id: str,
    conversation_id: str,
    frame: Any,
    message_id: str,
    decision: str = "",
) -> None:
    """This card is down — clear the badge, and drop it on the user's other installs.

    Two audiences, two channels. ``ai_attention`` is the account-level badge:
    which conversation still wants them, no content (设计 §2.2). The desktop
    channel carries the card itself — an install holding this cold card in a
    conversation it is not watching has a 继续 button that can now only 404, and
    nothing on its display streams will ever say so.

    Reads the kind / checkpoint straight off the stored frame so it works on the
    live-resolve delete path too, where no :class:`TurnSuspension` was rebuilt
    (and where ``decision`` is not ours to state — the conversation stream
    carries that). A frame that cannot be read is not worth failing over.
    """
    data = frame if isinstance(frame, dict) else {}
    card_kind = str(data.get("kind") or "")
    kind = attention_kind_of(card_kind)
    if kind is None or not user_id:
        return
    push_paused_card_settled(
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        checkpoint_id=str(data.get("checkpoint_id") or ""),
        kind=card_kind,
        decision=decision,
        decided_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
    await signal_attention_resolved(
        user_id=user_id,
        conversation_id=conversation_id,
        turn_id=message_id,
        interaction_id=str(data.get("checkpoint_id") or ""),
        kind=kind,
    )


async def restore_paused_turn(suspension: TurnSuspension) -> None:
    """Re-upsert a claimed frame after a failed cloud resume so the user can retry.

    ``claim`` is DELETE ... RETURNING — on resume failure the in-memory
    :class:`TurnSuspension` and the ``turn_journal`` rows still exist, but the
    ``paused_turns`` row is gone. This puts the frame back (sidecar's
    ``rollback_claim`` parity for the cloud path). Does NOT re-push (the user
    already got the original pause notification) and does NOT rewrite
    ``turn_journal`` (claim left those rows in place). Best-effort; never raises.
    """
    await _upsert_paused_frame(
        message_id=suspension.message_id,
        conversation_id=suspension.conversation_id,
        user_id=suspension.user_id,
        frame=suspension.to_json(),
        trace_id=suspension.trace_id,
    )
    # Claim cleared the latch; put it back so cold hydrate still sees a true pause.
    await _set_message_pause_latch(
        message_id=suspension.message_id,
        conversation_id=suspension.conversation_id,
        paused=True,
    )
    # The claim already told every device the card was settled; it wasn't. Re-light
    # the badge (firehose only — same reason this path does not re-push).
    await _signal_pause_attention(suspension)


async def load_paused_turn(
    message_id: str, *, conversation_id: str | None = None
) -> TurnSuspension | None:
    """Read a paused turn without claiming (D8 cold-path peek before settlement prewrite).

    Does not touch ``turn_journal`` — the subsequent :func:`claim_paused_turn` re-hydrates
    entries (including any settlement prewritten between peek and claim). ``None`` when
    absent / wrong conversation / unreadable.
    """
    try:
        async with async_session_factory() as db:
            row = await PausedTurnRepository(db).get(message_id)
            if row is None:
                return None
            if conversation_id is not None and row.conversation_id != conversation_id:
                return None
            if is_ceo_continue_frame(row.frame):
                return None
            refuse_if_leftover_team_preview(row.frame)
            return suspension_from_json(row.frame)
    except GoneError:
        raise
    except Exception as e:  # noqa: BLE001 — peek failure reads as "not resumable"
        logger.warning("suspension.load_failed", message_id=message_id, error=str(e))
        return None


async def paused_turn_exists(message_id: str, *, conversation_id: str) -> bool:
    """Is the frame still on disk? Raises on a DB fault instead of guessing.

    The claim-miss path needs to know whether the frame was *consumed* (someone else
    continued the turn ⇒ idempotent success) or whether the claim merely failed. It
    cannot ask :func:`load_paused_turn`, which swallows read errors into ``None``: that
    would turn a broken database into「已被处理」and let this request's own prewritten
    settlement pose as somebody else's decision.
    """
    async with async_session_factory() as db:
        row = await PausedTurnRepository(db).get(message_id)
    if row is None or row.conversation_id != conversation_id:
        return False
    return not is_ceo_continue_frame(row.frame)


async def claim_paused_turn(
    message_id: str,
    *,
    conversation_id: str | None = None,
    decision: str,
    settled_by: str = "",
) -> TurnSuspension | None:
    """Atomically read-and-delete a paused turn for resume; ``None`` if already
    claimed / absent.

    The atomic claim (DELETE ... RETURNING) means two racing ``/resume`` calls can't
    both continue the same turn — the loser gets ``None``. Pass ``conversation_id``
    (the one the route verified the caller owns) so a frame is only claimed within that
    conversation (IDOR-safe).

    Winning is what「结了这张卡」means, so the winner states its conclusion here:
    ``decision`` (what it is about to apply) and ``settled_by`` (结算方 — the origin
    device) land in the same transaction as the delete, keyed to the frame's own
    ``checkpoint_id``. That row is what every loser reads; without it a loser could
    only look at the journal's last ``*_resolved``, which on this path is usually the
    settlement it prewrote seconds earlier — telling the user their own decision took
    effect while somebody else's actually ran.

    Prewrite still happens *before* this claim (the other端 must see the card as
    spent immediately) and still dedupes without ``decision``, so the durable
    ``*_resolved`` row can be the loser's. After hydrate succeeds, the winner
    rewrites that row to its own decision (same-key duplicates collapse to one).
    Already-matching journal is left untouched.

    Claim competition / missing row → ``None``. After a successful claim, frame parse
    or journal load failure restores the row and **raises** (route 5xx, frame kept for
    retry) — never silently drop a claimed frame.

    The journal-so-far is re-hydrated from ``turn_journal`` (唯一事实源, it is not in the
    frame) onto :attr:`TurnSuspension.journal_entries` (the §8.3 fact-log stream, incl. the
    execution facts the display projection drops): the resume folds it via ``window_from_journal``
    to rebuild the captain window (执行级事件溯源 Phase 2 ④/⑤ — the window is a projection of the
    journal, no longer read from a frame ``transcript`` blob, which is no longer serialized).
    The display ``journal`` resume seed is a DERIVED property of those entries (P0-B Phase 3),
    so it seeds identically to the Sidecar. The window's prior-turn history is reloaded
    separately from the message DB by the caller (``service.resume_chat``) and threaded in. The
    stored rows are left in place: the resumed turn re-records them wholesale on completion (or
    the TTL sweep clears them if the turn is abandoned).
    """
    claimed: dict[str, Any] | None = None
    try:
        async with async_session_factory() as db:
            existing = await PausedTurnRepository(db).get(message_id)
            if existing is not None and is_ceo_continue_frame(existing.frame):
                return None
            row = await PausedTurnRepository(db).claim(
                message_id,
                conversation_id=conversation_id,
                decision=decision,
                settled_by=settled_by,
            )
            if row is None:
                return None
            # Materialize before the session closes (expire_on_commit).
            claimed = {
                "message_id": row.message_id,
                "conversation_id": row.conversation_id,
                "user_id": row.user_id,
                "frame": dict(row.frame) if isinstance(row.frame, dict) else row.frame,
                "trace_id": row.trace_id,
            }
    except Exception as e:  # noqa: BLE001 — claim competition / DB fault → not resumable
        logger.warning("suspension.claim_failed", message_id=message_id, error=str(e))
        return None

    assert claimed is not None
    try:
        async with async_session_factory() as db:
            rows = await TurnJournalRepository(db).load_after(message_id, -1)
        from agentcore.runtime.journal.seq_space import split_live_and_overflow_rows

        live, overflow = split_live_and_overflow_rows(rows)
        refuse_if_leftover_team_preview(claimed["frame"])
        suspension = suspension_from_json(claimed["frame"])
    except Exception as e:
        logger.error(
            "suspension.claim_hydrate_failed",
            message_id=message_id,
            error=str(e),
        )
        await _upsert_paused_frame(
            message_id=claimed["message_id"],
            conversation_id=claimed["conversation_id"],
            user_id=claimed["user_id"],
            frame=claimed["frame"],
            trace_id=claimed["trace_id"],
        )
        raise

    aligned = align_cold_resume_resolved_to_winner(
        live,
        turn_id=message_id,
        checkpoint_id=str(suspension.checkpoint_id or ""),
        decision=decision,
    )
    if aligned is not None:
        try:
            async with async_session_factory() as db:
                # Align collapses duplicate ``*_resolved`` rows; prefix-only
                # ``record()`` would leave the extra live seq in place.
                await TurnJournalRepository(db).replace_live(
                    turn_id=message_id,
                    conversation_id=claimed["conversation_id"],
                    trace_id=claimed["trace_id"],
                    entries=aligned,
                )
        except Exception as e:  # noqa: BLE001 — frame already consumed; resume still runs
            logger.warning(
                "suspension.claim_journal_align_failed",
                message_id=message_id,
                error=str(e),
            )
        live = aligned
    suspension.journal_entries = [*live, *overflow]

    if suspension.journal_degraded and not suspension.journal_entries:
        logger.warning(
            "suspension.claim_journal_degraded",
            message_id=message_id,
        )
    # Resume claimed the frame — clear cold pause latch so reload cannot paint a
    # fake「等待确认」from usage.paused after the user already continued.
    await _set_message_pause_latch(
        message_id=claimed["message_id"],
        conversation_id=claimed["conversation_id"],
        paused=False,
    )
    # Exactly one caller wins the claim, so exactly one「已处理」reaches the user's
    # other devices — whichever端 actually answered the card, carrying the decision
    # it just applied.
    await _signal_frame_resolved(
        user_id=claimed["user_id"],
        conversation_id=claimed["conversation_id"],
        frame=claimed["frame"],
        message_id=claimed["message_id"],
        decision=decision,
    )
    return suspension


async def clear_message_pause_latch(*, message_id: str, conversation_id: str) -> None:
    """Drop ``usage.paused`` on a turn whose frame is gone for good (TTL sweep).

    Claim clears the latch as part of continuing; a swept frame has no continuation to
    do it, and the latch alone is enough for a reopened client to paint a decision card
    that can now only fail. Best-effort, same as every other latch write.
    """
    await _set_message_pause_latch(
        message_id=message_id,
        conversation_id=conversation_id,
        paused=False,
    )


async def _set_message_pause_latch(
    *,
    message_id: str,
    conversation_id: str,
    paused: bool,
) -> None:
    """Best-effort write of ``usage.paused`` via :func:`merge_usage_status`."""
    from agentcore.core.message_merge import merge_usage_status
    from agentcore.db.repositories import MessageRepository

    try:
        async with async_session_factory() as db:
            repo = MessageRepository(db)
            existing = await repo.get_by_id(message_id, conversation_id=conversation_id)
            if existing is None:
                return
            incoming: dict = {"paused": paused}
            usage = existing.usage if isinstance(existing.usage, dict) else None
            status = usage.get("status") if usage else None
            if status:
                incoming["status"] = status
            elif paused:
                incoming["status"] = "running"
            merged = merge_usage_status(
                existing.usage if isinstance(existing.usage, dict) else None,
                incoming,
            )
            await repo.upsert_assistant(
                conversation_id=conversation_id,
                message_id=message_id,
                content=existing.content or "",
                reasoning_content=existing.reasoning_content,
                citations=existing.citations,
                evidence_ledger=existing.evidence_ledger,
                trace_id=existing.trace_id,
                metadata=merged,
                merge=True,
            )
    except Exception as e:  # noqa: BLE001 — latch must not block claim/restore
        logger.warning(
            "suspension.pause_latch_write_failed",
            message_id=message_id,
            paused=paused,
            error=str(e),
        )


async def list_paused_turns(conversation_id: str) -> list[TurnSuspension]:
    """A conversation's pending paused turns (oldest first), for reopen-time hydration.

    Read-only (does not claim); the resume call claims. Best-effort: an error yields an
    empty list so reopening a conversation never fails on a paused-turn lookup.
    """
    try:
        async with async_session_factory() as db:
            rows = await PausedTurnRepository(db).list_pending(conversation_id)
    except Exception as e:  # noqa: BLE001 — a list failure degrades to "none pending"
        logger.warning("suspension.list_failed", conversation_id=conversation_id, error=str(e))
        return []
    out: list[TurnSuspension] = []
    for r in rows:
        if is_ceo_continue_frame(r.frame):
            continue
        if is_leftover_team_preview_frame(r.frame):
            continue
        out.append(suspension_from_json(r.frame))
    return out
