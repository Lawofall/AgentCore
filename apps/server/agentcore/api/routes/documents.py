"""Documents API — tree CRUD; user rules expose ``always`` | ``on_demand`` apply_mode.

Self-only CRUD over the single ``documents`` content tree. ``apply_mode`` / ``description``
on the wire are **derived indexes** of the body's frontmatter (sole writable source).
Patching ``apply_mode`` edits frontmatter then re-derives — never a column-only write.
``frontmatter_error`` surfaces structural parse failure for the UI (unclosed fence).

Write-side always quota (闸在写侧): create / content edit / promote-to-always go through
the always-pool gate. Editing an existing always entry past the cap is allowed with
``quota_warning``; creating or promoting past the cap is refused (409).

``PATCH {disputed}`` is the 纠错通道 (「这条不对」): a user-only mark that stops an entry
from being injected / consulted while keeping it readable here. ``disputed_at`` rides
every node view so the UI can show and undo the mark.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agentcore.api.dependencies import AuthUser, get_document_repo
from agentcore.db.models import Document
from agentcore.db.repositories import DocumentRepository
from agentcore.documents.description import maybe_schedule_description_fill
from agentcore.documents.frontmatter import (
    FrontmatterEditError,
    FrontmatterError,
    ParsedFrontmatter,
    frontmatter_error_message,
    parse_entry_frontmatter,
    set_entry_frontmatter,
)
from agentcore.documents.write_guards import is_ai_core_memory_leaf
from agentcore.memory import memory_version
from agentcore.memory.always_quota import (
    always_entry_chars,
    check_always_write,
    measure_always_usage,
)

router = APIRouter(prefix="/documents", tags=["documents"])

DocKind = Literal["folder", "document"]
DocRole = Literal["rule", "general"]
DocApplyMode = Literal["always", "on_demand"]


class DocumentNodeView(BaseModel):
    """A tree node's metadata (list rows — body omitted so a listing stays light)."""

    id: str
    parent_id: str | None
    folder_id: str | None
    kind: str
    role: str
    ai_maintained: bool
    apply_mode: str
    description: str
    name: str
    frontmatter_error: str | None = None
    # Chars counting toward the always pool (same meter as write-side gate); null if not always.
    always_chars: int | None = None
    # When the user marked this entry wrong (纠错通道). Set ⇒ the entry is kept and still
    # readable / editable here, but never injected and never offered to ``consult``.
    disputed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DocumentDetailView(DocumentNodeView):
    """A node plus its markdown body and content-hash CAS tag (the editor's load payload)."""

    content: str
    version: str
    quota_warning: str | None = None


class DocumentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=500)
    kind: DocKind = "document"
    role: DocRole = "general"
    content: str = ""
    # Default always for user rules (UI); written into frontmatter on create.
    apply_mode: DocApplyMode = "always"
    parent_id: str | None = None
    folder_id: str | None = None


class DocumentContentRequest(BaseModel):
    content: str
    baseline: str | None = None


class DocumentWriteResult(BaseModel):
    ok: bool
    version: str
    conflict: bool = False
    frontmatter_error: str | None = None
    quota_warning: str | None = None


class DocumentPatchRequest(BaseModel):
    """Rename, reparent, change apply (via frontmatter edit), and/or mark 这条不对."""

    name: str | None = Field(default=None, min_length=1, max_length=500)
    parent_id: str | None = None
    reparent: bool = False
    apply_mode: DocApplyMode | None = None
    # True = 「这条不对」(stop injecting, keep the entry); False = undo the mark.
    # Only ever set by an explicit user action in the memory UI.
    disputed: bool | None = None


class AlwaysQuotaView(BaseModel):
    """Always-pool usage for the UI meter (percentage + absolute chars)."""

    used_chars: int
    max_chars: int
    percent: float
    # ``used_chars == global_chars + project_chars`` (project context = global ∪ this project).
    global_chars: int
    project_chars: int


