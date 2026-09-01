"""Honest prepare / turn-start aborts for local workspace presence.

Three user-visible presence cases (message already persisted → guide regenerate, not resend):

1. No desktop fulfillment session at all (desktop offline / not connected).
2. Desktop online for ``workspace`` but does not declare this ``root_id``.
3. Another device is online, but the one that started this turn is not (pinned
   channels only — see ``fulfill/origin.py``).

Prepare-phase local IO also shares a wall-clock budget (see
``settings.prepare_local_io_budget_seconds``) so op timeouts cannot sum unbound.
Budget exhaustion uses the existing channel-unresponsive copy — that aborts
prepare, it does not mean mid-turn file tools are disconnected. The budget is
in force only inside a :func:`prepare_local_io_span`; execution-phase tool IO —
including a delegate re-probing a *target* desk — never sees it bound
(双模式工作区.md §7.7).

Mid-turn settle timeouts fail that op only. File-family retire follows fulfiller
presence (``workspace.presence``), not timeout counts.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import NoReturn

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.fulfill.origin import ORIGIN_DEVICE_OFFLINE, current_origin_device
from agentcore.workspace.limits import (
    CHANNEL_DEAD_PREPARE_ABORT,
    LOCAL_ROOT_NOT_HELD,
    is_channel_dead_detail,
    is_liveness_timeout_detail,
)
from agentcore.workspace.presence import backend_needs_workspace_fulfiller
from agentcore.workspace.protocol import WorkspaceIOError

logger = get_logger(__name__)

# Case 1 — no workspace fulfiller online for this user.
LOCAL_DESKTOP_OFFLINE = (
    "本机桌面未连接，无法访问本地工作区。"
    "请打开桌面客户端并登录后，点「重新生成」"
    "（不要再次发送，以免出现两条用户消息）。"
)

# Case 2 — ``LOCAL_ROOT_NOT_HELD``, imported from ``workspace/limits.py``: mid-turn
# delivery says the same thing when roots change after this gate ran
# (``fulfill/dispatch.py`` → ``DeliverResult.ROOT_NOT_HELD``).

# Case 3 — re-export prepare-budget / IO-hang abort (not mid-turn presence).
LOCAL_CHANNEL_DEAD = CHANNEL_DEAD_PREPARE_ABORT

# Case 4 — some other install could serve this op, but it is not the machine the
# user is working from, and local file ops must not silently change machine.
LOCAL_ORIGIN_DEVICE_OFFLINE = (
    f"{ORIGIN_DEVICE_OFFLINE}"
    "（本地工作区操作不会转投其他电脑。）"
    "回到那台电脑后，点「重新生成」（不要再次发送）。"
)

PREPARE_LOCAL_ABORT_MESSAGES: frozenset[str] = frozenset(
    {
        LOCAL_DESKTOP_OFFLINE,
        LOCAL_ROOT_NOT_HELD,
        LOCAL_CHANNEL_DEAD,
        LOCAL_ORIGIN_DEVICE_OFFLINE,
    }
)

# Monotonic deadline shared by every prepare span of one turn (turn_runner's
# baseline → prepare's probe/exists), so the phase runs on ONE clock instead of
# per-op timeouts summed. Carrier only: binding it gates nothing by itself.
_prepare_local_io_turn_deadline: ContextVar[float | None] = ContextVar(
    "prepare_local_io_turn_deadline", default=None
)
# The deadline actually IN FORCE — set only inside a prepare span. Everything
# outside a span (LLM execute, tools, cross-desk delegate probes, persist) must
# read this as unbound.
_prepare_local_io_deadline: ContextVar[float | None] = ContextVar(
    "prepare_local_io_deadline", default=None
)


def is_prepare_local_abort_message(detail: str | None) -> bool:
    """True when ``detail`` is one of the three honest prepare/presence aborts."""
    return (detail or "").strip() in PREPARE_LOCAL_ABORT_MESSAGES


def raise_if_local_workspace_fulfiller_absent(
    *,
    user_id: str,
    backend: object | None,
) -> None:
    """Millisecond presence gate: local channel turns need a workspace fulfiller.

    Distinguishes desktop-offline vs root-not-held vs origin-device-gone. No-op
    for cloud / sidecar Path-backed local backends. Raises ``WorkspaceIOError``
    with an honest message.

    Asks the hub the same question delivery will ask (origin device included),
    so the gate can never pass a turn whose first op reports having no machine
    to run on.
    """
    if not backend_needs_workspace_fulfiller(backend):
        return
    channel = getattr(backend, "_channel", None)
    root_id = (getattr(channel, "root_id", None) or "") or None
    from agentcore.fulfill.hub import default_fulfiller_hub, origin_pinned

    hub = default_fulfiller_hub()
    origin_device_id = current_origin_device()
    pinned = bool(origin_device_id) and origin_pinned("workspace", root_id=root_id)
    if hub.has_fulfiller(
        user_id,
        root_id=root_id,
        channel="workspace",
        origin_device_id=origin_device_id,
        require_origin=pinned,
    ):
        return
    if pinned and hub.has_fulfiller(user_id, root_id=root_id, channel="workspace"):
        reason = "origin_device_offline"
        message = LOCAL_ORIGIN_DEVICE_OFFLINE
    elif hub.has_fulfiller(user_id, root_id=None, channel="workspace"):
        reason = "root_not_held"
        message = LOCAL_ROOT_NOT_HELD
    else:
        reason = "desktop_offline"
        message = LOCAL_DESKTOP_OFFLINE
    logger.info(
        "chat.local_presence_gate",
        reason=reason,
        root_id=root_id,
        user=user_id,
        origin_device=origin_device_id,
    )
    raise WorkspaceIOError(message)


def backend_uses_local_channel(backend: object | None) -> bool:
    """True when ``backend`` drives a desktop workspace channel — the budget's only subject.

    Cloud backends and sidecar Path-backed local workspaces do no desktop
    round-trips, so they neither need a presence gate nor a prepare budget.
    """
    return backend_needs_workspace_fulfiller(backend)


def prepare_local_io_budget_active() -> bool:
    """True inside a prepare span with the local IO deadline in force."""
    return _prepare_local_io_deadline.get() is not None


def remaining_prepare_local_io_budget() -> float | None:
    """Seconds left on the in-force prepare budget, or ``None`` outside a span."""
    deadline = _prepare_local_io_deadline.get()
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _deadline_after(seconds: float | None) -> float:
    """Absolute monotonic deadline ``seconds`` (default: settings budget) from now."""
    budget = (
        settings.prepare_local_io_budget_seconds if seconds is None else float(seconds)
    )
    return time.monotonic() + max(0.0, budget)


@contextmanager
def prepare_local_io_budget(seconds: float | None = None) -> Iterator[None]:
    """Put a fresh prepare deadline in force for this block, whatever the backend."""
    token = _prepare_local_io_deadline.set(_deadline_after(seconds))
    try:
        yield
    finally:
        _prepare_local_io_deadline.reset(token)


@contextmanager
def prepare_local_io_span(backend: object | None) -> Iterator[None]:
    """Put the prepare budget in force for this block (desktop-channel backends only).

    Adopts the turn-wide deadline when :func:`bind_prepare_local_io_deadline`
    already ran, so baseline + prepare probes share one clock; a standalone
    prepare (tests / stage-card / workflow entries) starts its own. Leaving the
    block un-gates the budget again — execution-phase IO must never run inside a
    span, or a hung *target* desk would abort the whole turn as case 3.
    """
    if not backend_uses_local_channel(backend):
        yield
        return
    turn_deadline = _prepare_local_io_turn_deadline.get()
    token = _prepare_local_io_deadline.set(
        turn_deadline if turn_deadline is not None else _deadline_after(None)
    )
    try:
        yield
    finally:
        _prepare_local_io_deadline.reset(token)


def prepare_local_io_deadline_bound() -> bool:
    """True when this turn already carries a prepare deadline (do not restart it)."""
    return _prepare_local_io_turn_deadline.get() is not None


def bind_prepare_local_io_deadline(seconds: float | None = None) -> Token:
    """Start the turn-wide prepare deadline (turn_runner spans baseline → prepare).

    Binding alone puts nothing in force: each prepare-phase block opts in via
    :func:`prepare_local_io_span`, so the budget cannot leak into execution.
    """
    return _prepare_local_io_turn_deadline.set(_deadline_after(seconds))


def reset_prepare_local_io_deadline(token: Token) -> None:
    """Clear a deadline started by :func:`bind_prepare_local_io_deadline`."""
    _prepare_local_io_turn_deadline.reset(token)


def raise_prepare_local_channel_dead(*, reason: str, detail: str | None = None) -> NoReturn:
    """Abort prepare with the case-3 channel-dead copy (never invents retryability)."""
    logger.info(
        "chat.prepare_local_io_abort",
        reason=reason,
        detail=(detail or "")[:200] or None,
    )
    raise WorkspaceIOError(LOCAL_CHANNEL_DEAD)


def reraise_prepare_liveness_timeout(exc: BaseException) -> None:
    """If ``exc`` is a prepare-relevant hang, raise case-3; else return (caller continues).

    Used when prepare budget is active: the first liveness timeout
    (including ``probe_exec``) aborts immediately — do not wait for a second hang.
    """
    if not isinstance(exc, WorkspaceIOError):
        return
    detail = str(exc).strip()
    if is_prepare_local_abort_message(detail):
        raise exc
    if is_channel_dead_detail(detail) or is_liveness_timeout_detail(detail):
        raise_prepare_local_channel_dead(reason="liveness_timeout", detail=detail)


async def await_prepare_local_io[T](awaitable: Awaitable[T]) -> T:
    """Await ``awaitable``, capped by the prepare local IO budget when bound.

    Budget exhaustion → case-3 abort. Propagates prepare presence / channel-dead
    messages unchanged; converts other liveness ``WorkspaceIOError``s to case 3.
    """
    remaining = remaining_prepare_local_io_budget()
    try:
        if remaining is None:
            return await awaitable
        if remaining <= 0:
            raise_prepare_local_channel_dead(reason="budget_exhausted")
        return await asyncio.wait_for(awaitable, timeout=remaining)
    except TimeoutError as e:
        # asyncio.TimeoutError is an alias of TimeoutError on 3.11+.
        logger.info("chat.prepare_local_io_abort", reason="budget_exhausted", detail=None)
        raise WorkspaceIOError(LOCAL_CHANNEL_DEAD) from e
    except WorkspaceIOError as e:
        reraise_prepare_liveness_timeout(e)
        raise
