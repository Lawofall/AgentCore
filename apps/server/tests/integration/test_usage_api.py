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
    assert (await client.get("/v1/usage/activity")).status_code == 401
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
    # The 30-day trend is a fixed-length series; today's spend is its last point.
    trend = body["recent_daily_cost"]
    assert len(trend) == 30
    assert trend[-1]["cost_total"] == 2500
    assert sum(p["cost_total"] for p in trend) == 2500
    assert body["month_by_model"] == []
    assert body["search"]["daily_used"] == 0
    assert "daily_limit" in body["search"]
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
    assert body["seat"]["plan"] == "basic"
    assert body["seat"]["until"] is None


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
    # 近 30 日趋势: spend is bucketed into UTC days and zero-filled to a 30-point,
    # oldest-first series ending today; rows older than the window are excluded.
    user_id = await register_and_login(client, "trenduser")

    now = datetime.now(UTC)
    today = now.replace(hour=12, minute=0, second=0, microsecond=0)
    three_ago = today - timedelta(days=3)
    six_ago = today - timedelta(days=6)
    thirty_one_ago = today - timedelta(days=31)  # outside the 30-day window

    async with session_factory() as session:
        for created, total in [
            (today, 500),
            (three_ago, 300),
            (six_ago, 100),
            (thirty_one_ago, 9999),  # must be excluded
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

    assert len(points) == 30
    # Oldest-first, ending today.
    assert points[-1]["date"] == today.date().isoformat()
    assert points[0]["date"] == (today - timedelta(days=29)).date().isoformat()
    by_date = {p["date"]: p["cost_total"] for p in points}
    assert by_date[today.date().isoformat()] == 500
    assert by_date[three_ago.date().isoformat()] == 300
    assert by_date[six_ago.date().isoformat()] == 100
    # The 31-days-ago row is outside the window, so the series total excludes it.
    assert sum(p["cost_total"] for p in points) == 900


async def test_usage_summary_empty_is_zero(client):
    await register_and_login(client, "emptyuser")

    r = await client.get("/v1/usage/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["today"]["cost"]["total"] == 0
    assert body["today"]["requests"] == 0
    assert body["month"]["usage"]["input"] == 0
    # The trend is still a fixed 30-point series, all zero.
    assert [p["cost_total"] for p in body["recent_daily_cost"]] == [0] * 30
    assert body["month_by_model"] == []
    assert body["search"]["daily_used"] == 0


def _call(
    call_id: str,
    *,
    run_id: str,
    model: str,
    total: int = 1000,
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
        "cost_estimated_nano": 0,
        "currency": "CNY",
        "duration_ms": 100,
    }


async def test_usage_summary_month_by_model_from_cost_calls(client, session_factory):
    user_id = await register_and_login(client, "bymodeluser")
    run_id = new_id()
    async with session_factory() as session:
        await CostEventRepository(session).record_calls(
            user_id=user_id,
            conversation_id=new_id(),
            message_id=new_id(),
            calls=[
                _call(new_id(), run_id=run_id, model="deepseek-v4-pro", total=5000),
                _call(
                    new_id(),
                    run_id=run_id,
                    model="deepseek-v4-flash",
                    total=1000,
                    input_tokens=80,
                    output_tokens=20,
                ),
            ],
        )

    body = (await client.get("/v1/usage/summary")).json()
    rows = body["month_by_model"]
    assert [r["model"] for r in rows] == ["deepseek-v4-pro", "deepseek-v4-flash"]
    assert rows[0]["cost_total"] == 5000
    assert rows[0]["calls"] == 1
    assert rows[1]["cost_total"] == 1000


async def test_usage_activity_groups_conversations_and_assist(
    client, session_factory
):
    user_id = await register_and_login(client, "activityuser")
    async with session_factory() as session:
        repo = CostEventRepository(session)
        conv_repo = ConversationRepository(session)
        older = await conv_repo.create(user_id=user_id, title="旧对话", commit=False)
        newer = await conv_repo.create(user_id=user_id, title="调研竞品", commit=False)
        await repo.record_runs(
            user_id=user_id,
            conversation_id=older.id,
            message_id=new_id(),
            runs=[_run(new_id(), role="captain", model="deepseek-v4-flash", total=300)],
        )
        await repo.record_runs(
            user_id=user_id,
            conversation_id=newer.id,
            message_id=new_id(),
            runs=[_run(new_id(), role="captain", model="deepseek-v4-pro", total=900)],
        )
        await repo.record_runs(
            user_id=user_id,
            conversation_id=None,
            message_id=None,
            runs=[_run(new_id(), role="assist", model="deepseek-v4-flash", total=150)],
        )
        older_id, newer_id = older.id, newer.id

    page = await client.get("/v1/usage/activity?window=month&page_size=1")
    assert page.status_code == 200, page.text
    page_body = page.json()
    assert len(page_body["data"]) == 1
    assert page_body["total"] == 3
    assert page_body["cost"]["total"] == 1350
    assert page_body["usage"]["input"] == 300

    r = await client.get("/v1/usage/activity?window=month")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    assert body["window"] == "month"
    assert body["cost"]["total"] == 1350
    assert body["usage"]["input"] == 300
    assert body["usage"]["output"] == 150
    titles = [row["title"] for row in body["data"]]
    assert titles[0] == "AI 改写"
    assert titles[1] == "调研竞品"
    assert titles[2] == "旧对话"
    by_title = {row["title"]: row for row in body["data"]}
    assert by_title["调研竞品"]["conversation_id"] == newer_id
    assert by_title["调研竞品"]["kind"] == "conversation"
    assert by_title["调研竞品"]["cost"]["total"] == 900
    assert by_title["调研竞品"]["deleted"] is False
    assert by_title["AI 改写"]["conversation_id"] is None
    assert by_title["AI 改写"]["kind"] == "assist"
    assert by_title["AI 改写"]["cost"]["total"] == 150
    assert by_title["旧对话"]["conversation_id"] == older_id


async def test_usage_activity_model_filter_slices_calls(client, session_factory):
    user_id = await register_and_login(client, "actmodeluser")
    async with session_factory() as session:
        conv = await ConversationRepository(session).create(
            user_id=user_id, title="混用", commit=False
        )
        run_id = new_id()
        await CostEventRepository(session).record_calls(
            user_id=user_id,
            conversation_id=conv.id,
            message_id=new_id(),
            calls=[
                _call(new_id(), run_id=run_id, model="deepseek-v4-pro", total=4000),
                _call(new_id(), run_id=run_id, model="deepseek-v4-flash", total=500),
            ],
        )
        conv_id = conv.id

    r = await client.get("/v1/usage/activity?window=month&model=deepseek-v4-pro")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["model"] == "deepseek-v4-pro"
    assert body["cost"]["total"] == 4000
    row = body["data"][0]
    assert row["conversation_id"] == conv_id
    assert row["models"] == ["deepseek-v4-pro"]
    assert row["cost"]["total"] == 4000


async def test_usage_activity_is_user_scoped(client, session_factory, new_client):
    owner_id = await register_and_login(client, "actowner")
    async with session_factory() as session:
        conv = await ConversationRepository(session).create(
            user_id=owner_id, title="秘密", commit=False
        )
        await CostEventRepository(session).record_runs(
            user_id=owner_id,
            conversation_id=conv.id,
            message_id=new_id(),
            runs=[_run(new_id(), role="captain", total=800)],
        )

    async with new_client() as other:
        await register_and_login(other, "actother")
        r = await other.get("/v1/usage/activity")
    assert r.status_code == 200, r.text
    assert r.json()["data"] == []
    assert r.json()["total"] == 0
    assert r.json()["cost"]["total"] == 0
    assert r.json()["usage"]["input"] == 0


async def test_usage_activity_window_today_excludes_older(client, session_factory):
    user_id = await register_and_login(client, "actwindow")
    now = datetime.now(UTC)
    yesterday = now.replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(
        days=1
    )
    async with session_factory() as session:
        conv = await ConversationRepository(session).create(
            user_id=user_id, title="今天", commit=False
        )
        await CostEventRepository(session).record_runs(
            user_id=user_id,
            conversation_id=conv.id,
            message_id=new_id(),
            runs=[_run(new_id(), role="captain", total=200)],
        )
        session.add(
            CostEvent(
                user_id=user_id,
                conversation_id=new_id(),
                message_id=new_id(),
                run_id=new_id(),
                role="captain",
                model="deepseek-v4-pro",
                cost_total_nano=999,
                created_at=yesterday,
            )
        )
        await session.commit()

    r = await client.get("/v1/usage/activity?window=today")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["data"][0]["title"] == "今天"
    assert body["data"][0]["cost"]["total"] == 200
    assert body["cost"]["total"] == 200
