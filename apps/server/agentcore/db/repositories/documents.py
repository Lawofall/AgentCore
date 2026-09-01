"""Document tree data access (「一切皆文档」单表载体, 核心接口定义 §6.2).

One repository over the single ``documents`` table. It serves three consumers that all
share the same rows:

- **Memory store backing**: AI-maintained long-term memory (``ai_maintained=true``) notes
  live under the per-(user, scope) convention tree ``AgentCore/记忆/``, addressed by their
  store-relative ``name`` ("画像.md", "主题/部署.md", …). ``DocumentMemoryStore`` maps the
  ``(user, path, scope)`` seam onto these rows (Agent记忆与知识系统 §5.0 / §5.7).
- **Rule injection**: both user rules (``ai_maintained=false``) and the always-injected memory
  core (``ai_maintained=true``) are ``role='rule', apply_mode='always'`` nodes, gathered per
  scope by ``list_injectable_rules`` for the two-tier ``<设定>`` block (§二). Collection
  stays role + folder_id + apply_mode (not parent-tree walk); when the convention dirs exist,
  results are further restricted to ``AgentCore/规则/`` / ``AgentCore/记忆/`` (bare ``记忆/``
  still accepted for memory during transition). Writes land under ``AgentCore/{规则,记忆}/``.
- **Generic tree CRUD**: the ``/documents`` API creates / reads / renames / moves / deletes any
  node (user rules are just ``role='rule', ai_maintained=false`` documents, §5.2).

**Frontmatter is the sole writable source** for ``apply``; DB ``apply_mode`` is a
derived index recomputed only via ``_set_content_and_derive``. ``description`` is
mirrored from frontmatter on body writes; async AI fill may set the column alone
when frontmatter has none (never mutates ``content``). ``ai_maintained`` stays DB-only.

All reads filter ``deleted_at IS NULL`` explicitly (this codebase has no global soft-delete
event listener — 照 boards.py / folders.py). Owner scoping is the structural default: mutations
resolve a node owner-scoped so a non-owner id is treated as absent (SEC-002). No DB FK — refs
are app-level ``*_id`` fields (§6.2). CAS is the caller's job (content-hash baseline under the
per-user memory lock, 照 api/routes/memory.py) so the repo stays db-only, no upward import.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal, cast

from sqlalchemy import Select, and_, delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from agentcore.core.types import new_id
from agentcore.db.models import DisputedLine, Document
from agentcore.db.models.documents import MAX_DISPUTED_LINES
from agentcore.documents.frontmatter import (
    FrontmatterEditError,
    FrontmatterError,
    ensure_apply_key,
    parse_entry_frontmatter,
    set_entry_frontmatter,
)

from ._base import _UNSET, commit_or_flush

# Cloud-documents convention root (Agent记忆与知识系统 §5.0). NOT the desktop local default
# path ``~/Documents/AgentCore/`` (workspace container) — same product name, different carrier.
AGENTCORE_ROOT_NAME = "AgentCore"

# User-owned rules directory under the convention root (§5.0 ``AgentCore/规则/``).
RULES_DIR_NAME = "规则"

# AI-memory notes folder under the convention root (§5.0 ``AgentCore/记忆/``). Reserved: a
# user's own folder is ``ai_maintained=false``, so it never collides with this node.
MEMORY_ROOT_NAME = "记忆"

# The canonical user-rule document ``remember`` appends to when the user gives an explicit
# directive (§5.7 用户规则入口①). Additional user-rule docs may be created via the tree API;
# injection gathers them all, this is only the well-known target for the tool path.
USER_RULES_DOC_NAME = "用户规则.md"


def _scope_clause(folder_id: str | None) -> ColumnElement[bool]:
    """WHERE fragment for a scope: NULL = the global layer, else that project's ``folder_id``."""
    if folder_id is None:
        return Document.folder_id.is_(None)
    return Document.folder_id == folder_id


def _derive_indexes(content: str) -> tuple[str, str]:
    """Recompute ``(apply_mode, description)`` from body frontmatter.

    Parse failure → index as ``on_demand`` / ``""`` so always-injection queries exclude the
    row; content is **not** repaired (猜默认值自动修复否决).
    """
    parsed = parse_entry_frontmatter(content)
    if isinstance(parsed, FrontmatterError):
        return "on_demand", ""
    return parsed.apply, parsed.description


