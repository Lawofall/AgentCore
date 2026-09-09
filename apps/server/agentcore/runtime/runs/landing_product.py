"""Product-landing path gate for pinned ``artifacts`` / ``artifact_dir``.

Any successful workspace write counts as product landing — including intermediate
dossier notes under ``AgentCore/文档/{research,reviews,debate}/``. Declared
``deliverable.artifacts`` no longer gate whether a dossier path counts.
"""

from __future__ import annotations

from collections.abc import Sequence

from agentcore.runtime.runs.contract import _normalize_artifact_relpath
from agentcore.tools.file_products import LANDING_TOOLS
from agentcore.workspace._paths import sanitize_write_relpath
from agentcore.workspace.stage_dirs import (
    DEBATE_DIR,
    DEBATE_PREFIX,
    RESEARCH_DIR,
    RESEARCH_PREFIX,
    REVIEWS_DIR,
    REVIEWS_PREFIX,
)

__all__ = [
    "is_dossier_intermediate_path",
    "is_product_landing_path",
    "filter_product_landing_paths",
    "landing_tool_path_from_args",
]


def is_dossier_intermediate_path(path: str) -> bool:
    """True when ``path`` sits under research / reviews / debate stage dirs."""
    p = _normalize_artifact_relpath(path)
    if not p:
        return False
    return (
        p in (RESEARCH_DIR, REVIEWS_DIR, DEBATE_DIR)
        or p.startswith(RESEARCH_PREFIX)
        or p.startswith(REVIEWS_PREFIX)
        or p.startswith(DEBATE_PREFIX)
    )


def is_product_landing_path(
    path: str | None,
    artifacts: Sequence[str] | None = None,
) -> bool:
    """Whether a landed path counts as product for files-form gates.

    Every workspace write counts (dossier notes included). Missing / empty path
    → ``True`` (compat for ``ToolAttempt`` without ``meta.path``). ``artifacts``
    is retained for call-site compatibility and is not consulted.
    """
    _ = artifacts
    return True


def filter_product_landing_paths(
    paths: Sequence[str],
    artifacts: Sequence[str] | None = None,
) -> list[str]:
    """Keep non-empty landed paths (stable order). ``artifacts`` unused (compat)."""
    _ = artifacts
    out: list[str] = []
    for raw in paths:
        if not raw or not str(raw).strip():
            continue
        out.append(str(raw))
    return out


def landing_tool_path_from_args(tool_name: str, args: dict | None) -> str | None:
    """A landing tool's TARGET path, read off its call arguments.

    This is attempt-level metadata (``ToolAttempt.meta.path``) for governance that runs
    when there is no successful result to read — same-path write-reject streaks, denied
    calls, liveness timeouts. It is **not** the delivery ledger: what a run produced
    comes from the tool's own self-report (``ToolResult.file_products``), never from
    arguments. A relocating call is recognized by its arguments, not by name: it names a
    ``source``, so its target is ``destination`` and a stray ``path`` is never mistaken
    for one; every other write names ``path``. :func:`sanitize_write_relpath` keeps this
    aligned with what the write tools actually land on disk.
    """
    if not isinstance(args, dict) or tool_name not in LANDING_TOOLS:
        return None
    raw = args.get("destination")
    if not isinstance(raw, str) or not raw.strip():
        if isinstance(args.get("source"), str):
            return None
        raw = args.get("path")
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip().replace("\\", "/")
    if not cleaned:
        return None
    return sanitize_write_relpath(cleaned)
