"""Team cancel/interrupt close wording — four structural kinds, no user-prose scan."""

from __future__ import annotations

from agentcore.runtime.coordination.cancel_close import (
    CEO_CANCEL_STARTED_MARK,
    DRIVE_INTERRUPTED_MARK,
    FORBID_NEVER_STARTED,
    NOT_STARTED_ALLOWED,
    NOT_STARTED_MARK,
    USER_STOPPED_MARK,
    classify_cancel_close,
)
from agentcore.runtime.coordination.inject import format_coordination_events
from agentcore.runtime.coordination.session import (
    CoordinationEvent,
    CoordinationEventKind,
    CoordinationSession,
    CoordinationSnapshot,
)


def _user_stopped_session() -> CoordinationSession:
    session = CoordinationSession(execution_id="c-stop", total_workers=2)
    session.user_stopped = True
    session._worker_started_at["w1"] = 1.0
    session._pending.append(
        CoordinationEvent(
            kind=CoordinationEventKind.DRIVE_CANCELLED,
            payload={"completed": 1, "total": 2},
        )
    )
    return session


def _drive_cancelled_session() -> CoordinationSession:
    session = CoordinationSession(execution_id="c-drive", total_workers=2)
    session._worker_started_at["w1"] = 1.0
    session._pending.append(
        CoordinationEvent(
            kind=CoordinationEventKind.DRIVE_CANCELLED,
            payload={"completed": 1, "total": 2},
        )
    )
    return session


def _ceo_cancel_started_session() -> CoordinationSession:
    session = CoordinationSession(execution_id="c-started", total_workers=2)
    session.ceo_cancel_worker_ids = {"w1"}
    session.ceo_cancel_started_ids = {"w1"}
    session.cancel_ids = {"w1"}
    session.completed_run_ids = {"w1"}
    session._worker_started_at["w1"] = 1.0
    return session


def _not_started_session() -> CoordinationSession:
    session = CoordinationSession(execution_id="c-queued", total_workers=2)
    session.ceo_cancel_worker_ids = {"w2"}
    session.cancel_ids = {"w2"}
    session.completed_run_ids = {"w2"}
    session.vacated_run_ids = {"w2"}
    return session


def test_classify_cancel_close_priority_and_four_kinds():
    assert classify_cancel_close(_user_stopped_session()) == "user_stopped"
    assert classify_cancel_close(_drive_cancelled_session()) == "drive_cancelled"
    assert classify_cancel_close(_ceo_cancel_started_session()) == "ceo_cancel_started"
    assert classify_cancel_close(_not_started_session()) == "not_started"

    both = _drive_cancelled_session()
    both.user_stopped = True
    assert classify_cancel_close(both) == "user_stopped"

    started_and_queued = _ceo_cancel_started_session()
    started_and_queued.ceo_cancel_worker_ids.add("w2")
    assert classify_cancel_close(started_and_queued) == "ceo_cancel_started"


def test_four_cancel_close_wordings_are_distinguishable():
    """user_stopped / DRIVE_CANCELLED / cancel_worker 已开工 / 仅排队撤出."""
    cancelled_all = [
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload={"completed": 1, "total": 2, "cancelled": True},
        )
    ]
    drive_ev = [
        CoordinationEvent(
            kind=CoordinationEventKind.DRIVE_CANCELLED,
            payload={"completed": 1, "total": 2},
        )
    ]
    sessions = {
        "user_stopped": _user_stopped_session(),
        "drive_cancelled": _drive_cancelled_session(),
        "ceo_cancel_started": _ceo_cancel_started_session(),
        "not_started": _not_started_session(),
    }
    inject = {
        "user_stopped": format_coordination_events(
            sessions["user_stopped"], cancelled_all
        ),
        "drive_cancelled": format_coordination_events(
            sessions["drive_cancelled"], drive_ev
        ),
        "ceo_cancel_started": format_coordination_events(
            sessions["ceo_cancel_started"], cancelled_all
        ),
        "not_started": format_coordination_events(
            sessions["not_started"], cancelled_all
        ),
    }
    for text in inject.values():
        assert "调度中断" not in text
        assert "团队已全部结束" not in text
        assert "all_completed：" not in text

    assert USER_STOPPED_MARK in inject["user_stopped"]
    assert DRIVE_INTERRUPTED_MARK not in inject["user_stopped"]
    assert NOT_STARTED_ALLOWED not in inject["user_stopped"]
    assert FORBID_NEVER_STARTED in inject["user_stopped"]

    assert DRIVE_INTERRUPTED_MARK in inject["drive_cancelled"]
    assert USER_STOPPED_MARK not in inject["drive_cancelled"]
    assert CEO_CANCEL_STARTED_MARK not in inject["drive_cancelled"]
    assert NOT_STARTED_ALLOWED not in inject["drive_cancelled"]
    assert FORBID_NEVER_STARTED in inject["drive_cancelled"]

    started = inject["ceo_cancel_started"]
    assert CEO_CANCEL_STARTED_MARK in started
    assert NOT_STARTED_ALLOWED not in started
    assert FORBID_NEVER_STARTED in started
    assert "没启动 / 没跑起来 / 一直未被启动" in started

    queued = inject["not_started"]
    assert NOT_STARTED_MARK in queued
    assert NOT_STARTED_ALLOWED in queued
    assert CEO_CANCEL_STARTED_MARK not in queued
    assert FORBID_NEVER_STARTED not in queued
    assert "没启动 / 没跑起来 / 一直未被启动" not in queued

    kinds = (
        "user_stopped",
        "drive_cancelled",
        "ceo_cancel_started",
        "not_started",
    )
    marks = (
        USER_STOPPED_MARK,
        DRIVE_INTERRUPTED_MARK,
        CEO_CANCEL_STARTED_MARK,
        NOT_STARTED_MARK,
    )
    for i, left in enumerate(kinds):
        for right in kinds[i + 1 :]:
            assert inject[left] != inject[right]
    for kind, mark in zip(kinds, marks, strict=True):
        owners = [k for k, text in inject.items() if mark in text]
        assert owners == [kind]


