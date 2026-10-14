"""Human rewind: record the cut and wait. Does not start a model turn."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_conversation_repo, get_db
from agentcore.conversation.common import resolve_local_binding
from agentcore.core.errors import ConflictError, NotFoundError, ValidationError
from agentcore.db.repositories import (
    ConversationRepository,
    MessageRepository,
    TurnJournalRepository,
)
from agentcore.folders.placement import resolve_folder_placement
from agentcore.runtime.graph.session.session import active_coordination_for_conversation
from agentcore.runtime.graph.shape.rewind import (
    RewindRejected,
    nodes_from_journal,
    plan_rewind,
    seal_snapshot_id,
)
from agentcore.runtime.facts import GraphRewindFact
from agentcore.storage import SnapshotNotFound
from agentcore.workspace.snapshots import restore_snapshot

from ._helpers import _get_owned_conversation, _require_conversation_write

router = APIRouter(prefix="/conversations", tags=["conversations"])


class GraphRewindRequest(BaseModel):
    message_id: str
    target_run_id: str
    reason: str = ""
    restore_files: bool = False


class GraphRewindResponse(BaseModel):
    scar: str
    cut_run_ids: list[str] = Field(default_factory=list)
    target_run_id: str
    files_restored: bool = False


@router.post("/{conversation_id}/graph/rewind", response_model=GraphRewindResponse)
async def rewind_graph(
    conversation_id: str,
    body: GraphRewindRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    session: AsyncSession = Depends(get_db),
) -> GraphRewindResponse:
    """Point the next window at a sealed segment. The assistant waits for the next sentence."""
    await _require_conversation_write(conversation_id, user.user_id, conv_repo._session)
    if active_coordination_for_conversation(conversation_id) is not None:
        raise ConflictError("这一轮还在进行，先停掉再从这里继续。")

    message = await MessageRepository(session).get_by_id(
        body.message_id, conversation_id=conversation_id
    )
    if message is None or getattr(message, "role", None) != "assistant":
        raise NotFoundError("没有这一段所在的回复。")

    entries = await TurnJournalRepository(session).load_owned(body.message_id, conversation_id)
    nodes, original = nodes_from_journal(entries)
    reason = body.reason.strip() or "你选择从这一段继续"
    try:
        planned = plan_rewind(
            nodes,
            body.target_run_id.strip(),
            reason,
            original_request=original,
        )
    except RewindRejected as exc:
        raise ValidationError(str(exc)) from exc

    files_restored = False
    if body.restore_files:
        conv = await _get_owned_conversation(conversation_id, user.user_id, conv_repo)
        if await resolve_local_binding(session, conv) is not None:
            raise ConflictError("本地工作区的文件不在服务器上，去掉勾选可以只回转助手记得的内容。")
        snapshot_id = seal_snapshot_id(entries, planned.target_id)
        if not snapshot_id:
            raise ValidationError("这段封口时没有留下文件快照。")
        placement = await resolve_folder_placement(conv.folder_id, session=session)
        try:
            await restore_snapshot(
                user_id=user.user_id,
                folder_id=conv.folder_id,
                folder_rel_path=placement.rel_path,
                conversation_id=conversation_id,
                snapshot_id=snapshot_id,
            )
        except SnapshotNotFound as exc:
            raise ValidationError("这段封口时的文件快照已经不在了。") from exc
        files_restored = True

    fact = GraphRewindFact(
        target_id=planned.target_id,
        scar=planned.scar,
        kept_ids=planned.kept_ids,
        cut_ids=planned.cut_ids,
        conclusions=planned.conclusions,
        original_request=planned.original_request,
        source="human",
        reason=planned.reason,
    ).to_fact()
    await TurnJournalRepository(session).append(
        turn_id=body.message_id,
        seq=None,
        conversation_id=conversation_id,
        trace_id=None,
        entry=fact.entry(),
    )
    return GraphRewindResponse(
        scar=planned.scar,
        cut_run_ids=list(planned.cut_ids),
        target_run_id=planned.target_id,
        files_restored=files_restored,
    )
