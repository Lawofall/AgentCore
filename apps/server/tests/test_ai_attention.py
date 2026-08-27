"""「AI 停住在等你」的账号级信号 (云对话多端同权 B2 · L1).

Covers the contract the mobile / desktop clients code against — the ``ai_attention``
firehose body, the required→resolved pair for hot cards, and the push fallback's
**per-surface** dedupe. That last one carries the刀's whole point: a desktop being
online says nothing about the phone in the user's pocket, so an open desktop must
never swallow the push.

Real ``ChatHub`` (the actual wire), faked push transport. No DB, no HTTP.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentcore.attention import (
    ATTENTION_EVENT_TYPE,
    TITLE_MAX_CHARS,
    AttentionKind,
    attention_kind_of,
    attention_title,
    bind_attention_scope,
    reset_attention_scope,
    signal_attention_required,
    signal_attention_resolved,
)
from agentcore.attention import signal as signal_mod
from agentcore.messaging.hub import ChatHub, Subscription
from agentcore.push.sender import PushNotification
from agentcore.runtime.interaction import InteractionKind, InteractionRegistry
from tests.conftest import LogSpy

ATTENTION_KEYS = {
    "type",
    "state",
    "conversation_id",
    "turn_id",
    "interaction_id",
    "kind",
    "title",
}


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> ChatHub:
    """A private hub standing in for the process-wide firehose."""
    fresh = ChatHub()
    monkeypatch.setattr("agentcore.messaging.hub.default_chat_hub", lambda: fresh)
    return fresh


@pytest.fixture
def pushes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, PushNotification]]:
    """Captured native pushes (the transport itself is never exercised).

    Answers 1 like the real ``notify_user`` does when a device takes the notification —
    a double that returned nothing would silently pin every ``pushed`` flag to false.
    """
    sent: list[tuple[str, PushNotification]] = []

    async def _capture(user_id: str, notification: PushNotification) -> int:
        sent.append((user_id, notification))
        return 1

    monkeypatch.setattr("agentcore.push.notify_user", _capture)
    return sent


def _drain(sub: Subscription) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    while True:
        try:
            event = sub._queue.get_nowait()  # noqa: SLF001 — test drain
        except asyncio.QueueEmpty:
            return events
        if event is not None:
            events.append(event)


# --- taxonomy + headline -----------------------------------------------------


def test_waiting_kinds_map_to_an_attention_kind():
    """Progress surfaces must not signal; pending questions now wait on the user."""
    for waiting in (
        "approval",
        "escalation",
        "ask_user",
        "plan_review",
    ):
        assert attention_kind_of(waiting) is AttentionKind(waiting)

    for progress in ("client_tool", "stage_card", "nonsense"):
        assert attention_kind_of(progress) is None


def test_attention_kinds_stay_aligned_with_the_interaction_wire():
    """The signal is keyed by the interaction / suspension kind string — no aliasing."""
    from agentcore.runtime.suspension import DURABLE_INTERACTION_KINDS

    for kind in AttentionKind:
        assert InteractionKind(kind.value).value == kind.value

    # Every durable (cold) pause kind is a blocking card.
    for durable in DURABLE_INTERACTION_KINDS:
        assert attention_kind_of(durable.value) is not None


def test_title_prefers_what_the_card_asks():
    assert (
        attention_title(AttentionKind.APPROVAL, {"tool_name": "file_write"})
        == "需要授权：file_write"
    )
    assert (
        attention_title(AttentionKind.ESCALATION, {"question": "用 A 方案还是 B 方案？"})
        == "用 A 方案还是 B 方案？"
    )


def test_title_falls_back_per_kind_and_stays_bounded():
    assert attention_title(AttentionKind.PLAN_REVIEW, {}) == "AI 计划待你确认"
    assert attention_title(AttentionKind.APPROVAL, None) == "AI 需要你的授权"

    long_question = "问" * 500
    title = attention_title(AttentionKind.ASK_USER, {"question": long_question})
    assert len(title) == TITLE_MAX_CHARS


# --- firehose body -----------------------------------------------------------


async def test_required_publishes_signal_without_card_content(hub: ChatHub, pushes):
    sub = hub.subscribe("u1", device_id="d1", platform="desktop")

    await signal_attention_required(
        user_id="u1",
        conversation_id="conv-1",
        turn_id="turn-1",
        interaction_id="appr-1",
        kind=AttentionKind.APPROVAL,
        title="需要授权：file_write",
        push=False,
    )

    assert _drain(sub) == [
        {
            "type": ATTENTION_EVENT_TYPE,
            "state": "required",
            "conversation_id": "conv-1",
            "turn_id": "turn-1",
            "interaction_id": "appr-1",
            "kind": "approval",
            "title": "需要授权：file_write",
        }
    ]


async def test_resolved_publishes_and_never_pushes(hub: ChatHub, pushes):
    sub = hub.subscribe("u1", device_id="d1", platform="desktop")

    await signal_attention_resolved(
        user_id="u1",
        conversation_id="conv-1",
        turn_id="turn-1",
        interaction_id="appr-1",
        kind=AttentionKind.APPROVAL,
    )

    (event,) = _drain(sub)
    assert event["state"] == "resolved"
    assert set(event) == ATTENTION_KEYS
    assert pushes == []


async def test_signal_is_a_noop_without_an_addressee(hub: ChatHub, pushes):
    sub = hub.subscribe("u1", device_id="d1")
    await signal_attention_required(
        user_id="",
        conversation_id="conv-1",
        turn_id="turn-1",
        interaction_id="appr-1",
        kind=AttentionKind.APPROVAL,
        title="x",
        push=True,
    )
    assert _drain(sub) == []
    assert pushes == []


async def test_publish_failure_never_reaches_the_turn(
    monkeypatch: pytest.MonkeyPatch, hub: ChatHub, pushes
):
    async def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("firehose down")

    monkeypatch.setattr(hub, "publish", _boom)

    await signal_attention_required(
        user_id="u1",
        conversation_id="conv-1",
        turn_id="turn-1",
        interaction_id="appr-1",
        kind=AttentionKind.APPROVAL,
        title="x",
        push=True,
    )
    # Swallowed, and the push behind it is skipped rather than half-sent.
    assert pushes == []


# --- push fallback + per-surface dedupe --------------------------------------


async def test_push_fires_when_nothing_is_listening(hub: ChatHub, pushes):
    await signal_attention_required(
        user_id="u1",
        conversation_id="conv-1",
        turn_id="turn-1",
        interaction_id="appr-1",
        kind=AttentionKind.APPROVAL,
        title="需要授权：file_write",
        push=True,
    )

    (user_id, notification) = pushes[0]
    assert user_id == "u1"
    assert notification.title == "AI 需要你的授权"
    assert notification.body == "需要授权：file_write"
    assert notification.data == {
        "conversation_id": "conv-1",
        "message_id": "turn-1",
        "interaction_id": "appr-1",
        "kind": "approval",
    }


async def test_open_desktop_does_not_swallow_the_push(hub: ChatHub, pushes):
    """本刀核心验收：桌面开着 ≠ 人带着的手机在线。

    ``mobile-web`` is in here on purpose: the browser is on a phone, but the push
    goes to an FCM token only the native build registers, so an open mobile-web
    tab is not evidence that the push target is reachable.
    """
    hub.subscribe("u1", device_id="desk-1", platform="desktop")
    hub.subscribe("u1", device_id="web-1", platform="web")
    hub.subscribe("u1", device_id="mweb-1", platform="mobile-web")
    hub.subscribe("u1", device_id="anon-1")  # undeclared surface

    await signal_attention_required(
        user_id="u1",
        conversation_id="conv-1",
        turn_id="turn-1",
        interaction_id="appr-1",
        kind=AttentionKind.APPROVAL,
        title="需要授权：file_write",
        push=True,
    )

    assert len(pushes) == 1


@pytest.mark.parametrize("platform", ["mobile", "android", "ios"])
async def test_live_mobile_firehose_suppresses_the_push(
    hub: ChatHub, pushes, platform: str
):
    hub.subscribe("u1", device_id="desk-1", platform="desktop")
    hub.subscribe("u1", device_id="phone-1", platform=platform)

    await signal_attention_required(
        user_id="u1",
        conversation_id="conv-1",
        turn_id="turn-1",
        interaction_id="appr-1",
        kind=AttentionKind.APPROVAL,
        title="需要授权：file_write",
        push=True,
    )

    assert pushes == []


async def test_push_resumes_once_the_phone_disconnects(hub: ChatHub, pushes):
    phone = hub.subscribe("u1", device_id="phone-1", platform="android")

    async def _signal() -> None:
        await signal_attention_required(
            user_id="u1",
            conversation_id="conv-1",
            turn_id="turn-1",
            interaction_id="appr-1",
            kind=AttentionKind.APPROVAL,
            title="x",
            push=True,
        )

    await _signal()
    assert pushes == []

    hub.unsubscribe(phone)
    await _signal()
    assert len(pushes) == 1


async def test_cold_card_signal_leaves_its_own_push_alone(hub: ChatHub, pushes):
    """Durable pauses keep their existing notification — the signal must not double it."""
    await signal_attention_required(
        user_id="u1",
        conversation_id="conv-1",
        turn_id="turn-1",
        interaction_id="cp-1",
        kind=AttentionKind.PLAN_REVIEW,
        title="AI 计划待你确认",
        push=False,
    )
    assert pushes == []


# --- what the signal log claims about the push -------------------------------


def _signal_spy(monkeypatch: pytest.MonkeyPatch) -> LogSpy:
    spy = LogSpy()
    monkeypatch.setattr(signal_mod, "logger", spy)
    return spy


async def _required(**over: Any) -> None:
    kwargs: dict[str, Any] = {
        "user_id": "u1",
        "conversation_id": "conv-1",
        "turn_id": "turn-1",
        "interaction_id": "appr-1",
        "kind": AttentionKind.APPROVAL,
        "title": "需要授权：file_write",
        "push": True,
    }
    kwargs.update(over)
    await signal_attention_required(**kwargs)


@pytest.mark.parametrize(
    ("devices_reached", "pushed", "outcome"),
    [(1, True, "delivered"), (0, False, "undelivered")],
)
async def test_signalled_reports_the_push_that_actually_landed(
    monkeypatch: pytest.MonkeyPatch,
    hub: ChatHub,
    devices_reached: int,
    pushed: bool,
    outcome: str,
):
    """``pushed`` answers「手机响了吗」, not「我调过 notify_user 吗」.

    With push unconfigured — production's default to this day — ``notify_user`` reaches
    zero devices, and the flag used to say true anyway: the first field a 真机 bring-up
    reads, lying in precisely the case it exists for.
    """

    async def _notify(_user_id: str, _notification: PushNotification) -> int:
        return devices_reached

    monkeypatch.setattr("agentcore.push.notify_user", _notify)
    spy = _signal_spy(monkeypatch)

    await _required()

    signalled = spy.get("attention.signalled")
    assert signalled["pushed"] is pushed
    assert signalled["push_outcome"] == outcome


async def test_a_live_phone_is_a_suppressed_push_not_a_failed_one(
    monkeypatch: pytest.MonkeyPatch, hub: ChatHub, pushes
):
    """Deliberate silence and broken transport are both ``pushed=false`` — only the
    outcome tells the operator which one they are looking at."""
    hub.subscribe("u1", device_id="phone-1", platform="android")
    spy = _signal_spy(monkeypatch)

    await _required()

    signalled = spy.get("attention.signalled")
    assert signalled["pushed"] is False
    assert signalled["push_outcome"] == "skipped_mobile_online"
    assert pushes == []


async def test_a_card_that_notifies_for_itself_reads_as_not_requested(
    monkeypatch: pytest.MonkeyPatch, hub: ChatHub, pushes
):
    spy = _signal_spy(monkeypatch)

    await _required(kind=AttentionKind.PLAN_REVIEW, push=False)

    signalled = spy.get("attention.signalled")
    assert signalled["pushed"] is False
    assert signalled["push_outcome"] == "not_requested"
    assert pushes == []


async def test_resolved_never_claims_a_push(monkeypatch: pytest.MonkeyPatch, hub: ChatHub):
    spy = _signal_spy(monkeypatch)

    await signal_attention_resolved(
        user_id="u1",
        conversation_id="conv-1",
        turn_id="turn-1",
        interaction_id="appr-1",
        kind=AttentionKind.APPROVAL,
    )

    signalled = spy.get("attention.signalled")
    assert signalled["pushed"] is False
    assert signalled["push_outcome"] == "not_requested"


# --- hot cards through the interaction bridge --------------------------------


async def _settle(registry: InteractionRegistry, request_id: str, conversation_id: str):
    for _ in range(200):
        if registry.resolve(request_id, "ok", conversation_id=conversation_id):
            return
        await asyncio.sleep(0)
    raise AssertionError(f"{request_id} never became resolvable")


async def _flush_signals() -> None:
    """Let the fire-and-forget signal tasks run to completion."""
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.fixture
def scope():
    token = bind_attention_scope(
        user_id="u1", conversation_id="conv-1", turn_id="turn-1"
    )
    yield
    reset_attention_scope(token)


async def test_hot_approval_signals_required_then_resolved(hub: ChatHub, pushes, scope):
    sub = hub.subscribe("u1", device_id="desk-1", platform="desktop")
    registry = InteractionRegistry()

    suspended = asyncio.create_task(
        registry.suspend(
            "appr-1",
            "conv-1",
            kind=InteractionKind.APPROVAL,
            payload={"tool_call_id": "tc-1", "tool_name": "file_write"},
            timeout=5.0,
        )
    )
    await _settle(registry, "appr-1", "conv-1")
    await suspended
    await _flush_signals()

    required, resolved = _drain(sub)
    assert required == {
        "type": ATTENTION_EVENT_TYPE,
        "state": "required",
        "conversation_id": "conv-1",
        "turn_id": "turn-1",
        "interaction_id": "appr-1",
        "kind": "approval",
        "title": "需要授权：file_write",
    }
    assert resolved["state"] == "resolved"
    assert resolved["interaction_id"] == "appr-1"
    # No phone listening → the hot card chases the user natively.
    assert len(pushes) == 1


async def test_hot_card_signals_resolved_on_timeout(hub: ChatHub, pushes, scope):
    sub = hub.subscribe("u1", device_id="desk-1", platform="desktop")
    registry = InteractionRegistry()

    with pytest.raises(TimeoutError):
        await registry.suspend(
            "appr-timeout",
            "conv-1",
            kind=InteractionKind.APPROVAL,
            payload={"tool_name": "code_execute"},
            timeout=0.01,
        )
    await _flush_signals()

    states = [e["state"] for e in _drain(sub)]
    assert states == ["required", "resolved"]


async def test_client_tool_suspend_signals_nothing(hub: ChatHub, pushes, scope):
    """A device fulfilling an op is not a human being waited on."""
    sub = hub.subscribe("u1", device_id="desk-1", platform="desktop")
    registry = InteractionRegistry()

    suspended = asyncio.create_task(
        registry.suspend(
            "ct-1",
            "conv-1",
            kind=InteractionKind.CLIENT_TOOL,
            payload={"op": "read_file"},
            timeout=5.0,
        )
    )
    await _settle(registry, "ct-1", "conv-1")
    await suspended
    await _flush_signals()

    assert _drain(sub) == []
    assert pushes == []


async def test_ceo_arbitrated_escalation_signals_nothing(hub: ChatHub, pushes, scope):
    """``awaiting=ceo`` is the team talking to itself — the user is not blocked."""
    sub = hub.subscribe("u1", device_id="desk-1", platform="desktop")
    registry = InteractionRegistry()

    suspended = asyncio.create_task(
        registry.suspend(
            "esc-ceo",
            "conv-1",
            kind=InteractionKind.ESCALATION,
            payload={"question": "选 A 还是 B", "awaiting": "ceo"},
            timeout=5.0,
        )
    )
    await _settle(registry, "esc-ceo", "conv-1")
    await suspended
    await _flush_signals()

    assert _drain(sub) == []
    assert pushes == []


async def test_user_facing_escalation_signals_with_its_question(hub: ChatHub, pushes, scope):
    sub = hub.subscribe("u1", device_id="desk-1", platform="desktop")
    registry = InteractionRegistry()

    suspended = asyncio.create_task(
        registry.suspend(
            "esc-user",
            "conv-1",
            kind=InteractionKind.ESCALATION,
            payload={"question": "线上库要不要直接改？", "awaiting": "user"},
            timeout=5.0,
        )
    )
    await _settle(registry, "esc-user", "conv-1")
    await suspended
    await _flush_signals()

    required, resolved = _drain(sub)
    assert required["kind"] == "escalation"
    assert required["title"] == "线上库要不要直接改？"
    assert resolved["state"] == "resolved"


async def test_hot_card_outside_a_turn_signals_nothing(hub: ChatHub, pushes):
    """No bound scope ⇒ no addressee ⇒ silence, not a guess."""
    sub = hub.subscribe("u1", device_id="desk-1", platform="desktop")
    registry = InteractionRegistry()

    suspended = asyncio.create_task(
        registry.suspend(
            "appr-orphan",
            "conv-1",
            kind=InteractionKind.APPROVAL,
            payload={"tool_name": "file_write"},
            timeout=5.0,
        )
    )
    await _settle(registry, "appr-orphan", "conv-1")
    await suspended
    await _flush_signals()

    assert _drain(sub) == []
    assert pushes == []


# --- cold cards (durable pause frames) ---------------------------------------


def _ask_user_frame():
    from agentcore.runtime.suspension import AskUserSuspension

    return AskUserSuspension(
        message_id="turn-cold",
        conversation_id="conv-cold",
        user_id="u1",
        captain_run_id="cap-1",
        checkpoint_id="cp-1",
        tool_call_id="tc-1",
        base_system_prompt="",
        user_message="帮我整理",
        question="要按项目分还是按时间分？",
    )


async def test_cold_pause_signals_required_from_the_frame(hub: ChatHub, pushes):
    from agentcore.runtime.suspension.persistence import _signal_pause_attention

    sub = hub.subscribe("u1", device_id="desk-1", platform="desktop")

    await _signal_pause_attention(_ask_user_frame())

    assert _drain(sub) == [
        {
            "type": ATTENTION_EVENT_TYPE,
            "state": "required",
            "conversation_id": "conv-cold",
            "turn_id": "turn-cold",
            "interaction_id": "cp-1",
            "kind": "ask_user",
            "title": "要按项目分还是按时间分？",
        }
    ]
    # The durable pause runs its own push (notify_user) — this signal adds none.
    assert pushes == []


async def test_settled_frame_signals_resolved(hub: ChatHub, pushes):
    from agentcore.runtime.suspension.persistence import _signal_frame_resolved

    sub = hub.subscribe("u1", device_id="desk-1", platform="desktop")
    frame = _ask_user_frame().to_json()

    await _signal_frame_resolved(
        user_id="u1",
        conversation_id="conv-cold",
        frame=frame,
        message_id="turn-cold",
    )

    (event,) = _drain(sub)
    assert event["state"] == "resolved"
    assert event["kind"] == "ask_user"
    assert event["interaction_id"] == "cp-1"
    assert pushes == []


async def test_unreadable_frame_signals_nothing(hub: ChatHub, pushes):
    from agentcore.runtime.suspension.persistence import _signal_frame_resolved

    sub = hub.subscribe("u1", device_id="desk-1", platform="desktop")

    await _signal_frame_resolved(
        user_id="u1", conversation_id="c", frame=None, message_id="m"
    )
    await _signal_frame_resolved(
        user_id="u1", conversation_id="c", frame={"kind": "junk"}, message_id="m"
    )

    assert _drain(sub) == []
