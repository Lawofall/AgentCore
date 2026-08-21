"""Workspace and handoff SSE event factories."""

from __future__ import annotations

from typing import Any

from agentcore.runtime.events.types import EventType, SSEEvent


def workspace_op_required(
    *,
    request_id: str,
    conversation_id: str,
    root_id: str,
    op: str,
    args: dict[str, Any],
    timeout_ms: int | None = None,
) -> SSEEvent:
    payload: dict[str, Any] = {
        "request_id": request_id,
        "conversation_id": conversation_id,
        "root_id": root_id,
        "op": op,
        "args": args,
    }
    if timeout_ms is not None:
        payload["timeout_ms"] = int(timeout_ms)
    return SSEEvent(
        type=EventType.WORKSPACE_OP_REQUIRED,
        payload=payload,
    )


def auto_folder_created(*, folder_id: str, name: str) -> SSEEvent:
    """裸聊写盘自动建好云文件夹（双模式工作区 §5.4 裸聊行）。

    只在真正建成那一次发射：跨回合复用同一张桌、或并发回合抢输后改用赢家的桌，都不再
    重复发。不是审批——回合照跑，客户端不得据此挂起；对话内也不再画落点条。
    """
    return SSEEvent(
        type=EventType.AUTO_FOLDER_CREATED,
        payload={"folder_id": folder_id, "name": name},
    )


def handoff_snapshot_done(*, snapshot_id: str, conversation_id: str, size_bytes: int) -> SSEEvent:
    return SSEEvent(
        type=EventType.HANDOFF_SNAPSHOT_DONE,
        payload={
            "snapshot_id": snapshot_id,
            "conversation_id": conversation_id,
            "size_bytes": size_bytes,
        },
    )


def workspace_snapshot_done(
    *, snapshot_id: str, conversation_id: str, size_bytes: int
) -> SSEEvent:
    return SSEEvent(
        type=EventType.WORKSPACE_SNAPSHOT_DONE,
        payload={
            "snapshot_id": snapshot_id,
            "conversation_id": conversation_id,
            "size_bytes": size_bytes,
        },
    )


def workspace_snapshot_failed(*, conversation_id: str) -> SSEEvent:
    return SSEEvent(
        type=EventType.WORKSPACE_SNAPSHOT_FAILED,
        payload={"conversation_id": conversation_id},
    )


def handoff_job_started(*, job_id: str, conversation_id: str, job_conversation_id: str) -> SSEEvent:
    return SSEEvent(
        type=EventType.HANDOFF_JOB_STARTED,
        payload={
            "job_id": job_id,
            "conversation_id": conversation_id,
            "job_conversation_id": job_conversation_id,
        },
    )


def handoff_apply_done(
    *, job_id: str, conversation_id: str, results: list[dict[str, Any]]
) -> SSEEvent:
    counts = {"applied": 0, "skipped": 0, "conflict": 0, "error": 0}
    for r in results:
        status = str(r.get("status", ""))
        if status in counts:
            counts[status] += 1
    return SSEEvent(
        type=EventType.HANDOFF_APPLY_DONE,
        payload={
            "job_id": job_id,
            "conversation_id": conversation_id,
            "results": results,
            "applied": counts["applied"],
            "skipped": counts["skipped"],
            "conflicts": counts["conflict"],
            "errors": counts["error"],
        },
    )
