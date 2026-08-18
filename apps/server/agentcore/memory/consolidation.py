"""Two-layer memory orchestration (episodic session digests → semantic consolidation).

Episodic (live path): each finished turn arms a per-conversation idle debounce /
turn-cap. When it fires, a ≤200-char session summary of everything since the
``memory_synced_at`` watermark (plus optional verified folder facts; action inventory
from turn_journal) is appended to ``memory_episodes`` and a light ``memory_updated`` tip
is pushed — never a direct preference/profile write. The tip is pushed only when the LLM
really summarized the window; a timed-out pass still stores its episode for the semantic
layer but shows no card.

Semantic (batch): after an episodic write (and on the periodic sweeper), if undigested
episodes ≥ ``memory_semantic_min_episodes`` OR age since last success ≥
``memory_semantic_max_age_hours``, one consolidator pass rewrites always-files
(including folder ``导航.md`` incremental merge) and applies topic ops, then pushes a
diff card. Digested episodes older than 30 days are purged on each sweeper pass.

Open-turn deferral, per-user locks, and ``memory_synced_at`` watermarks are unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from agentcore.billing.gate import BackgroundLlmSkip, BackgroundSkipReason, run_background_llm
from agentcore.config import settings
from agentcore.conversation.history import load_recent_history
from agentcore.conversation.store.merge import (
    MESSAGE_STATUS_COMPLETE,
    MESSAGE_STATUS_FAILED,
    MESSAGE_STATUS_INCOMPLETE,
    MESSAGE_STATUS_RUNNING,
)
from agentcore.core.log_context import log_context
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.errors import is_pool_timeout_error, is_schema_error
from agentcore.db.repositories import (
    ConversationRepository,
    MemoryUpdateRepository,
    MessageRepository,
    PausedTurnRepository,
    TurnJournalRepository,
)
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.factory import build_provider
from agentcore.llm.resolve import resolve_turn_model as resolve_user_model
from agentcore.memory.action_inventory import (
    TurnActionInventory,
    inventory_from_journal_entries,
    merge_inventories,
)
from agentcore.memory.episode_store import EpisodeStore, default_episode_store
from agentcore.memory.episodic import (
    LLMEpisodicSummarizer,
    append_episode,
    fallback_episode_summary,
    list_undigested_episodes,
    load_scope_meta,
    mark_episodes_digested,
    purge_digested_episodes,
    should_run_semantic,
)
from agentcore.memory.locks import user_memory_lock
from agentcore.memory.maintenance import MemoryUpdateItem
from agentcore.memory.semantic import (
    LLMSemanticConsolidator,
    consolidate_semantic_memory,
)
from agentcore.memory.store import MemoryStore, default_memory_store
from agentcore.messaging.hub import default_chat_hub
from agentcore.runtime.events.types import FinishReason
from agentcore.runtime.leases import TurnLeaseRepository

logger = get_logger(__name__)

# In-process failure cooldowns (compaction posture): conversation-local dict + one
# shared-upstream sweep gate. Multi-worker skew is acceptable; no DB column.
_failure_cooldown_until: dict[str, float] = {}
_shared_failure_cooldown_until: float = 0.0
_shared_failure_streak: int = 0

# Terminal states that must not feed episodic/semantic memory (失败/中断回合跳过沉淀).
_ABNORMAL_FINISH_REASONS = frozenset(
    {
        FinishReason.CANCELLED.value,
        FinishReason.INTERRUPTED.value,
        FinishReason.ERROR.value,
        FinishReason.PAUSED.value,
    }
)
_ABNORMAL_STATUSES = frozenset(
    {
        MESSAGE_STATUS_INCOMPLETE,
        MESSAGE_STATUS_FAILED,
        MESSAGE_STATUS_RUNNING,
    }
)
# Historical interrupt body chrome (writers stopped appending these). Kept so
# older rows still skip memory consolidation when metadata is thin.
_INCOMPLETE_NOTE = (
    "（已停止，本回合未完成。下面是已完成队员的产出，已为你保留；如需继续，可重新发送消息。）"
)
_INCOMPLETE_SUFFIX = "（已停止，本回合未完成——以上为已生成部分；如需继续，可重新发送消息。）"
_INCOMPLETE_NOTE_LEGACY = (
    "（连接中断，本回合未完成。下面是已完成队员的产出，已为你保留；如需继续，可重新发送消息。）"
)
_INCOMPLETE_SUFFIX_LEGACY = (
    "（连接中断，本回合未完成——以上为已生成部分；如需继续，可重新发送消息。）"
)
_INTERRUPTED_NOTE_LEGACY = "（已中断，可重试）"


def abnormal_turn_skip_reason(
    *,
    usage: dict | None,
    content: str | None,
    has_assistant: bool,
) -> str | None:
    """Return a short reason when the latest assistant turn must not feed memory.

    Skips cancelled / incomplete / failed / still-running turns and turns with no
    substantial assistant settlement. Normal completions (``end_turn`` and other
    finished outcomes with real content) return ``None``.
    """
    if not has_assistant:
        return "no_assistant"
    meta = usage if isinstance(usage, dict) else {}
    status = meta.get("status")
    finish = meta.get("finish_reason")
    if meta.get("incomplete") is True:
        return "incomplete"
    if isinstance(status, str) and status in _ABNORMAL_STATUSES:
        return f"status:{status}"
    if isinstance(finish, str) and finish in _ABNORMAL_FINISH_REASONS:
        return f"finish_reason:{finish}"
    body = (content or "").strip()
    if not body:
        return "empty_assistant"
    if (
        body in (_INCOMPLETE_NOTE, _INCOMPLETE_NOTE_LEGACY, _INTERRUPTED_NOTE_LEGACY)
        or body.endswith(_INCOMPLETE_SUFFIX)
        or body.endswith(_INCOMPLETE_SUFFIX_LEGACY)
        or body.endswith(_INTERRUPTED_NOTE_LEGACY)
    ):
        # Incomplete salvage note alone (or only chrome after a blank stream).
        cleaned = (
            body.replace(_INCOMPLETE_SUFFIX, "")
            .replace(_INCOMPLETE_NOTE, "")
            .replace(_INCOMPLETE_SUFFIX_LEGACY, "")
            .replace(_INCOMPLETE_NOTE_LEGACY, "")
            .replace(_INTERRUPTED_NOTE_LEGACY, "")
            .strip()
        )
        if not cleaned:
            return "empty_incomplete"
        # Streamed text + incomplete suffix still counts as incomplete settlement.
        if status != MESSAGE_STATUS_COMPLETE and finish not in (
            FinishReason.END_TURN.value,
            FinishReason.MAX_ROUNDS.value,
            FinishReason.DEGRADED.value,
            FinishReason.UNPRODUCTIVE.value,
        ):
            return "incomplete_body"
    # Legacy rows with content but no usage metadata: allow (pre-feature history).
    if status is None and finish is None:
        return None
    # Explicit complete / known finished reasons with body → eligible.
    if status == MESSAGE_STATUS_COMPLETE:
        return None
    if isinstance(finish, str) and finish in {
        FinishReason.END_TURN.value,
        FinishReason.MAX_ROUNDS.value,
        FinishReason.DEGRADED.value,
        FinishReason.UNPRODUCTIVE.value,
    }:
        return None
    return "unsettled"


async def conversation_turn_open(session, conversation_id: str) -> bool:
    """True when the conversation is MID-TURN: durably paused or live-running."""
    if await PausedTurnRepository(session).exists_for_conversation(conversation_id):
        return True
    fresh_after = datetime.now(UTC) - timedelta(seconds=settings.turn_lease_ttl_seconds)
    return await TurnLeaseRepository(session).exists_fresh_for_conversation(
        conversation_id, after=fresh_after
    )


async def _latest_assistant_row(
    session, conversation_id: str
) -> tuple[dict | None, str | None, bool]:
    """Latest assistant message's ``(usage, content, found)`` for settlement gating."""
    rows = await MessageRepository(session).list_recent(conversation_id, limit=40)
    for msg in reversed(rows):
        if msg.role == "assistant":
            usage = msg.usage if isinstance(msg.usage, dict) else None
            return usage, msg.content, True
    return None, None, False


