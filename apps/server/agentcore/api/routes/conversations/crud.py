"""Conversation CRUD: create / list / grouped / get / update / delete / export.

Every route requires an authenticated user and is scoped to that user's own
conversations: reads/writes pass ``user_id`` into the repository so a non-owner
receives 404 (never another user's data — IDOR-safe).

Project membership is birth-time only (``folder_id`` on create); there is no
PATCH …/folder — sessions keep their birth project for life.
"""

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Response

from agentcore.api.dependencies import (
    AuthUser,
    get_conversation_repo,
    get_conversation_share_repo,
    get_folder_desk_service,
    get_folder_repo,
    get_message_repo,
    get_turn_journal_repo,
)
from agentcore.api.download_headers import download_headers
from agentcore.api.schemas import (
    AutoTitleRequest,
    AutoTitleResponse,
    ConversationListResponse,
    ConversationSummary,
    CreateConversationRequest,
    DeletedConversationListResponse,
    DeletedConversationSummary,
    FolderGroup,
    GroupedConversationsResponse,
    PermissionAxesUpdate,
    StatusResponse,
    UpdateConversationRequest,
    conversation_summary_from_orm,
)
from agentcore.config import settings
from agentcore.conversation.common import (
    default_permission_axes_for_user,
    mint_title_if_empty,
)
from agentcore.conversation.context_gap import visible_window_messages
from agentcore.conversation.export import (
    conversation_to_json,
    conversation_to_markdown,
)
from agentcore.core.errors import AuthorizationError, ConflictError, NotFoundError
from agentcore.core.logging import get_logger
from agentcore.db.models import Conversation
from agentcore.db.repositories import (
    ConversationRepository,
    ConversationShareRepository,
    FolderRepository,
    MessageRepository,
    TurnJournalRepository,
)
from agentcore.folders.desk import resolve_desk_access
from agentcore.folders.service import FolderDeskService
from agentcore.workspace.retention import retention_cutoff

from ._helpers import _get_owned_conversation, _require_conversation_write

logger = get_logger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])

# Recycle-bin page size, mirroring the project bin: a human-scale list with no paging
# UI to spend a cursor on; this only bounds a pathological account.
_TRASH_LIST_LIMIT = 200


def _summary_with_count(
    conv: Conversation,
    counts: dict[str, int],
    unfolded: dict[str, int] | None = None,
    previews: dict[str, str] | None = None,
    first_user_messages: dict[str, str] | None = None,
    prefs: dict[str, tuple[bool, bool]] | None = None,
) -> ConversationSummary:
    """Build a conversation summary, filling ``message_count`` from a counts map.

    The list/grouped endpoints precompute counts in one query (see
    ``MessageRepository.counts_for_conversations``) and pass the map here so the
    sidebar gets each chat's count without an N+1; absent ids default to 0.

    ``previews`` is the same batch overlay for ``last_message_preview`` (last
    visible assistant sentence; absent → null, never a user turn).

    ``first_user_messages`` is the same batch overlay for an empty DB ``title``
    (display-only ``fallback_title``; absent → leave empty). Never writes the column.

    ``unfolded`` is the same trick for the un-folded backlog (:func:`_unfolded_counts`),
    and only its keys get a ``context_gap`` verdict — a conversation left out was never
    a candidate, which is not the same as one proven intact.

    ``prefs`` is the caller's per-user pin/archive (missing → unpinned / unarchived).
    """
    preview = None if previews is None else previews.get(conv.id)
    first_user = None if first_user_messages is None else first_user_messages.get(conv.id)
    pinned = archived = None
    if prefs is not None:
        pinned, archived = prefs.get(conv.id, (False, False))
        archived = archived or bool(getattr(conv, "archived_by_folder_delete", False))
    kwargs: dict = {
        "message_count": counts.get(conv.id, 0),
        "last_message_preview": preview,
        "first_user_message": first_user,
        "pinned": pinned,
        "archived": archived,
    }
    if unfolded is not None and conv.id in unfolded:
        kwargs["unfolded_messages"] = unfolded[conv.id]
    return conversation_summary_from_orm(conv, **kwargs)


