"""System-initiated harvest closing turn (异步团队产出投递 · 支柱 C).

When a detached coordination drive finishes, the harvester calls
:func:`run_harvest_closing_turn` to spawn a CEO turn that adopts the live
execution, consumes queued ``ALL_COMPLETED``, and delivers a final assistant
message. Meta stamps ``origin=execution_harvest`` for attribution.

The synthetic user row is the durable cross-process *claim* (partial unique
index ``uq_messages_execution_harvest``). A second insert for the same
``execution_id`` is ``IntegrityError`` → look up the claim: skip only when
the closing assistant already settled or a live turn lease is still beating;
otherwise continue the CEO turn (crash after insert must not drop the draft).

Credential routing matches ordinary turns / standing-task fires (conversation
model selection + billing preflight) — never hardcode ``llm_credentials=None``
(that silently falls through to the platform key).

When preflight refuses (quota / BYOK missing), or the local workspace channel is
already sticky-dead / dies during the closing turn, :func:`persist_harvest_fallback`
renders a **user-facing** close from session facts (products, node terminals,
uncompensated tool failures) — never ``format_for_ceo`` / ALL_COMPLETED.output,
which is CEO-audience text. No second LLM call (A1 / channel-dead harvest).

崩溃重驱恢复（D5 定案）走同一条收口，但**归属原回合**：``session.recovered_turn_id``
是被中断的那条助手消息，这次收口续写它（不建合成用户消息、不新开助手消息），成果
落回原气泡，原消息上的「曾中断恢复」标记由 ``recover`` 侧写入。
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy.exc import IntegrityError

from agentcore.billing.gate import preflight_llm_credentials
from agentcore.config import settings
from agentcore.conversation.common import (
    resolve_local_binding,
    resolve_permission_axes,
    resolve_profile_set,
)
from agentcore.conversation.history import load_chat_context
from agentcore.conversation.turn_backend import build_turn_backend
from agentcore.conversation.turn_runner import run_and_persist
from agentcore.core.errors import AgentCoreError
from agentcore.core.logging import get_logger
from agentcore.core.message_merge import (
    MESSAGE_STATUS_COMPLETE,
    MESSAGE_STATUS_FAILED,
    MESSAGE_STATUS_INCOMPLETE,
    MESSAGE_STATUS_RUNNING,
)
from agentcore.db.base import async_session_factory
from agentcore.db.models.conversations import is_execution_harvest_conflict
from agentcore.db.repositories import (
    BoardRepository,
    ConversationRepository,
    CostEventRepository,
    MessageRepository,
    UserRepository,
)
from agentcore.llm.resolve import (
    platform_llm_credentials,
    resolve_conversation_model_selection,
)
from agentcore.push import PushNotification, notify_user
from agentcore.runtime.events import EventSink
from agentcore.runtime.turn.runs import turn_runs
from agentcore.workspace.limits import (
    CHANNEL_DEAD_PREPARE_ABORT,
    CHANNEL_DEAD_USER_VISIBLE,
    EXEC_ENV_CLOUD_SANDBOX_DEAD_BODY_MARKER,
    EXEC_ENV_DEAD_BODY_MARKER,
    exec_env_dead_user_visible,
    is_channel_dead_detail,
)
from agentcore.workspace.protocol import WorkspaceIOError

if TYPE_CHECKING:
    from agentcore.llm.credentials import LLMCredentials
    from agentcore.runtime.coordination.session import CoordinationSession

logger = get_logger(__name__)

HarvestKind = Literal["success", "failure", "cancelled"]


def _coordination_parent_trace_id(
    session: CoordinationSession,
    *,
    recovered_turn_id: str | None = None,
) -> str | None:
    """Sync resolve of the coordination host turn's trace (writer or log context)."""
    from agentcore.core.log_context import get_log_value

    host_turn_id = (recovered_turn_id or session.host_turn_id or "").strip()
    writer = session.host_journal_writer
    if writer is not None:
        writer_tid = (getattr(writer, "turn_id", "") or "").strip()
        if not host_turn_id or writer_tid == host_turn_id or not writer_tid:
            wt = getattr(writer, "trace_id", None)
            if wt and str(wt).strip():
                return str(wt).strip()
    ctx = get_log_value("trace_id")
    return ctx or None


async def _resolve_harvest_trace_id(
    session: CoordinationSession,
    conversation_id: str,
    *,
    recovered_turn_id: str | None = None,
    db: Any | None = None,
) -> str:
    """Continue the arming turn's trace for harvest (same user interaction)."""
    from agentcore.core.log_context import get_log_value, new_trace_id

    parent = _coordination_parent_trace_id(
        session, recovered_turn_id=recovered_turn_id
    )
    if parent:
        return parent

    host_turn_id = (recovered_turn_id or session.host_turn_id or "").strip()
    if host_turn_id and db is not None:
        try:
            row = await MessageRepository(db).get_by_id(
                host_turn_id, conversation_id=conversation_id
            )
            if row and row.trace_id:
                tid = str(row.trace_id).strip()
                if tid:
                    return tid
        except (TypeError, AttributeError):
            # Broken / mocked session in unit tests — fall through to context / mint.
            pass

    ctx = get_log_value("trace_id")
    if ctx:
        return ctx

    return new_trace_id()


# Prompt copy only. Persisted ``harvest_kind`` stays success/failure/cancelled
# (soft_stop still classifies as cancelled); wording is picked separately.
_HARVEST_DONT_PASTE = (
    "勿粘贴协调事件 / 队员终态名册 / escalation 原文 / 中间合成草稿"
)

_HARVEST_USER_TEXT: dict[HarvestKind, str] = {
    "success": (
        "【系统收口】后台团队本波任务已全部完成。请综合队员产出，按终稿纪律向老板报告本波结果："
        f"交付物在前，过程简述从简；{_HARVEST_DONT_PASTE}；"
        "未交付的承诺产物须显式列出。"
        "活没干完就接着干；不需要后续动作则按终稿交付即可。"
    ),
    "failure": (
        "【系统收口】后台团队任务已结束，但有队员失败。请综合已有产出与失败情况向老板交代："
        f"交付物/缺口在前，失败原因简述从简；{_HARVEST_DONT_PASTE}；"
        "未交付的承诺产物须显式列出；勿假装全员成功。"
        "不要把失败当成功继续铺开。"
    ),
    "cancelled": (
        "【系统收口】后台团队任务已取消或中断。请基于已完成部分向老板交代："
        f"已交付与未完成清单在前，说明已取消；{_HARVEST_DONT_PASTE}；"
        "勿宣称已全部完成。调度已停，不要接着派活。"
    ),
}

