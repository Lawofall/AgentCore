"""Integration tests: quota enforcement gates a new turn with HTTP 429.

Auto-skips (via the shared ``client`` fixture) when no PostgreSQL is reachable.
Seeds the ``cost_events`` ledger above a configured cap, then asserts the
turn-starting endpoints refuse the *next* turn with 429 + ``QUOTA_EXCEEDED``
before any SSE stream / LLM call begins (成本配额与计费.md §一). The over-quota
spend is seeded straight into the ledger so the test never makes a real LLM call.
"""

import pytest

from agentcore.config import settings
from agentcore.core.types import new_id
from agentcore.db.repositories import (
    ConversationRepository,
    CostEventRepository,
    UserRepository,
)
from tests.integration.conftest import register_and_login


@pytest.fixture(autouse=True)
def _platform_billing():
    """Quota is the platform-mode 防线. Force platform billing + a platform key so
    ``platform_catalog_visible`` is true and account-default origin stays ``platform``.
    Without the key, catalog falls back to byok and keyless turns hit 402
    ``LLM_KEY_REQUIRED`` before ``enforce_quota`` (成本配额与计费.md §〇·五 / §一)."""
    original_mode = settings.billing_mode
    original_key = settings.platform_api_key
    settings.billing_mode = "platform"
    settings.platform_api_key = "sk-platform"
    yield
    settings.billing_mode = original_mode
    settings.platform_api_key = original_key


# Above the platform monthly cap (quota_monthly_cost_cny = ¥10) — and also above
# the ¥10 单日成本 backstop, so a fresh over-quota turn is refused whichever cost
# window trips first (both raise QUOTA_EXCEEDED).
_OVER_MONTHLY_NANO = 20_000_000_000
# Under the global caps, but enough to trip a tightened per-user monthly override (¥2).
_UNDER_GLOBAL_NANO = 3_000_000_000


def _run(run_id: str, *, total: int) -> dict:
    """A per-run ledger payload (runtime ``asdict(RunCost)`` shape) with small
    token counts (well under the daily-token cap) so the *cost* cap is what trips."""
    return {
        "run_id": run_id,
        "parent_run_id": None,
        "agent_id": run_id,
        "role": "captain",
        "model": "deepseek-v4-pro",
        "tokens": {"input": 100, "output": 50, "reasoning": 10, "cache_hit": 0, "cache_miss": 100},
        "cost": {"input": 0, "cached": 0, "output": total, "total": total},
        "cost_total_nano": total,
        "currency": "USD",
        "rounds": 1,
        "duration_ms": 1,
    }


async def _seed_spend(session_factory, *, user_id: str, conversation_id: str, total: int) -> None:
    async with session_factory() as session:
        await CostEventRepository(session).record_runs(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=new_id(),
            runs=[_run(new_id(), total=total)],
        )


async def _make_conversation(session_factory, *, user_id: str) -> str:
    async with session_factory() as session:
        conv = await ConversationRepository(session).create(user_id=user_id, title="t")
        return conv.id


async def test_send_message_blocked_when_over_monthly_quota(client, session_factory):
    user_id = await register_and_login(client, "quotauser")
    conv_id = await _make_conversation(session_factory, user_id=user_id)
    await _seed_spend(
        session_factory, user_id=user_id, conversation_id=conv_id, total=_OVER_MONTHLY_NANO
    )

    r = await client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={"content": "hi", "delivery": "steer"},
    )

    assert r.status_code == 429, r.text
    assert r.json()["error"]["code"] == "QUOTA_EXCEEDED"


async def test_regenerate_blocked_when_over_monthly_quota(client, session_factory):
    user_id = await register_and_login(client, "quotaregen")
    conv_id = await _make_conversation(session_factory, user_id=user_id)
    await _seed_spend(
        session_factory, user_id=user_id, conversation_id=conv_id, total=_OVER_MONTHLY_NANO
    )

    # A re-run is a fresh turn, so it passes the same gate (target message need not
    # exist — the quota check runs before the message is even loaded).
    r = await client.post(f"/v1/conversations/{conv_id}/messages/{new_id()}/regenerate", json={})

    assert r.status_code == 429, r.text
    assert r.json()["error"]["code"] == "QUOTA_EXCEEDED"


async def test_per_user_override_tightens_cap(client, session_factory):
    # Spend (¥3) is UNDER the global ¥10 cap, so default config would let the turn
    # through — but the user's own ¥2 monthly override trips 429. Proves the turn
    # gate resolves limits via QuotaLimits.for_user (per-user), not just config.
    user_id = await register_and_login(client, "quotaovr")
    conv_id = await _make_conversation(session_factory, user_id=user_id)
    await _seed_spend(
        session_factory, user_id=user_id, conversation_id=conv_id, total=_UNDER_GLOBAL_NANO
    )
    async with session_factory() as session:
        await UserRepository(session).set_quota(user_id, monthly_cost_cny=2.0)

    r = await client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={"content": "hi", "delivery": "steer"},
    )

    assert r.status_code == 429, r.text
    assert r.json()["error"]["code"] == "QUOTA_EXCEEDED"


async def test_ownership_check_precedes_quota(client, session_factory):
    # Ownership (404) is checked before quota (429): posting to a conversation the
    # user doesn't own returns 404 even when the user is over quota.
    user_id = await register_and_login(client, "quotaowner")
    owned = await _make_conversation(session_factory, user_id=user_id)
    await _seed_spend(
        session_factory, user_id=user_id, conversation_id=owned, total=_OVER_MONTHLY_NANO
    )

    r = await client.post(
        f"/v1/conversations/{new_id()}/messages",
        json={"content": "hi", "delivery": "steer"},
    )

    assert r.status_code == 404, r.text
