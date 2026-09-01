"""Prefix-cache observability — measure whether the prompt prefix actually gets reused.

审计议题 D4（先补可观测，本轮不动结构）. The system prompt puts volatile sections at the tail
(:class:`~agentcore.runtime.context.contributor.SectionOrder`) so the foundation prefix stays
byte-identical across turns and rides a provider's exact-prefix cache discount. That is a
*claim about billing*, and until now nothing measured it: ``cost.prompt_assembled.assembly_hash``
only says the assembled text drifted, never whether the upstream actually charged a hit — and
the real request is ``system + history + user``, which the provider matches as ONE token prefix,
so a tail edit invalidates every history token behind it too.

This module adds the measurement, nothing else. It never touches assembly order, never trims,
never gates a call. Two halves:

**Assembly side** — every :class:`~agentcore.runtime.context.assembler.ContextAssembler` layer
registers its section fingerprints (:func:`record_prompt_sections`). Layers nest (shared base →
CEO chat → per-turn tail), so :func:`flatten_sections` splices a container section back into the
leaves it was rendered from — matched by digest, exactly, never by name — giving one leaf list in
render order. Diffing consecutive turns of a conversation names the first leaf that moved.

**Call side** — :func:`observe_prefix_cache` (called from the single ``llm.call`` emit point)
compares this request's message chain with the previous call on the same chain, and pairs the
structural verdict with the provider's own numbers (``TokenUsage.cache_hit_tokens`` — parsed
upstream by ``TokenUsage.from_openai_wire``, both dialects; this module never re-parses wire
JSON). One ``cost.prefix_cache`` line per call answers:

- **命中率** — ``hit_ratio`` = cache_hit_tokens / input_tokens, with ``cache_reported`` saying
  whether the provider spoke about caching at all (a silent provider is NOT a 0% hit).
- **被什么击穿** — ``breach`` classifies the first divergence against the previous request
  (system prompt / mid-history rewrite / pure append), and ``breach_section`` names the leaf
  when the system prompt is the culprit (e.g. ``workspace_facts`` = 文件索引变动,
  ``attachment_context`` = 变尾段本身). ``folder_catalog`` slot is kept;
  production no longer assembles that section.
- **随对话长度的差异** — ``prompt_messages`` / ``prompt_chars`` / ``input_tokens`` /
  ``chain_calls`` let the analyzer bucket by conversation size.

Honesty limits (do not read past them): ``reusable_tokens`` is EXACT only when this request is a
pure append of the previous one (then it is the previous request's own measured
``input_tokens``); otherwise it is a chars-prorated estimate and says so via ``reusable_basis``.
Providers also cache in blocks and expire entries on their own schedule, so a small shortfall is
normal and a hit ratio of 0 on a cold chain means nothing.

State is in-process and bounded (per worker): losing it costs a few ``cold_chain`` lines, never
correctness. Nothing here may raise into the LLM path — callers wrap it, and the internals stay
allocation-cheap (digests only, never a copy of the prompt).
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

from agentcore.core.log_context import get_log_value
from agentcore.core.logging import get_logger
from agentcore.costing.ledger import ROLE_CAPTAIN

logger = get_logger(__name__)

# Truncated sha256: collision risk is irrelevant for "did this text change" and keeps the
# per-chain state (one digest per message) small enough to hold hundreds of conversations.
_DIGEST_CHARS = 16
# Bounded LRUs. A chain entry is one digest+chars pair per message (~40B), a conversation
# entry two leaf lists (~1KB) — a few hundred of each stays well under a megabyte.
_MAX_CHAINS = 512
_MAX_CONVERSATIONS = 256
# Cap the reported change list so one wholesale prompt rewrite cannot blow up a log line.
_MAX_CHANGED_REPORTED = 8

# ``breach`` values — why this request could not reuse the previous one's whole prefix.
BREACH_COLD_CHAIN = "cold_chain"  # nothing seen before on this chain (first call / evicted)
BREACH_IDENTICAL = "identical"  # same messages as last call (retry / re-ask)
BREACH_HISTORY_GROWTH = "history_growth"  # pure append — the best case, prefix fully reusable
BREACH_SYSTEM_PROMPT = "system_prompt"  # message 0 changed → the whole request is a miss
BREACH_HEAD_REWRITE = "head_rewrite"  # first message changed but it is not a system message
BREACH_HISTORY_REWRITE = "history_rewrite"  # a mid-history message changed (compaction / edit)

# ``reusable_basis`` values — how ``reusable_tokens`` was obtained.
BASIS_MEASURED = "measured"  # the previous request's own input_tokens (pure append)
BASIS_ESTIMATED = "estimated"  # chars-prorated from the stable message prefix
BASIS_NONE = "none"  # no comparable predecessor


def digest_text(text: str) -> str:
    """Truncated sha256 of ``text`` — the change-detection unit used throughout."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]


