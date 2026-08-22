"""search_conversations — Worker directory / search over the owner's past chats.

Worker-only (``AUDIENCE_WORKER_ONLY`` + ``ToolSurface.WORKER_ONLY`` + ``manual_wire``).
Wired after ``build_worker_registry`` by ``_wire_worker_conversation_log_tools``.
Never reaches the CEO toolset (``build_ceo_tool_registry`` only collects builtin
CEO-audience tools).

With account narrow-ticket creds (sidecar), calls the cloud HTTP API instead of
the local ConversationRepository (大众桌面无本机 PG).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from agentcore.conversation.log_export import search_snippet_from_messages
from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import ConversationRepository, MessageRepository
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_WORKER_ONLY,
    ToolRegistration,
    ToolSurface,
)

logger = get_logger(__name__)

_SEARCH_HARD_CAP = 30
_DEFAULT_LIMIT = 10
_MAX_LOOKBACK_HOURS = 168
_SOFT_MISS = (
    "未找到可查阅的历史对话（可能不存在、已删除，或不在可访问范围内）。"
)


def _is_account_cloud_failure(exc: BaseException) -> bool:
    from agentcore.account.credentials import AccountCloudError

    return isinstance(exc, AccountCloudError)


def _format_search_output(
    rows: list[dict[str, Any]],
    *,
    scope: str,
    soft_note: str | None,
) -> ToolResult:
    if not rows:
        text = soft_note + "\n" + _SOFT_MISS if soft_note else _SOFT_MISS
        return ToolResult(
            tool_call_id="",
            success=True,
            output=text,
            display={"result_count": 0, "scope": scope},
        )

    lines: list[str] = []
    if soft_note:
        lines.append(soft_note)
        lines.append("")
    lines.append(f"找到 {len(rows)} 场对话（scope={scope}）：")
    lines.append("")
    for row in rows:
        folder_bit = (
            f" · 文件夹「{row['folder_name']}」"
            if row.get("folder_name")
            else (" · 裸聊" if not row.get("folder_id") else "")
        )
        arch = " · 已归档" if row.get("archived") else ""
        lines.append(
            f"- `{row['conversation_id']}` · {row['title']} · "
            f"{row.get('message_count', 0)} 条消息 · 更新 {row.get('updated_at') or '—'}"
            f"{folder_bit}{arch}"
        )
        if row.get("snippet"):
            lines.append(f"  摘要：{row['snippet']}")
    output = "\n".join(lines)
    return ToolResult(
        tool_call_id="",
        success=True,
        output=output,
        output_limit=max(len(output), 4000),
        display={"result_count": len(rows), "scope": scope},
    )


async def _search_via_cloud(
    *,
    query: str,
    folder_id: str | None,
    include_archived: bool,
    global_chats_only: bool,
    exclude_conversation_id: str | None,
    limit: int,
    updated_within_hours: int | None,
    check_folder_owned: bool,
) -> tuple[list[dict[str, Any]], bool]:
    from agentcore.account.credentials import (
        AccountCloudError,
        cloud_search_conversations,
        get_account_credentials,
    )

    creds = get_account_credentials()
    assert creds is not None
    payload: dict[str, Any] = {
        "query": query,
        "folder_id": folder_id,
        "include_archived": include_archived,
        "global_chats_only": global_chats_only,
        "exclude_conversation_id": exclude_conversation_id,
        "limit": limit,
        "updated_within_hours": updated_within_hours,
        "check_folder_owned": check_folder_owned,
    }
    try:
        data = await cloud_search_conversations(creds, payload=payload)
    except AccountCloudError:
        raise
    except Exception as e:  # noqa: BLE001
        raise AccountCloudError(str(e)) from e
    rows_raw = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows_raw, list):
        raise AccountCloudError("account search missing rows")
    rows: list[dict[str, Any]] = [r for r in rows_raw if isinstance(r, dict)]
    folder_miss = bool(data.get("folder_miss")) if isinstance(data, dict) else False
    return rows, folder_miss


async def _search_via_db(
    *,
    user_id: str,
    query: str,
    folder_id: str | None,
    include_archived: bool,
    global_chats_only: bool,
    exclude_conversation_id: str | None,
    limit: int,
    updated_after: datetime | None,
    explicit_folder: str | None,
) -> tuple[list[dict[str, Any]], bool]:
    async with async_session_factory() as session:
        if explicit_folder:
            from agentcore.db.repositories import FolderRepository

            folder = await FolderRepository(session).get_by_id(
                explicit_folder, user_id=user_id
            )
            if folder is None:
                return [], True
        rows = await ConversationRepository(session).search_with_projections(
            user_id,
            query,
            limit=limit,
            folder_id=folder_id,
            include_archived=include_archived,
            global_chats_only=global_chats_only,
            exclude_conversation_id=exclude_conversation_id,
            updated_after=updated_after,
        )
        msg_repo = MessageRepository(session)
        for row in rows:
            try:
                msgs = await msg_repo.list_all_for_conversation(row["conversation_id"])
                snippet = search_snippet_from_messages(msgs, query)
                if snippet:
                    row["snippet"] = snippet
            except Exception:  # noqa: BLE001 — snippet is best-effort
                pass
    return rows, False


class SearchConversationsTool:
    """List / search the owner's conversations for on-demand log recall."""

    registration = ToolRegistration(
        surface=ToolSurface.WORKER_ONLY,
        audience=AUDIENCE_WORKER_ONLY,
        manual_wire=True,
    )

    # Host conversation's folder (None = bare chat). Used when scope=folder.
    folder_id: str | None = None

    def __init__(self, *, folder_id: str | None = None) -> None:
        self.folder_id = folder_id

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="search_conversations",
            description=(
                "检索当前用户账号下的历史对话目录（标题匹配；query 为空则按最近更新列出）。"
                "用于查阅「上次 / 以前」某场讨论的原文与过程——先搜到 conversation_id，再"
                "用 read_conversation 打开。不含本回合正在进行的宿主会话；不含已软删 / handoff"
                "宿主。偏好与巩固后的事实请用记忆主题（consult），不要用本工具代替。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "可选；标题关键词。空 = 按更新时间列最近对话。",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["all", "folder", "global_chats"],
                        "description": (
                            "all=全账号（默认）；folder=宿主对话所在文件夹；"
                            "global_chats=仅裸聊（无文件夹）。"
                        ),
                    },
                    "folder_id": {
                        "type": "string",
                        "description": "可选；指定其它文件夹 id（须属同一用户）。",
                    },
                    "include_archived": {
                        "type": "boolean",
                        "description": "是否包含已归档对话（默认 false）。",
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            f"返回条数，默认 {_DEFAULT_LIMIT}，硬顶 {_SEARCH_HARD_CAP}。"
                        ),
                    },
                    "updated_within_hours": {
                        "type": "integer",
                        "description": (
                            "可选；只返回近 N 小时内有更新的对话"
                            f"（1–{_MAX_LOOKBACK_HOURS}）。日复盘等周期任务应设置。"
                        ),
                    },
                },
                "required": [],
            },
            category=ToolCategory.SEARCH,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        query = str(arguments.get("query") or "").strip()
        scope = str(arguments.get("scope") or "all").strip() or "all"
        if scope not in {"all", "folder", "global_chats"}:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="scope 须为 all / folder / global_chats。",
                error="invalid scope",
            )
        include_archived = bool(arguments.get("include_archived") or False)
        try:
            limit = int(arguments.get("limit") or _DEFAULT_LIMIT)
        except (TypeError, ValueError):
            limit = _DEFAULT_LIMIT
        limit = max(1, min(limit, _SEARCH_HARD_CAP))

        updated_after: datetime | None = None
        updated_within_hours: int | None = None
        raw_hours = arguments.get("updated_within_hours")
        if raw_hours is not None and raw_hours != "":
            try:
                hours = int(raw_hours)
            except (TypeError, ValueError):
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output="updated_within_hours 须为正整数。",
                    error="invalid updated_within_hours",
                )
            hours = max(1, min(hours, _MAX_LOOKBACK_HOURS))
            updated_within_hours = hours
            updated_after = datetime.now(UTC) - timedelta(hours=hours)

        explicit_folder = str(arguments.get("folder_id") or "").strip() or None
        folder_id: str | None = None
        global_chats_only = False
        soft_note: str | None = None
        if explicit_folder:
            folder_id = explicit_folder
        elif scope == "folder":
            if not self.folder_id:
                soft_note = (
                    "当前是裸聊（无文件夹）；已按 all 范围检索。"
                    "请改用 scope=all 或 global_chats。"
                )
            else:
                folder_id = self.folder_id
        elif scope == "global_chats":
            global_chats_only = True

        host_id = context.conversation_id

        from agentcore.account.credentials import get_account_credentials

        try:
            if get_account_credentials() is not None:
                rows, folder_miss = await _search_via_cloud(
                    query=query,
                    folder_id=folder_id,
                    include_archived=include_archived,
                    global_chats_only=global_chats_only,
                    exclude_conversation_id=host_id or None,
                    limit=limit,
                    updated_within_hours=updated_within_hours,
                    check_folder_owned=bool(explicit_folder),
                )
            else:
                rows, folder_miss = await _search_via_db(
                    user_id=context.user_id,
                    query=query,
                    folder_id=folder_id,
                    include_archived=include_archived,
                    global_chats_only=global_chats_only,
                    exclude_conversation_id=host_id or None,
                    limit=limit,
                    updated_after=updated_after,
                    explicit_folder=explicit_folder,
                )
        except Exception as e:  # noqa: BLE001 — tool failure must not crash the turn
            cloud_fail = _is_account_cloud_failure(e)
            logger.warning(
                "conversation_log.search_failed",
                user_id=context.user_id,
                error=str(e),
                account_cloud_failed=cloud_fail,
            )
            if cloud_fail:
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output=f"检索历史对话失败。{e}",
                    error=getattr(e, "code", "account_cloud_failed"),
                )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="检索历史对话失败，请稍后再试。",
                error=str(e),
            )

        if folder_miss:
            logger.info(
                "conversation_log.search",
                result="folder_miss",
                user_id=context.user_id,
            )
            return ToolResult(
                tool_call_id="",
                success=True,
                output=_SOFT_MISS,
                display={"result_count": 0, "scope": scope},
            )

        logger.info(
            "conversation_log.search",
            result="ok",
            user_id=context.user_id,
            count=len(rows),
            scope=scope,
            run_id=context.run_id,
        )
        return _format_search_output(rows, scope=scope, soft_note=soft_note)
