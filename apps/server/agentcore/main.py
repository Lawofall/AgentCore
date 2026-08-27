"""FastAPI application entry point."""

import asyncio
import contextlib
import logging
import math
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agentcore.api.routes import (
    account,
    admin,
    auth,
    autonomy,
    boards,
    bookmarks,
    capabilities,
    conversations,
    demo_tape,
    devices,
    documents,
    favicon,
    feedback,
    files,
    folders,
    fulfill,
    git_credentials,
    inference,
    llm_model_profiles,
    llm_providers,
    memory,
    messages,
    model_catalog,
    notices,
    realtime,
    search,
    shared_spaces,
    sharing,
    standing_tasks,
    system,
    usage,
    users,
    workflows,
    workspaces,
)
from agentcore.auth.retention import refresh_token_retention_loop
from agentcore.config import settings
from agentcore.conversation.compaction import shutdown_compaction
from agentcore.core.errors import AgentCoreError, wire_moments
from agentcore.core.logging import get_logger, setup_logging
from agentcore.db.migration_check import check_migrations
from agentcore.mail.sender import is_smtp_configured
from agentcore.memory.consolidation import consolidation_loop, shutdown_scheduler
from agentcore.memory.explore_refresh import shutdown_explore_refresh_scheduler
from agentcore.middleware.client_version import ClientMinVersionMiddleware
from agentcore.middleware.csrf import CsrfMiddleware
from agentcore.middleware.errors import JSONErrorMiddleware
from agentcore.middleware.origin_device import OriginDeviceMiddleware
from agentcore.middleware.rate_limit import AuthRateLimitMiddleware
from agentcore.middleware.request_attribution import RequestAttributionMiddleware
from agentcore.runtime.audit_retention import audit_retention_loop
from agentcore.runtime.session_retention import session_retention_loop
from agentcore.runtime.stream_state_retention import stream_state_retention_loop
from agentcore.runtime.suspension.retention import paused_turn_retention_loop
from agentcore.security.keys import KeyEncryptor
from agentcore.standing_tasks.scheduler import standing_task_scheduler_loop
from agentcore.tools.builtin.web.search_backend import (
    aclose_search_backend,
    probe_search_at_startup,
)
from agentcore.workspace.retention import retention_loop

logger = logging.getLogger(__name__)

# Known placeholder secrets that must never reach production.
_INSECURE_SECRETS = {
    "",
    "dev-secret-change-in-production",
    "change-this-to-a-random-secret-in-production",
}


def _configured_api_worker_count() -> int:
    """Best-effort configured uvicorn/gunicorn worker count from env.

    Our entrypoint always runs a single process; operators sometimes still set
    ``WEB_CONCURRENCY`` / ``UVICORN_WORKERS`` / ``AGENTCORE_API_WORKERS`` when
    wrapping the image. Default 1 when unset or unparseable.
    """
    for key in ("AGENTCORE_API_WORKERS", "WEB_CONCURRENCY", "UVICORN_WORKERS"):
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        try:
            n = int(raw)
        except ValueError:
            continue
        if n >= 1:
            return n
    return 1


