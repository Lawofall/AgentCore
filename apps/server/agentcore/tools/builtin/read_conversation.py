"""read_conversation — read one past chat (dialogue by default).

``AUDIENCE_BOTH`` + ``manual_wire``; wired via ``_wire_conversation_log_tools``.
Default ``focus=dialogue`` is user/assistant visible text. ``focus=process``
opts into tools / debate / thinking. Pages are message-index cursors (``m:N``).

With account narrow-ticket creds (sidecar), calls the cloud HTTP API instead of
the local repositories (大众桌面无本机 PG).
"""

from __future__ import annotations

from typing import Any

from agentcore.conversation.log_export import (
    DEFAULT_FOCUS,
    FOCUS_PROCESS,
    MAX_CHUNK_CHARS,
    normalize_focus,
    page_conversation,
)
from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolFace, is_uuid_id
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import (
    ConversationRepository,
    MessageRepository,
    TurnJournalRepository,
)
from agentcore.tools.builtin.search_conversations import (
    _format_search_output,
    run_conversation_search,
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
_MISSING_LOCATOR = "请提供 conversation_id，或用 query 按标题/关键词定位。"
_MULTI_HIT_LEAD = "命中多场，请带 conversation_id 打开其中一场。"


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
    message_offset: int,
    message_end: int,
    focus: str,
    query: str | None,
    query_hit: bool,
    total_chars: int,
    run_id: str | None,
) -> ToolResult:
    if message_end > message_offset:
        shown = f"{message_offset + 1}–{message_end}/{message_count}"
    else:
        shown = f"{message_offset}/{message_count}"
    header_lines = [
        f"title: {title}",
        f"conversation_id: {conversation_id}",
        f"focus: {focus}",
        f"messages: {message_count}",
        f"offset: {shown}",
        f"time_range: {started_at or '—'} → {ended_at or '—'}",
        f"truncated: {truncated}",
    ]
    if query:
        header_lines.append(f"query: {query}")
        header_lines.append(f"matched: {query_hit}")
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
        focus=focus,
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
            "depth": focus,
        },
        metadata={
            "next_cursor": next_cursor,
            "truncated": truncated,
            "focus": focus,
            "stats": {
                "message_count": message_count,
                "message_offset": message_offset,
                "message_end": message_end,
                "total_chars": total_chars,
            },
        },
    )


