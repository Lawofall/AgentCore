"""Nearest-name resolution for user rules.

User rules inject on demand only. The index ``description`` is the enrollment
bit: empty stays out and does not hide another desk's enrolled row. Same name:
the nearest enrolled desk wins.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agentcore.documents.frontmatter import FrontmatterError, parse_entry_frontmatter
from agentcore.documents.path_rules import (
    PathRule,
    pattern_is_unbounded,
    patterns_are_unbounded,
)

_APPLY = frozenset({"always", "on_demand", "paths"})


def _consult_name(doc_name: str) -> str:
    return doc_name.removesuffix(".md").strip()


@dataclass(frozen=True)
class LiveRule:
    name: str
    content: str
    description: str
    apply: str
    patterns: tuple[str, ...]
    rank: int


@dataclass(frozen=True)
class ResolvedRules:
    """Winners only. ``always`` includes unbounded path rules."""

    always: tuple[LiveRule, ...]
    on_demand: tuple[LiveRule, ...]
    path: tuple[PathRule, ...]


def counts_as_always_content(content: str) -> bool:
    """True when this body would have occupied the retired user-rule always pool.

    Skill path metering still calls this. User-rule injection does not.
    """
    parsed = parse_entry_frontmatter(content)
    if isinstance(parsed, FrontmatterError):
        return False
    if parsed.apply == "always":
        return True
    return parsed.apply == "paths" and patterns_are_unbounded(parsed.paths)


def live_rule_from_doc(
    doc: object,
    *,
    column_apply: str,
    rank: int,
    apply_override: str | None = None,
) -> LiveRule | None:
    """One stored rule. Frontmatter ``apply`` wins when the key is present.

    ``apply_override`` is the assembly membership mode. It wins over both the
    column and frontmatter for account-level rules on an armed turn.
    """
    raw_name = (
        str(doc.get("name") or "")
        if isinstance(doc, Mapping)
        else str(getattr(doc, "name", "") or "")
    )
    name = _consult_name(raw_name)
    content = _content_of(doc)
    if not name:
        return None
    parsed = parse_entry_frontmatter(content)
    if isinstance(parsed, FrontmatterError):
        return None
    if apply_override in _APPLY:
        apply = apply_override
    else:
        apply = parsed.apply if parsed.apply_present else column_apply
    if apply not in _APPLY:
        return None
    # Enrollment is the index column, not the prose. A description sitting only
    # in the file does not enter the catalog until a person saves it onto the row.
    # Empty does not claim the name.
    description = _description_of(doc).strip()
    if not description:
        return None
    return LiveRule(
        name=name,
        content=content,
        description=description,
        apply="on_demand",
        patterns=(),
        rank=rank,
    )


def live_rule_from_mapping(
    doc: Mapping[str, object],
    *,
    column_apply: str,
    rank: int,
    apply_override: str | None = None,
) -> LiveRule | None:
    return live_rule_from_doc(
        doc,
        column_apply=column_apply,
        rank=rank,
        apply_override=apply_override,
    )


def resolve_rules(rules: Sequence[LiveRule]) -> ResolvedRules:
    """Last write per name wins. Callers pass nearer rules later."""
    winners: dict[str, LiveRule] = {}
    for rule in rules:
        if not rule.description.strip():
            continue
        winners[rule.name] = rule
    always: list[LiveRule] = []
    on_demand: list[LiveRule] = []
    path: list[PathRule] = []
    for rule in winners.values():
        channel = _channel(rule)
        if channel == "always":
            always.append(rule)
        elif channel == "on_demand":
            on_demand.append(rule)
        elif channel == "paths":
            body = _body(rule.content)
            if body:
                path.append(
                    PathRule(
                        name=rule.name,
                        description=rule.description,
                        patterns=tuple(
                            p for p in rule.patterns if not pattern_is_unbounded(p)
                        ),
                        body=body,
                    )
                )
    always.sort(key=lambda rule: (rule.rank, rule.name))
    on_demand.sort(key=lambda rule: rule.name)
    path.sort(key=lambda rule: rule.name)
    return ResolvedRules(always=tuple(always), on_demand=tuple(on_demand), path=tuple(path))


def _channel(rule: LiveRule) -> str | None:
    """User rules inject on demand only."""
    if not rule.description:
        return None
    return "on_demand"


def _body(content: str) -> str:
    parsed = parse_entry_frontmatter(content)
    if isinstance(parsed, FrontmatterError):
        return ""
    return (parsed.body if parsed.has_frontmatter else content).strip()


def _content_of(doc: object) -> str:
    if isinstance(doc, Mapping):
        return str(doc.get("content") or "")
    return str(getattr(doc, "content", "") or "")


def _description_of(doc: object) -> str:
    if isinstance(doc, Mapping):
        return str(doc.get("description") or "")
    return str(getattr(doc, "description", "") or "")