async def _publish_memory_updated(
    *,
    user_id: str,
    conversation_id: str,
    kind: str,
    update_payload: dict | None,
) -> None:
    """Best-effort firehose nudge — both layers require a visible notice (no silent writes)."""
    with contextlib.suppress(Exception):
        event: dict = {
            "type": "memory_updated",
            "conversation_id": conversation_id,
            "kind": kind,
        }
        if update_payload is not None:
            event["update"] = update_payload
        await default_chat_hub().publish([user_id], event)


async def _record_and_publish(
    *,
    conversation_id: str,
    user_id: str,
    kind: str,
    items: list[MemoryUpdateItem],
    summary: str | None = None,
    anchor_at: datetime | None = None,
) -> None:
    items_payload = [asdict(it) for it in items]
    async with async_session_factory() as session:
        row = await MemoryUpdateRepository(session).record(
            conversation_id=conversation_id,
            user_id=user_id,
            items=items_payload,
            kind=kind,
            summary=summary,
            anchor_at=anchor_at,
        )
        update_payload = {
            "id": row.id,
            "conversation_id": conversation_id,
            "created_at": row.created_at.isoformat(),
            "kind": kind,
            "summary": summary,
            "items": items_payload,
            "anchor_at": anchor_at.isoformat() if anchor_at is not None else None,
        }
    await _publish_memory_updated(
        user_id=user_id,
        conversation_id=conversation_id,
        kind=kind,
        update_payload=update_payload,
    )


