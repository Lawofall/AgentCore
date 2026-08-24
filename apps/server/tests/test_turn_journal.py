"""Tests for the Turn Journal projection transforms (§18.3 唯一事实源).

``entries_from_runs`` flattens the in-memory ``runs`` replay payload into the
journal's ordered facts; ``runs_from_entries`` projects them back. The two must be
exact inverses so a turn round-trips through ``turn_journal`` unchanged — that is
what lets the message's ``runs`` be a pure projection rather than a stored blob.

``window_from_journal`` is the EXECUTION-side projection (the other half of「一切皆
投影」): it folds the same facts back into the ``list[LLMMessage]`` the engine fed the
model, so resume reads it instead of the旁路 frame and the conformance golden can
assert it ``==`` the live transcript.
"""

from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.journal import (
    ensure_cancelled_turn_end,
    entries_from_runs,
    journal_entries_from_display_runs,
    last_turn_end_finish,
    runs_from_entries,
    window_from_journal,
)


def test_multi_agent_events_round_trip():
    runs = {
        "events": [
            {"type": "run_plan", "payload": {"execution_id": "e1"}, "timestamp": "t0"},
            {"type": "tool_use_start", "payload": {"tool_call_id": "c1"}, "timestamp": "t1"},
            {"type": "tool_use_end", "payload": {"tool_call_id": "c1"}, "timestamp": "t2"},
            {"type": "run_completed", "payload": {"run_id": "s1"}, "timestamp": "t3"},
        ],
        "finish_reason": "end_turn",
    }
    entries = entries_from_runs(runs)
    # Each event becomes a fact keeping its type as kind + its timestamp as ts; the
    # finish_reason rides a trailing turn_end fact.
    assert [e["kind"] for e in entries] == [
        "run_plan",
        "tool_use_start",
        "tool_use_end",
        "run_completed",
        "turn_end",
    ]
    assert entries[0]["ts"] == "t0"
    # Display fold synthesizes a ``team`` marker from ``run_plan`` when progressive
    # ``process_team`` is absent (legacy / events-only journals).
    assert runs_from_entries(entries) == {
        **runs,
        "process": [{"kind": "team", "execution_id": "e1"}],
    }


def test_single_agent_process_round_trips_with_events_empty():
    runs = {
        "events": [],
        "finish_reason": "end_turn",
        "process": [
            {"kind": "reasoning", "text": "想一想"},
            {
                "kind": "tool",
                "id": "c1",
                "tool_name": "read_file",
                "arguments": {"path": "a"},
                "result": "ok",
                "status": "success",
            },
        ],
    }
    entries = entries_from_runs(runs)
    assert [e["kind"] for e in entries] == [
        "process_reasoning",
        "process_tool",
        "turn_end",
    ]
    # The process steps restore verbatim and events stays [] (single-agent turn).
    assert runs_from_entries(entries) == runs


def test_empty_and_none_payloads():
    assert entries_from_runs(None) == []
    assert entries_from_runs({}) == []
    assert journal_entries_from_display_runs(None) is None
    assert journal_entries_from_display_runs({}) is None
    # Nothing replayable projects back to None (matches the old「runs is NULL」shape).
    assert runs_from_entries(None) is None
    assert runs_from_entries([]) is None


def test_finish_reason_only_round_trips():
    # A turn with no events/process but a finish_reason still carries a turn_end fact,
    # so its outcome survives (e.g. a salvaged cancelled turn with empty journal).
    runs = {"events": [], "finish_reason": "cancelled"}
    entries = entries_from_runs(runs)
    assert [e["kind"] for e in entries] == ["turn_end"]
    assert runs_from_entries(entries) == runs


def test_error_round_trips_on_turn_end():
    # A 报错回合 (Tier 2 a): no graph/process, just finish_reason=error + the terminal
    # error on turn_end. It round-trips so the inline error card replays on reload — the
    # live error rode a transport-only ``error`` SSE event, never journaled, so this
    # outcome fact is its only durable home.
    runs = {
        "events": [],
        "finish_reason": "error",
        "error": {"code": "PIPELINE_ERROR", "message": "boom"},
    }
    entries = entries_from_runs(runs)
    assert [e["kind"] for e in entries] == ["turn_end"]
    assert entries[0]["payload"] == {
        "finish_reason": "error",
        "error": {"code": "PIPELINE_ERROR", "message": "boom"},
    }
    assert runs_from_entries(entries) == runs


