"""Long-conversation compaction (执行引擎架构设计 §三 长对话压缩).

A long chat must not feed its WHOLE transcript to the LLM every turn: even under
DeepSeek's 1M window (which never overflows) that invites context rot, and a lapsed
prefix cache re-bills the full history. So turns OLDER than a recency window are
folded into a single rolling, structured summary (已确立事实 / 决策 / 未决问题 /
文件路径), and a turn loads ``[summary] + recent turns`` instead.

Design (mirrors the offline memory consolidation pattern):

- **Trigger (dual, post-turn)** — after each turn finalize (cloud + local),
  ``schedule_compaction_if_due`` arms a background pass when either (a)
  ``input_tokens ≥ compaction_trigger_input_tokens`` or (b) the DB watermark-after
  batch yields a non-empty ``_select_fold`` with ``compaction_message_trigger_min_fold``.
  Never use turn ``history_len`` (summary blocks inflate it). Self-throttle on success:
  the fold shrinks the foldable tail; next due needs another 16 foldable msgs or 32k
  tokens. Failure leaves the watermark untouched and arms a short in-process cooldown
  (``compaction_failure_cooldown_seconds``, or the failure's own ``retry_after`` when
  it is longer) so neither trigger re-schedules until it expires (``_inflight_tasks``
  still dedupes in-flight). Recency retain is ``compaction_recency_token_budget``
  packed from the newest user-led turn (count 12 is not the retain rule).
- **Dull-line (pre-turn)** — last-hop **single-request** ``prompt_tokens ≥ 32_000``
  and the token budget still leaves a fold that meets the internal empty-run gate
  → ``compact_before_turn`` awaits **one** pass. Failure does not refuse the send
  and does not push the watermark. Does not wait when nothing is foldable.
- **Near-ceiling (pre-turn, 定案⑦A)** — when last-turn **single-request**
  ``prompt_tokens`` are near **this turn's** model window (``compaction_near_context_ratio``
  of the effective window (assembly context budget, else catalog ``context_length``;
  absolute ``compaction_near_context_tokens`` when that window is unknown),
  ``compact_before_turn`` **awaits** fold pass(es) before history assemble.
  A successful fold proceeds even if the stored watermark still looks near. If the
  watermark is near **and** the fold did not write, the send is refused (product
  overflow copy) so the turn never spends an upstream 413. Does not wait for the
  user to type ``/compact``. This is the one path with a human blocked on it, so
  its passes run ``user_waiting``: a 429 fails on the spot rather than being slept
  off (``llm.provider.call_budget``). Bypasses the *guessed* failure cooldown (a
  retry might work, and a context at the ceiling is urgent) but not an upstream-declared
  one (``_in_declared_cooldown``): a 429 that names the moment its allowance returns
  has already answered「重试会不会成功」with no. Honesty: rolling summary may drop
  process detail — hard identifiers are kept best-effort; near-ceiling cannot shrink
  an already-mined recency window of huge verbatim turns.
- **Cooldowns expire on their own terms** — a declared one is capped at
  ``DECLARED_COOLDOWN_CAP_SECONDS`` (an upstream day reset must not freeze folding
  for half a day while the chat keeps growing) and is void as soon as the account's
  key or quota changes (``billing.allowance``), because the refusal it caches was
  about that account's allowance and not about this conversation. The LLM leaf's
  process 429 gate is a separate layer and is **per-scenario**: a title day-reset
  does not occupy compaction's slot.
- **Watermark** — ``compacted_through`` (the created_at of the last folded message)
  makes a re-fire idempotent and lets a long backlog fold INCREMENTALLY, oldest-first,
  across several passes until it catches up.
- **Cache** — the summary is computed ONCE and persisted, then reused verbatim across
  turns. Recomputing it every turn would rewrite the prompt prefix and bust DeepSeek's
  exact-prefix cache (runtime/resolve/prompt.py) — the one thing this must never do.

Robust by construction: credentials follow this conversation's chat payer via
``run_compaction_llm`` (explicit background-slot BYOK still wins; platform chat
still quota-gated + one BYOK retry on platform auth reject), the pass is gated
so a trivial fold never spends an LLM call, and ANY failure (LLM down, timeout,
empty output, quota skip) leaves the stored state untouched and returns without
raising — post-turn compaction is best-effort enrichment. Near-ceiling that
cannot write **refuses the send** instead of hitting upstream 413.

Best-effort is not the same as unsaid. Once a chat outgrows the loader's fallback
window, a fold that keeps failing stops being an unspent optimisation and starts
deleting the model's early memory of the conversation, in silence, while the
transcript on screen still shows every turn. ``conversation/context_gap`` decides
when that has actually happened and :func:`declared_recovery_at` hands it the
date upstream gave, so the composer can say so.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence

from agentcore.billing.allowance import allowance_epoch
from agentcore.billing.gate import BackgroundLlmSkip, run_compaction_llm
from agentcore.config import settings
from agentcore.conversation.compact_prompt import (
    _COMPACT_SYSTEM_PROMPT,  # noqa: F401 — tests import this name from compaction
    IDENTITY_LEDGER_FENCE,  # noqa: F401 — tests import this name from compaction
    attach_identity_ledger,
    compact_system_prompt,
    estimate_message_tokens,  # noqa: F401 — tests
    merge_identity_items,
    parse_identity_ledger,
    recency_keep_index,
    render_conversation_fold,
    render_identity_ledger,
    rows_for_summarizer,
    strip_identity_ledger,
)
from agentcore.core.errors import recovery_at_iso
from agentcore.core.logging import get_logger
from agentcore.core.text import truncate_head_tail
from agentcore.db.base import async_session_factory
from agentcore.db.models import Message
from agentcore.db.repositories import (
    ConversationRepository,
    MessageRepository,
    TurnMetricsRepository,
)
from agentcore.llm import LLMMessage
from agentcore.llm.background_failure import (
    declared_recovery_seconds as _declared_recovery_seconds,
)
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.factory import build_provider
from agentcore.llm.model_selection import build_selected_request, select_call
from agentcore.llm.provider.call_budget import complete_within_budget
from agentcore.llm.resolve import resolve_turn_model as resolve_user_model

logger = get_logger(__name__)


# Folding reads a window and writes structured prose — heavier than a title, so a
# longer ceiling than the memory extract. On timeout we yield nothing; the state is
# left intact and the next due turn retries (after failure cooldown).
# On the post-turn path this doubles as the 429 patience the provider retries
# against (``complete_within_budget``): a cooldown that fits in here is worth sitting
# out, and losing the fold to a timeout afterwards costs no more than abandoning it
# up front. The pre-turn path keeps the same deadline but spends none of it asleep —
# nobody is watching the first case, and a whole turn is waiting on the second.
_COMPACT_TIMEOUT_SECONDS = 45.0


_render_fold = render_conversation_fold


def _select_fold(
    batch: Sequence[Message],
    *,
    token_budget: int | None = None,
    min_fold: int,
    recency: int | None = None,
    journals: dict | None = None,
) -> list[Message]:
    """Oldest messages to fold this pass: everything before the token-budget keep window.

    Packs newest user-led turns up to ``token_budget`` (default
    ``compaction_recency_token_budget``), always keeping the latest user-led turn.
    ``recency`` is ignored — message count is not the retain rule. Returns ``[]``
    unless at least ``min_fold`` messages sit outside that window. ``batch`` is the
    un-folded tail, oldest-first; the last folded created_at becomes the watermark.

    The keep cut is already a user-led start, so the verbatim tail (when non-empty)
    starts on ``user`` and the loader can prefix an assistant-role summary without
    consecutive assistant roles.
    """
    del recency  # leftover kwarg so older call sites / tests can still pass it
    budget = (
        settings.compaction_recency_token_budget if token_budget is None else token_budget
    )
    keep_from = recency_keep_index(batch, token_budget=budget, journals=journals)
    fold = list(batch[:keep_from])
    while fold and getattr(fold[-1], "role", None) == "user":
        fold.pop()
    if len(fold) < min_fold:
        return []
    return fold


def compaction_message_due(
    batch: Sequence[Message],
    *,
    token_budget: int | None = None,
    min_fold: int | None = None,
    recency: int | None = None,
) -> bool:
    """Pure message-side due check: isomorphic to ``_select_fold`` non-empty.

    Uses ``compaction_message_trigger_min_fold`` by default (schedule gate), not the
    internal ``compaction_min_fold_messages`` (empty-run LLM guard inside compact).
    """
    del recency
    return bool(
        _select_fold(
            batch,
            token_budget=token_budget,
            min_fold=(
                settings.compaction_message_trigger_min_fold if min_fold is None else min_fold
            ),
        )
    )


# Compaction's own elision marker (domain voice); the head+tail mechanism is shared.
_COMPACT_ELISION_MARKER = "\n\n……（摘要过长，已保留首尾）……\n\n"


def _truncate_head_tail(content: str, limit: int) -> str:
    """Safety net if the model overruns the budget: keep BOTH ends (the trailing
    『涉及的文件与标识符』section carries the verbatim identifiers we most want to
    survive). Thin binding of ``core.text.truncate_head_tail`` with the compaction
    marker."""
    return truncate_head_tail(content, limit, marker=_COMPACT_ELISION_MARKER)


async def _summarize(
    provider,
    old_summary: str,
    messages: Sequence[Message],
    *,
    model: str,
    conversation_id: str,
    user_waiting: bool = False,
    file_ledger: str = "",
    journals: dict | None = None,
) -> str:
    """One flash, non-thinking call → the updated rolling summary ("" on failure).

    ``user_waiting`` is the pre-turn near-ceiling path: same 45s deadline, but the
    call may not spend any of it asleep on a ``Retry-After`` — a blocked turn would
    pay that wait twice (once staring at nothing, once on the retry that no longer
    fits). See ``llm.provider.call_budget``.
    """
    from agentcore.observability.session_llm_header import hydrate_session_header
    from agentcore.runtime.resolve.prompt.envelope import TURN_ENVELOPE_FENCE

    header = await hydrate_session_header(conversation_id)
    if header is not None and header.tools:
        from agentcore.runtime.resolve.prompt.envelope import history_row_to_llm_message

        history_msgs = [
            history_row_to_llm_message(row)
            for row in rows_for_summarizer(messages, journals)
        ]
        prior = strip_identity_ledger(old_summary).strip() or "（无，这是本对话的首次压缩）"
        extras: list[str] = []
        ledger = file_ledger.strip()
        if ledger:
            extras.append(f"# 本批涉及的文件\n{ledger}")
        extra = ("\n\n" + "\n\n".join(extras)) if extras else ""
        tail = (
            f"{TURN_ENVELOPE_FENCE}\n{compact_system_prompt()}\n\n"
            f"# 已有滚动摘要\n{prior}{extra}\n\n"
            "较早对话已在上面。只输出更新后的滚动摘要正文。"
        )
        req_messages = [
            LLMMessage(role="system", content=header.system),
            *history_msgs,
            LLMMessage(role="user", content=tail),
        ]
        request = build_selected_request(
            select_call("compaction", header.model or model),
            req_messages,
            tools=list(header.tools),
            tool_choice="none",
            stream=False,
        )
    else:
        request = build_selected_request(
            select_call("compaction", model),
            [
                LLMMessage(role="system", content=compact_system_prompt()),
                LLMMessage(
                    role="user",
                    content=_render_fold(
                        old_summary,
                        messages,
                        file_ledger=file_ledger,
                        journals=journals,
                    ),
                ),
            ],
            stream=False,
        )
    try:
        response = await complete_within_budget(
            provider,
            request,
            budget=_COMPACT_TIMEOUT_SECONDS,
            user_waiting=user_waiting,
        )
    except TimeoutError:
        logger.warning("compaction.timeout", conversation_id=conversation_id)
        return ""
    return _truncate_head_tail(
        (response.content or "").strip(), settings.compaction_summary_char_budget
    )


async def _load_unfolded_batch(conversation_id: str) -> list[Message]:
    """Watermark-after (or full) message batch for due / fold — oldest-first, capped."""
    async with async_session_factory() as session:
        conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
        if conv is None:
            return []
        scan = settings.compaction_context_max_messages
        msg_repo = MessageRepository(session)
        if conv.compacted_through is None:
            return list(await msg_repo.list_recent(conversation_id, limit=scan))
        return list(
            await msg_repo.list_recent_after(
                conversation_id, after=conv.compacted_through, limit=scan
            )
        )


def _assistant_turn_ids(messages: Sequence[Message]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for msg in messages:
        if getattr(msg, "role", None) != "assistant":
            continue
        mid = getattr(msg, "id", None)
        if not mid:
            continue
        key = str(mid)
        if key in seen:
            continue
        seen.add(key)
        ids.append(key)
    return ids


async def _load_turn_journals(turn_ids: Sequence[str]) -> dict:
    """Journal facts for a fold plan. Missing turns are absent."""
    ids = list(dict.fromkeys(turn_ids))
    if not ids:
        return {}
    from agentcore.db.repositories import TurnJournalRepository

    async with async_session_factory() as session:
        return await TurnJournalRepository(session).load_map(ids)


def _fold_before_keep(
    newest: Sequence[Message],
    oldest: Sequence[Message],
    *,
    token_budget: int,
    min_fold: int,
    journals: dict | None = None,
) -> list[Message]:
    """Oldest rows strictly before the newest-side keep window, if ≥ ``min_fold``.

    ``newest`` is a recent-biased scan (true near end). ``oldest`` is an oldest-first
    cap after the watermark (incremental catch-up). When they are the same full tail,
    this matches ``_select_fold``.
    """
    if not newest:
        return []
    keep_from = recency_keep_index(
        newest, token_budget=token_budget, journals=journals
    )
    if keep_from >= len(newest):
        return []
    cutoff = newest[keep_from].created_at
    fold = [
        m
        for m in oldest
        if getattr(m, "created_at", None) is not None and m.created_at < cutoff
    ]
    while fold and getattr(fold[-1], "role", None) == "user":
        fold.pop()
    if len(fold) < min_fold:
        return []
    return fold


async def _load_fold_windows(conversation_id: str) -> tuple[list[Message], list[Message], str]:
    """Newest keep-scan, oldest fold-cap, and stored summary prose+ledger."""
    scan = settings.compaction_context_max_messages
    max_fold = settings.compaction_max_fold_messages
    async with async_session_factory() as session:
        conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
        if conv is None:
            return [], [], ""
        msg_repo = MessageRepository(session)
        after = conv.compacted_through
        if after is None:
            newest = list(await msg_repo.list_recent(conversation_id, limit=scan))
            oldest, _total = await msg_repo.list_by_conversation(
                conversation_id, limit=max_fold
            )
            oldest = list(oldest)
        else:
            newest = list(
                await msg_repo.list_recent_after(conversation_id, after=after, limit=scan)
            )
            oldest, _more = await msg_repo.list_after(
                conversation_id, after=after, limit=max_fold
            )
            oldest = list(oldest)
        return newest, oldest, conv.compaction_summary or ""


async def _plan_fold(
    conversation_id: str,
    *,
    min_fold: int,
    token_budget: int | None = None,
) -> list[Message]:
    """Fold candidates for due / compact / dull-line (empty = nothing to fold)."""
    newest, oldest, _summary = await _load_fold_windows(conversation_id)
    journals = await _load_turn_journals(_assistant_turn_ids(newest))
    budget = (
        settings.compaction_recency_token_budget if token_budget is None else token_budget
    )
    return _fold_before_keep(
        newest, oldest, token_budget=budget, min_fold=min_fold, journals=journals
    )


async def _is_message_due(conversation_id: str) -> bool:
    """DB message trigger: fold plan non-empty with message_trigger_min_fold."""
    fold = await _plan_fold(
        conversation_id, min_fold=settings.compaction_message_trigger_min_fold
    )
    return bool(fold)


async def _has_foldable_beyond_recency(conversation_id: str) -> bool:
    """Internal empty-run gate: token-budget foldable ≥ ``compaction_min_fold_messages``."""
    fold = await _plan_fold(
        conversation_id, min_fold=settings.compaction_min_fold_messages
    )
    return bool(fold)


async def compact_conversation(
    conversation_id: str,
    *,
    trigger_input_tokens: int | None = None,
    user_waiting: bool = False,
) -> bool:
    """Fold this conversation's older turns into its rolling summary. Never raises.

    Watermark-gated and self-limiting: packs a newest-side keep window by
    ``compaction_recency_token_budget``, then folds the oldest un-folded prefix
    outside that window — but only when there is enough old material to be
    worth an LLM call (``compaction_min_fold_messages``); otherwise it no-ops without
    spending a call. Returns whether a new summary was written.

    ``user_waiting`` marks the pass a turn is blocked on (near-ceiling, pre-turn):
    it may not sit out an upstream cooldown, only fail fast and let the turn start.
    """
    if not settings.compaction_enabled:
        return False
    # Whose allowance this pass runs on — resolved with the conversation, and needed
    # again on the failure path so a cooldown can be retired when that account's key
    # or quota changes. Stays None when the failure happened before the DB read.
    user_id: str | None = None
    try:
        async with async_session_factory() as session:
            conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
            if conv is None:
                return False
            user_id = conv.user_id
            old_summary = conv.compaction_summary or ""
            scan = settings.compaction_context_max_messages
            max_fold = settings.compaction_max_fold_messages
            msg_repo = MessageRepository(session)
            after = conv.compacted_through
            if after is None:
                newest = list(await msg_repo.list_recent(conversation_id, limit=scan))
                oldest, _total = await msg_repo.list_by_conversation(
                    conversation_id, limit=max_fold
                )
                oldest = list(oldest)
            else:
                newest = list(
                    await msg_repo.list_recent_after(
                        conversation_id, after=after, limit=scan
                    )
                )
                oldest, _more = await msg_repo.list_after(
                    conversation_id, after=after, limit=max_fold
                )
                oldest = list(oldest)

            from agentcore.db.repositories import TurnJournalRepository

            journals = await TurnJournalRepository(session).load_map(
                _assistant_turn_ids([*newest, *oldest])
            )
            fold_msgs = _fold_before_keep(
                newest,
                oldest,
                token_budget=settings.compaction_recency_token_budget,
                min_fold=settings.compaction_min_fold_messages,
                journals=journals,
            )
            if not fold_msgs:
                return False
            new_watermark = fold_msgs[-1].created_at
            fold_turn_ids = [
                m.id
                for m in fold_msgs
                if getattr(m, "role", None) == "assistant" and getattr(m, "id", None)
            ]

        from agentcore.runtime.context.working_set import (
            build_fold_file_ledger,
            identity_items_from_traces,
            load_fold_tool_traces,
        )

        file_ledger = await build_fold_file_ledger(fold_turn_ids)
        trace_rows = await load_fold_tool_traces(fold_turn_ids)
        prior_ledger = ""
        if IDENTITY_LEDGER_FENCE in (old_summary or ""):
            prior_ledger = old_summary.split(IDENTITY_LEDGER_FENCE, 1)[1]
        prior_paths, prior_cmds = parse_identity_ledger(prior_ledger)
        new_paths, new_cmds = identity_items_from_traces(trace_rows)
        ledger_paths, ledger_cmds = merge_identity_items(
            prior_paths, prior_cmds, new_paths, new_cmds
        )
        identity_ledger = render_identity_ledger(paths=ledger_paths, commands=ledger_cmds)

        async def _runner(credentials: LLMCredentials) -> str:
            model = resolve_user_model(credentials)
            provider = build_provider(credentials, purpose="platform_internal")
            try:
                return await _summarize(
                    provider,
                    old_summary,
                    fold_msgs,
                    model=model,
                    conversation_id=conversation_id,
                    user_waiting=user_waiting,
                    file_ledger=file_ledger,
                    journals=journals,
                )
            finally:
                close = getattr(provider, "close", None)
                if close is not None:
                    await close()

        # No usable platform/BYOK key, platform allowance spent, or auth failed both
        # sides: skip WITHOUT advancing the watermark so a later pass can retry. A
        # refusal that dated its own recovery hands that date over here rather than
        # leaving us to guess 90 seconds at a wall upstream measured in hours.
        bg = await run_compaction_llm(user_id, conversation_id, runner=_runner)
        if isinstance(bg, BackgroundLlmSkip):
            _mark_failure_cooldown(
                conversation_id,
                declared_recovery_in=bg.declared_recovery_in,
                user_id=user_id,
            )
            return False
        summary = bg.value

        # Empty output (timeout / error / refusal): leave the stored state intact and
        # let a later due turn retry after cooldown — never persist a blank summary.
        if not summary.strip():
            _mark_failure_cooldown(conversation_id, user_id=user_id)
            return False

        stored = attach_identity_ledger(summary, identity_ledger)
        async with async_session_factory() as session:
            await ConversationRepository(session).set_compaction(
                conversation_id,
                summary=stored,
                compacted_through=new_watermark,
                input_tokens=trigger_input_tokens,
            )
        _clear_failure_cooldown(conversation_id)
        logger.info(
            "compaction.done",
            conversation_id=conversation_id,
            folded=len(fold_msgs),
            kept=max(0, len(newest) - recency_keep_index(
                newest, token_budget=settings.compaction_recency_token_budget
            )),
            summary_chars=len(stored),
            trigger_input_tokens=trigger_input_tokens,
        )
        return True
    except Exception as e:  # never break anything — the turn already completed
        _mark_failure_cooldown(
            conversation_id,
            declared_recovery_in=_declared_recovery_seconds(e),
            user_id=user_id,
        )
        logger.warning("compaction.failed", conversation_id=conversation_id, error=str(e))
        return False


# --- Trigger (live path) -----------------------------------------------------
# Fire-and-forget after a due turn, in-process (single-server posture, like
# consolidation / approvals). ``_inflight_tasks`` dedupes a burst of due turns onto
# one pass per conversation and lets the near-ceiling pre-turn path await the same
# task; ``_failure_cooldown_until`` blocks re-schedule after a failed pass;
# ``_declared_ready_at`` is the subset of those cooldowns upstream itself dated;
# ``_declared_recovery_at`` is that same date left UNCAPPED, for saying it out loud;
# ``_cooldown_allowance`` remembers whose allowance each cooldown was armed against,
# so a key swap or a quota bump retires it (see ``billing.allowance``); ``_tasks``
# holds references so a pass is not GC'd mid-flight and can be flushed on shutdown.
_inflight_tasks: dict[str, asyncio.Task] = {}
_failure_cooldown_until: dict[str, float] = {}
_declared_ready_at: dict[str, float] = {}
_declared_recovery_at: dict[str, float] = {}
_cooldown_allowance: dict[str, tuple[str, int]] = {}
_tasks: set[asyncio.Task] = set()


# An upstream-dated cooldown is worth obeying; it is not worth obeying literally.
# The dates cluster at the platform day reset (median 12.9h), and honouring one
# means this conversation stops being folded for half a day while it keeps growing —
# a context that outgrows its window is a broken chat, whereas one wasted LLM call
# per hour is a rounding error against the turns spent in the meantime. So an hour
# is where taking upstream at its word stops paying: past it we re-ask, cheaply.
DECLARED_COOLDOWN_CAP_SECONDS = 3600.0


def _mark_failure_cooldown(
    conversation_id: str,
    *,
    declared_recovery_in: float | None = None,
    user_id: str | None = None,
) -> None:
    """Arm in-process failure cooldown for this conversation.

    ``declared_recovery_in`` is the cooldown the *failure itself* supplied (see
    :func:`_declared_recovery_seconds`); it wins over the configured guess whenever it
    is longer, because 90 seconds is our estimate of「多久后重试才值得」while this is
    upstream's own answer. It is remembered separately so the near-ceiling path can
    tell a proven wall from a guessed one — and it holds even when the guess is
    switched off, since sitting out a wall upstream dated is not a guess. It is also
    capped at :data:`DECLARED_COOLDOWN_CAP_SECONDS`, and tied to ``user_id``'s
    allowance epoch so it dies the moment that account's key or quota changes.

    The cap is a *scheduling* decision — keep re-asking hourly rather than freeze a
    growing chat for half a day — so the un-capped date is kept alongside it. What we
    tell a user whose history has gone missing has to be upstream's actual answer:
    「16:00 恢复」when the allowance really returns at 04:00 next day would send them
    back at 16:05 to the same silence (:func:`declared_recovery_at`).
    """
    now = time.monotonic()
    secs = float(settings.compaction_failure_cooldown_seconds)
    dated: float | None = None
    if declared_recovery_in is not None:
        dated = min(float(declared_recovery_in), DECLARED_COOLDOWN_CAP_SECONDS)
        secs = max(secs, dated)
    if dated is None and secs <= 0:
        return
    if dated is not None:
        _declared_ready_at[conversation_id] = now + dated
        _declared_recovery_at[conversation_id] = now + float(declared_recovery_in or 0.0)
    if secs > 0:
        _failure_cooldown_until[conversation_id] = now + secs
    if user_id:
        _cooldown_allowance[conversation_id] = (user_id, allowance_epoch(user_id))


def _clear_failure_cooldown(conversation_id: str) -> None:
    _failure_cooldown_until.pop(conversation_id, None)
    _declared_ready_at.pop(conversation_id, None)
    _declared_recovery_at.pop(conversation_id, None)
    _cooldown_allowance.pop(conversation_id, None)


def _allowance_moved_on(conversation_id: str) -> bool:
    """True when the account changed its key / quota after this cooldown was armed.

    Both cooldowns rest on the same premise —「上游现在不会接这个账号的调用」— and
    that premise is about an account, not a conversation. When the user brings a new
    key (exactly what the 429 copy tells them to do) or an operator lifts the quota,
    the refusal we cached is about somebody else's allowance; obeying it would leave
    the chat frozen behind a wall that no longer exists.
    """
    owner = _cooldown_allowance.get(conversation_id)
    if owner is None:
        return False
    user_id, epoch = owner
    return allowance_epoch(user_id) != epoch


def _in_failure_cooldown(conversation_id: str) -> bool:
    """True while a prior failure cooldown is still active; expires lazily."""
    if _allowance_moved_on(conversation_id):
        _clear_failure_cooldown(conversation_id)
        return False
    until = _failure_cooldown_until.get(conversation_id)
    if until is None:
        return False
    if time.monotonic() >= until:
        # This one always outlasts the dated cooldown (it is armed at the longer of
        # the two), so its expiry retires the whole record — owner included.
        _clear_failure_cooldown(conversation_id)
        return False
    return True


def _in_declared_cooldown(conversation_id: str) -> float | None:
    """Seconds still left of an upstream-dated cooldown, or ``None``; expires lazily.

    The narrow subset of :func:`_in_failure_cooldown` that even the urgent
    near-ceiling path must respect — here a retry is not a long shot, it is a call
    upstream has already refused for the next N seconds. Capped at an hour, and
    void once the account's allowance has changed under it.
    """
    if _allowance_moved_on(conversation_id):
        _clear_failure_cooldown(conversation_id)
        return None
    ready_at = _declared_ready_at.get(conversation_id)
    if ready_at is None:
        return None
    remaining = ready_at - time.monotonic()
    if remaining <= 0:
        _declared_ready_at.pop(conversation_id, None)
        return None
    return remaining


def declared_recovery_at(conversation_id: str) -> str | None:
    """ISO-8601 UTC instant upstream dated this conversation's folding to resume, or ``None``.

    The honesty read of :func:`_mark_failure_cooldown`'s record, and the only one that
    uses the *un-capped* date: it answers「什么时候能好」for a user, not「多久后重试才
    值得」for the scheduler. ``None`` whenever we cannot answer without guessing — the
    refusal never named a moment, that moment has passed, the account's allowance moved
    on, or this process simply is not the one that took the refusal (the record is
    in-process, same posture as the cooldowns it rides along with). Callers must treat
    a ``None`` as「不知道」and say so, never as「马上就好」.

    The instant travels un-worded (:func:`~agentcore.core.errors.utc_moment_iso`): the
    reader's timezone is the client's to know, not ours to guess.
    """
    if _allowance_moved_on(conversation_id):
        _clear_failure_cooldown(conversation_id)
        return None
    ready_at = _declared_recovery_at.get(conversation_id)
    if ready_at is None:
        return None
    remaining = ready_at - time.monotonic()
    if remaining <= 0:
        _declared_recovery_at.pop(conversation_id, None)
        return None
    return recovery_at_iso(remaining)


def near_context_ceiling(input_tokens: int, context_length: int | None) -> bool:
    """True when ``input_tokens`` is near the model window (定案⑦A threshold).

    Uses ``compaction_near_context_ratio`` of ``context_length`` when known and
    positive; otherwise the absolute ``compaction_near_context_tokens`` floor.
    """
    if input_tokens <= 0:
        return False
    if context_length is not None and context_length > 0:
        threshold = int(context_length * settings.compaction_near_context_ratio)
        return input_tokens >= threshold
    return input_tokens >= settings.compaction_near_context_tokens


def _spawn_compact(
    conversation_id: str, input_tokens: int, *, user_waiting: bool = False
) -> asyncio.Task | None:
    """Arm one compact task; ``None`` if this conversation already has one in flight."""
    if conversation_id in _inflight_tasks:
        return None
    task = asyncio.ensure_future(
        _run(conversation_id, input_tokens, user_waiting=user_waiting)
    )
    _inflight_tasks[conversation_id] = task
    _tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        if _inflight_tasks.get(conversation_id) is t:
            _inflight_tasks.pop(conversation_id, None)

    task.add_done_callback(_done)
    return task


def _arm_compaction(conversation_id: str, input_tokens: int) -> None:
    """Schedule one background fold; caller must have already decided due + not inflight."""
    _spawn_compact(conversation_id, input_tokens)


async def schedule_compaction_if_due(conversation_id: str, input_tokens: int) -> None:
    """Arm a background fold IF token or message trigger is due. Best-effort; never raises.

    ``due = (input_tokens ≥ trigger) OR (_select_fold on DB batch with message_trigger_min_fold)``.
    Awaits only the due check (cheap DB read when tokens are under threshold); the fold itself
    stays fire-and-forget. In-flight conversations and failure-cooldown conversations are
    no-ops; failures do not advance the watermark.
    """
    if not settings.compaction_enabled:
        return
    if _in_failure_cooldown(conversation_id):
        logger.debug("compaction.cooldown_skip", conversation_id=conversation_id, path="schedule")
        return
    if conversation_id in _inflight_tasks:
        return
    try:
        due = input_tokens >= settings.compaction_trigger_input_tokens
        if not due:
            due = await _is_message_due(conversation_id)
        if not due:
            return
        _arm_compaction(conversation_id, input_tokens)
    except Exception as e:
        _mark_failure_cooldown(conversation_id)
        logger.warning(
            "compaction.schedule_failed",
            conversation_id=conversation_id,
            error=str(e),
        )


async def ensure_compaction_before_turn(
    conversation_id: str,
    *,
    input_tokens: int,
    context_length: int | None = None,
) -> bool:
    """Await fold pass(es) when last-turn tokens are near the model window.

    Never raises. Bypasses the guessed failure cooldown — near-ceiling is urgent and
    the next attempt may well succeed — but yields to an upstream-dated one, which is
    the same urgency meeting a refusal already issued: every turn until that moment
    would otherwise block on a fold guaranteed to fail. Returns whether any pass wrote
    a new summary. Caps at ``compaction_near_max_passes`` so a long backlog still
    advances incrementally without unbounded pre-turn wait.

    The passes it starts are marked ``user_waiting``: a turn is blocked on them, so
    they fail on an upstream cooldown instead of sleeping through it. A pass already
    in flight from the post-turn scheduler keeps the patience it was armed with —
    adopting it is still better than folding twice against one watermark, and its own
    ``wait_for`` bounds the wait either way.
    """
    if not settings.compaction_enabled:
        return False
    if not near_context_ceiling(input_tokens, context_length):
        return False
    declared_remaining = _in_declared_cooldown(conversation_id)
    if declared_remaining is not None:
        logger.debug(
            "compaction.cooldown_skip",
            conversation_id=conversation_id,
            path="near_ceiling",
            declared_remaining_sec=round(declared_remaining, 1),
        )
        return False

    wrote_any = False
    try:
        for _ in range(max(1, settings.compaction_near_max_passes)):
            existing = _inflight_tasks.get(conversation_id)
            if existing is not None:
                try:
                    ok = bool(await existing)
                except Exception:
                    ok = False
                wrote_any = wrote_any or ok
                if not ok:
                    break
                continue
            task = _spawn_compact(conversation_id, input_tokens, user_waiting=True)
            if task is None:
                existing = _inflight_tasks.get(conversation_id)
                if existing is None:
                    break
                try:
                    ok = bool(await existing)
                except Exception:
                    ok = False
                wrote_any = wrote_any or ok
                if not ok:
                    break
                continue
            try:
                ok = bool(await task)
            except Exception:
                ok = False
            if not ok:
                break
            wrote_any = True
        logger.info(
            "compaction.near_ceiling",
            conversation_id=conversation_id,
            input_tokens=input_tokens,
            context_length=context_length if context_length is not None else 0,
            wrote=wrote_any,
        )
        return wrote_any
    except Exception as e:
        logger.warning(
            "compaction.near_ceiling_failed",
            conversation_id=conversation_id,
            error=str(e),
        )
        return False


async def _assembly_context_budget(session: object, conversation_id: str) -> int | None:
    """Shorter ceiling on this conversation's assembly, or None for the model window."""
    try:
        from agentcore.db.models import LlmModelProfile
        from agentcore.db.repositories import ConversationRepository

        conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)  # type: ignore[arg-type]
        aid = getattr(conv, "assembly_id", None) if conv is not None else None
        if not aid:
            return None
        row = await session.get(LlmModelProfile, aid)  # type: ignore[attr-defined]
        if row is None or getattr(row, "user_id", None) != getattr(conv, "user_id", None):
            return None
        budget = getattr(row, "context_budget", None)
        if isinstance(budget, int) and not isinstance(budget, bool) and budget > 0:
            return budget
        return None
    except Exception:
        return None


