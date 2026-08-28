"""Disk is the delivery truth: tool self-report is not a landed file.

Worker ``COMPLETED`` still means the loop ended (甲⁺). User-facing
``delivered_files`` / 「路径已核」 only count paths that exist on the workspace.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agentcore.runtime.runs.file_acceptance import reject_absent_paths
from agentcore.runtime.runs.types import RunState
from agentcore.workspace.protocol import WorkspaceError


async def collect_absent_paths(backend: Any, paths: Sequence[str]) -> set[str]:
    """Paths that are not a regular file on ``backend`` (fail-closed on I/O error)."""
    absent: set[str] = set()
    seen: set[str] = set()
    exists = getattr(backend, "exists", None)
    if not callable(exists):
        return absent
    for raw in paths:
        path = str(raw or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        try:
            ok = await exists(path)
        except (WorkspaceError, OSError, TimeoutError):
            absent.add(path)
            continue
        except Exception:
            absent.add(path)
            continue
        if not ok:
            absent.add(path)
    return absent


def _acceptance_paths(state: RunState) -> list[str]:
    out: list[str] = []
    for row in state.file_acceptance or []:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path") or "").strip()
        if path:
            out.append(path)
    return out


async def stamp_results_disk_truth(
    results: Mapping[str, RunState | None],
    backend: Any,
) -> None:
    """Mutate ``file_acceptance``: accepted-but-missing → ``rejected(not_on_disk)``.

    No-op without a backend. Does not change ``RunPhase``.
    """
    if backend is None:
        return
    paths: list[str] = []
    for state in results.values():
        if state is None:
            continue
        paths.extend(_acceptance_paths(state))
    if not paths:
        return
    absent = await collect_absent_paths(backend, paths)
    if not absent:
        return
    for state in results.values():
        if state is None or not state.file_acceptance:
            continue
        state.file_acceptance = reject_absent_paths(state.file_acceptance, absent)
