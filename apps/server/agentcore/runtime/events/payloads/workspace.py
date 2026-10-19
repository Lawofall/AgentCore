"""Workspace / desktop client-tool SSE payload wire models
(factories: ``runtime/events/workspace.py`` / ``desktop.py``)."""

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


class ExternalMountRequiredPayload(WirePayload):
    """Transport-only client-tool request: mount a local directory for file tools.
    Path transport exception — may carry `path` / `well_known`+`target_name` for
    desktop resolve; success result must not include abs. ``mode`` is omitted
    on readonly; ``organize`` / ``attach_rw`` request a write upgrade.
    Confirm vs silent is the conversation boundary on desktop. ``root_id``
    upgrades an existing session root (no picker).
    NOT journaled."""

    request_id: str
    conversation_id: str
    path: str | None = absent()
    well_known: str | None = absent()
    target_name: str | None = absent()
    mode: Literal["organize", "attach_rw"] | None = absent()
    root_id: str | None = absent()


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
