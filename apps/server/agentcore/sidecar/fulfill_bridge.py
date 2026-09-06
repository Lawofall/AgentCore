"""The local engine's own CLIENT_TOOL 履约方 (sidecar-side fulfiller session).

Cloud mode: the desktop holds one ``GET /v1/fulfill`` SSE against the API process
and :mod:`agentcore.fulfill.dispatch` routes ``*_required`` frames onto it. The
sidecar hosts the SAME engine in a process the desktop spawned, so it has its own
in-process :class:`~agentcore.fulfill.hub.FulfillerHub` — with nobody registered
there, every channel op (host / mcp / board / board_read /
external_mount / terminal) settles instantly as 「no fulfiller（无履约方）」.

This bridge registers exactly one session on that in-process hub and drains it
onto the existing stdio JSON-RPC link as ``fulfill/frame`` notifications; the
desktop main process routes them to the renderer's CLIENT_TOOL ingress, which
settles back over ``respond``. Delivery therefore keeps ONE door (the hub) in
both modes — no EventSink double-delivery (双模式工作区 §7.7 否决「双投递 =
双真相源」).

Roots mirror the cloud fulfiller's declaration (connect-time query + the bindings
the API process records when a root is registered, :mod:`agentcore.fulfill.declare`),
fed by the ``localRootId`` the desktop already stamps on every turn: the sidecar's own
file ops go straight to ``Path`` (no frame at all) and ``terminal`` ops carry
``root_id=""`` → delivered as ``root_id=None``, but a worker desk bound to the
turn's local root does issue root-scoped ``workspace`` frames.

``localRootId`` alone is only the turn's *own* desk. Cross-desk delegate resolves
the target folder's binding to a second root, and bare chat brings no root at all,
so the bridge also installs itself as the process's
:mod:`~agentcore.fulfill.local_roots` declarer: every local workspace this engine
builds widens the declared set as it is built.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.fulfill.hub import (
    FULFILL_CHANNELS,
    FulfillerHub,
    FulfillerSession,
    default_fulfiller_hub,
)
from agentcore.fulfill.local_roots import (
    install_local_root_declarer,
    uninstall_local_root_declarer,
)
from agentcore.sidecar import protocol

logger = get_logger(__name__)

# JSON-RPC notification carrying one fulfill frame to the desktop main process.
FULFILL_FRAME_METHOD = "fulfill/frame"

# Platform tag on the hub session (cloud fills this from ``X-Client-Platform``).
FULFILL_PLATFORM = "sidecar"


class SidecarFulfillBridge:
    """One hub session for this sidecar process + its drain onto stdio.

    ``send`` is the raw JSON-RPC line sender (``SidecarServer._send``). The
    session is bound to the CURRENT account id: the desktop re-sends ``userId``
    per turn (a probe-spawned sidecar may have initialized as the ``local``
    alias), so :meth:`bind_user` re-registers when the principal changes —
    otherwise the engine would look for a fulfiller under a user nobody holds.
    """

    def __init__(
        self,
        send: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        hub: FulfillerHub | None = None,
        device_id: str | None = None,
    ) -> None:
        self._send = send
        self._hub = hub if hub is not None else default_fulfiller_hub()
        # One sidecar process = one device. The cloud device identity is not
        # reused: this hub is process-local and never sees a second client.
        self._device_id = device_id or f"sidecar-{os.getpid()}"
        self._session: FulfillerSession | None = None
        self._drain: asyncio.Task[None] | None = None
        # Root ids this device can serve; grows as turns declare their binding.
        self._roots: set[str] = set()

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def user_id(self) -> str | None:
        """The account id this bridge currently fulfils for (``None`` = unbound)."""
        return None if self._session is None else self._session.user_id

    def bind_user(self, user_id: str) -> None:
        """Register (or re-point) the session for ``user_id``. Idempotent."""
        uid = (user_id or "").strip()
        if not uid or (self._session is not None and self._session.user_id == uid):
            return
        # A different account key would otherwise leave the old session live
        # (``hub.register`` only replaces the same ``(user, device)`` pair).
        self.close()
        session = self._hub.register(
            uid,
            self._device_id,
            caps=FULFILL_CHANNELS,
            roots=self._roots,
            platform=FULFILL_PLATFORM,
        )
        self._session = session
        self._drain = asyncio.create_task(self._pump(session))
        install_local_root_declarer(self)
        logger.info(
            "sidecar.fulfill_bound",
            user_id=uid,
            device=self._device_id,
            caps=len(FULFILL_CHANNELS),
        )

    def declare_root(self, root_id: str) -> None:
        """Add a root this device can serve (cloud parity: registration receipts).

        Root-scoped ``workspace`` frames only reach a session whose roots contain
        the id; unscoped channels (host / mcp / board / board_read /
        external_mount / terminal) match regardless. Reached both with the turn's
        own ``localRootId`` and, as each local workspace is built, with a
        cross-desk target's root (:mod:`agentcore.fulfill.local_roots`).
        """
        rid = (root_id or "").strip()
        if not rid or rid in self._roots:
            return
        self._roots.add(rid)
        if self._session is not None:
            self._session.add_root(rid)

    def close(self) -> None:
        """Unregister the session and stop its drain. Idempotent.

        ``unregister`` already ends the pump with the close sentinel; the cancel
        only reaps a drain still parked on a send (rebind / shutdown), so no task
        outlives the loop it was created on.
        """
        uninstall_local_root_declarer(self)
        session, self._session = self._session, None
        drain, self._drain = self._drain, None
        if session is not None:
            self._hub.unregister(session)
        if drain is not None and not drain.done():
            drain.cancel()

    async def _pump(self, session: FulfillerSession) -> None:
        """Forward every hub frame to the desktop until the session closes."""
        while True:
            event = await session.get()
            if event is None:
                return
            try:
                await self._send(
                    protocol.make_notification(FULFILL_FRAME_METHOD, {"event": event})
                )
            except Exception as e:  # noqa: BLE001 — a dead pipe must not kill the loop
                logger.warning(
                    "sidecar.fulfill_send_failed",
                    device=self._device_id,
                    type=event.get("type"),
                    error=str(e),
                )
                return