async def run_semantic_for_scope(
    *,
    user_id: str,
    conversation_id: str,
    folder_id: str | None,
    store: MemoryStore,
    credentials,
    episode_store: EpisodeStore | None = None,
) -> bool:
    """Run one semantic consolidation for a (user, scope) when trigger conditions hold."""
    scope = folder_id
    ep_store = episode_store or default_episode_store()
    undigested = await list_undigested_episodes(ep_store, user_id, scope=scope)
    meta = await load_scope_meta(ep_store, user_id, scope=scope)
    oldest: datetime | None = None
    if undigested:
        try:
            oldest = datetime.fromisoformat(undigested[0].created_at.replace("Z", "+00:00"))
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=UTC)
        except ValueError:
            oldest = None
    if not should_run_semantic(
        undigested_count=len(undigested),
        last_semantic_at=meta.last_semantic_at,
        min_episodes=settings.memory_semantic_min_episodes,
        max_age_hours=settings.memory_semantic_max_age_hours,
        oldest_undigested_at=oldest,
    ):
        return False
    if credentials is None:
        logger.info(
            "memory.consolidation_skipped_no_credentials",
            conversation_id=conversation_id,
            user_id=user_id,
        )
        return False

    model = resolve_user_model(credentials)
    provider = build_provider(credentials, purpose="platform_internal")
    collected: list[MemoryUpdateItem] = []
    from agentcore.memory.always_quota import memory_write_conversation_id

    token = memory_write_conversation_id.set(conversation_id)
    try:
        outcome = await consolidate_semantic_memory(
            user_id=user_id,
            episodes=undigested,
            consolidator=LLMSemanticConsolidator(provider, model=model),
            store=store,
            today=datetime.now(UTC).date().isoformat(),
            section_cap=settings.memory_section_bullet_cap,
            max_topic_files=settings.memory_max_topic_files,
            folder_id=folder_id,
            collect_items=collected,
        )
    finally:
        memory_write_conversation_id.reset(token)
        await provider.close()

    if outcome is None:
        # Parse/timeout/exception — leave episodes undigested for a later retry.
        return False

    # Success (changed or noop): mark digested so the same summaries are not re-merged.
    await mark_episodes_digested(
        ep_store,
        user_id,
        [ep.id for ep in undigested],
        scope=scope,
        consolidated_at=datetime.now(UTC),
    )
    if outcome:
        await _record_and_publish(
            conversation_id=conversation_id,
            user_id=user_id,
            kind="semantic",
            items=collected,
            summary=None if collected else "记忆已整理",
        )
    logger.info(
        "memory.semantic_consolidated",
        user_id=user_id,
        conversation_id=conversation_id,
        episodes=len(undigested),
        changed=outcome,
    )
    return outcome


async def _load_conversation_action_inventory(
    session,
    conversation_id: str,
    *,
    max_turns: int = 40,
    after: datetime | None = None,
) -> TurnActionInventory:
    """Union tool actions from recent turn journals for this conversation.

    ``after`` (the consolidation watermark) keeps the inventory on the same turns as
    the message window, so already-summarized tool work is not re-reported as newly
    verified facts in the next episode.
    """
    repo = TurnJournalRepository(session)
    turn_ids = await repo.list_recent_turn_ids(conversation_id, limit=max_turns, after=after)
    if not turn_ids:
        return TurnActionInventory()
    # Newest-first from list_recent_turn_ids; harvest all for the window.
    parts: list[TurnActionInventory] = []
    for turn_id in turn_ids:
        entries = await repo.load(turn_id)
        parts.append(inventory_from_journal_entries(entries))
    return merge_inventories(parts)


