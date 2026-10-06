"""Conversation and folder (project = workspace) request/response schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agentcore.conversation.context_cut_plan import context_cut_undoable
from agentcore.core.types import WorkspaceBoundary


class PermissionAxesModel(BaseModel):
    """Conversation boundary (运行时单一真相源)."""

    model_config = {"extra": "ignore"}

    boundary: WorkspaceBoundary = WorkspaceBoundary.FOLDER

    def to_axes(self) -> WorkspaceBoundary:
        return self.boundary

    @classmethod
    def from_axes(cls, axes: WorkspaceBoundary) -> "PermissionAxesModel":
        return cls(boundary=axes)


class ContextGapModel(BaseModel):
    """早期对话没能进摘要、也已滑出原文窗口——这一轮 AI 确实读不到它们。

    The failure half of ``context_compacted``: that flag says folding HAS run, and a
    bool cannot tell a healthy rolling summary apart from folding that kept failing
    until the chat outgrew its window. Present only when the loss is provable from
    stored state (``conversation/context_gap.py``); absent means either intact or
    not computed, and a client must stay quiet on both rather than warn on a guess.

    Wording obligations for whoever renders this (same honesty bar as the memory
    always-quota card): name what did not happen and what the user can do, never let
    it read as a capability lost for good. Nothing was deleted — the transcript is
    whole on screen, and the backlog folds itself in incrementally once folding can
    run again. ``recovery_at`` is upstream's own date when it gave one; its absence
    means「不知道，会自动重试」and must not be dressed up as a deadline.
    """

    # Stored rows the window cut — exact, not an estimate (same arithmetic as the loader).
    dropped_messages: int
    # ISO-8601 UTC instant ("2026-08-14T16:00:00Z") from the upstream 429 that dated its
    # own recovery — the same instant the turn's 429 envelope carries as ``recovery_at``.
    # Never pre-worded: the client renders it in the reader's timezone, which the server
    # has no way to know (core.errors.utc_moment_iso).
    recovery_at: str | None = None


class CreateConversationRequest(BaseModel):
    title: str | None = None
    # File the new chat into a project at creation. Born into that project's
    # shared workspace (no session-level local_* columns written). None = 裸聊.
    folder_id: str | None = None
    # Desktop's default local container root for a 裸聊 (local-first intent).
    # Recorded only when ``folder_id`` is None; project chats inherit the project's
    # binding instead.
    local_container_root_id: str | None = Field(None, max_length=200)
    # Session permission axes. Omit → seed from the user's autonomy recipe
    # (default recipe = less_interrupt → session/auto/rules/session).
    permission_axes: PermissionAxesModel | None = None
    # 新建拍快照：显式 uuid = 钉该装配；省略 = 服务端写入当时星标装配。
    assembly_id: str | None = None
    # 幂等键：客户端为「这一次发送」自铸一个 id（重试 / 重按都复用同一个）。同一用户
    # 同一个键只会建出一条会话，第二次起原样返回首次那条（201，body 同形）。省略 =
    # 保持旧行为，一次请求建一条——老客户端不受影响。
    client_request_id: str | None = Field(None, min_length=1, max_length=100)


class ConversationSummary(BaseModel):
    id: str
    title: str | None = Field(
        description=(
            "会话标题。可能是服务端从首条用户消息算出的兜底展示值，不代表已铸出真标题。"
        ),
    )
    updated_at: datetime
    created_at: datetime
    message_count: int = 0
    # List projection: last visible assistant sentence. Null when none qualify.
    # Never a user turn, empty running placeholder, or stop/interrupt chrome.
    last_message_preview: str | None = None
    # Project membership; None = 裸聊. When set, effective workspace is the project's.
    folder_id: str | None = None
    # Desktop local-first intent for a 裸聊; moot once foldered.
    local_container_root_id: str | None = None
    pinned: bool = False
    archived: bool = False
    # Session permission axes (运行时单一真相源).
    permission_axes: PermissionAxesModel = Field(
        default_factory=PermissionAxesModel
    )
    # 深度研究自治（会话级显式旗标；不再由权限配方蕴含）。
    deep_research_auto: bool = False
    # 这场用的装配。新建写入星标或客户端所选。模型在这份装配上。
    assembly_id: str | None = None
    # True iff ORM has both compaction_summary and compacted_through.
    # Flag only — never expose rolling-summary text to clients.
    context_compacted: bool = False
    # Last folded message ``created_at``. Present only with ``context_compacted``.
    # Desktop timeline divider sits after this instant. Not the summary body.
    compacted_through: datetime | None = None
    # The latest user context cut can still be undone (watermark has not moved since).
    # Never the summary body and never the undo snapshot.
    context_cut_undoable: bool = False
    # 压缩没跟上，早期对话已经掉出窗口（见 ContextGapModel）。null = 完好或本端点未计算。
    context_gap: ContextGapModel | None = None

    model_config = {"from_attributes": True}

    @field_validator("permission_axes", mode="before")
    @classmethod
    def _coerce_axes(cls, value: object) -> object:
        if isinstance(value, WorkspaceBoundary):
            return PermissionAxesModel.from_axes(value)
        if isinstance(value, dict):
            return WorkspaceBoundary.from_mapping(value).to_dict()
        return value


def conversation_summary_from_orm(
    conv: object,
    *,
    message_count: int | None = None,
    unfolded_messages: int | None = None,
    last_message_preview: str | None = None,
    first_user_message: str | None = None,
    pinned: bool | None = None,
    archived: bool | None = None,
) -> ConversationSummary:
    """Assemble ``ConversationSummary`` with ``context_compacted`` + watermark (no summary body).

    ``unfolded_messages`` (messages the rolling summary does not cover yet) turns on
    the ``context_gap`` half: omit it on endpoints that do not count messages and the
    field stays null, which clients read as「未计算」and keep quiet about — a cheaper
    default than making every conversation write pay for a count nobody reads.

    ``last_message_preview`` is the same batch overlay as ``message_count``: list /
    grouped fills it; create / get / patch leave the default null.

    ``first_user_message`` is a read-side overlay for an empty DB ``title``: the
    truncated first user line (``fallback_title``). It never writes the column.
    """
    summary = ConversationSummary.model_validate(conv)
    watermark = getattr(conv, "compacted_through", None)
    compacted = bool(getattr(conv, "compaction_summary", None) and watermark)
    live_watermark = watermark if compacted else None
    updates: dict[str, object] = {
        "context_compacted": compacted,
        # Orphan watermark without a summary must not leak a fold boundary.
        "compacted_through": live_watermark,
        "context_cut_undoable": context_cut_undoable(
            getattr(conv, "context_cut_undo", None), live_watermark
        ),
        "last_message_preview": last_message_preview,
    }
    db_title = (summary.title or "").strip()
    if not db_title and first_user_message:
        from agentcore.conversation.common import fallback_title

        label = fallback_title(first_user_message)
        if label:
            updates["title"] = label
    if message_count is not None:
        updates["message_count"] = message_count
    if unfolded_messages is not None:
        from agentcore.conversation.context_gap import context_gap_for

        gap = context_gap_for(conv, unfolded_messages=unfolded_messages)
        if gap is not None:
            updates["context_gap"] = ContextGapModel(
                dropped_messages=gap.dropped_messages,
                recovery_at=gap.recovery_at,
            )
    if pinned is not None:
        updates["pinned"] = pinned
    if archived is not None:
        updates["archived"] = archived
    return summary.model_copy(update=updates)


class DeletedConversationSummary(BaseModel):
    """One recoverable conversation in「最近删除」."""

    id: str
    title: str | None
    # The project it will return to. A project that was itself deleted meanwhile is
    # still named here — the chat comes back pointing at it and reads as 未分组 until
    # that project is restored too.
    folder_id: str | None = None
    message_count: int = 0
    created_at: datetime
    deleted_at: datetime
    # When the retention sweeper is entitled to purge this chat for good. Server-owned
    # arithmetic, same as the project bin: a client must never re-derive「还剩几天」from
    # ``deleted_at`` plus a hard-coded window. The sweep runs on a cadence, so this is
    # the earliest possible purge, not a promise.
    purge_at: datetime

    @classmethod
    def from_conversation(
        cls, conv, *, purge_at: datetime, message_count: int = 0
    ) -> "DeletedConversationSummary":
        assert conv.deleted_at is not None  # repo only returns soft-deleted rows
        return cls(
            id=conv.id,
            title=conv.title,
            folder_id=conv.folder_id,
            message_count=message_count,
            created_at=conv.created_at,
            deleted_at=conv.deleted_at,
            purge_at=purge_at,
        )


class DeletedConversationListResponse(BaseModel):
    """Recoverable conversations, most recently deleted first.

    Hidden infrastructure hosts (handoff / standing) never appear, nor do chats already
    past retention — those are no longer restorable, and listing them would promise a
    recovery the sweeper is entitled to refuse. ``retention_days`` mirrors
    ``workspace_retention_days``, the same window the project bin runs on.

    ``total`` is every recoverable row in that window. ``data`` is a capped page, so
    emptying the bin follows ``total``, not ``len(data)``.
    """

    data: list[DeletedConversationSummary]
    total: int
    retention_days: int


class ConversationListResponse(BaseModel):
    data: list[ConversationSummary]
    total: int
    page: int
    page_size: int


class UpdateConversationRequest(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    archived: bool | None = None
    # 深度研究自治：省略 = 不变；显式 true/false 切换会话旗标（设置页 UI 另批）。
    deep_research_auto: bool | None = None
    # 会话级装配：省略 = 不变；显式 uuid = 钉装配；显式 null = 再钉当时星标。
    assembly_id: str | None = None


class PermissionAxesUpdate(BaseModel):
    """Switch the conversation's permission axes mid-session."""

    permission_axes: PermissionAxesModel


