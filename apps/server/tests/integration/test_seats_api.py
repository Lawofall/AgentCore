"""Paid-seat redeem + admin card-secret inventory (real PG)."""

from agentcore.billing.redemption import generate_code, hash_code, prefix_of
from agentcore.billing.seats import SKUS
from agentcore.core.types import new_id
from agentcore.db.models.seats import RedemptionCode
from tests.integration.conftest import login_admin, register_and_login


async def _seed_code(session_factory, *, sku: str, created_by: str) -> str:
    raw = generate_code(sku)  # type: ignore[arg-type]
    async with session_factory() as session:
        session.add(
            RedemptionCode(
                id=new_id(),
                code_hash=hash_code(raw),
                code_prefix=prefix_of(raw),
                sku=sku,
                status="unused",
                batch_id=new_id(),
                created_by=created_by,
            )
        )
        await session.commit()
    return raw


async def test_redeem_opens_plus_and_rejects_reuse(client, session_factory):
    user_id = await register_and_login(client, "seatplus")
    raw = await _seed_code(session_factory, sku="plus", created_by=user_id)

    first = await client.post("/v1/seats/redeem", json={"code": f"  {raw.lower()}  "})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["plan"] == "plus"
    assert body["until"]
    assert body["pending_plan"] is None

    summary = await client.get("/v1/usage/summary")
    assert summary.status_code == 200, summary.text
    seat = summary.json()["seat"]
    assert seat["plan"] == "plus"
    assert seat["until"]
    assert summary.json()["quota"]["monthly_cost_nano"] == int(
        SKUS["plus"].monthly_quota_yuan * 1_000_000_000
    )

    again = await client.post("/v1/seats/redeem", json={"code": raw})
    assert again.status_code == 409, again.text


async def test_redeem_unknown_code_is_404(client):
    await register_and_login(client, "seatmiss")
    r = await client.post("/v1/seats/redeem", json={"code": "ACP-0000-0000-0000"})
    assert r.status_code == 404, r.text


async def test_admin_issues_voids_and_lists_codes(client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)

    created = await client.post(
        "/v1/admin/redemption-codes",
        json={"sku": "flagship", "count": 2},
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["sku"] == "flagship"
    assert len(payload["codes"]) == 2
    assert all(c.startswith("ACF-") for c in payload["codes"])

    listed = await client.get("/v1/admin/redemption-codes")
    assert listed.status_code == 200, listed.text
    roster = listed.json()
    assert roster["unused_flagship"] == 2
    assert roster["unused_starter"] == 0
    assert roster["total"] == 2
    code_id = roster["data"][0]["id"]

    voided = await client.post(f"/v1/admin/redemption-codes/{code_id}/void")
    assert voided.status_code == 204, voided.text

    after = await client.get("/v1/admin/redemption-codes?status=void")
    assert after.status_code == 200, after.text
    assert after.json()["total"] == 1