async def _load_fit_watermark(
    conversation_id: str, model_id: str | None
) -> tuple[int, int | None]:
    """Last positive single-request prompt + this turn's model window.

    Metrics read failures return ``(0, window)`` so a telemetry blip cannot
    refuse a send. Empty-fail zeros are skipped inside ``latest_prompt_tokens``.
    """
    tokens = 0
    budget: int | None = None
    try:
        async with async_session_factory() as session:
            loaded = await TurnMetricsRepository(session).latest_prompt_tokens(
                conversation_id
            )
            if loaded is None:
                conv = await ConversationRepository(session).get_by_id_unscoped(
                    conversation_id
                )
                loaded = (
                    int(getattr(conv, "compaction_input_tokens", None) or 0)
                    if conv
                    else 0
                )
            tokens = int(loaded or 0)
            budget = await _assembly_context_budget(session, conversation_id)
    except Exception as e:
        logger.warning(
            "compaction.near_ceiling_failed",
            conversation_id=conversation_id,
            error=str(e),
        )
        tokens = 0
        budget = None
    from agentcore.llm.context_budget import effective_context_length

    return tokens, effective_context_length(model_id, budget)


def _overflow_error() -> Exception:
    from agentcore.core.errors import ContextOverflowError
    from agentcore.llm.errors import CONTEXT_OVERFLOW_PRODUCT

    return ContextOverflowError(CONTEXT_OVERFLOW_PRODUCT)


