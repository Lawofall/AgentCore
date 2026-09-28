"""Integration tests for the cost ledger write path (CostEventRepository).

Backed by a real PostgreSQL throwaway schema (auto-skips when none is reachable,
via the ``session_factory`` fixture). Pins the two properties the turn finalize
step depends on: the service envelope (user / conversation / message) is attached
to every run row, and the write is idempotent by ``run_id`` so a retried turn
never double-bills.

Also pins the **account-level** row shape (AI 改写 / 文档 description): a ledger
row that belongs to no conversation must persist, SUM into the account windows
(用量页 / 仪表盘 / 配额), and stay out of every conversation- or message-scoped
read — those are SQL-level properties, so only a real database can prove them.
"""

import pytest
from sqlalchemy import select

from agentcore.core.types import new_id
from agentcore.db.models import CostCall, CostEvent
from agentcore.db.repositories import CostEventRepository


def _run(
    run_id: str, *, role: str = "member", parent: str | None = None, total: int = 1000
) -> dict:
    """A per-run ledger payload in the runtime's ``asdict(RunCost)`` shape."""
    return {
        "run_id": run_id,
        "parent_run_id": parent,
        "agent_id": run_id,
        "role": role,
        "model": "deepseek-v4-pro",
        "tokens": {"input": 100, "output": 50, "reasoning": 0, "cache_hit": 60, "cache_miss": 40},
        "cost": {"input": 800, "cached": 100, "output": 200, "total": total},
        "cost_total_nano": total,
        "currency": "USD",
        "rounds": 1,
        "duration_ms": 500,
    }


async def test_record_runs_persists_with_envelope(session_factory):
    user_id, conv_id, msg_id = new_id(), new_id(), new_id()
    cap_id, mem_id = new_id(), new_id()

    async with session_factory() as session:
        written = await CostEventRepository(session).record_runs(
            user_id=user_id,
            conversation_id=conv_id,
            message_id=msg_id,
            runs=[
                _run(cap_id, role="captain", total=300),
                _run(mem_id, parent=cap_id, total=1200),
            ],
        )
    assert written == 2

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(CostEvent)
                    .where(CostEvent.message_id == msg_id)
                    .order_by(CostEvent.cost_total_nano)
                )
            )
            .scalars()
            .all()
        )

    assert len(rows) == 2
    captain, member = rows[0], rows[1]  # ordered by total asc (300, 1200)
    # Envelope attached by the repo (the runtime builders stay DB-unaware).
    for row in rows:
        assert row.user_id == user_id
        assert row.conversation_id == conv_id
        assert row.message_id == msg_id
        assert row.id  # minted per row (a Core bulk insert skips the ORM default)
    assert captain.role == "captain"
    assert captain.run_id == cap_id
    assert captain.parent_run_id is None
    assert member.role == "member"
    assert member.parent_run_id == cap_id
    assert member.cost_total_nano == 1200
    assert member.cost == {"input": 800, "cached": 100, "output": 200, "total": 1200}
    assert member.tokens["cache_hit"] == 60


async def test_record_runs_persists_namespaced_worker_ids(session_factory):
    # Regression: a real multi-agent turn's member/revision ids are NOT uuids —
    # delegated workers are ``del_<uuid>_N`` and revisions ``<run>_rev2``. The
    # ledger columns were once native uuid, so this single multi-row INSERT aborted
    # whole (DataError), silently dropping the turn's entire ledger — captain too.
    user_id, conv_id, msg_id = new_id(), new_id(), new_id()
    cap_id = new_id()  # captain root is a real uuid
    worker_id = f"del_{new_id()}_1"  # delegated worker — namespaced, not a uuid
    revision_id = f"{worker_id}_rev2"  # 定向唤回 续写 of that worker

    async with session_factory() as session:
        written = await CostEventRepository(session).record_runs(
            user_id=user_id,
            conversation_id=conv_id,
            message_id=msg_id,
            runs=[
                _run(cap_id, role="captain", total=300),
                _run(worker_id, parent=cap_id, total=1200),
                _run(revision_id, parent=worker_id, total=400),
            ],
        )
    assert written == 3

    async with session_factory() as session:
        rows = (
            (await session.execute(select(CostEvent).where(CostEvent.message_id == msg_id)))
            .scalars()
            .all()
        )
    by_id = {r.run_id: r for r in rows}
    assert set(by_id) == {cap_id, worker_id, revision_id}
    # the revision row hangs off its original worker (version chain reconstructable)
    assert by_id[revision_id].parent_run_id == worker_id
    assert by_id[worker_id].parent_run_id == cap_id
    assert by_id[revision_id].agent_id == revision_id


