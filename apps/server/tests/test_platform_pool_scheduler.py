"""Fill-first pick, 429/401/403 state, conversation stickiness, no env fallback on a live pool."""

from __future__ import annotations

import time

from agentcore.config import settings
from agentcore.core.errors import RETRY_AFTER_FROM_HEADER
from agentcore.core.log_context import log_context
from agentcore.llm.platform_pool import PlatformPoolMember, replace_platform_pool_snapshot
from agentcore.llm.platform_pool_scheduler import (
    account_runtime_for_admin,
    classify_go_limit_name,
    clear_account_runtime_state,
    failover_member,
    pick_schedulable_platform_pool_member,
    record_platform_account_exhausted,
    record_platform_auth_block,
    record_platform_rate_limit,
)
from agentcore.llm.platform_pool_state import (
    AccountRecord,
    MemoryPoolStateStore,
    RedisPoolStateStore,
    get_pool_state_store,
    override_pool_state_store,
    reset_pool_state_store,
)
from agentcore.llm.resolve import platform_llm_credentials

_GO = "https://opencode.ai/zen/go/v1"
_ZEN = "https://opencode.ai/zen/v1"
_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _member(
    *,
    cred_id: str,
    api_key: str,
    enabled: bool = True,
    subscription_day: int = 18,
) -> PlatformPoolMember:
    return PlatformPoolMember(
        id=cred_id,
        label=cred_id[:4],
        api_key=api_key,
        base_url=_GO,
        subscription_day=subscription_day,
        enabled=enabled,
    )


def _two() -> tuple[PlatformPoolMember, PlatformPoolMember]:
    a = _member(cred_id=_A, api_key="sk-a")
    b = _member(cred_id=_B, api_key="sk-b")
    replace_platform_pool_snapshot((a, b))
    return a, b


def _cool(
    member: PlatformPoolMember, seconds: float = 3600.0, *, limit_name: str = "5 hour"
) -> None:
    record_platform_rate_limit(
        api_key=member.api_key,
        base_url=member.base_url,
        retry_after_seconds=seconds,
        retry_after_source=RETRY_AFTER_FROM_HEADER,
        limit_name=limit_name,
    )


def test_classify_go_limit_name_tokens():
    assert classify_go_limit_name("5 hour") == "window"
    assert classify_go_limit_name("weekly") == "window"
    assert classify_go_limit_name("monthly") == "monthly"
    assert classify_go_limit_name("month") == "monthly"
    assert classify_go_limit_name(None) == "unknown"