def test_error_keeps_turn_non_none_despite_empty_graph_gate():
    # An execution-sourced 报错回合 (captain failed after running, no surface): its own run
    # events gate to [] like any non-surfaced turn, but the turn_end carries the error, so
    # the None-gate must NOT drop the turn — the error card still replays on reload (a2).
    entries = [
        {"kind": "turn_started", "payload": {"user_message": "go"}, "ts": None},
        {"kind": "run_started", "payload": {"run_id": "cap"}, "ts": "t0"},
        {
            "kind": "round_boundary",
            "payload": {"round_idx": 0, "run_id": "cap", "role": "captain"},
            "ts": None,
        },
        {"kind": "run_completed", "payload": {"run_id": "cap"}, "ts": "t1"},
        {
            "kind": "turn_end",
            "payload": {
                "finish_reason": "error",
                "error": {"code": "PIPELINE_ERROR", "message": "boom"},
            },
            "ts": None,
        },
    ]
    projected = runs_from_entries(entries)
    assert projected is not None
    assert projected["events"] == []  # captain run events gated (no surface)
    assert projected["finish_reason"] == "error"
    assert projected["error"] == {"code": "PIPELINE_ERROR", "message": "boom"}


def test_clean_turn_carries_no_error_key():
    # A clean turn never grows an ``error`` key on projection — parity with
    # _build_runs_payload, which only sets it for a 报错回合.
    runs = {
        "events": [{"type": "run_plan", "payload": {}, "timestamp": "t0"}],
        "finish_reason": "end_turn",
    }
    projected = runs_from_entries(entries_from_runs(runs))
    assert "error" not in projected
    assert projected == runs


def test_process_absent_key_not_emitted_on_projection():
    # A multi-agent turn (no process) must not grow a ``process`` key when projected
    # back, so the shape matches ``_build_runs_payload`` exactly.
    runs = {
        "events": [{"type": "run_plan", "payload": {}, "timestamp": "t0"}],
        "finish_reason": "end_turn",
    }
    projected = runs_from_entries(entries_from_runs(runs))
    assert "process" not in projected
    assert projected == runs


# --- G1 挂起中冷启动重载：turn_paused process 回退 --------------------------------
#
# Pause finalize skips writing process_* / run_process_* tails. On reload while still
# paused, runs_from_entries falls back to the last turn_paused snapshot's timelines.


def test_paused_reload_projects_process_from_turn_paused():
    # No process_* entries — typical mid-pause journal — but turn_paused carries the
    # pre-pause timeline + checkpoint marker. Projection must surface both.
    entries = [
        {"kind": "turn_started", "payload": {"user_message": "go"}, "ts": None},
        {
            "kind": "checkpoint_required",
            "payload": {"checkpoint_id": "cp-1", "prompt": "确认？"},
            "ts": "t0",
        },
        {
            "kind": "turn_paused",
            "payload": {
                "checkpoint_id": "cp-1",
                "suspension_kind": "ask_user",
                "content": "气泡",
                "reasoning": "想过",
                "process": [
                    {"kind": "reasoning", "text": "想过"},
                    {"kind": "tool", "id": "c1", "tool_name": "read_file", "status": "success"},
                    {"kind": "checkpoint", "checkpoint_id": "cp-1"},
                ],
                "run_processes": {
                    "w1": [{"kind": "reasoning", "text": "worker 想"}],
                },
                "citations": [],
                "controller": {},
            },
            "ts": "t1",
        },
        {"kind": "turn_end", "payload": {"finish_reason": "paused"}, "ts": None},
    ]
    runs = runs_from_entries(entries)
    assert runs is not None
    assert runs["finish_reason"] == "paused"
    assert [e["type"] for e in runs["events"]] == ["checkpoint_required"]
    assert runs["process"] == [
        {"kind": "reasoning", "text": "想过"},
        {"kind": "tool", "id": "c1", "tool_name": "read_file", "status": "success"},
        {"kind": "checkpoint", "checkpoint_id": "cp-1"},
    ]
    assert runs["run_processes"] == {"w1": [{"kind": "reasoning", "text": "worker 想"}]}
    # turn_paused itself never leaks into events.
    assert all(e["type"] != "turn_paused" for e in runs["events"])


def test_process_star_entries_prefer_over_turn_paused_fallback():
    # Completed / resumed turns write process_* — those win; snapshot is ignored.
    entries = [
        {
            "kind": "process_reasoning",
            "payload": {"kind": "reasoning", "text": "落库时间线"},
            "ts": None,
        },
        {
            "kind": "run_process_reasoning",
            "payload": {"run_id": "w1", "kind": "reasoning", "text": "落库 worker"},
            "ts": None,
        },
        {
            "kind": "turn_paused",
            "payload": {
                "checkpoint_id": "cp-old",
                "suspension_kind": "ask_user",
                "process": [{"kind": "reasoning", "text": "快照应被忽略"}],
                "run_processes": {"w1": [{"kind": "reasoning", "text": "快照 worker 忽略"}]},
            },
            "ts": "t0",
        },
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": None},
    ]
    runs = runs_from_entries(entries)
    assert runs["process"] == [{"kind": "reasoning", "text": "落库时间线"}]
    assert runs["run_processes"] == {"w1": [{"kind": "reasoning", "text": "落库 worker"}]}


