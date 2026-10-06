"""Skill catalog: 我的技能 + 官方槽名册 (not the deployment 图鉴).

``GET /v1/capabilities`` stays the platform blueprint. This route lists
on-demand documents the user wrote. Official HOW bodies stay in code — the
``slots`` rows are a name/summary roster.

``folder_id`` still selects a desk for ``writable`` (members may read).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_db
from agentcore.db.models import Document
from agentcore.db.repositories import DocumentRepository
from agentcore.folders.desk import resolve_desk_access
from agentcore.memory.rules_injection import rule_consult_name
from agentcore.runtime.skills import build_system_skill_registry

router = APIRouter(prefix="/skill-catalog", tags=["skill-catalog"])


class SkillSlotView(BaseModel):
    name: str
    summary: str


class MineSkillView(BaseModel):
    id: str
    name: str
    description: str
    content: str
    version: str


class SkillCatalogView(BaseModel):
    slots: list[SkillSlotView]
    mine: list[MineSkillView]
    folder_id: str | None = None
    writable: bool = True


def _eligible_mine_doc(doc: Document) -> bool:
    return (
        doc.kind == "document"
        and doc.role == "rule"
        and doc.apply_mode == "on_demand"
        and not doc.ai_maintained
        and doc.folder_id is None
        and doc.deleted_at is None
        and doc.disputed_at is None
    )


def _doc_repo(session: AsyncSession = Depends(get_db)) -> DocumentRepository:
    return DocumentRepository(session)


async def _require_scope(
    session: AsyncSession, user_id: str, folder_id: str | None, *, write: bool
) -> tuple[str, str | None, bool]:
    """Return ``(owner_user_id, folder_id, writable)``. 404 if the desk is missing."""
    if not folder_id:
        return user_id, None, True
    access = await resolve_desk_access(session, folder_id=folder_id, user_id=user_id)
    if access is None:
        raise HTTPException(status_code=404, detail={"message": "找不到这个文件夹"})
    if write and not access.is_owner:
        raise HTTPException(
            status_code=403, detail={"message": "只有桌主能改这张桌的技能目录"}
        )
    return access.owner_user_id, folder_id, access.is_owner


@router.get("", response_model=SkillCatalogView)
async def get_skill_catalog(
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    docs: DocumentRepository = Depends(_doc_repo),
    folder_id: str | None = Query(default=None),
) -> SkillCatalogView:
    """Official slot roster + this account's global on-demand skills."""
    _owner_id, scope, writable = await _require_scope(
        session, user.user_id, folder_id, write=False
    )
    return await _catalog_view(
        user, session=session, docs=docs, folder_id=scope, writable=writable
    )


async def _catalog_view(
    user: AuthUser,
    *,
    session: AsyncSession,
    docs: DocumentRepository,
    folder_id: str | None,
    writable: bool,
) -> SkillCatalogView:
    from agentcore.memory import memory_version

    del session
    registry = build_system_skill_registry()
    mine_docs = await docs.list_on_demand_user_rules(user.user_id, None)

    return SkillCatalogView(
        folder_id=folder_id,
        writable=writable,
        slots=[
            SkillSlotView(name=skill.name, summary=skill.summary)
            for skill in registry.list_all()
        ],
        mine=[
            MineSkillView(
                id=doc.id,
                name=rule_consult_name(doc.name),
                description=doc.description or "",
                content=doc.content or "",
                version=memory_version(doc.content or ""),
            )
            for doc in mine_docs
            if _eligible_mine_doc(doc)
        ],
    )
