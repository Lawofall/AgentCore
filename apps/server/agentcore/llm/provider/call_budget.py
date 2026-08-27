"""How long one LLM call may spend *asleep* on a 429 — decided by who is waiting.

Background one-shots (sidebar title, rolling compaction …) run under an
``asyncio.wait_for`` ceiling. That number is the call's wall clock, and until this
module existed the retry loop answered「这次 429 我该不该睡着等」with one global
constant that knew nothing about it.

**The cooldown being slept on is usually ours, not upstream's.** In 2.35 production
days, 138 of 1983 ``llm.rate_limit_no_retry`` hits logged ``retry_after=32.0`` at
``attempt=5`` — zero dispersion, and the only value under a minute in the whole
sample, because it is not a cooldown anyone declared: it is the last link of our own
2→4→8→16→32 chain on 429s that carried no ``Retry-After`` header at all (see
:mod:`agentcore.core.errors` and ``openai_compatible._parse_retry_after``). Reaching
it costs ~30 seconds of sleeping first, so no budget rescues those hits — 45s of
compaction patience minus 30s already spent minus the retry's own reserve leaves 10,
still short of 32. This module refuses them at the same attempt the global constant
did, and that is the correct outcome, not a miss.

**What a per-call budget does buy is refusing to sleep at all.** The same 45s fold
runs on two paths: post-turn (nobody is blocked, ~30s of backoff costs only clock)
and pre-turn near-ceiling, which the turn *awaits* before assembling history. On that
second path every one of those seconds is a user staring at nothing for a summary
that will not arrive, and it ends in the same silent fallback either way. Zero
patience turns it into「1 秒失败、回合立刻开始」. The title path gets the smaller half
of the same effect: a 20s deadline stops the chain one link early, so its failure is
a rate limit it can classify instead of a timeout blown through its own ``wait_for``.

So the caller answers one question — :func:`complete_within_budget`'s ``user_waiting``
— and two numbers follow from it:

- **deadline** — always ``budget``; the caller's ``wait_for`` and nothing else.
- **patience** (``LLMRequest.retry_patience_seconds``) — how much of that clock may
  be spent *asleep* between 429 attempts. ``budget`` when the call is background,
  **zero** when a turn is blocked on it: a blocked user gets the failure now, and the
  turn starts.

Derived from patience, never re-guessed:

- **no patience field** → :data:`~agentcore.core.errors.MAX_RETRY_AFTER`. Interactive
  turns have no wall clock; a human's patience is the budget, and that constant is
  what words the 429 they read.
- **a patience** → whatever is *left* of it, minus room for the retried attempt, and
  never past :data:`HOPELESS_RETRY_AFTER`.

Copy never keys off this module. A per-call ceiling moves ``retryable`` only — the
429 sentence stays keyed on ``MAX_RETRY_AFTER`` (see :mod:`agentcore.core.errors`) —
and patience counts only for scenarios whose failure degrades silently
(:data:`SILENT_DEGRADE_SCENARIOS`). That last rule is enforced where the provider
*reads* the field (:func:`provider_retry_ceiling`), not only where a well-behaved
caller stamps it: a stray ``retry_patience_seconds`` on a chat request would
otherwise narrow an interactive turn's ceiling below the sentence it prints.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from agentcore.core.errors import MAX_RETRY_AFTER
from agentcore.core.logging import get_logger
from agentcore.llm.provider.protocol import LLMProvider, LLMRequest, LLMResponse

logger = get_logger(__name__)

# Room held back for the retried attempt itself: sleeping the whole remaining
# patience guarantees the caller's ``wait_for`` fires before the retry can land.
# Sized at a fast non-thinking one-shot rather than a worst case — these callers
# degrade to the *same* silent fallback on a timeout as on a rate-limit failure,
# so an optimistic floor risks nothing and buys a real second chance.
RETRY_ATTEMPT_RESERVE = 5.0

# No budget, however generous, buys an hour-scale wait. Every cooldown production
# actually declares is one: 92% of no-retry hits are the upstream day reset (median
# 12.9h), and the sub-minute cluster next to them is our own backoff, not upstream
# back-pressure. Past this cap the header is a quota wall, and sitting it out is
# hopeless by construction rather than by whichever caller happened to hit it.
HOPELESS_RETRY_AFTER = 60.0

# Patience is honoured only for these. Every one of them swallows its own failure —
# an empty title falls back to the truncated first message, a failed fold leaves the
# stored summary untouched — so their 429 never becomes a sentence on screen, and a
# ceiling that differs from ``MAX_RETRY_AFTER`` cannot contradict the copy. Holds the
# scenarios actually wired to :func:`complete_within_budget` and no more: a name
# listed here in advance is a hole in the invariant, not a reservation.
#
# ``memory`` is the notable absence, and it is deliberate despite owning 76% of the
# rate-limit failures: the consolidation sweep runs under no ``wait_for``, so there is
# no wall clock to derive a patience from, and nobody is blocked on it either. Its
# 429s are the header-less kind that a budget cannot rescue anyway — the interactive
# ceiling already refuses them at the same attempt, and the sweep's own cooldown is
# what governs the retry.
#
# Title and compaction share this set but **not** the process 429 slot
# (``cooldown_gate.cooldown_lane``): a title day-reset Retry-After must not make
# compaction refuse before it probes.
SILENT_DEGRADE_SCENARIOS = frozenset({"title", "compaction"})


def retry_after_ceiling(patience: float | None, *, elapsed: float = 0.0) -> float:
    """Longest ``Retry-After`` this call may sit out, given what is left of ``patience``.

    ``elapsed`` is time already spent inside the call (upstream latency, earlier
    sleeps), so a chain of 429s narrows the ceiling instead of restarting it — which
    is what stops a header-less chain from sleeping its way through the whole budget.
    ``0.0`` patience — a turn is blocked on this call — yields ``0.0``: the first 429
    is refused on the spot, whether the cooldown is upstream's or our first 2s backoff.
    """
    if patience is None:
        return min(MAX_RETRY_AFTER, HOPELESS_RETRY_AFTER)
    remaining = patience - max(elapsed, 0.0) - RETRY_ATTEMPT_RESERVE
    return max(min(remaining, HOPELESS_RETRY_AFTER), 0.0)


def provider_retry_ceiling(*, scenario: str, patience: float | None, elapsed: float = 0.0) -> float:
    """The ceiling a provider may honour for a request it is about to retry.

    The read-side half of the invariant: patience stamped on a scenario whose 429
    reaches a bubble is **ignored**, not obeyed. Enforcing it only in
    :func:`complete_within_budget` left every ``provider.stream`` / ``provider
    .complete`` caller free to hand the retry loop a ceiling that contradicts the
    sentence the user is about to read; here the field is checked where it is used.
    """
    if patience is not None and scenario not in SILENT_DEGRADE_SCENARIOS:
        logger.warning(
            "llm.retry_patience_ignored",
            scenario=scenario,
            patience_sec=patience,
        )
        patience = None
    return retry_after_ceiling(patience, elapsed=elapsed)


async def complete_within_budget(
    provider: LLMProvider,
    request: LLMRequest,
    *,
    budget: float,
    user_waiting: bool = False,
) -> LLMResponse:
    """One timed completion — ``budget`` is the deadline, ``user_waiting`` the patience.

    Replaces a bare ``asyncio.wait_for(provider.complete(request), timeout=…)`` and
    raises ``TimeoutError`` the same way; the point is that the provider now learns
    how much of that deadline it may spend asleep, instead of guessing an
    interactive number — or, when a turn is blocked on this call, that it may spend
    none of it.

    ``user_waiting=True`` is for a call some human is sitting in front of even
    though its failure is silent (the pre-turn near-ceiling fold): patience drops to
    zero, so the first 429 fails immediately — no backoff chain, no cooldown — and the
    turn gets on with it.

    Refuses scenarios outside :data:`SILENT_DEGRADE_SCENARIOS`: their 429 reaches a
    bubble, and user-facing copy has exactly one ceiling.
    """
    if request.scenario not in SILENT_DEGRADE_SCENARIOS:
        raise ValueError(
            f"scenario {request.scenario!r} 的失败会走到用户面，不能带 per-call 预算："
            "限流文案的唯一来源是 core.errors.MAX_RETRY_AFTER"
        )
    patience = 0.0 if user_waiting else budget
    return await asyncio.wait_for(
        provider.complete(replace(request, retry_patience_seconds=patience)),
        timeout=budget,
    )