# --------------------------------------------------------------------------------------
# Assembly side: section fingerprints per turn
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SectionFingerprint:
    """One rendered prompt section, identified without keeping its text."""

    key: str
    chars: int
    digest: str


@dataclass(frozen=True, slots=True)
class ScopeRecord:
    """One assembler layer's sections plus the digest of what that layer rendered.

    ``render_digest`` is what makes nesting resolvable: an outer layer receives this layer's
    render as a single section, so the outer section's digest equals this ``render_digest``.
    """

    scope: str
    sections: tuple[SectionFingerprint, ...]
    render_digest: str


@dataclass(frozen=True, slots=True)
class SectionDelta:
    """What moved in the system prompt between the previous turn and this one."""

    comparable: bool
    first_changed: str
    changed: tuple[str, ...]


_EMPTY_DELTA = SectionDelta(comparable=False, first_changed="", changed=())


@dataclass
class _ConversationSections:
    turn_id: str
    scopes: OrderedDict[str, ScopeRecord]
    previous_leaves: tuple[SectionFingerprint, ...]


_conversation_sections: OrderedDict[str, _ConversationSections] = OrderedDict()


def _lru_put(store: OrderedDict, key: str, value: object, limit: int) -> None:
    store[key] = value  # type: ignore[assignment]
    store.move_to_end(key)
    while len(store) > limit:
        store.popitem(last=False)


def record_prompt_sections(
    *,
    scope: str,
    sections: Sequence[tuple[str, str]],
    conversation_id: str | None = None,
    turn_id: str | None = None,
) -> None:
    """Register one assembler layer's rendered sections for this turn.

    ``sections`` is ``(key, text)`` in RENDER order — the caller (the assembler) already
    sorted them, and this must stay a read: nothing here may reorder or trim.

    Conversation / turn identity comes from the ambient log context by default
    (``conversation_id`` groups turns, ``trace_id`` marks one turn); an explicit value is
    for tests. With no conversation identity there is nothing to compare across turns, so
    the call is a no-op. Seeing a new turn rolls the finished turn's leaves into
    ``previous_leaves`` — that snapshot is what :func:`prompt_section_delta` diffs against.

    First write per (turn, scope) wins. The shared base is assembled for the CEO first and
    then again for other prompts inside the same turn (desktop worker, crash delegate); a
    later overwrite would leave the CEO's outer layer holding a container digest that no
    longer resolves, silently coarsening every later turn's attribution.
    """
    conv = conversation_id if conversation_id is not None else get_log_value("conversation_id")
    if not conv:
        return
    turn = turn_id if turn_id is not None else get_log_value("trace_id")
    record = ScopeRecord(
        scope=scope,
        sections=tuple(
            SectionFingerprint(key=key, chars=len(text), digest=digest_text(text))
            for key, text in sections
        ),
        render_digest=digest_text("\n".join(text for _, text in sections)),
    )
    entry = _conversation_sections.get(conv)
    if entry is None:
        entry = _ConversationSections(turn_id=turn, scopes=OrderedDict(), previous_leaves=())
    elif entry.turn_id != turn:
        # Empty vs labelled is the same turn (tests bind trace_id after a layer
        # already recorded, or an inner assemble ran before bind). Only wipe when
        # both sides have a real, different id.
        if not entry.turn_id:
            entry.turn_id = turn
        elif turn:
            entry.previous_leaves = flatten_sections(entry.scopes) or entry.previous_leaves
            entry.scopes = OrderedDict()
            entry.turn_id = turn
    entry.scopes.setdefault(scope, record)
    _lru_put(_conversation_sections, conv, entry, _MAX_CONVERSATIONS)


