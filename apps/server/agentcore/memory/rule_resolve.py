"""Nearest-name resolution for user rules.

Same consult name across 常驻 / 按需 / 路径: the nearest desk wins, and that
document is the only one injected. Different names accumulate, outer desks
first. An empty 按需 description claims the name and stays out of the catalog.
Unbounded path patterns count as 常驻.
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
    """True when this body occupies the 常驻 pool (always, or an unbounded path rule)."""
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
    description = parsed.description if parsed.description_present else _description_of(doc)
    return LiveRule(
        name=name,
        content=content,
        description=(description or "").strip(),
        apply=apply,
        patterns=parsed.paths,
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
    if rule.apply == "always":
        return "always"
    if rule.apply == "on_demand":
        return "on_demand" if rule.description else None
    if rule.apply != "paths":
        return None
    if not rule.patterns:
        return None
    if patterns_are_unbounded(rule.patterns):
        return "always"
    if not rule.description:
        return None
    return "paths"


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