def test_legacy_paused_journal_without_turn_paused_keeps_empty_process():
    # Old frames: surface card + paused finish, no turn_paused → no invented timeline.
    entries = [
        {
            "kind": "checkpoint_required",
            "payload": {"checkpoint_id": "cp-legacy"},
            "ts": "t0",
        },
        {"kind": "turn_end", "payload": {"finish_reason": "paused"}, "ts": None},
    ]
    runs = runs_from_entries(entries)
    assert runs is not None
    assert runs["finish_reason"] == "paused"
    assert [e["type"] for e in runs["events"]] == ["checkpoint_required"]
    assert "process" not in runs
    assert "run_processes" not in runs


# --- Execution-sourced journals (§18.3 fact log) --------------------------------
#
# Fact-log journals store the full ungated stream plus execution facts. The surface
# gate + none-gate in ``runs_from_entries`` apply uniformly (idempotent on journals
# that were already gated at write).


def test_cancelled_salvage_with_exec_facts_still_round_trips():
    # A salvaged cancelled turn may carry execution facts (e.g. after pipeline cutover)
    # with an empty gated graph — the abnormal finish_reason must still project.
    entries = [
        {"kind": "turn_started", "payload": {"user_message": "go"}, "ts": None},
        {"kind": "turn_end", "payload": {"finish_reason": "cancelled"}, "ts": None},
    ]
    assert runs_from_entries(entries) == {"events": [], "finish_reason": "cancelled"}


def test_ensure_cancelled_turn_end_appends_when_absent():
    facts = [
        {"kind": "run_started", "payload": {"id": "r1"}, "ts": "t0"},
        {"kind": "run_completed", "payload": {"id": "r1"}, "ts": None},
    ]
    closed = ensure_cancelled_turn_end(facts)
    assert last_turn_end_finish(closed) == "cancelled"
    assert closed[:-1] == facts
    assert runs_from_entries(closed)["finish_reason"] == "cancelled"
    # Success-path helper must not be implied: raw facts stay without turn_end.
    assert last_turn_end_finish(facts) is None


def test_ensure_cancelled_turn_end_overrides_pause_snapshot():
    snapshot = [
        {"kind": "turn_paused", "payload": {"checkpoint_id": "cp"}, "ts": "t0"},
        {"kind": "turn_end", "payload": {"finish_reason": "paused"}, "ts": None},
    ]
    closed = ensure_cancelled_turn_end(snapshot)
    assert last_turn_end_finish(closed) == "cancelled"
    assert closed[0] == snapshot[0]
    assert runs_from_entries(closed)["finish_reason"] == "cancelled"


def test_ensure_cancelled_turn_end_empty_journal_still_projects():
    closed = ensure_cancelled_turn_end(None)
    assert closed == [
        {"kind": "turn_end", "payload": {"finish_reason": "cancelled"}, "ts": None}
    ]
    assert runs_from_entries(closed) == {"events": [], "finish_reason": "cancelled"}


def test_execution_sourced_plain_chat_turn_projects_to_none():
    # A plain chat turn's fact log: the captain ran (run_started/completed) but never
    # surfaced a graph and used no tools. Execution facts persist (for window rebuild),
    # but the DISPLAY is a plain bubble → runs projects to None (None-gate).
    entries = [
        {"kind": "turn_started", "payload": {"user_message": "hi"}, "ts": None},
        {"kind": "run_started", "payload": {"run_id": "cap"}, "ts": "t0"},
        {
            "kind": "round_boundary",
            "payload": {"round_idx": 0, "run_id": "cap", "role": "captain"},
            "ts": None,
        },
        {"kind": "llm_call", "payload": {"content": "hello", "round_idx": 0}, "ts": None},
        {"kind": "run_completed", "payload": {"run_id": "cap"}, "ts": "t1"},
        {"kind": "message_final", "payload": {"run_id": "cap", "content": "hello"}, "ts": None},
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": None},
    ]
    assert runs_from_entries(entries) is None


def test_execution_sourced_surfaced_turn_keeps_graph_drops_exec_facts():
    # A delegated turn (run_plan surfaces): the team graph projects to events; the
    # interleaved execution facts are skipped (they are not client-foldable).
    entries = [
        {"kind": "turn_started", "payload": {"user_message": "go"}, "ts": None},
        {
            "kind": "round_boundary",
            "payload": {"round_idx": 0, "run_id": "cap", "role": "captain"},
            "ts": None,
        },
        {"kind": "llm_call", "payload": {"tool_calls": [{"id": "d"}]}, "ts": None},
        {"kind": "run_plan", "payload": {"execution_id": "e1"}, "ts": "t0"},
        {"kind": "run_started", "payload": {"run_id": "w1"}, "ts": "t1"},
        {"kind": "run_completed", "payload": {"run_id": "w1"}, "ts": "t2"},
        {"kind": "message_final", "payload": {"run_id": "cap", "content": "done"}, "ts": None},
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": None},
    ]
    assert runs_from_entries(entries) == {
        "events": [
            {"type": "run_plan", "payload": {"execution_id": "e1"}, "timestamp": "t0"},
            {"type": "run_started", "payload": {"run_id": "w1"}, "timestamp": "t1"},
            {"type": "run_completed", "payload": {"run_id": "w1"}, "timestamp": "t2"},
        ],
        "finish_reason": "end_turn",
        "process": [{"kind": "team", "execution_id": "e1"}],
    }


