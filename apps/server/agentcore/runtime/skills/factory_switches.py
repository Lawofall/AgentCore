"""Per-assembly omit list for official HOW rows.

Empty list = every factory skill stays in ``<按需目录>`` and ``consult``.
A name on the list leaves both the directory row and fetch. New skills
default on once the recipe key is gone. Official 标准 / 完整 omit none.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from agentcore.runtime.skills.catalog import FACTORY_SKILL_NAMES
from agentcore.runtime.skills.product_help import (
    PRODUCT_HELP_NAME,
    PRODUCT_HELP_SECTION_SEP,
)

_OMITTED: ContextVar[frozenset[str]] = ContextVar(
    "omitted_factory_skills",
    default=frozenset(),
)


def all_factory_skill_names() -> tuple[str, ...]:
    """Every official HOW name, in registry order."""
    return FACTORY_SKILL_NAMES


def normalize_omitted_factory_skills(raw: object) -> tuple[str, ...]:
    """Keep known names, drop the rest, stable registry order."""
    if not isinstance(raw, (list, tuple)):
        return ()
    present = {item for item in raw if isinstance(item, str)}
    return tuple(name for name in FACTORY_SKILL_NAMES if name in present)


def omitted_factory_skills_from_flag(omit_all: bool) -> tuple[str, ...]:
    """Recipe shorthand: omit every current name, or none."""
    return FACTORY_SKILL_NAMES if omit_all else ()


def bind_omitted_factory_skills(raw: object) -> Token[frozenset[str]]:
    """Publish this turn's omitted official HOW names."""
    return _OMITTED.set(frozenset(normalize_omitted_factory_skills(raw)))


def reset_omitted_factory_skills(token: Token[frozenset[str]]) -> None:
    _OMITTED.reset(token)


def current_omitted_factory_skills() -> frozenset[str]:
    return _OMITTED.get()


def current_omit_factory_catalog() -> bool:
    """True when every current official HOW name is omitted."""
    omitted = _OMITTED.get()
    return bool(FACTORY_SKILL_NAMES) and all(name in omitted for name in FACTORY_SKILL_NAMES)


def consult_root_name(key: str) -> str:
    """Map ``product_help:<section>`` back to the factory skill name."""
    prefix = PRODUCT_HELP_NAME + PRODUCT_HELP_SECTION_SEP
    if key.startswith(prefix):
        return PRODUCT_HELP_NAME
    return key


def include_factory_skill(name: str) -> bool:
    """True when this turn still lists and fetches that official HOW."""
    return consult_root_name(name) not in _OMITTED.get()
