"""Integration tests for the cost & usage observability endpoints.

Auto-skips (via the shared ``client`` fixture) when no PostgreSQL is reachable.
Seeds the ``cost_events`` ledger directly (the write path is covered by
test_cost_ledger.py) and exercises the three reads end-to-end: the team payroll
(``/messages/{id}/cost``), the conversation total (``/conversations/{id}/cost``),
and the account dashboard (``/usage/summary``) — including auth + IDOR scoping.
"""

from datetime import UTC, datetime, timedelta

from agentcore.core.types import new_id
from agentcore.db.models import CostEvent
from agentcore.db.repositories import ConversationRepository, CostEventRepository
from tests.integration.conftest import register_and_login


def _run(
    run_id: str,
    *,
    role: str = "member",
    parent: str | None = None,
    model: str = "deepseek-v4-pro",
    total: int = 1000,
) -> dict:
    """A per-run ledger payload in the runtime's ``asdict(RunCost)`` shape."""
    return {
        "run_id": run_id,
        "parent_run_id": parent,
        "agent_id": run_id,
        "role": role,
        "model": model,
        "tokens": {"input": 100, "output": 50, "reasoning": 10, "cache_hit": 60, "cache_miss": 40},
        "cost": {"input": 800, "cached": 100, "output": 200, "total": total},
        "cost_total_nano": total,
        "currency": "CNY",
        "rounds": 2,
        "duration_ms": 500,
    }


async def test_usage_endpoints_require_auth(client):
    assert (await client.get("/v1/usage/summary")).status_code == 401
    assert (await client.get(f"/v1/messages/{new_id()}/cost")).status_code == 401
    assert (await client.get(f"/v1/conversations/{new_id()}/cost")).status_code == 401


async def test_message_cost_returns_payroll_and_turn_total(client, session_factory):
    user_id = await register_and_login(client, "payrolluser")

    conv_id, msg_id = new_id(), new_id()
    cap_id, mem_id = new_id(), new_id()
    async with session_factory() as session:
        await CostEventRepository(session).record_runs(
            user_id=user_id,
            conversation_id=conv_id,
            message_id=msg_id,
            runs=[
                _run(cap_id, role="captain", model="deepseek-v4-flash", total=300),
                _run(mem_id, parent=cap_id, total=1200),
            ],
        )

    r = await client.get(f"/v1/messages/{msg_id}/cost")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["message_id"] == msg_id
    assert len(body["agents"]) == 2
    # Captain first (written first, ordered created_at asc).
    assert body["agents"][0]["role"] == "captain"
    assert body["agents"][1]["role"] == "member"
    # Turn total = sum of the two priced rows; rounds summed.
    assert body["cost"]["total"] == 1500
    assert body["cost"]["input"] == 1600
    assert body["cost"]["currency"] == "CNY"
    assert body["rounds"] == 4
    # Display CNY is nano/1e9 (no FX): 1500 nano-CNY → tiny, rounds
    # to 0.00 元 but the field is present and a float.
    assert isinstance(body["cost"]["cny_total"], float)
    # Usage rolled up from both rows (short ledger keys).
    assert body["usage"]["input"] == 200
    assert body["usage"]["cache_hit"] == 120


async def test_message_cost_is_user_scoped(client, session_factory, new_client):
    # A second user must never see the first user's payroll (IDOR): the query is
    # scoped by user_id, so a non-owner gets zeros + an empty roster (no leak).
    owner_id = await register_and_login(client, "owneru")

    msg_id = new_id()
    async with session_factory() as session:
        await CostEventRepository(session).record_runs(
            user_id=owner_id,
            conversation_id=new_id(),
            message_id=msg_id,
            runs=[_run(new_id(), role="captain", total=500)],
        )

    async with new_client() as other:
        await register_and_login(other, "otheru")
        r = await other.get(f"/v1/messages/{msg_id}/cost")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agents"] == []
    assert body["cost"]["total"] == 0


async def test_conversation_cost_totals_and_ownership(
    client, session_factory, new_client
):
    user_id = await register_and_login(client, "convuser")

    # Own the conversation so the read passes the ownership gate.
    async with session_factory() as session:
        conv = await ConversationRepository(session).create(user_id=user_id, title="t")
        conv_id = conv.id
        await CostEventRepository(session).record_runs(
            user_id=user_id,
            conversation_id=conv_id,
            message_id=new_id(),
            runs=[_run(new_id(), role="captain", total=300)],
        )
        await CostEventRepository(session).record_runs(
            user_id=user_id,
            conversation_id=conv_id,
            message_id=new_id(),
            runs=[_run(new_id(), role="captain", total=700)],
        )

    r = await client.get(f"/v1/conversations/{conv_id}/cost")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["conversation_id"] == conv_id
    assert body["cost"]["total"] == 1000
    assert body["turns"] == 2  # two distinct message_ids

    # A non-owner is 404'd (consistent with other conversation reads).
    async with new_client() as other:
        await register_and_login(other, "convother")
        r = await other.get(f"/v1/conversations/{conv_id}/cost")
    assert r.status_code == 404


