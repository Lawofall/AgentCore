"""Conversation-scoped read_url fetch cache (P2: 会话级抓取缓存).

``read_url`` reaches the open internet on every call. Within ONE conversation the
same page is often re-read across turns (the user follows up on a source) or read
right after ``web_search`` surfaced it — each time paying the full fetch round-trip
and re-exposing the call to a now-flaky / rate-limited / 403 page. This module
memoises a fetched page's extracted text per conversation, so a repeat ``read_url``
of the same URL returns instantly from memory (within a freshness TTL) instead of
re-fetching.

Scope & posture mirror the 留人 roster (``runtime/sessions.py``): conversation-
scoped, bounded (idle TTL + per-conversation URL-count / byte caps + idle-
conversation reaping), and an in-process singleton (front with Redis to scale out;
cross-process persistence is out of scope — a miss just re-fetches).

It is a PURE latency/resilience optimisation, never a correctness dependency:
every entry can be re-derived by fetching, so a miss / eviction / restart only
costs one re-fetch, and only SUCCESSFUL fetches are cached (a 403 / timeout is
left to retry, backed by the per-host breaker). The cache key normalises the URL
the same way as citation de-dup (strip ``#fragment`` + trailing ``/``), so one
page = one key across both systems.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass

from agentcore.core.logging import get_logger

logger = get_logger(__name__)

# A fetched page stays fresh this long within a conversation. Short enough to bound
# staleness on pages that change (news / prices), long enough that the common
# "re-read the same source a few turns later" path hits.
URL_CACHE_TTL_SECONDS = 15 * 60.0
# Max distinct URLs cached per conversation (LRU-evict the least-recently-used).
URL_CACHE_MAX_ENTRIES = 64
# Max cached content bytes per conversation, so a long research conversation can't
# grow memory without bound (LRU-evict oldest until within budget).
URL_CACHE_MAX_BYTES = 4 * 1024 * 1024  # 4 MiB
# Max conversations held process-wide (LRU-evict the least-recently-used).
URL_CACHE_MAX_CONVERSATIONS = 256
# An idle conversation's whole cache is reaped after this long untouched.
URL_CACHE_CONVERSATION_TTL_SECONDS = 30 * 60.0


def _cache_key(url: str) -> str:
    """Normalised cache key: drop ``#fragment`` and a trailing ``/`` so the same
    page reached via slightly different URLs shares one entry (matches the citation
    de-dup key in ``runtime/citations._citation_key``)."""
    return (url or "").split("#", 1)[0].rstrip("/")


@dataclass
class UrlCacheEntry:
    """One cached page fetch.

    ``content`` is the extracted text already capped at the ``max_chars`` budget the
    fetch used; ``truncated`` records whether the page text hit that cap (so a later
    request for MORE characters than we captured correctly misses and re-fetches).
    """

    url: str
    title: str
    content: str
    snippet: str
    site: str
    max_chars: int
    truncated: bool
    stored_at: float


class ConversationUrlCache:
    """One conversation's bounded, LRU-ordered cache of fetched pages.

    Keyed by the normalised URL. Bounded three ways (mirrors ``SessionStore``): a
    freshness **TTL** per entry, an entry-**count** cap, and a content-**byte** cap
    (both LRU-evict). Eviction is lazy (on ``get`` / ``put``); ``last_access`` lets
    the registry reap an idle conversation's whole cache.
    """

    def __init__(
        self,
        *,
        max_entries: int = URL_CACHE_MAX_ENTRIES,
        max_bytes: int = URL_CACHE_MAX_BYTES,
        ttl_seconds: float = URL_CACHE_TTL_SECONDS,
    ) -> None:
        self._entries: OrderedDict[str, UrlCacheEntry] = OrderedDict()
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._ttl = ttl_seconds
        self.last_access: float = time.time()

    def get(self, url: str, *, min_chars: int) -> UrlCacheEntry | None:
        """The fresh entry for ``url`` that can satisfy a request needing
        ``min_chars`` characters, or ``None`` (caller then fetches).

        Misses when absent, expired (TTL), or the cached page was truncated and the
        caller now wants more characters than we captured — in which case re-fetching
        with the larger budget (and overwriting via :meth:`put`) is the right move.
        """
        self.last_access = time.time()
        key = _cache_key(url)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if (time.time() - entry.stored_at) > self._ttl:
            del self._entries[key]
            return None
        if entry.truncated and min_chars > len(entry.content):
            return None
        self._entries.move_to_end(key)
        return entry

    def put(self, entry: UrlCacheEntry) -> None:
        """Cache (or refresh) a successful fetch as most-recently-used, then enforce
        the TTL + count + byte caps."""
        self.last_access = time.time()
        key = _cache_key(entry.url)
        self._entries[key] = entry
        self._entries.move_to_end(key)
        self._prune()

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

    def _total_bytes(self) -> int:
        return sum(len(e.content) for e in self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, url: object) -> bool:
        return isinstance(url, str) and _cache_key(url) in self._entries


class UrlCacheRegistry:
    """Process-wide ``conversation_id → ConversationUrlCache`` map.

    A conversation's fetch cache survives across turns so a later turn's re-read
    hits. Bounded by a conversation **count** cap (LRU-evict) and an idle **TTL** (a
    conversation untouched within the window is dropped). Same single-worker in-
    process posture as the roster / approvals / breaker (front with Redis to scale).
    """

    def __init__(
        self,
        *,
        max_conversations: int = URL_CACHE_MAX_CONVERSATIONS,
        conversation_ttl_seconds: float = URL_CACHE_CONVERSATION_TTL_SECONDS,
        cache_max_entries: int = URL_CACHE_MAX_ENTRIES,
        cache_max_bytes: int = URL_CACHE_MAX_BYTES,
        cache_ttl_seconds: float = URL_CACHE_TTL_SECONDS,
    ) -> None:
        self._caches: OrderedDict[str, ConversationUrlCache] = OrderedDict()
        self._max_conversations = max_conversations
        self._conversation_ttl = conversation_ttl_seconds
        self._cache_max_entries = cache_max_entries
        self._cache_max_bytes = cache_max_bytes
        self._cache_ttl = cache_ttl_seconds

    def get_or_create(self, conversation_id: str) -> ConversationUrlCache:
        """The conversation's cache, creating it on first use. Reaps idle
        conversations first, then LRU-caps the conversation count."""
        self._evict_idle()
        cache = self._caches.get(conversation_id)
        if cache is None:
            cache = ConversationUrlCache(
                max_entries=self._cache_max_entries,
                max_bytes=self._cache_max_bytes,
                ttl_seconds=self._cache_ttl,
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
            logger.info("url_cache.conversation_evicted", evicted_conversation_id=cid)

    def __len__(self) -> int:
        return len(self._caches)

    def __contains__(self, conversation_id: object) -> bool:
        return conversation_id in self._caches


# Process-wide registry, shared by every read_url call so a conversation's fetch
# cache survives across turns (single-worker posture; front with Redis to scale out).
_registry: UrlCacheRegistry = UrlCacheRegistry()


def default_url_cache_registry() -> UrlCacheRegistry:
    """The process-wide read_url fetch-cache registry (shared by every turn)."""
    return _registry
