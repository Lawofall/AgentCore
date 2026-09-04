"""Auth, cookies, CORS, and rate-limit settings."""

from typing import Literal

from pydantic import BaseModel, computed_field, field_validator


class AuthSettings(BaseModel):
    jwt_secret_key: str = "dev-secret-change-in-production"
    # Explicit local-dev opt-in to keep using a known placeholder JWT secret.
    # Required together with DEBUG=true; otherwise boot refuses the placeholder
    # even when DEBUG is on (blocks "DEBUG=true but exposed to the network").
    allow_insecure_jwt_secret: bool = False
    jwt_access_token_expire_minutes: int = 30
    # Refresh tokens rotate on every use and each rotation stamps a fresh
    # now+N-day expiry (auth/service.py _issue_tokens) — a *sliding* idle window
    # capped by refresh_family_max_days / admin_refresh_family_max_hours (or
    # ephemeral_refresh_family_max_hours when persist_session=false). 30d
    # tolerates a month of inactivity before a forced re-login. The refresh +
    # CSRF cookies' max_age both track this value when persist_session=true;
    # persist_session=false omits Max-Age (browser session cookies).
    jwt_refresh_token_expire_days: int = 30
    # Absolute ceiling on a refresh family (from family_started_at), independent of
    # the sliding jwt_refresh_token_expire_days window. Past this → force re-login.
    refresh_family_max_days: int = 90
    # Admin-audience families get a tighter absolute ceiling (hours), overriding
    # refresh_family_max_days when client_aud=admin.
    admin_refresh_family_max_hours: int = 24
    # Absolute ceiling for persist_session=false families (session cookies / short
    # bearer TTL). Overrides the product/admin ceilings above when the family was
    # minted without persistent login.
    ephemeral_refresh_family_max_hours: int = 8
    # GC: keep terminal refresh rows (rotated/revoked/expired) this long so reuse
    # detection still sees recent rotations; then hard-delete.
    refresh_token_retention_days: int = 7
    refresh_token_sweep_interval_seconds: int = 6 * 3600
    refresh_token_sweep_batch_limit: int = 500
    # 12h — covers long local turns; desktop also remints at each startTurn/resume.
    # Keep shorter than session cookies so a leaked inference JWT ages out first.
    inference_token_expire_minutes: int = 720

    inference_token_mint_max: int = 10
    inference_token_mint_window_seconds: int = 60

    # Same TTL posture as inference: sidecar roster / desk-binding calls use a
    # folders narrow JWT (type=folders), reminted per local turn.
    folders_token_expire_minutes: int = 720

    # Same TTL posture: sidecar conversation-log tools (search/read) use an
    # account narrow JWT (type=account), reminted per local turn.
    account_token_expire_minutes: int = 720

    # Cloud user-preview URL ticket (type=preview). Short-lived click-to-open,
    # not a sidecar remint-per-turn token. Bound to conversation + process + port.
    preview_token_expire_minutes: int = 15

    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_path_prefix: str = ""

    cors_allow_origins: str = (
        "http://localhost:5173,http://localhost:5174,http://localhost:3000,"
        "http://localhost:5175,http://localhost:5176,app://agentcore,"
        "capacitor://localhost,http://localhost,https://localhost"
    )

    rate_limit_enabled: bool = True
    auth_rate_limit_max: int = 10
    auth_rate_limit_window_seconds: int = 60
    user_message_rate_limit_max: int = 20
    user_message_rate_limit_window_seconds: int = 60
    # Admin MFA TOTP / recovery verify (per user_id). Caps brute-force on the
    # ±1 TOTP window; orthogonal to the per-IP AuthRateLimitMiddleware.
    mfa_verify_rate_limit_max: int = 5
    mfa_verify_rate_limit_window_seconds: int = 30
    trust_proxy: bool = False
    # When trust_proxy is on, the client IP is read from X-Forwarded-For. XFF is appended
    # left→right by each hop, so the *leftmost* entry is client-controlled and trivially
    # spoofed to rotate the rate-limit key past per-IP throttling; the trustworthy value
    # is the entry your own proxy appended, counted from the RIGHT. Set this to the number
    # of trusted proxies you run in front of the app (1 = one nginx; 2 = CDN + nginx), so
    # the limiter keys off ``parts[-trusted_proxy_hops]`` (SEC-008).
    trusted_proxy_hops: int = 1

    csrf_enabled: bool = True

    # Public registration gate (开放注册). Default open; set REGISTRATION_OPEN=false
    # to emergency-close signups without reverting to invite codes.
    registration_open: bool = True
    # Immediate-create POST /v1/auth/register. Default off, independent of DEBUG —
    # flipping DEBUG for production triage must not reopen an unverified hatch.
    # Integration tests and local instant-signup opt in explicitly.
    legacy_register_enabled: bool = False
    # Outbound SMTP. Unconfigured (empty host or from) keeps the console sink:
    # DEBUG prints the body; non-DEBUG logs email.unconfigured and does not send.
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_address: str = ""
    smtp_from_name: str = "AgentCore"
    smtp_tls_mode: Literal["ssl", "starttls", "none"] = "ssl"
    # When true, login refuses accounts whose email_verified_at is NULL.
    # Default off: legacy rows stay usable. Flip only after a backfill.
    require_email_verified: bool = False
    email_code_ttl_seconds: int = 600
    email_code_max_attempts: int = 5
    # Send-code reputation limits (independent of AuthRateLimitMiddleware).
    email_send_cooldown_seconds: int = 60
    email_send_daily_max: int = 8
    email_send_ip_hourly_max: int = 20

    # TOTP issuer shown in authenticator apps (admin MFA).
    mfa_issuer_name: str = "AgentCore Admin"
    # When false, admin login is password-only (session isolation still applies).
    admin_mfa_required: bool = True
    # ``memory`` = process-local rate-limit counters (dev / single worker only).
    # ``redis`` = shared rate limiters (CSRF stays a signed cookie — no Redis store).
    # With shared cost_ledger_outbox, redis unlocks multi-worker API; memory +
    # multi-worker is refused at boot in non-DEBUG.
    rate_limit_backend: Literal["memory", "redis"] = "memory"

    @field_validator("rate_limit_backend", "smtp_tls_mode", mode="before")
    @classmethod
    def _normalize_lowercase_choice(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origins(self) -> list[str]:
        """Parsed, trimmed list of allowed CORS origins."""
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]
