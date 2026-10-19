"""ConversationSnapshot: cloud from_row + sidecar session parse."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentcore.core.errors import ClientTooOldError
from agentcore.core.types import DEFAULT_PERMISSION_AXES, WorkspaceBoundary
from agentcore.runtime.turn.snapshot import (
    ConversationSnapshot,
    SessionRequiredError,
    parse_sidecar_session,
)


def test_from_row_reads_delegate_mode_and_axes():
    conv = SimpleNamespace(
        permission_axes={"boundary": "read"},
        delegate_mode=True,
        folder_id="fold-1",
    )
    snap = ConversationSnapshot.from_row(conv)
    assert snap.permission_axes is WorkspaceBoundary.READ
    assert snap.prefer_delegate is True
    assert snap.folder_id == "fold-1"


def test_from_row_defaults_when_columns_missing():
    snap = ConversationSnapshot.from_row(SimpleNamespace())
    assert snap.permission_axes is DEFAULT_PERMISSION_AXES
    assert snap.prefer_delegate is False
    assert snap.folder_id is None


def test_parse_sidecar_session_requires_complete_object():
    with pytest.raises(SessionRequiredError) as ei:
        parse_sidecar_session({})
    assert str(ei.value) == ClientTooOldError().message

    with pytest.raises(SessionRequiredError):
        parse_sidecar_session(
            {
                "session": {
                    "permissionAxes": {"boundary": "folder"},
                    "delegateMode": True,
                    "folderId": None,
                }
            }
        )


def test_parse_sidecar_session_reads_prefer_delegate_and_bind():
    snap = parse_sidecar_session(
        {
            "session": {
                "permissionAxes": {"boundary": "computer"},
                "delegateMode": True,
                "folderId": "fold-1",
                "localRootId": "root-1",
                "localSubpath": "apps/web",
                "contextBudget": 128000,
            }
        }
    )
    assert snap.permission_axes is WorkspaceBoundary.COMPUTER
    assert snap.prefer_delegate is True
    assert snap.folder_id == "fold-1"
    assert snap.folder_binding_injected is True
    assert snap.folder_local_root_id == "root-1"
    assert snap.folder_local_subpath == "apps/web"
    assert snap.context_budget == 128000


def test_parse_sidecar_session_rejects_non_bool_delegate_mode():
    with pytest.raises(SessionRequiredError):
        parse_sidecar_session(
            {
                "session": {
                    "permissionAxes": {"boundary": "folder"},
                    "delegateMode": 1,
                    "folderId": None,
                    "localRootId": None,
                    "localSubpath": None,
                }
            }
        )
