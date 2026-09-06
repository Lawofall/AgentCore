"""In-process hub for the device-level CLIENT_TOOL fulfillment channel.

Unlike the conversation EventSink (who is watching the turn) and the IM
``ChatHub`` (user-level notify firehose), this hub answers: which *online
device* of this user can execute a given channel op against a given root, and
delivers the request frame to that device's SSE subscription.

Multi-device is first-class: each ``(user_id, device_id)`` holds at most one
live :class:`FulfillerSession`. Selection takes sessions whose ``caps`` include
the channel and whose ``roots`` hold the ``root_id`` (any capable session when
``root_id`` is ``None``), then prefers the device that started the turn
(``fulfill/origin.py``) and falls back to the most recently registered one.

For ops that act on the fulfilling machine itself — running commands, touching
disk, mounting directories — that preference is a hard constraint
(:data:`ORIGIN_PINNED_CHANNELS`): with the origin device gone, callers must fail
honestly rather than run the user's shell command on a second install.

Backpressure: fulfillment frames must not be silently shed. A full queue marks
the session unhealthy — it is closed so the client reconnects (and is removed
from the hub). Contrast ``ChatHub``, which drops the oldest undelivered event.

Departures are remembered for a short while (:meth:`FulfillerHub.seen_recently`):
a desktop's SSE drops and re-opens many times a day, and during that gap the hub
is honestly empty even though the machine never went anywhere. Delivery uses the
memory to tell "reconnecting" from "no client at all" — never to route an op.

Not every session is a fulfiller. The same stream carries account-owned state to
every install (``fulfill/user_signal.py``), and a browser client that can execute
nothing still wants it, so it connects declaring no caps. Selection and presence
both skip such a session (:attr:`FulfillerSession.can_fulfil`): being connected
is not the same fact as being able to run the user's shell command.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any

from agentcore.core.logging import get_logger

logger = get_logger(__name__)

# Channels a fulfiller may advertise via the ``caps`` query param.
FULFILL_CHANNELS: frozenset[str] = frozenset(
    {
        "workspace",
        "host",
        "mcp",
        "board",
        "board_read",
        "external_mount",
    }
)

# Channels whose ops execute against the fulfilling machine: shell / file IO
# (workspace), OS affordances (host), locally spawned stdio servers (mcp), and
# reading a directory off that disk (external_mount). Landing one of these on a
# device the user is not sitting at is a wrong answer, not a degraded one — so
# when the turn's origin device is known these pin to it.
#
# The rest are display surfaces: a board batch belongs to whichever
# install has the canvas open.
ORIGIN_PINNED_CHANNELS: frozenset[str] = frozenset(
    {
        "workspace",
        "host",
        "mcp",
        "external_mount",
    }
)


def origin_pinned(channel: str, *, root_id: str | None) -> bool:
    """True when this op must reach the turn's origin device or fail.

    A ``root_id`` already names one authorized directory on one install, so
    rooted ops keep their existing root-based location untouched; pinning only
    replaces the "any capable device" fallback taken when ``root_id`` is absent.
    """
    return root_id is None and channel in ORIGIN_PINNED_CHANNELS


# A stalled fulfiller's queue is capped here; once full the session is closed
# (never drop-oldest). Sized above typical concurrent CLIENT_TOOL in-flight so
# only a genuinely stuck client trips the health gate.
_FULFILLER_QUEUE_MAXSIZE = 128

# How long after a disconnect a device still counts as "just here". Production
# desktops re-open the fulfill SSE in 1–4s (tail into the teens), so a minute is
# several times the observed worst case while a machine that has been away
# longer reads as plainly absent — which is what fail-fast is for.
RECENT_PRESENCE_SECONDS = 60.0

# Departure marks are only ever read inside the window above; prune once the map
# grows past this so a long-lived process cannot accumulate dead devices.
_LAST_SEEN_PRUNE_THRESHOLD = 512


@dataclass
class FulfillerIdentity:
    """Stable connection identity declared at SSE open (plus mutable roots)."""

    user_id: str
    device_id: str
    platform: str | None
    caps: frozenset[str]
    roots: set[str] = field(default_factory=set)


class FulfillerSession:
    """One live ``GET /v1/fulfill`` connection's event queue + declared caps/roots.

    Drive it from the SSE route via :meth:`get` (or async iteration). It ends when
    the hub delivers the ``None`` sentinel (:meth:`close`). On client disconnect
    the route unregisters it from the hub.
    """

    def __init__(self, identity: FulfillerIdentity, *, registered_at: float) -> None:
        self.identity = identity
        self.registered_at = registered_at
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=_FULFILLER_QUEUE_MAXSIZE
        )
        self._closed = False

    @property
    def user_id(self) -> str:
        return self.identity.user_id

    @property
    def device_id(self) -> str:
        return self.identity.device_id

    @property
    def platform(self) -> str | None:
        return self.identity.platform

    @property
    def caps(self) -> frozenset[str]:
        return self.identity.caps

    @property
    def roots(self) -> set[str]:
        return self.identity.roots

    @property
    def can_fulfil(self) -> bool:
        """Whether this connection can execute anything (declared ≥1 channel).

        The browser client opens the same stream to read the account state it
        also carries — its queue, its settled cards — and declares no channels
        at all. :meth:`FulfillerHub.find` already skips it, being cap-filtered;
        this is what keeps the *presence* answers honest too, so an account
        whose only open client is a web tab is not read as a machine that is
        about to come back.
        """
        return bool(self.identity.caps)

    def add_root(self, root_id: str) -> None:
        """Widen the declared root set by one id (a registration receipt landed)."""
        rid = (root_id or "").strip()
        if rid:
            self.identity.roots.add(rid)

    def offer(self, event: dict[str, Any]) -> bool:
        """Enqueue ``event``. Returns ``False`` when the queue is full (unhealthy).

        Unlike the IM firehose, a full queue must not shed the oldest frame —
        callers close the session instead.
        """
        if self._closed:
            return False
        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            return False

    def close(self) -> None:
        """Signal end-of-stream (drain backlog, then sentinel). Idempotent."""
        if self._closed:
            return
        self._closed = True
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)

    async def get(self) -> dict[str, Any] | None:
        """Await the next event, or ``None`` once the stream is closed."""
        return await self._queue.get()

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event


class FulfillerHub:
    """Process-wide registry of live fulfillment sessions + query/deliver helpers."""

    def __init__(self) -> None:
        # user_id → live sessions (multi-device).
        self._by_user: dict[str, set[FulfillerSession]] = {}
        # (user_id, device_id) → session (at most one live connection per device).
        self._by_device: dict[tuple[str, str], FulfillerSession] = {}
        # (user_id, device_id) → monotonic time of the last disconnect.
        self._last_seen: dict[tuple[str, str], float] = {}

    def register(
        self,
        user_id: str,
        device_id: str,
        *,
        caps: Iterable[str],
        roots: Iterable[str] | None = None,
        platform: str | None = None,
    ) -> FulfillerSession:
        """Register (or replace) a live fulfillment connection for ``device_id``.

        Reconnecting the same device closes the previous session first so caps /
        roots always reflect the newest subscription.
        """
        key = (user_id, device_id)
        existing = self._by_device.get(key)
        if existing is not None:
            self.unregister(existing)

        cap_set = frozenset(c for c in caps if c in FULFILL_CHANNELS)
        identity = FulfillerIdentity(
            user_id=user_id,
            device_id=device_id,
            platform=platform,
            caps=cap_set,
            roots={r for r in (roots or ()) if r},
        )
        session = FulfillerSession(identity, registered_at=time.monotonic())
        self._by_user.setdefault(user_id, set()).add(session)
        self._by_device[key] = session
        logger.info(
            "fulfill.register",
            user=user_id,
            device=device_id,
            platform=platform,
            caps=sorted(cap_set),
            roots=len(identity.roots),
            devices=len(self._by_user.get(user_id, ())),
        )
        return session

    def unregister(self, session: FulfillerSession) -> None:
        """Remove a connection (on disconnect / unhealthy close). Idempotent."""
        key = (session.user_id, session.device_id)
        if self._by_device.get(key) is session:
            self._by_device.pop(key, None)
        conns = self._by_user.get(session.user_id)
        if conns is not None:
            conns.discard(session)
            if not conns:
                self._by_user.pop(session.user_id, None)
        session.close()
        if session.can_fulfil:
            now = time.monotonic()
            self._last_seen[key] = now
            self._prune_last_seen(now)
        logger.info(
            "fulfill.unregister",
            user=session.user_id,
            device=session.device_id,
        )

    def _prune_last_seen(self, now: float) -> None:
        """Drop departure marks nobody can still read (bounded memory)."""
        if len(self._last_seen) <= _LAST_SEEN_PRUNE_THRESHOLD:
            return
        cutoff = now - RECENT_PRESENCE_SECONDS
        self._last_seen = {k: ts for k, ts in self._last_seen.items() if ts > cutoff}

    def seen_recently(
        self,
        user_id: str,
        *,
        device_id: str | None = None,
        within: float | None = None,
    ) -> bool:
        """True when this device (or any of the user's) was connected just now.

        Online counts as seen; otherwise the last disconnect must be no older
        than ``within`` (default :data:`RECENT_PRESENCE_SECONDS`). This answers
        "is a client reconnecting right now?", nothing else — a caller that got
        ``None`` from :meth:`find` still has no one to hand the op to.

        Only sessions that could fulfil something count (:attr:`
        FulfillerSession.can_fulfil`). An observer — the browser client, here for
        the account state alone — is a real connection that is nonetheless not a
        machine coming back, and answering "yes" for it would park every local op
        of a desktop-less account for the whole grace before the same failure.
        """
        limit = RECENT_PRESENCE_SECONDS if within is None else within
        now = time.monotonic()
        if device_id is not None:
            live = self._by_device.get((user_id, device_id))
            if live is not None and live.can_fulfil:
                return True
            seen = self._last_seen.get((user_id, device_id))
            return seen is not None and (now - seen) <= limit
        if any(s.can_fulfil for s in self._by_user.get(user_id, ())):
            return True
        return any(
            (now - seen) <= limit
            for (uid, _device), seen in self._last_seen.items()
            if uid == user_id
        )

    def declare_root(self, user_id: str, device_id: str, root_id: str) -> bool:
        """Bind one root to a live session. Returns ``False`` when not online.

        Called from the registration receipts that mint a root (external grant,
        workspace bind): the response only means「这台设备能履约这个根」if the hub
        learned it in the same request, before the turn issues its first op.
        """
        session = self._by_device.get((user_id, device_id))
        if session is None:
            return False
        session.add_root(root_id)
        logger.info(
            "fulfill.root_declared",
            user=user_id,
            device=device_id,
            root_id=root_id,
            roots=len(session.roots),
        )
        return True

    def broadcast(self, user_id: str, event: dict[str, Any]) -> int:
        """Push ``event`` to every live session of ``user_id``. Returns delivery count.

        For state a *user* owns rather than a turn (their queue, their pending
        cards): every online install of the account gets the same frame, and each
        decides what to do with it. Unhealthy sessions are closed by
        :meth:`deliver` exactly as on the routed path.
        """
        delivered = 0
        for session in tuple(self._by_user.get(user_id, ())):
            if self.deliver(session, event):
                delivered += 1
        return delivered

    def get_session(self, user_id: str, device_id: str) -> FulfillerSession | None:
        """Return the live session for ``(user_id, device_id)``, if any."""
        return self._by_device.get((user_id, device_id))

    def find(
        self,
        user_id: str,
        *,
        root_id: str | None,
        channel: str,
        origin_device_id: str | None = None,
        require_origin: bool = False,
    ) -> FulfillerSession | None:
        """Pick the best online fulfiller for ``channel`` (+ optional ``root_id``).

        Rules: caps contain ``channel`` → roots contain ``root_id`` (any capable
        session when ``root_id`` is ``None``) → the turn's ``origin_device_id`` →
        most recently registered.

        ``require_origin`` turns that preference into a constraint: when the
        origin device is not among the candidates, return ``None`` instead of
        handing the op to a peer. It is inert while ``origin_device_id`` is
        ``None`` (unknown origin ⇒ the caller cannot be pinned to anything).
        """
        sessions = self._by_user.get(user_id)
        if not sessions:
            return None
        candidates = [
            s
            for s in sessions
            if channel in s.caps and (root_id is None or root_id in s.roots)
        ]
        if not candidates:
            return None
        if origin_device_id:
            origin = next(
                (s for s in candidates if s.device_id == origin_device_id), None
            )
            if origin is not None:
                return origin
            if require_origin:
                return None
        return max(candidates, key=lambda s: s.registered_at)

    def has_fulfiller(
        self,
        user_id: str,
        *,
        root_id: str | None,
        channel: str,
        origin_device_id: str | None = None,
        require_origin: bool = False,
    ) -> bool:
        """True when :meth:`find` would return a live session (same arguments).

        Presence gates must pass the selection arguments delivery will use, or a
        gate that let the turn through is followed by a delivery that reports no
        machine to run on.
        """
        return (
            self.find(
                user_id,
                root_id=root_id,
                channel=channel,
                origin_device_id=origin_device_id,
                require_origin=require_origin,
            )
            is not None
        )

    def deliver(self, session: FulfillerSession, event: dict[str, Any]) -> bool:
        """Push ``event`` onto ``session``. On queue-full, close it and return False."""
        if session.offer(event):
            return True
        logger.warning(
            "fulfill.queue_full_close",
            user=session.user_id,
            device=session.device_id,
            type=event.get("type"),
            qsize=_FULFILLER_QUEUE_MAXSIZE,
        )
        self.unregister(session)
        return False

    def connection_count(self, user_id: str) -> int:
        """How many live fulfillment connections ``user_id`` currently holds."""
        return len(self._by_user.get(user_id, ()))


_hub = FulfillerHub()


def default_fulfiller_hub() -> FulfillerHub:
    """The process-wide fulfiller hub (shared by the route + dispatch seam)."""
    return _hub
