"""Sidecar JSON-RPC: deliverMessage / queue list+cancel + local FIFO starter."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import ValidationError

from agentcore.api.schemas.messages import EditQueuedTurnRequest, SendMessageRequest
from agentcore.runtime.turn.delivery import (
    DeliveryBlockedError,
    NoLiveTurnError,
    deliver_in_flight,
    deliver_queued_item_to_captain,
    delivery_json_ack,
    edit_queued_item,
    queued_turns_json,
    raise_if_delivery_blocked,
    reorder_queued_items,
)
from agentcore.runtime.turn.queue import QueuedTurn, set_queue_starter
from agentcore.runtime.turn.runs import turn_runs
from agentcore.sidecar import protocol
from agentcore.sidecar.server_pkg.turns import (
    parse_client_turn_ids,
    rpc_agent_mentions,
    rpc_table_selection,
)


async def _await_turn_runs(
    conversation_id: str,
    occupying: asyncio.Task[None],
    *,
    timeout_s: float = 30,
) -> None:
    """Wait until ``turn_runs`` matches an already-started ``_turns`` task."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        live = turn_runs.get(conversation_id)
        if live is not None and not live.task.done():
            return
        if occupying.done():
            return
        await asyncio.sleep(0.05)


class DeliveryMixin:
    """Composed into ``SidecarServer``; attributes live on the composed class."""

    _initialized: bool
    _root: object | None
    _user_id: str
    _fifo_desktop_start: dict[str, asyncio.Future[None]]

    async def _send(self, message: dict[str, Any]) -> None:
        raise NotImplementedError

    async def _reply(self, request_id: Any, result: Any) -> None:
        raise NotImplementedError

    def live_turn_task(self, conversation_id: str) -> asyncio.Task[None] | None:
        raise NotImplementedError

    def _install_local_queue_starter(self) -> None:
        """FIFO drain uses this process's pipeline+outbox, not cloud ``stream_chat``."""
        set_queue_starter(self._start_queued_sidecar_turn)

    async def _start_queued_sidecar_turn(self, conversation_id: str, item: QueuedTurn) -> None:
        """Ask desktop to ``startTurn`` (occupy first) — same path as a user send.

        Does not mint a turn id or start the engine here. Drain waits until
        desktop's ``startTurn`` has entered ``_run_turn`` so the slot is
        occupied before the next item is considered.
        """
        if not self._initialized or self._root is None:
            raise RuntimeError("sidecar queue drain requires an initialized workspace")
        ids = parse_client_turn_ids(
            {
                "userMessageId": item.user_message_id,
                "messageId": item.message_id,
                "traceId": item.trace_id,
            }
        )
        if ids is None:
            raise RuntimeError(
                "sidecar FIFO drain requires client userMessageId, messageId, and 32-hex traceId"
            )
        user_message_id, message_id, trace_id = ids
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._fifo_desktop_start[message_id] = fut
        await self._send(
            protocol.make_notification(
                "queue/needStart",
                {
                    "conversationId": conversation_id,
                    "queueId": item.queue_id,
                    "userMessageId": user_message_id,
                    "messageId": message_id,
                    "traceId": trace_id,
                    "userMessage": item.content,
                    "agentMentions": list(item.agent_mentions),
                    "attachments": list(item.attachments),
                    "tableSelection": list(item.table_selection),
                },
            )
        )
        try:
            await asyncio.wait_for(fut, timeout=15)
        except TimeoutError:
            self._fifo_desktop_start.pop(message_id, None)
            raise RuntimeError(
                "desktop did not startTurn for queued item "
                f"(queueId={item.queue_id})"
            ) from None

    async def _on_deliver_message(self, request_id: Any, params: dict[str, Any]) -> None:
        if not self._initialized or self._root is None:
            await self._send(
                protocol.make_error(
                    request_id, protocol.NOT_INITIALIZED, "initialize must be called first"
                )
            )
            return
        conversation_id = str(params.get("conversationId") or "").strip()
        if not conversation_id:
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.INVALID_PARAMS,
                    "deliverMessage requires conversationId",
                )
            )
            return
        try:
            body = SendMessageRequest.model_validate(
                {
                    "content": str(params.get("content") or ""),
                    "delivery": params.get("delivery"),
                    "attachments": _rpc_attachment_dicts(params),
                    "agent_mentions": rpc_agent_mentions(params),
                    "table_selection": rpc_table_selection(params),
                }
            )
        except ValidationError as e:
            await self._send(
                protocol.make_error(
                    request_id, protocol.INVALID_PARAMS, f"invalid deliverMessage params: {e}"
                )
            )
            return

        try:
            raise_if_delivery_blocked(conversation_id)
        except DeliveryBlockedError as blocked:
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.PENDING_INTERACTIONS,
                    "pending interactions awaiting",
                    data={
                        "code": blocked.code,
                        "pending_kinds": blocked.pending_kinds,
                    },
                )
            )
            return

        live = turn_runs.get(conversation_id)
        if live is None or live.task.done():
            occupying = self.live_turn_task(conversation_id)
            if occupying is not None and not occupying.done():
                await _await_turn_runs(conversation_id, occupying)
                live = turn_runs.get(conversation_id)
        if live is None or live.task.done():
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.NO_LIVE_TURN,
                    "no live turn",
                    data={"code": "no_live_turn"},
                )
            )
            return

        from agentcore.conversation.midflight_persist import (
            delete_midflight_user_message,
            persist_midflight_user_message,
        )

        att_dicts = [a.model_dump() for a in body.attachments]
        mention_dicts = [m.model_dump() for m in body.agent_mentions]
        umid = str(params.get("userMessageId") or "").strip() or None
        try:
            user_message_id = await persist_midflight_user_message(
                conversation_id=conversation_id,
                content=body.content,
                user_message_id=umid,
                attachments=att_dicts,
                agent_mentions=mention_dicts,
            )
        except Exception as e:
            from agentcore.core.logging import get_logger

            get_logger(__name__).exception("chat.stream_error")
            await self._send(protocol.make_error(request_id, protocol.INTERNAL_ERROR, str(e)))
            return

        try:
            delivered = await deliver_in_flight(
                conversation_id=conversation_id,
                content=body.content,
                delivery=body.delivery,
                user_id=self._user_id,
                attachments=att_dicts,
                agent_mentions=mention_dicts,
                table_selection=list(body.table_selection),
                persist_attachments_fn=None,
                wait_for_start=False,
                require_live=True,
                user_message_id=user_message_id,
                message_id=str(params.get("messageId") or "").strip() or None,
                trace_id=str(params.get("traceId") or "").strip() or None,
            )
        except NoLiveTurnError:
            await delete_midflight_user_message(conversation_id, user_message_id)
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.NO_LIVE_TURN,
                    "no live turn",
                    data={"code": "no_live_turn"},
                )
            )
            return
        except Exception:
            await delete_midflight_user_message(conversation_id, user_message_id)
            raise

        if delivered is None:
            await delete_midflight_user_message(conversation_id, user_message_id)
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.NO_LIVE_TURN,
                    "no live turn",
                    data={"code": "no_live_turn"},
                )
            )
            return
        await self._reply(request_id, delivery_json_ack(delivered))

    async def _on_cancel_queued_turn(self, request_id: Any, params: dict[str, Any]) -> None:
        if not self._initialized:
            await self._send(
                protocol.make_error(
                    request_id, protocol.NOT_INITIALIZED, "initialize must be called first"
                )
            )
            return
        conversation_id = str(params.get("conversationId") or "").strip()
        queue_id = str(params.get("queueId") or "").strip()
        if not conversation_id or not queue_id:
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.INVALID_PARAMS,
                    "cancelQueuedTurn requires conversationId and queueId",
                )
            )
            return
        from agentcore.runtime.turn.delivery import withdraw_queued_item

        item = await withdraw_queued_item(conversation_id, queue_id)
        if item is None:
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.QUEUED_TURN_NOT_FOUND,
                    "queued turn not found",
                    data={"code": "queued_turn_not_found"},
                )
            )
            return
        from agentcore.conversation.midflight_persist import delete_midflight_user_message

        await delete_midflight_user_message(conversation_id, item.user_message_id)
        await self._reply(request_id, {"ok": True, "queueId": queue_id})

    async def _on_list_queued_turns(self, request_id: Any, params: dict[str, Any]) -> None:
        if not self._initialized:
            await self._send(
                protocol.make_error(
                    request_id, protocol.NOT_INITIALIZED, "initialize must be called first"
                )
            )
            return
        conversation_id = str(params.get("conversationId") or "").strip()
        if not conversation_id:
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.INVALID_PARAMS,
                    "listQueuedTurns requires conversationId",
                )
            )
            return
        await self._reply(request_id, {"items": queued_turns_json(conversation_id)})

    async def _on_reorder_queued_turns(self, request_id: Any, params: dict[str, Any]) -> None:
        if not self._initialized:
            await self._send(
                protocol.make_error(
                    request_id, protocol.NOT_INITIALIZED, "initialize must be called first"
                )
            )
            return
        conversation_id = str(params.get("conversationId") or "").strip()
        raw_ids = params.get("queueIds")
        queue_ids = (
            [str(item).strip() for item in raw_ids if str(item).strip()]
            if isinstance(raw_ids, list)
            else []
        )
        if not conversation_id or not queue_ids:
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.INVALID_PARAMS,
                    "reorderQueuedTurns requires conversationId and queueIds",
                )
            )
            return
        if not await reorder_queued_items(conversation_id, queue_ids):
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.INVALID_PARAMS,
                    "queue ids must be the current fifo",
                    data={"code": "invalid_queue_order"},
                )
            )
            return
        await self._reply(request_id, {"ok": True})

    async def _on_deliver_queued_turn_to_captain(
        self, request_id: Any, params: dict[str, Any]
    ) -> None:
        if not self._initialized:
            await self._send(
                protocol.make_error(
                    request_id, protocol.NOT_INITIALIZED, "initialize must be called first"
                )
            )
            return
        conversation_id = str(params.get("conversationId") or "").strip()
        queue_id = str(params.get("queueId") or "").strip()
        if not conversation_id or not queue_id:
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.INVALID_PARAMS,
                    "deliverQueuedTurnToCaptain requires conversationId and queueId",
                )
            )
            return
        result = await deliver_queued_item_to_captain(conversation_id, queue_id)
        if result.status == "missing":
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.QUEUED_TURN_NOT_FOUND,
                    "queued turn not found",
                    data={"code": "queued_turn_not_found"},
                )
            )
            return
        await self._reply(
            request_id,
            {
                "status": result.status,
                "queueId": queue_id,
                **(
                    {"interjectionId": result.interjection_id}
                    if result.interjection_id
                    else {}
                ),
            },
        )

    async def _on_edit_queued_turn(self, request_id: Any, params: dict[str, Any]) -> None:
        if not self._initialized:
            await self._send(
                protocol.make_error(
                    request_id, protocol.NOT_INITIALIZED, "initialize must be called first"
                )
            )
            return
        conversation_id = str(params.get("conversationId") or "").strip()
        queue_id = str(params.get("queueId") or "").strip()
        if not conversation_id or not queue_id:
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.INVALID_PARAMS,
                    "editQueuedTurn requires conversationId and queueId",
                )
            )
            return
        try:
            body = EditQueuedTurnRequest.model_validate(
                {
                    "content": str(params.get("content") or ""),
                    "attachments": _rpc_attachment_dicts(params),
                    "agent_mentions": rpc_agent_mentions(params),
                }
            )
        except ValidationError as e:
            await self._send(
                protocol.make_error(
                    request_id, protocol.INVALID_PARAMS, f"invalid editQueuedTurn params: {e}"
                )
            )
            return
        item = await edit_queued_item(
            conversation_id,
            queue_id,
            content=body.content,
            attachments=[a.model_dump() for a in body.attachments],
            agent_mentions=[m.model_dump() for m in body.agent_mentions],
        )
        if item is None:
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.QUEUED_TURN_NOT_FOUND,
                    "queued turn not found",
                    data={"code": "queued_turn_not_found"},
                )
            )
            return
        await self._reply(request_id, {"ok": True, "queueId": queue_id})


def _rpc_attachment_dicts(params: dict[str, Any]) -> list[dict[str, Any]]:
    raw = params.get("attachments")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]
