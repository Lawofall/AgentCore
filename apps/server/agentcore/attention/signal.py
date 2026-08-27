"""账号级「在等你」信号 (云对话多端同权 B2 · L1).

Two product shapes share this channel:

- **Turn stopped**: a blocking card (approval / ask_user / plan_review / …). The
  AI cannot move without the person.

Every other beat of a turn is either progress (they will see it when they look)
or completion (nothing is waiting). So this module has two triggers — a card went
up, a card came down — and two transports:

- **firehose** (``/v1/realtime``): a thin ``ai_attention`` event to every live
  connection of that user. It carries only *which* conversation needs them plus a
  ≤120-char headline; the card body is re-pulled over REST. Sending the payload
  here would make an account-wide notify channel carry conversation content
  (设计 §2.2「只送信号不送内容」).
- **fulfill** (``GET /v1/fulfill``): the same incremental ``ai_attention`` plus a
  connect-time ``ai_attention_snapshot`` replace. Realtime may still carry the
  incremental during the transition; clients replace only from the fulfill
  snapshot.
- **native push**: the last resort, only for ``required``, only when no mobile
  firehose is live. Push vibrates a pocket, so it is gated on「在等你」and
  never on progress or turn completion (设计 §8.1).

The dedupe is deliberately **per-surface, not per-account**: an open desktop says
nothing about whether the phone in the user's pocket can reach them, and treating
it as「online, skip the push」would silently defeat the whole point of this signal.

Everything here is best-effort and never raises into the turn — the same contract
``notify_user`` already keeps. A missed signal costs a badge; a raised one costs
the turn.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping
from enum import StrEnum
from typing import Any

from agentcore.attention.scope import current_attention_scope
from agentcore.core.logging import get_logger
from agentcore.runtime.interaction import ATTENTION_KINDS, spec_for_kind

logger = get_logger(__name__)

# The firehose event name. One type for both transitions — clients switch on
# ``state`` — so a new blocking card kind never needs a new event type
# (消息IM.md §四「新事件类型无需新通道」).
ATTENTION_EVENT_TYPE = "ai_attention"

# Headline budget. Long enough for a real question, short enough to stay a
# notification rather than a copy of the card.
TITLE_MAX_CHARS = 120

# ``attention.signalled.push_outcome`` — why the phone did or did not buzz. The
# ``pushed`` boolean alone cannot separate the three ways a push does not happen, and
# on a 真机 bring-up that separation is the whole diagnosis: a suppressed push is
# correct behaviour, an undelivered one is a broken transport.
PUSH_DELIVERED = "delivered"
PUSH_UNDELIVERED = "undelivered"
PUSH_SKIPPED_MOBILE_ONLINE = "skipped_mobile_online"
PUSH_NOT_REQUESTED = "not_requested"


class AttentionKind(StrEnum):
    """Card kinds that wait on the human — the 「在等你」 signal.

    Membership is :data:`~agentcore.runtime.interaction.ATTENTION_KINDS` (spec
    ``attention=True``). Blocking kinds stop the turn. Progress-only surfaces
    (``stage_card``, ``client_tool``) stay absent.
    """

    APPROVAL = "approval"
    ESCALATION = "escalation"
    ASK_USER = "ask_user"
    PLAN_REVIEW = "plan_review"


if frozenset(k.value for k in AttentionKind) != ATTENTION_KINDS:
    raise RuntimeError(
        "AttentionKind members must equal INTERACTION_KIND_SPECS where attention=True; "
        f"enum={sorted(k.value for k in AttentionKind)} "
        f"spec={sorted(ATTENTION_KINDS)}"
    )


# Per-kind headline used when the card carries no question of its own. Also the
# push notification's title line (the computed headline becomes its body).
_KIND_HEADLINE: Mapping[AttentionKind, str] = {
    AttentionKind.APPROVAL: "AI 需要你的授权",
    AttentionKind.ESCALATION: "AI 需要你的决定",
    AttentionKind.ASK_USER: "AI 需要你的回应",
    AttentionKind.PLAN_REVIEW: "AI 计划待你确认",
}

_PUSH_FALLBACK_BODY = "AI 已停下来等你处理。"


def attention_kind_of(raw: str) -> AttentionKind | None:
    """Map an interaction / suspension kind string to an 「在等你」 kind.

    ``None`` for anything that does not wait on the user (``client_tool``,
    ``stage_card``) — the caller then emits nothing. Source of truth is
    ``INTERACTION_KIND_SPECS.attention``, not a parallel enum table.
    """
    spec = spec_for_kind(raw)
    if spec is None or not spec.attention:
        return None
    try:
        return AttentionKind(raw)
    except ValueError:
        return None


def attention_title(kind: AttentionKind, payload: Mapping[str, Any] | None = None) -> str:
    """A ≤120-char headline for the badge / notification line.

    Prefers what the card actually asks (the escalation / ask_user question, the
    approval's tool name) and falls back to the kind's generic line. Never the
    card body — that is re-pulled over REST.
    """
    fields = payload or {}
    if kind is AttentionKind.APPROVAL:
        tool_name = str(fields.get("tool_name") or "").strip()
        title = f"需要授权：{tool_name}" if tool_name else ""
    else:
        title = str(fields.get("question") or "").strip()
    title = " ".join(title.split()) or _KIND_HEADLINE[kind]
    return title[:TITLE_MAX_CHARS]


def _mobile_firehose_online(user_id: str) -> bool:
    """True when a phone of this user holds a live ``/v1/realtime`` connection.

    Classification reuses ``resolve_channel_profile`` (the repo's single
    ``X-Client-Platform`` → surface map) rather than a parallel table. It is
    fail-open by construction: an absent / unknown platform is not mobile, so an
    unidentified client gets the push instead of silently swallowing it.

    That map counts ``mobile-web`` as web, and here that is the right answer even
    though the browser is on a phone: a push is delivered to FCM tokens, which
    only the native build registers (``apps/mobile/src/api/push.ts`` no-ops on
    web). A live mobile-web tab therefore says nothing about whether the app that
    would receive this push is up.
    """
    from agentcore.messaging.hub import default_chat_hub
    from agentcore.runtime.context.workspace_context import resolve_channel_profile

    return any(
        resolve_channel_profile(platform).surface == "mobile"
        for platform in default_chat_hub().online_platforms(user_id)
    )


def _attention_event(
    *,
    state: str,
    conversation_id: str,
    turn_id: str,
    interaction_id: str,
    kind: AttentionKind,
    title: str,
) -> dict[str, Any]:
    """The wire body — signal only, never the card's payload."""
    return {
        "type": ATTENTION_EVENT_TYPE,
        "state": state,
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "interaction_id": interaction_id,
        "kind": kind.value,
        "title": title,
    }


async def _publish(user_id: str, event: dict[str, Any]) -> None:
    from agentcore.messaging.hub import default_chat_hub

    await default_chat_hub().publish([user_id], event)


async def _push(
    user_id: str,
    *,
    conversation_id: str,
    turn_id: str,
    interaction_id: str,
    kind: AttentionKind,
    title: str,
) -> bool:
    """Fan a native notification out; True iff at least one device took it.

    Best-effort: ``notify_user`` swallows its own failures and answers 0, which is
    exactly what「配置了但一台都没送出去」looks like from here — so the caller can
    report the outcome instead of the intention.
    """
    from agentcore.push import PushNotification, notify_user

    headline = _KIND_HEADLINE[kind]
    fallback = _PUSH_FALLBACK_BODY
    delivered = await notify_user(
        user_id,
        PushNotification(
            title=headline,
            body=title if title != headline else fallback,
            data={
                "conversation_id": conversation_id,
                # ``message_id`` keeps the deep-link key the mobile client already
                # reads from the durable-pause push (push/notify 的既有约定).
                "message_id": turn_id,
                "interaction_id": interaction_id,
                "kind": kind.value,
            },
        ),
    )
    return bool(delivered)


async def signal_attention_required(
    *,
    user_id: str,
    conversation_id: str,
    turn_id: str,
    interaction_id: str,
    kind: AttentionKind,
    title: str,
    push: bool,
) -> None:
    """A blocking card just went up — signal every live client, then chase the phone.

    ``push=False`` for cards whose own trigger already sends a notification (the
    durable pause in ``runtime/suspension/persistence.py``), so this never
    double-notifies.
    """
    if not user_id:
        return
    pushed = False
    outcome = PUSH_NOT_REQUESTED
    try:
        event = _attention_event(
            state="required",
            conversation_id=conversation_id,
            turn_id=turn_id,
            interaction_id=interaction_id,
            kind=kind,
            title=title,
        )
        await _publish(user_id, event)
        from agentcore.fulfill.user_signal import push_attention

        push_attention(
            user_id=user_id,
            state="required",
            conversation_id=conversation_id,
            turn_id=turn_id,
            interaction_id=interaction_id,
            kind=kind.value,
            title=title,
        )
        if push:
            if _mobile_firehose_online(user_id):
                outcome = PUSH_SKIPPED_MOBILE_ONLINE
            else:
                pushed = await _push(
                    user_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    interaction_id=interaction_id,
                    kind=kind,
                    title=title,
                )
                outcome = PUSH_DELIVERED if pushed else PUSH_UNDELIVERED
    except Exception as e:  # noqa: BLE001 — a signal must never break the turn
        logger.warning(
            "attention.signal_failed",
            state="required",
            kind=kind.value,
            conversation_id=conversation_id,
            interaction_id=interaction_id,
            error=str(e),
        )
        return
    logger.info(
        "attention.signalled",
        state="required",
        kind=kind.value,
        conversation_id=conversation_id,
        interaction_id=interaction_id,
        pushed=pushed,
        push_outcome=outcome,
    )


async def signal_attention_resolved(
    *,
    user_id: str,
    conversation_id: str,
    turn_id: str,
    interaction_id: str,
    kind: AttentionKind,
    title: str = "",
) -> None:
    """The card came down — clear the badge on every端, whichever one settled it.

    Fires for a timeout / orphan / stop too: those also mean nothing is waiting on
    the user any more, which is exactly what the badge tracks. Never pushes — a
    notification for「你已经不用管了」is pure noise.
    """
    if not user_id:
        return
    try:
        resolved_title = title or _KIND_HEADLINE[kind]
        event = _attention_event(
            state="resolved",
            conversation_id=conversation_id,
            turn_id=turn_id,
            interaction_id=interaction_id,
            kind=kind,
            title=resolved_title,
        )
        await _publish(user_id, event)
        from agentcore.fulfill.user_signal import push_attention

        push_attention(
            user_id=user_id,
            state="resolved",
            conversation_id=conversation_id,
            turn_id=turn_id,
            interaction_id=interaction_id,
            kind=kind.value,
            title=resolved_title,
        )
    except Exception as e:  # noqa: BLE001 — a signal must never break the turn
        logger.warning(
            "attention.signal_failed",
            state="resolved",
            kind=kind.value,
            conversation_id=conversation_id,
            interaction_id=interaction_id,
            error=str(e),
        )
        return
    logger.info(
        "attention.signalled",
        state="resolved",
        kind=kind.value,
        conversation_id=conversation_id,
        interaction_id=interaction_id,
        pushed=False,
        push_outcome=PUSH_NOT_REQUESTED,
    )


# Strong references to in-flight fire-and-forget signals: the event loop only
# holds a weak one, so a task dropped here could be collected mid-await.
_inflight: set[asyncio.Task[None]] = set()


def schedule_attention(coro: Coroutine[Any, Any, None]) -> None:
    """Run an attention signal off the caller's critical path.

    The hot-card triggers sit inside the engine's suspend/settle path — one of
    them in a ``finally`` that also runs under cancellation, where awaiting is not
    an option. Outside a running loop the coroutine is closed rather than leaked.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return
    task = loop.create_task(coro)
    _inflight.add(task)
    task.add_done_callback(_inflight.discard)


def signal_hot_card_required(
    *,
    interaction_id: str,
    kind: AttentionKind,
    conversation_id: str,
    payload: Mapping[str, Any] | None = None,
) -> None:
    """Fire-and-forget「card up」for an in-process (hot) blocking card.

    Addressee comes from the ambient
    :data:`~agentcore.attention.scope.current_attention_scope` (the engine faces
    hold no ``user_id``); no scope ⇒ no addressee ⇒ no signal. The
    ``conversation_id`` the face suspended under wins over the scope's, so a card
    can never be advertised against the wrong conversation.
    """
    scope = current_attention_scope.get()
    if scope is None or not scope.user_id:
        return
    schedule_attention(
        signal_attention_required(
            user_id=scope.user_id,
            conversation_id=conversation_id or scope.conversation_id,
            turn_id=scope.turn_id,
            interaction_id=interaction_id,
            kind=kind,
            title=attention_title(kind, payload),
            push=True,
        )
    )


def signal_hot_card_resolved(
    *,
    interaction_id: str,
    kind: AttentionKind,
    conversation_id: str,
    payload: Mapping[str, Any] | None = None,
) -> None:
    """Fire-and-forget「card down」for an in-process (hot) blocking card."""
    scope = current_attention_scope.get()
    if scope is None or not scope.user_id:
        return
    schedule_attention(
        signal_attention_resolved(
            user_id=scope.user_id,
            conversation_id=conversation_id or scope.conversation_id,
            turn_id=scope.turn_id,
            interaction_id=interaction_id,
            kind=kind,
            title=attention_title(kind, payload),
        )
    )
