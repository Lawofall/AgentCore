"""Request rate limiting middleware.

Throttles auth endpoints (login, register, refresh) to blunt credential-stuffing
and registration spam on the public net. Per-account lockout already lives in the
auth service; this adds per-IP throttling across accounts.

Backend follows ``settings.rate_limit_backend``: ``memory`` (process-local) or
``redis`` (shared across workers via ``redis_rate_limit``). Core limiter interfaces
live in ``agentcore.core.rate_limit`` (framework-free); this module is the thin
ASGI adapter plus settings-backed singletons.
"""

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.core.rate_limit import (
    FixedWindowRateLimiter,
    RateLimitDecision,
    RateLimiter,
    SlidingWindowRateLimiter,
)

logger = get_logger(__name__)

# Re-export core limiters for existing ``from agentcore.middleware.rate_limit import …``
__all__ = [
    "AuthRateLimitMiddleware",
    "FixedWindowRateLimiter",
    "RateLimitDecision",
    "RateLimiter",
    "SlidingWindowRateLimiter",
    "auth_rate_limiter",
    "get_client_ip",
    "inference_token_mint_limiter",
    "message_rate_limiter",
    "mfa_verify_rate_limiter",
    "email_send_cooldown_limiter",
    "email_send_daily_limiter",
    "email_send_ip_limiter",
    "reset_rate_limit_state",
]


def _warn_redis_fallback(*, prefix: str, exc: Exception) -> None:
    """Redis construct/ping failed → keep serving with a process-local bucket.

    Availability over strict multi-worker throttling: a Redis outage must not
    fail-closed the login path. Ops can alert on the stable event name.
    """
    logger.warning(
        "security.rate_limit_redis_fallback",
        prefix=prefix,
        error=str(exc),
        detail="Redis rate limiter unavailable; falling back to in-memory bucket",
    )


# Module-level singletons sized from settings; exposed so tests can reset state.
def _build_auth_rate_limiter():
    if settings.rate_limit_backend == "redis":
        try:
            from agentcore.middleware.redis_rate_limit import (
                RedisFixedWindowRateLimiter,
                redis_client,
            )

            return RedisFixedWindowRateLimiter(
                client=redis_client(),
                prefix="rl:auth",
                max_requests=settings.auth_rate_limit_max,
                window_seconds=settings.auth_rate_limit_window_seconds,
            )
        except Exception as exc:
            _warn_redis_fallback(prefix="rl:auth", exc=exc)
    return FixedWindowRateLimiter(
        max_requests=settings.auth_rate_limit_max,
        window_seconds=settings.auth_rate_limit_window_seconds,
    )


def _build_sliding_rate_limiter(*, prefix: str, max_requests: int, window_seconds: float):
    if settings.rate_limit_backend == "redis":
        try:
            from agentcore.middleware.redis_rate_limit import (
                RedisSlidingWindowRateLimiter,
                redis_client,
            )

            return RedisSlidingWindowRateLimiter(
                client=redis_client(),
                prefix=prefix,
                max_requests=max_requests,
                window_seconds=window_seconds,
            )
        except Exception as exc:
            _warn_redis_fallback(prefix=prefix, exc=exc)
    return SlidingWindowRateLimiter(
        max_requests=max_requests,
        window_seconds=window_seconds,
    )


auth_rate_limiter = _build_auth_rate_limiter()
message_rate_limiter = _build_sliding_rate_limiter(
    prefix="rl:msg",
    max_requests=settings.user_message_rate_limit_max,
    window_seconds=settings.user_message_rate_limit_window_seconds,
)
inference_token_mint_limiter = _build_sliding_rate_limiter(
    prefix="rl:inf",
    max_requests=settings.inference_token_mint_max,
    window_seconds=settings.inference_token_mint_window_seconds,
)
mfa_verify_rate_limiter = _build_sliding_rate_limiter(
    prefix="rl:mfa",
    max_requests=settings.mfa_verify_rate_limit_max,
    window_seconds=settings.mfa_verify_rate_limit_window_seconds,
)
email_send_cooldown_limiter = _build_sliding_rate_limiter(
    prefix="rl:email-cd",
    max_requests=1,
    window_seconds=settings.email_send_cooldown_seconds,
)
email_send_daily_limiter = _build_sliding_rate_limiter(
    prefix="rl:email-day",
    max_requests=settings.email_send_daily_max,
    window_seconds=86400,
)
email_send_ip_limiter = _build_sliding_rate_limiter(
    prefix="rl:email-ip",
    max_requests=settings.email_send_ip_hourly_max,
    window_seconds=3600,
)


def reset_rate_limit_state() -> None:
    """Clear all counters (test isolation between cases)."""
    auth_rate_limiter.reset()
    message_rate_limiter.reset()
    inference_token_mint_limiter.reset()
    mfa_verify_rate_limiter.reset()
    email_send_cooldown_limiter.reset()
    email_send_daily_limiter.reset()
    email_send_ip_limiter.reset()
    from agentcore.conversation.inference_rate_limit import reset_inference_proxy_turn_claims

    reset_inference_proxy_turn_claims()


def get_client_ip(request: Request) -> str:
    """Resolve the client IP using the same trust_proxy / XFF hop rules as rate limiting
    (SEC-008). Auth session bookkeeping must call this — do not re-invent XFF parsing."""
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            # Take the Nth entry from the RIGHT — the IP appended by your own trusted
            # proxy — not the leftmost (client-controlled, trivially spoofed to rotate
            # the rate-limit key past per-IP throttling). N = number of trusted proxies
            # in front of the app (SEC-008).
            hops = settings.trusted_proxy_hops if settings.trusted_proxy_hops > 0 else 1
            if len(parts) >= hops:
                return parts[-hops]
            # Chain shorter than the configured trusted-proxy count → the request didn't
            # traverse the expected proxies, so XFF is untrustworthy; fall back to the
            # real socket peer rather than honor a (possibly spoofed) shorter chain.
    client = request.client
    return client.host if client else "unknown"


def _client_key(request: Request) -> str:
    return get_client_ip(request)


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    """Throttle POSTs under the auth path prefix per client IP."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: FixedWindowRateLimiter | None = None,
        path_prefix: str = "/v1/auth/",
    ) -> None:
        super().__init__(app)
        self._limiter = limiter or auth_rate_limiter
        self._path_prefix = path_prefix

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        throttled = (
            settings.rate_limit_enabled
            and request.method == "POST"
            and request.url.path.startswith(self._path_prefix)
        )
        if throttled and not self._limiter.allow(_client_key(request)):
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Too many requests. Slow down and retry shortly.",
                    }
                },
                headers={"Retry-After": str(int(settings.auth_rate_limit_window_seconds))},
            )
        return await call_next(request)