def flatten_sections(scopes: Mapping[str, ScopeRecord]) -> tuple[SectionFingerprint, ...]:
    """Splice nested layers into ONE leaf list in render order.

    A section whose digest equals another layer's ``render_digest`` *is* that layer's output,
    so it expands into that layer's sections. Digest equality means the two texts are the same
    text — there is no name convention to keep in sync, and a chain broken by an unrecorded
    layer degrades to reporting the container section (coarser attribution, never a wrong one).

    Assumes ONE prompt chain per turn (today: the CEO's). With two unrelated roots recorded
    under the same conversation, the larger one wins deterministically.
    """
    if not scopes:
        return ()
    records = list(scopes.values())
    by_render: dict[str, ScopeRecord] = {}
    for rec in records:
        existing = by_render.get(rec.render_digest)
        # Identical render (outer container == inner join) — keep the finer layer
        # so a turn with no volatile tail still splices.
        if existing is None or len(rec.sections) > len(existing.sections):
            by_render[rec.render_digest] = rec
    nested = {s.digest for r in records for s in r.sections if s.digest in by_render}
    roots = [r for r in records if r.render_digest not in nested]
    if not roots:  # every layer is contained (cycle / duplicate renders) — fall back by size
        roots = records
    root = max(roots, key=lambda r: sum(s.chars for s in r.sections))

    def expand(rec: ScopeRecord, seen: frozenset[str]) -> Iterator[SectionFingerprint]:
        for section in rec.sections:
            inner = by_render.get(section.digest)
            if inner is not None and inner.scope not in seen:
                yield from expand(inner, seen | {inner.scope})
            else:
                yield section

    return tuple(expand(root, frozenset({root.scope})))


def prompt_section_delta(conversation_id: str) -> SectionDelta:
    """Diff this turn's prompt leaves against the previous turn's.

    ``first_changed`` is the earliest leaf where the two render sequences diverge — the one
    that actually breaks the byte prefix; ``changed`` lists every leaf that moved (added,
    dropped, or edited) so a turn that churns several sections is still readable.
    ``comparable`` is False until a conversation has two tracked turns.
    """
    entry = _conversation_sections.get(conversation_id)
    if entry is None or not entry.previous_leaves:
        return _EMPTY_DELTA
    current = flatten_sections(entry.scopes)
    if not current:
        return _EMPTY_DELTA
    previous = entry.previous_leaves
    first_changed = ""
    for index in range(max(len(current), len(previous))):
        cur = current[index] if index < len(current) else None
        prev = previous[index] if index < len(previous) else None
        if cur is None:
            first_changed = prev.key if prev is not None else ""
            break
        if prev is None or cur.key != prev.key or cur.digest != prev.digest:
            first_changed = cur.key
            break
    prev_by_key = {s.key: s.digest for s in previous}
    cur_by_key = {s.key: s.digest for s in current}
    changed = [s.key for s in current if prev_by_key.get(s.key) != s.digest]
    changed.extend(s.key for s in previous if s.key not in cur_by_key)
    return SectionDelta(
        comparable=True,
        first_changed=first_changed,
        changed=tuple(changed[:_MAX_CHANGED_REPORTED]),
    )


# --------------------------------------------------------------------------------------
# Call side: message-chain diff + provider cache numbers
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChainState:
    """What the previous call on a chain left behind — digests, not text."""

    digests: tuple[str, ...]
    input_tokens: int
    calls: int


_chains: OrderedDict[str, ChainState] = OrderedDict()

# The chain scope of every ``cost_role=captain`` call. A constant, because the CEO's
# transcript belongs to the CONVERSATION and outlives the run that carries any one turn.
_CAPTAIN_CHAIN_SCOPE = "captain"


def _chain_scope() -> str:
    """Which continuously growing message list this call appends to.

    The CEO is conversation-scoped: its transcript spans user turns while the run that
    carries a turn does not (``runtime/pipeline/run.py`` mints a fresh captain run per
    turn, with ``agent_id == run_id == captain_run_id``, and a resumed turn mints another
    one). Scoping it to a run made every CEO turn its own chain, so turn N's opening call
    — the one whose whole ``system + history`` prefix the provider should have been
    holding — could only ever report ``cold_chain``.

    Everything else is run-scoped: a worker / 辩手 / 续写 transcript dies with its run and
    several of them run concurrently under one conversation, so they keep diffing only
    against themselves. ``cost_role`` is the discriminator because it is bound on every
    run path (``member`` / ``arena`` by default, ``captain`` only by the captain executor
    and the CEO turn entry points) — no run may inherit ``captain`` by omission.

    Honesty limit of the merge: two CEO turns overlapping in one conversation (a supersede)
    now interleave on one chain and read as mutual rewrites. That is rare, and the
    alternative — a chain that can never span turns — measures nothing at all.
    """
    if get_log_value("cost_role") == ROLE_CAPTAIN:
        return _CAPTAIN_CHAIN_SCOPE
    return "|".join((get_log_value("agent_id"), get_log_value("run_id")))


