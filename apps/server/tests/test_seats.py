"""Paid-seat redeem / expiry math (成本配额与计费.md §〇·六)."""

from datetime import UTC, datetime, timedelta

from agentcore.billing.redemption import generate_code, hash_code, normalize_code, prefix_of
from agentcore.billing.seats import (
    GRACE,
    PERIOD,
    SKUS,
    SeatSnapshot,
    plan_name,
    published_plan,
    quota_overrides,
    receipt_body,
    redeem,
    tick,
)

_NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def _snap(**kw) -> SeatSnapshot:
    return SeatSnapshot(
        plan=kw.get("plan"),
        until=kw.get("until"),
        pending_plan=kw.get("pending_plan"),
        reminded_until=kw.get("reminded_until"),
    )


def test_skus_match_published_catalog():
    assert SKUS["starter"].price_yuan == 19.9
    assert SKUS["starter"].monthly_quota_yuan == 20
    assert SKUS["starter"].search_daily == 80
    assert SKUS["plus"].price_yuan == 49.9
    assert SKUS["plus"].monthly_quota_yuan == 55
    assert SKUS["plus"].daily_quota_yuan == 8
    assert SKUS["plus"].search_daily == 150
    assert SKUS["flagship"].price_yuan == 89
    assert SKUS["flagship"].monthly_quota_yuan == 100
    assert SKUS["flagship"].search_daily == 300
    assert SKUS["flagship"].search_monthly == SKUS["plus"].search_monthly * 2


def test_basic_activate_opens_thirty_days():
    change = redeem(_snap(), "plus", _NOW)
    assert change.kind == "activate"
    assert change.plan == "plus"
    assert change.until == _NOW + PERIOD
    assert change.pending_plan is None
    assert quota_overrides("plus")["monthly_cost_cny"] == 55


def test_renew_extends_from_existing_until():
    until = _NOW + timedelta(days=10)
    change = redeem(_snap(plan="plus", until=until), "plus", _NOW)
    assert change.kind == "renew"
    assert change.until == until + PERIOD
    assert change.plan == "plus"


def test_renew_during_grace_still_extends_original_until():
    until = _NOW - timedelta(days=1)
    change = redeem(_snap(plan="plus", until=until), "plus", _NOW)
    assert change.kind == "renew"
    assert change.until == until + PERIOD


def test_after_grace_is_a_new_activation():
    until = _NOW - GRACE - timedelta(seconds=1)
    change = redeem(_snap(plan="plus", until=until), "plus", _NOW)
    assert change.kind == "activate"
    assert change.until == _NOW + PERIOD


def test_upgrade_starts_a_new_period_now():
    until = _NOW + timedelta(days=20)
    change = redeem(_snap(plan="plus", until=until), "flagship", _NOW)
    assert change.kind == "upgrade"
    assert change.plan == "flagship"
    assert change.until == _NOW + PERIOD
    assert change.pending_plan is None


def test_downgrade_defers_until_current_period_ends():
    until = _NOW + timedelta(days=12)
    change = redeem(_snap(plan="flagship", until=until), "plus", _NOW)
    assert change.kind == "defer_downgrade"
    assert change.plan == "flagship"
    assert change.until == until
    assert change.pending_plan == "plus"


def test_same_tier_renew_clears_pending_downgrade():
    until = _NOW + timedelta(days=8)
    change = redeem(
        _snap(plan="flagship", until=until, pending_plan="plus"),
        "flagship",
        _NOW,
    )
    assert change.kind == "renew"
    assert change.pending_plan is None
    assert change.until == until + PERIOD


def test_tick_applies_pending_at_until_without_grace():
    until = _NOW - timedelta(seconds=1)
    change = tick(_snap(plan="flagship", until=until, pending_plan="plus"), _NOW)
    assert change.kind == "apply_pending"
    assert change.plan == "plus"
    assert change.until == until + PERIOD
    assert change.pending_plan is None


def test_tick_keeps_plan_during_grace_then_expires():
    until = _NOW - timedelta(days=1)
    during = tick(_snap(plan="plus", until=until), _NOW)
    assert during.kind == "none"
    assert during.plan == "plus"
    expired = tick(_snap(plan="plus", until=_NOW - GRACE - timedelta(seconds=1)), _NOW)
    assert expired.kind == "expire"
    assert expired.plan is None
    assert quota_overrides(None)["monthly_cost_cny"] is None


def test_tick_reminds_once_three_days_before():
    until = _NOW + timedelta(days=2)
    first = tick(_snap(plan="plus", until=until), _NOW)
    assert first.kind == "remind"
    assert first.reminded_until == until
    again = tick(
        _snap(plan="plus", until=until, reminded_until=until),
        _NOW,
    )
    assert again.kind == "none"


def test_published_plan_counts_grace_as_paid():
    until = _NOW - timedelta(days=1)
    assert published_plan(_snap(plan="plus", until=until), _NOW) == "plus"
    assert (
        published_plan(
            _snap(plan="plus", until=_NOW - GRACE - timedelta(seconds=1)),
            _NOW,
        )
        == "basic"
    )


def test_receipt_copy_names_the_sku():
    change = redeem(_snap(), "flagship", _NOW)
    subject, body = receipt_body(change, opened_at=_NOW)
    assert plan_name("flagship") in subject
    assert "旗舰版" in body
    assert "30 天" in body


def test_code_normalize_and_hash_are_stable():
    raw = "acp-7k2m-9qwx-4hnt"
    assert normalize_code(f"  {raw}  ") == "ACP-7K2M-9QWX-4HNT"
    assert hash_code(normalize_code(raw)) == hash_code("ACP-7K2M-9QWX-4HNT")
    issued = generate_code("plus")
    assert issued.startswith("ACP-")
    assert prefix_of(issued).startswith("ACP-")
    starter = generate_code("starter")
    assert starter.startswith("ACS-")
    flagship = generate_code("flagship")
    assert flagship.startswith("ACF-")


def test_starter_upgrades_to_plus_and_downgrade_defers():
    until = _NOW + timedelta(days=20)
    up = redeem(_snap(plan="starter", until=until), "plus", _NOW)
    assert up.kind == "upgrade"
    assert up.plan == "plus"
    down = redeem(_snap(plan="plus", until=until), "starter", _NOW)
    assert down.kind == "defer_downgrade"
    assert down.pending_plan == "starter"


def test_free_plan_name_is_mianfei():
    assert plan_name(None) == "免费版"
    assert plan_name("starter") == "基础版"
