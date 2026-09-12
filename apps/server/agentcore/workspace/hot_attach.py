"""Same-turn hot attach of conversation external mounts onto a live backend.

Turn entry (``build_turn_backend``) and file-tool host-path mint both call
:func:`attach_grants_to_backend` so ``file_read external/…`` works without
waiting for the next resume.

Sidecar Path-I/O needs ``abs_path``. Grant rows never store it; desktop
hot-pushes it onto the live backend first. This helper copies live abs onto
grant-store rows so ``_mint`` cannot wipe the snapshot.

Ticketed sidecar must not open local Postgres: skip grant_store and keep the
desktop snapshot. Discriminator is narrow tickets, not ``location=local``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from agentcore.db.sidecar_tickets import sidecar_narrow_tickets_bound
from agentcore.workspace import grant_store
from agentcore.workspace.external_mounts import ExternalMount
from agentcore.workspace.protocol import WorkspaceBackend

if TYPE_CHECKING:
    from agentcore.desktop.channel import DesktopClientChannel
    from agentcore.workspace.channel import WorkspaceChannel


def _merge_live_abs(
    grants: dict[str, ExternalMount],
    live: dict[str, ExternalMount],
) -> dict[str, ExternalMount]:
    """Keep live abs when grant-store rows are root_id-only; keep live-only abs."""
    if not live:
        return grants
    out: dict[str, ExternalMount] = {}
    for alias, mount in grants.items():
        prev = live.get(alias)
        if not mount.abs_path and prev is not None and prev.abs_path:
            out[alias] = replace(mount, abs_path=prev.abs_path)
        else:
            out[alias] = mount
    for alias, prev in live.items():
        if alias not in out and prev.abs_path:
            out[alias] = prev
    return out


def _ensure_external_channel(
    backend: WorkspaceBackend,
    *,
    conversation_id: str,
    mounts: dict[str, ExternalMount],
    desktop_channel: DesktopClientChannel | None,
    workspace_channel: WorkspaceChannel | None,
) -> None:
    """Attach a desktop WorkspaceChannel when any grant is root_id-only."""
    if not any(not m.abs_path for m in mounts.values()):
        return
    if getattr(backend, "_external_bridge", None) is not None:
        # Bridge already present — attach_external_mounts refreshed mounts on it.
        return
    attach_ch = getattr(backend, "attach_external_channel", None)
    if not callable(attach_ch):
        return

    ch = workspace_channel
    if ch is None and desktop_channel is not None:
        from agentcore.config import settings
        from agentcore.workspace.channel import WorkspaceChannel

        ch = WorkspaceChannel(
            user_id=desktop_channel.user_id,
            conversation_id=conversation_id,
            registry=desktop_channel.registry,
            timeout_seconds=settings.workspace_op_timeout_seconds,
            root_id="",
            max_inflight=settings.workspace_channel_max_inflight,
        )
    if ch is not None:
        attach_ch(ch)


async def attach_grants_to_backend(
    backend: WorkspaceBackend,
    conversation_id: str,
    *,
    desktop_channel: DesktopClientChannel | None = None,
    workspace_channel: WorkspaceChannel | None = None,
) -> dict[str, ExternalMount]:
    """Load grants for ``conversation_id`` and attach them to ``backend`` (hot).

    Ensures a cloud / root_id-only bridge via ``desktop_channel`` or an existing
    ``workspace_channel`` (sidecar terminal channel) when needed.

    Sidecar: grant rows have no abs. Merge copies abs from the live backend
    (desktop ``updateExternalMounts``) so this call cannot drop Path-I/O.

    Ticketed sidecar: live snapshot is SoT — never ``grant_store``.
    """
    live = dict(getattr(backend, "_mounts", None) or {})
    if sidecar_narrow_tickets_bound():
        return live
    grants = await grant_store.grants_as_dict(conversation_id)
    mounts = _merge_live_abs(grants, live)
    attach = getattr(backend, "attach_external_mounts", None)
    if mounts and callable(attach):
        attach(mounts)
        _ensure_external_channel(
            backend,
            conversation_id=conversation_id,
            mounts=mounts,
            desktop_channel=desktop_channel,
            workspace_channel=workspace_channel,
        )
    return mounts
