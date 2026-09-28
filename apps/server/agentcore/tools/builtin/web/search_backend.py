"""Search backend — pluggable web search for built-in tools.

The process backend talks to a self-hosted SearXNG instance whose engine set is
curated to mainland-China-reachable engines (baidu/sogou + low-weight bing, see
``deploy/searxng/settings.yml``) — the public engines (google/ddg/brave) time out
from a China-hosted server.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.core.net import (
    SEARCH_TIMEOUT,
    WEB_CONNECT_TIMEOUT,
    EgressError,
    describe_net_error,
    outbound_async_client,
)
from agentcore.core.task_cancel import raise_if_task_cancelled
from agentcore.tools.builtin.web._net import (
    circuit_remaining,
    note_failure,
    note_success,
)

logger = get_logger(__name__)

DEFAULT_MAX_RESULTS = 5

# 工具执行阶段进度回调 (联网搜索前端展示优化): a backend fires this with a coarse phase token —
# "queued" (排队中: gated by the rate/concurrency limiter under a parallel-team burst),
# "querying" (正在检索: the engine request is in flight). "fallback" (改用备用引擎) is emitted
# by the sidecar cloud leg, not by this backend. Lets the tool layer surface a live waiting
# state; the backend stays off the event vocabulary — it only names phases (引擎纯化).
# ``None`` = no live sink.
PhaseCallback = Callable[[str], None]


def track_phase_durations(
    on_phase: PhaseCallback | None,
) -> tuple[PhaseCallback, Callable[[], None]]:
    """A6: wrap the existing ``on_phase`` channel to emit structured phase durations.

    Returns ``(wrapped_callback, finish)``. Each phase transition (and ``finish``) logs
    ``search.phase_duration`` with ``phase`` + ``duration_ms``. Still forwards to the
    UI callback when present — no parallel phase channel.
    """
    state: dict[str, Any] = {"phase": None, "t0": None}

    def _close_current() -> None:
        prev = state["phase"]
        t0 = state["t0"]
        if prev is None or t0 is None:
            return
        logger.info(
            "search.phase_duration",
            phase=prev,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
        state["phase"] = None
        state["t0"] = None

    def _wrapped(phase: str) -> None:
        _close_current()
        state["phase"] = phase
        state["t0"] = time.monotonic()
        if on_phase is not None:
            on_phase(phase)

    def _finish() -> None:
        _close_current()

    return _wrapped, _finish

# Cap concurrent in-flight requests to the single self-hosted SearXNG instance. A
# parallel team (A/B/C unlocked multi-worker research) can otherwise fire dozens of
# searches at once, saturating SearXNG's worker/accept pool → connections time out →
# 3 in a row trip the shared per-host breaker → the WHOLE team goes search-blind for
# the cooldown. The semaphore makes the burst queue into manageable waves instead of
# self-DOSing; tune up if SearXNG is scaled out.
_SEARCH_CONCURRENCY = 6

# Pace the *rate* of outbound searches over time — distinct from _SEARCH_CONCURRENCY,
# which caps how many run AT ONCE. The CN scraper engines (baidu/sogou) raise CAPTCHA
# based on the request RATE from one datacenter IP over a window, NOT instantaneous
# concurrency: a parallel team sustaining dozens of searches/min gets the IP flagged as
# a crawler → engines "suspended" → HTTP 200 with empty results (实测案例复盘 案例1: a
# 07:30 run fired 114 web_searches and went search-blind). A token bucket smooths that
# sustained rate while a lone search still fires instantly from a full bucket. The
# burst capacity is kept ≥ _SEARCH_CONCURRENCY so a single wave is never throttled below
# the concurrency ceiling (the burst test relies on this). Per-process like the
# semaphore/breaker (N API workers ⇒ N×rate); tune down if CAPTCHA persists, up if a
# scaled-out SearXNG / commercial API removes the per-IP limit.
_SEARCH_RATE_PER_SEC = 3.0  # steady-state refill: ~3 searches/sec
_SEARCH_RATE_BURST = 8.0  # bucket capacity: a fresh team's first wave passes immediately
_SEARCH_RATE_JITTER_S = 0.2  # extra randomised wait so paced requests don't fire in lockstep

# Transient gateway/server errors (notably SearXNG 502 when an upstream engine
# hiccups) frequently clear on a quick retry, so a 5xx is retried a couple of times
# with jittered backoff. Connect / timeout failures are NOT retried here: a down or
# blocked host should fast-fail into the per-host circuit breaker rather than stall
# on backoff. 4xx are client errors and are never retried.
_SEARCH_ATTEMPTS = 3
_SEARCH_RETRY_BASE_S = 0.3
_SEARCH_RETRY_JITTER_S = 0.3


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


def infer_search_language(query: str) -> str:
    """Infer search UI/result language from the query script (task-language proxy).

    Avoids SearXNG ``default_lang=auto`` / IP-locale hijacking Chinese research into
    Japanese SERPs (trace 2f52c042: ja.wikipedia / google.co.jp on 中文盘点).
    """
    text = query or ""
    if re.search(r"[\u3040-\u309f\u30a0-\u30ff]", text):
        return "ja"
    if re.search(r"[\uac00-\ud7af]", text):
        return "ko"
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"
    return "en"


class SearchBackend(Protocol):
    async def search(
        self,
        query: str,
        max_results: int = DEFAULT_MAX_RESULTS,
        on_phase: PhaseCallback | None = None,
        *,
        language: str | None = None,
    ) -> list[SearchResult]: ...

    async def aclose(self) -> None: ...


def _parse_results(data: dict[str, Any], max_results: int) -> list[SearchResult]:
    """Filter + dedup + truncate a search JSON payload into SearchResults.

    SearXNG exposes the summary as ``content``; cloud
    ``/v1/inference/web_search`` returns ``snippet``. Prefer ``content`` when
    present, else ``snippet``.

    SearXNG already orders results by cross-engine aggregate score, so we keep
    insertion order: drop entries missing url/title, dedup by normalized url
    (strip ``#fragment`` and trailing ``/`` so the same page from multiple
    engines collapses), then take the top ``max_results``.
    """
    results: list[SearchResult] = []
    seen: set[str] = set()
    for item in data.get("results", []):
        url = (item.get("url") or "").strip()
        title = (item.get("title") or "").strip()
        if not url or not title:
            continue
        key = url.split("#", 1)[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        # SearXNG: content; cloud inference web_search: snippet.
        snippet = item.get("content") or item.get("snippet") or ""
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= max_results:
            break
    return results


def _searxng_host(base_url: str) -> str:
    return (urlparse(base_url).hostname or "localhost").lower()


def describe_searxng_error(e: BaseException, *, base_url: str) -> str:
    """Honest copy for failures talking to the configured SearXNG host.

    Connect-class errors are always「本地搜索服务不可用」(loopback *or* compose
    service name) — never the public「出网受限」wording, and never docker teaching
    toward the model. Other failures fall through to :func:`describe_net_error`.
    Classification only; no SSRF change.
    """
    return describe_net_error(e, url=base_url, local_service=True)


class _TokenBucket:
    """Async token bucket: refills at ``rate`` tokens/sec up to ``capacity``.

    :meth:`acquire` waits until a whole token is available, then consumes one. Used to
    pace the *rate* of outbound searches (see ``_SEARCH_RATE_*``) so a parallel-team
    burst doesn't sustain a crawler-like request rate that gets the host IP CAPTCHA-
    flagged. Single-event-loop posture (like the breaker): a short lock guards the
    token accounting; the lock is released while waiting out a shortfall so refill stays
    accurate and waiters don't serialise behind a sleep.
    """

    def __init__(self, rate_per_sec: float, capacity: float) -> None:
        self._rate = rate_per_sec
        self._capacity = capacity
        self._tokens = capacity  # start full: a cold first search isn't penalised
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def would_wait(self) -> bool:
        """Whether :meth:`acquire` would currently block (best-effort, non-consuming).

        Peeks the refilled token count WITHOUT mutating state — used only to decide whether to
        surface a「排队中」phase hint, never for correctness. A racing acquire may change the
        answer between this check and the real acquire; that's fine for a pure UI hint."""
        now = time.monotonic()
        tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
        return tokens < 1.0

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._updated) * self._rate
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = 1.0 - self._tokens
            # Lock released: wait out the shortfall (+ jitter to desync paced waves), then
            # re-check. Racing waiters that wake together re-loop and re-wait — correct,
            # since only one can take the refilled token under the lock.
            await asyncio.sleep(deficit / self._rate + random.uniform(0, _SEARCH_RATE_JITTER_S))