@dataclass(frozen=True)
class _EpisodicDigest:
    """One episodic pass's text plus whether the LLM actually produced it.

    ``summarized=False`` means the summarizer timed out or came back empty and
    ``fallback_episode_summary`` stitched the window's first user turns together. That
    text is fine as raw material for the semantic pass (which reads episodes as input
    and decides for itself what is durable), but it must NEVER surface as a card: it is
    the user's own words verbatim, and a chat that opened with an ID number, a phone
    number and an address would have all three re-posted into the thread under a
    「已记下本场摘要」heading. No summary ⇒ no card.
    """

    summary: str
    summarized: bool


def _consolidation_failure_retryable(exc: BaseException) -> bool:
    """True when a later sweep could still get this window through — keep the watermark.

    Three shapes qualify. ``AgentCoreError`` with ``retryable=True`` is the failure
    saying so itself (upstream blip / timeout still inside the leaf's HTTP budget).
    A primary-pool checkout timeout is the second: it is our own saturation, the
    window was never even shown to the LLM, and it drains on its own.
    ``sqlalchemy.exc.TimeoutError`` is not an ``AgentCoreError``, so reading the
    flag alone filed it under *deterministic* and advanced the watermark past a
    window nothing had read — the only way this path loses data rather than just
    delaying it.

    The third is ``llm_failure_class == transient``. Leaf ``retryable`` is the
    in-call HTTP budget, not 「下次 sweep 还值不值得试」: a 429 whose next cooldown
    (32s) outgrew the 30s ceiling comes back ``retryable=False`` while still
    transient. Wave already reads :func:`llm_failure_class` for that split;
    consolidation must too, or the watermark advances and the window is gone.

    Everything else (AttributeError-class bugs, non-retryable terminal
    AgentCoreError) stays deterministic: advancing the watermark stops the sweeper
    re-burning LLM on them.
    """
    from agentcore.core.errors import (
        LLM_FAILURE_TRANSIENT,
        AgentCoreError,
        llm_failure_class,
    )

    if is_pool_timeout_error(exc):
        return True
    if llm_failure_class(exc) == LLM_FAILURE_TRANSIENT:
        return True
    return isinstance(exc, AgentCoreError) and bool(exc.retryable)


# Side-path failure buckets where retrying a *different* conversation against the
# same upstream is futile — the whole sweep must back off, not just this id.
# Conversation-local buckets (timeout, 4xx upstream, invalid_response, auth) stay
# on the per-conversation cooldown. Membership is decided by
# ``classify_background_llm_failure``, the single source for these buckets.
_SHARED_UPSTREAM_REASONS = frozenset(
    {"rate_limit", "quota_skip", "provider_unavailable", "upstream_unstable"}
)

# The same split for refusals the gate *returns* (:class:`BackgroundLlmSkip`), which
# arrive as their own vocabulary rather than a classifier bucket. Only
# ``QUOTA_EXCEEDED`` may stop the whole sweep: it is the mid-flight refusal — admission
# had already cleared the allowance, then the per-call gate or upstream itself turned
# the call down, which is the platform key everyone shares saying no (the
# ``upstream_rate_limit_error`` 429 wears exactly this face). The other three are
# decided from this account's own config before upstream is ever touched, and the
# sweep gate is process-global with no allowance epoch behind it: arming it on a
# keyless account — a permanent state that re-arms every sweep and never reaches the
# LLM success that retires it — would starve every *other* user's memory indefinitely.
_SHARED_SKIP_REASONS = frozenset({BackgroundSkipReason.QUOTA_EXCEEDED})

# An upstream-dated refusal is worth obeying; it is not worth obeying literally.
# Platform quota dates cluster at the day reset (median ~12.7h) while this gate is
# process-wide and — unlike compaction's per-conversation one — remembers no allowance
# epoch, so taking one at its word would freeze every account's memory for half a day
# over one account's wall, and keep freezing it after the key swap or quota bump that
# ended the wall early. Past an hour, re-asking costs one call per sweep, which is a
# rounding error against the wall it is probing.
_DECLARED_COOLDOWN_CAP_SECONDS = 3600.0