_HARVEST_SOFT_STOP_USER_TEXT = (
    "【系统收口】后台团队因请示用户而暂停。请基于已完成部分向老板交代当前进展与待决问题："
    f"已交付与未完成清单在前；{_HARVEST_DONT_PASTE}；"
    "勿宣称已全部完成。等用户拍板后再继续，不要自行接着干。"
)

_HARVEST_PUSH: dict[HarvestKind, tuple[str, str]] = {
    "success": ("团队任务已完成", "后台团队已交付终稿，打开对话查看。"),
    "failure": ("团队任务有失败", "后台团队已结束但有失败，打开对话查看收尾。"),
    "cancelled": ("团队任务已取消", "后台团队已取消或中断，打开对话查看收尾。"),
}

_HARVEST_USER_LEAD: dict[HarvestKind, str] = {
    "success": (
        "后台团队已经做完这一轮，但系统没能再生成一份新的综合说明。下面按已经拿到的结果直接收口。"
    ),
    "failure": (
        "后台团队这一轮已经结束，其中有没做成的部分；"
        "系统没能再生成一份新的综合说明。下面按已经拿到的结果如实交代。"
    ),
    "cancelled": (
        "后台团队这一轮已取消或中断；系统没能再生成一份新的综合说明。"
        "下面按已完成和未完成的部分收口。"
    ),
}

_NODE_STATUS_FACE: dict[str, str] = {
    "completed": "已完成",
    "failed": "没有完成",
    "cancelled": "已取消",
    "skipped": "没有执行",
    "queued": "尚未开始",
    "running": "进行中",
    "retrying": "进行中",
    "pending": "尚未完成",
}

# Display table only — not an intent classifier. Unknown names stay generic.
_TOOL_FACE: dict[str, str] = {
    "web_search": "网页搜索",
    "read_url": "打开网页",
    "download_url": "下载文件",
    "file_write": "写入文件",
    "file_read": "读取文件",
    "file_edit": "编辑文件",
    "str_replace": "改写文件",
    "write_section": "改写文件",
    "code_execute": "运行代码",
    "terminal": "运行命令",
    "host": "本机 Host",
    "host_shell": "运行本机命令",
    "test_run": "运行测试",
    "browser": "浏览网页",
    "browser_navigate": "浏览网页",
    "git": "版本管理",
    "md_to_docx": "导出 Word",
    "md_to_pdf": "导出 PDF",
}

_CHANNEL_DEAD_BODY_MARKERS = (
    "channel dead",
    "活性挂起",
    "本地工作区文件通道已挂起",
    "写盘通道不可用",
    "本地文件暂时连不上",
)


class HarvestDeferredError(Exception):
    """Conversation slot occupied — keep registry; caller must retry, not unregister."""

    def __init__(self, conversation_id: str, execution_id: str) -> None:
        self.conversation_id = conversation_id
        self.execution_id = execution_id
        super().__init__(f"harvest deferred: live turn on {conversation_id}")


class HarvestNotReadyError(Exception):
    """Local harvest missing user / root / outbox / sidecar — keep registry, not success."""

    def __init__(self, conversation_id: str, execution_id: str, reason: str) -> None:
        self.conversation_id = conversation_id
        self.execution_id = execution_id
        self.reason = reason
        super().__init__(f"harvest not ready ({reason}): {conversation_id}")


def harvest_closing_kind(session: CoordinationSession) -> HarvestKind:
    """Classify harvest outcome for synthetic user text (success / failure / cancelled)."""
    from agentcore.runtime.coordination.session import CoordinationEventKind

    if session.soft_stop:
        return "cancelled"
    if any(
        ev.kind is CoordinationEventKind.DRIVE_CANCELLED for ev in _iter_terminal_events(session)
    ):
        return "cancelled"
    if session.failed_run_ids:
        return "failure"
    cancelled = (session.cancel_ids & session.completed_run_ids) - session.failed_run_ids
    if cancelled:
        return "cancelled"
    return "success"


def format_harvest_user_text(session: CoordinationSession) -> str:
    """Synthetic harvest user text; attach draft / 团队成品 when present.

    When 团队成品 is inlined here, stamp ``session.harvest_user_embedded_output``
    so the harvest-closing coordination inject can skip the same blob.
    """
    if session.soft_stop:
        base = _HARVEST_SOFT_STOP_USER_TEXT
    else:
        base = _HARVEST_USER_TEXT[harvest_closing_kind(session)]
    extras: list[str] = []
    draft = (getattr(session, "draft", None) or "").strip()
    if draft:
        extras.append(f"当前合成草稿：\n{draft}")
    output = _all_completed_terminal_output(session)
    if output and output != draft:
        extras.append(f"团队成品：\n{output}")
        session.harvest_user_embedded_output = output
    if extras:
        return base + "\n\n" + "\n\n".join(extras)
    return base


def _iter_terminal_events(session: CoordinationSession) -> list[Any]:
    pending = list(getattr(session, "_pending", []) or [])
    stash = list(getattr(session, "_harvest_stash", []) or [])
    return pending + stash


def _all_completed_terminal_output(session: CoordinationSession) -> str:
    """Pull ``ALL_COMPLETED.output`` from pending events and harvest stash."""
    from agentcore.runtime.coordination.session import CoordinationEventKind

    chunks: list[str] = []
    for ev in _iter_terminal_events(session):
        if getattr(ev, "kind", None) is not CoordinationEventKind.ALL_COMPLETED:
            continue
        out = (getattr(ev, "payload", None) or {}).get("output")
        if isinstance(out, str) and out.strip():
            chunks.append(out.strip())
    return "\n\n".join(chunks)


def _tool_face(tool_name: str) -> str:
    name = (tool_name or "").strip()
    mapped = _TOOL_FACE.get(name)
    if mapped:
        return mapped
    from agentcore.runtime.browser.call_identity import browser_tool_face

    return browser_tool_face(name) or "有一步操作"