def _replace_body_keeping_frontmatter(existing: str, new_body: str) -> str | None:
    """Keep the exact frontmatter block of ``existing``; swap only the body bytes.

    Returns ``None`` when ``existing`` has no well-formed frontmatter (caller seeds anew).
    Text-level splice — never parse-then-serialize — so unknown keys / comments / order survive.
    """
    parsed = parse_entry_frontmatter(existing)
    if isinstance(parsed, FrontmatterError) or not parsed.has_frontmatter:
        return None
    prefix_len = len(existing) - len(parsed.body)
    return existing[:prefix_len] + new_body


def _memory_note_body_for_write(
    content: str, *, existing: str | None, apply_mode: str
) -> str:
    """Prepare memory-note markdown for upsert.

    Body-only writers (legacy ``/users/me/memory`` editor) must not wipe stored
    ``description`` / opaque FM lines: when the incoming text has no frontmatter and a
    prior note does, keep that block and replace only the body. Incoming text that already
    carries frontmatter is authoritative (consolidation / full-doc writers).
    """
    mode = apply_mode if apply_mode in ("always", "on_demand") else "on_demand"
    incoming = parse_entry_frontmatter(content)
    if isinstance(incoming, FrontmatterError):
        raise FrontmatterEditError(incoming.message)
    if not incoming.has_frontmatter and existing:
        preserved = _replace_body_keeping_frontmatter(existing, content)
        if preserved is not None:
            content = preserved
    return ensure_apply_key(content, mode)  # type: ignore[arg-type]


class DocumentRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    @staticmethod
    def _set_content_and_derive(doc: Document, content: str) -> None:
        """Sole body-write path: persist markdown and recompute derived index columns.

        Folders keep empty body / empty description; ``apply_mode`` is irrelevant for them
        (left untouched). Document ``apply_mode`` is only set here. ``description`` is
        re-derived from frontmatter here too — clearing a prior AI column-only fill when
        the body still has no frontmatter ``description`` (stale → regenerate).
        """
        doc.content = content
        if doc.kind != "document":
            doc.description = ""
            return
        apply_mode, description = _derive_indexes(content)
        doc.apply_mode = apply_mode
        doc.description = description

    # --- AgentCore/ convention tree (§5.0) -----------------------------------------------------

    def _agentcore_root_stmt(self, user_id: str, folder_id: str | None) -> Select:
        return select(Document).where(
            Document.user_id == user_id,
            _scope_clause(folder_id),
            Document.parent_id.is_(None),
            Document.kind == "folder",
            Document.name == AGENTCORE_ROOT_NAME,
            Document.deleted_at.is_(None),
        )

    async def get_agentcore_root(
        self, user_id: str, folder_id: str | None
    ) -> Document | None:
        """The per-scope ``AgentCore/`` convention root, or None if none exists yet."""
        result = await self._session.execute(self._agentcore_root_stmt(user_id, folder_id))
        return result.scalars().first()

    async def ensure_agentcore_root(self, user_id: str, folder_id: str | None) -> Document:
        """Find-or-create the per-scope ``AgentCore/`` convention root (user-visible)."""
        root = await self.get_agentcore_root(user_id, folder_id)
        if root is not None:
            return root
        root = Document(
            id=new_id(),
            user_id=user_id,
            parent_id=None,
            folder_id=folder_id,
            kind="folder",
            role="general",
            ai_maintained=False,
            name=AGENTCORE_ROOT_NAME,
            content="",
        )
        self._session.add(root)
        await self._session.flush()
        return root

    async def get_rules_dir(self, user_id: str, folder_id: str | None) -> Document | None:
        """The ``AgentCore/规则/`` folder for one scope, or None."""
        ac = await self.get_agentcore_root(user_id, folder_id)
        if ac is None:
            return None
        result = await self._session.execute(
            select(Document).where(
                Document.user_id == user_id,
                Document.parent_id == ac.id,
                Document.kind == "folder",
                Document.name == RULES_DIR_NAME,
                Document.deleted_at.is_(None),
            )
        )
        return result.scalars().first()

    async def ensure_rules_dir(self, user_id: str, folder_id: str | None) -> Document:
        """Find-or-create ``AgentCore/规则/`` for one scope (user-owned)."""
        existing = await self.get_rules_dir(user_id, folder_id)
        if existing is not None:
            return existing
        ac = await self.ensure_agentcore_root(user_id, folder_id)
        rules = Document(
            id=new_id(),
            user_id=user_id,
            parent_id=ac.id,
            folder_id=folder_id,
            kind="folder",
            role="general",
            ai_maintained=False,
            name=RULES_DIR_NAME,
            content="",
        )
        self._session.add(rules)
        await self._session.flush()
        return rules

    # --- memory store backing (ai_maintained=true notes under AgentCore/记忆/) ---

    async def _legacy_bare_memory_root(
        self, user_id: str, folder_id: str | None
    ) -> Document | None:
        """Pre-§5.0 bare ``记忆/`` at scope root (``parent_id IS NULL``) — migration source."""
        result = await self._session.execute(
            select(Document).where(
                Document.user_id == user_id,
                _scope_clause(folder_id),
                Document.parent_id.is_(None),
                Document.kind == "folder",
                Document.ai_maintained.is_(True),
                Document.name == MEMORY_ROOT_NAME,
                Document.deleted_at.is_(None),
            )
        )
        return result.scalars().first()

    async def get_memory_root(self, user_id: str, folder_id: str | None) -> Document | None:
        """The ``记忆`` folder for one (user, scope), or None if none exists yet.

        Prefers ``AgentCore/记忆/``; falls back to a pre-migration bare ``记忆/`` so reads
        keep working until the idempotent layout migration reparents it.
        """
        ac = await self.get_agentcore_root(user_id, folder_id)
        if ac is not None:
            result = await self._session.execute(
                select(Document).where(
                    Document.user_id == user_id,
                    Document.parent_id == ac.id,
                    Document.kind == "folder",
                    Document.ai_maintained.is_(True),
                    Document.name == MEMORY_ROOT_NAME,
                    Document.deleted_at.is_(None),
                )
            )
            under = result.scalars().first()
            if under is not None:
                return under
        return await self._legacy_bare_memory_root(user_id, folder_id)

    async def _ensure_memory_root(self, user_id: str, folder_id: str | None) -> Document:
        """Find-or-create ``AgentCore/记忆/`` (reparents a bare ``记忆/`` when present)."""
        root = await self.get_memory_root(user_id, folder_id)
        ac = await self.ensure_agentcore_root(user_id, folder_id)
        if root is not None:
            if root.parent_id != ac.id:
                # Legacy bare root still at scope top — hoist into the convention tree.
                root.parent_id = ac.id
                await self._session.flush()
            return root
        root = Document(
            id=new_id(),
            user_id=user_id,
            parent_id=ac.id,
            folder_id=folder_id,
            kind="folder",
            role="general",
            ai_maintained=True,
            name=MEMORY_ROOT_NAME,
            content="",
        )
        self._session.add(root)
        await self._session.flush()
        return root

    async def get_memory_note(
        self,
        user_id: str,
        name: str,
        folder_id: str | None,
        *,
        include_deleted: bool = False,
    ) -> Document | None:
        """One memory note by its store-relative ``name`` under the scope's 记忆 root.

        Live rows only by default. ``include_deleted=True`` also matches soft-deleted
        notes — used by the file→document migration so a user-deleted note is not
        re-imported from a leftover on-disk source (treated as already recorded).
        """
        root = await self.get_memory_root(user_id, folder_id)
        if root is None:
            return None
        conditions: list[ColumnElement[bool]] = [
            Document.user_id == user_id,
            Document.parent_id == root.id,
            Document.name == name,
        ]
        if not include_deleted:
            conditions.append(Document.deleted_at.is_(None))
        result = await self._session.execute(select(Document).where(*conditions))
        return result.scalars().first()

    async def save_memory_note(
        self,
        user_id: str,
        name: str,
        content: str,
        folder_id: str | None,
        *,
        role: str,
        apply_mode: str,
        writer: str = "ai",
    ) -> Document:
        """Upsert one memory note (creating ``AgentCore/记忆/`` on first write).

        ``apply_mode`` seeds frontmatter only when the body lacks an ``apply`` key
        (归并不改已有生效档). Body-only updates keep the stored frontmatter block
        (see ``_memory_note_body_for_write``). Derived columns always come from the body.

        ``writer`` is ``"ai"`` (default — consolidation / tools) or ``"user"`` (memory
        editor). Always-pool quota: AI growth past the cap raises
        :class:`~agentcore.memory.always_quota.AlwaysQuotaExceededError`; user edits of an
        existing always entry are allowed (warning is the caller's job on the documents
        API — this path does not surface warnings).
        """
        from agentcore.memory.always_quota import (
            AlwaysQuotaExceededError,
            always_entry_chars,
            check_always_write,
        )

        root = await self._ensure_memory_root(user_id, folder_id)
        note = await self.get_memory_note(user_id, name, folder_id)
        body = _memory_note_body_for_write(
            content,
            existing=note.content if note is not None else None,
            apply_mode=apply_mode,
        )
        derived_apply, _ = _derive_indexes(body)
        if role == "rule" and derived_apply == "always":
            existing_always = (
                note is not None and note.role == "rule" and note.apply_mode == "always"
            )
            who: Literal["user", "ai"] = "user" if writer == "user" else "ai"
            decision = await check_always_write(
                self,
                user_id,
                folder_id=folder_id,
                writer=who,
                editing_existing_always=existing_always,
                exclude_id=note.id if note is not None else None,
                new_content=body,
                new_is_always=True,
            )
            if not decision.allowed:
                usage = decision.usage
                assert usage is not None
                raise AlwaysQuotaExceededError(
                    usage,
                    decision.message,
                    file=name,
                    scope=folder_id,
                    attempted_chars=always_entry_chars(body),
                )
        if note is None:
            note = Document(
                id=new_id(),
                user_id=user_id,
                parent_id=root.id,
                folder_id=folder_id,
                kind="document",
                role=role,
                ai_maintained=True,
                name=name,
                content="",
            )
            self._session.add(note)
            self._set_content_and_derive(note, body)
        else:
            note.role = role
            self._set_content_and_derive(note, body)
        await self._session.commit()
        await self._session.refresh(note)
        return note

    async def delete_memory_note(self, user_id: str, name: str, folder_id: str | None) -> None:
        """Soft-delete one memory note (no-op if it does not exist)."""
        note = await self.get_memory_note(user_id, name, folder_id)
        if note is None:
            return
        note.deleted_at = datetime.now()
        await self._session.commit()

    async def list_memory_notes(self, user_id: str, folder_id: str | None) -> list[Document]:
        """All live memory notes under the scope's 记忆 root (empty when none)."""
        root = await self.get_memory_root(user_id, folder_id)
        if root is None:
            return []
        result = await self._session.execute(
            select(Document)
            .where(
                Document.user_id == user_id,
                Document.parent_id == root.id,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.name.asc())
        )
        return list(result.scalars().all())

    async def list_memory_project_scopes(self, user_id: str) -> list[str]:
        """``folder_id``s whose PROJECT memory layer holds a semantic (non-episodic) note.

        Mirrors ``FileMemoryStore.project_scopes``: a folder surfaces a「本文件夹记忆」node
        only where there is a real note to edit — consolidation-pipeline rows
        (``memory_episodes`` / scope state) do not count. Notes carry ``role='rule'`` for
        the 偏好/画像/主题 core, so a rule-role project note is the「has semantic memory」signal.
        """
        result = await self._session.execute(
            select(Document.folder_id)
            .where(
                Document.user_id == user_id,
                Document.folder_id.is_not(None),
                Document.ai_maintained.is_(True),
                Document.role == "rule",
                Document.kind == "document",
                Document.deleted_at.is_(None),
            )
            .distinct()
        )
        return sorted(str(fid) for fid in result.scalars().all() if fid)

    # --- rule injection (memory core + user rules are both role='rule') ---

    async def _injectable_parent_filter(
        self, user_id: str, folder_id: str | None, *, ai_maintained: bool
    ) -> ColumnElement[bool] | None:
        """Restrict injectables to the convention tree when that tree already exists.

        No convention dir → ``None`` (legacy scope-wide collect; avoids half-migration empty
        reads). User rules require ``parent_id == AgentCore/规则/``. Memory always-cores require
        ``AgentCore/记忆/``; a still-live bare ``记忆/`` parent is also accepted (transition /
        name-clash leftovers).
        """
        if ai_maintained:
            ac = await self.get_agentcore_root(user_id, folder_id)
            under: Document | None = None
            if ac is not None:
                result = await self._session.execute(
                    select(Document).where(
                        Document.user_id == user_id,
                        Document.parent_id == ac.id,
                        Document.kind == "folder",
                        Document.ai_maintained.is_(True),
                        Document.name == MEMORY_ROOT_NAME,
                        Document.deleted_at.is_(None),
                    )
                )
                under = result.scalars().first()
            if under is None:
                return None
            bare = await self._legacy_bare_memory_root(user_id, folder_id)
            if bare is not None:
                return or_(Document.parent_id == under.id, Document.parent_id == bare.id)
            return Document.parent_id == under.id

        rules_dir = await self.get_rules_dir(user_id, folder_id)
        if rules_dir is None:
            return None
        return Document.parent_id == rules_dir.id

    async def list_injectable_rules(
        self, user_id: str, folder_id: str | None, *, ai_maintained: bool | None
    ) -> list[Document]:
        """Always-injected ``rule`` docs of one scope + authorship (§二 two-tier injection).

        ``ai_maintained=True`` → the memory core (偏好.md / 画像.md); ``False`` → the user's own
        rule documents; ``None`` → both (one query; user rules first, then AI — same order as
        two sequential calls). ``apply_mode='on_demand'`` topics are excluded (they ride the
        directory, not ``<设定>``). Ordered by ``name`` for a stable prefix (and by
        ``ai_maintained`` when both). When convention dirs exist, only nodes under those
        parents are returned (see ``_injectable_parent_filter``).

        User-disputed entries are excluded here, which is also why they stop counting toward
        the always quota: the pool measures what actually rides the prompt.
        """
        conditions: list[ColumnElement[bool]] = [
            Document.user_id == user_id,
            _scope_clause(folder_id),
            Document.role == "rule",
            Document.apply_mode == "always",
            Document.kind == "document",
            Document.deleted_at.is_(None),
            Document.disputed_at.is_(None),
        ]
        order: tuple[ColumnElement[Any], ...]
        if ai_maintained is None:
            user_pf = await self._injectable_parent_filter(
                user_id, folder_id, ai_maintained=False
            )
            mem_pf = await self._injectable_parent_filter(
                user_id, folder_id, ai_maintained=True
            )
            user_branch: ColumnElement[bool] = Document.ai_maintained.is_(False)
            if user_pf is not None:
                user_branch = and_(user_branch, user_pf)
            mem_branch: ColumnElement[bool] = Document.ai_maintained.is_(True)
            if mem_pf is not None:
                mem_branch = and_(mem_branch, mem_pf)
            conditions.append(or_(user_branch, mem_branch))
            order = (Document.ai_maintained.asc(), Document.name.asc())
        else:
            conditions.append(Document.ai_maintained.is_(ai_maintained))
            parent_filter = await self._injectable_parent_filter(
                user_id, folder_id, ai_maintained=ai_maintained
            )
            if parent_filter is not None:
                conditions.append(parent_filter)
            order = (Document.name.asc(),)
        result = await self._session.execute(
            select(Document).where(*conditions).order_by(*order)
        )
        return list(result.scalars().all())

    async def list_on_demand_user_rules(
        self, user_id: str, folder_id: str | None
    ) -> list[Document]:
        """On-demand user-rule docs of one scope (``ai_maintained=false``, not memory topics).

        These ride the「规则目录」+ ``consult_rule`` — never the always ``<设定>`` budget.
        Same convention-parent filter as :meth:`list_injectable_rules` for user rules,
        and the same user-disputed exclusion (a disputed entry leaves the catalog too).
        """
        conditions: list[ColumnElement[bool]] = [
            Document.user_id == user_id,
            _scope_clause(folder_id),
            Document.role == "rule",
            Document.apply_mode == "on_demand",
            Document.ai_maintained.is_(False),
            Document.kind == "document",
            Document.deleted_at.is_(None),
            Document.disputed_at.is_(None),
        ]
        parent_filter = await self._injectable_parent_filter(
            user_id, folder_id, ai_maintained=False
        )
        if parent_filter is not None:
            conditions.append(parent_filter)
        result = await self._session.execute(
            select(Document).where(*conditions).order_by(Document.name.asc())
        )
        return list(result.scalars().all())

    # --- user rules (ai_maintained=false, role=rule) ---

    async def get_user_rules_doc(
        self, user_id: str, folder_id: str | None
    ) -> Document | None:
        """The canonical user-rule document for a scope (``remember`` target), or None.

        Prefers a doc under ``AgentCore/规则/``; falls back to any same-name live rule in
        the scope (pre-migration top-level) so append/dedupe keeps working across layout.
        """
        rules_dir = await self.get_rules_dir(user_id, folder_id)
        if rules_dir is not None:
            result = await self._session.execute(
                select(Document).where(
                    Document.user_id == user_id,
                    Document.parent_id == rules_dir.id,
                    Document.role == "rule",
                    Document.ai_maintained.is_(False),
                    Document.name == USER_RULES_DOC_NAME,
                    Document.deleted_at.is_(None),
                )
            )
            under = result.scalars().first()
            if under is not None:
                return under
        result = await self._session.execute(
            select(Document).where(
                Document.user_id == user_id,
                _scope_clause(folder_id),
                Document.role == "rule",
                Document.ai_maintained.is_(False),
                Document.name == USER_RULES_DOC_NAME,
                Document.deleted_at.is_(None),
            )
        )
        return result.scalars().first()

    async def upsert_user_rules_doc(
        self, user_id: str, folder_id: str | None, content: str
    ) -> Document:
        """Create-or-update the canonical user-rule document under ``AgentCore/规则/``.

        ``remember`` keeps the canonical doc on ``apply: always`` (forced into frontmatter).
        """
        doc = await self.get_user_rules_doc(user_id, folder_id)
        rules_dir = await self.ensure_rules_dir(user_id, folder_id)
        body = set_entry_frontmatter(content, apply="always")
        if doc is None:
            doc = Document(
                id=new_id(),
                user_id=user_id,
                parent_id=rules_dir.id,
                folder_id=folder_id,
                kind="document",
                role="rule",
                ai_maintained=False,
                name=USER_RULES_DOC_NAME,
                content="",
            )
            self._session.add(doc)
            self._set_content_and_derive(doc, body)
        else:
            if doc.parent_id != rules_dir.id:
                doc.parent_id = rules_dir.id
            self._set_content_and_derive(doc, body)
        await self._session.commit()
        await self._session.refresh(doc)
        return doc

    async def list_top_level_user_rules(
        self, user_id: str, folder_id: str | None
    ) -> list[Document]:
        """Live user-rule docs still at scope root (``parent_id IS NULL``) — migration sources."""
        result = await self._session.execute(
            select(Document)
            .where(
                Document.user_id == user_id,
                _scope_clause(folder_id),
                Document.parent_id.is_(None),
                Document.kind == "document",
                Document.role == "rule",
                Document.ai_maintained.is_(False),
                Document.deleted_at.is_(None),
            )
            .order_by(Document.name.asc())
        )
        return list(result.scalars().all())

    # --- generic tree CRUD (the /documents API; user rules are role=rule docs) ---

    async def create(
        self,
        user_id: str,
        *,
        name: str,
        parent_id: str | None = None,
        folder_id: str | None = None,
        kind: str = "document",
        role: str = "general",
        ai_maintained: bool = False,
        apply_mode: str = "on_demand",
        content: str = "",
    ) -> Document:
        """Create one tree node. For documents, ``apply_mode`` is written into frontmatter
        then derived — never stored as an independent writable copy."""
        doc = Document(
            id=new_id(),
            user_id=user_id,
            parent_id=parent_id,
            folder_id=folder_id,
            kind=kind,
            role=role,
            ai_maintained=ai_maintained,
            name=name,
            content="",
        )
        self._session.add(doc)
        if kind == "document":
            mode = apply_mode if apply_mode in ("always", "on_demand") else "on_demand"
            try:
                body = set_entry_frontmatter(content, apply=mode)  # type: ignore[arg-type]
            except FrontmatterEditError as exc:
                raise ValueError(str(exc)) from exc
            self._set_content_and_derive(doc, body)
        else:
            doc.content = ""
            doc.description = ""
        await self._session.commit()
        await self._session.refresh(doc)
        return doc

    async def get(self, document_id: str, *, user_id: str) -> Document | None:
        """Owner-scoped fetch (non-owner / unknown id → None → route 404; SEC-002)."""
        result = await self._session.execute(
            select(Document).where(
                Document.id == document_id,
                Document.user_id == user_id,
                Document.deleted_at.is_(None),
            )
        )
        return result.scalars().first()

    async def list_children(
        self, user_id: str, *, parent_id: str | None
    ) -> list[Document]:
        """A folder's direct children (``parent_id`` None = the user's top-level nodes)."""
        stmt = select(Document).where(
            Document.user_id == user_id,
            Document.deleted_at.is_(None),
        )
        stmt = stmt.where(
            Document.parent_id.is_(None) if parent_id is None else Document.parent_id == parent_id
        )
        result = await self._session.execute(stmt.order_by(Document.name.asc()))
        return list(result.scalars().all())

    async def update_content(
        self, document_id: str, *, user_id: str, content: str
    ) -> Document | None:
        """Overwrite a document's body and recompute derived indexes (CAS is the caller's job)."""
        doc = await self.get(document_id, user_id=user_id)
        if doc is None:
            return None
        self._set_content_and_derive(doc, content)
        await self._session.commit()
        await self._session.refresh(doc)
        return doc

    async def rename(self, document_id: str, *, user_id: str, name: str) -> Document | None:
        doc = await self.get(document_id, user_id=user_id)
        if doc is None:
            return None
        doc.name = name
        await self._session.commit()
        await self._session.refresh(doc)
        return doc

    async def update_apply_mode(
        self, document_id: str, *, user_id: str, apply_mode: str
    ) -> Document | None:
        """Set ``apply`` in the body's frontmatter (text-level edit) and re-derive indexes.

        No direct column write — the body remains the sole writable source.
        """
        doc = await self.get(document_id, user_id=user_id)
        if doc is None:
            return None
        if apply_mode not in ("always", "on_demand"):
            raise ValueError(f"invalid apply_mode: {apply_mode!r}")
        try:
            body = set_entry_frontmatter(doc.content, apply=apply_mode)  # type: ignore[arg-type]
        except FrontmatterEditError:
            raise
        self._set_content_and_derive(doc, body)
        await self._session.commit()
        await self._session.refresh(doc)
        return doc

    async def set_disputed(
        self, document_id: str, *, user_id: str, disputed: bool
    ) -> Document | None:
        """Mark / unmark one entry as user-disputed (纠错通道; body untouched).

        Only the explicit user action reaches here — nothing infers a dispute from
        conversation text. Marking never deletes: the row keeps its body so the user can
        still read (and restore) what was wrong. Re-marking an already-marked entry keeps
        the original timestamp, so「什么时候说的不对」stays honest.
        """
        doc = await self.get(document_id, user_id=user_id)
        if doc is None or doc.kind != "document":
            return None
        if disputed:
            if doc.disputed_at is None:
                doc.disputed_at = datetime.now()
        else:
            doc.disputed_at = None
        await self._session.commit()
        await self._session.refresh(doc)
        return doc

    async def dispute_memory_line(
        self,
        user_id: str,
        name: str,
        folder_id: str | None,
        *,
        new_content: str,
        line: DisputedLine,
    ) -> Document | None:
        """Move one bullet out of a note's body into ``disputed_lines`` — one transaction.

        The caller (``memory.dispute_line``) owns the markdown work and hands in the body
        with the line already gone plus the record to file; this method only has to land
        both columns together. Splitting them would risk the two failure modes that matter:
        a recorded line still riding the prompt, or a line dropped from the body with no
        trace of where it went.

        Past ``MAX_DISPUTED_LINES`` the oldest record falls off the front (see the model):
        the body edit still lands — refusing「这条不对」because an old undo is still on file
        would leave a line the user just rejected in the prompt.

        ``new_content`` is the **editor body** (no frontmatter), the same shape
        ``EditorBodyMemoryStore`` hands the markdown layer; the stored frontmatter block is
        re-attached here exactly as ``save_memory_note`` does. The always-pool quota is not
        consulted: removing text only shrinks it.
        """
        note = await self.get_memory_note(user_id, name, folder_id)
        if note is None:
            return None
        body = _memory_note_body_for_write(
            new_content, existing=note.content, apply_mode=note.apply_mode
        )
        apply_mode, description = _derive_indexes(body)
        note.content = body
        note.apply_mode = apply_mode
        note.description = description
        # Reassign rather than append: JSONB columns need a new object to be seen as dirty.
        note.disputed_lines = [*note.disputed_lines, line][-MAX_DISPUTED_LINES:]
        await self._session.commit()
        await self._session.refresh(note)
        return note

    async def restore_memory_line(
        self,
        user_id: str,
        name: str,
        folder_id: str | None,
        *,
        new_content: str,
        line_id: str,
    ) -> Document | None:
        """Undo one line-level dispute: body gets the bullet back, record drops it.

        Addressed by ``line_id``, never by position — an undo that missed would put back a
        line the user never asked for, while the toast said otherwise. An id that is no
        longer on file returns ``None`` (the caller reports it) instead of dropping some
        neighbouring row.

        The always-pool quota is deliberately NOT enforced here. The user is taking back
        his own correction, and refusing that because the pool is now full would strand the
        line in a place he cannot read it from — quota pressure is the consolidation pass's
        problem, not the undo button's. ``new_content`` is the editor body, as above.
        """
        note = await self.get_memory_note(user_id, name, folder_id)
        if note is None:
            return None
        kept = [row for row in note.disputed_lines if row["id"] != line_id]
        if len(kept) == len(note.disputed_lines):
            return None
        body = _memory_note_body_for_write(
            new_content, existing=note.content, apply_mode=note.apply_mode
        )
        apply_mode, description = _derive_indexes(body)
        note.content = body
        note.apply_mode = apply_mode
        note.description = description
        note.disputed_lines = kept
        await self._session.commit()
        await self._session.refresh(note)
        return note

    async def clear_memory_disputed_lines(self, user_id: str) -> int:
        """Drop every rejected-line record this user holds; returns the entries cleared.

        Bodies are untouched: the lines stay rejected (that is the user's correction), only
        the undo records go. This is the deliberate way out of a list the user has no
        intention of restoring from — the cap keeps it bounded, this empties it now.
        """
        result = await self._session.execute(
            update(Document)
            .where(
                Document.user_id == user_id,
                func.jsonb_array_length(Document.disputed_lines) > 0,
            )
            .values(disputed_lines=[])
            .returning(Document.id)
        )
        cleared = len(result.all())
        await self._session.commit()
        return cleared

    async def apply_description_if_empty(
        self,
        document_id: str,
        *,
        user_id: str,
        description: str,
        expected_content: str | None = None,
    ) -> Document | None:
        """Write AI ``description`` to the column only — never mutate ``content``.

        Used by async fill after user saves leave the field blank. User-written
        frontmatter ``description`` wins and is never overwritten. A prior non-empty
        column value is also left alone. When ``expected_content`` is set, skip if the
        body changed since generation (stale fill after a later save). Empty
        ``description`` arg is a no-op.
        """
        doc = await self.get(document_id, user_id=user_id)
        if doc is None or doc.kind != "document":
            return None
        text = (description or "").strip()
        if not text:
            return doc
        if expected_content is not None and doc.content != expected_content:
            return None
        parsed = parse_entry_frontmatter(doc.content)
        if isinstance(parsed, FrontmatterError):
            return None
        if parsed.description.strip():
            return doc
        if (doc.description or "").strip():
            return doc
        doc.description = text
        await self._session.commit()
        await self._session.refresh(doc)
        return doc

    async def _descendant_ids(self, user_id: str, root_id: str) -> list[str]:
        """All live descendant ids of a node (BFS), so a folder delete cascades its subtree."""
        ids: list[str] = []
        frontier = [root_id]
        while frontier:
            result = await self._session.execute(
                select(Document.id).where(
                    Document.user_id == user_id,
                    Document.parent_id.in_(frontier),
                    Document.deleted_at.is_(None),
                )
            )
            children = [row for row in result.scalars().all()]
            ids.extend(children)
            frontier = children
        return ids

    async def soft_delete(self, document_id: str, *, user_id: str) -> bool:
        """Soft-delete a node and (for a folder) its whole subtree. Idempotent.

        One batch ``UPDATE`` for the root + live descendants (not per-id ``session.get``).
        ``synchronize_session="fetch"`` keeps already-loaded ORM rows in this session in
        sync so later same-session reads see the new ``deleted_at``. Already-deleted rows
        are skipped via ``deleted_at IS NULL`` (idempotent).
        """
        doc = await self.get(document_id, user_id=user_id)
        if doc is None:
            return False
        now = datetime.now()
        ids = [document_id, *await self._descendant_ids(user_id, document_id)]
        await self._session.execute(
            update(Document)
            .where(
                Document.user_id == user_id,
                Document.id.in_(ids),
                Document.deleted_at.is_(None),
            )
            .values(deleted_at=now)
            .execution_options(synchronize_session="fetch")
        )
        await self._session.commit()
        return True

    async def move(
        self,
        document_id: str,
        *,
        user_id: str,
        parent_id: str | None,
        folder_id: str | None | object = _UNSET,
    ) -> Document | None:
        """Reparent a node (and optionally rescope it).

        ``folder_id`` uses the ``_UNSET`` sentinel (照 boards.update_meta) so an omitted value
        leaves the scope alone while an explicit ``None`` moves the node to the global layer.
        """
        doc = await self.get(document_id, user_id=user_id)
        if doc is None:
            return None
        doc.parent_id = parent_id
        if folder_id is not _UNSET:
            doc.folder_id = folder_id  # type: ignore[assignment]
        await self._session.commit()
        await self._session.refresh(doc)
        return doc

    async def hard_delete_for_folders(
        self, user_id: str, folder_ids: Sequence[str], *, commit: bool = True
    ) -> int:
        """Physically remove every document in these injection scopes.

        Soft-delete of a folder only hibernates injection (the rows stay so restore
        brings 设定 back). Permanent delete and retention purge call this so the
        orphans do not survive the desk.
        """
        ids = [fid for fid in folder_ids if fid]
        if not ids:
            return 0
        result = await self._session.execute(
            delete(Document).where(
                Document.user_id == user_id,
                Document.folder_id.in_(ids),
            )
        )
        await commit_or_flush(self._session, commit=commit)
        return int(cast("CursorResult[Any]", result).rowcount or 0)
