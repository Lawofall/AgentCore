"""One conversation folds into one graph. New segments hang off the window."""

from types import SimpleNamespace

from agentcore.conversation.history import _fold_history_messages
from agentcore.runtime.graph.shape.conversation_graph import (
    fold_conversation_graph,
    read_graph_context_ids,
    visible_context,
)
from agentcore.runtime.graph.shape.opening_segment import opening_plan_events
from agentcore.sidecar.chat_history import coerce_history_rows


def _plan(execution_id: str, runs: list[dict], *, plan_type: str = "multi_agent") -> dict:
    return {
        "kind": "run_plan",
        "payload": {
            "execution_id": execution_id,
            "plan_type": plan_type,
            "runs": runs,
        },
    }


def _done(run_id: str, text: str) -> dict:
    return {
        "kind": "run_completed",
        "payload": {"run_id": run_id, "output_summary": text},
    }


def test_window_leaf_is_the_downstream_conclusion():
    journal = [
        _plan(
            "ex-1",
            [
                {"id": "seg_a", "depends_on": []},
                {"id": "worker", "depends_on": ["seg_a"]},
            ],
        ),
        _done("seg_a", "先查完了"),
        _done("worker", "三家都高于预算"),
    ]
    assert visible_context(journal) == [
        {"id": "seg_a", "depends_on": []},
        {"id": "worker", "depends_on": ["seg_a"]},
    ]
    assert read_graph_context_ids([{"role": "assistant", "graph_context": visible_context(journal)}]) == [
        "worker"
    ]


def test_rewind_window_drops_the_cut_segment():
    journal = [
        _plan(
            "ex-1",
            [
                {"id": "seg_a", "depends_on": []},
                {"id": "worker", "depends_on": ["seg_a"]},
            ],
        ),
        _done("seg_a", "留下"),
        _done("worker", "丢掉"),
        {
            "kind": "graph_rewind",
            "payload": {
                "kept_ids": ["seg_a"],
                "cut_ids": ["worker"],
                "conclusions": [{"id": "seg_a", "text": "留下"}],
            },
        },
        _plan("ex-1", [{"id": "seg_follow", "depends_on": ["seg_a"]}]),
        _done("seg_follow", "回转后又写了一段"),
    ]
    assert [item["id"] for item in visible_context(journal)] == ["seg_a"]


def test_history_stamp_survives_the_next_user_row():
    journal = [
        _plan("ex-1", [{"id": "seg_a", "depends_on": []}]),
        _done("seg_a", "结论"),
    ]
    history = _fold_history_messages(
        [
            SimpleNamespace(role="user", content="先查价格", id="u1"),
            SimpleNamespace(role="assistant", content="结论", id="a1", evidence_ledger=None),
            SimpleNamespace(role="user", content="再比一家", id="u2"),
        ],
        journals={"a1": journal},
    )
    assert history[1]["graph_context"] == [{"id": "seg_a", "depends_on": []}]
    assert "graph_context" not in history[0]
    assert read_graph_context_ids(history[:-1]) == ["seg_a"]


def test_two_turns_fold_into_one_graph_and_skip_debate():
    turn_1 = [
        _plan(
            "ex-1",
            [
                {"id": "seg_a", "depends_on": []},
                {"id": "worker", "depends_on": ["seg_a"]},
            ],
        ),
        {"kind": "run_started", "payload": {"run_id": "seg_a"}},
        _done("seg_a", "甲"),
        _done("worker", "乙"),
        _plan(
            "ex-debate",
            [{"id": "debater", "depends_on": []}],
            plan_type="debate",
        ),
        _done("debater", "辩完了"),
    ]
    turn_2 = [
        _plan("ex-2", [{"id": "seg_b", "depends_on": ["worker"]}]),
        {"kind": "run_started", "payload": {"run_id": "seg_b"}},
    ]
    graph = fold_conversation_graph([turn_1, turn_2])
    assert [segment.id for segment in graph.segments] == ["seg_a", "worker", "seg_b"]
    assert graph.segments[2].depends_on == ("worker",)
    assert graph.segments[0].status == "sealed"
    assert graph.segments[2].status == "running"
    assert graph.progress_denominator == 1


def test_cut_segments_stay_on_the_graph_and_leave_the_denominator():
    graph = fold_conversation_graph(
        [
            [
                _plan(
                    "ex-1",
                    [
                        {"id": "seg_a", "depends_on": []},
                        {"id": "worker", "depends_on": ["seg_a"]},
                    ],
                ),
                _done("seg_a", "留"),
                _done("worker", "切"),
                {
                    "type": "graph_rewind",
                    "payload": {"cut_run_ids": ["worker"], "target_run_id": "seg_a"},
                },
                _plan("ex-2", [{"id": "seg_b", "depends_on": ["seg_a"]}]),
            ]
        ]
    )
    by_id = {segment.id: segment.status for segment in graph.segments}
    assert by_id == {"seg_a": "sealed", "worker": "cut", "seg_b": "waiting"}
    assert graph.progress_denominator == 1


def test_opening_event_appends_on_this_turn_only():
    events = opening_plan_events(
        execution_id="ex-2",
        captain_run_id="cap-2",
        user_message="再比一家",
        depends_on=["worker"],
    )
    plan = events[0].payload
    assert plan["execution_id"] == "ex-2"
    assert plan["runs"] == [
        {
            "id": "seg_cap-2",
            "agent_id": "seg_cap-2",
            "task": "再比一家",
            "depends_on": ["worker"],
            "parent_run_id": "cap-2",
            "kind": "agent",
        }
    ]
    assert [run["id"] for run in plan["runs"]] == ["seg_cap-2"]


def test_coerce_keeps_graph_context():
    rows = coerce_history_rows(
        [
            {
                "role": "assistant",
                "content": "结论",
                "graph_context": [{"id": "seg_a", "depends_on": []}],
                "extra": "drop",
            }
        ]
    )
    assert rows[0]["graph_context"] == [{"id": "seg_a", "depends_on": []}]
    assert "extra" not in rows[0]
