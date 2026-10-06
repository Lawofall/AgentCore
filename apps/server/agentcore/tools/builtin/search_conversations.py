"""search_conversations — search the owner's past chats (Cursor-shaped query).

``AUDIENCE_BOTH`` + ``ToolSurface.WORKER_ONLY`` + ``manual_wire``. Wired after
``build_*_registry`` by ``_wire_conversation_log_tools`` (CEO and worker).
Opening-table resident unless the assembly switch ``chats`` is off.

With account narrow-ticket creds (sidecar), calls the cloud HTTP API instead of
the local ConversationRepository (大众桌面无本机 PG).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentcore.conversation.log_export import search_hit_from_messages
from agentcore.core.logging import get_logger
from agentcore.core.search_query import (
    SEARCH_DEFAULT_LIMIT,
    parse_conversation_search_terms,
)
from agentcore.core.types import ToolApproval, ToolFace
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import ConversationRepository, MessageRepository
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    ToolRegistration,
    ToolSurface,
)

logger = get_logger(__name__)

_EMPTY_QUERY = (
    "请提供关键词。一次 1–2 个词；词多易空、可另开短查询。引号包精确短语。"
)
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
    lead: str | None = None,
) -> ToolResult:
    if not rows:
        if lead and soft_note:
            text = f"{lead}\n{soft_note}\n{_SOFT_MISS}"
        elif lead:
            text = f"{lead}\n{_SOFT_MISS}"
        elif soft_note:
            text = f"{soft_note}\n{_SOFT_MISS}"
        else:
            text = _SOFT_MISS
        return ToolResult(
            tool_call_id="",
            success=True,
            output=text,
            display={"result_count": 0, "scope": scope},
        )

    lines: list[str] = []
    if lead:
        lines.append(lead)
        lines.append("")
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
            hit_idx = row.get("hit_index")
            total = int(row.get("message_count") or 0)
            if isinstance(hit_idx, int) and total > 0:
                lines.append(f"  摘要（第 {hit_idx + 1}/{total} 条）：{row['snippet']}")
            else:
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
    exclude_conversation_id: str | None,
    limit: int,
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
        "include_archived": True,
        "global_chats_only": False,
        "exclude_conversation_id": exclude_conversation_id,
        "limit": limit,
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
    exclude_conversation_id: str | None,
    limit: int,
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
            include_archived=True,
            global_chats_only=False,
            exclude_conversation_id=exclude_conversation_id,
        )
        msg_repo = MessageRepository(session)
        for row in rows:
            try:
                msgs = await msg_repo.list_all_for_conversation(row["conversation_id"])
                snippet = search_hit_from_messages(msgs, query)
                if snippet:
                    row["snippet"] = snippet.snippet
                    if snippet.message_index is not None:
                        row["hit_index"] = snippet.message_index
            except Exception:  # noqa: BLE001 — snippet is best-effort
                pass
    return rows, False


_FOLDER_ALL = "all"
_BARE_CHAT_NOTE = "当前是裸聊（无文件夹）；已按 all 范围检索。"


@dataclass(frozen=True)
class SearchFolderResolution:
    """Where to search: ``resolved_folder`` None = whole account."""

    resolved_folder: str | None
    scope: str
    explicit_folder: str | None
    soft_note: str | None


def resolve_search_folder(
    *,
    host_folder_id: str | None,
    arguments: dict[str, Any],
) -> SearchFolderResolution:
    """Fill-in is ``folder_id`` only. Leftover ``scope`` still parsed.

    Omit = this folder (bare chat → all, with a note). Literal ``all`` =
    account. Any other value = neighbor folder id. Explicit ``folder_id``
    wins over leftover ``scope``. Invalid leftover ``scope`` is ignored.
    Does not scan ``query``.
    """
    raw = str(arguments.get("folder_id") or "").strip()
    leftover = str(arguments.get("scope") or "").strip()
    if leftover not in {"all", "folder"}:
        leftover = ""

    if raw:
        if raw.casefold() == _FOLDER_ALL:
            return SearchFolderResolution(None, "all", None, None)
        return SearchFolderResolution(raw, "folder", raw, None)
    if leftover == "all":
        return SearchFolderResolution(None, "all", None, None)
    if not host_folder_id:
        return SearchFolderResolution(None, "all", None, _BARE_CHAT_NOTE)
    return SearchFolderResolution(host_folder_id, "folder", None, None)


@dataclass(frozen=True)
class ConversationSearchRun:
    rows: list[dict[str, Any]]
    folder_miss: bool
    soft_note: str | None
    scope: str
    error: ToolResult | None = None


async def run_conversation_search(
    *,
    folder_id: str | None,
    arguments: dict[str, Any],
    context: ToolContext,
) -> ConversationSearchRun:
    """Shared search used by ``search_conversations`` and ``read_conversation`` locators."""
    query = str(arguments.get("query") or "").strip()
    located = resolve_search_folder(host_folder_id=folder_id, arguments=arguments)
    if not parse_conversation_search_terms(query):
        return ConversationSearchRun(
            rows=[],
            folder_miss=False,
            soft_note=None,
            scope=located.scope,
            error=ToolResult(
                tool_call_id="",
                success=True,
                output=_EMPTY_QUERY,
                display={"result_count": 0, "scope": located.scope},
            ),
        )
    # leftover ``limit`` is ignored; execute freeze at SEARCH_DEFAULT_LIMIT.
    limit = SEARCH_DEFAULT_LIMIT

    explicit_folder = located.explicit_folder
    resolved_folder = located.resolved_folder
    soft_note = located.soft_note
    scope = located.scope

    host_id = context.conversation_id

    from agentcore.account.credentials import get_account_credentials

    try:
        if get_account_credentials() is not None:
            rows, folder_miss = await _search_via_cloud(
                query=query,
                folder_id=resolved_folder,
                exclude_conversation_id=host_id or None,
                limit=limit,
                check_folder_owned=bool(explicit_folder),
            )
        else:
            rows, folder_miss = await _search_via_db(
                user_id=context.user_id,
                query=query,
                folder_id=resolved_folder,
                exclude_conversation_id=host_id or None,
                limit=limit,
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
            return ConversationSearchRun(
                rows=[],
                folder_miss=False,
                soft_note=None,
                scope=scope,
                error=ToolResult(
                    tool_call_id="",
                    success=False,
                    output=f"检索历史对话失败。{e}",
                    error=getattr(e, "code", "account_cloud_failed"),
                ),
            )
        return ConversationSearchRun(
            rows=[],
            folder_miss=False,
            soft_note=None,
            scope=scope,
            error=ToolResult(
                tool_call_id="",
                success=False,
                output="检索历史对话失败，请稍后再试。",
                error=str(e),
            ),
        )

    return ConversationSearchRun(
        rows=rows,
        folder_miss=folder_miss,
        soft_note=soft_note,
        scope=scope,
    )


class SearchConversationsTool:
    """Search the owner's conversations for on-demand log recall."""

    registration = ToolRegistration(
        surface=ToolSurface.WORKER_ONLY,
        audience=AUDIENCE_BOTH,
        manual_wire=True,
        catalog_summary="搜本账号对话",
        blurb="在你的历史对话里按关键词找",
    )

    # Host conversation's folder (None = bare chat). Used when scope=folder.
    folder_id: str | None = None

    def __init__(self, *, folder_id: str | None = None) -> None:
        self.folder_id = folder_id

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="search_conversations",
            description="检索本账号历史对话。用户规则 ≠ 本工具。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "标题与可见用户/助手正文。未加引号的词须同场都出现；"
                            "引号包精确短语。"
                        ),
                    },
                    "folder_id": {
                        "type": "string",
                        "description": "省略=本夹；all=全账号；其它=邻夹 id。",
                    },
                },
                "required": ["query"],
            },
            face=ToolFace.SEARCH,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        run = await run_conversation_search(
            folder_id=self.folder_id,
            arguments=arguments,
            context=context,
        )
        if run.error is not None:
            return run.error
        if run.folder_miss:
            logger.info(
                "conversation_log.search",
                result="folder_miss",
                user_id=context.user_id,
            )
            return ToolResult(
                tool_call_id="",
                success=True,
                output=_SOFT_MISS,
                display={"result_count": 0, "scope": run.scope},
            )

        logger.info(
            "conversation_log.search",
            result="ok",
            user_id=context.user_id,
            count=len(run.rows),
            scope=run.scope,
            run_id=context.run_id,
        )
        return _format_search_output(
            run.rows, scope=run.scope, soft_note=run.soft_note
        )