def test_started_cancel_inject_forbids_never_started_and_drops_schedule_cliche():
    session = _ceo_cancel_started_session()
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={"completed": 1, "total": 2, "cancelled": True},
            )
        ],
    )
    assert "调度中断" not in text
    assert FORBID_NEVER_STARTED in text
    assert "禁止对用户说「没启动 / 没跑起来 / 一直未被启动」" in text
    assert NOT_STARTED_ALLOWED not in text


def test_live_all_completed_without_payload_flag_uses_session_cancel_stamps():
    """Drive omits payload.cancelled; inject must still drop the success prefix."""
    session = _ceo_cancel_started_session()
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={"completed": 1, "total": 2},
            )
        ],
    )
    assert "all_completed：" not in text
    assert "team_cancelled：" in text
    assert CEO_CANCEL_STARTED_MARK in text


def test_post_session_all_completed_stamps_cancelled_on_ceo_cancel():
    from agentcore.runtime.delegate.drive_terminal import post_session_all_completed

    session = _ceo_cancel_started_session()
    posted: list[CoordinationEvent] = []
    session.post = posted.append  # type: ignore[method-assign]
    post_session_all_completed(session, output="ok")
    assert posted
    assert posted[0].kind is CoordinationEventKind.ALL_COMPLETED
    assert posted[0].payload.get("cancelled") is True


def test_success_close_copy_unchanged():
    session = CoordinationSession(execution_id="ok", total_workers=2)
    session.completed_run_ids = {"a", "b"}
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={"completed": 2, "total": 2},
            )
        ],
    )
    assert "团队已全部结束" in text
    assert "all_completed：" in text
    assert FORBID_NEVER_STARTED not in text
    assert NOT_STARTED_ALLOWED not in text


def test_cancel_worker_stamp_roundtrips_snapshot_and_old_key_missing():
    session = CoordinationSession(execution_id="snap-cw", total_workers=1)
    session.ceo_cancel_worker_ids = {"w1"}
    session.ceo_cancel_started_ids = {"w1"}
    raw = session.snapshot().to_dict()
    assert raw["ceo_cancel_worker_ids"] == ["w1"]
    assert raw["ceo_cancel_started_ids"] == ["w1"]
    restored = CoordinationSession.from_snapshot(CoordinationSnapshot.from_dict(raw))
    assert restored.ceo_cancel_worker_ids == {"w1"}
    assert restored.ceo_cancel_started_ids == {"w1"}

    legacy = CoordinationSnapshot.from_dict(
        {
            "execution_id": "legacy-cw",
            "progress_budget_remaining": 4,
            "decision_budget_remaining": 2,
        }
    )
    assert legacy is not None
    assert legacy.ceo_cancel_worker_ids == []
    assert legacy.ceo_cancel_started_ids == []
    old = CoordinationSession.from_snapshot(legacy)
    assert old.ceo_cancel_worker_ids == set()
    assert old.ceo_cancel_started_ids == set()
    assert classify_cancel_close(old) is None
