"""Skill catalog overlay: 我的技能 + 换用 + 藏起 (not the deployment 图鉴).

``GET /v1/capabilities`` stays the platform blueprint. This route is the user's
index: on-demand documents they wrote, and which official slots they occupy.

``folder_id`` selects a folder layer. Writes at that layer belong to the desk
owner; members may read the merged index. Restoring a layer only clears this
scope — outer account / ancestor keys still apply.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_db
from agentcore.db.models import Document
from agentcore.db.repositories import DocumentRepository, SkillMuteRepository, SkillSlotRepository
from agentcore.documents.frontmatter import strip_entry_frontmatter
from agentcore.folders.desk import resolve_desk_access
from agentcore.memory.account_prepare_cache import (
    drop_account_rules_memory_cache_for_user,
)
from agentcore.memory.rules_injection import rule_consult_name
from agentcore.runtime.capability_packs import enabled_packs
from agentcore.runtime.skills import build_system_skill_registry
from agentcore.runtime.skills.replacements import (
    overlay_layer,
    resolve_skill_overlay_layers,
)

router = APIRouter(prefix="/skill-catalog", tags=["skill-catalog"])

OverlayLayer = Literal["here", "inherited"]


class SkillReplacedBy(BaseModel):
    document_id: str
    name: str
    description: str = ""


class SkillSlotView(BaseModel):
    name: str
    summary: str
    replaced_by: SkillReplacedBy | None = None
    muted: bool = False
    replaced_layer: OverlayLayer | None = None
    muted_layer: OverlayLayer | None = None


class MineSkillView(BaseModel):
    id: str
    name: str
    description: str
    content: str
    version: str
    occupies: list[str] = Field(default_factory=list)


class SkillCatalogView(BaseModel):
    slots: list[SkillSlotView]
    mine: list[MineSkillView]
    folder_id: str | None = None
    writable: bool = True


class ReplaceSkillRequest(BaseModel):
    document_id: str = Field(..., min_length=1)


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


def _slot_repo(session: AsyncSession = Depends(get_db)) -> SkillSlotRepository:
    return SkillSlotRepository(session)


def _mute_repo(session: AsyncSession = Depends(get_db)) -> SkillMuteRepository:
    return SkillMuteRepository(session)


def _doc_repo(session: AsyncSession = Depends(get_db)) -> DocumentRepository:
    return DocumentRepository(session)


def _require_official_slot(slot: str) -> str:
    registry = build_system_skill_registry(enabled_packs=enabled_packs())
    key = slot.strip()
    if not key or registry.get(key) is None:
        raise HTTPException(status_code=400, detail={"message": "不是官方技能槽"})
    return key


async def _require_scope(
    session: AsyncSession, user_id: str, folder_id: str | None, *, write: bool
) -> tuple[str, str | None, bool]:
    """Return ``(overlay_user_id, folder_id, writable)``. 404 if the desk is missing."""
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
    slots: SkillSlotRepository = Depends(_slot_repo),
    mutes: SkillMuteRepository = Depends(_mute_repo),
    docs: DocumentRepository = Depends(_doc_repo),
    folder_id: str | None = Query(default=None),
) -> SkillCatalogView:
    """Official slots + this account's global on-demand skills (换用 / 藏起 overlay)."""
    del slots, mutes
    overlay_user_id, scope, writable = await _require_scope(
        session, user.user_id, folder_id, write=False
    )
    del overlay_user_id
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

    registry = build_system_skill_registry(enabled_packs=enabled_packs())
    resolved = await resolve_skill_overlay_layers(session, user.user_id, folder_id)
    mine_docs = await docs.list_on_demand_user_rules(user.user_id, None)
    occupies: dict[str, list[str]] = {}
    for slot_name, item in resolved.merged.replacements.items():
        occupies.setdefault(item.document_id, []).append(slot_name)

    return SkillCatalogView(
        folder_id=folder_id,
        writable=writable,
        slots=[
            SkillSlotView(
                name=skill.name,
                summary=skill.summary,
                replaced_by=(
                    SkillReplacedBy(
                        document_id=rep.document_id,
                        name=rep.document_name,
                        description=rep.summary,
                    )
                    if (rep := resolved.merged.replacements.get(skill.name)) is not None
                    else None
                ),
                muted=skill.name in resolved.merged.muted,
                replaced_layer=overlay_layer(
                    resolved.merged, resolved.here, kind="replaced", slot=skill.name
                ),
                muted_layer=overlay_layer(
                    resolved.merged, resolved.here, kind="muted", slot=skill.name
                ),
            )
            for skill in registry.list_all()
        ],
        mine=[
            MineSkillView(
                id=doc.id,
                name=rule_consult_name(doc.name),
                description=doc.description or "",
                content=doc.content or "",
                version=memory_version(doc.content or ""),
                occupies=occupies.get(doc.id, []),
            )
            for doc in mine_docs
            if _eligible_mine_doc(doc)
        ],
    )