class SearXNGBackend:
    """Search via a self-hosted SearXNG instance (JSON API).

    Holds a persistent ``httpx.AsyncClient``: every query hits the SAME fixed
    SearXNG host, so a long-lived client reuses the connection (keep-alive) across
    searches instead of paying a fresh TCP+TLS handshake per call. The backend is a
    process-wide singleton (:func:`get_search_backend`); its client is closed on app
    shutdown via :func:`aclose_search_backend`.
    """

    def __init__(self, base_url: str | None = None, *, api_key: str | None = None) -> None:
        self.base_url = (base_url or settings.searxng_url).rstrip("/")
        self.api_key = (api_key or "").strip()
        self._client: httpx.AsyncClient | None = None
        self._sem: asyncio.Semaphore | None = None
        self._bucket: _TokenBucket | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """Lazily build the shared client, bound to the running event loop.

        Lazy (not built in ``__init__``) so the client attaches to the server's
        loop at first use, not import time. Single event loop → there is no
        ``await`` between the None-check and the assignment, so concurrent first
        callers can't double-build.

        Connect uses the short ``WEB_CONNECT_TIMEOUT`` (a down host fast-fails into
        the breaker) while the overall budget is the generous ``SEARCH_TIMEOUT`` —
        a multi-engine search is slow-but-reachable, not unreachable.
        """
        if self._client is None:
            self._client = outbound_async_client(
                timeout=httpx.Timeout(SEARCH_TIMEOUT, connect=WEB_CONNECT_TIMEOUT)
            )
        return self._client

    def _get_sem(self) -> asyncio.Semaphore:
        """Lazily build the concurrency gate, bound to the running event loop.

        Lazy for the same reason as the client: an ``asyncio.Semaphore`` binds to
        the loop on first acquire, so building it here (right before ``async with``)
        keeps it on the server's loop rather than import time.
        """
        if self._sem is None:
            self._sem = asyncio.Semaphore(_SEARCH_CONCURRENCY)
        return self._sem

    def _get_bucket(self) -> _TokenBucket:
        """Lazily build the rate-limit token bucket, bound to the running event loop.

        Lazy for the same reason as the semaphore: its ``asyncio.Lock`` binds to the
        loop on first use, so building it here (right before ``acquire``) keeps it on the
        server's loop rather than import time.
        """
        if self._bucket is None:
            self._bucket = _TokenBucket(_SEARCH_RATE_PER_SEC, _SEARCH_RATE_BURST)
        return self._bucket

    async def aclose(self) -> None:
        """Close the persistent client and drop it (idempotent; re-lazies on next use)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._sem = None  # re-lazied (rebinds to a fresh loop) on next use
        self._bucket = None  # re-lazied (rebinds to a fresh loop) on next use

    async def search(
        self,
        query: str,
        max_results: int = DEFAULT_MAX_RESULTS,
        on_phase: PhaseCallback | None = None,
        *,
        language: str | None = None,
    ) -> list[SearchResult]:
        host = _searxng_host(self.base_url)
        remaining = circuit_remaining(host)
        if remaining > 0:
            # Honest cause: the breaker only opens after repeated request failures
            # (timeout / connection). Don't assert "未就绪/出网受限" — under a parallel
            # burst SearXNG is usually up but overloaded, and a misleading message
            # sends devs hunting a service that started fine. Checked BEFORE the
            # semaphore so a fast-fail never consumes a concurrency slot.
            raise EgressError(
                f"搜索服务 {host} 最近连续多次请求失败（超时或连接失败），"
                f"已临时熔断约 {int(remaining)}s，暂不重试；"
                f"若刚启动请稍候，或检查 {host} 是否过载/可达"
            )

        # Pace the outbound RATE before taking a concurrency slot (CAPTCHA defence, see
        # _SEARCH_RATE_*): acquired AFTER the breaker check (a fast-fail mustn't wait for
        # a token) and OUTSIDE the semaphore (waiting for a token mustn't hold a slot).
        # Retries inside the loop are already paced by their own backoff, so they don't
        # re-acquire a token here.
        bucket = self._get_bucket()
        sem = self._get_sem()
        # 工具执行阶段进度: surface「排队中」ONLY when the rate/concurrency gates will actually make
        # this call wait (a parallel-team burst) — a lone search from a full bucket skips straight
        # to「正在检索」. Best-effort, non-consuming peek, so an approximate answer is fine.
        if on_phase and (bucket.would_wait() or sem.locked()):
            on_phase("queued")
        await bucket.acquire()

        lang = (language or infer_search_language(query)).strip() or "en"
        # Explicit language pins result locale — never rely on SearXNG default_lang=auto /
        # server IP (that path produced ja.* pollution on Chinese research queries).
        params = {"q": query, "format": "json", "safesearch": "0", "language": lang}
        client = self._get_client()
        async with sem:  # throttle the parallel-team burst (see _SEARCH_CONCURRENCY)
            # 工具执行阶段进度: a slot is held and the request is about to fly — the main wait.
            if on_phase:
                on_phase("querying")
            for attempt in range(_SEARCH_ATTEMPTS):
                raise_if_task_cancelled()
                try:
                    request_kwargs: dict[str, Any] = {"params": params}
                    if self.api_key:
                        request_kwargs["headers"] = {
                            "Authorization": f"Bearer {self.api_key}"
                        }
                    resp = await client.get(f"{self.base_url}/search", **request_kwargs)
                    resp.raise_for_status()
                    data = resp.json()
                except (httpx.TimeoutException, httpx.NetworkError) as e:
                    # A down / blocked host: fast-fail into the breaker, do not retry.
                    raise_if_task_cancelled(e)
                    note_failure(host)
                    raise
                except httpx.HTTPStatusError as e:
                    raise_if_task_cancelled(e)
                    status = e.response.status_code
                    if status < 500:
                        raise  # client error (4xx): not transient, not a breaker fault
                    if attempt + 1 >= _SEARCH_ATTEMPTS:
                        note_failure(host)  # one failure per call, after exhausting retries
                        raise
                    delay = _SEARCH_RETRY_BASE_S * (2**attempt) + random.uniform(
                        0, _SEARCH_RETRY_JITTER_S
                    )
                    logger.info(
                        "tool.web_search_retry", host=host, attempt=attempt + 1, status=status
                    )
                    await asyncio.sleep(delay)
                else:
                    note_success(host)
                    return _parse_results(data, max_results)
        # Defensive: the loop always returns or raises on its last iteration.
        raise EgressError(f"搜索服务 {host} 连续返回服务端错误（5xx），已停止重试")


def describe_search_error(e: BaseException, backend: SearchBackend | None = None) -> str:
    """Pick SearXNG-local vs public egress copy from the active search backend."""
    if backend is None:
        return describe_net_error(e)
    base = getattr(backend, "base_url", None)
    if isinstance(backend, SearXNGBackend) and isinstance(base, str) and base:
        return describe_searxng_error(e, base_url=base)
    return describe_net_error(e)


_backend: SearchBackend | None = None


def get_search_backend() -> SearchBackend:
    """Build (once) the process-wide SearXNG backend."""
    global _backend
    if _backend is None:
        _backend = SearXNGBackend()
    return _backend


async def aclose_search_backend() -> None:
    """Close the process-wide search backend's HTTP client (app shutdown / tests).

    Wired into the app lifespan so the SearXNG keep-alive pool is released cleanly
    (no "Unclosed client" warning, no leaked sockets). Also the reset hook tests use
    to drop a backend built against a patched client. Duck-typed: closes any backend
    exposing ``aclose``. No-op if never built.
    """
    global _backend
    backend = _backend
    _backend = None
    if backend is not None:
        closer = getattr(backend, "aclose", None)
        if closer is not None:
            await closer()
    from agentcore.tools.builtin.web.cleversee import aclose_cleversee_backend

    await aclose_cleversee_backend()


async def probe_search_backend() -> tuple[bool, str] | None:
    """Best-effort SearXNG reachability check (success → debug, failure → warning).

    Returns ``(ok, detail)``, or ``None`` when the active backend isn't SearXNG
    (nothing to probe). **Never raises** — a down search dependency must not break
    app startup (``web_search`` just degrades). Unreachable / not-started SearXNG is
    logged at warning so it stays visible at boot; the reachable success path is
    debug-only (probe noise). Uses a throwaway client against ``/healthz`` with the
    short connect deadline so the check itself can't hang startup.
    """
    backend = get_search_backend()
    if not isinstance(backend, SearXNGBackend):
        return None  # custom backend: nothing SearXNG-specific to probe
    base = backend.base_url
    try:
        async with outbound_async_client(
            timeout=httpx.Timeout(5.0, connect=WEB_CONNECT_TIMEOUT)
        ) as client:
            resp = await client.get(f"{base}/healthz")
        ok = resp.status_code == 200
        detail = base if ok else f"{base} (HTTP {resp.status_code})"
    except Exception as exc:  # noqa: BLE001 - best-effort; any failure == unreachable
        ok = False
        detail = f"{base} ({type(exc).__name__})"
    if ok:
        logger.debug("searxng.reachable", url=detail)
    else:
        logger.warning(
            "searxng.unreachable",
            target=detail,
            hint="web_search 将不可用，直到 SearXNG 就绪："
            "docker compose -f deploy/docker-compose.dev.yml up -d searxng",
        )
    return ok, detail


# A fixed, innocuous canary for the boot real-search probe: common enough that any
# working engine returns hits, so an EMPTY result means the engine pool is degraded
# (every engine CAPTCHA-suspended / blocked), not that the query was too narrow.
_SEARCH_CANARY_QUERY = "新闻"


async def probe_search_results() -> tuple[bool, int] | None:
    """Best-effort real-search canary (success → debug, failure → warning).

    Stronger than :func:`probe_search_backend` (which only checks ``/healthz``
    reachability): runs ONE real query and reports whether the engine pool actually
    returns results. The production failure mode — SearXNG healthz-200 but every CN
    engine CAPTCHA-suspended, so ``web_search`` silently returns empty — is invisible to
    the reachability probe yet caught here. **One-shot at boot only**, never periodic: a
    frequent active search would itself add the per-IP request volume that triggers the
    very CAPTCHA this defends against. **Never raises** (best-effort, like the
    reachability probe). Returns ``(ok, result_count)`` or ``None`` when no search ran.
    """
    backend = get_search_backend()
    try:
        results = await backend.search(_SEARCH_CANARY_QUERY, max_results=1)
    except Exception as exc:  # noqa: BLE001 - best-effort; any failure == can't confirm
        logger.warning("searxng.canary_failed", reason=describe_search_error(exc, backend))
        return None
    ok = len(results) > 0
    if ok:
        logger.debug("searxng.canary_ok", result_count=len(results))
    else:
        logger.warning(
            "searxng.canary_empty",
            hint="SearXNG 可达但实搜返回 0 条：上游引擎可能全部被限流/CAPTCHA，"
            "web_search 将静默返回空。检查 deploy/searxng 引擎状态或重启 agentcore-searxng",
        )
    return ok, len(results)


async def probe_search_at_startup() -> None:
    """Boot-time search self-check: reachability, then (only if reachable) a real canary.

    Fire-and-forget from the app lifespan. Gating the canary on a reachable ``/healthz``
    keeps a real query off the network when SearXNG is simply down (the reachability
    line already says so) and avoids a pointless breaker hit during a cold boot. Never
    raises — both legs are best-effort.
    """
    reach = await probe_search_backend()
    if reach is not None and reach[0]:
        await probe_search_results()