def test_execution_sourced_single_agent_tool_turn_drops_captain_events_keeps_process():
    # A single-agent tool turn (captain used a tool, never delegated/checkpointed):
    # no surface type → its captain run/tool events drop from ``events`` (parity with
    # the old execution_journal() gate), but the process timeline replays.
    entries = [
        {"kind": "turn_started", "payload": {"user_message": "find x"}, "ts": None},
        {"kind": "run_started", "payload": {"run_id": "cap"}, "ts": "t0"},
        {
            "kind": "round_boundary",
            "payload": {"round_idx": 0, "run_id": "cap", "role": "captain"},
            "ts": None,
        },
        {"kind": "llm_call", "payload": {"tool_calls": [{"id": "c1"}]}, "ts": None},
        {"kind": "tool_use_start", "payload": {"tool_call_id": "c1"}, "ts": "t1"},
        {"kind": "tool_use_end", "payload": {"tool_call_id": "c1"}, "ts": "t2"},
        {"kind": "run_completed", "payload": {"run_id": "cap"}, "ts": "t3"},
        {
            "kind": "process_tool",
            "payload": {
                "kind": "tool",
                "id": "c1",
                "tool_name": "web_search",
                "result": "r",
                "status": "success",
            },
            "ts": None,
        },
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": None},
    ]
    assert runs_from_entries(entries) == {
        "events": [],
        "finish_reason": "end_turn",
        "process": [
            {
                "kind": "tool",
                "id": "c1",
                "tool_name": "web_search",
                "result": "r",
                "status": "success",
            }
        ],
    }


def test_legacy_channel_redirect_process_tool_normalizes_to_redirect():
    entries = [
        {
            "kind": "process_tool",
            "payload": {
                "kind": "tool",
                "id": "c1",
                "tool_name": "code_execute",
                "result": "禁止用 code_execute 打开源码再正则扫描（检测到：re.findall(）。",
                "status": "error",
                "failure": {
                    "message": "这一步想用脚本打开源码再搜索，没有执行。",
                    "code": "source_grep_redirect",
                },
            },
            "ts": None,
        },
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": None},
    ]
    runs = runs_from_entries(entries)
    assert runs is not None
    tool = runs["process"][0]
    assert tool["status"] == "redirect"
    assert tool["failure"]["code"] == "source_grep_redirect"


def test_classic_steer_interjection_survives_the_surface_gate():
    # 经典单聊插话: a steer on a plain chat turn journals DURABLE ``user_interjection``
    # facts but no run_plan / approval / question. ``user_interjection`` is itself a
    # surface type, so the gate must NOT wipe the events — retiring
    # ``turn_steer_accepted`` was precisely about a user utterance surviving reload
    # (user_interjection 先例). Both frames of one interjection_id replay; the client
    # folds them to a single record (last status wins).
    entries = [
        {"kind": "turn_started", "payload": {"user_message": "写个方案"}, "ts": None},
        {"kind": "run_started", "payload": {"run_id": "cap"}, "ts": "t0"},
        {
            "kind": "user_interjection",
            "payload": {
                "interjection_id": "ij-1",
                "content": "换个方向",
                "status": "received",
            },
            "ts": "t1",
        },
        {
            "kind": "user_interjection",
            "payload": {
                "interjection_id": "ij-1",
                "content": "换个方向",
                "status": "injected",
            },
            "ts": "t2",
        },
        {"kind": "run_completed", "payload": {"run_id": "cap"}, "ts": "t3"},
        {"kind": "message_final", "payload": {"run_id": "cap", "content": "好"}, "ts": None},
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": None},
    ]
    projected = runs_from_entries(entries)
    assert projected is not None
    interjections = [e for e in projected["events"] if e["type"] == "user_interjection"]
    assert [e["payload"]["status"] for e in interjections] == ["received", "injected"]
    assert {e["payload"]["content"] for e in interjections} == {"换个方向"}


# --- deltas 退场: worker output/thinking synthesized from message_final ------------
#
# A worker run no longer journals per-token run_output_delta / run_reasoning_delta.
# runs_from_entries reconstructs ONE equivalent delta block each from the worker's
# message_final fact, spliced just before its terminal event — so the client fold
# rebuilds 输出/思考 unchanged. The CAPTAIN's own message_final (its reply is the chat
# bubble) is never synthesized onto a run node.