async def _read_via_cloud(
    *,
    conversation_id: str,
    cursor: str | None,
    max_chars: int | None,
    focus: str,
    query: str | None,
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
        "focus": focus,
    }
    if query is not None:
        payload["query"] = query
    if cursor is not None:
        payload["cursor"] = cursor
    if max_chars is not None:
        payload["max_chars"] = max_chars
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

    folder_id: str | None = None

    def __init__(self, *, folder_id: str | None = None) -> None:
        self.folder_id = folder_id

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="read_conversation",
            description=(
                "读取一场历史对话。conversation_id 来自 search_conversations；"
                "也可只传 query：唯一命中则打开，多场列出。"
                "默认 focus=dialogue（用户/助手原文）；过程稿 focus=process。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": "string",
                        "description": (
                            "search_conversations 返回的 id；非 id 时按标题检索。"
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "无 id 时按标题/正文定位；有 id 时从第一条命中读起。"
                        ),
                    },
                    "cursor": {
                        "type": "string",
                        "description": "续读游标（m:消息下标）；首轮省略。",
                    },
                    "focus": {
                        "type": "string",
                        "enum": ["dialogue", "process"],
                        "default": "dialogue",
                        "description": (
                            "dialogue=用户/助手原文（默认）；"
                            "process=含工具、辩论、思考。"
                        ),
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": (
                            f"本页最大字符数（可选）；服务端硬顶 {MAX_CHUNK_CHARS}。"
                        ),
                    },
                },
                "required": [],
            },
            face=ToolFace.SEARCH,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        cid = str(arguments.get("conversation_id") or "").strip()
        if cid and context.conversation_id and cid == context.conversation_id:
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
                    "depth": DEFAULT_FOCUS,
                },
            )
        query_s = str(arguments.get("query") or "").strip() or None
        cursor = arguments.get("cursor")
        cursor_s = str(cursor).strip() if cursor else None
        focus_n = normalize_focus(str(arguments.get("focus") or DEFAULT_FOCUS))
        if focus_n is None:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="focus 须为 dialogue / process。",
                error="invalid focus",
            )
        max_chars: int | None = None
        if arguments.get("max_chars") is not None:
            try:
                max_chars = int(arguments["max_chars"])
            except (TypeError, ValueError):
                max_chars = None

        if is_uuid_id(cid):
            return await self._read_identified(
                cid,
                query_s=query_s,
                cursor_s=cursor_s,
                focus_n=focus_n,
                max_chars=max_chars,
                context=context,
            )

        locator = cid or query_s
        if not locator:
            return ToolResult(
                tool_call_id="",
                success=False,
                output=_MISSING_LOCATOR,
                error=_MISSING_LOCATOR,
            )
        resolved = await self._resolve_locator(locator, context)
        if isinstance(resolved, ToolResult):
            return resolved
        return await self._read_identified(
            resolved,
            query_s=query_s,
            cursor_s=cursor_s,
            focus_n=focus_n,
            max_chars=max_chars,
            context=context,
        )

    async def _resolve_locator(
        self, locator: str, context: ToolContext
    ) -> str | ToolResult:
        run = await run_conversation_search(
            folder_id=self.folder_id,
            arguments={"query": locator},
            context=context,
        )
        if run.error is not None:
            return run.error
        if run.folder_miss or not run.rows:
            logger.info(
                "conversation_log.read",
                result="locator_miss",
                locator=locator,
                run_id=context.run_id,
            )
            return _format_search_output(
                [], scope=run.scope, soft_note=run.soft_note
            )
        if len(run.rows) > 1:
            logger.info(
                "conversation_log.read",
                result="locator_multi",
                locator=locator,
                count=len(run.rows),
                run_id=context.run_id,
            )
            return _format_search_output(
                run.rows,
                scope=run.scope,
                soft_note=run.soft_note,
                lead=_MULTI_HIT_LEAD,
            )
        resolved = str(run.rows[0].get("conversation_id") or "").strip()
        if not is_uuid_id(resolved):
            return ToolResult(
                tool_call_id="",
                success=False,
                output="检索结果缺少有效 conversation_id。",
                error="invalid search row",
            )
        logger.info(
            "conversation_log.read",
            result="locator_unique",
            locator=locator,
            conversation_id=resolved,
            run_id=context.run_id,
        )
        return resolved

    async def _read_identified(
        self,
        cid: str,
        *,
        query_s: str | None,
        cursor_s: str | None,
        focus_n: str,
        max_chars: int | None,
        context: ToolContext,
    ) -> ToolResult:
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
                    "depth": DEFAULT_FOCUS,
                },
            )

        from agentcore.account.credentials import get_account_credentials

        try:
            if get_account_credentials() is not None:
                data = await _read_via_cloud(
                    conversation_id=cid,
                    cursor=cursor_s,
                    max_chars=max_chars,
                    focus=focus_n,
                    query=query_s,
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
                            "depth": DEFAULT_FOCUS,
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
                    message_offset=int(data.get("message_offset") or 0),
                    message_end=int(data.get("message_end") or 0),
                    focus=str(data.get("focus") or focus_n),
                    query=(
                        str(data["query"]).strip() or None
                        if data.get("query") is not None
                        else query_s
                    ),
                    query_hit=bool(data.get("query_hit")),
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
                            "depth": DEFAULT_FOCUS,
                        },
                    )

                messages = list(
                    await MessageRepository(session).list_all_for_conversation(cid)
                )
                journal_map: dict = {}
                if focus_n == FOCUS_PROCESS:
                    assistant_ids = [m.id for m in messages if m.role == "assistant"]
                    journal_map = await TurnJournalRepository(session).load_map(
                        assistant_ids
                    )
                chunk = page_conversation(
                    conv,
                    messages,
                    journal_map,
                    focus=focus_n,
                    cursor=cursor_s,
                    query=query_s,
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
                message_offset=chunk.message_offset,
                message_end=chunk.message_end,
                focus=chunk.focus,
                query=chunk.query,
                query_hit=chunk.query_hit,
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