async def _orm_summary(repo: ConversationRepository, user_id: str, conv: Conversation, **kwargs):
    flags = await repo.preference_flags_for(user_id, [conv.id])
    pinned, archived = flags.get(conv.id, (False, False))
    archived = archived or bool(getattr(conv, "archived_by_folder_delete", False))
    return conversation_summary_from_orm(conv, pinned=pinned, archived=archived, **kwargs)


async def _first_user_contents_for_untitled(
    msg_repo: MessageRepository, conversations: list[Conversation]
) -> dict[str, str]:
    """Batch first user bodies for conversations whose DB ``title`` is still empty."""
    untitled = [c.id for c in conversations if not (c.title and str(c.title).strip())]
    if not untitled:
        return {}
    return await msg_repo.first_user_contents_for_conversations(untitled)


async def _unfolded_counts(msg_repo: MessageRepository, counts: dict[str, int]) -> dict[str, int]:
    """Un-folded backlog per conversation, for the few chats it could matter for.

    A chat cannot be hiding history it never had: un-folded ≤ total, so anything at
    or under the smallest window the loader ever falls back to is intact by
    arithmetic alone. Screening on the counts we already have keeps the second query
    off the overwhelmingly common sidebar — every chat short — and narrows it to the
    handful of long ones when it does run.
    """
    floor = visible_window_messages(has_summary=False)
    candidates = [cid for cid, n in counts.items() if n > floor]
    if not candidates:
        return {}
    unfolded = await msg_repo.unfolded_counts_for_conversations(candidates)
    # Absent = every message already folded; say 0 rather than「没算过」.
    return {cid: unfolded.get(cid, 0) for cid in candidates}


@router.post("", response_model=ConversationSummary, status_code=201)
async def create_conversation(
    body: CreateConversationRequest,
    user: AuthUser,
    repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
):
    """Create a conversation; optionally idempotent on ``client_request_id``.

    One send used to be able to create two identical chats (a double-tap, or two
    sockets firing 14ms apart), each of which then ran a full turn and billed for
    it. A client that mints a ``client_request_id`` per send gets exactly one
    conversation back for that key — the repeat returns the first one, same 201,
    same body. Omitting the key keeps the old behaviour verbatim: no heuristic
    (same title / same user / N seconds) ever stands in for an explicit key.
    """
    client_request_id = (body.client_request_id or "").strip() or None
    conv = None
    created = True

    if client_request_id is not None:
        # Answer the repeat here, before the folder check and the profile
        # snapshot: it already has its conversation, and re-running those only
        # risks failing it for a reason the first request never hit (a profile
        # deleted in between) while spending queries on a result we discard.
        conv = await repo.get_by_client_request_id(
            user_id=user.user_id, client_request_id=client_request_id
        )
        created = conv is None

    if conv is None:
        # A non-null target folder must be one of the user's own live folders (else
        # 404), mirroring the move endpoint so a chat can never be born in someone
        # else's or a deleted folder.
        if body.folder_id is not None:
            folder = await folder_repo.get_accessible(body.folder_id, user_id=user.user_id)
            if not folder:
                raise NotFoundError("文件夹不存在")
            access = await resolve_desk_access(
                folder_repo._session, folder_id=body.folder_id, user_id=user.user_id
            )
            if access is None or not access.can_write:
                raise AuthorizationError("只读成员不能在此文件夹新建对话")
        # Session permission axes: explicit body wins; else seed from user recipe default.
        if body.permission_axes is not None:
            axes = body.permission_axes.to_axes().to_dict()
        else:
            axes = (await default_permission_axes_for_user(repo._session, user.user_id)).to_dict()
        # 新建拍快照：显式 profile 钉住；省略则写入当时账号默认（非活跟随）。
        from agentcore.llm.model_profiles import LlmModelProfileService

        profile_svc = LlmModelProfileService(repo._session)
        if "model_profile_id" in body.model_fields_set and body.model_profile_id is not None:
            await profile_svc.ensure_profile_usable(user.user_id, body.model_profile_id)
            pin = body.model_profile_id
        else:
            pin = await profile_svc.snapshot_default_profile_id(user.user_id)
        # Project chats inherit the project's workspace — never write session-level
        # local_* / container columns. 裸聊 keeps desktop local-first intent.
        container_root = body.local_container_root_id if body.folder_id is None else None
        if client_request_id is None:
            conv = await repo.create(
                user_id=user.user_id,
                title=body.title,
                folder_id=body.folder_id,
                local_container_root_id=container_root,
                permission_axes=axes,
                model_profile_id=pin,
            )
        else:
            conv, created = await repo.create_idempotent(
                user_id=user.user_id,
                client_request_id=client_request_id,
                title=body.title,
                folder_id=body.folder_id,
                local_container_root_id=container_root,
                permission_axes=axes,
                model_profile_id=pin,
            )

    # The create itself was invisible in production: the 8 duplicate-conversation
    # reports over 7 days could only be reconstructed from what the chats later
    # did. One line per accepted POST makes「一次发送建出两条」countable, and
    # ``idempotent_hit`` measures how often the key is actually saving a run.
    logger.info(
        "conversation.created",
        user_id=user.user_id,
        conversation_id=conv.id,
        folder_id=conv.folder_id,
        client_request_id=client_request_id,
        idempotent_hit=not created,
    )
    return conversation_summary_from_orm(conv)