def _node_status_face(status: str) -> str:
    key = (status or "").strip().lower()
    return _NODE_STATUS_FACE.get(key, "尚未完成")


def _payload_user_facts(session: CoordinationSession) -> dict[str, Any] | None:
    raw = session.harvest_user_facts
    has_posted = isinstance(raw, dict) and (
        raw.get("nodes") or raw.get("outstanding_tool_failures")
    )
    if has_posted:
        return raw
    from agentcore.runtime.coordination.session import CoordinationEventKind

    for ev in _iter_terminal_events(session):
        if getattr(ev, "kind", None) is not CoordinationEventKind.ALL_COMPLETED:
            continue
        facts = (getattr(ev, "payload", None) or {}).get("user_facts")
        if isinstance(facts, dict):
            return facts
    return None


def _worker_completed_rows(session: CoordinationSession) -> list[dict[str, Any]]:
    from agentcore.runtime.coordination.session import CoordinationEventKind

    rows: list[dict[str, Any]] = []
    for ev in _iter_terminal_events(session):
        if getattr(ev, "kind", None) is not CoordinationEventKind.WORKER_COMPLETED:
            continue
        payload = getattr(ev, "payload", None) or {}
        role = str(payload.get("role") or "").strip() or "队员"
        status = str(payload.get("status") or "completed").strip() or "completed"
        summary = str(payload.get("summary") or "").strip()
        rows.append({"role": role, "status": status, "summary": summary, "files": []})
    return rows


def _facts_from_session_leftovers(session: CoordinationSession) -> dict[str, Any]:
    """Assemble user facts from live_plan / completion sets / worker events."""
    nodes: list[dict[str, Any]] = []
    files: list[str] = []
    seen_files: set[str] = set()
    own = getattr(session, "file_ownership", None)
    live = getattr(session, "live_plan", None)
    completed = set(getattr(session, "completed_run_ids", ()) or ())
    failed = set(getattr(session, "failed_run_ids", ()) or ())
    cancelled = set(getattr(session, "cancel_ids", ()) or ())
    vacated = set(getattr(session, "vacated_run_ids", ()) or ())

    def _owned(run_id: str) -> list[str]:
        if own is None or not hasattr(own, "owned_paths"):
            return []
        return [p for p in own.owned_paths(run_id) if p]

    if live is not None:
        for node in getattr(live, "nodes", ()) or ():
            rid = str(getattr(node, "run_id", "") or "")
            role = str(getattr(node, "role", None) or getattr(node, "agent_name", None) or "队员")
            if rid in failed:
                status = "failed"
            elif rid in cancelled:
                status = "cancelled"
            elif rid in vacated:
                status = "skipped"
            elif rid in completed:
                status = "completed"
            else:
                status = "pending"
            node_files = _owned(rid)
            for path in node_files:
                if path not in seen_files:
                    seen_files.add(path)
                    files.append(path)
            nodes.append({"role": role, "status": status, "summary": "", "files": node_files})
    by_role = {str(row["role"]): i for i, row in enumerate(nodes)}
    for row in _worker_completed_rows(session):
        idx = by_role.get(str(row["role"]))
        if idx is None:
            nodes.append(row)
            by_role[str(row["role"])] = len(nodes) - 1
            continue
        existing = nodes[idx]
        existing["status"] = row["status"] or existing["status"]
        if row["summary"]:
            existing["summary"] = row["summary"]
    return {
        "nodes": nodes,
        "files": files,
        "outstanding_tool_failures": [],
    }


def _session_user_facts(session: CoordinationSession) -> dict[str, Any]:
    posted = _payload_user_facts(session)
    if posted is not None:
        return posted
    return _facts_from_session_leftovers(session)


def _render_user_harvest_body(session: CoordinationSession, kind: HarvestKind) -> str:
    """User-audience close from session facts. Never copies ``format_for_ceo``."""
    parts: list[str] = [_HARVEST_USER_LEAD[kind]]
    draft = (getattr(session, "draft", None) or "").strip()
    if draft:
        parts.append(draft)
    facts = _session_user_facts(session)
    files = [str(p).strip() for p in (facts.get("files") or []) if str(p).strip()]
    if files:
        parts.append("已有这些文件：\n" + "\n".join(f"- {path}" for path in files))
    node_lines: list[str] = []
    unfinished: list[str] = []
    for raw in facts.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "").strip() or "队员"
        status = str(raw.get("status") or "").strip()
        summary = str(raw.get("summary") or "").strip()
        face = _node_status_face(status)
        line = f"- {role}：{face}"
        if summary:
            line += f" — {summary}"
        node_lines.append(line)
        if status and status != "completed":
            unfinished.append(f"{role}（{face}）")
    if node_lines:
        parts.append("各成员这一轮的结果：\n" + "\n".join(node_lines))
    if unfinished:
        parts.append("还没做成的部分：" + "、".join(unfinished) + "。")
    failure_lines: list[str] = []
    for raw in facts.get("outstanding_tool_failures") or []:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "").strip() or "队员"
        step = _tool_face(str(raw.get("tool_name") or ""))
        failure_lines.append(f"- {role}的{step}没有成功")
    if failure_lines:
        parts.append("有些步骤没做成：\n" + "\n".join(failure_lines))
    return "\n\n".join(parts)


def _session_saw_channel_dead(session: CoordinationSession, body: str) -> bool:
    if getattr(session, "workspace_channel_dead", False):
        return True
    text = (body or "").lower()
    return any(m.lower() in text for m in _CHANNEL_DEAD_BODY_MARKERS)


def _is_channel_dead_failure_text(text: str | None) -> bool:
    detail = str(text or "").strip()
    if not detail:
        return False
    if detail == CHANNEL_DEAD_PREPARE_ABORT:
        return True
    return is_channel_dead_detail(detail)


def _exc_is_channel_dead(exc: BaseException) -> bool:
    if isinstance(exc, WorkspaceIOError) and _is_channel_dead_failure_text(str(exc)):
        return True
    return _is_channel_dead_failure_text(str(exc))


def _result_is_channel_dead_abort(result: dict[str, Any] | None) -> bool:
    """True when a salvaged harvest turn failed because the workspace channel is dead."""
    if not isinstance(result, dict):
        return False
    err = result.get("error")
    if isinstance(err, dict):
        return _is_channel_dead_failure_text(str(err.get("message") or err.get("detail") or ""))
    return _is_channel_dead_failure_text(err if isinstance(err, str) else None)


