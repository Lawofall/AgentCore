"""Closed scene groups for the Skill store shelf.

Discovery IA only — not projected onto the prompt tree, not a kind tab.
Persistence leaf: CHECK constraint + API/runtime share the same tuple.
"""

from __future__ import annotations

from typing import Literal

SKILL_STORE_GROUPS = (
    "legal",
    "writing",
    "research",
    "product",
    "engineering",
    "decision",
)

SkillStoreGroupName = Literal[
    "legal",
    "writing",
    "research",
    "product",
    "engineering",
    "decision",
]

SKILL_STORE_GROUP_SQL = (
    "shelf_group in ("
    "'legal', 'writing', 'research', 'product', 'engineering', 'decision'"
    ")"
)


def empty_group_counts() -> dict[str, int]:
    return {name: 0 for name in SKILL_STORE_GROUPS}


def is_skill_store_group(value: str | None) -> bool:
    return value in SKILL_STORE_GROUPS