def _validate_single_process_assumptions() -> None:
    """Refuse multi-worker boot — two registries are process-local by design.

    - **设备履约中枢** (``fulfill/hub.py``): a desktop's fulfill SSE lands on worker
      A while its turn runs on worker B, which then finds no fulfiller at all —
      local workspace / Host / MCP / 白板 tools break, and local-workspace turns are
      hard-refused by the presence gate as「本机桌面未连接」(not a soft degrade).
    - **对话事件流** (``runtime/turn/runs.py`` · ``runtime/events/conversation_hub.py``):
      attach / 跟播 landing on a worker that is not running the turn yields a bare
      204, or a ``follow=true`` stream that only ever heartbeats. This one is
      **silent** — the client cannot tell it apart from an idle conversation.

    ``RATE_LIMIT_BACKEND=redis`` only shares the limiters and the cost ledger already
    drains a shared Postgres outbox, so neither is the blocker: redis used to wave
    multi-worker straight through, which is exactly how 跟播 could break with no
    signal anywhere. No env var unlocks this — cross-process fan-out is the
    prerequisite, and 部署样例 already forbids multi-worker for the fulfill reason.

    Single-worker is not a readiness claim either: approval gates, IM, steer and
    journal settlement equally assume one process.

    DEBUG keeps local iteration runnable: loud warning only.
    """
    workers = _configured_api_worker_count()
    if workers <= 1:
        return

    backend = settings.rate_limit_backend
    detail = (
        f"configured API workers={workers} (AGENTCORE_API_WORKERS / "
        f"WEB_CONCURRENCY / UVICORN_WORKERS) but this build has no cross-process "
        f"event bus: the device fulfillment hub and the conversation event stream "
        f"(attach / follow) are process-local singletons, so a client's stream and "
        f"its turn can land on different workers — local-workspace / Host / MCP "
        f"tools hard-refuse, and follow streams silently degrade to 204 / "
        f"heartbeat-only. RATE_LIMIT_BACKEND={backend!r} does not unlock this (it "
        f"only shares limiters). Keep workers=1 until cross-process fan-out lands."
    )
    if settings.debug:
        get_logger(__name__).warning(
            "deploy.multi_worker_refused",
            workers=workers,
            rate_limit_backend=backend,
            detail=detail,
        )
        return
    raise RuntimeError(detail)


def _validate_jwt_secret() -> None:
    """Refuse known placeholder JWT secrets unless local-dev explicitly opts in.

    DEBUG alone is not enough — a publicly reachable process can still run with
    DEBUG=true. The placeholder is only allowed when DEBUG=true *and*
    ALLOW_INSECURE_JWT_SECRET=true.
    """
    if settings.jwt_secret_key not in _INSECURE_SECRETS:
        return
    if settings.debug and settings.allow_insecure_jwt_secret:
        get_logger(__name__).warning(
            "security.insecure_jwt_secret",
            detail="JWT_SECRET_KEY is a known placeholder; allowed only because "
            "DEBUG=true and ALLOW_INSECURE_JWT_SECRET=true",
        )
        return
    raise RuntimeError(
        "JWT_SECRET_KEY is unset or still a default placeholder. Set a strong, "
        "random secret, or for local development only set DEBUG=true and "
        "ALLOW_INSECURE_JWT_SECRET=true."
    )


def _validate_cors_credentials() -> None:
    """Credentialed CORS forbids ``*`` — browsers reject ``ACA-Origin: *`` + credentials.

    ``CORSMiddleware`` is installed with ``allow_credentials=True``; a wildcard
    origin list is therefore always unsafe. Production refuses to boot; DEBUG
    warns so local misconfig is visible without blocking iteration.
    """
    if "*" not in settings.cors_origins:
        return
    detail = (
        "CORS_ALLOW_ORIGINS contains '*' while credentialed CORS is enabled "
        "(allow_credentials=True). Browsers reject Access-Control-Allow-Origin: * "
        "with credentials; list explicit origins instead."
    )
    if settings.debug:
        get_logger(__name__).warning("security.cors_wildcard_credentials", detail=detail)
        return
    raise RuntimeError(detail)


def _validate_smtp_for_open_registration() -> None:
    """Warn when open registration cannot deliver verification emails.

    ``ConsoleEmailSender`` in non-DEBUG logs ``email.unconfigured`` and never raises,
    so ``POST /v1/auth/register/send-code`` returns 202 while signup cannot complete.
    Loud at boot — not fatal (same posture as ``security.csrf_disabled``).
    """
    if not settings.registration_open or is_smtp_configured():
        return
    get_logger(__name__).warning(
        "email.smtp_unconfigured_registration",
        detail=(
            "REGISTRATION_OPEN=true but SMTP is not configured (SMTP_HOST and "
            "SMTP_FROM_ADDRESS required). POST /v1/auth/register/send-code will "
            "return 202 while no verification email is delivered; signup cannot "
            "complete. Configure SMTP or set REGISTRATION_OPEN=false."
        ),
    )


