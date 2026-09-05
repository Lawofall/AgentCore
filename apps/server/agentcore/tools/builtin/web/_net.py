"""Per-host egress circuit breaker + run-scoped web-read retirement.

Stateless networking primitives live in :mod:`agentcore.core.net`. This module
holds the in-process per-host breaker used by ``web_fetch`` / search backends, and
the run-scoped ``web_fetch`` retirement latch that survives ``react_loop`` restart
(stream-stall → ``run.failed`` → Wave ``on_failure=retry``, contract write_pass /
retry) so a disabled web-read tool is not re-offered into another empty-spin pass.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from agentcore.core.net import EgressError

__all__ = [
    "WEB_HOST_FAIL_THRESHOLD",
    "WEB_HOST_CIRCUIT_COOLDOWN",
    "WEB_FETCH_RETIRE_STEER",
    "POST_READ_RETIRE_SEARCH_HINT",
    "EgressError",
    "circuit_remaining",
    "note_failure",
    "note_success",
    "mark_web_fetch_retired",
    "web_fetch_retire_message",
    "clear_web_fetch_retired",
    "is_web_fetch_retired",
    "consume_post_read_retire_search_hint",
]

WEB_HOST_FAIL_THRESHOLD = 3  # consecutive transport failures before tripping
WEB_HOST_CIRCUIT_COOLDOWN = 120.0  # how long a tripped host stays short-circuited

# Model-facing hard-stop after the run-scoped tool circuit breaker disables web_fetch.
# Survives loop restart via :func:`mark_web_fetch_retired` so Wave/contract retries
# cannot re-open the same empty-spin surface. Fact only — tools already withheld.
WEB_FETCH_RETIRE_STEER = (
    "web_fetch 外网深读已因连续失败停用，并已收束继续 web_search 空转。"
)

# Defense-in-depth if web_search still runs after retirement (tests / race before
# disabled_tools refresh). One-shot per run_id.
POST_READ_RETIRE_SEARCH_HINT = (
    "【收口】web_fetch 已停用：勿再把 web_search 当默认出路；"
    "基于已有材料交付或 handoff，标注检索/深读缺口即可。"
)


@dataclass
class _HostState:
    fails: int = 0
    open_until: float = 0.0


# Best-effort, in-process breaker. Single event loop → plain dict mutations are
# safe enough; state is intentionally ephemeral (resets on restart).
_states: dict[str, _HostState] = {}
# run_id → steer message. Mirrors LoopController disable for web_fetch across
# react_loop death (same process / same run_id).
_web_fetch_retired: dict[str, str] = {}
# run_ids that already received :data:`POST_READ_RETIRE_SEARCH_HINT`.
_web_fetch_retire_search_hinted: set[str] = set()


def circuit_remaining(host: str) -> float:
    """Seconds the breaker stays open for ``host`` (``0.0`` = closed/allowed)."""
    st = _states.get(host)
    if st is None:
        return 0.0
    return max(0.0, st.open_until - time.monotonic())


def note_success(host: str) -> None:
    """Clear a host's failure streak after a successful request."""
    _states.pop(host, None)


def note_failure(host: str) -> None:
    """Record a transport failure; trip the breaker at the configured threshold."""
    if not host:
        return
    st = _states.setdefault(host, _HostState())
    st.fails += 1
    if st.fails >= WEB_HOST_FAIL_THRESHOLD:
        st.open_until = time.monotonic() + WEB_HOST_CIRCUIT_COOLDOWN


def mark_web_fetch_retired(run_id: str, *, message: str | None = None) -> None:
    """Latch ``web_fetch`` as retired for this run (idempotent)."""
    rid = (run_id or "").strip()
    if not rid:
        return
    _web_fetch_retired[rid] = (message or WEB_FETCH_RETIRE_STEER).strip()


def web_fetch_retire_message(run_id: str) -> str | None:
    """Steer text if ``web_fetch`` was retired for ``run_id``, else ``None``."""
    rid = (run_id or "").strip()
    if not rid:
        return None
    return _web_fetch_retired.get(rid)


def is_web_fetch_retired(run_id: str) -> bool:
    return web_fetch_retire_message(run_id) is not None


def consume_post_read_retire_search_hint(run_id: str) -> str | None:
    """One-shot closing tip when ``web_search`` still runs after web_fetch retirement."""
    rid = (run_id or "").strip()
    if not rid or rid not in _web_fetch_retired:
        return None
    if rid in _web_fetch_retire_search_hinted:
        return None
    _web_fetch_retire_search_hinted.add(rid)
    return POST_READ_RETIRE_SEARCH_HINT


def clear_web_fetch_retired(run_id: str) -> None:
    """Drop the retirement latch (run teardown hygiene)."""
    rid = (run_id or "").strip()
    if rid:
        _web_fetch_retired.pop(rid, None)
        _web_fetch_retire_search_hinted.discard(rid)
