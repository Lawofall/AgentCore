"""BrowserSessionRegistry — multi ``session_id`` browser sessions per conversation (M0).

Replaces the old ``conversation_id → 单会话`` map. One conversation may hold many live
entries; the process-wide primary key is ``session_id``. Callers that omit ``session_id``
resolve via run binding → unique/active session → (on acquire) get-or-create, so the
legacy single-session path stays testable.

Guarantees carried over from the L3 lifecycle:

- **lazy create**: a session opens on the first ``browser_*`` / explicit create, not before;
- **idle TTL** + **max lifetime** with lazy + reaper recycle;
- **concurrency gate** (``browser_max_sessions``): cap is on live *entries*, not conversations;
- **crash → rebuild**: a dead driver is dropped so the next call rebuilds;
- **cascade cleanup**: closing / deleting a conversation tears down all its sessions.

Session creation is injected as a ``factory`` so the lifecycle is unit-testable with fakes
(no gVisor); the default factory goes through the sandbox provider's session surface.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.runtime.browser.keyframes import KeyframeTracker
from agentcore.tools.sandbox.browser.protocol import (
    BrowserSession,
    BrowserSessionAcquireError,
    BrowserSessionRequest,
    BrowserSessionsBusyError,
)

logger = get_logger(__name__)

SessionFactory = Callable[[BrowserSessionRequest], Awaitable[BrowserSession]]
HostKind = Literal["sandbox", "local"]


@dataclass(frozen=True)
class TakeoverMark:
    """The active user-takeover pinned onto a session entry (M2 · D16/D17).

    The registry entry is the single in-memory source of truth for「is this session under
    user takeover」— the tools consult it (``user_in_control``) and every teardown path
    uses it to finalize the durable record. ``record_id`` links to the ``browser_takeovers``
    row so a drop can close it; ``started_at`` lets the endpoint reconstruct state.
    """

    record_id: str
    user_id: str
    started_at: datetime


# Called on every session teardown that still carries an un-ended takeover (reap / crash /
# shutdown / delete) so the durable record is completed on ALL paths (D17). Injected so the
# registry stays DB-agnostic and unit-testable; awaited inside ``_drop``.
TakeoverFinalizer = Callable[[TakeoverMark, str], Awaitable[None]]


class BrowserSessionObserver(Protocol):
    """Live-hub hooks the registry fires on session lifecycle (M1 · D13).

    Declared here (the registry is the caller) so the live hub implements it structurally
    without the registry importing the live module — and tests inject a fake. Callbacks MUST
    be sync + non-blocking (the hub only schedules work): they run inside registry ops.
    """

    def on_session_ready(self, conversation_id: str, session_id: str = "") -> None:
        """A fresh session for this conversation now exists (start screencast for viewers)."""
        ...

    def on_session_gone(self, conversation_id: str, session_id: str = "") -> None:
        """A session was dropped / recycled (tell viewers session_closed when relevant)."""
        ...

    def is_watched(self, conversation_id: str, session_id: str | None = None) -> bool:
        """True while ≥1 live viewer is attached — spares the session from idle reaping."""
        ...


@dataclass(frozen=True)
class BrowserSessionInfo:
    """Public summary of one live registry entry (list/create API + tools)."""

    session_id: str
    conversation_id: str
    host_kind: HostKind
    run_id: str | None
    created_at: float
    last_used: float
    control: Literal["agent", "user"]
    # L7 最小：最近一次导航的 url/title（Local Bridge / 用户地址栏回写）。
    url: str | None = None
    title: str | None = None


@dataclass
class _Entry:
    session_id: str
    conversation_id: str
    session: BrowserSession
    keyframes: KeyframeTracker = field(default_factory=KeyframeTracker)
    host_kind: HostKind = "sandbox"
    run_id: str | None = None
    # Set while a user is actively driving this session by hand (M2 接管); None otherwise.
    takeover: TakeoverMark | None = None
    url: str | None = None
    title: str | None = None

    def info(self) -> BrowserSessionInfo:
        return BrowserSessionInfo(
            session_id=self.session_id,
            conversation_id=self.conversation_id,
            host_kind=self.host_kind,
            run_id=self.run_id,
            created_at=self.session.created_at,
            last_used=self.session.last_used,
            control="user" if self.takeover is not None else "agent",
            url=self.url,
            title=self.title,
        )


async def _default_factory(request: BrowserSessionRequest) -> BrowserSession:
    """Open a browser session: Local Bridge (host_kind=local) or gVisor sandbox.

    When ``host_kind=local``, Bridge failure raises ``BrowserSessionError``
    with ``host_unavailable`` (no mid-session switch to sandbox). Assembly may
    choose ``host_kind=sandbox`` up-front for 过桥 without Bridge (C4 = no
    mixed host on one session).
    """
    host_kind = getattr(request, "host_kind", None) or "sandbox"
    if host_kind == "local":
        from agentcore.runtime.browser.local_session import open_local_bridge_session

        return await open_local_bridge_session(request)

    from agentcore.workspace.locate import _default_server_sandbox

    sandbox = _default_server_sandbox()
    supports = getattr(sandbox, "supports_browser_sessions", None)
    if supports is None or not supports():
        from agentcore.tools.sandbox.browser.protocol import BrowserSessionError

        raise BrowserSessionError("当前后端不支持云端浏览器会话（需要 gVisor 沙箱）")
    return await sandbox.open_browser_session(request)


def _new_session_id() -> str:
    return uuid.uuid4().hex


class BrowserSessionRegistry:
    """Process-wide ``session_id → browser session`` map (many per conversation)."""

    def __init__(
        self,
        *,
        factory: SessionFactory | None = None,
        max_sessions: int | None = None,
        idle_ttl_seconds: float | None = None,
        max_lifetime_seconds: float | None = None,
    ) -> None:
        self._factory = factory or _default_factory
        self._max_sessions = max_sessions
        self._idle_ttl = idle_ttl_seconds
        self._max_lifetime = max_lifetime_seconds
        # Primary key = session_id (single in-memory structure — no parallel registry).
        self._entries: dict[str, _Entry] = {}
        # conversation_id → ordered session_ids (creation order; active tracked separately).
        self._by_conversation: dict[str, list[str]] = {}
        # conversation_id → currently activated session_id (UI / default resolve).
        self._active: dict[str, str] = {}
        # Serialize create/acquire per conversation (parallel tool calls).
        self._locks: dict[str, asyncio.Lock] = {}
        # M1 live hub (D13): notified on create/drop, consulted for watch-based TTL sparing.
        self._observer: BrowserSessionObserver | None = None
        # M2 takeover (D17): completes the durable record on every session teardown that
        # still carries an un-ended takeover. Wired by the takeover service.
        self._takeover_finalizer: TakeoverFinalizer | None = None

    def set_observer(self, observer: BrowserSessionObserver | None) -> None:
        """Wire the live hub so this registry can announce sessions + spare watched ones."""
        self._observer = observer

    def set_takeover_finalizer(self, finalizer: TakeoverFinalizer | None) -> None:
        """Wire the takeover service so a dropped session's open record gets completed."""
        self._takeover_finalizer = finalizer

    # -- config (read live so a test / ops change is honored) -------------------
    @property
    def max_sessions(self) -> int:
        if self._max_sessions is not None:
            return self._max_sessions
        return int(settings.browser_max_sessions)

    @property
    def idle_ttl(self) -> float:
        if self._idle_ttl is not None:
            return self._idle_ttl
        return float(settings.browser_session_idle_ttl_seconds)

    @property
    def max_lifetime(self) -> float:
        return (
            self._max_lifetime
            if self._max_lifetime is not None
            else float(settings.browser_session_max_lifetime_seconds)
        )

    def _lock_for(self, conversation_id: str) -> asyncio.Lock:
        lock = self._locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[conversation_id] = lock
        return lock

    def _expired(self, entry: _Entry, now: float) -> bool:
        s = entry.session
        # Max lifetime always wins — even a watched (live-tab) session recycles past it so a
        # pinned tab cannot keep a ~1GB sandbox forever (D13).
        if (now - s.created_at) > self.max_lifetime:
            return True
        if (now - s.last_used) > self.idle_ttl:
            # Idle — but spare a session that has live viewers attached (open 直播 tab).
            watched = self._observer is not None and self._observer.is_watched(
                entry.conversation_id, entry.session_id
            )
            return not watched
        return False

    def _entry_live(self, session_id: str) -> _Entry | None:
        entry = self._entries.get(session_id)
        if entry is None:
            return None
        if not entry.session.alive or self._expired(entry, time.time()):
            return None
        return entry

    def _live_ids(self, conversation_id: str) -> list[str]:
        """Live (non-expired) session ids for a conversation, preserving creation order."""
        ids = self._by_conversation.get(conversation_id) or []
        return [sid for sid in ids if self._entry_live(sid) is not None]

    def resolve_session_id(
        self,
        conversation_id: str,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> str | None:
        """Resolve which live session a caller without an explicit id should use.

        Order: explicit ``session_id`` (if live + owned) → run-bound (when ``run_id``
        given; no fall-through) → unique live → active live. Returns None when nothing
        matches (caller may get-or-create).
        """
        if session_id:
            entry = self._entry_live(session_id)
            if entry is not None and entry.conversation_id == conversation_id:
                return session_id
            return None
        live_ids = self._live_ids(conversation_id)
        if not live_ids:
            return None
        if run_id:
            for sid in live_ids:
                entry = self._entries.get(sid)
                if entry is not None and entry.run_id == run_id:
                    return sid
            # Prefer an unbound unique/active tab (bind on acquire) over always creating.
            if len(live_ids) == 1:
                only = self._entries.get(live_ids[0])
                if only is not None and only.run_id is None:
                    return live_ids[0]
            active = self._active.get(conversation_id)
            if active and active in live_ids:
                act = self._entries.get(active)
                if act is not None and act.run_id is None:
                    return active
            return None
        if len(live_ids) == 1:
            return live_ids[0]
        active = self._active.get(conversation_id)
        if active and active in live_ids:
            return active
        return None

    def set_active(self, conversation_id: str, session_id: str) -> bool:
        """Mark ``session_id`` as the conversation's activated default (False if not live)."""
        entry = self._entry_live(session_id)
        if entry is None or entry.conversation_id != conversation_id:
            return False
        self._active[conversation_id] = session_id
        return True

    def bind_run(self, session_id: str, run_id: str) -> bool:
        """CAS-bind a live entry to ``run_id`` (ok if unbound or already this run)."""
        entry = self._entry_live(session_id)
        if entry is None:
            return False
        if entry.run_id is None:
            entry.run_id = run_id
            return True
        return entry.run_id == run_id

    def unbind_run(self, run_id: str) -> int:
        """Release ``run_id`` binds so a later worker can reuse the live session(s).

        Clears ``entry.run_id`` only — does **not** close sessions. Concurrent workers
        keep their own binds; sequential workers that omit ``session_id`` then resolve
        via unbound unique/active (see :meth:`resolve_session_id`). Returns how many
        entries were unbound.
        """
        rid = (run_id or "").strip()
        if not rid:
            return 0
        n = 0
        for entry in self._entries.values():
            if entry.run_id == rid:
                entry.run_id = None
                n += 1
        if n:
            logger.info("browser.registry_unbound_run", run_id=rid, unbound=n)
        return n

    def list_by_conversation(self, conversation_id: str) -> list[BrowserSessionInfo]:
        """Live entries for a conversation (creation order)."""
        return [
            self._entries[sid].info()
            for sid in self._live_ids(conversation_id)
            if sid in self._entries
        ]

    # -- M2 takeover state (D16/D17): the entry is the in-memory source of truth ----------
    def is_taken_over(
        self,
        conversation_id: str,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> bool:
        """True while the resolved live session is under user takeover."""
        return self.takeover_mark(conversation_id, session_id=session_id, run_id=run_id) is not None

    def takeover_mark(
        self,
        conversation_id: str,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> TakeoverMark | None:
        """The active takeover mark for the resolved session, or None."""
        sid = self.resolve_session_id(conversation_id, session_id=session_id, run_id=run_id)
        if sid is None:
            return None
        entry = self._entry_live(sid)
        return entry.takeover if entry is not None else None

    def begin_takeover(
        self,
        conversation_id: str,
        mark: TakeoverMark,
        *,
        session_id: str | None = None,
    ) -> bool:
        """Pin ``mark`` onto the resolved live session; False if none is live."""
        sid = self.resolve_session_id(conversation_id, session_id=session_id)
        if sid is None:
            return False
        entry = self._entry_live(sid)
        if entry is None:
            return False
        entry.takeover = mark
        logger.info(
            "browser.takeover_marked",
            conversation_id=conversation_id,
            session_id=sid,
        )
        return True

    def end_takeover(
        self,
        conversation_id: str,
        *,
        session_id: str | None = None,
    ) -> TakeoverMark | None:
        """Clear + return the active takeover mark (explicit end), or None if not marked.

        Clearing here BEFORE any drop ensures an explicit end never double-finalizes: a
        later teardown finds no mark and skips the finalizer.
        """
        sid = self.resolve_session_id(conversation_id, session_id=session_id)
        if sid is None:
            return None
        entry = self._entries.get(sid)
        if entry is None or entry.takeover is None:
            return None
        mark = entry.takeover
        entry.takeover = None
        return mark

    def peek(
        self,
        conversation_id: str,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> BrowserSession | None:
        """The resolved live session WITHOUT creating one (live view: no session ⇒ None)."""
        sid = self.resolve_session_id(conversation_id, session_id=session_id, run_id=run_id)
        if sid is None:
            return None
        entry = self._entry_live(sid)
        return entry.session if entry is not None else None

    def peek_entry(
        self,
        conversation_id: str,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> _Entry | None:
        """Like :meth:`peek` but returns the full entry (keyframes / takeover / ids)."""
        sid = self.resolve_session_id(conversation_id, session_id=session_id, run_id=run_id)
        if sid is None:
            return None
        return self._entry_live(sid)

    def update_nav(
        self,
        session_id: str,
        *,
        url: str | None = None,
        title: str | None = None,
    ) -> bool:
        """L7 最小：回写最近导航 url/title（工具 / Bridge / 用户地址栏）。"""
        entry = self._entries.get(session_id)
        if entry is None:
            return False
        if url is not None:
            entry.url = url or None
        if title is not None:
            entry.title = title or None
        return True

    def get(self, session_id: str) -> BrowserSession | None:
        """Lookup by primary key without creating."""
        entry = self._entry_live(session_id)
        return entry.session if entry is not None else None

    def conversation_of(self, session_id: str) -> str | None:
        """Owning conversation_id for a mapped session (including stale), or None."""
        entry = self._entries.get(session_id)
        return entry.conversation_id if entry is not None else None

    def _notify_ready(self, conversation_id: str, session_id: str) -> None:
        if self._observer is None:
            return
        try:
            self._observer.on_session_ready(conversation_id, session_id)
        except TypeError:
            # Older fakes / hubs that only accept conversation_id.
            try:
                self._observer.on_session_ready(conversation_id)  # type: ignore[call-arg]
            except Exception:  # noqa: BLE001
                logger.warning("browser.observer_ready_failed", conversation_id=conversation_id)
        except Exception:  # noqa: BLE001 - a hub hiccup must not break session creation
            logger.warning("browser.observer_ready_failed", conversation_id=conversation_id)

    def _notify_gone(self, conversation_id: str, session_id: str) -> None:
        if self._observer is None:
            return
        try:
            self._observer.on_session_gone(conversation_id, session_id)
        except TypeError:
            try:
                self._observer.on_session_gone(conversation_id)  # type: ignore[call-arg]
            except Exception:  # noqa: BLE001
                logger.warning("browser.observer_gone_failed", conversation_id=conversation_id)
        except Exception:  # noqa: BLE001 - a hub hiccup must not break teardown
            logger.warning("browser.observer_gone_failed", conversation_id=conversation_id)

    async def create(
        self,
        request: BrowserSessionRequest,
        *,
        session_id: str | None = None,
        host_kind: HostKind = "sandbox",
        run_id: str | None = None,
        activate: bool = True,
    ) -> tuple[BrowserSession, KeyframeTracker, str]:
        """Open a new session entry (always creates — does not reuse).

        Returns ``(session, keyframes, session_id)``. Raises
        :class:`BrowserSessionsBusyError` when the concurrency cap is reached.
        """
        cid = request.conversation_id
        async with self._lock_for(cid):
            await self._enforce_capacity()
            sid = session_id or _new_session_id()
            if sid in self._entries:
                # Stale/dead slot with same id — drop before recreate (tests / recovery).
                await self._drop(sid, reason="stale")
            # Stamp so Local Bridge pageId == Registry session_id.
            request.session_id = sid
            request.host_kind = host_kind
            session = await self._factory(request)
            entry = _Entry(
                session_id=sid,
                conversation_id=cid,
                session=session,
                host_kind=host_kind,
                run_id=run_id or getattr(request, "run_id", None),
            )
            self._entries[sid] = entry
            self._by_conversation.setdefault(cid, []).append(sid)
            if activate or cid not in self._active:
                self._active[cid] = sid
            logger.info(
                "browser.registry_created",
                conversation_id=cid,
                session_id=sid,
                host_kind=host_kind,
                live=len(self._entries),
            )
            self._notify_ready(cid, sid)
            return entry.session, entry.keyframes, sid

    def _require_host_kind(self, entry: _Entry, host_kind: HostKind) -> None:
        """Refuse acquire when the live entry is bound to a different host (C4 / M2)."""
        if entry.host_kind != host_kind:
            raise BrowserSessionAcquireError(
                f"session_bound_elsewhere: 浏览器会话已绑定 {entry.host_kind}，"
                f"无法以 {host_kind} 使用",
                code="session_bound_elsewhere",
            )

    async def acquire(
        self, request: BrowserSessionRequest
    ) -> tuple[BrowserSession, KeyframeTracker]:
        """Get a live session for the request (resolve or get-or-create), plus keyframes.

        Resolution (when ``request.session_id`` absent): run-bound → unique/active →
        create (+ bind ``run_id`` when provided). Raises
        :class:`BrowserSessionsBusyError` when the concurrency cap is reached;
        :class:`BrowserSessionAcquireError` for ``session_not_found`` /
        ``session_bound_elsewhere`` (host_kind 互斥 / 跨 run 抢绑).
        """
        cid = request.conversation_id
        want_sid = getattr(request, "session_id", None) or None
        run_id = getattr(request, "run_id", None) or None
        host_kind: HostKind = getattr(request, "host_kind", None) or "sandbox"

        # Fast path without the lock only when no unbound→bind is needed. Binding an
        # unbound entry under ``run_id`` must be serialized (or CAS'd) so two workers
        # cannot claim the same tab for different runs.
        sid = self.resolve_session_id(cid, session_id=want_sid, run_id=run_id)
        if sid is not None:
            entry = self._entry_live(sid)
            if entry is not None and not (run_id and entry.run_id is None):
                self._require_host_kind(entry, host_kind)
                if run_id and entry.run_id is not None and entry.run_id != run_id:
                    raise BrowserSessionAcquireError(
                        "session_bound_elsewhere: 浏览器会话已绑定其它 run",
                        code="session_bound_elsewhere",
                    )
                return entry.session, entry.keyframes

        async with self._lock_for(cid):
            # Explicit sid that is missing / dead / wrong conversation → session_not_found
            # (do not recreate under the same id).
            if want_sid:
                mapped = self._entries.get(want_sid)
                live = self._entry_live(want_sid)
                if mapped is not None and mapped.conversation_id == cid and live is None:
                    await self._drop(want_sid, reason="stale")
                    mapped = None
                if mapped is None or mapped.conversation_id != cid or live is None:
                    raise BrowserSessionAcquireError(
                        f"session_not_found: 浏览器会话不存在（{want_sid}）",
                        code="session_not_found",
                    )

            sid = self.resolve_session_id(cid, session_id=want_sid, run_id=run_id)
            if sid is not None:
                entry = self._entry_live(sid)
                if entry is not None:
                    self._require_host_kind(entry, host_kind)
                    if run_id and not self.bind_run(sid, run_id):
                        # Explicit sid already bound to another run — do not steal.
                        if want_sid:
                            raise BrowserSessionAcquireError(
                                "session_bound_elsewhere: 浏览器会话已绑定其它 run",
                                code="session_bound_elsewhere",
                            )
                        # No explicit sid — fall through to create a fresh session.
                        sid = None
                        entry = None
                    if entry is not None:
                        return entry.session, entry.keyframes
            # Drop dead/expired entries for this conversation before recreating.
            for stale_sid in list(self._by_conversation.get(cid) or []):
                if stale_sid in self._entries and self._entry_live(stale_sid) is None:
                    await self._drop(stale_sid, reason="stale")
            session, keyframes, _new_sid = await self._create_unlocked(
                request,
                session_id=None,
                host_kind=host_kind,
                run_id=run_id,
            )
            return session, keyframes

    async def _create_unlocked(
        self,
        request: BrowserSessionRequest,
        *,
        session_id: str | None,
        host_kind: HostKind,
        run_id: str | None,
    ) -> tuple[BrowserSession, KeyframeTracker, str]:
        """Create under an already-held conversation lock (used by acquire)."""
        cid = request.conversation_id
        await self._enforce_capacity()
        sid = session_id or _new_session_id()
        request.session_id = sid
        request.host_kind = host_kind
        session = await self._factory(request)
        entry = _Entry(
            session_id=sid,
            conversation_id=cid,
            session=session,
            host_kind=host_kind,
            run_id=run_id,
        )
        self._entries[sid] = entry
        bucket = self._by_conversation.setdefault(cid, [])
        if sid not in bucket:
            bucket.append(sid)
        if cid not in self._active:
            self._active[cid] = sid
        logger.info(
            "browser.registry_created",
            conversation_id=cid,
            session_id=sid,
            host_kind=host_kind,
            live=len(self._entries),
        )
        self._notify_ready(cid, sid)
        return entry.session, entry.keyframes, sid

    async def _enforce_capacity(self) -> None:
        if len(self._entries) < self.max_sessions:
            return
        await self.reap()
        if len(self._entries) >= self.max_sessions:
            raise BrowserSessionsBusyError(
                f"云端浏览器会话已满（并发上限 {self.max_sessions}）。"
                "请稍后重试，或结束其它对话的浏览器会话后再试。"
            )

    def has_live_sandbox_on_desk(self, container_id: str) -> bool:
        """True when a live sandbox browser (including a watched live tab) is on this guest."""
        wanted = str(container_id)
        for entry in self._entries.values():
            if entry.host_kind != "sandbox":
                continue
            if not entry.session.alive:
                continue
            if getattr(entry.session, "desk_container_id", None) == wanted:
                return True
        return False

    async def close_sandbox_sessions_on_desk(self, container_id: str) -> None:
        """Tear down sandbox browsers on this guest. Local Bridge sessions are untouched."""
        wanted = str(container_id)
        stale = [
            sid
            for sid, entry in self._entries.items()
            if entry.host_kind == "sandbox"
            and getattr(entry.session, "desk_container_id", None) == wanted
        ]
        for sid in stale:
            await self._drop(sid, reason="desk_reaped")

    async def reap(self) -> int:
        """Close idle / over-lifetime / dead sessions. Returns how many were closed.

        Called lazily by the capacity gate and periodically by the lifespan loop.
        """
        now = time.time()
        stale = [
            sid
            for sid, entry in self._entries.items()
            if (not entry.session.alive) or self._expired(entry, now)
        ]
        for sid in stale:
            await self._drop(sid, reason="reaped")
        return len(stale)

    async def close_session(self, session_id: str) -> None:
        """Tear down one session by primary key."""
        if session_id in self._entries:
            await self._drop(session_id, reason="closed")

    async def close(self, conversation_id: str) -> None:
        """Cascade cleanup for one conversation (deletion / explicit close of all tabs)."""
        for sid in list(self._by_conversation.get(conversation_id) or []):
            await self._drop(sid, reason="closed")
        self._by_conversation.pop(conversation_id, None)
        self._active.pop(conversation_id, None)
        self._locks.pop(conversation_id, None)

    async def close_all(self, *, timeout: float | None = None) -> None:
        """Tear down every live session under a wall-clock cap (lifespan shutdown).

        Bookkeeping is instant; session ``close()`` (runsc kill/delete) runs in
        parallel. The bound is the whole gather — not runsc's 180s per-command
        wait, and not one session after another. Timed-out sandboxes are left
        for process exit / the reaper.
        """
        items = [(sid, entry) for sid, entry in list(self._entries.items())]
        self._entries.clear()
        self._by_conversation.clear()
        self._active.clear()
        self._locks.clear()
        if not items:
            return
        grace = (
            float(timeout)
            if timeout is not None
            else float(settings.browser_shutdown_close_all_seconds)
        )
        tasks = [
            asyncio.create_task(self._teardown_entry(sid, entry, reason="shutdown"))
            for sid, entry in items
        ]
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=grace,
            )
        except TimeoutError:
            logger.warning(
                "browser.close_all_timeout",
                session_count=len(items),
                timeout_seconds=grace,
            )
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _teardown_entry(self, session_id: str, entry: _Entry, *, reason: str) -> None:
        cid = entry.conversation_id
        if entry.takeover is not None:
            await self._finalize_takeover(entry.takeover, reason)
            entry.takeover = None
        try:
            await entry.session.close()
        except Exception:  # noqa: BLE001 - teardown is best-effort
            logger.warning(
                "browser.registry_close_failed",
                conversation_id=cid,
                session_id=session_id,
            )
        logger.info(
            "browser.registry_dropped",
            conversation_id=cid,
            session_id=session_id,
            reason=reason,
        )
        self._notify_gone(cid, session_id)

    async def _drop(self, session_id: str, *, reason: str) -> None:
        entry = self._entries.pop(session_id, None)
        if entry is None:
            return
        cid = entry.conversation_id
        bucket = self._by_conversation.get(cid)
        if bucket is not None:
            with contextlib.suppress(ValueError):
                bucket.remove(session_id)
            if not bucket:
                self._by_conversation.pop(cid, None)
        if self._active.get(cid) == session_id:
            remaining = self._by_conversation.get(cid) or []
            if remaining:
                self._active[cid] = remaining[-1]
            else:
                self._active.pop(cid, None)
        await self._teardown_entry(session_id, entry, reason=reason)
        # If another tab remains (and became active), announce ready so live viewers can
        # re-attach screencast to the new default.
        new_active = self._active.get(cid)
        if new_active and self._entry_live(new_active) is not None:
            self._notify_ready(cid, new_active)

    async def _finalize_takeover(self, mark: TakeoverMark, reason: str) -> None:
        finalizer = self._takeover_finalizer
        if finalizer is None:
            return
        try:
            await finalizer(mark, reason)
        except Exception:  # noqa: BLE001 - a留档 write failure must not break teardown
            logger.warning("browser.takeover_finalize_failed", record_id=mark.record_id)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        """True for a live ``session_id``, or a conversation that still has ≥1 entry."""
        if not isinstance(key, str):
            return False
        if key in self._entries:
            return True
        return key in self._by_conversation and bool(self._by_conversation[key])


_registry: BrowserSessionRegistry | None = None


def default_browser_session_registry() -> BrowserSessionRegistry:
    """The process-wide browser session registry (shared by tools + the reaper)."""
    global _registry
    if _registry is None:
        _registry = BrowserSessionRegistry()
    return _registry


async def browser_reaper_loop() -> None:
    """Background sweep: idle browser sessions, then idle cloud-desk guests.

    Session reap and desk reap share this loop's cadence; they are different
    objects (a browser close never freeze/pauses the desk). Local Bridge /
    sidecar never populate the desk map, so the desk sweep is a no-op there.
    """
    interval = float(settings.browser_reaper_interval_seconds)
    registry = default_browser_session_registry()
    while True:
        await asyncio.sleep(interval)
        try:
            closed = await registry.reap()
            if closed:
                logger.info("browser.reaper_swept", closed=closed, live=len(registry))
        except Exception:  # noqa: BLE001 - a sweep failure must not kill the loop
            logger.warning("browser.reaper_error")
        try:
            from agentcore.tools.sandbox.gvisor import reap_idle_desks

            closed_desks = await reap_idle_desks()
            if closed_desks:
                logger.info("sandbox.desk_reaper_swept", closed=closed_desks)
        except Exception:  # noqa: BLE001 - a desk sweep failure must not kill the loop
            logger.warning("sandbox.desk_reaper_error")


async def shutdown_browser_sessions() -> None:
    """Close every live session + the shared proxy (lifespan shutdown)."""
    if _registry is not None:
        await _registry.close_all()
    from agentcore.tools.sandbox.browser.proxy import shutdown_browser_proxy

    await shutdown_browser_proxy()
