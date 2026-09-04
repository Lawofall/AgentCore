"""Pick a turn's workspace backend (cloud / local)."""

from __future__ import annotations

from agentcore.folders.desk import resolve_folder_owner_user_id
from agentcore.folders.placement import resolve_folder_placement
from agentcore.runtime.events import EventSink
from agentcore.workspace import grant_store
from agentcore.workspace.hot_attach import attach_grants_to_backend
from agentcore.workspace.locate import LocalBinding, build_workspace
from agentcore.workspace.protocol import WorkspaceBackend


async def build_turn_backend(
    *,
    user_id: str,
    conversation_id: str,
    folder_id: str | None,
    sink: EventSink,
    local_binding: LocalBinding | None,
) -> WorkspaceBackend:
    """Pick a turn's workspace backend: local when bound, else cloud.

    Project conversations pass ``folder_id`` so cloud mode shares ``folder:<id>``;
    裸聊 passes ``folder_id=None`` for per-conversation ``conv:<id>`` scratch.

    Attaches W3 conversation-scoped external mounts when grants exist (skipped on
    member turns — 协作桌钉桌主盘, 成员不装配区外).
    """
    owner_id = user_id
    member_turn = False
    if folder_id:
        desk_owner = await resolve_folder_owner_user_id(folder_id)
        if desk_owner:
            owner_id = desk_owner
            member_turn = desk_owner != user_id
    placement = await resolve_folder_placement(folder_id)
    backend = build_workspace(
        user_id=owner_id,
        folder_id=folder_id,
        folder_rel_path=placement.rel_path,
        conversation_id=conversation_id,
        sink=sink,
        local_binding=local_binding,
    )
    # Cloud root_id-only grants: build a channel from sink so external/ ops reach desktop.
    # (Same helper mid-turn ``external_mount_readonly`` uses after a silent mint.)
    from agentcore.config import settings
    from agentcore.runtime.interaction import default_interaction_registry
    from agentcore.workspace.channel import WorkspaceChannel

    bootstrap_ch: WorkspaceChannel | None = None
    if not member_turn:
        grants = await grant_store.grants_as_dict(conversation_id)
        if grants and getattr(backend, "location", None) == "server" and any(
            not m.abs_path for m in grants.values()
        ):
            bootstrap_ch = WorkspaceChannel(
                user_id=user_id,
                conversation_id=conversation_id,
                registry=default_interaction_registry(),
                timeout_seconds=settings.workspace_op_timeout_seconds,
                root_id="",
                max_inflight=settings.workspace_channel_max_inflight,
            )
        await attach_grants_to_backend(
            backend,
            conversation_id,
            workspace_channel=bootstrap_ch,
        )
    # Code-index maintenance is kicked from write paths / code_search only —
    # not at turn entry (keeps TTFT / first thinking packet off the index path).
    # A′ write-lock short waits: emit workspace_lock_wait so desktop never fakes Thinking…
    bind_wait = getattr(backend, "set_lock_waiting_hook", None)
    if callable(bind_wait):

        def _on_lock_waiting(waiting: bool) -> None:
            if sink._closed:
                return
            from agentcore.runtime.events import workspace_lock_wait

            sink.emit(
                workspace_lock_wait(conversation_id=conversation_id, waiting=waiting)
            )

        bind_wait(_on_lock_waiting)
    return backend
