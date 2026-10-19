"""Shared sidecar RPC ``session`` snapshot for unit tests."""

from __future__ import annotations

from typing import Any

from agentcore.assembly.recipes import FULL, capability_preset_id


def sidecar_capability(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "assemblyId": capability_preset_id(FULL),
        "disabledTools": [],
        "omittedProjections": [],
        "omittedFactorySkills": [],
        "omitDeskRules": False,
        "enabledMcpServerIds": [],
        "skillMembership": None,
    }
    raw.update(overrides)
    return raw


def sidecar_session(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "permissionAxes": {"boundary": "folder"},
        "delegateMode": False,
        "folderId": None,
        "localRootId": None,
        "localSubpath": None,
        "capability": sidecar_capability(),
    }
    raw.update(overrides)
    return raw