def test_worker_output_and_reasoning_synthesized_before_run_completed():
    entries = [
        {"kind": "turn_started", "payload": {"user_message": "go"}, "ts": None},
        {
            "kind": "run_started",
            "payload": {"run_id": "cap", "agent_id": "cap", "kind": "captain"},
            "ts": "t0",
        },
        {"kind": "run_plan", "payload": {"execution_id": "e1"}, "ts": "t1"},
        {
            "kind": "run_started",
            "payload": {"run_id": "w1", "agent_id": "w1", "kind": "agent"},
            "ts": "t2",
        },
        {"kind": "run_completed", "payload": {"run_id": "w1", "agent_id": "w1"}, "ts": "t3"},
        {
            "kind": "message_final",
            "payload": {
                "run_id": "w1",
                "phase": "completed",
                "content": "worker输出",
                "reasoning": "worker思考",
            },
            "ts": None,
        },
        {"kind": "run_completed", "payload": {"run_id": "cap", "agent_id": "cap"}, "ts": "t4"},
        {
            "kind": "message_final",
            "payload": {"run_id": "cap", "content": "汇总回复", "reasoning": "captain思考"},
            "ts": None,
        },
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": None},
    ]
    # Reasoning precedes content (live order), both inherit the run_completed timestamp,
    # and they land immediately before the worker's run_completed.
    assert runs_from_entries(entries)["events"] == [
        {
            "type": "run_started",
            "payload": {"run_id": "cap", "agent_id": "cap", "kind": "captain"},
            "timestamp": "t0",
        },
        {"type": "run_plan", "payload": {"execution_id": "e1"}, "timestamp": "t1"},
        {
            "type": "run_started",
            "payload": {"run_id": "w1", "agent_id": "w1", "kind": "agent"},
            "timestamp": "t2",
        },
        {
            "type": "run_reasoning_delta",
            "payload": {"run_id": "w1", "agent_id": "w1", "delta": "worker思考"},
            "timestamp": "t3",
        },
        {
            "type": "run_output_delta",
            "payload": {"run_id": "w1", "agent_id": "w1", "delta": "worker输出"},
            "timestamp": "t3",
        },
        {"type": "run_completed", "payload": {"run_id": "w1", "agent_id": "w1"}, "timestamp": "t3"},
        {
            "type": "run_completed",
            "payload": {"run_id": "cap", "agent_id": "cap"},
            "timestamp": "t4",
        },
    ]


def test_captain_message_final_is_not_synthesized_onto_a_run_node():
    # The captain's reply is the bubble (turn-level content_delta), so its message_final
    # must never become a run_output_delta — even though the captain has a run node.
    entries = [
        {
            "kind": "run_started",
            "payload": {"run_id": "cap", "agent_id": "cap", "kind": "captain"},
            "ts": "t0",
        },
        {"kind": "run_plan", "payload": {"execution_id": "e1"}, "ts": "t1"},
        {"kind": "run_completed", "payload": {"run_id": "cap", "agent_id": "cap"}, "ts": "t2"},
        {
            "kind": "message_final",
            "payload": {"run_id": "cap", "content": "回复", "reasoning": "想"},
            "ts": None,
        },
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": None},
    ]
    events = runs_from_entries(entries)["events"]
    assert not any(e["type"] in ("run_output_delta", "run_reasoning_delta") for e in events)


def test_failed_worker_partial_output_synthesized_before_run_failed():
    # A FAILED worker can still have produced partial output/thinking: its message_final
    # (phase=failed) carries them, synthesized before run_failed (parity with the old
    # delta replay, which showed a failed run's partial output too).
    entries = [
        {"kind": "run_plan", "payload": {"execution_id": "e1"}, "ts": "t0"},
        {
            "kind": "run_started",
            "payload": {"run_id": "w1", "agent_id": "w1", "kind": "agent"},
            "ts": "t1",
        },
        {
            "kind": "run_failed",
            "payload": {"run_id": "w1", "agent_id": "w1", "error": "boom"},
            "ts": "t2",
        },
        {
            "kind": "message_final",
            "payload": {
                "run_id": "w1",
                "phase": "failed",
                "content": "半成品",
                "reasoning": "中途思考",
                "error": "boom",
            },
            "ts": None,
        },
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": None},
    ]
    assert runs_from_entries(entries)["events"] == [
        {"type": "run_plan", "payload": {"execution_id": "e1"}, "timestamp": "t0"},
        {
            "type": "run_started",
            "payload": {"run_id": "w1", "agent_id": "w1", "kind": "agent"},
            "timestamp": "t1",
        },
        {
            "type": "run_reasoning_delta",
            "payload": {"run_id": "w1", "agent_id": "w1", "delta": "中途思考"},
            "timestamp": "t2",
        },
        {
            "type": "run_output_delta",
            "payload": {"run_id": "w1", "agent_id": "w1", "delta": "半成品"},
            "timestamp": "t2",
        },
        {
            "type": "run_failed",
            "payload": {"run_id": "w1", "agent_id": "w1", "error": "boom"},
            "timestamp": "t2",
        },
    ]


