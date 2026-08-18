"""Sidecar prior-turn window: cloud ``load_chat_context``, or a confirmed same-window."""

from __future__ import annotations

from typing import Any

from agentcore.account.credentials import (
    AccountCloudError,
    AccountCredentials,
    cloud_chat_context,
    get_account_credentials,
)
from agentcore.core.logging import get_logger

logger = get_logger(__name__)

CHAT_CONTEXT_UNAVAILABLE_MESSAGE = "未能加载对话历史，请稍后重试。"
_RETRYABLE_CLOUD_CODES = frozenset({"account_cloud_server"})


class ChatContextUnavailableError(Exception):
    """Cloud window fetch failed and no confirmed same-window fallback exists."""

    def __init__(self, message: str = CHAT_CONTEXT_UNAVAILABLE_MESSAGE) -> None:
        super().__init__(message)
        self.message = message


def coerce_history_rows(raw: object) -> list[dict[str, Any]]:
    """Keep ``{role, content}`` (+ optional evidence_ledger) rows only."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        row: dict[str, Any] = {"role": role, "content": content}
        ledger = item.get("evidence_ledger")
        if isinstance(ledger, list) and ledger:
            row["evidence_ledger"] = list(ledger)
        out.append(row)
    return out


async def resolve_sidecar_turn_history(
    conversation_id: str,
    *,
    creds: AccountCredentials | None = None,
    fallback: list[Any] | None = None,
    prefer_cloud: bool = True,
) -> list[dict[str, Any]]:
    """Prefer cloud assembled window; use fallback only when the caller confirmed it.

    ``fallback is None`` means the window is unknown (desktop omitted history,
    harvest never stamped). A confirmed empty list is a new chat, not a fetch miss.
    ``prefer_cloud=False`` with a confirmed fallback returns that window without
    hitting chat-context (desktop already fetched the same endpoint). Harvest keeps
    the default so a stamp can still be refreshed. Cloud 5xx retries once; timeout
    / auth errors do not (avoid stacking the 60s HTTP budget). Still missing →
    raise; never pretend an empty window succeeded.

    One path stays lenient: no account ticket with an unconfirmed window returns
    empty rather than raising, because harvest calls with ``fallback=None`` and a
    not-logged-in account would then hard-fail. It emits
    ``chat_context.window_unknown_no_ticket`` so the blast radius is observable
    before that leniency is traded for a raise.
    """
    ticket = creds if creds is not None else get_account_credentials()
    confirmed = fallback is not None
    fallback_rows = coerce_history_rows(fallback) if confirmed else []
    if confirmed and not prefer_cloud:
        return fallback_rows
    if ticket is None:
        if not confirmed:
            logger.warning(
                "chat_context.window_unknown_no_ticket",
                conversation_id=conversation_id,
            )
        return fallback_rows
    last_exc: AccountCloudError | None = None
    for attempt in (1, 2):
        try:
            data = await cloud_chat_context(ticket, conversation_id=conversation_id)
            raw = data.get("history")
            if not isinstance(raw, list):
                raise AccountCloudError(
                    "account chat-context history is not a list",
                    code="account_cloud_failed",
                )
            return coerce_history_rows(raw)
        except AccountCloudError as exc:
            last_exc = exc
            retry = attempt == 1 and exc.code in _RETRYABLE_CLOUD_CODES
            logger.warning(
                "chat_context.sidecar_fetch_failed",
                conversation_id=conversation_id,
                error=exc.message,
                code=exc.code,
                attempt=attempt,
                retry=retry,
            )
            if retry:
                continue
            break
    if confirmed:
        return fallback_rows
    raise ChatContextUnavailableError() from last_exc