@dataclass(frozen=True, slots=True)
class PrefixCacheProbe:
    """One call's prefix-cache verdict: what could be reused vs. what the provider charged."""

    breach: str
    breach_section: str
    changed_sections: tuple[str, ...]
    cache_reported: bool
    input_tokens: int
    cache_hit_tokens: int
    hit_ratio: float
    reusable_tokens: int
    reusable_basis: str
    forfeited_tokens: int
    prompt_messages: int
    stable_prefix_messages: int
    prompt_chars: int
    stable_prefix_chars: int
    chain_calls: int

    def as_log_fields(self) -> dict[str, object]:
        return {
            "breach": self.breach,
            "breach_section": self.breach_section,
            "changed_sections": list(self.changed_sections),
            "cache_reported": self.cache_reported,
            "input_tokens": self.input_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "hit_ratio": self.hit_ratio,
            "reusable_tokens": self.reusable_tokens,
            "reusable_basis": self.reusable_basis,
            "forfeited_tokens": self.forfeited_tokens,
            "prompt_messages": self.prompt_messages,
            "stable_prefix_messages": self.stable_prefix_messages,
            "prompt_chars": self.prompt_chars,
            "stable_prefix_chars": self.stable_prefix_chars,
            "chain_calls": self.chain_calls,
        }


def message_fingerprints(messages: Sequence[object]) -> tuple[tuple[str, ...], tuple[int, ...]]:
    """Per-message (digest, chars) for an ``LLMMessage`` list.

    Covers everything the wire carries as prompt text: role, content (multimodal parts
    collapse to their text — an image swap alone is invisible here, and an image only ever
    arrives on a freshly appended message anyway), tool calls, and the tool-result id.
    """
    from agentcore.llm.provider.protocol import llm_content_text

    digests: list[str] = []
    sizes: list[int] = []
    for message in messages:
        text = llm_content_text(getattr(message, "content", None))
        parts = [str(getattr(message, "role", "")), text]
        tool_calls = getattr(message, "tool_calls", None) or ()
        for call in tool_calls:
            function = getattr(call, "function", None)
            parts.append(str(getattr(function, "name", "")))
            parts.append(str(getattr(function, "arguments", "")))
        tool_call_id = getattr(message, "tool_call_id", None)
        if tool_call_id:
            parts.append(str(tool_call_id))
        blob = "\x1f".join(parts)
        digests.append(digest_text(blob))
        sizes.append(len(blob))
    return tuple(digests), tuple(sizes)


def _first_divergence(current: Sequence[str], previous: Sequence[str]) -> int:
    limit = min(len(current), len(previous))
    for index in range(limit):
        if current[index] != previous[index]:
            return index
    return limit


def _classify(
    *,
    divergence: int,
    current_len: int,
    previous_len: int,
    first_role: str,
) -> str:
    if divergence < previous_len:
        if divergence == 0:
            return BREACH_SYSTEM_PROMPT if first_role == "system" else BREACH_HEAD_REWRITE
        return BREACH_HISTORY_REWRITE
    # The previous request survives whole as a prefix of this one.
    return BREACH_HISTORY_GROWTH if current_len > previous_len else BREACH_IDENTICAL