async def test_record_runs_is_idempotent_by_run_id(session_factory):
    # A retried turn re-sends the same run_ids; the second write must insert
    # nothing so a run is never double-billed (run_id unique, upsert do-nothing).
    user_id, conv_id, msg_id = new_id(), new_id(), new_id()
    run = _run(new_id(), role="captain", total=500)

    async with session_factory() as session:
        first = await CostEventRepository(session).record_runs(
            user_id=user_id, conversation_id=conv_id, message_id=msg_id, runs=[run]
        )
    async with session_factory() as session:
        second = await CostEventRepository(session).record_runs(
            user_id=user_id, conversation_id=conv_id, message_id=msg_id, runs=[run]
        )

    assert first == 1
    assert second == 0  # conflict on run_id → skipped

    async with session_factory() as session:
        rows = (
            (await session.execute(select(CostEvent).where(CostEvent.run_id == run["run_id"])))
            .scalars()
            .all()
        )
    assert len(rows) == 1


async def test_record_calls_is_idempotent_by_call_id(session_factory):
    """At-least-once drain retries must not double-bill the same call_id."""
    user_id, conv_id, msg_id = new_id(), new_id(), new_id()
    run_id = new_id()
    call = _call(f"call_{new_id()}", run_id=run_id, model="deepseek-v4-flash", total=2500)

    async with session_factory() as session:
        first = await CostEventRepository(session).record_calls(
            user_id=user_id,
            conversation_id=conv_id,
            message_id=msg_id,
            calls=[call],
            materialize_runs=True,
        )
    async with session_factory() as session:
        second = await CostEventRepository(session).record_calls(
            user_id=user_id,
            conversation_id=conv_id,
            message_id=msg_id,
            calls=[call],
            materialize_runs=True,
        )

    assert first == 1
    assert second == 0

    async with session_factory() as session:
        calls = (
            (await session.execute(select(CostCall).where(CostCall.call_id == call["call_id"])))
            .scalars()
            .all()
        )
        events = (
            (await session.execute(select(CostEvent).where(CostEvent.run_id == run_id)))
            .scalars()
            .all()
        )
    assert len(calls) == 1
    assert calls[0].cost_total_nano == 2500
    assert len(events) == 1
    assert events[0].cost_total_nano == 2500


async def test_record_calls_persists_platform_credential_id(session_factory):
    """Platform-paid call rows keep the pool-member id; BYOK rows stay NULL."""
    from dataclasses import asdict

    from agentcore.llm.provider.protocol import TokenUsage
    from agentcore.runtime.costing import priced_call_cost

    user_id, conv_id, msg_id = new_id(), new_id(), new_id()
    usage = TokenUsage(input_tokens=10, output_tokens=2, cache_miss_tokens=10)
    platform = priced_call_cost(
        model="deepseek-v4-flash",
        usage=usage,
        role="captain",
        run_id=new_id(),
        call_id=f"call_{new_id()}",
        credential_source="platform",
        platform_credential_id="go-1",
    )
    byok = priced_call_cost(
        model="deepseek-v4-flash",
        usage=usage,
        role="captain",
        run_id=new_id(),
        call_id=f"call_{new_id()}",
        credential_source="user",
        platform_credential_id="go-1",  # must be ignored on BYOK
    )
    async with session_factory() as session:
        await CostEventRepository(session).record_calls(
            user_id=user_id,
            conversation_id=conv_id,
            message_id=msg_id,
            calls=[asdict(platform), asdict(byok)],
        )
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(CostCall).where(CostCall.call_id.in_([platform.call_id, byok.call_id]))
                )
            )
            .scalars()
            .all()
        )
    by_id = {r.call_id: r for r in rows}
    assert by_id[platform.call_id].platform_credential_id == "go-1"
    assert by_id[byok.call_id].platform_credential_id is None


async def test_record_runs_empty_is_noop(session_factory):
    async with session_factory() as session:
        written = await CostEventRepository(session).record_runs(
            user_id=new_id(), conversation_id=new_id(), message_id=new_id(), runs=[]
        )
    assert written == 0