class ContextCutPreviewRequest(BaseModel):
    """Fold everything still in the model window before this message."""

    message_id: str = Field(..., min_length=1)


class ContextCutPreviewResponse(BaseModel):
    """Prose the user can edit before it becomes the prefix.

    ``keep_message_id`` may be the user message that opened the chosen
    assistant turn. ``summary`` is the editable prose only. Commit stores
    that prose plus the program-owned identity ledger for this fold.
    """

    summary: str
    keep_message_id: str
    fold_through: datetime
    fold_digest: str
    folded_count: int


class ContextCutCommitRequest(BaseModel):
    """Write the prose the user confirmed. Does not summarize again.

    ``summary`` is editable prose. The server drops any identity-ledger fence
    in it and appends the ledger for this fold.
    """

    message_id: str = Field(..., min_length=1)
    fold_digest: str = Field(..., min_length=64, max_length=64)
    summary: str = Field(..., min_length=1, max_length=32_000)


class DuplicateConversationRequest(BaseModel):
    """Clone the transcript through one message (克隆对话 / 行业 fork).

    ``until_message_id`` is required — there is no whole-conversation clone without
    a cutoff. The copy includes that row and every earlier row in render order;
    later turns stay only on the source. Original conversation is unchanged.
    """

    until_message_id: str = Field(..., min_length=1)


