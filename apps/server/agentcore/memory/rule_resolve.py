"""Resolve a saved markdown document onto always / on-demand / path.

A document enters only when frontmatter states ``apply``, an assembly
membership mode names the channel, or a snapshot list already classified the
body (``column_apply``). A stored row with no ``apply`` key does not enter.
``description`` is required only for the on-demand catalog.

Account rows (rank 0) and the current folder (rank 1) keep the same name.
There is no ancestor rank.
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
    """Winners per scope. ``always`` includes unbounded path rules."""

    always: tuple[LiveRule, ...]
    on_demand: tuple[LiveRule, ...]
    path: tuple[PathRule, ...]


def counts_as_always_content(content: str) -> bool:
    """True when this body occupies the always pool (常驻, or an unbounded path)."""
    parsed = parse_entry_frontmatter(content)
    if isinstance(parsed, FrontmatterError) or not parsed.apply_present:
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
    """One saved markdown document, or None when it has no channel this turn.

    ``apply_override`` is the assembly membership mode for an account document.
    Frontmatter ``apply`` wins next. ``column_apply`` is only the list a
    snapshot already sorted this body into (``global_rules`` and the rest).
    An unclassified row passes an empty column and does not enter.
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
    elif parsed.apply_present:
        apply = parsed.apply
    elif column_apply in _APPLY:
        apply = column_apply
    else:
        return None
    if apply not in _APPLY:
        return None
    description = _description_of(doc).strip()
    if not description and parsed.description_present:
        description = parsed.description.strip()
    if apply == "on_demand" and not description:
        return None
    patterns = parsed.paths if parsed.paths_present else ()
    return LiveRule(
        name=name,
        content=content,
        description=description,
        apply=apply,
        patterns=patterns,
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
    """Last write per (scope rank, name) wins. Scopes do not replace each other."""
    winners: dict[tuple[int, str], LiveRule] = {}
    for rule in rules:
        if rule.apply == "on_demand" and not rule.description.strip():
            continue
        winners[(rule.rank, rule.name)] = rule
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
    on_demand.sort(key=lambda rule: (rule.rank, rule.name))
    path.sort(key=lambda rule: rule.name)
    return ResolvedRules(always=tuple(always), on_demand=tuple(on_demand), path=tuple(path))


def _channel(rule: LiveRule) -> str | None:
    if rule.apply == "always":
        return "always"
    if rule.apply == "on_demand":
        return "on_demand" if rule.description.strip() else None
    if rule.apply == "paths":
        if patterns_are_unbounded(rule.patterns):
            return "always"
        return "paths" if rule.patterns else None
    return None


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
