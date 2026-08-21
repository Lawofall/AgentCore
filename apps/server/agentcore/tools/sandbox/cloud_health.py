"""Process-level cloud sandbox availability (probe → capability gate).

First probed at app lifespan when cloud execution is config-enabled, then kept
honest by TTL refresh. runsc can rot long after boot — an AppArmor policy
change, a wiped ``runtime_root``, a kernel upgrade that kills userns — so a
boot-only verdict would gate tool assembly on a fact that stopped being true.
Reads return the cached verdict immediately and schedule a *background*
re-probe once it ages out: cost stays one runsc smoke per TTL window instead of
one per ``code_execute``, and no caller ever blocks on a probe.

The result folds into ``code_execution_enabled_for`` (shape A). Shape B
browser / package_install is gated separately by ``browser_netns_health``.
``None`` (never probed) preserves
config-only semantics for tests / local / unbooted, and is a *different state*
from a probe that ran and failed — the latter carries a timestamp and a reason.
A refresh therefore replaces the verdict only when the new one is complete: a
known-bad verdict must never decay back to ``None`` while a re-probe is in
flight, since that would open the gate this probe exists to close.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from agentcore.config import settings
from agentcore.core.logging import get_logger

logger = get_logger(__name__)

# How long a healthy verdict is trusted before a background re-probe runs.
# Bounds the blind window after a post-boot regression without putting a runsc
# start in front of every call.
_HEALTHY_TTL_SECONDS = 300.0
# An unhealthy verdict is retried sooner (recovery should not wait out a full
# TTL) but backs off, so a persistently broken host is not re-probed in a loop.
_UNHEALTHY_RETRY_BASE_SECONDS = 30.0
_UNHEALTHY_RETRY_MAX_SECONDS = 300.0
_UNHEALTHY_RETRY_MAX_DOUBLINGS = 8
# Wall-clock bound on a single probe. A wedged runsc must neither hang boot nor
# pin the in-flight guard and freeze the verdict forever; fail-safe = unhealthy.
_PROBE_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class _Verdict:
    """One *completed* probe. The absence of a verdict (``None``) = never probed."""

    healthy: bool
    at_monotonic: float
    failure: tuple[str, str | None] | None
    consecutive_failures: int


# None = never probed → predicates keep config-only semantics (status quo).
_verdict: _Verdict | None = None
# In-flight background refresh; also the guard against scheduling a second one.
_refresh_task: asyncio.Task[None] | None = None


def cloud_sandbox_health() -> bool | None:
    """Current verdict: ``True`` / ``False``, or ``None`` if never probed.

    Returns the cached answer immediately — never probes inline. When the
    verdict has aged past its TTL a background re-probe is scheduled, so the
    *next* read reflects a sandbox that broke after boot. Scheduling needs a
    running event loop; sync callers (scripts / tests) simply keep the cache.
    """
    _schedule_refresh_if_stale()
    if _verdict is None:
        return None
    return _verdict.healthy


def cloud_sandbox_health_failure() -> tuple[str, str | None] | None:
    """``(reason, detail)`` from the last unhealthy probe; else ``None``."""
    if _verdict is None:
        return None
    return _verdict.failure


def cloud_sandbox_health_age_seconds() -> float | None:
    """Seconds since the cached verdict was formed; ``None`` if never probed."""
    if _verdict is None:
        return None
    return max(time.monotonic() - _verdict.at_monotonic, 0.0)


def reset_cloud_sandbox_health_for_tests() -> None:
    """Clear the process-wide cache so tests cannot leak health across cases."""
    global _verdict, _refresh_task
    _verdict = None
    task, _refresh_task = _refresh_task, None
    if task is not None and not task.done():
        task.cancel()


def set_cloud_sandbox_health_for_tests(
    healthy: bool | None,
    *,
    failure: tuple[str, str | None] | None = None,
    age_seconds: float = 0.0,
) -> None:
    """Inject a probe result for unit tests (``None`` = unprobed).

    ``age_seconds`` backdates the verdict so staleness paths can be exercised
    without sleeping.
    """
    global _verdict
    if healthy is None:
        _verdict = None
        return
    prior_failure = _verdict.failure if _verdict is not None else None
    _verdict = _Verdict(
        healthy=healthy,
        at_monotonic=time.monotonic() - age_seconds,
        failure=None if healthy else (failure or prior_failure or ("unhealthy", None)),
        consecutive_failures=0 if healthy else 1,
    )


def age_cloud_sandbox_health_for_tests(seconds: float) -> None:
    """Backdate the cached verdict so a real probe result can be aged out."""
    global _verdict
    if _verdict is None:
        return
    _verdict = _Verdict(
        healthy=_verdict.healthy,
        at_monotonic=_verdict.at_monotonic - seconds,
        failure=_verdict.failure,
        consecutive_failures=_verdict.consecutive_failures,
    )


def pending_cloud_sandbox_refresh_for_tests() -> asyncio.Task[None] | None:
    """The background refresh task a stale read scheduled, if any."""
    return _refresh_task


def cloud_execution_config_enabled() -> bool:
    """Whether config alone would allow server-side code execution."""
    return settings.gvisor_enabled or settings.code_execute_cloud_enabled


def _retry_after_seconds(verdict: _Verdict) -> float:
    if verdict.healthy:
        return _HEALTHY_TTL_SECONDS
    doublings = min(max(verdict.consecutive_failures - 1, 0), _UNHEALTHY_RETRY_MAX_DOUBLINGS)
    return min(_UNHEALTHY_RETRY_BASE_SECONDS * (2**doublings), _UNHEALTHY_RETRY_MAX_SECONDS)


def cloud_sandbox_health_is_stale() -> bool:
    """Whether the cached verdict is old enough to warrant a re-probe.

    Never probed is *not* stale: there is no verdict to refresh, and probing
    from a process that never booted the lifespan would change status-quo
    semantics for tests / local.
    """
    if _verdict is None:
        return False
    return (time.monotonic() - _verdict.at_monotonic) >= _retry_after_seconds(_verdict)


def _schedule_refresh_if_stale() -> None:
    """Fire-and-forget a re-probe when the verdict aged out. Never blocks."""
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        return
    if not cloud_sandbox_health_is_stale() or not cloud_execution_config_enabled():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _refresh_task = loop.create_task(refresh_cloud_sandbox_health())
    _refresh_task.add_done_callback(_clear_refresh_task)


def _clear_refresh_task(task: asyncio.Task[None]) -> None:
    global _refresh_task
    if _refresh_task is task:
        _refresh_task = None


async def probe_cloud_sandbox_at_startup() -> None:
    """Boot probe when cloud execution is config-enabled. Never raises.

    Uses the same default server sandbox as workspace construction. Missing
    ``health_check``, a false result, or any exception → unhealthy (tools withheld).
    """
    await _probe_and_store(phase="boot")


async def refresh_cloud_sandbox_health() -> None:
    """Re-probe now and replace the cached verdict. Never raises.

    Normally reached through the background scheduler; awaited directly by
    callers that need the verdict re-established synchronously.
    """
    await _probe_and_store(phase="refresh")


async def _probe_and_store(*, phase: str) -> None:
    """Run one probe and publish the verdict atomically (no intermediate ``None``)."""
    global _verdict
    if not cloud_execution_config_enabled():
        return

    previous = _verdict
    ok, reason, detail = await _run_probe()
    now = time.monotonic()

    if ok:
        _verdict = _Verdict(healthy=True, at_monotonic=now, failure=None, consecutive_failures=0)
        if previous is not None and not previous.healthy:
            logger.info("sandbox.cloud_health_ok", phase=phase, recovered=True)
        else:
            logger.debug("sandbox.cloud_health_ok", phase=phase)
        return

    was_healthy = previous is not None and previous.healthy
    _verdict = _Verdict(
        healthy=False,
        at_monotonic=now,
        failure=(reason, detail or None),
        consecutive_failures=(
            1 if previous is None or previous.healthy else previous.consecutive_failures + 1
        ),
    )
    logger.warning(
        "sandbox.cloud_health_failed",
        reason=reason,
        detail=detail or None,
        phase=phase,
        regressed=was_healthy or None,
        hint="云端 code_execute/test_run 将不装配，直到形状 A 沙箱可用；browser/package_install 看形状 B",
    )


async def _run_probe() -> tuple[bool, str, str]:
    """``(ok, reason, detail)`` for one sandbox ``health_check``. Never raises."""
    reason = "unhealthy"
    detail = ""
    try:
        from agentcore.workspace.locate import _default_server_sandbox

        sandbox = _default_server_sandbox()
        health_check = getattr(sandbox, "health_check", None)
        if health_check is None:
            return False, "missing_health_check", ""
        ok = bool(await asyncio.wait_for(health_check(), _PROBE_TIMEOUT_SECONDS))
        if ok:
            return True, "", ""
        failure = getattr(sandbox, "last_health_failure", None)
        if (
            isinstance(failure, tuple)
            and len(failure) >= 1
            and isinstance(failure[0], str)
            and failure[0]
        ):
            reason = failure[0]
            if len(failure) > 1 and failure[1]:
                detail = str(failure[1])[:200]
        return False, reason, detail
    except TimeoutError:
        return False, "probe_timeout", f"health_check > {_PROBE_TIMEOUT_SECONDS}s"
    except Exception as exc:  # noqa: BLE001 — probe must never break startup
        return False, type(exc).__name__, str(exc)[:200]