def _capped_declared_recovery(declared_recovery_in: float | None) -> float | None:
    """Upstream's own「多久后重试才值得」, clamped to :data:`_DECLARED_COOLDOWN_CAP_SECONDS`."""
    if declared_recovery_in is None or declared_recovery_in <= 0:
        return None
    return min(float(declared_recovery_in), _DECLARED_COOLDOWN_CAP_SECONDS)


def _mark_conversation_failure_cooldown(
    conversation_id: str, *, reason: str, declared_recovery_in: float | None = None
) -> None:
    """Arm in-process per-conversation cooldown (no-op if disabled and undated).

    A ``declared_recovery_in`` wins over the configured guess whenever it is longer,
    and holds even when the guess is switched off — sitting out a wall upstream dated
    is not a guess.
    """
    secs = float(settings.memory_consolidation_failure_cooldown_seconds)
    dated = _capped_declared_recovery(declared_recovery_in)
    if dated is not None:
        secs = max(secs, dated)
    if secs <= 0:
        return
    until = time.monotonic() + secs
    _failure_cooldown_until[conversation_id] = until
    logger.warning(
        "memory.consolidation_backoff",
        scope="conversation",
        reason=reason,
        cooldown_seconds=secs,
        declared_recovery_sec=declared_recovery_in,
        resume_at_monotonic=until,
        conversation_id=conversation_id,
        streak=0,
    )


def _clear_conversation_failure_cooldown(conversation_id: str) -> None:
    _failure_cooldown_until.pop(conversation_id, None)


def _in_conversation_failure_cooldown(conversation_id: str) -> bool:
    """True while a prior conversation cooldown is still active; expires lazily."""
    until = _failure_cooldown_until.get(conversation_id)
    if until is None:
        return False
    if time.monotonic() >= until:
        _failure_cooldown_until.pop(conversation_id, None)
        return False
    return True


def _mark_shared_failure_cooldown(
    *, reason: str, declared_recovery_in: float | None = None
) -> None:
    """Arm exponential shared-upstream cooldown (capped); abort further consolidations.

    ``declared_recovery_in`` wins over the exponential guess whenever it is longer.
    The ladder needs four refused rounds to climb from 5 minutes to 30, and each rung
    is a sweep spent re-asking a question upstream has already answered; a dated
    refusal lets the gate open at the ceiling instead of burning its way up. It also
    holds when the ladder is configured off, and is clamped by
    :data:`_DECLARED_COOLDOWN_CAP_SECONDS`.
    """
    global _shared_failure_cooldown_until, _shared_failure_streak
    base = settings.memory_consolidation_shared_failure_cooldown_base_seconds
    max_s = settings.memory_consolidation_shared_failure_cooldown_max_seconds
    secs = 0.0
    if base > 0 and max_s > 0:
        secs = float(min(base * (2**_shared_failure_streak), max_s))
    dated = _capped_declared_recovery(declared_recovery_in)
    if dated is not None:
        secs = max(secs, dated)
    if secs <= 0:
        return
    _shared_failure_streak += 1
    until = time.monotonic() + secs
    _shared_failure_cooldown_until = until
    logger.warning(
        "memory.consolidation_backoff",
        scope="sweep",
        reason=reason,
        cooldown_seconds=secs,
        declared_recovery_sec=declared_recovery_in,
        resume_at_monotonic=until,
        conversation_id="",
        streak=_shared_failure_streak,
    )


def _mark_skip_cooldown(conversation_id: str, *, skip: BackgroundLlmSkip) -> None:
    """Back off after a refusal the gate *returned* — it never reaches ``except``.

    Routes by :data:`_SHARED_SKIP_REASONS` and hands the refusal's own recovery date
    to whichever layer takes it, so the wall upstream already named is not re-derived
    from a streak.
    """
    reason = str(skip.reason)
    if skip.reason in _SHARED_SKIP_REASONS:
        _mark_shared_failure_cooldown(
            reason=reason, declared_recovery_in=skip.declared_recovery_in
        )
        return
    _mark_conversation_failure_cooldown(
        conversation_id, reason=reason, declared_recovery_in=skip.declared_recovery_in
    )


def _clear_shared_failure_cooldown() -> None:
    """Reset shared gate after a successful consolidation (recovery path)."""
    global _shared_failure_cooldown_until, _shared_failure_streak
    _shared_failure_cooldown_until = 0.0
    _shared_failure_streak = 0


