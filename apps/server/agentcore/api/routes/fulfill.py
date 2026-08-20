"""Device-level CLIENT_TOOL fulfillment SSE (本机工作区履约通道).

Each online desktop (or other capable client) holds one long-lived
``GET /v1/fulfill`` subscription declaring ``device_id``, channel ``caps``, and
the permanent ``roots`` it currently holds. The server routes ``*_required``
frames to the matching device instead of the turn display sink — so a healthy
desktop that is not watching the conversation can still fulfil local ops.

Conversation-scoped grants are **not** in that query param. They are bound to
this device when the desktop registers them (``fulfill/declare.py``) and are
re-seeded here from storage, because a reconnect builds a brand-new session:
the client pushing its whole grant set back was a second source of truth for a
fact the server already owns, and the window before it landed was where a
mount's first op met an empty hub.

Auth is the access-token cookie (same as ``/v1/realtime``). SSE cannot refresh a
token mid-stream; on 401 the client reconnects after refresh.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from agentcore.api.dependencies import AuthUser, get_db
from agentcore.api.sse import release_request_db_before_sse
from agentcore.core.errors import ValidationError
from agentcore.core.logging import get_logger
from agentcore.db.repositories import PausedTurnRepository
from agentcore.db.repositories.external_grants import ExternalGrantRepository
from agentcore.fulfill.hub import (
    FULFILL_CHANNELS,
    FulfillerHub,
    FulfillerSession,
    default_fulfiller_hub,
)
from agentcore.fulfill.user_signal import (
    attention_snapshot_frame,
    turn_activity_snapshot_frame,
)
from agentcore.runtime.leases import list_fresh_conversation_ids_for_user
from agentcore.runtime.turn.queue import turn_queue
from agentcore.runtime.turn.runs import turn_runs

logger = get_logger(__name__)

router = APIRouter(prefix="/fulfill", tags=["fulfill"])

# Idle gap after which a heartbeat comment is sent (keep proxies / NAT warm).
_HEARTBEAT_SECONDS = 25.0


def _parse_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_caps(raw: str | None) -> frozenset[str]:
    parts = _parse_csv(raw)
    unknown = sorted({p for p in parts if p not in FULFILL_CHANNELS})
    if unknown:
        raise ValidationError(f"unknown fulfill caps: {', '.join(unknown)}")
    return frozenset(parts)


async def _bound_grant_roots(
    session: AsyncSession, user_id: str, device_id: str
) -> list[str]:
    """External-grant roots this device registered, read back on (re)connect.

    The binding lives in the grant row; the session holding it does not survive
    a reconnect. Read before ``release_request_db_before_sse`` — the request's
    DB session is returned to the pool before the stream body starts.
    """
    roots = await ExternalGrantRepository(session).list_root_ids_for_device(
        user_id=user_id, device_id=device_id
    )
    if roots:
        logger.info(
            "fulfill.grant_roots_seeded",
            user=user_id,
            device=device_id,
            roots=len(roots),
        )
    return roots


async def _running_conversation_ids_for_seed(
    session: AsyncSession, user_id: str
) -> list[str] | None:
    """Fresh ``turn_leases`` for this user, unioned with this process's live runs.

    Leases are the durable truth (restart empties the registry). The registry
    covers the brief window after ``register`` and before the lease row exists.
    ``None`` means the lease query failed — do not send a replace snapshot
    (an empty ``running: []`` would extinguish every live light). A successful
    empty list is the real「none running」and *is* sent.
    """
    try:
        ids = await list_fresh_conversation_ids_for_user(user_id, session=session)
    except Exception:  # noqa: BLE001 — connect must still open
        logger.exception("fulfill.turn_activity_seed_failed", user=user_id)
        return None
    seen = set(ids)
    for run in turn_runs.live_runs():
        if run.user_id == user_id and run.conversation_id not in seen:
            seen.add(run.conversation_id)
            ids.append(run.conversation_id)
    return ids


async def _attention_entries_for_seed(
    session: AsyncSession, user_id: str
) -> list[dict] | None:
    """paused_turns for this user, unioned with this process's registry hot cards.

    One user-scoped paused query — not an N-conversation recovery scan. Registry
    covers in-process cards that have not been persisted yet. ``None`` means the
    paused query failed — do not send a replace snapshot (an empty ``entries: []``
    would extinguish every waiting light). A successful empty list is the real
    「none waiting」and *is* sent.
    """
    from agentcore.attention.snapshot import merge_attention_entries

    try:
        rows = await PausedTurnRepository(session).list_pending_for_user(user_id)
    except Exception:  # noqa: BLE001 — connect must still open
        logger.exception("fulfill.attention_seed_failed", user=user_id)
        return None
    return merge_attention_entries(rows, user_id=user_id)


def _format_event(event: dict) -> str:
    """Serialize a hub event dict as one ``text/event-stream`` frame."""
    event_type = str(event.get("type", "message"))
    data = json.dumps(event, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


def _seed_registered_session(
    session: FulfillerSession,
    hub: FulfillerHub,
    *,
    running_conversation_ids: list[str] | None = None,
    attention_entries: list[dict] | None = None,
) -> None:
    """Replay in-flight ops onto a capable fulfiller; always seed account state.

    A reconnect must re-push CLIENT_TOOL frames the previous session already
    saw (registry Futures stay open). Observers — web tabs, zero caps,
    :attr:`FulfillerSession.can_fulfil` is false — share this stream for the
    same account snapshots, but must not rehang: that would re-deliver
    ``workspace_op`` / ``host_op`` onto the live desktop and run the side
    effect twice. Do not paper over that with request_id dedup on the
    desktop (a reconnect that dropped the first frame would swallow it).

    Queue seed is one ``turn_queue_account_snapshot`` (empty ``queues`` included)
    so the client can replace its cloud table — never a loop of per-conversation
    ``turn_queue_snapshot``. ``running_conversation_ids`` is the account's
    live-turn set (fresh ``turn_leases``, plus this process's registry for the
    register-before-lease window). ``None`` skips that replace (query failed;
    connection still opens). A successful empty list is still delivered so the
    client can replace. ``attention_entries`` is the account's waiting-card
    set (``paused_turns`` by user plus this process's registry hot cards).
    Same ``None`` vs ``[]`` split: empty ``entries: []`` extinguishes stale
    lights; a failed read must not.
    """
    from agentcore.runtime.events.client_tool_reattach import rehang_pending_client_tools

    if session.can_fulfil:
        rehang_pending_client_tools(session.user_id)
    account_queue = turn_queue.account_snapshot_frame(session.user_id)
    hub.deliver(session, account_queue)
    logger.info(
        "fulfill.queue_account_snapshot_pushed",
        user=session.user_id,
        device=session.device_id,
        queues=len(account_queue["payload"]["queues"]),
    )
    if running_conversation_ids is not None:
        running = list(running_conversation_ids)
        hub.deliver(session, turn_activity_snapshot_frame(running))
        logger.info(
            "fulfill.turn_activity_snapshot_pushed",
            user=session.user_id,
            device=session.device_id,
            running=len(running),
        )
    if attention_entries is not None:
        entries = list(attention_entries)
        hub.deliver(session, attention_snapshot_frame(entries))
        logger.info(
            "fulfill.attention_snapshot_pushed",
            user=session.user_id,
            device=session.device_id,
            entries=len(entries),
        )


async def _fulfill_stream(
    session: FulfillerSession,
    hub: FulfillerHub,
) -> AsyncIterator[str]:
    """Yield SSE frames for ``session`` until the client disconnects.

    Mirrors ``realtime._firehose``: persistent ``get`` across heartbeat windows
    (never cancelled on a mere timeout); ``ready`` inside ``try`` so disconnect
    still runs unregister.
    """
    get_task: asyncio.Task[dict | None] | None = None
    try:
        yield _format_event({"type": "ready"})
        while True:
            if get_task is None:
                get_task = asyncio.ensure_future(session.get())
            done, _ = await asyncio.wait({get_task}, timeout=_HEARTBEAT_SECONDS)
            if not done:
                yield ": keep-alive\n\n"
                continue
            event = get_task.result()
            get_task = None
            if event is None:
                return
            yield _format_event(event)
    finally:
        if get_task is not None:
            get_task.cancel()
        hub.unregister(session)


@router.get("")
async def fulfill_stream(
    user: AuthUser,
    device_id: Annotated[str, Query(min_length=1, max_length=128)],
    session: AsyncSession = Depends(get_db),
    caps: Annotated[str, Query()] = "",
    roots: Annotated[str, Query()] = "",
    x_client_platform: Annotated[str | None, Header(alias="X-Client-Platform")] = None,
) -> StreamingResponse:
    """Open this device's fulfillment channel (server→client SSE).

    Query params: ``device_id`` (required), ``caps`` (comma-separated channel
    names), ``roots`` (the device's permanent authorized roots, may be empty).
    Platform comes from ``X-Client-Platform``. Conversation grants bound to this
    device are added from storage — the client does not re-declare them.
    """
    cap_set = _parse_caps(caps)
    root_list = _parse_csv(roots)
    root_list.extend(await _bound_grant_roots(session, user.user_id, device_id))
    running_ids = await _running_conversation_ids_for_seed(session, user.user_id)
    attention_entries = await _attention_entries_for_seed(session, user.user_id)
    await release_request_db_before_sse(session)

    hub = default_fulfiller_hub()
    fulfiller = hub.register(
        user.user_id,
        device_id,
        caps=cap_set,
        roots=root_list,
        platform=x_client_platform,
    )
    _seed_registered_session(
        fulfiller,
        hub,
        running_conversation_ids=running_ids,
        attention_entries=attention_entries,
    )

    return StreamingResponse(
        _fulfill_stream(fulfiller, hub),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

