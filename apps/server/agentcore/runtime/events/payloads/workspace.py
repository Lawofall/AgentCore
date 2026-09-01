"""Workspace / whiteboard / desktop client-tool SSE payload wire models
(factories: ``runtime/events/workspace.py`` / ``board.py`` / ``desktop.py``)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agentcore.runtime.events.payloads._base import WirePayload, absent


class WorkspaceOpRequiredPayload(WirePayload):
    """Transport-only client-tool request: apply a workspace file op on the bound
    desktop and POST the result back. NOT journaled.

    ``timeout_ms`` (optional): liveness budget echoed from the server channel so the
    desktop can AbortSignal the IPC op; derived from the outer tool deadline.
    """

    request_id: str
    conversation_id: str
    root_id: str
    op: str
    args: dict[str, Any]
    timeout_ms: int | None = absent()


class BoardOp(WirePayload):
    """One structured whiteboard op (AI协作白板 M2). The closed verb set is shared with
    the server tool + the desktop applier; fields beyond `op` are op-specific."""

    op: Literal["add_node", "connect", "move", "set_text", "delete", "group"]
    ref: str | None = absent()
    id: str | None = absent()
    kind: Literal["sticky", "rectangle", "ellipse", "diamond", "text"] | None = absent()
    text: str | None = absent()
    x: float | None = absent()
    y: float | None = absent()
    width: float | None = absent()
    height: float | None = absent()
    color: str | None = absent()
    from_: str | None = Field(
        default=None, alias="from", json_schema_extra={"ts": "absent"}
    )
    to: str | None = absent()
    label: str | None = absent()
    members: list[str] | None = absent()


class BoardOpRequiredPayload(WirePayload):
    """Transport-only client-tool request: apply a batch of board ops to the open
    whiteboard canvas (`board_id`). The board counterpart of `workspace_op_required`;
    NOT journaled."""

    request_id: str
    conversation_id: str
    board_id: str
    ops: list[BoardOp]
    summary: str


class BoardReadRequiredPayload(WirePayload):
    """Transport-only client-tool request: rasterize board elements (`ids`) to a PNG and
    POST it back so the vision reader can read it. NOT journaled."""

    request_id: str
    conversation_id: str
    board_id: str
    ids: list[str]


class DesktopNotifyRequiredPayload(WirePayload):
    """Transport-only client-tool request: show an OS notification on the bound desktop
    (`desktop_notify`). NOT journaled."""

    request_id: str
    conversation_id: str
    title: str
    body: str | None = absent()


class ExternalMountReadonlyRequiredPayload(WirePayload):
    """Transport-only client-tool request: silently mount a local directory read-only
    (`external_mount_readonly`). Path transport exception — may carry `path` /
    `well_known`+`target_name` for desktop resolve; success result must not include abs.
    NOT journaled."""

    request_id: str
    conversation_id: str
    path: str | None = absent()
    well_known: str | None = absent()
    target_name: str | None = absent()


class HostOpRequiredPayload(WirePayload):
    """Transport-only client-tool request: run a Host op on the bound desktop
    (`host_*` tools). NOT journaled."""

    request_id: str
    conversation_id: str
    op: str
    args: dict[str, Any] = Field(default_factory=dict)


class McpOpRequiredPayload(WirePayload):
    """Transport-only client-tool request: run a local MCP Client op on the bound
    desktop (stdio list_tools / call_tool). NOT journaled."""

    request_id: str
    conversation_id: str
    op: str
    args: dict[str, Any] = Field(default_factory=dict)


class AutoFolderCreatedPayload(WirePayload):
    """裸聊写盘自动建的云文件夹（双模式工作区 §5.4 裸聊行）——告知落点，不改会话归属。

    ``name`` 是建桌那一刻的名字；用户当场改名后客户端以文件夹现名为准（按 ``folder_id``
    查），本 payload 不追改名。
    """

    folder_id: str
    name: str


class HandoffSnapshotDonePayload(WirePayload):
    snapshot_id: str
    conversation_id: str
    size_bytes: int


class WorkspaceSnapshotDonePayload(WirePayload):
    """Post-turn auto-backup succeeded (EPHEMERAL — clears failure UX)."""

    snapshot_id: str
    conversation_id: str
    size_bytes: int


class WorkspaceSnapshotFailedPayload(WirePayload):
    """Post-turn auto-backup failed (EPHEMERAL — toast / panel banner; no error detail)."""

    conversation_id: str


class HandoffJobStartedPayload(WirePayload):
    job_id: str
    conversation_id: str
    job_conversation_id: str


class HandoffApplyResult(WirePayload):
    path: str
    status: Literal["applied", "skipped", "conflict", "error"]
    change_type: Literal["added", "modified", "deleted"] | None
    detail: str


class HandoffApplyDonePayload(WirePayload):
    job_id: str
    conversation_id: str
    results: list[HandoffApplyResult]
    applied: int
    skipped: int
    conflicts: int
    errors: int