def test_synthesis_skips_empty_content_or_reasoning():
    # A non-thinking worker produced no reasoning → only an output delta is synthesized
    # (no empty run_reasoning_delta is injected).
    entries = [
        {"kind": "run_plan", "payload": {"execution_id": "e1"}, "ts": "t0"},
        {
            "kind": "run_started",
            "payload": {"run_id": "w1", "agent_id": "w1", "kind": "agent"},
            "ts": "t1",
        },
        {"kind": "run_completed", "payload": {"run_id": "w1", "agent_id": "w1"}, "ts": "t2"},
        {
            "kind": "message_final",
            "payload": {
                "run_id": "w1",
                "phase": "completed",
                "content": "只有输出",
                "reasoning": "",
            },
            "ts": None,
        },
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": None},
    ]
    types = [e["type"] for e in runs_from_entries(entries)["events"]]
    assert types == ["run_plan", "run_started", "run_output_delta", "run_completed"]


# --- window_from_journal: execution projection (§三, the LLM-window fold) ----------
#
# These build journals by hand to pin the fold rules; test_engine_facts.py drives the
# REAL react_loop and asserts the projection reconstructs the live transcript verbatim
# (the unit-level conformance check the Phase 2 golden generalizes to paused turns).


def _fact(kind: str, payload: dict, ts=None) -> dict:
    return {"kind": kind, "payload": payload, "ts": ts}


def _started(system_prompt="S", user_message="go", history_len=0) -> dict:
    return _fact(
        "turn_started",
        {
            "system_prompt": system_prompt,
            "user_message": user_message,
            "model_profile": "m",
            "history_len": history_len,
        },
    )


def _run_head(
    run_id: str,
    system_prompt="WSYS",
    user_message="## task\ndo it",
    user_origin="context_blocks",
) -> dict:
    return _fact(
        "run_head",
        {
            "run_id": run_id,
            "system_prompt": system_prompt,
            "user_message": user_message,
            "user_origin": user_origin,
        },
    )


def _boundary(run_id="cap", round_idx=0, role="captain") -> dict:
    return _fact("round_boundary", {"round_idx": round_idx, "run_id": run_id, "role": role})


def _llm(run_id="cap", round_idx=0, *, content="", reasoning="", tool_calls=None) -> dict:
    return _fact(
        "llm_call",
        {
            "run_id": run_id,
            "round_idx": round_idx,
            "content": content,
            "reasoning_content": reasoning,
            "tool_calls": tool_calls or [],
            "usage": {},
            "finish_reason": "tool_calls" if tool_calls else "stop",
        },
    )


def _tc(call_id: str, name: str, arguments: str) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def _tool_call(call_id: str, result: str, run_id="cap", name="t") -> dict:
    # The execution tool_call fact the window fold reads its tool result from (the FULL
    # post-annotation text); the display tool_use_start/end pair rides separately.
    return _fact(
        "tool_call",
        {
            "run_id": run_id,
            "tool_call_id": call_id,
            "name": name,
            "arguments": "{}",
            "result": result,
            "success": True,
        },
    )


def test_window_folds_head_assistant_tool_drops_final_answer():
    # A single-agent tool turn: round 0 calls a tool, round 1 answers with text. The
    # window is system + user + assistant(tool_call) + tool(result) — the round-1 final
    # answer is the loop's RETURN, never appended to the window the model saw.
    entries = [
        _started(system_prompt="SYS", user_message="find x"),
        _boundary(round_idx=0),
        _llm(round_idx=0, reasoning="thinking", tool_calls=[_tc("c1", "search", '{"q":"x"}')]),
        _tool_call("c1", "the result"),
        _boundary(round_idx=1),
        _llm(round_idx=1, content="the answer"),
        _fact("message_final", {"run_id": "cap", "content": "the answer"}),
        _fact("turn_end", {"finish_reason": "end_turn"}),
    ]
    assert window_from_journal(entries) == [
        LLMMessage(role="system", content="SYS"),
        LLMMessage(role="user", content="find x"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="c1",
                    type="function",
                    function=ToolCallFunction(name="search", arguments='{"q":"x"}'),
                )
            ],
            reasoning_content="thinking",
        ),
        LLMMessage(role="tool", content="the result", tool_call_id="c1"),
    ]


def test_window_none_without_turn_started():
    # A display-only journal (no execution facts) has no head anchor → the
    # captain window is not reconstructable (Phase 1), so the projection returns None.
    entries = [
        _fact("run_plan", {"execution_id": "e1"}, ts="t0"),
        _fact("tool_use_end", {"tool_call_id": "c1", "result": "r"}, ts="t1"),
        _fact("turn_end", {"finish_reason": "end_turn"}),
    ]
    assert window_from_journal(entries) is None
    assert window_from_journal(None) is None
    assert window_from_journal([]) is None


def test_window_splices_caller_history_between_system_and_user():
    # The facts carry only history_len; the caller supplies the prior-turn messages,
    # spliced between system and user exactly as the executor builds the transcript.
    history = [
        LLMMessage(role="user", content="earlier"),
        LLMMessage(role="assistant", content="earlier reply"),
    ]
    entries = [
        _started(system_prompt="SYS", user_message="now"),
        _boundary(round_idx=0),
        _llm(round_idx=0, content="done"),
    ]
    assert window_from_journal(entries, history=history) == [
        LLMMessage(role="system", content="SYS"),
        LLMMessage(role="user", content="earlier"),
        LLMMessage(role="assistant", content="earlier reply"),
        LLMMessage(role="user", content="now"),
    ]