@router.post("/{conversation_id}/duplicate", response_model=ConversationSummary, status_code=201)
async def duplicate_conversation(
    conversation_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    msg_repo: MessageRepository = Depends(get_message_repo),
):
    """Clone a conversation into a brand-new one carrying a copy of its transcript (克隆对话).

    Owner-scoped (404 for a non-owner / missing source). The copy inherits the source's
    folder (so it stays in the same project/workspace) and local-first intent, with a
    「… 副本」title, then bulk-copies the source's messages via
    ``MessageRepository.copy_all`` (content-level fields only — see that method for what is
    intentionally not carried over, e.g. the team-graph replay journal). Returns the new
    conversation summary with its (copied) message count so the sidebar can insert it.
    """
    src = await conv_repo.get_by_id(conversation_id, user_id=user.user_id)
    if not src:
        raise NotFoundError("对话不存在")
    await _require_conversation_write(conversation_id, user.user_id, conv_repo._session)
    base = (src.title or "").strip()
    title = (f"{base} 副本" if base else "副本")[:500]
    # 克隆：有源钉则拷贝；源为存量 null 则拍当时账号默认。
    from agentcore.llm.model_profiles import LlmModelProfileService

    src_pin = getattr(src, "model_profile_id", None) or None
    if src_pin is None:
        src_pin = await LlmModelProfileService(conv_repo._session).snapshot_default_profile_id(
            user.user_id
        )
    new_conv = await conv_repo.create(
        user_id=user.user_id,
        title=title,
        folder_id=src.folder_id,
        local_container_root_id=src.local_container_root_id,
        permission_axes=dict(src.permission_axes or {}),
        deep_research_auto=bool(getattr(src, "deep_research_auto", False)),
        model_profile_id=src_pin,
    )
    count = await msg_repo.copy_all(conversation_id, new_conv.id)
    # Sidebar inserts this row into an infinite-stale cache — same preview
    # overlay as list/grouped, or the clone stays blank until a full refetch.
    previews = await msg_repo.previews_for_conversations([new_conv.id])
    return conversation_summary_from_orm(
        new_conv,
        message_count=count,
        last_message_preview=previews.get(new_conv.id),
    )


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    user: AuthUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    archived: bool = Query(
        False, description="True 返回已归档对话（「已归档」视图）；默认仅返回未归档"
    ),
    repo: ConversationRepository = Depends(get_conversation_repo),
    msg_repo: MessageRepository = Depends(get_message_repo),
):
    offset = (page - 1) * page_size
    conversations, total = await repo.list_by_user(
        user.user_id, limit=page_size, offset=offset, archived=archived
    )
    ids = [c.id for c in conversations]
    counts = await msg_repo.counts_for_conversations(ids)
    previews = await msg_repo.previews_for_conversations(ids)
    first_users = await _first_user_contents_for_untitled(msg_repo, conversations)
    unfolded = await _unfolded_counts(msg_repo, counts)
    prefs = await repo.preference_flags_for(user.user_id, ids)
    return ConversationListResponse(
        data=[
            _summary_with_count(c, counts, unfolded, previews, first_users, prefs)
            for c in conversations
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/grouped", response_model=GroupedConversationsResponse)
async def list_conversations_grouped(
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    msg_repo: MessageRepository = Depends(get_message_repo),
    desk: FolderDeskService = Depends(get_folder_desk_service),
):
    """Folders + their conversations + the ungrouped remainder (sidebar).

    Declared before ``/{conversation_id}`` so "grouped" isn't captured as an id.
    A conversation whose folder is missing/deleted falls back to ungrouped.
    Includes desks the caller joined (shared-with-me) so member threads group.
    """
    folders = list(await folder_repo.list_by_user(user.user_id))
    desk_role: dict[str, tuple[str, str, str]] = {
        f.id: ("owner", "accepted", f.user_id) for f in folders
    }
    seen = set(desk_role)
    for view in await desk.list_shared_with_me(user_id=user.user_id):
        if view.id in seen:
            continue
        shared = await folder_repo.get_by_id_unscoped(view.id)
        if shared is not None:
            folders.append(shared)
            seen.add(shared.id)
            desk_role[shared.id] = (view.my_role, view.my_state, view.owner_user_id)
    conversations = await conv_repo.list_all_by_user(user.user_id)
    ids = [c.id for c in conversations]
    counts = await msg_repo.counts_for_conversations(ids)
    previews = await msg_repo.previews_for_conversations(ids)
    first_users = await _first_user_contents_for_untitled(msg_repo, conversations)
    unfolded = await _unfolded_counts(msg_repo, counts)
    prefs = await conv_repo.preference_flags_for(user.user_id, ids)

    buckets: dict[str, list[ConversationSummary]] = {f.id: [] for f in folders}
    ungrouped: list[ConversationSummary] = []
    for conv in conversations:
        summary = _summary_with_count(conv, counts, unfolded, previews, first_users, prefs)
        if conv.folder_id in buckets:
            buckets[conv.folder_id].append(summary)
        else:
            ungrouped.append(summary)

    return GroupedConversationsResponse(
        folders=[
            FolderGroup(
                id=f.id,
                name=f.name,
                mode="local" if f.local_root_id else "cloud",
                local_root_id=f.local_root_id,
                local_subpath=f.local_subpath,
                owner_user_id=desk_role[f.id][2],
                my_role=desk_role[f.id][0],  # type: ignore[arg-type]
                my_state=desk_role[f.id][1],  # type: ignore[arg-type]
                conversations=buckets[f.id],
            )
            for f in folders
        ],
        ungrouped=ungrouped,
    )


# --- 最近删除 (conversation recycle bin) ---
# Declared ahead of the ``/{conversation_id}`` matchers: FastAPI resolves in
# registration order, so a literal ``/trash`` segment must be registered first to win.
#
# Not to be confused with ``/{conversation_id}/trash`` (``routes/conversations/trash.py``),
# which is the workspace's own file soft-delete area — this one is the deleted *chats*
# themselves, the twin of ``/folders/trash``.


def _conversation_purge_at(conv: Conversation) -> datetime:
    """When the retention sweeper may hard-purge this soft-deleted conversation."""
    assert conv.deleted_at is not None
    return conv.deleted_at + timedelta(days=settings.workspace_retention_days)


@router.get("/trash", response_model=DeletedConversationListResponse)
async def list_deleted_conversations(
    user: AuthUser,
    repo: ConversationRepository = Depends(get_conversation_repo),
    msg_repo: MessageRepository = Depends(get_message_repo),
):
    """列出可恢复的已删对话（最近删除）。

    Delete has always been a soft delete — the rows never went anywhere — but nothing
    ever listed them, so「删了就没了」was true in practice. Same retention window and
    same cutoff as the project bin: a chat is offered here only while the sweeper is
    still forbidden to purge it.
    """
    conversations = await repo.list_deleted_by_user(
        user.user_id, not_before=retention_cutoff(), limit=_TRASH_LIST_LIMIT
    )
    counts = await msg_repo.counts_for_conversations([c.id for c in conversations])
    return DeletedConversationListResponse(
        data=[
            DeletedConversationSummary.from_conversation(
                c,
                purge_at=_conversation_purge_at(c),
                message_count=counts.get(c.id, 0),
            )
            for c in conversations
        ],
        total=len(conversations),
        retention_days=settings.workspace_retention_days,
    )


@router.post("/trash/{conversation_id}/restore", response_model=ConversationSummary)
async def restore_deleted_conversation(
    conversation_id: str,
    user: AuthUser,
    repo: ConversationRepository = Depends(get_conversation_repo),
    msg_repo: MessageRepository = Depends(get_message_repo),
):
    """恢复一个已删对话：回到删除前的项目分组、置顶 / 归档状态与最近活动位置。

    Past retention the answer is 409, never a silent success — the transcript may
    already be gone. Losing the race with the purge sweep between the lookup and the
    conditional UPDATE surfaces the same way (「该对话已被清理」); that window is
    reported honestly rather than reconciled.

    Public share links revoked by the delete stay revoked (see
    :meth:`ConversationRepository.restore`) — the client says so instead of implying a
    live link came back.
    """
    conv = await repo.get_deleted_by_id(conversation_id, user_id=user.user_id)
    if not conv:
        raise NotFoundError("对话不存在或不在最近删除中")
    if conv.deleted_at <= retention_cutoff():
        raise ConflictError(
            f"该对话已超过 {settings.workspace_retention_days} 天保留期，无法恢复"
        )

    restored = await repo.restore(
        conversation_id, user_id=user.user_id, not_before=retention_cutoff()
    )
    if restored is None:
        raise ConflictError("该对话已被清理，无法恢复")
    logger.info(
        "conversation.restored",
        conversation_id=conversation_id,
        user_id=user.user_id,
        folder_id=restored.folder_id,
    )
    counts = await msg_repo.counts_for_conversations([conversation_id])
    return conversation_summary_from_orm(
        restored, message_count=counts.get(conversation_id, 0)
    )


@router.get("/{conversation_id}", response_model=ConversationSummary)
async def get_conversation(
    conversation_id: str,
    user: AuthUser,
    repo: ConversationRepository = Depends(get_conversation_repo),
    msg_repo: MessageRepository = Depends(get_message_repo),
):
    conv = await repo.get_by_id(conversation_id, user_id=user.user_id)
    if not conv:
        raise NotFoundError("对话不存在")
    first_user = None
    if not (conv.title and str(conv.title).strip()):
        first_users = await msg_repo.first_user_contents_for_conversations([conversation_id])
        first_user = first_users.get(conversation_id)
    return await _orm_summary(repo, user.user_id, conv, first_user_message=first_user)


@router.post("/{conversation_id}/auto-title", response_model=AutoTitleResponse)
async def auto_title_conversation(
    conversation_id: str,
    body: AutoTitleRequest,
    user: AuthUser,
    repo: ConversationRepository = Depends(get_conversation_repo),
):
    """Mint a conversation title from the first user message (await).

    Local sidecar has no cloud SSE ``title_generated`` path — the desktop calls this
    in parallel with the first local turn. Shares the same mint core as cloud
    ``schedule_title_generation`` (user message only; ``assistant_reply=""``).

    Already-titled conversations return the existing title without calling the LLM.
    Mint failure returns ``title=""`` (HTTP 200) and does not persist a fallback.
    """
    conv = await repo.get_by_id(conversation_id, user_id=user.user_id)
    if not conv:
        raise NotFoundError("对话不存在")
    existing = str(conv.title).strip() if conv.title else ""
    if existing:
        return AutoTitleResponse(title=existing)

    title = await mint_title_if_empty(
        conversation_id=conversation_id,
        user_id=user.user_id,
        user_message=body.user_message,
        sink=None,
    )
    # Empty string is a successful "no title yet" — not an HTTP error. Degraded
    # mints do not write ``conversations.title`` (so a later turn can retry).
    return AutoTitleResponse(title=title or "")


@router.put("/{conversation_id}/permission-axes", response_model=ConversationSummary)
async def set_permission_axes(
    conversation_id: str,
    body: PermissionAxesUpdate,
    user: AuthUser,
    repo: ConversationRepository = Depends(get_conversation_repo),
):
    """Switch the session permission axes (降档/升档确认由客户端负责).

    Takes effect on the next turn / durable resume (gate is built at turn entry).
    Illegal combo ``command=auto`` ∧ ``file_write=ask`` is rejected by the schema.
    """
    conv = await repo.get_by_id(conversation_id, user_id=user.user_id)
    if not conv:
        raise NotFoundError("对话不存在")
    await _require_conversation_write(conversation_id, user.user_id, repo._session)
    previous = dict(conv.permission_axes or {})
    next_axes = body.permission_axes.to_axes().to_dict()
    if previous != next_axes:
        updated = await repo.set_permission_axes(
            conversation_id, user_id=user.user_id, permission_axes=next_axes
        )
        if not updated:
            raise NotFoundError("对话不存在")
        conv = updated
        logger.info(
            "conversation.permission_axes_changed",
            conversation_id=conversation_id,
            previous=previous,
            permission_axes=next_axes,
        )
        from agentcore.runtime.audit.permission_events import (
            record_permission_axes_change,
        )

        await record_permission_axes_change(
            user_id=user.user_id,
            conversation_id=conversation_id,
            previous=previous,
            next_axes=next_axes,
        )
    return await _orm_summary(repo, user.user_id, conv)


@router.patch("/{conversation_id}", response_model=ConversationSummary)
async def update_conversation(
    conversation_id: str,
    body: UpdateConversationRequest,
    user: AuthUser,
    repo: ConversationRepository = Depends(get_conversation_repo),
):
    # Patch only the fields the client sent: an omitted field is left untouched.
    fields = body.model_fields_set
    conv = await repo.get_by_id(conversation_id, user_id=user.user_id)
    if not conv:
        raise NotFoundError("对话不存在")
    shared_write = fields & {"title", "deep_research_auto", "model_profile_id"}
    if shared_write:
        await _require_conversation_write(conversation_id, user.user_id, repo._session)
    if "title" in fields and body.title is not None:
        conv = await repo.update_title(conversation_id, body.title, user_id=user.user_id)
    # Sidebar housekeeping toggles (对话基础功能补齐): pin floats the row to the top,
    # archive hides it from the live list (both reversible, no tri-state → a null is
    # ignored as「unchanged」).
    if "pinned" in fields and body.pinned is not None:
        conv = await repo.set_pinned(conversation_id, body.pinned, user_id=user.user_id)
    if "archived" in fields and body.archived is not None:
        conv = await repo.set_archived(conversation_id, body.archived, user_id=user.user_id)
    if "deep_research_auto" in fields and body.deep_research_auto is not None:
        updated = await repo.set_deep_research_auto(
            conversation_id, body.deep_research_auto, user_id=user.user_id
        )
        if updated:
            conv = updated
    # 会话级模型组合: explicit profile_id pins; null re-pins to account default snapshot.
    if "model_profile_id" in fields:
        from agentcore.llm.model_profiles import LlmModelProfileService

        profile_svc = LlmModelProfileService(repo._session)
        profile_id = body.model_profile_id
        if profile_id is None:
            profile_id = await profile_svc.snapshot_default_profile_id(user.user_id)
        else:
            await profile_svc.ensure_profile_usable(user.user_id, profile_id)
        updated = await repo.set_model_profile(conversation_id, profile_id, user_id=user.user_id)
        if updated:
            conv = updated
    return await _orm_summary(repo, user.user_id, conv)


@router.delete("/{conversation_id}", response_model=StatusResponse)
async def delete_conversation(
    conversation_id: str,
    user: AuthUser,
    repo: ConversationRepository = Depends(get_conversation_repo),
    share_repo: ConversationShareRepository = Depends(get_conversation_share_repo),
):
    """软删对话：保留期内可从「最近删除」恢复（``POST …/trash/{id}/restore``）。

    What the restore brings back is the conversation row and its transcript, in the
    project / pin / archive state it left in. What it does **not** bring back is
    listed below: the share links this cascade revokes, and the live sandbox / grant
    side-state torn down with it. Those are one-way on purpose, and the client says so
    — the copy on the confirm and in the bin must not out-promise this function.
    """
    deleted = await repo.soft_delete(conversation_id, user_id=user.user_id)
    if not deleted:
        raise NotFoundError("对话不存在")
    # Cascade-revoke any public share links (分享对话): deleting a conversation must
    # kill its read-only links so a stale snapshot can't outlive it. Owner already
    # proven by the soft_delete above, so a blanket per-conversation revoke is safe.
    await share_repo.revoke_all_for_conversation(conversation_id)
    # W3/P1: drop conversation external grants + organize plan/journal.
    from agentcore.workspace import grant_store, organize_journal, organize_plan_store

    await grant_store.clear_conversation(conversation_id)
    organize_plan_store.clear_conversation(conversation_id)
    organize_journal.clear_conversation(conversation_id)
    # L3 team-browser: tear down any live sandbox session (no-op when none exists;
    # teardown errors are swallowed+logged inside the registry, never fail the delete).
    from agentcore.runtime.browser import default_browser_session_registry

    await default_browser_session_registry().close(conversation_id)
    return StatusResponse()


def _download_headers(filename: str) -> dict[str, str]:
    """Content-Disposition for export downloads (RFC 5987); see ``download_headers``."""
    return download_headers(filename, fallback="conversation")


def _safe_export_stem(title: str, conversation_id: str) -> str:
    """A filesystem-safe base name for an export file, derived from the title.

    Strips path separators / control chars and caps the length; falls back to the
    conversation id when the title is empty or strips to nothing.
    """
    cleaned = "".join(
        ch for ch in (title or "") if ch.isprintable() and ch not in '/\\:*?"<>|'
    ).strip()
    cleaned = cleaned[:80].strip()
    return cleaned or f"conversation-{conversation_id[:8]}"


@router.get("/{conversation_id}/export")
async def export_conversation(
    conversation_id: str,
    user: AuthUser,
    format: str = Query("md", pattern="^(md|json)$"),
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    msg_repo: MessageRepository = Depends(get_message_repo),
    journal_repo: TurnJournalRepository = Depends(get_turn_journal_repo),
):
    """Export a conversation's full transcript as a download (导出对话).

    Reads the WHOLE transcript server-side (not a scroll window, so nothing is
    missed) and renders it owner-scoped (404 for a non-owner). ``format=md`` is a
    clean, content-only Markdown record (the default a user reads / pastes);
    ``format=json`` is a full-fidelity dump for power users / re-import. JSON
    projects ``finish_reason`` from turn journal / usage when available. Spend is
    never exported — it lives in the cost ledger, not the message body.
    """
    conv = await _get_owned_conversation(conversation_id, user.user_id, conv_repo)
    messages = await msg_repo.list_all_for_conversation(conversation_id)
    stem = _safe_export_stem(conv.title, conversation_id)
    journal_map = await journal_repo.load_map([m.id for m in messages])
    if format == "json":
        payload = conversation_to_json(conv, messages, journal_map=journal_map)
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        return Response(
            content=content,
            media_type="application/json",
            headers=_download_headers(f"{stem}.json"),
        )
    content = conversation_to_markdown(conv, messages, journal_map=journal_map)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers=_download_headers(f"{stem}.md"),
    )