def build_harvest_fallback_content(
    session: CoordinationSession,
    *,
    kind: HarvestKind,
    error_message: str = "",
) -> str:
    """Assemble a no-LLM user-visible closing from session facts (A1/A2).

    Does not copy ``format_for_ceo`` / ALL_COMPLETED.output — that text is for
    the CEO model. Draft (CEO ``update_synthesis``) is already user-intended.
    """
    draft = (getattr(session, "draft", None) or "").strip()
    parts: list[str] = []
    if _session_saw_channel_dead(session, draft):
        parts.append(CHANNEL_DEAD_USER_VISIBLE)
    if getattr(session, "exec_env_dead", False) or (
        draft
        and (
            EXEC_ENV_DEAD_BODY_MARKER in draft
            or EXEC_ENV_CLOUD_SANDBOX_DEAD_BODY_MARKER in draft
        )
    ):
        # Same classified cause the live notice gave (None → cause-free fallback).
        # A draft that already said 云端隔离执行 must not fall back to the
        # local-machine opening.
        reason = getattr(session, "exec_env_dead_reason", None)
        if not reason and draft and EXEC_ENV_CLOUD_SANDBOX_DEAD_BODY_MARKER in draft:
            reason = "exec_env_sandbox_unavailable"
        parts.append(exec_env_dead_user_visible(reason))
    parts.append(_render_user_harvest_body(session, kind))
    err = (error_message or "").strip()
    if err:
        parts.append(f"（系统说明：{err}）")
    return "\n\n".join(parts)


async def persist_harvest_fallback(
    *,
    db: Any,
    conversation_id: str,
    execution_id: str,
    user_id: str,
    session: CoordinationSession,
    kind: HarvestKind,
    error_message: str = "",
    target_message_id: str | None = None,
) -> str:
    """Persist structured fallback assistant row + best-effort push. Returns content.

    ``target_message_id`` (崩溃重驱恢复) closes the ORIGINAL turn in place instead of
    appending a row: the same no-LLM body lands on that message with a terminal
    status, so the recovered bubble stops spinning and no stray message appears.
    """
    from agentcore.core.message_merge import MESSAGE_STATUS_COMPLETE
    from agentcore.db.repositories import MessageRepository

    content = build_harvest_fallback_content(session, kind=kind, error_message=error_message)
    metadata = {
        "origin": "execution_harvest_fallback",
        "execution_id": execution_id,
        "harvest_kind": kind,
        "no_llm": True,
        "channel_dead": _session_saw_channel_dead(session, content),
    }
    repo = MessageRepository(db)
    if target_message_id:
        await repo.upsert_assistant(
            conversation_id=conversation_id,
            message_id=target_message_id,
            content=content,
            metadata={**metadata, "status": MESSAGE_STATUS_COMPLETE, "paused": False},
            merge=True,
        )
    else:
        await repo.create(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            metadata=metadata,
        )
    logger.info(
        "coordination.harvest_fallback_persisted",
        conversation_id=conversation_id,
        execution_id=execution_id,
        harvest_kind=kind,
        content_chars=len(content),
        channel_dead=_session_saw_channel_dead(session, content),
        target_message_id=target_message_id or "",
    )
    with contextlib.suppress(Exception):
        await notify_user(
            user_id,
            PushNotification(
                title="团队任务已收口（未重新调用模型）",
                body="已将已有综合/终端产出推送到对话；打开查看。",
                data={
                    "conversation_id": conversation_id,
                    "execution_id": execution_id,
                    "origin": "execution_harvest_fallback",
                    "harvest_kind": kind,
                },
            ),
        )
    return content


async def _persist_harvest_fallback_local(
    sidecar: Any,
    *,
    conversation_id: str,
    execution_id: str,
    user_id: str,
    session: CoordinationSession,
    kind: HarvestKind,
    error_message: str = "",
    user_message: str,
    user_message_id: str,
    message_id: str,
    trace_id: str,
) -> None:
    """No-LLM harvest close via outbox (never MessageRepository / local PG)."""
    content = build_harvest_fallback_content(session, kind=kind, error_message=error_message)
    outbox = sidecar._outbox_store
    if outbox is None:
        logger.warning(
            "coordination.harvest_not_ready",
            conversation_id=conversation_id,
            execution_id=execution_id,
            reason="no_outbox",
        )
        raise HarvestNotReadyError(conversation_id, execution_id, "no_outbox")
    await sidecar._outbox_finalize(
        outbox,
        conversation_id=conversation_id,
        user_message=user_message,
        user_message_id=user_message_id,
        trace_id=trace_id,
        result={
            "message_id": message_id,
            "content": content,
            "journal_entries": [],
        },
        origin="execution_harvest",
        execution_id=execution_id,
        harvest_kind=kind,
    )
    logger.info(
        "coordination.harvest_fallback_persisted",
        conversation_id=conversation_id,
        execution_id=execution_id,
        harvest_kind=kind,
        content_chars=len(content),
        channel_dead=_session_saw_channel_dead(session, content),
        via="sidecar",
    )
    await _notify_harvest_complete(
        user_id=user_id,
        conversation_id=conversation_id,
        execution_id=execution_id,
        kind=kind,
    )