class AutoTitleRequest(BaseModel):
    """Local-first parallel title mint: first user message only (no assistant reply)."""

    user_message: str = Field(..., min_length=1)

    @field_validator("user_message")
    @classmethod
    def _strip_nonempty(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("user_message 不能为空")
        return trimmed


class AutoTitleResponse(BaseModel):
    """Resulting conversation title (existing or freshly minted).

    Empty string means the mint did not persist a title (failure leaves the
    column empty so a later turn can retry). Not an error.
    """

    title: str


class CreateFolderRequest(BaseModel):
    """Create a project (= workspace). ``mode`` is required and immutable after create."""

    model_config = {"extra": "forbid"}

    name: str
    mode: Literal["local", "cloud"]
    # Required when ``mode=local``; forbidden when ``mode=cloud``.
    local_root_id: str | None = Field(None, max_length=200)
    local_subpath: str | None = Field(None, max_length=400)
    # Nest the new folder inside this one (omit / null = top level). Resolved to a
    # ``rel_path`` prefix server-side; there is no ``parent_id`` column.
    parent_id: str | None = None

    @model_validator(mode="after")
    def _validate_mode_binding(self) -> "CreateFolderRequest":
        if self.mode == "local":
            if not self.local_root_id:
                raise ValueError("local 模式必须提供 local_root_id")
            # Empty string ≡ unbound-at-root; store as NULL for stable reuse lookup.
            if self.local_subpath == "":
                self.local_subpath = None
        elif self.local_root_id is not None or self.local_subpath is not None:
            raise ValueError("cloud 模式不能绑定本地路径")
        return self


class UpdateFolderRequest(BaseModel):
    """Rename and/or re-parent — the local-mode binding stays immutable.

    ``parent_id`` is a *request* field, not storage: the server turns it into a
    ``rel_path`` prefix, which is the single source of truth for nesting
    (双模式工作区 §5.4). Omit it to leave the folder where it is; send ``null`` to
    move it to the top level.
    """

    name: str | None = None
    parent_id: str | None = None


class FolderSummary(BaseModel):
    id: str
    name: str
    mode: Literal["local", "cloud"]
    local_root_id: str | None = None
    local_subpath: str | None = None
    # Where the folder sits in the user-visible cloud tree, POSIX and relative to
    # the tree root (``设计/图标`` = 图标 nested in 设计). ``id`` remains the handle
    # every reference uses; this is the display / navigation coordinate and it
    # changes on rename or move.
    rel_path: str | None = None
    # Convenience projection of ``rel_path``'s prefix so clients can build the tree
    # without parsing paths. Derived, never stored — there is no ``parent_id``
    # column to drift out of sync with the path.
    parent_rel_path: str | None = None
    owner_user_id: str
    my_role: Literal["owner", "editor", "viewer"] = "owner"
    my_state: Literal["accepted", "pending"] = "accepted"
    # Invited people on this desk (pending + accepted). Owner is not in
    # folder_members, so 0 means a private desk. Sidebar only paints the
    # people mark on the owner's own cloud row when this is > 0.
    collaborator_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_folder(
        cls,
        folder,
        *,
        my_role: Literal["owner", "editor", "viewer"] = "owner",
        my_state: Literal["accepted", "pending"] = "accepted",
        collaborator_count: int = 0,
    ) -> "FolderSummary":
        from agentcore.workspace.cloud_tree import parent_rel_path

        rel_path = folder.rel_path or None
        return cls(
            id=folder.id,
            name=folder.name,
            mode="local" if folder.local_root_id else "cloud",
            local_root_id=folder.local_root_id,
            local_subpath=folder.local_subpath,
            rel_path=rel_path,
            parent_rel_path=(parent_rel_path(rel_path) or None) if rel_path else None,
            owner_user_id=folder.user_id,
            my_role=my_role,
            my_state=my_state,
            collaborator_count=collaborator_count,
            created_at=folder.created_at,
            updated_at=folder.updated_at,
        )


class InviteFolderMemberRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    role: Literal["editor", "viewer"] = "editor"


class UpdateFolderMemberRequest(BaseModel):
    role: Literal["editor", "viewer"]


class FolderMemberSummary(BaseModel):
    user_id: str
    role: Literal["owner", "editor", "viewer"]
    state: Literal["accepted", "pending"]
    invited_by: str | None = None
    joined_at: datetime | None = None
    display_name: str | None = None
    username: str | None = None


class FolderMemberListResponse(BaseModel):
    data: list[FolderMemberSummary]
    total: int


class DeletedFolderSummary(BaseModel):
    """One recoverable folder in「最近删除」."""

    id: str
    name: str
    mode: Literal["local", "cloud"]
    local_root_id: str | None = None
    local_subpath: str | None = None
    created_at: datetime
    deleted_at: datetime
    # When the retention sweeper is entitled to purge this project for good. The server
    # owns the arithmetic so a client never re-derives「还剩几天」from ``deleted_at`` and
    # a hard-coded window; the sweep runs on a 6-hour cadence, so treat this as the
    # earliest possible purge, not a promise.
    purge_at: datetime

    @classmethod
    def from_folder(cls, folder, *, purge_at: datetime) -> "DeletedFolderSummary":
        assert folder.deleted_at is not None  # repo only returns soft-deleted rows
        return cls(
            id=folder.id,
            name=folder.name,
            mode="local" if folder.local_root_id else "cloud",
            local_root_id=folder.local_root_id,
            local_subpath=folder.local_subpath,
            created_at=folder.created_at,
            deleted_at=folder.deleted_at,
            purge_at=purge_at,
        )


class DeletedFolderListResponse(BaseModel):
    """Recoverable projects, most recently deleted first.

    Only projects the user deleted appear — machine reclaims (a race loser's auto
    cloud desk) and projects soft-deleted before the recycle bin existed are omitted,
    as are projects already past retention (they are no longer restorable).
    ``retention_days`` mirrors ``workspace_retention_days``.

    ``total`` is every recoverable row in that window. ``data`` is a capped page, so
    emptying the bin follows ``total``, not ``len(data)``.
    """

    data: list[DeletedFolderSummary]
    total: int
    retention_days: int


class TrashEmptyResponse(BaseModel):
    """How much of one「最近删除」half a collection purge actually removed.

    ``purged`` is bin rows hard-deleted. ``skipped_busy`` is folders a live turn
    still holds — those stay in the bin. A restore that wins the race is neither:
    that row is already gone.
    """

    purged: int
    skipped_busy: int = 0


class FolderGroup(BaseModel):
    """A project plus the conversations it holds (grouped sidebar payload)."""

    id: str
    name: str
    mode: Literal["local", "cloud"]
    local_root_id: str | None = None
    local_subpath: str | None = None
    owner_user_id: str
    my_role: Literal["owner", "editor", "viewer"] = "owner"
    my_state: Literal["accepted", "pending"] = "accepted"
    collaborator_count: int = 0
    conversations: list[ConversationSummary]


class GroupedConversationsResponse(BaseModel):
    folders: list[FolderGroup]
    ungrouped: list[ConversationSummary]
