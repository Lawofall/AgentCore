"""Shared sidecar RPC ``session`` snapshot for unit tests."""

from __future__ import annotations

from typing import Any


def sidecar_session(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "permissionAxes": {"boundary": "folder"},
        "delegateMode": False,
        "folderId": None,
        "localRootId": None,
        "localSubpath": None,
    }
    raw.update(overrides)
    return raw
