"""Account-level 交代 membership on an assembly.

Folder 设定 stay on the document. Account rules (``folder_id`` is null) inject
from ``assembly_skills``. Creating or editing one of those documents keeps the
starred assembly's row in step, so the library and the star agree until a
later edit names a different mode on another assembly.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models import AssemblySkill, LlmModelProfile


async def skill_maps(
    session: AsyncSession, assembly_ids: Sequence[str]
) -> dict[str, dict[str, str]]:
    """document id → apply mode, keyed by assembly. Missing ids are absent."""
    ids = [item for item in assembly_ids if item]
    if not ids:
        return {}
    result = await session.execute(
        select(AssemblySkill).where(AssemblySkill.assembly_id.in_(ids))
    )
    out: dict[str, dict[str, str]] = {}
    for skill in result.scalars():
        out.setdefault(skill.assembly_id, {})[skill.document_id] = skill.apply_mode
    return out

_MODES = frozenset({"always", "on_demand", "paths"})


def _is_account_rule(doc: object) -> bool:
    if getattr(doc, "folder_id", None):
        return False
    if getattr(doc, "ai_maintained", False):
        return False
    if getattr(doc, "kind", None) != "document":
        return False
    from agentcore.memory.rule_resolve import live_rule_from_doc

    return live_rule_from_doc(doc, column_apply="", rank=0) is not None


async def seed_assembly_skills_from_account(
    session: AsyncSession, user_id: str, assembly_id: str
) -> None:
    """Copy every account markdown that has a 带法 onto a new assembly."""
    from agentcore.db.repositories import DocumentRepository
    from agentcore.memory.rule_resolve import live_rule_from_doc

    repo = DocumentRepository(session)
    docs = await repo.list_scoped_markdown(user_id, None)
    seen: set[str] = set()
    for doc in docs:
        if doc.id in seen:
            continue
        live = live_rule_from_doc(doc, column_apply="", rank=0)
        if live is None or live.apply not in _MODES:
            continue
        seen.add(doc.id)
        session.add(
            AssemblySkill(
                assembly_id=assembly_id,
                document_id=doc.id,
                apply_mode=live.apply,
            )
        )
    if seen:
        await session.commit()


async def copy_assembly_skills(
    session: AsyncSession, source_id: str, dest_id: str
) -> None:
    result = await session.execute(
        select(AssemblySkill).where(AssemblySkill.assembly_id == source_id)
    )
    rows = list(result.scalars())
    for skill in rows:
        session.add(
            AssemblySkill(
                assembly_id=dest_id,
                document_id=skill.document_id,
                apply_mode=skill.apply_mode,
            )
        )
    if rows:
        await session.commit()


async def note_account_rule(session: AsyncSession, doc: object) -> None:
    """Keep the starred assembly's membership aligned with an account-rule write.

    The star's row takes the new mode, or is created when the star does not
    name the document yet (market install and a new 交代). Other assemblies
    keep their own mode.
    """
    if not _is_account_rule(doc):
        return
    user_id = str(getattr(doc, "user_id", "") or "")
    document_id = str(getattr(doc, "id", "") or "")
    mode = str(getattr(doc, "apply_mode", "") or "")
    if not user_id or not document_id:
        return
    from agentcore.db.models import User

    user = await session.get(User, user_id)
    aid = getattr(user, "default_assembly_id", None) if user else None
    if not isinstance(aid, str) or not aid:
        return
    row = await session.get(LlmModelProfile, aid)
    if row is None or row.user_id != user_id:
        return
    existing = await session.get(AssemblySkill, (aid, document_id))
    if existing is None:
        session.add(
            AssemblySkill(assembly_id=aid, document_id=document_id, apply_mode=mode)
        )
    else:
        existing.apply_mode = mode
    await session.commit()
