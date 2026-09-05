"""Load 换用 / 藏起 overlay for system skill slots.

User skills stay ordinary on-demand documents. This module only resolves
``slot_name → document body`` for consult listing / fetch. System skill
source of truth stays in code.

Layers merge farthest → nearest: account (private) then folder-chain
(desk owner's rows). Nearer keys overwrite; clearing a layer inherits
the outer one — it does not force factory.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import DocumentRepository
from agentcore.db.repositories.folders import FolderRepository
from agentcore.db.repositories.skill_slots import SkillMuteRepository, SkillSlotRepository
from agentcore.documents.frontmatter import strip_entry_frontmatter
from agentcore.memory.rules_injection import rule_consult_name

logger = get_logger(__name__)

OverlayLayer = Literal["here", "inherited"]


@dataclass(frozen=True)
class SkillReplacement:
    """Live user body occupying one official skill slot."""

    summary: str
    body: str
    document_id: str
    document_name: str


@dataclass(frozen=True)
class SkillOverlay:
    """One index: 换用 bodies + 藏起 names."""

    replacements: dict[str, SkillReplacement]
    muted: frozenset[str]


@dataclass(frozen=True)
class ResolvedSkillOverlay:
    """Merged index plus this scope's own layer (for toolbox provenance)."""

    merged: SkillOverlay
    here: SkillOverlay


def merge_skill_overlays(*layers: SkillOverlay) -> SkillOverlay:
    """Farthest layer first; nearer ``update`` / union wins. Empty layers are skips."""
    replacements: dict[str, SkillReplacement] = {}
    muted: set[str] = set()
    for layer in layers:
        replacements.update(layer.replacements)
        muted.update(layer.muted)
    return SkillOverlay(replacements=replacements, muted=frozenset(muted))


def overlay_layer(
    merged: SkillOverlay,
    here: SkillOverlay,
    *,
    kind: Literal["replaced", "muted"],
    slot: str,
) -> OverlayLayer | None:
    """Whether a merged hit was written at this scope or inherited."""
    if kind == "replaced":
        if slot not in merged.replacements:
            return None
        return "here" if slot in here.replacements else "inherited"
    if slot not in merged.muted:
        return None
    return "here" if slot in here.muted else "inherited"


def skill_mutes_from_payload(payload: Mapping[str, Any] | None) -> frozenset[str]:
    """Parse sidecar / cloud ``rules/list`` ``skill_mutes`` (absent → empty)."""
    if not payload:
        return frozenset()
    raw = payload.get("skill_mutes")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(item).strip() for item in raw if str(item).strip())


def overlay_from_payload(payload: Mapping[str, Any] | None) -> SkillOverlay:
    return SkillOverlay(
        replacements=skill_replacements_from_payload(payload),
        muted=skill_mutes_from_payload(payload),
    )


def skill_replacements_from_payload(
    payload: Mapping[str, Any] | None,
) -> dict[str, SkillReplacement]:
    """Parse sidecar / cloud ``rules/list`` extra field (absent → empty)."""
    if not payload:
        return {}
    raw = payload.get("skill_replacements")
    if not isinstance(raw, list):
        return {}
    out: dict[str, SkillReplacement] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        slot = str(item.get("slot") or "").strip()
        document_id = str(item.get("document_id") or "").strip()
        document_name = str(item.get("document_name") or "").strip()
        body = str(item.get("content") or "")
        if not slot or not document_id or not document_name or not body.strip():
            continue
        out[slot] = SkillReplacement(
            summary=str(item.get("description") or ""),
            body=body,
            document_id=document_id,
            document_name=document_name,
        )
    return out


async def _replacements_for_rows(
    session: AsyncSession,
    user_id: str,
    rows: Sequence[Any],
) -> dict[str, SkillReplacement]:
    if not rows:
        return {}
    repo = DocumentRepository(session)
    out: dict[str, SkillReplacement] = {}
    for row in rows:
        doc = await repo.get(row.document_id, user_id=user_id)
        if doc is None or doc.disputed_at is not None or doc.deleted_at is not None:
            continue
        if (
            doc.role != "rule"
            or doc.apply_mode != "on_demand"
            or doc.ai_maintained
            or doc.folder_id is not None
        ):
            continue
        stripped = strip_entry_frontmatter(doc.content or "")
        if stripped is None or not stripped.strip():
            continue
        out[row.slot_name] = SkillReplacement(
            summary=doc.description or "",
            body=stripped,
            document_id=doc.id,
            document_name=rule_consult_name(doc.name),
        )
    return out


