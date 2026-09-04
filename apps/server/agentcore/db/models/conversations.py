"""Conversation domain models: Conversation, Folder, Message, ConversationShare."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid

# --- Conversations ---


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        # 一次发送只落一条会话。客户端并发多发 / 用户重按「新建」会把同一个意图发成
        # 两个 POST（线上实测最短间隔 14ms），两条各跑一整轮、双倍计费。客户端自铸
        # 的 ``client_request_id`` 是唯一去重依据（与 IM 的 ``client_msg_id`` 同款），
        # 唯一索引本身是并发下的最终裁判：先查后插会在两个连接间漏过。
        # 局部索引跳过不带键的行——老客户端不传键，必须照常一次一条地新建。
        Index(
            "uq_conversations_user_client_request",
            "user_id",
            "client_request_id",
            unique=True,
            postgresql_where=text("client_request_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    agent_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        default="00000000-0000-0000-0000-000000000000",
        server_default=text("'00000000-0000-0000-0000-000000000000'"),
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False, server_default=text("''"))
    # Sidebar housekeeping (对话基础功能补齐):
    # - ``pinned`` floats a conversation to the top of the sidebar / list (ordered
    #   pinned-first, then by recency).
    # - ``archived`` hides a conversation from the default sidebar / grouped list
    #   without deleting it; surfaced only in the「已归档」view, reversible.
    pinned: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    archived: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # Why this row is archived (最近删除 · 项目恢复). ``archived`` alone cannot tell a
    # user's own「归档」apart from the collateral archive a project soft-delete does,
    # so restoring the project would drag deliberately-archived chats back into the
    # sidebar. Set ONLY on rows that were still un-archived at project delete time,
    # and cleared when that project is restored. Rows soft-deleted before this column
    # existed carry false — those projects restore with their chats left archived.
    archived_by_folder_delete: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )
    mode: Mapped[str] = mapped_column(String(20), default="chat", server_default=text("'chat'"))
    # Permission axes (会话级权限 · 安全权限与治理):
    # {file_write, command, host}. Runtime gates read THIS column — not
    # users.autonomy_policy (which only seeds new conversations with a recipe).
    # Default = 少打断/托管: session + auto + session.
    # ``team_kickoff`` / ``command=kickoff`` were rewritten by
    # ``c1d8e4a7b2f6``; leftover keys are ignored / fail enum (no read-side merge).
    permission_axes: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {
            "file_write": "session",
            "command": "auto",
            "host": "session",
        },
        server_default=text(
            "'{\"file_write\":\"session\",\"command\":\"auto\",\"host\":\"session\"}'::jsonb"
        ),
    )
    # 深度研究自治（会话级独立旗标）: when True, CEO may auto-adopt worker motion_cards
    # and call debate (prompt-layer fork). Only this column enables that path.
    deep_research_auto: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )
    # Auto-adopted debates started under 深度研究自治 (explicit flag). Cap = 1
    # per session; over the limit ceo_format gracefully degrades (no error).
    deep_research_auto_debate_count: Mapped[int] = mapped_column(
        Integer, server_default=text("0")
    )
    # Session-level model combination pin (模型组合). New chats snapshot a profile
    # id at create time. NULL remains valid for legacy rows and expands via
    # account ``users.default_model_profile_id`` (live) — not the new-chat path.
    # Live reference into ``llm_model_profiles`` (or a virtual system preset id);
    # expanded at turn time via ``llm/model_profiles.py``.
    model_profile_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), nullable=True
    )
    # Project this conversation was born into; NULL = 裸聊 (ungrouped). App-level FK
    # (no DB constraint, per repo convention). Soft-deleting a project archives members
    # in place (keeps ``folder_id``); permanent wipe hard-deletes member rows.
    folder_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False), index=True, nullable=True)
    # Bare-chat silent auto cloud desk (写盘自动建云桌). Orthogonal to ``folder_id`` —
    # never auto-promotes affiliation / sidebar / memory scope. NULL until first
    # provision; reused across turns via ``ensure_bare_chat_auto_cloud_desk``.
    auto_desk_folder_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), nullable=True
    )
    # Desktop's intended local container root for a 裸聊, captured at creation
    # (NULL = cloud intent: web / mobile /「云端临时对话」). Used when resolving
    # effective local binding for ungrouped chats; ignored once ``folder_id`` is set
    # (project chats inherit the project's immutable binding). Auto-promote is vetoed —
    # locality is birth-time / explicit bind only (双模式工作区).
    local_container_root_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 裸聊 scratch workspace binding (per-conversation ``conv:<id>``). The desktop FS
    # root handle for THIS conversation's local scratch. NULL = cloud. Project chats
    # inherit binding from the Folder row instead — this column stays NULL for them.
    local_root_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Sub-path within ``local_root_id`` for the conversation's scratch workspace.
    # "" = the root itself (an explicitly-bound directory). A non-empty segment scopes
    # the workspace under a shared container root.
    local_subpath: Mapped[str | None] = mapped_column(String(400), nullable=True)
    # Long-term memory consolidation watermark (Agent记忆与知识系统 §1.5): the
    # created_at of the last message folded into the user's memory file by the
    # offline consolidation pass. NULL = never consolidated. The runner skips when
    # no message is newer than this, and the sweeper backstop selects conversations
    # whose latest message is newer than it (有未整合的新内容) yet has settled.
    memory_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Long-conversation compaction (执行引擎架构设计 §三 长对话压缩 / conversation/
    # compaction.py). A rolling summary folds turns OLDER than the recency window into
    # 已确立事实 / 决策 / 未决问题 / 文件路径, so a long chat feeds [summary] + recent
    # turns instead of the whole transcript — fighting context rot + cache-lapse cost,
    # not window overflow (DeepSeek's 1M does not overflow). Three columns, all NULL =
    # never compacted (the loader falls back to the plain recent window):
    #   compaction_summary       — the current rolling summary text
    #   compacted_through        — watermark: created_at of the last message folded in
    #   compaction_input_tokens  — the turn input tokens measured at the last (re)compaction
    # Computed OFF the turn by the token-triggered background pass, then REUSED across
    # turns (compute once, never per-turn) so the DeepSeek exact-prefix cache holds —
    # recomputing the prefix every turn would bust it (see runtime/resolve/prompt.py).
    compaction_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    compacted_through: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    compaction_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Client-minted idempotency key for「新建会话」(see the partial unique index above).
    # NULL = the caller did not send one (every client before this column shipped),
    # and those rows are exempt from the constraint rather than deduped by a guess.
    client_request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    # 最近活动 = 回合。Only ``touch_activity`` (message create / upsert_assistant)
    # restamps this. Rename / pin / compact / memory / soft-delete must not —
    # there is deliberately no ORM ``onupdate``.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- Folders ---
# Project = workspace (项目即工作区). Every folder owns a shared workspace:
# local (``local_root_id`` set) or cloud (both binding columns NULL → disk
# scope ``folder:<id>``). Conversations born into a folder inherit it; bare
# chats keep per-conversation ``conv:<id>`` scratch. Soft-deleted like
# conversations; soft-delete archives member conversations (does not ungroup).


class Folder(Base):
    __tablename__ = "folders"
    __table_args__ = (
        # 同层禁重名（双模式工作区 §5.4）。``rel_path`` 是云文件夹物理落点的单一
        # 真相源, 所以「同层唯一」就是「整条相对路径唯一」——父子关系由前缀表达,
        # 不存在第二个 parent_id 需要一起约束。局部索引跳过本机文件夹 (rel_path
        # NULL) 与软删行 (软删会把目录搬去墓碑区, 名字当场释放给新文件夹复用)。
        # 大小写不敏感: Windows / macOS 盘上 ``报告`` 与 ``报告`` 是同一个目录,
        # DB 放两行会让改名时的物理 mv 互相覆盖。
        Index(
            "uq_folders_user_rel_path_live",
            "user_id",
            text("lower(rel_path)"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND rel_path IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Cloud folder placement — POSIX path relative to the user's visible tree root
    # (``workspaces/<user>/tree/``), **the** single source of truth for where the
    # directory physically is. Nesting is expressed by prefix (``设计/图标`` sits
    # inside ``设计``); there is deliberately no ``parent_id`` to drift from it.
    # NULL = local-mode folder (its files live on the user's own disk; the
    # ``local_root_id`` + ``local_subpath`` branch below is unchanged).
    # Renames / moves rewrite this for the whole subtree in one transaction and
    # ``mv`` the directory; ``id`` stays put so standing tasks / memory / boards /
    # write-claim ledgers keep resolving.
    rel_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Local-mode binding: desktop FS root id. NULL + NULL ``local_subpath`` = cloud
    # project (shared ``folder:<id>`` scope). Opaque desktop handle (not a
    # server-owned UUID). Immutable after create (no relocate this period).
    local_root_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Sub-path within ``local_root_id``. NULL/"" = bound at the root itself.
    local_subpath: Mapped[str | None] = mapped_column(String(400), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Who asked for the soft-delete (最近删除 recycle bin). ``'user'`` = a deliberate
    # delete from the sidebar / CEO ``delete_folder``, the only kind the recycle bin
    # lists and restores. ``'auto_desk_reclaim'`` = a race loser's silently-minted
    # bare-chat cloud desk being reclaimed — machine litter that carries a
    # conversation-derived name and would otherwise read as a real project. NULL =
    # soft-deleted before this column existed; deliberately NOT backfilled, so a
    # brand-new recycle bin under-lists rather than surfacing old auto-desk junk.
    delete_origin: Mapped[str | None] = mapped_column(String(20), nullable=True)


class FolderMember(Base):
    """Collaboration-desk membership (双模式工作区 §八). Independent of IM chat_members.

    Owner is ``Folder.user_id``, not a row here. Invited members are editor/viewer
    with pending → accepted. Blocking auto-rejects pending; does not kick accepted.
    """

    __tablename__ = "folder_members"
    __table_args__ = (
        CheckConstraint(
            "role in ('editor', 'viewer')",
            name="ck_folder_members_role",
        ),
        CheckConstraint(
            "state in ('accepted', 'pending')",
            name="ck_folder_members_state",
        ),
        Index("ix_folder_members_user_id", "user_id"),
    )

    folder_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(
        String(20), default="pending", server_default=text("'pending'")
    )
    invited_by: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ConversationPreference(Base):
    """Per-user pin/archive for a conversation (对标 ChatMember.pinned).

    Conversation.pinned / archived stay as legacy columns; list and PATCH read
    this table so two members cannot stomp each other.
    """

    __tablename__ = "conversation_preferences"
    __table_args__ = (Index("ix_conversation_preferences_user_id", "user_id"),)

    conversation_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True)
    pinned: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    archived: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )


# --- Messages ---

# One visible 系统收口 user row per execution (rows at/after the timestamptz
# bound). Process-local ``harvest_scheduled`` does not survive restart /
# multi-instance; this index is the durable *claim*. Losing the insert means
# another process already claimed — skip only when a closing assistant already
# settled or a fresh turn lease is still beating. Historical pre-bound rows
# stay outside the predicate so CREATE INDEX can succeed without deleting them.
UQ_MESSAGES_EXECUTION_HARVEST = "uq_messages_execution_harvest"
_HARVEST_USER_EXECUTION_WHERE = (
    "role = 'user' "
    "AND usage ->> 'origin' = 'execution_harvest' "
    "AND COALESCE(usage ->> 'execution_id', '') <> '' "
    "AND created_at >= TIMESTAMPTZ '2026-08-18 06:00:00+00'"
)


def is_execution_harvest_conflict(exc: BaseException) -> bool:
    """True when ``exc`` is the harvest-user unique index losing the race."""
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    if name == UQ_MESSAGES_EXECUTION_HARVEST:
        return True
    return UQ_MESSAGES_EXECUTION_HARVEST in f"{exc} {orig or ''}"


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        # 全 App 最高频读形「按时序翻页一个对话」: 每个读路径都是
        # WHERE conversation_id = ? ORDER BY created_at [LIMIT ?]
        # (list_latest/list_before/list_after/list_recent/list_recent_after/
        #  list_all_for_conversation/delete_after/latest_created_at)。复合
        # (conversation_id, created_at) 让其走索引有序扫描 + LIMIT 提前停; 并按最左前缀
        # 覆盖「仅按 conversation_id 过滤」(counts_for_conversations / journal load_map 的
        # IN(...)), 故无需再单列索引 conversation_id (项目审计-成本性能专项 PERF-001)。
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        Index(
            UQ_MESSAGES_EXECUTION_HARVEST,
            text("(usage ->> 'execution_id')"),
            unique=True,
            postgresql_where=text(_HARVEST_USER_EXECUTION_WHERE),
        ),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    conversation_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # User-referenced attachments metadata (list of {name, path, truncated}).
    attachments: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # Conversation-page ``@`` team-role chips (soft mention). List of
    # {agent_id, role}. Orthogonal to ``attachments`` — never a
    # MessageAttachment.kind; never a hard-route. Empty [] on assistant /
    # legacy rows.
    agent_mentions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # Web sources consulted for this (assistant) message: list of
    # {url, title, snippet, site}. Rendered as source cards; UI-only metadata.
    citations: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # 回合调研台账（引用即出处 P1, DERIVED）：除 blocked 外全量登记（登记宽）；
    # 成稿闸用 deep_read∪selected。含 id/tier/query/deep_read/selected/doc_kind/
    # registrant/citable。与 citations 池正交；[] = legacy / 无台账。
    evidence_ledger: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # 历史「下一步推荐」chips 列：live mint / ``followups_generated`` 已下线，新回合写空
    # []；列保留给旧行回放。开辩入口走 stage_card。
    followups: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # 回合 ¥ 成本 (P2 DERIVED)：finalize 回写的 message_end.cost 快照（nano-CNY 分量 +
    # currency；cny_total 在读路径按 nano/1e9 投影为元）。与 followups/title 同辙——重载 footer
    # 直接用；hover 工资单明细仍走 GET /v1/messages/{id}/cost（cost_events 台账）。
    # NULL for user / unmetered / pre-feature rows.
    cost: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 回复反馈 (点赞/点踩, 对话基础功能补齐): the user's explicit satisfaction signal on an
    # assistant reply — "up" | "down" | NULL(未评价). Toggleable (re-clicking the same
    # side clears it back to NULL). Stored as a plain durable signal only; it does not
    # feed any runtime logic yet — the column exists so future quality analysis has a
    # first-class place to read from instead of being lost. NULL on user rows.
    feedback: Mapped[str | None] = mapped_column(String(4), nullable=True)
    # The turn's replay payload (team graph / single-agent 思考+工具 timeline) is NO
    # LONGER stored here — it is the唯一事实源 ``turn_journal`` table (§8.3 Turn
    # Journal), keyed by this message id, and PROJECTED into MessageDetail.runs on
    # read. See agentcore.runtime.journal.
    # Correlation key to the turn's runtime logs (logs/dev.jsonl): the assistant
    # message joins to its interaction's full log trace (chat.turn_*/llm/tool/...)
    # — message rows otherwise carry only UUIDs, so trace_id is what makes a turn
    # greppable from a persisted reply. NULL on user / untraced (handoff) messages.
    # 32-hex, minted by core/log_context.new_trace_id (not a DB-format uuid).
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # A1+ 回合文件 diff 基线：云端 labeled 快照 id；本地 sidecar 约定 id=message_id
    #（``AgentCore/baselines/{id}.zip``，可不经本列）。NULL = 未打基线 / 失败 / 旧行
    # → 前端降级工具参数预览。
    baseline_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


# --- Conversation shares (公开只读分享链接: 对标 ChatGPT 分享) ---
# A public, read-only link to a snapshot of a conversation. 分享 is an explicit,
# opt-in action (隐私承诺: 分享 = 显式操作、操作前明示), so a row only exists once
# the owner创建分享. The transcript is FROZEN into ``snapshot`` at share time
# (所见即所享): the public page renders that copy, so later edits/deletes to the
# live messages never leak into a shared link, and no future turns are exposed.
# Content-only by design — the snapshot holds just role + content + timestamp, never
# reasoning / cost / team graph / files (those are private). The row id doubles as
# the unguessable URL token (uuid4 = 122 bits). Revoked (not hard-deleted) so a
# killed link 404s immediately while the audit trail survives; cascade-revoked when
# the conversation is deleted or the account is注销 (ownership lifecycle).


class ConversationShare(Base):
    __tablename__ = "conversation_shares"
    __table_args__ = (
        # The owner's "manage shares for this conversation" list.
        Index("ix_conversation_shares_conversation", "conversation_id"),
        # Account-注销 cascade revokes every share a user created.
        Index("ix_conversation_shares_user", "user_id"),
    )

    # PK doubles as the public share token (uuid4, unguessable) — the public URL is
    # ``/shared/<id>``. No separate token column needed (consistent with repo PKs).
    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    # The shared conversation + its owner (app-level FKs, per repo convention).
    conversation_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    # Conversation title captured at share time (the public page heading), frozen
    # alongside the transcript so a later rename doesn't change a live link.
    title: Mapped[str] = mapped_column(String(500), server_default=text("''"))
    # The frozen, content-only transcript: a list of {role, content, created_at(iso)}
    # for the user/assistant turns at share time. Immutable — the public render reads
    # this, never the live messages (所见即所享 + no future-turn leak).
    snapshot: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    # Optional auto-expiry (security default: 30d at create). NULL = never expires.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set when the owner revokes the link (or a cascade does); a revoked share 404s
    # on the public page. Soft (not a row delete) so revocation is observable and the
    # link can never silently reactivate.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- Memory updates (记忆更新对话内可见: Agent记忆与知识系统 §1.6 实时提示) ---
# One semantic/quota write notice, anchored to the conversation that triggered it,
# so the thread can show a「记忆已更新」card — what the AI remembered FROM this
# conversation. Session digests are not cards. Consolidation runs OFF the turn
# path (memory/consolidation.py), AFTER the turn + its turn_journal are already persisted,
# and is conversation-level (it folds a window of turns), so it is its OWN record — not a
# per-turn ``turn_journal`` fact: a dedicated row keyed by conversation_id (never a message
# id), projected into the messages-window read (latest page only) + pushed live on the
# per-user firehose.


class MemoryUpdateRow(Base):
    """One memory write notice anchored to a conversation.

    Written when a semantic consolidation lands with add/update/remove items, or
    the always-pool / billing skip needs a ``quota`` card — never for a session digest
    (those stay in ``memory_episodes``). ``kind`` selects the UI card: ``semantic``
    (diff ``items``) or ``quota`` (always-pool / billing skip). ``items`` is a list of
    ``{action, file, section, scope, content, target}``.

    **Lifecycle** (no DB FK — app-level cascade, per repo convention): dropped with its
    conversation on hard-delete (``ConversationRepository.hard_delete``). NOT tied to any
    message id (it post-dates the whole window), so message delete / regenerate never touch
    it — a re-run doesn't un-remember what an earlier pass already learned.

    ``anchor_at`` is a display ordering hint that does NOT change any of that: a plain
    timestamp, deliberately not a message id, so deleting the message it points near can
    never dangle it or take the card down with it.
    """

    __tablename__ = "memory_updates"
    __table_args__ = (
        # The conversation-tail card read (newest-first) + whole-conversation cascade.
        Index("ix_memory_updates_conversation", "conversation_id", "created_at"),
        # Account-注销 cascade + future per-user「记忆动态」feed.
        Index("ix_memory_updates_user", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    conversation_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    # "semantic" | "quota" — card shape for the conversation-tail / feed.
    kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'semantic'"))
    # Semantic: usually null. Quota: why the write was skipped.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Semantic applied changes: list of {action, file, section, scope, content, target}.
    # Shape owned by memory/maintenance.py MemoryUpdateItem.
    items: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # created_at of the LAST message this pass consolidated — where the card belongs in
    # the thread. ``created_at`` alone cannot answer that: consolidation fires on an
    # idle debounce, so the card is written after the window it covers, and by
    # then newer turns may already sit below it. NULL for rows written before this
    # column existed and for writes with no message window (leak-scan, quota).
    anchor_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


# --- Message bookmarks (消息收藏: 对话内消息 bookmark → 侧栏「已收藏」) ---
# A user's saved pointer to one message, so an important reply can be found again
# from any device (跨设备 = server-stored, fetched on demand — not a device-local
# star). Per-user and message-level: the (user_id, message_id) pair is unique, so
# re-bookmarking is idempotent and un-bookmarking is a single delete. No DB FK
# (app-level cascade, per repo convention): the row is dropped when its message /
# conversation is hard-deleted (regenerate / single-message delete / conversation
# purge), and the「已收藏」list INNER JOINs live messages+conversations so a
# not-yet-cascaded or soft-deleted-conversation row never renders anyway.


class MessageBookmark(Base):
    __tablename__ = "message_bookmarks"
    __table_args__ = (
        # One bookmark per user per message; re-adding the same pair is a no-op.
        UniqueConstraint(
            "user_id", "message_id", name="uq_message_bookmarks_user_message"
        ),
        # The「已收藏」list read: a user's bookmarks, newest-first.
        Index("ix_message_bookmarks_user_created", "user_id", "created_at"),
        # Per-conversation star-state read + conversation-purge cascade.
        Index("ix_message_bookmarks_conversation", "conversation_id"),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    # The bookmarking user (app-level FK → users; account注销 cascades these rows).
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    # The owning conversation (denormalized so a jump / star-state / purge cascade
    # needs no message round-trip).
    conversation_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    # The bookmarked message.
    message_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


# --- External directory grants (W3 区外授权: 对话级持久, 非进程生命周期) ---
# Server holds alias / root_id / label / mode only. Absolute OS paths stay on the
# desktop (``fs-session-grants.json``). Orthogonal to workspace binding. Cleared on
# revoke / conversation soft-delete / hard-delete cascade.


class ConversationExternalGrant(Base):
    """One conversation-scoped external directory grant under ``external/<alias>/``.

    **Lifecycle** (no DB FK — app-level, per repo convention): created/updated via
    ``POST …/external-grants``; dropped on revoke, soft-delete clear, or
    ``ConversationRepository.hard_delete`` cascade. Desktop reconciles root_id ↔
    local path on open; orphans without a desktop path are revoked server-side.
    """

    __tablename__ = "conversation_external_grants"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "alias",
            name="uq_conversation_external_grants_conv_alias",
        ),
        UniqueConstraint(
            "conversation_id",
            "root_id",
            name="uq_conversation_external_grants_conv_root",
        ),
        Index("ix_conversation_external_grants_conversation", "conversation_id"),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    conversation_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    alias: Mapped[str] = mapped_column(String(64), nullable=False)
    # Desktop authorized-root handle (never an absolute path).
    root_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Which install holds the folder — captured from the registering request's
    # ``X-Client-Device``. Routing a mount op is「哪台机器上有这个目录」, and only
    # this row knows: the device's fulfill session is rebuilt on every reconnect,
    # so the binding is re-seeded from here (``GET /v1/fulfill``). NULL = the
    # registration carried no device (pre-binding rows, non-desktop callers).
    device_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    label: Mapped[str] = mapped_column(String(500), nullable=False, server_default=text("''"))
    # "readonly" | "organize" | "attach_rw"
    mode: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'readonly'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