def _in_shared_failure_cooldown() -> bool:
    """True while shared-upstream cooldown is active; expires lazily (recovery)."""
    global _shared_failure_cooldown_until
    if _shared_failure_cooldown_until <= 0:
        return False
    if time.monotonic() >= _shared_failure_cooldown_until:
        _shared_failure_cooldown_until = 0.0
        return False
    return True


def _reset_failure_cooldowns_for_tests() -> None:
    """Clear in-process cooldown state between unit tests."""
    global _shared_failure_cooldown_until, _shared_failure_streak
    _failure_cooldown_until.clear()
    _shared_failure_cooldown_until = 0.0
    _shared_failure_streak = 0


async def consolidate_conversation(
    conversation_id: str, *, store: MemoryStore | None = None
) -> bool:
    """Write one episodic session summary for a settled conversation; maybe semantic.

    Returns True when an episodic summary was written (semantic may or may not follow).
    Never raises.
    """
    if _in_shared_failure_cooldown() or _in_conversation_failure_cooldown(conversation_id):
        return False
    store = store or default_memory_store()
    ep_store = default_episode_store()
    # Captured so the failure path can advance the sweeper watermark without re-query.
    latest: datetime | None = None
    try:
        async with user_memory_lock_for(conversation_id) as user_id:
            if user_id is None:
                return False
            async with async_session_factory() as session:
                latest = await MessageRepository(session).latest_created_at(conversation_id)
                conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
                if conv is None or latest is None:
                    return False
                if await conversation_turn_open(session, conversation_id):
                    logger.info(
                        "memory.consolidation_deferred_open_turn",
                        conversation_id=conversation_id,
                    )
                    return False
                usage, assistant_content, has_assistant = await _latest_assistant_row(
                    session, conversation_id
                )
                skip_reason = abnormal_turn_skip_reason(
                    usage=usage,
                    content=assistant_content,
                    has_assistant=has_assistant,
                )
                if skip_reason is not None:
                    # Abnormal terminal turn: skip episodic entirely and advance the
                    # watermark so the sweeper does not retry until new messages arrive.
                    await ConversationRepository(session).set_memory_synced_at(
                        conversation_id, latest
                    )
                    logger.debug(
                        "memory.consolidation_skipped_abnormal_turn",
                        conversation_id=conversation_id,
                        reason=skip_reason,
                        finish_reason=(
                            usage.get("finish_reason") if isinstance(usage, dict) else None
                        ),
                        status=usage.get("status") if isinstance(usage, dict) else None,
                    )
                    return False
                synced = conv.memory_synced_at
                if synced is not None and latest <= synced:
                    return False
                folder_id = conv.folder_id
                # Only what arrived since the last pass. Re-reading a fixed recent tail
                # made adjacent episodes overlap — in the worst observed case the second
                # card restated the whole first one, because both summarized the same
                # messages. The 40-message cap still bounds a long unconsolidated gap.
                window = await load_recent_history(
                    session,
                    conversation_id,
                    max_messages=settings.memory_consolidation_window_messages,
                    after=synced,
                )
                actions = await _load_conversation_action_inventory(
                    session,
                    conversation_id,
                    max_turns=settings.memory_consolidation_window_messages,
                    after=synced,
                )

            credentials: LLMCredentials | None = None
            wrote_episodic = False
            if window:

                async def _episodic_runner(creds: LLMCredentials) -> _EpisodicDigest:
                    model = resolve_user_model(creds)
                    provider = build_provider(creds, purpose="platform_internal")
                    try:
                        summarizer = LLMEpisodicSummarizer(provider, model=model)
                        summary = await summarizer.summarize(
                            window,
                            max_chars=settings.memory_episodic_summary_max_chars,
                            actions=actions,
                        )
                        if summary.strip():
                            return _EpisodicDigest(summary=summary, summarized=True)
                        return _EpisodicDigest(
                            summary=fallback_episode_summary(
                                window, max_chars=settings.memory_episodic_summary_max_chars
                            ),
                            summarized=False,
                        )
                    finally:
                        await provider.close()

                bg = await run_background_llm(user_id, purpose="memory", runner=_episodic_runner)
                if isinstance(bg, BackgroundLlmSkip):
                    # Refused (no key / allowance spent / auth) rather than raised, so
                    # this never reaches the ``except`` arm that arms the backoff — and
                    # the watermark cannot stand in for one. Leaving it put is right:
                    # it is what keeps the window from being lost. But that is also
                    # precisely what puts this conversation back in the pending set
                    # every 300s, each time for another call upstream has already
                    # refused. Only a cooldown stops that.
                    _mark_skip_cooldown(conversation_id, skip=bg)
                    return False
                credentials = bg.credentials
                digest = bg.value
                episode = await append_episode(
                    ep_store,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    summary=digest.summary,
                    scope=folder_id,
                    max_chars=settings.memory_episodic_summary_max_chars,
                    actions=actions,
                )
                wrote_episodic = True
                if digest.summarized:
                    await _record_and_publish(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        kind="episodic",
                        items=[],
                        summary=episode.summary,
                        anchor_at=latest,
                    )
                else:
                    logger.warning(
                        "memory.episodic_card_suppressed",
                        conversation_id=conversation_id,
                        user_id=user_id,
                        episode_id=episode.id,
                        reason="no_llm_summary",
                    )

            async with async_session_factory() as session:
                await ConversationRepository(session).set_memory_synced_at(conversation_id, latest)

            if wrote_episodic:
                with contextlib.suppress(Exception):
                    await run_semantic_for_scope(
                        user_id=user_id,
                        conversation_id=conversation_id,
                        folder_id=folder_id,
                        store=store,
                        credentials=credentials,
                        episode_store=ep_store,
                    )

            _clear_conversation_failure_cooldown(conversation_id)
            # Only an actual LLM success proves shared upstream is healthy again.
            # Empty-window watermark advances must not lift a rate-limit sweep gate.
            if credentials is not None:
                _clear_shared_failure_cooldown()
            logger.info(
                "memory.consolidated",
                conversation_id=conversation_id,
                user_id=user_id,
                changed=wrote_episodic,
                layer="episodic",
            )
            return wrote_episodic
    except Exception as e:
        from agentcore.llm.background_failure import classify_background_llm_failure

        reason = classify_background_llm_failure(e)
        # Transient upstream (5xx / rate limit / timeout): leave the watermark so the
        # next sweep retries. Deterministic failure: advance it, same posture as the
        # abnormal-turn skip above — otherwise the 300s sweeper re-selects this
        # conversation and re-burns an LLM call on it forever.
        dropped_through: datetime | None = None
        if latest is not None and not _consolidation_failure_retryable(e):
            try:
                async with async_session_factory() as session:
                    await ConversationRepository(session).set_memory_synced_at(
                        conversation_id, latest
                    )
                dropped_through = latest
            except Exception:
                # Watermark unchanged → the sweeper will retry; report it as a plain
                # failure below rather than claiming a window was dropped.
                pass
        if dropped_through is not None:
            logger.warning(
                "memory.consolidation_window_dropped",
                conversation_id=conversation_id,
                error=str(e),
                error_type=type(e).__name__,
                reason=reason,
                window_through=dropped_through.isoformat(),
            )
        else:
            logger.warning(
                "memory.consolidation_failed",
                conversation_id=conversation_id,
                error=str(e),
                # One error text lands in different reason buckets when a layer
                # re-wraps the exception; the class name tells those apart.
                error_type=type(e).__name__,
                reason=reason,
            )
            # Layered backoff (retryable path only — watermark already handles
            # deterministic drops). Shared upstream → whole-sweep gate; else
            # conversation-local cooldown so the sweeper skips this id briefly.
            if _consolidation_failure_retryable(e):
                if reason in _SHARED_UPSTREAM_REASONS:
                    _mark_shared_failure_cooldown(reason=reason)
                else:
                    _mark_conversation_failure_cooldown(conversation_id, reason=reason)
        return False


