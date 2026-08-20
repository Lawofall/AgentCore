"""Turn Journal repository — the §18.3 fact stream survives a real Postgres round trip.

The other half of the Phase 2 resume gate (执行级事件溯源 §18.3，DB 往返 golden):
the conformance golden (``tests/test_pause_conformance.py``) proves the projection folds
an in-memory journal back to the live transcript; this proves the journal itself survives
``TurnJournalRepository.record`` → ``load`` through PostgreSQL JSONB unchanged — so the
nested ``tool_calls`` / ``reasoning_content`` / the full-text ``tool_call`` result do not
get mangled by serialization, and ``window_from_journal(loaded) == window_from_journal(
in-memory)``. Without this, a resume rebuilding the window from the DB could silently
diverge from one rebuilt in-process.

Backed by real PostgreSQL via the ``session_factory`` fixture (auto-skips when none is
reachable), same posture as the paused-turn repo integration tests.
"""

from uuid import uuid4

from agentcore.db.repositories import TurnJournalRepository
from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.facts import (
    LlmCallFact,
    NoteFact,
    RoundBoundaryFact,
    ToolCallFact,
    TurnStartedFact,
)
from agentcore.runtime.journal import runs_from_entries, window_from_journal


def _paused_journal() -> list[dict]:
    """A realistic pause-at-delegate journal: a completed tool round (with a full-text
    citation-annotated result + an injected note) then a suspended delegate (no tool_call
    fact), interleaving the captain's execution facts with a display ``tool_use_start`` and
    the trailing ``plan_review_required`` card — every payload shape the round trip must
    preserve byte-for-byte."""
    annotated = "结果正文\n\n[来源编号] 上述来源对应的引用号：[1]=https://example.com/a"
    return [
        TurnStartedFact(system_prompt="你是 CEO。", user_message="调研并撰写", model_profile="chat")
        .to_fact()
        .entry(),
        RoundBoundaryFact(round_idx=0, run_id="cap", role="captain").to_fact().entry(),
        LlmCallFact(
            run_id="cap",
            round_idx=0,
            content="",
            reasoning_content="让我先查一下",
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "search", "arguments": '{"q": "x"}'},
                }
            ],
            finish_reason="tool_calls",
        )
        .to_fact()
        .entry(),
        # display tool card (skipped by the window, kept by display)
        {
            "kind": "tool_use_start",
            "payload": {"tool_call_id": "c1", "tool_name": "search"},
            "ts": None,
        },
        ToolCallFact(
            run_id="cap",
            tool_call_id="c1",
            name="search",
            arguments='{"q": "x"}',
            result=annotated,
            success=True,
        )
        .to_fact()
        .entry(),
        NoteFact(role="user", content="换个角度再想想", reason="nudge", run_id="cap")
        .to_fact()
        .entry(),
        RoundBoundaryFact(round_idx=1, run_id="cap", role="captain").to_fact().entry(),
        LlmCallFact(
            run_id="cap",
            round_idx=1,
            tool_calls=[
                {
                    "id": "d1",
                    "type": "function",
                    "function": {"name": "delegate", "arguments": "{}"},
                }
            ],
            finish_reason="tool_calls",
        )
        .to_fact()
        .entry(),
        # suspended INSIDE delegate: a display tool_use_start but NO tool_call fact.
        {
            "kind": "tool_use_start",
            "payload": {"tool_call_id": "d1", "tool_name": "delegate"},
            "ts": None,
        },
        {
            "kind": "plan_review_required",
            "payload": {"checkpoint_id": "ck1", "steps": [], "pending": []},
            "ts": None,
        },
    ]


def _expected_window(annotated: str) -> list[LLMMessage]:
    return [
        LLMMessage(role="system", content="你是 CEO。"),
        LLMMessage(role="user", content="调研并撰写"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="c1",
                    type="function",
                    function=ToolCallFunction(name="search", arguments='{"q": "x"}'),
                )
            ],
            reasoning_content="让我先查一下",
        ),
        LLMMessage(role="tool", content=annotated, tool_call_id="c1"),
        LLMMessage(role="user", content="换个角度再想想"),
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


async def test_journal_round_trips_through_postgres_and_window_folds(session_factory):
    # Persist the pause journal, load it back, and assert the window folds IDENTICALLY to
    # the in-memory fold — the DB layer (JSONB) preserves every fact payload, so a resume
    # rebuilding the captain window from the DB matches one rebuilt in-process.
    turn_id, conv_id = str(uuid4()), str(uuid4())
    entries = _paused_journal()
    annotated = entries[4]["payload"]["result"]  # the tool_call fact's full-text result

    async with session_factory() as s:
        await TurnJournalRepository(s).record(
            turn_id=turn_id, conversation_id=conv_id, trace_id="trace1", entries=entries
        )
    async with session_factory() as s:
        loaded = await TurnJournalRepository(s).load(turn_id)

    # The stored stream comes back kind-for-kind, payload-for-payload (JSONB faithful).
    assert [e["kind"] for e in loaded] == [e["kind"] for e in entries]
    assert loaded == entries

    # THE GATE: the window folds the same from the DB-loaded journal as from the in-memory
    # one — and equals the hand-computed captain transcript (suspended delegate has no tool
    # message; the completed search keeps its full-text annotated result; the note folds in).
    in_memory = window_from_journal(entries)
    from_db = window_from_journal(loaded)
    assert from_db == in_memory
    assert from_db == _expected_window(annotated)

    # DISPLAY side also survives: the plan_review card + the tool cards project back.
    runs = runs_from_entries(loaded)
    assert runs is not None
    assert any(e["type"] == "plan_review_required" for e in runs["events"])