def test_fill_first_picks_oldest_while_healthy(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    a, b = _two()
    creds = platform_llm_credentials()
    assert creds is not None
    assert creds.api_key == a.api_key
    assert creds.platform_credential_id == a.id
    again = platform_llm_credentials()
    assert again is not None
    assert again.platform_credential_id == a.id
    assert pick_schedulable_platform_pool_member() is a
    assert b.id != a.id


def test_new_task_moves_to_next_after_429(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "sk-env")
    monkeypatch.setattr(settings, "platform_base_url", _ZEN)
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    a, b = _two()
    _cool(a)
    creds = platform_llm_credentials()
    assert creds is not None
    assert creds.api_key == b.api_key
    assert creds.platform_credential_id == b.id
    assert creds.base_url == _GO


def test_cooling_pool_does_not_fall_back_to_env(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "sk-env")
    monkeypatch.setattr(settings, "platform_base_url", _ZEN)
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    a, b = _two()
    _cool(a)
    _cool(b)
    creds = platform_llm_credentials()
    assert creds is not None
    assert creds.api_key == a.api_key
    assert creds.platform_credential_id == a.id


def test_retry_after_expiry_returns_to_fill_first(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    ticks = {"t": 1_000.0}
    monkeypatch.setattr("agentcore.llm.platform_pool_state.time.time", lambda: ticks["t"])
    monkeypatch.setattr("agentcore.llm.platform_pool_scheduler.time.time", lambda: ticks["t"])
    a, b = _two()
    _cool(a, seconds=10.0)
    picked = platform_llm_credentials()
    assert picked is not None
    assert picked.platform_credential_id == b.id
    ticks["t"] = 1_012.0
    creds = platform_llm_credentials()
    assert creds is not None
    assert creds.platform_credential_id == a.id


def test_sticky_stays_on_failover_account_after_a_recovers(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    ticks = {"t": 1_000.0}
    monkeypatch.setattr("agentcore.llm.platform_pool_state.time.time", lambda: ticks["t"])
    monkeypatch.setattr("agentcore.llm.platform_pool_scheduler.time.time", lambda: ticks["t"])
    a, b = _two()
    with log_context(conversation_id="conv-sticky"):
        first = platform_llm_credentials()
        assert first is not None
        assert first.platform_credential_id == a.id
        _cool(a, seconds=10.0)
        switched = platform_llm_credentials()
        assert switched is not None
        assert switched.platform_credential_id == b.id
        ticks["t"] = 1_012.0
        still = platform_llm_credentials()
        assert still is not None
        assert still.platform_credential_id == b.id
    with log_context(conversation_id="conv-new"):
        fresh = platform_llm_credentials()
        assert fresh is not None
        assert fresh.platform_credential_id == a.id


def test_401_blocks_and_is_not_picked_again(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    a, b = _two()
    record_platform_auth_block(api_key=a.api_key, base_url=a.base_url)
    creds = platform_llm_credentials()
    assert creds is not None
    assert creds.platform_credential_id == b.id
    nxt = failover_member(api_key=a.api_key, base_url=a.base_url)
    assert nxt is not None
    assert nxt.id == b.id
    record_platform_auth_block(api_key=b.api_key, base_url=b.base_url)
    assert platform_llm_credentials() is None


def test_regionerror_blocks_and_is_not_picked_again(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    a, b = _two()
    record_platform_auth_block(
        api_key=a.api_key, base_url=a.base_url, reason="regionerror"
    )
    creds = platform_llm_credentials()
    assert creds is not None
    assert creds.platform_credential_id == b.id
    rec = get_pool_state_store().get(a.id)
    assert rec is not None
    assert rec.status == "blocked"
    assert rec.source == "regionerror"
    nxt = failover_member(api_key=a.api_key, base_url=a.base_url)
    assert nxt is not None
    assert nxt.id == b.id


def test_account_runtime_for_admin_maps_store():
    assert account_runtime_for_admin("missing").status == "healthy"
    store = get_pool_state_store()
    store.set(
        _A,
        AccountRecord(
            status="blocked",
            recovery_at=None,
            limit_name=None,
            source="upstream_401",
        ),
    )
    blocked = account_runtime_for_admin(_A)
    assert blocked.status == "blocked"
    assert blocked.recovery_at is None
    assert blocked.limit_name is None

    future = time.time() + 120.0
    store.set(
        _A,
        AccountRecord(
            status="cooling",
            recovery_at=future,
            limit_name="5 hour",
            source="retry_after",
        ),
    )
    cooling = account_runtime_for_admin(_A)
    assert cooling.status == "cooling"
    assert cooling.limit_name == "5 hour"
    assert cooling.recovery_at is not None
    assert abs(cooling.recovery_at.timestamp() - future) < 1.0

    store.set(
        _A,
        AccountRecord(
            status="exhausted",
            recovery_at=future,
            limit_name="monthly",
            source="retry_after",
        ),
    )
    assert account_runtime_for_admin(_A).status == "exhausted"

    store.set(
        _A,
        AccountRecord(
            status="degraded",
            recovery_at=None,
            limit_name=None,
            source="unused",
        ),
    )
    assert account_runtime_for_admin(_A).status == "healthy"


def test_admin_clear_unblocks(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    a, _b = _two()
    record_platform_auth_block(api_key=a.api_key, base_url=a.base_url)
    clear_account_runtime_state(a.id)
    creds = platform_llm_credentials()
    assert creds is not None
    assert creds.platform_credential_id == a.id


def test_monthly_limit_marks_exhausted_and_skips(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    a, b = _two()
    record_platform_rate_limit(
        api_key=a.api_key,
        base_url=a.base_url,
        retry_after_seconds=86_400.0,
        retry_after_source=RETRY_AFTER_FROM_HEADER,
        limit_name="monthly",
    )
    creds = platform_llm_credentials()
    assert creds is not None
    assert creds.platform_credential_id == b.id


def test_credits_exhausted_skips_to_next_until_month_end(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    a, b = _two()
    record_platform_account_exhausted(
        api_key=a.api_key, base_url=a.base_url, reason="creditserror"
    )
    creds = platform_llm_credentials()
    assert creds is not None
    assert creds.platform_credential_id == b.id
    rec = get_pool_state_store().get(a.id)
    assert rec is not None
    assert rec.status == "exhausted"
    assert rec.source == "creditserror"
    assert rec.recovery_at is not None
    assert rec.recovery_at > time.time() + 60
    runtime = account_runtime_for_admin(a.id)
    assert runtime.status == "exhausted"
    assert runtime.source == "creditserror"


def test_window_429_without_retry_after_cools_five_hours(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    a, b = _two()
    record_platform_rate_limit(
        api_key=a.api_key,
        base_url=a.base_url,
        retry_after_seconds=None,
        retry_after_source="local_backoff",
        limit_name="5 hour",
    )
    creds = platform_llm_credentials()
    assert creds is not None
    assert creds.platform_credential_id == b.id
    rec = get_pool_state_store().get(a.id)
    assert rec is not None
    assert rec.status == "cooling"
    assert rec.recovery_at is not None
    assert rec.recovery_at > time.time() + 5 * 3600 - 2


def test_memory_store_expires_cooling():
    store = MemoryPoolStateStore()
    override_pool_state_store(store)
    try:
        store.set(
            "x",
            AccountRecord(
                status="cooling",
                recovery_at=time.time() - 1,
                limit_name="5 hour",
                source=RETRY_AFTER_FROM_HEADER,
            ),
        )
        assert store.get("x") is None
    finally:
        reset_pool_state_store()


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.ttl: dict[str, int] = {}

    def get(self, key: str) -> bytes | None:
        return self.data.get(key)

    def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        self.data[key] = value
        if ex is None:
            self.ttl.pop(key, None)
        else:
            self.ttl[key] = ex

    def delete(self, key: str) -> None:
        self.data.pop(key, None)
        self.ttl.pop(key, None)


def test_redis_store_roundtrip_and_blocked_has_no_ttl():
    fake = _FakeRedis()
    store = RedisPoolStateStore(fake)
    store.set(
        "acct",
        AccountRecord(
            status="cooling",
            recovery_at=time.time() + 30,
            limit_name="weekly",
            source=RETRY_AFTER_FROM_HEADER,
        ),
    )
    got = store.get("acct")
    assert got is not None
    assert got.status == "cooling"
    assert fake.ttl[next(iter(fake.ttl))] >= 1
    store.set(
        "acct",
        AccountRecord(
            status="blocked", recovery_at=None, limit_name=None, source="upstream_401"
        ),
    )
    blocked = store.get("acct")
    assert blocked is not None
    assert blocked.status == "blocked"
    assert fake.ttl.get("ac:ppool:acct:acct") is None
    store.set_sticky("conv-1", "acct")
    assert store.get_sticky("conv-1") == "acct"
