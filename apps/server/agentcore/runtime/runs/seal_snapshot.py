"""Zip the workspace when a segment seals, so a later rewind can restore it."""

from __future__ import annotations

from agentcore.core.logging import get_logger
from agentcore.runtime.facts import SealSnapshotFact, record_turn_fact

logger = get_logger(__name__)


async def snapshot_at_seal(*, user_id: str, conversation_id: str, run_id: str) -> None:
    """Best-effort seal snapshot. A zip failure does not unseal the segment.

    Local workspaces are skipped: the server does not hold those files.
    """
    if not user_id or not conversation_id or not run_id:
        return
    from agentcore.runtime.facts import current_fact_log

    # A live turn binds the fact log. Isolated executor tests do not, and must
    # not open a workspace zip.
    if current_fact_log.get() is None:
        return
    try:
        from agentcore.conversation.common import resolve_local_binding
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories import ConversationRepository
        from agentcore.folders.placement import resolve_folder_placement
        from agentcore.workspace.snapshots import create_snapshot

        async with async_session_factory() as session:
            conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
            if conv is None or conv.user_id != user_id:
                return
            if await resolve_local_binding(session, conv) is not None:
                return
            placement = await resolve_folder_placement(conv.folder_id, session=session)
            folder_id = conv.folder_id
            rel_path = placement.rel_path
        ref = await create_snapshot(
            user_id=user_id,
            folder_id=folder_id,
            folder_rel_path=rel_path,
            conversation_id=conversation_id,
            label=f"seal:{run_id}",
        )
    except Exception as exc:
        logger.warning(
            "rewind.seal_snapshot_failed",
            run_id=run_id,
            conversation_id=conversation_id,
            error=str(exc),
        )
        return
    record_turn_fact(SealSnapshotFact(run_id=run_id, snapshot_id=ref.snapshot_id).to_fact())