async def resolve_skill_layer(
    session: AsyncSession, user_id: str, folder_id: str | None = None
) -> SkillOverlay:
    """One scope's overlay (caller session). ``folder_id=None`` is the account layer."""
    if not user_id.strip():
        return SkillOverlay(replacements={}, muted=frozenset())
    rows = await SkillSlotRepository(session).list_for_scope(user_id, folder_id)
    muted_rows = await SkillMuteRepository(session).list_for_scope(user_id, folder_id)
    return SkillOverlay(
        replacements=await _replacements_for_rows(session, user_id, rows),
        muted=frozenset(row.slot_name for row in muted_rows),
    )


async def resolve_skill_replacements(
    session: AsyncSession, user_id: str, folder_id: str | None = None
) -> dict[str, SkillReplacement]:
    """Merged replacements for this folder (account when ``folder_id`` is omitted)."""
    overlay = await resolve_skill_overlay(session, user_id, folder_id=folder_id)
    return overlay.replacements


async def resolve_skill_mutes(
    session: AsyncSession, user_id: str, folder_id: str | None = None
) -> frozenset[str]:
    overlay = await resolve_skill_overlay(session, user_id, folder_id=folder_id)
    return overlay.muted


async def _folder_chain_ids(
    session: AsyncSession, folder_id: str, *, owner_user_id: str
) -> list[str]:
    chain = await FolderRepository(session).list_ancestor_chain_ids(
        folder_id, user_id=owner_user_id
    )
    if folder_id not in chain:
        return []
    return chain


async def resolve_skill_overlay_layers(
    session: AsyncSession, user_id: str, folder_id: str | None = None
) -> ResolvedSkillOverlay:
    """Account (caller, private) + desk-owner folder chain (near wins)."""
    account = await resolve_skill_layer(session, user_id, None)
    if not folder_id or not folder_id.strip():
        return ResolvedSkillOverlay(merged=account, here=account)

    from agentcore.folders.desk import resolve_folder_owner_user_id

    owner = await resolve_folder_owner_user_id(folder_id, session=session) or user_id
    chain = await _folder_chain_ids(session, folder_id, owner_user_id=owner)
    layers = [account]
    here = SkillOverlay(replacements={}, muted=frozenset())
    for fid in chain:
        layer = await resolve_skill_layer(session, owner, fid)
        layers.append(layer)
        if fid == folder_id:
            here = layer
    return ResolvedSkillOverlay(merged=merge_skill_overlays(*layers), here=here)


async def resolve_skill_overlay(
    session: AsyncSession, user_id: str, folder_id: str | None = None
) -> SkillOverlay:
    resolved = await resolve_skill_overlay_layers(session, user_id, folder_id)
    return resolved.merged


async def load_skill_replacements_from_db(
    user_id: str, folder_id: str | None = None
) -> dict[str, SkillReplacement]:
    """Consult path: own a session; overlay IO must not break the turn."""
    overlay = await load_skill_overlay_from_db(user_id, folder_id=folder_id)
    return overlay.replacements


async def load_skill_overlay_from_db(
    user_id: str, folder_id: str | None = None
) -> SkillOverlay:
    if not user_id.strip():
        return SkillOverlay(replacements={}, muted=frozenset())
    try:
        async with async_session_factory() as session:
            return await resolve_skill_overlay(session, user_id, folder_id=folder_id)
    except Exception as e:  # noqa: BLE001 — consult must not die on overlay IO
        logger.warning(
            "consult.skill_replacements_load_failed",
            user_id=user_id,
            error=str(e),
        )
        return SkillOverlay(replacements={}, muted=frozenset())


async def load_skill_replacements(
    user_id: str, *, folder_id: str | None = None
) -> dict[str, SkillReplacement]:
    overlay = await load_skill_overlay(user_id, folder_id=folder_id)
    return overlay.replacements


async def load_skill_overlay(
    user_id: str, *, folder_id: str | None = None
) -> SkillOverlay:
    """Ticketed sidecar reads the prepare snapshot; cloud reads the overlay tables."""
    empty = SkillOverlay(replacements={}, muted=frozenset())
    try:
        from agentcore.account.credentials import get_account_credentials
        from agentcore.memory.account_prepare_cache import (
            get_account_rules_memory_snapshot,
            prepare_account_folder_id,
        )

        if get_account_credentials() is not None:
            fid = folder_id if folder_id is not None else prepare_account_folder_id.get()
            snap = get_account_rules_memory_snapshot(user_id, fid)
            if snap is None:
                return empty
            return overlay_from_payload(snap.rules_payload)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "consult.skill_replacements_snapshot_failed",
            user_id=user_id,
            error=str(e),
        )
        return empty
    return await load_skill_overlay_from_db(user_id, folder_id=folder_id)