async def _run_local_harvest_closing_turn(
    sidecar: Any,
    *,
    session: CoordinationSession,
    conversation_id: str,
    execution_id: str,
) -> None:
    """Sidecar harvest: bind_turn → ``_pump`` → pipeline → OutboxStore.finalize(local).

    Ordinary local harvest always opens a new turn. D5 ``recovered_turn_id``
    continues the original *cloud* bubble and must not be reused here — this
    path must not pretend a local pump is that continuation.
    """
    import asyncio

    from agentcore.account.credentials import account_credentials_scope
    from agentcore.core.log_context import log_context
    from agentcore.core.types import new_id
    from agentcore.folders.credentials import folders_credentials_scope
    from agentcore.runtime.delegate.post_close_gate import (
        EXECUTION_HARVEST_ORIGIN,
        bind_user_message_origin,
        reset_user_message_origin,
    )
    from agentcore.sidecar import server as sidecar_server
    from agentcore.sidecar.server_pkg.turns import _inference_search_creds
    from agentcore.tools.builtin.web.cloud_fallback import (
        inference_search_credentials_scope,
    )

    recovered = (getattr(session, "recovered_turn_id", "") or "").strip()
    kind = harvest_closing_kind(session)
    user_text = format_harvest_user_text(session)
    user_id = str(sidecar._user_id or "").strip()
    if not user_id:
        logger.warning(
            "coordination.harvest_not_ready",
            conversation_id=conversation_id,
            execution_id=execution_id,
            reason="no_user",
            via="sidecar",
        )
        raise HarvestNotReadyError(conversation_id, execution_id, "no_user")
    if sidecar._root is None:
        logger.warning(
            "coordination.harvest_not_ready",
            conversation_id=conversation_id,
            execution_id=execution_id,
            reason="no_workspace_root",
        )
        raise HarvestNotReadyError(conversation_id, execution_id, "no_workspace_root")

    scope = sidecar.folder_scope_for(conversation_id)
    folder_id = session.birth_desk_id or (scope.folder_id if scope else None)
    binding_injected = bool(session.folder_binding_injected) or (
        scope.binding_injected if scope is not None else False
    )
    folder_local_root_id = session.folder_local_root_id
    if folder_local_root_id is None and scope is not None:
        folder_local_root_id = scope.local_root_id
    folder_local_subpath = session.folder_local_subpath or (
        scope.local_subpath if scope is not None else ""
    )

    user_message_id = new_id()
    message_id = new_id()
    trace_id = await _resolve_harvest_trace_id(session, conversation_id)
    turn_creds = sidecar._creds_for(conversation_id, trace_id, message_id)
    outbox = sidecar._outbox_store
    if outbox is None:
        logger.warning(
            "coordination.harvest_not_ready",
            conversation_id=conversation_id,
            execution_id=execution_id,
            reason="no_outbox",
        )
        raise HarvestNotReadyError(conversation_id, execution_id, "no_outbox")
    outbox.bind_turn(
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        user_message=user_text,
        message_id=message_id,
        trace_id=trace_id,
        origin="execution_harvest",
        execution_id=execution_id,
        harvest_kind=kind,
    )
    await outbox.begin_turn(
        conversation_id=conversation_id,
        message_id=message_id,
        trace_id=trace_id,
    )

    async def _finalize(result: dict[str, Any]) -> None:
        if outbox is None:
            return
        await sidecar._outbox_finalize(
            outbox,
            conversation_id=conversation_id,
            user_message=user_text,
            user_message_id=user_message_id,
            trace_id=trace_id,
            result=result,
            origin="execution_harvest",
            execution_id=execution_id,
            harvest_kind=kind,
        )

    if turn_creds is None or getattr(session, "workspace_channel_dead", False):
        err = "" if turn_creds is not None else "系统收口需要可用的模型凭证。"
        if getattr(session, "workspace_channel_dead", False):
            logger.warning(
                "coordination.harvest_channel_dead_skip_llm",
                conversation_id=conversation_id,
                execution_id=execution_id,
                via="sidecar",
            )
            err = CHANNEL_DEAD_PREPARE_ABORT
        else:
            logger.warning(
                "coordination.harvest_credentials_unavailable",
                conversation_id=conversation_id,
                execution_id=execution_id,
                via="sidecar",
            )
        with contextlib.suppress(Exception):
            await _persist_harvest_fallback_local(
                sidecar,
                conversation_id=conversation_id,
                execution_id=execution_id,
                user_id=user_id,
                session=session,
                kind=kind,
                error_message=err,
                user_message=user_text,
                user_message_id=user_message_id,
                message_id=message_id,
                trace_id=trace_id,
            )
        if outbox is not None:
            outbox.clear_turn(message_id)
        logger.info(
            "coordination.harvest_closing_turn_done",
            conversation_id=conversation_id,
            execution_id=execution_id,
            harvest_kind=kind,
            ignored_recovered_turn_id=recovered,
            via="sidecar",
        )
        return

    sink = EventSink()
    if outbox is not None:
        sink.bind_content_checkpoint(
            conversation_id=conversation_id,
            message_id=message_id,
        )
    backend = sidecar._make_backend()
    saver, deleter = sidecar._suspension_hooks()
    session_saver, session_loader = sidecar._session_hooks(conversation_id)

    async def _run() -> None:
        from agentcore.runtime.coordination.session import adopt_active_execution

        adopt_active_execution(conversation_id, event_sink=sink, reopen_harvest=True)
        origin_token = bind_user_message_origin(EXECUTION_HARVEST_ORIGIN)
        result: dict[str, Any] | None = None
        pump = asyncio.create_task(sidecar._pump(message_id, sink, conversation_id=conversation_id))
        try:
            try:
                with (
                    log_context(
                        trace_id=trace_id,
                        conversation_id=conversation_id,
                        user_id=user_id,
                        message_id=message_id,
                    ),
                    inference_search_credentials_scope(_inference_search_creds(turn_creds)),
                    folders_credentials_scope(sidecar._folders_creds),
                    account_credentials_scope(sidecar._account_creds),
                ):
                    from agentcore.sidecar.chat_history import (
                        ChatContextUnavailableError,
                        resolve_sidecar_turn_history,
                    )

                    try:
                        history = await resolve_sidecar_turn_history(
                            conversation_id,
                            creds=sidecar._account_creds,
                            fallback=sidecar.stamped_history(conversation_id),
                        )
                    except ChatContextUnavailableError as exc:
                        logger.warning(
                            "coordination.harvest_chat_context_unavailable",
                            conversation_id=conversation_id,
                            execution_id=execution_id,
                            error=exc.message,
                            via="sidecar",
                        )
                        with contextlib.suppress(Exception):
                            await _persist_harvest_fallback_local(
                                sidecar,
                                conversation_id=conversation_id,
                                execution_id=execution_id,
                                user_id=user_id,
                                session=session,
                                kind=kind,
                                error_message=exc.message,
                                user_message=user_text,
                                user_message_id=user_message_id,
                                message_id=message_id,
                                trace_id=trace_id,
                            )
                        return
                    sidecar.stamp_turn_history(conversation_id, history)
                    result = await sidecar_server.run_chat_pipeline(
                        conversation_id=conversation_id,
                        user_message=user_text,
                        history=history,
                        sink=sink,
                        user_id=user_id,
                        backend=backend,
                        folder_id=folder_id,
                        folder_binding_injected=binding_injected,
                        folder_local_root_id=folder_local_root_id,
                        folder_local_subpath=folder_local_subpath,
                        approvals_enabled=sidecar._approvals_enabled,
                        permission_axes=sidecar.permission_axes_for(conversation_id),
                        llm_credentials=turn_creds,
                        session_saver=session_saver,
                        session_loader=session_loader,
                        suspension_saver=saver,
                        suspension_deleter=deleter,
                        message_id=message_id,
                        x_client_platform="desktop",
                    )
            except Exception as e:
                if not _exc_is_channel_dead(e):
                    raise
                session.workspace_channel_dead = True
                logger.warning(
                    "coordination.harvest_channel_dead_after_turn",
                    conversation_id=conversation_id,
                    execution_id=execution_id,
                    error=str(e),
                    via="sidecar_exception",
                )
                with contextlib.suppress(Exception):
                    await _persist_harvest_fallback_local(
                        sidecar,
                        conversation_id=conversation_id,
                        execution_id=execution_id,
                        user_id=user_id,
                        session=session,
                        kind=kind,
                        error_message=str(e) or CHANNEL_DEAD_PREPARE_ABORT,
                        user_message=user_text,
                        user_message_id=user_message_id,
                        message_id=message_id,
                        trace_id=trace_id,
                    )
                return
            if _result_is_channel_dead_abort(result):
                session.workspace_channel_dead = True
                err_text = ""
                raw_err = result.get("error") if isinstance(result, dict) else None
                if isinstance(raw_err, dict):
                    err_text = str(raw_err.get("message") or raw_err.get("detail") or "")
                elif raw_err is not None:
                    err_text = str(raw_err)
                logger.warning(
                    "coordination.harvest_channel_dead_after_turn",
                    conversation_id=conversation_id,
                    execution_id=execution_id,
                    error=err_text or CHANNEL_DEAD_PREPARE_ABORT,
                    via="sidecar_salvaged_result",
                )
                with contextlib.suppress(Exception):
                    await _persist_harvest_fallback_local(
                        sidecar,
                        conversation_id=conversation_id,
                        execution_id=execution_id,
                        user_id=user_id,
                        session=session,
                        kind=kind,
                        error_message=err_text or CHANNEL_DEAD_PREPARE_ABORT,
                        user_message=user_text,
                        user_message_id=user_message_id,
                        message_id=message_id,
                        trace_id=trace_id,
                    )
                return
            await _finalize(result or {"message_id": message_id, "content": ""})
            await _notify_harvest_complete(
                user_id=user_id,
                conversation_id=conversation_id,
                execution_id=execution_id,
                kind=kind,
            )
        except asyncio.CancelledError:
            # Symmetric with ordinary ``_run_turn``: compose via outbox.salvage
            # before the OPEN row can be left for desktop salvageOpen (empty).
            from agentcore.sidecar.server_pkg.turns import _salvage_interrupt_reason

            journal = list(sink.execution_journal() or [])
            content = sink.streamed_content() or ""
            if outbox is not None:
                await outbox.salvage(
                    journal=journal,
                    content=content,
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    message_id=message_id,
                    origin="execution_harvest",
                    execution_id=execution_id,
                    harvest_kind=kind,
                    interrupt_reason=_salvage_interrupt_reason(),
                )
            sidecar._log_turn_cancelled(
                turn_id=message_id,
                conversation_id=conversation_id,
                message_id=message_id,
                trace_id=trace_id,
                content_chars=len(content),
                journal_entries=len(journal),
                salvaged=outbox is not None,
            )
            raise
        finally:
            reset_user_message_origin(origin_token)
            sink.close(reason="sidecar_harvest_finally")
            with contextlib.suppress(Exception):
                await pump
            if outbox is not None:
                outbox.clear_turn(message_id)

    task = asyncio.create_task(
        _run(),
        name=f"harvest-close-{execution_id[:8]}",
    )
    sidecar._register_turn(message_id, task, conversation_id=conversation_id)
    try:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        sidecar._unregister_turn(message_id)
    logger.info(
        "coordination.harvest_closing_turn_done",
        conversation_id=conversation_id,
        execution_id=execution_id,
        harvest_kind=kind,
        ignored_recovered_turn_id=recovered,
        via="sidecar",
    )


