"""Unified interaction primitive — the one suspend-resume bridge (§8.2 / §8.6).

Hot-path kinds (approval / escalation / client_tool) share
ONE in-process :class:`InteractionRegistry`: the engine task awaits an
:class:`asyncio.Future`; a separate HTTP request (the unified resolve endpoint) settles
it. Cold-path kinds (``ask_user`` / ``plan_review``) do **not** await
here — they finalize the turn onto a durable frame and continue via ``POST .../resume``.
``stage_card`` is a journaled surface without a bridge Future.

This is the §8.6 **ClientRequestBridge** port (Protocol in ``runtime/ports.py``):
one pending registry → one ``list_pending`` → one resolve endpoint for hot-path kinds.
Per-kind differences (the typed result; whether the exchange is journaled) stay in the
thin typed faces: :class:`~agentcore.runtime.approvals.ApprovalGate`,
:class:`~agentcore.tools.builtin.ask_user.AskUserTool` (cold resume, no registry),
:class:`~agentcore.workspace.channel.WorkspaceChannel`.

State is in-process (single-worker posture, like the rate limiter — see
``config.py``); front with Redis to scale to multiple workers. Each request is
tagged with its ``conversation_id`` so a resolve aimed at another conversation is
refused (defense in depth on top of the route's ownership check — the request id is
otherwise the only key).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentcore.attention import AttentionKind


class InteractionKind(StrEnum):
    """The kinds of suspend point that share the bridge.

    User-facing decision-card kinds (approval / ask / checkpoint / …) also appear in
    :data:`INTERACTION_KIND_SPECS` — that table is the wire-contract single source
    dumped by ``scripts/dump_interaction_kinds.py`` (``pnpm gen:types``).
    ``CLIENT_TOOL`` is bridge-only (workspace / board ops) and is intentionally
    absent from the user-facing wire table.
    """

    APPROVAL = "approval"  # GRANTABLE tool gate → result: ApprovalDecision
    ASK_USER = "ask_user"  # CEO checkpoint → result: CheckpointResponse
    CLIENT_TOOL = "client_tool"  # desktop workspace op → result: envelope dict
    # DAG structured checkpoint (结构化挂起 2a): the WaveScheduler paused after a
    # ``checkpoint_after`` step → result: CheckpointResponse (continue / stop).
    PLAN_REVIEW = "plan_review"
    # 阻塞式求决策 (escalate blocking=true): a delegated worker hit a「猜错就作废」fork and
    # suspended. Classic path asks the user directly; coordination-active path awaits CEO
    # ``resolve_escalation`` (awaiting=ceo, not user-answerable) →
    # result: ``{answer | use_assumption}``.
    # Unlike the halting gates above, this does NOT pause the turn — siblings keep running
    # and a timeout degrades to the worker's stated assumption (设计: 06-规划/阻塞式求决策设计).
    ESCALATION = "escalation"
    # leftover 阶段推进卡：kind 仍在 journal / 时间线；热路 resolve 一律 410。
    # 开辩须用户在对话里点名。不挂起幕 1，不占 bridge Future。
    STAGE_CARD = "stage_card"


@dataclass(frozen=True, slots=True)
class InteractionKindSpec:
    """Complete declaration of one user-facing interaction form.

    Wire triple (``required_event`` / ``resolved_event`` / ``id_field``) must stay
    aligned with ``EventType`` + payload models. Behavior flags are the single
    source for hot-path / gate / recovery / journal-surface / attention subsets —
    consumers derive frozensets from this table instead of hand-copying kind names.
    Dumped by ``scripts/dump_interaction_kinds.py`` (``pnpm gen:types``).
    """

    required_event: str
    resolved_event: str | None
    id_field: str
    # In-process Future on :class:`InteractionRegistry` (resolve via HTTP while
    # the engine task awaits). Not the same as ``pauses_turn``: escalation is hot
    # but siblings keep running.
    hot: bool
    # Pending card pauses the host turn in ProjectedTurn (GATE). Cold-path kinds
    # that persist to ``paused_turns`` are ``pauses_turn and not hot``.
    pauses_turn: bool
    # ``GET …/recovery`` pending subset: user can answer after reload / reconnect.
    reconnect_answerable: bool
    # ``required_event`` counts as a journal surface (reload must keep events).
    journal_surface: bool
    # Maps to the account-level 「在等你」 signal. Progress-only kinds stay false.
    attention: bool


# User-facing decision / ask kinds → wire shape + behavior. ``CLIENT_TOOL`` excluded
# (bridge-only workspace / board ops; no user-facing card).
INTERACTION_KIND_SPECS: Mapping[InteractionKind, InteractionKindSpec] = {
    InteractionKind.APPROVAL: InteractionKindSpec(
        "approval_required",
        "approval_resolved",
        "approval_id",
        hot=True,
        pauses_turn=True,
        reconnect_answerable=True,
        journal_surface=True,
        attention=True,
    ),
    InteractionKind.ESCALATION: InteractionKindSpec(
        "escalation_required",
        "escalation_resolved",
        "escalation_id",
        hot=True,
        pauses_turn=False,
        reconnect_answerable=True,
        journal_surface=True,
        attention=True,
    ),
    InteractionKind.ASK_USER: InteractionKindSpec(
        "checkpoint_required",
        "checkpoint_resolved",
        "checkpoint_id",
        hot=False,
        pauses_turn=True,
        reconnect_answerable=False,
        journal_surface=True,
        attention=True,
    ),
    InteractionKind.PLAN_REVIEW: InteractionKindSpec(
        "plan_review_required",
        "plan_review_resolved",
        "checkpoint_id",
        hot=False,
        pauses_turn=True,
        reconnect_answerable=False,
        journal_surface=True,
        attention=True,
    ),
    InteractionKind.STAGE_CARD: InteractionKindSpec(
        "stage_card_required",
        "stage_card_resolved",
        "stage_card_id",
        hot=False,
        pauses_turn=False,
        reconnect_answerable=True,
        journal_surface=True,
        attention=False,
    ),
}


def _kind_values(*, attr: str) -> frozenset[str]:
    return frozenset(
        kind.value for kind, spec in INTERACTION_KIND_SPECS.items() if getattr(spec, attr)
    )


HOT_KINDS: frozenset[str] = _kind_values(attr="hot")
GATE_KINDS: frozenset[str] = _kind_values(attr="pauses_turn")
RECOVERY_PENDING_KINDS: frozenset[str] = _kind_values(attr="reconnect_answerable")
ATTENTION_KINDS: frozenset[str] = _kind_values(attr="attention")
JOURNAL_SURFACE_EVENTS: frozenset[str] = frozenset(
    spec.required_event for spec in INTERACTION_KIND_SPECS.values() if spec.journal_surface
)
# Cold-path kinds that persist to ``paused_turns`` (设计 §4.7). Derived: gate that
# is not an in-process Future. :class:`~agentcore.runtime.suspension.SuspensionKind`
# mirrors this set (ratchet in ``tests/test_suspension_kind_registry.py``).
DURABLE_INTERACTION_KINDS: frozenset[InteractionKind] = frozenset(
    kind
    for kind, spec in INTERACTION_KIND_SPECS.items()
    if spec.pauses_turn and not spec.hot
)


def spec_for_kind(kind: str | InteractionKind) -> InteractionKindSpec | None:
    """Look up a user-facing spec; ``None`` for bridge-only / unknown strings."""
    if isinstance(kind, InteractionKind):
        return INTERACTION_KIND_SPECS.get(kind)
    try:
        return INTERACTION_KIND_SPECS.get(InteractionKind(kind))
    except ValueError:
        return None


def is_user_answerable(kind: str, payload: dict[str, Any] | None) -> bool:
    """False when this instance is CEO-arbitrated (``awaiting=ceo``), not a user card.

    Kind-level flags still say escalation is hot / reconnect-answerable / attention;
    the payload discriminator is the only instance filter (not a parallel kind set).
    Unknown kinds are not user-answerable.
    """
    if spec_for_kind(kind) is None:
        return False
    return (payload or {}).get("awaiting") != "ceo"


def is_hot_user_pending_kind(kind: str, payload: dict[str, Any] | None) -> bool:
    """True for hot-path kinds awaiting the user (excludes ``awaiting=ceo``).

    Also the gate for the AI attention signal (云对话多端同权 B2): a card the CEO
    arbitrates has not stopped the turn on a human, so it must not reach the user's
    firehose or their phone either.
    """
    spec = spec_for_kind(kind)
    if spec is None or not spec.hot:
        return False
    return is_user_answerable(kind, payload)


@dataclass
class InteractionRequest:
    """A suspended interaction: its identity + the Future its awaiter is blocked on.

    ``payload`` is the request body emitted to the client (kept so a future
    ``list_pending`` consumer can re-render a pending card on reconnect); ``future``
    settles with the kind-specific result the resolve endpoint delivers.
    """

    id: str
    kind: InteractionKind
    conversation_id: str
    future: asyncio.Future[Any]
    payload: dict[str, Any] = field(default_factory=dict)


class InteractionRegistry:
    """Process-wide bridge mapping a pending ``request_id`` → its awaiter's Future.

    Replaces the three per-kind registries (approval / checkpoint / workspace-op):
    the engine task ``create``s a request and awaits its Future; the resolve
    endpoint ``resolve``s it. One instance holds every kind, so there is a single
    source of pending interactions (``list_pending``) and a single resolve path —
    the §8.6 ClientRequestBridge. Bridges the engine task (producer of the request,
    consumer of the result) and the resolve HTTP request (which delivers it); both
    run in the same process / event loop in the MVP.
    """

    def __init__(self) -> None:
        self._pending: dict[str, InteractionRequest] = {}

    def create(
        self,
        request_id: str,
        conversation_id: str,
        *,
        kind: InteractionKind,
        payload: dict[str, Any] | None = None,
    ) -> asyncio.Future[Any]:
        """Register a pending interaction and return the Future to await.

        ``payload`` mirrors the ``*_required`` event body (for ``list_pending``);
        the awaiting face owns the result's type (a decision enum, the user's
        checkpoint answer, the desktop's op envelope).
        """
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = InteractionRequest(
            id=request_id,
            kind=kind,
            conversation_id=conversation_id,
            future=fut,
            payload=payload or {},
        )
        return fut

    def resolve(self, request_id: str, result: Any, *, conversation_id: str) -> bool:
        """Settle a pending interaction with its (kind-specific) result.

        Returns False if the request is unknown, already settled, or belongs to a
        different conversation than the caller claims.
        """
        pending = self._pending.get(request_id)
        if pending is None or pending.future.done():
            return False
        if pending.conversation_id != conversation_id:
            return False
        pending.future.set_result(result)
        return True

    async def suspend(
        self,
        request_id: str,
        conversation_id: str,
        *,
        kind: InteractionKind,
        payload: dict[str, Any] | None = None,
        timeout: float | None,
        on_suspended: Callable[[], object] | None = None,
    ) -> Any:
        """Register a pending interaction, signal it, and await its resolution.

        The create → signal → await → discard dance every face (approval / ask_user
        / client_tool / plan_review) used to copy verbatim. ``on_suspended`` is
        invoked right AFTER the entry is registered and BEFORE the await, so a racing
        resolve always finds it — each face passes its ``*_required`` SSE emit here.
        Raises :class:`TimeoutError` when unresolved within ``timeout`` (the caller
        maps it to its kind-specific default + log + ``*_resolved`` emit, or re-raises
        a typed error). ``timeout=None`` waits indefinitely (提问确认交互统一 D2).
        The entry is ALWAYS discarded on exit — resolved, timed out, or cancelled —
        so no face can leak a pending request. Per-kind differences (the result type,
        the resolved emit, the timeout default) stay in the faces.

        For the kinds that stop the turn on a human this is also the account-level
        「需要你」boundary (云对话多端同权 B2 §2.2): the same two moments the SSE card
        appears and disappears fan an ``ai_attention`` signal to every device the
        user has, and — while the card is up and no phone is listening — a native
        push. Both are fire-and-forget: the engine must not wait on a notification,
        and the exit signal has to survive running under cancellation.
        """
        fut = self.create(request_id, conversation_id, kind=kind, payload=payload)
        if on_suspended is not None:
            on_suspended()
        card_kind = _blocking_card_kind(kind, payload)
        if card_kind is not None:
            from agentcore.attention import signal_hot_card_required

            signal_hot_card_required(
                interaction_id=request_id,
                kind=card_kind,
                conversation_id=conversation_id,
                payload=payload,
            )
        try:
            if timeout is None:
                return await fut
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self.discard(request_id)
            if card_kind is not None:
                from agentcore.attention import signal_hot_card_resolved

                signal_hot_card_resolved(
                    interaction_id=request_id,
                    kind=card_kind,
                    conversation_id=conversation_id,
                    payload=payload,
                )

    def get(self, request_id: str) -> InteractionRequest | None:
        """Look up a pending interaction (e.g. to verify its kind before resolving)."""
        return self._pending.get(request_id)

    def discard(self, request_id: str) -> None:
        """Forget a request once its awaiter is done with it."""
        self._pending.pop(request_id, None)

    def list_pending(self, conversation_id: str | None = None) -> list[InteractionRequest]:
        """All un-settled interactions, optionally scoped to one conversation."""
        items = [r for r in self._pending.values() if not r.future.done()]
        if conversation_id is not None:
            items = [r for r in items if r.conversation_id == conversation_id]
        return items


def _blocking_card_kind(
    kind: InteractionKind, payload: dict[str, Any] | None
) -> AttentionKind | None:
    """The :class:`~agentcore.attention.AttentionKind` this suspend blocks a human on.

    ``None`` when nobody is waiting on the user — ``client_tool`` is a device
    fulfilling an op, and a CEO-arbitrated escalation is the team talking to
    itself. Imported lazily: this module is imported almost everywhere, and the
    attention package pulls in the messaging hub + push transport.
    """
    from agentcore.attention import attention_kind_of

    if not is_hot_user_pending_kind(kind.value, payload):
        return None
    return attention_kind_of(kind.value)


_registry = InteractionRegistry()


def default_interaction_registry() -> InteractionRegistry:
    """The process-wide interaction registry (engine faces + resolve endpoint)."""
    return _registry