def _fm_error(doc: Document) -> str | None:
    if doc.kind != "document":
        return None
    return frontmatter_error_message(doc.content)


def _always_chars(doc: Document) -> int | None:
    """Pool chars for always-injected rule docs; null for everything else."""
    if doc.kind != "document" or doc.role != "rule" or doc.apply_mode != "always":
        return None
    return always_entry_chars(doc.content)


def _node(doc: Document) -> DocumentNodeView:
    return DocumentNodeView(
        id=doc.id,
        parent_id=doc.parent_id,
        folder_id=doc.folder_id,
        kind=doc.kind,
        role=doc.role,
        ai_maintained=doc.ai_maintained,
        apply_mode=doc.apply_mode,
        description=doc.description,
        name=doc.name,
        frontmatter_error=_fm_error(doc),
        always_chars=_always_chars(doc),
        disputed_at=doc.disputed_at,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def _detail(doc: Document, *, quota_warning: str | None = None) -> DocumentDetailView:
    return DocumentDetailView(
        id=doc.id,
        parent_id=doc.parent_id,
        folder_id=doc.folder_id,
        kind=doc.kind,
        role=doc.role,
        ai_maintained=doc.ai_maintained,
        apply_mode=doc.apply_mode,
        description=doc.description,
        name=doc.name,
        frontmatter_error=_fm_error(doc),
        always_chars=_always_chars(doc),
        disputed_at=doc.disputed_at,
        content=doc.content,
        version=memory_version(doc.content),
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        quota_warning=quota_warning,
    )


def _resolve_create_apply_mode(*, role: str, kind: str, apply_mode: DocApplyMode) -> str:
    """Only user-rule documents may be on_demand; everything else stays always."""
    if role == "rule" and kind == "document":
        return apply_mode
    return "always"


def _raise_quota_denied(message: str | None) -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "code": "ALWAYS_QUOTA_EXCEEDED",
            "message": message or "常驻条目配额已满",
        },
    )


async def _preview_create_body(
    *, role: str, kind: str, apply_mode: DocApplyMode, content: str
) -> tuple[str, bool]:
    """Return (body_as_stored, is_always_rule) for quota projection before create."""
    if kind != "document" or role != "rule":
        return content, False
    mode = _resolve_create_apply_mode(role=role, kind=kind, apply_mode=apply_mode)
    try:
        body = set_entry_frontmatter(content, apply=mode)  # type: ignore[arg-type]
    except FrontmatterEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return body, mode == "always"


@router.get("/always-quota", response_model=AlwaysQuotaView)
async def get_always_quota(
    user: AuthUser,
    folder_id: str | None = None,
    repo: DocumentRepository = Depends(get_document_repo),
) -> AlwaysQuotaView:
    """Always-pool usage for the injection context (global + optional project)."""
    usage = await measure_always_usage(repo, user.user_id, folder_id=folder_id)
    return AlwaysQuotaView(
        used_chars=usage.used_chars,
        max_chars=usage.max_chars,
        percent=usage.percent,
        global_chars=usage.global_chars,
        project_chars=usage.project_chars,
    )


@router.get("", response_model=list[DocumentNodeView])
async def list_documents(
    user: AuthUser,
    parent_id: str | None = None,
    repo: DocumentRepository = Depends(get_document_repo),
) -> list[DocumentNodeView]:
    """List a folder's direct children (``parent_id`` omitted = the user's top-level nodes)."""
    nodes = await repo.list_children(user.user_id, parent_id=parent_id)
    return [_node(n) for n in nodes]


