"""Conversation-scoped web_search result cache (案例1 #5: 检索去重 / 共享检索缓存).

``web_search`` hits the network (SearXNG, or Tavily on fallback) on every call.
Within ONE conversation a research team — the CEO plus its delegated workers, which
all share the conversation_id — frequently re-issues the SAME query: duplicate
searches across parallel workers and across ReAct rounds, especially when a degraded
worker keeps retrying searches (实测案例复盘 案例1: workers fired 13–16 searches).
Each repeat pays the round-trip and adds pressure on the single SearXNG instance's
concurrency gate / per-host breaker — so duplicates make the WHOLE team more likely
to trip search-blind. This memoises a query's results per conversation so a repeat
returns instantly from memory within a freshness TTL.

Deliberately mirrors the ``read_url`` :class:`ConversationUrlCache` (``url_cache``):
same conversation scope, bounded the same three ways (entry TTL + per-conversation
count / byte caps + idle-conversation reaping), same single-worker in-process
posture (front with Redis to scale out; a miss just re-searches). It is kept a
SEPARATE module rather than generalising the URL cache so the proven read_url path
stays untouched and the two evolve independently — search keys on the normalised
query + result-count budget, read_url on the URL + char budget. (A generic
``ConversationScopedCache`` base extracted from both is a possible later refactor.)

Pure latency/resilience optimisation, never a correctness dependency: only
SUCCESSFUL, non-empty searches are cached, so a miss / eviction / restart only costs
one re-search. The "shared retrieval cache" is automatic: workers in the same
conversation share the conversation_id, so a query one worker ran serves the rest.
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass

from agentcore.core.logging import get_logger
from agentcore.tools.builtin.web.search_backend import SearchResult

logger = get_logger(__name__)

# A search result set stays fresh this long within a conversation. Slightly shorter
# than read_url's 15 min: search rankings / news move a bit faster than a fetched
# page's body, but the common "the team re-runs this query a few rounds later" path
# still hits.
SEARCH_CACHE_TTL_SECONDS = 10 * 60.0
# Max distinct queries cached per conversation (LRU-evict the least-recently-used).
SEARCH_CACHE_MAX_ENTRIES = 128
# Max cached content bytes per conversation, so a long research conversation can't
# grow memory without bound (LRU-evict oldest until within budget).
SEARCH_CACHE_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB
# Max conversations held process-wide (LRU-evict the least-recently-used).
SEARCH_CACHE_MAX_CONVERSATIONS = 256
# An idle conversation's whole cache is reaped after this long untouched.
SEARCH_CACHE_CONVERSATION_TTL_SECONDS = 30 * 60.0
# A query that just came back EMPTY is remembered (negatively) this long, so a degraded
# worker re-issuing the SAME empty query within the window is served the empty result
# from memory instead of re-hitting SearXNG. Kept MUCH shorter than the positive TTL: an
# empty is usually transient (engine CAPTCHA / hiccup) and a genuine retry once it likely
# cleared is wanted — we only suppress the immediate retry STORM (实测案例复盘 案例1: a
# degraded worker fired 13–16 searches, each empty re-hit deepening the CAPTCHA ban that
# blanks the WHOLE team). Negative-cached only within a conversation (workers share the
# conversation_id), same single-worker in-process posture as the positive cache.
SEARCH_EMPTY_TTL_SECONDS = 45.0


# Latin-script token (letters / digits / common ASCII punct inside a word). Used to
# decide whether A4 word-order sorting applies — CJK / mixed queries keep order.
_LATIN_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_\-./]*$", re.IGNORECASE)


def _query_key(query: str, language: str | None = None, *, exact: bool = False) -> str:
    """Normalised cache key (A4): casefold + whitespace-collapse; Latin word-order sort.

    Phase-1 normalisation only — **no stopword removal** (negation words must stay).
    ``exact=True`` skips word-order sorting (debate carve-out: independent evidence
    discipline). ``language`` is part of the key so a zh-pinned result set never
    serves an en (or ja) request for the same ASCII query string.
    """
    base = re.sub(r"\s+", " ", (query or "").strip().casefold())
    if not exact and base:
        tokens = base.split(" ")
        if len(tokens) > 1 and all(_LATIN_TOKEN_RE.fullmatch(t) for t in tokens):
            base = " ".join(sorted(tokens))
    lang = (language or "").strip().casefold()
    return f"{lang}|{base}" if lang else base


def _entry_bytes(results: list[SearchResult]) -> int:
    """Rough byte size of a cached result set (title + url + snippet), for the cap."""
    return sum(len(r.title) + len(r.url) + len(r.snippet) for r in results)


@dataclass
class SearchCacheEntry:
    """One cached query → results set.

    ``max_results`` records the cap that produced ``results`` so a later call asking
    for MORE than we captured (when the backend was capped) correctly misses and
    re-searches — the search analogue of the read_url cache's ``truncated`` flag.
    """

    query: str
    results: list[SearchResult]
    max_results: int
    stored_at: float
    language: str = ""


class ConversationSearchCache:
    """One conversation's bounded, LRU-ordered cache of web_search results.

    Keyed by the normalised query. Bounded three ways (mirrors
    :class:`ConversationUrlCache`): a freshness **TTL** per entry, an entry-**count**
    cap, and a content-**byte** cap (both LRU-evict). Eviction is lazy (on
    ``get`` / ``put``); ``last_access`` lets the registry reap an idle conversation.
    """

    def __init__(
        self,
        *,
        max_entries: int = SEARCH_CACHE_MAX_ENTRIES,
        max_bytes: int = SEARCH_CACHE_MAX_BYTES,
        ttl_seconds: float = SEARCH_CACHE_TTL_SECONDS,
        empty_ttl_seconds: float = SEARCH_EMPTY_TTL_SECONDS,
    ) -> None:
        self._entries: OrderedDict[str, SearchCacheEntry] = OrderedDict()
        # Negative cache: normalised query → time the empty result was recorded. Bounded
        # the same way (LRU by count + TTL); keyed/normalised exactly like the positive
        # cache so the same query collapses across both.
        self._empty: OrderedDict[str, float] = OrderedDict()
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._ttl = ttl_seconds
        self._empty_ttl = empty_ttl_seconds
        self.last_access: float = time.time()

    def get(
        self,
        query: str,
        *,
        min_results: int,
        language: str | None = None,
        exact: bool = False,
    ) -> SearchCacheEntry | None:
        """The fresh entry for ``query`` that can satisfy a request needing
        ``min_results`` results, or ``None`` (caller then searches).

        Misses when absent, expired (TTL), or the cached set was capped (returned as
        many as it was asked for, so MORE may exist) and the caller now wants more
        than we captured — re-searching with the larger budget is then correct. A set
        that returned fewer than its cap is everything the backend had, so it serves
        any request. ``exact`` selects the A4 debate carve-out key (no word-order share).
        """
        self.last_access = time.time()
        key = _query_key(query, language, exact=exact)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if (time.time() - entry.stored_at) > self._ttl:
            del self._entries[key]
            return None
        if len(entry.results) >= entry.max_results and min_results > len(entry.results):
            return None
        self._entries.move_to_end(key)
        return entry

    def put(self, entry: SearchCacheEntry, *, exact: bool = False) -> None:
        """Cache (or refresh) a successful search as most-recently-used, then enforce
        the TTL + count + byte caps."""
        self.last_access = time.time()
        key = _query_key(entry.query, entry.language or None, exact=exact)
        self._empty.pop(key, None)  # a real result supersedes any stale "recently empty" marker
        self._entries[key] = entry
        self._entries.move_to_end(key)
        self._prune()

    def is_recently_empty(
        self, query: str, *, language: str | None = None, exact: bool = False
    ) -> bool:
        """Whether ``query`` returned empty within the negative-cache window.

        True means "serve empty without hitting the network" — it suppresses an
        immediate re-search of a query that just came back empty (engine CAPTCHA /
        hiccup), which is what turns a degraded worker's retries into a storm against
        the shared SearXNG. An expired marker is pruned and misses (a genuine retry).
        """
        self.last_access = time.time()
        key = _query_key(query, language, exact=exact)
        stored = self._empty.get(key)
        if stored is None:
            return False
        if (time.time() - stored) > self._empty_ttl:
            del self._empty[key]
            return False
        self._empty.move_to_end(key)
        return True

    def note_empty(
        self, query: str, *, language: str | None = None, exact: bool = False
    ) -> None:
        """Record that ``query`` just returned empty (negative cache), bounded LRU + TTL."""
        self.last_access = time.time()
        key = _query_key(query, language, exact=exact)
        self._empty[key] = time.time()
        self._empty.move_to_end(key)
        self._prune_empty()

    def is_idle(self, ttl_seconds: float) -> bool:
        """Whether this cache has not been touched within ``ttl_seconds`` (the
        registry uses it to drop a whole idle conversation)."""
        return (time.time() - self.last_access) > ttl_seconds

    def _prune(self) -> None:
        now = time.time()
        expired = [k for k, e in self._entries.items() if (now - e.stored_at) > self._ttl]
        for k in expired:
            del self._entries[k]
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        while self._total_bytes() > self._max_bytes and len(self._entries) > 1:
            self._entries.popitem(last=False)

    def _prune_empty(self) -> None:
        now = time.time()
        expired = [k for k, t in self._empty.items() if (now - t) > self._empty_ttl]
        for k in expired:
            del self._empty[k]
        while len(self._empty) > self._max_entries:
            self._empty.popitem(last=False)

    def _total_bytes(self) -> int:
        return sum(_entry_bytes(e.results) for e in self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, query: object) -> bool:
        return isinstance(query, str) and _query_key(query) in self._entries


class SearchCacheRegistry:
    """Process-wide ``conversation_id → ConversationSearchCache`` map.

    A conversation's search cache survives across turns so a later turn's re-query
    hits. Bounded by a conversation **count** cap (LRU-evict) and an idle **TTL** (a
    conversation untouched within the window is dropped). Same single-worker in-
    process posture as the read_url cache / roster / approvals (front with Redis to
    scale out).
    """

    def __init__(
        self,
        *,
        max_conversations: int = SEARCH_CACHE_MAX_CONVERSATIONS,
        conversation_ttl_seconds: float = SEARCH_CACHE_CONVERSATION_TTL_SECONDS,
        cache_max_entries: int = SEARCH_CACHE_MAX_ENTRIES,
        cache_max_bytes: int = SEARCH_CACHE_MAX_BYTES,
        cache_ttl_seconds: float = SEARCH_CACHE_TTL_SECONDS,
        cache_empty_ttl_seconds: float = SEARCH_EMPTY_TTL_SECONDS,
    ) -> None:
        self._caches: OrderedDict[str, ConversationSearchCache] = OrderedDict()
        self._max_conversations = max_conversations
        self._conversation_ttl = conversation_ttl_seconds
        self._cache_max_entries = cache_max_entries
        self._cache_max_bytes = cache_max_bytes
        self._cache_ttl = cache_ttl_seconds
        self._cache_empty_ttl = cache_empty_ttl_seconds

    def get_or_create(self, conversation_id: str) -> ConversationSearchCache:
        """The conversation's cache, creating it on first use. Reaps idle
        conversations first, then LRU-caps the conversation count."""
        self._evict_idle()
        cache = self._caches.get(conversation_id)
        if cache is None:
            cache = ConversationSearchCache(
                max_entries=self._cache_max_entries,
                max_bytes=self._cache_max_bytes,
                ttl_seconds=self._cache_ttl,
                empty_ttl_seconds=self._cache_empty_ttl,
            )
            self._caches[conversation_id] = cache
        self._caches.move_to_end(conversation_id)
        while len(self._caches) > self._max_conversations:
            self._caches.popitem(last=False)
        return cache

    def _evict_idle(self) -> None:
        idle = [cid for cid, c in self._caches.items() if c.is_idle(self._conversation_ttl)]
        for cid in idle:
            del self._caches[cid]
            # Victim id is not canonical conversation_id: this runs in the caller's
            # request context, and merge_contextvars would mix user_id / trace_id.
            logger.info("search_cache.conversation_evicted", evicted_conversation_id=cid)

    def __len__(self) -> int:
        return len(self._caches)

    def __contains__(self, conversation_id: object) -> bool:
        return conversation_id in self._caches


# Process-wide registry, shared by every web_search call so a conversation's result
# cache survives across turns (single-worker posture; front with Redis to scale out).
_registry: SearchCacheRegistry = SearchCacheRegistry()


def default_search_cache_registry() -> SearchCacheRegistry:
    """The process-wide web_search result-cache registry (shared by every turn)."""
    return _registry
