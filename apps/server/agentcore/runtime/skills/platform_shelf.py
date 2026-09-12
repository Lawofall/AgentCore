"""First-party Skill store SKUs: code templates on the public shelf.

Not system Skills. Listing / version ids are deterministic UUID5s so install
records can key them without a DB listing row. Template body change ⇒ new
version id ⇒ ``has_update`` for already-installed copies.
"""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID, uuid5

from agentcore.runtime.legal_skills import LEGAL_SKILLS, DomainSkillTemplate

PLATFORM_AUTHOR = "官方"
_PLATFORM_NS = UUID("8f3c1a6e-2b47-4d9a-9e15-0c8b7a4d2f61")


def platform_listing_id(name: str) -> str:
    return str(uuid5(_PLATFORM_NS, name))


def platform_version_id(name: str, body: str) -> str:
    digest = sha256(body.encode("utf-8")).hexdigest()
    return str(uuid5(_PLATFORM_NS, f"{name}:{digest}"))


def platform_templates() -> tuple[DomainSkillTemplate, ...]:
    return LEGAL_SKILLS


def get_platform_template(listing_id: str) -> DomainSkillTemplate | None:
    for skill in LEGAL_SKILLS:
        if platform_listing_id(skill.name) == listing_id:
            return skill
    return None


def platform_matches_query(
    skill: DomainSkillTemplate,
    q: str | None,
    group: str | None = None,
) -> bool:
    if group is not None and skill.group != group:
        return False
    needle = (q or "").strip().casefold()
    if not needle:
        return True
    haystacks = (skill.name, skill.title, skill.summary, PLATFORM_AUTHOR)
    return any(needle in text.casefold() for text in haystacks)