@router.post("", response_model=DocumentDetailView)
async def create_document(
    body: DocumentCreateRequest,
    user: AuthUser,
    repo: DocumentRepository = Depends(get_document_repo),
) -> DocumentDetailView:
    """Create a tree node (always ``ai_maintained=false`` — user-owned).

    A child inherits its parent's ``folder_id`` scope; a root node takes the requested scope.
    New ``role='rule'`` documents with no parent land under ``AgentCore/规则/`` (§5.0).
    """
    folder_id = body.folder_id
    parent_id = body.parent_id
    if parent_id is not None:
        parent = await repo.get(parent_id, user_id=user.user_id)
        if parent is None:
            raise HTTPException(status_code=404, detail="parent not found")
        if parent.kind != "folder":
            raise HTTPException(status_code=400, detail="parent is not a folder")
        folder_id = parent.folder_id
    elif body.role == "rule" and body.kind == "document":
        rules_dir = await repo.ensure_rules_dir(user.user_id, folder_id)
        parent_id = rules_dir.id
        folder_id = rules_dir.folder_id

    preview_body, is_always = await _preview_create_body(
        role=body.role, kind=body.kind, apply_mode=body.apply_mode, content=body.content
    )
    if is_always:
        decision = await check_always_write(
            repo,
            user.user_id,
            folder_id=folder_id,
            writer="user",
            editing_existing_always=False,
            exclude_id=None,
            new_content=preview_body,
            new_is_always=True,
        )
        if not decision.allowed:
            _raise_quota_denied(decision.message)

    try:
        doc = await repo.create(
            user.user_id,
            name=body.name,
            parent_id=parent_id,
            folder_id=folder_id,
            kind=body.kind,
            role=body.role,
            ai_maintained=False,
            apply_mode=_resolve_create_apply_mode(
                role=body.role, kind=body.kind, apply_mode=body.apply_mode
            ),
            content=body.content if body.kind == "document" else "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Async empty-description fill — never blocks / fails the save (定案).
    maybe_schedule_description_fill(
        document_id=doc.id,
        user_id=user.user_id,
        kind=doc.kind,
        description=doc.description or "",
        content=doc.content or "",
    )
    return _detail(doc)


@router.get("/{document_id}", response_model=DocumentDetailView)
async def get_document(
    document_id: str,
    user: AuthUser,
    repo: DocumentRepository = Depends(get_document_repo),
) -> DocumentDetailView:
    doc = await repo.get(document_id, user_id=user.user_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    return _detail(doc)


@router.put("/{document_id}", response_model=DocumentWriteResult)
async def update_document_content(
    document_id: str,
    body: DocumentContentRequest,
    user: AuthUser,
    repo: DocumentRepository = Depends(get_document_repo),
) -> DocumentWriteResult:
    """Overwrite a document's body (CAS-guarded; conflict instead of clobber)."""
    doc = await repo.get(document_id, user_id=user.user_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    current_version = memory_version(doc.content)
    if body.baseline is not None and body.baseline != current_version:
        return DocumentWriteResult(
            ok=False,
            version=current_version,
            conflict=True,
            frontmatter_error=_fm_error(doc),
        )

    quota_warning: str | None = None
    if doc.kind == "document" and doc.role == "rule":
        parsed = parse_entry_frontmatter(body.content)
        if isinstance(parsed, FrontmatterError):
            new_is_always = False
        elif isinstance(parsed, ParsedFrontmatter):
            new_is_always = parsed.apply == "always"
        else:
            new_is_always = False

        editing_existing_always = doc.apply_mode == "always"
        decision = await check_always_write(
            repo,
            user.user_id,
            folder_id=doc.folder_id,
            writer="user",
            editing_existing_always=editing_existing_always,
            exclude_id=doc.id,
            new_content=body.content,
            new_is_always=new_is_always,
        )
        if not decision.allowed:
            _raise_quota_denied(decision.message)
        quota_warning = decision.warning

    updated = await repo.update_content(document_id, user_id=user.user_id, content=body.content)
    assert updated is not None
    # Clear → regenerate; non-empty description never auto-overwritten (定案).
    maybe_schedule_description_fill(
        document_id=updated.id,
        user_id=user.user_id,
        kind=updated.kind,
        description=updated.description or "",
        content=updated.content or "",
    )
    return DocumentWriteResult(
        ok=True,
        version=memory_version(body.content),
        frontmatter_error=_fm_error(updated),
        quota_warning=quota_warning,
    )


@router.patch("/{document_id}", response_model=DocumentDetailView)
async def patch_document(
    document_id: str,
    body: DocumentPatchRequest,
    user: AuthUser,
    repo: DocumentRepository = Depends(get_document_repo),
) -> DocumentDetailView:
    """Rename, reparent, set apply via frontmatter, and/or mark the entry wrong.

    Set ``reparent`` to apply ``parent_id``. ``disputed`` is the 纠错通道: the user says
    「这条不对」in the memory UI and the entry stops being injected / consulted while
    staying on disk (no silent delete, and the mark survives later AI rewrites because it
    lives in a column, not in the body). Nothing infers this from conversation text.
    """
    doc = await repo.get(document_id, user_id=user.user_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    if body.name is not None:
        await repo.rename(document_id, user_id=user.user_id, name=body.name)
    if body.reparent:
        new_folder = doc.folder_id
        if body.parent_id is not None:
            parent = await repo.get(body.parent_id, user_id=user.user_id)
            if parent is None:
                raise HTTPException(status_code=404, detail="parent not found")
            if parent.kind != "folder":
                raise HTTPException(status_code=400, detail="parent is not a folder")
            new_folder = parent.folder_id
        await repo.move(
            document_id, user_id=user.user_id, parent_id=body.parent_id, folder_id=new_folder
        )
    quota_warning: str | None = None
    if body.apply_mode is not None:
        current = await repo.get(document_id, user_id=user.user_id)
        assert current is not None
        if current.ai_maintained:
            raise HTTPException(
                status_code=400, detail="cannot change apply_mode of AI-maintained documents"
            )
        if current.role != "rule" or current.kind != "document":
            raise HTTPException(
                status_code=400, detail="apply_mode only applies to rule documents"
            )
        if body.apply_mode == "always" and current.apply_mode != "always":
            try:
                preview = set_entry_frontmatter(current.content, apply="always")
            except FrontmatterEditError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            decision = await check_always_write(
                repo,
                user.user_id,
                folder_id=current.folder_id,
                writer="user",
                editing_existing_always=False,
                exclude_id=current.id,
                new_content=preview,
                new_is_always=True,
            )
            if not decision.allowed:
                _raise_quota_denied(decision.message)
            quota_warning = decision.warning
        try:
            await repo.update_apply_mode(
                document_id, user_id=user.user_id, apply_mode=body.apply_mode
            )
        except FrontmatterEditError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if body.disputed is not None:
        marked = await repo.set_disputed(
            document_id, user_id=user.user_id, disputed=body.disputed
        )
        if marked is None:
            raise HTTPException(status_code=400, detail="cannot dispute this node")
    refreshed = await repo.get(document_id, user_id=user.user_id)
    assert refreshed is not None
    return _detail(refreshed, quota_warning=quota_warning)


@router.delete("/{document_id}", response_model=DocumentWriteResult)
async def delete_document(
    document_id: str,
    user: AuthUser,
    repo: DocumentRepository = Depends(get_document_repo),
) -> DocumentWriteResult:
    """Soft-delete a node and (for a folder) its whole subtree.

    AI-maintained core leaves (偏好 / 画像 / 导航) keep their protocol names, so
    this DELETE is refused. Empty the body instead (``PUT …/memory/files/{kind}``
    with empty content) — injection skips the empty note; the list still shows
    a placeholder. On-demand AI topics and user-owned entries remain deletable.
    """
    doc = await repo.get(document_id, user_id=user.user_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    if is_ai_core_memory_leaf(name=doc.name, ai_maintained=doc.ai_maintained):
        raise HTTPException(
            status_code=400,
            detail="cannot delete AI-maintained core memory leaves",
        )
    ok = await repo.soft_delete(document_id, user_id=user.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="document not found")
    return DocumentWriteResult(ok=True, version=memory_version(""))