def _validate_production_security() -> None:
    """Fail fast on insecure config. JWT secret always checked; other guards skip in debug."""
    _validate_jwt_secret()
    _validate_cors_credentials()
    _validate_single_process_assumptions()
    if settings.debug:
        return
    _validate_smtp_for_open_registration()
    # byok makes a per-user API key mandatory, so a usable master key is required
    # to store it. Without one the model-config page can't save a key and every
    # turn is blocked — fail closed at boot rather than ship a server that looks
    # healthy (livez/readyz green) but can't chat (安全权限与治理.md §七).
    if settings.billing_mode == "byok" and not settings.encryption_key:
        raise RuntimeError(
            "ENCRYPTION_KEY is unset but billing_mode=byok requires it (users "
            "store their own API key, encrypted at rest). Generate one: "
            'python -c "import secrets; print(secrets.token_hex(32))".'
        )
    # Any non-empty ENCRYPTION_KEY must be valid 64-hex — a malformed value otherwise
    # surfaces later as binascii / KeyEncryptor 500s on BYOK paths (resolve / MFA).
    if settings.encryption_key:
        try:
            KeyEncryptor(settings.encryption_key)
        except ValueError as exc:
            raise RuntimeError(
                "ENCRYPTION_KEY is malformed (must be 64 hex chars = 32 bytes). "
                'Generate one: python -c "import secrets; print(secrets.token_hex(32))".'
            ) from exc
    if not settings.cookie_secure:
        logger.warning(
            "security.cookie_insecure",
            detail="COOKIE_SECURE is false in a non-debug run: auth cookies may "
            "travel over plain HTTP; set COOKIE_SECURE=true when serving over HTTPS",
        )
    # SameSite=None is required for the cross-site desktop (app://) → API cookie to
    # ride credentialed requests, but browsers silently drop a None cookie that
    # isn't also Secure — fail closed rather than ship broken desktop auth.
    if settings.cookie_samesite.lower() == "none" and not settings.cookie_secure:
        raise RuntimeError(
            "COOKIE_SAMESITE=none requires COOKIE_SECURE=true (browsers drop a "
            "SameSite=None cookie without Secure). Set COOKIE_SECURE=true."
        )
    # CSRF is the *only* thing standing between a cross-site page and the ambient
    # access cookie for browser/desktop sessions (SameSite=None is required for the
    # desktop app:// origin, so the cookie rides cross-site requests by design), and
    # turning it off is invisible at runtime — the server looks healthy and every
    # request succeeds. Loud, not fatal: refusing the boot only ever fired mid-deploy
    # on a self-hosted install, where a server that will not start is the worse
    # outcome and the operator is right there reading this line.
    if not settings.csrf_enabled:
        logger.warning(
            "security.csrf_disabled",
            detail="CSRF_ENABLED=false leaves cookie-session clients (admin console "
            "/ desktop) with no CSRF protection: any cross-site page could drive "
            "authenticated state changes using the ambient access cookie. Set "
            "CSRF_ENABLED=true (bearer-token mobile clients are unaffected).",
        )
    # The code-execution tool class (code_execute AND test_run — a test suite runs
    # arbitrary project code through the SAME sandbox chain) on a cloud/server worker
    # runs untrusted model/user code. With GVISOR_ENABLED the runsc sandbox provides a
    # real isolation boundary; without it, execution is a plain subprocess INSIDE the
    # API container — no namespace/seccomp/rlimit/egress isolation, so it is effectively
    # authenticated RCE with access to JWT_SECRET_KEY / ENCRYPTION_KEY and every user's
    # encrypted keys. The whole class is default-off on cloud and gated by the SAME
    # config here (both withheld from the worker registry via code_execution_enabled_for),
    # but a single CODE_EXECUTE_CLOUD_ENABLED flip would silently expose it; require a
    # second, explicitly-named acknowledgement so the unsafe config can't be reached by
    # accident (SEC-005).
    if (
        settings.code_execute_cloud_enabled
        and not settings.gvisor_enabled
        and not settings.code_execute_cloud_unsafe_ack
    ):
        raise RuntimeError(
            "CODE_EXECUTE_CLOUD_ENABLED=true runs untrusted code in a plain subprocess "
            "inside the API container — NOT an isolation boundary (authenticated RCE with "
            "access to in-process secrets). Keep it off (recommended; local/sidecar "
            "workers still run code), enable GVISOR_ENABLED=true for a real sandbox, "
            "or — only without gVisor — set "
            "CODE_EXECUTE_CLOUD_UNSAFE_ACK=true to acknowledge the risk explicitly."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    _validate_production_security()
    # Cloud sandbox availability: first probe when config would enable execution
    # (the verdict then TTL-refreshes in the background — runsc can rot after boot).
    # Failure must not block boot — folds into ``code_execution_enabled_for`` so
    # code_execute/test_run stay withheld and workspace_context says 未装配.
    from agentcore.tools.sandbox.cloud_health import probe_cloud_sandbox_at_startup

    await probe_cloud_sandbox_at_startup()
    # Server-side ``git`` binary: withholds the git tool on an image/PATH without it
    # rather than letting every call surface as FileNotFoundError. Orthogonal to the
    # sandbox — git is spawned by the API process, not inside gVisor.
    from agentcore.tools.builtin.git_ops.binary_health import probe_git_binary_at_startup

    await probe_git_binary_at_startup()
    # Schema-drift notice: warn loudly (never block) when the live DB is behind the
    # migration head — or at head yet missing schema the ORM maps — so a missing
    # migration surfaces at boot instead of as a mid-session UndefinedColumnError
    # on a core endpoint.
    await check_migrations()

    # Platform credential pool: decrypt into the process snapshot so the sync
    # pick path (platform_llm_credentials) does not open a session. Empty pool
    # → env PLATFORM_API_KEY fallback. Failure here must not block boot.
    from agentcore.llm.platform_credential_service import (
        platform_credential_pool_refresh_loop,
        reload_platform_credential_pool_from_factory,
    )

    await reload_platform_credential_pool_from_factory()
    pool_refresh_task = asyncio.create_task(platform_credential_pool_refresh_loop())

    # Background retention sweep (决策⑦): physically purge soft-deleted workspaces
    # past their grace period. Best-effort and self-contained; cancelled cleanly
    # on shutdown. Disabled config → no task.
    retention_task: asyncio.Task | None = None
    if settings.workspace_retention_enabled:
        retention_task = asyncio.create_task(retention_loop())

    # Long-term-memory consolidation backstop (Agent记忆 §1.5): periodically sweep
    # settled conversations whose latest message is past the watermark and fold them
    # into the user's memory — covers a debounce dropped by a restart / closed
    # client. The live path is the per-turn idle debounce (memory/consolidation.py).
    consolidation_task: asyncio.Task | None = None
    if settings.memory_consolidation_enabled:
        consolidation_task = asyncio.create_task(consolidation_loop())

    # Memory layout / documents→tables backfill runs in deploy (stop-api window),
    # not at lifespan — see scripts/migrate_memory_pipeline.py.

    # Dev-only demo-tape recorder (录制层): tap every live turn's SSE stream into
    # demos/recordings/ so a satisfying run can be exported as a tape verbatim.
    # No-op unless DEMO_TAPE_RECORD_ENABLED; never enabled in production.
    if settings.demo_tape_record_enabled:
        from agentcore.demo_tape.recorder import install_recorder

        install_recorder()

    # Dev-experience: log SearXNG ✓/✗ at boot so a not-started search dependency is
    # visible immediately instead of only surfacing mid-run as a breaker message. The
    # probe also runs a one-shot real-search canary when reachable, so a healthz-200-but-
    # every-engine-CAPTCHA SearXNG (the production failure mode) is visible at boot too.
    # Fire-and-forget — bounded by the probe's own short timeout and never blocks or
    # fails startup (web_search just degrades while SearXNG is down).
    searxng_probe_task = asyncio.create_task(probe_search_at_startup())

    # Recoverable-worker roster TTL sweep (留人 跨进程落盘 P3): prune run_sessions
    # rows idle past the 7-day window so the durable roster stays bounded.
    session_retention_task: asyncio.Task | None = None
    if settings.session_roster_persist_enabled:
        session_retention_task = asyncio.create_task(session_retention_loop())

    audit_retention_task: asyncio.Task | None = None
    audit_retention_task = asyncio.create_task(audit_retention_loop())

    refresh_token_retention_task = asyncio.create_task(refresh_token_retention_loop())

    # Paused-turn TTL sweep (结构化挂起 2b): prune paused_turns frames abandoned past
    # the 7-day window so durable suspensions stay bounded. The live resolve path drops
    # connected pauses; this only catches the disconnected, never-resumed remainder.
    paused_turn_retention_task: asyncio.Task | None = None
    if settings.structured_suspension_persist_enabled:
        paused_turn_retention_task = asyncio.create_task(paused_turn_retention_loop())

    # In-flight stream snapshot TTL (mirror paused_turns 7d): prune leftover
    # turn_stream_state rows. Live finalize / delete cascade drop the connected
    # path; this only catches the disconnected remainder. days<=0 disables.
    stream_state_retention_task = asyncio.create_task(stream_state_retention_loop())

    # Standing tasks / 定时自动化 L1: poll next_run_at + lease, spawn cloud runs.
    standing_task_scheduler_task: asyncio.Task | None = None
    if settings.standing_task_scheduler_enabled:
        standing_task_scheduler_task = asyncio.create_task(standing_task_scheduler_loop())

    # Durable RUNNING lease sweeper (crash recover): claim heartbeat-expired leases and
    # redrive unfinished DAG via recover_turn. Boot pass runs inside the loop.
    # Install the DelegateTool factory BEFORE the sweeper so a boot reclaim can redrive
    # (not just salvage) unfinished workers.
    turn_lease_sweep_task: asyncio.Task | None = None
    if settings.turn_lease_enabled:
        from agentcore.runtime.crash_delegate import production_crash_delegate_factory
        from agentcore.runtime.leases import turn_lease_sweep_loop
        from agentcore.runtime.recover_hooks import set_crash_delegate_factory

        set_crash_delegate_factory(production_crash_delegate_factory)
        turn_lease_sweep_task = asyncio.create_task(turn_lease_sweep_loop())

    # L3 团队浏览器 (M0): recycle idle / over-lifetime browser sandboxes (~1GB each).
    # Only when gVisor is available (browser is cloud-only + gVisor-gated); the lazy
    # on-access checks are the live path, this is the go-quiet backstop.
    browser_reaper_task: asyncio.Task | None = None
    if settings.gvisor_enabled:
        from agentcore.runtime.browser.registry import browser_reaper_loop

        browser_reaper_task = asyncio.create_task(browser_reaper_loop())
        # L3 团队浏览器 M1 直播 (D13): wire the live hub as the registry's observer now so a
        # session created before the first viewer still announces + spares watched-session TTL.
        from agentcore.runtime.browser.live import default_browser_live_hub

        default_browser_live_hub()
        # L3 团队浏览器 M2 接管 (D17): wire the takeover finalizer now so an active takeover's
        # record is completed even when its session is reaped/recycled with no prior endpoint hit.
        from agentcore.runtime.browser.takeover import default_browser_takeover_service

        default_browser_takeover_service()

    # Single-process event-loop lag: 1 Hz sleep overrun. Answers「当时卡没卡」
    # without a metrics backend; cancelled on shutdown with the other loops.
    from agentcore.observability.event_loop_lag import event_loop_lag_loop

    event_loop_lag_task = asyncio.create_task(event_loop_lag_loop())

    # Cost ledger durable drain (as-built: 成本配额 §三): shared Postgres
    # ``cost_ledger_outbox``; each process self-drains (SKIP LOCKED). Multi-worker
    # API also needs ``RATE_LIMIT_BACKEND=redis`` (startup guardrail).
    from agentcore.billing.cost_ledger_queue import get_cost_ledger_queue

    cost_ledger_queue = get_cost_ledger_queue()
    cost_ledger_queue.start()

    boot_log = get_logger(__name__)
    boot_log.info(
        "server.started",
        host=settings.host,
        port=settings.port,
        turn_lease_enabled=settings.turn_lease_enabled,
        # Same provenance pair as GET /version: semver answers「线上跑的哪个版本」,
        # git_sha pins the exact build when two deploys share a version.
        version=system.app_version(),
        git_sha=settings.git_sha,
    )

    try:
        yield
    finally:
        boot_log.info("server.shutdown", reason="lifespan")
        # Signal the lag probe to stop *before* salvage: a 20s salvage busy-loop
        # would otherwise look like event-loop stall and spam event_loop.lag.
        # Do not await here — cancel of sleep(1) is enough to silence it; the
        # await stays after salvage so a stuck probe cannot squeeze the 20s grace.
        event_loop_lag_task.cancel()
        # Graceful turn salvage BEFORE tearing down DB consumers / sweeper reclaim:
        # interrupt live turns like /stop, await unwind (grace), force-release leftovers.
        # Stop the lease sweeper first so it cannot race reclaim during salvage.
        if turn_lease_sweep_task is not None:
            turn_lease_sweep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await turn_lease_sweep_task
            turn_lease_sweep_task = None
        from agentcore.runtime.turn.runs import salvage_turns_on_shutdown

        with contextlib.suppress(Exception):
            await salvage_turns_on_shutdown()

        async def _teardown_after_salvage() -> None:
            await cost_ledger_queue.stop()
            # Stop the boot probe if shutdown races its short window (no-op once done).
            searxng_probe_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await searxng_probe_task
            if retention_task is not None:
                retention_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await retention_task
            if consolidation_task is not None:
                consolidation_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await consolidation_task
            if session_retention_task is not None:
                session_retention_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await session_retention_task
            if audit_retention_task is not None:
                audit_retention_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await audit_retention_task
            refresh_token_retention_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await refresh_token_retention_task
            if paused_turn_retention_task is not None:
                paused_turn_retention_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await paused_turn_retention_task
            stream_state_retention_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stream_state_retention_task
            pool_refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pool_refresh_task
            if standing_task_scheduler_task is not None:
                standing_task_scheduler_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await standing_task_scheduler_task
            event_loop_lag_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await event_loop_lag_task
            if browser_reaper_task is not None:
                browser_reaper_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await browser_reaper_task
                # Tear down any live browser sandboxes + the shared egress proxy.
                from agentcore.runtime.browser.registry import shutdown_browser_sessions

                await shutdown_browser_sessions()
            from agentcore.tools.sandbox.gvisor import close_all_desk_sessions

            await close_all_desk_sessions()
            # Flush in-flight debounced passes and cancel pending timers.
            await shutdown_scheduler()
            await shutdown_explore_refresh_scheduler()
            # Flush in-flight long-conversation compaction folds.
            await shutdown_compaction()
            # Release the shared SearXNG keep-alive pool.
            await aclose_search_backend()
            if settings.demo_tape_record_enabled:
                from agentcore.demo_tape.recorder import uninstall_recorder

                uninstall_recorder()

        try:
            await asyncio.wait_for(
                _teardown_after_salvage(),
                timeout=float(settings.shutdown_teardown_seconds),
            )
        except TimeoutError:
            boot_log.warning(
                "server.shutdown_teardown_timeout",
                timeout_seconds=settings.shutdown_teardown_seconds,
            )


app = FastAPI(
    title="AgentCore",
    description="Multi-Agent AI Workspace API",
    version=system.app_version(),
    lifespan=lifespan,
)

# Middleware runs outermost-last-added: register the rate limiter first so CORS
# wraps it and even a 429 response carries the CORS headers the browser needs.
# Innermost: stamp http_method/path/req_id + client_platform/version onto the
# same task that checkouts use (must sit inside BaseHTTPMiddleware so
# contextvars are not stranded on a parent).
app.add_middleware(RequestAttributionMiddleware)
# Also innermost + pure ASGI: the turn task created by a route handler copies this
# context, so CLIENT_TOOL ops can be pinned to the device that sent the request.
app.add_middleware(OriginDeviceMiddleware)
app.add_middleware(CsrfMiddleware)
app.add_middleware(AuthRateLimitMiddleware)
# Desktop + native mobile floors (426 CLIENT_TOO_OLD) before CSRF/rate-limit work;
# still inside CORS so the rejection carries Access-Control-* headers.
app.add_middleware(ClientMinVersionMiddleware)
# Added just before CORS so it sits *inside* the CORS layer: an unhandled error
# (anything not an AgentCoreError, e.g. a raw DB error) becomes a JSON 500 that
# still flows back out through CORSMiddleware and gets the CORS headers — instead
# of Starlette's outermost bare 500 that lacks them and surfaces as a misleading
# CORS/network error in the browser.
app.add_middleware(JSONErrorMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Downloads (导出对话 / workspace zips) carry the filename in Content-Disposition;
    # browsers hide non-simple response headers cross-origin unless explicitly exposed,
    # so the renderer can read the server's sanitized UTF-8 filename instead of guessing.
    expose_headers=["Content-Disposition", "X-CSRF-Token"],
)


@app.exception_handler(AgentCoreError)
async def agentcore_error_handler(request, exc: AgentCoreError):
    from fastapi.responses import JSONResponse

    # Surface Retry-After on errors that carry a cool-down (e.g. RateLimitedError),
    # whole seconds per RFC 7231, rounded up so the client never retries early.
    headers: dict[str, str] | None = None
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None and retry_after > 0:
        headers = {"Retry-After": str(math.ceil(retry_after))}

    # Absolute moments ride flat on ``error`` here; the SSE / inference-leaf envelope
    # carries them inside ``error.context`` instead (that slot also holds upstream
    # previews and counters, which have no business in a plain REST body). A strict
    # whitelist either way — the quota gate's copy no longer names a UTC clock time,
    # so this is the only way a client can put one in the reader's own zone.
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {"code": exc.code, "message": exc.message, **wire_moments(exc)}
        },
        headers=headers,
    )