def _call(
    call_id: str,
    *,
    run_id: str,
    model: str,
    total: int = 1000,
    estimated: int = 0,
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> dict:
    return {
        "call_id": call_id,
        "run_id": run_id,
        "parent_run_id": None,
        "agent_id": run_id,
        "role": "captain",
        "model": model,
        "tokens": {
            "input": input_tokens,
            "output": output_tokens,
            "reasoning": 0,
            "cache_hit": 0,
            "cache_miss": input_tokens,
        },
        "cost": {"input": total // 2, "cached": 0, "output": total // 2, "total": total},
        "cost_total_nano": total,
        "cost_estimated_nano": estimated,
        "currency": "USD",
        "duration_ms": 100,
    }


async def test_aggregate_by_model_groups_cost_calls(session_factory):
    """Per-model payroll must GROUP BY ``cost_calls.model`` (not ``cost_events``).

    A single run with two models must attribute each call to its own model — the
    reason we forbid aggregating ``cost_events.model`` (first-call only).
    """
    from datetime import UTC, datetime, timedelta

    user_id, conv_id, msg_id = new_id(), new_id(), new_id()
    run_id = new_id()
    other_user = new_id()

    async with session_factory() as session:
        repo = CostEventRepository(session)
        await repo.record_calls(
            user_id=user_id,
            conversation_id=conv_id,
            message_id=msg_id,
            calls=[
                _call(new_id(), run_id=run_id, model="deepseek-v4-pro", total=5000, input_tokens=200),
                _call(
                    new_id(),
                    run_id=run_id,
                    model="deepseek-v4-flash",
                    total=1000,
                    input_tokens=80,
                    output_tokens=20,
                ),
                _call(new_id(), run_id=run_id, model="deepseek-v4-pro", total=3000, input_tokens=100),
            ],
        )
        # Another account's spend must not leak into a scoped aggregate.
        await repo.record_calls(
            user_id=other_user,
            conversation_id=new_id(),
            message_id=new_id(),
            calls=[_call(new_id(), run_id=new_id(), model="deepseek-v4-pro", total=99999)],
        )

    since = datetime.now(UTC) - timedelta(days=1)
    async with session_factory() as session:
        rows = await CostEventRepository(session).aggregate_by_model_for_window(
            user_id=user_id, since=since
        )

    assert [r["model"] for r in rows] == ["deepseek-v4-pro", "deepseek-v4-flash"]
    by_model = {r["model"]: r for r in rows}
    assert by_model["deepseek-v4-pro"] == {
        "model": "deepseek-v4-pro",
        "calls": 2,
        "tokens_total": 400,  # (200+50) + (100+50)
        "cost_total": 8000,
        "cost_estimated_total": 0,
    }
    assert by_model["deepseek-v4-flash"] == {
        "model": "deepseek-v4-flash",
        "calls": 1,
        "tokens_total": 100,  # 80+20
        "cost_total": 1000,
        "cost_estimated_total": 0,
    }

    # Platform-wide includes the other user's pro spend.
    async with session_factory() as session:
        platform = await CostEventRepository(session).aggregate_by_model_for_window(since=since)
    platform_by = {r["model"]: r for r in platform}
    assert platform_by["deepseek-v4-pro"]["cost_total"] == 8000 + 99999
    assert platform_by["deepseek-v4-pro"]["calls"] == 3


async def _seed_turn_and_account_level_spend(session_factory, *, username: str):
    """One conversation turn + one account-level (no-conversation) call, same account.

    Returns ``(user_id, conv_id, msg_id, turn_call, assist_call)``.
    """
    from dataclasses import asdict

    from agentcore.db.repositories import UserRepository
    from agentcore.llm.provider.protocol import TokenUsage
    from agentcore.runtime.costing import (
        PERSONA_REWRITE,
        ROLE_ASSIST,
        ROLE_CAPTAIN,
        priced_call_cost,
    )

    conv_id, msg_id = new_id(), new_id()
    async with session_factory() as session:
        user = await UserRepository(session).create(
            username=username, display_name=username
        )
    user_id = user.user_id

    turn_call = priced_call_cost(
        model="deepseek-v4-pro",
        usage=TokenUsage(input_tokens=300, output_tokens=40),
        role=ROLE_CAPTAIN,
        run_id=new_id(),
        persona="CEO",
        call_id=f"call_{new_id()}",
        credential_source="platform",
    )
    # AI 改写: no run tree, no conversation, no turn — only an account.
    assist_call = priced_call_cost(
        model="deepseek-v4-flash",
        usage=TokenUsage(input_tokens=120, output_tokens=25),
        role=ROLE_ASSIST,
        persona=PERSONA_REWRITE,
        call_id=f"call_{new_id()}",
        credential_source="platform",
    )

    async with session_factory() as session:
        repo = CostEventRepository(session)
        await repo.record_calls(
            user_id=user_id,
            conversation_id=conv_id,
            message_id=msg_id,
            calls=[asdict(turn_call)],
            materialize_runs=True,
        )
        await repo.record_calls(
            user_id=user_id,
            conversation_id=None,
            message_id=None,
            calls=[asdict(assist_call)],
            materialize_runs=True,
        )
    return user_id, conv_id, msg_id, turn_call, assist_call


async def test_account_level_row_persists_without_a_conversation(session_factory):
    """AI 改写 / 文档 description spend has no conversation and is billed anyway.

    Before「放宽账本」the three ledger tables took ``conversation_id`` NOT NULL, so
    the call meter dropped this spend rather than hang it on an unrelated chat.
    The row now persists with NULL conversation + NULL message and the ``assist``
    role — never a fabricated conversation id.
    """
    from agentcore.runtime.costing import ROLE_ASSIST

    user_id, _conv, _msg, _turn, assist_call = await _seed_turn_and_account_level_spend(
        session_factory, username="acct-level-write"
    )

    async with session_factory() as session:
        call_row = (
            await session.execute(
                select(CostCall).where(CostCall.call_id == assist_call.call_id)
            )
        ).scalar_one()
        event_row = (
            await session.execute(
                select(CostEvent).where(CostEvent.run_id == assist_call.run_id)
            )
        ).scalar_one()

    for row in (call_row, event_row):
        assert row.user_id == user_id
        assert row.conversation_id is None
        assert row.message_id is None
        assert row.role == ROLE_ASSIST
        assert row.persona == "AI 改写"
    assert call_row.cost_total_nano == assist_call.cost_total_nano
    # Materialized per-run aggregate mirrors the authoritative call detail.
    assert event_row.cost_total_nano == assist_call.cost_total_nano


async def test_account_level_row_sums_into_account_reads(session_factory):
    """Every account-scoped read must see the no-conversation row.

    These are the surfaces the decision was made for: 用量页 / 仪表盘 today+month
    windows, the 7-day trend, the per-model split, and the admin per-user payroll.
    Account-level spend counts as money but not as a 请求 — ``turns`` counts
    distinct ``message_id`` and this row has none.
    """
    from datetime import UTC, datetime, timedelta

    user_id, _conv, _msg, turn_call, assist_call = (
        await _seed_turn_and_account_level_spend(
            session_factory, username="acct-level-read"
        )
    )
    both = turn_call.cost_total_nano + assist_call.cost_total_nano
    assert assist_call.cost_total_nano > 0  # else the test proves nothing
    since = datetime.now(UTC) - timedelta(days=1)

    async with session_factory() as session:
        repo = CostEventRepository(session)
        window = await repo.aggregate_for_window(user_id=user_id, since=since)
        daily = await repo.aggregate_daily_for_window(user_id=user_id, since=since)
        by_model = await repo.aggregate_by_model_for_window(user_id=user_id, since=since)
        by_user = await repo.aggregate_by_user_for_window(since=since)

    assert window["cost"]["total"] == both
    assert window["usage"]["input"] == 300 + 120
    # 请求数 unmoved: the account-level row belongs to no assistant turn.
    assert window["turns"] == 1
    assert sum(daily.values()) == both

    models = {row["model"]: row for row in by_model}
    assert models["deepseek-v4-flash"]["cost_total"] == assist_call.cost_total_nano
    assert models["deepseek-v4-pro"]["cost_total"] == turn_call.cost_total_nano

    payroll = {row["user_id"]: row for row in by_user}
    assert payroll[user_id]["cost_total"] == both
    assert payroll[user_id]["turns"] == 1


async def test_account_level_row_stays_out_of_conversation_reads(session_factory):
    """No conversation may claim it, and no turn payroll may show it.

    The exclusions are structural (equality / IN / NOT NULL filters), not a
    special case — a row with no conversation simply cannot match one.
    """
    user_id, conv_id, msg_id, turn_call, _assist = (
        await _seed_turn_and_account_level_spend(
            session_factory, username="acct-level-scoped"
        )
    )

    async with session_factory() as session:
        repo = CostEventRepository(session)
        conversation = await repo.aggregate_for_conversation(conv_id, user_id=user_id)
        payroll = list(await repo.list_for_message(msg_id, user_id=user_id))
        roster = await repo.aggregate_cost_by_conversations([conv_id])
        by_message = await repo.aggregate_cost_by_message_for_conversation(conv_id)

    assert conversation["cost"]["total"] == turn_call.cost_total_nano
    assert [row.run_id for row in payroll] == [turn_call.run_id]
    assert roster == {conv_id: turn_call.cost_total_nano}
    assert by_message == {msg_id: turn_call.cost_total_nano}


async def test_account_level_spend_counts_against_quota(session_factory):
    """额度数字会涨: ``enforce_quota`` SUMs the same account window.

    A cap below the account-level spend must refuse the next turn (money is
    money).
    """
    from dataclasses import asdict

    from agentcore.conversation.quota import QuotaLimits, enforce_quota
    from agentcore.core.errors import QuotaExceededError
    from agentcore.llm.provider.protocol import TokenUsage
    from agentcore.runtime.costing import PERSONA_DESCRIPTION, ROLE_ASSIST, priced_call_cost

    user_id = new_id()
    # 文档 description 自动补 — the account's only spend.
    assist_call = priced_call_cost(
        model="deepseek-v4-flash",
        usage=TokenUsage(input_tokens=2000, output_tokens=400),
        role=ROLE_ASSIST,
        persona=PERSONA_DESCRIPTION,
        call_id=f"call_{new_id()}",
        credential_source="platform",
    )
    assert assist_call.cost_total_nano > 0

    async with session_factory() as session:
        await CostEventRepository(session).record_calls(
            user_id=user_id,
            conversation_id=None,
            message_id=None,
            calls=[asdict(assist_call)],
            materialize_runs=True,
        )

    async with session_factory() as session:
        repo = CostEventRepository(session)
        with pytest.raises(QuotaExceededError) as excinfo:
            await enforce_quota(
                repo,
                user_id,
                limits=QuotaLimits(
                    daily_tokens=0,
                    monthly_cost_nano=0,
                    daily_cost_nano=assist_call.cost_total_nano,
                ),
            )
        assert excinfo.value.dimension == "daily_cost"


async def test_materialize_message_runs_upserts_worker_role_and_run(session_factory):
    """cost_calls authority → cost_events with member role / parent_run_id / persona.

    Pins the cloud finalize reconcile path: even when in-memory ``cost_runs`` only
    carried the captain, materializing from call details restores the worker row
    without double-billing (one cost_events row per run_id).
    """
    from dataclasses import asdict

    from agentcore.llm.provider.protocol import TokenUsage
    from agentcore.runtime.costing import ROLE_CAPTAIN, ROLE_MEMBER, priced_call_cost

    user_id, conv_id, msg_id = new_id(), new_id(), new_id()
    cap_id, mem_id = new_id(), f"del_{new_id()}_researcher"
    cap_call = priced_call_cost(
        model="deepseek-v4-flash",
        usage=TokenUsage(input_tokens=80, output_tokens=10),
        role=ROLE_CAPTAIN,
        run_id=cap_id,
        persona="CEO",
        call_id=f"call_{new_id()}",
        credential_source="platform",
    )
    mem_call = priced_call_cost(
        model="deepseek-v4-flash",
        usage=TokenUsage(input_tokens=400, output_tokens=60),
        role=ROLE_MEMBER,
        run_id=mem_id,
        parent_run_id=cap_id,
        agent_id=mem_id,
        persona="调研员",
        call_id=f"call_{new_id()}",
        credential_source="platform",
    )

    async with session_factory() as session:
        repo = CostEventRepository(session)
        await repo.record_calls(
            user_id=user_id,
            conversation_id=conv_id,
            message_id=msg_id,
            calls=[asdict(cap_call), asdict(mem_call)],
            materialize_runs=False,
        )
        # Incomplete captain-only dual-write (the undercount bug shape).
        await repo.record_runs(
            user_id=user_id,
            conversation_id=conv_id,
            message_id=msg_id,
            runs=[
                {
                    "run_id": cap_id,
                    "parent_run_id": None,
                    "agent_id": cap_id,
                    "role": ROLE_CAPTAIN,
                    "persona": "CEO",
                    "model": "deepseek-v4-flash",
                    "tokens": {"input": 10, "output": 1},
                    "cost": {"total": 1},
                    "cost_total_nano": 1,  # deliberately under-counted
                    "currency": "USD",
                    "rounds": 1,
                    "duration_ms": 1,
                }
            ],
        )
        run_ids = await repo.materialize_message_runs(
            user_id=user_id,
            conversation_id=conv_id,
            message_id=msg_id,
        )
    assert run_ids == {cap_id, mem_id}

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(CostEvent).where(CostEvent.message_id == msg_id)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2
    by_role = {r.role: r for r in rows}
    assert by_role[ROLE_MEMBER].run_id == mem_id
    assert by_role[ROLE_MEMBER].parent_run_id == cap_id
    assert by_role[ROLE_MEMBER].persona == "调研员"
    assert by_role[ROLE_MEMBER].cost_total_nano == mem_call.cost_total_nano
    # Upsert replaced the under-counted captain row from calls (not DO NOTHING stale).
    assert by_role[ROLE_CAPTAIN].cost_total_nano == cap_call.cost_total_nano
    assert by_role[ROLE_CAPTAIN].cost_total_nano != 1
