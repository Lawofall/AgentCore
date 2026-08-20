"""留人 roster — the live store of recoverable worker runs, keyed by run, scoped
by conversation (乙 带现场续派).

A worker's :class:`~agentcore.runtime.runs.session.RunSession` lives here so the CEO
can 带现场续派 (delegate ``continue_from_run_id``) it later: the ``delegate`` tool
registers each COMPLETED worker as soon as it finishes, and a continuation task looks
one up by ``run_id`` to continue it on its own transcript.

Two layers (P2 治理, 见 docs/03-AI核心/多轮编排与同人续派.md §四):

* :class:`SessionStore` — ONE conversation's roster. Bounded so an active
  conversation can't grow memory without limit: a per-session idle **TTL**, a
  **count** cap, and a transcript **byte** cap (the latter two LRU-evict). Eviction
  is lazy (on access) — no sweeper.
* :class:`SessionRegistry` — the process-wide ``conversation_id → SessionStore`` map
  so a roster **survives across turns** (跨回合「改下刚才那个」). Bounded by a
  conversation count cap (LRU) and an idle TTL ("conversation 超时清"). Exposed as a
  module singleton via :func:`default_session_registry`, mirroring the in-process
  single-worker posture of approvals / channel / locks (front with Redis to scale
  out). A miss / expiry at either layer surfaces as ``get → None`` → 回落甲.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from agentcore.core.logging import get_logger
from agentcore.runtime.runs.constants import (
    DEFAULT_ROSTER_MAX_BYTES,
    DEFAULT_ROSTER_MAX_CONVERSATIONS,
    DEFAULT_ROSTER_MAX_SESSIONS,
    DEFAULT_ROSTER_TTL_SECONDS,
)
from agentcore.runtime.runs.session import RunSession

logger = get_logger(__name__)

# Durable-roster persistence callbacks (留人 跨进程落盘 P3), implemented by the
# DB-aware caller (conversation/turn_runner.py) / Sidecar local file store and
# plumbed through the pipeline to delegate / revise so the tools and pipeline stay
# storage-unaware. ``SessionSaver`` write-throughs a finished / revised session
# (pipeline wraps it in ``SessionRosterWriter``: schedule on the hot path, flush at
# turn end); ``SessionLoader`` rehydrates one by run_id on an in-memory roster miss.
# Both optional — absent ⇒ in-memory-only (P2).
SessionSaver = Callable[[RunSession], Awaitable[None]]
SessionLoader = Callable[[str], Awaitable[RunSession | None]]

# Sync hook invoked immediately before an LRU drop (so callers can schedule
# write-through before the in-memory object disappears). Optional.
EvictPersist = Callable[[RunSession], None]

# Cap of recently-evicted run_ids remembered for continuation cause diagnosis.
_RECENT_EVICT_CAP = 64


def _session_bytes(session: RunSession) -> int:
    """Approximate in-memory weight of a session = its transcript text length.

    The transcript dominates (spec / content are tiny); used only for the byte cap,
    so a character count is a good-enough proxy without serializing anything."""
    return sum(len(m.content or "") for m in session.transcript)


class SessionStore:
    """One conversation's bounded, LRU-ordered roster of recoverable worker runs.

    Keyed by ``run_id``. Bounded three ways (P2 治理): a per-session idle **TTL**
    (a run not revised within the window expires → 定向唤回 misses → 回落甲), a
    **count** cap, and a transcript **byte** cap (both evict the least-recently-used
    run). All eviction is lazy (on ``put`` / ``get``); ``last_access`` lets the
    registry reap an idle conversation's whole roster.
    """

    def __init__(
        self,
        *,
        max_sessions: int = DEFAULT_ROSTER_MAX_SESSIONS,
        max_bytes: int = DEFAULT_ROSTER_MAX_BYTES,
        ttl_seconds: float = DEFAULT_ROSTER_TTL_SECONDS,
    ) -> None:
        self._sessions: OrderedDict[str, RunSession] = OrderedDict()
        # 星型存 · 链式渲：图上续派链末端 id → 现场根 run_id（不另开 session）。
        self._aliases: dict[str, str] = {}
        self._max_sessions = max_sessions
        self._max_bytes = max_bytes
        self._ttl = ttl_seconds
        self.last_access: float = time.time()
        # When True, byte/count LRU may drop megas (disk/file loader can rehydrate).
        # When False, prefer keeping large continuable sessions over the byte cap.
        self._durable: bool = False
        self._evict_persist: EvictPersist | None = None
        # run_id → eviction reason (bytes|count), for continuation cause diagnosis.
        self._recently_evicted: OrderedDict[str, str] = OrderedDict()

    def bind_evict_persist(
        self,
        persist: EvictPersist | None,
        *,
        durable: bool | None = None,
    ) -> None:
        """Wire per-turn write-through + durable flag (pipeline calls each turn).

        ``persist`` is invoked synchronously just before an LRU drop so the
        SessionRosterWriter can schedule a save. ``durable`` defaults to
        ``persist is not None``.
        """
        self._evict_persist = persist
        self._durable = bool(persist is not None) if durable is None else durable

    def eviction_reason(self, run_id: str) -> str | None:
        """Return ``bytes`` / ``count`` if ``run_id`` was recently LRU-evicted."""
        return self._recently_evicted.get((run_id or "").strip())

    def put(self, session: RunSession) -> None:
        """Register / refresh a run's recoverable session (most-recently-used), then
        enforce the TTL + count + byte caps."""
        self.last_access = time.time()
        # A re-put clears a prior eviction mark (session is live again).
        self._recently_evicted.pop(session.run_id, None)
        self._sessions[session.run_id] = session
        self._sessions.move_to_end(session.run_id)
        self._prune()

    def link_alias(self, alias_run_id: str, root_run_id: str) -> None:
        """Map a continuation-node id onto the session root (星型存).

        CEO / UI often pass the chain tip from the graph; lookups resolve through
        this alias to the same :class:`RunSession` keyed by ``root_run_id``.
        """
        alias = (alias_run_id or "").strip()
        root = (root_run_id or "").strip()
        if not alias or not root or alias == root:
            return
        self.last_access = time.time()
        self._aliases[alias] = root

    def root_for_alias(self, run_id: str) -> str | None:
        """Return the session-root id if ``run_id`` is a known chain-tip alias."""
        return self._aliases.get((run_id or "").strip())

    def get(self, run_id: str) -> RunSession | None:
        """The live session for ``run_id`` (refreshing its recency), or ``None`` when
        it is absent or has expired — both → 回落甲 at the call site.

        Also accepts a continuation-chain tip previously registered via
        :meth:`link_alias` (resolves to the session root).
        """
        self.last_access = time.time()
        key = run_id
        if key not in self._sessions:
            root = self._aliases.get(key)
            if root is not None:
                key = root
        session = self._sessions.get(key)
        if session is None:
            return None
        if self._is_expired(session):
            self._drop_session(key, reason="ttl", note_evict=False)
            logger.info("roster.session_expired", run_id=key)
            return None
        self._sessions.move_to_end(key)
        return session

    def is_idle(self, ttl_seconds: float) -> bool:
        """Whether this roster has not been touched within ``ttl_seconds`` (the
        registry uses it to drop a conversation's whole roster)."""
        return (time.time() - self.last_access) > ttl_seconds

    def _is_expired(self, session: RunSession) -> bool:
        return (time.time() - session.updated_at) > self._ttl

    def _note_evicted(self, run_id: str, reason: str) -> None:
        self._recently_evicted[run_id] = reason
        self._recently_evicted.move_to_end(run_id)
        while len(self._recently_evicted) > _RECENT_EVICT_CAP:
            self._recently_evicted.popitem(last=False)

    def _drop_session(
        self,
        run_id: str,
        *,
        reason: str,
        note_evict: bool = True,
    ) -> None:
        session = self._sessions.pop(run_id, None)
        dead = [a for a, root in self._aliases.items() if root == run_id or a == run_id]
        for a in dead:
            del self._aliases[a]
        if session is None:
            return
        if note_evict and reason in ("bytes", "count"):
            # Persist-before-evict: schedule durable write while we still hold the object.
            if self._evict_persist is not None:
                try:
                    self._evict_persist(session)
                except Exception as e:  # noqa: BLE001 — never break the turn on persist
                    logger.warning(
                        "roster.evict_persist_failed",
                        run_id=run_id,
                        reason=reason,
                        error=str(e),
                    )
            nbytes = _session_bytes(session)
            self._note_evicted(run_id, reason)
            logger.info(
                "roster.session_evicted",
                run_id=run_id,
                reason=reason,
                bytes=nbytes,
                total_bytes=self._total_bytes(),
                max_bytes=self._max_bytes,
                n_sessions=len(self._sessions),
            )

    def _pick_byte_eviction_victim(self) -> str | None:
        """Choose a non-MRU victim for the byte cap.

        With durable persistence: classic LRU (oldest first).
        Without: prefer dropping smaller sessions so a continuable mega is kept
        even when the roster is temporarily over the byte cap.
        """
        if len(self._sessions) <= 1:
            return None
        items = list(self._sessions.items())
        non_mru = items[:-1]
        if self._durable:
            return non_mru[0][0]
        mega_floor = max(1, self._max_bytes // 2)
        small = [rid for rid, s in non_mru if _session_bytes(s) < mega_floor]
        if small:
            return small[0]
        # No durable backend and every non-MRU is a mega → keep them (tolerate over-cap).
        return None

    def _prune(self) -> None:
        """Drop expired sessions, then LRU-evict until within the count and byte caps.

        Byte eviction persists-before-drop when a hook is bound; without durable
        persistence, megas (≥ half the byte cap) are protected over the cap.
        """
        now = time.time()
        expired = [rid for rid, s in self._sessions.items() if (now - s.updated_at) > self._ttl]
        for rid in expired:
            self._drop_session(rid, reason="ttl", note_evict=False)
            logger.info("roster.session_expired", run_id=rid)
        while len(self._sessions) > self._max_sessions:
            oldest, _ = next(iter(self._sessions.items()))
            self._drop_session(oldest, reason="count")
        while self._total_bytes() > self._max_bytes and len(self._sessions) > 1:
            victim = self._pick_byte_eviction_victim()
            if victim is None:
                break
            self._drop_session(victim, reason="bytes")

    def _total_bytes(self) -> int:
        return sum(_session_bytes(s) for s in self._sessions.values())

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, run_id: object) -> bool:
        return run_id in self._sessions

    def list_sessions(self) -> list[RunSession]:
        """当前未过期的可续写 session 快照（开赛证人探测等只读遍历用）。"""
        self.last_access = time.time()
        live: list[RunSession] = []
        expired: list[str] = []
        for rid, session in self._sessions.items():
            if self._is_expired(session):
                expired.append(rid)
            else:
                live.append(session)
        for rid in expired:
            self._drop_session(rid, reason="ttl", note_evict=False)
            logger.info("roster.session_expired", run_id=rid)
        return live


class SessionRegistry:
    """Process-wide ``conversation_id → SessionStore`` map (留人 跨回合).

    A conversation's roster survives across turns so a later turn's ``revise`` can
    recall an earlier turn's worker (P2 作用域：turn 结束不清). Bounded by a
    conversation **count** cap (LRU-evict the least-recently-used conversation) and
    an idle **TTL** (a conversation untouched within the window is dropped —
    "conversation 超时清", then a fresh empty roster is created → 回落甲). Same
    single-worker in-process posture as approvals / channel / locks.
    """

    def __init__(
        self,
        *,
        max_conversations: int = DEFAULT_ROSTER_MAX_CONVERSATIONS,
        conversation_ttl_seconds: float = DEFAULT_ROSTER_TTL_SECONDS,
        store_max_sessions: int = DEFAULT_ROSTER_MAX_SESSIONS,
        store_max_bytes: int = DEFAULT_ROSTER_MAX_BYTES,
        store_ttl_seconds: float = DEFAULT_ROSTER_TTL_SECONDS,
    ) -> None:
        self._stores: OrderedDict[str, SessionStore] = OrderedDict()
        self._max_conversations = max_conversations
        self._conversation_ttl = conversation_ttl_seconds
        self._store_max_sessions = store_max_sessions
        self._store_max_bytes = store_max_bytes
        self._store_ttl = store_ttl_seconds

    def get_or_create(self, conversation_id: str) -> SessionStore:
        """The conversation's roster, creating it on first use. Reaps idle
        conversations first, then LRU-caps the conversation count."""
        self._evict_idle()
        store = self._stores.get(conversation_id)
        if store is None:
            store = SessionStore(
                max_sessions=self._store_max_sessions,
                max_bytes=self._store_max_bytes,
                ttl_seconds=self._store_ttl,
            )
            self._stores[conversation_id] = store
        self._stores.move_to_end(conversation_id)
        while len(self._stores) > self._max_conversations:
            self._stores.popitem(last=False)
        return store

    def _evict_idle(self) -> None:
        idle = [cid for cid, s in self._stores.items() if s.is_idle(self._conversation_ttl)]
        for cid in idle:
            del self._stores[cid]
            # Victim id is not canonical conversation_id: this runs in the caller's
            # request context, and merge_contextvars would mix user_id / trace_id.
            logger.info("roster.conversation_evicted", evicted_conversation_id=cid)

    def __len__(self) -> int:
        return len(self._stores)

    def __contains__(self, conversation_id: object) -> bool:
        return conversation_id in self._stores


# Process-wide roster registry, shared by every turn's pipeline so a conversation's
# 留人 survives across turns (single-worker posture; front with Redis to scale out).
_registry: SessionRegistry = SessionRegistry()


def default_session_registry() -> SessionRegistry:
    """The process-wide roster registry (shared by every turn's pipeline)."""
    return _registry
