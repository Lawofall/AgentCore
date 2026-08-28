"""Disk-as-truth stamp: accepted self-report that is not on disk is rejected."""

from __future__ import annotations

import pytest

from agentcore.runtime.runs.disk_truth import stamp_results_disk_truth
from agentcore.runtime.runs.file_acceptance import REASON_NOT_ON_DISK, build_file_acceptance
from agentcore.runtime.runs.types import RunPhase, RunState


class _ExistsBackend:
    def __init__(self, present: set[str]) -> None:
        self.present = present

    async def exists(self, path: str) -> bool:
        return path in self.present


@pytest.mark.asyncio
async def test_stamp_rejects_claimed_path_missing_on_disk():
    state = RunState(
        phase=RunPhase.COMPLETED,
        content="ok",
        files_touched=["build/icon.ico", "src/a.ts"],
        file_acceptance=build_file_acceptance(
            ["build/icon.ico", "src/a.ts"],
            phase=RunPhase.COMPLETED,
        ),
    )
    results = {"w1": state}
    await stamp_results_disk_truth(results, _ExistsBackend({"src/a.ts"}))
    by_path = {row["path"]: row for row in state.file_acceptance}
    assert by_path["build/icon.ico"]["status"] == "rejected"
    assert by_path["build/icon.ico"]["reason"] == REASON_NOT_ON_DISK
    assert by_path["src/a.ts"]["status"] == "accepted"
    assert state.phase is RunPhase.COMPLETED


@pytest.mark.asyncio
async def test_stamp_noops_without_backend():
    state = RunState(
        phase=RunPhase.COMPLETED,
        file_acceptance=build_file_acceptance(["a.md"], phase=RunPhase.COMPLETED),
    )
    await stamp_results_disk_truth({"w1": state}, None)
    assert state.file_acceptance[0]["status"] == "accepted"