def test_window_folds_note_as_user_message_in_order():
    # An engine-injected note (a convergence NUDGE) is part of the real window: it folds
    # to a user message after the round's assistant+tool, before the next round.
    entries = [
        _started(),
        _boundary(round_idx=0),
        _llm(round_idx=0, tool_calls=[_tc("c1", "search", "{}")]),
        _tool_call("c1", "r"),
        _fact("note", {"role": "user", "content": "重复了，换个思路", "reason": "nudge"}),
        _boundary(round_idx=1),
        _llm(round_idx=1, content="ok"),
    ]
    window = window_from_journal(entries)
    # ...system, user, assistant(tool), tool, note(user)
    assert window[-1] == LLMMessage(role="user", content="重复了，换个思路")
    assert window[-2] == LLMMessage(role="tool", content="r", tool_call_id="c1")
    assert window[2].role == "assistant"


def test_window_scopes_to_captain_ignoring_worker_facts():
    # A delegated turn: worker facts interleave during the captain's delegate call. The
    # captain window must contain ONLY the captain's assistant(delegate) + the delegate
    # result — the worker's own assistant (different run_id) and its file_write tool
    # (different tool_call_id) are excluded, proving run-scope + tool_call_id pairing.
    entries = [
        _started(system_prompt="SYS", user_message="build it"),
        _boundary(run_id="cap", round_idx=0, role="captain"),
        _llm(run_id="cap", round_idx=0, tool_calls=[_tc("d1", "delegate", "{}")]),
        _fact("tool_use_start", {"tool_call_id": "d1", "tool_name": "delegate"}),
        # --- worker runs inside the delegate call ---
        _boundary(run_id="w1", round_idx=0, role="worker"),
        _llm(run_id="w1", round_idx=0, tool_calls=[_tc("fw", "file_write", "{}")]),
        _tool_call("fw", "written", run_id="w1"),
        _boundary(run_id="w1", round_idx=1, role="worker"),
        _llm(run_id="w1", round_idx=1, content="worker done"),
        _fact("message_final", {"run_id": "w1", "content": "worker done"}),
        # --- captain's delegate result returns ---
        _tool_call("d1", "team product"),
        _boundary(run_id="cap", round_idx=1, role="captain"),
        _llm(run_id="cap", round_idx=1, content="final reply"),
        _fact("message_final", {"run_id": "cap", "content": "final reply"}),
        _fact("turn_end", {"finish_reason": "end_turn"}),
    ]
    assert window_from_journal(entries) == [
        LLMMessage(role="system", content="SYS"),
        LLMMessage(role="user", content="build it"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="d1",
                    type="function",
                    function=ToolCallFunction(name="delegate", arguments="{}"),
                )
            ],
            reasoning_content=None,
        ),
        LLMMessage(role="tool", content="team product", tool_call_id="d1"),
    ]
    # Worker without ``run_head`` (legacy journal): rounds-only — never the false
    # CEO ``turn_started`` head. Run isolation still holds.
    worker_window = window_from_journal(entries, run_id="w1")
    assert worker_window is not None
    assert worker_window[0] == LLMMessage(
        role="assistant",
        content=None,
        tool_calls=[
            ToolCall(
                id="fw",
                type="function",
                function=ToolCallFunction(name="file_write", arguments="{}"),
            )
        ],
        reasoning_content=None,
    )
    assert worker_window[1] == LLMMessage(role="tool", content="written", tool_call_id="fw")
    assert all(m.role != "system" for m in worker_window)


def test_window_worker_run_head_not_turn_started():
    # Worker with ``run_head``: window anchors on the worker task-prompt, never the
    # CEO ``turn_started`` (SYS / "build it").
    entries = [
        _started(system_prompt="SYS", user_message="build it"),
        _boundary(run_id="cap", round_idx=0, role="captain"),
        _llm(run_id="cap", round_idx=0, tool_calls=[_tc("d1", "delegate", "{}")]),
        _run_head(
            "w1",
            system_prompt="WORKER-SYS",
            user_message="## 你的任务\n写文件",
        ),
        _boundary(run_id="w1", round_idx=0, role="worker"),
        _llm(run_id="w1", round_idx=0, tool_calls=[_tc("fw", "file_write", "{}")]),
        _tool_call("fw", "written", run_id="w1"),
        _tool_call("d1", "team product"),
    ]
    worker_window = window_from_journal(entries, run_id="w1")
    assert worker_window == [
        LLMMessage(role="system", content="WORKER-SYS"),
        LLMMessage(role="user", content="## 你的任务\n写文件"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="fw",
                    type="function",
                    function=ToolCallFunction(name="file_write", arguments="{}"),
                )
            ],
            reasoning_content=None,
        ),
        LLMMessage(role="tool", content="written", tool_call_id="fw"),
    ]
    # Captain fold unchanged.
    captain = window_from_journal(entries, run_id="cap")
    assert captain[0] == LLMMessage(role="system", content="SYS")
    assert captain[1] == LLMMessage(role="user", content="build it")


