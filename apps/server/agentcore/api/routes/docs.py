"""Creation-tool 文档 CRUD + CAS body write + public share minting.

Docs hang on a cloud folder (协作桌成员看见同一份). Distinct from ``/v1/documents``
(记忆 / 规则树). Outsiders 404; viewers read; owner/editor write. ``can_write``
mints / lists / revokes frozen ``/shared/<id>`` snapshots (viewer 403).
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_db, get_doc_repo, get_doc_share_repo
from agentcore.api.schemas import (
    CreateDocRequest,
    CreateShareRequest,
    DocBodyWriteRequest,
    DocDetail,
    DocSummary,
    DocWriteResult,
    ShareListResponse,
    ShareSummary,
    StatusResponse,
    UpdateDocRequest,
)
from agentcore.core.errors import AuthorizationError, NotFoundError, ValidationError
from agentcore.db.models import Doc, DocShare, Folder
from agentcore.db.repositories import DocRepository, DocShareRepository
from agentcore.doc.share import freeze_share_snapshot
from agentcore.folders.desk import DeskAccess, resolve_desk_access

router = APIRouter(prefix="/docs", tags=["docs"])

_DEFAULT_TITLE = "未命名文档"


def _is_cloud_folder(folder: Folder) -> bool:
    return not folder.local_root_id


def _summary(doc: Doc, folder_name: str, *, can_write: bool) -> DocSummary:
    return DocSummary(
        id=doc.id,
        title=doc.title,
        folder_id=doc.folder_id,
        folder_name=folder_name,
        version=doc.version,
        can_write=can_write,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def _detail(doc: Doc, folder_name: str, *, can_write: bool) -> DocDetail:
    return DocDetail(
        **_summary(doc, folder_name, can_write=can_write).model_dump(),
        body=doc.body if isinstance(doc.body, dict) else {"schemaVersion": 1, "blocks": []},
    )


async def _require_desk(
    session: AsyncSession, *, folder_id: str, user_id: str
) -> DeskAccess:
    access = await resolve_desk_access(session, folder_id=folder_id, user_id=user_id)
    if access is None:
        raise NotFoundError("文档不存在")
    return access


async def _load_visible(
    repo: DocRepository, *, doc_id: str, user_id: str
) -> tuple[Doc, DeskAccess]:
    doc = await repo.get_live(doc_id)
    if doc is None:
        raise NotFoundError("文档不存在")
    access = await _require_desk(
        repo._session, folder_id=doc.folder_id, user_id=user_id
    )
    if not _is_cloud_folder(access.folder):
        raise NotFoundError("文档不存在")
    return doc, access


def _share_summary(share: DocShare) -> ShareSummary:
    return ShareSummary(
        id=share.id,
        url=f"/shared/{share.id}",
        title=share.title,
        created_at=share.created_at,
        expires_at=share.expires_at,
    )


def _expires_at_from_request(body: CreateShareRequest | None) -> datetime | None:
    days = 30 if body is None else body.expires_in_days
    if days is None:
        return None
    return datetime.now(UTC) + timedelta(days=days)


@router.post("", response_model=DocSummary, status_code=201)
async def create_doc(
    body: CreateDocRequest,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    repo: DocRepository = Depends(get_doc_repo),
):
    access = await resolve_desk_access(
        session, folder_id=body.folder_id, user_id=user.user_id
    )
    if access is None:
        raise NotFoundError("文件夹不存在")
    if not _is_cloud_folder(access.folder):
        raise ValidationError("文档只能建在云文件夹上")
    if not access.can_write:
        raise AuthorizationError("只读成员不能新建文档")
    title = (body.title or "").strip() or _DEFAULT_TITLE
    doc = await repo.create(
        user_id=user.user_id, folder_id=body.folder_id, title=title
    )
    return _summary(doc, access.folder.name, can_write=True)


@router.get("", response_model=list[DocSummary])
async def list_docs(
    user: AuthUser,
    folder_id: str | None = Query(default=None),
    repo: DocRepository = Depends(get_doc_repo),
):
    if folder_id is not None:
        access = await resolve_desk_access(
            repo._session, folder_id=folder_id, user_id=user.user_id
        )
        if access is None:
            raise NotFoundError("文件夹不存在")
    rows = await repo.list_visible(user.user_id, folder_id=folder_id)
    return [_summary(doc, name, can_write=can_write) for doc, name, can_write in rows]


@router.get("/{doc_id}", response_model=DocDetail)
async def get_doc(
    doc_id: str,
    user: AuthUser,
    repo: DocRepository = Depends(get_doc_repo),
):
    doc, access = await _load_visible(repo, doc_id=doc_id, user_id=user.user_id)
    return _detail(doc, access.folder.name, can_write=access.can_write)


@router.patch("/{doc_id}", response_model=DocSummary)
async def update_doc(
    doc_id: str,
    body: UpdateDocRequest,
    user: AuthUser,
    repo: DocRepository = Depends(get_doc_repo),
):
    doc, access = await _load_visible(repo, doc_id=doc_id, user_id=user.user_id)
    if not access.can_write:
        raise AuthorizationError("只读成员不能改文档")
    title = body.title.strip() if isinstance(body.title, str) else None
    if title == "":
        title = _DEFAULT_TITLE
    doc = await repo.update_meta(doc, title=title)
    return _summary(doc, access.folder.name, can_write=True)


@router.put("/{doc_id}/body", response_model=DocWriteResult)
async def write_doc_body(
    doc_id: str,
    body: DocBodyWriteRequest,
    user: AuthUser,
    repo: DocRepository = Depends(get_doc_repo),
):
    doc, access = await _load_visible(repo, doc_id=doc_id, user_id=user.user_id)
    if not access.can_write:
        raise AuthorizationError("只读成员不能改文档")
    doc, conflict = await repo.save_body(doc, body=body.body, baseline=body.baseline)
    if conflict:
        return DocWriteResult(
            ok=False,
            version=doc.version,
            conflict=True,
            doc=_detail(doc, access.folder.name, can_write=True),
        )
    return DocWriteResult(ok=True, version=doc.version)


@router.delete("/{doc_id}", response_model=StatusResponse)
async def delete_doc(
    doc_id: str,
    user: AuthUser,
    repo: DocRepository = Depends(get_doc_repo),
    share_repo: DocShareRepository = Depends(get_doc_share_repo),
):
    doc, access = await _load_visible(repo, doc_id=doc_id, user_id=user.user_id)
    if not access.can_write:
        raise AuthorizationError("只读成员不能删除文档")
    await share_repo.revoke_all_for_doc(doc.id)
    await repo.soft_delete(doc)
    return StatusResponse()


@router.post("/{doc_id}/shares", response_model=ShareSummary, status_code=201)
async def create_doc_share(
    doc_id: str,
    user: AuthUser,
    body: CreateShareRequest | None = None,
    repo: DocRepository = Depends(get_doc_repo),
    share_repo: DocShareRepository = Depends(get_doc_share_repo),
):
    doc, access = await _load_visible(repo, doc_id=doc_id, user_id=user.user_id)
    if not access.can_write:
        raise AuthorizationError("只读成员不能分享文档")
    share = await share_repo.create(
        doc_id=doc.id,
        user_id=user.user_id,
        title=(doc.title or "").strip() or "未命名文档",
        snapshot=freeze_share_snapshot(doc.body),
        expires_at=_expires_at_from_request(body),
    )
    return _share_summary(share)


@router.get("/{doc_id}/shares", response_model=ShareListResponse)
async def list_doc_shares(
    doc_id: str,
    user: AuthUser,
    repo: DocRepository = Depends(get_doc_repo),
    share_repo: DocShareRepository = Depends(get_doc_share_repo),
):
    doc, access = await _load_visible(repo, doc_id=doc_id, user_id=user.user_id)
    if not access.can_write:
        raise AuthorizationError("只读成员不能管理分享")
    shares = await share_repo.list_active_for_doc(doc.id)
    data = [_share_summary(s) for s in shares]
    return ShareListResponse(data=data, total=len(data))


@router.delete("/{doc_id}/shares/{share_id}", response_model=StatusResponse)
async def revoke_doc_share(
    doc_id: str,
    share_id: str,
    user: AuthUser,
    repo: DocRepository = Depends(get_doc_repo),
    share_repo: DocShareRepository = Depends(get_doc_share_repo),
):
    _doc, access = await _load_visible(repo, doc_id=doc_id, user_id=user.user_id)
    if not access.can_write:
        raise AuthorizationError("只读成员不能撤销分享")
    revoked = await share_repo.revoke(share_id, doc_id=doc_id)
    if not revoked:
        raise NotFoundError("分享不存在")
    return StatusResponse()