app.include_router(system.router)
app.include_router(admin.router, prefix="/v1")
app.include_router(account.router, prefix="/v1")
app.include_router(auth.router, prefix="/v1")
app.include_router(autonomy.router, prefix="/v1")
app.include_router(boards.router, prefix="/v1")
app.include_router(bookmarks.router, prefix="/v1")
app.include_router(capabilities.router, prefix="/v1")
app.include_router(conversations.router, prefix="/v1")
app.include_router(demo_tape.router, prefix="/v1")
app.include_router(devices.router, prefix="/v1")
app.include_router(documents.router, prefix="/v1")
app.include_router(favicon.router, prefix="/v1")
app.include_router(feedback.router, prefix="/v1")
app.include_router(files.router, prefix="/v1")
app.include_router(folders.router, prefix="/v1")
app.include_router(fulfill.router, prefix="/v1")
app.include_router(git_credentials.router, prefix="/v1")
app.include_router(notices.router, prefix="/v1")
app.include_router(inference.router, prefix="/v1")
app.include_router(llm_providers.router, prefix="/v1")
app.include_router(llm_model_profiles.router, prefix="/v1")
app.include_router(memory.router, prefix="/v1")
app.include_router(messages.router, prefix="/v1")
app.include_router(model_catalog.router, prefix="/v1")
app.include_router(realtime.router, prefix="/v1")
app.include_router(search.router, prefix="/v1")
app.include_router(shared_spaces.router, prefix="/v1")
# Conversation sharing (分享对话): owner-only manage under /v1, plus the public
# read-only page at the root (/shared/{token}, no /v1, no auth).
app.include_router(sharing.router, prefix="/v1")
app.include_router(sharing.public_router)
app.include_router(standing_tasks.router, prefix="/v1")
app.include_router(standing_tasks.hooks_router, prefix="/v1")
app.include_router(workflows.router, prefix="/v1")
app.include_router(usage.router, prefix="/v1")
app.include_router(users.router, prefix="/v1")
app.include_router(workspaces.router, prefix="/v1")