def test_window_captain_note_mid_delegate_attributed_by_run_id():
    # 边界② cleared: a captain note (a force-finalize) is recorded AFTER the delegate
    # returns but BEFORE any new captain round_boundary, so the most-recent boundary is
    # the worker's (active run = w1). It still folds into the CAPTAIN window because the
    # note carries run_id="cap" — the old active-run fallback would have dropped it.
    entries = [
        _started(system_prompt="SYS", user_message="build it"),
        _boundary(run_id="cap", round_idx=0, role="captain"),
        _llm(run_id="cap", round_idx=0, tool_calls=[_tc("d1", "delegate", "{}")]),
        _fact("tool_use_start", {"tool_call_id": "d1", "tool_name": "delegate"}),
        # worker runs inside the delegate call → active run becomes w1.
        _boundary(run_id="w1", round_idx=0, role="worker"),
        _llm(run_id="w1", round_idx=0, content="worker done"),
        _fact("message_final", {"run_id": "w1", "content": "worker done"}),
        _tool_call("d1", "team product"),  # delegate returns to the captain
        # captain force-finalize note, injected while the active run is STILL w1.
        _fact(
            "note",
            {
                "role": "user",
                "content": "请基于已有信息给出最终答复。",
                "reason": "finalize",
                "run_id": "cap",
            },
        ),
    ]
    assert window_from_journal(entries) == [
        LLMMessage(role="system", content="SYS"),
        LLMMessage(role="user", content="build it"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="d1",
                    type="function",
                    function=ToolCallFunction(name="delegate", arguments="{}"),
                )
            ],
            reasoning_content=None,
        ),
        LLMMessage(role="tool", content="team product", tool_call_id="d1"),
        LLMMessage(role="user", content="请基于已有信息给出最终答复。"),
    ]
    # Contrast: the SAME note WITHOUT a run_id falls back to the active run (w1), so it is
    # NOT attributed to the captain window — the exact pre-fix 边界② bug, now gated.
    legacy = entries[:-1] + [_fact("note", {"role": "user", "content": "X", "reason": "finalize"})]
    assert window_from_journal(legacy)[-1] == LLMMessage(
        role="tool", content="team product", tool_call_id="d1"
    )


def test_window_paused_turn_ends_at_suspended_call_no_phantom_tool():
    # The resume-shape conformance check: at a pause the turn suspends INSIDE the tool
    # (ask_user / delegate) — its ``tool_use_start`` is journaled but NO ``tool_call`` fact
    # is recorded (the result is still pending). The window must end at the assistant that
    # issued the suspended call, with NO tool message — exactly what frame.transcript
    # holds — so resume can append the settled result itself (执行级事件溯源 §18.3).
    entries = [
        _started(system_prompt="SYS", user_message="A 还是 B?"),
        _boundary(round_idx=0),
        _llm(round_idx=0, tool_calls=[_tc("call_ask", "ask_user", '{"q":"A/B"}')]),
        _fact("tool_use_start", {"tool_call_id": "call_ask", "tool_name": "ask_user"}),
        # paused here: no tool_use_end for call_ask, no further rounds.
    ]
    assert window_from_journal(entries) == [
        LLMMessage(role="system", content="SYS"),
        LLMMessage(role="user", content="A 还是 B?"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_ask",
                    type="function",
                    function=ToolCallFunction(name="ask_user", arguments='{"q":"A/B"}'),
                )
            ],
            reasoning_content=None,
        ),
    ]


def test_window_prior_completed_tool_kept_but_suspended_one_dropped():
    # A pause AFTER a completed tool round: round 0's search completed (has a tool_call
    # fact) → its tool message stays; round 1's delegate is suspended (no tool_call fact)
    # → its tool message is omitted. Proves the presence-keyed rule is per-call.
    entries = [
        _started(system_prompt="SYS", user_message="build"),
        _boundary(round_idx=0),
        _llm(round_idx=0, tool_calls=[_tc("c1", "search", "{}")]),
        _tool_call("c1", "found it"),
        _boundary(round_idx=1),
        _llm(round_idx=1, tool_calls=[_tc("d1", "delegate", "{}")]),
        _fact("tool_use_start", {"tool_call_id": "d1", "tool_name": "delegate"}),
        # paused inside delegate.
    ]
    window = window_from_journal(entries)
    assert window == [
        LLMMessage(role="system", content="SYS"),
        LLMMessage(role="user", content="build"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="c1",
                    type="function",
                    function=ToolCallFunction(name="search", arguments="{}"),
                )
            ],
            reasoning_content=None,
        ),
        LLMMessage(role="tool", content="found it", tool_call_id="c1"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="d1",
                    type="function",
                    function=ToolCallFunction(name="delegate", arguments="{}"),
                )
            ],
            reasoning_content=None,
        ),
    ]