_SETTLED_HARVEST_STATUSES = frozenset(
    {
        MESSAGE_STATUS_COMPLETE,
        MESSAGE_STATUS_FAILED,
        MESSAGE_STATUS_INCOMPLETE,
    }
)


async def _existing_harvest_claim_action(
    db: Any,
    *,
    conversation_id: str,
    execution_id: str,
) -> tuple[Literal["skip", "continue"], str | None]:
    """Decide what a unique-index claim means.

    The user row is only a claim. Skip when a closing assistant already
    settled, or when a fresh turn lease says another process is still in
    the CEO turn. Otherwise continue (optionally resuming a zombie assistant).
    """
    from agentcore.db.repositories import MessageRepository
    from agentcore.runtime.leases.repo import TurnLeaseRepository

    repo = MessageRepository(db)
    claimed = await repo.get_execution_harvest_user(
        conversation_id=conversation_id,
        execution_id=execution_id,
    )
    fresh_after = datetime.now(UTC) - timedelta(seconds=settings.turn_lease_ttl_seconds)
    if await TurnLeaseRepository(db).exists_fresh_for_conversation(
        conversation_id, after=fresh_after
    ):
        return "skip", None
    if claimed is None:
        return "continue", None
    following = await repo.get_first_assistant_after(
        conversation_id=conversation_id,
        after=claimed.created_at,
        after_id=claimed.id,
    )
    if following is None:
        return "continue", None
    usage = following.usage if isinstance(following.usage, dict) else {}
    status = usage.get("status")
    if status in _SETTLED_HARVEST_STATUSES:
        return "skip", None
    if status == MESSAGE_STATUS_RUNNING:
        return "continue", following.id
    return "continue", None


