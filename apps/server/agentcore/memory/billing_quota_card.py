"""User-visible notice when background memory consolidation skips for platform quota.

Reuses the ``memory_updates`` + ``kind=quota`` card shell (same as always-pool
quota cards) with a distinct dedup fingerprint so one conversation does not get
spammed while quota stays exhausted.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import MemoryUpdateRepository
from agentcore.memory.always_quota import QUOTA_CARD_KIND
from agentcore.memory.maintenance import MemoryUpdateItem
from agentcore.messaging.hub import default_chat_hub

logger = get_logger(__name__)

_BILLING_QUOTA_FINGERPRINT = "billing_platform_quota_skip"
_CARD_SUMMARY = (
    "平台额度已用尽，这轮对话的记忆没能存下。"
    "额度重置后可继续，或接入自己的 key。"
)


def _billing_quota_fingerprint(items: list | None) -> bool:
    if not items:
        return False
    for it in items:
        if isinstance(it, dict) and it.get("action") == "quota":
            return it.get("content") == _BILLING_QUOTA_FINGERPRINT
    return False


async def record_billing_quota_skip_card_once(
    session,
    *,
    user_id: str,
    conversation_id: str,
    anchor_at: datetime | None = None,
):
    """Persist a billing-quota skip card, or ``None`` when this session already saw one."""
    repo = MemoryUpdateRepository(session)
    rows = await repo.list_for_conversation(conversation_id, limit=50)
    latest = next(
        (
            r
            for r in reversed(rows)
            if r.kind == QUOTA_CARD_KIND and _billing_quota_fingerprint(r.items)
        ),
        None,
    )
    if latest is not None:
        logger.info(
            "memory.billing_quota_card_suppressed",
            conversation_id=conversation_id,
            fingerprint=_BILLING_QUOTA_FINGERPRINT,
        )
        return None

    items = [
        MemoryUpdateItem(
            action="quota",
            file="",
            section="",
            scope="global",
            content=_BILLING_QUOTA_FINGERPRINT,
            target="",
            project_id=None,
        ),
    ]
    row = await repo.record(
        conversation_id=conversation_id,
        user_id=user_id,
        items=[asdict(item) for item in items],
        kind=QUOTA_CARD_KIND,
        summary=_CARD_SUMMARY,
        anchor_at=anchor_at,
    )
    logger.info(
        "memory.billing_quota_card",
        conversation_id=conversation_id,
        user_id=user_id,
    )
    return row


async def notify_billing_quota_skip(
    user_id: str,
    conversation_id: str,
    *,
    anchor_at: datetime | None = None,
) -> None:
    """Best-effort ``memory_updates`` card when memory chrome skips for platform quota."""
    import contextlib

    try:
        async with async_session_factory() as session:
            row = await record_billing_quota_skip_card_once(
                session,
                user_id=user_id,
                conversation_id=conversation_id,
                anchor_at=anchor_at,
            )
            if row is None:
                return
            update_payload = {
                "id": row.id,
                "conversation_id": conversation_id,
                "created_at": row.created_at.isoformat(),
                "kind": row.kind,
                "summary": row.summary,
                "items": row.items,
                "anchor_at": anchor_at.isoformat() if anchor_at is not None else None,
            }
        with contextlib.suppress(Exception):
            await default_chat_hub().publish(
                [user_id],
                {
                    "type": "memory_updated",
                    "conversation_id": conversation_id,
                    "kind": QUOTA_CARD_KIND,
                    "update": update_payload,
                },
            )
    except Exception as e:  # noqa: BLE001 - card is best-effort
        logger.warning(
            "memory.billing_quota_card_failed",
            user_id=user_id,
            conversation_id=conversation_id,
            error=str(e),
        )
