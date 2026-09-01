"""Document subsystem model (「一切皆文档」单表载体).

The Document subsystem is a single content tree that holds every AI-context artifact —
user rules, AI-maintained long-term memory, and ordinary documents — as rows in ONE
``documents`` table (Agent记忆与知识系统 §五 / 核心接口定义 §6.2「文件模型单表设计」). It is
not three tables: rule / memory / knowledge are the SAME entity (a markdown document) taking
different values on orthogonal metadata axes:

- ``ai_maintained`` (§5.2 谁写): ``false`` = a user-owned rule the AI may draft but never
  silently rewrite; ``true`` = AI-maintained long-term memory. Stays a **DB-only** column
  (never frontmatter) — it is writer identity, not entry content.
- scope (§5.3 位置即作用域): carried by ``folder_id`` — ``NULL`` = global root; a workspace
  ``Folder`` id = that project's layer. App-level ref, no DB FK (§6.2).
- ``apply_mode`` / ``description``: **derived indexes** of the md body's frontmatter
  (``apply`` / ``description``). Frontmatter is the sole writable source of truth; these
  columns are recomputed on every body write and must never be set by a bypass path.
- ``disputed_at`` (§纠错通道): the user marked this entry wrong → it stops being injected
  while the row and its body stay put. **DB-only**, same reason as ``ai_maintained``: the
  AI owns the body, so a frontmatter flag would be cleared by the next rewrite — exactly
  the failure this channel exists to prevent.
- ``disputed_lines`` (§纠错通道·行级): the same channel at bullet granularity — what the
  user saw was one sentence, so one sentence is what he gets to reject. The line moves out
  of ``content`` into this column instead of being flagged in place, because a bullet has
  no identity that survives a rewrite. Entry-level and line-level coexist: the former
  silences a whole entry, the latter one line of it.

``parent_id`` is the intra-tree parent; ``kind`` is the node type (folder / document;
``upload`` / ``base`` reserved). Soft-deleted (``deleted_at``); CAS via SHA-256 of the body.
"""

from datetime import datetime
from typing import TypedDict

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid

# Node types (核心接口定义 §6.2). Phase 1 uses folder / document; upload / base are reserved
# in the CheckConstraint so a later cut (uploads, the workspace base node) needs no schema churn.
DOCUMENT_KINDS = ("folder", "document", "upload", "base")
# AI-context roles (§5.2). ``rule`` (user rule XOR AI memory, split by ``ai_maintained``),
# ``general`` (ordinary document / memory sidecar), ``attachment`` reserved.
DOCUMENT_ROLES = ("rule", "general", "attachment")
# Injection strategies — two live values only (``conditional`` removed; was a dead reserved
# value with zero rows). Missing frontmatter ``apply`` defaults to ``on_demand``.
DOCUMENT_APPLY_MODES = ("always", "on_demand")


class DisputedLine(TypedDict):
    """One bullet the user rejected, as stored in ``Document.disputed_lines``.

    The shape is a type, not a convention: the row is written in one place
    (:func:`new_disputed_line`) and read in three (undo, the recovery list, and the
    entry's own body write), and every reader needs ``text`` to be there — a record
    nobody can restore from is the one failure this channel must not have.

    ``id`` is what an undo addresses. Position cannot be: rejecting three lines and then
    undoing the first shifts every later index down by one, so an index-addressed undo
    would put back a DIFFERENT line while the toast said it put back this one. The whole
    case for「一键、不弹确认」rests on the undo being exact.
    """

    id: str
    section: str
    text: str
    disputed_at: str


# Upper bound per entry (see ``disputed_lines``). The channel's own honest boundary is that
# the AI can re-learn a rejected fact and the user rejects it again — a steady loop with no
# natural end, so the record it produces needs an end of its own. Past the cap the OLDEST
# record is dropped rather than the new rejection refused: the correction stands either way
# (the line is out of the body), only the option of putting a long-forgotten one back expires.
MAX_DISPUTED_LINES = 50