async def maybe_compact_near_ceiling(
    conversation_id: str,
    *,
    model_id: str | None = None,
) -> bool:
    """Pre-turn facade: load last prompt tokens + model window, then maybe await fold.

    Token source: latest positive ``turn_metrics.prompt_tokens`` (legacy fallback
    ``input_tokens``), else ``conversations.compaction_input_tokens``. Best-effort;
    never raises.
    """
    if not settings.compaction_enabled:
        return False
    try:
        tokens, context_length = await _load_fit_watermark(conversation_id, model_id)
        return await ensure_compaction_before_turn(
            conversation_id,
            input_tokens=tokens,
            context_length=context_length,
        )
    except Exception as e:
        logger.warning(
            "compaction.near_ceiling_failed",
            conversation_id=conversation_id,
            error=str(e),
        )
        return False


async def ensure_dull_compaction_before_turn(
    conversation_id: str,
    *,
    input_tokens: int,
) -> bool:
    """Await one fold at the 32k dull line. Failure does not refuse the send.

    Skips when nothing outside the recency budget meets the internal empty-run
    gate, when compaction is off, or when an upstream-dated cooldown is active.
    Guessed failure cooldown is respected here (unlike near-ceiling).
    """
    if not settings.compaction_enabled:
        return False
    if input_tokens < settings.compaction_trigger_input_tokens:
        return False
    if _in_failure_cooldown(conversation_id):
        logger.debug("compaction.cooldown_skip", conversation_id=conversation_id, path="dull")
        return False
    declared_remaining = _in_declared_cooldown(conversation_id)
    if declared_remaining is not None:
        logger.debug(
            "compaction.cooldown_skip",
            conversation_id=conversation_id,
            path="dull",
            declared_remaining_sec=round(declared_remaining, 1),
        )
        return False
    try:
        if not await _has_foldable_beyond_recency(conversation_id):
            return False

        existing = _inflight_tasks.get(conversation_id)
        if existing is not None:
            try:
                return bool(await existing)
            except Exception:
                return False
        task = _spawn_compact(conversation_id, input_tokens, user_waiting=True)
        if task is None:
            existing = _inflight_tasks.get(conversation_id)
            if existing is None:
                return False
            try:
                return bool(await existing)
            except Exception:
                return False
        try:
            ok = bool(await task)
        except Exception:
            return False
        logger.info(
            "compaction.dull_line",
            conversation_id=conversation_id,
            input_tokens=input_tokens,
            wrote=ok,
        )
        return ok
    except Exception as e:
        logger.warning(
            "compaction.dull_line_failed",
            conversation_id=conversation_id,
            error=str(e),
        )
        return False


