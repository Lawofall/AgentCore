"""Local-desk file reachability — presence, not op timeouts.

A LocalWorkspace talks to the user's disk through a desktop fulfiller. Whether
those files are *connected* is the same question the turn-start presence gate
asks the fulfill hub. A settle timeout only means that one request did not
finish; language-service diagnostics are a separate capability.

Mid-turn retire / write-desk dispatch use this module. Reconnect grace
(:meth:`FulfillerHub.seen_recently`) counts as still reachable so a brief SSE
drop does not strip the file family and then put it back.
"""

from __future__ import annotations

from typing import Any

from agentcore.fulfill.hub import default_fulfiller_hub, origin_pinned
from agentcore.fulfill.origin import current_origin_device
from agentcore.workspace.channel import WorkspaceChannel


def backend_needs_workspace_fulfiller(backend: object | None) -> bool:
    """True for LocalWorkspace (desktop channel), not sidecar Path-backed local."""
    if backend is None or getattr(backend, "location", None) != "local":
        return False
    return getattr(backend, "_channel", None) is not None


def local_workspace_files_reachable(
    *,
    user_id: str | None,
    backend: object | None,
    origin_device_id: str | None = None,
) -> bool | None:
    """Whether this desk's files can be reached through a workspace fulfiller.

    Returns:
      * ``None`` — not a desktop-channel backend (cloud / sidecar Path), or
        ``user_id`` is missing so the hub cannot be asked.
      * ``True`` — a matching fulfiller is live, or that device disconnected
        inside the reconnect grace.
      * ``False`` — no live fulfiller and grace has elapsed.
    """
    if not backend_needs_workspace_fulfiller(backend):
        return None
    uid = (user_id or "").strip()
    if not uid:
        return None
    channel = getattr(backend, "_channel", None)
    root_id = (getattr(channel, "root_id", None) or "") or None
    hub = default_fulfiller_hub()
    origin = (
        (origin_device_id or "").strip()
        or (current_origin_device() or "").strip()
        or None
    )
    pinned = bool(origin) and origin_pinned("workspace", root_id=root_id)
    if hub.has_fulfiller(
        uid,
        root_id=root_id,
        channel="workspace",
        origin_device_id=origin,
        require_origin=pinned,
    ):
        return True
    if origin is not None:
        if hub.seen_recently(uid, device_id=origin):
            return True
    elif hub.seen_recently(uid):
        return True
    return False


def diagnostics_rides_fulfill_channel(backend: Any | None) -> bool:
    """True when inner-loop diagnostics would take the desktop fulfill hop.

    Write receipts must not wait on that hop. Explicit ``code_diagnostics`` still
    may; a timeout there fails only that call.
    """
    if backend is None:
        return False
    if isinstance(getattr(backend, "_channel", None), WorkspaceChannel):
        return True
    desktop = getattr(backend, "_desktop_channel", None)
    return isinstance(desktop, WorkspaceChannel)
