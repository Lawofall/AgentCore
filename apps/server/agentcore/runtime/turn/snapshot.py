"""Conversation-row snapshot for one turn.

Adapters (cloud turn handler / sidecar RPC) construct this. The engine never
loads permission / 统筹 / folder from the conversations table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentcore.core.types import DEFAULT_PERMISSION_AXES, WorkspaceBoundary


@dataclass(frozen=True, slots=True)
class ConversationSnapshot:
    """Session flags in force for this turn.

    Required fields have no engine-side DB fallback. A new conversation-row
    flag that the turn needs belongs here — not as another optional RPC key.
    """

    permission_axes: WorkspaceBoundary
    prefer_delegate: bool
    folder_id: str | None
    folder_binding_injected: bool = False
    folder_local_root_id: str | None = None
    folder_local_subpath: str | None = None
    context_budget: int | None = None

    @classmethod
    def default(
        cls,
        *,
        permission_axes: WorkspaceBoundary | None = None,
        prefer_delegate: bool = False,
        folder_id: str | None = None,
        folder_binding_injected: bool = False,
        folder_local_root_id: str | None = None,
        folder_local_subpath: str | None = None,
        context_budget: int | None = None,
    ) -> ConversationSnapshot:
        """Evals / tests / jobs with no conversation row."""
        return cls(
            permission_axes=permission_axes
            if permission_axes is not None
            else DEFAULT_PERMISSION_AXES,
            prefer_delegate=prefer_delegate,
            folder_id=folder_id,
            folder_binding_injected=folder_binding_injected,
            folder_local_root_id=folder_local_root_id,
            folder_local_subpath=folder_local_subpath,
            context_budget=context_budget,
        )

    @classmethod
    def from_row(
        cls,
        conv: Any,
        *,
        folder_binding_injected: bool = False,
        folder_local_root_id: str | None = None,
        folder_local_subpath: str | None = None,
        context_budget: int | None = None,
    ) -> ConversationSnapshot:
        """Cloud adapter: the conversation row already in hand."""
        raw_axes = getattr(conv, "permission_axes", None)
        try:
            axes = WorkspaceBoundary.from_mapping(raw_axes)
        except ValueError:
            axes = DEFAULT_PERMISSION_AXES
        return cls(
            permission_axes=axes,
            prefer_delegate=bool(getattr(conv, "delegate_mode", False)),
            folder_id=getattr(conv, "folder_id", None),
            folder_binding_injected=folder_binding_injected,
            folder_local_root_id=folder_local_root_id,
            folder_local_subpath=folder_local_subpath,
            context_budget=context_budget,
        )


SESSION_REQUIRED_KEYS = (
    "permissionAxes",
    "delegateMode",
    "folderId",
    "localRootId",
    "localSubpath",
)


class SessionRequiredError(ValueError):
    """startTurn / resume omitted a complete ``session`` snapshot."""


def _normalize_folder_id(raw: Any) -> str | None:
    if raw is None:
        return None
    cleaned = str(raw).strip()
    return cleaned or None


def _normalize_subpath(raw: Any) -> str:
    if raw is None:
        return ""
    return str(raw).strip()


def _optional_budget(raw: Any) -> int | None:
    if raw is None or raw is False:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def parse_sidecar_session(params: dict[str, Any]) -> ConversationSnapshot:
    """Require RPC ``session`` with every snapshot key (null folder = bare)."""
    from agentcore.core.errors import ClientTooOldError

    raw = params.get("session")
    if not isinstance(raw, dict):
        raise SessionRequiredError(ClientTooOldError().message)
    if any(key not in raw for key in SESSION_REQUIRED_KEYS):
        raise SessionRequiredError(ClientTooOldError().message)
    try:
        axes = WorkspaceBoundary.from_mapping(raw.get("permissionAxes"))
    except ValueError as exc:
        raise SessionRequiredError(ClientTooOldError().message) from exc
    delegate = raw.get("delegateMode")
    if not isinstance(delegate, bool):
        raise SessionRequiredError(ClientTooOldError().message)
    return ConversationSnapshot(
        permission_axes=axes,
        prefer_delegate=delegate,
        folder_id=_normalize_folder_id(raw.get("folderId")),
        folder_binding_injected=True,
        folder_local_root_id=_normalize_folder_id(raw.get("localRootId")),
        folder_local_subpath=_normalize_subpath(raw.get("localSubpath")),
        context_budget=_optional_budget(raw.get("contextBudget")),
    )


def apply_snapshot_to_suspension(suspension: Any, snapshot: ConversationSnapshot) -> None:
    """Overlay the current session snapshot onto a claimed pause frame."""
    suspension.folder_id = snapshot.folder_id
    suspension.folder_binding_injected = snapshot.folder_binding_injected
    suspension.folder_local_root_id = snapshot.folder_local_root_id
    suspension.folder_local_subpath = snapshot.folder_local_subpath or ""