async def compact_before_turn(
    conversation_id: str,
    *,
    model_id: str | None = None,
) -> None:
    """Near-ceiling: await and refuse if nothing wrote. Dull line 32k: await once, still send.

    A successful near-ceiling fold proceeds even if the stored watermark still looks
    near. Post-turn ``schedule_compaction_if_due`` stays best-effort skip.
    """
    tokens, context_length = await _load_fit_watermark(conversation_id, model_id)
    near = near_context_ceiling(tokens, context_length)
    if near:
        wrote = False
        if settings.compaction_enabled:
            wrote = await ensure_compaction_before_turn(
                conversation_id,
                input_tokens=tokens,
                context_length=context_length,
            )
        if not wrote:
            raise _overflow_error()
        return
    if settings.compaction_enabled and tokens >= settings.compaction_trigger_input_tokens:
        await ensure_dull_compaction_before_turn(
            conversation_id, input_tokens=tokens
        )


async def _run(
    conversation_id: str, input_tokens: int, *, user_waiting: bool = False
) -> bool:
    return await compact_conversation(
        conversation_id, trigger_input_tokens=input_tokens, user_waiting=user_waiting
    )


async def shutdown_compaction(*, timeout: float | None = None) -> None:
    """Await in-flight folds on app shutdown; abandon after a short bound.

    Fold is best-effort enrichment. A wedged LLM call must not hold the Docker
    stop window — leftover tasks are cancelled and left for process exit.
    """
    pending = [task for task in _tasks if not task.done()]
    if not pending:
        return
    grace = (
        float(timeout) if timeout is not None else float(settings.compaction_shutdown_seconds)
    )
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True),
            timeout=grace,
        )
    except TimeoutError:
        logger.warning(
            "compaction.shutdown_timeout",
            pending=len(pending),
            timeout_seconds=grace,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