async def test_find_latest_multi_agent_execution(session_factory):
    # 跨回合同图追加「latest」解析：同对话最新一张 multi_agent 图；辩论图不可追加；
    # prefer_turn_id = 本回合图优先；exclude_turn_id 仅 prompt 回显排除当前回合；
    # 无候选 → None（调用方显式回错，禁静默新建）。
    conv = str(uuid4())
    m1, m2, m_debate = str(uuid4()), str(uuid4()), str(uuid4())

    def plan_entries(eid: str, plan_type: str = "multi_agent") -> list[dict]:
        return [
            {
                "kind": "run_plan",
                "payload": {
                    "execution_id": eid,
                    "plan_type": plan_type,
                    "task_summary": "",
                    "agents": [],
                    "runs": [],
                },
                "ts": None,
            }
        ]

    async with session_factory() as s:
        repo = TurnJournalRepository(s)
        await repo.record(
            turn_id=m1, conversation_id=conv, trace_id=None, entries=plan_entries("exec1")
        )
        await repo.record(
            turn_id=m2, conversation_id=conv, trace_id=None, entries=plan_entries("exec2")
        )
        # 最新写入的是辩论图——必须被 plan_type 过滤，不得当成「最近可追加」。
        await repo.record(
            turn_id=m_debate,
            conversation_id=conv,
            trace_id=None,
            entries=plan_entries("exec3", plan_type="debate"),
        )

    async with session_factory() as s:
        repo = TurnJournalRepository(s)
        assert (
            await repo.find_latest_multi_agent_execution(conversation_id=conv) == "exec2"
        )
        assert (
            await repo.find_latest_multi_agent_execution(
                conversation_id=conv, exclude_turn_id=m2
            )
            == "exec1"
        )
        # 本回合优先：即便对话级最新是 exec2，prefer m1 仍应落到 exec1。
        assert (
            await repo.find_latest_multi_agent_execution(
                conversation_id=conv, prefer_turn_id=m1
            )
            == "exec1"
        )
        assert (
            await repo.find_latest_multi_agent_execution(
                conversation_id=conv, prefer_turn_id=m1, prefer_only=True
            )
            == "exec1"
        )
        # prefer 空 turn：prefer_only → None；默认 fallthrough → 对话级最新。
        empty_turn = str(uuid4())
        assert (
            await repo.find_latest_multi_agent_execution(
                conversation_id=conv, prefer_turn_id=empty_turn, prefer_only=True
            )
            is None
        )
        assert (
            await repo.find_latest_multi_agent_execution(
                conversation_id=conv, prefer_turn_id=empty_turn
            )
            == "exec2"
        )
        assert (
            await repo.find_latest_multi_agent_execution(conversation_id=str(uuid4()))
            is None
        )


async def test_find_latest_mlr_execution_namespaced_synthesizer(session_factory):
    """P0：DAG 铸造 del_<uuid>_synthesizer 须被 MLR 宿主查找命中（与 appendable 对齐）。"""
    conv = str(uuid4())
    mid = str(uuid4())
    rid = "del_2468005e-cf60-4032-84e4-9eca57633098_synthesizer"
    entries = [
        {
            "kind": "run_plan",
            "payload": {
                "execution_id": "exec_mlr",
                "plan_type": "multi_agent",
                "task_summary": "",
                "agents": [],
                "runs": [
                    {"id": "del_x_lens_0", "agent_id": "del_x_lens_0"},
                    {"id": rid, "agent_id": rid},
                ],
            },
            "ts": None,
        }
    ]
    async with session_factory() as s:
        await TurnJournalRepository(s).record(
            turn_id=mid, conversation_id=conv, trace_id=None, entries=entries
        )
    async with session_factory() as s:
        repo = TurnJournalRepository(s)
        assert await repo.find_latest_multi_agent_execution(conversation_id=conv) == (
            "exec_mlr"
        )
        assert await repo.find_latest_mlr_execution(conversation_id=conv) == "exec_mlr"



async def test_record_replaces_turn_wholesale(session_factory):
    # A resume reuses the turn_id and re-records on completion: record must REPLACE the
    # live-band prefix occupancy, not append — so the window never doubles up.
    turn_id, conv_id = str(uuid4()), str(uuid4())
    entries = _paused_journal()

    async with session_factory() as s:
        repo = TurnJournalRepository(s)
        await repo.record(turn_id=turn_id, conversation_id=conv_id, trace_id=None, entries=entries)
    async with session_factory() as s:
        await TurnJournalRepository(s).record(
            turn_id=turn_id, conversation_id=conv_id, trace_id=None, entries=entries
        )
    async with session_factory() as s:
        loaded = await TurnJournalRepository(s).load(turn_id)

    assert loaded == entries  # replaced, not doubled