async def test_usage_summary_windows_and_quota(client, session_factory):
    user_id = await register_and_login(client, "summaryuser")

    async with session_factory() as session:
        await CostEventRepository(session).record_runs(
            user_id=user_id,
            conversation_id=new_id(),
            message_id=new_id(),
            runs=[_run(new_id(), role="captain", total=2500)],
        )

    r = await client.get("/v1/usage/summary")
    assert r.status_code == 200, r.text
    body = r.json()

    # The seeded row lands in both today's and this month's windows.
    assert body["today"]["cost"]["total"] == 2500
    assert body["month"]["cost"]["total"] == 2500
    assert body["today"]["requests"] == 1
    assert body["today"]["usage"]["input"] == 100
    # The 7-day trend is a fixed-length series; today's spend is its last point.
    trend = body["recent_daily_cost"]
    assert len(trend) == 7
    assert trend[-1]["cost_total"] == 2500
    assert sum(p["cost_total"] for p in trend) == 2500
    # Quota defaults (决策④ / F2) are surfaced. Read from settings so the
    # assertion tracks the config group instead of hard-coding numbers.
    from agentcore.config import settings

    assert body["quota"]["daily_tokens"] == settings.quota_daily_tokens
    assert body["quota"]["monthly_cost_nano"] == int(
        settings.quota_monthly_cost_cny * 1_000_000_000
    )
    assert body["quota"]["daily_cost_nano"] == int(settings.quota_daily_cost_cny * 1_000_000_000)
    assert "daily_requests" not in body["quota"]
    assert "cny_per_usd" not in body


async def test_usage_summary_quota_shows_global_limits(client):
    # Every account sees the same global quota_* caps (per-user override absent).
    from agentcore.config import settings

    await register_and_login(client, "summaryquota")

    r = await client.get("/v1/usage/summary")

    assert r.status_code == 200, r.text
    quota = r.json()["quota"]
    assert quota["daily_tokens"] == settings.quota_daily_tokens
    assert quota["monthly_cost_nano"] == int(settings.quota_monthly_cost_cny * 1_000_000_000)
    assert quota["daily_cost_nano"] == int(settings.quota_daily_cost_cny * 1_000_000_000)
    assert "daily_requests" not in quota


async def test_usage_summary_recent_daily_cost_buckets_by_utc_day(
    client, session_factory
):
    # 近 7 日趋势: spend is bucketed into UTC days and zero-filled to a 7-point,
    # oldest-first series ending today; rows older than the window are excluded.
    user_id = await register_and_login(client, "trenduser")

    now = datetime.now(UTC)
    today = now.replace(hour=12, minute=0, second=0, microsecond=0)
    three_ago = today - timedelta(days=3)
    six_ago = today - timedelta(days=6)
    eight_ago = today - timedelta(days=8)  # outside the 7-day window

    async with session_factory() as session:
        for created, total in [
            (today, 500),
            (three_ago, 300),
            (six_ago, 100),
            (eight_ago, 9999),  # must be excluded
        ]:
            session.add(
                CostEvent(
                    user_id=user_id,
                    conversation_id=new_id(),
                    message_id=new_id(),
                    run_id=new_id(),
                    role="captain",
                    model="deepseek-v4-pro",
                    cost_total_nano=total,
                    created_at=created,
                )
            )
        await session.commit()

    r = await client.get("/v1/usage/summary")
    assert r.status_code == 200, r.text
    points = r.json()["recent_daily_cost"]

    assert len(points) == 7
    # Oldest-first, ending today.
    assert points[-1]["date"] == today.date().isoformat()
    assert points[0]["date"] == six_ago.date().isoformat()
    by_date = {p["date"]: p["cost_total"] for p in points}
    assert by_date[today.date().isoformat()] == 500
    assert by_date[three_ago.date().isoformat()] == 300
    assert by_date[six_ago.date().isoformat()] == 100
    # The 8-days-ago row is outside the window, so the series total excludes it.
    assert sum(p["cost_total"] for p in points) == 900


async def test_usage_summary_empty_is_zero(client):
    await register_and_login(client, "emptyuser")

    r = await client.get("/v1/usage/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["today"]["cost"]["total"] == 0
    assert body["today"]["requests"] == 0
    assert body["month"]["usage"]["input"] == 0
    # The trend is still a fixed 7-point series, all zero.
    assert [p["cost_total"] for p in body["recent_daily_cost"]] == [0] * 7
