"""RunSession durable roster — repository + bridge + TTL sweep (乙 热修 P3).

Backed by real PostgreSQL via the ``session_factory`` fixture (auto-skips when none
is reachable). Pins the round trip that makes 定向唤回 survive a restart / eviction:
upsert-by-run_id (insert then conflict-update), the load/save bridge the pipeline
wires into delegate / revise, and the 7-day idle TTL sweep.
"""

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import update

from agentcore.config import settings
from agentcore.db.models import Conversation, RunSessionRow
from agentcore.db.repositories import RunSessionRepository
from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime import session_persistence as persist_mod
from agentcore.runtime import session_retention as retention_mod
from agentcore.runtime.runs import RunSession, RunSpec


async def _own(session_factory, cid: str) -> None:
    async with session_factory() as s:
        s.add(Conversation(id=cid, user_id=str(uuid4())))
        await s.commit()
from agentcore.runtime.runs.types import RunPolicy


def _session(run_id: str, content: str, recall: int = 0) -> RunSession:
    spec = RunSpec(run_id=run_id, task="做A", role="研究员", policy=RunPolicy())
    return RunSession(
        run_id=run_id,
        spec=spec,
        transcript=[
            LLMMessage(role="system", content="SYS"),
            LLMMessage(role="user", content="原始请求"),
            LLMMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id="c1",
                        function=ToolCallFunction(name="web_search", arguments="{}"),
                    )
                ],
            ),
            LLMMessage(role="tool", content="结果", tool_call_id="c1"),
            LLMMessage(role="assistant", content=content),
        ],
        content=content,
        recall_count=recall,
    )


async def test_upsert_inserts_then_conflict_updates(session_factory):
    cid = str(uuid4())
    await _own(session_factory, cid)
    async with session_factory() as s:
        repo = RunSessionRepository(s)
        await repo.upsert(
            conversation_id=cid,
            run_id="del_x_1",
            spec={"run_id": "del_x_1"},
            transcript=[{"role": "assistant", "content": "v1"}],
            content="v1",
            recall_count=0,
        )

    async with session_factory() as s:
        row = await RunSessionRepository(s).get("del_x_1")
    assert row is not None
    assert row.conversation_id == cid
    assert row.content == "v1"
    assert row.recall_count == 0
    first_updated = row.updated_at

    # Same run_id again → conflict path updates content / transcript / recall_count
    # and advances updated_at (which the TTL sweep reads), without a second row.
    async with session_factory() as s:
        await RunSessionRepository(s).upsert(
            conversation_id=cid,
            run_id="del_x_1",
            spec={"run_id": "del_x_1"},
            transcript=[{"role": "assistant", "content": "v2"}],
            content="v2",
            recall_count=1,
        )

    async with session_factory() as s:
        row2 = await RunSessionRepository(s).get("del_x_1")
    assert row2.content == "v2"
    assert row2.recall_count == 1
    assert row2.updated_at >= first_updated


async def test_save_load_bridge_round_trips(session_factory, monkeypatch):
    # The bridge uses telemetry_session_factory → repoint it at the test schema.
    monkeypatch.setattr(persist_mod, "telemetry_session_factory", session_factory)
    cid = str(uuid4())
    await _own(session_factory, cid)
    session = _session("del_y_1", "最终产出", recall=2)

    await persist_mod.save_run_session(cid, session)
    loaded = await persist_mod.load_run_session("del_y_1")

    assert loaded is not None
    assert loaded.run_id == "del_y_1"
    assert loaded.content == "最终产出"
    assert loaded.recall_count == 2
    # full transcript — incl. the tool-call turn + tool result — replays for continue_run
    assert [m.role for m in loaded.transcript] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert loaded.transcript[2].tool_calls[0].function.name == "web_search"
    assert loaded.transcript[3].tool_call_id == "c1"
    assert loaded.spec.role == "研究员"


async def test_load_miss_returns_none(session_factory, monkeypatch):
    monkeypatch.setattr(persist_mod, "telemetry_session_factory", session_factory)
    assert await persist_mod.load_run_session("nope_never_saved") is None


async def test_retention_sweep_prunes_aged_and_batches(session_factory, monkeypatch):
    monkeypatch.setattr(retention_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(settings, "session_roster_persist_enabled", True)
    monkeypatch.setattr(settings, "session_roster_retention_days", 7)
    # batch limit 2 with 3 aged rows → the loop must do >1 round to clear them all.
    monkeypatch.setattr(settings, "session_roster_sweep_batch_limit", 2)
    cid = str(uuid4())
    await _own(session_factory, cid)

    async with session_factory() as s:
        repo = RunSessionRepository(s)
        for i in range(3):
            await repo.upsert(
                conversation_id=cid,
                run_id=f"aged_{i}",
                spec={},
                transcript=[],
                content="x",
                recall_count=0,
            )
        await repo.upsert(
            conversation_id=cid,
            run_id="fresh",
            spec={},
            transcript=[],
            content="x",
            recall_count=0,
        )

    # Age the three past the 7-day window; leave `fresh` at now().
    aged = datetime.now() - timedelta(days=10)
    async with session_factory() as s:
        await s.execute(
            update(RunSessionRow)
            .where(RunSessionRow.run_id.in_(["aged_0", "aged_1", "aged_2"]))
            .values(updated_at=aged)
        )
        await s.commit()

    deleted = await retention_mod.run_session_retention_sweep()

    assert deleted == 3  # all aged rows, cleared across multiple batches
    async with session_factory() as s:
        survivor = await RunSessionRepository(s).get("fresh")
        gone = await RunSessionRepository(s).get("aged_0")
    assert survivor is not None  # recently-touched session is kept
    assert gone is None


async def test_retention_sweep_noop_when_disabled(session_factory, monkeypatch):
    monkeypatch.setattr(retention_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(settings, "session_roster_persist_enabled", False)
    assert await retention_mod.run_session_retention_sweep() == 0