async def run_harvest_closing_turn(
    *,
    conversation_id: str,
    execution_id: str,
) -> None:
    """Adopt the live execution and run a system closing CEO turn.

    Ordinary detached drives get a fresh turn (synthetic user row + new assistant
    reply). A crash-redriven drive (``session.recovered_turn_id``) instead CONTINUES
    the interrupted turn: no synthetic user row, and the synthesis is written back
    onto that assistant message under its own journal.

    Sidecar (``get_active_sidecar()``) never opens local PG: bind_turn → pipeline →
    ``OutboxStore.finalize(local)``. D5 ``recovered_turn_id`` is cloud-only and is
    ignored on the local path.

    Raises:
        HarvestDeferredError: another turn owns the conversation slot — do **not**
            treat as success or clear the coordination registry.
    """
    from agentcore.runtime.coordination.session import (
        active_coordination,
        adopt_active_execution,
    )

    session = active_coordination(execution_id)
    if session is None:
        logger.info(
            "coordination.harvest_no_session",
            conversation_id=conversation_id,
            execution_id=execution_id,
        )
        return
    if session.turn_attached:
        logger.info(
            "coordination.harvest_skipped_reattached",
            conversation_id=conversation_id,
            execution_id=execution_id,
        )
        return
    if getattr(session, "host_turn_paused", False):
        logger.info(
            "coordination.harvest_skipped_host_paused",
            conversation_id=conversation_id,
            execution_id=execution_id,
        )
        return
    from agentcore.runtime.turn.ceo_continue import host_turn_is_ceo_paused

    if await host_turn_is_ceo_paused(conversation_id, session.host_turn_id):
        logger.info(
            "coordination.harvest_skipped_host_paused",
            conversation_id=conversation_id,
            execution_id=execution_id,
            via="persisted",
        )
        return

    # Another turn already owns the conversation slot — keep registry; retry later.
    existing = turn_runs.get(conversation_id)
    if existing is not None and not existing.task.done():
        logger.info(
            "coordination.harvest_deferred_live_turn",
            conversation_id=conversation_id,
            execution_id=execution_id,
        )
        raise HarvestDeferredError(conversation_id, execution_id)

    from agentcore.sidecar.server_pkg.core import get_active_sidecar, is_sidecar_process

    sidecar = get_active_sidecar()
    if sidecar is not None:
        live = sidecar.live_turn_task(conversation_id)
        if live is not None and not live.done():
            logger.info(
                "coordination.harvest_deferred_live_turn",
                conversation_id=conversation_id,
                execution_id=execution_id,
                via="sidecar",
            )
            raise HarvestDeferredError(conversation_id, execution_id)
        await _run_local_harvest_closing_turn(
            sidecar,
            session=session,
            conversation_id=conversation_id,
            execution_id=execution_id,
        )
        return
    if is_sidecar_process():
        logger.warning(
            "coordination.harvest_not_ready",
            conversation_id=conversation_id,
            execution_id=execution_id,
            reason="sidecar_unavailable",
        )
        raise HarvestNotReadyError(conversation_id, execution_id, "sidecar_unavailable")

    kind = harvest_closing_kind(session)
    user_text = format_harvest_user_text(session)
    # 崩溃重驱恢复归属原回合 (D5)：这次收口是那条消息的续写，不是新回合。
    recovered_turn_id = (getattr(session, "recovered_turn_id", "") or "").strip()

    async with async_session_factory() as db:
        conv = await ConversationRepository(db).get_by_id_unscoped(conversation_id)
        if not conv:
            logger.warning(
                "coordination.harvest_conversation_missing",
                conversation_id=conversation_id,
                execution_id=execution_id,
            )
            return
        user_id = str(conv.user_id)
        folder_id = conv.folder_id
        user = await UserRepository(db).get_by_id(user_id)
        if user is None:
            logger.warning(
                "coordination.harvest_user_missing",
                conversation_id=conversation_id,
                execution_id=execution_id,
                user_id=user_id,
            )
            return
        try:
            selection = await resolve_conversation_model_selection(db, conv, user_id)
            llm_credentials: LLMCredentials | None = await preflight_llm_credentials(
                session=db,
                user=user,
                cost_repo=CostEventRepository(db),
                byok_missing_message=(
                    "系统收口需要可用的模型凭证，请先填入 API Key。"
                ),
                model_origin=selection.origin,
                provider_id=selection.provider_id,
            )
            if selection.origin == "platform":
                llm_credentials = platform_llm_credentials(model=selection.model)
        except AgentCoreError as e:
            logger.warning(
                "coordination.harvest_credentials_unavailable",
                conversation_id=conversation_id,
                execution_id=execution_id,
                error=e.message or str(e),
                code=getattr(e, "code", None),
            )
            # A1: push existing synthesis/terminal without another LLM call.
            with contextlib.suppress(Exception):
                await persist_harvest_fallback(
                    db=db,
                    conversation_id=conversation_id,
                    execution_id=execution_id,
                    user_id=user_id,
                    session=session,
                    kind=kind,
                    error_message=e.message or str(e),
                    target_message_id=recovered_turn_id or None,
                )
            return
        # Channel already sticky-dead from the team wave: skip prepare/LLM and
        # deliver the same no-LLM fallback (avoid STREAM_ERROR empty shell).
        if getattr(session, "workspace_channel_dead", False):
            logger.warning(
                "coordination.harvest_channel_dead_skip_llm",
                conversation_id=conversation_id,
                execution_id=execution_id,
            )
            with contextlib.suppress(Exception):
                await persist_harvest_fallback(
                    db=db,
                    conversation_id=conversation_id,
                    execution_id=execution_id,
                    user_id=user_id,
                    session=session,
                    kind=kind,
                    error_message=CHANNEL_DEAD_PREPARE_ABORT,
                    target_message_id=recovered_turn_id or None,
                )
            return
        local_binding = await resolve_local_binding(db, conv)
        profile_set = await resolve_profile_set(db, conv, user_id)
        permission_axes = await resolve_permission_axes(db, conversation_id)

        board = await BoardRepository(db).get_by_conversation_id(conversation_id, user_id=user_id)
        board_id = board.id if board else None
        from agentcore.db.repositories import MessageRepository, TurnJournalRepository

        inherited_entries: list[dict] | None = None
        if recovered_turn_id:
            # Continuation: the interrupted turn's facts are the journal prefix this
            # segment appends to, so finalize re-persists ONE stream (the recovery
            # facts recover.py already wrote stay put) instead of overwriting at seq 0.
            inherited_entries = await TurnJournalRepository(db).load(recovered_turn_id)
        else:
            try:
                await MessageRepository(db).create(
                    conversation_id=conversation_id,
                    role="user",
                    content=user_text,
                    metadata={
                        "origin": "execution_harvest",
                        "execution_id": execution_id,
                        "harvest_kind": kind,
                    },
                )
            except IntegrityError as exc:
                await db.rollback()
                if not is_execution_harvest_conflict(exc):
                    raise
                action, resume_id = await _existing_harvest_claim_action(
                    db,
                    conversation_id=conversation_id,
                    execution_id=execution_id,
                )
                if action == "skip":
                    logger.info(
                        "coordination.harvest_idempotent_skip",
                        conversation_id=conversation_id,
                        execution_id=execution_id,
                    )
                    return
                if resume_id:
                    recovered_turn_id = resume_id
                    inherited_entries = await TurnJournalRepository(db).load(
                        recovered_turn_id
                    )
                else:
                    logger.info(
                        "coordination.harvest_claim_continue",
                        conversation_id=conversation_id,
                        execution_id=execution_id,
                    )
        history = await load_chat_context(db, conversation_id)
        # The synthetic user row is passed to the pipeline as ``user_message``, so drop
        # it from the window tail. Nothing was appended on the continuation path.
        if not recovered_turn_id:
            history = history[:-1] if history else []
        parent_trace_id = await _resolve_harvest_trace_id(
            session,
            conversation_id,
            recovered_turn_id=recovered_turn_id or None,
            db=db,
        )

    sink = EventSink()
    backend = await build_turn_backend(
        user_id=user_id,
        conversation_id=conversation_id,
        folder_id=folder_id,
        sink=sink,
        local_binding=local_binding,
    )

    async def _run() -> None:
        from agentcore.core.log_context import log_context
        from agentcore.runtime.delegate.post_close_gate import (
            EXECUTION_HARVEST_ORIGIN,
            bind_user_message_origin,
            reset_user_message_origin,
        )

        # Adopt before pipeline so CEO wait binds the live execution_id.
        adopt_active_execution(conversation_id, event_sink=sink, reopen_harvest=True)
        origin_token = bind_user_message_origin(EXECUTION_HARVEST_ORIGIN)
        try:
            with log_context(
                trace_id=parent_trace_id,
                conversation_id=conversation_id,
                user_id=user_id,
            ):
                try:
                    result = await run_and_persist(
                        conversation_id=conversation_id,
                        user_message=user_text,
                        user_id=user_id,
                        folder_id=folder_id,
                        sink=sink,
                        history=history,
                        attachments=None,
                        backend=backend,
                        llm_credentials=llm_credentials,
                        profile_set=profile_set,
                        permission_axes=permission_axes,
                        board_id=board_id,
                        llm_supports_tools=None,
                        x_client_platform=None,
                        continue_message_id=recovered_turn_id or None,
                        inherited_journal_entries=inherited_entries,
                    )
                except Exception as e:
                    if not _exc_is_channel_dead(e):
                        raise
                    session.workspace_channel_dead = True
                    logger.warning(
                        "coordination.harvest_channel_dead_after_turn",
                        conversation_id=conversation_id,
                        execution_id=execution_id,
                        error=str(e),
                        via="exception",
                    )
                    async with async_session_factory() as fb_db:
                        with contextlib.suppress(Exception):
                            await persist_harvest_fallback(
                                db=fb_db,
                                conversation_id=conversation_id,
                                execution_id=execution_id,
                                user_id=user_id,
                                session=session,
                                kind=kind,
                                error_message=str(e) or CHANNEL_DEAD_PREPARE_ABORT,
                                target_message_id=recovered_turn_id or None,
                            )
                    return
                if _result_is_channel_dead_abort(result):
                    session.workspace_channel_dead = True
                    err_text = ""
                    raw_err = result.get("error") if isinstance(result, dict) else None
                    if isinstance(raw_err, dict):
                        err_text = str(raw_err.get("message") or raw_err.get("detail") or "")
                    elif raw_err is not None:
                        err_text = str(raw_err)
                    logger.warning(
                        "coordination.harvest_channel_dead_after_turn",
                        conversation_id=conversation_id,
                        execution_id=execution_id,
                        error=err_text or CHANNEL_DEAD_PREPARE_ABORT,
                        via="salvaged_result",
                    )
                    async with async_session_factory() as fb_db:
                        with contextlib.suppress(Exception):
                            await persist_harvest_fallback(
                                db=fb_db,
                                conversation_id=conversation_id,
                                execution_id=execution_id,
                                user_id=user_id,
                                session=session,
                                kind=kind,
                                error_message=err_text or CHANNEL_DEAD_PREPARE_ABORT,
                                target_message_id=recovered_turn_id or None,
                            )
                    return
        finally:
            reset_user_message_origin(origin_token)
        await _notify_harvest_complete(
            user_id=user_id,
            conversation_id=conversation_id,
            execution_id=execution_id,
            kind=kind,
        )

    import asyncio

    task = asyncio.create_task(
        _run(),
        name=f"harvest-close-{execution_id[:8]}",
    )
    turn_runs.register(conversation_id=conversation_id, task=task, sink=sink, user_id=user_id)
    # Wait for the closing turn so the harvester can clear the registry afterward
    # if the turn never re-attached (edge failure).
    with contextlib.suppress(asyncio.CancelledError):
        await task
    logger.info(
        "coordination.harvest_closing_turn_done",
        conversation_id=conversation_id,
        execution_id=execution_id,
        harvest_kind=kind,
        recovered_turn_id=recovered_turn_id,
    )


async def _notify_harvest_complete(
    *,
    user_id: str,
    conversation_id: str,
    execution_id: str,
    kind: HarvestKind = "success",
) -> None:
    title, body = _HARVEST_PUSH[kind]
    with contextlib.suppress(Exception):
        await notify_user(
            user_id,
            PushNotification(
                title=title,
                body=body,
                data={
                    "conversation_id": conversation_id,
                    "execution_id": execution_id,
                    "origin": "execution_harvest",
                    "harvest_kind": kind,
                },
            ),
        )
