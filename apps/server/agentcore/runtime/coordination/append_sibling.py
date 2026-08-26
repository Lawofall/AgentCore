"""Same-batch sibling seat / artifact crosses (append admit pre-emit gate).

``sibling_role`` rejects **true parallel double-booking** of a seat: same
normalized role, no ancestor edge, and no disjoint deliverable scope. Serial
same-seat via ``depends_on`` is a legal handoff. Parallel fan-out that keeps
the same role name but scopes distinct deliverables (playbook evaluators /
angle specialists) is also legal — do not force playbook renames.

``sibling_artifact`` rejects parallel desk×path crosses; ancestor pairs skip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentcore.runtime.coordination.isomorphic import _node_role, _node_task
from agentcore.workspace.write_claims import (
    make_ownership_key,
    normalize_ownership_path,
    ownership_display_path,
    resolve_ownership_desk,
)

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan

# Paths like site/copy.md, `site/index.html`, ./foo/bar.ts
_PATH_RE = re.compile(
    r"(?:`|\"|')?"
    r"((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9.+-]+)"
    r"(?:`|\"|')?"
)


@dataclass(frozen=True)
class AppendOverlap:
    """One new node colliding with one live / ownership holder."""

    new_role: str
    new_run_id: str
    live_role: str
    live_run_id: str
    reason: str  # "role" | "deliverable" | "role+deliverable" | "sibling_role" | "sibling_artifact"
    # Bare rel_path for user-facing reject text (sibling / deliverable); never desk key.
    path: str = ""


def _norm_role(role: str) -> str:
    """Seat key: strip whitespace, lowercase."""
    return "".join((role or "").lower().split())


def roles_overlap(a: str, b: str) -> bool:
    """True when two roles claim the same seat (normalized name equality)."""
    na, nb = _norm_role(a), _norm_role(b)
    if not na or not nb:
        return False
    return na == nb


def _normalize_path(path: str) -> str:
    """Align with WriteCoordinator keys (case-preserving)."""
    return normalize_ownership_path(path)


def _paths_in_text(text: str) -> set[str]:
    if not text:
        return set()
    return {_normalize_path(m.group(1)) for m in _PATH_RE.finditer(text)}


def node_artifact_paths(node: Any) -> set[str]:
    """Concrete ``deliverable.artifacts`` bare file paths (display / acceptance).

    Directory prefixes, stage dirs, and globs are acceptance-only — excluded here.
    Ledger keys use :func:`node_artifact_keys` (desk × path).
    """
    from agentcore.runtime.runs.artifact_dir import is_file_ownership_path

    out: set[str] = set()
    deliverable = getattr(node, "deliverable", None)
    if deliverable is None:
        return out
    for art in getattr(deliverable, "artifacts", None) or []:
        if isinstance(art, str) and art.strip() and is_file_ownership_path(art):
            key = _normalize_path(art)
            if key:
                out.add(key)
    return out


def node_ownership_desk(
    node: Any,
    *,
    birth_desk_id: str | None = None,
) -> str:
    """Effective desk for a plan node: ``target_folder_id or birth desk``."""
    return resolve_ownership_desk(
        target_folder_id=getattr(node, "target_folder_id", None),
        birth_desk_id=birth_desk_id,
    )


def node_artifact_keys(
    node: Any,
    *,
    birth_desk_id: str | None = None,
) -> set[str]:
    """Composite ownership keys (desk × path) for declare / sibling / handoff."""
    desk = node_ownership_desk(node, birth_desk_id=birth_desk_id)
    return {make_ownership_key(desk, p) for p in node_artifact_paths(node) if p}


def node_file_targets(node: Any) -> set[str]:
    """Declared artifact paths + paths mentioned in the task text."""
    out = set(node_artifact_paths(node))
    out |= _paths_in_text(_node_task(node))
    return out


def _ancestors_for_plan(plan: RunPlan) -> dict[str, frozenset[str]]:
    from agentcore.runtime.runs.executor.context import _ancestors_by_id

    return _ancestors_by_id(plan)


def _deliverable_scope(node: Any) -> frozenset[str]:
    """Scope tokens that distinguish fan-out peers sharing a role name.

    Empty scope = whole-seat claim (collides with any same-role peer). Non-empty
    disjoint scopes (distinct artifact paths) are
    intentional parallel fan-out — not double-booking.
    """
    tokens: set[str] = set()
    for path in node_artifact_paths(node):
        tokens.add(f"art:{path}")
    return frozenset(tokens)


def find_sibling_role_crosses(plan: RunPlan) -> list[AppendOverlap]:
    """Same-batch double-booking of a seat (no ancestor, no disjoint scope).

    Parallel peers with equal ``_norm_role`` are ``sibling_role`` unless:
    - one is an ancestor of the other (``depends_on`` serial handoff), or
    - both declare non-empty, disjoint deliverable scopes (fan-out).
    """
    if not plan.nodes:
        return []
    ancestors = _ancestors_for_plan(plan)
    by_seat: dict[str, list[Any]] = {}
    for n in plan.nodes:
        seat = _norm_role(_node_role(n))
        if not seat:
            continue
        by_seat.setdefault(seat, []).append(n)
    hits: list[AppendOverlap] = []
    for holders in by_seat.values():
        if len(holders) < 2:
            continue
        for i, a in enumerate(holders):
            for b in holders[i + 1 :]:
                a_anc = ancestors.get(a.run_id, frozenset())
                b_anc = ancestors.get(b.run_id, frozenset())
                if a.run_id in b_anc or b.run_id in a_anc:
                    continue
                scope_a = _deliverable_scope(a)
                scope_b = _deliverable_scope(b)
                if scope_a and scope_b and scope_a.isdisjoint(scope_b):
                    continue
                hits.append(
                    AppendOverlap(
                        new_role=_node_role(b) or b.run_id,
                        new_run_id=b.run_id,
                        live_role=_node_role(a) or a.run_id,
                        live_run_id=a.run_id,
                        reason="sibling_role",
                    )
                )
    return hits


def find_sibling_artifact_crosses(
    plan: RunPlan,
    *,
    birth_desk_id: str | None = None,
) -> list[AppendOverlap]:
    """Same-batch seat + desk×artifact crosses (shared pre-emit / admit gate).

    Includes :func:`find_sibling_role_crosses` so fresh-batch callers that only
    invoke this helper still reject normalized role-name duplicates. Artifact
    pairs still skip when one node is an ancestor of the other.
    """
    hits: list[AppendOverlap] = find_sibling_role_crosses(plan)
    if not plan.nodes:
        return hits
    ancestors = _ancestors_for_plan(plan)
    by_key: dict[str, list[Any]] = {}
    for n in plan.nodes:
        for key in node_artifact_keys(n, birth_desk_id=birth_desk_id):
            by_key.setdefault(key, []).append(n)
    seen_pairs: set[tuple[str, str]] = set()
    for key, holders in by_key.items():
        if len(holders) < 2:
            continue
        bare = ownership_display_path(key)
        for i, a in enumerate(holders):
            for b in holders[i + 1 :]:
                a_anc = ancestors.get(a.run_id, frozenset())
                b_anc = ancestors.get(b.run_id, frozenset())
                if a.run_id in b_anc or b.run_id in a_anc:
                    continue
                pair = tuple(sorted((a.run_id, b.run_id)))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                hits.append(
                    AppendOverlap(
                        new_role=_node_role(b) or b.run_id,
                        new_run_id=b.run_id,
                        live_role=_node_role(a) or a.run_id,
                        live_run_id=a.run_id,
                        reason="sibling_artifact",
                        path=bare,
                    )
                )
    return hits