def new_disputed_line(*, section: str, text: str) -> DisputedLine:
    """Mint one record — the single place a ``disputed_lines`` element is created."""
    return DisputedLine(
        id=_new_uuid(),
        section=section,
        text=text,
        disputed_at=datetime.now().isoformat(),
    )


class Document(Base):
    """One node in the user's Document tree (folder or content document).

    A ``rule``-role, ``ai_maintained=false`` document is a user rule; ``ai_maintained=true`` is
    AI memory — same table, same injection pipeline. Ordinary documents are ``general``. The
    tree is per-user; ``parent_id`` gives structure and ``folder_id`` gives the injection scope
    (NULL = global). No DB ForeignKey — references are app-level ``*_id`` fields (§6.2).
    """

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "kind in ('folder', 'document', 'upload', 'base')",
            name="ck_documents_kind",
        ),
        CheckConstraint(
            "role in ('rule', 'general', 'attachment')",
            name="ck_documents_role",
        ),
        CheckConstraint(
            "apply_mode in ('always', 'on_demand')",
            name="ck_documents_apply_mode",
        ),
        # Tree navigation ("children of a folder") + the memory store's per-scope note
        # lookups (all keyed by (user_id, parent_id)).
        Index("ix_documents_user_parent", "user_id", "parent_id"),
        # Scope-wide reads: rule injection (WHERE user_id, folder_id, role, apply_mode) and
        # the project-memory rail (distinct folder_id). folder_id NULL = the global layer.
        Index("ix_documents_user_folder", "user_id", "folder_id"),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), nullable=False)
    # Intra-tree parent (app-level FK). NULL = a top-level node of its scope (the user's
    # cloud root for the global layer, §5.3). A folder node's children carry its id here.
    parent_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False), nullable=True)
    # Injection scope (位置即作用域, §5.3): NULL = global (every conversation); a workspace
    # ``Folder`` id = that project's layer. Denormalized onto every node so a scope query is a
    # flat filter. App-level ref, not a DB FK: soft-deleting a folder does not cascade these
    # rows (restore must bring 设定 back). Injection skips a missing/soft-deleted folder;
    # permanent delete / retention purge physically remove the scope.
    folder_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False), nullable=True)
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'document'")
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'general'")
    )
    # §5.2 谁写：false = user-owned rule (AI may draft, never silently rewrite); true = AI
    # long-term memory. DB-only — never mirrored into frontmatter.
    ai_maintained: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # Derived index of frontmatter ``apply`` (缺席 → on_demand). Never write directly —
    # only via DocumentRepository body writes that recompute both derived columns.
    apply_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'on_demand'")
    )
    # Derived index of frontmatter ``description`` (缺席 / empty → "").
    description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    # 纠错通道: set when the USER explicitly marks this entry wrong (「这条不对」). Injection
    # and the on-demand catalog skip it; the row, its body and this timestamp stay so the
    # correction is traceable and reversible (no silent physical delete). Never set by AI.
    disputed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 行级纠错通道: bullets the user marked wrong one line at a time (:class:`DisputedLine`,
    # capped at ``MAX_DISPUTED_LINES``). The line is REMOVED from ``content`` when disputed
    # and re-inserted on undo — deliberately not a flag on the bullet in place: a bullet has
    # no identity in the BODY that survives a consolidation rewrite, and the mark must (same
    # reason ``disputed_at`` is a column, not frontmatter). The record row does carry an
    # ``id``, because the AI never rewrites this column — that id is how an undo names the
    # line. Moving the text out also means the next consolidation cannot read it back in.
    # Never written by AI.
    disputed_lines: Mapped[list[DisputedLine]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # Node name within its parent. For memory notes this is the store-relative path
    # (e.g. "画像.md", "主题/部署.md") so the (user, path, scope) seam maps 1:1 to a row.
    name: Mapped[str] = mapped_column(String(500), nullable=False, server_default=text("''"))
    # Markdown body ("" for folder nodes). Sole writable source for apply / description
    # via frontmatter. CAS is a SHA-256 of these bytes (memory.memory_version).
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