class _UserLockForConversation:
    """Resolve a conversation's owner, then hold that user's memory lock."""

    def __init__(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id
        self._cm = None

    async def __aenter__(self) -> str | None:
        async with async_session_factory() as session:
            conv = await ConversationRepository(session).get_by_id_unscoped(self._conversation_id)
        if conv is None:
            return None
        self._cm = user_memory_lock(conv.user_id)
        await self._cm.__aenter__()
        return conv.user_id

    async def __aexit__(self, *exc) -> None:
        if self._cm is not None:
            await self._cm.__aexit__(*exc)


def user_memory_lock_for(conversation_id: str) -> _UserLockForConversation:
    """Async-context wrapper yielding the owner user_id while holding their lock."""
    return _UserLockForConversation(conversation_id)


# --- Debounce scheduler (live path → episodic) --------------------------------

Runner = Callable[[str], Awaitable[object]]


class MemoryConsolidationScheduler:
    """Per-conversation debounce + turn-cap trigger for episodic summary writes."""

    def __init__(self, *, idle_seconds: float, turn_cap: int, runner: Runner) -> None:
        self._idle = idle_seconds
        self._turn_cap = turn_cap
        self._runner = runner
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._counts: dict[str, int] = {}
        self._tasks: set[asyncio.Task] = set()

    def schedule(self, conversation_id: str) -> None:
        """Register a finished turn: arm/reset the debounce, or fire at the cap."""
        self._counts[conversation_id] = self._counts.get(conversation_id, 0) + 1
        if self._turn_cap and self._counts[conversation_id] >= self._turn_cap:
            self._fire(conversation_id)
            return
        self._cancel_timer(conversation_id)
        loop = asyncio.get_running_loop()
        self._timers[conversation_id] = loop.call_later(self._idle, self._fire, conversation_id)

    def _fire(self, conversation_id: str) -> None:
        self._cancel_timer(conversation_id)
        self._counts.pop(conversation_id, None)
        task = asyncio.ensure_future(self._run(conversation_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, conversation_id: str) -> None:
        try:
            with log_context(conversation_id=conversation_id):
                await self._runner(conversation_id)
        except Exception as e:
            logger.warning(
                "memory.consolidation_run_failed",
                conversation_id=conversation_id,
                error=str(e),
            )

    def _cancel_timer(self, conversation_id: str) -> None:
        timer = self._timers.pop(conversation_id, None)
        if timer is not None:
            timer.cancel()

    async def shutdown(self) -> None:
        """Cancel pending timers and await in-flight passes (clean lifespan exit)."""
        for timer in list(self._timers.values()):
            timer.cancel()
        self._timers.clear()
        self._counts.clear()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


_default_scheduler: MemoryConsolidationScheduler | None = None


def get_scheduler() -> MemoryConsolidationScheduler:
    """Process-wide scheduler bound to the real runner (lazy, settings-configured)."""
    global _default_scheduler
    if _default_scheduler is None:
        _default_scheduler = MemoryConsolidationScheduler(
            idle_seconds=settings.memory_consolidation_idle_seconds,
            turn_cap=settings.memory_consolidation_turn_cap,
            runner=consolidate_conversation,
        )
    return _default_scheduler


def schedule_consolidation(conversation_id: str) -> None:
    """Arm the debounce for a finished turn (no-op when the feature is disabled)."""
    if not settings.memory_consolidation_enabled:
        return
    get_scheduler().schedule(conversation_id)


async def shutdown_scheduler() -> None:
    """Flush the process-wide scheduler on app shutdown (no-op if never built)."""
    if _default_scheduler is not None:
        await _default_scheduler.shutdown()


# --- Periodic sweeper (backstop) ---------------------------------------------


async def consolidation_sweep_once() -> int:
    """One backstop sweep: episodic-write settled chats with un-synced messages.

    Shared-upstream cooldown aborts the rest of the batch (and skips the sweep when
    already armed). Per-conversation cooldown skips that id without stopping others.
    Also purges digested episodes past the retention window (no separate GC loop).
    """
    with contextlib.suppress(Exception):
        purged = await purge_digested_episodes(
            older_than_days=settings.memory_episode_retention_days
        )
        if purged:
            logger.info("memory.episodes_purged", count=purged)
    if _in_shared_failure_cooldown():
        return 0
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.memory_consolidation_idle_seconds)
    async with async_session_factory() as session:
        pending = await ConversationRepository(session).list_pending_memory_consolidation(
            idle_before=cutoff,
            limit=settings.memory_consolidation_sweep_batch_limit,
        )
    attempted = 0
    for conversation_id in pending:
        if _in_shared_failure_cooldown():
            break
        if _in_conversation_failure_cooldown(conversation_id):
            continue
        with log_context(conversation_id=conversation_id):
            await consolidate_conversation(conversation_id)
            attempted += 1
    return attempted


async def consolidation_loop() -> None:
    """Forever: sleep, then run one backstop sweep. Cancelled cleanly on shutdown."""
    interval = settings.memory_consolidation_sweep_interval_seconds
    while True:
        try:
            await asyncio.sleep(interval)
            count = await consolidation_sweep_once()
            if count:
                logger.info("memory.consolidation_swept", count=count)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log = logger.error if is_schema_error(e) else logger.warning
            log("memory.consolidation_sweep_failed", error=str(e))
