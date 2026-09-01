"""read_conversation — deep-read of one past chat (messages + journal).

``AUDIENCE_BOTH`` + ``manual_wire``; wired via ``_wire_conversation_log_tools``.
Supports cursor continuation so a multi-chunk transcript can be reassembled — never
silently summarised via the default 4k ToolResult head+tail truncate.

With account narrow-ticket creds (sidecar), calls the cloud HTTP API instead of
the local repositories (大众桌面无本机 PG).
"""

from __future__ import annotations

from typing import Any

from agentcore.conversation.log_export import (
    MAX_CHUNK_CHARS,
    chunk_transcript,
    render_conversation_log,
)
from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import (
    ConversationRepository,
    MessageRepository,
    TurnJournalRepository,
)
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    ToolRegistration,
    ToolSurface,
)

logger = get_logger(__name__)

_SOFT_MISS = (
    "无法打开该对话（可能不存在、已删除、为 handoff 宿主，或不在可访问范围内）。"
)
_HOST_MISS = "那是本回合正在进行的宿主会话——请直接看本会话工作记忆，无需 read_conversation。"


def _is_account_cloud_failure(exc: BaseException) -> bool:
    from agentcore.account.credentials import AccountCloudError

    return isinstance(exc, AccountCloudError)


def _ok_result_from_chunk(
    *,
    title: str,
    conversation_id: str,
    transcript: str,
    truncated: bool,
    next_cursor: str | None,
    started_at: str | None,
    ended_at: str | None,
    message_count: int,
    char_offset: int,
    total_chars: int,
    run_id: str | None,
) -> ToolResult:
    header_lines = [
        f"title: {title}",
        f"conversation_id: {conversation_id}",
        f"messages: {message_count}",
        f"time_range: {started_at or '—'} → {ended_at or '—'}",
        f"truncated: {truncated}",
        f"offset: {char_offset}/{total_chars}",
    ]
    if next_cursor:
        header_lines.append(f"next_cursor: {next_cursor}")
    header_lines.append("")
    header_lines.append("--- transcript ---")
    header_lines.append("")
    output = "\n".join(header_lines) + transcript
    output_limit = max(len(output), MAX_CHUNK_CHARS)

    logger.info(
        "conversation_log.read",
        result="ok",
        conversation_id=conversation_id,
        truncated=truncated,
        chars=len(transcript),
        run_id=run_id,
    )
    return ToolResult(
        tool_call_id="",
        success=True,
        output=output,
        output_limit=output_limit,
        display={
            "title": title,
            "conversation_id": conversation_id,
            "truncated": truncated,
            "depth": "full",
        },
        metadata={
            "next_cursor": next_cursor,
            "truncated": truncated,
            "stats": {
                "message_count": message_count,
                "char_offset": char_offset,
                "total_chars": total_chars,
            },
        },
    )


async def _read_via_cloud(
    *,
    conversation_id: str,
    cursor: str | None,
    max_chars: int | None,
) -> dict[str, Any]:
    from agentcore.account.credentials import (
        AccountCloudError,
        cloud_read_conversation,
        get_account_credentials,
    )

    creds = get_account_credentials()
    assert creds is not None
    payload: dict[str, Any] = {
        "conversation_id": conversation_id,
        "cursor": cursor,
        "max_chars": max_chars,
    }
    try:
        data = await cloud_read_conversation(creds, payload=payload)
    except AccountCloudError:
        raise
    except Exception as e:  # noqa: BLE001
        raise AccountCloudError(str(e)) from e
    if not isinstance(data, dict):
        raise AccountCloudError("account read response is not an object")
    return data


