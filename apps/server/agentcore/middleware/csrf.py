"""CSRF protection for cookie-authenticated clients (admin console, desktop).

Cross-origin SPAs cannot read an API-scoped cookie, so the token is delivered via the
CORS-exposed ``X-CSRF-Token`` response header and the client echoes it in the same
header on mutating requests. It goes out at exactly four moments: login and refresh
(:func:`issue_csrf_token` — the responses that open or renew a session), ``GET
/v1/auth/me`` when it authenticates as a cookie session
(:func:`issue_csrf_token_for_cookie_session` — the identity handshake a cold start
makes instead of logging in), and the 403 that rejects a session holding no usable
one, which hands back a token the client can retry the *same* request with.

Deliberately not on every response. The token's lifetime is the refresh window and it
is unrevocable (the one stamped on a logout still verifies after the next login), so
stamping every GET and every SSE reconnect would smear it across proxy access logs,
user-exported network traces and error-reporting breadcrumbs — whereas the four above
are one response per session opening, not per request.

The token is **stateless**: it is HMAC-signed and verified against the request's
authenticated ``user_id`` (see :func:`agentcore.security.sign_csrf_token`), so it
survives server restarts/reloads and works across workers with no shared store.
Bearer-token clients (mobile) are exempt.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from agentcore.api.dependencies import ACCESS_TOKEN_COOKIE
from agentcore.config import settings
from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import AuthenticationError
from agentcore.core.logging import get_logger
from agentcore.security.csrf import CsrfRejectReason, csrf_reject_reason, sign_csrf_token
from agentcore.security.tokens import decode_access_token

logger = get_logger(__name__)

CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_EXEMPT_PREFIXES = (
    "/v1/auth/login",
    "/v1/auth/register",
    "/v1/auth/password",
    "/v1/auth/refresh",
    "/v1/auth/token",
    "/v1/hooks/",
    "/shared/",
)


def issue_csrf_token(response: Response, user_id: str) -> str:
    """Mint a CSRF token for ``user_id`` and put it on a response that opens or renews
    a session (login / refresh).

    This is the client's normal supply: it arms the session for as long as that
    session lives, and every renewal replaces it. Only a handshake mints — a response
    that merely serves a request leaves the client's token alone.
    """
    token = sign_csrf_token(user_id)
    response.headers[CSRF_HEADER] = token
    return token


def csrf_session_user_id(request: Request) -> str | None:
    """The session this request authenticates as, or ``None`` if it is not a cookie
    session (pure bearer client, anonymous, or an expired/forged access cookie)."""
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        return None
    try:
        return decode_access_token(token)
    except AuthenticationError:
        return None


def issue_csrf_token_for_cookie_session(
    request: Request, response: Response, user_id: str
) -> str | None:
    """Arm ``response`` iff ``request`` authenticated as a cookie session, else ``None``.

    For the one handshake that is neither a login nor a refresh: ``GET /v1/auth/me``,
    which a cold start makes on a still-live access cookie. Cookie-session-ness is
    decided exactly as enforcement decides it — by the access cookie, never by a header
    the client chooses — so the surface that is never checked is also never armed: a
    pure bearer client (mobile, the desktop main-process outbox) has no cookie to bind
    a token to and would only carry a useless secret around.

    ``user_id`` is the *authenticated* subject; requiring it to match the cookie's keeps
    this from minting for anyone but the session that would be checked, however the
    auth layer resolves identity.
    """
    if not settings.csrf_enabled:
        return None
    if csrf_session_user_id(request) != user_id:
        return None
    return issue_csrf_token(response, user_id)


class CsrfMiddleware(BaseHTTPMiddleware):
    """Require a valid ``X-CSRF-Token`` on cookie-session mutating requests, and
    re-arm the session on the 403 when it turns out not to hold one."""

    async def dispatch(self, request: Request, call_next):
        if not settings.csrf_enabled or not self._is_protected(request):
            return await call_next(request)
        # A cookie session must pass the check even when the request also carries an
        # Authorization header: the auth layer prefers the cookie (``access_token or
        # bearer``), so treating a bearer header as an exemption would let an attacker
        # skip CSRF by bolting a bogus one onto a cross-site request while still
        # authenticating via the ambient cookie — collapsing CSRF protection onto the
        # CORS allowlist (SEC-003). Only a *pure* bearer client (mobile) is exempt,
        # and it is ruled out here by having no access cookie to decode.
        user_id = csrf_session_user_id(request)
        if user_id is not None:
            rejection = self._reject(request, user_id)
            if rejection is not None:
                return rejection
        return await call_next(request)

    @staticmethod
    def _is_protected(request: Request) -> bool:
        if request.method in _SAFE_METHODS:
            return False
        return not any(request.url.path.startswith(p) for p in _EXEMPT_PREFIXES)

    @staticmethod
    def _reject(request: Request, user_id: str) -> Response | None:
        """The 403 for a mutating cookie-session request with no usable token, or
        ``None`` when the presented token verifies."""
        reason = csrf_reject_reason(user_id, request.headers.get(CSRF_HEADER) or "")
        if reason is None:
            return None
        # One rejection per client is routine — the 403 below re-arms the session and
        # the retry lands. What ops alert on is the shape that does not clear: a live
        # session 403ing every mutating request while every GET still succeeds, which
        # reads to users as "the app ignores my clicks". ``reason`` separates "never
        # had one" from "aged out" from a real binding failure, and ``user_id`` makes
        # a residual rejection attributable to a session.
        logger.warning(
            "security.csrf_rejected",
            path=request.url.path,
            method=request.method,
            reason=reason.value,
            user_id=user_id,
            client_platform=request.headers.get("X-Client-Platform"),
            client_version=request.headers.get("X-Client-Version"),
        )
        response = JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": ErrorCode.CSRF_FAILED.value,
                    "message": "CSRF token missing or invalid. Re-login and retry.",
                }
            },
        )
        # A client that merely has no usable token — cold start on a still-live access
        # cookie, a renderer reload, an aged-out one — is armed by this very 403, so
        # replaying the same request succeeds and the fault costs one round trip
        # instead of a re-login. A signature mismatch is different: the presented
        # token was minted for a *different* session, so re-arming this one would
        # silently re-aim the client at whoever owns the cookie now — the user
        # retries, it "works", and the write lands on the other account. That case
        # stays loud, and its root cause (two SPAs pointed at one API origin, sharing
        # a cookie jar) is fixed at the deploy layer.
        if reason is not CsrfRejectReason.SIGNATURE_MISMATCH:
            response.headers[CSRF_HEADER] = sign_csrf_token(user_id)
        return response