def _drop_overlay_cache(user_id: str, owner_user_id: str) -> None:
    drop_account_rules_memory_cache_for_user(user_id)
    if owner_user_id != user_id:
        drop_account_rules_memory_cache_for_user(owner_user_id)


@router.put("/replacements/{slot}", response_model=SkillCatalogView)
async def put_skill_replacement(
    slot: str,
    body: ReplaceSkillRequest,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    slots: SkillSlotRepository = Depends(_slot_repo),
    docs: DocumentRepository = Depends(_doc_repo),
    folder_id: str | None = Query(default=None),
) -> SkillCatalogView:
    """Bind a global on-demand document onto an official skill slot at this layer."""
    key = _require_official_slot(slot)
    owner_id, scope, writable = await _require_scope(
        session, user.user_id, folder_id, write=True
    )
    doc = await docs.get(body.document_id, user_id=owner_id)
    if doc is None:
        raise HTTPException(status_code=404, detail={"message": "找不到要换用的技能"})
    if not _eligible_mine_doc(doc):
        raise HTTPException(
            status_code=400,
            detail={"message": "只能换用账号里已启用的按需技能"},
        )
    stripped = strip_entry_frontmatter(doc.content or "")
    if stripped is None or not stripped.strip():
        raise HTTPException(status_code=400, detail={"message": "这份技能还没有正文"})
    await slots.upsert(
        user_id=owner_id, slot_name=key, document_id=doc.id, folder_id=scope
    )
    _drop_overlay_cache(user.user_id, owner_id)
    return await _catalog_view(
        user, session=session, docs=docs, folder_id=scope, writable=writable
    )


@router.delete("/replacements/{slot}", response_model=SkillCatalogView)
async def delete_skill_replacement(
    slot: str,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    slots: SkillSlotRepository = Depends(_slot_repo),
    docs: DocumentRepository = Depends(_doc_repo),
    folder_id: str | None = Query(default=None),
) -> SkillCatalogView:
    """Clear 换用 at this layer (inherit outer / factory)."""
    key = _require_official_slot(slot)
    owner_id, scope, writable = await _require_scope(
        session, user.user_id, folder_id, write=True
    )
    await slots.delete(user_id=owner_id, slot_name=key, folder_id=scope)
    _drop_overlay_cache(user.user_id, owner_id)
    return await _catalog_view(
        user, session=session, docs=docs, folder_id=scope, writable=writable
    )


@router.put("/mutes/{slot}", response_model=SkillCatalogView)
async def put_skill_mute(
    slot: str,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    mutes: SkillMuteRepository = Depends(_mute_repo),
    docs: DocumentRepository = Depends(_doc_repo),
    folder_id: str | None = Query(default=None),
) -> SkillCatalogView:
    """Hide this official slot from the model's on-demand catalog at this layer."""
    key = _require_official_slot(slot)
    owner_id, scope, writable = await _require_scope(
        session, user.user_id, folder_id, write=True
    )
    await mutes.add(user_id=owner_id, slot_name=key, folder_id=scope)
    _drop_overlay_cache(user.user_id, owner_id)
    return await _catalog_view(
        user, session=session, docs=docs, folder_id=scope, writable=writable
    )


@router.delete("/mutes/{slot}", response_model=SkillCatalogView)
async def delete_skill_mute(
    slot: str,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    mutes: SkillMuteRepository = Depends(_mute_repo),
    docs: DocumentRepository = Depends(_doc_repo),
    folder_id: str | None = Query(default=None),
) -> SkillCatalogView:
    """Clear 藏起 at this layer (inherit outer mute if any)."""
    key = _require_official_slot(slot)
    owner_id, scope, writable = await _require_scope(
        session, user.user_id, folder_id, write=True
    )
    await mutes.delete(user_id=owner_id, slot_name=key, folder_id=scope)
    _drop_overlay_cache(user.user_id, owner_id)
    return await _catalog_view(
        user, session=session, docs=docs, folder_id=scope, writable=writable
    )
