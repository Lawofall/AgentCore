"""本回合团队状态：journal 派生核 + 无卡不扫正文。"""

from agentcore.runtime.events import (
    EventSink,
    EventType,
    FinishReason,
    message_end,
    run_plan,
    run_started,
)
from agentcore.runtime.journal.team_batch import team_batch_from_entries
from agentcore.runtime.verify import finish_guard


def _plan(eid: str, runs: list[dict], *, host: str | None = None) -> dict:
    payload: dict = {"execution_id": eid, "runs": runs}
    if host:
        payload["host_message_id"] = host
    return {"kind": "run_plan", "payload": payload}


def _worker(rid: str, kind: str = "agent") -> dict:
    return {"id": rid, "kind": kind}


def _started(rid: str, kind: str = "agent") -> dict:
    return {"kind": "run_started", "payload": {"run_id": rid, "kind": kind}}


def _done(rid: str, kind: str = "run_completed") -> dict:
    return {"kind": kind, "payload": {"run_id": rid}}


def test_empty_is_no_batch():
    assert team_batch_from_entries([]) == {"kind": "no_batch"}
    assert team_batch_from_entries(None) == {"kind": "no_batch"}


def test_plan_without_start_is_preview_no_batch():
    entries = [
        _plan("e1", [_worker("w1"), _worker("w2")]),
    ]
    assert team_batch_from_entries(entries) == {"kind": "no_batch"}


def test_plan_plus_started_is_in_flight():
    entries = [
        _plan("e1", [_worker("cap", "captain"), _worker("w1"), _worker("w2")]),
        _started("cap", "captain"),
        _started("w1"),
    ]
    assert team_batch_from_entries(entries) == {
        "kind": "in_flight",
        "worker_count": 2,
    }


def test_all_terminal_is_settled():
    entries = [
        _plan("e1", [_worker("w1"), _worker("w2")]),
        _started("w1"),
        _started("w2"),
        _done("w1"),
        _done("w2", "run_failed"),
    ]
    assert team_batch_from_entries(entries) == {
        "kind": "settled",
        "worker_count": 2,
    }


def test_delivery_status_same_execution_settles():
    entries = [
        _plan("e1", [_worker("w1"), _worker("w2")]),
        _started("w1"),
        {"kind": "delivery_status", "payload": {"execution_id": "e1", "state": "partial"}},
    ]
    assert team_batch_from_entries(entries) == {
        "kind": "settled",
        "worker_count": 2,
    }


def test_captain_not_counted():
    entries = [
        _plan("e1", [_worker("cap", "captain"), _worker("w1")]),
        _started("w1"),
        _done("w1"),
    ]
    assert team_batch_from_entries(entries) == {
        "kind": "settled",
        "worker_count": 1,
    }


def test_older_execution_workers_excluded():
    entries = [
        _plan("e0", [_worker("old1"), _worker("old2")]),
        _started("old1"),
        _done("old1"),
        _done("old2"),
        _plan("e1", [_worker("w1")]),
        _started("w1"),
    ]
    assert team_batch_from_entries(entries) == {
        "kind": "in_flight",
        "worker_count": 1,
    }


def test_host_message_id_plan_skipped_graph_append_counts():
    entries = [
        _plan("e1", [_worker("ignored")], host="host-msg"),
        {
            "kind": "graph_append",
            "payload": {
                "execution_id": "e1",
                "added_run_ids": ["w1", "w2"],
            },
        },
        _started("w1"),
        _started("w2"),
    ]
    assert team_batch_from_entries(entries) == {
        "kind": "in_flight",
        "worker_count": 2,
    }


def test_foreign_delivery_status_does_not_settle_preview():
    entries = [
        _plan("e2", [_worker("w1")]),
        {"kind": "delivery_status", "payload": {"execution_id": "e1", "state": "delivered"}},
    ]
    assert team_batch_from_entries(entries) == {"kind": "no_batch"}


def test_sink_stamps_no_batch_on_message_end():
    sink = EventSink()
    ev = message_end(FinishReason.END_TURN)
    sink.emit(ev)
    assert ev.payload["team_batch"] == {"kind": "no_batch"}
    close = next(e for e in sink.history_snapshot() if e.type is EventType.MESSAGE_END)
    assert close.payload["team_batch"] == {"kind": "no_batch"}


def test_sink_stamps_in_flight_from_journal():
    sink = EventSink()
    sink.emit(
        run_plan(
            execution_id="e1",
            plan_type="multi_agent",
            task_summary="t",
            agents=[{"id": "a1", "role": "研究员"}],
            runs=[
                {"id": "cap", "kind": "captain", "agent_id": "ceo"},
                {"id": "w1", "kind": "agent", "agent_id": "a1"},
            ],
        )
    )
    sink.emit(run_started("w1", "a1", kind="agent"))
    ev = message_end(FinishReason.END_TURN)
    sink.emit(ev)
    assert ev.payload["team_batch"] == {"kind": "in_flight", "worker_count": 1}


def test_finish_guard_no_verdict_does_not_scan_dispatch_claims():
    content = "按任务卡流程派工。两路队员已全部完成 2/2。"
    assert finish_guard(content, citation_count=0) == []
