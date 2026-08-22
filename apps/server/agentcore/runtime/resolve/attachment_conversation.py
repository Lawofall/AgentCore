"""Conversation-attachment deep-read (never client shallow ``text``).

Host / soft-miss / cloud-or-local DB live here so file-attachment rendering and
prompt assembly can change without touching this path.
"""

from __future__ import annotations

from agentcore.core.logging import get_logger
from agentcore.workspace.attachment_parse import ATTACHMENT_INLINE_MAX_CHARS

logger = get_logger(__name__)

# Soft-miss notes for conversation attachments (跨会话对话日志访问定案 P1).
# Never fall back to client shallow ``text`` — that would silently fake a deep read.
_CONV_ATTACH_SOFT_MISS = (
    "无法打开该对话（可能不存在、已删除、为 handoff，或不在可访问范围内）。"
)
_CONV_ATTACH_NO_ID = "缺少 conversation_id，无法服务端深读；未注入客户端浅文。"
_CONV_ATTACH_HOST = (
    "那是本回合正在进行的宿主会话——请直接看本会话工作记忆，无需附件深读。"
)
_CONV_ATTACH_UNAVAILABLE = (
    "暂时无法深读该对话（云端或本机库暂时不可用）；未注入客户端浅文。"
)
_CONV_ATTACH_TRUNC_NOTE = (
    "\n\n… [truncated for prompt; 完整日志请派查阅 Worker `read_conversation` 续读"
    "（conversation_id={cid}{cursor_part}）]"
)


def _format_conversation_attach_block(
    *,
    name: str,
    cid: str,
    title: str,
    transcript: str,
    truncated: bool,
    next_cursor: str | None,
) -> str:
    """Shared header/body for local DB and account-cloud deep-read chunks."""
    display_title = title or name
    note = " (truncated; continue via read_conversation)" if truncated else ""
    body = transcript
    if truncated:
        cursor_part = f", next_cursor={next_cursor}" if next_cursor else ""
        body += _CONV_ATTACH_TRUNC_NOTE.format(cid=cid, cursor_part=cursor_part)
    return f"--- Conversation: {display_title}{note} ---\n{body}"


async def _deep_read_conversation_attachment_via_cloud(
    *,
    cid: str,
    name: str,
) -> str:
    """Sidecar account-ticket path — same cloud surface as ``read_conversation``."""
    from agentcore.account.credentials import (
        AccountCloudError,
        cloud_read_conversation,
        get_account_credentials,
    )

    creds = get_account_credentials()
    assert creds is not None
    payload = {
        "conversation_id": cid,
        "cursor": None,
        "max_chars": ATTACHMENT_INLINE_MAX_CHARS,
    }
    try:
        data = await cloud_read_conversation(creds, payload=payload)
    except AccountCloudError as e:
        logger.warning(
            "attachment.conversation_cloud_failed",
            conversation_id=cid,
            error=str(e),
            code=getattr(e, "code", None),
        )
        return f"--- Conversation: {name} ---\n{_CONV_ATTACH_UNAVAILABLE}"
    except Exception as e:  # noqa: BLE001 — soft-degrade; never HARD the turn
        logger.warning(
            "attachment.conversation_cloud_failed",
            conversation_id=cid,
            error=str(e),
        )
        return f"--- Conversation: {name} ---\n{_CONV_ATTACH_UNAVAILABLE}"

    if not isinstance(data, dict):
        return f"--- Conversation: {name} ---\n{_CONV_ATTACH_UNAVAILABLE}"

    status = str(data.get("status") or "")
    if status == "soft_miss":
        return f"--- Conversation: {name} ---\n{_CONV_ATTACH_SOFT_MISS}"
    if status != "ok":
        logger.warning(
            "attachment.conversation_cloud_unexpected_status",
            conversation_id=cid,
            status=status,
        )
        return f"--- Conversation: {name} ---\n{_CONV_ATTACH_UNAVAILABLE}"

    next_cursor = (
        str(data["next_cursor"]) if data.get("next_cursor") is not None else None
    )
    return _format_conversation_attach_block(
        name=name,
        cid=str(data.get("conversation_id") or cid),
        title=str(data.get("title") or ""),
        transcript=str(data.get("transcript") or ""),
        truncated=bool(data.get("truncated")),
        next_cursor=next_cursor,
    )


async def _deep_read_conversation_attachment(
    att: dict,
    *,
    name: str,
    user_id: str | None,
    host_conversation_id: str | None,
) -> str:
    """Server-side deep transcript for ``kind=conversation`` — never client shallow text.

    Missing id / owner soft-miss / handoff / host → explicit note.
    With sidecar account ticket → cloud read (``cloud_read_conversation``); else local DB.
    Cloud / DB-connectivity failure → soft unavailable note (never HARD the turn).
    Over-long → first prompt-capped chunk + Worker ``read_conversation`` continuation hint.
    """
    from agentcore.account.credentials import get_account_credentials
    from agentcore.conversation.log_export import (
        chunk_transcript,
        render_conversation_log,
    )
    from agentcore.db.base import async_session_factory
    from agentcore.db.errors import is_db_connectivity_error
    from agentcore.db.repositories import (
        ConversationRepository,
        MessageRepository,
        TurnJournalRepository,
    )

    cid = str(att.get("conversation_id") or "").strip()
    if not cid:
        return f"--- Conversation: {name} ---\n{_CONV_ATTACH_NO_ID}"
    if host_conversation_id and cid == host_conversation_id:
        return f"--- Conversation: {name} ---\n{_CONV_ATTACH_HOST}"

    if get_account_credentials() is not None:
        return await _deep_read_conversation_attachment_via_cloud(cid=cid, name=name)

    if not user_id:
        return f"--- Conversation: {name} ---\n{_CONV_ATTACH_SOFT_MISS}"

    try:
        async with async_session_factory() as session:
            conv = await ConversationRepository(session).get_by_id(cid, user_id=user_id)
            if conv is None or conv.mode == "handoff":
                return f"--- Conversation: {name} ---\n{_CONV_ATTACH_SOFT_MISS}"
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
                cursor=None,
                max_chars=ATTACHMENT_INLINE_MAX_CHARS,
            )
    except Exception as e:  # noqa: BLE001 — classify; only connectivity soft-degrades
        if is_db_connectivity_error(e):
            logger.warning(
                "attachment.conversation_db_unavailable",
                conversation_id=cid,
                error=str(e),
            )
            return f"--- Conversation: {name} ---\n{_CONV_ATTACH_UNAVAILABLE}"
        raise

    return _format_conversation_attach_block(
        name=name,
        cid=cid,
        title=chunk.title,
        transcript=chunk.transcript,
        truncated=chunk.truncated,
        next_cursor=chunk.next_cursor,
    )