class ReadConversationTool:
    """Open one owner-scoped conversation as a deep markdown transcript."""

    registration = ToolRegistration(
        surface=ToolSurface.WORKER_ONLY,
        audience=AUDIENCE_BOTH,
        manual_wire=True,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="read_conversation",
            description=(
                "按 conversation_id 读取一场历史对话的整段原文与过程（用户/助手正文、思考、"
                "工具调用与结果、辩论、证据与引用）。超长时返回 truncated + next_cursor，"
                "请带着 cursor 续读并自行拼回全文——禁止把单次截断当成「摘要版全文」。"
                "读完后蒸馏结论并记下出处 id/标题（默认不要把百万字原文原样塞回）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": "string",
                        "description": "要打开的对话 id（来自 search_conversations）。",
                    },
                    "cursor": {
                        "type": "string",
                        "description": "续读游标；首轮省略 = 从最早消息起。",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": (
                            f"本块最大字符数（可选）；服务端硬顶 {MAX_CHUNK_CHARS}。"
                        ),
                    },
                },
                "required": ["conversation_id"],
            },
            category=ToolCategory.SEARCH,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        cid = str(arguments.get("conversation_id") or "").strip()
        if not cid:
            msg = "缺少 conversation_id 参数。"
            return ToolResult(tool_call_id="", success=False, output=msg, error=msg)

        if context.conversation_id and cid == context.conversation_id:
            logger.info(
                "conversation_log.read",
                result="host_exclude",
                conversation_id=cid,
                run_id=context.run_id,
            )
            return ToolResult(
                tool_call_id="",
                success=True,
                output=_HOST_MISS,
                display={
                    "title": "",
                    "conversation_id": cid,
                    "truncated": False,
                    "depth": "full",
                },
            )

        cursor = arguments.get("cursor")
        cursor_s = str(cursor).strip() if cursor else None
        max_chars: int | None = None
        if arguments.get("max_chars") is not None:
            try:
                max_chars = int(arguments["max_chars"])
            except (TypeError, ValueError):
                max_chars = None

        from agentcore.account.credentials import get_account_credentials

        try:
            if get_account_credentials() is not None:
                data = await _read_via_cloud(
                    conversation_id=cid,
                    cursor=cursor_s,
                    max_chars=max_chars,
                )
                status = str(data.get("status") or "")
                if status == "soft_miss":
                    logger.info(
                        "conversation_log.read",
                        result="soft_miss",
                        conversation_id=cid,
                        run_id=context.run_id,
                    )
                    return ToolResult(
                        tool_call_id="",
                        success=True,
                        output=_SOFT_MISS,
                        display={
                            "title": "",
                            "conversation_id": cid,
                            "truncated": False,
                            "depth": "full",
                        },
                    )
                if status != "ok":
                    raise RuntimeError(f"unexpected account read status: {status}")
                return _ok_result_from_chunk(
                    title=str(data.get("title") or ""),
                    conversation_id=str(data.get("conversation_id") or cid),
                    transcript=str(data.get("transcript") or ""),
                    truncated=bool(data.get("truncated")),
                    next_cursor=(
                        str(data["next_cursor"])
                        if data.get("next_cursor") is not None
                        else None
                    ),
                    started_at=(
                        str(data["started_at"])
                        if data.get("started_at") is not None
                        else None
                    ),
                    ended_at=(
                        str(data["ended_at"]) if data.get("ended_at") is not None else None
                    ),
                    message_count=int(data.get("message_count") or 0),
                    char_offset=int(data.get("char_offset") or 0),
                    total_chars=int(data.get("total_chars") or 0),
                    run_id=context.run_id,
                )

            async with async_session_factory() as session:
                conv = await ConversationRepository(session).get_by_id(
                    cid, user_id=context.user_id
                )
                # Soft miss: wrong owner / soft-deleted / missing / handoff host.
                if conv is None or conv.mode == "handoff":
                    logger.info(
                        "conversation_log.read",
                        result="soft_miss",
                        conversation_id=cid,
                        run_id=context.run_id,
                    )
                    return ToolResult(
                        tool_call_id="",
                        success=True,
                        output=_SOFT_MISS,
                        display={
                            "title": "",
                            "conversation_id": cid,
                            "truncated": False,
                            "depth": "full",
                        },
                    )

                messages = list(
                    await MessageRepository(session).list_all_for_conversation(cid)
                )
                assistant_ids = [m.id for m in messages if m.role == "assistant"]
                journal_map = await TurnJournalRepository(session).load_map(assistant_ids)
                full = render_conversation_log(conv, messages, journal_map)
                chunk = chunk_transcript(
                    full,
                    conversation=conv,
                    messages=messages,
                    cursor=cursor_s,
                    max_chars=max_chars,
                )

            return _ok_result_from_chunk(
                title=chunk.title,
                conversation_id=chunk.conversation_id,
                transcript=chunk.transcript,
                truncated=chunk.truncated,
                next_cursor=chunk.next_cursor,
                started_at=chunk.started_at,
                ended_at=chunk.ended_at,
                message_count=chunk.message_count,
                char_offset=chunk.char_offset,
                total_chars=chunk.total_chars,
                run_id=context.run_id,
            )
        except Exception as e:  # noqa: BLE001
            cloud_fail = _is_account_cloud_failure(e)
            logger.warning(
                "conversation_log.read_failed",
                conversation_id=cid,
                error=str(e),
                account_cloud_failed=cloud_fail,
            )
            if cloud_fail:
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output=f"读取历史对话失败。{e}",
                    error=getattr(e, "code", "account_cloud_failed"),
                )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="读取历史对话失败，请稍后再试。",
                error=str(e),
            )