def compute_probe(
    *,
    digests: Sequence[str],
    sizes: Sequence[int],
    first_role: str,
    input_tokens: int,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    previous: ChainState | None,
    section_delta: SectionDelta = _EMPTY_DELTA,
) -> PrefixCacheProbe:
    """Pure metric computation — the unit under test (no globals, no logging).

    ``reusable_tokens`` answers "how much of this prompt SHOULD have been billed as cached".
    On a pure append that is the previous request's measured ``input_tokens`` (its whole
    prompt is a literal prefix of this one). Otherwise only the leading identical messages
    survive, and tokens for them are unknown, so we prorate this request's ``input_tokens``
    by the stable share of chars — flagged ``estimated`` because chars-per-token differs
    between CJK prose and tool JSON. ``forfeited_tokens`` is the shortfall: full prompt price
    paid for tokens the provider had already seen.
    """
    prompt_chars = sum(sizes)
    prompt_messages = len(digests)
    if previous is None:
        return PrefixCacheProbe(
            breach=BREACH_COLD_CHAIN,
            breach_section="",
            changed_sections=(),
            cache_reported=bool(cache_hit_tokens or cache_miss_tokens),
            input_tokens=input_tokens,
            cache_hit_tokens=cache_hit_tokens,
            hit_ratio=round(cache_hit_tokens / input_tokens, 4) if input_tokens else 0.0,
            reusable_tokens=0,
            reusable_basis=BASIS_NONE,
            forfeited_tokens=0,
            prompt_messages=prompt_messages,
            stable_prefix_messages=0,
            prompt_chars=prompt_chars,
            stable_prefix_chars=0,
            chain_calls=1,
        )

    divergence = _first_divergence(digests, previous.digests)
    breach = _classify(
        divergence=divergence,
        current_len=prompt_messages,
        previous_len=len(previous.digests),
        first_role=first_role,
    )
    stable_chars = sum(sizes[:divergence])
    if breach in (BREACH_HISTORY_GROWTH, BREACH_IDENTICAL):
        reusable_tokens = previous.input_tokens
        reusable_basis = BASIS_MEASURED
    elif divergence and prompt_chars:
        reusable_tokens = round(input_tokens * stable_chars / prompt_chars)
        reusable_basis = BASIS_ESTIMATED
    else:
        reusable_tokens = 0
        reusable_basis = BASIS_NONE
    # A provider that never mentions caching would otherwise read as "reused nothing".
    cache_reported = bool(cache_hit_tokens or cache_miss_tokens)
    forfeited = max(reusable_tokens - cache_hit_tokens, 0) if cache_reported else 0
    attributable = breach == BREACH_SYSTEM_PROMPT and section_delta.comparable
    return PrefixCacheProbe(
        breach=breach,
        breach_section=section_delta.first_changed if attributable else "",
        changed_sections=section_delta.changed if attributable else (),
        cache_reported=cache_reported,
        input_tokens=input_tokens,
        cache_hit_tokens=cache_hit_tokens,
        hit_ratio=round(cache_hit_tokens / input_tokens, 4) if input_tokens else 0.0,
        reusable_tokens=reusable_tokens,
        reusable_basis=reusable_basis,
        forfeited_tokens=forfeited,
        prompt_messages=prompt_messages,
        stable_prefix_messages=divergence,
        prompt_chars=prompt_chars,
        stable_prefix_chars=stable_chars,
        chain_calls=previous.calls + 1,
    )


def observe_prefix_cache(
    *,
    scenario: str,
    model: str,
    messages: Sequence[object] | None,
    input_tokens: int,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
) -> PrefixCacheProbe | None:
    """Probe this call against the previous one on the same chain and emit ``cost.prefix_cache``.

    A "chain" is one continuously growing message list: the CEO's turns in a conversation,
    or one delegated run's ReAct rounds. :func:`_chain_scope` decides which of the two this
    call is, and ``scenario`` joins the key on both — a title / compaction / memory call
    rides the same conversation but is a different prompt shape and comparing it against the
    chat transcript would report a breach on every line.

    Calls with no conversation identity (catalog probes, evals) have no chain to compare
    against and are skipped rather than logged as permanent misses.

    Returns the probe (tests / callers), or ``None`` when nothing was measurable.
    """
    if not messages or input_tokens <= 0:
        return None
    conversation_id = get_log_value("conversation_id")
    if not conversation_id:
        return None
    chain_key = "|".join((conversation_id, scenario, _chain_scope()))
    digests, sizes = message_fingerprints(messages)
    probe = compute_probe(
        digests=digests,
        sizes=sizes,
        first_role=str(getattr(messages[0], "role", "")),
        input_tokens=input_tokens,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
        previous=_chains.get(chain_key),
        section_delta=prompt_section_delta(conversation_id),
    )
    _lru_put(
        _chains,
        chain_key,
        ChainState(digests=digests, input_tokens=input_tokens, calls=probe.chain_calls),
        _MAX_CHAINS,
    )
    # Per-call probe: debug so a long sidecar session does not rotate llm.call
    # out of the 20MB×5 jsonl window. Raise LOG_LEVEL=DEBUG to keep D4 lines.
    logger.debug(
        "cost.prefix_cache",
        scenario=scenario,
        model=model,
        **probe.as_log_fields(),
    )
    return probe


def reset_prefix_cache_state() -> None:
    """Drop all in-process probe state (test isolation)."""
    _chains.clear()
    _conversation_sections.clear()
