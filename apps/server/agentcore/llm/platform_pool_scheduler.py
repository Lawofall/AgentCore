"""Fill-first pick + 429/401/403 state machine for the platform credential pool.

Selection stays inside ``platform_llm_credentials``. This module decides *which*
enabled member is eligible: sticky conversation pin, then the oldest healthy
member (fill-first). 80% demotion is not applied this round — only upstream
429 / 401 / 403 ``RegionError`` move a member out of the healthy set.

Recovery times come from the 429 ``Retry-After`` (and ``metadata.limitName``
to distinguish monthly exhausted from a 5h/weekly cool-off). We do not guess
window length from the limit name when the header is present.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from agentcore.core.errors import LLMAuthError, LLMQuotaExceededError
from agentcore.core.logging import get_logger
from agentcore.llm.platform_pool import PlatformPoolMember, iter_platform_pool_members
from agentcore.llm.platform_pool_state import AccountRecord, get_pool_state_store

logger = get_logger(__name__)

LimitKind = Literal["monthly", "window", "unknown"]

_TASK_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
_MONTHLY_NAMES = frozenset({"monthly", "month"})
_WINDOW_NAMES = frozenset(
    {"weekly", "week", "5 hour", "5h", "5-hour", "5_hour", "5hour"}
)


def classify_go_limit_name(name: str | None) -> LimitKind:
    """Map structured ``metadata.limitName`` to a state-machine kind."""
    if not name:
        return "unknown"
    token = " ".join(name.strip().lower().split())
    if token in _MONTHLY_NAMES:
        return "monthly"
    if token in _WINDOW_NAMES:
        return "window"
    return "unknown"


def _task_id() -> str | None:
    from agentcore.core.log_context import get_log_value

    raw = get_log_value("conversation_id")
    if raw and _TASK_ID_RE.fullmatch(raw):
        return raw
    return None


def _enabled_members() -> tuple[PlatformPoolMember, ...]:
    return tuple(m for m in iter_platform_pool_members() if m.enabled)


def pool_has_enabled_members() -> bool:
    """True when the snapshot has at least one admin-enabled member."""
    return bool(_enabled_members())


def member_for_credentials(api_key: str, base_url: str) -> PlatformPoolMember | None:
    """Pool member bound to this ``(api_key, base_url)`` pair, if any."""
    key = (api_key or "").strip()
    url = (base_url or "").rstrip("/")
    if not key:
        return None
    for member in iter_platform_pool_members():
        if member.api_key == key and member.base_url.rstrip("/") == url:
            return member
    return None


def _is_schedulable(member: PlatformPoolMember, *, now: float) -> bool:
    record = get_pool_state_store().get(member.id)
    if record is None:
        return True
    if record.status == "blocked":
        return False
    if record.status in {"cooling", "exhausted"}:
        if record.recovery_at is None:
            return False
        return record.recovery_at <= now
    # ``degraded`` is unused this round (no 80% cut); still eligible.
    return True


def _is_blocked(member: PlatformPoolMember) -> bool:
    record = get_pool_state_store().get(member.id)
    return record is not None and record.status == "blocked"


def pick_schedulable_platform_pool_member(
    *, sticky_key: str | None = None
) -> PlatformPoolMember | None:
    """Fill-first among enabled members that are not cooling / exhausted / blocked.

    A conversation pin wins while that member stays schedulable, so a task that
    already failed over to B does not jump back to A when A's window recovers.
    """
    enabled = _enabled_members()
    if not enabled:
        return None
    now = time.time()
    task = sticky_key if sticky_key is not None else _task_id()
    store = get_pool_state_store()
    if task:
        pinned_id = store.get_sticky(task)
        if pinned_id:
            for member in enabled:
                if member.id == pinned_id and _is_schedulable(member, now=now):
                    store.set_sticky(task, member.id)
                    return member
    for member in enabled:
        if _is_schedulable(member, now=now):
            if task:
                store.set_sticky(task, member.id)
            return member
    return None


def pick_last_resort_platform_pool_member() -> PlatformPoolMember | None:
    """First enabled member that is not blocked (may still be cooling).

    Used only when nothing is schedulable, so the leaf can raise the existing
    429 CTA instead of falling back to the env key.
    """
    for member in _enabled_members():
        if not _is_blocked(member):
            return member
    return None


def _monthly_recovery_at(subscription_day: int, *, now: float) -> float:
    from agentcore.billing.go_windows import subscription_month_bounds

    moment = datetime.fromtimestamp(now, tz=UTC)
    _start, end = subscription_month_bounds(moment, subscription_day)
    return end.timestamp()


def record_platform_rate_limit(
    *,
    api_key: str,
    base_url: str,
    retry_after_seconds: float | None,
    retry_after_source: str,
    limit_name: str | None,
) -> None:
    """Mark the member cooling or monthly-exhausted. No-op for env / override keys."""
    member = member_for_credentials(api_key, base_url)
    if member is None:
        return
    now = time.time()
    kind = classify_go_limit_name(limit_name)
    wait = (
        float(retry_after_seconds)
        if retry_after_seconds is not None and retry_after_seconds > 0
        else None
    )
    if kind == "monthly":
        recovery = (
            now + wait
            if wait is not None
            else _monthly_recovery_at(member.subscription_day, now=now)
        )
        record = AccountRecord(
            status="exhausted",
            recovery_at=recovery,
            limit_name=limit_name,
            source=retry_after_source,
        )
    else:
        recovery = now + wait if wait is not None else now + 1.0
        record = AccountRecord(
            status="cooling",
            recovery_at=recovery,
            limit_name=limit_name,
            source=retry_after_source,
        )
    get_pool_state_store().set(member.id, record)
    logger.info(
        "platform_pool.cooling",
        credential_id=member.id,
        status=record.status,
        limit_name=limit_name or "",
        recovery_at=record.recovery_at,
        source=retry_after_source,
    )


def record_platform_auth_block(
    *, api_key: str, base_url: str, reason: str = "upstream_401"
) -> None:
    """Drop the member until an operator re-enables it.

    Same ``blocked`` state and ``platform_pool.blocked`` alert for 401 (ban / bad
    key) and 403 ``RegionError``. Callers decide whether the current request may
    hop: 401 must not; 403 may, before commit.
    """
    member = member_for_credentials(api_key, base_url)
    if member is None:
        return
    get_pool_state_store().set(
        member.id,
        AccountRecord(
            status="blocked",
            recovery_at=None,
            limit_name=None,
            source=reason,
        ),
    )
    logger.error(
        "platform_pool.blocked",
        credential_id=member.id,
        reason=reason,
    )


def platform_account_remaining(api_key: str, base_url: str) -> float:
    """Seconds until this member is schedulable. ``0`` if healthy / unknown."""
    member = member_for_credentials(api_key, base_url)
    if member is None:
        return 0.0
    record = get_pool_state_store().get(member.id)
    if record is None:
        return 0.0
    if record.status == "blocked":
        return 0.0
    if record.recovery_at is None:
        return 0.0
    return max(record.recovery_at - time.time(), 0.0)


def failover_member(*, api_key: str, base_url: str) -> PlatformPoolMember | None:
    """Next fill-first member after the current one was marked unschedulable.

    Returns ``None`` when the current leaf is not a pool member, or when no
    other member is schedulable. The leaf calls this on 429 and on 403
    ``RegionError``; 401 must not hop, even when another member is free.
    """
    current = member_for_credentials(api_key, base_url)
    if current is None:
        return None
    nxt = pick_schedulable_platform_pool_member()
    if nxt is None or nxt.id == current.id:
        return None
    return nxt


def clear_account_runtime_state(credential_id: str) -> None:
    """Admin re-enable / rotate / delete: drop cooling and blocked flags."""
    cid = (credential_id or "").strip()
    if not cid:
        return
    get_pool_state_store().clear(cid)


@dataclass(frozen=True, slots=True)
class AdminAccountRuntime:
    """Admin-facing overlay. ``degraded`` is unused this round and reads healthy."""

    status: Literal["healthy", "cooling", "exhausted", "blocked"]
    recovery_at: datetime | None
    limit_name: str | None


def account_runtime_for_admin(credential_id: str) -> AdminAccountRuntime:
    """Read pool-state for the credentials list. Absence = healthy."""
    cid = (credential_id or "").strip()
    if not cid:
        return AdminAccountRuntime("healthy", None, None)
    record = get_pool_state_store().get(cid)
    if record is None or record.status not in {"cooling", "exhausted", "blocked"}:
        return AdminAccountRuntime("healthy", None, None)
    recovery = (
        datetime.fromtimestamp(record.recovery_at, tz=UTC)
        if record.recovery_at is not None
        else None
    )
    return AdminAccountRuntime(record.status, recovery, record.limit_name)


def platform_pool_unavailable_error(
    *, blocked: bool = False
) -> LLMAuthError | LLMQuotaExceededError:
    """Honest wall when every enabled member is unschedulable. No env fallback."""
    if blocked:
        return LLMAuthError(provider_name="platform")
    return LLMQuotaExceededError(
        "平台模型额度已用完，本回合无法继续。请等待上游额度恢复，"
        "或接入自己的 API Key 立即继续。",
        credential_source="platform",
    )
